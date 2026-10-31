"""
Missions (the parent orchestrator, services/missions.py): the parent plans,
the queue's file-effect check judges, and the two are never confused.

Covered here, with the model and (mostly) the queue faked:
  - plan schema validation, strict, with named problems
  - a bad plan is re-asked once with the problems named, then the mission
    fails honestly instead of improvising
  - subtasks run in order through the queue, outcomes recorded as claimed
    (the agent's words) versus verified (the gate's verdict, with evidence)
  - a failed subtask stops the mission by default; the mission-level
    keep-going option runs the rest and still reports the failure
  - a subtask waiting on the user pauses the mission, and it resumes
  - a restart mid-mission (a fresh runner over the same database) resumes
    from the first unfinished subtask without redoing finished work
  - the report's totals and its usage section ("not tracked in this build")
  - end to end through the real TaskQueue: an honest researcher is verified,
    a lying one is contradicted by the gate and fails the mission

Run: .venv/bin/python tests/brain/run_missions_test.py
"""
import asyncio, json, os, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_missions_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.missions as M
import app.services.task_queue as TQ
import app.services.browser_sandbox as BS
import app.agent.researcher as RS

DBF = TMP / "xod.db"
M.DB = DBF
TQ.DB_PATH, TQ.SHOTS_DIR = DBF, TMP / "shots"
TQ.notify = lambda title, msg: None
TQ._phone = lambda task, kind: None
M._spawn = lambda mid: None   # tests drive missions by hand

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


# --------------------------------------------------------------------- fakes
class FakeAsk:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "{}"


class FakeQueue:
    """One scripted behavior per created task: a dict for get(), or a callable."""

    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.created = []
        self.tasks = {}

    def create(self, request, title=None):
        tid = f"task-fake{len(self.created) + 1}"
        self.created.append({"request": request, "title": title})
        self.tasks[tid] = {"behavior": self.behaviors[len(self.created) - 1], "gets": 0}
        return {"id": tid, "status": "queued", "title": title}

    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None:
            return None
        rec["gets"] += 1
        b = rec["behavior"]
        if callable(b):
            return b(rec)
        return {"id": tid, "title": "", "request": "", **b}


DONE = {"status": "done", "result": "All done."}
CONTRA = {"status": "failed",
          "result": "I made the file, honest.\n\nNot finished: the task asked for report.txt to be created, but it is not on disk"}
CRASH = {"status": "failed", "result": "Something went wrong: the engine exploded"}


def plan_json(subs):
    return json.dumps({"subtasks": subs})


def sub(title, instruction, effects=None):
    return {"title": title, "instruction": instruction, "expected_effects": effects or []}


def make_runner(behaviors, replies, **kw):
    q = FakeQueue(behaviors)
    ask = FakeAsk(replies)
    return M.MissionRunner(queue=q, ask=ask, poll_sec=0.01), q, ask


