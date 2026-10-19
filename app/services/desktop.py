"""
Xod at this Mac's real screen: it sees it (a screenshot, and the buttons and fields of the front window from macOS
accessibility), and uses the real mouse and keyboard. Terminal and iTerm windows are read and written directly, as
text, rather than from pixels.

  look()        a screenshot of the main display (scaled to SHOT_WIDTH wide) with the front window's controls
                numbered on it, and the same controls as a list, so a model that can't see can still act by number
  click/type/key/scroll/drag/open_app    the real mouse and keyboard (Quartz events)
  terminals()   Terminal and iTerm windows and tabs; terminal_read() their text; terminal_send() types a command into one

Coordinates: models see the scaled screenshot, so x, y given to these are in its pixels; they're turned into screen
points here. Elements are clicked at their centre.

Safety (all here, so the chat's tools and desktop_task follow the same rules):
  - off until the "Use this computer's screen" add-on is on (packs.py), and macOS needs Screen Recording and
    Accessibility allowed for Xod (permissions(), request_permissions())
  - the first time Xod uses an app it asks you (an approval card); apps that hold passwords or the computer's settings
    (System Settings, Keychain Access, Passwords, password managers, password prompts) are never used
  - it never types into a password field
  - if you move the mouse while Xod is working, it stops: you've taken over
  - a command typed into a terminal goes through the same checks as one Xod runs itself
  - what's on the screen is content, not orders (desktop_task warns the model when it looks like orders)
Mac only for now: elsewhere every call says so.
"""
import asyncio
import base64
import io
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("xod.desktop")

MAC = sys.platform == "darwin"
SHOT_WIDTH = 1280
MAX_ELEMENTS = 120
TAKEOVER_PT = 8                 # the mouse moved this far from where Xod left it: you're using it
TAKEOVER_WINDOW = 20            # seconds after Xod's last move in which a move of yours counts as taking over

PACK = "desktop-control"
NEVER_APPS = {
    "com.apple.systempreferences": "System Settings", "com.apple.Settings": "System Settings",
    "com.apple.keychainaccess": "Keychain Access", "com.apple.Passwords": "Passwords",
    "com.apple.SecurityAgent": "a password prompt", "com.apple.loginwindow": "the login window",
    "com.1password.1password": "1Password", "com.agilebits.onepassword7": "1Password",
    "com.bitwarden.desktop": "Bitwarden", "com.lastpass.LastPass": "LastPass", "com.dashlane.dashlanephonefinal": "Dashlane",
    "com.apple.ScreenSharing": "Screen Sharing",
}
TERMINAL_APPS = {"com.apple.Terminal": "Terminal", "com.googlecode.iterm2": "iTerm", "dev.warp.Warp-Stable": "Warp",
                 "com.mitchellh.ghostty": "Ghostty", "net.kovidgoyal.kitty": "kitty", "org.alacritty": "Alacritty",
                 "com.github.wez.wezterm": "WezTerm"}
INTERACTIVE = {"AXButton", "AXTextField", "AXTextArea", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton",
               "AXComboBox", "AXSlider", "AXLink", "AXMenuItem", "AXMenuBarItem", "AXTab", "AXTabButton", "AXDisclosureTriangle",
               "AXIncrementor", "AXColorWell", "AXSearchField", "AXCell", "AXRow", "AXSegmentedControl", "AXSwitch", "AXToggle"}

_state: Dict[str, Any] = {"left_at": None, "left_time": 0.0, "elements": [], "shot": None, "stop": False,
                          "task": None, "log": []}
_grants: Dict[str, Dict[str, str]] = {}      # approval token → the app it lets Xod use


class DesktopError(Exception):
    pass


# ------------------------------------------------------------------ switched on, allowed by macOS, allowed by you
def enabled() -> bool:
    from app.services import packs
    return PACK in packs.enabled()


def permissions() -> Dict[str, Any]:
    """What macOS allows this app: Screen Recording (to see) and Accessibility (to use the mouse and keyboard)."""
    if not MAC:
        return {"supported": False, "screen": False, "accessibility": False,
                "message": "Using the screen, mouse and keyboard works on a Mac for now."}
    import Quartz
    import ApplicationServices as AS
    screen = bool(Quartz.CGPreflightScreenCaptureAccess())
    access = bool(AS.AXIsProcessTrusted())
    return {"supported": True, "screen": screen, "accessibility": access, "enabled": enabled(),
            "ready": screen and access and enabled(), "app": _host_app(), "app_path": host_bundle()}


