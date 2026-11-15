#!/usr/bin/env python3
"""AGE-77 iteration 3, from live re-grade 5 on the shipped v1.1.9
(missions mission-eddf0a8568 T2C and mission-e1b2583574 Tier 4 L1,
graded 2026-11-15; report ~/workspace/xod-eval/LIVE-REGRADE-5-2026-11-15.md):

Re-grade 5 proved both iteration 2 mechanisms live (the identified
file read in full on the first pass; the listing_represent retry
fired for the first time) and both missions still failed, one layer
up. Two defects, pinned here:

  1. T2C: the discovery step was delegated across two workers by
     filename range. The worker holding directive.txt read it in
     full and delivered its contents through its delegate messages,
     then tried to verify exact bytes through computer_run_command,
     which its read-only envelope denies, and closed its final
     report "Not verified" instead of delivering. judge_report turns
     that close into a worker failure (every other evaluation screen
     passes; the same refusal would have been a recorded caveat on
     the done path), no in-loop continuation covers the caveat-close
     shape, and a delegated step has no outcome-level repair at all:
     _delegated_outcome returns terminally, so a partial fan-out
     whose only failures are report admissions fails the step with
     no recovery, and the write step never runs. The fix is a
     bounded completion retry in _delegated_outcome, the delegated
     sibling of the outcome retry's read repair: when a fan-out ends
     undelivered and every failure is a report admission, the
     undelivered briefs re-run once with a completion note (deliver
     from the reads; a check the read-only tools cannot run is a
     caveat, not a non-delivery), the delivered workers are reused,
     the spend is marked in the saved contract as outcome_retry kind
     delegated_completion, and the same all-workers-delivered rule
     judges the retry. No verdict standard moves: the worker verdict
     on a caveat close still fails, and a fan-out with any other
     failure shape earns no retry.
  2. Tier 4 L1: on the listing_represent retry the worker read all
     three sources and classified all three correctly, in
     bullet-label form (path, then kind and provenance after a
     dash). The classification gate failed the step anyway: its
     matcher counted a file as classified only when the file's
     clause carried a copular predicate, so only the wire bullet
     ("reprint of") counted and the registry and blog bullets scored
     zero. The gate consumed the retry report itself, in full
     (executing it on the verbatim report reproduces the recorded
     verdict). The fix counts a file as classified when its clause
     states a source kind in label form too, with the kind vocabulary
     judged on the clause after the file paths are removed, so the
     kind words inside the filenames themselves (registry_primary,
     launch_blog) cannot make a bare listing pass.

The graded shapes are verbatim from the re-grade 5 evidence (the
Tier 4 step and both of its reports; the T2C worker split, file
contents, and caveat close), with only the scratch-root prefix
swapped for a temp root in the runner fixtures.

Run: .venv/bin/python tests/brain/run_missions_age77_iter3_test.py
"""
import asyncio, json, os, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_age77i3_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import app.services.missions as M
import app.services.task_queue as TQ
import app.services.workers as W

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


# ------------------------------------------------- Tier 4 fixtures (verbatim)
# The graded step and both reports are verbatim from the re-grade 5
# evidence (t4l1-grade.txt); the paths are the live run's own, because
# the classification gate is a pure text judgment.
T4_SUB = {"title": "Inventory and read sources",
          "instruction": (
              "Recursively list /Users/reneturcios/xod-t4-scratch, classify "
              "every candidate by its kind, and read each identified source "
              "file itself, reporting its relevant contents and provenance; "
              "exclude synthesis.md and use no external sources. Rank the "
              "state registry extract above the company blog, treat a wire "
              "reprint as the same source, and do not establish a figure "
              "from secondary coverage alone; report plainly if the relevant "
              "primary source is unavailable."),
          "expected_effects": [],
          "contract": {"inputs": [{"source": "goal"}],
                       "tools": ["computer_list_directory", "computer_read_file"]}}

