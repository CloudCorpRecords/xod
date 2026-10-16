"""
Goals: something that takes days ("find a flat under $2k by November", "learn enough Spanish for my trip"), with a
plan, regular check-ins and progress, plus an optional morning brief.

A goal gets a short plan when it's made (the model writes 3–7 steps, a deadline if one was said, and how often to
check in). Each check-in is an ordinary task (task_queue): it works on the next unfinished step, never sends, buys or
signs up without asking, and ends with a PROGRESS line that moves the plan on. So check-ins show up in Tasks, keep
their approval gates, and reach the phone like any task.

The morning brief is a task once a day at the time you choose: your goals and their next steps, what's waiting for
you, today's calendar if one is connected, and news on what you care about. Short.
"""
import datetime as dt
import json
import logging
import re
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.goals")
DB = DATA_DIR / "xod.db"
BRIEF = DATA_DIR / "brief.json"
_PROGRESS_RE = re.compile(r"PROGRESS:\s*(?:step\s*(\d+)\s*(done|blocked)(?::\s*(.+))?|goal\s+(done))", re.I)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS goals (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, outcome TEXT, deadline TEXT, plan TEXT, log TEXT, status TEXT,
        check_hours INTEGER DEFAULT 24, next_check_at REAL, last_task_id TEXT, checking INTEGER DEFAULT 0,
        created_at REAL, updated_at REAL, request TEXT)""")
    return c


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["plan"] = json.loads(d.get("plan") or "[]")
    d["log"] = json.loads(d.get("log") or "[]")
    d["checking"] = bool(d.get("checking"))
    done = sum(1 for s in d["plan"] if s.get("status") == "done")
    d["progress"] = {"done": done, "total": len(d["plan"])}
    d["next_step"] = next((s["text"] for s in d["plan"] if s.get("status") != "done"), None)
    return d


def list_all(include_done: bool = True) -> List[Dict[str, Any]]:
    with _conn() as c:
        rows = [_row(r) for r in c.execute("SELECT * FROM goals ORDER BY created_at DESC")]
    return rows if include_done else [g for g in rows if g["status"] == "active"]


def get(gid: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        r = c.execute("SELECT * FROM goals WHERE id = ?", (gid,)).fetchone()
    return _row(r) if r else None


def _save(gid: str, **fields: Any) -> None:
    sets, vals = [], []
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(json.dumps(v) if k in ("plan", "log") else v)
    sets.append("updated_at = ?")
    vals.append(time.time())
    with _conn() as c:
        c.execute(f"UPDATE goals SET {', '.join(sets)} WHERE id = ?", (*vals, gid))


# ------------------------------------------------------------------ making one
PLAN = """Someone wants help with a goal that will take days or weeks. Today is {today}.
Their words: {text}

