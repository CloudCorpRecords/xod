"""AGE-37 (D20) and AGE-38 (D21), from the Tier 2 grade
(TIER2-GRADE-2026-11-11.md, evidence t2-raw.json) on the v1.1.3 tree:

  - D20: the plan declared the goal-named sales.csv as a file input;
    assembly stopped the step before any task was created because
    the file could not be read, and the stop was terminal, although
    sales-2025.csv (item,amount, total 12) sat in the same folder.
    The fix gives assembly one bounded, evidence based adaptation:
    exactly one plausible candidate in the same folder (same type,
    the declared stem or that stem extended at a name boundary) is
    delivered instead, the discrepancy is named in the step's
    instruction and request, and the change is recorded as plan
    version 2 in the mission's plan of record. No candidate, or an
    ambiguous set, still fails honestly, and the missing file is
    never invented.
  - D21: the plan paired a create effect for chosen.txt with a
    contract of computer_manage_file only, which cannot write it;
    assembly stopped the step and the AGE-32 dispatch repair by
    design left file-effect steps untouched. The repair now covers
    that shape: the canonical serving tool from the _effect_tools
    mapping is added, recorded in the saved contract and as a
    verbatim repair note in the assembled request, exactly as the
    AGE-32 repair records its repairs. Already-serving contracts,
    steps declaring no contract, and the research and run repairs
    behave exactly as before.

The graded plans and assembly texts are verbatim from the grade
records, with only the scratch-root prefix swapped for a temp root.
The T2C inbox contents beyond the graded first line (CODE: ZULU-9)
are test fixture.

Run: .venv/bin/python tests/brain/run_missions_tier2_adapt_test.py
"""
import asyncio, csv, io, json, os, re, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_t2adapt_test_"))
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
S.mkdir(parents=True, exist_ok=True)
(S / "sales-2025.csv").write_text("item,amount\npens,3\nbooks,4\nmugs,5\n")

# ---- verbatim graded plans (root prefix swapped only) -------------------
T2B_GOAL = (f"Work in the folder {S}. There is a CSV file in that folder named "
            "sales.csv with columns item,count. Read it, add up the count column, "
            "and write a file named summary.txt in that folder containing exactly "
            "'Total: N' where N is the sum, and nothing else.")
T2B_PLAN = {"subtasks": [
    {"title": "Calculate total",
     "instruction": "Read sales.csv, sum the values in its count column, and report the sum.",
     "expected_effects": [],
     "inputs": [{"source": "goal"}, {"source": "file", "ref": f"{S}/sales.csv"}],
     "tools": ["computer_read_file"]},
    {"title": "Write summary",
     "instruction": "Write summary.txt containing exactly 'Total: N', replacing N with the reported sum, with no trailing newline or other content.",
     "expected_effects": [{"kind": "create", "path": f"{S}/summary.txt", "content": None}],
     "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
     "tools": ["computer_write_file"]},
]}
T2C_GOAL = (f"Work in the folder {S}. Inside it there is a folder named inbox "
            "holding several text files. Exactly one of them has a first line that "
            "starts with 'CODE:'. Find that file, copy its full contents into a file "
            "named chosen.txt in the working folder, and create a file named code.txt "
            "in the working folder containing only the code value that follows "
            "'CODE:' on that first line.")
