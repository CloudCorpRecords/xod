"""
Missions fix round 4 (Lane 4): verdict re-checks, the T1A test artifact,
and T1D write tools.

Regression suite for the 2026-10-31 Tier 1 re-run 3 record
(ESCALATION-T1-RERUN3-2026-10-31.md, wobbles 5 and 6):

  - T1A (mission-0120cf5da6): the plan rows were doubled (six subtask
    rows for a three step plan, two paraphrased copies interleaved by
    idx), step 2 was contradicted with "the task asked for sample.txt`
    to be created, but it is not on disk" (note the backtick: the path
    was extracted from the previous step's backticked report, which the
    round 3 carry-forward had pasted into this step's request), the
    mission stopped on that stale reason, and the test step was never
    dispatched: test_wordfreq.py was absent entirely. wordfreq.py and
    sample.txt were on disk at verdict time.
  - T1D (mission-a858614482): the write subtask reported it had no
    tools to create files ("I can't create or verify files on disk
    with the tools available to me"), and note.txt never landed. A
    mission task is created with queue.create(request, title) only:
    no tool set is ever specified, checked, or guaranteed for a write
    step anywhere on the path MissionRunner to TaskQueue to the
    researcher and worker lanes.

Covered here:
  - a verdict reason is composed from a fresh disk check at verdict
    time: a file that exists when the verdict is composed is never
    named as missing, at subtask outcome time and again at mission
    finish time (a parallel duplicate landing the files later).
  - two drivers planning the same mission at the same time produce
    one plan and one set of subtask rows, not a doubled plan.
  - a T1A-shaped mission end to end through a queue that runs the
    real file-effect gate: wordfreq.py, sample.txt and
    test_wordfreq.py all land, the test file holds real pytest tests,
    and python3 -m pytest --collect-only on it succeeds.
  - a T1D-shaped mission end to end: the write step runs with the
    real file tools and note.txt lands with exact bytes.
  - a write step that cannot be equipped with file tools fails at
    assembly time, loudly, before any task is created.

The GateQueue below mimics the real TaskQueue's finish path: the
scripted worker does its work, then the real
TaskQueue._verify_file_effects decides done versus failed from the
request text and the disk. Run commands and pytest use the system
python3 (which has pytest on this machine), as production plans do.

Run: .venv/bin/python tests/brain/run_missions_fix4_test.py
"""
import asyncio, json, os, subprocess, sys, tempfile, threading, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_missions_fix4_"))
os.environ["XOD_REAL_HOME"] = os.environ.get("HOME", "")
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
# python3 in this suite means the macOS python3 at /usr/bin (the one with
# pytest installed, and the one production plans invoke). A non-login shell
# on this machine can otherwise resolve python3 to the Xcode developer
# stub, which has no pytest and would fail the run for a PATH reason that
# says nothing about the code under test.
os.environ["PATH"] = "/usr/bin:" + os.environ.get("PATH", "")
# The same python3 finds pytest in the real account's user site-packages;
# this suite fakes HOME (above) to isolate the app database, which would
# hide them, so point PYTHONUSERBASE at the real one for the suite's
# subprocesses. The repo venv running the suite itself ignores user site.
for _cand in sorted((Path(os.environ.get("XOD_REAL_HOME", "")) / "Library" / "Python").glob("*")):
    if (_cand / "lib" / "python" / "site-packages" / "pytest").is_dir():
        os.environ["PYTHONUSERBASE"] = str(_cand)
        break
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.missions as M
import app.services.task_queue as TQ
from app.agent.tools import execute_tool as _execute_tool

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