Write a short, practical plan an assistant with web research, files and connected apps can work through, one step per
check-in. The assistant finds, compares, shortlists and drafts; booking, paying, signing up and sending are done by the
person (or only with their OK), so write steps like "Find…", "Compare…", "Shortlist…", "Draft…", never "Book…" or
"Once booked…". The plan is for them, not for "the client". JSON only:
{{"title": "the goal in a few words", "outcome": "what done looks like, in one sentence",
 "deadline": "YYYY-MM-DD only if they said when it must be done by (a trip date is not a deadline), else null",
 "steps": ["3 to 7 concrete steps, each doable in one sitting"],
 "check_every_hours": 24 (or 12 if it's urgent, 72 or 168 if it's slow)}}"""


async def plan_for(text: str) -> Dict[str, Any]:
    from app.services.llm import llm_service, parse_llm_json, JSON_MIN_TOKENS
    try:
        reply = await llm_service.chat_completion(messages=[{"role": "user", "content": PLAN.format(today=dt.date.today().isoformat(), text=text)}],
                                                  temperature=0.2, max_tokens=max(JSON_MIN_TOKENS, 900), think=False, task="goal_plan")
        p = parse_llm_json(reply.get("content", "")) or {}
    except Exception as e:
        logger.warning(f"goal plan: {e}")
        p = {}
    steps = [str(s).strip() for s in (p.get("steps") or []) if str(s).strip()][:7]
    if len(steps) < 2:
        steps = ["Work out exactly what's needed and what matters most", "Find the options", "Compare them and narrow down",
                 "Report the best choices with the next action"]
    deadline = p.get("deadline") if isinstance(p.get("deadline"), str) and re.match(r"^\d{4}-\d{2}-\d{2}$", p.get("deadline") or "") else None
    try:
        hours = int(p.get("check_every_hours") or 24)
    except (TypeError, ValueError):
        hours = 24
    return {"title": str(p.get("title") or text)[:80].strip(), "outcome": str(p.get("outcome") or "")[:300], "deadline": deadline,
            "steps": steps, "check_hours": max(6, min(168, hours))}


async def create(text: str, start_now: bool = True) -> Dict[str, Any]:
    p = await plan_for(text)
    gid = f"goal-{uuid.uuid4().hex[:8]}"
    now = time.time()
    plan = [{"text": s, "status": "todo"} for s in p["steps"]]
    with _conn() as c:
        c.execute("INSERT INTO goals (id, title, outcome, deadline, plan, log, status, check_hours, next_check_at, created_at, "
                  "updated_at, request) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (gid, p["title"], p["outcome"], p["deadline"], json.dumps(plan), "[]", "active", p["check_hours"],
                   now if start_now else now + p["check_hours"] * 3600, now, now, text))
    if start_now:
        check_in(gid)
    return get(gid)


def update(gid: str, **fields: Any) -> Optional[Dict[str, Any]]:
    g = get(gid)
    if not g:
        return None
    ok = {}
    if fields.get("status") in ("active", "paused", "done"):
        ok["status"] = fields["status"]
    if fields.get("check_hours"):
        ok["check_hours"] = max(6, min(168, int(fields["check_hours"])))
    if isinstance(fields.get("plan"), list):
        ok["plan"] = [{"text": str(s.get("text") if isinstance(s, dict) else s)[:300],
                       "status": (s.get("status") if isinstance(s, dict) and s.get("status") in ("todo", "done", "blocked") else "todo")}
                      for s in fields["plan"]][:12]
    if ok:
        _save(gid, **ok)
    return get(gid)


def delete(gid: str) -> bool:
    with _conn() as c:
        return c.execute("DELETE FROM goals WHERE id = ?", (gid,)).rowcount > 0


# ------------------------------------------------------------------ check-ins
def compose(g: Dict[str, Any]) -> str:
    marks = {"done": "✓", "blocked": "✗"}
    steps = "\n".join(f"{i}. {marks.get(s['status'], '·')} {s['text']}" + (f" (blocked: {s['note']})" if s.get("note") else "")
                      for i, s in enumerate(g["plan"], 1))
    nxt = next((i for i, s in enumerate(g["plan"], 1) if s["status"] != "done"), None)
    notes = "\n".join(f"- {time.strftime('%b %d', time.localtime(e['at']))}: {e['text']}" for e in g["log"][-5:])
    return (f"This is a check-in on my ongoing goal: {g['title']}.\n"
            + (f"Done means: {g['outcome']}\n" if g.get("outcome") else "")
            + (f"Deadline: {g['deadline']} (today is {dt.date.today().isoformat()})\n" if g.get("deadline") else "")
            + f"\nPlan (✓ done, ✗ blocked, · to do):\n{steps}\n"
            + (f"\nWhat earlier check-ins found:\n{notes}\n" if notes else "")
            + (f"\nWork on step {nxt} now. " if nxt else "\nAll steps are done: check whether the goal is met. ")
            + "Research, compare and draft as needed, but don't send, buy, book or sign up for anything without asking me. "
            "Finish with what you found (short, with links), then one last line exactly like one of these:\n"
            f"PROGRESS: step {nxt or 1} done\nPROGRESS: step {nxt or 1} blocked: <why>\nPROGRESS: goal done")


def check_in(gid: str) -> Dict[str, Any]:
    from app.services.task_queue import task_queue
    g = get(gid)
    if not g:
        return {"success": False, "error": "No such goal."}
    if g["checking"]:
        return {"success": False, "error": "A check-in is already running."}
    task = task_queue.create(compose(g), title=f"◎ {g['title']}")
    _save(gid, last_task_id=task["id"], checking=1, next_check_at=time.time() + g["check_hours"] * 3600)
    return {"success": True, "task": task}


def apply_progress(gid: str, report: str) -> Dict[str, Any]:
    """What a check-in said, onto the plan and the log."""
    g = get(gid)
    if not g:
        return {}
    plan, log = g["plan"], g["log"]
    status = g["status"]
    for m in _PROGRESS_RE.finditer(report or ""):
        if m.group(4):
            status = "done"
            for s in plan:
                s["status"] = "done"
            continue
        i = int(m.group(1)) - 1
        if 0 <= i < len(plan):
            plan[i]["status"] = m.group(2).lower()
            if m.group(3):
                plan[i]["note"] = m.group(3).strip()[:200]
            elif "note" in plan[i] and plan[i]["status"] == "done":
                plan[i].pop("note")
    summary = _PROGRESS_RE.sub("", report or "").strip()
    summary = re.sub(r"\s+", " ", re.sub(r"<!--.*?-->", "", summary, flags=re.S))[:500]
    if summary:
        log.append({"at": time.time(), "text": summary})
    if all(s["status"] == "done" for s in plan) and plan:
        status = "done"
    _save(gid, plan=plan, log=log[-30:], status=status, checking=0)
    return get(gid)


def _follow_up() -> None:
    from app.services.task_queue import task_queue
    for g in list_all():
        if not g["checking"] or not g.get("last_task_id"):
            continue
        t = task_queue.get(g["last_task_id"])
        st = (t or {}).get("status")
        if st == "done":
            apply_progress(g["id"], (t or {}).get("result") or "")
        elif st in ("failed", "cancelled") or not t:
            _save(g["id"], checking=0, log=(g["log"] + [{"at": time.time(), "text": f"The check-in {st or 'went missing'}."}])[-30:])


# ------------------------------------------------------------------ the morning brief
def brief_settings() -> Dict[str, Any]:
    try:
        return {"enabled": False, "time": "08:00", "last_date": "", **json.loads(BRIEF.read_text())}
    except Exception:
        return {"enabled": False, "time": "08:00", "last_date": ""}


def set_brief(enabled: Optional[bool] = None, at: Optional[str] = None) -> Dict[str, Any]:
    s = brief_settings()
    if enabled is not None:
        s["enabled"] = bool(enabled)
    if at and re.match(r"^\d{1,2}:\d{2}$", at):
        h, m = at.split(":")
        s["time"] = "%02d:%02d" % (max(0, min(23, int(h))), max(0, min(59, int(m))))
        if s.get("last_date") == dt.date.today().isoformat() and dt.datetime.now().strftime("%H:%M") < s["time"]:
            s["last_date"] = ""          # moved later today: it still comes today
    BRIEF.write_text(json.dumps(s))
    return s


def compose_brief() -> str:
    lines = [f"Make my morning brief for {dt.date.today().strftime('%A %B %d')}. Keep it short: a few bullets per part, "
             "skip any part with nothing in it, and don't send, buy or change anything."]
    goals = list_all(include_done=False)
    if goals:
        lines.append("\nMy goals (say where each stands and the next step):")
        for g in goals:
            lines.append(f"- {g['title']}: {g['progress']['done']}/{g['progress']['total']} steps done; next: {g['next_step'] or 'check it is met'}"
                         + (f"; deadline {g['deadline']}" if g.get("deadline") else ""))
    try:
        from app.services.task_queue import task_queue
        waiting = [t for t in task_queue.list(limit=50) if t.get("status") in ("waiting", "needs_input", "blocked")]
        if waiting:
            lines.append("\nWaiting for me: " + "; ".join(t.get("title") or "a task" for t in waiting[:6]))
    except Exception:
        pass
    lines.append("\nIf a calendar is connected, list today's events. Then the 3 most useful news items on what I care about"
                 + _interests() + ", with links.")
    return "\n".join(lines)


def _interests() -> str:
    try:
        from app.services import user_memory
        note = user_memory.core_note() or ""
        return f" (from what you know about me: {note[:400]})" if note else ""
    except Exception:
        return ""


def _brief_due() -> bool:
    s = brief_settings()
    if not s["enabled"]:
        return False
    today = dt.date.today().isoformat()
    return s.get("last_date") != today and dt.datetime.now().strftime("%H:%M") >= s["time"]


def run_brief() -> Dict[str, Any]:
    from app.services.task_queue import task_queue
    s = brief_settings()
    s["last_date"] = dt.date.today().isoformat()
    BRIEF.write_text(json.dumps(s))
    task = task_queue.create(compose_brief(), title="☀ Morning brief")
    return {"success": True, "task": task}


# ------------------------------------------------------------------ ticking (from the routines scheduler)
def tick() -> List[str]:
    started = []
    _follow_up()
    now = time.time()
    for g in list_all(include_done=False):
        if not g["checking"] and g.get("next_check_at") and g["next_check_at"] <= now:
            r = check_in(g["id"])
            if r.get("success"):
                started.append(g["id"])
    if _brief_due():
        run_brief()
        started.append("brief")
    return started


# ------------------------------------------------------------------ from the chat
_NEW_RE = re.compile(r"^\s*(?:please\s+)?(?:(?:set|make|create|add|start)\s+(?:a\s+|me\s+a\s+|my\s+)?(?:new\s+)?goal(?:\s+(?:to|for|of))?|"
                     r"new goal|goal|my goal is(?: to)?|i(?:'d| would) like to make (?:it )?a goal to)\s*[:\-–]?\s*(.{6,})", re.I | re.S)
_BRIEF_RE = re.compile(r"\b(?:turn|switch|set)\s+(on|off|up)\b[^.?!]{0,30}\bmorning brief(?:ing)?\b|\bmorning brief(?:ing)?\b[^.?!]{0,20}\b(on|off)\b|"
                       r"\b(?:give|send) me a morning brief(?:ing)?\b|\b(?:start|stop)\b[^.?!]{0,20}\bmorning brief(?:ing)?\b", re.I)
_AT_RE = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)


def route(text: str) -> Optional[Dict[str, Any]]:
    t = text or ""
    m = _BRIEF_RE.search(t)
    if m:
        off = bool(re.search(r"\b(?:off|stop)\b", m.group(0), re.I))
        at = None
        a = _AT_RE.search(t)
        if a:
            h, mi, ap = int(a.group(1)), int(a.group(2) or 0), (a.group(3) or "").lower()
            if ap == "pm" and h < 12:
                h += 12
            if ap == "am" and h == 12:
                h = 0
            at = "%02d:%02d" % (h, mi)
        return {"kind": "brief", "on": not off, "at": at}
    m = _NEW_RE.match(t)
    if m and "?" not in t[:12]:
        return {"kind": "goal", "text": m.group(1).strip()}
    return None


async def stream_chat(decision: Dict[str, Any]):
    if decision["kind"] == "brief":
        s = set_brief(enabled=decision["on"], at=decision.get("at"))
        h, m = map(int, s["time"].split(":"))
        when = dt.time(h, m).strftime("%I:%M %p").lstrip("0")
        yield {"type": "content", "content": (f"Morning brief is on: every day at {when}, as a task (and on your phone if it's linked). "
                                               "It covers your goals, what's waiting for you, today's calendar if one is connected, "
                                               "and news on what you care about.") if s["enabled"] else "Morning brief is off."}
        yield {"type": "done"}
        return
    yield {"type": "status", "content": "Making a plan for your goal…"}
    yield {"type": "tool_call", "name": "xod_goal", "args": {"goal": decision["text"][:200]}, "id": "goal_new"}
    g = await create(decision["text"])
    yield {"type": "tool_result", "name": "xod_goal", "id": "goal_new", "summary": f"{len(g['plan'])} steps · check-ins every "
           f"{g['check_hours']} h", "data": g}
    every = "day" if g["check_hours"] == 24 else (f"{g['check_hours']} hours" if g["check_hours"] < 48 else f"{g['check_hours'] // 24} days")
    steps = "\n".join(f"{i}. {s['text']}" for i, s in enumerate(g["plan"], 1))
    yield {"type": "content", "content": (f"**Goal: {g['title']}**" + (f" (by {g['deadline']})" if g.get("deadline") else "") + "\n\n"
                                          + (f"Done means: {g['outcome']}\n\n" if g.get("outcome") else "") + f"{steps}\n\n"
                                          f"I've started on step 1 (it's in Tasks), and I'll check in every {every} and move the plan "
                                          "on. I won't send, buy or sign up for anything without asking. See it any time in "
                                          "Tools → Routines & goals.")}
    yield {"type": "done"}
