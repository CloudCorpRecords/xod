"""
Missions verdict integrity (fix round 1, Lane A): the mission verdict must
not contradict its own subtasks, the disk, or a declared run check.

Regression suite for the 2026-10-31 Tier 1 baseline (T1A/T1B), where a
mission closed "done" with every subtask "verified" while two subtask
reports said "not complete", one named a pytest failure, and the produced
test file contained a single planner instruction sentence instead of code;
a second mission shipped a research file with an unresolved self-instruction
line inside it; and plans interleaved duplicated steps (1,1,2,2,3,3).

Covered here, with the queue faked (the missions layer is what is on trial):
  - a subtask whose own report says it is not complete cannot be verified,
    and the mission ends failed naming that subtask
  - with keep-going set, the rest still run and the mission still ends
    failed, with the failure count and the failed subtask named
  - a created file whose actual content is a planner instruction sentence
    (the instruction-text test file; the research file leak line) cannot
    verify its subtask
  - plan validation rejects declared content that is itself an instruction
    sentence, while legitimate dictated content (proof strings, notes,
    code, ordinary user-facing instructions) still passes
  - a declared run effect that fails, or whose target never ran, blocks a
    verified subtask and a done mission; a passing run still verifies
  - plan validation dedupes identical steps (merged double plans collapse)
    while keeping same-title steps whose effects differ
  - a clean mission still closes done with every subtask verified

Run: .venv/bin/python tests/brain/run_missions_verdict_test.py
"""
import asyncio, json, os, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_missions_verdict_"))
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
        b = rec["behavior"]
        return {"id": tid, "title": "", "request": "", **b}


DONE = {"status": "done", "result": "All done."}


def plan_json(subs):
    return json.dumps({"subtasks": subs})


def sub(title, instruction, effects=None):
    return {"title": title, "instruction": instruction, "expected_effects": effects or []}


def make_runner(behaviors, replies):
    q = FakeQueue(behaviors)
    return M.MissionRunner(queue=q, ask=FakeAsk(replies), poll_sec=0.01), q


INSTRUCTION_TEST_FILE = ("Construct runnable pytest tests after inspection of the "
                         "inspected module and its imports, then run them.")
RESEARCH_LEAK = ("insert the exact verified source URL from the preceding "
                 "source check before saving")


# --------------------------------------------------------------------- plan validation
def case_validate_leak():
    bad = {"subtasks": [sub("Write the tests", "Write tests for wordfreq",
                            [{"kind": "create", "path": "/tmp/w/test_wordfreq.py",
                              "content": INSTRUCTION_TEST_FILE}])]}
    _, p = M.validate_plan(bad)
    check("validate: declared content that is a planner instruction is rejected",
          any("instruction" in x for x in p), str(p))
    bad2 = {"subtasks": [sub("Write research", "Write the research file",
                             [{"kind": "create", "path": "/tmp/w/research.md",
                               "content": f"Jupiter has 115 moons.\n{RESEARCH_LEAK}\n"}])]}
    _, p = M.validate_plan(bad2)
    check("validate: an unresolved source-insert instruction in content is rejected",
          any("instruction" in x for x in p), str(p))
    good_cases = {
        "a dictated proof string": "baseline proof 7731",
        "an ordinary note": "Shopping list\nmilk, eggs, flour\nRemember the bakery closes at six.",
        "ordinary code": "def add(a, b):\n    return a + b\n",
        "a user-facing instruction with no planner meta-reference":
            "Insert the key and turn it before saving your settings.",
    }
    for label, content in good_cases.items():
        plan, problems = M.validate_plan(
            {"subtasks": [sub("Write it", "Write the file",
                              [{"kind": "create", "path": "/tmp/w/f.txt", "content": content}])]})
        check(f"validate: legitimate dictated content passes ({label})",
              plan is not None and not problems, str(problems))


