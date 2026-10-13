"""
Xod's own research notebook, and what it feels like doing today.

While you're away, the companion reads up on things (services/soul.py, muse). Before, every read became a
postcard. Now most of what it learns goes quietly into its notebook, and only now and then does it bring
you something: when it found something new about a thing you care about, it hasn't told you anything in a
while, and it hasn't used up its few shares for the day.

The parts:
  gaps       it only reads up on what it isn't sure of, so every read can add something: questions from your
             chats it got corrected on, failed its own check on, or hedged on (the general part only, never
             anything about you), and, for the topics it follows, questions it probes itself on: it answers each
             twice and says how sure it is; answers that disagree or low confidence mean it doesn't really know.
             What it does know is recorded too, so it doesn't spend time on it again.
  notebook   <data>/notebook.db: topics it follows (yours, and ones it picked up itself while reading),
             notes (the question, the answer from the pages, whether its own guess was wrong, facts, sources,
             the follow-up question it's now unsure about). Chats use the notebook: a question it has read up on
             gets that answer (shown as a Notebook step), with when it read it and the sources.
  drives     curiosity, mastery, connection, rest (0-1). Curiosity builds up while it hasn't read anything,
             connection while it hasn't shared anything, rest when its mood's energy is low. They drift back
             to a baseline over hours.
  today      once a day it picks a plan (go deep on one topic, wander, think about your projects, take it
             slow) by sampling how well each plan went before (Thompson sampling over Beta counts), weighted
             by its drives. The plan sets how much it does and what kind of thing.
  rewards    intrinsic: how unsure it was, whether its guess turned out wrong (the most it can learn), and how
             new the answer is compared with what's already in the notebook on that topic (embedding distance). Extrinsic: your reaction
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
        CREATE TABLE IF NOT EXISTS gaps (id TEXT PRIMARY KEY, ts REAL, question TEXT, norm TEXT, topic TEXT, source TEXT,
            guess TEXT, context TEXT, priority REAL, status TEXT, uncertainty REAL, tries INTEGER DEFAULT 0, note_id TEXT);
        CREATE INDEX IF NOT EXISTS gaps_status ON gaps(status, priority);
        CREATE INDEX IF NOT EXISTS gaps_norm ON gaps(norm);
    """)
    for col in ("answer TEXT", "changed INTEGER DEFAULT 0", "uncertainty REAL", "gap_id TEXT", "from_chat INTEGER DEFAULT 0"):
        try:
            c.execute(f"ALTER TABLE notes ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass                      # already there
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


# ------------------------------------------------------------------ knowledge gaps: what it isn't sure of

# It only researches what the model isn't sure of, so every read can actually add something. Gaps come from:
#   your chats   a question it got corrected on, one its own check failed, one it hedged on ("I'm not sure…"):
#                the general, factual part only, never anything about you
#   probing      for a topic it follows, it writes a few questions someone like you might ask, answers each twice
#                (sampled) and says how sure it is. Answers that disagree with each other mean it doesn't really know
#                (the idea behind SelfCheckGPT and semantic entropy). Its own "how sure" counts for less: a small
#                model says 100 about things it's making up. And anything about recent or current events is unsure
#                by definition: it happened after the model learned what it knows, however confident it sounds.
#                Unsure ones get researched; the rest are recorded as known so it doesn't spend time on them.
UNSURE = 0.35          # below this it considers itself sure enough: nothing worth reading up
MAX_OPEN_GAPS = 200
PERSONAL_RE = re.compile(r"\b(my|me|mine|myself|i|i'?m|i'?ve|i'?d|i'?ll|our|ours|we|us)\b", re.I)
COMMAND_RE = re.compile(r"^\s*(please\s+)?(write|draft|make|build|create|open|send|remind|schedule|delete|move|fix|run|play|"
                        r"generate|draw|translate|summari[sz]e|rewrite|code|help|tidy|clean|book|order|apply|show|list|tell me a)\b", re.I)
HEDGE_RE = re.compile(r"\b(i'?m not (sure|certain)|i don'?t know|not (entirely )?sure|i couldn'?t (find|confirm|verify)|"
                      r"i (can'?t|cannot) (confirm|verify)|my (knowledge|training|information) (may|might|could) be (out of date|outdated)|"
                      r"as of my (last|knowledge) (update|cutoff)|i don'?t have (reliable |current |up-to-date )?(information|data|details))\b", re.I)


STALE_RE = re.compile(r"\b(latest|most recent|recent(ly)?|current(ly)?|right now|nowadays|today|this (year|month|week|season)|"
                      r"so far|as of|newest|upcoming|still|anymore|these days)\b", re.I)


def stale(question: str) -> bool:
    """About things that may have changed since the model was trained."""
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", question or "")]
    return bool(STALE_RE.search(question or "")) or any(y >= time.localtime().tm_year - 1 for y in years)


def _content(text: str) -> set:
    nums = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight",
            "9": "nine", "10": "ten", "11": "eleven", "12": "twelve"}
    words = re.findall(r"[a-z0-9][a-z0-9.,'-]*", (text or "").lower())
    out = set()
    for w in words:
        w = w.strip(".,'-")
        w = nums.get(w, w)
        if len(w) > 2 or w.isdigit():
            out.add(w)
    return out - {"the", "and", "are", "was", "has", "have", "about", "with", "from", "that", "this", "its", "for", "which"}


