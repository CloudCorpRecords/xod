"""Grade fix cluster 2 (Tier 5): a mission step whose plan calls for
delegated parallel workers must actually spawn them.

Live failure this suite answers (LIVE-GRADES-2026-11-13, Tier 5 live,
mission-c797b09d0b): the planner authored seven shard-range steps, each
instructing "Delegate these six files to three parallel workers,
assigning each worker a disjoint pair and a brief covering only that
pair", and execution diverged completely: the workers table held 0 rows,
step 1's own task read its six shards itself (7 computer_read_file
calls), step 1's report was then failed as an admitted incompletion
against the full 40-file mission scope, and steps 2-7 were skipped.

Covered here, through the real MissionRunner + workers.coordinate with
a scripted worker agent (the missions layer and the workers store are
the real code; no live LLM):
  - D1 a Tier 5-shaped delegated step spawns one worker per authored
    brief (rows in the workers table, parented coordinator -> subtask),
    the briefs are disjoint (each worker's brief names only its pair),
    the workers run concurrently, NO queue task is created for the
    step, their results are aggregated into the step's claimed result,
    and a later write step consumes that aggregate and lands its file.
  - D2 prose-only delegation (the live plan's exact shape: delegation
    in the instruction, no structured briefs) fails the step honestly
    naming the missing briefs, creates no task, spawns no workers, and
    is never silently self-executed by the parent.
  - D2b the word "Delegate" in a step TITLE alone is not a delegation
    call: a "Delegate parallel shard reads" step whose instruction is
    to collect totals from workers (the T5-D grading shape) still runs
    as an ordinary task.
  - D3 a failed worker stays in the denominator (T5-B): 3 worker rows
    (2 done, 1 failed), the step is recorded failed (never done on a
    partial fan-out), and the failure is itemized in the step evidence.
  - D4 plan validation: duplicate briefs, a single brief, and a
    delegated step declaring file effects are all rejected at planning
    with the reason named.
  - D5 step-scope judgment: a six-file step whose report notes the
    whole 40-file sweep remains incomplete is judged on its own scope
    (done), while a first-person admission about its own scope and an
    unscoped incompletion still fail.

Run: python tests/brain/run_tier5_delegation_test.py
"""
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_t5_delegation_"))
os.environ.update({
    "HOME": str(TMP),
    "XOD_DATA_DIR": str(TMP / "data"),
    "XOD_NO_UPDATE_CHECK": "1",
})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import app.services.missions as M  # noqa: E402
import app.services.task_queue as TQ  # noqa: E402
import app.services.workers as W  # noqa: E402

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


# --------------------------------------------------------------------- fakes
class FakeAsk:
    def __init__(self, replies):
        self.replies = list(replies)

    async def __call__(self, prompt):
        return self.replies.pop(0) if self.replies else "{}"


class FakeQueue:
    """Records every task the runner creates; behaviors may be callables
    receiving the task record (including its assembled request)."""

    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.created = []
        self.tasks = {}

    def create(self, request, title=None, origin=None):
        tid = f"task-t5d-{len(self.created) + 1}"
        self.created.append({"id": tid, "request": request, "title": title})
        idx = len(self.created) - 1
        beh = self.behaviors[idx] if idx < len(self.behaviors) else {"status": "failed", "result": "unexpected task"}
        self.tasks[tid] = {"id": tid, "request": request, "title": title or "", "behavior": beh}
        return {"id": tid, "status": "queued", "title": title}

    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None:
            return None
        b = rec["behavior"]
        if callable(b):
            return b(rec)
        return {"id": tid, "title": rec["title"], "request": rec["request"], **b}


RUNS = {}          # worker id -> {"brief", "start", "end"}
FAIL_MARK = {"name": None}   # shard filename whose pair's worker cannot read it


async def fake_agent(brief, wid):
    rec = RUNS.setdefault(wid, {"brief": brief, "start": time.time(), "end": None})
    job = brief.split("JOB:\n", 1)[1].split("\n", 1)[0].strip()
    await asyncio.sleep(0.3)
    paths = re.findall(r"(/[\w./-]*shard-\d+\.txt)", job)
    if FAIL_MARK["name"] and any(FAIL_MARK["name"] in p for p in paths):
        yield {"type": "tool_result", "name": "computer_read_file",
               "data": {"success": False, "error": f"EACCES: {FAIL_MARK['name']} unreadable"}}
        yield {"type": "content", "content": f"Could not read {FAIL_MARK['name']}."}
        rec["end"] = time.time()
        return
    total = 0
    notes = []
    for p in paths:
        text = Path(p).read_text(encoding="utf-8")
        total += int(re.search(r"value (\d+)", text).group(1))
        m = re.search(r"FLAG: (\S+)", text)
        if m:
            notes.append(f"Found FLAG: {m.group(1)} in {Path(p).name}")
    yield {"type": "tool_result", "name": "computer_read_file",
           "data": {"success": True, "content": f"read {len(paths)} files, sum={total}"}}
    yield {"type": "content",
           "content": f"Pair total for {', '.join(Path(p).name for p in paths)}: {total}. "
                      + " ".join(notes)}
    rec["end"] = time.time()


