"""
The event log: one append-only record of everything this Xod does, kept in the app's data
folder (DATA_DIR/event_log.jsonl) so an attached operator agent (the timeline tool in
services/harness.py) can read back what actually happened, in order, with the ids that tie
a mission, its subtasks, their tasks, the tool calls inside them and the model calls that
powered them into one correlated picture.

Events are written by the choke points that already persist the work: the task queue's
status writer, the missions store, the tool executor's step log, the model-call recorder,
the approval log, the Crew, the beacon, the Partner client and the config endpoint. Each
emitter names its actor and attaches the correlation ids it knows. Nothing is scraped from
UI state, and an event is written only when the thing it describes has actually been
persisted by its own subsystem: the log observes the stores, it never invents activity.

Writing never blocks or fails the work: any error here is logged and swallowed, the rule
the usage ledger (services/usage_ledger.py) already follows. Payloads are kept small on
purpose (long strings are cut): the log is an index into the real records, which the
harness deep reads (task_read, mission_read, crew_read) return in full.

The file is bounded: past MAX_LINES lines the oldest events are dropped by a later append.
The timeline is a window on recent work, not an archive; the underlying task, mission,
crew and ledger records are the archive. Sequence numbers keep rising across a trim, so a
reader's cursor never goes backwards.
"""
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.event_log")

LOG = DATA_DIR / "event_log.jsonl"
MAX_LINES = 30000
KEEP_LINES = 20000
TRIM_EVERY = 256            # appends between file-size checks
MAX_STR = 500               # payload strings are cut here; the deep reads carry the full text

_lock = threading.Lock()
_seq: Optional[int] = None  # last sequence number this process used, seeded from the file
_appends = 0


def _seed_seq() -> int:
    """The highest seq already in the file (0 when there is none), so a restarted
    process continues the sequence instead of restarting it."""
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in reversed(lines):
        try:
            rec = json.loads(line)
            if isinstance(rec, dict) and isinstance(rec.get("seq"), int):
                return rec["seq"]
        except ValueError:
            continue
    return 0


def _small(value: Any, depth: int = 0) -> Any:
    """A payload value, bounded: strings cut at MAX_STR, containers one level deep,
    anything exotic reduced to its string shape (also cut)."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_STR]
    if depth >= 2:
        return str(value)[:MAX_STR]
    if isinstance(value, dict):
        return {str(k)[:80]: _small(v, depth + 1) for k, v in list(value.items())[:20]}
    if isinstance(value, (list, tuple)):
        return [_small(v, depth + 1) for v in list(value)[:20]]
    return str(value)[:MAX_STR]


def ids_from_origin(origin: Any) -> Dict[str, str]:
    """The correlation ids inside an activity origin ("task:<id>", "mission:<id>").
    Other origins (a chat, Xod on its own) carry no ids: the origin itself rides in
    the payload instead, so nothing is ever guessed."""
    out: Dict[str, str] = {}
    if isinstance(origin, str):
        if origin.startswith("task:"):
            out["task_id"] = origin[5:]
        elif origin.startswith("mission:"):
            out["mission_id"] = origin[8:]
    return out


def emit(kind: str, actor: str = "", ids: Optional[Dict[str, Any]] = None,
         payload: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Appends one event. Returns the record, or None when the write failed (the
    failure is logged, never raised: observability must not break the work)."""
    global _seq, _appends
    try:
        with _lock:
            if _seq is None:
                _seq = _seed_seq()
            _seq += 1
            rec = {"seq": _seq, "ts": time.time(), "kind": str(kind),
                   "actor": str(actor or ""),
                   "ids": {str(k): v for k, v in (ids or {}).items() if v},
                   "payload": _small(payload or {})}
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            _appends += 1
            if _appends % TRIM_EVERY == 0:
                _trim_locked()
        try:   # the append-only daily log (services/memory_layers.py) keeps what this bounded log trims
            from app.services import memory_layers
            memory_layers.from_event(rec)
        except Exception:
            pass
        return rec
    except Exception as e:
        logger.debug(f"event log skipped: {e}")
        return None


def _trim_locked() -> None:
    """Drops the oldest events when the file has grown past MAX_LINES. Called with
    the lock held, rarely (every TRIM_EVERY appends), and never raises."""
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
        if len(lines) <= MAX_LINES:
            return
        tmp = LOG.with_suffix(".tmp")
        tmp.write_text("\n".join(lines[-KEEP_LINES:]) + "\n", encoding="utf-8")
        os.replace(tmp, LOG)
    except Exception as e:
        logger.debug(f"event log trim skipped: {e}")


def _read_all() -> List[Dict[str, Any]]:
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
            if isinstance(rec, dict) and "seq" in rec and "kind" in rec:
                out.append(rec)
        except ValueError:
            continue
    out.sort(key=lambda r: (r.get("seq") or 0))
    return out


_ID_ARGS = ("task_id", "mission_id", "subtask_id", "crew_job", "ref")


def query(since: Optional[float] = None, until: Optional[float] = None,
          kinds: Optional[List[str]] = None, limit: int = 200,
          cursor: Optional[int] = None, **ids: Any) -> Dict[str, Any]:
    """Events in sequence order, filtered. kinds are prefixes ("task" matches every
    task.* event). cursor is the last seq a previous page returned; the next page is
    strictly after it, so pages never overlap and never skip. Unknown id arguments
    are ignored rather than guessed at."""
    limit = max(1, min(int(limit or 200), 1000))
    want = {k: ids[k] for k in _ID_ARGS if ids.get(k)}
    rows = []
    for rec in _read_all():
        if cursor is not None and (rec.get("seq") or 0) <= cursor:
            continue
        ts = float(rec.get("ts") or 0)
        if since is not None and ts < since:
            continue
        if until is not None and ts > until:
            continue
        if kinds and not any(str(rec.get("kind") or "").startswith(k) for k in kinds):
            continue
        rec_ids = rec.get("ids") or {}
        if any(rec_ids.get(k) != v for k, v in want.items()):
            continue
        rows.append(rec)
        if len(rows) >= limit:
            break
    return {"events": rows,
            "next_cursor": rows[-1]["seq"] if len(rows) == limit else None,
            "count": len(rows)}


def stats() -> Dict[str, Any]:
    """The log's own shape, for the snapshot: how many events it holds and the
    newest sequence number (0 and 0 before the first event)."""
    rows = _read_all()
    if not rows:
        return {"events": 0, "last_seq": 0}
    return {"events": len(rows), "last_seq": rows[-1].get("seq") or 0}