class SlowAsk:
    """A planner that takes a moment, so two drivers overlap in planning."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        await asyncio.sleep(0.05)
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


class GateQueue:
    """The real gate, a scripted worker. create() runs the worker for
    that step (it does the step's file work, as a tooled worker would),
    and get() decides the task's fate with the real
    TaskQueue._verify_file_effects over the assembled request and the
    disk, returning the same Not finished shape the real queue writes."""

    def __init__(self, workers, reports=None):
        self.workers = list(workers)
        self.reports = list(reports or [])
        self.created = []
        self.tasks = {}
        self._gate = TQ.TaskQueue.__new__(TQ.TaskQueue)

    def create(self, request, title=None):
        tid = f"task-gate{len(self.created) + 1}"
        self.created.append({"request": request, "title": title})
        idx = len(self.created) - 1
        if idx < len(self.workers) and self.workers[idx] is not None:
            self.workers[idx](request)
        self.tasks[tid] = {"request": request, "idx": idx}
        return {"id": tid, "status": "queued", "title": title}

    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None:
            return None
        problems = self._gate._verify_file_effects({"id": tid, "request": rec["request"]}, time.time())
        report = self.reports[rec["idx"]] if rec["idx"] < len(self.reports) else "All done."
        if problems:
            return {"id": tid, "status": "failed",
                    "result": f"{report}\n\nNot finished: {'; '.join(problems)}"}
        return {"id": tid, "status": "done", "result": report}


DONE = {"status": "done", "result": "All done."}


def plan_json(subs):
    return json.dumps({"subtasks": subs})


def sub(title, instruction, effects=None):
    return {"title": title, "instruction": instruction, "expected_effects": effects or []}


def make_runner(behaviors, replies, **kw):
    q = FakeQueue(behaviors)
    return M.MissionRunner(queue=q, ask=FakeAsk(replies), poll_sec=0.01), q


def run_tool(name, args):
    """Runs one real agent tool to completion from synchronous test code."""
    out = {}

    def _go():
        try:
            out["r"] = asyncio.run(_execute_tool(name, args))
        except Exception as e:   # surfaced to the caller below
            out["e"] = e

    t = threading.Thread(target=_go)
    t.start()
    t.join()
    if "e" in out:
        raise out["e"]
    return out["r"]


# ------------------------------------------------- (a) stale verdict reasons
async def case_stale_outcome():
    """The task's own check ran while a parallel step was mid-flight and
    recorded sample.txt as missing; the file lands before the mission
    composes its verdict, so the verdict must not name it missing."""
    d = TMP / "stale"
    d.mkdir(parents=True)
    sample, wf = d / "sample.txt", d / "wordfreq.py"

    def landing(rec):
        sample.write_text("the cat and the dog\n")
        wf.write_text("import sys\nprint('the: 1')\n")
        return {"id": "task-fake1", "status": "failed",
                "result": "I made the files.\n\nNot finished: the task asked for "
                          "sample.txt to be created, but it is not on disk"}

    subs = [sub("Build the files", "Create sample.txt and wordfreq.py",
                [{"kind": "create", "path": str(sample), "content": None},
                 {"kind": "create", "path": str(wf), "content": None}])]
    runner, _ = make_runner([landing], [plan_json(subs)])
    m = M.create_mission("stale verdict shape")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s = m["subtasks"][0]
    check("stale outcome: the mission closes done when the declared effects are true at verdict time",
          m["state"] == "done", f"state={m['state']} reason={m['failure_reason']}")
    check("stale outcome: the subtask is verified, not contradicted by the stale check",
          s["status"] == "done" and s["verified_status"] == "verified", str(s))
    check("stale outcome: no stated reason names an existing file as missing",
          "not on disk" not in (m["failure_reason"] or "")
          and all("not on disk" not in e for e in s["evidence"]),
          f"reason={m['failure_reason']} evidence={s['evidence']}")


async def case_doubled_verdict():
    """A doubled plan: two parallel copies of the same create step. The
    first copy's check runs before the files land and fails it; the
    second copy lands them. At mission finish (verdict time) the first
    copy's recorded reason is stale and must not stand."""
    d = TMP / "doubled"
    d.mkdir(parents=True)
    sample, wf = d / "sample.txt", d / "wordfreq.py"
    effects = [{"kind": "create", "path": str(sample), "content": None},
               {"kind": "create", "path": str(wf), "content": None}]
    stale = {"status": "failed",
             "result": "I made the files.\n\nNot finished: the task asked for sample.txt "
                       "to be created, but it is not on disk; the task asked for "
                       "wordfreq.py to be created, but it is not on disk"}

    def land_and_done(rec):
        sample.write_text("the cat and the dog\n")
        wf.write_text("import sys\nprint('the: 1')\n")
        return {"id": "task-fake2", **DONE}

    subs = [sub("Build the files", "Create sample.txt and wordfreq.py", effects),
            sub("Build the files (second copy)", "Create sample.txt and wordfreq.py", effects)]
    runner, q = make_runner([stale, land_and_done], [plan_json(subs)])
    m = M.create_mission("doubled plan verdict", continue_on_failure=True)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    rep = M.get_report(m["id"])
    all_evidence = [e for s in rep["subtasks"] for e in s["evidence"]]
    check("doubled verdict: both copies ran", len(q.created) == 2, str(len(q.created)))
    check("doubled verdict: the files landed", sample.is_file() and wf.is_file())
    check("doubled verdict: the mission closes done, every declared effect true at verdict time",
          m["state"] == "done", f"state={m['state']} reason={m['failure_reason']}")
    check("doubled verdict: the first copy is not left contradicted over files that exist",
          m["subtasks"][0]["status"] == "done" and m["subtasks"][0]["verified_status"] == "verified",
          str(m["subtasks"][0]))
    check("doubled verdict: no stated reason names an existing file as missing",
          "not on disk" not in (m["failure_reason"] or "")
          and all("not on disk" not in e for e in all_evidence),
          f"reason={m['failure_reason']} evidence={all_evidence}")


# ------------------------------------------------- (b) the doubled plan itself
async def case_concurrent_plan():
    """Two drivers drive the same mission at once (the production shape
    behind the six row plans: two processes share one database, each
    with its own driver loop). Planning must happen once."""
    d = TMP / "race"
    d.mkdir(parents=True)

    def plan_with(tag):
        return [sub(f"Step one {tag}", f"Do the first thing {tag}",
                    [{"kind": "create", "path": str(d / "a.txt"), "content": None}]),
                sub(f"Step two {tag}", f"Do the second thing {tag}",
                    [{"kind": "create", "path": str(d / "b.txt"), "content": None}]),
                sub(f"Step three {tag}", f"Do the third thing {tag}",
                    [{"kind": "create", "path": str(d / "c.txt"), "content": None}])]

    ask = SlowAsk([plan_json(plan_with("alpha")), plan_json(plan_with("beta"))])
    q = FakeQueue([DONE] * 6)
    r1 = M.MissionRunner(queue=q, ask=ask, poll_sec=0.01)
    r2 = M.MissionRunner(queue=q, ask=ask, poll_sec=0.01)
    m = M.create_mission("raced planning")
    await asyncio.gather(r1.drive(m["id"]), r2.drive(m["id"]))
    m = M.get_mission(m["id"])
    check("concurrent plan: one plan's worth of subtask rows exists, not a doubled plan",
          len(m["subtasks"]) == 3, f"subtasks={len(m['subtasks'])}")
    check("concurrent plan: each step was dispatched once",
          len(q.created) == 3, f"created={len(q.created)}")
    check("concurrent plan: the mission closes done",
          m["state"] == "done", f"state={m['state']} reason={m['failure_reason']}")


# ------------------------------------------------- (b) T1A end to end
SAMPLE_TEXT = ("the quick brown fox and the lazy dog\n"
               "the dog and the fox looked on\n"
               "a bright day and a quiet path\n")

WORDFREQ_PY = '''import re
import sys
from collections import Counter


def main():
    text = open(sys.argv[1], encoding="utf-8").read()
    counts = Counter(w.lower() for w in re.findall(r"[A-Za-z]+", text))
    for word, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]:
        print(f"{word}: {n}")


if __name__ == "__main__":
    main()
'''

TEST_WORDFREQ_PY = '''import subprocess
import sys


def run_tool(tmp_path, text):
    src = tmp_path / "input.txt"
    src.write_text(text, encoding="utf-8")
    out = subprocess.run([sys.executable, "wordfreq.py", str(src)],
                         capture_output=True, text=True)
    assert out.returncode == 0
    return out.stdout


def test_counts_and_format(tmp_path):
    assert run_tool(tmp_path, "the cat and the dog\\n") == "the: 2\\nand: 1\\ncat: 1\\ndog: 1\\n"


def test_case_insensitive(tmp_path):
    assert run_tool(tmp_path, "Dog DOG dog\\n") == "dog: 3\\n"


def test_tie_order_alphabetical(tmp_path):
    assert run_tool(tmp_path, "banana apple cherry\\n") == "apple: 1\\nbanana: 1\\ncherry: 1\\n"


def test_ten_word_limit(tmp_path):
    words = " ".join(chr(ord("a") + i) for i in range(15))
    lines = run_tool(tmp_path, words).strip().splitlines()
    assert len(lines) == 10
'''


async def case_t1a_end_to_end():
    d = TMP / "t1a"
    d.mkdir(parents=True)
    subs = [
        sub("Create sample text",
            f"Create sample.txt in {d} with a few paragraphs of text containing repeated words.",
            [{"kind": "create", "path": str(d / "sample.txt"), "content": None}]),
        sub("Build and run the tool",
            "Create wordfreq.py to read the file named by its command-line argument and "
            "print at most the 10 most frequent words as 'word: count', one per line. "
            "Count words case-insensitively and use a deterministic order for ties, "
            "then run it on sample.txt.",
            [{"kind": "create", "path": str(d / "wordfreq.py"), "content": None},
             {"kind": "run", "command": "python3 wordfreq.py sample.txt",
              "cwd": str(d), "expect_exit": 0}]),
        sub("Test the tool",
            "Create runnable pytest tests in test_wordfreq.py covering the command-line "
            "output, case-insensitive counting, punctuation, tie ordering, and the "
            "10-word limit. Run the tests and fix any failures.",
            [{"kind": "create", "path": str(d / "test_wordfreq.py"), "content": None},
             {"kind": "run", "command": "python3 -m pytest test_wordfreq.py",
              "cwd": str(d), "expect_exit": 0}]),
    ]
    workers = [lambda req: (d / "sample.txt").write_text(SAMPLE_TEXT),
               lambda req: (d / "wordfreq.py").write_text(WORDFREQ_PY),
               lambda req: (d / "test_wordfreq.py").write_text(TEST_WORDFREQ_PY)]
    reports = [f"Created `{d}/sample.txt` (5 lines, 366 bytes).",
               "Confirmed the tool works and printed the top words.",
               "All done."]
    q = GateQueue(workers, reports)
    runner = M.MissionRunner(queue=q, ask=FakeAsk([plan_json(subs)]), poll_sec=0.01)
    m = M.create_mission("build a wordfreq CLI with tests")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    testfile = d / "test_wordfreq.py"
    check("T1A: every step was dispatched, the test step included",
          len(q.created) == 3, f"created={len(q.created)}")
    check("T1A: the mission closes done",
          m["state"] == "done", f"state={m['state']} reason={m['failure_reason']}")
    check("T1A: wordfreq.py and sample.txt landed",
          (d / "wordfreq.py").is_file() and (d / "sample.txt").is_file())
    check("T1A: test_wordfreq.py landed", testfile.is_file())
    body = testfile.read_text() if testfile.is_file() else ""
    check("T1A: the test file holds real test functions, not instruction text",
          "def test_" in body and "assert" in body and "subprocess" in body,
          body[:200])
    if testfile.is_file():
        proc = subprocess.run(["python3", "-m", "pytest", "--collect-only", "-q", str(testfile)],
                              cwd=str(d), capture_output=True, text=True, timeout=120)
        check("T1A: python3 -m pytest --collect-only succeeds on the produced tests",
              proc.returncode == 0 and "test_" in proc.stdout,
              f"rc={proc.returncode} out={proc.stdout[-300:]} err={proc.stderr[-300:]}")
    else:
        check("T1A: python3 -m pytest --collect-only succeeds on the produced tests",
              False, "the test file is absent, so there is nothing to collect")


# ------------------------------------------------- (c) T1D end to end
async def case_t1d_end_to_end():
    d = TMP / "t1d"
    d.mkdir(parents=True)
    proofdir, note = d / "proofdir", d / "proofdir" / "note.txt"
    subs = [
        sub("Create the folder", f"Create {proofdir}.",
            [{"kind": "create", "path": str(proofdir), "content": None}]),
        sub("Write the note",
            "Create note.txt inside proofdir with exactly the text baseline proof 7731 "
            "and no trailing newline.",
            [{"kind": "create", "path": str(note), "content": "baseline proof 7731"}]),
        sub("Read and report", f"Read {note} and report its contents."),
    ]
    workers = [lambda req: run_tool("computer_manage_file", {"action": "mkdir", "src": str(proofdir)}),
               lambda req: run_tool("computer_write_file",
                                    {"filepath": str(note), "content": "baseline proof 7731"}),
               None]
    reports = [f"Created `{proofdir}`.", "Created the note.", "Read it back: baseline proof 7731."]
    q = GateQueue(workers, reports)
    runner = M.MissionRunner(queue=q, ask=FakeAsk([plan_json(subs)]), poll_sec=0.01)
    m = M.create_mission("make a proof folder and note")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    check("T1D: the mission closes done",
          m["state"] == "done", f"state={m['state']} reason={m['failure_reason']}")
    check("T1D: every step was dispatched", len(q.created) == 3, f"created={len(q.created)}")
    check("T1D: proofdir exists and note.txt landed with exact bytes",
          proofdir.is_dir() and note.is_file() and note.read_bytes() == b"baseline proof 7731",
          f"dir={proofdir.is_dir()} note={note.is_file()} "
          f"bytes={note.read_bytes() if note.is_file() else None}")
    have_check = hasattr(M, "_write_tool_problem") and hasattr(M, "_available_tool_names")
    check("T1D: the missions layer checks a write step's tools at assembly time", have_check)
    if have_check:
        s2 = m["subtasks"][1]
        req = M.assemble_request(m["id"], s2)
        check("T1D: the write step's assembled request can be equipped with file tools",
              M._write_tool_problem(s2, req) is None,
              str(M._write_tool_problem(s2, req)))


async def case_no_write_tools():
    """Simulated at the missions layer: the tool selector offers a write
    step no file tool. Assembly must fail loudly, naming the step and
    the tool problem, before any task is created."""
    have_check = hasattr(M, "_write_tool_problem") and hasattr(M, "_available_tool_names")
    check("assembly: a write-tool check exists at the missions layer", have_check)
    if not have_check:
        return
    orig = M._available_tool_names
    M._available_tool_names = lambda request: ["computer_read_file", "computer_list_directory"]
    try:
        target = TMP / "notools" / "note.txt"
        subs = [sub("Write the note", "Create note.txt with the proof text",
                    [{"kind": "create", "path": str(target), "content": "baseline proof 7731"}])]
        runner, q = make_runner([DONE], [plan_json(subs)])
        m = M.create_mission("write a note with no tools available")
        await runner.drive(m["id"])
        m = M.get_mission(m["id"])
        s = m["subtasks"][0]
        check("assembly: no task was created for the unequippable write step",
              len(q.created) == 0, f"created={len(q.created)}")
        check("assembly: the mission fails and names the step",
              m["state"] == "failed" and "Write the note" in (m["failure_reason"] or ""),
              f"state={m['state']} reason={m['failure_reason']}")
        check("assembly: the failure names the tool problem",
              "tool" in (m["failure_reason"] or "").lower()
              and any("tool" in e.lower() for e in s["evidence"]),
              f"reason={m['failure_reason']} evidence={s['evidence']}")
        check("assembly: no file landed", not target.exists())
    finally:
        M._available_tool_names = orig


async def main():
    await case_stale_outcome()
    await case_doubled_verdict()
    await case_concurrent_plan()
    await case_t1a_end_to_end()
    await case_t1d_end_to_end()
    await case_no_write_tools()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1 if failed else 0)


asyncio.run(main())
