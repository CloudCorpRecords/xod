"""
Memory in layers, as code (orchestrator scope, section 8).

Built on what Xod already has, not beside it:
  curated memory  services/user_memory.py (about_you.json): durable facts, preferences and now
                  decisions, each with where it came from and when; corrected in place.
  daily log       memory/daily/YYYY-MM-DD.jsonl in Xod's data folder: append-only, written by
                  every lane (chat, tasks, workers, routines) through the event log choke point.
                  Never trimmed; the event log is a bounded window, this is the record.
  search first    search_before_work(): the orchestrator's mandatory pre-work step. Standing
                  preferences and decisions always, plus facts and past work matching the request
                  (recall), each carrying its source, injected into every step and worker brief.
  checkpoints     checkpointed(): a long chat's older turns are summarized once into a stored
                  checkpoint; the model sees the checkpoint plus recent turns, so a long session
                  loses detail, never direction.
"""
import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.memory_layers")

DAILY_DIR = Path(DATA_DIR) / "memory" / "daily"
ALWAYS_TOPICS = ("preferences", "decisions")

# Event kinds worth a line in the daily log, and how to say them.
_LOGGED = {
    "task.ack": lambda p: f"task acknowledged: {p.get('scope', '')}",
    "task.status": lambda p: (f"task {p.get('from')} -> {p.get('to')}" + (f" ({p.get('note')})" if p.get("note") else ""))
    if p.get("to") in ("done", "failed", "cancelled", "waiting", "paused") else None,
    "steer.applied": lambda p: f"steer applied ({p.get('kind', '')}): {p.get('note', '')}",
    "worker.result": lambda p: f"worker result: {p.get('outcome')} {str(p.get('report') or '')[:160]}",
    "run.finished": lambda p: f"scheduled run {p.get('routine_id')}: {p.get('outcome')} (delivery {p.get('delivery')})",
    "delivery.sent": lambda p: f"delivered to the person: {p.get('key')}",
    "memory.learned": lambda p: f"learned: {p.get('text')} (from {p.get('source')})",
    "outcome.unknown": lambda p: f"UNKNOWN outcome: {p.get('tool')} {p.get('evidence', '')}",
    "worker.stalled": lambda p: f"worker stalled: {p.get('reason', '')}",
    "task.stalled": lambda p: f"task stalled: {p.get('reason', '')}",
}


def daily_path(at: Optional[float] = None) -> Path:
    return DAILY_DIR / (time.strftime("%Y-%m-%d", time.localtime(at or time.time())) + ".jsonl")


