"""Grade fix cluster 1 (T2C read-through), from the 2026-11-13 live
grade (LIVE-GRADES-2026-11-13.md, mission-b2829363e2 step 1):

The AGE-39 fix verified live in the identification direction: step 1's
worker identified directive.txt / CODE: ZULU-9 from the listing's
first_line previews, but never called computer_read_file on the
identified file although its contract carried the tool and the step's
instruction asked for the file's full contents with line endings. It
reported the limitation and was recorded done / unverifiable, so
step 2 lacked the contents.

The fix: a discovery step whose own instruction demands the identified
file's contents is judged on the step's tool record as well as its
report. A report that names a match but shows no successful
computer_read_file of that file in the task's evidence slice (task
evidence entries, evidence_json, agent_steps, or the agent log slice
for the task) is not accepted as done; it fails contradicted with that
true reason. A worker that tried and failed to read still fails
honestly (no fabrication path is opened), and a discovery that does
not demand contents keeps its AGE-39 disposition exactly.

Run: .venv/bin/python tests/brain/run_discovery_readthrough_test.py
"""
import asyncio, json, os, sqlite3, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_discovery_readthrough_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import app.services.missions as M  # noqa: E402
import app.services.task_queue as TQ  # noqa: E402
import app.services.agent_log as AL  # noqa: E402

DBF = TMP / "xod.db"
M.DB = DBF
AL.DB = DBF
TQ.DB_PATH, TQ.SHOTS_DIR = DBF, TMP / "shots"
TQ.notify = lambda title, msg: None
if hasattr(TQ, "_phone"):
    TQ._phone = lambda task, kind: None
M._spawn = lambda mid: None

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)

S = TMP / "xod-t2c-scratch"
(S / "inbox").mkdir(parents=True, exist_ok=True)
(S / "inbox" / "directive.txt").write_text("CODE: ZULU-9\nDirective body.\nSecond line.\n")
(S / "inbox" / "note-morning.txt").write_text("Morning note.\n")
(S / "inbox" / "memo-supplies.txt").write_text("Supplies memo.\n")

CONTENTS_SUB = {"title": "Find the matching file",
                "instruction": (f"List the text files in {S}/inbox and inspect their first "
                                "lines. Report the absolute path of the unique file whose "
                                "first line starts with 'CODE:' and report its full "
                                "contents, including its line endings, for the next step."),
                "expected_effects": [],
                "contract": {"inputs": [{"source": "goal"}],
                             "tools": ["computer_list_directory", "computer_read_file"]}}
IDENTITY_SUB = {"title": "Find the matching file",
                "instruction": (f"List the text files in {S}/inbox and inspect their first "
                                "lines. Report the absolute path of the unique file whose "
                                "first line starts with 'CODE:' and the exact text "
                                "following that prefix, excluding the line ending."),
                "expected_effects": [],
                "contract": {"inputs": [{"source": "goal"}],
                             "tools": ["computer_list_directory", "computer_read_file"]}}

# The graded step-1 report (live grade, root prefix swapped only).
LIVE_RESULT = (f"The matching file is {S}/inbox/directive.txt. Its first line is "
               "CODE: ZULU-9, so the code value is ZULU-9. The rest of the file and "
               "its line endings weren't available, so I can't reproduce its "
               "complete contents.")
CONTENTS_RESULT = (f"The matching file is {S}/inbox/directive.txt. Its first line is "
                   "CODE: ZULU-9, so the code value is ZULU-9. Its full contents are: "
                   "CODE: ZULU-9 / Directive body. / Second line.")
LIST_EVIDENCE = [{"tool": "computer_list_directory", "path": str(S / "inbox")}]
READ_EVIDENCE = LIST_EVIDENCE + [{"tool": "computer_read_file",
                                  "path": str(S / "inbox" / "directive.txt")}]
FAILED_READ_EVIDENCE = LIST_EVIDENCE + [{"tool": "computer_read_file",
                                         "path": str(S / "inbox" / "directive.txt"),
                                         "success": False, "error": "permission denied"}]

def outcome(sub, result, **task_extra):
    task = {"status": "done", "result": result}
    task.update(task_extra)
    return M._outcome_from_task(task, sub)

print("== cluster 1: identification without the read is not done ==")
out = outcome(CONTENTS_SUB, LIVE_RESULT, evidence=list(LIST_EVIDENCE))
check("graded shape: listing-only evidence + contents demand fails",
      out["status"] == "failed" and out["verified_status"] == "contradicted", str(out))
check("graded shape: evidence names the missing read of the identified file",
      any("no successful read" in e for e in out["evidence"]), str(out["evidence"]))
out = outcome(CONTENTS_SUB, CONTENTS_RESULT, evidence=list(FAILED_READ_EVIDENCE))
check("a failed read is not a read: contents claimed on a failed read still fails",
      out["status"] == "failed", str(out))
out = outcome(CONTENTS_SUB, CONTENTS_RESULT,
              evidence=[{"tool": "computer_read_file",
                         "path": str(S / "inbox" / "note-morning.txt")}])
check("reading a different candidate does not satisfy the read-through",
      out["status"] == "failed", str(out))

