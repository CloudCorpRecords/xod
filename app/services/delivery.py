"""
Background work with delivery accounting (orchestrator scope, section 7; Phase 4).

Routines (services/routines.py) are the scheduler. This module gives every scheduled run a
first-class record and decides, per the routine's own notification policy, whether its result
reaches the person, exactly once.

  routine_runs  one row per run: started, finished, outcome, the result's fingerprint, and its
                delivery state (pending, delivered, suppressed, deduped)
  deliveries    the outbox: one row per thing that must reach the person, keyed so the same
                finding can never be delivered twice

Outcomes: no_change (a watch that found nothing new), changed (a watch that found something),
done (an ordinary routine finished), failed, decision (it needs the person). The policy says
which outcomes are delivered; routine no-change runs stay silent.

Delivery means the result is in a message in the person's Xod chat ("Background updates"), not
in a file or a log. The send is two-phase so a restart at any point delivers exactly once:
  1. the deliveries row is written as 'sending' (keyed; a second identical result is 'deduped')
  2. the chat message is written, carrying the delivery key
  3. the row becomes 'sent', then a desktop/phone ping is fired (best effort)
On startup, a row left 'sending' is checked at the destination: if its message is in the chat
it is marked sent (never re-sent); if not, it is sent now. Unknown is checked, never assumed.
"""
import hashlib
import json
import logging
import re
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xod.delivery")

OUTCOMES = ("no_change", "changed", "done", "failed", "decision")
DEFAULT_POLICY = {"task": ["done", "failed", "decision", "changed"],
                  "watch": ["changed", "failed", "decision"]}
SESSION_TITLE = "Background updates"
WATCH_PROTOCOL = ("\n\n(This is a watch. End your report with exactly one line: either "
                  "\"WATCH: nothing new\" when you found nothing that matches, or \"WATCH: found\" followed "
                  "by what you found, each item on its own line. A source you could not read is not "
                  "\"nothing new\": say \"WATCH: could not check\" and name it.)")
MAX_CONSECUTIVE_FAILURES = 3


