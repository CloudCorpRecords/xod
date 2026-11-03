"""
What Xod knows about you: a short sheet of facts about the person it works with (name, work, where they are,
people in their life, what they're building, how they like answers), so it can answer "what's my name?",
fill in the obvious, and stop asking things it has been told.

Where facts come from, always recorded per fact:
- what you say in chat. After a turn where you talk about yourself, one small model call picks out lasting
  facts ("I'm job hunting for remote Python roles" -> "Looking for remote Python roles"). "Remember that ..."
  and "forget ..." work directly.
- your Mac, only when you ask ("Get to know me" in Settings), from the sources you tick: your account name,
  your Contacts "Me" card, git's identity, time zone and region, the apps you have (what you do), your code
  projects, and Xod's job profile. You see every fact before it's kept.

Everything lives in about_you.json in Xod's data folder, on this Mac. Every fact can be edited or forgotten,
and learning from chats can be switched off. Passwords, keys and card numbers are never kept.

Shared with your other computers (memory_sync.py, off by default): every add, edit and forget here goes in a small
change log (about_you_changes.json) that your other Xods pull from; merge() applies theirs. Forgets leave a tombstone
for 90 days, so a computer that was offline can't bring a forgotten fact back. Facts that came from another computer
say so in their source ("you said, on laptop"), and they're checked again (secrets, length, topic) before they're kept.

How it's used: core_note() is a short block (the key facts first) in every system prompt, in a stable order so
the model server can keep it cached; relevant(query) adds the other facts that match a question.
"""
import asyncio
import contextvars
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.user_memory")

PATH = Path(DATA_DIR) / "about_you.json"
CHANGES_PATH = Path(DATA_DIR) / "about_you_changes.json"
_lock = threading.RLock()
MAX_CHANGES = 2000
TOMBSTONE_DAYS = 90
# Who said it, for facts learned in this turn: "phone" when the chat came from the paired phone (main.chat_endpoint).
VIA = contextvars.ContextVar("user_memory_via", default="")

TOPICS = ["identity", "work", "location", "people", "projects", "interests", "preferences", "routines", "other"]
# One fact per key: a new one replaces the old ("works at Acme" -> "works at Globex").
KEYS = ["name", "nickname", "pronouns", "birthday", "email", "phone", "city", "country", "timezone", "language",
        "job", "company", "school", "partner"]
KEY_ORDER = {k: i for i, k in enumerate(KEYS)}
CORE_CHARS = 900          # the always-on block stays short: it's read on every request

# Never keep these, whoever offers them.
SECRET_RE = re.compile(r"\b(password|passcode|pin\b|api[ _-]?key|secret|token|ssn|social security|credit card|card number|"
                       r"cvv|routing number|account number|seed phrase|private key)\b|\b(?:\d[ -]?){13,19}\b|\b(sk|fw|ghp|xox[bp])[-_][A-Za-z0-9]{12,}", re.I)
# Talk about oneself (worth one small model call to look for facts), as opposed to a request or a question.
ABOUT_ME_RE = re.compile(r"\b(i'?m|i am|i'?ve|i'?ll be|i'?d (rather|prefer)|"
                         r"i (just |recently |now |also |still |usually |always |never )?(was|have|had|live|lived|moved|work|worked|"
                         r"like|love|hate|prefer|use|study|studied|got|started|quit|left|joined|bought|own|drive|speak|grew up|"
                         r"was born|graduated|run|teach|play|build|make|go to)|my (name|wife|husband|partner|girlfriend|boyfriend|son|daughter|kid|kids|mom|dad|mother|"
                         r"father|brother|sister|dog|cat|job|work|company|boss|team|startup|birthday|email|phone|address|"
                         r"city|home|school|major|favorite|favourite)|call me|we'?re|our (company|startup|team|family))\b", re.I)
REMEMBER_RE = re.compile(r"^\s*(?:please\s+)?(?:remember|note|keep in mind|don'?t forget)\s+(?:that\s+)?(.{3,300})$", re.I)
FORGET_RE = re.compile(r"^\s*(?:please\s+)?forget\s+(?:that\s+|about\s+)?(.{2,200})$", re.I)
FORGET_ALL_RE = re.compile(r"^\s*(?:please\s+)?forget (?:everything|all) (?:you know )?(?:about me)?\s*\.?$", re.I)


def _default() -> Dict[str, Any]:
    # "past": facts that stopped being true (moved, changed jobs), kept with when they ended, like a timeline.
    return {"facts": [], "past": [], "settings": {"learn": True, "recall": True}, "gathered_at": {}}


def load() -> Dict[str, Any]:
    try:
        d = json.loads(PATH.read_text())
        base = _default()
        base.update(d)
        base["settings"] = {**_default()["settings"], **(d.get("settings") or {})}
        return base
    except (OSError, ValueError):
        return _default()


def save(d: Dict[str, Any]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.replace(tmp, PATH)
    try:
        os.chmod(PATH, 0o600)
    except OSError:
        pass


def _norm(t: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) > 2 and w not in ("the", "and", "user", "their", "they")}


