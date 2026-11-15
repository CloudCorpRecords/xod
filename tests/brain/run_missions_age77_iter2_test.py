#!/usr/bin/env python3
"""AGE-77 iteration 2, from live re-grade 4 on the shipped v1.1.8
(missions mission-5eebb4b573 T2C and mission-323cccf9a8 Tier 4 L1,
graded 2026-11-14; report ~/workspace/xod-eval/LIVE-REGRADE-4-2026-11-14.md):

Re-grade 4 proved the AGE-77 planner repair live (both step 1
contracts carried computer_list_directory with a list-first
instruction) and both missions still failed, with the outcome retry
never engaging: no outcome_retry marker in either saved contract,
zero outcome_retry mentions in either event log, each step ran
exactly once. Two defects, pinned here:

  1. The disclaimer retry could not fire in production, for two
     independent reasons, each sufficient on its own.
     (a) The record: _successful_listings reads a listing's folder
     from the payload, path, folder_path, filepath or detail fields
     of a task's tool entries. The only production record of a
     step's tool calls is the agent log's step rows, and
     agent_log.title_for records a listing with an empty detail, so
     every real listing yields an empty path and is dropped: the
     branch short-circuits on an empty listings list for every real
     task. The iteration 1 suite fed the task record a shape
     production never writes (a task agent_steps JSON of tool, path
     and success entries), so the gap stayed invisible. The fix is
     at the record: a listing step's detail now carries the listed
     path, the field the consumer already reads.
     (b) The phrasing: _listing_disclaimer was cut to the re-grade 3
     wording and returns None on both verbatim re-grade 4 reports
     (checked against the shipped code): "the available listing
     identifies only one file", "their names and source kinds are
     not available", "aren't available in the information provided"
     all miss the re-grade 3 patterns. The patterns are broadened
     conservatively, and a report that uses its listings stays no
     disclaimer.
  2. Identification without a read had no repair. The discovery
     gate correctly refuses an identification that rests only on a
     listing preview when the step's instruction demands the file's
     contents, and the step then just failed (T2C: directive.txt
     identified from the preview, never read, contents reported as
     not available). The outcome retry now carries a read repair in
     the same bounded spirit as the missing-capability repair: a
     discovery step whose instruction demands the identified file's
     contents, whose report identifies a file the step's own tool
     record shows no successful read of, and whose contract holds
     computer_read_file, is retried once with the file named and an
     explicit instruction to read it before answering. The spend is
     marked in the saved contract, a second preview-only
     identification ends the step, and no verdict gate is loosened:
     the same gates judge the retry's report. Classification steps
     keep the disclaimer repair as their repair path (the Tier 4
     shape, defect 1), and a classification under-delivery with no
     disclaimer keeps its terminal failure, as iteration 1 pinned.

The graded shapes are verbatim from the re-grade 4 evidence (the
two step instructions and the two worker reports), with only the
scratch-root prefix swapped for a temp root, and the tool records
are written through the real agent_log.step path, in the exact
shape production records them.

Run: .venv/bin/python tests/brain/run_missions_age77_iter2_test.py
"""
import asyncio, json, os, sqlite3, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_age77i2_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import app.services.missions as M
import app.services.task_queue as TQ
from app.services import activity, agent_log

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
    print(f"  {'PASS' if ok else 'FAIL'} {name}"
          + (f"  {detail}" if detail and not ok else ""), flush=True)


# ---------------------------------------------------------------- fixtures
T2 = TMP / "xod-t2-scratch"
(T2 / "inbox").mkdir(parents=True, exist_ok=True)
(T2 / "inbox" / "directive.txt").write_text("CODE: ZULU-9\nDirective body.\n")
(T2 / "inbox" / "note-morning.txt").write_text("Morning note.\n")
(T2 / "inbox" / "memo-supplies.txt").write_text("Supplies memo.\nMore lines.\n")
DIRECTIVE = str(T2 / "inbox" / "directive.txt")

T4 = TMP / "xod-t4-scratch"
T4.mkdir(parents=True, exist_ok=True)
(T4 / "launch_blog.txt").write_text("Northwind Traders launches.\n")
(T4 / "registry_primary.txt").write_text("Registered 2018-03-14.\n")
(T4 / "syndicated_wire.txt").write_text("Northwind Traders launches (wire).\n")

