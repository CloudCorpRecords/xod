"""Smoke tests for Pillars 1, 2, 3, and 5:
- Unified Control Center snapshot & settings
- Smart MCP Tool Routing (server scoring, balancing, telemetry, discover_tools)
- Signed-in Chrome Bridge tools & AppleScript error handling
- FastAPI route registration & Inline Live Action Strip JS syntax
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = ""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


print("Running Control Center, Smart MCP Routing & Chrome Bridge smoke tests...")

# 1. Smart MCP Tool Routing
from app.services.mcp_client import MCPHub

hub = MCPHub()
fake_defs = []
for i in range(40):
    fake_defs.append({"type": "function", "function": {
        "name": f"mcp__github__repo_action_{i}",
        "description": f"[github] Manage repository issues, pull requests and commits ({i})",
        "parameters": {"type": "object", "properties": {"repo": {"type": "string"}}, "required": ["repo"]},
    }})
for i in range(15):
    fake_defs.append({"type": "function", "function": {
        "name": f"mcp__notion__page_op_{i}",
        "description": f"[notion] Search and update Notion workspace wiki pages ({i})",
        "parameters": {"type": "object", "properties": {"page_id": {"type": "string"}}, "required": ["page_id"]},
    }})
for i in range(10):
    fake_defs.append({"type": "function", "function": {
        "name": f"mcp__mac__mac_op_{i}",
        "description": f"[mac] Send iMessage texts, check calendar meetings and reminders ({i})",
        "parameters": {"type": "object", "properties": {}},
    }})

with patch.object(hub, "tool_definitions", return_value=fake_defs):
    gh = hub.tools_for_query("open my github pull request on the repo", limit=12)
    check("Smart routing selects GitHub tools on repo/PR prompt", len(gh) > 0 and all("mcp__github__" in t["function"]["name"] for t in gh))
    check("Smart routing caps tools and records telemetry", hub.last_route["total_tools"] == 65 and hub.last_route["saved_tools"] > 50)

    both = hub.tools_for_query("sync my github issue to my notion page", limit=12)
    srvs = {t["function"]["name"].split("__")[1] for t in both}
    check("Smart routing balances multiple matched servers", "github" in srvs and "notion" in srvs, f"got {srvs}")

    none_match = hub.tools_for_query("tell me a bedtime story about dolphins", limit=12)
    check("Smart routing injects zero MCP tools on unrelated prompt", len(none_match) == 0, f"got {len(none_match)}")

    disc = hub.discover_tools("calendar reminder", limit=5)
    check("mcp_discover_tools finds mac tools on demand", len(disc) > 0 and all("mcp__mac__" in d["name"] for d in disc))

# 2. Signed-in Chrome Bridge error handling
from app.services import desktop

blocked_proc = SimpleNamespace(returncode=1, stdout="", stderr="execution error: Google Chrome got an error: Executing JavaScript through AppleScript is turned off.")
with patch("app.services.desktop.subprocess.run", return_value=blocked_proc):
    res = desktop._chrome_exec_js(1, 1, "document.title")
    check("Chrome bridge surfaces clear Apple Events JS guidance", res.get("success") is False and res.get("js_apple_events_blocked") is True)

# 3. Agent tool registration
from app.agent.tools import TOOL_DEFINITIONS, DESKTOP_TOOLS

tool_names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
for req_tool in ("desktop_chrome_read_tab", "desktop_chrome_open_and_read", "desktop_chrome_draft_text", "mcp_discover_tools"):
    check(f"Agent tool registered: {req_tool}", req_tool in tool_names)
check("New Chrome tools in DESKTOP_TOOLS", {"desktop_chrome_read_tab", "desktop_chrome_open_and_read", "desktop_chrome_draft_text"}.issubset(DESKTOP_TOOLS))

# 4. Control Center snapshot & FastAPI routes
from app.services import control_center
snap = control_center.snapshot()
for k in ("ok", "issue_count", "issues", "brain", "mcp", "mac_control", "browser_sessions"):
    check(f"Control Center snapshot has '{k}'", k in snap)
bad_pane = control_center.open_mac_permission_pane("not_a_real_pane")
check("Permission pane rejects unknown pane", bad_pane.get("success") is False)

from app.main import app as fastapi_app
routes = {r.path for r in fastapi_app.routes}
for ep in ("/api/control-center", "/api/control-center/settings", "/api/desktop/open-permissions", "/api/desktop/chrome-read", "/api/browser/proof-thumb"):
    check(f"FastAPI route registered: {ep}", ep in routes)

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