def _same(a: str, b: str) -> bool:
    x, y = _norm(a), _norm(b)
    return bool(x and y) and len(x & y) / max(1, len(x | y)) >= 0.7


def _clean(text: str) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip().strip(".")
    return t[:1].upper() + t[1:240] if t else ""


# ------------------------------------------------------------------------------------- the change log (for sharing)

def _load_changes() -> Dict[str, Any]:
    try:
        d = json.loads(CHANGES_PATH.read_text())
        return {"seq": int(d.get("seq") or 0), "changes": list(d.get("changes") or [])}
    except (OSError, ValueError):
        return {"seq": 0, "changes": []}


def _log(op: str, **data: Any) -> None:
    """One local change, for your other computers to pull (memory_sync). Called under _lock."""
    c = _load_changes()
    c["seq"] += 1
    c["changes"].append({"seq": c["seq"], "op": op, "at": time.time(), **data})
    c["changes"] = c["changes"][-MAX_CHANGES:]
    CHANGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHANGES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(c))
    os.replace(tmp, CHANGES_PATH)
    try:
        os.chmod(CHANGES_PATH, 0o600)
    except OSError:
        pass
    try:
        from app.services import memory_sync
        memory_sync.nudge(poke=True)
    except Exception:
        pass


def changes_since(seq: int) -> Dict[str, Any]:
    """{seq, changes, complete}: this computer's own changes after seq. On a first pull, or when the log was trimmed
    past seq, it's a full copy instead (complete False): every fact first kept here as a put, plus the forgets."""
    with _lock:
        c = _load_changes()
        out = [x for x in c["changes"] if x["seq"] > seq]
        # A first pull (seq 0) is always a full copy: facts kept before sharing existed were never logged.
        complete = seq > 0 and (not c["changes"] or seq >= c["changes"][0]["seq"] - 1)
        if not complete:
            d = load()
            out = [{"seq": c["seq"], "op": "put", "fact": _shareable(f)} for f in d["facts"] if not f.get("received_from")]
            out += [{"seq": c["seq"], "op": "forget", "id": i, "at": t} for i, t in (d.get("tombstones") or {}).items()]
            if d.get("cleared_at"):
                out.append({"seq": c["seq"], "op": "clear", "at": d["cleared_at"]})
        return {"seq": c["seq"], "changes": out, "complete": complete}


def _shareable(f: Dict[str, Any]) -> Dict[str, Any]:
    return {k: f.get(k) for k in ("id", "text", "topic", "key", "added", "updated", "pinned", "via", "origin")}


def _tombstone(d: Dict[str, Any], fact_id: str, at: float) -> None:
    t = d.setdefault("tombstones", {})
    t[fact_id] = max(at, t.get(fact_id) or 0)
    cutoff = time.time() - TOMBSTONE_DAYS * 86400
    d["tombstones"] = {k: v for k, v in t.items() if v >= cutoff}


def _origin() -> str:
    try:
        import socket
        return socket.gethostname().split(".")[0][:60]
    except Exception:
        return ""


def merge(changes: List[Dict[str, Any]], from_host: str) -> Dict[str, Any]:
    """Applies another computer's changes here. Nothing received is logged again (no echoes): each computer
    pulls from each of the others. Returns {"kept": [...], "forgot": [...], "skipped": n}."""
    out: Dict[str, Any] = {"kept": [], "forgot": [], "skipped": 0}
    host = (from_host or "another computer")[:60]
    with _lock:
        d = load()
        for ch in changes or []:
            op = ch.get("op")
            if op == "clear":
                at = float(ch.get("at") or 0)
                if at > (d.get("cleared_at") or 0):
                    d["cleared_at"] = at
                    gone = [f for f in d["facts"] if (f.get("updated") or 0) <= at]
                    d["facts"] = [f for f in d["facts"] if (f.get("updated") or 0) > at]
                    out["forgot"] += [f["text"] for f in gone]
                continue
            if op == "forget":
                fid, at = str(ch.get("id") or ""), float(ch.get("at") or 0)
                if not fid:
                    continue
                _tombstone(d, fid, at)
                hit = next((f for f in d["facts"] if f["id"] == fid), None)
                if hit and (hit.get("updated") or 0) <= at:
                    d["facts"].remove(hit)
                    out["forgot"].append(hit["text"])
                continue
            if op != "put" or not isinstance(ch.get("fact"), dict):
                out["skipped"] += 1
                continue
            f = ch["fact"]
            text = _clean(f.get("text"))
            fid, upd = str(f.get("id") or ""), float(f.get("updated") or 0)
            if (not re.fullmatch(r"f[0-9a-f]{6,32}", fid) or not text or len(text) < 3 or SECRET_RE.search(text)
                    or upd > time.time() + 300):
                out["skipped"] += 1
                continue
            if (d.get("tombstones") or {}).get(fid, 0) >= upd or (d.get("cleared_at") or 0) >= upd:
                continue    # forgotten here after that version was written
            topic = f.get("topic") if f.get("topic") in TOPICS else "other"
            key = f.get("key") if f.get("key") in KEYS else None
            via = f.get("via") if f.get("via") in ("chat", "phone", "mac", "you edited") else "chat"
            origin = str(f.get("origin") or host)[:60]
            source = f"you said, on {origin}" if via == "chat" else ("from your phone, via " + origin if via == "phone"
                                                                    else f"{via}, on {origin}")
            mine = next((x for x in d["facts"] if x["id"] == fid), None) \
                or next((x for x in d["facts"] if key and x.get("key") == key), None) \
                or next((x for x in d["facts"] if _same(x["text"], text)), None)
            if mine:
                if mine["id"] != fid and not (key and mine.get("key") == key and not _same(mine["text"], text)):
                    # The same fact said on two computers: both settle on the smaller id, whichever copy is newer,
                    # so a forget of it reaches both.
                    if fid < mine["id"]:
                        old_id = mine["id"]
                        mine["id"] = fid
                        _tombstone(d, old_id, time.time())
                    fid = mine["id"]
                newer = upd > (mine.get("updated") or 0) or (upd == (mine.get("updated") or 0) and origin > str(mine.get("origin") or ""))
                if not newer:
                    continue
                if key and mine.get("key") == key and not _same(mine["text"], text):
                    d.setdefault("past", []).append({"text": mine["text"], "key": key, "topic": mine["topic"],
                                                     "from": mine.get("added"), "until": upd, "source": mine.get("source")})
                    d["past"] = d["past"][-100:]
                mine.update(id=fid, text=text, topic=topic, key=key, updated=upd, source=source, via=via, origin=origin,
                            received_from=host, pinned=bool(mine.get("pinned") or f.get("pinned")))
                out["kept"].append(text)
            else:
                d["facts"].append({"id": fid, "text": text, "topic": topic, "key": key, "source": source,
                                   "added": float(f.get("added") or upd), "updated": upd, "used": 0,
                                   "pinned": bool(f.get("pinned")), "via": via, "origin": origin, "received_from": host})
                out["kept"].append(text)
        save(d)
    return out