def case_validate_dedupe():
    a = sub("Build the CLI", "Build wordfreq", [{"kind": "create", "path": "/tmp/w/wordfreq.py", "content": None}])
    b = sub("Write the tests", "Write tests", [{"kind": "create", "path": "/tmp/w/test_wordfreq.py", "content": None}])
    plan, problems = M.validate_plan({"subtasks": [a, b, dict(a), dict(b)]})
    check("validate: a merged double plan collapses to one sequence",
          plan is not None and len(plan["subtasks"]) == 2, str(plan))
    a2 = sub("Build the CLI", "Build wordfreq", [{"kind": "create", "path": "/tmp/w/other.py", "content": None}])
    plan, _ = M.validate_plan({"subtasks": [a, a2]})
    check("validate: same title and instruction but different effects are kept",
          plan is not None and len(plan["subtasks"]) == 2, str(plan))


# --------------------------------------------------------------------- mission outcomes
async def case_instruction_file():
    d = TMP / "wordfreq"
    d.mkdir(parents=True)
    target = d / "test_wordfreq.py"
    target.write_text(INSTRUCTION_TEST_FILE + "\n")   # the baseline shape: not code
    subs = [sub("Write the tests", "Write pytest tests for wordfreq",
                [{"kind": "create", "path": str(target), "content": None}])]
    runner, _ = make_runner([DONE], [plan_json(subs)])
    m = M.create_mission("build a wordfreq CLI with tests")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s = m["subtasks"][0]
    check("instruction file: the mission ends failed, not done", m["state"] == "failed", m["state"])
    check("instruction file: the subtask is not verified",
          s["status"] == "failed" and s["verified_status"] != "verified", str(s))
    check("instruction file: the failure names the subtask",
          "Write the tests" in (m["failure_reason"] or ""), str(m["failure_reason"]))


async def case_research_leak_file():
    target = TMP / "research.md"
    target.write_text(f"Jupiter has 115 moons, per NASA.\n{RESEARCH_LEAK}\n")
    subs = [sub("Write research", "Write the research file",
                [{"kind": "create", "path": str(target), "content": None}])]
    runner, _ = make_runner([DONE], [plan_json(subs)])
    m = M.create_mission("research to file")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s = m["subtasks"][0]
    check("research leak: the mission ends failed and the subtask is not verified",
          m["state"] == "failed" and s["verified_status"] != "verified",
          f"state={m['state']} sub={s}")


async def case_not_complete():
    subs = [sub("Write the tests", "Write pytest tests for wordfreq")]
    honest = {"status": "done",
              "result": "The CLI is built, but this step is not complete: the tests were not written."}
    runner, _ = make_runner([honest], [plan_json(subs)])
    m = M.create_mission("build a wordfreq CLI with tests")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s = m["subtasks"][0]
    check("not complete: a subtask reporting incompletion cannot be verified",
          s["status"] == "failed" and s["verified_status"] != "verified", str(s))
    check("not complete: the mission ends failed naming the subtask",
          m["state"] == "failed" and "Write the tests" in (m["failure_reason"] or ""),
          f"state={m['state']} reason={m['failure_reason']}")

    subs2 = [sub("Run the tests", "Run pytest and report",
                 [{"kind": "create", "path": f"{TMP}/t.py", "content": None}]),
             sub("Sum up", "Say what happened")]
    named_failure = {"status": "done",
                     "result": "I ran pytest: the test suite failed at collection with a SyntaxError."}
    runner2, q2 = make_runner([named_failure, DONE], [plan_json(subs2)])
    m2 = M.create_mission("build and test", continue_on_failure=True)
    await runner2.drive(m2["id"])
    m2 = M.get_mission(m2["id"])
    check("not complete: keep-going still runs the rest", len(q2.created) == 2, str(len(q2.created)))
    check("not complete: keep-going mission still ends failed, count and subtask named",
          m2["state"] == "failed" and "1 of 2" in (m2["failure_reason"] or "")
          and "Run the tests" in (m2["failure_reason"] or ""),
          f"state={m2['state']} reason={m2['failure_reason']}")
    check("not complete: the clean subtask after it still verifies",
          m2["subtasks"][1]["status"] == "done" and m2["subtasks"][1]["verified_status"] == "verified",
          str(m2["subtasks"][1]))