T4_RETRY_REPORT = (
    "Northwind Traders\u2019 registry extract records incorporation in "
    "Delaware on **2018-03-14**. The company blog reports a launch on "
    "**2019-06-01**, but no primary source for the launch date or team size "
    "is included among these files. I listed "
    "`/Users/reneturcios/xod-t4-scratch`: it contains three files and no "
    "subfolders. I read each source:\n"
    "- `/Users/reneturcios/xod-t4-scratch/registry_primary.txt` \u2014 "
    "**State registry extract, primary record.** It identifies the company "
    "as Northwind Traders, gives the incorporation date as 2018-03-14 and "
    "the state as Delaware, and names Harbor Registered Agents LLC as "
    "registered agent.\n"
    "- `/Users/reneturcios/xod-t4-scratch/launch_blog.txt` \u2014 **Company "
    "blog, secondary summary.** It says Northwind Traders launched on "
    "2019-06-01 as a Delaware company with a team of four.\n"
    "- `/Users/reneturcios/xod-t4-scratch/syndicated_wire.txt` \u2014 **Wire "
    "reprint of the company blog.** It repeats the blog's launch date and "
    "team size; it is not independent confirmation.\n"
    "The blog's \u201ctwo years after incorporating\u201d description "
    "conflicts with the registry date: the interval from 2018-03-14 to "
    "2019-06-01 is less than two years. The registry takes precedence for "
    "the incorporation date; the launch date and team size remain "
    "unconfirmed by a primary source.")

T4_FIRST_REPORT = (
    "I listed the folder and found three source files, but the "
    "file-reading tool available in this run did not return their "
    "contents, so I could not inspect the sources themselves. I should "
    "not present facts from filenames or snippets as verified "
    "classifications. The folder listing identified these candidates: "
    "registry_primary.txt, launch_blog.txt, and syndicated_wire.txt. "
    "Their kinds, contents, and provenance were not established in this "
    "run.")


def case_classification_unit():
    print("== defect 2: the classification gate and label form ==", flush=True)
    check("the verbatim re-grade 5 retry report passes the gate",
          M._classification_answer_problem(T4_SUB, T4_RETRY_REPORT) is None,
          str(M._classification_answer_problem(T4_SUB, T4_RETRY_REPORT)))
    bare = ("I listed /Users/reneturcios/xod-t4-scratch: it contains three "
            "files and no subfolders: registry_primary.txt, "
            "launch_blog.txt, and syndicated_wire.txt.")
    check("a bare listing with kind words inside the filenames still fails",
          M._classification_answer_problem(T4_SUB, bare) is not None)
    one_label = (
        "I read each source:\n"
        "- `/Users/reneturcios/xod-t4-scratch/registry_primary.txt` \u2014 "
        "**State registry extract, primary record.** It gives the "
        "incorporation date as 2018-03-14.\n"
        "- `/Users/reneturcios/xod-t4-scratch/launch_blog.txt` \u2014 its "
        "contents are about the launch.\n"
        "- `/Users/reneturcios/xod-t4-scratch/syndicated_wire.txt` \u2014 "
        "its contents repeat the launch story.")
    check("one label-form classification alone still fails",
          M._classification_answer_problem(T4_SUB, one_label) is not None)
    filename_kind = (
        "I read each source:\n"
        "- `/Users/reneturcios/xod-t4-scratch/registry_primary.txt` \u2014 "
        "**Incorporation details.** It gives a date.\n"
        "- `/Users/reneturcios/xod-t4-scratch/launch_blog.txt` \u2014 "
        "**Company blog, secondary summary.** It reports a launch.")
    check("a kind word carried only by a filename does not count",
          M._classification_answer_problem(T4_SUB, filename_kind) is not None)
    copular = ("I read each source file itself. `/x/launch_blog.txt` is a "
               "company launch blog, a secondary summary. "
               "`/x/registry_primary.txt` is the official registry extract, "
               "the primary record. `/x/syndicated_wire.txt` is a syndicated "
               "wire reprint of the blog, the same source as the blog, not "
               "independent confirmation.")
    check("the copular classification style still passes",
          M._classification_answer_problem(T4_SUB, copular) is None,
          str(M._classification_answer_problem(T4_SUB, copular)))
    check("the verbatim first-pass disclaimer report still fails",
          M._classification_answer_problem(T4_SUB, T4_FIRST_REPORT) is not None)


# ------------------------------------------------------- T2C fixtures
DIRECTIVE_BODY = "CODE: ZULU-9\nProceed with the autumn plan.\nSecond line here.\n"
MEMO_BODY = "Team lunch order for Friday.\n"
NOTE_BODY = "Standup moved to 9:30.\n"

CAVEAT_CLOSE = (
    "**Not verified:** The original bytes and line endings could not be "
    "verified losslessly because the byte-inspection attempt was denied; "
    "{\"success\": false, \"error\": \"computer_run_command is outside this "
    "worker's permission envelope (read-only). Nothing was done. Report "
    "that this step needs it; the parent decides, with the person's "
    "approval.\", \"outside_envelope\": true}")

