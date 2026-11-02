"""
Missions planner artifact integrity (fix round 3, Lane 3): the planner's
instruction text must never become a deliverable, and a write step whose
assembled content payload is empty must fail at assembly, before any tool.

Regression suite for the 2026-10-31 Tier 1 re-run 2 record (T1A and T1B):
  - T1A, third consecutive run: test_wordfreq.py on disk was a single
    angle-bracketed instruction sentence ("<pytest tests invoking the
    actual CLI via subprocess with sys.executable and tmp_path input
    files; assert exact stdout formatting and counts, ...>") and died at
    pytest collection. The plan shape in the raw record: three subtasks
    (sample, CLI with a run effect, tests with create + run effects), and
    every create effect declares content null, so the file's content is
    produced at execution time, never at plan time. The round 1 leak
    detector needed an imperative opener plus a planning-process
    reference; the bracketed sentence has neither, so both the plan-time
    check (when the planner does declare the sentence) and the on-disk
    verdict check (the execution-time route) returned None for it.
  - T1B, new in re-run 2: the research steps finished, then the write
    step was invoked with no content ("computer_write_file needs
    content. Nothing was done."), research.md never landed, and the
    mission failed honestly but late. Two mission-layer causes: the
    runner assembled each subtask's request from that subtask alone, so
    content produced by earlier steps never reached the write step; and
    nothing stopped a write subtask whose declared content assembled to
    an empty payload before a task (and its tool calls) was created.

Covered here, with the queue faked (the missions layer is on trial):
  - the angle-bracketed instruction sentence is recognised as planner
    instruction text, declared in a plan and on disk, in the production
    plan shape; legitimate content (markup, dictated strings, code,
    notes) is not
  - a mission whose planner declares that sentence as the test file's
    content fails at planning with the leak named; no task is created
    and nothing is written to disk as the deliverable
  - a mission whose worker writes that sentence at execution time
    (declared content null, the production route) fails with the leak
    named in the evidence, whether or not the subtask declares a run
  - a write step's assembled request carries the earlier steps' reported
    results, so content produced upstream reaches the write step
  - a write step whose plan declares content but whose assembled payload
    is empty fails at assembly with the subtask named; its task is
    never created and no file lands

Run: .venv/bin/python tests/brain/run_missions_planner_integrity_test.py
"""
import asyncio, json, os, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_missions_planner_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
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

# The exact production shape from t1-raw2.json (T1A): the leaked file was
# one angle-bracketed instruction sentence, not code.
ANGLE_LEAK = ("<pytest tests invoking the actual CLI via subprocess with "
              "sys.executable and tmp_path input files; assert exact stdout "
              "formatting and counts, and error exits for missing or "
              "nonexistent inputs>")


def plan_json(subs):
    return json.dumps({"subtasks": subs})


def sub(title, instruction, effects=None):
    return {"title": title, "instruction": instruction, "expected_effects": effects or []}


def make_runner(behaviors, replies):
    q = FakeQueue(behaviors)
    return M.MissionRunner(queue=q, ask=FakeAsk(replies), poll_sec=0.01), q


def production_plan(d, test_content):
    """The T1A plan shape from the raw record: sample, CLI + run, tests
    create + run. Only the test file's declared content varies."""
    return [
        sub("Create sample text", "Create sample.txt with a few paragraphs of repeated words",
            [{"kind": "create", "path": str(d / "sample.txt"), "content": None}]),
        sub("Implement and run CLI", "Create wordfreq.py and run it on sample.txt",
            [{"kind": "create", "path": str(d / "wordfreq.py"), "content": None},
             {"kind": "run", "command": "python3 wordfreq.py sample.txt",
              "cwd": str(d), "expect_exit": 0}]),
        sub("Create and run tests", "Create test_wordfreq.py and run it with pytest",
            [{"kind": "create", "path": str(d / "test_wordfreq.py"), "content": test_content},
             {"kind": "run", "command": "python3 -m pytest test_wordfreq.py",
              "cwd": str(d), "expect_exit": 0}]),
    ]


# --------------------------------------------------------------------- (a) the leak detector
def case_detector():
    check("detector: the angle-bracketed instruction sentence is a leak",
          M._find_instruction_leak(ANGLE_LEAK) is not None,
          str(M._find_instruction_leak(ANGLE_LEAK)))
    check("detector: the round 1 imperative shape still matches",
          M._find_instruction_leak("Construct runnable pytest tests after inspection of the "
                                   "inspected module and its imports, then run them.") is not None)
    good = {
        "a dictated proof string": "baseline proof 7731",
        "ordinary code": "def test_add():\n    assert add(1, 1) == 2\n",
        "a markup line with a closing tag": "<note>remember to check the milk</note>",
        "a bracketed placeholder inside a sentence": "Write the greeting as: Hello <name>, welcome back.",
        "a comparison in code": "if a < b and b > c:\n    pass\n",
    }
    for label, content in good.items():
        check(f"detector: legitimate content passes ({label})",
              M._find_instruction_leak(content) is None,
              str(M._find_instruction_leak(content)))


def case_validate_angle():
    d = TMP / "validate"
    d.mkdir(parents=True)
    _, p = M.validate_plan({"subtasks": production_plan(d, ANGLE_LEAK)})
    check("validate: declared angle-bracket content is rejected as a planner instruction",
          any("planner instruction" in x for x in p), str(p))
    check("validate: the rejection quotes the leaked sentence",
          any("pytest tests invoking" in x for x in p), str(p))


