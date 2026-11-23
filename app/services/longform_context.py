"""
Long-form context: holding long conversations and long documents without losing the thread
(docs/LONGFORM-CONTEXT-DESIGN.md, AGE-96).

Three pieces, all stdlib (the light core ships without numpy; semantic search over finished
work stays with services/recall.py):

  chunk store   ingest_text() / ingest_messages() split a long input (a pasted transcript, a
                file's text, a whole chat thread) into paragraph-aware chunks in longform.db,
                each with a stored token estimate. Re-ingesting a source replaces its chunks.
  recall        recall(query, budget) ranks chunks by keyword (FTS5 BM25, LIKE fallback) and
                assembles the best ones in document order inside a hard token budget: the
                result's tokens never exceed the budget, and chunks are skipped whole, never
                cut mid-thought.
  compaction    fit_budget() runs in the chat path after memory_layers.checkpointed(): when a
                history measures over budget, the full history is ingested first (so nothing
                becomes unrecoverable), the newest tail stays verbatim, and the middle becomes
                one checkpoint message from the existing summarizer seam, or a deterministic
                extractive digest if summarizing fails. Under budget it is the identity.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.longform")

DB = DATA_DIR / "longform.db"

CHUNK_CHARS = 1200          # target chunk size; paragraphs pack up to this
CHUNK_OVERLAP = 150         # chars shared between consecutive chunks
DEFAULT_HISTORY_BUDGET = 24000   # tokens of history the chat path will carry before compacting
TAIL_FRACTION = 0.55        # of the budget: the verbatim recent tail
DIGEST_FRACTION = 0.25      # of the budget: cap for the extractive fallback digest


def estimate_tokens(text: str) -> int:
    """The rough cut used across the codebase: ~4 chars per token."""
    return math.ceil(len(text) / 4) if text else 0


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS sources (
        id TEXT PRIMARY KEY, kind TEXT, title TEXT, session_id TEXT, created REAL, chars INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS chunks (
        id TEXT PRIMARY KEY, source_id TEXT, seq INTEGER, text TEXT, tokens INTEGER)""")
    c.execute("CREATE INDEX IF NOT EXISTS chunks_source ON chunks(source_id, seq)")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(id UNINDEXED, text, tokenize='porter unicode61')")
    return c


# --------------------------------------------------------------------------------------------- chunking