# The re-grade 4 steps: the contracts are the post-repair contracts
# the live planner produced (listing tool present, list first).
T2C_STEP1 = {"title": "Classify inbox files",
             "instruction": f"List the inbox at {T2}/inbox and classify "
                            f"every entry as a text file whose first line "
                            f"starts with 'CODE:', another text file, or a "
                            f"non-text-file entry. Read the matching file "
                            f"in full and report its path, exact contents, "
                            f"and the exact text following 'CODE:' on its "
                            f"first line, excluding the line ending.",
             "expected_effects": [], "inputs": [{"source": "goal"}],
             "tools": ["computer_list_directory", "computer_read_file"]}
T4L1_STEP1 = {"title": "Inspect and classify sources",
              "instruction": f"List the folder {T4} and any subfolders, "
                             f"classify every candidate file by name and "
                             f"source kind, and read each source file "
                             f"itself; report its relevant claims, exact "
                             f"dates or figures, provenance, conflicts, "
                             f"and gaps. Treat a press release or official "
                             f"registry extract as a primary source and a "
                             f"company blog as secondary; a syndicated "
                             f"wire reprint of the blog is the same source "
                             f"as the blog, not independent confirmation. "
                             f"Do not treat this instruction or the "
                             f"checklist as a source. When you finish, "
                             f"write the adjudicated summary to "
                             f"synthesis.md in the same folder.",
              "expected_effects": [], "inputs": [{"source": "goal"}],
              "tools": ["computer_list_directory", "computer_read_file"]}

# The graded worker reports, verbatim (scratch prefix swapped).
T2C_REPORT = (
    f"The inbox contains three files and no folders. The visible "
    f"matching entry is **`{DIRECTIVE}`**, whose first line is "
    f"`CODE: ZULU-9`. The exact text after `CODE:`, excluding the line "
    f"ending, is **` ZULU-9`**\u2014including the leading space. I can't "
    f"report the file's exact full contents or classify the other two "
    f"entries: their details and the full file contents aren't "
    f"available in the information provided.")
T4L1_REPORT = (
    f"The folder contains three files and no subfolders, but the "
    f"available listing identifies only one file. I can't report the "
    f"sources' claims or resolve conflicts without reading the files.\n"
    f"- `{T4}/launch_blog.txt` \u2014 its first line identifies it as a "
    f"**company launch blog, a secondary summary**. Its remaining "
    f"contents were not available.\n"
    f"- Two other files are counted, but their names and source kinds "
    f"are not available.\n"
    f"No primary source can be verified from the available "
    f"information, and no dates or figures about Northwind Traders "
    f"can be established. Any wire reprint of the blog should be "
    f"treated as the same source, not independent confirmation. I "
    f"did not write `synthesis.md`.")
# The same Tier 4 disclaimer shape with no file named at all: nothing
# for a read repair to name, so the disclaimer repair is the path.
T4L1_NOFILE_REPORT = (
    f"The folder contains three files and no subfolders, but the "
    f"available listing identifies only one file. I can't report the "
    f"sources' claims or resolve conflicts without reading the files. "
    f"Two other files are counted, but their names and source kinds "
    f"are not available. No primary source can be verified from the "
    f"available information, and no dates or figures about Northwind "
    f"Traders can be established.")

T2C_READ_REPORT = (
    f"I read `{DIRECTIVE}` in full. The matching file is "
    f"`{DIRECTIVE}`. Its first line is `CODE: ZULU-9`, and the exact "
    f"text following 'CODE:' on its first line, excluding the line "
    f"ending, is ` ZULU-9`. Its exact contents are:\n"
    f"CODE: ZULU-9\nDirective body.\n"
    f"The other two entries are plain text files whose first lines "
    f"do not start with 'CODE:'.")
T4L1_CLASSIFIED = (
    f"I read each source file itself. `{T4}/launch_blog.txt` is a "
    f"company launch blog, a secondary summary. "
    f"`{T4}/registry_primary.txt` is the official registry extract, "
    f"the primary record. `{T4}/syndicated_wire.txt` is a syndicated "
    f"wire reprint of the blog, the same source as the blog, not "
    f"independent confirmation.")
T4L1_SUMMARY = ("The primary source is the state registry extract "
                "(registry_primary.txt); the blog is secondary "
                "coverage and the wire reprint is not independent "
                "of it.")
T2C_ECHO = ("The identified file is directive.txt and the code text "
            "after 'CODE:' is ZULU-9.")


def as_subtask(plan_step):
    return {"title": plan_step["title"], "instruction": plan_step["instruction"],
            "expected_effects": plan_step["expected_effects"],
            "contract": {"inputs": plan_step["inputs"], "tools": plan_step["tools"]}}


