"""AGE-40 (D23), from the Tier 2 re-grade (TIER2-REGRADE-2026-11-11.md)
on main 5700609:

  T2B's AGE-37 adaptation fired live in full: the plan of record moved
  to version 2 with an adaptations entry, the adapted step delivered
  sales-2025.csv, and the worker returned the honest total 12 naming
  the discrepancy. The step was then recorded failed / contradicted on
  the single evidence "Checked on disk: the task reads from sales.csv,
  but it is not on disk": task_queue's declared-input claim layer
  (layer 6, _claimed_input_effects) mines the task's assembled request
  for files the task reads from, and the adapted request still opens
  with the step's preserved original instruction sentence, while its
  adaptation paragraph and assembly note name the replaced file in
  read-shaped sentences ("the declared file ... could not be read",
  "The plan declared the file ... as an input, but it could not be
  read"). The layer judged the step against the file the adaptation
  replaced; step 2 was skipped and summary.txt was never created.

  The same layer correctly fails the unadapted T1N control, so the fix
  makes the layer adaptation-aware rather than weaker: the layer reads
  the adaptation record the request itself carries (the record
  missions.py writes from the plan's adaptations entry: the missing
  file, the candidate, and the delivered-instead statement) and the
  replaced file no longer counts as a declared input of the adapted
  step. The exclusion is scoped to that one recorded replacement: any
  other missing input the request names still fails, an adaptation
  marker without the full record excludes nothing, and an unadapted
  step is judged exactly as before.

  Fixing the queue layer exposed the second false contradiction the
  graded failure had masked (the task never reached the done path):
  the worker's verbatim report ends "I did not create summary.txt",
  which is true and correct, summary.txt being step 2's declared
  effect, but missions' incompletion screen read any "did not create"
  sentence as the step admitting its own incompletion. The screen now
  takes the plan's scope into account at the verdict call site: a
  create/write/run admission naming a deliverable the plan assigns to
  a sibling step is a scope statement, not an admission. Admissions
  naming the step's own effects, an unrelated file, or the step's own
  completion keep their force, and every other caller of the screen
  is unchanged.

This suite drives the graded T2B shape end to end through a queue
double whose finish verdict is the REAL queue check
(TaskQueue._verify_file_effects on the task's actual assembled
request), so the graded composition (task result tail "Not finished:
...", mission evidence "Checked on disk: ...") is reproduced, not
paraphrased. The graded plan and the worker's claimed result are
verbatim from the re-grade records, with only the scratch-root prefix
swapped for a temp root.

Run: .venv/bin/python tests/brain/run_missions_adapted_input_test.py
"""
import asyncio, csv, io, json, os, re, sys, tempfile, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_age40_test_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO UPDATE CHECK": "1", "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import app.services.missions as M  # noqa: E402
import app.services.task_queue as TQ  # noqa: E402

DBF = TMP / "xod.db"
M.DB = DBF
TQ.DB_PATH, TQ.SHOTS_DIR = DBF, TMP / "shots"
TQ.notify = lambda title, msg: None
if hasattr(TQ, "_phone"):
    TQ._phone = lambda task, kind: None
M._spawn = lambda mid: None

VERIFIER = TQ.TaskQueue()

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)

S = TMP / "xod-t2-scratch"
S.mkdir(parents=True, exist_ok=True)
(S / "sales-2025.csv").write_text("item,amount\npens,3\nbooks,4\nmugs,5\n")

T2B_GOAL = (f"Work in the folder {S}. There is a CSV file in that folder named "
            "sales.csv with columns item,count. Read it, add up the count column, "
            "and write a file named summary.txt in that folder containing exactly "
            "'Total: N' where N is the sum, and nothing else.")
# The step 1 instruction is the re-grade's plan-of-record
# original_instruction, verbatim.
T2B_PLAN = {"subtasks": [
    {"title": "Calculate total",
     "instruction": "Read sales.csv, sum the values in its count column, and report the total.",
     "expected_effects": [],
     "inputs": [{"source": "goal"}, {"source": "file", "ref": f"{S}/sales.csv"}],
     "tools": ["computer_read_file"]},
    {"title": "Write summary",
     "instruction": "Write summary.txt containing exactly 'Total: N', replacing N with the reported sum, with no trailing newline or other content.",
     "expected_effects": [{"kind": "create", "path": f"{S}/summary.txt", "content": None}],
     "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
     "tools": ["computer_write_file"]},
]}
# The re-grade's step 1 claimed result, verbatim in substance (the
# scratch-root prefix swapped).
STEP1_RESULT = (f"The total is **12**. The declared `sales.csv` could not be read. "
                f"I used `{S}/sales-2025.csv`, which has columns `item,amount` rather "
                f"than `item,count`, and summed the `amount` values. I did not create "
                f"`summary.txt`.")
MISSING_PROBLEM = "the task reads from sales.csv, but it is not on disk"

