"""
Understanding the question, and knowing afterwards whether the answer was any good.

Three things, all per chat turn:

  understand   A follow-up like "and the other one?" or "how much is it" means nothing on its own. Before Xod
               decides anything, a short call rewrites it as a standalone request ("How much does the Sony
               WH-1000XM6 cost?"), so the router, the memory lookup, the web search and the planner all work
               from what you meant. Only for messages that lean on the conversation; the rest skip it.
               The rewrites are saved as their own training task ("understand").

  labels       Every model call in a turn (router, planner, tool decisions, the rewrite) is tied to the turn
               (dataset_recorder.capture calls attach()). How the turn went then labels them, so the training
               data for Xod's small models is cleaner than "everything the teacher said":
                 -1  you corrected it ("no, that's wrong", "not what I asked"), asked the same thing again, or
                     its own check failed
                 +1  you thanked it, or its own check passed and you moved on without complaint
               Unlabelled stays unlabelled: no guessing.

  self-check   After an answer, when the Mac is idle, the local model (never a cloud one) reads the question as
               understood and the answer, and says PASS or FAIL. Skipped when no model runs on this Mac.

  lessons      A correction becomes a lesson ("when asked X, the answer Y was corrected: Z"). The next time a
               question looks like that one, the lesson goes into the prompt, so it doesn't make the same
               mistake twice.

Turns are stored in the dataset database (table turns), next to the examples they label.
"""
import asyncio
import contextvars
import json
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xod.turns")

ATTACH_TASKS = ("router", "planner", "react_step", "understand")
_current: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("xod_turn", default=None)
_judging = 0

FOLLOWUP_RE = re.compile(
    r"^\s*(and|also|what about|how about|same|so|but|then|why|how come|really|more|another|which one|ok(ay)?,? (and|so|what|how))\b|"
    r"\b(it|its|it'?s|that|this|those|these|them|they|their|he|she|him|her|there|one|ones|the (first|second|third|last|other|cheaper|"
    r"bigger|better|former|latter) (one|option)?)\b", re.I)
RETRY_RE = re.compile(r"\b(try again|again\??$|i (said|asked|meant)|not what i (asked|meant|said)|answer the question|that'?s not (it|what))\b", re.I)


# ------------------------------------------------------------------ storage

def _conn():
    from app.services import dataset_recorder
    c = dataset_recorder._conn()
    c.execute("""CREATE TABLE IF NOT EXISTS turns (
        id TEXT PRIMARY KEY, session_id TEXT, ts REAL, question TEXT, understood TEXT, answer TEXT, tools TEXT,
        examples TEXT DEFAULT '[]', self_check TEXT DEFAULT '', self_reason TEXT DEFAULT '', user_signal TEXT DEFAULT '',
        signal_text TEXT DEFAULT '', label INTEGER DEFAULT 0, lesson TEXT DEFAULT '')""")
    c.execute("CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id, ts)")
    return c


def _words(text: str) -> set:
    try:
        from app.services.soul import _keywords
        return set(_keywords(text or ""))
    except Exception:
        return set(re.findall(r"[a-z]{4,}", (text or "").lower()))


# ------------------------------------------------------------------ the turn

def begin(session_id: str, question: str) -> Optional[Dict[str, Any]]:
    """A new message arrived. Judges the previous turn in this chat by it, then starts this one."""
    if not isinstance(question, str) or not question.strip():
        return None
    try:
        _judge_previous(session_id, question)
    except Exception as e:
        logger.debug(f"turn feedback: {e}")
    turn = {"id": f"turn-{uuid.uuid4().hex[:12]}", "session_id": session_id or "", "ts": time.time(), "question": question[:2000],
            "understood": "", "examples": []}
    try:
        c = _conn()
        c.execute("INSERT INTO turns (id, session_id, ts, question) VALUES (?, ?, ?, ?)", (turn["id"], turn["session_id"], turn["ts"], turn["question"]))
        c.commit()
        c.close()
    except Exception as e:
        logger.debug(f"turn: {e}")
    _current.set(turn)
    return turn