# -------------------------------------------- production-shaped records
def record_steps(task_id, calls):
    """Write a task's tool record through the real agent_log.step
    path, exactly as production records it: (tool, args, result)."""
    tok = activity.origin.set(f"task:{task_id}")
    try:
        for tool, args, result in calls:
            agent_log.step(tool, args, result)
    finally:
        activity.origin.reset(tok)


def wipe_steps():
    agent_log._conn().close()  # ensure the schema exists
    with sqlite3.connect(str(agent_log.DB)) as c:
        c.execute("DELETE FROM agent_steps")


def list_call(folder):
    return ("computer_list_directory", {"path": str(folder)},
            {"success": True, "entries": []})


def read_call(path):
    return ("computer_read_file", {"filepath": str(path)},
            {"success": True, "content": Path(path).read_text(),
             "lines_count": 2})


FAILED_READ_CALL = ("computer_read_file", {},
                    {"success": False, "error": "needs filepath"})


# ------------------------------------------------------- defect 1 (unit)
def case_record_and_disclaimer_unit():
    print("== defect 1: the record and the disclaimer detection ==", flush=True)
    title, detail = agent_log.title_for(
        "computer_list_directory", {"path": str(T4)}, {"success": True})
    check("a listing step's record carries the listed path",
          detail == str(T4), f"title={title!r} detail={detail!r}")
    wipe_steps()
    record_steps("task-unit-listings",
                 [("computer_read_file", {"filepath": str(T4)},
                   {"success": False, "error": "Path is not a regular file"}),
                  list_call(T4), list_call(T4)])
    check("successful listings are read from the production record",
          M._successful_listings({"id": "task-unit-listings"}) == [str(T4)],
          str(M._successful_listings({"id": "task-unit-listings"})))
    check("the graded Tier 4 report disclaims its listings",
          M._listing_disclaimer(T4L1_REPORT) is not None,
          str(M._listing_disclaimer(T4L1_REPORT)))
    check("the graded T2C report disclaims its listings",
          M._listing_disclaimer(T2C_REPORT) is not None,
          str(M._listing_disclaimer(T2C_REPORT)))
    check("the no-file Tier 4 disclaimer is detected",
          M._listing_disclaimer(T4L1_NOFILE_REPORT) is not None,
          str(M._listing_disclaimer(T4L1_NOFILE_REPORT)))
    honest = ("launch_blog.txt is the company launch blog, a secondary "
              "source. registry_primary.txt is the state registry extract, "
              "the primary record. syndicated_wire.txt is a wire reprint "
              "of the blog, not an independent source.")
    check("a report that uses its listings is no disclaimer",
          M._listing_disclaimer(honest) is None)
    sub = as_subtask(T2C_STEP1)
    out = {"status": "failed", "claimed": T2C_REPORT}
    check("the T2C report is no capability plea (branch order is safe)",
          M._missing_capability_tool(sub, out) is None,
          str(M._missing_capability_tool(sub, out)))


# ------------------------------------------------------- defect 2 (unit)
def case_unread_identified_unit():
    print("== defect 2: identified without a read, detection ==", flush=True)
    names = ("the graded T2C shape names the unread identified file",
             "an identified file that was read repairs nothing",
             "a contract without the read tool repairs nothing",
             "a done outcome is never repaired",
             "a classification step is served by the disclaimer repair")
    if not hasattr(M, "_unread_identified_file"):
        for name in names:
            check(name, False, "no read repair on this tree")
        return
    wipe_steps()
    record_steps("task-unit-read",
                 [list_call(T2 / "inbox"), FAILED_READ_CALL, list_call(T2)])
    sub = as_subtask(T2C_STEP1)
    out = {"status": "failed", "claimed": T2C_REPORT}
    task = {"id": "task-unit-read"}
    check(names[0], M._unread_identified_file(sub, out, task) == DIRECTIVE,
          str(M._unread_identified_file(sub, out, task)))
    wipe_steps()
    record_steps("task-unit-read2",
                 [list_call(T2 / "inbox"), read_call(DIRECTIVE)])
    check(names[1],
          M._unread_identified_file(sub, out, {"id": "task-unit-read2"}) is None,
          str(M._unread_identified_file(sub, out, {"id": "task-unit-read2"})))
    noread = as_subtask(T2C_STEP1)
    noread["contract"] = {"inputs": [{"source": "goal"}],
                          "tools": ["computer_list_directory"]}
    check(names[2], M._unread_identified_file(noread, out, task) is None)
    done = {"status": "done", "claimed": T2C_REPORT}
    check(names[3], M._unread_identified_file(sub, done, task) is None)
    t4sub = as_subtask(T4L1_STEP1)
    t4out = {"status": "failed", "claimed": T4L1_REPORT}
    check(names[4], M._unread_identified_file(t4sub, t4out, task) is None)


