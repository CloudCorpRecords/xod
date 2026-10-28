"""
"This Mac" connector: Calendar, Reminders, Notes, Mail, Messages, Contacts, places nearby and Home (through your
Shortcuts), with the Mac's own apps and services.

It's an MCP server that Xod starts with its own Python (no Node, no accounts). Whatever accounts
you've added to macOS (iCloud, Google, Exchange) come along for free, and nothing leaves the Mac.
macOS asks once per app ("Xod wants to control Calendar"); you can change that later in System
Settings → Privacy & Security → Automation.

Reading is marked read-only. Creating and changing things is not, so Xod asks before doing it (unless
you trust this connector fully). Deleting is marked destructive: Xod always asks first, whatever the
trust setting. Mail is never sent from here: drafts open in Mail for you to send.
Lists return each item's id; changing or deleting something takes that id.

Every value reaches AppleScript as an argument (`on run argv`), never pasted into the script.

Messages: sending always asks first (it reaches someone else). Reading needs Full Disk Access for Xod: macOS keeps
messages in ~/Library/Messages/chat.db, which is only ever read. Places: Apple Maps' own search (MapKit), around where
you are (Location Services) or a place you name. Home: your Shortcuts (made with Home actions: lights, scenes, locks);
one that unlocks, opens or disarms something always asks first.
"""
import html
import os
import re
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

ALWAYS_ASK = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)   # sending, unlocking

server = MCPServer(
    name="mac", title="This Mac",
    instructions="Calendar, Reminders, Notes, Mail, Messages, Contacts, places nearby and Home (Shortcuts) on this Mac. "
                 "Read freely; creating or changing asks the user first; deleting, sending a message and unlocking always "
                 "ask. To change or delete something, list or search first to get its id.",
    version="0.9.7",
)

SEP, END = "\x1f", "\x1e"   # field and record separators that never appear in normal text



# What goes wrong is said to the model (and so to you): a ToolError's message reaches it, any other error arrives as a
# bare "Error executing tool". Still a ValueError / RuntimeError for code that catches those.
class Refusal(ToolError, ValueError):
    """The request can't be done as asked (who? which one? not found)."""


class Problem(ToolError, RuntimeError):
    """This Mac can't do it right now (a permission, Location Services, an app that didn't answer)."""

def _osa(script: str, *args: Any, timeout: int = 60) -> str:
    p = subprocess.run(["/usr/bin/osascript", "-", *[str(a) for a in args]], input=script, capture_output=True,
                       text=True, timeout=timeout)
    if p.returncode != 0:
        err = p.stderr.strip()
        if "-1743" in err or "Not authorized" in err:
            raise Problem("macOS hasn't allowed Xod to use this app yet. Allow it in System Settings → "
                               "Privacy & Security → Automation, then try again.")
        if "-600" in err or "isn't running" in err:
            raise Problem("That app isn't available on this Mac.")
        raise Problem(err[-300:] or "AppleScript failed")
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
        raise Refusal(f"Use a date like 2026-10-14T15:00, not {value!r}")


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


# ---------------------------------------------------------------- Contacts

_people_cache: Dict[str, Any] = {"at": 0.0, "people": []}

PEOPLE = r'''
on run argv
    set S to ASCII character 31
    set R to ASCII character 30
    set U to ASCII character 29
    set out to ""
    tell application "Contacts"
        set ids to id of every person
        set nms to name of every person
        set orgs to organization of every person
        set phs to value of phones of every person
        set ems to value of emails of every person
    end tell
    repeat with i from 1 to count of ids
        set ph to ""
        repeat with x in item i of phs
            set ph to ph & x & U
        end repeat
        set em to ""
        repeat with x in item i of ems
            set em to em & x & U
        end repeat
        set out to out & (item i of ids) & S & my txt(item i of nms) & S & my txt(item i of orgs) & S & ph & S & em & R
    end repeat
    return out
end run
on txt(v)
    if v is missing value then return ""
    return v as string
end txt
'''

CONTACT_ADD = r'''
on run argv
    tell application "Contacts"
        set p to make new person with properties {first name:(item 1 of argv), last name:(item 2 of argv), organization:(item 5 of argv), note:(item 6 of argv)}
        if (item 3 of argv) is not "" then make new phone at end of phones of p with properties {label:"mobile", value:(item 3 of argv)}
        if (item 4 of argv) is not "" then make new email at end of emails of p with properties {label:"home", value:(item 4 of argv)}
        save
        return name of p
    end tell
end run
'''