class FakeAsk:
    def __init__(self, replies): self.replies = list(replies)
    async def __call__(self, prompt): return self.replies.pop(0) if self.replies else "{}"

class VerifyQueue:
    """A queue double whose finish verdict is the real queue check:
    the worker behavior produces the step's report (and its on-disk
    effects), then TaskQueue._verify_file_effects judges the task's
    actual assembled request, and any problems end the task the way
    the real queue ends it (status failed, result tail "Not finished:
    ...")."""
    def __init__(self, behaviors):
        self.behaviors = list(behaviors); self.created = []; self.tasks = {}
    def create(self, request, title=None, origin=None):
        tid = f"task-fake{len(self.created) + 1}"
        self.created.append({"request": request, "title": title})
        self.tasks[tid] = {"request": request,
                           "behavior": self.behaviors[len(self.created) - 1],
                           "final": None}
        return {"id": tid, "status": "queued", "title": title}
    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None: return None
        if rec["final"] is None:
            b = rec["behavior"]
            out = b(rec) if callable(b) else dict(b)
            out = {"id": tid, "title": "", "request": rec["request"], **out}
            problems = VERIFIER._verify_file_effects(
                {"id": tid, "request": rec["request"]}, time.time())
            if problems:
                out["status"] = "failed"
                out["result"] = (out.get("result") or "") + \
                    f"\n\nNot finished: {'; '.join(problems)}"
            rec["final"] = out
        return rec["final"]

CAPTURED = {}

async def case_t2b_runner():
    print("== AGE-40: the T2B mission through the runner, real queue check ==", flush=True)
    def step1(rec):
        rows = list(csv.reader(io.StringIO((S / "sales-2025.csv").read_text())))
        total = sum(int(r[1]) for r in rows[1:])
        assert total == 12, total
        return {"status": "done", "result": STEP1_RESULT}
    def step2(rec):
        n = re.search(r"[Tt]otal is \*\*(\d+)\*\*", rec["request"])
        (S / "summary.txt").write_text(f"Total: {n.group(1) if n else '?'}")
        return {"status": "done", "result": "Wrote summary.txt."}
    q = VerifyQueue([step1, step2])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(T2B_PLAN)]), poll_sec=0.01)
    m = M.create_mission(T2B_GOAL)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"]); subs = m["subtasks"]
    CAPTURED["req1"] = q.created[0]["request"] if q.created else ""
    check("the adapted request carries the adaptation record (plan v2 note)",
          "adapted (plan v2)" in CAPTURED["req1"])
    check("the mission completes on the adapted step", m["state"] == "done",
          f"state={m['state']} reason={m.get('failure_reason')}")
    check("step 1 is done, not contradicted by the replaced file",
          subs and subs[0]["status"] == "done", str(subs[0].get("evidence") if subs else None))
    check("step 1's evidence does not carry the false input problem",
          subs and not any(MISSING_PROBLEM in e for e in subs[0].get("evidence") or []),
          str(subs[0].get("evidence") if subs else None))
    check("step 2 ran and is done", len(subs) > 1 and subs[1]["status"] == "done",
          str([s["status"] for s in subs]))
    check("summary.txt carries exactly 'Total: 12'",
          (S / "summary.txt").exists() and (S / "summary.txt").read_text() == "Total: 12",
          str((S / "summary.txt").read_text() if (S / "summary.txt").exists() else None))
    plan = m.get("plan") or {}
    check("the plan of record is version 2 with one adaptation recorded",
          plan.get("version") == 2 and len(plan.get("adaptations") or []) == 1)
    check("the missing file was never invented", not (S / "sales.csv").exists())

def case_layer_unit():
    print("== AGE-40: the declared-input layer on the adapted request ==", flush=True)
    req1 = CAPTURED.get("req1") or ""
    inputs = TQ._claimed_input_effects(req1)
    check("the replaced file is not a declared input of the adapted step",
          not any(p.name == "sales.csv" for p in inputs),
          str([str(p) for p in inputs]))
    problems = VERIFIER._verify_file_effects({"id": "task-unit1", "request": req1},
                                             time.time())
    check("the queue check on the adapted step's request finds no problem",
          problems == [], str(problems))
    # Scope: the exclusion covers the recorded replacement only. A
    # second, unrelated missing input named by the same request is
    # still held against the step.
    req_extra = req1 + f"\nAlso read the file {S}/other-missing-9137.txt and quote it."
    problems2 = VERIFIER._verify_file_effects({"id": "task-unit2", "request": req_extra},
                                              time.time())
    check("an unrelated missing input in the same request still fails",
          any("other-missing-9137.txt" in p for p in problems2), str(problems2))
    check("...and only that input fails (the replaced file stays excluded)",
          not any(MISSING_PROBLEM in p for p in problems2), str(problems2))
    # The record must be complete: an adaptation marker alone, with no
    # declared-file statement and no candidate delivery, excludes
    # nothing.
    ghost = (f"Assembly note: this step was adapted (plan v2) before its task "
             f"was created. Read the file {S}/ghost-9137.txt and report it.")
    problems3 = VERIFIER._verify_file_effects({"id": "task-unit3", "request": ghost},
                                              time.time())
    check("control: an adaptation marker without the full record excludes nothing",
          any("ghost-9137.txt" in p for p in problems3), str(problems3))

