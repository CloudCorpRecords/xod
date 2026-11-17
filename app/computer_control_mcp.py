"""
Xod computer-control MCP server (stdio): explicit-target observations and actions.

  "computer-control": {"command": "{xod_python}", "args": ["-m", "app.computer_control_mcp"],
                       "cwd": "/path/to/xod", "trust": "ask"}

services/computer_bridge.py registers exactly that spec into the MCP hub at
startup. Default trust is ask. Explicit read_only trust permits only look
through this verified launcher. Actions and desktop capture remain gated,
including at always trust. An annotation alone never grants permission.

The work itself lives behind the adapter interface in
services/computer_control.py: FakeAdapter proves the wiring with no device,
BopsAdapter shells to a configured bops CLI for a real signed-in device
browser (unproven on a real device from the Linux build sandbox).
"""
import json
from typing import Any, Dict, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import StrictBool, StrictInt

from app.services import computer_control

ACT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

server = MCPServer(
    name="xod-computer-control",
    title="Xod Computer Control",
    instructions=("Computer control for the person's real browser surface: look first "
                  "(elements plus visible text), then click by selector or text, type, "
                  "scroll, hover, drag or press a key. App launch uses configured aliases. "
                  "Actions need approval; explicit read-only trust permits only targeted look."),
    version="0.2.0",
)

_adapter = computer_control.get_adapter()


async def _call(method, *args):
    # This is a dedicated device process, not the web UI's event loop. Keep
    # adapter invocation serialized: parallel pointer/key gestures can corrupt
    # one another, and cancelling an await cannot stop a worker thread's action.
    result = method(*args)
    if result.get("success") is not True:
        if method.__name__ == "type_text":
            # SDKs log tool errors. Do not put typed text or field read-back in one.
            result = {"success": False, "adapter": result.get("adapter"),
                      "fake": result.get("fake", False),
                      "error": "Typing was not verified. Check the field and driver configuration; inspect before retrying.",
                      "outcome_unknown": result.get("outcome_unknown", False)}
        # MCP isError must agree with the adapter, not report a failed action as success.
        raise ToolError(json.dumps(result))
    return result


@server.tool(annotations=READ)
async def computer_look(target: str = "") -> Dict[str, Any]:
    """Look at a browser tab: its interactive elements, visible text, title and URL."""
    return await _call(_adapter.look, target)


@server.tool(annotations=ACT)
async def computer_screenshot(target: str = "") -> Dict[str, Any]:
    """Take a screenshot of a browser tab, or of the whole desktop when no target is given."""
    return await _call(_adapter.screenshot, target)


@server.tool(annotations=ACT)
async def computer_click(target: str = "", selector: str = "", text: str = "",
                         index: Optional[StrictInt] = None) -> Dict[str, Any]:
    """Click an element in a browser tab, by CSS selector, visible text, or look index."""
    return await _call(_adapter.click, target, selector, text, index)


@server.tool(annotations=ACT)
async def computer_type(target: str = "", selector: str = "", text: str = "",
                        value: str = "", clear: StrictBool = False) -> Dict[str, Any]:
    """Type text into a field in a browser tab (selector or field label), with read-back."""
    return await _call(_adapter.type_text, target, selector, text, value, clear)


@server.tool(annotations=ACT)
async def computer_press(target: str = "", key: str = "", modifiers: str = "") -> Dict[str, Any]:
    """Press a key in a browser tab, for example Enter, Escape, or Tab, with optional modifiers."""
    return await _call(_adapter.press, target, key, modifiers)


@server.tool(annotations=ACT)
async def computer_scroll(target: str = "", delta_x: StrictInt = 0,
                          delta_y: StrictInt = 0) -> Dict[str, Any]:
    """Send a bounded wheel event to the explicit tab's viewport center. Not proof of movement."""
    return await _call(_adapter.scroll, target, delta_x, delta_y)


@server.tool(annotations=ACT)
async def computer_hover(target: str = "", selector: str = "", text: str = "",
                         index: Optional[StrictInt] = None) -> Dict[str, Any]:
    """Hover exactly one visible tab element by selector, exact label, or fresh look index."""
    return await _call(_adapter.hover, target, selector, text, index)


@server.tool(annotations=ACT)
async def computer_drag(target: str = "", start_x: StrictInt = 0, start_y: StrictInt = 0,
                        end_x: StrictInt = 0, end_y: StrictInt = 0,
                        steps: StrictInt = 10) -> Dict[str, Any]:
    """Send a pointer drag within the explicit tab viewport. Coordinates are CSS pixels."""
    return await _call(_adapter.drag, target, start_x, start_y, end_x, end_y, steps)


@server.tool(annotations=ACT)
async def computer_open_app(application: str = "") -> Dict[str, Any]:
    """Request an allowlisted app alias on macOS/Windows, not a shell command or proof of visibility."""
    return await _call(_adapter.open_app, application)


if __name__ == "__main__":
    server.run("stdio")
