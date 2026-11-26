"""
Xod as an MCP server, so other AI apps (Claude Desktop, Cursor, Claude Code…) can hand work to it.

This is a small bridge: it talks to the Xod app that's already running (XOD_URL, default
http://127.0.0.1:8000) over its local API, so Xod's own rules still apply. Tasks still pause
for your approval in Xod before anything is sent, paid, posted or deleted, and everything
shows up in Xod's Activity panel.

Add to Claude Desktop (claude_desktop_config.json) or any MCP client:

  "xod": {"command": "/path/to/xod/.venv/bin/python", "args": ["-m", "app.mcp_server"],
          "cwd": "/path/to/xod"}

Xod has to be running (the desktop app or ./run.sh).
"""
import json
import os
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

XOD_URL = os.environ.get("XOD_URL", "http://127.0.0.1:8000").rstrip("/")
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
ACT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

server = MCPServer(
    name="xod",
    title="Xod",
    instructions=("Xod is a private AI agent running on this Mac with its own browser, file access, "
                  "on-device image generation and a task inbox. Use ask_xod for research and questions, "
                  "start_task for longer jobs (it asks the user before sending or paying anything), and "
                  "get_task to check on one."),
    version="1.1.36",
)


async def _api(method: str, path: str, **kw) -> Any:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5)) as c:
            r = await c.request(method, f"{XOD_URL}{path}", **kw)
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        raise RuntimeError("Xod isn't running. Open the Xod app (or run ./run.sh) and try again.")


@server.tool(annotations=READ)
async def ask_xod(question: str) -> str:
    """Ask Xod to research or answer something. It can search the web, read pages, use its memory
    and look at files on this Mac, then answers with sources. Takes from seconds to a few minutes."""
    parts: List[str] = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=5)) as c:
            async with c.stream("POST", f"{XOD_URL}/api/chat", json={"messages": [{"role": "user", "content": question}]}) as r:
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line[6:])
                    except ValueError:
                        continue
                    if ev.get("type") == "content":
                        parts.append(ev.get("content") or "")
                    elif ev.get("type") == "error":
                        return f"Xod hit an error: {ev.get('content') or ev.get('error')}"
    except httpx.ConnectError:
        return "Xod isn't running. Open the Xod app (or run ./run.sh) and try again."
    return "".join(parts).strip() or "Xod finished without a written answer."


@server.tool(annotations=ACT)
async def start_task(request: str) -> Dict[str, Any]:
    """Hand Xod a longer job to do in the background (e.g. 'apply to the junior Python role on
    indeed.com', 'tidy my Downloads folder'). Returns a task id. Xod pauses and asks the user in its
    own window before anything is submitted, sent, paid or deleted."""
    res = await _api("POST", "/api/tasks", json={"request": request})
    t = res.get("task") or {}
    return {"task_id": t.get("id"), "status": t.get("status"), "title": t.get("title")}


@server.tool(annotations=READ)
async def get_task(task_id: str) -> Dict[str, Any]:
    """Status, latest progress and (when finished) the report of a task started with start_task."""
    try:
        data = await _api("GET", f"/api/tasks/{task_id}")
        t = data.get("task") or data
    except Exception:
        data = await _api("GET", "/api/tasks")
        tasks = data.get("tasks", data if isinstance(data, list) else [])
        t = next((x for x in tasks if x.get("id") == task_id), None)
    if not t:
        return {"error": f"No task {task_id}"}
    return {k: t.get(k) for k in ("id", "title", "status", "now", "waiting_kind", "result")}


