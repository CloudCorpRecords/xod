#!/usr/bin/env python3
"""AGE-77, from live re-grade 3 on the shipped v1.1.6 (missions
mission-2a1c3bb5a8 T2C and mission-d1da8282c1 Tier 4 L1, graded
2026-11-14; report ~/workspace/xod-eval/LIVE-REGRADE-3-2026-11-14.md):

  - T2C step 1 ("Classify inbox files") was planned as a plain task
    with the tool contract [computer_manage_file, computer_read_file].
    Its instruction begins "List the text files in .../inbox", and
    computer_list_directory is not in the contract. The worker made
    one model call and zero tool calls and stopped: "I can read the
    files, but the declared tools don't include a way to list the
    inbox. Please allow `computer_list_directory` or provide the
    filenames." The discovery gate failed the step, step 2 was
    skipped, and the mission failed with no deliverables.
  - Tier 4 L1 step 1 ("Inventory and examine sources") carried the
    same two-tool contract. The task layer's healing substituted
    computer_list_directory twice (both listings ok: true, three
    files), but the worker attempted no read and reported that two
    of the three files' names "were not available in the directory
    information shown", contradicting its own listings. The
    classification gate failed the step and the mission.

Three fix surfaces, pinned here:

  1. The planner's declared contract for a step whose own text
     requires enumerating a folder (a discovery step, a
     classification step, or an explicit list-the-contents step)
     is repaired at assembly with computer_list_directory, the
     AGE-32/AGE-38 repair extended to the listing need. Steps that
     declare no contract, steps already carrying a listing tool,
     a plain read of one named file, and a write step placing what
     it was handed are untouched.
  2. A step whose finished report is a missing-capability plea
     naming a real catalog tool its declared contract leaves out
     gets one bounded retry under the repaired contract, instead of
     the plea ending the step (and, without continue_on_failure,
     the mission). The repair and the spend are recorded in the
     step's saved contract; a second plea ends the step as before.
  3. A step whose report disclaims listing results its own tool
     record shows it received (a successful computer_list_directory
     in the same run) gets one bounded retry with those results
     re-presented in the request, rendered from a fresh listing of
     the same folders. A disclaimer with no successful listing in
     the record, and a plain under-delivery with no disclaimer,
     keep their terminal failure.

The graded shapes are verbatim from the re-grade 3 evidence (the
two plans and the two worker reports), with only the scratch-root
prefix swapped for a temp root.

Run: .venv/bin/python tests/brain/run_missions_age77_test.py
"""
import asyncio, json, os, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_age77_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import app.services.missions as M
import app.services.task_queue as TQ

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

T4 = TMP / "xod-t4-scratch"
T4.mkdir(parents=True, exist_ok=True)
(T4 / "launch_blog.txt").write_text("Northwind Traders launches.\n")
(T4 / "registry_primary.txt").write_text("Registered 2018-03-14.\n")
(T4 / "syndicated_wire.txt").write_text("Northwind Traders launches (wire).\n")

T2C_STEP1 = {"title": "Classify inbox files",
             "instruction": f"List the text files in {T2}/inbox and classify "
                            f"each by name as having or not having a first "
                            f"line starting with 'CODE:'. Read the matching "
                            f"file itself and report its path, full contents, "
                            f"and the exact first-line text following 'CODE:', "
                            f"excluding the line ending.",
             "expected_effects": [], "inputs": [{"source": "goal"}],
             "tools": ["computer_manage_file", "computer_read_file"]}
T4L1_STEP1 = {"title": "Inventory and examine sources",
              "instruction": f"Using only files in {T4}, name and classify "
                             f"every candidate by kind, then read every "
                             f"identified source file itself and report its "
                             f"relevant claims, conflicts and gaps; do not "
                             f"treat synthesis.md as evidence. Rank the state "
                             f"registry extract and other issuing-authority "
                             f"records above secondary coverage, treat a "
                             f"syndicated reprint as the same source as its "
                             f"original, and report plainly when a figure "
                             f"lacks an accessible primary source rather than "
                             f"presenting it as established.",
              "expected_effects": [], "inputs": [{"source": "goal"}],
              "tools": ["computer_manage_file", "computer_read_file"]}

