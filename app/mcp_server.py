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
from typing import Any, Dict, List

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
    version="0.7.12",
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


if __name__ == "__main__":
    server.run("stdio")