def current() -> Optional[Dict[str, Any]]:
    return _current.get()


def attach(example_id: str, task: str) -> None:
    """Called by dataset_recorder.capture: this example belongs to the current turn."""
    turn = _current.get()
    if turn is not None and example_id and task in ATTACH_TASKS:
        turn["examples"].append(example_id)


def finish(turn: Optional[Dict[str, Any]], answer: str, tools: List[str]) -> None:
    if not turn:
        return
    try:
        c = _conn()
        c.execute("UPDATE turns SET answer = ?, tools = ?, examples = ?, understood = ? WHERE id = ?",
                  ((answer or "")[:4000], json.dumps(sorted(set(tools))[:12]), json.dumps(turn["examples"]), turn.get("understood") or "", turn["id"]))
        c.commit()
        c.close()
    except Exception as e:
        logger.debug(f"turn: {e}")
    if answer and len(answer.strip()) > 15:
        try:
            asyncio.get_running_loop().create_task(_self_check_later(turn["id"]))
        except RuntimeError:
            pass
        try:   # it said it wasn't sure: something for the companion to read up on later (services/curiosity.py)
            from app.services import curiosity
            if curiosity.HEDGE_RE.search(answer):
                curiosity.add_gap(turn.get("understood") or turn["question"], "hedge", guess=answer, priority=0.75)
        except Exception as e:
            logger.debug(f"gap: {e}")


def _label(examples: List[str], value: int, reason: str) -> int:
    from app.services import dataset_recorder
    n = 0
    if value < 0:
        for ex in examples:
            n += 1 if dataset_recorder.label(ex, -1, reason) else 0
        return n
    c = dataset_recorder._conn()
    try:
        for ex in examples:      # good only where nothing decided otherwise yet
            n += c.execute("UPDATE llm_examples SET label = 1, label_reason = ? WHERE id = ? AND label = 0", (reason[:200], ex)).rowcount
        c.commit()
    finally:
        c.close()
    return n


def classify(text: str, prev_question: str = "") -> str:
    from app.services.soul import CORRECT_RE, PRAISE_RE
    t = (text or "").strip()
    if (CORRECT_RE.search(t) or RETRY_RE.search(t)) and len(t) < 400:
        return "correction"
    if PRAISE_RE.search(t) and len(t) < 300:
        return "praise"
    a, b = _words(prev_question), _words(t)
    if len(a) >= 2 and len(b) >= 2 and len(a & b) / len(a | b) >= 0.6:
        return "asked_again"
    return "moved_on"


def _judge_previous(session_id: str, text: str) -> None:
    if not session_id:
        return
    c = _conn()
    try:
        prev = c.execute("SELECT * FROM turns WHERE session_id = ? AND user_signal = '' AND answer IS NOT NULL ORDER BY ts DESC LIMIT 1",
                         (session_id,)).fetchone()
        if not prev or time.time() - prev["ts"] > 6 * 3600:
            return
        signal = classify(text, prev["question"])
        examples = json.loads(prev["examples"] or "[]")
        label, lesson = 0, ""
        if signal in ("correction", "asked_again"):
            label = -1
            if signal == "correction":
                lesson = (f"When asked \"{(prev['understood'] or prev['question'])[:160]}\", the answer "
                          f"(\"{re.sub(r'\s+', ' ', prev['answer'] or '')[:160]}\") was corrected: \"{text[:220]}\"")
        elif signal == "praise":
            label = 1
        elif prev["self_check"] == "pass":
            label = 1
        if signal == "correction":
            try:   # got it wrong: the general part of the question is worth reading up on
                from app.services import curiosity
                curiosity.add_gap(prev["understood"] or prev["question"], "correction", guess=prev["answer"] or "", priority=0.9, context=text)
            except Exception as e:
                logger.debug(f"gap: {e}")
        if label:
            reason = {"correction": "you corrected it", "asked_again": "you asked again", "praise": "you thanked it",
                      "moved_on": "self-check passed, no complaint"}[signal]
            _label(examples, label, reason)
        c.execute("UPDATE turns SET user_signal = ?, signal_text = ?, label = CASE WHEN ? != 0 THEN ? ELSE label END, lesson = ? WHERE id = ?",
                  (signal, text[:300], label, label, lesson, prev["id"]))
        c.commit()
    finally:
        c.close()