# --------------------------------------------------------------------- cases
def case_validate():
    good = {"subtasks": [sub("A", "Do A", [{"kind": "create", "path": "/tmp/a.txt", "content": "x"}]),
                         sub("B", "Answer B")]}
    plan, problems = M.validate_plan(good)
    check("validate: a good plan passes", plan is not None and not problems)
    bad_kind = {"subtasks": [sub("A", "Do A", [{"kind": "move", "path": "/tmp/a", "content": None}])]}
    _, p = M.validate_plan(bad_kind)
    check("validate: a bad effect kind is named", any("kind must be one of" in x for x in p), str(p))
    missing = {"subtasks": [{"title": "A", "instruction": "Do A"}]}
    _, p = M.validate_plan(missing)
    check("validate: missing expected_effects is named", any("expected_effects must be a list" in x for x in p), str(p))
    del_content = {"subtasks": [sub("A", "Do A", [{"kind": "delete", "path": "/tmp/a", "content": "x"}])]}
    _, p = M.validate_plan(del_content)
    check("validate: delete with content is named", any("delete effect cannot carry content" in x for x in p), str(p))
    rel = {"subtasks": [sub("A", "Do A", [{"kind": "create", "path": "relative/a.txt", "content": None}])]}
    _, p = M.validate_plan(rel)
    check("validate: a relative path is named", any("absolute or start with" in x for x in p), str(p))
    dotdot = {"subtasks": [sub("A", "Do A", [{"kind": "create", "path": "/tmp/../etc/a", "content": None}])]}
    _, p = M.validate_plan(dotdot)
    check("validate: a .. path is named", any("must not contain" in x for x in p), str(p))
    _, p = M.validate_plan({"subtasks": []})
    check("validate: an empty plan is rejected", bool(p))
    _, p = M.validate_plan("not even a dict")
    check("validate: a non-object reply is rejected", bool(p))
    run_ok = {"subtasks": [sub("Test it", "Run the tests",
                               [{"kind": "run", "command": "python3 test_a.py", "cwd": "/tmp/proj",
                                 "expect_exit": 0}])]}
    plan, problems = M.validate_plan(run_ok)
    eff = (plan or {}).get("subtasks", [{}])[0].get("expected_effects", [{}])[0]
    check("validate: a run effect passes with its fields kept",
          plan is not None and eff.get("kind") == "run" and eff.get("command") == "python3 test_a.py"
          and eff.get("cwd") == "/tmp/proj" and eff.get("expect_exit") == 0, str(problems))
    run_default = {"subtasks": [sub("Test it", "Run the tests",
                                    [{"kind": "run", "command": "python3 test_a.py", "cwd": "/tmp/proj"}])]}
    plan, _ = M.validate_plan(run_default)
    check("validate: a run effect defaults expect_exit to 0",
          plan is not None and plan["subtasks"][0]["expected_effects"][0]["expect_exit"] == 0)
    run_typed = {"subtasks": [sub("Test it", "Run the tests",
                                  [{"type": "run", "command": "python3 test_a.py", "cwd": "/tmp/proj"}])]}
    plan, _ = M.validate_plan(run_typed)
    check("validate: a run effect spelled with type also passes",
          plan is not None and plan["subtasks"][0]["expected_effects"][0]["kind"] == "run")
    _, p = M.validate_plan({"subtasks": [sub("T", "Do", [{"kind": "run", "command": "  ", "cwd": "/tmp/proj"}])]})
    check("validate: a run effect with no command is named",
          any("command must be a non-empty string" in x for x in p), str(p))
    _, p = M.validate_plan({"subtasks": [sub("T", "Do", [{"kind": "run", "command": "python3 t.py",
                                                          "cwd": "relative/dir"}])]})
    check("validate: a run effect with a relative cwd is named",
          any("cwd must be absolute or start with" in x for x in p), str(p))
    _, p = M.validate_plan({"subtasks": [sub("T", "Do", [{"kind": "run", "command": "python3 t.py",
                                                          "cwd": "/tmp/proj", "expect_exit": "0"}])]})
    check("validate: a run effect with a non-integer expect_exit is named",
          any("expect_exit must be an integer" in x for x in p), str(p))


async def case_decompose():
    good = plan_json([sub("A", "Do A")])
    ask = FakeAsk([json.dumps({"subtasks": [{"title": "A", "instruction": "Do A"}]}), good])
    plan = await M.decompose("goal", ask)
    check("decompose: a bad plan is re-asked and the retry can pass", plan is not None and len(ask.prompts) == 2)
    check("decompose: the re-ask names the problem", "expected_effects must be a list" in ask.prompts[1])
    ask2 = FakeAsk(["{}", "{}"])
    try:
        await M.decompose("goal", ask2)
        check("decompose: two bad plans raise PlanRejected", False)
    except M.PlanRejected as e:
        check("decompose: two bad plans raise PlanRejected", len(e.problems) >= 1 and len(ask2.prompts) == 2)