# The graded worker reports, verbatim (scratch prefix swapped).
T2C_PLEA = ("I can read the files, but the declared tools don\u2019t include "
            "a way to list the inbox. Please allow `computer_list_directory` "
            "or provide the filenames.")
T4L1_REPORT = (
    f"I could identify only one of the three files in `{T4}`, and I could "
    f"not read its full contents. The available information is therefore "
    f"insufficient to report Northwind Traders\u2019 claims, conflicts, or "
    f"figures. I did not create `synthesis.md`.\n\n"
    f"- **`launch_blog.txt`** \u2014 its listing identifies it as a company "
    f"launch blog and secondary summary. Its full contents were not "
    f"available.\n"
    f"- **Two other files** \u2014 their names and kinds were not available "
    f"in the directory information shown. The folder contained no "
    f"subfolders.\n\n"
    f"I could not verify whether a registry extract, an issuing-authority "
    f"record, or a syndicated reprint is among the files, or whether any "
    f"reported figure has an accessible primary source. A complete review "
    f"requires the names and contents of all three files; `synthesis.md`, "
    f"if present, should be excluded as a source.")


def as_subtask(plan_step):
    return {"title": plan_step["title"], "instruction": plan_step["instruction"],
            "expected_effects": plan_step["expected_effects"],
            "contract": {"inputs": plan_step["inputs"], "tools": plan_step["tools"]}}


# ------------------------------------------------------- surface 1 (unit)
def case_listing_repair_unit():
    print("== surface 1: the listing need is repaired at assembly ==", flush=True)
    if not hasattr(M, "_instruction_needs_listing"):
        for name in ("T2C step 1 (discovery) gains computer_list_directory",
                     "Tier 4 L1 step 1 (classification) gains computer_list_directory",
                     "an explicit list-the-contents step gains the listing tool",
                     "a contract already carrying the listing tool is untouched",
                     "a plain read of one named file is not force-fed the listing tool",
                     "a write step placing handed sources gains no listing tool",
                     "a step declaring no contract is untouched"):
            check(name, False, "no listing-need repair on this tree")
        return
    r = M._capability_contract_repair(as_subtask(T2C_STEP1))
    check("T2C step 1 (discovery) gains computer_list_directory",
          r is not None and r[0] == ["computer_manage_file", "computer_read_file",
                                     "computer_list_directory"], str(r))
    check("the repair note names the listing need",
          r is not None and "list" in r[1].lower()
          and "computer_list_directory" in r[1], str(r and r[1]))
    r = M._capability_contract_repair(as_subtask(T4L1_STEP1))
    check("Tier 4 L1 step 1 (classification) gains computer_list_directory",
          r is not None and r[0][-1] == "computer_list_directory", str(r))

    explicit = {"title": "List the downloads",
                "instruction": f"List the files in {T2}/inbox and report "
                               f"how many there are.",
                "expected_effects": [],
                "contract": {"inputs": [{"source": "goal"}],
                             "tools": ["computer_read_file"]}}
    r = M._capability_contract_repair(explicit)
    check("an explicit list-the-contents step gains the listing tool",
          r is not None and r[0] == ["computer_read_file",
                                     "computer_list_directory"], str(r))

    served = as_subtask(T2C_STEP1)
    served["contract"] = {"inputs": [{"source": "goal"}],
                          "tools": ["computer_manage_file", "computer_read_file",
                                    "computer_list_directory"]}
    check("a contract already carrying the listing tool is untouched",
          M._capability_contract_repair(served) is None)

    plain_read = {"title": "Read the sales file",
                  "instruction": f"Read {T2}/inbox/directive.txt and report "
                                 f"the exact text of its first line.",
                  "expected_effects": [],
                  "contract": {"inputs": [{"source": "goal"}],
                               "tools": ["computer_read_file"]}}
    check("a plain read of one named file is not force-fed the listing tool",
          M._capability_contract_repair(plain_read) is None)

    write_step = {"title": "Write the listing file",
                  "instruction": f"Write the list of delivered file names to "
                                 f"{T2}/listing.txt, one name per line.",
                  "expected_effects": [{"kind": "create",
                                        "path": str(T2 / "listing.txt"),
                                        "content": None}],
                  "contract": {"inputs": [{"source": "goal"}],
                               "tools": ["computer_write_file"]}}
    check("a write step placing handed sources gains no listing tool",
          M._capability_contract_repair(write_step) is None)

    nocontract = as_subtask(T2C_STEP1)
    nocontract["contract"] = {"inputs": [{"source": "goal"}], "tools": None}
    check("a step declaring no contract is untouched",
          M._capability_contract_repair(nocontract) is None)