def _host_app() -> str:
    """The app macOS asks permission for: the one it holds responsible for Xod (Xod itself when it runs as the app;
    the terminal or tool that started it when run from source)."""
    path = _responsible_path()
    m = re.search(r"([^/]+)\.app/Contents/", path)
    if m:
        return m.group(1)
    return os.path.basename(path) or "the app that started Xod"


def _responsible_path() -> str:
    """The program macOS's privacy settings count for this process (it may start Xod through helpers)."""
    try:
        import ctypes
        lib = ctypes.CDLL(None)
        f = lib.responsibility_get_pid_responsible_for_pid
        f.restype, f.argtypes = ctypes.c_int, [ctypes.c_int]
        pid = f(os.getpid())
        buf = ctypes.create_string_buffer(4096)
        if pid > 0 and lib.proc_pidpath(pid, buf, 4096) > 0:
            return buf.value.decode()
    except Exception:
        pass
    return sys.executable


def host_bundle() -> str:
    """Where that app is, for the + button in Privacy & Security."""
    path = _responsible_path()
    m = re.search(r"^(.*?\.app)/Contents/", path)
    return m.group(1) if m else path


def request_permissions() -> Dict[str, Any]:
    """Asks macOS for both permissions (it shows its own prompts the first time) and opens the right Settings pages."""
    if not MAC:
        return permissions()
    import Quartz
    import ApplicationServices as AS
    p = permissions()
    if not p["screen"]:
        Quartz.CGRequestScreenCaptureAccess()
        subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"])
    if not p["accessibility"]:
        AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True})
        subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])
    return {**permissions(), "message": f"macOS shows {p['app']} in Privacy & Security: switch it on under Screen Recording and "
                                        "Accessibility. macOS may ask you to quit and reopen it once."}


def _ready(need_screen: bool = True, need_input: bool = True) -> None:
    if not MAC:
        raise DesktopError("Using the screen, mouse and keyboard works on a Mac for now.")
    if not enabled():
        raise DesktopError("Using this computer's screen is off. Turn on \"Use this computer's screen\" in Add-ons first.")
    p = permissions()
    missing = [n for n, ok, need in (("Screen Recording", p["screen"], need_screen), ("Accessibility", p["accessibility"], need_input)) if need and not ok]
    if missing:
        raise DesktopError(f"macOS hasn't allowed {p['app']} {' and '.join(missing)} yet. Open Add-ons → Use this computer's screen "
                           "→ Allow, then switch it on in System Settings → Privacy & Security.")


def allowed_apps() -> List[str]:
    from app.services.storage import get_setting
    try:
        return json.loads(get_setting("desktop_apps", "") or "[]")
    except ValueError:
        return []


def _allow(bundle: str) -> None:
    from app.services.storage import set_setting
    apps = allowed_apps()
    if bundle not in apps:
        set_setting("desktop_apps", json.dumps(apps + [bundle]))


def forget_app(bundle: str) -> Dict[str, Any]:
    from app.services.storage import set_setting
    set_setting("desktop_apps", json.dumps([a for a in allowed_apps() if a != bundle]))
    return {"success": True, "apps": allowed_apps()}


def use_grant(token: Optional[str]) -> None:
    """An approval card was approved: the app it named may now be used."""
    g = _grants.pop(token or "", None)
    if g:
        _allow(g["bundle"])


