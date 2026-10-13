"""
Xod's own research notebook, and what it feels like doing today.

While you're away, the companion reads up on things (services/soul.py, muse). Before, every read became a
postcard. Now most of what it learns goes quietly into its notebook, and only now and then does it bring
you something: when it found something new about a thing you care about, it hasn't told you anything in a
while, and it hasn't used up its few shares for the day.

The parts:
  notebook   <data>/notebook.db: topics it follows (yours, and ones it picked up itself while reading),
             notes (what it learned, the facts, the sources, the follow-up question it now wonders about).
             A topic with an open follow-up question becomes a thread it keeps pulling on over days.
  drives     curiosity, mastery, connection, rest (0-1). Curiosity builds up while it hasn't read anything,
             connection while it hasn't shared anything, rest when its mood's energy is low. They drift back
             to a baseline over hours.
  today      once a day it picks a plan (go deep on one topic, wander, think about your projects, take it
             slow) by sampling how well each plan went before (Thompson sampling over Beta counts), weighted
             by its drives. The plan sets how much it does and what kind of thing.
  rewards    intrinsic: how new a read was compared with what's already in the notebook on that topic
             (embedding distance), and whether it came back with concrete facts. Extrinsic: your reaction
             to something it shared (thanks / not my thing / try it), and you bringing a topic up in chat.
             Each topic keeps Beta(a, b) counts of these, so it picks topics that pay off more often.
  sharing    a score from novelty, how much the topic is yours, and its connection drive, behind hard limits
             (a few a day, hours apart, never while something it told you is still unread).

Plain about what it is: a small bandit with a mood, not feelings. Everything stays in <data>; the Heart
panel shows today's plan, the drives and the notebook.
"""
import asyncio
import json
import logging
import math
import random
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.paths import DATA_DIR

logger = logging.getLogger("xod.curiosity")

DB = DATA_DIR / "notebook.db"
_lock = threading.RLock()

DRIVE_BASE = {"curiosity": 0.55, "mastery": 0.5, "connection": 0.45, "rest": 0.3}
DRIVE_HALF_LIFE_H = 8.0
MIN_SHARE_GAP_H = 3.0
SHARE_THRESHOLD = 0.55
MAX_OWN_TOPICS = 40

PLANS = {
    "deep_dive": {"label": "going deep on {topic}", "musings": 6, "acts": {"wonder": 3.0, "reflect": 0.4, "daydream": 0.3}},
    "wander": {"label": "poking around a few different things", "musings": 5, "acts": {"wonder": 2.0, "reflect": 0.8, "daydream": 0.8}},
    "tinker": {"label": "thinking about your projects", "musings": 4, "acts": {"wonder": 0.6, "reflect": 3.0, "daydream": 0.4}},
    "slow": {"label": "taking it slow today", "musings": 2, "acts": {"wonder": 0.6, "reflect": 0.5, "daydream": 2.0}},
}


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS topics (name TEXT PRIMARY KEY, origin TEXT, added REAL, visits INTEGER DEFAULT 0,
            last REAL DEFAULT 0, a REAL DEFAULT 1, b REAL DEFAULT 1, thread TEXT DEFAULT '', mentioned TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, ts REAL, topic TEXT, question TEXT, text TEXT, facts TEXT,
            sources TEXT, novelty REAL, reward REAL, share REAL, shared INTEGER DEFAULT 0, reaction TEXT DEFAULT '',
            journal_id TEXT, embedding BLOB, emb_model TEXT);
        CREATE INDEX IF NOT EXISTS notes_topic ON notes(topic);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    return c


def _get(c: sqlite3.Connection, key: str, default: Any) -> Any:
    r = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    try:
        return json.loads(r["value"]) if r else default
    except ValueError:
        return default


def _put(c: sqlite3.Connection, key: str, value: Any) -> None:
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))


def _today() -> str:
    return time.strftime("%Y-%m-%d")


# ------------------------------------------------------------------ drives

