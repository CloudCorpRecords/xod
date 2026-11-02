"""
Everyday abilities on this Mac, one Allow each (Tools → Abilities): Messages, Location, Calendar, Home, Reminders,
Contacts, Music. Each is a part of the "This Mac" connector (app/connectors/mac.py); Allow switches the connector on and has
macOS ask its own question once ("Xod wants to control Calendar", Location Services), or opens the right page of
System Settings when macOS won't ask (Full Disk Access, for reading Messages).

Checking where each one stands never makes macOS ask anything: Automation is looked up with
AEDeterminePermissionToAutomateTarget (no prompt), Location with its authorization status, Messages by whether its
database can be opened.
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

MAC = sys.platform == "darwin"
CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
FULL_DISK = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
LOCATION_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_LocationServices"
AUTOMATION_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"

ABILITIES: List[Dict[str, Any]] = [
    {"id": "messages", "name": "Messages", "icon": "message-circle", "blurb": "Read and send messages",
     "app": "com.apple.MobileSMS", "probe": 'tell application "Messages" to count accounts',
     "try": "Text Sam that I'm running 10 minutes late"},
    {"id": "location", "name": "Location", "icon": "navigation", "blurb": "Find places near you",
     "try": "Find a coffee shop near me"},
    {"id": "calendar", "name": "Calendar", "icon": "calendar", "blurb": "See your day and add events",
     "app": "com.apple.iCal", "probe": 'tell application "Calendar" to count calendars',
     "try": "What's on my calendar tomorrow?"},
    {"id": "home", "name": "Home", "icon": "house", "blurb": "Control lights, locks and more (through your Shortcuts)",
     "try": "Turn off the living room lights"},
    {"id": "reminders", "name": "Reminders", "icon": "list-checks", "blurb": "Set and check reminders",
     "app": "com.apple.reminders", "probe": 'tell application "Reminders" to count lists',
     "try": "Remind me to call the dentist at 5"},
    {"id": "contacts", "name": "Contacts", "icon": "contact", "blurb": "Find and add contacts",
     "app": "com.apple.AddressBook", "probe": 'tell application "Contacts" to count people',
     "try": "What's Alex's phone number?"},
    {"id": "music", "name": "Music", "icon": "music", "blurb": "Play and control your music",
     "app": "com.apple.Music", "probe": 'tell application "Music" to count playlists',
     "try": "Play some music"},
]
_BY_ID = {a["id"]: a for a in ABILITIES}

# ------------------------------------------------------------------ AppleScript automation targets
# The apps the generic AppleScript tool (computer_run_applescript) commonly tells, beyond the Abilities above.
# They share the same machinery: the no-prompt automation() check, the one-time Allow flow in allow(), and the
# remembered state below. They are not listed on the Abilities screen up front; one appears there once a task
# has been held back for it (see surface_automation_target), so the person has exactly one place to allow it.
_EXTRA_TARGETS: List[Dict[str, Any]] = [
    {"id": "finder", "name": "Finder", "icon": "folder", "blurb": "Work with files and folders",
     "app": "com.apple.finder", "probe": 'tell application "Finder" to count windows',
     "try": "Show my Downloads folder"},
    {"id": "safari", "name": "Safari", "icon": "compass", "blurb": "Control Safari windows and tabs",
     "app": "com.apple.Safari", "probe": 'tell application "Safari" to count windows',
     "try": "Open this page in Safari"},
    {"id": "notes", "name": "Notes", "icon": "sticky-note", "blurb": "Read and add notes",
     "app": "com.apple.Notes", "probe": 'tell application "Notes" to count folders',
     "try": "Add this to my notes"},
    {"id": "mail", "name": "Mail", "icon": "mail", "blurb": "Read and send mail in Mail",
     "app": "com.apple.mail", "probe": 'tell application "Mail" to count accounts',
     "try": "Check my Mail inbox"},
    {"id": "terminal", "name": "Terminal", "icon": "terminal", "blurb": "Run commands in Terminal",
     "app": "com.apple.Terminal", "probe": 'tell application "Terminal" to count windows',
     "try": "Run this in Terminal"},
    {"id": "system_events", "name": "System Events", "icon": "cog", "blurb": "Keystrokes and UI scripting",
     "app": "com.apple.systemevents", "probe": 'tell application "System Events" to count processes',
     "try": "Press play for me"},
    {"id": "system_settings", "name": "System Settings", "icon": "settings", "blurb": "Change settings",
     "app": "com.apple.systempreferences", "probe": 'tell application "System Settings" to count panes',
     "try": "Open the Wi-Fi settings"},
]
AUTOMATION_TARGETS: List[Dict[str, Any]] = [a for a in ABILITIES if a.get("app")] + _EXTRA_TARGETS
_TARGET_BY_ID = {a["id"]: a for a in AUTOMATION_TARGETS}
_TARGET_BY_NAME = {a["name"].lower(): a for a in AUTOMATION_TARGETS}
_TARGET_BY_BUNDLE = {a["app"].lower(): a for a in AUTOMATION_TARGETS}
_TELL_RE = re.compile(r"tell\s+(?:application|app)\s+(?:id\s+)?\"([^\"]+)\"", re.I)


def applescript_target(script: str) -> Optional[Dict[str, Any]]:
    """The known automation target a script tells, from its first tell application (or tell app, or
    tell application id "bundle") line, or None. A script with no tell, or one telling an app Xod has
    no bundle id for, has no target the no-prompt check can look up, so it is left alone."""
    m = _TELL_RE.search(script or "")
    if not m:
        return None
    who = m.group(1).strip()
    return _TARGET_BY_NAME.get(who.lower()) or _TARGET_BY_BUNDLE.get(who.lower())


def _guard_remembered() -> Dict[str, str]:
    from app.services.storage import get_setting
    try:
        return json.loads(get_setting("applescript_guard", "") or "{}")
    except ValueError:
        return {}


def _guard_remember(aid: str, state: str) -> None:
    from app.services.storage import set_setting
    r = _guard_remembered()
    r[aid] = state
    set_setting("applescript_guard", json.dumps(r))


def surface_automation_target(target: Dict[str, Any], state: str) -> bool:
    """Surface a held-back target on the Abilities screen, once. The first hold for a target records it
    (status() then lists it, with its Allow) and returns True; later holds for the same target return
    False and change nothing, so a dismissal or a no is not re-surfaced task after task. A later grant
    needs no clearing here: the pre-check runs every time and an allowed state proceeds regardless."""
    if target["id"] in _guard_remembered():
        return False
    _guard_remember(target["id"], state)
    return True


def applescript_block(script: str) -> Optional[Dict[str, Any]]:
    """The permission guard for the generic AppleScript tool: the named result that replaces running
    the script, or None when the script may run. Untargeted scripts (no known tell target) and targets
    macOS cannot report on (unknown: the app is not running, or this is not a Mac) run as before. An
    allowed target runs. An ask or denied target does NOT run: firing it from inside a task is what
    makes macOS show its Automation prompt mid-task, and nothing would remember a dismissal."""
    target = applescript_target(script)
    if not target:
        return None
    st = automation(target["app"])
    if st not in ("ask", "denied"):
        return None
    surfaced = surface_automation_target(target, st)
    return {"success": False, "error_code": "applescript_permission_not_granted",
            "error": (f"AppleScriptPermissionNotGranted: Xod is not allowed to control {target['name']} "
                      f"yet (macOS Automation says {st}), so the script was not run and no prompt was "
                      f"shown. {target['name']} can be allowed once on the Abilities screen; until then, "
                      "do not retry scripts that target it."),
            "target": target["name"], "target_id": target["id"], "state": st, "surfaced": surfaced}


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode(), "big")


def automation(bundle: str) -> str:
    """Whether macOS lets Xod control an app: allowed, denied, ask (not asked yet) or unknown (it isn't running, so
    macOS can't say). Never shows a prompt."""
    if not MAC:
        return "unknown"
    try:
        cs = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")

        class AEDesc(ctypes.Structure):
            _fields_ = [("descriptorType", ctypes.c_uint32), ("dataHandle", ctypes.c_void_p)]
        desc = AEDesc()
        b = bundle.encode()
        if cs.AECreateDesc(_fourcc("bund"), b, len(b), ctypes.byref(desc)) != 0:
            return "unknown"
        cs.AEDeterminePermissionToAutomateTarget.restype = ctypes.c_int32
        st = cs.AEDeterminePermissionToAutomateTarget(ctypes.byref(desc), _fourcc("****"), _fourcc("****"), ctypes.c_bool(False))
        cs.AEDisposeDesc(ctypes.byref(desc))
    except Exception:
        return "unknown"
    return {0: "allowed", -1743: "denied", -1744: "ask"}.get(st, "unknown")


def messages_readable() -> bool:
    import sqlite3
    try:
        c = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True, timeout=2)
        c.execute("SELECT 1 FROM message LIMIT 1")
        c.close()
        return True
    except Exception:
        return False