def _digits(phone: str) -> str:
    return "".join(ch for ch in phone or "" if ch.isdigit())[-10:]


def _people(fresh: bool = False) -> List[Dict[str, Any]]:
    """Everyone in Contacts (read in one go, kept for 10 minutes): id, name, company, phones, emails."""
    if not fresh and time.time() - _people_cache["at"] < 600 and _people_cache["people"]:
        return _people_cache["people"]
    rows = _rows(_osa(PEOPLE, timeout=120), ["id", "name", "company", "phones", "emails"])
    people = [{"id": r["id"], "name": r["name"], "company": r["company"],
               "phones": [x for x in r["phones"].split("\x1d") if x], "emails": [x for x in r["emails"].split("\x1d") if x]}
              for r in rows]
    _people_cache.update(at=time.time(), people=people)
    return people


def _who(handle: str) -> str:
    """A phone number or email from Messages, as the name in your Contacts when there is one."""
    h = (handle or "").strip()
    try:
        people = _people()
    except Exception:
        return h
    d = _digits(h)
    for p in people:
        if (d and any(_digits(x) == d for x in p["phones"])) or h.lower() in [e.lower() for e in p["emails"]]:
            return p["name"] or h
    return h


@server.tool(annotations=READ)
def contacts_find(search: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Finds people in Contacts by name, company, phone number or email, with their phone numbers and emails: someone's
    number or address, or who a number belongs to."""
    q = (search or "").strip().lower()
    if not q:
        raise Refusal("Who should I look for?")
    d = _digits(q)
    hits = [p for p in _people() if q in (p["name"] or "").lower() or q in (p["company"] or "").lower()
            or (len(d) >= 4 and any(d in _digits(x) for x in p["phones"])) or any(q in e.lower() for e in p["emails"])]
    return hits[:max(1, min(int(limit), 50))]


@server.tool(annotations=WRITE)
def contacts_add(first_name: str, last_name: str = "", phone: str = "", email: str = "", company: str = "", note: str = "") -> str:
    """Adds a person (a new contact) to Contacts, with a phone number and email if given."""
    name = _osa(CONTACT_ADD, first_name, last_name, phone, email, company, note).strip()
    _people_cache["at"] = 0.0
    return f"Added {name} to Contacts."


# ---------------------------------------------------------------- Messages

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"

SEND_CHAT = r'''
on run argv
    tell application "Messages" to send (item 2 of argv) to chat id (item 1 of argv)
    return "chat"
end run
'''

SEND_TO = r'''
on run argv
    tell application "Messages"
        try
            set s to 1st account whose service type = iMessage
            send (item 2 of argv) to participant (item 1 of argv) of s
            return "iMessage"
        on error
            set s to 1st account whose service type = SMS
            send (item 2 of argv) to participant (item 1 of argv) of s
            return "SMS"
        end try
    end tell
end run
'''


def _chat_db():
    try:
        c = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True, timeout=5)
        c.execute("SELECT 1 FROM message LIMIT 1")
        return c
    except Exception:
        raise Problem("Xod can't read your messages yet: macOS keeps them private until you allow it. Turn on Xod in "
                           "System Settings → Privacy & Security → Full Disk Access (Tools → Abilities → Messages opens it), "
                           "then ask again. Sending works without it.")


def _body(text: Any, blob: Any) -> str:
    """A message's text. Newer macOS keeps it only inside attributedBody (an archived NSAttributedString)."""
    if text:
        return str(text)
    b = bytes(blob or b"")
    i = b.find(b"NSString")
    j = b.find(b"+", i) if i >= 0 else -1
    if j < 0 or j + 1 >= len(b):
        return ""
    n, start = b[j + 1], j + 2
    if n == 0x81:
        n, start = int.from_bytes(b[j + 2:j + 4], "little"), j + 4
    elif n == 0x82:
        n, start = int.from_bytes(b[j + 2:j + 5], "little"), j + 5
    return b[start:start + n].decode("utf-8", "replace")


def _msg_time(v: Any) -> str:
    if not v:
        return ""
    secs = v / 1e9 if v > 1e12 else v                  # nanoseconds since 2001 (seconds on old macOS)
    utc = datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=secs)
    return utc.astimezone().strftime("%Y-%m-%d %H:%M")