def remove_received(from_host: str) -> int:
    """'Remove what came from Laptop': drops those facts here only (not a forget everywhere)."""
    with _lock:
        d = load()
        n = len(d["facts"])
        d["facts"] = [f for f in d["facts"] if f.get("received_from") != from_host]
        save(d)
        return n - len(d["facts"])


def add(text: str, topic: str = "other", key: Optional[str] = None, source: str = "you said",
        pinned: bool = False) -> Optional[Dict[str, Any]]:
    """Keeps a fact (or refreshes the same one). Returns it, or None if it was empty or looked like a secret."""
    text = _clean(text)
    if not text or len(text) < 3 or SECRET_RE.search(text):
        return None
    topic = topic if topic in TOPICS else "other"
    key = key if key in KEYS else None
    if key == "name":
        m = re.search(r"(?i:name is|named|called|call me|i'?m|goes by)\s+([A-Z][\w-]+(?:\s+[A-Z][\w-]+){0,3})", text) or \
            re.search(r"([A-Z][\w-]+(?:\s+[A-Z][\w-]+){0,3})\s*$", text)
        if m:
            text = f"Name is {m.group(1)}"
    now = time.time()
    with _lock:
        d = load()
        facts = d["facts"]
        hit = next((f for f in facts if key and f.get("key") == key), None) or next((f for f in facts if _same(f["text"], text)), None)
        fresh = True
        if hit:
            changed = hit["text"] != text
            fresh = changed
            if changed and key and hit.get("key") == key and not _same(hit["text"], text):
                # Not a correction of wording but a change in their life: keep the old one on the timeline.
                d.setdefault("past", []).append({"text": hit["text"], "key": key, "topic": hit["topic"],
                                                 "from": hit.get("added"), "until": now, "source": hit.get("source")})
                d["past"] = d["past"][-100:]
            hit.update(text=text, topic=topic if key or changed else hit["topic"], updated=now, source=source if changed else hit["source"])
            if key:
                hit["key"] = key
            hit["pinned"] = hit.get("pinned") or pinned
            if changed:
                hit.update(via=_via(source), origin=_origin())
                hit.pop("received_from", None)
            fact = hit
        else:
            fact = {"id": "f" + uuid.uuid4().hex[:10], "text": text, "topic": topic, "key": key, "source": source,
                    "added": now, "updated": now, "used": 0, "pinned": pinned, "via": _via(source), "origin": _origin()}
            facts.append(fact)
        save(d)
        if fresh:
            _log("put", fact=_shareable(fact))
    if key == "name":
        _sync_name(text)
    return {**fact, "_new": fresh}


def _via(source: str) -> str:
    if "phone" in (source or ""):
        return "phone"
    if source == "you edited":
        return "you edited"
    return "chat" if (source or "").startswith("you") else "mac"


def _sync_name(text: str) -> None:
    """The companion calls you by the same name."""
    m = re.search(r"(?i:name is|named|called)\s+([A-Z][\w-]+)", text) or re.match(r"\s*([A-Z][\w-]+)", text)
    if not m:
        return
    try:
        from app.services import soul
        with soul._lock:
            s = soul.load()
            if s.get("name") != m.group(1):
                s["name"] = m.group(1)
                soul.save(s)
    except Exception:
        pass


