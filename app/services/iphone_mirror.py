"""
An iPhone through macOS iPhone Mirroring (Mac only).

The Mac shows Rene's iPhone in the 'iPhone Mirroring' app window. Xod reads
that window with the same Accessibility walk the Muse card watcher uses
(services/muse_backends.py: MacAX), and taps by pressing the element under
a point (AXUIElementCopyElementAtPosition + AXPress), falling back to no
press at all rather than moving Rene's real mouse. Reading also returns a
screenshot of just that window (screencapture -R on its bounds), so a model
can see the phone as well as read its text.

Rules:
- Off unless Rene switches it on (setting 'iphone_mirroring', default off).
  phone_mcp refuses mirror calls while it's off.
- Mac only. On Windows/Linux every call says so plainly.
- When Rene unlocks/uses his iPhone, Mirroring pauses and shows text like
  'iPhone in Use'. status()/read_screen() say that plainly ('paused: your
  iPhone is unlocked/in use') instead of returning an empty screen.
- No password is ever typed, and secure fields are never read for values.
"""
import base64
import os
import subprocess
import sys
import tempfile
import threading
from typing import Any, Dict, List, Optional

SETTING = "iphone_mirroring"
PLATFORM = "iphone-mirroring"
APP_NAME = "iPhone Mirroring"
_PAUSED_MARKERS = ("iphone in use", "iphone is in use", "unlock your iphone",
                   "mirroring paused", "iphone locked", "use your iphone")

_lock = threading.Lock()
_injected: Dict[str, Any] = {}  # tests: {"ax": fake, "pid": int, "screenshot": fn}


def enabled() -> bool:
    from app.services.storage import get_setting
    return get_setting(SETTING, "off") == "on"


def set_enabled(on: bool) -> Dict[str, Any]:
    from app.services.storage import set_setting
    set_setting(SETTING, "on" if on else "off")
    return status()


def _pid() -> Optional[int]:
    if "pid" in _injected:
        return _injected["pid"]
    try:
        r = subprocess.run(["pgrep", "-x", APP_NAME], capture_output=True, text=True, timeout=5)
        pids = (r.stdout or "").split()
        return int(pids[0]) if pids else None
    except Exception:
        return None


def _ax():
    if "ax" in _injected:
        return _injected["ax"]
    from app.services import muse_backends
    b = muse_backends.backend("darwin")
    return b.ax_api()


def _label(ax, el) -> str:
    for name in ("AXTitle", "AXDescription", "AXValue"):
        try:
            v = ax.attr(el, name)
        except Exception:
            v = None
        if v:
            return str(v)
    return ""


def _walk(ax, root) -> Dict[str, Any]:
    texts: List[str] = []
    buttons: List[str] = []

    def inner(el, depth):
        if depth > 30 or len(texts) > 300:
            return
        role = str(ax.attr(el, "AXRole") or "")
        label = _label(ax, el).strip()
        if label:
            if role == "AXButton":
                buttons.append(label)
            elif role == "AXStaticText":
                texts.append(label)
        for child in (ax.attr(el, "AXChildren") or []):
            inner(child, depth + 1)

    inner(root, 0)
    return {"texts": texts, "buttons": buttons}


def _paused(texts: List[str]) -> Optional[str]:
    joined = " ".join(texts).lower()
    if any(m in joined for m in _PAUSED_MARKERS):
        return ("iPhone Mirroring is paused: your iPhone is unlocked / in use. "
                "Lock your iPhone and Mirroring will resume, then Xod can see it again.")
    return None


