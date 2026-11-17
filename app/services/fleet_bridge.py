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

Discovery order: explicit folder OR default folder, then PATH, then the exact
published packages installed by this checkout's lockfile. No startup downloads.

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
    None means no usable local or pinned published install was found.
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
        from app.services.fleet_policy import published_spec, PACKAGE_ROOT
        # npm may put this checkout's .bin on PATH. Use the explicit validated
        # launcher rather than treating our published fallback as a foreign shim.
        own_bin = False
        if hit and hit["source"] == "path":
            try:
                own_bin = Path(hit["command"]).resolve() == (PACKAGE_ROOT / tool / "dist" / "cli.js").resolve()
            except OSError:
                pass
        if hit is None or own_bin:
            hit, _ = published_spec(tool)
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

    Idempotent and additive: existing same-name connections are preserved,
    including custom commands, disabled settings, credentials and trust.
    """
    found = find_fleet_tools()
    missing = [tool for tool, hit in found.items() if hit is None]
    from app.services.fleet_policy import published_spec
    errors = {tool: published_spec(tool)[1] for tool in missing}
    for tool, error in errors.items():
        logger.info("fleet bridge: %s unavailable: %s", tool, error)
    specs = {tool: {"command": hit["command"], "args": list(hit["args"]), "trust": trust}
             for tool, hit in found.items() if hit}
    if not specs:
        logger.info("fleet bridge: no fleet tools found on this machine; nothing registered")
        return {"success": False, "registered": [], "missing": missing,
                "errors": errors,
                "error": "No fleet tools found (looked in $XOD_FLEET_TOOLS_DIR, "
                         "~/workspace/fleet-tools, PATH, and pinned published packages). "
                         + " ".join(str(e) for e in errors.values())}
    from app.services import mcp_client
    result = mcp_client.add_servers({"mcpServers": specs}, overwrite=False)
    registered = result.get("added") or []
    preserved = result.get("preserved") or []
    logger.info(f"fleet bridge: registered {registered}; missing {missing}")
    return {"success": bool(registered or preserved), "registered": registered,
            "preserved": preserved, "missing": missing, "errors": errors,
            "sources": {tool: found[tool]["source"] for tool in registered}}
