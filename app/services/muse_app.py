"""
The Muse desktop app (not the `muse` CLI that Crew drives): when you've switched on "Approve everything
Muse and Claude Code ask", Xod watches the app's approval cards and presses the approving button on each
one for you, through macOS Accessibility (pressed directly, so it works whatever window is in front and
never moves your mouse).

Which button, in order: "Always allow for this recipient" (or "…for this site"), then "Send", then "Allow".
Never "Deny" or "Don't send". An always-allow press is one Rene chose: it sends this one and stops future
cards for that recipient/site, and the log says so, so he can undo it in Muse.

Xod doesn't poll every few seconds: macOS tells it the moment Muse's window changes (an Accessibility
observer on Muse's pid), it waits 300ms for the window to settle, then scans — a card should be handled
within a couple of seconds of appearing. A slow scan every 30s is the backstop in case an event is missed.
The observer follows Muse across restarts and stops when the switch is off. The scan itself walks the
Accessibility tree in-process with pyobjc; driving System Events per element took ~25s a scan.

Every card seen is logged once, ever (a hash of the card in the approvals DB survives restarts): its type
("Approval request for Gmail" → Gmail), the button pressed, and for Gmail the From/To/Subject/attachments;
the full card text is in the row's detail. A card it may not press is logged too, with the reason. At 8 AM,
if anything happened, one summary notification and one history entry say what was approved overnight.

Test mode: XOD_MUSE_APP_DRY_RUN=1 makes scans read-only (nothing is ever pressed), so the switch and the
UI can be click-tested without touching real cards.
"""
import asyncio
import hashlib
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("xod.muse_app")

SETTING = "muse_auto_approve"
BACKSTOP_SEC = 30.0
DEBOUNCE_SEC = 0.3
# After a window-change trigger, look three times: cards build in stages, and the buttons (the part
# Xod presses) often arrive a beat after the card itself. Each look is a ~0.25s tree walk with no
# model call and no screenshot, and looks only ever happen on a trigger or the 30s backstop.
BURST_DELAYS = (0.25, 1.0, 2.5)
SCAN_TIMEOUT = 10.0  # a healthy in-process walk takes well under a second; this only guards a stuck tree
REPRESS_AFTER = 4.0   # a pressed card should be gone; one still showing after this is a new showing (or a failed press)
MAX_PRESS_TRIES = 3   # ...and after this many presses of one showing, stop and log it as failed
SUMMARY_HOUR = 8
DRY_RUN = bool(os.environ.get("XOD_MUSE_APP_DRY_RUN"))

_state: Dict[str, Any] = {"task": None, "warned": False, "last": None, "count": 0, "error": None,
                          "present": set(), "pressed_ids": set(), "press_marks": {}, "fail_logged": set(), "scan_lock": None,
                          "observer": None, "observer_pid": None, "debouncer": None,
                          "loop": None, "backstop_at": 0.0}


def enabled() -> bool:
    from app.services.storage import get_setting
    return get_setting(SETTING, "off") == "on"


def set_enabled(on: bool) -> Dict[str, Any]:
    from app.services.storage import get_setting, set_setting
    was = enabled()
    set_setting(SETTING, "on" if on else "off")
    if on and not was:
        set_setting("muse_auto_approve_since", str(time.time()))
    if on:
        start()
    else:
        _stop_observer()
        _state["error"] = None  # a warning from when it was on must not linger
        _state["warned"] = False
    return status()


def status() -> Dict[str, Any]:
    return {"on": enabled(), "approved": _state["count"], "last": _state["last"],
            "accessibility_error": _state.get("error") if enabled() else None,
            "dry_run": DRY_RUN}


def _describe_error(e: Exception) -> str:
    """What Crew shows under the switch: the Accessibility fix only when that's really the problem."""
    msg = str(e).strip()
    if "accessibility" in msg.lower():
        return ("Xod needs Accessibility access to press Allow in the Muse app "
                "(System Settings → Privacy & Security → Accessibility).")
    return f"Can't reach the Muse app's approvals: {msg[:200]}" if msg else "Can't reach the Muse app's approvals."


