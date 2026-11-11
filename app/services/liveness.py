"""
The activity timer at the heart of Xod's self-review (design settled
2026-11-10, after the AGE-7 re-grade caught a total server wedge:
hello unanswered for over five minutes, the main thread parked in
open(), four worker threads parked on locks, the process alive with
near-zero CPU and nothing anywhere that noticed).

The mechanism, as designed: it is not a separate prober program.
Every time something comes through (a request, a job, a step, an
event) a timer starts or resets. If the window passes and nothing has
come through, the timer triggers a review of what is going on, and
whatever supervises this process restarts it if the review finds it
dead or wedged. Diagnosis first; a restart is the review's decision,
never a blind kill on a timer alone.

This module is the timer, kept inside the app:

- Activity is concrete, from the code's own choke points. A request
  the server actually processed (app/main.py's middleware), an event
  (services/event_log.emit, which every subsystem already flows
  through), a task starting (services/task_queue._update reaching
  "working", and services/orchestrator._claim_next, whose direct SQL
  claim is where stepwise tasks enter "working"), a task genuinely
  progressing (task_queue._touch_progress, the one clock the task
  watchdog already trusts), a task leaving "working".
- The timer's state is a ledger on disk (DATA_DIR/activity.json),
  rewritten atomically at most once a second: this process's pid, when
  it booted, the last activity and what it was, and the work in flight
  with each item's last genuine progress time.
- Nothing in this module polls, loops, or restarts anything. The
  review lives with the supervisor that owns this process (on the
  desktop, the shell's backend supervision in electron/liveness.js),
  because the reviewer has to survive the wedge it reviews: the
  AGE-7 wedge parked this process's event loop and its worker
  threads, so a review running in here would have parked with it.
  What this module guarantees instead is the evidence the review
  reads: if this process wedges, the ledger stops advancing, and the
  staleness itself, next to the in-flight work it names, is the
  signal. A wedged flush fails safe in the same direction.

An idle Xod is quiet by construction: with no work in flight and no
recent activity, a stale ledger is just an idle machine, and the
review side stands down on exactly that condition.
"""
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.liveness")

LEDGER_PATH = DATA_DIR / "activity.json"
FLUSH_EVERY_SEC = 1.0

_lock = threading.Lock()
_boot_at = time.time()
_last_activity_at: float = _boot_at
_last_activity_kind: str = "boot"
_last_activity_detail: str = ""
_in_flight: Dict[str, Dict[str, Any]] = {}
_last_flush_at: float = 0.0


def _snapshot_locked() -> Dict[str, Any]:
    return {
        "pid": os.getpid(),
        "boot_at": _boot_at,
        "last_activity_at": _last_activity_at,
        "last_activity_kind": _last_activity_kind,
        "last_activity_detail": _last_activity_detail,
        "in_flight": {k: dict(v) for k, v in _in_flight.items()},
        "updated_at": time.time(),
    }


def snapshot() -> Dict[str, Any]:
    """The timer's current state, as the ledger on disk carries it."""
    with _lock:
        return _snapshot_locked()


def _flush_locked(force: bool = False) -> None:
    global _last_flush_at
    now = time.time()
    if not force and now - _last_flush_at < FLUSH_EVERY_SEC:
        return
    _last_flush_at = now
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LEDGER_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_snapshot_locked()), encoding="utf-8")
        os.replace(tmp, LEDGER_PATH)
    except Exception:
        # The ledger is evidence for the reviewer, never a reason to
        # break the work itself. A failed flush leaves the previous
        # ledger in place, stale, which is the fail-safe direction.
        logger.debug("liveness ledger flush failed", exc_info=True)


def _touch_locked(kind: str, detail: str = "") -> None:
    global _last_activity_at, _last_activity_kind, _last_activity_detail
    _last_activity_at = time.time()
    _last_activity_kind = kind
    _last_activity_detail = detail[:200]


def touch(kind: str, detail: str = "") -> None:
    """Records that something came through: a request the server
    processed, an event emitted, any sign of life. Resets the timer."""
    try:
        with _lock:
            _touch_locked(kind, detail)
            _flush_locked()
    except Exception:
        pass   # activity bookkeeping never breaks the activity


def work_started(work_id: str, kind: str = "task", label: str = "") -> None:
    """A unit of work is now in flight (a task reached "working")."""
    try:
        with _lock:
            now = time.time()
            _in_flight[str(work_id)] = {"kind": kind, "label": label[:200],
                                        "since": now, "last_progress_at": now}
            _touch_locked("work_started", str(work_id))
            _flush_locked(force=True)
    except Exception:
        pass


def work_progressed(work_id: str) -> None:
    """In-flight work genuinely progressed (a step advance, tool
    activity: the same events task_queue._touch_progress trusts)."""
    try:
        with _lock:
            item = _in_flight.get(str(work_id))
            if item is not None:
                item["last_progress_at"] = time.time()
            _touch_locked("work_progress", str(work_id))
            _flush_locked()
    except Exception:
        pass


def work_finished(work_id: str) -> None:
    """A unit of work left flight (done, blocked, paused, cancelled)."""
    try:
        with _lock:
            _in_flight.pop(str(work_id), None)
            _touch_locked("work_finished", str(work_id))
            _flush_locked(force=True)
    except Exception:
        pass


def read_ledger(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Reads the ledger back from disk (the reviewer's view). None
    when there is no ledger yet or it cannot be read."""
    try:
        return json.loads(Path(path or LEDGER_PATH).read_text(encoding="utf-8"))
    except Exception:
        return None


def last_progress_at(ledger: Dict[str, Any]) -> float:
    """When anything last genuinely moved, from a ledger snapshot:
    the newest of the process's last activity and every in-flight
    item's last progress. The review compares this against its clock."""
    latest = float(ledger.get("last_activity_at") or 0.0)
    for item in (ledger.get("in_flight") or {}).values():
        latest = max(latest, float(item.get("last_progress_at") or 0.0))
    return latest
