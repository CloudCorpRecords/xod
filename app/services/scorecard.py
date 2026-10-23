"""
The Scorecard: how Xod is doing, from what already happened. Nothing new is recorded for it; it reads:

  chat turns   services/turn_quality.py's `turns` (Xod's own check after each answer, and what you did next:
               corrected it, asked again, thanked it) and the tools each turn used
  model calls  services/usage.py's `llm_calls`: how many, how long, what they cost
  Crew jobs    services/crew.py: done, failed or stopped, how long, what the agent asked and what you decided
  routing      services/evals.py: the latest replay of requests through the router and planner, and how it moved

Each one over a window (the last 7 days by default), next to the window before it, so a change shows as a change.
"""
import json
import time
from typing import Any, Dict, List, Optional


def _db():
    from app.services import dataset_recorder
    return dataset_recorder._conn()


def _turns(since: float, until: float) -> Dict[str, Any]:
    try:
        c = _db()
        r = c.execute("""SELECT COUNT(*) AS turns, SUM(self_check = 'pass') AS passed, SUM(self_check = 'fail') AS failed,
                         SUM(user_signal = 'correction') AS corrected, SUM(user_signal = 'asked_again') AS asked_again,
                         SUM(user_signal = 'praise') AS praised, SUM(label = 1) AS good, SUM(label = -1) AS bad
                         FROM turns WHERE ts >= ? AND ts < ?""", (since, until)).fetchone()
        out = {k: (r[k] or 0) for k in r.keys()}
        bad = c.execute("""SELECT ts, question, self_check, self_reason, user_signal, tools FROM turns
                           WHERE ts >= ? AND ts < ? AND (label = -1 OR self_check = 'fail' OR user_signal IN ('correction', 'asked_again'))
                           ORDER BY ts DESC LIMIT 8""", (since, until)).fetchall()
        tools = c.execute("SELECT tools FROM turns WHERE ts >= ? AND ts < ?", (since, until)).fetchall()
        c.close()
    except Exception:
        return {"turns": 0}
    judged = out["passed"] + out["failed"]
    out["pass_rate"] = round(out["passed"] / judged, 3) if judged else None
    out["went_wrong"] = [{"at": b["ts"], "question": (b["question"] or "")[:200], "why": (b["self_reason"] or b["user_signal"] or "")[:200],
                          "tools": _names(b["tools"])[:6]} for b in bad]
    count: Dict[str, int] = {}
    for (t,) in tools:
        for n in _names(t):
            count[n] = count.get(n, 0) + 1
    out["top_tools"] = sorted(count.items(), key=lambda x: -x[1])[:10]
    return out


def _names(tools: Any) -> List[str]:
    try:
        v = json.loads(tools) if isinstance(tools, str) else tools
    except ValueError:
        v = str(tools or "").split(",")
    return [str(x.get("name") if isinstance(x, dict) else x).strip() for x in (v or []) if x]


def _calls(since: float, until: float) -> Dict[str, Any]:
    try:
        c = _db()
        r = c.execute("""SELECT COUNT(*) AS calls, AVG(ms) AS avg_ms, SUM(cost_usd) AS cost, SUM(local) AS local_calls,
                         SUM(prompt_tokens + completion_tokens) AS tokens FROM llm_calls WHERE ts >= ? AND ts < ?""",
                      (since, until)).fetchone()
        c.close()
        return {"calls": r["calls"] or 0, "avg_seconds": round((r["avg_ms"] or 0) / 1000, 1), "cost_usd": round(r["cost"] or 0, 4),
                "local_calls": r["local_calls"] or 0, "tokens": r["tokens"] or 0}
    except Exception:
        return {"calls": 0}


def _crew(since: float, until: float) -> Dict[str, Any]:
    try:
        from app.services import crew
        crew._load()
        jobs = [j for j in crew._jobs.values() if since <= j.get("started", 0) < until]
    except Exception:
        return {"jobs": 0}
    by: Dict[str, Dict[str, int]] = {}
    asked = approved = denied = 0
    minutes = []
    for j in jobs:
        a = by.setdefault(j.get("agent") or "?", {})
        a[j.get("status") or "?"] = a.get(j.get("status") or "?", 0) + 1
        for ap in (j.get("approvals") or {}).values():
            asked += 1
            approved += ap.get("state") == "approved"
            denied += ap.get("state") == "denied"
        if j.get("ended") and j.get("started"):
            minutes.append((j["ended"] - j["started"]) / 60)
    done = sum(1 for j in jobs if j.get("status") == "done")
    finished = sum(1 for j in jobs if j.get("status") in ("done", "failed"))
    return {"jobs": len(jobs), "done": done, "failed": sum(1 for j in jobs if j.get("status") == "failed"),
            "success_rate": round(done / finished, 3) if finished else None, "by_agent": by,
            "approvals_asked": asked, "approved": approved, "denied": denied,
            "median_minutes": round(sorted(minutes)[len(minutes) // 2], 1) if minutes else None}


def card(days: int = 7) -> Dict[str, Any]:
    from app.services import evals
    now = time.time()
    span = days * 86400
    suites = []
    for name in evals.suites():
        latest = evals.latest(name)
        suites.append({"suite": name, "about": evals.suite(name).get("title") or name.replace("_", " "),
                       "runs": evals.history(name, n=6),
                       "latest": ({k: v for k, v in latest.items() if k != "results"} |
                                  {"failed": [r for r in latest["results"] if not r["pass"]]}) if latest else None})
    return {"days": days,
            "now": {"turns": _turns(now - span, now), "calls": _calls(now - span, now), "crew": _crew(now - span, now)},
            "before": {"turns": _turns(now - 2 * span, now - span), "calls": _calls(now - 2 * span, now - span),
                       "crew": _crew(now - 2 * span, now - span)},
            "routing": {"suites": suites, "running": evals.status()}}