def location_status() -> str:
    """allowed, denied, ask or unknown, from Location Services (no prompt)."""
    if not MAC:
        return "unknown"
    try:
        import CoreLocation
        if not CoreLocation.CLLocationManager.locationServicesEnabled():
            return "off"
        st = CoreLocation.CLLocationManager.alloc().init().authorizationStatus()
    except Exception:
        return "unknown"
    return {0: "ask", 1: "denied", 2: "denied", 3: "allowed", 4: "allowed"}.get(int(st), "unknown")


def shortcut_names() -> List[str]:
    try:
        r = subprocess.run(["/usr/bin/shortcuts", "list"], capture_output=True, text=True, timeout=20)
        return [x.strip() for x in r.stdout.splitlines() if x.strip()] if r.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        return []


def _remembered() -> Dict[str, str]:
    from app.services.storage import get_setting
    try:
        return json.loads(get_setting("abilities_allowed", "") or "{}")
    except ValueError:
        return {}


def _remember(aid: str, state: str) -> None:
    from app.services.storage import set_setting
    r = _remembered()
    r[aid] = state
    set_setting("abilities_allowed", json.dumps(r))


def _connector_on() -> bool:
    try:
        from app.services import mcp_client
        spec = mcp_client.load_config().get("mac") or {}
        return bool(spec.get("enabled"))
    except Exception:
        return False