async def case_happy():
    subs = [sub("Write the note", "Write a note file", [{"kind": "create", "path": f"{TMP}/note.txt", "content": "hello brain"}]),
            sub("Remove the old", "Remove the old file", [{"kind": "delete", "path": f"{TMP}/old.txt", "content": None}]),
            sub("Sum up", "Say what happened in one line")]
    runner, q, _ = make_runner([DONE, DONE, DONE], [plan_json(subs)])
    m = M.create_mission("make and tidy")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    check("happy: mission done", m["state"] == "done", m["state"])
    check("happy: all subtasks recorded done and verified",
          all(s["status"] == "done" and s["verified_status"] == "verified" for s in m["subtasks"]))
    check("happy: claimed is the agent's own words", m["subtasks"][0]["claimed"] == "All done.")
    check("happy: subtasks carry their task ids", all(s["task_id"] for s in m["subtasks"]))
    check("happy: tasks created in plan order, titled as mission steps",
          len(q.created) == 3 and "mission step 1" in q.created[0]["title"] and "mission step 3" in q.created[2]["title"])
    check("happy: single-line content is dictated in the gate's exact-text form",
          "with exact text: hello brain" in q.created[0]["request"], q.created[0]["request"])
    check("happy: a delete is dictated in the gate's words", "Delete the file at" in q.created[1]["request"])
    rep = M.get_report(m["id"])
    check("happy: report totals", rep and rep["totals"] == {"subtasks": 3, "done": 3, "verified": 3, "failed": 0},
          str(rep and rep["totals"]))
    check("happy: report usage is honest", rep["usage"]["status"] == "not tracked in this build")
    check("happy: report keeps claimed and verified apart",
          rep["subtasks"][0]["claimed_result"] == "All done." and rep["subtasks"][0]["verified_status"] == "verified")


async def case_stop_on_failure():
    subs = [sub("Make report", "Make the report", [{"kind": "create", "path": "/tmp/report.txt", "content": None}]),
            sub("Then this", "Do the next thing")]
    runner, q, _ = make_runner([CONTRA, DONE], [plan_json(subs)])
    m = M.create_mission("two steps")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s1, s2 = m["subtasks"]
    check("stop: a contradicted step is recorded as contradicted, not failed",
          s1["verified_status"] == "contradicted", s1["verified_status"])
    check("stop: the evidence is the gate's own words", any("Checked on disk:" in e for e in s1["evidence"]), str(s1["evidence"]))
    check("stop: the claim survives beside the verdict", s1["claimed"] == "I made the file, honest.")
    check("stop: mission failed and names the step", m["state"] == "failed" and "Step 1" in (m["failure_reason"] or ""),
          str(m["failure_reason"]))
    check("stop: later steps never run and are marked skipped",
          len(q.created) == 1 and s2["status"] == "skipped", f"created={len(q.created)} s2={s2['status']}")


async def case_continue_on_failure():
    subs = [sub("Make report", "Make the report", [{"kind": "create", "path": "/tmp/report.txt", "content": None}]),
            sub("Then this", "Do the next thing")]
    runner, q, _ = make_runner([CONTRA, DONE], [plan_json(subs)])
    m = M.create_mission("two steps", continue_on_failure=True)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    rep = M.get_report(m["id"])
    check("continue: both steps ran", len(q.created) == 2)
    check("continue: mission still reports the failure honestly",
          m["state"] == "failed" and rep["totals"]["failed"] == 1 and rep["totals"]["verified"] == 1,
          f"state={m['state']} totals={rep['totals']}")
    check("continue: the second step is done and verified",
          m["subtasks"][1]["status"] == "done" and m["subtasks"][1]["verified_status"] == "verified")


async def case_crash_is_not_contradiction():
    runner, _, _ = make_runner([CRASH], [plan_json([sub("Boom", "Do a thing")])])
    m = M.create_mission("one step")
    await runner.drive(m["id"])
    s = M.get_mission(m["id"])["subtasks"][0]
    check("crash: a task crash is a failure, not a contradiction",
          s["verified_status"] == "failed" and s["status"] == "failed", s["verified_status"])