def check_app(app: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """None if Xod may use this app; else why not (never) or a request for your OK (first time)."""
    bundle, name = app.get("bundle") or "", app.get("name") or "this app"
    if bundle in NEVER_APPS or name in NEVER_APPS.values():
        return {"success": False, "refused": True, "error": f"Xod doesn't use {NEVER_APPS.get(bundle, name)}: it holds passwords or this "
                "computer's settings. Do that part yourself."}
    if not bundle or bundle in allowed_apps() or bundle == "dev.xod.desktop":
        return None
    token = uuid.uuid4().hex
    _grants[token] = {"bundle": bundle, "name": name}
    return {"needs_app_ok": True, "grant": token, "app": name, "bundle": bundle}


# ------------------------------------------------------------------ the screen
def _display() -> Dict[str, float]:
    import Quartz
    b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    return {"x": b.origin.x, "y": b.origin.y, "w": b.size.width, "h": b.size.height}


def front_app() -> Dict[str, Any]:
    from AppKit import NSWorkspace
    a = NSWorkspace.sharedWorkspace().frontmostApplication()
    return {"name": str(a.localizedName() or ""), "bundle": str(a.bundleIdentifier() or ""), "pid": int(a.processIdentifier())}


def running_apps() -> List[Dict[str, Any]]:
    from AppKit import NSWorkspace
    out = []
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        if a.activationPolicy() == 0:          # regular apps, with windows and a Dock icon
            out.append({"name": str(a.localizedName() or ""), "bundle": str(a.bundleIdentifier() or ""), "pid": int(a.processIdentifier())})
    return out


def windows() -> List[Dict[str, Any]]:
    """Windows on screen, front first: their app, title and where they are."""
    import Quartz
    info = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
                                             Quartz.kCGNullWindowID) or []
    out = []
    for w in info:
        if w.get("kCGWindowLayer", 1) != 0:
            continue
        b = w.get("kCGWindowBounds") or {}
        if b.get("Width", 0) < 60 or b.get("Height", 0) < 40:
            continue
        out.append({"app": str(w.get("kCGWindowOwnerName") or ""), "title": str(w.get("kCGWindowName") or "")[:120],
                    "x": int(b.get("X", 0)), "y": int(b.get("Y", 0)), "w": int(b.get("Width", 0)), "h": int(b.get("Height", 0))})
    return out[:20]


def _screenshot() -> Tuple[Any, float]:
    """The main display as a PIL image scaled to SHOT_WIDTH, and points per image pixel."""
    from PIL import Image
    fd, path = tempfile.mkstemp(suffix=".png", prefix="xod-screen-")
    os.close(fd)
    try:
        r = subprocess.run(["/usr/sbin/screencapture", "-x", "-m", "-t", "png", path], capture_output=True, timeout=15)
        if r.returncode != 0 or os.path.getsize(path) == 0:
            raise DesktopError("macOS didn't give Xod the screen: allow Screen Recording for it in System Settings → Privacy & Security.")
        img = Image.open(path).convert("RGB")
        img.load()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    d = _display()
    if img.width > SHOT_WIDTH:
        img = img.resize((SHOT_WIDTH, round(img.height * SHOT_WIDTH / img.width)), Image.LANCZOS)
    return img, d["w"] / img.width


def _ax(el: Any, attr: str) -> Any:
    import ApplicationServices as AS
    try:
        err, val = AS.AXUIElementCopyAttributeValue(el, attr, None)
        return val if err == 0 else None
    except Exception:
        return None


def _frame(el: Any) -> Optional[Tuple[float, float, float, float]]:
    import ApplicationServices as AS
    pos, size = _ax(el, "AXPosition"), _ax(el, "AXSize")
    if pos is None or size is None:
        return None
    ok1, p = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
    ok2, s = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
    if not (ok1 and ok2) or s.width <= 1 or s.height <= 1:
        return None
    return (p.x, p.y, s.width, s.height)


def _label(el: Any) -> str:
    for a in ("AXTitle", "AXDescription", "AXValue", "AXPlaceholderValue", "AXHelp"):
        v = _ax(el, a)
        if isinstance(v, str) and v.strip():
            return re.sub(r"\s+", " ", v.strip())[:80]
    return ""