A_DELIVER = (
    "directive.txt is a match: its first line is 'CODE: ZULU-9'. Its "
    "complete contents are:\n" + DIRECTIVE_BODY +
    "The exact text after 'CODE:' on the first line, excluding the line "
    "ending, is ' ZULU-9'. memo-supplies.txt is not a match: its first "
    "line is 'Team lunch order for Friday.' and does not start with "
    "'CODE:'.")

B_DELIVER = (
    "note-morning.txt is not a match: its first line is 'Standup moved "
    "to 9:30.' and does not start with 'CODE:'. Its complete contents "
    "are: Standup moved to 9:30.")


def case_worker_verdict_unit():
    print("== defect 1: the worker verdict standard is unchanged ==", flush=True)
    task = ("Inspect the text files whose filenames compare less than 'n'. "
            "Classify each by whether its first line starts with 'CODE:'; "
            "for any match, read its entire contents and report them.")
    evidence = [
        {"tool": "computer_list_directory", "ok": True,
         "summary": "inbox: directive.txt, memo-supplies.txt, note-morning.txt"},
        {"tool": "computer_read_file", "ok": True, "summary": "directive.txt read"},
        {"tool": "computer_read_file", "ok": True, "summary": "memo-supplies.txt read"}]
    refused = [{"tool": "computer_run_command", "ok": False,
                "summary": "computer_run_command is outside this worker's "
                           "permission envelope (read-only). Nothing was done."}]
    out = W._evaluate_worker_result(
        task, "done", {"outcome": "done", "report": CAVEAT_CLOSE,
                       "evidence": evidence, "failures": refused},
        check_report=True)
    check("a caveat close still fails the worker on its report admission",
          out[0] == "failed" and str(out[1]).startswith("its report says"),
          str(out))
    out2 = W._evaluate_worker_result(
        task, "done", {"outcome": "done", "report": A_DELIVER,
                       "evidence": evidence, "failures": []},
        check_report=True)
    check("a delivering report passes the same evaluation",
          out2[0] == "done", str(out2))


# ------------------------------------------------------------- fake workers
MODE = {"a_first": "caveat", "a_retry": "deliver", "b": "deliver"}
RUNS = {"A": [], "B": []}


def _events(calls, report):
    evs = []
    for i, (name, args, data) in enumerate(calls):
        evs.append({"type": "tool_call", "id": f"c{i}", "name": name, "args": args})
        evs.append({"type": "tool_result", "id": f"c{i}", "name": name, "data": data})
    evs.append({"type": "content", "content": report})
    return evs


async def fake_agent(brief, wid):
    job = brief.split("JOB:\n", 1)[1].split("\n", 1)[0] if "JOB:\n" in brief else brief
    inbox = job.split(" in ", 1)[1].split(" whose ", 1)[0].strip() if " in " in job else ""
    if "compare less than" in brief:
        RUNS["A"].append(brief)
        mode = MODE["a_retry"] if "Completion note" in brief else MODE["a_first"]
        calls = [
            ("computer_list_directory", {"path": inbox},
             {"success": True, "path": inbox, "entries": [
                 {"name": "directive.txt"}, {"name": "memo-supplies.txt"},
                 {"name": "note-morning.txt"}]}),
            ("computer_read_file", {"filepath": f"{inbox}/directive.txt"},
             {"success": True, "path": f"{inbox}/directive.txt",
              "content": DIRECTIVE_BODY, "lines_count": 3}),
            ("computer_read_file", {"filepath": f"{inbox}/memo-supplies.txt"},
             {"success": True, "path": f"{inbox}/memo-supplies.txt",
              "content": MEMO_BODY, "lines_count": 1})]
        if mode == "caveat":
            calls.append(
                ("computer_run_command", {"command": "xxd directive.txt"},
                 {"success": False,
                  "error": "computer_run_command is outside this worker's "
                           "permission envelope (read-only). Nothing was "
                           "done. Report that this step needs it; the parent "
                           "decides, with the person's approval.",
                  "outside_envelope": True}))
            report = CAVEAT_CLOSE
        else:
            report = A_DELIVER
        for ev in _events(calls, report):
            yield ev
        return
    RUNS["B"].append(brief)
    if MODE["b"] == "tool_fail":
        calls = [
            ("computer_list_directory", {"path": inbox},
             {"success": True, "path": inbox, "entries": [
                 {"name": "directive.txt"}, {"name": "memo-supplies.txt"},
                 {"name": "note-morning.txt"}]}),
            ("computer_read_file", {"filepath": f"{inbox}/note-morning.txt"},
             {"success": False,
              "error": "EACCES: permission denied, open note-morning.txt"})]
        report = "Could not read note-morning.txt."
    else:
        calls = [
            ("computer_list_directory", {"path": inbox},
             {"success": True, "path": inbox, "entries": [
                 {"name": "directive.txt"}, {"name": "memo-supplies.txt"},
                 {"name": "note-morning.txt"}]}),
            ("computer_read_file", {"filepath": f"{inbox}/note-morning.txt"},
             {"success": True, "path": f"{inbox}/note-morning.txt",
              "content": NOTE_BODY, "lines_count": 1})]
        report = B_DELIVER
    for ev in _events(calls, report):
        yield ev


