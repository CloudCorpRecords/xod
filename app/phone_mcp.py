"""
Xod's phone MCP server (stdio): reach paired Android phones from Xod's agent,
Crew jobs (Muse, Claude Code) and any other MCP client.

  "phone": {"command": "/path/to/xod/.venv/bin/python", "args": ["-m", "app.phone_mcp"],
            "cwd": "/path/to/xod"}

All the rules live in services/phone_mcp.py: only phones that paired on
their own screen, capabilities switched on in the phone app, sends/pays/
deletes/unlocks ask on the phone, every call logged on both sides. Calls
travel over Agent Mail (signed, sealed, keys pinned); no port is opened.
"""
import os
from typing import Any, Dict

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from app.services import phone_mcp as core

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
ACT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

server = MCPServer(
    name="xod-phone",
    title="Xod Phone",
    instructions=("Tools for the user's paired Android phone(s). phone_list first to see what's paired "
                  "and allowed: capabilities switched off on the phone answer 'not allowed on this phone'. "
                  "Anything that sends, pays, deletes or unlocks asks the owner on the phone itself."),
    version="0.9.4",
)


@server.tool(annotations=READ)
async def phone_list() -> Dict[str, Any]:
    """Paired phones (online/last seen, battery, capability switches), and whether this computer is locked/awake."""
    return core.phone_list()


@server.tool(annotations=ACT)
async def phone_send(address: str, text: str) -> Dict[str, Any]:
    """Send a notification/message to a paired phone's owner. Asks on the phone before sending."""
    return await core.call_tool("phone_send", address=address, text=text)


@server.tool(annotations=ACT)
async def phone_ask(address: str, question: str, options: str = "") -> Dict[str, Any]:
    """Ask the phone's owner a question with buttons (comma-separated options); waits for the answer."""
    return await core.call_tool("phone_ask", address=address, question=question,
                                options=[o.strip() for o in options.split(",") if o.strip()])


@server.tool(annotations=ACT)
async def phone_open_app(address: str, app: str) -> Dict[str, Any]:
    """Open an app on a paired phone by name (needs 'apps' switched on in the phone app)."""
    return await core.call_tool("phone_open_app", address=address, app=app)


@server.tool(annotations=READ)
async def phone_screen_read(address: str) -> Dict[str, Any]:
    """Read the phone's current screen (only once Accessibility control is on in the phone app)."""
    return await core.call_tool("phone_screen_read", address=address)


@server.tool(annotations=ACT)
async def phone_screen_tap(address: str, x: int, y: int) -> Dict[str, Any]:
    """Tap the phone's screen at x,y (Accessibility on; asks on the phone first)."""
    return await core.call_tool("phone_screen_tap", address=address, x=x, y=y)


@server.tool(annotations=READ)
async def phone_location(address: str) -> Dict[str, Any]:
    """The phone's location (only if location is granted in the phone app)."""
    return await core.call_tool("phone_location", address=address)


@server.tool(annotations=ACT)
async def phone_sms_send(address: str, to: str, text: str) -> Dict[str, Any]:
    """Send an SMS from a paired phone (needs 'sms' on; asks on the phone first)."""
    return await core.call_tool("phone_sms_send", address=address, to=to, text=text)


@server.tool(annotations=READ)
async def phone_notifications(address: str) -> Dict[str, Any]:
    """Read/list the phone's notifications (needs 'notifications' on in the phone app)."""
    return await core.call_tool("phone_notifications", address=address)


@server.tool(annotations=READ)
async def phone_calendar(address: str) -> Dict[str, Any]:
    """Read the phone's calendar (needs 'calendar' on in the phone app)."""
    return await core.call_tool("phone_calendar", address=address)


@server.tool(annotations=READ)
async def phone_contacts(address: str, query: str = "") -> Dict[str, Any]:
    """Read/search the phone's contacts (needs 'contacts' on in the phone app)."""
    return await core.call_tool("phone_contacts", address=address, query=query)


@server.tool(annotations=READ)
async def phone_fetch(address: str, url: str) -> Dict[str, Any]:
    """Fetch a URL from the phone's network (needs 'network' on in the phone app)."""
    return await core.call_tool("phone_fetch", address=address, url=url)


@server.tool(annotations=ACT)
async def phone_app_run(address: str, app: str) -> Dict[str, Any]:
    """Run an app on the phone's invisible second display (needs 'background_apps' on; the owner's screen is untouched)."""
    return await core.call_tool("phone_app_run", address=address, app=app)


@server.tool(annotations=READ)
async def phone_files_list(address: str, path: str = "") -> Dict[str, Any]:
    """List files in the phone's Xod folder / picked folders (needs 'files' on in the phone app)."""
    return await core.call_tool("phone_files_list", address=address, path=path)


@server.tool(annotations=READ)
async def phone_file_read(address: str, path: str) -> Dict[str, Any]:
    """Read a file in the phone's Xod folder / picked folders (needs 'files' on in the phone app)."""
    return await core.call_tool("phone_file_read", address=address, path=path)


@server.tool(annotations=ACT)
async def phone_stop(address: str) -> Dict[str, Any]:
    """Stop a running phone action now. Always allowed: it only stops."""
    return await core.call_tool("phone_stop", address=address)


if __name__ == "__main__":
    if os.environ.get("XOD_PHONE_MCP_SELFTEST"):
        print(sorted(core.TOOLS) + ["phone_list"])
    server.run("stdio")