async def fake_synth(job, ok_results):
    total = 0
    flags = []
    for r in ok_results:
        m = re.search(r"Pair total for [^:]+: (\d+)", r["report"])
        if m:
            total += int(m.group(1))
        flags += re.findall(r"FLAG: (\S+)", r["report"])
    out = f"Step total: {total} from {len(ok_results)} workers."
    if flags:
        out += " Flags: " + ", ".join(flags) + "."
    return out


W.agent = fake_agent
W.synthesize = fake_synth

WS = TMP / "sweep"
WS.mkdir(parents=True, exist_ok=True)
VALUES = {1: 11, 2: 22, 3: 33, 4: 44, 5: 55, 6: 66}
for i, v in VALUES.items():
    extra = "\nFLAG: ORCHID-7" if i == 4 else ""
    (WS / f"shard-{i:02d}.txt").write_text(f"value {v}{extra}\n", encoding="utf-8")
TOTAL = sum(VALUES.values())   # 231


def brief(a, b):
    return {"task": f"Read {WS}/shard-{a:02d}.txt and {WS}/shard-{b:02d}.txt and report "
                    f"the sum of their values and any FLAG line",
            "outcome": f"The pair sum for shards {a:02d} and {b:02d}, with any FLAG noted",
            "facts": [f"The files live in {WS}"],
            "constraints": ["Read only these two files; no other shard is yours"]}


def delegated_step(delegate):
    return {"title": "Sweep shards 01-06",
            "instruction": "Delegate these six files to three parallel workers, assigning "
                           "each worker a disjoint pair and a brief covering only that pair.",
            "expected_effects": [], "inputs": [{"source": "goal"}],
            "tools": ["computer_read_file"], "delegate": delegate}


# --------------------------------------------------------------------- cases
async def case_d1_delegated_step_executes():
    print("== D1: authored delegation spawns workers, aggregates, records rows ==", flush=True)
    out = WS / "sweep_summary.txt"
    plan = {"subtasks": [
        delegated_step([brief(1, 2), brief(3, 4), brief(5, 6)]),
        {"title": "Write sweep summary",
         "instruction": f"Write {out} carrying the step 1 total.",
         "expected_effects": [{"kind": "create", "path": str(out), "content": None}],
         "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
         "tools": ["computer_read_file", "computer_write_file"]},
    ]}

    def write_step(rec):
        m = re.search(r"Step total: (\d+)", rec["request"])
        if not m:
            return {"id": rec["id"], "title": rec["title"], "request": rec["request"],
                    "status": "failed", "result": "No worker total reached this step."}
        total = m.group(1)
        out.write_text(f"Total: {total}\n", encoding="utf-8")
        return {"id": rec["id"], "title": rec["title"], "request": rec["request"],
                "status": "done", "result": f"Wrote {out} with the workers' total."}

    q = FakeQueue([write_step])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]),
                             poll_sec=0.01)
    m = M.create_mission(f"Sweep the six shard files in {WS} and write {out}.")
    t0 = time.time()
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s1, s2 = m["subtasks"][0], m["subtasks"][1]

    check("D1: mission completes done", m["state"] == "done", str(m.get("failure_reason")))
    check("D1: delegated step created NO queue task (only the write step did)",
          len(q.created) == 1 and s1.get("task_id") is None,
          f"created={len(q.created)} s1_task={s1.get('task_id')}")
    check("D1: step 1 done and verified on worker evidence",
          s1["status"] == "done" and s1["verified_status"] == "verified",
          f"{s1['status']}/{s1['verified_status']}")
    coords = [w for w in W.list_workers(s1["id"]) if w["role"] == "coordinator"]
    check("D1: one coordinator row parented on the subtask", len(coords) == 1, str(coords))
    workers = W.list_workers(coords[0]["id"]) if coords else []
    check("D1: workers table holds exactly the 3 authored workers, all done",
          len(workers) == 3 and all(w["state"] == "done" for w in workers),
          str([(w["id"], w["state"]) for w in workers]))
    pairs = [(1, 2), (3, 4), (5, 6)]
    disjoint = len(workers) == 3 and all(
        all(f"shard-{n:02d}" in RUNS[w["id"]]["brief"] for n in pair)
        and all(f"shard-{n:02d}" not in RUNS[w["id"]]["brief"]
                for other in pairs if other != pair for n in other)
        for w, pair in zip(workers, pairs))
    check("D1: each worker's brief covers only its own disjoint pair", disjoint)
    starts = [RUNS[w["id"]]["start"] for w in workers]
    ends = [RUNS[w["id"]]["end"] for w in workers if RUNS[w["id"]]["end"]]
    check("D1: workers ran concurrently (0.3s sleeps overlapped)",
          len(ends) == 3 and max(starts) < min(ends), f"starts={starts} ends={ends}")
    check("D1: worker results aggregated into the step's claimed result",
          f"Step total: {TOTAL}" in (s1.get("claimed") or "")
          and "ORCHID-7" in (s1.get("claimed") or ""),
          (s1.get("claimed") or "")[:200])
    check("D1: step evidence records the worker rows as evidence",
          any("workers table" in e and "3/3 done" in e for e in s1.get("evidence") or []),
          str(s1.get("evidence")))
    check("D1: write step consumed the aggregate and landed its file",
          out.exists() and out.read_text(encoding="utf-8") == f"Total: {TOTAL}\n"
          and s2["status"] == "done")