def researchable(question: str) -> bool:
    """A general, factual question it could look up: not about you, not a request to do something."""
    q = (question or "").strip()
    if not 10 <= len(q) <= 240 or PERSONAL_RE.search(q) or COMMAND_RE.search(q):
        return False
    try:
        from app.services.user_memory import SECRET_RE
        if SECRET_RE.search(q):
            return False
    except Exception:
        pass
    return True


def _norm(q: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (q or "").lower()).strip()


def add_gap(question: str, source: str, guess: str = "", priority: float = 0.7, context: str = "", topic: str = "") -> bool:
    """Something it wasn't sure of. Returns True if it was added as an open question."""
    try:
        from app.services import soul
        if not soul.load()["settings"].get("chat_gaps", True) and source in ("correction", "self_check", "hedge"):
            return False
    except Exception:
        pass
    if not researchable(question):
        return False
    with _lock:
        c = _conn()
        try:
            n = _norm(question)
            if c.execute("SELECT 1 FROM gaps WHERE norm = ?", (n,)).fetchone():
                c.execute("UPDATE gaps SET priority = MAX(priority, ?) WHERE norm = ? AND status = 'open'", (priority, n))
                c.commit()
                return False
            if c.execute("SELECT COUNT(*) FROM gaps WHERE status = 'open'").fetchone()[0] >= MAX_OPEN_GAPS:
                c.execute("DELETE FROM gaps WHERE id = (SELECT id FROM gaps WHERE status = 'open' ORDER BY priority, ts LIMIT 1)")
            c.execute("INSERT INTO gaps (id, ts, question, norm, topic, source, guess, context, priority, status, uncertainty) "
                      "VALUES (?,?,?,?,?,?,?,?,?, 'open', ?)",
                      (f"g{int(time.time() * 1000)}{random.randint(100, 999)}", time.time(), question.strip()[:240], n, topic, source,
                       re.sub(r"\s+", " ", guess or "")[:300], (context or "")[:300], priority, None))
            c.commit()
            return True
        finally:
            c.close()


PROBE_QUESTIONS = ("Write {n} specific factual questions about {topic} that {who} might want answered: recent developments, "
                   "numbers, dates, names, how things work, details that are easy to get wrong. One question per line, no numbering, "
                   "nothing else.")
PROBE_ANSWERS = ("Answer each question from what you know, in at most 15 words, then say how sure you are from 0 to 100. If you "
                 "don't know, guess anyway and give a low number. One line per question, in this format:\n"
                 "1. <answer> | sure: <0-100>\n\nQuestions:\n{qs}")


def _parse_answers(raw: str, n: int) -> List[Tuple[str, Optional[float]]]:
    out: List[Tuple[str, Optional[float]]] = [("", None)] * n
    for line in (raw or "").splitlines():
        m = re.match(r"^\s*\**(\d+)[.)]\s*(.*?)\s*\|\s*sure\s*:?\s*(\d{1,3})", line, re.I)
        if m and 1 <= int(m.group(1)) <= n:
            out[int(m.group(1)) - 1] = (m.group(2).strip(), min(100.0, float(m.group(3))) / 100)
    return out