# ------------------------------------------------------------------ the buttons on a card
def _pick_button(labels: List[str]) -> Optional[str]:
    """The approving button to press, or None. Order: an always-allow that names a recipient or a site,
    then Send, then Allow. 'Always allow' with no named scope is not pressed (fall back to one-time);
    Deny / Don't send and anything negative are never pressed."""
    clean = [str(l).strip() for l in labels if str(l).strip()]
    for l in clean:
        low = l.lower()
        if low.startswith("always allow") and ("recipient" in low or "site" in low):
            return l
    for l in clean:
        if l.lower() == "allow all once":
            return l
    for want in ("send", "allow once", "allow"):
        for l in clean:
            if l.lower() == want:
                return l
    return None


def _card_type(title: str) -> str:
    m = re.match(r"(?i)approval request for\s+(.+)", str(title or "").strip())
    return (m.group(1).strip() if m else "Muse") or "Muse"


_FIELD_NEXT = r"(?=\s+(?:From|To|Cc|Bcc|Subject|Attachments?|Date|Send|Allow|Always|Don\'t send|Deny)\b|$)"


def _field(text: str, name: str) -> str:
    m = re.search(rf"(?i)\b{name}\s*:?\s*(.+?){_FIELD_NEXT}", text or "")
    return " ".join(m.group(1).split()).strip(" <>") if m else ""


def _card_fields(what: str, texts: Optional[List[str]] = None) -> Dict[str, Any]:
    """From/To/Subject/Attachments from the card. Each is its own element on a real card (no colon:
    'To hiring@example.com'), so read the elements first and fall back to the joined text."""
    out: Dict[str, Any] = {"from": "", "to": "", "subject": "", "attachments": []}
    for item in texts or []:
        m = re.match(r"(?i)(from|to|subject|attachments?)\s*:?\s*(.*)", str(item).strip())
        if not m:
            continue
        key, val = m.group(1).lower(), " ".join(m.group(2).split())
        if key.startswith("attachment"):
            out["attachments"] = [a.strip() for a in re.split(r"[,;]", val) if a.strip()]
        else:
            out[key] = val
    if not any(out.values()):
        # Joined-text fallback (one big element): anchor on an email address, so ordinary words
        # ("Tree to upload...") are never mistaken for the To field.
        to_m = re.search(r"(?i)\bto\s*:?\s*<?([\w.+\-]+@[\w\-.]+\.\w+)>?", what or "")
        from_m = re.search(r"(?i)\bfrom\s*:?\s*(.+?)(?=\s+to\s*:?\s*<?[\w.+\-]+@|$)", what or "")
        subj_m = re.search(r"(?i)\bsubject\s*:?\s*(.+?)(?=\s+attachments?\b|\s+Hi\b|$)", what or "")
        atts = _field(what, "Attachments") or _field(what, "Attachment")
        names = [a.strip() for a in re.split(r"[,;]", atts) if a.strip()] if atts else \
            re.findall(r"[\w\-]+\.(?:pdf|docx?|xlsx?|pptx?|csv|txt|png|jpe?g|zip)\b", what or "", flags=re.I)
        out = {"from": " ".join(from_m.group(1).split()) if from_m and to_m else "",
               "to": to_m.group(1) if to_m else "",
               "subject": " ".join(subj_m.group(1).split()) if subj_m and to_m else "",
               "attachments": names if to_m else []}
    return out


# ------------------------------------------------------------------ reading the Muse app (in-process AX)
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


def _pressed_from_db() -> Dict[str, float]:
    """Cards already pressed, per the approvals DB (survives restarts): id -> when."""
    try:
        from app.services import agent_log
        return {r["id"]: (r.get("ts") or 0) for r in agent_log.approvals(500)
                if r.get("kind") == "muse_app" and r.get("decision") == "allowed by Xod"}
    except Exception:
        return {}