async def case_pause_resume():
    state = {"gets": 0}

    def waiting_then_done(rec):
        state["gets"] += 1
        if state["gets"] <= 1:
            return {"id": "task-fake1", "status": "waiting", "result": ""}
        return {"id": "task-fake1", **DONE}

    runner, _, _ = make_runner([waiting_then_done], [plan_json([sub("Ask first", "Do it after asking")])])
    m = M.create_mission("needs the user midway")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    check("pause: a waiting subtask pauses the mission",
          m["state"] == "paused" and m["subtasks"][0]["status"] == "waiting", m["state"])
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    check("pause: the mission resumes and finishes", m["state"] == "done", m["state"])


async def case_restart_resume():
    gate = {"open": False}

    def working_until_open(rec):
        if not gate["open"]:
            return {"id": "task-fake2", "status": "working", "result": ""}
        return {"id": "task-fake2", **DONE}

    subs = [sub("First", "Do the first thing"), sub("Second", "Do the second thing")]
    runner, q, _ = make_runner([DONE, working_until_open], [plan_json(subs)])
    m = M.create_mission("survive a restart")
    drive_task = asyncio.create_task(runner.drive(m["id"]))
    for _ in range(200):
        await asyncio.sleep(0.02)
        cur = M.get_mission(m["id"])
        if len(q.created) == 2 and cur["subtasks"][0]["status"] == "done":
            break
    drive_task.cancel()
    try:
        await drive_task
    except asyncio.CancelledError:
        pass
    mid = M.get_mission(m["id"])
    check("restart: first step stayed done across the kill",
          mid["subtasks"][0]["status"] == "done" and mid["subtasks"][1]["task_id"] == "task-fake2")
    gate["open"] = True
    runner2 = M.MissionRunner(queue=q, ask=FakeAsk([]), poll_sec=0.01)   # a fresh parent, same database
    await runner2.drive(m["id"])
    m = M.get_mission(m["id"])
    check("restart: resumed from the first unfinished step, nothing recreated",
          m["state"] == "done" and len(q.created) == 2, f"state={m['state']} created={len(q.created)}")


def case_render():
    multi = {"title": "T", "instruction": "Write it",
             "expected_effects": [{"kind": "create", "path": "/tmp/m.txt", "content": "line one\nline two"}]}
    req = M.render_request(multi)
    check("render: multi-line content is not dictated in exact-text form", "exact text:" not in req, req)
    check("render: multi-line content still rides on the instruction", "line one" in req and "Create the file at /tmp/m.txt" in req)
    single = {"title": "T", "instruction": "Write it",
              "expected_effects": [{"kind": "create", "path": "/tmp/s.txt", "content": "one line"}]}
    check("render: single-line content uses exact text", "with exact text: one line" in M.render_request(single))
    trailing = {"title": "T", "instruction": "Write it with a newline at the end",
                "expected_effects": [{"kind": "create", "path": "/tmp/nl.txt", "content": "one line\n"}]}
    req = M.render_request(trailing)
    check("render: content with a trailing newline is not dictated as exact text",
          "with exact text:" not in req and "Create the file at /tmp/nl.txt." in req, req)
    running = {"title": "T", "instruction": "Run it",
               "expected_effects": [{"kind": "run", "command": "python3 test_a.py",
                                     "cwd": "/tmp/proj", "expect_exit": 0}]}
    check("render: a run effect uses the gate's canonical phrasing",
          "Run exactly this command from the directory /tmp/proj and require exit code 0: python3 test_a.py"
          in M.render_request(running), M.render_request(running))


