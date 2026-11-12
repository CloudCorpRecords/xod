"""AGE-39 (D22), from the Tier 2 grade T2C failure
(mission-ab61176b23 step 1, msub-46fd4affbb, task-eef25f4bb3):

The discovery step ("Find the matching file") called
computer_read_file on the inbox directory, listed the directory,
never read any inbox file, and reported verbatim that it could not
identify the matching file or its code. It was still recorded done /
unverifiable on absence of contradiction, and the mission consumed
the non-discovery downstream. The AGE-36 answer-content judgment is
scoped to research instructions (a source and its URL to report),
which a discovery instruction does not demand, and the incompletion
screen did not fire on this admission's wording, so a no-effect
discovery step had no answer-content judgment at all.

The fix: a no-effect step whose own instruction demands a discovery
(inspect candidate files, identify the one matching a stated
criterion, report it and the value it carries, judged on the step's
own instruction and title only) is judged on whether its claimed
result actually makes that discovery. An explicit admission of
non-identification, a listing of candidates with no identification
statement, a named file with none of the discovered value the
instruction requires, and an empty report all fail (contradicted),
the disposition the incompletion screen and AGE-36 give a report
whose content defeats verification. Blocked stays reserved for
missing input or tool reports, which the blocked screen ahead of
this judgment still decides first.

Controls pinned here: a genuine discovery (the file named and the
code reported) still records done, held out as well as graded; the
T1VC report step still fails on its honest admission; honest
non-completion still fails; a missing-tool report still records
blocked; a plain read step and a pure reasoning step are not judged
for a discovery; the AGE-36 research shapes behave exactly as
before; a producing step keeps its effect based verdict.

The graded shapes are verbatim from the Tier 2 evidence
(t2-raw.json), with only the scratch-root prefix swapped for a
temp root.

Run: .venv/bin/python tests/brain/run_missions_discovery_content_test.py
"""
import asyncio, json, os, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_discovery_content_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO_UPDATE_CHECK": "1"})
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

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)

S = TMP / "xod-t2-scratch"
(S / "inbox").mkdir(parents=True, exist_ok=True)
(S / "inbox" / "directive.txt").write_text("CODE: ZULU-9\nDirective body.\n")
(S / "inbox" / "note-morning.txt").write_text("Morning note.\n")
(S / "inbox" / "memo-supplies.txt").write_text("Supplies memo.\n")

# ---- verbatim graded texts (root prefix swapped only) --------------------
GOAL = (f"Work in the folder {S}. Inside it there is a folder named inbox "
        "holding several text files. Exactly one of them has a first line that "
        "starts with 'CODE:'. Find that file, copy its full contents into a file "
        "named chosen.txt in the working folder, and create a file named code.txt "
        "in the working folder containing only the code value that follows "
        "'CODE:' on that first line.")
PLAN = {"subtasks": [
    {"title": "Find the matching file",
     "instruction": f"List the text files in {S}/inbox and inspect their first lines. Report the absolute path of the unique file whose first line starts with 'CODE:' and the exact text following that prefix, excluding the line ending.",
     "expected_effects": [], "inputs": [{"source": "goal"}],
     "tools": ["computer_manage_file", "computer_read_file"]},
    {"title": "Copy the chosen file",
     "instruction": f"Copy the matching file identified in step 1 to {S}/chosen.txt, preserving its full contents.",
     "expected_effects": [{"kind": "create", "path": f"{S}/chosen.txt", "content": None}],
     "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
     "tools": ["computer_manage_file"]},
    {"title": "Write the code",
     "instruction": f"Write the code value reported in step 1 to {S}/code.txt, without adding any other content.",
     "expected_effects": [{"kind": "create", "path": f"{S}/code.txt", "content": None}],
     "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
     "tools": ["computer_write_file"]},
]}
STEP1_RESULT = ("I can\u2019t identify the matching file or its code: the available listing "
                "does not include the files\u2019 first lines.\n\n"
                f"The listing shows `directive.txt` and `memo-supplies.txt` in `{S}/inbox`. "
                "It reports a third file, but its name is not visible.")
LISTING_ONLY_RESULT = (f"The listing shows `directive.txt` and `memo-supplies.txt` in "
                       f"`{S}/inbox`. It reports a third file, but its name is not visible.")
