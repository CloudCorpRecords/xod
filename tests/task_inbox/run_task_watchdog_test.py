"""
Task inbox regression test: real cancel and the progress watchdog.

The incident (task-9bb5a62674): a task sat in "working" for about 40 minutes
producing nothing. POST cancel returned {"success": true, "stopping": true}
twice and the task never left "working", because the graceful cancel flag is
only checked between steps and a hung await inside a step never sees it. The
doctor route (recover_stuck) did clear it in the end, but only after the row
was left untouched for 1200s: stuck() keyed on updated_at, and every cancel
attempt refreshed updated_at, so trying to cancel masked the wedge.

Covered here, against isolated queues and databases like the sibling suites:
  (a) a run that awaits forever is force-cancelled after the cancel grace
      period, and a second cancel forces immediately; both end "cancelled"
      with a history note saying the cancel had to be forced
  (b) a working task with no genuine progress is flagged stalled (flag and
      reason on the task dict, history entry, "Stalled: no progress for N
      minutes" now-text), while a task emitting progress is not
  (c) cancel notes and other _update pokes do NOT reset the progress clock
      (regression for the masking bug): stuck() still sees the task
  (d) past twice the stall threshold the watchdog auto-recovers the task
      (worker cancelled, task failed with the honest reason) and the queue
      still processes the next task

Run: .venv/bin/python tests/task_inbox/run_task_watchdog_test.py
"""
import asyncio, os, sys, tempfile, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
TMP = Path(tempfile.mkdtemp(prefix="xod_task_watchdog_"))
os.environ["XOD_DATASET_DB"] = str(_DATA / "test_runs" / "flywheel_test.db")
import app.services.task_queue as TQ
import app.services.browser_sandbox as BS
import app.agent.researcher as RS

MAIN_DB, ALT_DB = TMP / "tasks.db", TMP / "tasks2.db"
TQ.DB_PATH, TQ.SHOTS_DIR = MAIN_DB, TMP / "shots"
TQ.notify = lambda title, msg: None
if hasattr(TQ, "_phone"):
    TQ._phone = lambda task, kind: None


async def _boom():
    raise RuntimeError("Background Chrome failed to start or open remote debugging port.")


BS.browser_sandbox_service.ensure_browser_running = _boom


class _HangResearcher:
    """Emits one progress step, then awaits forever: the wedged run."""

    async def run_research_stream(self, messages):
        yield {"type": "status", "content": "✓ started the step"}
        while True:
            await asyncio.sleep(3600)


class _QuickResearcher:
    async def run_research_stream(self, messages):
        yield {"type": "content", "content": "quick-done"}


results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


async def wait_for(q, task_id, statuses, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = q.get(task_id)
        if t and t["status"] in statuses:
            return t
        await asyncio.sleep(0.1)
    return q.get(task_id)


def age_progress(task_id, seconds_ago):
    """Sets 'last genuine progress was seconds_ago in the past' in whichever
    progress clock this build has (the point of (c) is which clock that is)."""
    at = time.time() - seconds_ago
    c = TQ._conn()
    cols = [r[1] for r in c.execute("PRAGMA table_info(tasks)").fetchall()]
    if "last_progress_at" in cols:
        c.execute("UPDATE tasks SET last_progress_at = ? WHERE id = ?", (at, task_id))
    c.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (at, task_id))
    c.commit()
    c.close()
    return at


def history_texts(t):
    return [str(h.get("text", "")) for h in (t.get("history") or [])]