def _scan(press: bool, skip_ids: Optional[set] = None) -> Dict[str, Any]:
    """Find each approval card (a group named "Approval request for …"), read what it asks, choose its
    approving button, and press it when press=True (and this isn't a dry run). press=False only reads."""
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
        texts, buttons = [], {}

        def inner(el, depth):
            if depth > 12:
                return
            role, label = str(ax.attr(el, "AXRole") or ""), _label(ax, el).strip()
            if role == "AXButton" and label:
                buttons.setdefault(label, el)
            elif label and role == "AXStaticText":
                texts.append(label)
            for child in children(el):
                inner(child, depth + 1)

        inner(card, 0)
        return buttons, " ".join(" ".join(texts).split()), texts

    def nearby_button(card):
        """Some cards (the side-panel Gmail one) keep their buttons just OUTSIDE the card group,
        in the panel around it. Climb a few parents and look there - never inside another card."""
        el = card
        for _ in range(3):
            parent = ax.attr(el, "AXParent")
            if parent is None:
                return None, None
            btns = {}

            def hunt(x, d):
                if x is not card and str(ax.attr(x, "AXRole") or "") == "AXGroup" \
                        and "approval request" in _label(ax, x).lower():
                    return
                if str(ax.attr(x, "AXRole") or "") == "AXButton":
                    lab = _label(ax, x).strip()
                    if lab:
                        btns.setdefault(lab, x)
                if d < 8:
                    for c in children(x):
                        hunt(c, d + 1)

            hunt(parent, 0)
            chosen = _pick_button(list(btns))
            if chosen is not None:
                return btns[chosen], chosen
            el = parent
        return None, None

    def walk(el, depth):
        if time.time() > deadline:
            raise TimeoutError(f"Muse app scan timed out after {SCAN_TIMEOUT:.0f}s")
        if depth > 40:
            return
        if str(ax.attr(el, "AXRole") or "") == "AXGroup" and "approval request" in _label(ax, el).lower():
            title = _label(ax, el)
            buttons, what, texts = card_info(el)
            chosen = _pick_button(list(buttons))
            if chosen is None:
                btn2, chosen2 = nearby_button(el)
                if btn2 is not None:
                    buttons[chosen2] = btn2
                    chosen = chosen2
            entry = {"title": title, "what": what, "pressed": False, "button": chosen, "reason": "",
                     "type": _card_type(title), **_card_fields(what, texts)}
            if chosen is not None and press and _card_id(entry) in (skip_ids or set()):
                entry["reason"] = "already pressed: not pressing again"
            elif chosen is None:
                labs = [l for l in buttons if l.lower() in ("deny", "don't send", "deny all")]
                others = [l for l in buttons if l.lower() not in ("deny", "don't send", "deny all")]
                if others:
                    entry["reason"] = "no button Xod may press (saw: %s)" % ", ".join(others)
                elif labs == ["Deny"]:
                    entry["reason"] = "only a Deny button"
                elif labs:
                    entry["reason"] = "only negative buttons (%s)" % ", ".join(labs)
                else:
                    entry["reason"] = "no button Xod may press"
            elif press and DRY_RUN:
                entry["reason"] = "dry run: not pressed (test mode)"
            elif press:
                err = ax.perform(buttons[chosen], "AXPress")
                entry["pressed"] = err == 0
                if err != 0:
                    entry["reason"] = f"press failed: AXPress returned {err}"
            found.append(entry)
            return
        for child in children(el):
            walk(child, depth + 1)

    walk(ax.create_app(pid), 0)
    return {"running": True, "approved": found}


# ------------------------------------------------------------------ the record of what Xod did
def _card_id(card: Dict[str, Any]) -> str:
    if card.get("_id"):
        return str(card["_id"])
    full = f"{card.get('type') or _card_type(card.get('title') or '')}\n{card.get('what') or card.get('title') or ''}"
    return "museapp-" + hashlib.sha256(full.encode()).hexdigest()[:16]