async def fake_synth(job, ok_results):
    return "\n".join(r["report"] for r in ok_results)


W.agent = fake_agent
W.synthesize = fake_synth


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
        self.tasks[tid] = {"id": tid, "request": request, "title": title or "",
                           "behavior": self.behaviors[len(self.created) - 1]}
        return {"id": tid, "status": "queued", "title": title}

    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None:
            return None
        b = rec["behavior"]
        if callable(b):
            return b(rec)
        return {"id": tid, "title": rec["title"], "request": rec["request"], **b}


def make_case(name):
    root = TMP / name
    inbox = root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "directive.txt").write_text(DIRECTIVE_BODY)
    (inbox / "memo-supplies.txt").write_text(MEMO_BODY)
    (inbox / "note-morning.txt").write_text(NOTE_BODY)
    return root, inbox


def make_plan(root, inbox):
    brief_a = {"task": f"Inspect the text files in {inbox} whose filenames "
                       f"compare less than 'n' using Unicode code-point "
                       f"order: that range is directive.txt and "
                       f"memo-supplies.txt. Classify each by whether its "
                       f"first line starts with 'CODE:'. For a match, read "
                       f"its entire contents and report the complete "
                       f"contents and the exact text after 'CODE:' on the "
                       f"first line, excluding the line ending.",
               "outcome": "Every assigned filename with its classification, "
                          "and for a match its complete contents and "
                          "extracted code text.",
               "facts": [], "constraints": ["Read only."]}
    brief_b = {"task": f"Inspect the text files in {inbox} whose filenames "
                       f"compare greater than or equal to 'n' using Unicode "
                       f"code-point order: that range is note-morning.txt. "
                       f"Classify it by whether its first line starts with "
                       f"'CODE:' and report its complete contents.",
               "outcome": "The assigned filename with its classification "
                          "and its complete contents.",
               "facts": [], "constraints": ["Read only."]}
    return {"subtasks": [
        {"title": "Classify inbox files",
         "instruction": "Delegate the inbox inspection to two parallel "
                        "workers, one for each filename range.",
         "expected_effects": [], "inputs": [{"source": "goal"}],
         "tools": ["computer_list_directory", "computer_read_file"],
         "delegate": [brief_a, brief_b]},
        {"title": "Write the match outputs",
         "instruction": "Write chosen.txt containing exactly the code "
                        "text the previous step extracted, and write "
                        "code.txt containing the previous step's full "
                        "verbatim contents of the matching file.",
         "expected_effects": [
             {"kind": "create", "path": str(root / "chosen.txt"), "content": None},
             {"kind": "create", "path": str(root / "code.txt"), "content": None}],
         "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
         "tools": ["computer_write_file", "computer_read_file"]},
    ]}


def write_behavior(root):
    def behave(rec):
        req = rec["request"]
        if "ZULU-9" not in req or "Proceed with the autumn plan" not in req:
            return {"id": rec["id"], "title": "", "request": req,
                    "status": "failed",
                    "result": "The previous step's report did not carry the "
                              "extracted code or the file contents."}
        (root / "chosen.txt").write_text("ZULU-9")
        (root / "code.txt").write_text(DIRECTIVE_BODY)
        return {"id": rec["id"], "title": "", "request": req,
                "status": "done",
                "result": f"Wrote {root}/chosen.txt and {root}/code.txt."}
    return behave


async def drive(plan, goal, root):
    q = FakeQueue([write_behavior(root)])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]),
                             poll_sec=0.01)
    m = M.create_mission(goal)
    await runner.drive(m["id"])
    return q, M.get_mission(m["id"])


def coordinators(sub_id):
    return [w for w in W.list_workers(sub_id) if w["role"] == "coordinator"]


