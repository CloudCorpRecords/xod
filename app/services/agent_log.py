"""
What Xod did, step by step and by which agent, and every approval it asked for, kept for the Agent page.

  steps      every tool call (reading too, unlike the undo log in activity.py), with a plain title ("Read sources.md",
             "Ran npm install chalk"), a one-line detail, whether it worked, the job it belongs to (origin: "chat",
             "task:<id>"…) and which agent did it ("main", "sub1", "sub2"… or an outside agent's name)
  approvals  each "waiting for your OK" (a connected app's change, the safety check, a task's submit), and what you
             decided: allowed, declined, or still waiting
"""
import contextvars
import json
import logging
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.agent_log")
DB = DATA_DIR / "xod.db"
KEEP_DAYS = 30

# Which agent is acting: "main", or "sub1", "sub2"… while a subagent works (services/team.py).
agent = contextvars.ContextVar("xod_agent", default="main")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS agent_steps (
        id TEXT PRIMARY KEY, ts REAL, origin TEXT, agent TEXT, tool TEXT, title TEXT, detail TEXT, ok INTEGER)""")
    c.execute("CREATE INDEX IF NOT EXISTS agent_steps_origin ON agent_steps(origin, ts)")
    c.execute("""CREATE TABLE IF NOT EXISTS agent_approvals (
        id TEXT PRIMARY KEY, ts REAL, kind TEXT, app TEXT, title TEXT, detail TEXT, origin TEXT,
        decision TEXT, decided_at REAL)""")
    return c


# ------------------------------------------------------------------ steps
def _base(p: Any) -> str:
    s = str(p or "").rstrip("/\\")
    return os.path.basename(s) or s


def title_for(tool: str, args: Dict[str, Any], result: Any) -> (str, str):
    """A step as a person would say it, and one line of what came of it."""
    a = args or {}
    r = result if isinstance(result, dict) else {}
    path = a.get("filepath") or a.get("file_path") or a.get("path") or a.get("src") or a.get("folder") or ""
    ok = not (r.get("success") is False or r.get("error") or (r.get("exit_code") not in (None, 0)))
    if tool == "computer_run_command":
        cmd = str(a.get("command") or "")
        out = (r.get("stdout") or r.get("stderr") or "").strip().splitlines()
        return f"Ran {cmd[:70]}", (f"exit {r.get('exit_code')}" + (f" · {out[-1][:120]}" if out else "")) if r else ""
    if tool == "computer_start_job":
        return f"Started {a.get('name') or 'a background job'}", str(a.get("command") or "")[:140]
    if tool == "computer_wait_for":
        return f"Waited for {a.get('condition', 'it').replace('_', ' ')}", str(r.get("detail") or r.get("error") or "")[:140]
    if tool == "computer_read_file":
        return f"Read {_base(path)}", f"{r.get('lines_count') or ''} lines".strip() if ok else str(r.get("error", ""))[:140]
    if tool in ("computer_list_directory", "computer_directory_tree", "fs_list_workspace_files"):
        return f"Looked in {_base(path) or 'a folder'}", ""
    if tool == "computer_find_files":
        n = len(r.get("results") or [])
        return f"Searched files for “{str(a.get('query') or '')[:50]}”", f"{n} found"
    if tool in ("web_search", "tavily_search", "you_search"):
        n = len(r.get("results") or [])
        return f"Searched the web for “{str(a.get('query') or '')[:60]}”", f"{n} results" if n else ""
    if tool == "web_scrape" or tool == "browser_navigate":
        return f"Opened {str(a.get('url') or 'a page')[:80]}", str(r.get("title") or "")[:120]
    if tool == "team_delegate":
        return f"Handed off: {str(a.get('task') or '')[:70]}", str(r.get("summary") or r.get("error") or "")[:160]
    if tool.startswith("mcp__"):
        from app.services.mcp_client import _action, _app_name
        _, server, name = (tool.split("__", 2) + ["", ""])[:3]
        if r.get("needs_approval"):
            return f"Asked to {_action(name)} ({_app_name(server)})", "waiting for your OK"
        return f"Used {name.replace('_', ' ')} ({_app_name(server)})", str(r.get("content") or r.get("error") or "")[:140].replace("\n", " ")
    try:
        from app.services import activity
        t, d = activity.describe(tool, a, result, ok, [])
        return t, d
    except Exception:
        return tool.replace("_", " ").capitalize(), ""


def step(tool: str, args: Dict[str, Any], result: Any) -> None:
    """Records one tool call (from tools.execute_tool). Never fails the call."""
    try:
        from app.services import activity
        t, d = title_for(tool, args, result)
        r = result if isinstance(result, dict) else {}
        ok = not (r.get("success") is False or r.get("error") or (r.get("exit_code") not in (None, 0)))
        if r.get("needs_approval"):
            ok = True
        with _conn() as c:
            c.execute("INSERT INTO agent_steps VALUES (?,?,?,?,?,?,?,?)",
                      (uuid.uuid4().hex[:12], time.time(), activity.origin.get(), agent.get(), tool, t[:200], (d or "")[:300], int(ok)))
    except Exception as e:
        logger.debug(f"step not recorded: {e}")


def steps_for(origin: str) -> Dict[str, List[Dict[str, Any]]]:
    """A job's steps, by agent: {"main": [...], "sub1": [...]}, oldest first."""
    with _conn() as c:
        rows = c.execute("SELECT * FROM agent_steps WHERE origin = ? ORDER BY ts", (origin,)).fetchall()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["agent"] or "main", []).append({"ts": r["ts"], "tool": r["tool"], "title": r["title"],
                                                          "detail": r["detail"], "ok": bool(r["ok"])})
    return out


def agents_in(origin: str) -> List[str]:
    with _conn() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT agent FROM agent_steps WHERE origin = ?", (origin,))]


def prune() -> None:
    with _conn() as c:
        c.execute("DELETE FROM agent_steps WHERE ts < ?", (time.time() - KEEP_DAYS * 86400,))
        c.execute("DELETE FROM agent_approvals WHERE ts < ?", (time.time() - KEEP_DAYS * 86400,))


# ------------------------------------------------------------------ approvals
def approval_opened(pid: str, kind: str, app: str, title: str, detail: str = "") -> None:
    try:
        from app.services import activity
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO agent_approvals VALUES (?,?,?,?,?,?,?,?,?)",
                      (pid, time.time(), kind, app[:80], title[:200], (detail or "")[:400], activity.origin.get(), "waiting", None))
    except Exception as e:
        logger.debug(f"approval not recorded: {e}")


def approval_decided(pid: str, decision: str) -> None:
    try:
        with _conn() as c:
            c.execute("UPDATE agent_approvals SET decision = ?, decided_at = ? WHERE id = ? AND decision = 'waiting'",
                      (decision, time.time(), pid))
    except Exception as e:
        logger.debug(f"approval decision not recorded: {e}")


def approvals(limit: int = 100) -> List[Dict[str, Any]]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM agent_approvals ORDER BY ts DESC LIMIT ?", (limit,))]
