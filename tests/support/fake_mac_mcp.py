"""A stand-in for the "This Mac" connector (app/connectors/mac.py) with the same tool names and
annotations, keeping reminders and events in a JSON file instead of the real apps. For tests only:
  python tests/support/fake_mac_mcp.py /path/to/state.json"""
import json
import sys
import uuid
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)
STATE = Path(sys.argv[1])
server = MCPServer(name="mac", title="This Mac (test)", instructions="Reminders and Calendar (test copy).", version="0.0.1")


def load():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"reminders": [], "events": []}


def save(s):
    STATE.write_text(json.dumps(s, indent=1))


@server.tool(annotations=READ)
def reminders_list(list_name: str = "", include_completed: bool = False) -> list:
    """Reminders (open ones unless include_completed), each with its id."""
    return [r for r in load()["reminders"] if include_completed or not r["completed"]]


@server.tool(annotations=WRITE)
def reminders_add(title: str, due: str = "", list_name: str = "", notes: str = "") -> str:
    """Adds a reminder, optionally due at a time like 2026-10-14T09:00."""
    s = load()
    s["reminders"].append({"id": "x-apple-reminder://" + uuid.uuid4().hex[:8], "title": title, "due": due, "notes": notes,
                           "list": list_name or "Reminders", "completed": False})
    save(s)
    return f"Added “{title}”."


@server.tool(annotations=WRITE)
def reminders_update(reminder_id: str, title: str = "", due: str = "", notes: str = "", completed: str = "") -> str:
    """Changes a reminder (id from reminders_list): title, due, notes, or completed "true"/"false" to check it off."""
    s = load()
    r = next((x for x in s["reminders"] if x["id"] == reminder_id), None)
    if not r:
        raise ValueError("No reminder with that id.")
    if title: r["title"] = title
    if due: r["due"] = due
    if notes: r["notes"] = notes
    if str(completed).lower() in ("true", "1", "yes"): r["completed"] = True
    if str(completed).lower() in ("false", "0", "no"): r["completed"] = False
    save(s)
    return f"Updated “{r['title']}”."


@server.tool(annotations=DELETE)
def reminders_delete(reminder_id: str) -> str:
    """Deletes a reminder (id from reminders_list)."""
    s = load()
    before = len(s["reminders"])
    s["reminders"] = [x for x in s["reminders"] if x["id"] != reminder_id]
    if len(s["reminders"]) == before:
        raise ValueError("No reminder with that id.")
    save(s)
    return "Deleted it."


@server.tool(annotations=READ)
def calendar_events(days_ahead: int = 7, search: str = "") -> list:
    """Upcoming events with their ids."""
    return [e for e in load()["events"] if not search or search.lower() in e["title"].lower()]


@server.tool(annotations=WRITE)
def calendar_update_event(event_id: str, title: str = "", start: str = "", end: str = "", location: str = "", notes: str = "") -> str:
    """Changes an event (id from calendar_events). Times like 2026-10-14T15:00."""
    s = load()
    e = next((x for x in s["events"] if x["id"] == event_id), None)
    if not e:
        raise ValueError("No event with that id.")
    if title: e["title"] = title
    if start: e["start"] = start
    if end: e["end"] = end
    save(s)
    return f"Updated “{e['title']}”."


if __name__ == "__main__":
    server.run("stdio")