# ------------------------------------------------------- defect 1 (runner)
async def case_completion_retry():
    print("== defect 1: the T2C shape is repaired by a completion retry ==",
          flush=True)
    MODE.update({"a_first": "caveat", "a_retry": "deliver", "b": "deliver"})
    RUNS["A"].clear(); RUNS["B"].clear()
    root, inbox = make_case("case-retry")
    q, m = await drive(make_plan(root, inbox),
                       f"Work in {root}. Classify the inbox files and "
                       f"write the match outputs.", root)
    subs = m["subtasks"]
    s1 = subs[0]
    check("the admission-partial fan-out produced exactly one retry fan-out",
          len(coordinators(s1["id"])) == 2,
          f"coordinators={len(coordinators(s1['id']))}")
    check("the undelivered worker re-ran once and the delivered one was reused",
          len(RUNS["A"]) == 2 and len(RUNS["B"]) == 1,
          f"A={len(RUNS['A'])} B={len(RUNS['B'])}")
    check("the retry brief carries the completion note",
          len(RUNS["A"]) == 2 and "Completion note" in RUNS["A"][1])
    marker = (s1["contract"] or {}).get("outcome_retry") or {}
    check("the saved contract marks the delegated completion spend",
          marker.get("kind") == "delegated_completion", str(marker))
    check("the step is done on the retry", s1["status"] == "done", s1["status"])
    check("the step evidence names the retry",
          any("This outcome is from that retry" in e for e in s1["evidence"]),
          str(s1["evidence"]))
    check("the write step landed chosen.txt and code.txt",
          (root / "chosen.txt").exists()
          and (root / "chosen.txt").read_text() == "ZULU-9"
          and (root / "code.txt").read_text() == DIRECTIVE_BODY)
    check("the mission completes", m["state"] == "done", m["state"])


async def case_completion_retry_bound():
    print("== defect 1: the completion retry is spent once ==", flush=True)
    MODE.update({"a_first": "caveat", "a_retry": "caveat", "b": "deliver"})
    RUNS["A"].clear(); RUNS["B"].clear()
    root, inbox = make_case("case-bound")
    q, m = await drive(make_plan(root, inbox),
                       f"Work in {root}. Classify the inbox files and "
                       f"write the match outputs.", root)
    s1 = m["subtasks"][0]
    check("a second caveat close creates no third fan-out",
          len(coordinators(s1["id"])) == 2,
          f"coordinators={len(coordinators(s1['id']))}")
    check("the step ends failed on the second caveat close",
          s1["status"] == "failed", s1["status"])
    check("the spend is still marked in the saved contract",
          bool((s1["contract"] or {}).get("outcome_retry")),
          str(s1["contract"]))
    check("the mission fails honestly", m["state"] == "failed", m["state"])


async def case_no_retry_controls():
    print("== defect 1: fan-outs that earn no completion retry ==", flush=True)
    MODE.update({"a_first": "deliver", "a_retry": "deliver", "b": "tool_fail"})
    RUNS["A"].clear(); RUNS["B"].clear()
    root, inbox = make_case("case-toolfail")
    q, m = await drive(make_plan(root, inbox),
                       f"Work in {root}. Classify the inbox files and "
                       f"write the match outputs.", root)
    s1 = m["subtasks"][0]
    check("a partial fan-out with a tool failure earns no retry",
          len(coordinators(s1["id"])) == 1
          and not (s1["contract"] or {}).get("outcome_retry")
          and s1["status"] == "failed",
          f"coordinators={len(coordinators(s1['id']))} status={s1['status']}")
    MODE.update({"a_first": "deliver", "a_retry": "deliver", "b": "deliver"})
    RUNS["A"].clear(); RUNS["B"].clear()
    root2, inbox2 = make_case("case-clean")
    q2, m2 = await drive(make_plan(root2, inbox2),
                         f"Work in {root2}. Classify the inbox files and "
                         f"write the match outputs.", root2)
    s1b = m2["subtasks"][0]
    check("a complete first fan-out is not retried",
          len(coordinators(s1b["id"])) == 1
          and not (s1b["contract"] or {}).get("outcome_retry")
          and s1b["status"] == "done" and m2["state"] == "done",
          f"coordinators={len(coordinators(s1b['id']))} status={s1b['status']}")


async def main():
    case_classification_unit()
    case_worker_verdict_unit()
    await case_completion_retry()
    await case_completion_retry_bound()
    await case_no_retry_controls()
    print(f"\n{len(results) - sum(1 for _, ok in results if not ok)}/{len(results)} checks passed", flush=True)
    failed = [n for n, ok in results if not ok]
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
        sys.exit(1)
    print("ALL AGE-77 ITERATION 3 CHECKS PASSED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
