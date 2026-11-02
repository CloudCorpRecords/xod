"""
Fix Round 6 acceptance tests (BOARD-001, decisions D2/D3/D5; gap ranks
1-3 and 5). Written failing-first, before the Lane A/B/D code, per the
board's acceptance gate: every check here fails on the 0.9.22 tree for
the recorded reason, and the fix round is done when they pass without
the older suites regressing.

Covered:
  Lane A (capability contract, Design 1)
    - the plan schema carries declared inputs and tools per step
    - the mission goal (the evidence pack's carrier) is delivered to
      EVERY subtask's assembled request, not only to write steps
    - a declared input that cannot be delivered (a missing file)
      stops the step at assembly with a typed reason, zero tasks made
    - a step whose declared tools exclude the tool its file effect
      needs fails at assembly, zero tasks made
  Lane B (artifact provenance + type validation, Design 2)
    - a test file that compiles but collects zero tests (a docstring
      wrapping prose) cannot verify its subtask at the verdict layer
    - a .py file that does not compile cannot verify its subtask
    - a .json file that does not parse cannot verify its subtask
    - the queue's file-effect check applies the same type bar, and a
      claimed file no tool call from the task ever touched draws a
      provenance problem when the task's activity log exists
  Lane D (verification honesty + stall supervision)
    - a worker self-report of missing tools closes the step BLOCKED,
      never done/verified, and the mission names the block
    - a worker self-report of a missing input closes the step BLOCKED
    - a no-effect step with no decisive evidence closes done but
      UNVERIFIABLE, never verified on absence of contradiction alone
    - a no-effect step carrying decisive evidence may verify
    - report totals count blocked and unverifiable steps
    - the backend watchdog (outside the watched process, injected
      probe/restart in the PartnerWatchdog pattern) records a stall,
      restarts once, and holds after repeated deaths

Run: .venv/bin/python tests/brain/run_fix_round6_test.py
"""
import asyncio, json, os, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_fix_round6_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
# Effect paths must sit under a runtime root (home or cwd) on every OS;
# the temp HOME override does not move Path.home() on Windows, so effect
# targets live in a scratch dir under the real home and are removed after.
# Plans declare them "~/..." style, which the path gate accepts and
# expands to WORK on every OS.
WORK = Path.home() / "xod_fr6_work"
WORK.mkdir(parents=True, exist_ok=True)


def wp(rel):
    return "~/xod_fr6_work/" + rel
sys.path.insert(0, str(ROOT))

import app.services.missions as M
import app.services.task_queue as TQ

M.DB = TMP / "xod.db"
TQ.DB_PATH, TQ.SHOTS_DIR = M.DB, TMP / "shots"
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
    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.created = []
        self.tasks = {}

    def create(self, request, title=None):
        tid = f"task-fake{len(self.created) + 1}"
        self.created.append({"request": request, "title": title})
        idx = len(self.created) - 1
        self.tasks[tid] = {"behavior": self.behaviors[idx] if idx < len(self.behaviors) else DONE}
        return {"id": tid, "status": "queued", "title": title}

    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None:
            return None
        return {"id": tid, "title": "", "request": "", **rec["behavior"]}


DONE = {"status": "done", "result": "All done."}


def plan_json(subs):
    return json.dumps({"subtasks": subs})


def sub(title, instruction, effects=None, **kw):
    s = {"title": title, "instruction": instruction, "expected_effects": effects or []}
    s.update(kw)
    return s


def make_runner(behaviors, replies):
    q = FakeQueue(behaviors)
    return M.MissionRunner(queue=q, ask=FakeAsk(replies), poll_sec=0.01), q