def status() -> Dict[str, Any]:
    """Where each ability stands: allowed, ask (one click to allow), denied (System Settings), with a line on why."""
    if not MAC:
        return {"supported": False, "abilities": [], "why": "These run through the Mac's own apps, so they're Mac only for now."}
    on, mem = _connector_on(), _remembered()
    out = []
    for a in ABILITIES:
        st, note = "ask", ""
        if a.get("app"):
            st = automation(a["app"])
            if st == "unknown":
                st = mem.get(a["id"], "ask")
            if a["id"] == "messages" and st == "allowed" and not messages_readable():
                st, note = "partly", "Sending works. Reading needs Full Disk Access for Xod."
        elif a["id"] == "location":
            st = location_status()
            if st == "off":
                st, note = "denied", "Location Services is off on this Mac."
        elif a["id"] == "home":
            n = len(shortcut_names())
            st = mem.get("home", "ask")
            note = (f"{n} shortcut{'s' if n != 1 else ''} on this Mac. Lights, scenes and locks run through shortcuts you make "
                    "with Home actions in the Shortcuts app; one that unlocks something always asks first.") if n else \
                   "Make a shortcut with Home actions in the Shortcuts app (\"Lights off\"), and Xod can run it by name."
        if st == "allowed" and not on:
            st, note = "ask", "One click switches it on."
        out.append({**{k: a[k] for k in ("id", "name", "icon", "blurb", "try")}, "state": st, "note": note})
    # Targets a task was held back for (the AppleScript guard) surface here once, with the same Allow.
    held = _guard_remembered()
    for a in _EXTRA_TARGETS:
        if a["id"] not in held:
            continue
        st = automation(a["app"])
        if st == "unknown":
            st = held[a["id"]] if held[a["id"]] in ("ask", "denied") else "ask"
        note = "A task asked to control this app and was held back instead of prompting mid-task. Allow it here, once."
        if st == "allowed" and not on:
            st, note = "ask", "One click switches it on."
        out.append({**{k: a[k] for k in ("id", "name", "icon", "blurb", "try")}, "state": st, "note": note})
    return {"supported": True, "connector": on, "abilities": out}


