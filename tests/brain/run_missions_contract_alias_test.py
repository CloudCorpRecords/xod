"""
Missions harness fix (2026-11-08): the capability gate and tool-name aliases.

Live record (the owner's Mac, shipped 1.0.2, mission-baba238e08): step 1,
"Create proof directory", was stopped at assembly before any task was
created because the plan declared its tool as "computer_create_directory"
and the contract checker did not recognize that name as able to create
a directory:

    subtask "Create proof directory" must create the directory
    /Users/owner/xod-t1-scratch/proofdir, but its declared
    capability contract (tools: computer_create_directory) includes
    none of the tools that could do it (computer_manage_file,
    fs_create_folder, computer_write_file).

The executor already canonicalizes model-invented tool names through an
alias map (app/agent/researcher.py _canonicalize_tool_name: "mkdir" and
"create_folder" already map to fs_create_folder, "write_file" to
computer_write_file). The contract checker never applied that map, and
the map itself had no entry for the directory-creation name the planner
reached for. Sibling audit in the same pass: a modify effect whose
contract names only computer_edit_file (the tool that exists precisely
to modify files) was likewise unrecognizable to the checker, whose
write set named only the two whole-file writers.

Covered here:
  - the live shape: a directory create whose contract names
    computer_create_directory passes the contract check and the step
    is dispatched (a task is created), where before it died at
    assembly with zero tasks made.
  - the planner's sibling names for the same effects canonicalize the
    same way: create_directory / computer_create_folder for a
    directory, create_file / computer_create_file / write_file for a
    file, computer_delete_file / delete_file for a delete.
  - a modify effect is performable by computer_edit_file.
  - the gate still bites: a read-only contract for a create still
    fails at assembly, a directory tool name does not satisfy a file
    write, and an invented name that maps to nothing still fails.

Run: .venv/bin/python tests/brain/run_missions_contract_alias_test.py
"""
import asyncio, json, os, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_missions_alias_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.missions as M

M.DB = TMP / "xod.db"
M._spawn = lambda mid: None   # tests drive missions by hand

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


# --------------------------------------------------------------------- fakes
class FakeAsk:
    def __init__(self, replies):
        self.replies = list(replies)

    async def __call__(self, prompt):
        return self.replies.pop(0) if self.replies else "{}"


class FakeQueue:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.created = []
        self.tasks = {}

    def create(self, request, title=None, origin=None):
        tid = f"task-fake{len(self.created) + 1}"
        self.created.append({"request": request, "title": title})
        idx = len(self.created) - 1
        self.tasks[tid] = self.behaviors[idx] if idx < len(self.behaviors) else DONE
        return {"id": tid, "status": "queued", "title": title}

    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None:
            return None
        return {"id": tid, **rec}


DONE = {"status": "done", "result": "All done."}
SCRATCH = str(TMP / "xod-t1-scratch")
PROOFDIR = SCRATCH + "/proofdir"


def sub(title, instruction, effects=None, tools=None):
    s = {"title": title, "instruction": instruction, "expected_effects": effects or []}
    if tools is not None:
        s["contract"] = {"inputs": [], "tools": tools}
    return s


def dir_sub(tools):
    return sub("Create proof directory", f"Create the directory {PROOFDIR}.",
               [{"kind": "create", "path": PROOFDIR, "content": None}], tools=tools)


# ------------------------------------------------- the contract check itself
def case_contract_check():
    problem = M._contract_tool_problem(dir_sub(["computer_create_directory"]))
    check("alias: computer_create_directory satisfies a directory create",
          problem is None, problem or "")

    for name in ("create_directory", "computer_create_folder", "mkdir", "create_folder"):
        problem = M._contract_tool_problem(dir_sub([name]))
        check(f"alias: {name} satisfies a directory create", problem is None, problem or "")

    note = PROOFDIR + "/note.txt"
    for name in ("create_file", "computer_create_file", "write_file", "computer_write_file"):
        s = sub("Write proof note", f"Create the file at {note} with exact text: baseline proof 7731",
                [{"kind": "create", "path": note, "content": "baseline proof 7731"}], tools=[name])
        problem = M._contract_tool_problem(s)
        check(f"alias: {name} satisfies a file create", problem is None, problem or "")

    for name in ("computer_delete_file", "delete_file", "computer_manage_file"):
        s = sub("Remove the note", f"Delete the file at {note}.",
                [{"kind": "delete", "path": note, "content": None}], tools=[name])
        problem = M._contract_tool_problem(s)
        check(f"alias: {name} satisfies a delete", problem is None, problem or "")

    s = sub("Fix the note", f"Write the updated file at {note}.",
            [{"kind": "modify", "path": note, "content": None}], tools=["computer_edit_file"])
    problem = M._contract_tool_problem(s)
    check("audit: computer_edit_file satisfies a modify effect", problem is None, problem or "")

    # The gate still bites where the contract truly cannot perform the effect.
    problem = M._contract_tool_problem(dir_sub(["computer_read_file"]))
    check("gate: a read-only contract still fails a directory create",
          problem is not None, problem or "no problem reported")
    s = sub("Write proof note", f"Create the file at {PROOFDIR}/note.txt.",
            [{"kind": "create", "path": PROOFDIR + "/note.txt", "content": None}],
            tools=["computer_create_directory"])
    problem = M._contract_tool_problem(s)
    check("gate: a directory tool name does not satisfy a file write",
          problem is not None, problem or "no problem reported")
    s = sub("Write proof note", f"Create the file at {PROOFDIR}/note.txt.",
            [{"kind": "create", "path": PROOFDIR + "/note.txt", "content": None}],
            tools=["computer_frobnicate"])
    problem = M._contract_tool_problem(s)
    check("gate: an invented name that maps to nothing still fails",
          problem is not None, problem or "no problem reported")


# ------------------------------------------------------- the mission end to end
async def case_live_shape_dispatches():
    subs = [{"title": "Create proof directory",
             "instruction": f"Create the directory {PROOFDIR}.",
             "expected_effects": [{"kind": "create", "path": PROOFDIR, "content": None}],
             "inputs": [], "tools": ["computer_create_directory"]},
            {"title": "Write proof note",
             "instruction": "Create note.txt inside proofdir with exactly the text baseline proof 7731.",
             "expected_effects": [{"kind": "create", "path": PROOFDIR + "/note.txt",
                                   "content": "baseline proof 7731"}],
             "inputs": [], "tools": ["computer_write_file"]}]
    q = FakeQueue([DONE, DONE])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps({"subtasks": subs})]), poll_sec=0.01)
    m = M.create_mission(f"Create a folder named proofdir inside {SCRATCH}")
    await runner.drive(m["id"])
    check("live shape: the directory step was dispatched (a task was created)",
          len(q.created) >= 1, f"created={len(q.created)}")
    m = M.get_mission(m["id"])
    s = m["subtasks"][0]
    check("live shape: step 1 is not an assembly failure",
          s["status"] != "failed" or not any("Assembly stopped" in e for e in s["evidence"]),
          f"status={s['status']} evidence={s['evidence']}")


async def main():
    case_contract_check()
    await case_live_shape_dispatches()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1 if failed else 0)


asyncio.run(main())