def case_hardening():
    """The 2026-10-30 hardening round: directory steps and honest evidence."""
    dsub = {"title": "Create Project Directory",
            "instruction": "Create a new directory at /tmp/xod-proj for the project.",
            "expected_effects": [{"kind": "create", "path": "/tmp/xod-proj", "content": None}]}
    req = M.render_request(dsub)
    check("render: a directory step asks for a directory, not a file",
          "Create the directory at /tmp/xod-proj." in req and "Create the file at /tmp/xod-proj" not in req, req)
    done_task = {"status": "done", "result": "Created it."}
    out = M._outcome_from_task(done_task, dsub)
    check("evidence: existence-only checks say the contents were not checked",
          out["verified_status"] == "verified" and "contents were not checked" in out["evidence"][0], str(out))
    ssub = {"title": "T", "instruction": "Write it",
            "expected_effects": [{"kind": "create", "path": "/tmp/s.txt", "content": "one line"}]}
    out2 = M._outcome_from_task(done_task, ssub)
    check("evidence: dictated text checks say the text was checked",
          "dictated text" in out2["evidence"][0], str(out2))


# --------------------------------------------------------------------- real queue
async def _boom():
    raise RuntimeError("Background Chrome failed to start or open remote debugging port.")


class _HonestResearcher:
    effect = None

    async def run_research_stream(self, messages):
        if type(self).effect is not None:
            type(self).effect()
        yield {"type": "content", "content": "Created it, as asked."}


class _LyingResearcher:
    async def run_research_stream(self, messages):
        yield {"type": "content", "content": "All done."}


async def case_real_queue():
    BS.browser_sandbox_service.ensure_browser_running = _boom
    target = TMP / "real.txt"
    _HonestResearcher.effect = lambda: target.write_bytes(b"hello brain")
    RS.AgenticResearcher = _HonestResearcher
    q = TQ.TaskQueue()
    q.start()
    plan = plan_json([sub("Write the file", "Write the greeting file",
                          [{"kind": "create", "path": str(target), "content": "hello brain"}])])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([plan]), poll_sec=0.05)
    m = M.create_mission("real queue, honest agent")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    check("real queue: honest work is verified by the gate",
          m["state"] == "done" and m["subtasks"][0]["verified_status"] == "verified",
          f"state={m['state']} sub={m['subtasks'][0]}")
    RS.AgenticResearcher = _LyingResearcher
    plan2 = plan_json([sub("Write the file", "Write the greeting file",
                            [{"kind": "create", "path": str(TMP / "missing.txt"), "content": "hello brain"}])])
    runner2 = M.MissionRunner(queue=q, ask=FakeAsk([plan2]), poll_sec=0.05)
    m2 = M.create_mission("real queue, lying agent")
    await runner2.drive(m2["id"])
    m2 = M.get_mission(m2["id"])
    check("real queue: a lying agent is contradicted and fails the mission",
          m2["state"] == "failed" and m2["subtasks"][0]["verified_status"] == "contradicted",
          f"state={m2['state']} sub={m2['subtasks'][0]}")

    # The mission-1938ea6dd9 shape (H6): step 2 creates a file inside the
    # directory step 1 made. Before the parser fix, the queue manufactured a
    # file claim for the container path itself and contradicted honest work.
    d = TMP / "xod-hardening-proof"
    proof = d / "proof.txt"

    def _make_container():
        d.mkdir(parents=True, exist_ok=True)
        proof.write_text("hardening proof")

    _HonestResearcher.effect = _make_container
    RS.AgenticResearcher = _HonestResearcher
    plan3 = plan_json([
        sub("Create directory", f"Create the directory {d}.",
            [{"kind": "create", "path": str(d), "content": None}]),
        sub("Create proof.txt",
            f"Create a file named proof.txt inside {d} containing the line: hardening proof.",
            [{"kind": "create", "path": str(proof), "content": "hardening proof"}]),
        sub("Read proof.txt", f"Read the contents of {proof} and report its contents."),
    ])
    runner3 = M.MissionRunner(queue=q, ask=FakeAsk([plan3]), poll_sec=0.05)
    m3 = M.create_mission("real queue, container shape")
    await runner3.drive(m3["id"])
    m3 = M.get_mission(m3["id"])
    check("real queue: a file inside its mission-made directory verifies (H6)",
          m3["state"] == "done" and m3["subtasks"][1]["verified_status"] == "verified",
          f"state={m3['state']} subs={[(s['title'], s['verified_status']) for s in m3['subtasks']]}")