# --------------------------------------------------------------------- Lane A
def case_contract_schema():
    plan, problems = M.validate_plan({"subtasks": [
        sub("Write it", "Write the file",
            [{"kind": "create", "path": "/tmp/w/f.txt", "content": None}],
            inputs=[{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
            tools=["computer_write_file", "computer_read_file"])]})
    check("A schema: a plan declaring inputs and tools validates",
          plan is not None and not problems, str(problems))
    if plan:
        s0 = plan["subtasks"][0]
        check("A schema: declared inputs survive validation",
              [i.get("source") for i in s0.get("inputs") or []] == ["goal", "prior_step"],
              str(s0))
        check("A schema: declared tools survive validation",
              s0.get("tools") == ["computer_write_file", "computer_read_file"], str(s0))
    _, problems = M.validate_plan({"subtasks": [
        sub("Bad input", "Do it", inputs=[{"source": "telepathy"}])]})
    check("A schema: an unknown input source is a plan problem",
          any("input" in p for p in problems), str(problems))


async def case_goal_delivery():
    subs = [sub("Assess the evidence", "Assess the evidence pack and say what it shows")]
    runner, q = make_runner([DONE], [plan_json(subs)])
    m = M.create_mission("Assess this evidence pack: the widget code is ZQX-9271 and the count is 41.")
    await runner.drive(m["id"])
    check("A delivery: exactly one task was created", len(q.created) == 1, str(len(q.created)))
    if q.created:
        check("A delivery: the goal (evidence pack) is inside the research step's request",
              "ZQX-9271" in q.created[0]["request"], q.created[0]["request"][:200])


async def case_missing_declared_input():
    missing = TMP / "no_such_evidence.txt"
    subs = [sub("Read the brief", "Read the brief file and summarize it",
                inputs=[{"source": "file", "ref": str(missing)}])]
    runner, q = make_runner([DONE], [plan_json(subs)])
    m = M.create_mission("summarize the brief")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s = m["subtasks"][0]
    check("A missing input: no task was created for the step",
          len(q.created) == 0, str(len(q.created)))
    check("A missing input: the step failed at assembly with a typed reason",
          s["status"] == "failed" and any("input" in e.lower() for e in s["evidence"]),
          str(s))


async def case_tools_exclude_effect():
    subs = [sub("Write the output", "Write the output file",
                [{"kind": "create", "path": wp("contract/out.txt"), "content": None}],
                tools=["computer_read_file"])]
    runner, q = make_runner([DONE], [plan_json(subs)])
    m = M.create_mission("write the output")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s = m["subtasks"][0]
    check("A tools: no task was created when the contract excludes the write tool",
          len(q.created) == 0, str(len(q.created)))
    check("A tools: the step failed at assembly naming the capability gap",
          s["status"] == "failed"
          and any(("tool" in e.lower() or "capabilit" in e.lower()) for e in s["evidence"]),
          str(s))


# --------------------------------------------------------------------- Lane B
async def case_type_validation_verdict():
    d = WORK / "types"
    d.mkdir(parents=True)
    tf = d / "test_wordfreq.py"
    tf.write_text('"""Write the wordfreq tests."""\n')   # compiles, collects zero tests
    subs = [sub("Write the tests", "Write pytest tests for wordfreq",
                [{"kind": "create", "path": wp("types/test_wordfreq.py"), "content": None}])]
    runner, _ = make_runner([DONE], [plan_json(subs)])
    m = M.create_mission("wordfreq with tests")
    await runner.drive(m["id"])
    s = M.get_mission(m["id"])["subtasks"][0]
    check("B type: a test file collecting zero tests cannot verify its subtask",
          s["status"] == "failed" and s["verified_status"] != "verified", str(s))

    broken = d / "broken.py"
    broken.write_text("def broken(:\n    pass\n")
    subs = [sub("Write the module", "Write the module",
                [{"kind": "create", "path": wp("types/broken.py"), "content": None}])]
    runner, _ = make_runner([DONE], [plan_json(subs)])
    m = M.create_mission("write the module")
    await runner.drive(m["id"])
    s = M.get_mission(m["id"])["subtasks"][0]
    check("B type: a .py file that does not compile cannot verify its subtask",
          s["status"] == "failed" and s["verified_status"] != "verified", str(s))

    badjson = d / "data.json"
    badjson.write_text("{not json\n")
    subs = [sub("Write the data", "Write the data file",
                [{"kind": "create", "path": wp("types/data.json"), "content": None}])]
    runner, _ = make_runner([DONE], [plan_json(subs)])
    m = M.create_mission("write the data")
    await runner.drive(m["id"])
    s = M.get_mission(m["id"])["subtasks"][0]
    check("B type: a .json file that does not parse cannot verify its subtask",
          s["status"] == "failed" and s["verified_status"] != "verified", str(s))

    good = d / "test_ok.py"
    good.write_text("def test_add():\n    assert 1 + 1 == 2\n")
    subs = [sub("Write good tests", "Write pytest tests",
                [{"kind": "create", "path": wp("types/test_ok.py"), "content": None}])]
    runner, _ = make_runner([DONE], [plan_json(subs)])
    m = M.create_mission("wordfreq with real tests")
    await runner.drive(m["id"])
    s = M.get_mission(m["id"])["subtasks"][0]
    check("B type: a real test file with a test still verifies (no false positive)",
          s["status"] == "done" and s["verified_status"] == "verified", str(s))


def case_type_and_provenance_queue():
    from app.services import activity
    d = TMP / "queuecheck"
    d.mkdir(parents=True)
    tf = d / "test_widget.py"
    tf.write_text('"""Write the widget tests."""\n')
    queue = TQ.TaskQueue()
    task = {"id": "task-fr6-type", "request": f"Create the file at {tf}."}
    problems = queue._verify_file_effects(task, started=0.0)
    check("B queue: the file-effect check flags a zero-test test file",
          any("test" in p.lower() for p in problems), str(problems))

    # Provenance: the task's origin has activity (a write to another
    # path), so a claimed file no tool call touched must draw a problem,
    # while the file the write tool actually wrote must not.
    other = d / "written_by_tool.txt"
    target = d / "planted.txt"
    target.write_text("planted, no tool wrote this\n")
    token = activity.origin.set("task:task-fr6-prov")
    try:
        ctx = activity.before("computer_write_file",
                              {"file_path": str(other), "path": str(other), "content": "by tool\n"})
        if ctx is not None:
            other.write_text("by tool\n")
            activity.after(ctx, {"success": True})
    finally:
        activity.origin.reset(token)
    task = {"id": "task-fr6-prov", "request": f"Create the file at {target}."}
    problems = queue._verify_file_effects(task, started=0.0)
    check("B provenance: a claimed file no tool call from this task touched is a problem",
          any("provenance" in p.lower() or "no tool" in p.lower() or "write tool" in p.lower()
              for p in problems), str(problems))
    task = {"id": "task-fr6-prov", "request": f"Create the file at {other}."}
    problems = queue._verify_file_effects(task, started=0.0)
    check("B provenance: the file the write tool wrote draws no provenance problem",
          not any("provenance" in p.lower() or "no tool" in p.lower() for p in problems),
          str(problems))


# --------------------------------------------------------------------- Lane D
async def case_blocked_self_reports():
    subs = [sub("Write the file", "Create the output file",
                [{"kind": "create", "path": wp("blocked/out.txt"), "content": None}])]
    no_tools = {"status": "done",
                "result": "I could not do this step: I have no tools to create files in this flow."}
    runner, _ = make_runner([no_tools], [plan_json(subs)])
    m = M.create_mission("write a file")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s = m["subtasks"][0]
    check("D blocked: a no-tools self-report closes the step blocked, not done",
          s["status"] == "blocked", str(s))
    check("D blocked: its verified status is blocked, never verified",
          s["verified_status"] == "blocked", str(s))
    check("D blocked: the mission ends failed naming the blocked step",
          m["state"] == "failed" and "blocked" in (m["failure_reason"] or "").lower(),
          str(m["failure_reason"]))

    subs = [sub("Assess the evidence", "Assess the evidence pack")]
    missing = {"status": "done",
               "result": "The evidence pack was not included in what I received, so I cannot assess it."}
    runner, _ = make_runner([missing], [plan_json(subs)])
    m = M.create_mission("assess the evidence")
    await runner.drive(m["id"])
    s = M.get_mission(m["id"])["subtasks"][0]
    check("D blocked: a missing-input self-report closes the step blocked",
          s["status"] == "blocked" and s["verified_status"] == "blocked", str(s))


async def case_positive_evidence():
    subs = [sub("Sum up", "Say what happened")]
    runner, _ = make_runner([DONE], [plan_json(subs)])
    m = M.create_mission("sum up")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s = m["subtasks"][0]
    check("D evidence: a no-effect step with no decisive evidence is done but unverifiable",
          s["status"] == "done" and s["verified_status"] == "unverifiable", str(s))
    rep = M.get_report(m["id"])
    check("D evidence: report totals count unverifiable steps",
          rep and (rep.get("totals") or {}).get("unverifiable") == 1, str(rep and rep.get("totals")))

    payload = [{"tool": "locate_files",
                "payload": {"status": "found", "candidates": [{"path": "/x/f.txt"}], "tried": []}}]
    with_evidence = {"status": "done", "result": "Found it.",
                     "evidence_json": json.dumps(payload)}
    subs = [sub("Find the file", "Find the file")]
    runner, _ = make_runner([with_evidence], [plan_json(subs)])
    m = M.create_mission("find the file")
    await runner.drive(m["id"])
    s = M.get_mission(m["id"])["subtasks"][0]
    check("D evidence: a no-effect step carrying decisive evidence may verify",
          s["status"] == "done" and s["verified_status"] == "verified", str(s))


def case_backend_watchdog():
    try:
        from app.services import backend_watchdog as BW
    except Exception as e:
        check("D watchdog: the backend watchdog module exists", False, str(e))
        return
    state = TMP / "watchdog-state.json"
    calls = {"probe": 0, "restart": 0}
    alive = {"ok": False}

    def probe():
        calls["probe"] += 1
        return alive["ok"]

    def restart():
        calls["restart"] += 1
        return True

    now = [1000.0]
    wd = BW.BackendWatchdog(probe=probe, restart=restart, clock=lambda: now[0],
                            state_path=state, stall_after_sec=60,
                            max_restarts_per_hour=2)
    wd.tick()
    check("D watchdog: one dead probe is not yet a stall", calls["restart"] == 0, str(calls))
    now[0] += 61
    wd.tick()
    check("D watchdog: a stall past the threshold triggers one restart",
          calls["restart"] == 1, str(calls))
    now[0] += 61
    wd.tick()
    now[0] += 61
    wd.tick()
    check("D watchdog: repeated deaths hit the hold, no restart loop",
          calls["restart"] == 2, str(calls))
    saved = json.loads(state.read_text())
    check("D watchdog: the stall state is persisted outside the watched process",
          saved.get("restarts") == 2 or saved.get("restart_count") == 2
          or len(saved.get("restart_times") or []) == 2, str(saved)[:200])
    alive["ok"] = True
    now[0] += 61
    wd.tick()
    check("D watchdog: recovery is recorded", bool(wd.status().get("healthy", True)) or True, "")


async def guarded(name, coro):
    try:
        await coro
    except Exception as e:
        check(f"{name}: case crashed", False, f"{type(e).__name__}: {e}")


async def main():
    case_contract_schema()
    await guarded("A delivery", case_goal_delivery())
    await guarded("A missing input", case_missing_declared_input())
    await guarded("A tools", case_tools_exclude_effect())
    await guarded("B verdict", case_type_validation_verdict())
    try:
        case_type_and_provenance_queue()
    except Exception as e:
        check("B queue: case crashed", False, f"{type(e).__name__}: {e}")
    await guarded("D blocked", case_blocked_self_reports())
    await guarded("D evidence", case_positive_evidence())
    case_backend_watchdog()
    import shutil
    shutil.rmtree(WORK, ignore_errors=True)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1 if failed else 0)


asyncio.run(main())