def case_t1n_control():
    print("== AGE-40 control: the unadapted missing input still fails (T1N) ==", flush=True)
    sub = {"title": "Read the input",
           "instruction": f"Read the file {S}/definitely-missing-input-4213.txt and report its contents.",
           "expected_effects": [],
           "contract": {"inputs": [{"source": "goal"}], "tools": ["computer_read_file"]}}
    req = M.render_request(sub)
    problems = VERIFIER._verify_file_effects({"id": "task-unit4", "request": req},
                                             time.time())
    check("the unadapted step is still failed on its missing declared input",
          problems == ["the task reads from definitely-missing-input-4213.txt, "
                       "but it is not on disk"], str(problems))

def _sig(text, exempt=None):
    try:
        return (M._incompletion_signal(text, exempt_objects=exempt)
                if exempt else M._incompletion_signal(text))
    except TypeError:
        return "NO-EXEMPTION-PARAM"

def case_screen_unit():
    print("== AGE-40: the incompletion screen and sibling deliverables ==", flush=True)
    check("the screen, unscoped, still reads the report as an admission",
          _sig(STEP1_RESULT) is not None)
    check("an admission naming a sibling step's deliverable is a scope statement",
          _sig(STEP1_RESULT, {"summary.txt"}) is None,
          str(_sig(STEP1_RESULT, {"summary.txt"})))
    check("an admission naming an unrelated file is not exempted",
          _sig(STEP1_RESULT, {"other.txt"}) is not None)
    check("a self-incompletion admission is never exempted",
          _sig("This step is not complete: the tests were not written.",
               {"summary.txt"}) is not None)

async def case_screen_control():
    print("== AGE-40 control: an unscoped 'did not create' still fails ==", flush=True)
    folder = TMP / "screenctl"; folder.mkdir(exist_ok=True)
    (folder / "data.txt").write_text("hello\n")
    plan = {"subtasks": [
        {"title": "Read the data",
         "instruction": "Read data.txt and report its first line.",
         "expected_effects": [],
         "inputs": [{"source": "goal"}, {"source": "file", "ref": f"{folder}/data.txt"}],
         "tools": ["computer_read_file"]}]}
    goal = f"Work in the folder {folder}. Read data.txt and report its first line."
    behavior = {"status": "done",
                "result": "The first line is hello. I did not create the summary."}
    q = VerifyQueue([behavior])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]), poll_sec=0.01)
    m = M.create_mission(goal)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"]); subs = m["subtasks"]
    check("control: no sibling declares the file, so the admission still fails the step",
          m["state"] == "failed" and subs and subs[0]["status"] == "failed"
          and any("admits it did not finish" in e for e in subs[0].get("evidence") or []),
          f"state={m['state']} subs={[(s['status'], s.get('evidence')) for s in subs]}")

async def case_no_candidate_control():
    print("== AGE-40 control: no candidate, honest failure preserved ==", flush=True)
    folder = TMP / "nocand"; folder.mkdir(exist_ok=True)
    (folder / "notes.txt").write_text("nothing here\n")
    goal = (f"Work in the folder {folder}. There is a CSV file named sales.csv "
            "with columns item,count. Read it and write summary.txt.")
    plan = {"subtasks": [
        {"title": "Calculate total", "instruction": "Read sales.csv, sum the count column, and report the sum.",
         "expected_effects": [], "inputs": [{"source": "goal"}, {"source": "file", "ref": f"{folder}/sales.csv"}],
         "tools": ["computer_read_file"]},
        {"title": "Write summary", "instruction": "Write summary.txt with the total.",
         "expected_effects": [{"kind": "create", "path": f"{folder}/summary.txt", "content": None}],
         "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}], "tools": ["computer_write_file"]}]}
    q = VerifyQueue([])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]), poll_sec=0.01)
    m = M.create_mission(goal)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    check("control: with no plausible candidate the mission still fails honestly",
          m["state"] == "failed" and "could not be read" in (m.get("failure_reason") or ""),
          f"state={m['state']} reason={m.get('failure_reason')}")
    check("control: no task was created and nothing was invented",
          len(q.created) == 0 and not (folder / "sales.csv").exists()
          and not (folder / "summary.txt").exists())

async def main():
    await case_t2b_runner()
    case_layer_unit()
    case_t1n_control()
    case_screen_unit()
    await case_screen_control()
    await case_no_candidate_control()
    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed ==========", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        return 1
    print("ALL AGE-40 ADAPTED-INPUT CHECKS PASSED")
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