GENUINE_RESULT = (f"The matching file is {S}/inbox/directive.txt. Its first line is "
                  "'CODE: ZULU-9', so the code is ZULU-9.")
FILE_ONLY_RESULT = f"The matching file is {S}/inbox/directive.txt."
HELD_SUB = {"title": "Identify the reference file",
            "instruction": f"Examine each text file in {S}/reports and identify the one whose "
                           "first line begins with 'REF:'. Report its full path and the "
                           "exact text following 'REF:'.",
            "expected_effects": [],
            "contract": {"inputs": [{"source": "goal"}],
                         "tools": ["computer_read_file"]}}
HELD_ADMISSION = ("I was unable to determine which file matches: the folder listing "
                  "does not show first lines.")
HELD_GENUINE = (f"I examined each file. The one that matches is {S}/reports/memo-q.txt, "
                "whose first line reads 'REF: KILO-4'; the reference is KILO-4.")
JUPITER_SUB = {"title": "Research Jupiter moons",
               "instruction": "Find a reliable source for Jupiter's moon count and report "
                              "the count, the date or context of the count, and the source "
                              "name and URL. Do not present an unverified count as current.",
               "expected_effects": [],
               "contract": {"inputs": [{"source": "goal"}], "tools": ["web.run"]}}
JUPITER_RESULT = ("NASA reports that Jupiter has **115 moons officially recognized by the "
                  "International Astronomical Union**. Source: [NASA Science]"
                  "(https://science.nasa.gov/jupiter/jupiter-moons/)")
EVEREST_RESULT = ("t6-load-ok\n\n**Source links**\n"
                  "1. [How Cold Is Mount Everest?](https://www.nepalintrepidtreks.com)")
T1VC_SUB = {"title": "Report the result",
            "instruction": "Report the actual pytest result honestly. If the tests failed as "
                           "expected, explain that f returns 1 while the tests require 2.",
            "expected_effects": [], "contract": {"inputs": [], "tools": []}}
T1VC_RESULT = ("The pytest run **failed as expected**: 1 test failed because `f()` returns "
               "`1`, while the test requires `2`")

def as_sub(plan_step, tools=None):
    return {"title": plan_step["title"], "instruction": plan_step["instruction"],
            "expected_effects": plan_step["expected_effects"],
            "contract": {"inputs": plan_step["inputs"],
                         "tools": plan_step["tools"] if tools is None else tools}}

def outcome(sub, result):
    return M._outcome_from_task({"status": "done", "result": result}, sub)

print("== unit: the graded T2C discovery step through the verdict ==")
out = outcome(as_sub(PLAN["subtasks"][0]), STEP1_RESULT)
check("D22 defect: the admitted non-discovery is not recorded done",
      out["status"] == "failed" and out["verified_status"] == "contradicted", str(out))
check("D22 defect: the failure evidence names that the discovery was not made",
      any("discover" in e.lower() or "did not identify" in e.lower()
          or "not identif" in e.lower() for e in out["evidence"]), str(out["evidence"]))
out = outcome(as_sub(PLAN["subtasks"][0]), LISTING_ONLY_RESULT)
check("D22 defect: a listing of candidates with no identification is not recorded done",
      out["status"] == "failed", str(out))
out = outcome(as_sub(PLAN["subtasks"][0]), FILE_ONLY_RESULT)
check("D22 defect: naming a file without the discovered value it must report is not done",
      out["status"] == "failed", str(out))
out = outcome(as_sub(PLAN["subtasks"][0]), "")
check("D22 defect: an empty report for a discovery step is not recorded done",
      out["status"] == "failed", str(out))
out = outcome(HELD_SUB, HELD_ADMISSION)
check("D22 defect: a held-out discovery admission is not recorded done",
      out["status"] == "failed", str(out))

print("== unit: controls whose dispositions must not move ==")
out = outcome(as_sub(PLAN["subtasks"][0]), GENUINE_RESULT)
check("control: a genuine discovery (file named, code reported) records done",
      out["status"] == "done", str(out))
out = outcome(HELD_SUB, HELD_GENUINE)
check("control: a held-out genuine discovery records done",
      out["status"] == "done", str(out))
out = outcome(T1VC_SUB, T1VC_RESULT)
check("control: T1VC no-effect report step still fails on its honest admission",
      out["status"] == "failed", str(out))