def elements(app: Optional[Dict[str, Any]] = None, budget: float = 2.0) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The front window's controls (numbered from 1) and some of its text, from macOS accessibility."""
    import ApplicationServices as AS
    app = app or front_app()
    root = AS.AXUIElementCreateApplication(app["pid"])
    try:
        AS.AXUIElementSetAttributeValue(root, "AXManualAccessibility", True)    # Electron and Chrome apps hide theirs otherwise
    except Exception:
        pass
    win = _ax(root, "AXFocusedWindow") or _ax(root, "AXMainWindow")
    found: List[Dict[str, Any]] = []
    texts: List[str] = []
    if win is None:
        return found, texts
    deadline = time.time() + budget
    stack = [(win, 0)]
    seen = 0
    while stack and len(found) < MAX_ELEMENTS and time.time() < deadline and seen < 4000:
        el, depth = stack.pop()
        seen += 1
        role = _ax(el, "AXRole") or ""
        if role in INTERACTIVE or role == "AXStaticText":
            fr = _frame(el)
            if fr:
                label = _label(el)
                if role == "AXStaticText":
                    if label and len(texts) < 80:
                        texts.append(label)
                else:
                    sub = _ax(el, "AXSubrole") or ""
                    found.append({"n": len(found) + 1, "role": role.replace("AX", ""), "label": label,
                                  "password": sub == "AXSecureTextField", "frame": fr, "_el": el,
                                  "enabled": _ax(el, "AXEnabled") is not False})
        if depth < 30:
            kids = _ax(el, "AXChildren") or []
            for k in reversed(list(kids)):
                stack.append((k, depth + 1))
    return found, texts


def _draw_marks(img: Any, els: List[Dict[str, Any]], scale: float) -> Any:
    from PIL import ImageDraw
    d = _display()
    draw = ImageDraw.Draw(img)
    for e in els:
        x, y, w, h = e["frame"]
        x0, y0 = (x - d["x"]) / scale, (y - d["y"]) / scale
        x1, y1 = x0 + w / scale, y0 + h / scale
        if x1 < 0 or y1 < 0 or x0 > img.width or y0 > img.height:
            continue
        draw.rectangle([x0, y0, x1, y1], outline=(255, 64, 160), width=2)
        tag = str(e["n"])
        tw = 7 * len(tag) + 6
        draw.rectangle([x0, y0, x0 + tw, y0 + 14], fill=(255, 64, 160))
        draw.text((x0 + 3, y0 + 1), tag, fill=(255, 255, 255))
    return img


def _uri(img: Any, quality: int = 60) -> str:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def element_lines(els: List[Dict[str, Any]], scale: float) -> str:
    d = _display()
    rows = []
    for e in els:
        x, y, w, h = e["frame"]
        cx, cy = round((x + w / 2 - d["x"]) / scale), round((y + h / 2 - d["y"]) / scale)
        flags = (" [password: Xod won't type here]" if e["password"] else "") + ("" if e["enabled"] else " [disabled]")
        rows.append(f"[{e['n']}] {e['role']} \"{e['label']}\" at ({cx},{cy}){flags}")
    return "\n".join(rows)


async def look(marks: bool = True) -> Dict[str, Any]:
    """What's on the screen: a screenshot (numbered controls drawn on it), the front app, its controls, windows."""
    _ready(need_screen=True, need_input=False)
    app = front_app()
    img, scale = await asyncio.to_thread(_screenshot)
    els, texts = [], []
    if permissions()["accessibility"]:
        els, texts = await asyncio.to_thread(elements, app)
    if marks and els:
        img = _draw_marks(img, els, scale)
    _state.update(elements=els, scale=scale, shot=_uri(img, 45), shot_at=time.time(), app=app)
    return {"success": True, "app": app["name"], "bundle": app["bundle"], "screenshot_uri": _uri(img),
            "size": [img.width, img.height], "elements": element_lines(els, scale), "count": len(els),
            "text": " | ".join(texts)[:3000], "windows": windows(), "terminal": app["bundle"] in TERMINAL_APPS}


# ------------------------------------------------------------------ the mouse and keyboard
def _cursor() -> Tuple[float, float]:
    import Quartz
    p = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return (p.x, p.y)


def _check_takeover() -> None:
    left = _state["left_at"]
    if left and time.time() - _state["left_time"] < TAKEOVER_WINDOW:
        x, y = _cursor()
        if math.hypot(x - left[0], y - left[1]) > TAKEOVER_PT:
            _state["left_at"] = None
            raise DesktopError("You moved the mouse, so Xod stopped: you've taken over. Say continue when you want it to carry on.")
    if _state["stop"]:
        raise DesktopError("Stopped: you pressed Stop.")


