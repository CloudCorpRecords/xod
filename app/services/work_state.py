"""
What Xod is doing right now, for Xod itself: its background tasks (running, waiting for you, just finished),
routines and when they run next, background jobs it started, and a browser step waiting for your OK. Put into
the prompt when you ask about its work ("what are you working on?", "how did the application go?", "anything
waiting on me?", "what did you do while I was away?"), so the answer comes from what really happened instead
of a guess. xod_tasks (the tool) gives the full details and result of any one task.
"""
import re
import time
from typing import Any, Dict, List, Optional

# Asking about Xod's own work.
WORK_RE = re.compile(
    r"\b(what are you (doing|working on|up to)|what('?s| is) (running|going on|in progress)|status|progress|"
    r"(any|my|your|the|background) (tasks?|jobs?|routines?)|tasks?\b|routines?\b|in the background|"
    r"how did (it|that|the|my)\b|did (it|that|the|you) (finish|work|go through|get sent|send|submit)|"
    r"is (it|that|the .{1,30}) (done|finished|ready|still running)|anything (waiting|pending|for me)|waiting (on|for) me|"
    r"while i was (away|gone|out|asleep)|since (i left|last time|yesterday)|what did you (do|get done|find|finish)|"
    r"catch me up|recap|what'?s new)\b", re.I)


def _ago(ts: Optional[float]) -> str:
    if not ts:
        return ""
    m = int((time.time() - ts) / 60)
    if m < 1:
        return "just now"
    if m < 60:
        return f"{m} min ago"
    if m < 60 * 24:
        return f"{m // 60} h ago"
    return f"{m // (60 * 24)} days ago"


def _in(ts: Optional[float]) -> str:
    if not ts:
        return ""
    m = int((ts - time.time()) / 60)
    if m < 1:
        return "any moment"
    if m < 60:
        return f"in {m} min"
    if m < 60 * 24:
        return f"in {m // 60} h"
    return time.strftime("%a %-I:%M %p", time.localtime(ts))


def _short(s: Any, n: int) -> str:
    t = re.sub(r"\s+", " ", re.sub(r"[#*_`>|]+", " ", str(s or ""))).strip()
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def snapshot(since: Optional[float] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"working": [], "waiting": [], "queued": [], "finished": [], "routines": [], "jobs": [], "browser": None}
    try:
        from app.services.task_queue import task_queue
        day = time.time() - 36 * 3600
        for t in task_queue.list(80):
            item = {"id": t["id"], "title": t["title"], "status": t["status"], "now": t.get("now") or "",
                    "updated": t.get("updated_at")}
            if t["status"] == "working":
                out["working"].append(item)
            elif t["status"] == "waiting":
                det = t.get("waiting_detail") or {}
                item["needs"] = {"approval": "your OK", "question": "an answer from you", "blocked": "you to clear a check"}.get(
                    t.get("waiting_kind") or "", "you")
                item["detail"] = _short(det.get("question") or det.get("label") or det.get("message") or "" if isinstance(det, dict) else det, 140)
                out["waiting"].append(item)
            elif t["status"] == "queued":
                out["queued"].append(item)
            elif t["status"] in ("done", "failed", "cancelled") and (t.get("finished_at") or 0) > (since or day):
                item["finished"] = t.get("finished_at")
                item["result"] = _short(t.get("result"), 220)
                out["finished"].append(item)
    except Exception:
        pass
    try:
        from app.services import routines
        for r in routines.list_all():
            if r.get("enabled"):
                out["routines"].append({"name": r["name"], "when": r.get("schedule_text") or "", "next": r.get("next_run_at"),
                                        "last": r.get("last_run_at"), "last_status": r.get("last_status") or ""})
    except Exception:
        pass
    try:
        from app.services.system_control import system_control_service
        out["jobs"] = [{"id": j["id"], "name": j["name"], "status": j["status"], "started": j.get("start_time")}
                       for j in system_control_service.list_jobs() if j.get("status") in ("running", "starting")]
    except Exception:
        pass
    try:
        from app.services.browser_sandbox import browser_sandbox_service as sb
        if sb.pending_submit:
            p = sb.pending_submit
            out["browser"] = {"waiting": True, "label": p.get("label"), "page": p.get("title"), "since": p.get("created_at")}
        elif sb.mission_busy:
            m = sb.latest_mission or {}
            out["browser"] = {"working": True, "goal": _short(m.get("goal"), 100), "steps": len(m.get("steps") or [])}
    except Exception:
        pass
    return out