def edit(fact_id: str, text: str) -> Optional[Dict[str, Any]]:
    text = _clean(text)
    if not text or SECRET_RE.search(text):
        return None
    with _lock:
        d = load()
        f = next((f for f in d["facts"] if f["id"] == fact_id), None)
        if not f:
            return None
        f.update(text=text, updated=time.time(), source="you edited", via="you edited", origin=_origin())
        f.pop("received_from", None)
        save(d)
        _log("put", fact=_shareable(f))
    if f.get("key") == "name":
        _sync_name(text)
    return f


def forget(fact_id: str) -> bool:
    with _lock:
        d = load()
        n = len(d["facts"])
        d["facts"] = [f for f in d["facts"] if f["id"] != fact_id]
        gone = len(d["facts"]) < n
        if gone:
            now = time.time()
            _tombstone(d, fact_id, now)
            _log("forget", id=fact_id, at=now)
        save(d)
        return gone


def forget_matching(what: str) -> List[str]:
    """'forget my phone number', 'forget that I live in Austin': removes the facts that match. Returns their text."""
    words = _norm(what)
    key_words = {"phone": "phone", "number": "phone", "email": "email", "birthday": "birthday", "name": "name",
                 "address": "city", "city": "city", "job": "job", "work": "job", "company": "company"}
    keys = {key_words[w] for w in words if w in key_words}
    gone = []
    with _lock:
        d = load()
        keep, now = [], time.time()
        for f in d["facts"]:
            fw = _norm(f["text"])
            if (f.get("key") in keys) or (words and len(words & fw) / max(1, len(words)) >= 0.6):
                gone.append(f["text"])
                _tombstone(d, f["id"], now)
                _log("forget", id=f["id"], at=now)
            else:
                keep.append(f)
        d["facts"] = keep
        save(d)
    return gone


def forget_all() -> int:
    with _lock:
        d = load()
        n = len(d["facts"])
        d["facts"] = []
        d["cleared_at"] = time.time()
        save(d)
        _log("clear", at=d["cleared_at"])
        return n


def facts() -> List[Dict[str, Any]]:
    return load()["facts"]


