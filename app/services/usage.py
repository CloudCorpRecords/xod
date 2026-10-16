"""
Every model call Xod makes, local and cloud, for the Usage tab on Xod's page.

Each call is one row: when, which model (and whether it ran on this computer or online), what it was for (the chat,
the worker, a subagent, the done-check, the safety check, a goal plan…), which job and which agent, tokens in and out
(the server's own counts, or ~4 characters a token when it gives none), how long it took and, for cloud models, what
it cost. Rows older than 90 days are dropped.

What it was for comes from the call's task tag (llm.py callers pass one), or from `purpose` when a part of Xod sets
it around its calls (worker, team), or the kind of call.
"""
import contextvars
import datetime as dt
import logging
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.usage")
DB = DATA_DIR / "xod.db"
KEEP_DAYS = 90
LOCAL = {"ollama", "lm_studio", "xod", "local", "specialist"}

purpose = contextvars.ContextVar("xod_usage_purpose", default="")

LABELS = {
    "chat": "Chat answers", "stream": "Chat answers", "deliberate": "Planning and tool calls", "worker": "Worker (jobs on the computer)",
    "subagent": "Subagents", "work_check": "Done-checks", "sentinel": "Safety check", "goal_plan": "Goal plans", "react_step": "Follow-up steps",
    "repair": "Self-repair", "builder_plan": "App builder", "about_self": "What works / about itself", "router": "Routing",
    "turn_understand": "Understanding follow-ups", "memory": "Memory", "vision": "Seeing pictures", "model_test": "Model tests",
}


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS llm_calls (
        id TEXT PRIMARY KEY, ts REAL, provider TEXT, model TEXT, local INTEGER, purpose TEXT, origin TEXT, agent TEXT,
        prompt_tokens INTEGER, completion_tokens INTEGER, estimated INTEGER, ms INTEGER, cost_usd REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS llm_calls_ts ON llm_calls(ts)")
    return c


def record(provider: str, model: str, usage: Optional[Dict[str, Any]], prompt_chars: int, completion_chars: int,
           kind: str = "", task: Optional[str] = None, started: Optional[float] = None) -> None:
    """One finished call (from llm.py). Never fails the call."""
    try:
        u = usage or {}
        pt, ct = u.get("prompt_tokens"), u.get("completion_tokens")
        estimated = pt is None or ct is None
        if pt is None:
            pt = max(1, prompt_chars // 4)
        if ct is None:
            ct = max(0, completion_chars // 4)
        cost = None
        local = provider in LOCAL
        if not local:
            from app.services import llm_routing
            cost = llm_routing.cost(model, int(pt), int(ct))
        try:
            from app.services import activity, agent_log
            origin, agent = activity.origin.get(), agent_log.agent.get()
        except Exception:
            origin, agent = "", "main"
        what = task or purpose.get() or kind or "chat"
        ms = int((time.time() - started) * 1000) if started else None
        with _conn() as c:
            c.execute("INSERT INTO llm_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (uuid.uuid4().hex[:12], time.time(), provider, model, int(local), what, origin, agent,
                       int(pt), int(ct), int(estimated), ms, cost))
        if hash(uuid.uuid4()) % 400 == 0:       # now and then, drop what's past keeping (here and in the step log)
            prune()
            from app.services import agent_log
            agent_log.prune()
    except Exception as e:
        logger.debug(f"usage not recorded: {e}")


def prune() -> None:
    with _conn() as c:
        c.execute("DELETE FROM llm_calls WHERE ts < ?", (time.time() - KEEP_DAYS * 86400,))


# ------------------------------------------------------------------ reading it
RANGES = {"today": None, "7d": 7, "30d": 30, "90d": 90}


def _since(rng: str) -> float:
    if rng == "today":
        return dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return time.time() - (RANGES.get(rng) or 7) * 86400


def _rows(c, sql: str, args=()) -> List[Dict[str, Any]]:
    return [dict(r) for r in c.execute(sql, args)]


def _tps(r: Dict[str, Any]) -> Optional[float]:
    secs = (r.get("ms") or 0) / 1000
    return round(r["completion_tokens"] / secs, 1) if secs > 0.2 and (r.get("completion_tokens") or 0) >= 16 else None


def overview(rng: str = "7d") -> Dict[str, Any]:
    since = _since(rng)
    with _conn() as c:
        t = dict(c.execute("""SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens),0) AS prompt, COALESCE(SUM(completion_tokens),0) AS completion,
            COALESCE(SUM(cost_usd),0) AS cost, COALESCE(SUM(CASE WHEN local=1 THEN prompt_tokens+completion_tokens END),0) AS local_tokens,
            COALESCE(SUM(CASE WHEN local=0 THEN prompt_tokens+completion_tokens END),0) AS cloud_tokens,
            COALESCE(SUM(estimated),0) AS estimated, COALESCE(SUM(ms),0) AS ms,
            COALESCE(SUM(CASE WHEN ms > 200 AND completion_tokens >= 16 THEN completion_tokens END),0) AS timed_completion,
            COALESCE(SUM(CASE WHEN ms > 200 AND completion_tokens >= 16 THEN ms END),0) AS timed_ms
            FROM llm_calls WHERE ts >= ?""", (since,)).fetchone())
        t["total"] = t["prompt"] + t["completion"]
        t["tokens_per_sec"] = round(t["timed_completion"] / (t["timed_ms"] / 1000), 1) if t["timed_ms"] else None
        by_model = _rows(c, """SELECT provider, model, local, COUNT(*) AS calls, SUM(prompt_tokens) AS prompt, SUM(completion_tokens) AS completion,
            COALESCE(SUM(cost_usd),0) AS cost, SUM(CASE WHEN ms > 200 AND completion_tokens >= 16 THEN completion_tokens END) AS tc, SUM(CASE WHEN ms > 200 AND completion_tokens >= 16 THEN ms END) AS tm,
            AVG(ms) AS avg_ms FROM llm_calls WHERE ts >= ? GROUP BY provider, model ORDER BY (SUM(prompt_tokens)+SUM(completion_tokens)) DESC""", (since,))
        for m in by_model:
            m["tokens_per_sec"] = round(m["tc"] / (m["tm"] / 1000), 1) if m.get("tm") else None
            m.pop("tc", None), m.pop("tm", None)
        by_purpose = _rows(c, """SELECT purpose, COUNT(*) AS calls, SUM(prompt_tokens)+SUM(completion_tokens) AS tokens, COALESCE(SUM(cost_usd),0) AS cost
            FROM llm_calls WHERE ts >= ? GROUP BY purpose ORDER BY tokens DESC""", (since,))
        for p in by_purpose:
            p["label"] = LABELS.get(p["purpose"], p["purpose"].replace("_", " ").capitalize())
        by_job = _rows(c, """SELECT origin, COUNT(*) AS calls, SUM(prompt_tokens)+SUM(completion_tokens) AS tokens, COALESCE(SUM(cost_usd),0) AS cost,
            COUNT(DISTINCT agent) AS agents, MAX(ts) AS at FROM llm_calls WHERE ts >= ? AND origin != '' GROUP BY origin ORDER BY tokens DESC LIMIT 12""", (since,))
        by_agent = _rows(c, """SELECT CASE WHEN agent = 'main' THEN 'main' ELSE 'subagents' END AS who, COUNT(*) AS calls,
            SUM(prompt_tokens)+SUM(completion_tokens) AS tokens FROM llm_calls WHERE ts >= ? GROUP BY who""", (since,))
        days = 14 if rng in ("today", "7d") else (30 if rng == "30d" else 90)
        start = (dt.datetime.now() - dt.timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        daily = _rows(c, """SELECT date(ts, 'unixepoch', 'localtime') AS day, SUM(CASE WHEN local=1 THEN prompt_tokens+completion_tokens ELSE 0 END) AS local,
            SUM(CASE WHEN local=0 THEN prompt_tokens+completion_tokens ELSE 0 END) AS cloud, COALESCE(SUM(cost_usd),0) AS cost, COUNT(*) AS calls
            FROM llm_calls WHERE ts >= ? GROUP BY day ORDER BY day""", (start.timestamp(),))
        hourly = []
        if rng == "today":
            hourly = _rows(c, """SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) AS hour, SUM(prompt_tokens+completion_tokens) AS tokens
                FROM llm_calls WHERE ts >= ? GROUP BY hour ORDER BY hour""", (since,))
        recent = _rows(c, "SELECT * FROM llm_calls WHERE ts >= ? ORDER BY ts DESC LIMIT 60", (since,))
    have = {d["day"]: d for d in daily}
    series = []
    for i in range(days):
        day = (start + dt.timedelta(days=i)).strftime("%Y-%m-%d")
        series.append(have.get(day) or {"day": day, "local": 0, "cloud": 0, "cost": 0, "calls": 0})
    for r in recent:
        r["tokens_per_sec"] = _tps(r)
        r["label"] = LABELS.get(r["purpose"], r["purpose"].replace("_", " ").capitalize())
    _titles(by_job)
    _titles(recent)
    budget = None
    try:
        from app.services import llm_routing
        s = llm_routing.settings()
        budget = {"monthly_usd": s.get("monthly_budget_usd") or s.get("budget_usd"), "month_spend": round(llm_routing.month_spend(), 4)}
    except Exception:
        pass
    return {"range": rng, "totals": t, "by_model": by_model, "by_purpose": by_purpose, "by_job": by_job, "by_agent": by_agent,
            "daily": series, "hourly": hourly, "recent": recent, "budget": budget}


def _titles(rows: List[Dict[str, Any]]) -> None:
    """A job's name instead of "task:abc123" / "chat:sess_…"."""
    tasks, chats = {}, {}
    try:
        from app.services.task_queue import task_queue
        tasks = {t["id"]: t.get("title") for t in task_queue.list(limit=300)}
    except Exception:
        pass
    try:
        from app.services import storage
        chats = {s["id"]: s.get("title") for s in storage.list_sessions()}
    except Exception:
        pass
    for r in rows:
        o = r.get("origin") or ""
        r["job"] = (tasks.get(o[5:]) if o.startswith("task:") else chats.get(o[5:]) if o.startswith("chat:") else
                    "On its own" if o == "xod (on its own)" else o) or ("A task" if o.startswith("task:") else "A chat" if o.startswith("chat:") else "")


def for_origin(origin: str) -> Dict[str, Any]:
    """One job's model use, for its sheet on the Activity tab."""
    with _conn() as c:
        r = dict(c.execute("""SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens),0) AS prompt, COALESCE(SUM(completion_tokens),0) AS completion,
            COALESCE(SUM(cost_usd),0) AS cost, COALESCE(SUM(ms),0) AS ms FROM llm_calls WHERE origin = ?""", (origin,)).fetchone())
        r["models"] = [x[0] for x in c.execute("SELECT DISTINCT model FROM llm_calls WHERE origin = ?", (origin,))]
    return r