T2C_PLAN = {"subtasks": [
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

def as_subtask(plan_step):
    return {"title": plan_step["title"], "instruction": plan_step["instruction"],
            "expected_effects": plan_step["expected_effects"],
            "contract": {"inputs": plan_step["inputs"], "tools": plan_step["tools"]}}

class FakeAsk:
    def __init__(self, replies): self.replies = list(replies)
    async def __call__(self, prompt): return self.replies.pop(0) if self.replies else "{}"

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

# ------------------------------------------------------------------ AGE-38 unit
def case_age38_unit():
    print("== AGE-38: file-effect contract repair, unit level ==", flush=True)
    step = as_subtask(T2C_PLAN["subtasks"][1])
    prob = M._contract_tool_problem(step)
    check("base gate text reproduced: the unrepaired contract stops step 2 at assembly",
          prob is not None and "computer_manage_file" in prob
          and "computer_write_file, fs_write_workspace_file" in prob, str(prob))
    if not hasattr(M, "_capability_contract_repair"):
        check("file-effect contract is repaired", False, "no repair function")
        return
    r = M._capability_contract_repair(step)
    check("file-effect contract is repaired with the canonical write tool",
          r is not None and r[0] == ["computer_manage_file", "computer_write_file"], str(r))
    check("the repair note names the declared tools, the effect and the addition",
          r is not None and "computer_manage_file" in r[1] and "chosen.txt" in r[1]
          and "Added to the contract: computer_write_file" in r[1], str(r and r[1]))
    if r:
        fixed_step = as_subtask(T2C_PLAN["subtasks"][1])
        fixed_step["contract"] = {"inputs": fixed_step["contract"]["inputs"], "tools": r[0]}
        check("the repaired contract passes the contract gate",
              M._contract_tool_problem(fixed_step) is None)
    serving = as_subtask(T2C_PLAN["subtasks"][1]); serving["contract"]["tools"] = ["computer_write_file"]
    check("control: an already-serving file-effect contract is untouched",
          M._capability_contract_repair(serving) is None)
    noc = as_subtask(T2C_PLAN["subtasks"][1]); noc["contract"]["tools"] = None
    check("control: a step declaring no contract is untouched",
          M._capability_contract_repair(noc) is None)
    edit = {"title": "Edit a file", "instruction": "Edit the file in place.",
            "expected_effects": [{"kind": "modify", "path": f"{S}/x.txt", "content": None}],
            "contract": {"inputs": [], "tools": ["computer_edit_file"]}}
    check("control: a modify effect served by the edit tool is untouched",
          M._capability_contract_repair(edit) is None)
    research = {"title": "Research a fact",
                "instruction": "Find a reliable source for the fact and report it with the source name and URL.",
                "expected_effects": [], "contract": {"inputs": [], "tools": []}}
    rr = M._capability_contract_repair(research)
    check("control: the AGE-32 research repair behaves exactly as before",
          rr is not None and rr[0] == ["web.run"], str(rr))
    runstep = {"title": "Run the checks", "instruction": "Run the check script and report its exit code.",
               "expected_effects": [{"kind": "run", "command": "make check", "cwd": str(S), "expect_exit": 0}],
               "contract": {"inputs": [], "tools": []}}
    ru = M._capability_contract_repair(runstep)
    check("control: the AGE-32 run repair behaves exactly as before",
          ru is not None and ru[0] == ["computer_run_command"], str(ru))

# ------------------------------------------------------------------ AGE-37 unit
def case_age37_unit():
    print("== AGE-37: file-input adaptation, unit level ==", flush=True)
    if not hasattr(M, "_file_input_adaptation"):
        for n in ("adaptation is offered for the T2B step",
                  "the candidate delivered is sales-2025.csv from the same folder",
                  "the adapted instruction names the discrepancy and the actual columns",
                  "the assembly note is evidence based (folder contents, first line)"):
            check(n, False, "no adaptation function on this tree")
    else:
        a = M._file_input_adaptation(as_subtask(T2B_PLAN["subtasks"][0]))
        check("adaptation is offered for the T2B step", a is not None)
        if a:
            adapted, note, record = a
            refs = [d.get("ref") for d in adapted["contract"]["inputs"] if d.get("source") == "file"]
            check("the candidate delivered is sales-2025.csv from the same folder",
                  refs == [f"{S}/sales-2025.csv"], str(refs))
            check("the adapted instruction names the discrepancy and the actual columns",
                  "sales.csv" in adapted["instruction"] and "sales-2025.csv" in adapted["instruction"]
                  and "item,amount" in adapted["instruction"], adapted["instruction"][:400])
            check("the assembly note is evidence based (folder contents, first line)",
                  "sales-2025.csv" in note and "item,amount" in note and "not invented" in note, note[:300])
            check("the adaptation record holds the original and the evidence",
                  record.get("missing") == f"{S}/sales.csv"
                  and record.get("candidate") == f"{S}/sales-2025.csv"
                  and "sales-2025.csv" in (record.get("folder_contents") or []), str(record))
    # Negative controls run on both trees when the helper exists; on base
    # they are recorded as passing controls (no adaptation exists there
    # either, which is the defect, counted above).
    if hasattr(M, "_file_input_adaptation"):
        empty = TMP / "empty-folder"; empty.mkdir(exist_ok=True)
        (empty / "notes.txt").write_text("hello\n")
        st = {"title": "Read data", "instruction": "Read data.csv and report the total.",
              "expected_effects": [],
              "contract": {"inputs": [{"source": "file", "ref": f"{empty}/data.csv"}], "tools": ["computer_read_file"]}}
        check("control: no plausible candidate in the folder, no adaptation",
              M._file_input_adaptation(st) is None)
        amb = TMP / "amb-folder"; amb.mkdir(exist_ok=True)
        (amb / "sales-2024.csv").write_text("item,amount\na,1\n")
        (amb / "sales-2025.csv").write_text("item,amount\nb,2\n")
        st2 = {"title": "Read data", "instruction": "Read sales.csv and report the total.",
               "expected_effects": [],
               "contract": {"inputs": [{"source": "file", "ref": f"{amb}/sales.csv"}], "tools": ["computer_read_file"]}}
        check("control: two plausible candidates are ambiguous, no adaptation",
              M._file_input_adaptation(st2) is None)
        st3 = as_subtask(T2B_PLAN["subtasks"][0])
        st3["contract"] = {"inputs": [{"source": "file", "ref": f"{S}/sales-2025.csv"}], "tools": ["computer_read_file"]}
        check("control: a deliverable file input is not adapted",
              M._file_input_adaptation(st3) is None)

# ------------------------------------------------------------------ runners
async def case_t2b_runner():
    print("== AGE-37: the T2B mission through the runner ==", flush=True)
    def step1(rec):
        req = rec_request(rec)
        rows = list(csv.reader(io.StringIO("item,amount\npens,3\nbooks,4\nmugs,5\n")))
        total = sum(int(r[1]) for r in rows[1:])
        return {"id": rec_id(rec), "title": "", "request": "",
                "status": "done",
                "result": f"The declared file sales.csv was not in the folder, so I used sales-2025.csv (columns item,amount). The total is {total}."}
    def step2(rec):
        req = rec_request(rec)
        n = re.search(r"[Tt]otal is (\d+)", req)
        (S / "summary.txt").write_text(f"Total: {n.group(1) if n else '?'}")
        return {"id": rec_id(rec), "title": "", "request": "", "status": "done", "result": "Wrote summary.txt."}
    # FakeQueue behaviors receive the task record only; patch via closure below.
    q = FakeQueue([step1, step2])
    orig_create = q.create
    def create(request, title=None, origin=None):
        out = orig_create(request, title=title, origin=origin)
        q.tasks[out["id"]]["request"] = request
        return out
    q.create = create
    def get(tid):
        rec = q.tasks.get(tid)
        if rec is None: return None
        b = rec["behavior"]
        if callable(b): return b({**rec, "id": tid, "request": rec.get("request", "")})
        return {"id": tid, "title": "", "request": "", **b}
    q.get = get
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(T2B_PLAN)]), poll_sec=0.01)
    m = M.create_mission(T2B_GOAL)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"]); subs = m["subtasks"]
    check("the mission completes on the adapted step", m["state"] == "done", f"state={m['state']} reason={m.get('failure_reason')}")
    check("step 1 is done, not assembly-failed", subs[0]["status"] == "done", str(subs[0].get("evidence")))
    check("summary.txt carries the honest total from the candidate file",
          (S / "summary.txt").exists() and (S / "summary.txt").read_text() == "Total: 12",
          str((S / "summary.txt").read_text() if (S / "summary.txt").exists() else None))
    plan = m.get("plan") or {}
    check("the plan of record moved to version 2 with one adaptation recorded",
          plan.get("version") == 2 and len(plan.get("adaptations") or []) == 1, str(plan.get("version")) + " " + str(plan.get("adaptations")))
    check("step 1's saved contract delivers the candidate, not the missing file",
          any(d.get("ref") == f"{S}/sales-2025.csv" for d in (subs[0]["contract"] or {}).get("inputs") or []),
          str(subs[0]["contract"]))
    check("step 1's input delivery is the candidate file",
          any(d.get("ref") == f"{S}/sales-2025.csv" for d in subs[0].get("input_delivery") or []),
          str(subs[0].get("input_delivery")))
    check("the adapted request carries the assembly adaptation note",
          len(q.created) >= 1 and "adapted (plan v2)" in q.created[0]["request"])
    check("the missing file was never invented", not (S / "sales.csv").exists())