def settings(changes: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        d = load()
        for k in ("learn", "recall"):
            if k in changes:
                d["settings"][k] = bool(changes[k])
        save(d)
        return d["settings"]


def _core(fs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keyed = sorted((f for f in fs if f.get("key")), key=lambda f: KEY_ORDER.get(f["key"], 99))
    rest = sorted((f for f in fs if not f.get("key")),
                  key=lambda f: (not f.get("pinned"), TOPICS.index(f["topic"]) if f["topic"] in TOPICS else 99, f["added"]))
    out, used = [], 0
    for f in keyed + rest:
        if used + len(f["text"]) > CORE_CHARS:
            continue
        out.append(f)
        used += len(f["text"]) + 4
    return out


def core_note() -> str:
    """The always-on block for system prompts. Same order every time (so it stays cached) until a fact changes."""
    fs = facts()
    if not fs:
        return ""
    lines = [f"- {f['text']}" for f in _core(fs)]
    return ("What you know about the user (they told you, or it's from their Mac with their OK). Use it naturally "
            "when it helps, e.g. their name or what they're working on; don't recite it or say where you learned it "
            "unless asked:\n" + "\n".join(lines))


def relevant(query: str, k: int = 4) -> List[Dict[str, Any]]:
    """Facts outside the core block that match this question."""
    fs = facts()
    core_ids = {f["id"] for f in _core(fs)}
    q = _norm(query)
    if not q:
        return []
    scored = []
    for f in fs:
        if f["id"] in core_ids:
            continue
        s = len(q & _norm(f["text"]))
        if s:
            scored.append((s, f))
    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored[:k]]


PAST_Q_RE = re.compile(r"\b(before|used to|previous(ly)?|former|old|earlier|first|originally|back when|anymore|still)\b", re.I)


def relevant_note(query: str) -> str:
    rel = relevant(query)
    out = ("\n\n(Also known about the user: " + "; ".join(f["text"] for f in rel) + ".)") if rel else ""
    past = load().get("past") or []
    if past and PAST_Q_RE.search(query or ""):
        q = _norm(query)
        hits = [p for p in past if (p.get("key") and p["key"] in q) or q & _norm(p["text"])] or past[-4:]
        out += "\n(No longer true, from their timeline: " + "; ".join(
            f"{p['text']} (until {time.strftime('%b %Y', time.localtime(p['until']))})" for p in hits[-5:]) + ".)"
    return out


# ----------------------------------------------------------------------------------------------- learning

EXTRACT_PROMPT = """You keep a short, accurate memory of facts about the user so Xod can help them better later.
From the user's latest message, pick out lasting facts ABOUT THE USER or their life: who they are, their work and
projects, where they live, people in their life (by relationship), what they like or prefer, goals and routines.
Only what they clearly said in THIS message, and only what isn't already known (listed below). Not: requests or questions, facts about the world, passing states ("I'm tired"),
anything about other people beyond their relationship and name, passwords, account or card numbers, health details.
Write each fact short, with no subject (not their name, not "the user"): "Name is Ana", "Works as a nurse at
St. Mary's", "Lives in Denver", "Has a dog named Biscuit", "Prefers short answers". If a fact replaces one they
already have (they moved, changed jobs), give it the same key so the old one is replaced.

Reply with ONLY JSON: {"facts": [{"fact": "...", "topic": "identity|work|location|people|projects|interests|preferences|routines|other",
"key": "name|nickname|pronouns|birthday|email|phone|city|country|timezone|language|job|company|school|partner|null"}],
"forget": ["<what they asked you to forget, if anything>"]}
No lasting facts: {"facts": [], "forget": []}."""


def worth_a_look(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 8 or len(t) > 2000 or not load()["settings"].get("learn", True):
        return False
    if REMEMBER_RE.match(t) or FORGET_RE.match(t):
        return True
    if not ABOUT_ME_RE.search(t):
        return False
    # "What's my name?" is a question, not something to learn from.
    return not (t.endswith("?") and re.match(r"^\s*(what|who|where|when|how|do|does|did|is|are|can|could|which)\b", t, re.I))


# Results of recent learning, by chat turn: the page asks for them after the answer (learning can finish later).
_turns: Dict[str, Dict[str, Any]] = {}


def start_learning(text: str, via: str = "") -> str:
    """Learns from this message in the background. Returns a turn id the page can ask about (turn_result).
    via: "phone" when the message came from the paired phone (its facts say so)."""
    tid = uuid.uuid4().hex[:12]
    _turns[tid] = {"done": False}
    if len(_turns) > 50:
        for k in list(_turns)[:-50]:
            _turns.pop(k, None)

    async def run():
        VIA.set(via)
        try:
            got = await learn_from_message(text)
        except Exception as e:
            logger.debug(f"memory: {e}")
            got = {"learned": [], "forgot": []}
        _turns[tid] = {"done": True, **got}
    _turns[tid]["task"] = asyncio.ensure_future(run())
    return tid


def turn_result(tid: str) -> Dict[str, Any]:
    r = _turns.get(tid)
    if not r:
        return {"done": True, "learned": [], "forgot": []}
    return {k: v for k, v in r.items() if k != "task"}


async def learn_from_message(text: str, session_id: str = "") -> Dict[str, Any]:
    """Looks at one message the user sent for facts about them. Returns {"learned": [...], "forgot": [...]}."""
    out: Dict[str, Any] = {"learned": [], "forgot": []}
    t = (text or "").strip()
    if FORGET_ALL_RE.match(t):
        n = forget_all()
        out["forgot"] = [f"everything ({n} facts)"]
        return out
    if not worth_a_look(t):
        return out
    from app.services.llm import llm_service, parse_llm_json
    known = "\n".join(f"- [{f.get('key') or f['topic']}] {f['text']}" for f in facts()[-40:]) or "(nothing yet)"
    try:
        reply = await llm_service.chat_completion(
            messages=[{"role": "system", "content": EXTRACT_PROMPT},
                      {"role": "user", "content": f"Already known:\n{known}\n\nLatest message from the user:\n{t[:1500]}"}],
            temperature=0.0, max_tokens=400, think=False)
        data = parse_llm_json(reply.get("content", "")) or {}
    except Exception as e:
        logger.debug(f"memory: couldn't read the message: {e}")
        data = {}
    m = REMEMBER_RE.match(t)
    if m and not data.get("facts"):
        data["facts"] = [{"fact": m.group(1), "topic": "other"}]   # "remember that ..." always keeps something
    for what in data.get("forget") or []:
        if isinstance(what, str) and what.strip():
            out["forgot"] += forget_matching(what)
    fm = FORGET_RE.match(t)
    if fm and not out["forgot"]:
        out["forgot"] += forget_matching(fm.group(1))
    for item in data.get("facts") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key") if item.get("key") not in (None, "null", "") else None
        f = add(str(item.get("fact") or ""), str(item.get("topic") or "other"), key,
                source="you said, from your phone" if VIA.get() == "phone" else "you said")
        if f and f.get("_new"):          # only what's new or changed, not what it already knew
            out["learned"].append({"id": f["id"], "text": f["text"]})
    return out


# ------------------------------------------------------------------------------------- from this Mac

SOURCES = [
    {"id": "account", "label": "Your Mac account name", "reads": "the full name on your macOS user account"},
    {"id": "contacts", "label": "Your Contacts card", "reads": "your own \"Me\" card in Contacts: name, birthday, email, phone, company, job title, city",
     "asks": "macOS will ask you to let Xod use Contacts"},
    {"id": "profile", "label": "Xod's job profile", "reads": "the name, email, location and title you gave Xod for job applications"},
    {"id": "git", "label": "Git identity", "reads": "the name and email in your git settings"},
    {"id": "locale", "label": "Time zone and language", "reads": "your Mac's time zone, region and language"},
    {"id": "apps", "label": "Your apps", "reads": "the names of the apps in Applications, to see what you work on and enjoy",
     "model": True},
    {"id": "projects", "label": "Your code projects", "reads": "names and README first lines of git projects in Documents, Desktop, Developer and similar folders",
     "model": True},
]
if sys.platform == "win32":     # no Contacts app on Windows; the rest read the Windows equivalents
    SOURCES = [s for s in SOURCES if s["id"] != "contacts"]
    for s in SOURCES:
        s.update({"account": {"label": "Your Windows account name", "reads": "the full name on your Windows user account"},
                  "locale": {"reads": "your PC's time zone, region and language"},
                  "apps": {"reads": "the names of the apps in your Start menu, to see what you work on and enjoy"}}.get(s["id"], {}))


def _run(cmd: List[str], timeout: float = 10.0) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:
        return ""


def _windows_full_name() -> str:
    """The display name on the Windows account (a Microsoft account's name, or a local account's full name)."""
    import ctypes
    size = ctypes.c_ulong(256)
    buf = ctypes.create_unicode_buffer(256)
    try:
        if ctypes.windll.secur32.GetUserNameExW(3, buf, ctypes.byref(size)):     # 3: NameDisplay
            return buf.value.strip()
    except Exception:
        pass
    return ""


def _from_account() -> List[Dict[str, Any]]:
    full = _windows_full_name() if sys.platform == "win32" else _run(["/usr/bin/id", "-F"])
    return [{"fact": f"Name is {full}", "topic": "identity", "key": "name"}] if full and " " in full or full.istitle() else []


CONTACTS_SCRIPT = '''
tell application "Contacts"
    set c to my card
    set out to "name=" & (name of c) & linefeed
    try
        set n to nickname of c
        if n is not missing value then set out to out & "nickname=" & n & linefeed
    end try
    try
        set b to birth date of c
        if b is not missing value then set out to out & "birthday=" & (month of b as string) & " " & (day of b as string) & linefeed
    end try
    try
        set o to organization of c
        if o is not missing value then set out to out & "org=" & o & linefeed
    end try
    try
        set t to job title of c
        if t is not missing value then set out to out & "title=" & t & linefeed
    end try
    repeat with e in emails of c
        set out to out & "email=" & (value of e) & linefeed
    end repeat
    repeat with p in phones of c
        set out to out & "phone=" & (value of p) & linefeed
    end repeat
    repeat with a in addresses of c
        try
            set out to out & "city=" & (city of a) & ", " & (country of a) & linefeed
        end try
    end repeat
    return out
end tell
'''


def _from_contacts() -> List[Dict[str, Any]]:
    try:
        p = subprocess.run(["/usr/bin/osascript", "-"], input=CONTACTS_SCRIPT, capture_output=True, text=True, timeout=40)
    except Exception:
        raise RuntimeError("Contacts didn't answer in time.")
    if p.returncode != 0:
        err = p.stderr
        if "-1743" in err or "Not authorized" in err:
            raise RuntimeError("macOS hasn't let Xod read Contacts. Allow it in System Settings → Privacy & Security → Automation.")
        if "my card" in err or "-1728" in err:
            raise RuntimeError("Contacts has no \"Me\" card. In Contacts, select your card and choose Card → Make This My Card.")
        raise RuntimeError("Couldn't read your Contacts card.")
    vals: Dict[str, List[str]] = {}
    for line in p.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip(",").strip()
            if v and v != "missing value" and not v.startswith("missing value"):
                vals.setdefault(k, []).append(v)
    out = []
    if vals.get("name"):
        out.append({"fact": f"Name is {vals['name'][0]}", "topic": "identity", "key": "name"})
    if vals.get("nickname"):
        out.append({"fact": f"Goes by {vals['nickname'][0]}", "topic": "identity", "key": "nickname"})
    if vals.get("birthday"):
        out.append({"fact": f"Birthday is {vals['birthday'][0]}", "topic": "identity", "key": "birthday"})
    if vals.get("title") or vals.get("org"):
        job = " at ".join(x for x in (vals.get("title", [""])[0], vals.get("org", [""])[0]) if x)
        out.append({"fact": f"Works as {job}" if vals.get("title") else f"Works at {job}", "topic": "work", "key": "job"})
    if vals.get("email"):
        out.append({"fact": f"Email is {vals['email'][0]}", "topic": "identity", "key": "email"})
    if vals.get("phone"):
        out.append({"fact": f"Phone is {vals['phone'][0]}", "topic": "identity", "key": "phone"})
    if vals.get("city"):
        out.append({"fact": f"Lives in {vals['city'][0].strip(', ')}", "topic": "location", "key": "city"})
    return out


def _from_profile() -> List[Dict[str, Any]]:
    try:
        from app.services.browser_sandbox import load_user_profile
        p = load_user_profile() or {}
    except Exception:
        return []
    out = []
    name = p.get("full_name") or p.get("name")
    if name:
        out.append({"fact": f"Name is {name}", "topic": "identity", "key": "name"})
    if p.get("email"):
        out.append({"fact": f"Email is {p['email']}", "topic": "identity", "key": "email"})
    loc = p.get("location") or p.get("city")
    if loc:
        out.append({"fact": f"Lives in {loc}", "topic": "location", "key": "city"})
    title = p.get("current_title") or p.get("title") or p.get("headline")
    if title:
        out.append({"fact": f"Works as {title}", "topic": "work", "key": "job"})
    return out


def _from_git() -> List[Dict[str, Any]]:
    name = _run(["git", "config", "--global", "user.name"])
    email = _run(["git", "config", "--global", "user.email"])
    out = []
    if name:
        out.append({"fact": f"Goes by \"{name}\" in git", "topic": "work"})
    if email and "noreply" not in email:
        out.append({"fact": f"Uses {email} for code commits", "topic": "work"})
    return out


def _from_locale() -> List[Dict[str, Any]]:
    if sys.platform == "win32":
        return _from_locale_windows()
    out = []
    try:
        tz = os.path.realpath("/etc/localtime").split("zoneinfo/")[-1]
        if "/" in tz:
            out.append({"fact": f"Time zone is {tz.replace('_', ' ')}", "topic": "location", "key": "timezone"})
    except Exception:
        pass
    loc = _run(["/usr/bin/defaults", "read", "-g", "AppleLocale"])
    langs = _run(["/usr/bin/defaults", "read", "-g", "AppleLanguages"])
    lang = (re.findall(r'"?([a-z]{2}(?:-[A-Za-z]+)?)"?', langs) or [""])[0]
    if lang:
        names = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "it": "Italian",
                 "ja": "Japanese", "zh": "Chinese", "ko": "Korean", "nl": "Dutch", "ru": "Russian", "ar": "Arabic", "hi": "Hindi"}
        out.append({"fact": f"Mac language is {names.get(lang[:2], lang)}", "topic": "preferences", "key": "language"})
    if loc and "_" in loc:
        out.append({"fact": f"Mac region is {loc.split('_')[1][:2]}", "topic": "location"})
    return out


LANGUAGES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "it": "Italian",
             "ja": "Japanese", "zh": "Chinese", "ko": "Korean", "nl": "Dutch", "ru": "Russian", "ar": "Arabic", "hi": "Hindi"}