async def uncertainty(ask, questions: List[str]) -> List[Dict[str, Any]]:
    """How unsure the model is about each question: two sampled answers (do they agree?) plus its own confidence."""
    qs = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    raws = await asyncio.gather(ask("Answer briefly and honestly.", PROBE_ANSWERS.format(qs=qs), 60 + 40 * len(questions)),
                                ask("Answer briefly and honestly.", PROBE_ANSWERS.format(qs=qs), 60 + 40 * len(questions)))
    a1, a2 = _parse_answers(raws[0], len(questions)), _parse_answers(raws[1], len(questions))
    texts = [x[0] or "?" for x in a1] + [x[0] or "?" for x in a2]
    try:
        from app.services import recall
        vecs, model = await recall._embed(texts)
    except Exception:
        vecs, model = [], ""
    out = []
    for i, q in enumerate(questions):
        confs = [x for x in (a1[i][1], a2[i][1]) if x is not None]
        conf = sum(confs) / len(confs) if confs else 0.4
        agree = 0.5
        if a1[i][0] and a2[i][0]:
            # Same key words and numbers in both ("eight legs" / "A spider has eight legs") is agreement, whatever
            # the phrasing; the embedding catches agreement in different words.
            asked = _content(q)          # words repeated from the question aren't agreement
            w1, w2 = _content(a1[i][0]) - asked, _content(a2[i][0]) - asked
            overlap = len(w1 & w2) / max(1, min(len(w1), len(w2))) if w1 and w2 else 0.0
            sem = 0.0
            if vecs:
                sim = float(np.dot(vecs[i], vecs[len(questions) + i]))
                lo, hi = (0.6, 0.92) if model == "nomic" else (0.3, 0.9)
                sem = min(1.0, max(0.0, (sim - lo) / (hi - lo)))
            agree = max(overlap, sem)
        u = 0.7 * (1 - agree) + 0.3 * (1 - conf)
        if stale(q):
            u = max(u, 0.6)          # it can't know, however sure it sounds
        u = round(u, 3)
        out.append({"question": q, "uncertainty": u, "confidence": round(conf, 2), "agreement": round(agree, 2),
                    "stale": stale(q), "guess": a1[i][0] or a2[i][0]})
    return out


