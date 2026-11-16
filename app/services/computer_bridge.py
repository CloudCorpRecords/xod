"""
Computer-control bridge (AGE-85): registers Xod's own computer-control MCP
server with the harness's MCP hub, in the AGE-84 fleet bridge pattern.

The server (app/computer_control_mcp.py) exposes look, screenshot, click,
type and press behind the adapter interface in
services/computer_control.py. The hub already knows how to run stdio MCP
servers, offer their tools as mcp__computer-control__<tool>, hold calls for
approval, and run the approved call: registration is the missing piece, and
this module is only that.

Trust is "ask" for the whole server in v1, reads included: a look at a real
signed-in browser can expose private page content, and click, type and press
act on the person's machine, so nothing here runs without the person's OK
on the approval card. Nobody raises this server's trust silently; the person
can change it themselves in Tools -> Connections, like any other MCP server.

The adapter choice is passed through the server env, never hidden:
XOD_COMPUTER_CONTROL_ADAPTER and XOD_COMPUTER_CONTROL_BOPS are forwarded
when set on the host, and every adapter result names the adapter that ran.
With neither set, the server answers through the fake adapter only where a
test asks for it; on a real machine the bops adapter reports honestly when
no bops CLI is configured.
"""
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("xod.computer_bridge")

SERVER_NAME = "computer-control"
ENV_ADAPTER = "XOD_COMPUTER_CONTROL_ADAPTER"
ENV_BOPS = "XOD_COMPUTER_CONTROL_BOPS"


def server_spec(trust: str = "ask") -> Dict[str, Any]:
    """The mcp_servers.json entry for the computer-control server."""
    from app.paths import APP_ROOT
    env: Dict[str, str] = {}
    for key in (ENV_ADAPTER, ENV_BOPS):
        if os.environ.get(key):
            env[key] = os.environ[key]
    spec: Dict[str, Any] = {"command": "{xod_python}",
                            "args": ["-m", "app.computer_control_mcp"],
                            "cwd": str(APP_ROOT), "trust": trust}
    if env:
        spec["env"] = env
    return spec


def register_computer_control(trust: str = "ask") -> Dict[str, Any]:
    """Registers the computer-control server with the MCP hub's config.

    Idempotent and additive: servers the person added themselves are never
    touched. Unlike the fleet bridge there is nothing to discover: the
    server ships with Xod, so registration always writes exactly one entry.
    """
    from app.services import mcp_client
    result = mcp_client.add_servers({"mcpServers": {SERVER_NAME: server_spec(trust)}})
    registered = result.get("added") or []
    logger.info(f"computer bridge: registered {registered} at trust {trust}")
    return {"success": bool(registered), "registered": registered,
            "server": SERVER_NAME, "trust": trust,
            "tools": ["computer_look", "computer_screenshot", "computer_click",
                      "computer_type", "computer_press"]}
