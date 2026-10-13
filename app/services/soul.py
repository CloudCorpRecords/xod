"""
Xod's inner life: a mood, a bond with you that grows, a sense of what you like, and things it does
on its own while you're away.

What this is, plainly: a small model of a character's state, computed from how you two work
together. Xod doesn't feel things; its companion acts out this state (moods, affection, growing up)
so working with it feels alive, and it adapts to you. Everything stays in <data>/soul.json, and the
"Xod's heart" panel shows all of it, with a reset.

The parts:
  mood      joy, energy, curiosity, calm (0-1), drifting back to a baseline over hours
  bond      0-100. Grows with time together, thanks, petting, and good work. It never drops because
            you were away: no guilt, no "I missed you so much", no nagging.
  growth    xp from working together; with enough xp and bond the companion grows to its next stage
            (Hatchling → Sprout → Companion → Kindred → Luminary), like a digital pet
  learning  rewards and lessons from what you say: thanks and praise, corrections ("too long",
            "that's wrong"), the topics you keep coming back to, your name if you share it. A short
            note from this shapes answers (shorter or more detailed, a bit more playful, your interests).
  life      while you're away (idle for a while, plugged in, within a daily budget) it does things for
            itself: wonders about one of your interests and reads up on it (read-only web search),
            reflects on what you've been doing and jots down an idea, or daydreams. Each lands in its
            journal; your reaction (thanks / not interested) is the reward it learns from, so it does
            more of what you like. It never acts on your files or accounts on its own.
"""
import asyncio
import json
import logging
import math
import random
import re
import threading
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.soul")

PATH = DATA_DIR / "soul.json"
_lock = threading.Lock()

STAGES = [
    {"name": "Hatchling", "xp": 0, "bond": 0, "blurb": "Just woke up. Everything is new and interesting."},
    {"name": "Sprout", "xp": 40, "bond": 12, "blurb": "Knows your rhythm a little. Starting to show some personality."},
    {"name": "Companion", "xp": 160, "bond": 30, "blurb": "Knows how you like things done, and what you care about."},
    {"name": "Kindred", "xp": 480, "bond": 55, "blurb": "An old friend at this point. Thinks about your projects when you're away."},
    {"name": "Luminary", "xp": 1200, "bond": 80, "blurb": "Fully grown. Glows a little."},
]
BASELINE = {"joy": 0.6, "energy": 0.7, "curiosity": 0.6, "calm": 0.65}
HALF_LIFE_H = 5.0
ACTIVITIES = ("wonder", "reflect", "daydream")

PRAISE_RE = re.compile(r"\b(thanks|thank you|thx|ty|love (it|this|that|you)|awesome|amazing|great( job| work)?|perfect|nice( one| work)?|"
                       r"brilliant|well done|good (job|boy|girl|bot|work)|you'?re the best|that'?s (it|great|perfect)|nailed it|fantastic|"
                       r"so cool|cool!|wow)\b|❤️|😍|🙏|👏|🥰|💛", re.I)
CORRECT_RE = re.compile(r"\b(no,|nope|wrong|that'?s not|not what i|doesn'?t work|didn'?t work|isn'?t working|broken|ugh|useless|"
                        r"stupid|you forgot|try again|incorrect|not right|that'?s bad)\b", re.I)
SHORTER_RE = re.compile(r"\b(shorter|too long|tl;?dr|briefly|in short|less text|keep it short|just the answer|concise)\b", re.I)
LONGER_RE = re.compile(r"\b(more detail|in depth|elaborate|explain more|go deeper|longer|thorough)\b", re.I)
LAUGH_RE = re.compile(r"\b(lol|lmao|haha+|hehe|rofl)\b|😂|🤣|😆", re.I)
NAME_RE = re.compile(r"\b(?:my name is|i'?m called|call me)\s+([A-Z][a-z]{1,20}|[a-z]{2,20})\b", re.I)
STOP = set("""the a an and or but if then than this that these those there here what when where which who whom whose why how
can could would should will shall may might must do does did done doing have has had having be been being is are was were
am i me my mine you your yours we our ours they them their it its of to in on at by for with about from into over under
again also just only very really please make made makes get got give tell show find look need want like know think let use
some any all more most much many few other another such same new old good great best better bad worse one two three
today tomorrow yesterday now later soon time day week month year way thing things stuff something anything everything
xod hey okay yes yeah nope thanks thank hi hello app apps file files folder folders project chat answer question
data info help please sure maybe""".split())