async def probe(ask, who: str, topic: Dict[str, Any], n: int = 4) -> List[Dict[str, Any]]:
    """Questions about a topic, each with how unsure it is. Records them all (known ones too, so it doesn't re-ask)."""
    raw = await ask("You write good questions.", PROBE_QUESTIONS.format(n=n, topic=topic["name"], who=who), 220)
    qs = [re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", l).strip() for l in (raw or "").splitlines()]
    qs = [q for q in qs if q.endswith("?") and 12 < len(q) < 200]
    if topic.get("thread"):
        qs.insert(0, topic["thread"])
    c = _conn()
    try:
        seen = {r["norm"] for r in c.execute("SELECT norm FROM gaps").fetchall()}
    finally:
        c.close()
    qs = [q for q in dict.fromkeys(qs) if _norm(q) not in seen][:n]
    if not qs:
        return []
    scored = await uncertainty(ask, qs)
    with _lock:
        c = _conn()
        try:
            for s in scored:
                status = "open" if s["uncertainty"] >= UNSURE else "known"
                c.execute("INSERT OR IGNORE INTO gaps (id, ts, question, norm, topic, source, guess, context, priority, status, uncertainty) "
                          "VALUES (?,?,?,?,?, 'probe', ?, '', ?, ?, ?)",
                          (f"g{int(time.time() * 1000)}{random.randint(100, 999)}", time.time(), s["question"], _norm(s["question"]),
                           topic["name"], s["guess"][:300], round(0.3 + 0.5 * s["uncertainty"], 3), status, s["uncertainty"]))
            c.commit()
        finally:
            c.close()
    return scored


def open_gaps(limit: int = 10) -> List[Dict[str, Any]]:
    c = _conn()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM gaps WHERE status = 'open' ORDER BY priority DESC, ts DESC LIMIT ?", (limit,)).fetchall()]
    finally:
        c.close()


async def next_question(ask, who: str, topic: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """What to read up on: a question from your chats it couldn't answer well, or the thing it's least sure of about
    one of its topics. None when it already knows its topics well enough (nothing worth reading)."""
    if topic is None:
        chat = [g for g in open_gaps(20) if g["source"] in ("correction", "self_check", "hedge") and (g["tries"] or 0) < 2]
        if chat:
            return chat[0]            # its topic is named while reading (read_up)
        topic = pick_topic()
    if not topic:
        return None
    waiting = [g for g in open_gaps(40) if g["topic"] == topic["name"] and (g["tries"] or 0) < 2]
    if waiting:
        return waiting[0]
    scored = await probe(ask, who, topic)
    unsure = sorted((s for s in scored if s["uncertainty"] >= UNSURE), key=lambda s: -s["uncertainty"])
    if not unsure:
        # It knows this topic well enough for now: less reason to come back soon.
        with _lock:
            c = _conn()
            try:
                c.execute("UPDATE topics SET b = b + 0.4, last = ? WHERE name = ?", (time.time(), topic["name"]))
                c.commit()
            finally:
                c.close()
        return None
    c = _conn()
    try:
        r = c.execute("SELECT * FROM gaps WHERE norm = ?", (_norm(unsure[0]["question"]),)).fetchone()
    finally:
        c.close()
    return dict(r) if r else None


def _topic_of(question: str) -> str:
    """A topic name for a question from a chat: the one of its topics it mentions, or its main words."""
    c = _conn()
    try:
        names = [r["name"] for r in c.execute("SELECT name FROM topics").fetchall()]
    finally:
        c.close()
    q = question.lower()
    for n in names:
        if re.search(r"\b" + re.escape(n) + r"\b", q):
            return n
    try:
        from app.services.soul import _keywords
        kw = _keywords(question)
        return " ".join(kw[:2]) or "your questions"
    except Exception:
        return "your questions"


# ------------------------------------------------------------------ reading up

READ_PROMPT = (
    "You weren't sure about this question{why}: {question}\n{guess}"
    "You looked it up in your own browser and read these pages:\n{pages}\n\n"
    "Write exactly these lines, using only what's in the pages:\n"
    "ANSWER: the answer to the question, in one or two sentences, or UNKNOWN if the pages don't say\n"
    "CHANGED: yes if the pages show your earlier guess was wrong or out of date, otherwise no\n"
    "LEARNED: what you found out, in two short sentences, said to {who}. Start with something like 'I wasn't sure "
    "about … so I looked it up:'{chat_hint}\n"
    "FACTS: two or three short concrete facts from the pages, separated by ' | '\n"
    "NEXT: one follow-up question you're now unsure about (something the pages raised but didn't answer)\n"
    "SPARK: a related topic you'd like to look into some day, in one to three words, or none\n"
    "TOPIC: what the question is about, in one to three words (like 'Grímsvötn volcano' or 'sourdough')")


def _lines(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    key = None
    for line in (text or "").splitlines():
        m = re.match(r"^\s*\**\s*(ANSWER|CHANGED|LEARNED|FACTS|NEXT|SPARK|TOPIC)\s*\**\s*:\s*(.*)$", line, re.I)
        if m:
            key = m.group(1).upper()
            out[key] = m.group(2).strip()
        elif key in ("LEARNED", "ANSWER") and line.strip():
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


def _gap_tried(gap_id: str, answered: bool) -> None:
    with _lock:
        c = _conn()
        try:
            c.execute("UPDATE gaps SET tries = COALESCE(tries, 0) + 1, status = CASE WHEN ? THEN 'answered' "
                      "WHEN COALESCE(tries, 0) + 1 >= 2 THEN 'unanswered' ELSE status END WHERE id = ?", (1 if answered else 0, gap_id))
            c.commit()
        finally:
            c.close()


async def read_up(ask, voice: str, who: str, web: bool, topic: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """One research session on something it isn't sure of. Returns the note, or None (nothing it needed to look up,
    the browser is busy, or the pages didn't answer it). Without web reading there's nothing new to learn from, so
    it doesn't pretend: no note."""
    if not web:
        return None
    gap = await next_question(ask, who, topic)
    if not gap:
        return None
    question, topic_name = gap["question"], gap["topic"] or _topic_of(gap["question"])
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
    pages, sources = [], []
    for it in items[:3]:
        pages.append(f"- {it.get('title', '')}: {str(it.get('content') or it.get('snippet') or '')[:900]}")
        sources.append({"title": str(it.get("title", ""))[:100], "url": it.get("url")})
    if not pages:
        _gap_tried(gap["id"], False)
        return None
    from_chat = gap["source"] in ("correction", "self_check", "hedge")
    raw = await ask(voice, READ_PROMPT.format(
        question=question, who=who, pages="\n".join(pages),
        why=f" (it came up when {who} asked you{' and corrected you' if gap['source'] == 'correction' else ''})" if from_chat else "",
        guess=(f"Your earlier answer was: {gap['guess']}\n" if gap.get("guess") else "") +
              (f"What {who} said about it: {gap['context']}\n" if gap.get("context") else ""),
        chat_hint=f" Mention that it came up when {who} asked." if from_chat else ""), 320)
    got = _lines(raw)
    answer = re.sub(r"\s+", " ", got.get("ANSWER") or "").strip()
    answered = bool(answer) and not answer.upper().startswith("UNKNOWN")
    text = re.sub(r"\s+", " ", got.get("LEARNED") or "").strip().strip('"')
    if not answered or len(text) < 20:
        _gap_tried(gap["id"], False)
        with _lock:
            c = _conn()
            try:
                _credit_day(c, 0.1)
                c.commit()
            finally:
                c.close()
        return None
    changed = (got.get("CHANGED") or "").strip().lower().startswith("y")
    facts = [f.strip(" .-") for f in (got.get("FACTS") or "").split("|") if len(f.strip()) > 4][:3]
    nxt = (got.get("NEXT") or "").strip().strip('"')[:200]
    spark = (got.get("SPARK") or "").strip().strip('".').lower()[:40]
    named = (got.get("TOPIC") or "").strip().strip('".').lower()[:40]
    if not gap["topic"] and 2 < len(named) and len(named.split()) <= 4:
        topic_name = named            # a question from a chat: the model names its subject better than keywords do
    novelty, emb, model = await _novelty(topic_name, f"{question} {answer} {' '.join(facts)}")
    unsure = gap.get("uncertainty") if gap.get("uncertainty") is not None else 0.6
    # What it's worth: how unsure it was, whether it turned out to be wrong, and whether this is new to the notebook.
    reward = round(min(1.0, (0.35 + 0.45 * unsure + (0.3 if changed else 0.0) + (0.15 if from_chat else 0.0)) * (0.5 + 0.5 * novelty)), 3)
    note = {"id": f"n{int(time.time() * 1000)}", "ts": time.time(), "topic": topic_name, "question": question, "answer": answer,
            "changed": changed, "uncertainty": unsure, "from_chat": from_chat, "text": text, "facts": facts, "sources": sources[:3],
            "novelty": novelty, "reward": reward, "next": nxt, "gap_id": gap["id"]}
    with _lock:
        c = _conn()
        try:
            c.execute("INSERT INTO notes (id, ts, topic, question, text, facts, sources, novelty, reward, share, shared, embedding, emb_model, "
                      "answer, changed, uncertainty, gap_id, from_chat) VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?,?,?)",
                      (note["id"], note["ts"], topic_name, question, text, json.dumps(facts), json.dumps(sources[:3]),
                       novelty, reward, emb, model, answer, 1 if changed else 0, unsure, gap["id"], 1 if from_chat else 0))
            c.execute("UPDATE gaps SET status = 'answered', tries = COALESCE(tries, 0) + 1, note_id = ? WHERE id = ?", (note["id"], gap["id"]))
            c.execute("INSERT OR IGNORE INTO topics (name, origin, added) VALUES (?, 'itself', ?)", (topic_name, time.time()))
            c.execute("UPDATE topics SET visits = visits + 1, last = ?, a = a + ?, b = b + ? WHERE name = ?",
                      (time.time(), reward, 1 - reward, topic_name))
            if len(nxt) > 8 and nxt.endswith("?") and researchable(nxt):
                c.execute("UPDATE topics SET thread = ? WHERE name = ?", (nxt, topic_name))
            if spark and spark not in ("none", "n/a") and len(spark) > 2 and spark != topic_name:
                own = c.execute("SELECT COUNT(*) FROM topics WHERE origin = 'itself'").fetchone()[0]
                if own < MAX_OWN_TOPICS:
                    c.execute("INSERT OR IGNORE INTO topics (name, origin, added) VALUES (?, 'itself', ?)", (spark, time.time()))
            _drives(c)
            _nudge(c, curiosity=-0.18, mastery=0.12 * (reward - 0.4) + (0.08 if changed else 0), last_read=time.time())
            _credit_day(c, reward)
            c.commit()
        finally:
            c.close()
    return note


# ------------------------------------------------------------------ using what it learned

KNOW_MIN_SIM = 0.72        # nomic: a question and a note that answers it


async def knowledge(query: str, k: int = 3) -> List[Dict[str, Any]]:
    """Notes from its own reading that answer something like this question, best first."""
    c = _conn()
    try:
        rows = c.execute("SELECT id, ts, topic, question, answer, text, facts, sources, embedding FROM notes "
                         "WHERE emb_model = 'nomic' AND embedding IS NOT NULL AND answer IS NOT NULL AND answer != ''").fetchall()
    finally:
        c.close()
    if not rows:
        return []
    from app.services import recall
    vecs, model = await recall._embed([query], query=True)
    if model != "nomic":
        return []
    q = vecs[0]
    scored = []
    for r in rows:
        ev = np.frombuffer(r["embedding"], dtype=np.float32)
        if ev.shape == q.shape:
            scored.append((float(np.dot(ev, q)), r))
    scored.sort(key=lambda x: -x[0])
    return [{"sim": round(s, 3), "id": r["id"], "ts": r["ts"], "topic": r["topic"], "question": r["question"], "answer": r["answer"],
             "facts": json.loads(r["facts"] or "[]"), "sources": json.loads(r["sources"] or "[]")}
            for s, r in scored[:k] if s >= KNOW_MIN_SIM]


def knowledge_note(found: List[Dict[str, Any]]) -> str:
    if not found:
        return ""
    from app.services.recall import when_label
    lines = []
    for n in found:
        src = ", ".join(s.get("url", "") for s in n["sources"][:2] if s.get("url"))
        lines.append(f"- [{when_label(n['ts'])}] {n['question']} → {n['answer']}" + (f" ({'; '.join(n['facts'])})" if n["facts"] else "") +
                     (f" Sources: {src}" if src else ""))
    return ("From your own notebook (things you looked up yourself because you weren't sure; use them if they answer "
            "the question, say you read up on it and when, and search again if it may have changed since):\n" + "\n".join(lines))


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
            if note:
                interest = 1.0 if (note.get("from_chat") or note.get("topic") in yours) else 0.45
                if note.get("changed"):
                    novelty = min(1.0, novelty + 0.3)        # it had it wrong: worth telling
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
        rows = c.execute("SELECT id, ts, topic, question, answer, changed, from_chat, text, facts, sources, novelty, shared, reaction FROM notes "
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
            gaps = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) AS n FROM gaps GROUP BY status").fetchall()}
            wondering = [dict(r) for r in c.execute("SELECT question, source, uncertainty FROM gaps WHERE status = 'open' "
                                                    "ORDER BY priority DESC, ts DESC LIMIT 4").fetchall()]
            c.commit()
        finally:
            c.close()
    return {"today": {"plan": day["plan"], "label": day["label"], "topic": day.get("topic", ""), "shared": day.get("shared", 0),
                      "done": day.get("reads_or_thoughts", 0)},
            "drives": d, "last_share": raw.get("last_share", 0),
            "topics": [{"name": t["name"], "origin": t["origin"], "visits": t["visits"], "liking": round(t["a"] / (t["a"] + t["b"]), 2),
                        "thread": t["thread"]} for t in topics],
            "notes": notes(12), "note_count": n, "gaps": gaps, "wondering": wondering}


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
    lines = [f"- [{_ago(n['ts'])} · {n['topic']}] Wasn't sure: {n['question']} Found: {n['answer'] or n['text']}" +
             (" (its earlier guess was wrong)" if n.get("changed") else "") + (f" Facts: {'; '.join(n['facts'])}" if n["facts"] else "")
             for n in ns]
    wondering = open_gaps(3)
    if wondering:
        lines.append("Still unsure about (not looked up yet): " + "; ".join(g["question"] for g in wondering))
    return (f"Your own reading lately, from your notebook (today you feel like {day['label']}; mention these only if "
            "asked what you've been up to or if one is relevant):\n" + "\n".join(lines))