def _drives(c: sqlite3.Connection) -> Dict[str, float]:
    d = _get(c, "drives", None) or {"v": dict(DRIVE_BASE), "at": time.time(), "last_read": 0, "last_share": 0}
    hours = max(0.0, (time.time() - d.get("at", time.time())) / 3600)
    k = 1 - math.pow(0.5, hours / DRIVE_HALF_LIFE_H)
    v = d["v"]
    for key, base in DRIVE_BASE.items():
        v[key] = v.get(key, base) + (base - v.get(key, base)) * k
    # Hunger builds with time: curiosity while it hasn't read anything, connection while it hasn't told you anything.
    v["curiosity"] += min(0.35, 0.04 * hours) if time.time() - d.get("last_read", 0) > 3600 else 0
    v["connection"] += min(0.35, 0.03 * hours) if time.time() - d.get("last_share", 0) > 3 * 3600 else 0
    try:
        from app.services import soul
        energy = soul.load()["mood"].get("energy", 0.7)
        v["rest"] = max(v["rest"], 1 - energy)
    except Exception:
        pass
    d["v"] = {key: round(min(1.0, max(0.0, x)), 4) for key, x in v.items()}
    d["at"] = time.time()
    _put(c, "drives", d)
    return d["v"]


def _nudge(c: sqlite3.Connection, **deltas: float) -> None:
    d = _get(c, "drives", None) or {"v": dict(DRIVE_BASE), "at": time.time(), "last_read": 0, "last_share": 0}
    for k, x in deltas.items():
        if k in DRIVE_BASE:
            d["v"][k] = round(min(1.0, max(0.0, d["v"].get(k, DRIVE_BASE[k]) + x)), 4)
        else:
            d[k] = x
    _put(c, "drives", d)


def drives() -> Dict[str, float]:
    with _lock:
        c = _conn()
        try:
            v = _drives(c)
            c.commit()
            return v
        finally:
            c.close()


# ------------------------------------------------------------------ topics

def _sync_your_topics(c: sqlite3.Connection) -> List[str]:
    try:
        from app.services import soul
        yours = soul.interests(None, 8)
    except Exception:
        yours = []
    for t in yours:
        c.execute("INSERT OR IGNORE INTO topics (name, origin, added) VALUES (?, 'you', ?)", (t, time.time()))
        c.execute("UPDATE topics SET origin = 'you' WHERE name = ? AND origin != 'you'", (t,))
    return yours


def _sample_topic(c: sqlite3.Connection, yours: List[str], exclude: Tuple[str, ...] = ()) -> Optional[sqlite3.Row]:
    rows = c.execute("SELECT * FROM topics").fetchall()
    best, best_v = None, -1.0
    now = time.time()
    for r in rows:
        if r["name"] in exclude:
            continue
        # A topic you no longer bring up fades unless it keeps paying off on its own.
        if r["origin"] == "you" and r["name"] not in yours and r["a"] < 3:
            continue
        v = random.betavariate(max(0.2, r["a"]), max(0.2, r["b"]))
        v += 0.3 if r["name"] in yours else 0.0
        v += 0.25 / (1 + r["visits"])                       # not explored much yet
        v += 0.12 if r["thread"] else 0.0                     # an open question it wants to follow up
        if now - (r["last"] or 0) < 6 * 3600:
            v -= 0.35                                          # just read about it
        if v > best_v:
            best, best_v = r, v
    return best


def pick_topic(exclude: Tuple[str, ...] = ()) -> Optional[Dict[str, Any]]:
    with _lock:
        c = _conn()
        try:
            yours = _sync_your_topics(c)
            plan = _today_plan(c)
            if plan.get("plan") == "deep_dive" and plan.get("topic") and plan["topic"] not in exclude and random.random() < 0.8:
                r = c.execute("SELECT * FROM topics WHERE name = ?", (plan["topic"],)).fetchone()
            else:
                r = _sample_topic(c, yours, exclude)
            c.commit()
            return dict(r) if r else None
        finally:
            c.close()