_RUN_HARNESS = '''

import sys as _sys, traceback as _tb


def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS " + name)
        except Exception:
            failed += 1
            print("FAIL " + name)
            _tb.print_exc()
    print("%d passed, %d failed" % (len(tests) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    _sys.exit(_main())
'''
_FAILING_TESTS = "\n".join(
    f"def test_case_{i}():\n    assert undefined_helper_{i}() == {i}\n" for i in range(1, 6)
) + _RUN_HARNESS
_PASSING_TESTS = ("def add(a, b):\n    return a + b\n\n\n"
                  + "\n".join(f"def test_case_{i}():\n    assert add({i}, 1) == {i + 1}\n"
                              for i in range(1, 6))
                  + _RUN_HARNESS)


async def case_real_queue_run_effects():
    """End to end through the real queue: a subtask that writes a test file
    and declares a run effect is judged by the run, not by the file's
    existence (the hardening probe case, H4 remainder)."""
    BS.browser_sandbox_service.ensure_browser_running = _boom
    q = TQ.TaskQueue()
    q.start()
    py = sys.executable

    d = TMP / "run-proj"
    target = d / "test_calc.py"

    def _write_failing():
        d.mkdir(parents=True, exist_ok=True)
        target.write_text(_FAILING_TESTS)

    _HonestResearcher.effect = _write_failing
    RS.AgenticResearcher = _HonestResearcher
    plan = plan_json([sub("Write the tests", "Write the test file",
                          [{"kind": "create", "path": str(target), "content": None},
                           {"kind": "run", "command": f"{py} test_calc.py", "cwd": str(d),
                            "expect_exit": 0}])])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([plan]), poll_sec=0.05)
    m = M.create_mission("real queue, failing tests")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s = m["subtasks"][0]
    check("run effects: a test file whose tests all fail is contradicted by the run check",
          m["state"] == "failed" and s["verified_status"] == "contradicted",
          f"state={m['state']} sub={s}")
    check("run effects: the contradiction evidence carries the exit code and the failure output",
          any("exited with code 1" in e and "NameError" in e for e in s["evidence"]),
          str(s["evidence"]))

    d2 = TMP / "run-proj-ok"
    target2 = d2 / "test_calc.py"

    def _write_passing():
        d2.mkdir(parents=True, exist_ok=True)
        target2.write_text(_PASSING_TESTS)

    _HonestResearcher.effect = _write_passing
    RS.AgenticResearcher = _HonestResearcher
    plan2 = plan_json([sub("Write the tests", "Write the test file",
                           [{"kind": "create", "path": str(target2), "content": None},
                            {"kind": "run", "command": f"{py} test_calc.py", "cwd": str(d2),
                             "expect_exit": 0}])])
    runner2 = M.MissionRunner(queue=q, ask=FakeAsk([plan2]), poll_sec=0.05)
    m2 = M.create_mission("real queue, passing tests")
    await runner2.drive(m2["id"])
    m2 = M.get_mission(m2["id"])
    s2 = m2["subtasks"][0]
    check("run effects: a test file whose tests pass verifies",
          m2["state"] == "done" and s2["verified_status"] == "verified",
          f"state={m2['state']} sub={s2}")
    check("run effects: the verified evidence states what ran, where, and its exit code",
          any("ran the declared check" in e and "test_calc.py" in e and str(d2) in e
              and "exited with code 0" in e for e in s2["evidence"]),
          str(s2["evidence"]))


async def main():
    case_validate()
    case_render()
    case_hardening()
    await case_decompose()
    await case_happy()
    await case_stop_on_failure()
    await case_continue_on_failure()
    await case_crash_is_not_contradiction()
    await case_pause_resume()
    await case_restart_resume()
    await case_real_queue()
    await case_real_queue_run_effects()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1 if failed else 0)


asyncio.run(main())
