"""
Xod's episodic memory: everything it has done with you, searchable. Each finished chat turn, background task
(routine runs included) and thing the companion found on its own becomes an "episode" with its date. Asking
"what was that article you found last week?", "what did we decide about the logo?" or "when did I last ask
about flights?" searches them.

Retrieval is hybrid, the way current agent-memory systems do it (Mem0, Zep, Letta's recall memory):
- keywords: SQLite full-text search (BM25), for names, numbers and exact words;
- meaning: embeddings from a small local model (nomic-embed-text in LM Studio; a built-in hashed vector when
  that isn't available), for the same idea in other words;
- the two rankings merged with reciprocal-rank fusion, nudged toward recent episodes;
- time: "yesterday", "last week", "in September", "3 days ago", "on Monday" narrow the search to that window.

Everything is in recall.db in Xod's data folder. Embeddings are always computed on this Mac (never by a cloud
model, whatever answers your questions). Indexing is incremental: after each chat turn, when a task finishes,
and once at startup for what came before.
"""
import asyncio
import json
import logging
import math
import re
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.paths import DATA_DIR
from app.timefmt import nopad

logger = logging.getLogger("xod.recall")

DB = DATA_DIR / "recall.db"
EMBED_URL = "http://127.0.0.1:1234/v1/embeddings"
EMBED_MODEL = "text-embedding-nomic-embed-text-v1.5"
_index_lock: Optional[asyncio.Lock] = None
_embed_down_until = 0.0
_embed_warm = False          # the first request may load the embedding model (several seconds)

KIND_LABEL = {"chat": "chat", "task": "task", "companion": "Xod on its own"}
SKIP_RE = re.compile(r"^\s*(hi|hey|hello|yo|thanks?( you)?|ok(ay)?|cool|nice|great|good (morning|night)|test|go ahead|yes|no|continue)\W*$", re.I)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=10, check_same_thread=False)   # used from a worker thread while indexing
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS episodes (
        id TEXT PRIMARY KEY, kind TEXT, ref TEXT, ts REAL, title TEXT, text TEXT, session_id TEXT,
        embedding BLOB, emb_model TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS episodes_ts ON episodes(ts)")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(id UNINDEXED, title, text, tokenize='porter unicode61')")
    c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    return c


def _meta(c: sqlite3.Connection, key: str, default: str = "0") -> str:
    r = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else default


def _set_meta(c: sqlite3.Connection, key: str, value: Any) -> None:
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))


# --------------------------------------------------------------------------------------------- vectors

def _hash_vec(text: str) -> np.ndarray:
    from app.services.memory_service import memory_service
    return memory_service._fallback_semantic_vector(text)


async def _embed(texts: List[str], query: bool = False) -> Tuple[List[np.ndarray], str]:
    """Embeddings from the local model, in one request; the hashed fallback if it isn't available."""
    global _embed_down_until, _embed_warm
    prefix = "search_query: " if query else "search_document: "
    if time.time() >= _embed_down_until:
        try:
            import httpx
            timeout = 8.0 if len(texts) > 1 else 3.0
            if not _embed_warm:
                timeout = 40.0           # LM Studio loads the embedding model on first use
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(EMBED_URL, json={"model": EMBED_MODEL, "input": [prefix + t[:2000] for t in texts]})
            if res.status_code == 200:
                rows = sorted(res.json().get("data", []), key=lambda d: d.get("index", 0))
                vecs = []
                for r in rows:
                    v = np.array(r["embedding"], dtype=np.float32)
                    n = np.linalg.norm(v)
                    vecs.append(v / n if n else v)
                if len(vecs) == len(texts):
                    _embed_warm = True
                    return vecs, "nomic"
            _embed_down_until = time.time() + 120
        except Exception as e:
            logger.debug(f"recall: local embeddings unavailable ({e})")
            _embed_down_until = time.time() + 120
    return [_hash_vec(t) for t in texts], "hash"


