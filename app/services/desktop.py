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
    if not (screen and access):
        # macOS answers a running program from what it knew when it started: a switch you've just turned on only shows
        # to a new process. Ask a fresh one (macOS counts it as the same app).
        fs, fa = _fresh_check()
        screen, access = screen or fs, access or fa
    return {"supported": True, "screen": screen, "accessibility": access, "enabled": enabled(),
            "ready": screen and access and enabled(), "app": _host_app(), "app_path": host_bundle()}


_fresh: Dict[str, Any] = {"at": 0.0, "value": (False, False)}


def _fresh_check() -> Tuple[bool, bool]:
    if time.time() - _fresh["at"] < 2:
        return _fresh["value"]
    try:
        r = subprocess.run([sys.executable, "-c", "import Quartz, ApplicationServices as A;"
                            "print(int(bool(Quartz.CGPreflightScreenCaptureAccess())), int(bool(A.AXIsProcessTrusted())))"],
                           capture_output=True, text=True, timeout=10)
        a, b = (r.stdout.split() + ["0", "0"])[:2]
        value = (a == "1", b == "1")
    except Exception:
        value = (False, False)
    _fresh.update(at=time.time(), value=value)
    return value


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


PANES = {"screen": "Privacy_ScreenCapture", "accessibility": "Privacy_Accessibility", "automation": "Privacy_Automation"}
_asked: Dict[str, float] = {}           # when Xod last opened each Settings page (so it doesn't keep popping them up)


def _open_pane(which: str) -> None:
    subprocess.Popen(["open", f"x-apple.systempreferences:com.apple.preference.security?{PANES[which]}"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _asked[which] = time.time()


def request_permissions(force: bool = True) -> Dict[str, Any]:
    """Asks macOS for what's missing: its own prompts first, then the exact Settings page, one at a time (the screen
    first), so you only have to flip a switch. force=False skips a page opened in the last 2 minutes."""
    if not MAC:
        return permissions()
    import Quartz
    import ApplicationServices as AS
    p = permissions()
    opened = []
    for which, ok in (("screen", p["screen"]), ("accessibility", p["accessibility"])):
        if ok:
            continue
        if not force and time.time() - _asked.get(which, 0) < 120:
            break
        if which == "screen":
            Quartz.CGRequestScreenCaptureAccess()
        else:
            AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True})
        _open_pane(which)
        opened.append(which)
        break                                 # one page at a time; the next opens once this one is on
    p = permissions()
    need = [n for n, ok in (("Screen Recording", p["screen"]), ("Accessibility", p["accessibility"])) if not ok]
    msg = ("Everything's allowed." if not need else
           f"I opened System Settings → Privacy & Security → {need[0]} for you: switch on {p['app']} there"
           + (f" (if it isn't listed, press + and pick {p['app_path']})" if p['app_path'] else "") + ". "
           + ("Then the same for Accessibility. " if len(need) > 1 else "") + "Xod notices by itself and carries on."
           + (f" (This Xod was started from {p['app']}, so macOS asks for {p['app']}; in the Xod app it's listed as Xod.)" if p["app"] != "Xod" else ""))
    return {**p, "opened": opened, "missing": need, "message": msg}


async def wait_for_permissions(seconds: float, need_screen: bool = True, need_input: bool = True, on_wait=None) -> bool:
    """Opens what's missing and waits (up to seconds) for you to switch it on; the next page opens as each one is done."""
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        p = permissions()
        missing = [w for w, ok, need in (("screen", p["screen"], need_screen), ("accessibility", p["accessibility"], need_input)) if need and not ok]
        if not missing:
            return True
        if missing[0] != last:
            last = missing[0]
            res = request_permissions(force=False)
            if on_wait:
                on_wait(res["message"])
        await asyncio.sleep(1)
    return False


def _ready(need_screen: bool = True, need_input: bool = True) -> None:
    if not MAC:
        raise DesktopError("Using the screen, mouse and keyboard works on a Mac for now.")
    if not enabled():
        raise DesktopError("Using this computer's screen is off. Turn on \"Use this computer's screen\" in Add-ons first.")
    p = permissions()
    missing = [n for n, ok, need in (("Screen Recording", p["screen"], need_screen), ("Accessibility", p["accessibility"], need_input)) if need and not ok]
    if missing:
        res = request_permissions(force=False)          # open the page for them rather than send them looking
        raise DesktopError(f"macOS hasn't allowed {p['app']} {' and '.join(missing)} yet. {res['message']} Say continue when it's on.")


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