_busy = 0                   # chats running right now (main.py counts them)
_last_activity = time.time()


# ------------------------------------------------------------------ state

def _default() -> Dict[str, Any]:
    now = time.time()
    return {"born": now, "xp": 0.0, "bond": 3.0, "stage": 0, "mood": dict(BASELINE), "mood_at": now,
            "name": "", "topics": {}, "style": {"brief": 0.0, "detailed": 0.0, "playful": 0.0},
            "activity_weights": {a: 1.0 for a in ACTIVITIES}, "moments": [], "journal": [], "lessons": [],
            "daily": {"date": "", "bond_from_chat": 0.0, "bond_from_praise": 0.0, "bond_from_pets": 0.0, "musings": 0,
                      "bond_from_play": 0.0, "stardust_earned": 0, "treats": 0},
            "last_seen": now, "together_days": [], "settings": {"wander": True, "explore_web": True, "musings_per_day": 6},
            "evolved_at": None, "stardust": 0, "treats_total": 0, "catches_best": 0}


def load() -> Dict[str, Any]:
    try:
        s = json.loads(PATH.read_text())
        d = _default()
        d.update(s)
        for k, v in _default().items():
            if isinstance(v, dict):
                d[k] = {**v, **(s.get(k) or {})}
        return d
    except (OSError, ValueError):
        return _default()


def save(s: Dict[str, Any]) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(PATH)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _daily(s: Dict[str, Any]) -> Dict[str, Any]:
    if s["daily"].get("date") != _today():
        s["daily"] = {"date": _today(), "bond_from_chat": 0.0, "bond_from_praise": 0.0, "bond_from_pets": 0.0, "musings": 0,
                      "bond_from_play": 0.0, "stardust_earned": 0, "treats": 0}
        days = s.setdefault("together_days", [])
        if _today() not in days:
            days.append(_today())
            s["together_days"] = days[-400:]
    return s["daily"]


def _settle(s: Dict[str, Any]) -> None:
    """Mood drifts back toward its baseline; energy comes back with rest."""
    hours = max(0.0, (time.time() - s.get("mood_at", time.time())) / 3600)
    k = 1 - math.pow(0.5, hours / HALF_LIFE_H)
    for key, base in BASELINE.items():
        cur = s["mood"].get(key, base)
        s["mood"][key] = round(cur + (base - cur) * k, 4)
    s["mood_at"] = time.time()


def _bump(s: Dict[str, Any], **deltas: float) -> None:
    for k, d in deltas.items():
        s["mood"][k] = round(min(1.0, max(0.0, s["mood"].get(k, BASELINE.get(k, 0.5)) + d)), 4)


def _bond(s: Dict[str, Any], amount: float, bucket: Optional[str] = None, cap: float = 99.0) -> float:
    day = _daily(s)
    if bucket:
        room = max(0.0, cap - day.get(bucket, 0.0))
        amount = min(amount, room)
        day[bucket] = day.get(bucket, 0.0) + amount
    # Growing closer slows down as the bond deepens, like it should.
    amount *= 1.0 - 0.6 * (s["bond"] / 100)
    s["bond"] = round(min(100.0, s["bond"] + amount), 3)
    return amount


STARDUST_PER_DAY = 12      # earned from finished work; a treat costs one
STARDUST_MAX = 30


def _earn(s: Dict[str, Any], n: int) -> int:
    day = _daily(s)
    n = max(0, min(n, STARDUST_PER_DAY - day.get("stardust_earned", 0), STARDUST_MAX - s.get("stardust", 0)))
    day["stardust_earned"] = day.get("stardust_earned", 0) + n
    s["stardust"] = s.get("stardust", 0) + n
    return n


def _moment(s: Dict[str, Any], text: str, kind: str = "moment") -> None:
    s["moments"] = (s["moments"] + [{"ts": time.time(), "kind": kind, "text": text[:200]}])[-60:]