# -------------------------------------------------------------------------------------------- indexing

def _plain(md: str, n: int) -> str:
    t = re.sub(r"<details>.*?</details>", " ", md or "", flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)|\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[#*_`>|]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:n] + ("…" if len(t) > n else "")


def _tools_used(tool_events: str) -> str:
    try:
        evs = json.loads(tool_events or "[]")
    except Exception:
        return ""
    names, urls = [], []
    for e in evs:
        if e.get("type") == "tool_call" and e.get("name") and e["name"] not in names:
            names.append(e["name"])
        data = e.get("data") if isinstance(e.get("data"), dict) else {}
        for r in (data.get("results") or [])[:3]:
            if isinstance(r, dict) and r.get("content") and r.get("url"):
                urls.append(r["url"])
    out = ""
    if names:
        out += " Used: " + ", ".join(names[:6]) + "."
    if urls:
        out += " Read: " + ", ".join(urls[:4]) + "."
    return out


def _collect_chats(c: sqlite3.Connection, limit: int) -> List[Dict[str, Any]]:
    """Finished chat turns since the last run: each question with the answer that followed it."""
    from app.services.storage import get_db_connection
    wm = float(_meta(c, "chat_ts"))
    db = get_db_connection()
    try:
        users = db.execute("SELECT m.id, m.session_id, m.content, m.created_at, s.title FROM messages m "
                           "LEFT JOIN sessions s ON s.id = m.session_id WHERE m.role = 'user' AND m.created_at > ? "
                           "ORDER BY m.created_at ASC LIMIT ?", (wm, limit)).fetchall()
        out = []
        for u in users:
            reply = db.execute("SELECT content, tool_events, created_at FROM messages WHERE session_id = ? AND role = 'assistant' "
                               "AND created_at >= ? ORDER BY created_at ASC LIMIT 1", (u["session_id"], u["created_at"])).fetchone()
            if not reply:
                break                                  # still being answered: pick it up next time
            q = str(u["content"] or "")
            if q.startswith("[") and '"type"' in q[:40]:
                try:
                    q = " ".join(p.get("text", "") for p in json.loads(q) if isinstance(p, dict))
                except Exception:
                    pass
            wm = u["created_at"]
            if SKIP_RE.match(q) or len(q.strip()) < 4:
                continue
            out.append({"id": f"chat:{u['id']}", "kind": "chat", "ref": u["id"], "ts": u["created_at"],
                        "title": u["title"] or "Chat", "session_id": u["session_id"],
                        "text": f"You asked: {_plain(q, 600)}\nXod answered: {_plain(reply['content'], 900)}{_tools_used(reply['tool_events'])}"})
        _set_meta(c, "chat_ts", wm)
        return out
    finally:
        db.close()


def _collect_tasks(c: sqlite3.Connection, limit: int) -> List[Dict[str, Any]]:
    from app.services import task_queue as tq
    wm = float(_meta(c, "task_ts"))
    try:
        db = tq._conn()
        rows = db.execute("SELECT id, title, request, status, result, finished_at FROM tasks WHERE finished_at IS NOT NULL "
                          "AND finished_at > ? ORDER BY finished_at ASC LIMIT ?", (wm, limit)).fetchall()
        db.close()
    except Exception:
        return []
    out = []
    for r in rows:
        wm = r["finished_at"]
        out.append({"id": f"task:{r['id']}", "kind": "task", "ref": r["id"], "ts": r["finished_at"], "title": r["title"],
                    "session_id": "", "text": f"Background task ({r['status']}): {_plain(r['request'], 400)}\nResult: {_plain(r['result'] or '', 1000)}"})
    _set_meta(c, "task_ts", wm)
    return out


def _collect_companion(c: sqlite3.Connection) -> List[Dict[str, Any]]:
    try:
        from app.services import soul
        journal = soul.load().get("journal") or []
    except Exception:
        return []
    wm = float(_meta(c, "companion_ts"))
    out = []
    for j in journal:
        if j.get("ts", 0) <= wm or not j.get("text"):
            continue
        wm = max(wm, j["ts"])
        src = "; ".join(s.get("url", "") for s in (j.get("sources") or [])[:3])
        out.append({"id": f"companion:{j.get('id')}", "kind": "companion", "ref": j.get("id"), "ts": j["ts"],
                    "title": f"Xod {'read up on' if j.get('kind') == 'found' else 'thought about'} {j.get('topic') or 'something'}",
                    "session_id": "", "text": j["text"] + (f" Facts: {'; '.join(j['facts'])}." if j.get("facts") else "") +
                    (f" Sources: {src}" if src else "")})
    _set_meta(c, "companion_ts", wm)
    return out


async def index_new(limit: int = 200) -> int:
    """Adds what happened since the last run. Returns how many episodes were added."""
    global _index_lock
    if _index_lock is None:
        _index_lock = asyncio.Lock()
    async with _index_lock:
        c = _conn()
        try:
            eps = await asyncio.to_thread(lambda: _collect_chats(c, limit) + _collect_tasks(c, limit) + _collect_companion(c))
            added = 0
            for i in range(0, len(eps), 32):
                batch = eps[i:i + 32]
                vecs, model = await _embed([f"{e['title']}. {e['text']}" for e in batch])
                for e, v in zip(batch, vecs):
                    c.execute("INSERT OR REPLACE INTO episodes (id, kind, ref, ts, title, text, session_id, embedding, emb_model) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (e["id"], e["kind"], e["ref"], e["ts"], e["title"], e["text"], e["session_id"],
                               v.astype(np.float32).tobytes(), model))
                    c.execute("DELETE FROM episodes_fts WHERE id = ?", (e["id"],))
                    c.execute("INSERT INTO episodes_fts (id, title, text) VALUES (?, ?, ?)", (e["id"], e["title"], e["text"]))
                    added += 1
            # Upgrade older episodes that only got the hashed vector, now that the local model answers.
            if model == "nomic" or not eps:
                old = c.execute("SELECT id, title, text FROM episodes WHERE emb_model = 'hash' LIMIT 64").fetchall()
                if old:
                    vecs, m2 = await _embed([f"{o['title']}. {o['text']}" for o in old])
                    if m2 == "nomic":
                        for o, v in zip(old, vecs):
                            c.execute("UPDATE episodes SET embedding = ?, emb_model = 'nomic' WHERE id = ?", (v.astype(np.float32).tobytes(), o["id"]))
            c.commit()
            if added:
                logger.info(f"recall: indexed {added} new episodes")
            return added
        finally:
            c.close()