def chunk_text(text: str) -> List[str]:
    """Paragraph-aware chunks of ~CHUNK_CHARS with CHUNK_OVERLAP shared between neighbours.
    A paragraph longer than the target is hard-split; short paragraphs pack together."""
    paras = [p for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    pieces: List[str] = []
    for p in paras:
        if len(p) <= CHUNK_CHARS:
            pieces.append(p)
        else:
            for i in range(0, len(p), CHUNK_CHARS):
                pieces.append(p[i:i + CHUNK_CHARS])
    chunks: List[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + len(piece) + 2 > CHUNK_CHARS:
            chunks.append(cur)
            cur = cur[-CHUNK_OVERLAP:] + "\n\n" + piece if CHUNK_OVERLAP else piece
        else:
            cur = cur + "\n\n" + piece if cur else piece
    if cur.strip():
        chunks.append(cur)
    return chunks


# --------------------------------------------------------------------------------------------- ingest

def ingest_text(text: str, kind: str = "document", title: str = "",
                session_id: Optional[str] = None, source_id: Optional[str] = None) -> Dict[str, Any]:
    """Chunks a long input into the store. Same source_id re-ingested replaces its chunks,
    so a growing chat thread or a revised document never duplicates."""
    text = text or ""
    if source_id is None:
        digest = hashlib.sha256(f"{kind}\x00{session_id or ''}\x00{text}".encode()).hexdigest()[:16]
        source_id = f"lf-{digest}"
    chunks = chunk_text(text)
    with _conn() as c:
        c.execute("DELETE FROM chunks_fts WHERE id IN (SELECT id FROM chunks WHERE source_id = ?)", (source_id,))
        c.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
        c.execute("INSERT OR REPLACE INTO sources (id, kind, title, session_id, created, chars) VALUES (?,?,?,?,?,?)",
                  (source_id, kind, title, session_id, time.time(), len(text)))
        for seq, chunk in enumerate(chunks):
            cid = f"{source_id}:{seq}"
            c.execute("INSERT OR REPLACE INTO chunks (id, source_id, seq, text, tokens) VALUES (?,?,?,?,?)",
                      (cid, source_id, seq, chunk, estimate_tokens(chunk)))
            c.execute("INSERT OR REPLACE INTO chunks_fts (id, text) VALUES (?,?)", (cid, chunk))
    return {"source_id": source_id, "kind": kind, "chunks": len(chunks),
            "chars": len(text), "tokens": sum(estimate_tokens(ch) for ch in chunks)}


def render_messages(messages: List[Dict[str, Any]]) -> str:
    return "\n\n".join(f"{m.get('role', 'user')}: {_content_text(m.get('content'))}" for m in messages)


def ingest_messages(session_id: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Stores a whole chat history under chat-<session_id>, replacing the previous copy."""
    return ingest_text(render_messages(messages), kind="chat", title=f"chat {session_id}",
                       session_id=session_id, source_id=f"chat-{session_id}")


def status() -> Dict[str, Any]:
    with _conn() as c:
        sources = c.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
        chunks = c.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        tokens = c.execute("SELECT COALESCE(SUM(tokens), 0) AS t FROM chunks").fetchone()["t"]
    return {"sources": sources, "chunks": chunks, "tokens": tokens}


# --------------------------------------------------------------------------------------------- recall

def _terms(query: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(t) > 1]


def _ranked(query: str, session_id: Optional[str], limit: int) -> List[sqlite3.Row]:
    """Chunks ranked for the query: FTS5 BM25 first; a LIKE scan (newest first) when FTS has
    nothing, the query has no usable terms, or FTS itself errors."""
    scope = "AND s.session_id = ?" if session_id else ""
    params: List[Any] = []
    with _conn() as c:
        terms = _terms(query)
        if terms:
            match = " OR ".join(f'"{t}"' for t in terms[:12])
            try:
                rows = c.execute(
                    f"""SELECT ch.id, ch.source_id, ch.seq, ch.text, ch.tokens, s.title, s.kind
                        FROM chunks_fts f JOIN chunks ch ON ch.id = f.id JOIN sources s ON s.id = ch.source_id
                        WHERE chunks_fts MATCH ? {scope}
                        ORDER BY bm25(chunks_fts) LIMIT ?""",
                    [match] + ([session_id] if session_id else []) + [limit]).fetchall()
                if rows:
                    return rows
            except sqlite3.Error as e:
                logger.debug(f"fts recall fell back to scan: {e}")
        like = " OR ".join("ch.text LIKE ?" for _ in terms[:6]) or "1 = 1"
        args: List[Any] = [f"%{t}%" for t in terms[:6]]
        rows = c.execute(
            f"""SELECT ch.id, ch.source_id, ch.seq, ch.text, ch.tokens, s.title, s.kind
                FROM chunks ch JOIN sources s ON s.id = ch.source_id
                WHERE ({like}) {scope}
                ORDER BY s.created DESC, ch.seq LIMIT ?""",
            args + ([session_id] if session_id else []) + [limit]).fetchall()
        return rows


def recall(query: str, budget_tokens: int, session_id: Optional[str] = None,
           limit: int = 40) -> Dict[str, Any]:
    """Assembles the chunks that best match the query inside a hard token budget.
    Chosen chunks come back in document order; tokens_used never exceeds budget_tokens."""
    ranked = _ranked(query, session_id, limit)
    chosen: List[sqlite3.Row] = []
    used = 0
    for row in ranked:
        cost = int(row["tokens"]) + 12   # per-chunk header allowance
        if used + cost <= budget_tokens:
            chosen.append(row)
            used += cost
    chosen.sort(key=lambda r: (r["source_id"], r["seq"]))
    parts = [f"[{r['title'] or r['kind']} · part {r['seq'] + 1}]\n{r['text']}" for r in chosen]
    return {"text": "\n\n".join(parts), "chunks": len(chosen), "tokens_used": used,
            "budget_tokens": budget_tokens,
            "sources": sorted({r["source_id"] for r in chosen})}


# --------------------------------------------------------------------------------------------- compaction

async def _default_summarizer(older: List[Dict[str, Any]], prior: str) -> str:
    from app.services import memory_layers
    return await memory_layers.summarizer(older, prior)


# Injectable for tests, same seam style as memory_layers.summarizer.
summarizer: Callable[[List[Dict[str, Any]], str], Awaitable[str]] = _default_summarizer


def _digest(middle: List[Dict[str, Any]], budget_tokens: int) -> str:
    """Deterministic extractive fallback: the head of each middle message, capped."""
    cap = max(200, int(budget_tokens * DIGEST_FRACTION) * 4)
    lines: List[str] = []
    total = 0
    for m in middle:
        line = f"{m.get('role', 'user')}: {_content_text(m.get('content'))[:240]}"
        if total + len(line) > cap:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


async def fit_budget(session_id: Optional[str], messages: List[Dict[str, Any]],
                     budget_tokens: int = DEFAULT_HISTORY_BUDGET) -> List[Dict[str, Any]]:
    """Fits a chat history to a token budget without hard truncation. Under budget: unchanged.
    Over budget: the full history is ingested into the chunk store first, leading system
    messages and the newest tail (TAIL_FRACTION of the budget) stay verbatim, and the middle
    becomes one checkpoint message (summarizer seam; extractive digest if that fails)."""
    total = sum(estimate_tokens(_content_text(m.get("content"))) for m in messages)
    if total <= budget_tokens or len(messages) < 4:
        return messages
    if session_id:
        try:
            ingest_messages(session_id, messages)
        except Exception as e:
            logger.debug(f"longform ingest before compaction: {e}")
    head: List[Dict[str, Any]] = []
    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        head.append(messages[i])
        i += 1
    rest = messages[i:]
    tail: List[Dict[str, Any]] = []
    tail_tokens = 0
    tail_cap = int(budget_tokens * TAIL_FRACTION)
    for m in reversed(rest):
        cost = estimate_tokens(_content_text(m.get("content")))
        if tail and tail_tokens + cost > tail_cap:
            break
        tail.append(m)
        tail_tokens += cost
    tail.reverse()
    middle = rest[:len(rest) - len(tail)]
    if not middle:
        return messages
    try:
        summary = (await summarizer(middle, "")).strip()
    except Exception as e:
        logger.warning(f"longform summarizer failed, using digest: {e}")
        summary = ""
    if not summary:
        summary = _digest(middle, budget_tokens)
    note = {"role": "user", "content": "(Earlier conversation compacted to fit; the full text is "
            f"kept in long-form context and can be recalled.)\n{summary}"}
    ack = {"role": "assistant", "content": "Understood; continuing from that summary."}
    return head + [note, ack] + tail