def _log(card: Dict[str, Any]) -> bool:
    """One approvals-history row per card, ever: the id is a hash of the card, kept in the DB, so a
    restart doesn't log (or press towards) the same card twice. Returns True if this was new."""
    what = card.get("what") or card.get("title") or "an approval"
    pid = _card_id(card)
    try:
        from app.services import agent_log
        if any(r["id"] == pid and time.time() - (r.get("ts") or 0) < 600
               for r in agent_log.approvals(500) if r.get("kind") == "muse_app"):
            return False
        ctype = card.get("type") or _card_type(card.get("title") or "")
        fields = {**_card_fields(what), **{k: card.get(k) for k in ("from", "to", "subject", "attachments") if card.get(k)}}
        lines = [f"type: {ctype}", f"button: {card.get('button') or '(none)'}"]
        for k in ("from", "to", "subject"):
            if fields.get(k):
                lines.append(f"{k}: {fields[k]}")
        if fields.get("attachments"):
            lines.append("attachments: " + ", ".join(fields["attachments"]))
        if card.get("button") and str(card["button"]).lower().startswith("always allow"):
            who = fields.get("to") or ""
            lines.append(f"standing permission: Always allowed: {who}" if who else "standing permission: always-allowed by that button")
        if card.get("reason"):
            lines.append(f"reason: {card['reason']}")
        lines += ["---", what]
        headline = f"{ctype}: {fields.get('subject') or what[:120]}"
        agent_log.approval_opened(pid, "muse_app", "Muse app", headline, "\n".join(lines))
        agent_log.approval_decided(pid, "allowed by Xod" if card.get("pressed") else f"not pressed: {card.get('reason') or 'not pressed'}")
    except Exception as e:
        logger.debug(f"muse app approval not logged: {e}")
        return False
    _state["last"] = {"what": what, "at": time.time(), "pressed": bool(card.get("pressed")),
                      "button": card.get("button"), "type": card.get("type")}
    if card.get("pressed"):
        _state["count"] += 1
    logger.info(f"Muse app approval {'allowed' if card.get('pressed') else 'seen, not pressed'}: {what[:160]}")
    return True


def build_summary(since_ts: float) -> Dict[str, Any]:
    """What the 8 AM summary says, from the approvals rows since since_ts (pure read; testable)."""
    from app.services import agent_log
    rows = [r for r in agent_log.approvals(500)
            if r.get("kind") == "muse_app" and (r.get("ts") or 0) >= since_ts]
    by_type: Dict[str, int] = {}
    always: List[str] = []
    not_pressed: List[str] = []
    pressed = 0
    for r in rows:
        detail = r.get("detail") or ""
        ctype = next((l[6:].strip() for l in detail.splitlines() if l.startswith("type: ")), "Muse")
        if r.get("decision") == "allowed by Xod":
            pressed += 1
            by_type[ctype] = by_type.get(ctype, 0) + 1
            m = re.search(r"standing permission: Always allowed: (.+)", detail)
            if m and m.group(1).strip() not in always:
                always.append(m.group(1).strip())
        else:
            reason = (r.get("decision") or "not pressed").removeprefix("not pressed: ")
            not_pressed.append(f"{ctype}: {reason}")
    parts = [f"{pressed} card{'s' if pressed != 1 else ''} approved"]
    if by_type:
        parts.append("(" + ", ".join(f"{t} {n}" for t, n in sorted(by_type.items())) + ")")
    text = " ".join(parts) + "."
    if always:
        text += " Always-allowed now: " + ", ".join(always) + " (undo in Muse if that's wrong)."
    if not_pressed:
        text += " Not pressed: " + "; ".join(not_pressed) + "."
    return {"count": pressed, "by_type": by_type, "always_allowed": always,
            "not_pressed": not_pressed, "text": text, "rows": len(rows)}


