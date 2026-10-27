"""
The Muse desktop app (not the `muse` CLI that Crew drives): when you've switched on "Approve everything
Muse and Claude Code ask", Xod watches the app's "Needs review" cards and presses Allow on each one for
you, through macOS Accessibility (the button is pressed directly, so it works whatever window is in front
and never moves your mouse). Every one is logged in the approvals history with what was asked.

The Accessibility tree is walked in-process with pyobjc (ApplicationServices). The old way — driving
System Events through osascript and asking each element for its role and name one Apple Event at a
time — took ~25s a scan on a real Mac, past the watcher's timeout, so it could never approve anything.

Xod needs Accessibility access for this (System Settings → Privacy & Security → Accessibility); without
it the watcher says so in Crew (`status()["accessibility_error"]`), and for every other failure it shows
the actual reason, not the Accessibility advice. Requests it has already seen are logged once, not on
every 4-second scan.
"""
import asyncio
import logging
import subprocess
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xod.muse_app")

SETTING = "muse_auto_approve"
EVERY_SEC = 4.0
SCAN_TIMEOUT = 10.0  # a healthy in-process walk takes well under a second; this only guards a stuck tree

_state: Dict[str, Any] = {"task": None, "warned": False, "last": None, "count": 0, "error": None, "present": set()}


def enabled() -> bool:
    from app.services.storage import get_setting
    return get_setting(SETTING, "off") == "on"


def set_enabled(on: bool) -> Dict[str, Any]:
    from app.services.storage import set_setting
    set_setting(SETTING, "on" if on else "off")
    if on:
        start()
    else:
        _state["error"] = None  # a warning from when it was on must not linger (bug: it stayed after switch-off)
        _state["warned"] = False
    return status()


def status() -> Dict[str, Any]:
    return {"on": enabled(), "approved": _state["count"], "last": _state["last"],
            "accessibility_error": _state.get("error") if enabled() else None}


def _describe_error(e: Exception) -> str:
    """What Crew shows under the switch: the Accessibility fix only when that's really the problem."""
    msg = str(e).strip()
    if "accessibility" in msg.lower():
        return ("Xod needs Accessibility access to press Allow in the Muse app "
                "(System Settings → Privacy & Security → Accessibility).")
    return f"Can't reach the Muse app's approvals: {msg[:200]}" if msg else "Can't reach the Muse app's approvals."


class _AX:
    """The slice of ApplicationServices used here, so tests can stand in a fake tree."""

    def __init__(self):
        from ApplicationServices import (AXIsProcessTrusted, AXUIElementCopyAttributeValue,
                                          AXUIElementCreateApplication, AXUIElementPerformAction,
                                          AXUIElementSetMessagingTimeout)
        self._trusted, self._copy = AXIsProcessTrusted, AXUIElementCopyAttributeValue
        self._create, self._perform = AXUIElementCreateApplication, AXUIElementPerformAction
        self._set_timeout = AXUIElementSetMessagingTimeout

    def trusted(self) -> bool:
        return bool(self._trusted())

    def create_app(self, pid: int):
        app = self._create(pid)
        try:
            self._set_timeout(app, 3.0)  # one stuck element must not hang the whole scan
        except Exception:
            pass
        return app

    def attr(self, el, name):
        err, value = self._copy(el, name, None)
        return value if err == 0 else None

    def perform(self, el, action) -> int:
        return int(self._perform(el, action))


def _ax_api() -> _AX:
    return _AX()


def _muse_pid() -> Optional[int]:
    r = subprocess.run(["pgrep", "-x", "Muse"], capture_output=True, text=True, timeout=5)
    pids = (r.stdout or "").split()
    return int(pids[0]) if pids else None


def _label(ax: _AX, el) -> str:
    for name in ("AXTitle", "AXDescription", "AXValue"):
        value = ax.attr(el, name)
        if value:
            return str(value)
    return ""


def _scan(press: bool) -> Dict[str, Any]:
    """Find each pending approval card (a group named "Approval request for …" with an Allow button),
    read what it asks, and press Allow when press=True. press=False only reads (tests)."""
    ax = _ax_api()
    if not ax.trusted():
        raise PermissionError("Xod needs Accessibility access to press Allow in the Muse app")
    pid = _muse_pid()
    if pid is None:
        return {"running": False, "approved": []}
    deadline = time.time() + SCAN_TIMEOUT
    found: List[Dict[str, Any]] = []

    def children(el):
        return ax.attr(el, "AXChildren") or []

    def card_info(card):
        texts, allow = [], None

        def inner(el, depth):
            nonlocal allow
            if depth > 8:
                return
            role, label = str(ax.attr(el, "AXRole") or ""), _label(ax, el).strip()
            if role == "AXButton" and label.lower() == "allow":
                allow = el
            elif label and role in ("AXStaticText", "AXButton") and label.lower() != "deny":
                texts.append(label)
            for child in children(el):
                inner(child, depth + 1)

        inner(card, 0)
        return allow, " ".join(" ".join(texts).split())

    def walk(el, depth):
        if time.time() > deadline:
            raise TimeoutError(f"Muse app scan timed out after {SCAN_TIMEOUT:.0f}s")
        if depth > 40:
            return
        if str(ax.attr(el, "AXRole") or "") == "AXGroup" and _label(ax, el).lower().startswith("approval request"):
            btn, what = card_info(el)
            if btn is not None:
                pressed = bool(press and ax.perform(btn, "AXPress") == 0)
                found.append({"title": _label(ax, el), "what": what, "pressed": pressed})
            return
        for child in children(el):
            walk(child, depth + 1)

    walk(ax.create_app(pid), 0)
    return {"running": True, "approved": found}


def _log(card: Dict[str, Any]) -> None:
    what = card.get("what") or card.get("title") or "an approval"
    try:
        from app.services import agent_log
        pid = f"museapp-{uuid.uuid4().hex[:12]}"
        agent_log.approval_opened(pid, "muse_app", "Muse app", what[:200], "")
        agent_log.approval_decided(pid, "allowed by Xod" if card.get("pressed") else "not pressed")
    except Exception as e:
        logger.debug(f"muse app approval not logged: {e}")
    _state["last"] = {"what": what, "at": time.time(), "pressed": bool(card.get("pressed"))}
    if card.get("pressed"):
        _state["count"] += 1
    logger.info(f"Muse app approval {'allowed' if card.get('pressed') else 'seen, not pressed'}: {what[:160]}")


async def scan_once() -> List[Dict[str, Any]]:
    """Press Allow on every Muse app card waiting now and log each new one once."""
    res = await asyncio.to_thread(_scan, True)
    cards = res.get("approved") or []
    keys = {(c.get("title") or "", c.get("what") or "") for c in cards}
    for c in cards:
        if (c.get("title") or "", c.get("what") or "") not in _state["present"]:
            _log(c)
    _state["present"] = keys
    _state["error"] = None
    return cards


async def _watch() -> None:
    while True:
        try:
            if enabled():
                await scan_once()
                _state["warned"] = False
        except Exception as e:
            _state["error"] = _describe_error(e)
            if not _state["warned"]:
                logger.warning(f"Can't reach the Muse app's approvals: {e}")
                _state["warned"] = True
        await asyncio.sleep(EVERY_SEC)


def start() -> None:
    if sys.platform != "darwin":
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    t = _state["task"]
    if t is None or t.done():
        _state["task"] = asyncio.create_task(_watch())
