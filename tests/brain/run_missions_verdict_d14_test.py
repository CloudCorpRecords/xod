#!/usr/bin/env python3
"""D14 (AGE-31): the mission verdict's incompletion prose screen
vetoed a step whose declared effects verified fresh at verdict time.
Graded live in AGE-21 re-grade run 5, T1A (mission-ff78e4c2eb, tree
b45ec29): step 3 declared a create effect for its test file and a
pinned pytest run effect. The worker's shell had no pytest (the
AGE-27 worker-layer residual), and its honest closing sentence ("No
test failures could be checked or fixed until pytest is available")
tripped the screen, which fired BEFORE the fresh declared-effect
check in both verdict paths, so the check that verified this exact
effect in run 4 never ran. Run 4 passed and run 5 failed on the
worker's wording, not the work; the deliverables were genuine.

The rule, restored to the fix-round-4 design's intent: for steps
with declared effects, the fresh declared-effect verification
decides. Worker prose is evidence about the work, never a veto over
checkable effects, and never the stated reason when the effects
themselves fail. Concretely:

- Recorded-problems path ("Not finished:" marker, declared effects
  exist): the fresh check alone composes the verdict. A step whose
  effects verify is verified whatever its report admits; a step
  whose effects fail is failed on the fresh problems, the true
  reasons. The blocked-signal screen stays first.
- Done-task path: the verdict re-executes declared run effects
  itself, and that re-execution is a positive verification prose
  cannot veto, so the screen yields for steps declaring runs. The
  screen keeps its force where the verdict has no such positive
  re-verification to stand on: steps with no declared effects, and
  steps whose declared effects are file-only (file effects in this
  path are checked types-only by the Lane B design; the queue's
  finish check owns existence), including the standing pin that a
  create-only step whose file never landed and whose report names
  a failing suite still fails.

The declared run effect here re-executes a small checker script
with the current interpreter instead of the graded pytest command:
the verdict machinery under test (fresh check, run re-execution,
screen ordering) is identical either way, and a repo suite must not
depend on pytest provisioning, which is the AGE-27 suite's own
territory. The report text is the graded verbatim closing.

Run: .venv/bin/python tests/brain/run_missions_verdict_d14_test.py
"""
import os, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_d14_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO_UPDATE_CHECK": "1"})
sys.path.insert(0, str(ROOT))

import app.services.missions as M
import app.services.task_queue as TQ

M.DB = str(TMP / "missions.db")
TQ.DB_PATH = str(TMP / "tasks.db")

failures = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


SCRATCH = TMP / "xod-t1-scratch"
TESTFILE = SCRATCH / "test_wordfreq.py"
CHECKER = SCRATCH / "check_tests.py"
CLAIMED = ("Created the test file, but could not verify the tests in my own "
           "shell: pytest is not installed in the Python environment used.\n\n"
           "No test failures could be checked or fixed until pytest is "
           "available.")
PYTEST_SENTENCE = ("No test failures could be checked or fixed until pytest "
                   "is available")

WORDFREQ = '''"""Count word frequencies in a text file."""
import re, sys
from collections import Counter


def counts(text):
    return Counter(re.findall(r"[A-Za-z']+", text.lower()))


def main(argv):
    text = Path(argv[1]).read_text() if len(argv) > 1 else sys.stdin.read()
    for word, n in counts(text).most_common(10):
        print(f"{word}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''
TESTS = '''from pathlib import Path
from wordfreq import counts


def test_counts():
    assert counts("apple banana apple") == {"apple": 2, "banana": 1}


def test_case_insensitive():
    assert counts("Apple APPLE aPpLe") == {"apple": 3}
'''
CHECKER_OK = '''from pathlib import Path
from wordfreq import counts