# ------------------------------------------------------- surface 2 (unit)
def case_missing_capability_unit():
    print("== surface 2: missing-capability detection ==", flush=True)
    if not hasattr(M, "_missing_capability_tool"):
        for name in ("the graded T2C plea names the missing listing tool",
                     "a plea for a tool already in the contract repairs nothing",
                     "a plea naming a tool outside the catalog repairs nothing",
                     "an ordinary failure report is not a capability plea",
                     "a step with no declared contract is not contract-repaired",
                     "a done outcome is never retried"):
            check(name, False, "no missing-capability detection on this tree")
        return
    sub = as_subtask(T2C_STEP1)
    out = {"status": "failed", "claimed": T2C_PLEA}
    check("the graded T2C plea names the missing listing tool",
          M._missing_capability_tool(sub, out) == "computer_list_directory",
          str(M._missing_capability_tool(sub, out)))
    served = as_subtask(T2C_STEP1)
    served["contract"] = {"inputs": [{"source": "goal"}],
                          "tools": ["computer_manage_file", "computer_read_file",
                                    "computer_list_directory"]}
    check("a plea for a tool already in the contract repairs nothing",
          M._missing_capability_tool(served, out) is None)
    fake = {"status": "blocked",
            "claimed": "I have no tool to move the files between folders. "
                       "Please allow `computer_teleport`."}
    check("a plea naming a tool outside the catalog repairs nothing",
          M._missing_capability_tool(sub, fake) is None)
    ordinary = {"status": "failed",
                "claimed": "The folder was empty, so there was nothing "
                           "to classify."}
    check("an ordinary failure report is not a capability plea",
          M._missing_capability_tool(sub, ordinary) is None)
    nocontract = as_subtask(T2C_STEP1)
    nocontract["contract"] = {"inputs": [{"source": "goal"}], "tools": None}
    check("a step with no declared contract is not contract-repaired",
          M._missing_capability_tool(nocontract, out) is None)
    done = {"status": "done", "claimed": T2C_PLEA}
    check("a done outcome is never retried",
          M._missing_capability_tool(sub, done) is None)


# ------------------------------------------------------- surface 3 (unit)
def case_disclaimer_unit():
    print("== surface 3: disclaimed listings, detection ==", flush=True)
    if not hasattr(M, "_successful_listings") or not hasattr(M, "_listing_disclaimer"):
        for name in ("successful listings are read from the task record",
                     "the graded Tier 4 report disclaims its listings",
                     "a report that uses its listings is no disclaimer"):
            check(name, False, "no disclaimed-listing detection on this tree")
        return
    task = {"id": "task-fake",
            "agent_steps": json.dumps([
                {"tool": "computer_read_file", "path": str(T4),
                 "success": False, "error": "Path is not a regular file"},
                {"tool": "computer_list_directory", "path": str(T4),
                 "success": True},
                {"tool": "computer_list_directory", "path": str(T4),
                 "success": True}])}
    check("successful listings are read from the task record",
          M._successful_listings(task) == [str(T4)],
          str(M._successful_listings(task)))
    check("the graded Tier 4 report disclaims its listings",
          M._listing_disclaimer(T4L1_REPORT) is not None,
          str(M._listing_disclaimer(T4L1_REPORT)))
    honest = ("launch_blog.txt is the company launch blog, a secondary "
              "source. registry_primary.txt is the state registry extract, "
              "the primary record. syndicated_wire.txt is a wire reprint "
              "of the blog, not an independent source.")
    check("a report that uses its listings is no disclaimer",
          M._listing_disclaimer(honest) is None)


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