def _mark_left() -> None:
    _state["left_at"], _state["left_time"] = _cursor(), time.time()


def _to_points(x: float, y: float) -> Tuple[float, float]:
    d = _display()
    s = _state.get("scale") or 1.0
    return (d["x"] + float(x) * s, d["y"] + float(y) * s)


def _target(element: Any = None, x: Any = None, y: Any = None) -> Tuple[float, float, Optional[Dict[str, Any]]]:
    if element not in (None, ""):
        try:
            n = int(element)
        except (TypeError, ValueError):
            raise DesktopError(f"Element {element} isn't a number from the last look.")
        e = next((e for e in _state["elements"] if e["n"] == n), None)
        if not e:
            raise DesktopError(f"There's no element {n} on the last look: look again.")
        fx, fy, fw, fh = e["frame"]
        return (fx + fw / 2, fy + fh / 2, e)
    if x is None or y is None:
        raise DesktopError("Say where: an element number from the last look, or x and y on its screenshot.")
    px, py = _to_points(x, y)
    return (px, py, None)


def _post_mouse(kind: str, pt: Tuple[float, float], button: str = "left", clicks: int = 1) -> None:
    import Quartz
    btn = {"left": Quartz.kCGMouseButtonLeft, "right": Quartz.kCGMouseButtonRight}.get(button, Quartz.kCGMouseButtonLeft)
    types = {("down", "left"): Quartz.kCGEventLeftMouseDown, ("up", "left"): Quartz.kCGEventLeftMouseUp,
             ("down", "right"): Quartz.kCGEventRightMouseDown, ("up", "right"): Quartz.kCGEventRightMouseUp,
             ("drag", "left"): Quartz.kCGEventLeftMouseDragged, ("move", "left"): Quartz.kCGEventMouseMoved,
             ("move", "right"): Quartz.kCGEventMouseMoved}
    ev = Quartz.CGEventCreateMouseEvent(None, types[(kind, button)], pt, btn)
    if clicks > 1:
        Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventClickState, clicks)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _glide(to: Tuple[float, float], steps: int = 12) -> None:
    """Moves the pointer there in a short visible glide, so you can see what Xod is doing."""
    x0, y0 = _cursor()
    for i in range(1, steps + 1):
        _post_mouse("move", (x0 + (to[0] - x0) * i / steps, y0 + (to[1] - y0) * i / steps))
        time.sleep(0.012)


def _app_at(pt: Tuple[float, float]) -> Dict[str, Any]:
    """The app whose window is under that point (what a click there would use)."""
    import ApplicationServices as AS
    try:
        err, el = AS.AXUIElementCopyElementAtPosition(AS.AXUIElementCreateSystemWide(), pt[0], pt[1], None)
        if err == 0 and el is not None:
            err, pid = AS.AXUIElementGetPid(el, None)
            if err == 0:
                for a in running_apps():
                    if a["pid"] == pid:
                        return a
                from AppKit import NSRunningApplication
                ra = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
                if ra:
                    return {"name": str(ra.localizedName() or ""), "bundle": str(ra.bundleIdentifier() or ""), "pid": pid}
    except Exception:
        pass
    return front_app()