@server.tool(annotations=READ)
def messages_recent(limit: int = 15, unread_only: bool = False) -> List[Dict[str, Any]]:
    """Your latest conversations in Messages (iMessage and SMS texts): who, the last message, when, and how many are
    unread. Each has a chat id for messages_read and messages_send."""
    c = _chat_db()
    rows = c.execute("""
        SELECT ch.guid, ch.chat_identifier, ch.display_name, m.text, m.attributedBody, m.date, m.is_from_me,
               (SELECT COUNT(*) FROM chat_message_join j2 JOIN message m2 ON m2.ROWID = j2.message_id
                 WHERE j2.chat_id = ch.ROWID AND m2.is_read = 0 AND m2.is_from_me = 0) AS unread
        FROM chat ch JOIN chat_message_join j ON j.chat_id = ch.ROWID JOIN message m ON m.ROWID = j.message_id
        WHERE m.ROWID = (SELECT MAX(message_id) FROM chat_message_join WHERE chat_id = ch.ROWID)
        ORDER BY m.date DESC LIMIT 200""").fetchall()
    c.close()
    out = []
    for guid, ident, display, text, blob, date, mine, unread in rows:
        if unread_only and not unread:
            continue
        out.append({"chat": guid, "with": display or _who(ident), "last": ("You: " if mine else "") + _body(text, blob)[:300],
                    "when": _msg_time(date), "unread": unread})
        if len(out) >= max(1, min(int(limit), 50)):
            break
    return out


@server.tool(annotations=READ)
def messages_read(with_whom: str, limit: int = 20) -> List[Dict[str, str]]:
    """The messages (texts) in one conversation, oldest first: `with_whom` is a chat id from messages_recent, a phone
    number, an email or a name from Contacts."""
    c = _chat_db()
    chats = [r[0] for r in c.execute("SELECT ROWID FROM chat WHERE guid = ? OR chat_identifier = ?", (with_whom, with_whom))]
    if not chats:
        wanted = set()
        if not any(ch.isdigit() for ch in with_whom) and "@" not in with_whom:
            for p in contacts_find(with_whom, 5):
                wanted |= {_digits(x) for x in p["phones"]} | {e.lower() for e in p["emails"]}
        if _digits(with_whom):
            wanted.add(_digits(with_whom))
        wanted.add(with_whom.lower())
        chats = [rowid for rowid, ident in c.execute("SELECT ROWID, chat_identifier FROM chat")
                 if _digits(ident or "") in wanted or (ident or "").lower() in wanted]
    if not chats:
        c.close()
        raise Refusal(f"No conversation with {with_whom} in Messages.")
    q = ",".join("?" * len(chats))
    rows = c.execute(f"""SELECT m.text, m.attributedBody, m.date, m.is_from_me, h.id FROM message m
                         JOIN chat_message_join j ON j.message_id = m.ROWID LEFT JOIN handle h ON h.ROWID = m.handle_id
                         WHERE j.chat_id IN ({q}) ORDER BY m.date DESC LIMIT ?""", (*chats, max(1, min(int(limit), 100)))).fetchall()
    c.close()
    return [{"from": "You" if mine else _who(h or ""), "text": _body(t, b), "when": _msg_time(d)}
            for t, b, d, mine, h in reversed(rows) if _body(t, b)]


@server.tool(annotations=ALWAYS_ASK)
def messages_send(to: str, text: str) -> str:
    """Sends a text message (iMessage, or SMS through your iPhone) with Messages: text someone, message them. `to` is a
    phone number, an email, a chat id from messages_recent, or a name from Contacts. Always waits for the user's OK."""
    if not (text or "").strip():
        raise Refusal("What should the message say?")
    target = (to or "").strip()
    if not target:
        raise Refusal("Who should it go to?")
    if ";" in target and target.split(";")[0] in ("iMessage", "SMS", "RCS", "any"):
        _osa(SEND_CHAT, target, text)
        return "Sent."
    shown = target
    if not any(ch.isdigit() for ch in target) and "@" not in target:
        people = [p for p in contacts_find(target, 5) if p["phones"] or p["emails"]]
        if not people:
            raise Refusal(f"There's no {target} with a phone number or email in Contacts.")
        exact = [p for p in people if (p["name"] or "").lower() == target.lower()]
        if len(people) > 1 and len(exact) != 1:
            raise Refusal("More than one person matches: " + "; ".join(p["name"] for p in people) + ". Which one?")
        p = (exact or people)[0]
        target, shown = (p["phones"] or p["emails"])[0], p["name"]
    how = _osa(SEND_TO, target, text).strip()
    return f"Sent to {shown} by {how}."


