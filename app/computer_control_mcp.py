"""
Xod computer-control MCP server (stdio, AGE-85): look, screenshot, click, type, press.

  "computer-control": {"command": "{xod_python}", "args": ["-m", "app.computer_control_mcp"],
                       "cwd": "/path/to/xod", "trust": "ask"}

services/computer_bridge.py registers exactly that spec into the MCP hub at
startup. Trust "ask" is the safety story for v1: the hub holds EVERY call,
the read-only-looking ones included, for the person's OK on the approval
card before this server runs it, so no action here can bypass Xod's
approval flow. The tool annotations deliberately carry no readOnlyHint
auto-pass in v1 either.

The work itself lives behind the adapter interface in
services/computer_control.py: FakeAdapter proves the wiring with no device,
BopsAdapter shells to a configured bops CLI for a real signed-in device
browser (unproven on a real device from the Linux build sandbox).
"""
import os
from typing import Any, Dict, Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from app.services import computer_control

ACT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

server = MCPServer(
    name="xod-computer-control",
    title="Xod Computer Control",
    instructions=("Computer control for the person's real browser surface: look first "
                  "(elements plus visible text), then click by selector or text, type, "
                  "or press a key. Every call waits for the person's approval in Xod."),
    version="0.1.0",
)

_adapter = computer_control.get_adapter()


@server.tool(annotations=ACT)
async def computer_look(target: str = "") -> Dict[str, Any]:
    """Look at a browser tab: its interactive elements, visible text, title and URL."""
    return _adapter.look(target)


@server.tool(annotations=ACT)
async def computer_screenshot(target: str = "") -> Dict[str, Any]:
    """Take a screenshot of a browser tab, or of the whole desktop when no target is given."""
    return _adapter.screenshot(target)


@server.tool(annotations=ACT)
async def computer_click(target: str = "", selector: str = "", text: str = "",
                         index: Optional[int] = None) -> Dict[str, Any]:
    """Click an element in a browser tab, by CSS selector, visible text, or look index."""
    return _adapter.click(target, selector, text, index)


@server.tool(annotations=ACT)
async def computer_type(target: str = "", selector: str = "", text: str = "",
                        value: str = "", clear: bool = False) -> Dict[str, Any]:
    """Type text into a field in a browser tab (selector or field label), with read-back."""
    return _adapter.type_text(target, selector, text, value, clear)


@server.tool(annotations=ACT)
async def computer_press(target: str = "", key: str = "", modifiers: str = "") -> Dict[str, Any]:
    """Press a key in a browser tab, for example Enter, Escape, or Tab, with optional modifiers."""
    return _adapter.press(target, key, modifiers)


if __name__ == "__main__":
    server.run("stdio")