def _app_of_pid(pid: int) -> Optional[Dict[str, Any]]:
    from AppKit import NSRunningApplication
    a = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if a is None:
        return None
    return {"name": str(a.localizedName() or ""), "bundle": str(a.bundleIdentifier() or ""), "pid": int(pid)}


def front_app() -> Dict[str, Any]:
    """The app in front right now. NSWorkspace's answer goes stale in a program with no window of its own (it only
    updates on its run loop, which Xod's server doesn't run), so ask Accessibility, then the window list."""
    try:
        import ApplicationServices as AS
        err, el = AS.AXUIElementCopyAttributeValue(AS.AXUIElementCreateSystemWide(), "AXFocusedApplication", None)
        if err == 0 and el is not None:
            err, pid = AS.AXUIElementGetPid(el, None)
            if err == 0 and pid:
                a = _app_of_pid(pid)
                if a:
                    return a
    except Exception:
        pass
    try:
        import Quartz
        for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
                                                   Quartz.kCGNullWindowID) or []:
            if w.get("kCGWindowLayer", 1) == 0:
                a = _app_of_pid(int(w.get("kCGWindowOwnerPID", 0)))
                if a:
                    return a
    except Exception:
        pass
    from AppKit import NSWorkspace
    a = NSWorkspace.sharedWorkspace().frontmostApplication()
    return {"name": str(a.localizedName() or ""), "bundle": str(a.bundleIdentifier() or ""), "pid": int(a.processIdentifier())}


def running_apps() -> List[Dict[str, Any]]:
    """Regular apps running now. (NSWorkspace's list, like its front app, can lag in a program with no run loop:
    _running() and _app_at() ask by bundle id and process id instead, which are always current.)"""
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
        err, _ = AS.AXUIElementCopyAttributeValue(root, "AXWindows", None)
        if err == AS.kAXErrorAPIDisabled:
            raise DesktopError("macOS has Accessibility on for Xod, but only lets it use it after Xod is reopened once. "
                               "Quit Xod and open it again, then say continue.")
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


def _post(ev: Any) -> None:
    """The one place a real mouse or keyboard event leaves Xod (tests replace this, so they never touch the screen)."""
    import Quartz
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


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
    _post(ev)


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
                a = _app_of_pid(pid)
                if a:
                    return a
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
        _post(ev)
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
    await asyncio.to_thread(_type_now, text, enter)
    shown = text if len(text) <= 60 else text[:57] + "…"
    return _did(f"typed \"{shown}\"" + (" and pressed Return" if enter else ""), app)


def _type_now(text: str, enter: bool) -> None:
    """Types it into what's focused. Pasted (your clipboard is put back right after): apps take a paste reliably, where
    synthetic key events for each letter are dropped by some (Notes did) and arrive as a "paste" in others anyway.
    Return is pressed on its own afterwards, so full-screen terminal agents (Muse, Claude Code) submit instead of
    adding a new line."""
    if text:
        saved = _clipboard_get()
        _clipboard_set(text)
        time.sleep(0.05)
        _press(["cmd", "v"])
        time.sleep(0.35)
        _clipboard_set(saved) if saved is not None else None
    if enter:
        time.sleep(0.25)
        _press(["return"])
    _mark_left()


def _clipboard_get() -> Optional[str]:
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString
        return NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
    except Exception:
        return None


def _clipboard_set(text: str) -> None:
    from AppKit import NSPasteboard, NSPasteboardTypeString
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)