# ------------------------------------------------------- surface 2 (runner)
DROP = TMP / "drop"
DROP.mkdir(parents=True, exist_ok=True)
(DROP / "a.txt").write_text("a\n")
(DROP / "b.txt").write_text("b" * 40 + "\n")
COUNT_GOAL = (f"Work in the folder {TMP}. Report how many files the drop "
              f"folder holds and name the largest one.")
COUNT_PLAN = {"subtasks": [
    {"title": "Count the drop folder",
     "instruction": f"Report how many files the folder {DROP} holds and "
                    f"name the largest one.",
     "expected_effects": [], "inputs": [{"source": "goal"}],
     "tools": ["computer_read_file"]},
    {"title": "Echo the count",
     "instruction": "State the count and the largest file name from the "
                    "previous step in one sentence.",
     "expected_effects": [],
     "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
     "tools": []},
]}
COUNT_PLEA = ("I have no tool to see what is in the folder, so I cannot "
              "count its files. Please allow `computer_list_directory` or "
              "provide the file names.")
COUNT_DONE = "The folder holds 2 files. The largest is b.txt."
COUNT_ECHO = "The drop folder holds 2 files and the largest is b.txt."


async def case_capability_retry_runner():
    print("== surface 2: a capability plea is repaired and retried ==", flush=True)
    q = FakeQueue([{"status": "done", "result": COUNT_PLEA},
                   {"status": "done", "result": COUNT_DONE},
                   {"status": "done", "result": COUNT_ECHO}])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(COUNT_PLAN)]),
                             poll_sec=0.01)
    m = M.create_mission(COUNT_GOAL)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    subs = m["subtasks"]
    check("the plea produced exactly one retry task for the step",
          len(q.created) == 3, f"created={len(q.created)}")
    if len(q.created) == 3:
        req2 = q.created[1]["request"]
        check("the retry request declares the repaired contract",
              "capability contract): computer_read_file, computer_list_directory"
              in req2, req2[:400])
        check("the retry request records the repair and the plea",
              "capability retry" in req2 and "computer_list_directory" in req2)
    check("the step's saved contract carries the repair and the spend",
          (subs[0]["contract"] or {}).get("tools")
          == ["computer_read_file", "computer_list_directory"]
          and bool((subs[0]["contract"] or {}).get("outcome_retry")),
          str(subs[0]["contract"]))
    check("the step is done on the retry's report",
          subs[0]["status"] == "done", subs[0]["status"])
    check("the step's evidence names the retry",
          any("retry" in e for e in subs[0]["evidence"]),
          str(subs[0]["evidence"]))
    check("the mission completes", m["state"] == "done", m["state"])


async def case_capability_retry_bound():
    print("== surface 2: the retry is spent once ==", flush=True)
    q = FakeQueue([{"status": "done", "result": COUNT_PLEA},
                   {"status": "done", "result": COUNT_PLEA}])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(COUNT_PLAN)]),
                             poll_sec=0.01)
    m = M.create_mission(COUNT_GOAL)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    subs = m["subtasks"]
    check("a second plea creates no third task",
          len(q.created) == 2, f"created={len(q.created)}")
    check("the step ends blocked on the second plea",
          subs[0]["status"] == "blocked", subs[0]["status"])
    check("the mission fails honestly", m["state"] == "failed", m["state"])


