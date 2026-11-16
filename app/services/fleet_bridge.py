"""
Fleet bridge (AGE-84): Tree's published fleet gate tools, offered to the harness.

The five gate tools Tree gates every PR with (cidiff, reprocheck, testdelta, skillcheck,
contextbudget) are published npm packages (@reneturcios/*). Each one is a CLI, and each one
also speaks MCP over stdio (`<tool> mcp`). The harness already knows how to run outside MCP
servers (services/mcp_client.py): they are listed in <data>/mcp_servers.json, their tools are
offered to the model as mcp__<server>__<tool>, and execute_tool routes those names to the hub.

What was missing is discovery and registration: nothing in Xod knew the fleet tools exist,
where they live on this machine, or that they have an MCP mode. This module is that piece:

  find_fleet_tools()      where each CLI actually is (honest: a tool that is not found
                          is reported missing, never faked)
  fleet_server_specs()    the mcp_servers.json entries for the tools that were found
  register_fleet_tools()  writes those entries through mcp_client.add_servers, so the
                          hub connects them like any other outside server

Discovery order per tool: $XOD_FLEET_TOOLS_DIR when set (a fleet-tools install folder,
or a folder holding the shims directly; setting it replaces the default folder), then
~/workspace/fleet-tools (Tree's install), then PATH.

Trust: entries are registered with trust "ask", Xod's default for outside servers, so a
call waits for the person's OK on the approval card. That matters most for reprocheck,
which runs test commands inside a repo; this bridge never silently auto-approves it.
Whoever runs the machine can raise a server's trust in Tools -> Connections, like any
other MCP server.

Scope: the five gate tools only. The rest of the fleet (1,000+ published servers) is
future work; each additional tool is the same registration once it is installed locally.
"""
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("xod.fleet_bridge")

FLEET_TOOLS = ("cidiff", "reprocheck", "testdelta", "skillcheck", "contextbudget")
ENV_DIR = "XOD_FLEET_TOOLS_DIR"
DEFAULT_DIR = Path.home() / "workspace" / "fleet-tools"


def _shim_in(folder: Path, tool: str) -> Optional[Path]:
    """The executable for `tool` inside one candidate folder, or None.

    A fleet-tools install keeps the shims under node_modules/.bin; a folder of
    bare shims (or a checkout's dist) keeps them at the top.
    """
    for candidate in (folder / "node_modules" / ".bin" / tool, folder / tool):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def find_fleet_tools() -> Dict[str, Optional[Dict[str, Any]]]:
    """Locates each fleet CLI on this machine.

    Returns {tool: {"command": ..., "args": ["mcp"], "source": ...} or None}.
    None means not found here; the caller reports it missing rather than guessing.
    """
    override = os.environ.get(ENV_DIR)
    if override:
        folders = [("env", Path(override).expanduser())]
    else:
        folders = [("fleet-tools", DEFAULT_DIR)]
    found: Dict[str, Optional[Dict[str, Any]]] = {}
    for tool in FLEET_TOOLS:
        hit: Optional[Dict[str, Any]] = None
        for source, folder in folders:
            shim = _shim_in(folder, tool)
            if shim:
                hit = {"command": str(shim), "args": ["mcp"], "source": source}
                break
        if hit is None:
            on_path = shutil.which(tool)
            if on_path:
                hit = {"command": on_path, "args": ["mcp"], "source": "path"}
        found[tool] = hit
    return found


def fleet_server_specs(trust: str = "ask") -> Dict[str, Dict[str, Any]]:
    """mcp_servers.json entries (Claude-Desktop shape) for the tools that were found."""
    specs: Dict[str, Dict[str, Any]] = {}
    for tool, hit in find_fleet_tools().items():
        if hit:
            specs[tool] = {"command": hit["command"], "args": list(hit["args"]), "trust": trust}
    return specs


def register_fleet_tools(trust: str = "ask") -> Dict[str, Any]:
    """Registers every found fleet tool with the MCP hub's config.

    Idempotent (add_servers overwrites a same-name entry) and additive: servers the
    person added themselves are never touched. When nothing is found, nothing is
    written and the report says so.
    """
    found = find_fleet_tools()
    missing = [tool for tool, hit in found.items() if hit is None]
    specs = {tool: {"command": hit["command"], "args": list(hit["args"]), "trust": trust}
             for tool, hit in found.items() if hit}
    if not specs:
        logger.info("fleet bridge: no fleet tools found on this machine; nothing registered")
        return {"success": False, "registered": [], "missing": missing,
                "error": "No fleet tools found (looked in $XOD_FLEET_TOOLS_DIR, "
                         "~/workspace/fleet-tools, and PATH)."}
    from app.services import mcp_client
    result = mcp_client.add_servers({"mcpServers": specs})
    registered = result.get("added") or []
    logger.info(f"fleet bridge: registered {registered}; missing {missing}")
    return {"success": bool(registered), "registered": registered, "missing": missing,
            "sources": {tool: found[tool]["source"] for tool in registered}}
