"""
What the Agent page shows (static/js/agent.js), one function per tab:

  activity    every job: tasks, goal check-ins, routine runs and chats where Xod did something, newest first, each
              with a one-line summary; open one for its steps by agent (MAIN, SUBAGENT 01…)
  approvals   every "waiting for your OK" and what you decided (services/agent_log.py)
  browser     jobs that used the browser, with the last screenshot and the site
  schedules   reminders (once), routines (daily, weekly, every few hours), goals' check-ins, the morning brief
  identity    who Xod is now (its soul: stage, mood, bond) and what it remembers (about you, its memory)
  team        the brains on each tier and outside teammate agents (services/team.py)
"""
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.services import agent_log


def _one_line(text: str, n: int = 140) -> str:
    t = re.sub(r"<!--.*?-->", "", str(text or ""), flags=re.S)
    t = re.sub(r"[*_`#>|]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    s = m.group(1) if m and len(m.group(1)) > 25 else t
    return s[:n] + ("…" if len(s) > n else "")


def status_line() -> str:
    """What Xod is doing right now, for under its name."""
    try:
        from app.services.task_queue import task_queue
        working = [t for t in task_queue.list(limit=20) if t.get("status") == "working"]
        if working:
            return _one_line(working[0].get("now") or working[0].get("title") or "Working", 60).rstrip("…")
        waiting = [t for t in task_queue.list(limit=40) if t.get("status") == "waiting"]
        if waiting:
            return f"Waiting for you ({len(waiting)})"
    except Exception:
        pass
    return "Ready"


def activity(limit: int = 80) -> List[Dict[str, Any]]:
    from app.services.task_queue import task_queue
    items: List[Dict[str, Any]] = []
    for t in task_queue.list(limit=limit):
        origin = f"task:{t['id']}"
        items.append({"id": origin, "kind": "task", "title": t.get("title") or "A task",
                      "summary": _one_line(t.get("result") or t.get("now") or ""), "status": t.get("status"),
                      "at": t.get("updated_at") or t.get("created_at"), "agents": len(agent_log.agents_in(origin)),
                      "has_screenshot": bool(t.get("has_screenshot"))})
    # Chats where Xod used tools (asking a question with no tools isn't a job).
    with agent_log._conn() as c:
        rows = c.execute("SELECT origin, MAX(ts) AS at, COUNT(*) AS n FROM agent_steps WHERE origin LIKE 'chat:%' OR origin = "
                         "'xod (on its own)' GROUP BY origin ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        last = {r["origin"]: c.execute("SELECT title FROM agent_steps WHERE origin = ? ORDER BY ts DESC LIMIT 1", (r["origin"],)).fetchone()
                for r in rows}
    titles = {}
    try:
        from app.services import storage
        titles = {s["id"]: s.get("title") for s in storage.list_sessions()}
    except Exception:
        pass
    for r in rows:
        o = r["origin"]
        sid = o.split(":", 1)[1] if o.startswith("chat:") else ""
        items.append({"id": o, "kind": "chat" if sid else "own", "title": (titles.get(sid) if sid else "On its own") or "A chat",
                      "summary": f"{r['n']} step{'s' if r['n'] != 1 else ''} · last: {(last[o]['title'] if last.get(o) else '')}"[:140],
                      "status": "done", "at": r["at"], "agents": len(agent_log.agents_in(o))})
    items.sort(key=lambda x: x.get("at") or 0, reverse=True)
    return items[:limit]


def detail(origin: str) -> Dict[str, Any]:
    steps = agent_log.steps_for(origin)
    out: Dict[str, Any] = {"id": origin, "steps": steps, "order": sorted(steps, key=lambda a: (a != "main", a))}
    try:
        from app.services import usage
        out["usage"] = usage.for_origin(origin)
    except Exception:
        out["usage"] = None
    if origin.startswith("task:"):
        from app.services.task_queue import task_queue
        t = task_queue.get(origin[5:]) or {}
        out.update(kind="task", title=t.get("title"), status=t.get("status"), result=t.get("result") or "", now=t.get("now"),
                   request=(t.get("request") or "")[:600], at=t.get("updated_at"), has_screenshot=bool(t.get("has_screenshot")),
                   history=t.get("history") or [])
        if not steps and t.get("history"):          # older tasks: their progress notes stand in for steps
            out["steps"] = {"main": [{"ts": h.get("at") or 0, "title": h.get("note") or str(h), "detail": "", "ok": True}
                                     if isinstance(h, dict) else {"ts": 0, "title": str(h), "detail": "", "ok": True}
                                     for h in t["history"]]}
            out["order"] = ["main"]
    elif origin.startswith("chat:"):
        try:
            from app.services import storage
            s = storage.get_session(origin[5:]) or {}
            out.update(kind="chat", title=s.get("title") or "A chat", status="done")
        except Exception:
            out.update(kind="chat", title="A chat", status="done")
    else:
        out.update(kind="own", title="On its own", status="done")
    return out


def approvals() -> List[Dict[str, Any]]:
    return agent_log.approvals(150)


def browser(limit: int = 40) -> List[Dict[str, Any]]:
    from app.services.task_queue import task_queue
    out = []
    for t in task_queue.list(limit=200):
        mission = t.get("mission_json") or {}
        if not (t.get("has_screenshot") or mission):
            continue
        url = mission.get("current_url") or mission.get("start_url") or next((s.get("url") for s in reversed(mission.get("steps") or []) if s.get("url")), "")
        out.append({"id": t["id"], "title": t.get("title"), "site": (urlparse(url).hostname or "").removeprefix("www.") if url else "",
                    "status": t.get("status"), "at": t.get("updated_at"),
                    "shot": f"/api/tasks/{t['id']}/screenshot" if t.get("has_screenshot") else None})
        if len(out) >= limit:
            break
    return out


def schedules() -> Dict[str, Any]:
    from app.services import goals, routines
    rs = routines.list_all()
    reminders = [r for r in rs if r.get("source") == "reminder"]
    others = [r for r in rs if r.get("source") != "reminder"]
    group = lambda kinds: [{"id": r["id"], "name": r["name"], "when": r["schedule_text"], "next": r.get("next_run_at"), "enabled": r["enabled"],
                            "last": r.get("last_status")} for r in others if r["schedule"].get("kind") in kinds]
    return {"reminders": [{"id": r["id"], "name": r["instructions"], "when": r["schedule_text"], "next": r.get("next_run_at"),
                           "done": not r.get("next_run_at")} for r in reminders],
            "daily": group(("daily", "weekly")), "interval": group(("every",)), "manual": group(("manual", "once")),
            "goals": [{"id": g["id"], "name": g["title"], "progress": g["progress"], "next": g.get("next_check_at"), "status": g["status"],
                       "every_hours": g["check_hours"]} for g in goals.list_all(include_done=False)],
            "brief": goals.brief_settings()}


def add_reminder(text: str, at: str) -> Dict[str, Any]:
    from app.services import routines
    text = (text or "").strip()
    if not text:
        return {"success": False, "error": "Say what to remind you of."}
    sched = routines._clean_schedule({"kind": "once", "at": at})
    if sched["kind"] != "once" or not routines.next_run(sched):
        return {"success": False, "error": "Pick a time in the future."}
    r = routines.create(text[:60], text, sched, source="reminder")
    return {"success": True, "reminder": r}


def identity() -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": "Xod"}
    try:
        from app.services import soul
        s = soul.public()
        out["soul"] = {"knows_you_as": s.get("name") or "",       # soul's "name" is the person's, not Xod's
                       "stage": s.get("stage_name"), "blurb": s.get("stage_blurb"), "mood": s.get("feeling") or s.get("mood"),
                       "bond": s.get("bond_word"), "days": s.get("together_days"), "born": s.get("born"),
                       "interests": s.get("interests", [])[:6], "lessons": [l.get("text") if isinstance(l, dict) else str(l) for l in s.get("lessons", [])][:4]}
    except Exception:
        out["soul"] = None
    try:
        from app.services import user_memory
        fs = user_memory.facts()
        out["about_you"] = {"count": len(fs), "recent": [f.get("text") for f in fs[-5:][::-1]],
                            "updated": max((f.get("updated_at") or f.get("created_at") or 0) for f in fs) if fs else None}
    except Exception:
        out["about_you"] = None
    try:
        from app.services.memory_service import memory_service
        out["memory"] = memory_service.get_stats()
    except Exception:
        out["memory"] = None
    return out


async def team_view() -> Dict[str, Any]:
    from app.services import team
    return {"settings": team.load(), "tiers": await team.tiers(), "brains": await team.brains(), "agents": team.outside_agents(),
            "on": await team.team_on()}