print("== cluster 1: controls whose dispositions must not move ==")
out = outcome(CONTENTS_SUB, CONTENTS_RESULT, evidence=list(READ_EVIDENCE))
check("control: identification + successful read of the match records done",
      out["status"] == "done", str(out))
out = outcome(IDENTITY_SUB, LIVE_RESULT.replace(
    " The rest of the file and its line endings weren't available, so I can't "
    "reproduce its complete contents.", ""), evidence=list(LIST_EVIDENCE))
check("control: a discovery that demands no contents keeps the AGE-39 disposition",
      out["status"] == "done", str(out))
out = outcome(CONTENTS_SUB, CONTENTS_RESULT)
check("control: no tool record at all + contents demand fails (nothing was shown read)",
      out["status"] == "failed", str(out))

print("== cluster 1: the agent log slice is a live read record ==")
with AL._conn() as c:
    c.execute("INSERT INTO agent_steps VALUES (?,?,?,?,?,?,?,?)",
              ("step-read-1", 1.0, "task:task-logread", "main", "computer_read_file",
               "Read directive.txt", "3 lines", 1))
out = outcome(CONTENTS_SUB, CONTENTS_RESULT, id="task-logread")
check("control: a successful read in the agent log slice satisfies the read-through",
      out["status"] == "done", str(out))
out = outcome(CONTENTS_SUB, CONTENTS_RESULT, id="task-nologread")
check("an unrelated task's log slice does not leak into this step",
      out["status"] == "failed", str(out))

print("== cluster 1: end to end through the runner ==")
GOAL = (f"Work in the folder {S}. Inside it there is a folder named inbox holding "
        "several text files. Exactly one of them has a first line that starts with "
        "'CODE:'. Find that file, copy its full contents into chosen.txt, and create "
        "code.txt containing only the code value.")
PLAN = {"subtasks": [
    {"title": CONTENTS_SUB["title"], "instruction": CONTENTS_SUB["instruction"],
     "expected_effects": [], "inputs": [{"source": "goal"}],
     "tools": ["computer_list_directory", "computer_read_file"]},
    {"title": "Copy the chosen file",
     "instruction": f"Copy the matching file identified in step 1 to {S}/chosen.txt, preserving its full contents.",
     "expected_effects": [{"kind": "create", "path": f"{S}/chosen.txt", "content": None}],
     "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
     "tools": ["computer_manage_file"]},
    {"title": "Write the code",
     "instruction": f"Write the code value reported in step 1 to {S}/code.txt.",
     "expected_effects": [{"kind": "create", "path": f"{S}/code.txt", "content": None}],
     "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
     "tools": ["computer_write_file"]},
]}

class FakeAsk:
    def __init__(self, replies): self.replies = list(replies)
    async def __call__(self, prompt):
        return self.replies.pop(0) if self.replies else "{}"

class FakeQueue:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors); self.created = []; self.tasks = {}
    def create(self, request, title=None, origin=None):
        tid = f"task-rt{len(self.created) + 1}"
        self.created.append({"request": request, "title": title})
        self.tasks[tid] = {"id": tid, "behavior": self.behaviors[len(self.created) - 1]}
        return {"id": tid, "status": "queued", "title": title}
    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None: return None
        b = rec["behavior"]
        if callable(b): return b(rec)
        return {"id": tid, "title": "", "request": "", **b}

async def e2e(step1):
    def copy_step(rec):
        (S / "chosen.txt").write_text("CODE: ZULU-9\nDirective body.\nSecond line.\n")
        return {"id": rec["id"], "title": "", "request": "", "status": "done",
                "result": "Copied the chosen file."}
    def code_step(rec):
        (S / "code.txt").write_text("ZULU-9")
        return {"id": rec["id"], "title": "", "request": "", "status": "done",
                "result": "Wrote the code."}
    q = FakeQueue([step1, copy_step, code_step])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(PLAN)]), poll_sec=0.01)
    m = M.create_mission(GOAL)
    await runner.drive(m["id"])
    return M.get_mission(m["id"])

m = asyncio.run(e2e({"status": "done", "result": LIVE_RESULT,
                     "evidence": list(LIST_EVIDENCE)}))
subs = m["subtasks"]
check("e2e: step 1 fails on the missing read-through",
      subs[0]["status"] == "failed", str(subs[0]))
check("e2e: the copy and code steps do not run on an unread discovery",
      subs[1]["status"] == "skipped" and subs[2]["status"] == "skipped", str(subs))
check("e2e: the mission fails and chosen.txt is not produced",
      m["state"] == "failed" and not (S / "chosen.txt").exists(), f"state={m['state']}")

m = asyncio.run(e2e({"status": "done", "result": CONTENTS_RESULT,
                     "evidence": list(READ_EVIDENCE)}))
check("e2e control: identification + read completes the mission",
      m["state"] == "done" and (S / "chosen.txt").exists() and (S / "code.txt").exists(),
      f"state={m['state']} reason={m.get('failure_reason')}")

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
if failed:
    print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1)
print("ALL DISCOVERY READ-THROUGH CHECKS PASSED", flush=True)
