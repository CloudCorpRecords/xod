"""
"This Mac" connector: Calendar, Reminders, Notes and Mail through the Mac's own apps.

It's an MCP server that Xod starts with its own Python (no Node, no accounts). Whatever accounts
you've added to macOS (iCloud, Google, Exchange) come along for free, and nothing leaves the Mac.
macOS asks once per app ("Xod wants to control Calendar"); you can change that later in System
Settings → Privacy & Security → Automation.

Reading is marked read-only. Creating and changing things is not, so Xod asks before doing it (unless
you trust this connector fully). Deleting is marked destructive: Xod always asks first, whatever the
trust setting. Mail is never sent from here: drafts open in Mail for you to send.
Lists return each item's id; changing or deleting something takes that id.

Every value reaches AppleScript as an argument (`on run argv`), never pasted into the script.
"""
import html
import subprocess
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

server = MCPServer(
    name="mac", title="This Mac",
    instructions="Calendar, Reminders, Notes and Mail on this Mac. Read freely; creating or changing asks the user first; "
                 "deleting always asks. To change or delete something, list or search first to get its id.",
    version="0.7.2",
)

SEP, END = "\x1f", "\x1e"   # field and record separators that never appear in normal text


def _osa(script: str, *args: Any, timeout: int = 60) -> str:
    p = subprocess.run(["/usr/bin/osascript", "-", *[str(a) for a in args]], input=script, capture_output=True,
                       text=True, timeout=timeout)
    if p.returncode != 0:
        err = p.stderr.strip()
        if "-1743" in err or "Not authorized" in err:
            raise RuntimeError("macOS hasn't allowed Xod to use this app yet. Allow it in System Settings → "
                               "Privacy & Security → Automation, then try again.")
        if "-600" in err or "isn't running" in err:
            raise RuntimeError("That app isn't available on this Mac.")
        raise RuntimeError(err[-300:] or "AppleScript failed")
    return p.stdout


def _rows(out: str, fields: List[str]) -> List[Dict[str, str]]:
    rows = []
    for rec in out.split(END):
        rec = rec.strip("\n")
        if not rec:
            continue
        vals = rec.split(SEP)
        rows.append({f: (vals[i] if i < len(vals) and vals[i] != "missing value" else "") for i, f in enumerate(fields)})
    return rows