def _maybe_summary(now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """At/after 8 AM, once a day, if anything happened: one notification + one history entry."""
    from app.services.storage import get_setting, set_setting
    now = now or datetime.now()
    if now.hour < SUMMARY_HOUR:
        return None
    today = now.strftime("%Y-%m-%d")
    if get_setting("muse_app_summary_day", "") == today:
        return None
    since = max(float(get_setting("muse_auto_approve_since", "0") or 0),
                float(get_setting("muse_app_summary_ts", "0") or 0))
    s = build_summary(since)
    if not s["rows"]:
        return None
    set_setting("muse_app_summary_day", today)
    set_setting("muse_app_summary_ts", str(time.time()))
    try:
        from app.services import agent_log
        pid = f"museapp-summary-{today}"
        agent_log.approval_opened(pid, "muse_app_summary", "Muse app", "Last night, while you slept", s["text"])
        agent_log.approval_decided(pid, "8 AM summary")
    except Exception as e:
        logger.debug(f"summary not logged: {e}")
    try:
        from app.services.task_queue import notify
        notify("Muse approvals last night", s["text"])
    except Exception as e:
        logger.debug(f"summary notification failed: {e}")
    return s


# ------------------------------------------------------------------ being told the moment a card appears
class _Debouncer:
    """A burst of window-change events becomes one scan, DEBOUNCE_SEC after the last of them."""

    def __init__(self, delay: float, fn: Callable[[], None]):
        self.delay, self.fn, self._timer = delay, fn, None

    def call(self) -> None:
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self.delay, self.fn)
        self._timer.daemon = True
        self._timer.start()

    def cancel(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None


class _Observer:
    """An AXObserver on Muse's pid, in its own thread with a CFRunLoop. Listen-only: it never presses;
    it just calls on_event, and the scan that follows does the pressing."""

    NOTIFICATIONS = ("AXCreated", "AXUIElementDestroyed", "AXLayoutChanged", "AXValueChanged",
                     "AXTitleChanged", "AXFocusedUIElementChanged", "AXFocusedWindowChanged", "AXWindowCreated")

    def __init__(self, pid: int, on_event: Callable[[], None]):
        self.pid, self._stop = pid, threading.Event()
        self._thread = threading.Thread(target=self._run, args=(on_event,), daemon=True, name="xod-muse-observer")
        self._thread.start()

    def _run(self, on_event) -> None:
        try:
            import objc
            from ApplicationServices import (AXObserverAddNotification, AXObserverCreate,
                                             AXObserverGetRunLoopSource, AXUIElementCreateApplication)
            from CoreFoundation import (CFRunLoopAddSource, CFRunLoopGetCurrent, CFRunLoopRunInMode,
                                        kCFRunLoopDefaultMode)

            watched = []

            def subscribe(el):
                try:
                    if any(el is w for w in watched):
                        return
                    watched.append(el)
                    for name in self.NOTIFICATIONS:
                        AXObserverAddNotification(obs, el, name, None)
                except Exception:
                    pass

            @objc.callbackFor(AXObserverCreate)
            def cb(observer, element, notification, refcon):
                if str(notification) == "AXWindowCreated":
                    subscribe(element)
                on_event()

            err, obs = AXObserverCreate(self.pid, cb, None)
            if err != 0:
                logger.warning(f"couldn't watch the Muse app (AXObserver {err}); the backstop scan still runs")
                return
            app = AXUIElementCreateApplication(self.pid)
            subscribe(app)
            try:
                from ApplicationServices import AXUIElementCopyAttributeValue
                err2, windows = AXUIElementCopyAttributeValue(app, "AXWindows", None)
                for w in (windows if err2 == 0 and windows else []):
                    subscribe(w)
            except Exception:
                pass
            CFRunLoopAddSource(CFRunLoopGetCurrent(), AXObserverGetRunLoopSource(obs), kCFRunLoopDefaultMode)
            while not self._stop.is_set():
                CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.25, False)
        except Exception as e:
            logger.warning(f"Muse app observer stopped ({e}); the backstop scan still runs")

    def stop(self) -> None:
        self._stop.set()


def _observer_factory(pid: int, on_event: Callable[[], None]):
    return _Observer(pid, on_event)


def _stop_observer() -> None:
    obs, _state["observer"], _state["observer_pid"] = _state.get("observer"), None, None
    if obs is not None:
        try:
            obs.stop()
        except Exception:
            pass
    if _state.get("debouncer"):
        _state["debouncer"].cancel()


def _sync_observer(pid: Optional[int]) -> None:
    """Observer attached to exactly this pid (or none): re-attaches when Muse restarts, stops when off/gone."""
    if pid is None or not enabled():
        if _state.get("observer") is not None:
            _stop_observer()
        return
    if _state.get("observer") is not None and _state.get("observer_pid") == pid:
        return
    _stop_observer()

    def on_event():
        loop = _state.get("loop")
        if loop is not None:
            loop.call_soon_threadsafe(_schedule_burst)

    _state["observer"] = _observer_factory(pid, on_event)
    _state["observer_pid"] = pid