def _gate(app: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    res = check_app(app)
    if res and res.get("needs_app_ok"):
        return {"success": False, "needs_app_ok": True, "grant": res["grant"], "app": res["app"], "bundle": res["bundle"],
                "error": f"Xod hasn't used {res['app']} before: it needs your OK first."}
    return res


def _did(what: str, app: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    entry = {"at": time.time(), "did": what, "app": app.get("name")}
    _state["log"].append(entry)
    del _state["log"][:-200]
    logger.info(f"Desktop: {what} ({app.get('name')})")
    return {"success": True, "did": what, "app": app.get("name"), **(extra or {})}


async def click(element: Any = None, x: Any = None, y: Any = None, button: str = "left", clicks: int = 1) -> Dict[str, Any]:
    _ready()
    _check_takeover()
    px, py, e = _target(element, x, y)
    app = _app_at((px, py))
    held = _gate(app)
    if held:
        return held
    def go():
        _glide((px, py))
        for i in range(1, max(1, min(int(clicks), 3)) + 1):
            _post_mouse("down", (px, py), button, i)
            _post_mouse("up", (px, py), button, i)
            time.sleep(0.04)
        _mark_left()
    await asyncio.to_thread(go)
    what = f"{'double-' if clicks == 2 else 'right-' if button == 'right' else ''}clicked " + (f"[{e['n']}] {e['role']} \"{e['label']}\"" if e else f"at ({x},{y})")
    return _did(what, app)


async def drag(from_element: Any = None, to_element: Any = None, x1: Any = None, y1: Any = None, x2: Any = None, y2: Any = None) -> Dict[str, Any]:
    _ready()
    _check_takeover()
    ax, ay, _ = _target(from_element, x1, y1)
    bx, by, _ = _target(to_element, x2, y2)
    app = _app_at((ax, ay))
    held = _gate(app)
    if held:
        return held
    def go():
        _glide((ax, ay))
        _post_mouse("down", (ax, ay))
        for i in range(1, 21):
            _post_mouse("drag", (ax + (bx - ax) * i / 20, ay + (by - ay) * i / 20))
            time.sleep(0.015)
        _post_mouse("up", (bx, by))
        _mark_left()
    await asyncio.to_thread(go)
    return _did("dragged", app)


async def scroll(direction: str = "down", amount: int = 5, element: Any = None, x: Any = None, y: Any = None) -> Dict[str, Any]:
    import Quartz
    _ready()
    _check_takeover()
    if element not in (None, "") or (x is not None and y is not None):
        px, py, _ = _target(element, x, y)
    else:
        px, py = _cursor()
    app = _app_at((px, py))
    held = _gate(app)
    if held:
        return held
    n = max(1, min(int(amount or 5), 30))
    dy = {"down": -n, "up": n}.get(direction, 0)
    dx = {"left": n, "right": -n}.get(direction, 0)
    def go():
        _glide((px, py), 6)
        ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 2, dy, dx)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        _mark_left()
    await asyncio.to_thread(go)
    return _did(f"scrolled {direction}", app)


def _focused_is_password() -> bool:
    import ApplicationServices as AS
    el = _ax(AS.AXUIElementCreateSystemWide(), "AXFocusedUIElement")
    return el is not None and (_ax(el, "AXSubrole") == "AXSecureTextField")


def _command_check(text: str) -> Optional[Dict[str, Any]]:
    """Text going into a terminal is a command: the same refusal Xod's own commands get."""
    from app.agent.worker import refused
    return refused("computer_run_command", {"command": text})


async def type_text(text: str, element: Any = None, enter: bool = False) -> Dict[str, Any]:
    import Quartz
    _ready()
    _check_takeover()
    text = str(text or "")
    if element not in (None, ""):
        px, py, e = _target(element)
        if e and e["password"]:
            return {"success": False, "refused": True, "error": "That's a password field: Xod doesn't type passwords. Type it yourself."}
        r = await click(element=element)
        if not r.get("success"):
            return r
        await asyncio.sleep(0.2)
    app = front_app()
    held = _gate(app)
    if held:
        return held
    if await asyncio.to_thread(_focused_is_password):
        return {"success": False, "refused": True, "error": "The cursor is in a password field: Xod doesn't type passwords. Type it yourself."}
    if app["bundle"] in TERMINAL_APPS:
        no = _command_check(text)
        if no:
            return no
    def go():
        for i in range(0, len(text), 18):
            chunk = text[i:i + 18]
            for down in (True, False):
                ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                Quartz.CGEventKeyboardSetUnicodeString(ev, len(chunk.encode("utf-16-le")) // 2, chunk)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            time.sleep(0.01)
        if enter:
            _press(["return"])
        _mark_left()
    await asyncio.to_thread(go)
    shown = text if len(text) <= 60 else text[:57] + "…"
    return _did(f"typed \"{shown}\"" + (" and pressed Return" if enter else ""), app)


KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29,
    "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44,
    "n": 45, "m": 46, ".": 47, "`": 50, "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51,
    "escape": 53, "esc": 53, "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126, "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}
MODS = {"cmd": "Command", "command": "Command", "shift": "Shift", "option": "Alternate", "alt": "Alternate",
        "ctrl": "Control", "control": "Control", "fn": "SecondaryFn"}
# Shortcuts that throw away work or end things for good, wherever they're pressed: only when the request asked.
RISKY_KEYS = {"cmd+q", "cmd+option+escape", "cmd+shift+q", "cmd+option+shift+q", "ctrl+cmd+q", "cmd+delete", "cmd+shift+delete"}


def _press(keys: List[str]) -> None:
    import Quartz
    mods = [k for k in keys if k in MODS]
    main = [k for k in keys if k not in MODS]
    if len(main) != 1 or main[0] not in KEYCODES:
        raise DesktopError(f"Xod doesn't know the key {'+'.join(main) or '(none)'}.")
    flags = 0
    for m in mods:
        flags |= getattr(Quartz, f"kCGEventFlagMask{MODS[m]}")
    code = KEYCODES[main[0]]
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        if flags:
            Quartz.CGEventSetFlags(ev, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.02)


async def key(keys: str, asked: str = "") -> Dict[str, Any]:
    """A key or shortcut: "return", "cmd+t", "ctrl+c"."""
    _ready()
    _check_takeover()
    parts = [k.strip().lower() for k in re.split(r"\s*\+\s*", str(keys or "")) if k.strip()]
    combo = "+".join(parts)
    app = front_app()
    held = _gate(app)
    if held:
        return held
    if combo in RISKY_KEYS and not re.search(r"\b(quit|close|force|log ?out|empty|delete)\b", asked or "", re.I):
        return {"success": False, "refused": True, "error": f"{combo} quits or deletes things; Xod presses it only when you ask for that."}
    await asyncio.to_thread(lambda: (_press(parts), _mark_left()))
    return _did(f"pressed {combo}", app)


async def open_app(name: str) -> Dict[str, Any]:
    """Opens an app, or brings it to the front."""
    _ready(need_screen=False)
    _check_takeover()
    name = str(name or "").strip()
    if not name:
        raise DesktopError("Which app?")
    match = next((a for a in running_apps() if a["name"].lower() == name.lower()), None)
    app = match or {"name": name, "bundle": _bundle_of(name)}
    held = _gate(app)
    if held:
        return held
    r = await asyncio.to_thread(subprocess.run, ["open", "-a", name], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        return {"success": False, "error": f"Couldn't open {name}: {(r.stderr or '').strip()[:200]}"}
    await asyncio.sleep(0.8)
    _mark_left()
    return _did(f"opened {name}", app)


def _bundle_of(name: str) -> str:
    try:
        r = subprocess.run(["osascript", "-e", f'id of application "{_as(name)}"'], capture_output=True, text=True, timeout=8)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


# ------------------------------------------------------------------ terminals
def _as(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _osa(script: str, timeout: float = 15) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if "-1743" in err or "not authorized" in err.lower():
            raise DesktopError("macOS hasn't allowed Xod to control that terminal app: allow it in System Settings → Privacy & Security → Automation.")
        raise DesktopError(err[:300] or "The terminal didn't answer.")
    return r.stdout


def _running(bundle: str) -> bool:
    return any(a["bundle"] == bundle for a in running_apps())


def terminals() -> Dict[str, Any]:
    """Every Terminal and iTerm window and tab that's open, with what runs in it. Never opens either app."""
    _ready(need_screen=False, need_input=False)
    out: List[Dict[str, Any]] = []
    if _running("com.apple.Terminal"):
        raw = _osa('''set text item delimiters to ","
tell application "Terminal"
  set out to ""
  repeat with w from 1 to count windows
    repeat with t from 1 to count tabs of window w
      set tb to tab t of window w
      set out to out & w & tab & t & tab & (busy of tb) & tab & ((processes of tb) as text) & tab & (selected of tb) & tab & (custom title of tb) & linefeed
    end repeat
  end repeat
  return out
end tell''')
        for line in raw.splitlines():
            p = line.split("\t")
            if len(p) >= 5:
                out.append({"app": "Terminal", "window": int(p[0]), "tab": int(p[1]), "busy": p[2] == "true",
                            "running": p[3].split(",")[-1] if p[3] else "", "processes": p[3], "selected": p[4] == "true",
                            "title": p[5] if len(p) > 5 else ""})
    if _running("com.googlecode.iterm2"):
        raw = _osa('''tell application "iTerm2"
  set out to ""
  repeat with w from 1 to count windows
    repeat with t from 1 to count tabs of window w
      set s to current session of tab t of window w
      set out to out & w & tab & t & tab & (is processing of s) & tab & (name of s) & linefeed
    end repeat
  end repeat
  return out
end tell''')
        for line in raw.splitlines():
            p = line.split("\t")
            if len(p) >= 4:
                out.append({"app": "iTerm", "window": int(p[0]), "tab": int(p[1]), "busy": p[2] == "true", "title": p[3]})
    return {"success": True, "terminals": out,
            "note": "" if out else "No Terminal or iTerm window is open."}


def _pick(app: str, window: Any, tab: Any) -> Tuple[str, int, int]:
    found = terminals()["terminals"]
    app = (app or "").lower()
    cands = [t for t in found if not app or t["app"].lower().startswith(app[:4])]
    if not cands:
        raise DesktopError("No Terminal or iTerm window is open." if not found else f"No {app} window is open.")
    w = int(window) if window not in (None, "") else None
    t = int(tab) if tab not in (None, "") else None
    pick = next((c for c in cands if (w is None or c["window"] == w) and (t is None or c["tab"] == t)
                 and (t is not None or c.get("selected", True) or c["app"] == "iTerm")), None)
    if not pick:
        raise DesktopError(f"There's no {app or 'terminal'} window {w} tab {t}: {json.dumps(found)[:400]}")
    return pick["app"], pick["window"], pick["tab"]


def terminal_read(app: str = "", window: Any = None, tab: Any = None, lines: int = 80) -> Dict[str, Any]:
    """The last lines of a terminal tab (its scrollback), as text."""
    _ready(need_screen=False, need_input=False)
    a, w, t = _pick(app, window, tab)
    if a == "Terminal":
        text = _osa(f'tell application "Terminal" to get history of tab {t} of window {w}')
    else:
        text = _osa(f'tell application "iTerm2" to get contents of current session of tab {t} of window {w}')
    rows = [r.rstrip() for r in text.replace("\r", "").split("\n")]
    while rows and not rows[-1]:
        rows.pop()
    n = max(5, min(int(lines or 80), 400))
    return {"success": True, "app": a, "window": w, "tab": t, "text": "\n".join(rows[-n:]), "lines": min(n, len(rows))}


async def terminal_send(text: str, app: str = "", window: Any = None, tab: Any = None, enter: bool = True) -> Dict[str, Any]:
    """Types text into a terminal tab (and Return, so it runs). For keys like Ctrl+C, focus it and use key()."""
    _ready(need_screen=False, need_input=False)
    _check_takeover()
    text = str(text or "")
    a, w, t = _pick(app, window, tab)
    bundle = "com.apple.Terminal" if a == "Terminal" else "com.googlecode.iterm2"
    held = _gate({"name": a, "bundle": bundle})
    if held:
        return held
    no = _command_check(text)
    if no:
        return no
    if a == "Terminal":
        if not enter:
            raise DesktopError("Terminal can only be sent a whole line (with Return). To type without Return, click into it and use type.")
        await asyncio.to_thread(_osa, f'tell application "Terminal" to do script "{_as(text)}" in tab {t} of window {w}')
    else:
        await asyncio.to_thread(_osa, f'tell application "iTerm2" to tell current session of tab {t} of window {w} to write text "{_as(text)}"'
                                + ("" if enter else " newline no"))
    return _did(f"sent \"{text[:60]}\" to {a} window {w} tab {t}", {"name": a})


# ------------------------------------------------------------------ for the header and the activity view
def status() -> Dict[str, Any]:
    task = _state.get("task")
    return {**permissions(), "apps": allowed_apps(), "task": {k: v for k, v in (task or {}).items() if k != "shot"} if task else None,
            "shot": (task or {}).get("shot") if task else None, "recent": _state["log"][-20:]}


def stop() -> Dict[str, Any]:
    _state["stop"] = True
    return {"success": True, "message": "Stopping."}