def schedule_index(delay: float = 1.0) -> None:
    """Index soon, in the background (after a chat turn, when a task finishes)."""
    async def later():
        await asyncio.sleep(delay)
        try:
            await index_new()
        except Exception as e:
            logger.debug(f"recall: indexing failed: {e}")
    try:
        asyncio.get_running_loop().create_task(later())
    except RuntimeError:
        pass


# ------------------------------------------------------------------------------------------------ time

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
DAYS = {d: i for i, d in enumerate(["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}


def parse_when(text: str, now: Optional[datetime] = None) -> Optional[Tuple[float, float, str]]:
    """'yesterday', 'last week', 'in September', '3 days ago', 'on Monday', 'this morning' -> (start, end, label)."""
    now = now or datetime.now()
    t = (text or "").lower()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def span(a: datetime, b: datetime, label: str):
        return a.timestamp(), b.timestamp(), label
    if re.search(r"\b(this morning|earlier today|today)\b", t):
        return span(day0, now + timedelta(minutes=1), "today")
    if re.search(r"\b(last night)\b", t):
        return span(day0 - timedelta(hours=8), day0 + timedelta(hours=4), "last night")
    if re.search(r"\byesterday\b", t):
        return span(day0 - timedelta(days=1), day0, "yesterday")
    m = re.search(r"\b(\d+|a|one|two|three|four|five|six|seven|couple of|few) (day|week|month)s? ago\b", t)
    if m:
        n = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "couple of": 2, "few": 3}.get(m.group(1))
        n = n or int(m.group(1))
        unit = {"day": 1, "week": 7, "month": 30}[m.group(2)]
        center = day0 - timedelta(days=n * unit)
        pad = timedelta(days=max(1, unit // 2 + (1 if unit > 1 else 0)))
        return span(center - pad + timedelta(days=1 if unit == 1 else 0), center + pad + timedelta(days=0 if unit > 1 else 0), m.group(0))
    if re.search(r"\bthis week\b", t):
        return span(day0 - timedelta(days=now.weekday()), now + timedelta(minutes=1), "this week")
    if re.search(r"\blast week\b", t):
        start = day0 - timedelta(days=now.weekday() + 7)
        return span(start, start + timedelta(days=7), "last week")
    if re.search(r"\bthis month\b", t):
        return span(day0.replace(day=1), now + timedelta(minutes=1), "this month")
    if re.search(r"\blast month\b", t):
        first = day0.replace(day=1)
        prev = (first - timedelta(days=1)).replace(day=1)
        return span(prev, first, "last month")
    m = re.search(r"\b(?:on|last) (monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if m:
        back = (now.weekday() - DAYS[m.group(1)]) % 7 or 7
        d = day0 - timedelta(days=back)
        return span(d, d + timedelta(days=1), m.group(0))
    m = re.search(r"\b(?:in|during|back in|last) (january|february|march|april|may|june|july|august|september|october|november|december)\b", t)
    if m:
        mo = MONTHS[m.group(1)]
        year = now.year if mo <= now.month else now.year - 1
        a = datetime(year, mo, 1)
        b = datetime(year + (mo == 12), mo % 12 + 1, 1)
        return span(a, b, m.group(0))
    if re.search(r"\b(recently|lately|the other day)\b", t):
        return span(day0 - timedelta(days=14), now + timedelta(minutes=1), "recently")
    return None


# ---------------------------------------------------------------------------------------------- search

_STOP = set("the a an and or of to in on for with is are was were be by at from as it its this that what which who how "
            "when where why do does did i you me my your we our can will should would about into than then there "
            "remember last time yesterday week month ago earlier before again us xod".split())


def _fts_query(q: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", q.lower()) if w not in _STOP and len(w) > 1]
    return " OR ".join(f'"{w}"' for w in words[:12])


def when_label(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    days = (datetime.now().date() - d.date()).days
    if days == 0:
        return f"today {d.strftime(nopad('%-I:%M %p'))}"
    if days == 1:
        return f"yesterday {d.strftime(nopad('%-I:%M %p'))}"
    if days < 7:
        return d.strftime(nopad("%A %-I:%M %p"))
    return d.strftime(nopad("%b %-d, %Y"))


async def search(query: str, k: int = 5, kinds: Optional[List[str]] = None, when: Optional[str] = None,
                 exclude_session: str = "") -> Dict[str, Any]:
    """Episodes that match the question, best first. {"results": [...], "window": label or None}."""
    try:
        await asyncio.wait_for(index_new(limit=60), 6)     # include what just happened
    except Exception:
        pass
    window = parse_when(when or query)
    c = _conn()
    try:
        where, args = [], []
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            args += kinds
        if exclude_session:
            where.append("(session_id IS NULL OR session_id != ?)")
            args.append(exclude_session)
        base = (" WHERE " + " AND ".join(where)) if where else ""
        rows = c.execute(f"SELECT id, kind, ref, ts, title, text, session_id, embedding, emb_model FROM episodes{base}", args).fetchall()
        if not rows:
            return {"results": [], "window": window[2] if window else None}
        if window:
            inside = [r for r in rows if window[0] <= r["ts"] < window[1]]
            rows = inside or rows                     # nothing in that window: say so, but still show the closest
        by_id = {r["id"]: r for r in rows}
        # Keywords (BM25)
        kw_rank: Dict[str, int] = {}
        fq = _fts_query(query)
        if fq:
            try:
                hits = c.execute("SELECT id FROM episodes_fts WHERE episodes_fts MATCH ? ORDER BY bm25(episodes_fts) LIMIT 60", (fq,)).fetchall()
                for i, h in enumerate(x for x in hits if x["id"] in by_id):
                    kw_rank[h["id"]] = i
            except sqlite3.OperationalError:
                pass
        # Meaning (embeddings), compared within the same kind of vector
        sem_rank: Dict[str, int] = {}
        models = {r["emb_model"] for r in rows}
        qvecs: Dict[str, np.ndarray] = {}
        if "nomic" in models:
            v, m = await _embed([query], query=True)
            if m == "nomic":
                qvecs["nomic"] = v[0]
        if "hash" in models or "nomic" not in qvecs:
            qvecs["hash"] = _hash_vec(query)
        scored = []
        for r in rows:
            qv = qvecs.get(r["emb_model"])
            if qv is None or not r["embedding"]:
                continue
            ev = np.frombuffer(r["embedding"], dtype=np.float32)
            if ev.shape != qv.shape:
                continue
            scored.append((float(np.dot(ev, qv)), r["id"]))
        scored.sort(reverse=True)
        floor = 0.45 if "nomic" in qvecs else 0.2
        for i, (s, rid) in enumerate(x for x in scored[:60] if x[0] >= floor):
            sem_rank[rid] = i
        # Reciprocal-rank fusion, plus a little for recency
        now = time.time()
        fused = []
        for rid in set(kw_rank) | set(sem_rank):
            score = sum(1.0 / (60 + rank[rid]) for rank in (kw_rank, sem_rank) if rid in rank)
            age_days = max(0.0, (now - by_id[rid]["ts"]) / 86400)
            score *= 1.0 + 0.25 * math.exp(-age_days / 30)
            fused.append((score, rid))
        if not fused and window:
            fused = [(r["ts"], r["id"]) for r in rows]   # "what did we do yesterday?": everything in the window
        fused.sort(reverse=True)
        results = []
        for score, rid in fused[:k]:
            r = by_id[rid]
            results.append({"id": rid, "kind": r["kind"], "kind_label": KIND_LABEL.get(r["kind"], r["kind"]),
                            "when": when_label(r["ts"]), "ts": r["ts"], "title": r["title"], "text": r["text"][:900],
                            "session_id": r["session_id"] or "", "ref": r["ref"], "score": round(score, 4)})
        return {"results": results, "window": window[2] if window else None,
                "in_window": bool(window) and all(window[0] <= x["ts"] < window[1] for x in results)}
    finally:
        c.close()


def note_for(found: Dict[str, Any]) -> str:
    """A block for a prompt: the episodes, dated, as context."""
    res = found.get("results") or []
    if not res:
        if found.get("window"):
            return f"(Your memory has nothing from {found['window']}.)"
        return ""
    head = "From your memory of earlier conversations and work (dated; use what helps, say when it was):"
    if found.get("window") and not found.get("in_window"):
        head += f" Nothing was from {found['window']}; these are the closest."
    return head + "\n" + "\n".join(f"- [{r['when']} · {r['kind_label']} · {r['title'][:60]}] {r['text'][:500]}" for r in res)


def stats() -> Dict[str, Any]:
    c = _conn()
    try:
        rows = c.execute("SELECT kind, COUNT(*) n, MIN(ts) first FROM episodes GROUP BY kind").fetchall()
        return {r["kind"]: {"count": r["n"], "since": r["first"]} for r in rows}
    finally:
        c.close()