async def case_d2_prose_only_delegation_fails_honestly():
    print("== D2: prose-only delegation fails honestly, never self-executed ==", flush=True)
    plan = {"subtasks": [
        delegated_step([]),
        {"title": "Write sweep summary", "instruction": "Write the summary.",
         "expected_effects": [], "inputs": [{"source": "prior_step", "ref": "1"}], "tools": []},
    ]}
    q = FakeQueue([])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]),
                             poll_sec=0.01)
    m = M.create_mission(f"Sweep the six shard files in {WS}.")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s1, s2 = m["subtasks"][0], m["subtasks"][1]
    ev = " ".join(s1.get("evidence") or [])
    check("D2: mission failed", m["state"] == "failed", m["state"])
    check("D2: step failed naming the missing structured briefs",
          s1["status"] == "failed" and "no structured worker briefs" in ev, ev[:250])
    check("D2: evidence states the step was NOT self-executed",
          "NOT carried out by the parent itself" in ev, ev[:250])
    check("D2: no queue task was created at all", len(q.created) == 0, str(len(q.created)))
    check("D2: no worker rows exist for the step",
          W.list_workers(s1["id"]) == [], str(W.list_workers(s1["id"])))
    check("D2: the dependent step was skipped, not run", s2["status"] == "skipped", s2["status"])


async def case_d2b_title_only_delegate_word_is_not_a_call():
    print("== D2b: 'Delegate' in a title alone is not a delegation call (T5-D shape) ==", flush=True)
    plan = {"subtasks": [
        {"title": "Delegate parallel shard reads",
         "instruction": "Collect shard totals from workers and report them.",
         "expected_effects": [], "inputs": [{"source": "goal"}], "tools": ["computer_read_file"]},
        {"title": "Close out", "instruction": "Record that the collection ran.",
         "expected_effects": [], "inputs": [{"source": "prior_step", "ref": "1"}], "tools": []},
    ]}
    done = {"status": "done", "result": "Workers reported shard-A=42, shard-B=8, total=50."}
    q = FakeQueue([done, done])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]), poll_sec=0.01)
    m = M.create_mission("Collect the shard totals and close out.")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    check("D2b: the collection step ran as an ordinary task and the mission completed",
          m["state"] == "done" and len(q.created) == 2
          and m["subtasks"][0]["status"] == "done",
          f"state={m['state']} created={len(q.created)} s1={m['subtasks'][0]['status']}")