def _open(url: str) -> None:
    subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def allow(aid: str) -> Dict[str, Any]:
    """The person's Allow: switches the This Mac connector on, then has macOS ask its question (or opens Settings)."""
    import asyncio
    a = _BY_ID.get(aid) or _TARGET_BY_ID.get(aid)
    if not a:
        return {"success": False, "error": "Unknown ability."}
    if not MAC:
        return {"success": False, "error": "Mac only for now."}
    if not _connector_on():
        from app.services import connectors
        r = await connectors.connect("mac", "http://127.0.0.1/oauth/callback")
        if not r.get("success"):
            return {"success": False, "error": r.get("error") or "Couldn't switch on This Mac."}
    msg = ""
    if a.get("probe"):
        st = automation(a["app"])
        if st == "denied":
            _open(AUTOMATION_PANE)
            return {"success": False, "state": "denied", "error": f"macOS was told no for {a['name']} before. Turn on Xod → "
                    f"{a['name']} in the Automation page that just opened."}
        r = await asyncio.to_thread(subprocess.run, ["/usr/bin/osascript", "-e", a["probe"]], capture_output=True, text=True, timeout=180)
        ok = r.returncode == 0
        _remember(aid, "allowed" if ok else "denied")
        if not ok:
            return {"success": False, "state": "denied", "error": ("macOS didn't allow it. " if "-1743" in r.stderr else "")
                    + (r.stderr.strip()[-200:] or "It didn't work.")}
        if aid == "messages" and not messages_readable():
            _open(FULL_DISK)
            return {"success": True, "state": "partly", "message": "Sending works now. To read your messages too, turn on Xod in "
                    "the Full Disk Access page that just opened (macOS doesn't let apps ask for that one)."}
        msg = f"{a['name']} is allowed."
    elif aid == "location":
        st = await asyncio.to_thread(_ask_location)
        if st != "allowed":
            _open(LOCATION_PANE)
            return {"success": False, "state": st, "error": "macOS didn't turn on Location for Xod. Turn on Xod in the Location "
                    "Services page that just opened. Until then, Xod can still search near a place you name."}
        msg = "Location is allowed."
    elif aid == "home":
        n = len(shortcut_names())
        _remember("home", "allowed")
        msg = (f"Xod can run your {n} shortcuts." if n else "Xod can run your shortcuts. You don't have any yet: make one with "
               "Home actions in the Shortcuts app (\"Lights off\"), then ask Xod to run it.")
    return {"success": True, "state": "allowed", "message": msg}


def _ask_location() -> str:
    import CoreLocation
    from Foundation import NSDate, NSRunLoop
    mgr = CoreLocation.CLLocationManager.alloc().init()
    if mgr.authorizationStatus() == 0:
        mgr.requestWhenInUseAuthorization()
        end = time.time() + 90
        while mgr.authorizationStatus() == 0 and time.time() < end:
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.3))
    return {0: "ask", 1: "denied", 2: "denied", 3: "allowed", 4: "allowed"}.get(int(mgr.authorizationStatus()), "unknown")