# ---------------------------------------------------------------- where you are, and places nearby

def _run_loop(done: Any, seconds: float) -> None:
    from Foundation import NSDate, NSRunLoop
    end = time.time() + seconds
    while not done() and time.time() < end:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))


def _here() -> Dict[str, Any]:
    """This Mac's location from Location Services (Wi-Fi), once you've allowed Xod."""
    import CoreLocation
    from Foundation import NSObject
    got: Dict[str, Any] = {}

    class _Where(NSObject):
        def locationManager_didUpdateLocations_(self, mgr, locs):
            got["loc"] = locs[-1]

        def locationManager_didFailWithError_(self, mgr, err):
            got["err"] = err

    mgr = CoreLocation.CLLocationManager.alloc().init()
    d = _Where.alloc().init()
    mgr.setDelegate_(d)
    if mgr.authorizationStatus() == 0:                  # never asked: macOS asks now
        mgr.requestWhenInUseAuthorization()
        _run_loop(lambda: mgr.authorizationStatus() != 0, 60)
    if mgr.authorizationStatus() in (1, 2):
        raise Problem("Location Services is off for Xod. Turn it on in System Settings → Privacy & Security → "
                           "Location Services, or say where to look (near: \"Austin, TX\").")
    mgr.requestLocation()
    _run_loop(lambda: "loc" in got or "err" in got, 20)
    if "loc" not in got:
        raise Problem("This Mac couldn't tell where it is just now (Location Services needs Wi-Fi). Say where to look instead.")
    c = got["loc"].coordinate()
    out: Dict[str, Any] = {"lat": round(c.latitude, 5), "lon": round(c.longitude, 5), "accuracy_m": int(got["loc"].horizontalAccuracy())}
    marks: Dict[str, Any] = {}
    CoreLocation.CLGeocoder.alloc().init().reverseGeocodeLocation_completionHandler_(got["loc"], lambda m, e: marks.update(m=m))
    _run_loop(lambda: "m" in marks, 10)
    if marks.get("m"):
        pm = marks["m"][0]
        out["place"] = ", ".join(x for x in (pm.locality(), pm.administrativeArea(), pm.country()) if x)
    return out


def _search(query: str, center: Any = None, radius_m: float = 3000, limit: int = 8) -> List[Any]:
    """Apple Maps' own search (MapKit): no account and no key."""
    import MapKit
    req = MapKit.MKLocalSearchRequest.alloc().init()
    req.setNaturalLanguageQuery_(query)
    if center is not None:
        req.setRegion_(MapKit.MKCoordinateRegionMakeWithDistance(center, radius_m * 2, radius_m * 2))
    got: Dict[str, Any] = {}
    MapKit.MKLocalSearch.alloc().initWithRequest_(req).startWithCompletionHandler_(lambda r, e: got.update(r=r, e=e))
    _run_loop(lambda: "r" in got, 20)
    if "r" not in got:
        raise Problem("Apple Maps didn't answer. Check the internet connection.")
    return list(got["r"].mapItems())[:limit] if got["r"] else []


# Apple Maps and Location Services answer on the main thread, and MCP runs plain tools on a worker thread, where
# those answers never arrive: these two are async, so they run on the main thread (each takes a second or two).
@server.tool(annotations=READ)
async def location_here() -> Dict[str, Any]:
    """Where this Mac is now (Location Services): coordinates and the town. macOS asks the first time."""
    return _here()


@server.tool(annotations=READ)
async def places_nearby(what: str = "", near: str = "", radius_km: float = 3, limit: int = 8, query: str = "",
                        location: str = "", place: str = "", address: str = "", where: str = "") -> List[Dict[str, Any]]:
    """Finds places with Apple Maps (coffee, a pharmacy, gas, restaurants, a store by name…), with each one's address,
    distance, phone and website. `what`: the kind of place. `near`: the place the request names (a neighborhood and
    city, an address: "Union Square, San Francisco"); always pass it when one is named. Leave `near` empty only for
    "near me", which uses this Mac's location."""
    near = near or location or place or address or where      # the names models use for it besides `near`
    try:
        return _places(what, near, radius_km, limit, query)
    except ToolError:
        raise
    except Exception as e:      # whatever else went wrong, said plainly rather than as a bare error
        raise Problem(f"Apple Maps search didn't work: {type(e).__name__}: {e}"[:300])