def _from_locale_windows() -> List[Dict[str, Any]]:
    """Windows: the time zone's name, and the language and region from the user's locale (like en-US)."""
    import ctypes
    import time as _time
    out = []
    tz = (_time.tzname[0] or "").strip()
    if tz:
        out.append({"fact": f"Time zone is {tz}", "topic": "location", "key": "timezone"})
    try:
        buf = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
            lang, _, region = buf.value.partition("-")
            if lang:
                out.append({"fact": f"PC language is {LANGUAGES.get(lang.lower(), buf.value)}", "topic": "preferences", "key": "language"})
            if len(region) == 2:
                out.append({"fact": f"PC region is {region.upper()}", "topic": "location"})
    except Exception:
        pass
    return out


_WIN_BUILTIN = re.compile(r"(^(Settings|Calculator|Camera|Clock|Photos|Paint|Notepad|Snipping Tool|File Explorer|Microsoft Store|"
                          r"Windows .*|Microsoft Edge|Media Player|Feedback Hub|Get Help|Tips|Maps|Weather|Xbox.*|Phone Link|Sticky Notes|"
                          r"Control Panel|Task Manager|Run|Command Prompt|Registry Editor|Character Map|Xod|Sound Recorder|"
                          r"Microsoft Clipchamp|Family|Copilot|Dev Home|Quick Assist|Solitaire.*|Steps Recorder|Remote Desktop.*|"
                          r"Recovery Drive|Narrator|Magnifier|On-Screen Keyboard|Paint 3D|WordPad|Snip & Sketch|Mail|Calendar|People)$)|"
                          r"(Uninstall|Readme|Help|Documentation|Website|Release Notes|Manual|License|Changelog)", re.I)