out = outcome(as_sub(PLAN["subtasks"][0]),
              "I was unable to complete the inspection and could not finish the report.")
check("control: honest non-completion of a discovery step still fails",
      out["status"] == "failed", str(out))
out = outcome(as_sub(PLAN["subtasks"][0]),
              "I have no tools to read files from this step, so I cannot identify the file.")
check("control: a missing-tool report still records blocked, not failed or done",
      out["status"] == "blocked", str(out))
read_step = {"title": "Read the note",
             "instruction": "Read note.txt in the proof directory and report what it "
                            "says for the next step.",
             "expected_effects": [],
             "contract": {"inputs": [{"source": "goal"}], "tools": ["computer_read_file"]}}
out = outcome(read_step, "All done.")
check("control: a plain read step is not judged for a discovery",
      out["status"] == "done", str(out))
reasoning = {"title": "Weigh the findings",
             "instruction": "Decide which of the two delivered findings is better supported "
                            "and explain the choice in one paragraph, using only the "
                            "delivered inputs.",
             "expected_effects": [], "contract": {"inputs": [{"source": "goal"}], "tools": []}}
out = outcome(reasoning, "All done.")
check("control: a pure reasoning step is not judged for a discovery",
      out["status"] == "done", str(out))
out = outcome(JUPITER_SUB, JUPITER_RESULT)
check("control: the genuine sourced research answer still records done",
      out["status"] == "done", str(out))
out = outcome(JUPITER_SUB, EVEREST_RESULT)
check("control: the research non-answer still fails under the AGE-36 judgment",
      out["status"] == "failed", str(out))
(S / "chosen.txt").write_text("CODE: ZULU-9\nDirective body.\n")
out = outcome(as_sub(PLAN["subtasks"][1],
                     tools=["computer_manage_file", "computer_write_file"]), "All done.")
check("control: a producing step keeps its effect based verdict",
      out["status"] == "done" and out["verified_status"] == "verified", str(out))
(S / "chosen.txt").unlink()

print("== end to end: the graded T2C composition through the runner ==")
class FakeAsk:
    def __init__(self, replies): self.replies = list(replies)
    async def __call__(self, prompt):
        return self.replies.pop(0) if self.replies else "{}"

class FakeQueue:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors); self.created = []; self.tasks = {}
    def create(self, request, title=None, origin=None):
        tid = f"task-fake{len(self.created) + 1}"
        self.created.append({"request": request, "title": title})
        self.tasks[tid] = {"behavior": self.behaviors[len(self.created) - 1]}
        return {"id": tid, "status": "queued", "title": title}
    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None: return None
        b = rec["behavior"]
        if callable(b): return b(rec)
        return {"id": tid, "title": "", "request": "", **b}

async def e2e():
    def copy_step(rec):
        (S / "chosen.txt").write_text("CODE: ZULU-9\nDirective body.\n")
        return {"id": "task-fake2", "title": "", "request": "", "status": "done",
                "result": "Copied the chosen file."}
    def code_step(rec):
        (S / "code.txt").write_text("ZULU-9")
        return {"id": "task-fake3", "title": "", "request": "", "status": "done",
                "result": "Wrote the code."}
    q = FakeQueue([{"status": "done", "result": STEP1_RESULT}, copy_step, code_step])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(PLAN)]), poll_sec=0.01)
    m = M.create_mission(GOAL)
    await runner.drive(m["id"])
    return M.get_mission(m["id"])

m = asyncio.run(e2e())
subs = m["subtasks"]
check("D22 defect: step 1 is not done in the mission",
      subs[0]["status"] == "failed", str(subs[0]))
check("D22 defect: the copy step does not run on the non-discovery",
      subs[1]["status"] == "skipped", str(subs[1]))
check("D22 defect: the code step does not run on the non-discovery",
      subs[2]["status"] == "skipped", str(subs[2]))
check("D22 defect: the mission fails on the non-discovery",
      m["state"] == "failed", f"state={m['state']}")
check("D22 defect: chosen.txt is not produced from the non-discovery",
      not (S / "chosen.txt").exists())
check("D22 defect: code.txt is not produced from the non-discovery",
      not (S / "code.txt").exists())

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
if failed:
    print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1)
print("ALL DISCOVERY CONTENT CHECKS PASSED", flush=True)