def _places(what: str, near: str, radius_km: float, limit: int, query: str) -> List[Dict[str, Any]]:
    import CoreLocation
    # Small models often send the whole request as `query` ("coffee shops near Union Square, San Francisco").
    what = (what or query).strip()
    if not near.strip():
        m = re.match(r"(.+?)\s+(?:near|around|in)\s+(.+)$", what, re.I)
        if m and not re.fullmatch(r"(me|here|us)", m.group(2).strip(), re.I):
            what, near = m.group(1).strip(), m.group(2).strip()
    if not what:
        raise Refusal("Say what to look for, e.g. coffee or a pharmacy.")
    if near.strip():
        found = _search(near.strip(), limit=1)
        if not found:
            raise Refusal(f"Apple Maps doesn't know {near}.")
        c = found[0].placemark().coordinate()
        where = near.strip()
    else:
        h = _here()
        c = CoreLocation.CLLocationCoordinate2DMake(h["lat"], h["lon"])
        where = h.get("place") or "you"
    origin = CoreLocation.CLLocation.alloc().initWithLatitude_longitude_(c.latitude, c.longitude)
    out = []
    for it in _search(what, c, max(0.3, min(float(radius_km), 50)) * 1000, max(1, min(int(limit), 20))):
        pm, p = it.placemark(), it.placemark().coordinate()
        loc = CoreLocation.CLLocation.alloc().initWithLatitude_longitude_(p.latitude, p.longitude)
        out.append({"name": it.name(), "address": pm.title() or "", "km": round(origin.distanceFromLocation_(loc) / 1000, 2),
                    "phone": it.phoneNumber() or "", "website": str(it.url()) if it.url() else "",
                    "maps": f"https://maps.apple.com/?q={quote(it.name() or '')}&ll={p.latitude},{p.longitude}", "near": where})
    return sorted(out, key=lambda x: x["km"])


# ---------------------------------------------------------------- Home (through your Shortcuts)

SHORTCUTS = "/usr/bin/shortcuts"
SECURITY_RE = re.compile(r"\b(unlock|disarm|garage|gate|open (?:the )?door|alarm off|security off)\b", re.I)


def _shortcuts(*args: str, timeout: int = 60, input_text: str = "") -> str:
    cmd = [SHORTCUTS, *args]
    path = None
    if input_text:
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write(input_text)
        cmd += ["--input-path", path]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    finally:
        if path:
            os.unlink(path)
    if p.returncode != 0:
        raise Problem((p.stderr or p.stdout).strip()[-300:] or "Shortcuts didn't run it.")
    return p.stdout


@server.tool(annotations=READ)
def shortcuts_list(search: str = "") -> List[str]:
    """Your Shortcuts, by name. Smart home control (lights, scenes, thermostat, plugs, locks) runs through these: a
    shortcut made in the Shortcuts app with Home actions ("Lights off", "Movie time", "Lock front door")."""
    names = [x.strip() for x in _shortcuts("list").splitlines() if x.strip()]
    words = [w for w in (search or "").lower().split() if len(w) > 2]
    return [n for n in names if not words or any(w in n.lower() for w in words)]


@server.tool(annotations=WRITE)
def shortcuts_run(name: str, input_text: str = "") -> str:
    """Runs one of your Shortcuts by name: turn the lights on or off, a scene, a plug, the thermostat, lock a door…
    (shortcuts_list shows them). One that unlocks, opens or disarms something needs home_security_shortcut instead."""
    if SECURITY_RE.search(name or ""):
        raise Refusal("That one unlocks, opens or disarms something: use home_security_shortcut, which always asks first.")
    names = shortcuts_list()
    if name not in names:
        raise Refusal(f"There's no shortcut called {name!r}. Your shortcuts: " + ", ".join(names[:30]))
    out = _shortcuts("run", name, input_text=input_text, timeout=120).strip()
    return f"Ran “{name}”." + (f" It said: {out[:500]}" if out else "")


@server.tool(annotations=ALWAYS_ASK)
def home_security_shortcut(name: str) -> str:
    """Runs a shortcut that unlocks a door, opens a garage or gate, or disarms an alarm. Always waits for the user's OK."""
    if name not in shortcuts_list():
        raise Refusal(f"There's no shortcut called {name!r}.")
    out = _shortcuts("run", name, timeout=120).strip()
    return f"Ran “{name}”." + (f" It said: {out[:500]}" if out else "")


if __name__ == "__main__":
    server.run("stdio")
