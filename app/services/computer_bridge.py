"""
Computer-control bridge (AGE-85): registers Xod's own computer-control MCP
server with the harness's MCP hub, in the AGE-84 fleet bridge pattern.

The server (app/computer_control_mcp.py) exposes look, screenshot, click,
type, press, scroll, hover, drag and allowlisted app launch behind the adapter interface in
services/computer_control.py. The hub already knows how to run stdio MCP
servers, offer their tools as mcp__computer-control__<tool>, hold calls for
approval, and run the approved call: registration is the missing piece, and
this module is only that.

Default trust is ask because a signed-in page can contain private information.
Explicit read_only trust permits only look through the verified built-in
launcher; mutations and screenshots still require approval, even with always.
Nobody raises trust silently. Existing connection settings are preserved.

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
    server ships with Xod. Existing same-name configuration is preserved.
    """
    from app.services import mcp_client
    result = mcp_client.add_servers({"mcpServers": {SERVER_NAME: server_spec(trust)}}, overwrite=False)
    registered = result.get("added") or []
    preserved = result.get("preserved") or []
    effective_trust = mcp_client.load_config().get(SERVER_NAME, {}).get("trust", "ask")
    logger.info("computer bridge: registered %s, preserved %s", registered, preserved)
    return {"success": bool(registered or preserved), "registered": registered, "preserved": preserved,
            "server": SERVER_NAME, "trust": effective_trust,
            "tools": ["computer_look", "computer_screenshot", "computer_click",
                       "computer_type", "computer_press", "computer_scroll",
                       "computer_hover", "computer_drag", "computer_open_app"]}


def verified_look(spec, tool):
    """Operation-scoped eligibility, never inferred from a server name/hint.

    The person must still explicitly choose read_only trust for this built-in
    launcher. Its configured bops driver is part of that local trust decision.
    """
    from app.paths import APP_ROOT
    if tool != "computer_look" or not isinstance(spec, dict):
        return False
    env = spec.get("env") or {}
    if not isinstance(env, dict) or set(env) - {ENV_ADAPTER, ENV_BOPS}:
        return False
    if env.get(ENV_ADAPTER, "bops") not in ("bops", "fake"):
        return False
    return (spec.get("command") == "{xod_python}"
            and spec.get("args") == ["-m", "app.computer_control_mcp"]
            and spec.get("cwd") == str(APP_ROOT)
            and not spec.get("url") and not spec.get("secret_env"))