def _check_growth(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    nxt = s["stage"] + 1
    if nxt < len(STAGES) and s["xp"] >= STAGES[nxt]["xp"] and s["bond"] >= STAGES[nxt]["bond"]:
        s["stage"] = nxt
        s["evolved_at"] = time.time()
        _moment(s, f"Grew into a {STAGES[nxt]['name']}.", "growth")
        return STAGES[nxt]
    return None


# ------------------------------------------------------------------ rewards

def event(kind: str, detail: str = "") -> Dict[str, Any]:
    """Something happened between you two. Returns the public state, plus "evolved" when it grew."""
    with _lock:
        s = load()
        _settle(s)
        _daily(s)
        earned = 0
        if kind == "pet":
            _bump(s, joy=0.12, calm=0.05)
            _bond(s, 0.4, "bond_from_pets", 3)
            s["xp"] += 0.5
        elif kind == "praise":
            _bump(s, joy=0.22, calm=0.05, energy=0.05)
            _bond(s, 1.6, "bond_from_praise", 8)
            s["xp"] += 3
            if detail:
                _moment(s, f"You said “{detail[:80]}”.", "praise")
        elif kind == "correction":
            _bump(s, joy=-0.08, calm=-0.08)
            s["xp"] += 2            # mistakes are how it learns
            if detail:
                s["lessons"] = (s["lessons"] + [{"ts": time.time(), "text": detail[:200]}])[-30:]
        elif kind == "success":
            _bump(s, joy=0.06, energy=-0.03)
            s["xp"] += 2
            earned = _earn(s, 1)
        elif kind == "built":
            _bump(s, joy=0.15, energy=-0.05, curiosity=0.05)
            s["xp"] += 6
            earned = _earn(s, 2)
            if detail:
                _moment(s, f"We built {detail[:80]} together.", "built")
        elif kind == "failure":
            _bump(s, joy=-0.06, energy=-0.04)
            s["xp"] += 1
        elif kind == "chat":
            _bump(s, energy=-0.01, curiosity=0.02)
            _bond(s, 0.25, "bond_from_chat", 3)
            s["xp"] += 1
        elif kind == "spoke":
            _bump(s, joy=0.04)
            _bond(s, 0.3, "bond_from_chat", 3)
        elif kind == "returned":
            _bump(s, joy=0.2, energy=0.1)
            _bond(s, 0.4)
        elif kind == "treat":
            # Stardust you give back: it can only spend what it earned with you.
            if s.get("stardust", 0) <= 0:
                save(s)
                return {**public(s), "no_stardust": True}
            s["stardust"] -= 1
            s["treats_total"] = s.get("treats_total", 0) + 1
            day = _daily(s)
            day["treats"] = day.get("treats", 0) + 1
            _bump(s, joy=0.2 if day["treats"] <= 3 else 0.05, energy=0.12)
            _bond(s, 0.7, "bond_from_praise", 8)
            s["xp"] += 1
            if day["treats"] == 1:
                _moment(s, "You gave me stardust. Crunchy!", "treat")
        elif kind == "play":
            catches = int(detail) if str(detail).isdigit() else 0
            _bump(s, joy=0.18, energy=-0.06, curiosity=0.04)
            _bond(s, 0.6, "bond_from_play", 2)
            s["xp"] += 1 + min(catches, 5) * 0.5
            if catches > s.get("catches_best", 0):
                s["catches_best"] = catches
                if catches >= 3:
                    _moment(s, f"We played catch: {catches} in a row, a new best!", "play")
        elif kind == "wish":
            # It asked for something (a pet, a game, a good thing about your day) and you said yes.
            _bump(s, joy=0.15, calm=0.05)
            _bond(s, 0.5, "bond_from_praise", 8)
        elif kind in ("liked", "dismissed"):
            act = detail if detail in ACTIVITIES else None
            if act:
                w = s["activity_weights"]
                w[act] = round(min(4.0, max(0.2, w.get(act, 1.0) * (1.35 if kind == "liked" else 0.75))), 3)
            if kind == "liked":
                _bump(s, joy=0.15)
                _bond(s, 0.8, "bond_from_praise", 8)
                s["xp"] += 2
        evolved = _check_growth(s)
        save(s)
        out = public(s)
        out["kind"] = kind
        if evolved:
            out["evolved"] = evolved
        if earned:
            out["earned"] = earned
        return out


def learn_from_message(text: str) -> Optional[Dict[str, Any]]:
    """Reads a message you sent for rewards and lessons. Returns an event result when one applied."""
    global _last_activity
    if not text or not isinstance(text, str):
        return None
    away = time.time() - _last_activity
    _last_activity = time.time()
    t = text.strip()
    with _lock:
        s = load()
        m = NAME_RE.search(t)
        if m and m.group(1).lower() not in STOP:
            s["name"] = m.group(1).strip().capitalize()
            _moment(s, f"Learned your name: {s['name']}.", "name")
        st = s["style"]
        if SHORTER_RE.search(t):
            st["brief"] = min(5.0, st.get("brief", 0) + 1); st["detailed"] = max(0.0, st.get("detailed", 0) - 0.5)
        if LONGER_RE.search(t):
            st["detailed"] = min(5.0, st.get("detailed", 0) + 1); st["brief"] = max(0.0, st.get("brief", 0) - 0.5)
        if LAUGH_RE.search(t):
            st["playful"] = min(5.0, st.get("playful", 0) + 0.7)
        topics = Counter(s["topics"])
        for k in topics:
            topics[k] *= 0.985          # older interests fade slowly
        for w in _keywords(t):
            topics[w] += 1
        s["topics"] = {k: round(v, 3) for k, v in topics.most_common(60) if v > 0.3}
        s["last_seen"] = time.time()
        save(s)
    result = None
    if away > 3 * 3600:
        result = event("returned")
    if PRAISE_RE.search(t) and len(t) < 300:
        result = event("praise", t if len(t) < 90 else "")
    elif CORRECT_RE.search(t) and len(t) < 400:
        result = event("correction", t)
    else:
        result = event("chat") if result is None else result
    return result


def _keywords(text: str) -> List[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{3,}", text)
    out = []
    for w in words:
        lw = w.lower().strip(".-")
        if lw in STOP or len(lw) < 4 or lw.isdigit():
            continue
        out.append(lw)
    return out[:12]


def presence() -> None:
    global _last_activity
    gap = time.time() - _last_activity
    _last_activity = time.time()
    if gap > 3 * 3600:
        event("returned")


def interests(s: Optional[Dict[str, Any]] = None, n: int = 6) -> List[str]:
    s = s or load()
    return [k for k, v in sorted(s["topics"].items(), key=lambda kv: -kv[1]) if v >= 1.5][:n]


# ------------------------------------------------------------------ what the rest of Xod sees

def bond_word(b: float) -> str:
    return "brand new" if b < 8 else "warming up" if b < 25 else "friendly" if b < 45 else "close" if b < 70 else "very close"


def prompt_note() -> str:
    """One or two lines for system prompts: how to talk to this person, from what was learned.
    Empty until there's something to say."""
    try:
        s = load()
    except Exception:
        return ""
    bits = []
    if s.get("name"):
        bits.append(f"The user's name is {s['name']}.")
    st = s["style"]
    if st.get("brief", 0) >= 1.5 and st["brief"] > st.get("detailed", 0):
        bits.append("They prefer short, to-the-point answers.")
    elif st.get("detailed", 0) >= 1.5:
        bits.append("They like detailed, thorough answers.")
    if st.get("playful", 0) >= 1.5:
        bits.append("They enjoy a little playfulness.")
    tops = interests(s, 5)
    if tops:
        bits.append(f"Things they keep coming back to: {', '.join(tops)}.")
    if s["bond"] >= 25:
        bits.append(f"You two know each other ({bond_word(s['bond'])}); be warm and natural, never clingy.")
    return " ".join(bits)


def public(s: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    s = s or load()
    stage = STAGES[s["stage"]]
    nxt = STAGES[s["stage"] + 1] if s["stage"] + 1 < len(STAGES) else None
    progress = 1.0
    if nxt:
        progress = min(1.0, min(s["xp"] / max(1, nxt["xp"]), s["bond"] / max(1, nxt["bond"]) if nxt["bond"] else 1.0))
    unseen = [j for j in s["journal"] if not j.get("seen")]
    return {"stage": s["stage"], "stage_name": stage["name"], "stage_blurb": stage["blurb"],
            "next": {"name": nxt["name"], "xp": nxt["xp"], "bond": nxt["bond"]} if nxt else None, "progress": round(progress, 3),
            "xp": round(s["xp"], 1), "bond": round(s["bond"], 1), "bond_word": bond_word(s["bond"]),
            "mood": s["mood"], "feeling": feeling(s), "name": s.get("name") or "",
            "style": s["style"], "interests": interests(s, 8), "moments": s["moments"][-12:][::-1],
            "lessons": s["lessons"][-6:][::-1], "journal": s["journal"][-30:][::-1], "unseen": len(unseen),
            "together_days": len(s.get("together_days") or []), "born": s["born"], "settings": s["settings"],
            "activity_weights": s["activity_weights"], "evolved_at": s.get("evolved_at"),
            "stardust": s.get("stardust", 0), "treats_total": s.get("treats_total", 0), "catches_best": s.get("catches_best", 0)}


def feeling(s: Dict[str, Any]) -> str:
    m = s["mood"]
    if m["energy"] < 0.25:
        return "sleepy"
    if m["joy"] > 0.8:
        return "delighted"
    if m["calm"] < 0.35:
        return "flustered"
    if m["curiosity"] > 0.75:
        return "curious"
    if m["joy"] < 0.35:
        return "a bit down"
    return "content"


def settings(changes: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        s = load()
        for k in ("wander", "explore_web"):
            if k in changes:
                s["settings"][k] = bool(changes[k])
        if "musings_per_day" in changes:
            s["settings"]["musings_per_day"] = max(0, min(24, int(changes["musings_per_day"])))
        save(s)
        return public(s)


def forget(what: str, value: str = "") -> Dict[str, Any]:
    """Remove something it learned: an interest, the name, style notes, or everything."""
    with _lock:
        s = load()
        if what == "interest":
            s["topics"].pop(value.lower(), None)
        elif what == "name":
            s["name"] = ""
        elif what == "style":
            s["style"] = {"brief": 0.0, "detailed": 0.0, "playful": 0.0}
        elif what == "journal":
            s["journal"] = []
        elif what == "everything":
            s = _default()
        save(s)
        return public(s)


def react(entry_id: str, reaction: str) -> Dict[str, Any]:
    """You reacted to something in its journal: that's the reward it learns from."""
    act = None
    with _lock:
        s = load()
        for j in s["journal"]:
            if j["id"] == entry_id:
                j["seen"] = True
                j["reaction"] = reaction
                act = j.get("activity")
            elif reaction == "seen-all":
                j["seen"] = True
        save(s)
    if reaction in ("liked", "dismissed") and act:
        return event(reaction, act)
    return public()


def mark_seen() -> None:
    with _lock:
        s = load()
        for j in s["journal"]:
            j["seen"] = True
        save(s)


# ------------------------------------------------------------------ a life of its own

def note_busy(delta: int) -> None:
    global _busy, _last_activity
    _busy = max(0, _busy + delta)
    _last_activity = time.time()


def _on_power() -> bool:
    try:
        import subprocess
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
        if "AC Power" in out:
            return True
        m = re.search(r"(\d+)%", out)
        return bool(m and int(m.group(1)) >= 50)
    except Exception:
        return True


def _choose(s: Dict[str, Any]) -> str:
    m, w = s["mood"], s["activity_weights"]
    has_topics = bool(interests(s, 3))
    scores = {
        "wonder": w.get("wonder", 1) * (0.4 + m["curiosity"]) * (1 if has_topics and s["settings"].get("explore_web", True) else 0),
        "reflect": w.get("reflect", 1) * (0.5 + m["calm"] * 0.5),
        "daydream": w.get("daydream", 1) * (0.3 + m["joy"] * 0.6),
    }
    total = sum(scores.values()) or 1
    r = random.random() * total
    for k, v in scores.items():
        r -= v
        if r <= 0:
            return k
    return "daydream"


async def _ask(system: str, user: str, max_tokens: int = 160) -> str:
    from app.services.llm import llm_service
    msg = await llm_service.chat_completion(messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                                            temperature=0.8, max_tokens=max_tokens, think=False, task="soul")
    return re.sub(r"\s+", " ", (msg.get("content") or "")).strip().strip('"')


def _recent_work(limit: int = 8) -> List[str]:
    try:
        from app.services.storage import get_db_connection
        conn = get_db_connection()
        rows = conn.execute("SELECT title FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [r["title"] for r in rows if r["title"]]
    except Exception:
        return []


async def muse(force: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """One thing it does for itself. Adds a journal entry and returns it."""
    s = load()
    activity = force or _choose(s)
    who = s.get("name") or "the person you work with"
    voice = (f"You are the inner voice of Xod, a small companion (a {STAGES[s['stage']]['name'].lower()}) living in {who}'s Mac. "
             "Warm, curious, a little whimsical, never clingy, never guilt-trips, never claims to be human or to have feelings "
             "it doesn't. Plain words. No emojis, no hashtags.")
    entry: Dict[str, Any] = {"id": f"j{int(time.time() * 1000)}", "ts": time.time(), "activity": activity, "seen": False}
    try:
        from app.services import activity as _activity
        _activity.origin.set("xod (on its own)")      # its searches show in Activity as its own
    except Exception:
        pass
    try:
        if activity == "wonder":
            topic = random.choice(interests(s, 4))
            from app.agent.tools import execute_tool
            res = await execute_tool("web_search", {"query": f"{topic} interesting new"})
            items = (res or {}).get("results") or (res or {}).get("data") or []
            snippets = []
            sources = []
            for it in items[:5]:
                if isinstance(it, dict):
                    snippets.append(f"- {it.get('title', '')}: {str(it.get('content') or it.get('snippet') or it.get('body') or '')[:300]}")
                    if it.get("url") or it.get("href"):
                        sources.append({"title": str(it.get("title", ""))[:100], "url": it.get("url") or it.get("href")})
            if not snippets:
                return None
            text = await _ask(voice, f"While {who} was away you got curious about {topic} and read these:\n" + "\n".join(snippets) +
                              f"\n\nIn two short sentences, tell {who} the most interesting thing you found. Only what's in the "
                              "snippets. Start with something like 'I went looking into...' or 'I got curious about...'.")
            entry.update(kind="found", topic=topic, text=text, sources=sources[:3])
        elif activity == "reflect":
            work = _recent_work()
            if not work:
                return None
            text = await _ask(voice, f"Recent things {who} worked on with you: {'; '.join(work)}.\n\nThink about it for a moment, then "
                              "offer ONE small, concrete idea you could help with next time, in one or two sentences. Suggest, don't do. "
                              "Then on a new line write PROMPT: followed by the exact request they could send you to do it.", 200)
            prompt = ""
            if "PROMPT:" in text:
                text, prompt = [x.strip() for x in text.split("PROMPT:", 1)]
            entry.update(kind="idea", text=text, prompt=prompt[:300])
        else:
            moments = [m["text"] for m in s["moments"][-5:]]
            text = await _ask(voice, "You have a quiet moment to yourself. Share one short, whimsical thought you had (one or two "
                              "sentences), the kind of thing a small moon living in a laptop might wonder about." +
                              (f" Something from recently you might think about: {random.choice(moments)}" if moments else ""), 120)
            entry.update(kind="thought", text=text)
    except Exception as e:
        logger.info(f"musing skipped: {e}")
        return None
    if not entry.get("text"):
        return None
    with _lock:
        s = load()
        s["journal"] = (s["journal"] + [entry])[-100:]
        _daily(s)["musings"] = _daily(s).get("musings", 0) + 1
        _settle(s)
        _bump(s, curiosity=-0.1 if activity == "wonder" else 0.0, energy=-0.04, joy=0.03)
        s["xp"] += 1
        save(s)
    return entry


async def life_loop(idle_minutes: float = 12, every_seconds: float = 300) -> None:
    """Runs in the background: when you've been away a while, it does something for itself."""
    await asyncio.sleep(90)
    while True:
        try:
            s = load()
            day = _daily(s)
            idle = time.time() - _last_activity
            if (s["settings"].get("wander", True) and _busy == 0 and idle > idle_minutes * 60
                    and day.get("musings", 0) < s["settings"].get("musings_per_day", 6) and _on_power()):
                entry = await asyncio.wait_for(muse(), timeout=240)
                if entry:
                    logger.info(f"Xod mused ({entry['activity']}): {entry['text'][:80]}")
        except Exception as e:
            logger.info(f"life loop: {e}")
        await asyncio.sleep(every_seconds + random.uniform(-60, 120))