@server.tool(annotations=READ)
async def search_memory(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Search what Xod remembers about the user and their past research."""
    res = await _api("POST", "/api/memory/search", json={"query": query, "limit": max(1, min(limit, 25))})
    return [{"content": r.get("content", "")[:1000], "category": r.get("category")} for r in res.get("results", [])]


@server.tool(annotations=ACT)
async def generate_image(prompt: str, width: int = 768, height: int = 768) -> Dict[str, Any]:
    """Make an image on this Mac with Xod's on-device models. Returns the file path."""
    res = await _api("POST", "/api/media/image/generate", json={"prompt": prompt, "width": width, "height": height})
    return {k: res.get(k) for k in ("success", "output_path", "error", "model")}


@server.tool(annotations=READ)
async def list_routines() -> List[Dict[str, Any]]:
    """Xod's saved routines (jobs it repeats, by hand or on a schedule)."""
    res = await _api("GET", "/api/routines")
    return [{"id": r["id"], "name": r["name"], "schedule": r["schedule_text"], "enabled": r["enabled"]} for r in res.get("routines", [])]


@server.tool(annotations=ACT)
async def run_routine(routine_id: str) -> Dict[str, Any]:
    """Run one of Xod's saved routines now (see list_routines). Returns the task it started."""
    res = await _api("POST", f"/api/routines/{routine_id}/run")
    t = res.get("task") or {}
    return {"success": res.get("success"), "task_id": t.get("id"), "error": res.get("error")}


@server.tool(annotations=READ)
async def recent_activity(limit: int = 20) -> List[Dict[str, Any]]:
    """What Xod has done recently on this Mac (files changed, pages opened, commands run)."""
    res = await _api("GET", f"/api/activity?limit={max(1, min(limit, 100))}")
    return [{"title": i["title"], "detail": i["detail"], "ok": i["ok"]} for i in res.get("items", [])]


@server.tool(annotations=READ)
async def browser_identity(port: int = 0) -> Dict[str, Any]:
    """Identify which Chrome instance Xod currently holds (sandbox vs user_chrome vs other_agent_chrome)."""
    q = f"?port={port}" if port > 0 else ""
    return await _api("GET", f"/api/browser/identity{q}")


@server.tool(annotations=ACT)
async def browser_adopt(port: int = 0) -> Dict[str, Any]:
    """Safely adopt the user's real signed-in Chrome over CDP (never hijacks another agent or pretends sandbox is user Chrome)."""
    body = {"port": port} if port > 0 else {}
    return await _api("POST", "/api/browser/adopt", json=body)


@server.tool(annotations=ACT)
async def browser_detach() -> Dict[str, Any]:
    """Detach from an adopted user Chrome back to Xod's sandbox without closing or disturbing the user's tabs."""
    return await _api("POST", "/api/browser/detach")


@server.tool(annotations=ACT)
async def browser_tabs(
    action: str = "list",
    target_id: str = "",
    url: str = "about:blank",
    target_index: int = -1,
    target_url_contains: str = "",
    target_title_contains: str = "",
) -> Dict[str, Any]:
    """Control any browser tab (list, new, switch, close, claim, release) by id, index, URL, or title."""
    payload: Dict[str, Any] = {"action": action, "url": url}
    if target_id:
        payload["target_id"] = target_id
    if target_index >= 0:
        payload["target_index"] = target_index
    if target_url_contains:
        payload["target_url_contains"] = target_url_contains
    if target_title_contains:
        payload["target_title_contains"] = target_title_contains
    return await _api("POST", "/api/browser/tabs", json=payload)


@server.tool(annotations=ACT)
async def browser_run(
    steps: List[Dict[str, Any]],
    owner: str = "mcp-steerer",
    stop_on_failure: bool = True,
    max_recoveries: int = 2,
) -> Dict[str, Any]:
    """Run a verified multi-step browser script (navigate, click, type, select, check, hover, drag_and_drop, dialog, extract, recall, assert)."""
    return await _api(
        "POST",
        "/api/browser/run",
        json={
            "steps": steps,
            "owner": owner,
            "stop_on_failure": stop_on_failure,
            "max_recoveries": max_recoveries,
        },
    )


@server.tool(annotations=ACT)
async def browser_sessions(
    action: str = "list",
    session_id: str = "",
    jobs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Manage concurrent multi-browser sessions (list, close, or run parallel jobs across 2+ independent browsers)."""
    if action == "list":
        return await _api("GET", "/api/browser/sessions")
    if action == "close" and session_id:
        return await _api("DELETE", f"/api/browser/sessions/{session_id}")
    if action == "parallel" and jobs:
        return await _api("POST", "/api/browser/sessions/parallel", json={"jobs": jobs})
    return {"success": False, "error": f"Unsupported browser_sessions action: {action}"}


@server.tool(annotations=ACT)
async def browser_find_tab(site_or_url: str, adopt_first: bool = True) -> Dict[str, Any]:
    """Auto-adopt the user's real signed-in Chrome when available and jump straight to an already-open tab for a site (e.g. facebook, x, linkedin, gmail) or open it."""
    return await _api("POST", "/api/browser/find-tab", json={"site_or_url": site_or_url, "adopt_first": adopt_first})


@server.tool(annotations=ACT)
async def browser_steer(instruction: str) -> Dict[str, Any]:
    """Send a live mid-mission steering instruction into a running or paused Xod browser mission (Replit/Muse style)."""
    return await _api("POST", "/api/browser/mission/steer", json={"instruction": instruction, "source": "mcp-steerer"})


if __name__ == "__main__":
    server.run("stdio")