def _when(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise ValueError(f"Use a date like 2026-10-14T15:00, not {value!r}")


CAL_FIND = r'''
on findEvent(eid)
    tell application "Calendar"
        repeat with c in calendars
            set hits to (every event of c whose uid is eid)
            if (count of hits) > 0 then return item 1 of hits
        end repeat
    end tell
    error "That event isn't in Calendar any more."
end findEvent
'''

# AppleScript helpers shared by the scripts below.
LIB = r'''
on mkdate(y, mo, d, h, mi)
    set dt to current date
    set day of dt to 1
    set year of dt to (y as integer)
    set month of dt to (mo as integer)
    set day of dt to (d as integer)
    set hours of dt to (h as integer)
    set minutes of dt to (mi as integer)
    set seconds of dt to 0
    return dt
end mkdate
on iso(dt)
    if dt is missing value then return ""
    return (dt as «class isot» as string)
end iso
on txt(v)
    if v is missing value then return ""
    return v as string
end txt
'''


def _parts(dt: datetime) -> List[int]:
    return [dt.year, dt.month, dt.day, dt.hour, dt.minute]


# ---------------------------------------------------------------- Calendar

@server.tool(annotations=READ)
def calendar_events(days_ahead: int = 7, search: str = "") -> List[Dict[str, str]]:
    """Events from now until `days_ahead` days from now, across all calendars (iCloud, Google…),
    optionally only those whose title contains `search`. Repeating events show their first date."""
    start = datetime.now()
    end = start + timedelta(days=max(0, min(int(days_ahead), 90)))
    script = LIB + r'''
on run argv
    set startD to my mkdate(item 1 of argv, item 2 of argv, item 3 of argv, item 4 of argv, item 5 of argv)
    set endD to my mkdate(item 6 of argv, item 7 of argv, item 8 of argv, item 9 of argv, item 10 of argv)
    set q to item 11 of argv
    set S to ASCII character 31
    set R to ASCII character 30
    set out to ""
    tell application "Calendar"
        repeat with c in calendars
            set cname to name of c
            repeat with e in (every event of c whose start date ≥ startD and start date ≤ endD)
                set t to summary of e
                if q is "" or t contains q then
                    set out to out & cname & S & t & S & my iso(start date of e) & S & my iso(end date of e) & S & my txt(location of e) & S & (uid of e) & R
                end if
            end repeat
        end repeat
    end tell
    return out
end run
'''
    rows = _rows(_osa(script, *_parts(start), *_parts(end), search or ""), ["calendar", "title", "start", "end", "location", "id"])
    return sorted(rows, key=lambda r: r["start"])


@server.tool(annotations=WRITE)
def calendar_create_event(title: str, start: str, end: str = "", calendar: str = "", location: str = "", notes: str = "") -> str:
    """Adds an event. `start`/`end` like 2026-10-14T15:00 (end defaults to an hour later).
    `calendar` is a calendar's name; empty uses the first one you can write to."""
    s = _when(start)
    e = _when(end) if end else s + timedelta(hours=1)
    script = LIB + r'''
on run argv
    set startD to my mkdate(item 1 of argv, item 2 of argv, item 3 of argv, item 4 of argv, item 5 of argv)
    set endD to my mkdate(item 6 of argv, item 7 of argv, item 8 of argv, item 9 of argv, item 10 of argv)
    set t to item 11 of argv
    set calName to item 12 of argv
    tell application "Calendar"
        if calName is "" then
            set c to first calendar whose writable is true
        else
            set c to first calendar whose name is calName
        end if
        make new event at end of events of c with properties {summary:t, start date:startD, end date:endD, location:(item 13 of argv), description:(item 14 of argv)}
        return name of c
    end tell
end run
'''
    cal = _osa(script, *_parts(s), *_parts(e), title, calendar, location, notes).strip()
    return f"Added “{title}” to {cal}, {s:%a %b %-d %-I:%M %p}–{e:%-I:%M %p}."


@server.tool(annotations=WRITE)
def calendar_update_event(event_id: str, title: str = "", start: str = "", end: str = "", location: str = "", notes: str = "") -> str:
    """Changes an event (id from calendar_events). Only the fields you pass change; moving `start`
    without `end` keeps the event's length."""
    s = _when(start) if start else None
    e = _when(end) if end else None
    script = LIB + CAL_FIND + r'''
on run argv
    set ev to my findEvent(item 1 of argv)
    tell application "Calendar"
        set oldLen to (end date of ev) - (start date of ev)
        if (item 2 of argv) is not "" then set summary of ev to (item 2 of argv)
        if (item 3 of argv) is "1" then
            set newStart to my mkdate(item 5 of argv, item 6 of argv, item 7 of argv, item 8 of argv, item 9 of argv)
            set start date of ev to newStart
            if (item 4 of argv) is not "1" then set end date of ev to newStart + oldLen
        end if
        if (item 4 of argv) is "1" then set end date of ev to my mkdate(item 10 of argv, item 11 of argv, item 12 of argv, item 13 of argv, item 14 of argv)
        if (item 15 of argv) is not "" then set location of ev to (item 15 of argv)
        if (item 16 of argv) is not "" then set description of ev to (item 16 of argv)
        return (summary of ev) & (ASCII character 31) & my iso(start date of ev)
    end tell
end run
'''
    out = _osa(script, event_id, title, "1" if s else "0", "1" if e else "0", *(_parts(s) if s else [0] * 5),
               *(_parts(e) if e else [0] * 5), location, notes).strip().split(SEP)
    return f"Updated “{out[0]}”" + (f", now starting {out[1]}." if len(out) > 1 and out[1] else ".")


@server.tool(annotations=DELETE)
def calendar_delete_event(event_id: str) -> str:
    """Deletes an event (id from calendar_events). Calendar has no undo for this."""
    script = LIB + CAL_FIND + r'''
on run argv
    set ev to my findEvent(item 1 of argv)
    tell application "Calendar"
        set t to summary of ev
        delete ev
        return t
    end tell
end run
'''
    return f"Deleted the event “{_osa(script, event_id).strip()}”."


# ---------------------------------------------------------------- Reminders

@server.tool(annotations=READ)
def reminders_list(list_name: str = "", include_completed: bool = False) -> List[Dict[str, str]]:
    """Reminders (open ones unless include_completed), from one list or all lists."""
    script = LIB + r'''
on run argv
    set ln to item 1 of argv
    set wantDone to (item 2 of argv) is "1"
    set S to ASCII character 31
    set R to ASCII character 30
    set out to ""
    tell application "Reminders"
        if ln is "" then
            set ls to lists
        else
            set ls to {list ln}
        end if
        repeat with l in ls
            if wantDone then
                set rs to reminders of l
            else
                set rs to (reminders of l whose completed is false)
            end if
            repeat with r in rs
                set out to out & (name of l) & S & (name of r) & S & my iso(due date of r) & S & (completed of r as string) & S & my txt(body of r) & S & (id of r) & R
            end repeat
        end repeat
    end tell
    return out
end run
'''
    return _rows(_osa(script, list_name or "", "1" if include_completed else "0"), ["list", "title", "due", "completed", "notes", "id"])


@server.tool(annotations=WRITE)
def reminders_add(title: str, due: str = "", list_name: str = "", notes: str = "") -> str:
    """Adds a reminder, optionally due at a time like 2026-10-14T09:00, to a list (default list if empty)."""
    d = _when(due) if due else None
    script = LIB + r'''
on run argv
    set t to item 1 of argv
    set ln to item 2 of argv
    set b to item 3 of argv
    tell application "Reminders"
        if ln is "" then
            set l to default list
        else
            set l to list ln
        end if
        set r to make new reminder at end of reminders of l with properties {name:t, body:b}
        if (item 4 of argv) is "1" then
            set due date of r to my mkdate(item 5 of argv, item 6 of argv, item 7 of argv, item 8 of argv, item 9 of argv)
        end if
        return name of l
    end tell
end run
'''
    lst = _osa(script, title, list_name, notes, "1" if d else "0", *(_parts(d) if d else [0, 0, 0, 0, 0])).strip()
    return f"Added “{title}” to {lst}" + (f", due {d:%a %b %-d %-I:%M %p}." if d else ".")


@server.tool(annotations=WRITE)
def reminders_update(reminder_id: str, title: str = "", due: str = "", notes: str = "", completed: str = "") -> str:
    """Changes a reminder (id from reminders_list): new title, due time (2026-10-14T09:00), notes, or
    completed "true"/"false" to check it off or reopen it. Only what you pass changes."""
    d = _when(due) if due else None
    script = LIB + r'''
on run argv
    tell application "Reminders"
        set r to reminder id (item 1 of argv)
        if (item 2 of argv) is not "" then set name of r to (item 2 of argv)
        if (item 3 of argv) is not "" then set body of r to (item 3 of argv)
        if (item 4 of argv) is "true" then set completed of r to true
        if (item 4 of argv) is "false" then set completed of r to false
        if (item 5 of argv) is "1" then set due date of r to my mkdate(item 6 of argv, item 7 of argv, item 8 of argv, item 9 of argv, item 10 of argv)
        return name of r
    end tell
end run
'''
    done = str(completed).strip().lower()
    done = "true" if done in ("true", "1", "yes", "done") else "false" if done in ("false", "0", "no") else ""
    name = _osa(script, reminder_id, title, notes, done, "1" if d else "0", *(_parts(d) if d else [0] * 5)).strip()
    return f"Checked off “{name}”." if done == "true" and not (title or notes or d) else f"Updated “{name}”."


@server.tool(annotations=DELETE)
def reminders_delete(reminder_id: str) -> str:
    """Deletes a reminder (id from reminders_list). To finish one, use reminders_update with completed="true"."""
    script = r'''
on run argv
    tell application "Reminders"
        set r to reminder id (item 1 of argv)
        set t to name of r
        delete r
        return t
    end tell
end run
'''
    return f"Deleted the reminder “{_osa(script, reminder_id).strip()}”."


# ---------------------------------------------------------------- Notes

@server.tool(annotations=READ)
def notes_search(search: str, limit: int = 10) -> List[Dict[str, str]]:
    """Notes whose title or text contains `search`, newest first (title, folder, date and a preview)."""
    script = LIB + r'''
on run argv
    set q to item 1 of argv
    set S to ASCII character 31
    set R to ASCII character 30
    set out to ""
    tell application "Notes"
        repeat with n in (notes whose name contains q or plaintext contains q)
            set p to plaintext of n
            if (length of p) > 200 then set p to text 1 thru 200 of p
            set out to out & (id of n) & S & (name of n) & S & (name of container of n) & S & my iso(modification date of n) & S & p & R
        end repeat
    end tell
    return out
end run
'''
    rows = _rows(_osa(script, search), ["id", "title", "folder", "modified", "preview"])
    return sorted(rows, key=lambda r: r["modified"], reverse=True)[:max(1, min(int(limit), 50))]


@server.tool(annotations=READ)
def notes_read(note_id: str) -> str:
    """The full text of a note (ids come from notes_search)."""
    script = r'''
on run argv
    tell application "Notes" to return plaintext of note id (item 1 of argv)
end run
'''
    return _osa(script, note_id)[:20000]


@server.tool(annotations=WRITE)
def notes_create(title: str, text: str, folder: str = "") -> str:
    """Creates a note with this title and plain text, in a folder (default: Notes)."""
    body = f"<h1>{html.escape(title)}</h1>" + "".join(f"<div>{html.escape(line) or '<br>'}</div>" for line in text.splitlines())
    script = r'''
on run argv
    set fname to item 3 of argv
    tell application "Notes"
        if fname is "" then
            set f to default folder of default account
        else
            set f to first folder whose name is fname
        end if
        make new note at f with properties {name:(item 1 of argv), body:(item 2 of argv)}
        return name of f
    end tell
end run
'''
    f = _osa(script, title, body, folder).strip()
    return f"Created the note “{title}” in {f}."


@server.tool(annotations=WRITE)
def notes_update(note_id: str, text: str, mode: str = "append", title: str = "") -> str:
    """Changes a note (id from notes_search): mode "append" adds `text` at the end, "replace" replaces
    the whole note with `title` (or its current title) and `text`."""
    lines = "".join(f"<div>{html.escape(line) or '<br>'}</div>" for line in text.splitlines())
    script = r'''
on run argv
    tell application "Notes"
        set n to note id (item 1 of argv)
        if (item 2 of argv) is "replace" then
            set t to (item 4 of argv)
            if t is "" then set t to name of n
            set body of n to "<h1>" & t & "</h1>" & (item 3 of argv)
        else
            set body of n to (body of n) & (item 3 of argv)
        end if
        return name of n
    end tell
end run
'''
    m = "replace" if str(mode).lower().startswith("rep") else "append"
    name = _osa(script, note_id, m, lines, html.escape(title)).strip()
    return f"{'Rewrote' if m == 'replace' else 'Added to'} the note “{name}”."


@server.tool(annotations=DELETE)
def notes_delete(note_id: str) -> str:
    """Deletes a note (id from notes_search). It goes to Recently Deleted in Notes for 30 days."""
    script = r'''
on run argv
    tell application "Notes"
        set n to note id (item 1 of argv)
        set t to name of n
        delete n
        return t
    end tell
end run
'''
    return f"Deleted the note “{_osa(script, note_id).strip()}” (it's in Recently Deleted for 30 days)."


# ---------------------------------------------------------------- Mail

@server.tool(annotations=READ)
def mail_search(search: str = "", limit: int = 15, unread_only: bool = False) -> List[Dict[str, str]]:
    """Recent messages in your inboxes (all accounts), optionally only those whose subject or sender
    contains `search`, or only unread ones. Looks at the newest 300 messages."""
    script = LIB + r'''
on run argv
    set q to item 1 of argv
    set lim to (item 2 of argv) as integer
    set unreadOnly to (item 3 of argv) is "1"
    set S to ASCII character 31
    set R to ASCII character 30
    set out to ""
    set n to 0
    tell application "Mail"
        set ms to messages of inbox
        set total to count of ms
        if total > 300 then set total to 300
        repeat with i from 1 to total
            set m to item i of ms
            set subj to my txt(subject of m)
            set snd to my txt(sender of m)
            if (q is "" or subj contains q or snd contains q) and (not unreadOnly or (read status of m) is false) then
                set out to out & (id of m) & S & snd & S & subj & S & my iso(date received of m) & S & ((read status of m) as string) & R
                set n to n + 1
                if n ≥ lim then exit repeat
            end if
        end repeat
    end tell
    return out
end run
'''
    return _rows(_osa(script, search, max(1, min(int(limit), 50)), "1" if unread_only else "0", timeout=120),
                 ["id", "from", "subject", "received", "read"])


@server.tool(annotations=READ)
def mail_read(message_id: str) -> Dict[str, str]:
    """One message's sender, subject, date and text (ids come from mail_search)."""
    script = LIB + r'''
on run argv
    set mid to (item 1 of argv) as integer
    set S to ASCII character 31
    tell application "Mail"
        set m to first message of inbox whose id is mid
        return my txt(sender of m) & S & my txt(subject of m) & S & my iso(date received of m) & S & my txt(content of m)
    end tell
end run
'''
    f = _osa(script, message_id).split(SEP)
    keys = ["from", "subject", "received", "text"]
    out = {k: (f[i] if i < len(f) else "") for i, k in enumerate(keys)}
    out["text"] = out["text"][:20000]
    return out


@server.tool(annotations=WRITE)
def mail_draft(to: str, subject: str, text: str) -> str:
    """Opens a new email in Mail, addressed and written, for the user to review and send.
    It is never sent from here."""
    script = r'''
on run argv
    tell application "Mail"
        set m to make new outgoing message with properties {subject:(item 2 of argv), content:(item 3 of argv), visible:true}
        repeat with addr in paragraphs of (item 1 of argv)
            if (length of addr) > 0 then
                tell m to make new to recipient at end of to recipients with properties {address:addr}
            end if
        end repeat
        activate
    end tell
end run
'''
    addresses = "\n".join(a.strip() for a in to.replace(";", ",").split(",") if a.strip())
    _osa(script, addresses, subject, text)
    return f"Opened a draft to {to} in Mail. It hasn't been sent; review it and press Send yourself."


@server.tool(annotations=WRITE)
def mail_mark(message_id: str, read: str = "", flagged: str = "") -> str:
    """Marks a message (id from mail_search) read/unread (`read` "true"/"false") and/or flagged."""
    script = r'''
on run argv
    tell application "Mail"
        set m to first message of inbox whose id is ((item 1 of argv) as integer)
        if (item 2 of argv) is "true" then set read status of m to true
        if (item 2 of argv) is "false" then set read status of m to false
        if (item 3 of argv) is "true" then set flagged status of m to true
        if (item 3 of argv) is "false" then set flagged status of m to false
        return subject of m
    end tell
end run
'''
    norm = lambda v: "true" if str(v).lower() in ("true", "1", "yes") else "false" if str(v).lower() in ("false", "0", "no") else ""
    return f"Updated “{_osa(script, message_id, norm(read), norm(flagged)).strip()}”."


@server.tool(annotations=DELETE)
def mail_delete(message_id: str) -> str:
    """Moves a message (id from mail_search) to the Trash in Mail."""
    script = r'''
on run argv
    tell application "Mail"
        set m to first message of inbox whose id is ((item 1 of argv) as integer)
        set s to subject of m
        delete m
        return s
    end tell
end run
'''
    return f"Moved “{_osa(script, message_id).strip()}” to the Trash in Mail."


if __name__ == "__main__":
    server.run("stdio")
