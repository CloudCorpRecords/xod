"""Grade-fix cluster 4 (Tier 7): manual-run race + prior contents for watch tasks.

Pins the two defects found by the 2026-11-13 live grade (LIVE-GRADES-2026-11-13.md,
"Tier 7 live", attempts 1-2), through the real routines scheduler, task_queue and
delivery outbox with scripted task outcomes (no live LLM):

  A (run-record race): POST /api/routines/{id}/run returns at task creation and
     run_now overwrote routines.last_task_id/last_run_id unconditionally, while
     _follow_up only ever settled that one pair: five same-second manual runs of
     one routine left four run records with no outcome minutes later (orphaned).
     Fix contract: runs of one routine serialize; a run requested while one is
     in flight is recorded as queued (a first-class run record), starts when the
     in-flight run settles, and every run record reaches a terminal, truthful
     state (finished outcome, or 'superseded' if the routine was turned off
     before the queued run could start). No orphans, ever.
  B (prior contents): run-over-run state reached the framework (result hashes)
     but never the watch task, so an instruction asking the model to compare
     against "what you saw on the previous check" failed every cycle, honestly,
     and fired the 3-failure auto-disable. Fix contract: on cycle 2+, the watch
     task's request carries the previous completed check's reported contents
     (bounded, labelled with the result hash the framework already tracks), so a
     comparison-shaped instruction is answerable from the task input alone.
     The honesty contract is untouched: with prior contents delivered, a
     'WATCH: could not check' still classifies failed (T7-C) and three genuine
     failures in a row still auto-disable the routine (T7-E).

Run: python tests/brain/run_tier7_watchfix_test.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_t7_fix_"))
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

import app.services.task_queue as TQ  # noqa: E402
import app.services.routines as RT  # noqa: E402
import app.services.delivery as DL  # noqa: E402
from app.services import storage  # noqa: E402

PINGS = []
TQ.notify = lambda title, msg: PINGS.append(("task", title, msg))
TQ._phone = lambda task, kind: None
DL._ping = lambda title, text: PINGS.append(("delivery", title, text))
if hasattr(storage, "init_db"):
    storage.init_db()
q = TQ.task_queue = TQ.TaskQueue()

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


# --------------------------------------------------------------------- A: the race

def case_a1_five_same_second_runs_no_orphans():
    print("== A1: five same-second manual runs serialize; no orphaned run records ==", flush=True)
    r = RT.create("Race Watch A", "Report the watched file's current contents.",
                  schedule={"kind": "every", "hours": 1}, kind="watch")
    rid = r["id"]
    outs = [RT.run_now(rid, reason="you asked") for _ in range(5)]
    check("A1: first run starts a task immediately", bool((outs[0].get("task") or {}).get("id")))
    check("A1: runs 2-5 are recorded as queued, not started on top of the in-flight run",
          all(o.get("queued") is True and not o.get("task") for o in outs[1:]),
          str([{"queued": o.get("queued"), "task": bool(o.get("task"))} for o in outs[1:]]))
    recs = DL.runs(rid, 50)
    check("A1: all five requests have a run record", len(recs) == 5, str(len(recs)))
    check("A1: exactly one run is in flight; four are queued",
          len(DL.open_runs(rid)) == 1 and len(DL.queued_runs(rid)) == 4,
          f"open={len(DL.open_runs(rid))} queued={len(DL.queued_runs(rid))}")

    seen_tasks = []
    cur = outs[0]["task"]["id"]
    for i in range(5):
        seen_tasks.append(cur)
        q._finish(cur, "done", f"WATCH: found\ncontents version {i + 1} token-{i + 1}", note="Finished")
        RT._follow_up()
        DL.deliver_pending()
        cur = (RT.get(rid) or {}).get("last_task_id")
    recs = DL.runs(rid, 50)
    check("A1: the five runs executed as five distinct tasks, one at a time",
          len(set(seen_tasks)) == 5, str(seen_tasks))
    check("A1: every run record reached a terminal state (no orphans)",
          len(recs) == 5 and all(x["finished_at"] is not None and x["outcome"] for x in recs),
          str([(x["id"], x["finished_at"], x["outcome"]) for x in recs]))
    check("A1: every run's outcome is the truthful 'changed'",
          all(x["outcome"] == "changed" for x in recs), str([x["outcome"] for x in recs]))
    check("A1: routine run counter counts all five runs", (RT.get(rid) or {}).get("runs") == 5,
          str((RT.get(rid) or {}).get("runs")))


def case_a2_run_requested_while_previous_unsettled_queues():
    print("== A2: a run requested after its predecessor's task ended but before settle still queues ==", flush=True)
    r = RT.create("Race Watch A2", "Report the watched file's current contents.",
                  schedule={"kind": "every", "hours": 1}, kind="watch")
    rid = r["id"]
    o1 = RT.run_now(rid)
    q._finish(o1["task"]["id"], "done", "WATCH: found\nfirst contents", note="Finished")
    o2 = RT.run_now(rid)   # run record for run 1 is still open: this must queue, not overlap
    check("A2: second run queues behind the unsettled first run",
          o2.get("queued") is True and not o2.get("task"), str(o2.keys()))
    RT._follow_up()
    DL.deliver_pending()
    started = RT.get(rid)
    check("A2: settling the first run starts the queued run",
          bool(started.get("last_task_id")) and started["last_task_id"] != o1["task"]["id"],
          str(started.get("last_task_id")))
    q._finish(started["last_task_id"], "done", "WATCH: found\nsecond contents", note="Finished")
    RT._follow_up()
    DL.deliver_pending()
    recs = DL.runs(rid, 50)
    check("A2: both run records terminal, none orphaned",
          len(recs) == 2 and all(x["finished_at"] is not None for x in recs),
          str([(x["finished_at"], x["outcome"]) for x in recs]))


def case_a3_queued_run_superseded_when_routine_turned_off():
    print("== A3: a queued run whose routine is turned off ends superseded, never orphaned ==", flush=True)
    r = RT.create("Race Watch A3", "Report the watched file's current contents.",
                  schedule={"kind": "every", "hours": 1}, kind="watch")
    rid = r["id"]
    o1 = RT.run_now(rid)
    o2 = RT.run_now(rid)
    check("A3: second run queued", o2.get("queued") is True, str(o2.keys()))
    RT.update(rid, enabled=False)
    q._finish(o1["task"]["id"], "done", "WATCH: found\nonly contents", note="Finished")
    RT._follow_up()
    DL.deliver_pending()
    recs = {x["id"]: x for x in DL.runs(rid, 50)}
    qrec = recs.get(o2.get("run_id") or "")
    check("A3: queued run record is terminal with outcome 'superseded'",
          qrec is not None and qrec["finished_at"] is not None and qrec["outcome"] == "superseded"
          and qrec["delivery_state"] == "suppressed",
          str(qrec))
    check("A3: no task was started for the superseded run",
          (RT.get(rid) or {}).get("last_task_id") == o1["task"]["id"])


# --------------------------------------------------------------------- B: prior contents

def _cycle(rid, report):
    out = RT.run_now(rid, reason="schedule")
    tid = out["task"]["id"]
    req = q.get(tid)["request"]
    q._finish(tid, "done", report, note="Finished")
    RT._follow_up()
    DL.deliver_pending()
    return req


def case_b1_prior_contents_reach_the_task():
    print("== B1: cycle 2+ task input carries the previous check's contents + hash ==", flush=True)
    r = RT.create("Contents Watch B",
                  "Compare the file against what you saw on the previous check and report any change.",
                  schedule={"kind": "every", "hours": 1}, kind="watch")
    rid = r["id"]
    req1 = _cycle(rid, "WATCH: found\ngreen 1")
    check("B1: first cycle has no prior-contents block (nothing to compare against yet)",
          "What you found on the previous check" not in req1,
          req1[-300:])
    run1 = DL.runs(rid, 1)[0]
    req2 = _cycle(rid, "WATCH: found\namber 42")
    check("B1: second cycle's task input contains the first check's reported contents",
          "green 1" in req2, req2[-400:])
    check("B1: second cycle's task input carries the framework's result hash for that check",
          bool(run1["result_hash"]) and run1["result_hash"] in req2,
          f"hash={run1['result_hash']}")
    run2 = DL.runs(rid, 1)[0]
    req3 = _cycle(rid, "WATCH: found\namber 42")
    check("B1: third cycle compares against the SECOND check (amber 42), hash updated",
          "amber 42" in req3 and bool(run2["result_hash"]) and run2["result_hash"] in req3,
          req3[-400:])


def case_b2_prior_contents_bounded():
    print("== B2: prior contents are bounded in the task input ==", flush=True)
    r = RT.create("Contents Watch B2", "Report the watched page's contents.",
                  schedule={"kind": "every", "hours": 1}, kind="watch")
    rid = r["id"]
    big = "start-sentinel " + ("x" * 6000) + " end-sentinel"
    _cycle(rid, f"WATCH: found\n{big}")
    out = RT.run_now(rid)
    req = q.get(out["task"]["id"])["request"]
    check("B2: the head of the prior contents is delivered", "start-sentinel" in req)
    check("B2: the tail beyond the bound is not delivered (truncation is marked)",
          "end-sentinel" not in req and "truncated" in req, f"req len={len(req)}")
    q._finish(out["task"]["id"], "done", "WATCH: nothing new", note="Finished")
    RT._follow_up()
    DL.deliver_pending()


def case_b3_honesty_contract_survives_prior_contents():
    print("== B3: with prior contents delivered, genuine could-not-check still fails + auto-disables (T7-C/T7-E) ==", flush=True)
    r = RT.create("Contents Watch B3",
                  "Compare the file against what you saw on the previous check and report any change.",
                  schedule={"kind": "every", "hours": 1}, kind="watch")
    rid = r["id"]
    _cycle(rid, "WATCH: found\ngreen 1")   # now a prior check exists
    for i in range(DL.MAX_CONSECUTIVE_FAILURES):
        out = RT.run_now(rid)
        req = q.get(out["task"]["id"])["request"]
        if i == 0:
            check("B3: the failing cycles DO receive the prior contents (failure is genuine, not missing state)",
                  "green 1" in req, req[-300:])
        q._finish(out["task"]["id"], "done",
                  "WATCH: could not check whether the file changed: the file was not present in the directory listing",
                  note="Finished")
        RT._follow_up()
        DL.deliver_pending()
    recs = DL.runs(rid, 10)
    fails = [x for x in recs if x["outcome"] == "failed"]
    check("B3: every could-not-check cycle classified 'failed', never 'no_change'",
          len(fails) >= DL.MAX_CONSECUTIVE_FAILURES and all(x["outcome"] != "no_change" for x in recs),
          str([(x["outcome"]) for x in recs]))
    check("B3: routine auto-disabled after 3 consecutive genuine failures",
          (RT.get(rid) or {}).get("enabled") is False)


def main():
    case_a1_five_same_second_runs_no_orphans()
    case_a2_run_requested_while_previous_unsettled_queues()
    case_a3_queued_run_superseded_when_routine_turned_off()
    case_b1_prior_contents_reach_the_task()
    case_b2_prior_contents_bounded()
    case_b3_honesty_contract_survives_prior_contents()
    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed ==========", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        return 1
    print("ALL TIER 7 WATCH-FIX CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