# ------------------------------------------------------------------ understanding the question

UNDERSTAND_PROMPT = (
    "Rewrite the user's latest message as one standalone request that someone who can't see the conversation would "
    "understand. Replace every 'it', 'that', 'the other one', 'there' and so on with what it refers to, and fill in what "
    "the message leaves out from the conversation. Keep their wording, language and intent; don't answer it, don't add "
    "anything they didn't ask. If it already stands on its own, repeat it unchanged. Reply with the rewritten message only.")


def needs_understanding(query: str, history: List[Dict[str, Any]]) -> bool:
    if not any(m.get("role") == "assistant" and isinstance(m.get("content"), str) for m in history):
        return False
    q = (query or "").strip()
    return 0 < len(q) <= 240 and len(q.split()) <= 18 and bool(FOLLOWUP_RE.search(q))


def _conversation(history: List[Dict[str, Any]]) -> str:
    lines = []
    for m in history[-4:]:
        c = m.get("content")
        if m.get("role") in ("user", "assistant") and isinstance(c, str) and c.strip():
            lines.append(f"{'User' if m['role'] == 'user' else 'Assistant'}: {re.sub(r'\s+', ' ', c)[:500]}")
    return "\n".join(lines)


async def understand(query: str, history: List[Dict[str, Any]]) -> str:
    """The standalone version of a follow-up, or "" when it isn't one (or the rewrite didn't work)."""
    if not needs_understanding(query, history):
        return ""
    from app.services.llm import llm_service
    msgs = [{"role": "system", "content": UNDERSTAND_PROMPT},
            {"role": "user", "content": f"Conversation:\n{_conversation(history)}\n\nLatest message: {query}"}]
    # Blocks the answer, so it gets a few seconds at most (a warm local model takes 1-2 s, a cloud one well under 1);
    # on a timeout the question is used as it was asked.
    local = llm_service.get_endpoint_info().get("provider") in ("lm_studio", "ollama", "xod", "local")
    try:
        msg = await asyncio.wait_for(llm_service.chat_completion(messages=msgs, temperature=0.0, max_tokens=90, think=False,
                                                                 task="understand"), 6 if local else 8)
    except Exception as e:
        logger.debug(f"understand: {e}")
        return ""
    out = re.sub(r"\s+", " ", (msg.get("content") or "")).strip().strip('"').strip()
    out = re.sub(r"^(rewritten( message)?|standalone( request)?)\s*:\s*", "", out, flags=re.I)
    if not out or len(out) > 300 or out.lower() == query.strip().lower() or "\n" in out:
        return ""
    turn = _current.get()
    if turn is not None:
        turn["understood"] = out
    return out


# ------------------------------------------------------------------ its own check

JUDGE_PROMPT = ("You check an assistant's answer before it's used to teach a smaller model. Be strict but fair: an answer "
                "passes when it directly addresses what was asked, with no refusal, no dodge, no obvious error or "
                "contradiction, and no specifics that look made up.")


async def _self_check_later(turn_id: str, delay: float = 4.0) -> None:
    global _judging
    await asyncio.sleep(delay)
    from app.services import soul
    for _ in range(40):                  # wait (up to ~2 min) until no chat is using the model
        if soul._busy == 0:
            break
        await asyncio.sleep(3)
    else:
        return
    if _judging:
        return
    _judging += 1
    try:
        await self_check(turn_id)
    finally:
        _judging -= 1