# ------------------------------------------------------------------ fakes
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
        self.tasks[tid] = {"behavior": self.behaviors[len(self.created) - 1]}
        return {"id": tid, "status": "queued", "title": title}

    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None:
            return None
        b = rec["behavior"]
        if callable(b):
            return b(rec)
        return {"id": tid, "title": "", "request": "", **b}


T2C_PLAN = {"subtasks": [
    {**T2C_STEP1},
    {"title": "Echo the discovery",
     "instruction": "State the identified file and the code text from "
                    "the previous step in one sentence.",
     "expected_effects": [],
     "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
     "tools": []},
]}
T2C_GOAL = f"Work in {T2}. Classify the inbox files and report the match."

T4_PLAN = {"subtasks": [
    {**T4L1_STEP1},
    {"title": "Summarize the classification",
     "instruction": "Report, in one paragraph, which source is primary "
                    "and which is not independent, based on the previous "
                    "step's findings.",
     "expected_effects": [],
     "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
     "tools": []},
]}
T4_GOAL = (f"Research the company Northwind Traders using only the "
           f"source files in {T4}, then summarize which source is primary.")


async def drive(plan, goal, behaviors):
    q = FakeQueue(behaviors)
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]),
                             poll_sec=0.01)
    m = M.create_mission(goal)
    await runner.drive(m["id"])
    return q, M.get_mission(m["id"])


# ------------------------------------------------------- defect 2 (runner)
async def case_read_repair_runner():
    print("== defect 2: the T2C shape is repaired by a read ==", flush=True)
    wipe_steps()
    record_steps("task-fake1",
                 [list_call(T2 / "inbox"), FAILED_READ_CALL, list_call(T2)])
    record_steps("task-fake2", [read_call(DIRECTIVE)])
    q, m = await drive(T2C_PLAN, T2C_GOAL,
                       [{"status": "done", "result": T2C_REPORT},
                        {"status": "done", "result": T2C_READ_REPORT},
                        {"status": "done", "result": T2C_ECHO}])
    subs = m["subtasks"]
    check("the preview identification produced exactly one retry",
          len(q.created) == 3, f"created={len(q.created)}")
    if len(q.created) == 3:
        req2 = q.created[1]["request"]
        check("the retry names the identified file and orders the read",
              DIRECTIVE in req2 and "read" in req2.lower()
              and "before answering" in req2, req2[:500])
    marker = (subs[0]["contract"] or {}).get("outcome_retry") or {}
    check("the saved contract marks the read repair spend",
          marker.get("kind") == "read_identified"
          and marker.get("file") == DIRECTIVE, str(marker))
    check("the step is done on the retry's read report",
          subs[0]["status"] == "done", subs[0]["status"])
    check("the step's evidence names the retry",
          any("retry" in e for e in subs[0]["evidence"]),
          str(subs[0]["evidence"]))
    check("the mission completes", m["state"] == "done", m["state"])


async def case_read_repair_bound():
    print("== defect 2: the read repair is spent once ==", flush=True)
    wipe_steps()
    record_steps("task-fake1",
                 [list_call(T2 / "inbox"), FAILED_READ_CALL, list_call(T2)])
    record_steps("task-fake2", [list_call(T2 / "inbox")])
    q, m = await drive(T2C_PLAN, T2C_GOAL,
                       [{"status": "done", "result": T2C_REPORT},
                        {"status": "done", "result": T2C_REPORT}])
    subs = m["subtasks"]
    check("a second preview identification creates no third task",
          len(q.created) == 2, f"created={len(q.created)}")
    check("the step ends failed on the second pass",
          subs[0]["status"] == "failed", subs[0]["status"])
    check("the spend is still marked in the saved contract",
          bool((subs[0]["contract"] or {}).get("outcome_retry")),
          str(subs[0]["contract"]))
    check("the mission fails honestly", m["state"] == "failed", m["state"])