async def main():
    # ---------------------------------------------------------- (a) real cancel
    print("== (a) cancel of a hung run ==", flush=True)
    TQ.DB_PATH = MAIN_DB
    q = TQ.TaskQueue()
    q.start()
    RS.AgenticResearcher = _HangResearcher

    TQ.CANCEL_GRACE_SEC = 0.5  # tiny grace via the named constant
    t1 = q.create("Hang forever, grace case")
    w = await wait_for(q, t1["id"], ("working",), timeout=10)
    check("grace case: task is working", w and w["status"] == "working", str(w and w["status"]))
    r = await q.cancel(t1["id"])
    check("grace case: first cancel keeps graceful semantics", r.get("success") and r.get("stopping"), str(r))
    done = await wait_for(q, t1["id"], ("cancelled", "failed", "done"), timeout=8)
    check("grace case: force-cancelled after the grace period", done and done["status"] == "cancelled",
          f"status={done and done['status']}")
    check("grace case: finished_at is set", bool(done and done.get("finished_at")))
    check("grace case: history records the cancel had to be forced",
          any("forc" in h.lower() for h in history_texts(done or {})), str(history_texts(done or {})))

    TQ.CANCEL_GRACE_SEC = 3600  # grace cannot fire; only a second cancel can force
    t2 = q.create("Hang forever, second cancel case")
    w = await wait_for(q, t2["id"], ("working",), timeout=10)
    check("second case: task is working", w and w["status"] == "working", str(w and w["status"]))
    await q.cancel(t2["id"])
    r2 = await q.cancel(t2["id"])
    done2 = await wait_for(q, t2["id"], ("cancelled", "failed", "done"), timeout=8)
    check("second case: second cancel forces immediately", done2 and done2["status"] == "cancelled",
          f"second={r2} status={done2 and done2['status']}")
    check("second case: history records the cancel had to be forced",
          any("forc" in h.lower() for h in history_texts(done2 or {})), str(history_texts(done2 or {})))
    TQ.CANCEL_GRACE_SEC = 30

    # ------------------------------------------- (b) + (c) stalled flag, masking
    print("== (b/c) watchdog stall flag and the masking regression ==", flush=True)
    TQ.DB_PATH = ALT_DB
    q2 = TQ.TaskQueue()  # never started: pure watchdog bookkeeping, no worker

    quiet = q2.create("No progress task")
    q2._update(quiet["id"], status="working", now="Starting")
    q2._progress(quiet["id"], "✓ one real step")
    aged_to = age_progress(quiet["id"], 1000)
    # (c) the masking bug: poking the row (what a cancel attempt does) must
    # not reset the progress clock, and stuck() must still see the task.
    q2._update(quiet["id"], note="Stopping after the current step", now="Stopping…")
    q2._update(quiet["id"], note="Stopping after the current step", now="Stopping…")
    after = q2.get(quiet["id"])
    lp = (after or {}).get("last_progress_at")
    check("(c) progress clock exists and is separate from updated_at", lp is not None,
          f"keys={sorted((after or {}).keys())}")
    check("(c) cancel-note pokes do not reset the progress clock",
          lp is not None and abs(lp - aged_to) < 1.0, f"last_progress_at={lp} aged_to={aged_to}")
    stuck_ids = [t["id"] for t in q2.stuck(120)] if hasattr(q2, "stuck") else []
    check("(c) stuck() still sees the task after cancel pokes", quiet["id"] in stuck_ids, f"stuck={stuck_ids}")

    lively = q2.create("Progressing task")
    q2._update(lively["id"], status="working", now="Starting")
    q2._progress(lively["id"], "✓ a fresh step just now")

    if hasattr(q2, "run_watchdog"):
        await q2.run_watchdog(stall_sec=300, recover_sec=100000)
    qt, lt = q2.get(quiet["id"]), q2.get(lively["id"])
    check("(b) no-progress task is flagged stalled", bool(qt and qt.get("stalled")), str(qt and qt.get("stalled")))
    check("(b) stalled reason is on the task dict and names the silence",
          "no progress" in str((qt or {}).get("stalled_reason") or "").lower(),
          str((qt or {}).get("stalled_reason")))
    check("(b) now-text says it stalled", str((qt or {}).get("now") or "").startswith("Stalled: no progress for"),
          str((qt or {}).get("now")))
    check("(b) history has the stalled entry",
          any(h.startswith("Stalled: no progress for") for h in history_texts(qt or {})),
          str(history_texts(qt or {})))
    check("(b) a task emitting progress is not stalled", not (lt or {}).get("stalled"),
          str((lt or {}).get("stalled_reason")))
    check("(b) stalled task is still working (flagged, not killed, at 1x)",
          (qt or {}).get("status") == "working", str((qt or {}).get("status")))

    # The doctor route must agree with the watchdog: the same task, poked by
    # cancel attempts and never progressed since, is recoverable right away,
    # not only after updated_at ages past the threshold untouched.
    fixed = await q2.recover_stuck(120)
    qt = q2.get(quiet["id"])
    check("(c) doctor path: recover_stuck clears the poked-but-wedged task",
          fixed == ["No progress task"] and (qt or {}).get("status") == "failed",
          f"fixed={fixed} status={(qt or {}).get('status')}")

    # ------------------------------------------------- (d) auto-recovery at 2x
    print("== (d) auto-recovery past twice the threshold ==", flush=True)
    TQ.DB_PATH = MAIN_DB
    RS.AgenticResearcher = _HangResearcher
    t3 = q.create("Hang forever, watchdog recovery case")
    w = await wait_for(q, t3["id"], ("working",), timeout=10)
    check("recovery case: task is working", w and w["status"] == "working", str(w and w["status"]))
    if hasattr(q, "_touch_progress"):
        check("(d) watchdog recovery entry point exists", hasattr(q, "run_watchdog"))
        q._touch_progress(t3["id"], at=time.time() - 5000)
        await q.run_watchdog(stall_sec=60, recover_sec=120)
    else:
        check("(d) watchdog recovery entry point exists", False, "no _touch_progress/run_watchdog")
    dead = await wait_for(q, t3["id"], ("failed", "cancelled", "done"), timeout=10)
    check("(d) no-progress task is auto-recovered as failed", dead and dead["status"] == "failed",
          f"status={dead and dead['status']}")
    check("(d) failure says honestly that it stopped making progress",
          "progress" in str((dead or {}).get("result") or "").lower()
          or any("progress" in h.lower() for h in history_texts(dead or {})),
          str((dead or {}).get("result")))
    RS.AgenticResearcher = _QuickResearcher
    t4 = q.create("Reply quickly after the wedged one")
    nxt = await wait_for(q, t4["id"], ("done", "failed", "cancelled"), timeout=15)
    check("(d) the queue still processes the next task", nxt and nxt["status"] == "done",
          f"status={nxt and nxt['status']} result={(nxt or {}).get('result')}")

    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed ==========", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