assert counts("apple banana apple") == {"apple": 2, "banana": 1}
assert counts("Apple APPLE") == {"apple": 2}
print("checker ok")
'''
CHECKER_BAD = '''raise SystemExit(1)
'''


def subtask():
    return {"id": "sub-d14", "title": "Test the tool",
            "instruction": ("Create runnable pytest tests in test_wordfreq.py "
                            "covering counts and case-insensitivity. Run the "
                            "tests and fix any failures before finishing."),
            "expected_effects": [
                {"kind": "create", "path": str(TESTFILE), "content": None},
                {"kind": "run", "command": f"{sys.executable} check_tests.py",
                 "cwd": str(SCRATCH), "expect_exit": 0}],
            "required_inputs": []}


def setup_scratch(checker=CHECKER_OK, with_tests=True):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    (SCRATCH / "wordfreq.py").write_text(WORDFREQ)
    if with_tests:
        TESTFILE.write_text(TESTS)
    elif TESTFILE.exists():
        TESTFILE.unlink()
    CHECKER.write_text(checker)


def main():
    # ---- recorded-problems path (the graded composition)
    setup_scratch()
    task = {"status": "failed",
            "result": CLAIMED + "\n\nNot finished: the task's own check "
                      "reported a problem with the declared run.",
            "error": ""}
    out = M._outcome_from_task(task, subtask())
    check("recorded path: effects that verify fresh are verified, whatever "
          "the prose admits",
          out["status"] == "done" and out["verified_status"] == "verified",
          str(out))

    setup_scratch(checker=CHECKER_BAD)
    out = M._outcome_from_task(task, subtask())
    check("recorded path: genuinely failing runs still fail the step",
          out["status"] == "failed" and out["verified_status"] == "contradicted",
          str(out))

    setup_scratch(with_tests=False)
    out = M._outcome_from_task(task, subtask())
    ev = " ".join(out.get("evidence") or [])
    check("recorded path: admission with false effects still fails",
          out["status"] == "failed" and out["verified_status"] == "contradicted",
          str(out))
    check("recorded path: the failure is named by the disk reason, not the "
          "prose screen",
          "Check" in ev and PYTEST_SENTENCE not in ev, ev)

    # ---- done-task path
    setup_scratch()
    task_done = {"status": "done", "result": CLAIMED, "error": ""}
    out = M._outcome_from_task(task_done, subtask())
    check("done path: the verdict follows the re-verified run effect, not "
          "the worker's prose",
          out["status"] == "done" and out["verified_status"] == "verified",
          str(out))

    setup_scratch(checker=CHECKER_BAD)
    out = M._outcome_from_task(task_done, subtask())
    check("done path: genuinely failing runs still fail the step",
          out["status"] == "failed" and out["verified_status"] == "contradicted",
          str(out))

    # ---- the screen's surviving catches
    sub_file_only = subtask()
    sub_file_only["expected_effects"] = [
        {"kind": "create", "path": str(TESTFILE), "content": None}]
    setup_scratch(with_tests=False)
    out = M._outcome_from_task({
        "status": "done",
        "result": "I ran pytest: the test suite failed at collection with "
                  "a SyntaxError.",
        "error": ""}, sub_file_only)
    check("done path: a create-only step with no landed file and a report "
          "naming a failing suite still fails",
          out["status"] == "failed", str(out))

    sub_none = subtask()
    sub_none["expected_effects"] = []
    out = M._outcome_from_task({
        "status": "done", "result": CLAIMED, "error": ""}, sub_none)
    check("done path: a step with no declared effects admitting "
          "incompletion still cannot verify",
          out["status"] == "failed", str(out))

    setup_scratch()
    out = M._outcome_from_task({
        "status": "done",
        "result": "I have no tools to create files or run commands in this "
                  "environment, so the tests cannot run.",
        "error": ""}, subtask())
    check("done path: the blocked screen still fires first",
          out["status"] == "blocked" and out["verified_status"] == "blocked",
          str(out))

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S)")
        return 1
    print("ALL D14 VERDICT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