def _apps() -> List[str]:
    names = set()
    if sys.platform == "win32":
        from app.agent.operator import _windows_apps
        return sorted({name for name, _ in _windows_apps().values() if not _WIN_BUILTIN.search(name)})[:200]
    for base in ("/Applications", os.path.expanduser("~/Applications")):
        try:
            for n in os.listdir(base):
                if n.endswith(".app"):
                    names.add(n[:-4])
        except OSError:
            pass
    junk = re.compile(r"^(Safari|Mail|Calendar|Contacts|Notes|Reminders|Photos|Music|TV|Podcasts|Books|Maps|Messages|FaceTime|"
                      r"App Store|System Settings|Preview|TextEdit|Calculator|Clock|Weather|Stocks|News|Home|Freeform|"
                      r"Font Book|Image Capture|Chess|Stickies|Shortcuts|Siri|Time Machine|Utilities|Xod)$")
    return sorted(n for n in names if not junk.match(n))[:200]


def _projects() -> List[str]:
    home = Path.home()
    roots = [home / d for d in ("Documents", "Desktop", "Developer", "Projects", "projects", "code", "Code", "src", "dev", "GitHub", "repos")]
    found = []
    skip = str(Path(DATA_DIR).resolve())
    for root in roots:
        if not root.is_dir():
            continue
        for depth_dirs in (root.iterdir(),):
            for d in depth_dirs:
                try:
                    if not d.is_dir() or d.name.startswith(".") or str(d.resolve()).startswith(skip):
                        continue
                    cands = [d] + [c for c in d.iterdir() if c.is_dir() and not c.name.startswith(".")][:30]
                except OSError:
                    continue
                for c in cands:
                    if (c / ".git").exists():
                        line = ""
                        for readme in ("README.md", "readme.md", "README", "README.txt"):
                            try:
                                text = (c / readme).read_text(errors="ignore")[:2000]
                            except OSError:
                                continue
                            for ln in text.splitlines():
                                ln = re.sub(r"[#*`>\[\]!]|\(http\S+\)", "", ln).strip()
                                if len(ln) > 20 and not ln.lower().startswith(("install", "license", "build status")):
                                    line = ln[:160]
                                    break
                            break
                        try:
                            mtime = max((c / ".git").stat().st_mtime, c.stat().st_mtime)
                        except OSError:
                            mtime = 0
                        found.append((mtime, f"{c.name}: {line}" if line else c.name))
    found.sort(reverse=True)
    seen, out = set(), []
    for _, s in found:
        if s.split(":")[0] not in seen:
            seen.add(s.split(":")[0])
            out.append(s)
    return out[:15]