async def self_check(turn_id: str) -> Optional[str]:
    from app.services import soul
    from app.services.llm import llm_service
    ep = await asyncio.to_thread(soul.local_endpoint)
    if not ep:
        return None
    c = _conn()
    try:
        t = c.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
    finally:
        c.close()
    if not t or not t["answer"]:
        return None
    q = t["understood"] or t["question"]
    tools = json.loads(t["tools"] or "[]")
    answer = t["answer"][:1500]
    # Kept under warmup.BIG_PROMPT_CHARS, so it doesn't push the router's prompt out of a local model's cache.
    user = (f"Question: {q[:500]}\n" + (f"(The assistant used: {', '.join(tools)})\n" if tools else "") +
            f"\nAnswer:\n{answer}\n\nReply with PASS or FAIL, then a few words why.")
    try:
        msg = await asyncio.wait_for(llm_service.chat_completion(
            messages=[{"role": "system", "content": JUDGE_PROMPT}, {"role": "user", "content": user}],
            temperature=0.0, max_tokens=40, think=False, endpoint_override=ep), 60)
    except Exception as e:
        logger.debug(f"self-check: {e}")
        return None
    out = re.sub(r"\s+", " ", msg.get("content") or "").strip()
    m = re.match(r"^\W*(PASS|FAIL)\W*(.*)$", out, re.I)
    if not m:
        return None
    verdict, why = m.group(1).lower(), m.group(2)[:200]
    c = _conn()
    try:
        c.execute("UPDATE turns SET self_check = ?, self_reason = ? WHERE id = ?", (verdict, why, turn_id))
        c.commit()
        examples = json.loads(t["examples"] or "[]")
        if verdict == "fail":
            _label(examples, -1, f"self-check: {why}"[:200])
            try:
                from app.services import curiosity
                curiosity.add_gap(q, "self_check", guess=answer, priority=0.8, context=f"its own check said: {why}")
            except Exception as e:
                logger.debug(f"gap: {e}")
            c.execute("UPDATE turns SET label = -1 WHERE id = ?", (turn_id,))
            c.commit()
    finally:
        c.close()
    logger.info(f"self-check {verdict}: {q[:60]} ({why[:60]})")
    return verdict


# ------------------------------------------------------------------ lessons

def lessons_note(query: str, k: int = 2) -> str:
    """Past corrections that look like this question, for the prompt."""
    q = _words(query)
    if len(q) < 1:
        return ""
    try:
        c = _conn()
        rows = c.execute("SELECT question, understood, lesson FROM turns WHERE lesson != '' ORDER BY ts DESC LIMIT 300").fetchall()
        c.close()
    except Exception:
        return ""
    scored = []
    for r in rows:
        w = _words(r["understood"] or r["question"])
        shared = len(q & w)
        if shared >= 2 or (shared >= 1 and len(q) <= 2):
            scored.append((shared / max(1, len(q | w)), r["lesson"]))
    scored.sort(reverse=True)
    if not scored:
        return ""
    return ("Lessons from times you were corrected on something like this (don't repeat the mistake):\n" +
            "\n".join(f"- {l}" for _, l in scored[:k]))


def stats() -> Dict[str, Any]:
    try:
        c = _conn()
        r = c.execute("""SELECT COUNT(*) AS turns, SUM(understood != '') AS understood,
                         SUM(self_check = 'pass') AS passed, SUM(self_check = 'fail') AS failed,
                         SUM(user_signal = 'correction') AS corrected, SUM(user_signal = 'asked_again') AS asked_again,
                         SUM(user_signal = 'praise') AS praised, SUM(label = 1) AS good, SUM(label = -1) AS bad,
                         SUM(lesson != '') AS lessons FROM turns""").fetchone()
        c.close()
        return {k: (r[k] or 0) for k in r.keys()}
    except Exception:
        return {}