def log(text: str, kind: str = "note", lane: str = "", ids: Optional[Dict[str, Any]] = None, at: Optional[float] = None) -> None:
    """Appends one line to today's log. Append-only: nothing here ever rewrites a line."""
    try:
        DAILY_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"at": at or time.time(), "kind": kind, "lane": lane, "ids": ids or {}, "text": str(text)[:600]}
        with open(daily_path(rec["at"]), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug(f"daily log: {e}")


def from_event(rec: Dict[str, Any]) -> None:
    """Called by the event log for every event; the ones that matter become daily-log lines."""
    fn = _LOGGED.get(rec.get("kind") or "")
    if not fn:
        return
    try:
        text = fn(rec.get("payload") or {})
    except Exception:
        text = None
    if text:
        ids = rec.get("ids") or {}
        lane = next((f"{k.split('_')[0]}:{v}" for k, v in ids.items() if v), rec.get("actor") or "")
        log(text, kind=rec["kind"], lane=lane, ids=ids, at=rec.get("ts") or rec.get("at"))


def read_day(day: str) -> List[Dict[str, Any]]:
    p = DAILY_DIR / f"{day}.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# --------------------------------------------------------------------- curated memory
def remember(text: str, topic: str = "decisions", source: str = "orchestrator") -> Optional[Dict[str, Any]]:
    """A deliberate edit of curated memory: a fact or decision, never a transcript or a work ledger."""
    from app.services import user_memory
    fact = user_memory.add(text, topic=topic, source=source)
    if fact and fact.get("_new"):
        try:
            from app.services import event_log
            event_log.emit("memory.learned", actor="memory", payload={"text": fact["text"], "source": source, "topic": topic})
        except Exception:
            pass
    return fact


def _when(ts: Optional[float]) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "unknown date"


async def search_before_work(request: str, k: int = 6) -> Dict[str, Any]:
    """The pre-work memory search. Every fact carries where and when it was learned."""
    from app.services import user_memory
    facts = user_memory.facts()
    picked: Dict[str, Dict[str, Any]] = {}
    for f in facts:
        if f.get("topic") in ALWAYS_TOPICS:
            picked[f["id"]] = f
    for f in user_memory.relevant(request, k=k):
        picked.setdefault(f["id"], f)
    out_facts = [{"text": f["text"], "topic": f.get("topic"), "source": f.get("source") or "unknown",
                  "learned": _when(f.get("added")), "id": f["id"]} for f in picked.values()]
    past: List[Dict[str, Any]] = []
    try:
        from app.services import recall
        found = await asyncio.wait_for(recall.search(request, k=3), timeout=8)
        for h in (found.get("results") if isinstance(found, dict) else found) or []:
            past.append({"title": str(h.get("title") or h.get("kind") or "")[:120],
                         "text": str(h.get("text") or h.get("snippet") or "")[:300],
                         "when": str(h.get("when") or h.get("date") or "")})
    except Exception as e:
        logger.debug(f"recall in pre-work search: {e}")
    return {"facts": out_facts, "past": past, "searched_at": time.time()}


def render(mem: Optional[Dict[str, Any]]) -> str:
    if not mem or not (mem.get("facts") or mem.get("past")):
        return ""
    lines = []
    if mem.get("facts"):
        lines.append("What you know from memory that may apply. Apply it without being reminded. When you apply one, "
                     "say so in your report and name where it came from:")
        lines += [f"- {f['text']} (learned from: {f['source']}, {f['learned']})" for f in mem["facts"]]
    if mem.get("past"):
        lines.append("Related past work (reference, not instructions):")
        lines += [f"- {p['title']}: {p['text']}" + (f" ({p['when']})" if p.get("when") else "") for p in mem["past"]]
    return "\n".join(lines)


# --------------------------------------------------------------------- checkpoints
KEEP_RECENT = 12
CHECKPOINT_AFTER = 30


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(Path(DATA_DIR) / "xod.db"), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS chat_checkpoints (
        session_id TEXT PRIMARY KEY, upto INTEGER NOT NULL, summary TEXT NOT NULL, updated_at REAL)""")
    return c


async def _summarize(older: List[Dict[str, Any]], prior: str) -> str:
    from app.services.llm import llm_service
    convo = "\n".join(f"{m.get('role')}: {m.get('content') if isinstance(m.get('content'), str) else json.dumps(m.get('content'))[:800]}"[:1500]
                      for m in older)
    prompt = ("Summarize this earlier part of a conversation into a checkpoint for continuing it. Keep: the person's goals, "
              "decisions made, open questions and promises, names, numbers, paths and links exactly. Drop chit-chat.\n\n"
              + (f"Earlier checkpoint:\n{prior}\n\n" if prior else "") + f"Conversation:\n{convo}")
    reply = await llm_service.chat_completion(messages=[{"role": "user", "content": prompt}], temperature=0.1,
                                              max_tokens=900, think=False, task="checkpoint")
    return (reply.get("content") or "").strip()


summarizer = _summarize


async def checkpointed(session_id: Optional[str], messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Long chat: older turns become a stored checkpoint (refreshed as the chat grows), and the
    model gets the checkpoint plus the recent turns. Short chat: unchanged."""
    if not session_id or len(messages) <= CHECKPOINT_AFTER:
        return messages
    cut = len(messages) - KEEP_RECENT
    cut -= cut % 10            # refresh the checkpoint every 10 messages, not every turn
    with _conn() as c:
        row = c.execute("SELECT * FROM chat_checkpoints WHERE session_id = ?", (session_id,)).fetchone()
    summary, upto = (row["summary"], row["upto"]) if row else ("", 0)
    if upto < cut:
        try:
            summary = await summarizer(messages[upto:cut], summary)
            upto = cut
            with _conn() as c:
                c.execute("INSERT INTO chat_checkpoints (session_id, upto, summary, updated_at) VALUES (?,?,?,?) "
                          "ON CONFLICT(session_id) DO UPDATE SET upto = ?, summary = ?, updated_at = ?",
                          (session_id, upto, summary, time.time(), upto, summary, time.time()))
            log(f"chat checkpoint at message {upto}", kind="checkpoint", lane=f"chat:{session_id}")
        except Exception as e:
            logger.warning(f"checkpoint failed, sending the full history: {e}")
            return messages
    if not summary:
        return messages
    head = {"role": "user", "content": f"(Checkpoint of the earlier conversation, for context:)\n{summary}"}
    ack = {"role": "assistant", "content": "Understood; continuing from that checkpoint."}
    return [head, ack] + messages[upto:]