SUMMARY_PROMPT = """You're getting to know the user from their Mac, with their permission. From the list below, write 2-5
short facts about the user that you're confident about, in third person ("Builds iOS apps (Xcode, TestFlight)",
"Makes music in Logic Pro", "Working on xod, an AI assistant for the Mac"). Skip anything you'd only be guessing.
Reply with ONLY JSON: {"facts": [{"fact": "...", "topic": "work|projects|interests|preferences|other"}]}"""


async def _summarize(kind: str, items: List[str]) -> List[Dict[str, Any]]:
    if not items:
        return []
    from app.services.llm import llm_service, parse_llm_json
    label = "Apps installed on their Mac" if kind == "apps" else "Their recent code projects (name: what the README says)"
    try:
        reply = await llm_service.chat_completion(
            messages=[{"role": "system", "content": SUMMARY_PROMPT},
                      {"role": "user", "content": f"{label}:\n" + "\n".join(f"- {i}" for i in items)}],
            temperature=0.1, max_tokens=400, think=False)
        data = parse_llm_json(reply.get("content", "")) or {}
    except Exception as e:
        raise RuntimeError(f"The model couldn't look at these: {e}")
    return [{"fact": str(f.get("fact") or ""), "topic": f.get("topic") or ("projects" if kind == "projects" else "interests")}
            for f in data.get("facts") or [] if isinstance(f, dict) and f.get("fact")][:5]


SOURCE_LABEL = {"account": "your Mac account", "contacts": "your Contacts card", "profile": "Xod's job profile",
                "git": "your git settings", "locale": "your Mac's settings", "apps": "the apps on your Mac",
                "projects": "your code projects"}
if sys.platform == "win32":
    SOURCE_LABEL.update(account="your Windows account", locale="your PC's settings", apps="the apps on your PC")


async def gather(sources: List[str]) -> Dict[str, Any]:
    """Reads the ticked sources and returns proposed facts (nothing is kept yet). {"proposals": [...], "errors": {...}}"""
    proposals, errors = [], {}
    simple = {"account": _from_account, "contacts": _from_contacts, "profile": _from_profile, "git": _from_git, "locale": _from_locale}
    for src in sources:
        try:
            if src in simple:
                got = await asyncio.to_thread(simple[src])
            elif src == "apps":
                got = await _summarize("apps", await asyncio.to_thread(_apps))
            elif src == "projects":
                got = await _summarize("projects", await asyncio.to_thread(_projects))
            else:
                continue
        except Exception as e:
            errors[src] = str(e)[:240]
            continue
        for g in got:
            text = _clean(g.get("fact", ""))
            if not text or SECRET_RE.search(text):
                continue
            known = next((f for f in facts() if (g.get("key") and f.get("key") == g.get("key")) or _same(f["text"], text)), None)
            proposals.append({"text": text, "topic": g.get("topic") or "other", "key": g.get("key"), "source": src,
                              "source_label": SOURCE_LABEL.get(src, src),
                              "known": bool(known and known["text"] == text), "replaces": known["text"] if known and known["text"] != text else ""})
    # One proposal per key (the first source wins: account < contacts < profile in SOURCES order).
    seen, uniq = set(), []
    for p in proposals:
        k = p["key"] or p["text"].lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    return {"proposals": uniq, "errors": errors}


def keep(proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    kept = []
    for p in proposals:
        f = add(p.get("text", ""), p.get("topic", "other"), p.get("key"), source=f"from {SOURCE_LABEL.get(p.get('source'), 'your Mac')}")
        if f:
            kept.append(f)
    with _lock:
        d = load()
        for p in proposals:
            d["gathered_at"][p.get("source", "")] = time.time()
        save(d)
    return kept