# ------------------------------------------------------------------ today

def _close_day(c: sqlite3.Connection, day: Dict[str, Any]) -> None:
    """Yesterday's plan gets credit for how it went."""
    if not day or not day.get("plan") or not day.get("reads_or_thoughts"):
        return
    stats = _get(c, "plan_stats", {})
    st = stats.get(day["plan"], {"a": 1.0, "b": 1.0})
    avg = min(1.0, max(0.0, day.get("reward", 0) / max(1, day["reads_or_thoughts"])))
    st["a"] += avg
    st["b"] += 1 - avg
    stats[day["plan"]] = st
    _put(c, "plan_stats", stats)


def _today_plan(c: sqlite3.Connection) -> Dict[str, Any]:
    yours = _sync_your_topics(c)        # your interests change through the day; keep its topic list current
    day = _get(c, "today", {})
    if day.get("date") == _today():
        return day
    _close_day(c, day)
    d = _drives(c)
    stats = _get(c, "plan_stats", {})
    has_topics = bool(c.execute("SELECT 1 FROM topics LIMIT 1").fetchone())
    has_work = True
    try:
        from app.services import soul
        has_work = bool(soul._recent_work(2))
    except Exception:
        pass
    fit = {"deep_dive": 0.6 * d["curiosity"] + 0.4 * d["mastery"] if has_topics else 0.0,
           "wander": d["curiosity"] if has_topics else 0.0,
           "tinker": (0.7 * d["connection"] + 0.3 * d["mastery"]) if has_work else 0.0,
           "slow": 0.25 + d["rest"]}
    scored = {p: random.betavariate(stats.get(p, {}).get("a", 1.0), stats.get(p, {}).get("b", 1.0)) * (0.2 + f)
              for p, f in fit.items()}
    plan = max(scored, key=scored.get)
    topic = ""
    if plan == "deep_dive":
        r = _sample_topic(c, yours)
        topic = r["name"] if r else ""
        if not topic:
            plan = "wander"
    label = PLANS[plan]["label"].format(topic=topic)
    day = {"date": _today(), "plan": plan, "topic": topic, "label": label, "reads_or_thoughts": 0, "reward": 0.0, "shared": 0}
    _put(c, "today", day)
    logger.info(f"today Xod feels like {label}")
    return day


def today() -> Dict[str, Any]:
    with _lock:
        c = _conn()
        try:
            day = _today_plan(c)
            c.commit()
            return {**day, "musings": PLANS[day["plan"]]["musings"], "acts": PLANS[day["plan"]]["acts"]}
        finally:
            c.close()


def _credit_day(c: sqlite3.Connection, reward: float, count: int = 1, shared: int = 0) -> None:
    day = _today_plan(c)
    day["reads_or_thoughts"] = day.get("reads_or_thoughts", 0) + count
    day["reward"] = round(day.get("reward", 0) + reward, 3)
    day["shared"] = day.get("shared", 0) + shared
    _put(c, "today", day)


# ------------------------------------------------------------------ reading up

QUERY_TEMPLATES = ["{t} surprising facts", "{t} new discovery", "{t} news this month", "{t} little-known history",
                   "how does {t} work", "{t} common misconceptions", "{t} research 2026"]

READ_PROMPT = (
    "While {who} was away you got curious about {topic}{q} and read these pages:\n{pages}\n\n"
    "Write exactly these four lines, using only what's in the pages:\n"
    "LEARNED: the single most interesting specific thing you learned, in two short sentences, said to {who} "
    "(a name, a number, a discovery, how something works; not that you found a list or a page). Start with "
    "something like 'I went looking into...' or 'I got curious about...'.\n"
    "FACTS: two or three short concrete facts from the pages, separated by ' | '\n"
    "NEXT: one follow-up question you now wonder about (something the pages raised but didn't answer)\n"
    "SPARK: a related topic you'd like to look into some day, in one to three words, or none")