def rec_request(rec): return rec.get("request", "")
def rec_id(rec): return rec.get("id", "task-fake")

async def case_t2b_no_candidate():
    print("== AGE-37 control: no candidate, honest failure ==", flush=True)
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
    q = FakeQueue([])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]), poll_sec=0.01)
    m = M.create_mission(goal)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    check("control: with no plausible candidate the mission still fails honestly",
          m["state"] == "failed" and "could not be read" in (m.get("failure_reason") or ""),
          f"state={m['state']} reason={m.get('failure_reason')}")
    check("control: no task was created and nothing was invented",
          len(q.created) == 0 and not (folder / "sales.csv").exists() and not (folder / "summary.txt").exists())

async def case_t2c_runner():
    print("== AGE-38: the T2C mission through the runner ==", flush=True)
    inbox = S / "inbox"; inbox.mkdir(exist_ok=True)
    (inbox / "directive.txt").write_text("CODE: ZULU-9\nFollow the river road.\n")
    (inbox / "note-morning.txt").write_text("Morning notes.\n")
    (inbox / "memo-supplies.txt").write_text("Supplies memo.\n")
    s1 = {"status": "done", "result": f"The matching file is {S}/inbox/directive.txt and the code is ZULU-9."}
    def step2(rec):
        (S / "chosen.txt").write_text((inbox / "directive.txt").read_text())
        return {"id": "task-fake2", "title": "", "request": "", "status": "done", "result": "Copied the chosen file to chosen.txt."}
    def step3(rec):
        (S / "code.txt").write_text("ZULU-9")
        return {"id": "task-fake3", "title": "", "request": "", "status": "done", "result": "Wrote code.txt."}
    q = FakeQueue([s1, step2, step3])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(T2C_PLAN)]), poll_sec=0.01)
    m = M.create_mission(T2C_GOAL)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"]); subs = m["subtasks"]
    check("the mission completes", m["state"] == "done", f"state={m['state']} reason={m.get('failure_reason')}")
    check("step 2's saved contract carries the repair",
          (subs[1]["contract"] or {}).get("tools") == ["computer_manage_file", "computer_write_file"],
          str(subs[1]["contract"]))
    check("step 2's request records the repair",
          len(q.created) >= 2 and "contract was repaired" in q.created[1]["request"])
    check("chosen.txt and code.txt landed exact",
          (S / "chosen.txt").exists() and (S / "chosen.txt").read_text() == "CODE: ZULU-9\nFollow the river road.\n"
          and (S / "code.txt").exists() and (S / "code.txt").read_text() == "ZULU-9")

async def main():
    case_age38_unit()
    case_age37_unit()
    await case_t2b_runner()
    await case_t2b_no_candidate()
    await case_t2c_runner()
    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed ==========", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        return 1
    print("ALL T2 ADAPT REPRO CHECKS PASSED")
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