async def case_d3_failed_worker_in_denominator():
    print("== D3: a failed worker stays in the denominator and fails the step ==", flush=True)
    FAIL_MARK["name"] = "shard-05.txt"
    try:
        plan = {"subtasks": [delegated_step([brief(1, 2), brief(3, 4), brief(5, 6)])]}
        # validate_plan requires >= 2 subtasks; pad with an independent closer.
        plan["subtasks"].append(
            {"title": "Note the outcome", "instruction": "Record that the sweep ran.",
             "expected_effects": [], "inputs": [{"source": "goal"}], "tools": []})
        q = FakeQueue([])
        runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]),
                                 poll_sec=0.01)
        m = M.create_mission(f"Sweep the six shard files in {WS}.")
        await runner.drive(m["id"])
        m = M.get_mission(m["id"])
        s1 = m["subtasks"][0]
        coords = [w for w in W.list_workers(s1["id"]) if w["role"] == "coordinator"]
        workers = W.list_workers(coords[0]["id"]) if coords else []
        ev = " ".join(s1.get("evidence") or [])
        check("D3: step recorded failed, never done on a partial fan-out",
              s1["status"] == "failed", s1["status"])
        coord_result = (coords[0].get("result") or {}) if coords else {}
        judged = coord_result.get("workers") or []
        check("D3: all 3 workers remain as rows, coordinator denominator 2 done / 1 failed",
              len(workers) == 3
              and sum(1 for w in judged if w["outcome"] == "done") == 2
              and sum(1 for w in judged if w["outcome"] == "failed") == 1,
              str([(w["id"], w["state"]) for w in workers]) + " judged=" + str(judged))
        check("D3: failure itemized in step evidence with its worker task",
              "Worker did not deliver" in ev and "shard-05" in ev, ev[:300])
        check("D3: step evidence keeps the explicit denominator (2/3 done)",
              "2/3 done" in ev, ev[:300])
    finally:
        FAIL_MARK["name"] = None


def case_d4_plan_validation():
    print("== D4: plan validation of delegate briefs ==", flush=True)
    base = {"title": "Sweep", "instruction": "Delegate the files to parallel workers.",
            "expected_effects": [], "inputs": [], "tools": []}
    other = {"title": "Close", "instruction": "Close out.", "expected_effects": [],
             "inputs": [], "tools": []}
    dup = {"subtasks": [{**base, "delegate": [brief(1, 2), brief(1, 2)]}, other]}
    _, p = M.validate_plan(dup)
    check("D4: duplicate (non-disjoint) briefs rejected", any("disjoint" in x for x in p), str(p))
    single = {"subtasks": [{**base, "delegate": [brief(1, 2)]}, other]}
    _, p = M.validate_plan(single)
    check("D4: a single brief rejected (not parallel delegation)",
          any("at least two" in x for x in p), str(p))
    fx = {"subtasks": [{**base, "delegate": [brief(1, 2), brief(3, 4)],
                        "expected_effects": [{"kind": "create", "path": "/tmp/x.txt",
                                              "content": None}]}, other]}
    _, p = M.validate_plan(fx)
    check("D4: delegated step declaring file effects rejected (workers are read-only)",
          any("read-only" in x for x in p), str(p))
    good = {"subtasks": [{**base, "delegate": [brief(1, 2), brief(3, 4)]}, other]}
    plan, p = M.validate_plan(good)
    check("D4: a valid delegated plan validates and keeps its briefs",
          plan is not None and len(plan["subtasks"][0]["delegate"]) == 2, str(p))


def case_d5_step_scope_judgment():
    print("== D5: a shard step is judged on its own scope, not the mission's ==", flush=True)
    sub = {"title": "Sweep shards 01-06",
           "instruction": "Check these six files and report their values.",
           "expected_effects": [], "contract": {}}
    live_report = ("Checked shard-01 through shard-06 and recorded all six values. "
                   "Only these six files were checked; the 40-file sweep remains incomplete.")
    out = M._outcome_from_task({"status": "done", "result": live_report}, sub)
    check("D5: mission-scope incompletion note does not fail the step's own scope",
          out["status"] == "done", str(out))
    own = M._outcome_from_task(
        {"status": "done", "result": "I could not complete checking these six files."}, sub)
    check("D5: a first-person admission about its own scope still fails",
          own["status"] == "failed", str(own))
    unscoped = {"title": "Check the files", "instruction": "Check the files.",
                "expected_effects": [], "contract": {}}
    out2 = M._outcome_from_task(
        {"status": "done", "result": "The 40-file sweep remains incomplete."}, unscoped)
    check("D5: with no enumerated step scope, an incompletion still fails",
          out2["status"] == "failed", str(out2))


async def main():
    await case_d1_delegated_step_executes()
    await case_d2_prose_only_delegation_fails_honestly()
    await case_d2b_title_only_delegate_word_is_not_a_call()
    await case_d3_failed_worker_in_denominator()
    case_d4_plan_validation()
    case_d5_step_scope_judgment()
    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed ==========", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        return 1
    print("ALL TIER 5 DELEGATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