def _screenshot(pid: Optional[int]) -> Optional[str]:
    """A PNG (base64 data URI) of just the Mirroring window, or None."""
    if "screenshot" in _injected:
        return _injected["screenshot"]()
    try:
        from app.services import desktop
        win = next((w for w in desktop.windows() if w.get("app") == APP_NAME), None)
        if not win:
            return None
        fd, path = tempfile.mkstemp(suffix=".png", prefix="xod-mirror-")
        os.close(fd)
        try:
            rect = f"{win['x']},{win['y']},{win['w']},{win['h']}"
            r = subprocess.run(["/usr/sbin/screencapture", "-x", "-R", rect, "-t", "png", path],
                               capture_output=True, timeout=15)
            if r.returncode != 0 or os.path.getsize(path) == 0:
                return None
            return "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    except Exception:
        return None


def status(ax=None, pid: Optional[int] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"supported": sys.platform == "darwin", "enabled": enabled(),
                           "platform": PLATFORM, "running": False, "paused": False, "message": ""}
    if sys.platform != "darwin":
        out["message"] = "iPhone Mirroring works on a Mac only."
        return out
    if not out["enabled"]:
        out["message"] = "Off. Switch iPhone Mirroring on in Xod's phone panel to let Xod use it."
        return out
    p = _pid() if pid is None else pid
    out["running"] = p is not None
    if p is None:
        out["message"] = "iPhone Mirroring isn't open on this Mac."
        return out
    try:
        api = ax or _ax()
        walked = _walk(api, api.create_app(p))
        msg = _paused(walked["texts"])
        out["paused"] = msg is not None
        out["message"] = msg or "Connected: Xod can read and tap the mirrored iPhone."
    except Exception as e:
        out["message"] = f"Can't reach iPhone Mirroring: {e}"
    return out


def list_entry() -> Dict[str, Any]:
    """The virtual phone phone_list shows for the mirrored iPhone."""
    st = status()
    on = bool(st["enabled"] and st["running"] and not st["paused"])
    return {"address": PLATFORM, "nickname": "iPhone (Mirroring)", "platform": PLATFORM,
            "enabled": st["enabled"], "online": on, "paused": st["paused"],
            "message": st["message"],
            "capabilities": {"screen": bool(st["enabled"]), "tap": bool(st["enabled"])}}


def read_screen(ax=None) -> Dict[str, Any]:
    if sys.platform != "darwin":
        return {"success": False, "error": "iPhone Mirroring works on a Mac only."}
    if not enabled():
        return {"success": False, "error": "iPhone Mirroring is off. Switch it on in Xod's phone panel first."}
    pid = _pid()
    if pid is None:
        return {"success": False, "error": "iPhone Mirroring isn't open on this Mac."}
    api = ax or _ax()
    walked = _walk(api, api.create_app(pid))
    msg = _paused(walked["texts"])
    if msg:
        return {"success": False, "paused": True, "error": msg, "texts": walked["texts"]}
    return {"success": True, "platform": PLATFORM, "texts": walked["texts"],
            "buttons": walked["buttons"], "screenshot_uri": _screenshot(pid)}


def tap(x: int, y: int, ax=None) -> Dict[str, Any]:
    if sys.platform != "darwin":
        return {"success": False, "error": "iPhone Mirroring works on a Mac only."}
    if not enabled():
        return {"success": False, "error": "iPhone Mirroring is off. Switch it on in Xod's phone panel first."}
    pid = _pid()
    if pid is None:
        return {"success": False, "error": "iPhone Mirroring isn't open on this Mac."}
    api = ax or _ax()
    walked = _walk(api, api.create_app(pid))
    msg = _paused(walked["texts"])
    if msg:
        return {"success": False, "paused": True, "error": msg}
    el_at = getattr(api, "element_at", None)
    if el_at is None:
        return {"success": False, "error": "This Mac's Accessibility backend can't tap by position."}
    el = el_at(float(x), float(y))
    if el is None:
        return {"success": False, "error": f"Nothing tappable at {x},{y} in the mirrored iPhone."}
    err = api.perform(el, "AXPress")
    return {"success": err == 0, "platform": PLATFORM, "x": x, "y": y,
            **({} if err == 0 else {"error": f"AXPress returned {err}"})}