# ------------------------------------------------------- surface 3 (runner)
T4_GOAL = (f"Research the company Northwind Traders using only the source "
           f"files in {T4}, then summarize which source is primary.")
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
T4_LISTED = {"status": "done", "result": T4L1_REPORT,
             "agent_steps": json.dumps([
                 {"tool": "computer_read_file", "path": str(T4),
                  "success": False, "error": "Path is not a regular file"},
                 {"tool": "computer_list_directory", "path": str(T4),
                  "success": True},
                 {"tool": "computer_list_directory", "path": str(T4),
                  "success": True}])}
T4_CLASSIFIED = ("launch_blog.txt is the company launch blog, a secondary "
                 "source. registry_primary.txt is the state registry "
                 "extract, the primary record, an official record. "
                 "syndicated_wire.txt is a wire reprint of the blog, not "
                 "an independent source. I read all three files in full.")
T4_SUMMARY = ("The primary source is the state registry extract "
              "(registry_primary.txt); the blog is secondary coverage "
              "and the wire reprint is not independent of it.")


async def case_disclaimer_retry_runner():
    print("== surface 3: disclaimed listings are re-presented ==", flush=True)
    q = FakeQueue([T4_LISTED,
                   {"status": "done", "result": T4_CLASSIFIED},
                   {"status": "done", "result": T4_SUMMARY}])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(T4_PLAN)]),
                             poll_sec=0.01)
    m = M.create_mission(T4_GOAL)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
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
    check("the step is done on the retry's classification",
          subs[0]["status"] == "done", subs[0]["status"])
    check("the step's evidence names the retry",
          any("retry" in e for e in subs[0]["evidence"]),
          str(subs[0]["evidence"]))
    check("the mission completes", m["state"] == "done", m["state"])


async def case_disclaimer_controls():
    print("== surface 3: controls keep their terminal failure ==", flush=True)
    # A disclaimer with NO successful listing in the record: no retry.
    q = FakeQueue([{"status": "done", "result": T4L1_REPORT}])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(T4_PLAN)]),
                             poll_sec=0.01)
    m = M.create_mission(T4_GOAL)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    check("a disclaimer with no listing in the record is not retried",
          len(q.created) == 1 and m["subtasks"][0]["status"] == "failed",
          f"created={len(q.created)} status={m['subtasks'][0]['status']}")
    # A listing in the record but a report that merely under-delivers
    # (classifies one source, disclaims nothing): no retry.
    under = ("launch_blog.txt is the company launch blog. The other "
             "files are also in the folder.")
    q2 = FakeQueue([{"status": "done", "result": under,
                     "agent_steps": json.dumps([
                         {"tool": "computer_list_directory",
                          "path": str(T4), "success": True}])}])
    runner2 = M.MissionRunner(queue=q2, ask=FakeAsk([json.dumps(T4_PLAN)]),
                              poll_sec=0.01)
    m2 = M.create_mission(T4_GOAL)
    await runner2.drive(m2["id"])
    m2 = M.get_mission(m2["id"])
    check("an under-delivery with no disclaimer is not retried",
          len(q2.created) == 1 and m2["subtasks"][0]["status"] == "failed",
          f"created={len(q2.created)} status={m2['subtasks'][0]['status']}")


async def main():
    case_listing_repair_unit()
    case_missing_capability_unit()
    case_disclaimer_unit()
    await case_capability_retry_runner()
    await case_capability_retry_bound()
    await case_disclaimer_retry_runner()
    await case_disclaimer_controls()
    print(f"\n{len(results) - sum(1 for _, ok in results if not ok)}/{len(results)} checks passed", flush=True)
    failed = [n for n, ok in results if not ok]
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
        sys.exit(1)
    print("ALL AGE-77 CHECKS PASSED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