# --------------------------------------------------------------------- (a) end to end
async def case_planning_leak():
    d = TMP / "planleak"
    d.mkdir(parents=True)
    reply = plan_json(production_plan(d, ANGLE_LEAK))
    runner, q = make_runner([], [reply, reply])   # decompose re-asks once
    m = M.create_mission("build a wordfreq CLI with tests")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    check("planning leak: the mission fails at planning, not done",
          m["state"] == "failed", m["state"])
    check("planning leak: the failure names the leak",
          "planner instruction" in (m["failure_reason"] or ""), str(m["failure_reason"]))
    check("planning leak: no subtask task was ever created (no tool ran)",
          len(q.created) == 0, str(len(q.created)))
    check("planning leak: the sentence was never written to disk as the deliverable",
          not (d / "test_wordfreq.py").exists())


async def case_execution_leak():
    # Production route: the plan declares content null; the worker writes
    # the instruction sentence at execution time and claims success.
    d = TMP / "execleak"
    d.mkdir(parents=True)
    (d / "sample.txt").write_text("the quick brown fox and the lazy dog\n")
    (d / "wordfreq.py").write_text("import sys\nprint('the: 2')\n")
    (d / "test_wordfreq.py").write_text(ANGLE_LEAK + "\n")
    subs = production_plan(d, None)
    runner, _ = make_runner([DONE, DONE, DONE], [plan_json(subs)])
    m = M.create_mission("build a wordfreq CLI with tests")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s = [x for x in m["subtasks"] if x["title"] == "Create and run tests"][0]
    check("execution leak: the mission ends failed, not done", m["state"] == "failed", m["state"])
    check("execution leak: the subtask is contradicted, not verified",
          s["status"] == "failed" and s["verified_status"] == "contradicted", str(s))
    check("execution leak: the evidence names planner instruction text",
          any("planner instruction" in e for e in s["evidence"]), str(s["evidence"]))

    # The create-only variant (the interleaved plan's test subtask had no
    # run effect): nothing else can catch the file, so the leak check is
    # the only thing standing between it and a verified subtask.
    d2 = TMP / "execleak2"
    d2.mkdir(parents=True)
    target = d2 / "test_wordfreq.py"
    target.write_text(ANGLE_LEAK + "\n")
    subs2 = [sub("Add runnable pytest tests", "Create test_wordfreq.py with pytest tests",
                 [{"kind": "create", "path": str(target), "content": None}])]
    runner2, _ = make_runner([DONE], [plan_json(subs2)])
    m2 = M.create_mission("add tests")
    await runner2.drive(m2["id"])
    m2 = M.get_mission(m2["id"])
    s2 = m2["subtasks"][0]
    check("execution leak (no run effect): the mission ends failed and the subtask is not verified",
          m2["state"] == "failed" and s2["verified_status"] != "verified",
          f"state={m2['state']} sub={s2}")
    check("execution leak (no run effect): the evidence names planner instruction text",
          any("planner instruction" in e for e in s2["evidence"]), str(s2["evidence"]))


# --------------------------------------------------------------------- (b) payload assembly
RESEARCH_RESULT = {"status": "done",
                   "result": "Jupiter has 115 moons as of 9 April 2026 (Wikipedia, "
                             "https://en.wikipedia.org/wiki/Moons_of_Jupiter). Everest summit "
                             "averages are minus 36 C in January and minus 19 C in July."}


async def case_carry_forward():
    d = TMP / "research"
    d.mkdir(parents=True)
    target = d / "research.md"
    subs = [sub("Research the facts", "Research Jupiter moons and Everest summit temperature"),
            sub("Write research file", "Write the sourced research into research.md",
                [{"kind": "create", "path": str(target), "content": None}])]
    runner, q = make_runner([RESEARCH_RESULT, DONE], [plan_json(subs)])
    m = M.create_mission("research two facts into a file")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    check("carry-forward: the mission closes done", m["state"] == "done",
          f"state={m['state']} reason={m['failure_reason']}")
    check("carry-forward: the write step's assembled request carries the upstream result",
          len(q.created) == 2 and "115 moons" in q.created[1]["request"]
          and "Everest" in q.created[1]["request"],
          str(q.created[1]["request"] if len(q.created) > 1 else q.created))
    check("carry-forward: the first step's request carries nothing extra",
          "115 moons" not in q.created[0]["request"], str(q.created[0]["request"]))


async def case_empty_declared_payload():
    d = TMP / "emptywrite"
    d.mkdir(parents=True)
    target = d / "research.md"
    subs = [sub("Research the facts", "Research Jupiter moons and Everest summit temperature"),
            sub("Write and verify research file", "Write the sourced research into research.md",
                [{"kind": "create", "path": str(target), "content": ""}])]
    runner, q = make_runner([RESEARCH_RESULT, DONE], [plan_json(subs)])
    m = M.create_mission("research two facts into a file")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s = m["subtasks"][1]
    check("empty payload: the mission ends failed, not done", m["state"] == "failed", m["state"])
    check("empty payload: the failure names the subtask",
          "Write and verify research file" in (m["failure_reason"] or ""), str(m["failure_reason"]))
    check("empty payload: the failure says the assembled content was empty",
          "empty" in (m["failure_reason"] or "").lower(), str(m["failure_reason"]))
    check("empty payload: the write step's task was never created (the tool never ran)",
          len(q.created) == 1, str(len(q.created)))
    check("empty payload: the subtask is recorded failed, not verified",
          s["status"] == "failed" and s["verified_status"] != "verified", str(s))
    check("empty payload: no file landed", not target.exists())


async def main():
    case_detector()
    case_validate_angle()
    await case_planning_leak()
    await case_execution_leak()
    await case_carry_forward()
    await case_empty_declared_payload()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1 if failed else 0)


asyncio.run(main())