# ------------------------------------------------------------------ the watcher
async def scan_once() -> List[Dict[str, Any]]:
    """Press the approving button on every Muse app card waiting now; log each new one once.
    One scan at a time (an observer event and the backstop must never press together). A pressed card
    should disappear: if the same card is still showing REPRESS_AFTER seconds later it is treated as a
    new showing and pressed again, up to MAX_PRESS_TRIES presses, then logged as press-failed. (Upload
    cards often share text, so 'pressed once, skip forever' stalls the agent on the next identical card.)"""
    if _state.get("scan_lock") is None:
        _state["scan_lock"] = asyncio.Lock()
    if _state["scan_lock"].locked():
        _state["scan_again"] = True  # a card may have appeared mid-scan: one follow-up scan is queued
        return []
    async with _state["scan_lock"]:
        cards: List[Dict[str, Any]] = []
        while True:
            _state["scan_again"] = False
            now = time.time()
            marks = _state["press_marks"]
            if not _state.get("primed"):
                for pid, ts in _pressed_from_db().items():
                    marks.setdefault(pid, {"tries": 1, "last": ts})
            skip = {pid for pid, m in marks.items()
                    if m["tries"] >= MAX_PRESS_TRIES or now - m["last"] < REPRESS_AFTER}
            capped = {pid for pid, m in marks.items() if m["tries"] >= MAX_PRESS_TRIES}
            res = await asyncio.to_thread(_scan, True, skip)
            cards = res.get("approved") or []
            keys = {(c.get("title") or "", c.get("what") or "") for c in cards}
            for c in cards:
                cid = _card_id(c)
                if c.get("pressed"):
                    _state["pressed_ids"].add(cid)
                    m = marks.setdefault(cid, {"tries": 0, "last": now})
                    m["tries"], m["last"] = m["tries"] + 1, time.time()
                if cid in capped and cid not in _state["fail_logged"]:
                    _state["fail_logged"].add(cid)
                    _log({**c, "_id": cid + "-stuck", "pressed": False,
                          "reason": f"press failed: still showing after {MAX_PRESS_TRIES} presses"})
                if (c.get("title") or "", c.get("what") or "") not in _state["present"]:
                    _log(c)
            _state["present"] = keys
            _state["present_ids"] = {_card_id(c) for c in cards}
            for pid in [pid for pid in marks if pid not in _state["present_ids"]]:
                marks.pop(pid, None)          # the showing ended; an identical card later is a new showing
                _state["fail_logged"].discard(pid)
            _state["primed"] = True
            _state["error"] = None
            if not _state.get("scan_again"):
                return cards


async def _run_scan() -> None:
    try:
        await scan_once()
        _state["warned"] = False
    except Exception as e:
        _state["error"] = _describe_error(e)
        if not _state["warned"]:
            logger.warning(f"Can't reach the Muse app's approvals: {e}")
            _state["warned"] = True


def _fire_scan() -> None:
    loop = _state.get("loop")
    if loop is not None and enabled():
        asyncio.run_coroutine_threadsafe(_run_scan(), loop)


def _schedule_burst() -> None:
    """On the event loop: cancel the pending burst and schedule BURST_DELAYS looks. Each look is
    the cheap AX walk; the scan lock keeps them one at a time."""
    loop = _state.get("loop")
    if loop is None or not enabled():
        return
    for h in _state.get("burst_handles") or []:
        h.cancel()
    _state["burst_handles"] = [loop.call_later(d, lambda: asyncio.ensure_future(_run_scan()))
                               for d in BURST_DELAYS]


async def _watch() -> None:
    _state["loop"] = asyncio.get_running_loop()
    if _state.get("debouncer") is None:
        _state["debouncer"] = _Debouncer(DEBOUNCE_SEC, _fire_scan)
    while True:
        try:
            if enabled():
                pid = await asyncio.to_thread(_muse_pid)
                _sync_observer(pid)
                if time.time() - _state["backstop_at"] >= BACKSTOP_SEC:
                    _state["backstop_at"] = time.time()
                    await _run_scan()
                    await asyncio.to_thread(_maybe_summary)
            else:
                _stop_observer()
        except Exception as e:
            _state["error"] = _describe_error(e)
            if not _state["warned"]:
                logger.warning(f"Can't reach the Muse app's approvals: {e}")
                _state["warned"] = True
        await asyncio.sleep(1)


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