def _chunks(text: str, units: int = 18) -> List[str]:
    """Pieces of at most `units` UTF-16 code units (what one key event carries), never splitting a character: an emoji
    or accented letter cut in half would type as garbage."""
    out, cur, n = [], "", 0
    for ch in text:
        w = len(ch.encode("utf-16-le")) // 2
        if n + w > units and cur:
            out.append(cur)
            cur, n = "", 0
        cur += ch
        n += w
    if cur:
        out.append(cur)
    return out


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
        _post(ev)
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
    bundle = _bundle_of(name)
    match = next((a for a in running_apps() if a["name"].lower() == name.lower()), None)
    app = {"name": (match or {}).get("name") or name, "bundle": bundle or (match or {}).get("bundle", "")}
    held = _gate(app)
    if held:
        return held
    r = await asyncio.to_thread(subprocess.run, ["open", "-a", name], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        return {"success": False, "error": f"Couldn't open {name}: {(r.stderr or '').strip()[:200]}"}
    # "open" can leave it behind the app that asked (macOS keeps the front app in front): bring it forward and wait
    # until it really is in front, so the next key goes to it and not to whatever was there before.
    front = await asyncio.to_thread(_bring_forward, name, app.get("bundle") or "")
    _mark_left()
    if not front:
        return {"success": False, "error": f"Opened {name}, but it didn't come to the front (it's {front_app()['name']}). Click its window, or try again."}
    return _did(f"opened {name}", app)


def _bring_forward(name: str, bundle: str, seconds: float = 6.0) -> bool:
    from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps
    deadline = time.time() + seconds
    while time.time() < deadline:
        f = front_app()
        if (bundle and f["bundle"] == bundle) or f["name"].lower() == name.lower():
            return True
        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle) if bundle else []
        if not apps:
            apps = [a for a in NSRunningApplication.runningApplicationsWithBundleIdentifier_(_bundle_of(name) or "x")] if not bundle else []
        for a in apps or []:
            a.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            # Newer macOS won't let a background program pull another app forward, but Accessibility can: make it
            # the front app and raise its window.
            try:
                import ApplicationServices as AS
                el = AS.AXUIElementCreateApplication(a.processIdentifier())
                AS.AXUIElementSetAttributeValue(el, "AXFrontmost", True)
                win = _ax(el, "AXMainWindow") or (list(_ax(el, "AXWindows") or []) or [None])[0]
                if win is not None:
                    AS.AXUIElementPerformAction(win, "AXRaise")
            except Exception:
                pass
        time.sleep(0.3)
    return False


def _bundle_of(name: str) -> str:
    try:
        r = subprocess.run(["osascript", "-e", f'id of application "{_as(name)}"'], capture_output=True, text=True, timeout=8)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