# ------------------------------------------------------- defect 1 (runner)
async def case_disclaimer_repair_runner():
    print("== defect 1: the Tier 4 shape is repaired by re-presentation ==",
          flush=True)
    wipe_steps()
    record_steps("task-fake1", [list_call(T4), list_call(T4)])
    record_steps("task-fake2", [read_call(T4 / "launch_blog.txt"),
                                read_call(T4 / "registry_primary.txt"),
                                read_call(T4 / "syndicated_wire.txt")])
    q, m = await drive(T4_PLAN, T4_GOAL,
                       [{"status": "done", "result": T4L1_REPORT},
                        {"status": "done", "result": T4L1_CLASSIFIED},
                        {"status": "done", "result": T4L1_SUMMARY}])
    subs = m["subtasks"]
    check("the disclaimer produced exactly one retry task for the step",
          len(q.created) == 3, f"created={len(q.created)}")
    if len(q.created) == 3:
        req2 = q.created[1]["request"]
        check("the retry request re-presents every listed file name",
              all(n in req2 for n in ("launch_blog.txt",
                                      "registry_primary.txt",
                                      "syndicated_wire.txt")), req2[:600])
        check("the retry request names the contradiction plainly",
              "re-presented" in req2 and "contradicts" in req2)
    marker = (subs[0]["contract"] or {}).get("outcome_retry") or {}
    check("the saved contract marks the re-presentation spend",
          marker.get("kind") == "listing_represent", str(marker))
    check("the step is done on the retry's classification",
          subs[0]["status"] == "done", subs[0]["status"])
    check("the mission completes", m["state"] == "done", m["state"])


async def case_disclaimer_nofile_runner():
    print("== defect 1: a disclaimer naming no file is re-presented ==",
          flush=True)
    wipe_steps()
    record_steps("task-fake1", [list_call(T4), list_call(T4)])
    record_steps("task-fake2", [read_call(T4 / "launch_blog.txt"),
                                read_call(T4 / "registry_primary.txt"),
                                read_call(T4 / "syndicated_wire.txt")])
    q, m = await drive(T4_PLAN, T4_GOAL,
                       [{"status": "done", "result": T4L1_NOFILE_REPORT},
                        {"status": "done", "result": T4L1_CLASSIFIED},
                        {"status": "done", "result": T4L1_SUMMARY}])
    subs = m["subtasks"]
    check("the no-file disclaimer produced exactly one retry",
          len(q.created) == 3, f"created={len(q.created)}")
    if len(q.created) == 3:
        req2 = q.created[1]["request"]
        check("the retry request re-presents every listed file name",
              all(n in req2 for n in ("launch_blog.txt",
                                      "registry_primary.txt",
                                      "syndicated_wire.txt")), req2[:600])
    check("the step is done on the retry's classification",
          subs[0]["status"] == "done", subs[0]["status"])
    check("the mission completes", m["state"] == "done", m["state"])


async def case_production_controls():
    print("== controls on production-shaped records ==", flush=True)
    # A disclaimer with NO tool record at all: no retry.
    wipe_steps()
    q, m = await drive(T4_PLAN, T4_GOAL,
                       [{"status": "done", "result": T4L1_REPORT}])
    check("a disclaimer with no record is not retried",
          len(q.created) == 1 and m["subtasks"][0]["status"] == "failed",
          f"created={len(q.created)} status={m['subtasks'][0]['status']}")
    # A listing in the record and a report that merely under-delivers
    # (classifies one source, disclaims nothing): no retry.
    wipe_steps()
    record_steps("task-fake1", [list_call(T4)])
    under = ("launch_blog.txt is the company launch blog. The other "
             "files are also in the folder.")
    q2, m2 = await drive(T4_PLAN, T4_GOAL,
                         [{"status": "done", "result": under}])
    check("an under-delivery with no disclaimer is not retried",
          len(q2.created) == 1 and m2["subtasks"][0]["status"] == "failed",
          f"created={len(q2.created)} status={m2['subtasks'][0]['status']}")


async def main():
    case_record_and_disclaimer_unit()
    case_unread_identified_unit()
    await case_read_repair_runner()
    await case_read_repair_bound()
    await case_disclaimer_repair_runner()
    await case_disclaimer_nofile_runner()
    await case_production_controls()
    print(f"\n{len(results) - sum(1 for _, ok in results if not ok)}/{len(results)} checks passed", flush=True)
    failed = [n for n, ok in results if not ok]
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
        sys.exit(1)
    print("ALL AGE-77 ITERATION 2 CHECKS PASSED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