async def case_run_effects():
    d = TMP / "runproj"
    d.mkdir(parents=True)
    script = d / "check_tests.py"
    script.write_text("import sys\nprint('2 tests failed')\nsys.exit(1)\n")
    subs = [sub("Write and run the tests", "Write the test harness and run it",
                [{"kind": "create", "path": str(script), "content": None},
                 {"kind": "run", "command": f"{sys.executable} check_tests.py",
                  "cwd": str(d), "expect_exit": 0}])]
    runner, _ = make_runner([DONE], [plan_json(subs)])
    m = M.create_mission("build with a run check that fails")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s = m["subtasks"][0]
    check("run effect: a failed declared run blocks verified and fails the mission",
          m["state"] == "failed" and s["status"] == "failed" and s["verified_status"] != "verified",
          f"state={m['state']} sub={s}")
    check("run effect: the evidence carries the run outcome",
          any("exited with code 1" in e for e in s["evidence"]), str(s["evidence"]))

    subs2 = [sub("Run the missing check", "Run the check",
                 [{"kind": "run", "command": f"{sys.executable} never_written.py",
                   "cwd": str(d), "expect_exit": 0}])]
    runner2, _ = make_runner([DONE], [plan_json(subs2)])
    m2 = M.create_mission("run check that never ran")
    await runner2.drive(m2["id"])
    m2 = M.get_mission(m2["id"])
    s2 = m2["subtasks"][0]
    check("run effect: an absent run target blocks verified and fails the mission",
          m2["state"] == "failed" and s2["verified_status"] != "verified",
          f"state={m2['state']} sub={s2}")


async def case_dup_plan_mission():
    a = sub("Build the CLI", "Build wordfreq", [{"kind": "create", "path": "/tmp/w/wordfreq.py", "content": None}])
    b = sub("Write the tests", "Write tests", [{"kind": "create", "path": "/tmp/w/test_wordfreq.py", "content": None}])
    runner, q = make_runner([DONE, DONE], [plan_json([a, b, dict(a), dict(b)])])
    m = M.create_mission("duplicated plan")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    check("dedupe: a doubled plan runs each step once and closes done",
          m["state"] == "done" and len(q.created) == 2 and len(m["subtasks"]) == 2,
          f"state={m['state']} created={len(q.created)} subs={len(m['subtasks'])}")


async def case_clean_mission():
    d = TMP / "cleanproj"
    d.mkdir(parents=True)
    proof = d / "proof.txt"
    proof.write_text("baseline proof 7731")
    script = d / "check_ok.py"
    script.write_text("print('all checks pass')\n")
    subs = [sub("Write the proof", "Write the proof file",
                [{"kind": "create", "path": str(proof), "content": "baseline proof 7731"}]),
            sub("Run the checks", "Run the checks",
                [{"kind": "create", "path": str(script), "content": None},
                 {"kind": "run", "command": f"{sys.executable} check_ok.py",
                  "cwd": str(d), "expect_exit": 0}])]
    passed = {"status": "done", "result": "Checks done: 5 passed, 0 failed, no failures."}
    runner, _ = make_runner([DONE, passed], [plan_json(subs)])
    m = M.create_mission("clean mission")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    rep = M.get_report(m["id"])
    check("clean: the mission closes done", m["state"] == "done", f"state={m['state']} reason={m['failure_reason']}")
    check("clean: every subtask is done and verified (no false positives)",
          all(s["status"] == "done" and s["verified_status"] == "verified" for s in m["subtasks"]),
          str([(s["title"], s["status"], s["verified_status"]) for s in m["subtasks"]]))
    check("clean: report totals are all verified",
          rep and rep["totals"] == {"subtasks": 2, "done": 2, "verified": 2, "failed": 0},
          str(rep and rep["totals"]))


async def main():
    case_validate_leak()
    case_validate_dedupe()
    await case_instruction_file()
    await case_research_leak_file()
    await case_not_complete()
    await case_run_effects()
    await case_dup_plan_mission()
    await case_clean_mission()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1 if failed else 0)


asyncio.run(main())