# ------------------------------------------------------------------ terminals
def _as(s: str) -> str:
    """Text as an AppleScript string: backslashes, quotes and line breaks escaped (a line break would end the command)."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n").replace("\t", "\\t")


_automation_ok: Dict[str, bool] = {}


def _osa(script: str, timeout: float = 15, app: str = "") -> str:
    """Runs AppleScript. The first time Xod talks to an app, macOS asks you ("Xod wants to control Terminal"): that
    prompt waits for you, so the first call waits longer. Refused: the Automation page opens for you."""
    first = app and not _automation_ok.get(app)
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120 if first else timeout)
    except subprocess.TimeoutExpired:
        raise DesktopError(f"{app or 'The app'} didn't answer" + (": macOS may be asking you to let Xod control it, answer OK." if first else "."))
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if "-1743" in err or "not authorized" in err.lower() or "not allowed" in err.lower():
            _open_pane("automation")
            raise DesktopError(f"macOS hasn't let Xod control {app or 'that app'}. I opened System Settings → Privacy & Security → "
                               f"Automation: under {_host_app()}, switch on {app or 'the app'}, then say continue.")
        raise DesktopError(err[:300] or "The terminal didn't answer.")
    if app:
        _automation_ok[app] = True
    return r.stdout


MAX_WINDOWS = 3          # Terminal windows Xod keeps open at once (it reuses them; past this it says so)


def owned_windows() -> List[Dict[str, Any]]:
    """The Terminal windows Xod opened that are still open (it forgets ones you closed)."""
    from app.services.storage import get_setting, set_setting
    try:
        mine = json.loads(get_setting("desktop_terminals", "") or "[]")
    except ValueError:
        mine = []
    if not mine:
        return []
    try:
        live = {(t["window"]): t for t in terminals()["terminals"] if t["app"] == "Terminal"}
    except DesktopError:
        return mine
    still = [dict(m, busy=live[m["window"]].get("busy"), running=live[m["window"]].get("running", "")) for m in mine if m["window"] in live]
    if len(still) != len(mine):
        set_setting("desktop_terminals", json.dumps([{k: v for k, v in m.items() if k not in ("busy", "running")} for m in still]))
    return still


def _remember_window(wid: int, agent: str, folder: str) -> None:
    from app.services.storage import set_setting
    mine = [m for m in owned_windows() if m["window"] != wid]
    mine.append({"window": wid, "agent": agent, "folder": folder, "at": time.time()})
    set_setting("desktop_terminals", json.dumps([{k: v for k, v in m.items() if k not in ("busy", "running")} for m in mine]))


def _forget_window(wid: int) -> None:
    from app.services.storage import set_setting
    set_setting("desktop_terminals", json.dumps([{k: v for k, v in m.items() if k not in ("busy", "running")}
                                                 for m in owned_windows() if m["window"] != wid]))


def open_terminal_with(command: str, agent: str = "", folder: str = "") -> Dict[str, Any]:
    """A Terminal window running this command, for an agent you want to watch. Reuses Xod's own windows first: the one
    already running this agent in this folder, else one of Xod's that's free (back at its prompt). Opens a new one only
    under MAX_WINDOWS, and titles it "Xod · <agent>" so it's clear which windows are Xod's."""
    _ready(need_screen=False, need_input=False)
    mine = owned_windows()
    same = next((m for m in mine if m.get("agent") == agent and m.get("folder") == folder and m.get("busy")), None)
    if same:
        return {"window": same["window"], "reused": True, "already_running": True}
    free = next((m for m in mine if not m.get("busy")), None)
    if free:
        _osa(f'tell application "Terminal" to do script "{_as(command)}" in tab 1 of window id {free["window"]}', app="Terminal")
        wid = free["window"]
        reused = True
    else:
        if len(mine) >= MAX_WINDOWS:
            raise DesktopError(f"Xod already has {len(mine)} Terminal windows open ("
                               + ", ".join(f"window {m['window']}: {m.get('agent') or 'shell'}" for m in mine)
                               + "). Stop one first, or Xod reuses one when it's free.")
        out = _osa(f'tell application "Terminal"\n activate\n do script "{_as(command)}"\n delay 0.5\n return id of front window\nend tell', app="Terminal")
        try:
            wid = int(out.strip())
        except ValueError:
            raise DesktopError("Terminal opened a window but didn't say which.")
        reused = False
    try:
        _osa(f'tell application "Terminal" to set custom title of tab 1 of window id {wid} to "{_as("Xod · " + (agent or "shell"))}"', app="Terminal")
    except DesktopError:
        pass
    _remember_window(wid, agent, folder)
    return {"window": wid, "reused": reused}


def close_terminal_window(wid: int) -> Dict[str, Any]:
    """Stops what runs in one of Xod's own windows and closes it. Windows Xod didn't open are never closed."""
    _ready(need_screen=False, need_input=False)
    wid = int(wid)
    if not any(m["window"] == wid for m in owned_windows()):
        raise DesktopError(f"Window {wid} isn't one Xod opened, so Xod leaves it alone.")
    try:
        tty = _osa(f'tell application "Terminal" to return tty of tab 1 of window id {wid}', app="Terminal").strip()
    except DesktopError:
        _forget_window(wid)
        return {"success": True, "closed": wid, "note": "It was already closed."}
    stopped = 0
    if re.match(r"^/dev/ttys\d+$", tty):
        for pid in subprocess.run(["ps", "-t", tty[5:], "-o", "pid="], capture_output=True, text=True).stdout.split():
            subprocess.run(["kill", pid], capture_output=True)
            stopped += 1
        time.sleep(1.0)
    _osa(f'tell application "Terminal" to close (every window whose id is {wid}) saving no', app="Terminal")
    _forget_window(wid)
    return {"success": True, "closed": wid, "stopped": stopped}


def _running(bundle: str) -> bool:
    from AppKit import NSRunningApplication
    return bool(NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle))


def terminals() -> Dict[str, Any]:
    """Every Terminal and iTerm window and tab that's open, with what runs in it. Never opens either app.
    Windows are named by their id, which stays the same however you rearrange them (their order changes every time
    you click one, so "window 2" could be a different window a second later)."""
    _ready(need_screen=False, need_input=False)
    out: List[Dict[str, Any]] = []
    sep = 'set sep to (character id 9)\nset text item delimiters to ","\n'      # "tab" inside a tell block means its tabs
    if _running("com.apple.Terminal"):
        raw = _osa(sep + '''tell application "Terminal"
  set out to ""
  repeat with w from 1 to count windows
    set wid to id of window w
    repeat with t from 1 to count tabs of window w
      set tb to tab t of window w
      set out to out & wid & sep & w & sep & t & sep & (busy of tb) & sep & ((processes of tb) as text) & sep & (selected of tb) & sep & (custom title of tb) & linefeed
    end repeat
  end repeat
  return out
end tell''', app="Terminal")
        for line in raw.splitlines():
            p = line.split("\t")
            if len(p) >= 6:
                out.append({"app": "Terminal", "window": int(p[0]), "front": p[1] == "1", "tab": int(p[2]), "busy": p[3] == "true",
                            "running": p[4].split(",")[-1] if p[4] else "", "processes": p[4], "selected": p[5] == "true",
                            "title": p[6] if len(p) > 6 else ""})
    if _running("com.googlecode.iterm2"):
        raw = _osa(sep + '''tell application "iTerm2"
  set out to ""
  repeat with w from 1 to count windows
    set wid to id of window w
    repeat with t from 1 to count tabs of window w
      set s to current session of tab t of window w
      set out to out & wid & sep & w & sep & t & sep & (is processing of s) & sep & (name of s) & linefeed
    end repeat
  end repeat
  return out
end tell''', app="iTerm")
        for line in raw.splitlines():
            p = line.split("\t")
            if len(p) >= 5:
                out.append({"app": "iTerm", "window": int(p[0]), "front": p[1] == "1", "tab": int(p[2]), "busy": p[3] == "true",
                            "selected": True, "title": p[4]})
    return {"success": True, "terminals": out,
            "note": "" if out else "No Terminal or iTerm window is open."}


def _pick(app: str, window: Any, tab: Any, sending: bool = False) -> Tuple[str, int, int]:
    """The tab meant: by window id (from terminals()) and tab number; with none given, the front window's tab.
    Typing needs the window named when more than one is open, so text never lands in the wrong one."""
    found = terminals()["terminals"]
    app = (app or "").lower()
    cands = [t for t in found if not app or t["app"].lower().startswith(app[:4])]
    if not cands:
        raise DesktopError("No Terminal or iTerm window is open." if not found else f"No {app} window is open.")
    w = int(window) if window not in (None, "") else None
    t = int(tab) if tab not in (None, "") else None
    if w is None and sending and len({(c["app"], c["window"]) for c in cands}) > 1:
        raise DesktopError("More than one terminal window is open: say which (its window id from terminal_list), so Xod "
                           "doesn't type into the wrong one.")
    pick = next((c for c in cands if (c["window"] == w if w is not None else c["front"])
                 and (c["tab"] == t if t is not None else c.get("selected", True))), None)
    if not pick:
        raise DesktopError(f"There's no {app or 'terminal'} window {w if w is not None else '(front)'} tab {t or '(current)'}. "
                           f"Open ones: {json.dumps([{k: c[k] for k in ('app', 'window', 'tab', 'title')} for c in found])[:500]}")
    return pick["app"], pick["window"], pick["tab"]


def terminal_read(app: str = "", window: Any = None, tab: Any = None, lines: int = 80) -> Dict[str, Any]:
    """The last lines of a terminal tab (its scrollback), as text."""
    _ready(need_screen=False, need_input=False)
    a, w, t = _pick(app, window, tab)
    if a == "Terminal":
        text = _osa(f'tell application "Terminal" to get history of tab {t} of window id {w}', app="Terminal")
    else:
        text = _osa(f'tell application "iTerm2" to get contents of current session of tab {t} of window id {w}', app="iTerm")
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
    a, w, t = _pick(app, window, tab, sending=True)
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
        busy = next((x.get("busy") for x in terminals()["terminals"] if x["app"] == "Terminal" and x["window"] == w and x["tab"] == t), False)
        if busy and enter:
            # A program runs in that tab (a full-screen agent like Muse or Claude Code): it takes text that arrives at once
            # as a paste, Return included, so it would never submit. Bring that exact window forward, paste, press Return.
            await asyncio.to_thread(_osa, f'tell application "Terminal"\n activate\n set index of window id {w} to 1\n'
                                          f' set selected of tab {t} of window id {w} to true\nend tell', 15, "Terminal")
            if not await asyncio.to_thread(_bring_forward, "Terminal", "com.apple.Terminal"):
                raise DesktopError("Terminal didn't come to the front, so Xod didn't type anything.")
            await asyncio.sleep(0.3)
            await asyncio.to_thread(_type_now, text, True)
        else:
            await asyncio.to_thread(_osa, f'tell application "Terminal" to do script "{_as(text)}" in tab {t} of window id {w}', 15, "Terminal")
    else:
        await asyncio.to_thread(_osa, f'tell application "iTerm2" to tell current session of tab {t} of window id {w} to write text "{_as(text)}"'
                                + ("" if enter else " newline no"), 15, "iTerm")
    return _did(f"sent \"{text[:60]}\" to {a} window {w} tab {t}", {"name": a})


# ------------------------------------------------------------------ for the header and the activity view
def status() -> Dict[str, Any]:
    task = _state.get("task")
    return {**permissions(), "apps": allowed_apps(), "task": {k: v for k, v in (task or {}).items() if k != "shot"} if task else None,
            "shot": (task or {}).get("shot") if task else None, "recent": _state["log"][-20:]}


def stop() -> Dict[str, Any]:
    _state["stop"] = True
    return {"success": True, "message": "Stopping."}