def is_empty(s: Dict[str, Any]) -> bool:
    return not any(s[k] for k in ("working", "waiting", "queued", "finished", "routines", "jobs", "browser"))


def note(since: Optional[float] = None) -> str:
    """A short block for a prompt: what Xod's own work looks like right now. Empty when there's nothing."""
    s = snapshot(since)
    if is_empty(s):
        return "Your own work right now: no background tasks, routines or jobs."
    lines = []
    for t in s["waiting"]:
        lines.append(f"- WAITING for {t['needs']}: \"{t['title']}\"" + (f" ({t['detail']})" if t.get("detail") else "") + f", since {_ago(t['updated'])}")
    if s["browser"] and s["browser"].get("waiting"):
        lines.append(f"- WAITING for your OK in the browser: press \"{s['browser']['label']}\" on {s['browser']['page']}")
    for t in s["working"]:
        lines.append(f"- Working on \"{t['title']}\"" + (f": {_short(t['now'], 90)}" if t.get("now") else ""))
    if s["browser"] and s["browser"].get("working"):
        lines.append(f"- In the browser right now: {s['browser']['goal']} (step {s['browser']['steps']})")
    for t in s["queued"][:4]:
        lines.append(f"- Queued: \"{t['title']}\"")
    for t in s["finished"][:6]:
        verb = {"done": "Finished", "failed": "Couldn't finish", "cancelled": "Stopped"}.get(t["status"], t["status"])
        lines.append(f"- {verb} {_ago(t['finished'])}: \"{t['title']}\"" + (f" — {t['result']}" if t.get("result") else ""))
    for j in s["jobs"][:4]:
        lines.append(f"- Background job running: {j['name']} ({j['id']}, started {j['started']})")
    for r in s["routines"][:6]:
        lines.append(f"- Routine \"{r['name']}\" ({r['when']}): next {_in(r['next'])}" +
                     (f", last ran {_ago(r['last'])} ({r['last_status']})" if r.get("last") else ""))
    return "Your own work right now (from your task list; use it when asked about your work, otherwise only mention "\
           "something waiting on the user if it matters):\n" + "\n".join(lines[:18])


def find_task(query: str) -> Optional[Dict[str, Any]]:
    """The task a person means ("the application", "the Acme one", an id)."""
    from app.services.task_queue import task_queue
    q = (query or "").strip().lower()
    tasks = task_queue.list(120)
    for t in tasks:
        if t["id"].lower() == q:
            return task_queue.get(t["id"])
    words = {w for w in re.findall(r"[a-z0-9]+", q) if len(w) > 2}
    best, best_score = None, 0
    for t in tasks:
        tw = set(re.findall(r"[a-z0-9]+", (t["title"] + " " + (t.get("request") or "")).lower()))
        score = len(words & tw)
        if score > best_score:
            best, best_score = t, score
    return task_queue.get(best["id"]) if best else None


def task_details(t: Dict[str, Any]) -> Dict[str, Any]:
    hist = t.get("history") or []
    return {"id": t["id"], "title": t["title"], "request": t.get("request"), "status": t["status"],
            "waiting_for": t.get("waiting_kind"), "waiting_detail": t.get("waiting_detail"),
            "created": time.strftime("%b %-d %-I:%M %p", time.localtime(t["created_at"])),
            "finished": time.strftime("%b %-d %-I:%M %p", time.localtime(t["finished_at"])) if t.get("finished_at") else None,
            "steps": t.get("steps"), "now": t.get("now"), "result": t.get("result"),
            "history": [f"{time.strftime('%-I:%M %p', time.localtime(h.get('at', 0)))} {h.get('text')}" for h in hist[-15:]]}