def _conn() -> sqlite3.Connection:
    from app.services import routines
    c = sqlite3.connect(str(routines.DB), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS routine_runs (
        id TEXT PRIMARY KEY, routine_id TEXT NOT NULL, task_id TEXT, started_at REAL NOT NULL, finished_at REAL,
        outcome TEXT, result_hash TEXT, excerpt TEXT, delivery_state TEXT, delivery_key TEXT, note TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS routine_runs_r ON routine_runs(routine_id, started_at)")
    c.execute("""CREATE TABLE IF NOT EXISTS deliveries (
        key TEXT PRIMARY KEY, run_id TEXT, routine_id TEXT, state TEXT NOT NULL,   -- sending | sent
        text TEXT, session_id TEXT, message_id TEXT, created_at REAL, sent_at REAL)""")
    return c


def _emit(event: str, **payload: Any) -> None:
    try:
        from app.services import event_log
        event_log.emit(event, actor="delivery", payload=payload)
    except Exception:
        pass


# --------------------------------------------------------------------- runs
def run_started(routine_id: str, task_id: Optional[str]) -> str:
    rid = f"run-{uuid.uuid4().hex[:10]}"
    with _conn() as c:
        c.execute("INSERT INTO routine_runs (id, routine_id, task_id, started_at) VALUES (?,?,?,?)",
                  (rid, routine_id, task_id, time.time()))
    _emit("run.started", run_id=rid, routine_id=routine_id, task_id=task_id)
    return rid


def runs(routine_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM routine_runs WHERE routine_id = ? ORDER BY started_at DESC LIMIT ?",
                                           (routine_id, limit)).fetchall()]


def open_run_for_task(task_id: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        r = c.execute("SELECT * FROM routine_runs WHERE task_id = ? AND finished_at IS NULL ORDER BY started_at LIMIT 1",
                      (task_id,)).fetchone()
    return dict(r) if r else None


_WATCH_RE = re.compile(r"^\s*WATCH:\s*(nothing new|found|could not check)\s*(.*)$", re.I | re.M | re.S)


def classify(kind: str, task: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The run's outcome and the fingerprint of what it found, from the task's real end state."""
    if not task:
        return {"outcome": "failed", "finding": "", "note": "its task is gone"}
    st = task.get("status")
    result = task.get("result") or ""
    if st in ("failed", "cancelled"):
        return {"outcome": "failed", "finding": result[-600:], "note": f"its task {st}"}
    if st == "waiting":
        return {"outcome": "decision", "finding": json.dumps(task.get("waiting_detail") or {})[:600],
                "note": f"it needs you ({task.get('waiting_kind')})"}
    if kind != "watch":
        return {"outcome": "done", "finding": result, "note": ""}
    m = None
    for m in _WATCH_RE.finditer(result):
        pass
    if m is None:
        # A watch that did not say what it found has not established "nothing new".
        return {"outcome": "failed", "finding": result[-600:], "note": "the watch did not report a WATCH: line"}
    verdict = m.group(1).lower()
    if verdict == "nothing new":
        return {"outcome": "no_change", "finding": "", "note": ""}
    if verdict == "could not check":
        return {"outcome": "failed", "finding": m.group(2).strip()[:600], "note": "a source could not be checked"}
    return {"outcome": "changed", "finding": m.group(2).strip(), "note": ""}


_STOP = {"the", "a", "an", "is", "are", "was", "were", "and", "or", "of", "in", "on", "at", "to", "for", "it", "its", "this",
         "that", "with", "as", "be", "now", "today", "found", "new", "line", "lines", "file", "says", "contains", "watch"}


def _tokens(text: str):
    low = (text or "").lower()
    nums = set(re.findall(r"\d+(?:[.,]\d+)?", low))
    words = {w for w in re.findall(r"[a-z][a-z'-]+", low) if w not in _STOP and len(w) > 1}
    return nums, words


def same_finding(a: str, b: str) -> bool:
    """The same thing found again, however the model words it (live S7: a restock re-delivered because the
    report was phrased differently). Every number must match exactly (a new price or size is new) and the
    words must largely overlap."""
    na, wa = _tokens(a)
    nb, wb = _tokens(b)
    if na != nb:
        return False
    if not wa or not wb:
        return wa == wb
    return len(wa & wb) / len(wa | wb) >= 0.5


def _fingerprint(finding: str) -> str:
    norm = "\n".join(sorted(l.strip().lower() for l in (finding or "").splitlines() if l.strip()))
    return hashlib.sha256(norm.encode()).hexdigest()[:20]


def run_finished(run_id: str, routine: Dict[str, Any], task: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Records the run's outcome and queues its delivery per the routine's policy (or not)."""
    kind = routine.get("kind") or "task"
    policy = routine.get("notify_on") or DEFAULT_POLICY[kind]
    got = classify(kind, task)
    outcome, finding = got["outcome"], got["finding"]
    fp = _fingerprint(finding) if finding else ""
    key = f"{routine['id']}:{outcome}:{fp}" if outcome in ("changed", "decision") else f"{routine['id']}:{outcome}:{run_id}"
    state = "pending" if outcome in policy else "suppressed"
    excerpt = finding if outcome == "changed" else ((task or {}).get("result") or "")
    with _conn() as c:
        c.execute("UPDATE routine_runs SET finished_at = ?, outcome = ?, result_hash = ?, excerpt = ?, delivery_state = ?, "
                  "delivery_key = ?, note = ? WHERE id = ?",
                  (time.time(), outcome, fp, excerpt[:4000], state, key, got["note"], run_id))
    _emit("run.finished", run_id=run_id, routine_id=routine["id"], outcome=outcome, delivery=state)
    return {"outcome": outcome, "delivery_state": state, "key": key}


def note_decision(routine: Dict[str, Any], task: Dict[str, Any]) -> None:
    """A run waiting on the person: one decision record (and delivery) per distinct wait."""
    fp = _fingerprint(json.dumps(task.get("waiting_detail") or {}, sort_keys=True) + str(task.get("waiting_kind")))
    with _conn() as c:
        seen = c.execute("SELECT 1 FROM routine_runs WHERE task_id = ? AND outcome = 'decision' AND result_hash = ?",
                         (task["id"], fp)).fetchone()
        if seen:
            return
        rid = f"run-{uuid.uuid4().hex[:10]}"
        policy = routine.get("notify_on") or DEFAULT_POLICY[routine.get("kind") or "task"]
        state = "pending" if "decision" in policy else "suppressed"
        c.execute("INSERT INTO routine_runs (id, routine_id, task_id, started_at, finished_at, outcome, result_hash, excerpt, "
                  "delivery_state, delivery_key, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (rid, routine["id"], task["id"], time.time(), time.time(), "decision", fp,
                   json.dumps(task.get("waiting_detail") or {})[:2000], state, f"{routine['id']}:decision:{fp}",
                   f"it needs you ({task.get('waiting_kind')})"))


def consecutive_failures(routine_id: str) -> int:
    n = 0
    for r in runs(routine_id, 20):
        if r["finished_at"] is None:
            continue
        if r["outcome"] != "failed":
            break
        n += 1
    return n


# --------------------------------------------------------------------- the outbox
def _session_id() -> str:
    from app.services import storage
    with _conn() as c:
        r = c.execute("SELECT id FROM sessions WHERE title = ? ORDER BY created_at LIMIT 1", (SESSION_TITLE,)).fetchone()
    if r:
        return r["id"]
    return storage.create_session(title=SESSION_TITLE)["id"]


def _marker(key: str) -> str:
    return f"<!-- xod-delivery:{key} -->"


def _in_chat(key: str) -> Optional[str]:
    """The id of the chat message carrying this delivery, read from the destination itself."""
    with _conn() as c:
        r = c.execute("SELECT id FROM messages WHERE content LIKE ? LIMIT 1", (f"%{_marker(key)}%",)).fetchone()
    return r["id"] if r else None


def _render(routine: Dict[str, Any], run: Dict[str, Any]) -> str:
    head = {"changed": f"**{routine['name']}** found something new:",
            "failed": f"**{routine['name']}** did not complete this run" + (f" ({run['note']})" if run.get("note") else "") + ":",
            "decision": f"**{routine['name']}** needs you" + (f" ({run['note']})" if run.get("note") else "") + ".",
            "done": f"**{routine['name']}** finished:"}.get(run["outcome"], f"**{routine['name']}**:")
    body = (run.get("excerpt") or "").strip()
    return f"{head}\n\n{body[:3000]}\n\n{_marker(run['delivery_key'])}".strip()


# Swappable for tests: called after the chat message is safely written.
def _ping(title: str, text: str) -> None:
    try:
        from app.services.task_queue import notify, _phone
        notify(title, text[:200])
    except Exception:
        pass


def _send(key: str, text: str, title: str) -> str:
    from app.services import storage
    found = _in_chat(key)
    if found:
        return found        # it already landed (a restart between the write and the bookkeeping)
    msg = storage.add_message(_session_id(), "assistant", text)
    return msg["id"]


def deliver_pending() -> List[str]:
    """Delivers every pending run result once; returns the keys delivered now."""
    from app.services import routines
    delivered = []
    with _conn() as c:
        pending = [dict(r) for r in c.execute("SELECT * FROM routine_runs WHERE delivery_state = 'pending' ORDER BY finished_at").fetchall()]
    for run in pending:
        routine = routines.get(run["routine_id"]) or {"id": run["routine_id"], "name": run["routine_id"]}
        key = run["delivery_key"]
        text = _render(routine, run)
        with _conn() as c:
            cur = c.execute("INSERT OR IGNORE INTO deliveries (key, run_id, routine_id, state, text, created_at) VALUES (?,?,?,?,?,?)",
                            (key, run["id"], run["routine_id"], "sending", text, time.time()))
            fresh = cur.rowcount == 1
            if not fresh:
                existing = c.execute("SELECT * FROM deliveries WHERE key = ?", (key,)).fetchone()
        if fresh and run["outcome"] == "changed":
            with _conn() as c:
                earlier = c.execute("SELECT r.excerpt FROM deliveries d JOIN routine_runs r ON r.id = d.run_id "
                                    "WHERE d.routine_id = ? AND d.state = 'sent' AND r.outcome = 'changed' AND d.key != ?",
                                    (run["routine_id"], key)).fetchall()
            if any(same_finding(e["excerpt"] or "", run.get("excerpt") or "") for e in earlier):
                with _conn() as c:
                    c.execute("DELETE FROM deliveries WHERE key = ? AND state = 'sending'", (key,))
                    c.execute("UPDATE routine_runs SET delivery_state = 'deduped' WHERE id = ?", (run["id"],))
                _emit("delivery.deduped", run_id=run["id"], key=key, why="same finding, worded differently")
                continue
        if not fresh and existing["state"] == "sent":
            # This finding already reached the person (an earlier run found the same thing).
            with _conn() as c:
                c.execute("UPDATE routine_runs SET delivery_state = 'deduped' WHERE id = ?", (run["id"],))
            _emit("delivery.deduped", run_id=run["id"], key=key)
            continue
        if not fresh and existing["run_id"] != run["id"]:
            continue        # another run owns this key and is mid-send; it will finish it
        mid = _send(key, text, routine["name"])
        with _conn() as c:
            c.execute("UPDATE deliveries SET state = 'sent', sent_at = ?, message_id = ?, session_id = ? WHERE key = ?",
                      (time.time(), mid, _session_id(), key))
            c.execute("UPDATE routine_runs SET delivery_state = 'delivered' WHERE id = ?", (run["id"],))
        _emit("delivery.sent", run_id=run["id"], key=key, message_id=mid)
        _ping(routine["name"], text.split("\n", 1)[0].replace("**", ""))
        delivered.append(key)
    return delivered


def recover() -> Dict[str, int]:
    """At startup: deliveries left mid-send are settled at the destination, never blindly re-sent."""
    out = {"confirmed": 0, "resent": 0}
    with _conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM deliveries WHERE state = 'sending'").fetchall()]
    for d in rows:
        mid = _in_chat(d["key"])
        if mid:
            out["confirmed"] += 1
        else:
            from app.services import storage
            mid = storage.add_message(_session_id(), "assistant", d["text"])["id"]
            out["resent"] += 1
        with _conn() as c:
            c.execute("UPDATE deliveries SET state = 'sent', sent_at = ?, message_id = ? WHERE key = ?", (time.time(), mid, d["key"]))
            c.execute("UPDATE routine_runs SET delivery_state = 'delivered' WHERE id = ?", (d["run_id"],))
    return out


def delivered_messages() -> List[Dict[str, Any]]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM deliveries ORDER BY created_at").fetchall()]