def _lines(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    key = None
    for line in (text or "").splitlines():
        m = re.match(r"^\s*\**\s*(LEARNED|FACTS|NEXT|SPARK)\s*\**\s*:\s*(.*)$", line, re.I)
        if m:
            key = m.group(1).upper()
            out[key] = m.group(2).strip()
        elif key == "LEARNED" and line.strip():
            out[key] += " " + line.strip()
    return out


async def _novelty(topic: str, text: str) -> Tuple[float, Optional[bytes], str]:
    """1.0 = nothing like it in the notebook on this topic yet; 0 = it already knew this."""
    try:
        from app.services import recall
        vecs, model = await recall._embed([text])
    except Exception:
        return 0.6, None, ""
    v = vecs[0]
    c = _conn()
    try:
        rows = c.execute("SELECT embedding FROM notes WHERE topic = ? AND emb_model = ? ORDER BY ts DESC LIMIT 40",
                         (topic, model)).fetchall()
    finally:
        c.close()
    sims = []
    for r in rows:
        if r["embedding"]:
            ev = np.frombuffer(r["embedding"], dtype=np.float32)
            if ev.shape == v.shape:
                sims.append(float(np.dot(ev, v)))
    if not sims:
        return 1.0, v.astype(np.float32).tobytes(), model
    # nomic similarities for paraphrases sit around 0.85+, unrelated text around 0.4-0.5
    lo, hi = (0.55, 0.9) if model == "nomic" else (0.2, 0.8)
    nov = 1 - (max(sims) - lo) / (hi - lo)
    return round(min(1.0, max(0.0, nov)), 3), v.astype(np.float32).tobytes(), model


def _question_for(topic: Dict[str, Any]) -> str:
    if topic.get("thread") and random.random() < 0.7:
        return topic["thread"]
    return random.choice(QUERY_TEMPLATES).format(t=topic["name"])


async def read_up(ask, voice: str, who: str, web: bool, topic: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """One research session: pick a topic (or use the one given), read about it, write a note. Returns the note."""
    topic = topic or pick_topic()
    if not topic:
        return None
    question = _question_for(topic)
    sources: List[Dict[str, str]] = []
    if web:
        from app.services import web_reader
        from app.services.browser_sandbox import browser_sandbox_service
        if browser_sandbox_service.mission_busy:
            return None
        try:
            from app.services import activity as _act
            ctx = await asyncio.to_thread(_act.before, "web_search", {"query": question})
        except Exception:
            _act, ctx = None, None
        res = await web_reader.search_and_read(question, read_top=2, budget=900)
        if _act and ctx:
            try:
                await asyncio.to_thread(_act.after, ctx, res)      # shows in Activity as its own search
            except Exception:
                pass
        items = [it for it in res.get("results") or [] if it.get("content")] or (res.get("results") or [])[:4]
        pages = []
        for it in items[:3]:
            pages.append(f"- {it.get('title', '')}: {str(it.get('content') or it.get('snippet') or '')[:900]}")
            sources.append({"title": str(it.get("title", ""))[:100], "url": it.get("url")})
        if not pages:
            return None
        raw = await ask(voice, READ_PROMPT.format(who=who, topic=topic["name"], q=f" ({question})" if question != topic["name"] else "",
                                                  pages="\n".join(pages)), 260)
    else:
        raw = await ask(voice, (f"While {who} was away you got curious about {topic['name']} ({question}). From what you already know "
                                "(no searching; skip anything that might be out of date or that you're unsure of), write exactly these lines:\n"
                                "LEARNED: one small, surprising thing about it, in two short sentences, said to them. Start with "
                                "something like 'I was thinking about...'.\nFACTS: two short facts you're sure of, separated by ' | '\n"
                                "NEXT: one follow-up question you now wonder about\nSPARK: a related topic in one to three words, or none"), 220)
    got = _lines(raw)
    text = got.get("LEARNED") or (raw.strip() if raw and "LEARNED" not in raw.upper() else "")
    text = re.sub(r"\s+", " ", text).strip().strip('"')
    if len(text) < 20:
        return None
    facts = [f.strip(" .-") for f in (got.get("FACTS") or "").split("|") if len(f.strip()) > 4][:3]
    nxt = (got.get("NEXT") or "").strip().strip('"')[:200]
    spark = (got.get("SPARK") or "").strip().strip('".').lower()[:40]
    novelty, emb, model = await _novelty(topic["name"], text + " " + " ".join(facts))
    reward = round(novelty * (1.0 if facts else 0.6) * (1.0 if sources or not web else 0.7), 3)
    note = {"id": f"n{int(time.time() * 1000)}", "ts": time.time(), "topic": topic["name"], "question": question, "text": text,
            "facts": facts, "sources": sources[:3], "novelty": novelty, "reward": reward, "next": nxt,
            "origin": topic.get("origin") or "itself"}
    with _lock:
        c = _conn()
        try:
            c.execute("INSERT INTO notes (id, ts, topic, question, text, facts, sources, novelty, reward, share, shared, embedding, emb_model) "
                      "VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?)",
                      (note["id"], note["ts"], topic["name"], question, text, json.dumps(facts), json.dumps(sources[:3]),
                       novelty, reward, emb, model))
            c.execute("UPDATE topics SET visits = visits + 1, last = ?, a = a + ?, b = b + ?, thread = ? WHERE name = ?",
                      (time.time(), reward, 1 - reward, nxt if len(nxt) > 8 else "", topic["name"]))
            if spark and spark not in ("none", "n/a") and len(spark) > 2 and spark != topic["name"]:
                own = c.execute("SELECT COUNT(*) FROM topics WHERE origin = 'itself'").fetchone()[0]
                if own < MAX_OWN_TOPICS:
                    c.execute("INSERT OR IGNORE INTO topics (name, origin, added) VALUES (?, 'itself', ?)", (spark, time.time()))
            _drives(c)
            _nudge(c, curiosity=-0.18, mastery=0.12 * (reward - 0.4), last_read=time.time())
            _credit_day(c, reward)
            c.commit()
        finally:
            c.close()
    return note


def credit_thought(reward: float = 0.4) -> None:
    """A reflection or a daydream counts toward the day too (a little)."""
    with _lock:
        c = _conn()
        try:
            _credit_day(c, reward)
            c.commit()
        finally:
            c.close()


# ------------------------------------------------------------------ telling you (now and then)

def consider_sharing(entry: Dict[str, Any], note: Optional[Dict[str, Any]] = None, asked: bool = False) -> Tuple[bool, float]:
    """Whether this one is worth bringing to you now. Most aren't: they stay in the notebook."""
    with _lock:
        c = _conn()
        try:
            d = _drives(c)
            raw = _get(c, "drives", {})
            day = _today_plan(c)
            try:
                from app.services import soul
                st = soul.load()
                limit = int(st["settings"].get("shares_per_day", 2))
                pending = any(not j.get("seen") and not j.get("quiet") for j in st.get("journal") or [])
                yours = soul.interests(st, 8)
            except Exception:
                limit, pending, yours = 2, False, []
            kind = entry.get("kind")
            novelty = (note or {}).get("novelty", 0.5)
            if kind == "found" or (note and kind == "thought"):
                interest = 1.0 if (note or {}).get("topic") in yours else 0.45
            elif kind == "idea":
                interest, novelty = 0.85, 0.6
            else:                      # a daydream: charming once in a while, not every time
                interest, novelty = 0.25, 0.4
            score = round(0.45 * novelty + 0.35 * interest + 0.2 * d["connection"], 3)
            if asked:
                ok = True              # "What are you thinking about?": you asked, so it tells you
            elif limit <= 0 or day.get("shared", 0) >= limit or pending:
                ok = False
            elif time.time() - raw.get("last_share", 0) < MIN_SHARE_GAP_H * 3600:
                ok = False
            else:
                ok = score >= SHARE_THRESHOLD and random.random() < 0.45 + 0.55 * d["connection"]
            if note:
                c.execute("UPDATE notes SET share = ?, shared = ?, journal_id = ? WHERE id = ?",
                          (score, 1 if ok else 0, entry.get("id"), note["id"]))
            if ok and not asked:
                _nudge(c, connection=-0.4, last_share=time.time())
                _credit_day(c, 0.0, count=0, shared=1)
            c.commit()
            return ok, score
        finally:
            c.close()


def reaction(journal_id: str, reaction: str) -> None:
    """You reacted to something it shared: the strongest reward it gets."""
    if reaction not in ("liked", "dismissed"):
        return
    with _lock:
        c = _conn()
        try:
            r = c.execute("SELECT id, topic, reaction FROM notes WHERE journal_id = ?", (journal_id,)).fetchone()
            good = 1.0 if reaction == "liked" else 0.0
            if r and r["reaction"] != reaction:
                c.execute("UPDATE notes SET reaction = ? WHERE id = ?", (reaction, r["id"]))
                c.execute("UPDATE topics SET a = a + ?, b = b + ? WHERE name = ?", (1.5 * good, 1.5 * (1 - good), r["topic"]))
            _nudge(c, connection=-0.1 if good else 0.0, mastery=0.08 if good else -0.05)
            _credit_day(c, good, count=0)
            c.commit()
        finally:
            c.close()


def user_mentioned(text: str) -> None:
    """You brought up something it has been reading about: a quiet reward for that topic (once a day each)."""
    t = (text or "").lower()
    if len(t) < 4:
        return
    with _lock:
        c = _conn()
        try:
            for r in c.execute("SELECT name, mentioned FROM topics").fetchall():
                if r["mentioned"] != _today() and re.search(r"\b" + re.escape(r["name"]) + r"\b", t):
                    c.execute("UPDATE topics SET a = a + 0.4, mentioned = ? WHERE name = ?", (_today(), r["name"]))
            c.commit()
        except sqlite3.Error:
            pass
        finally:
            c.close()


# ------------------------------------------------------------------ for the page and for chats

def notes(limit: int = 20) -> List[Dict[str, Any]]:
    c = _conn()
    try:
        rows = c.execute("SELECT id, ts, topic, question, text, facts, sources, novelty, shared, reaction FROM notes "
                         "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "facts": json.loads(r["facts"] or "[]"), "sources": json.loads(r["sources"] or "[]")} for r in rows]
    finally:
        c.close()


def public() -> Dict[str, Any]:
    with _lock:
        c = _conn()
        try:
            d = _drives(c)
            day = _today_plan(c)
            raw = _get(c, "drives", {})
            topics = [dict(r) for r in c.execute(
                "SELECT name, origin, visits, a, b, thread FROM topics ORDER BY (a / (a + b)) DESC, visits DESC LIMIT 10").fetchall()]
            n = c.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            c.commit()
        finally:
            c.close()
    return {"today": {"plan": day["plan"], "label": day["label"], "topic": day.get("topic", ""), "shared": day.get("shared", 0),
                      "done": day.get("reads_or_thoughts", 0)},
            "drives": d, "last_share": raw.get("last_share", 0),
            "topics": [{"name": t["name"], "origin": t["origin"], "visits": t["visits"], "liking": round(t["a"] / (t["a"] + t["b"]), 2),
                        "thread": t["thread"]} for t in topics],
            "notes": notes(12), "note_count": n}


def recent_note(limit: int = 5) -> str:
    """For a prompt, when you ask what it's been up to."""
    try:
        ns = notes(limit)
        day = today()
    except Exception:
        return ""
    if not ns:
        return ""
    from app.services.work_state import _ago
    lines = [f"- [{_ago(n['ts'])} · {n['topic']}] {n['text']}" + (f" Facts: {'; '.join(n['facts'])}" if n["facts"] else "")
             for n in ns]
    return (f"Your own reading lately, from your notebook (today you feel like {day['label']}; mention these only if "
            "asked what you've been up to or if one is relevant):\n" + "\n".join(lines))
