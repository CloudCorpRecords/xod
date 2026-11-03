"""
Xod at the real screen on Windows (services/desktop.py), with the session, windows and accessibility tree stood
in for, and the Win32 calls recorded at the _win_api() boundary (nothing is clicked on this computer):
  - the session is reported honestly: in the signed-in desktop session it can work; in session 0 (a service or an
    SSH shell) it says so and stops there
  - off until its add-on is on; the setup card works on Windows (no "Mac for now")
  - apps that hold passwords or the computer's settings are never used (Settings, the credential broker, password
    managers, by executable name); any other app needs your OK the first time
  - typing goes by paste (ctrl+v) with your clipboard put back; never into a password field; closing/locking
    shortcuts only when asked
  - the Win32 seams themselves: virtual-key codes (cmd means the Windows key), wheel deltas, mouse flags
  - terminal windows are listed from real window data; sending types into the named one; reading exact text says
    it isn't supported on Windows yet; a UIA failure degrades to no controls, never a crash
Runs on Windows only (the Windows CI job runs it; elsewhere it skips).
  python tests/smoke/run_windows_desktop_test.py
"""
import asyncio
import ctypes
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-windesktop-"))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'ok' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


if sys.platform != "win32":
    print("skips: Windows only")
    raise SystemExit(0)


class FakeUser32:
    """The user32 half of _win_api(): records the calls the Windows backend makes."""
    def __init__(self):
        self.events = []
        self.cursor = (700.0, 300.0)

    def SetCursorPos(self, x, y):
        self.cursor = (float(x), float(y))
        self.events.append(("cursor", x, y))
        return True

    def GetCursorPos(self, ptr):
        p = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_long))
        p[0], p[1] = int(self.cursor[0]), int(self.cursor[1])
        return True

    def mouse_event(self, flags, dx, dy, data, extra):
        self.events.append(("mouse_event", flags, data))

    def keybd_event(self, vk, scan, flags, extra):
        self.events.append(("keybd", vk, flags))

    def __getattr__(self, name):
        # Anything not faked here (window enumeration, titles, rects) goes to the real user32,
        # whose signatures _win_api() has already declared.
        return getattr(ctypes.windll.user32, name)

    def GetForegroundWindow(self):
        return 0

    def IsIconic(self, hwnd):
        return False

    def ShowWindow(self, hwnd, cmd):
        return True

    def SetForegroundWindow(self, hwnd):
        return True


class FakeWindows:
    """Stands in for the Windows desktop: session, front app, elements, clipboard, launches.
    The low-level seams (_press, _post_mouse, _post_scroll, _cursor) stay real and land on FakeUser32."""
    def __init__(self, d):
        self.d = d
        self.api = FakeUser32()
        self.session = {"session_id": 1, "console_session": 1, "input_desktop": True, "interactive": True}
        self.front = {"name": "Notepad", "bundle": "notepad.exe", "pid": 42}
        self.clipboard = "your own clipboard"
        self.password_focus = False
        self.launched = []
        d._win_native = {**d._win_api(), "user32": self.api}   # real structs/session DLLs, fake user32 boundary
        d._win_session = lambda: dict(self.session)
        d._display = lambda: {"x": -1920.0, "y": 0.0, "w": 1920.0, "h": 1080.0}   # a display left of the primary
        d.front_app = lambda: self.front
        d._app_at = lambda pt: self.front
        d._focused_is_password = lambda: self.password_focus
        d._user_active = lambda: False
        d._clipboard_get = lambda: self.clipboard
        d._clipboard_set = lambda t: setattr(self, "clipboard", t)
        d._win_clipboard_clear = lambda: setattr(self, "clipboard", "")
        d._win_launch_app = lambda name: (self.launched.append(name),
                                          setattr(self, "front", {"name": name, "bundle": d._WIN_APP_EXES.get(name.lower(), ""), "pid": 43}),
                                          (True, ""))[2]

    def screenshot(self):
        from PIL import Image
        return Image.new("RGB", (1280, 720), (30, 30, 40)), 1.5   # _screenshot's contract: already scaled to SHOT_WIDTH

    def elements(self, app=None, budget=2.0):
        els = [{"n": 1, "role": "Button", "label": "Save", "password": False,
                "frame": (-1500.0, 100.0, 100.0, 40.0), "_el": None, "enabled": True},
               {"n": 2, "role": "Edit", "label": "Name", "password": False,
                "frame": (-1400.0, 200.0, 200.0, 30.0), "_el": None, "enabled": True}]
        return els, ["Some window text"]


async def main() -> None:
    from app.services import desktop, packs
    from app.services.desktop import DesktopError
    from app.agent import tools

    print("W1 off until its add-on is on; the setup card speaks Windows")
    check("off by default", not desktop.enabled())
    check("supported on Windows", desktop.SUPPORTED and desktop.WIN and not desktop.MAC)
    card = tools._desktop_setup_card({})
    check("setup card doesn't say Mac-only", "Mac for now" not in str(card.get("error") or ""), str(card)[:200])
    packs.set_enabled("desktop-control", True)
    check("on: _screen_on follows SUPPORTED", tools._screen_on())

    fake = FakeWindows(desktop)
    desktop._screenshot = fake.screenshot
    real_win_elements = desktop._win_elements
    desktop._win_elements = fake.elements
    api = fake.api

    print("W2 the session, reported honestly")
    p = desktop.permissions()
    check("in the desktop session: supported and ready", p["supported"] and p["platform"] == "windows" and p["screen"] and p["accessibility"] and p["ready"], str(p))
    check("status carries the session", desktop.status()["session_id"] == 1)
    fake.session = {"session_id": 0, "console_session": 1, "input_desktop": False, "interactive": False}
    p = desktop.permissions()
    check("session 0: not ready, and it says which session", not p["screen"] and "session 0" in p["message"], p["message"])
    try:
        desktop._ready()
        check("session 0: _ready refuses", False)
    except DesktopError as e:
        check("session 0: _ready refuses", "session" in str(e), str(e))
    check("waiting can't fix a session", await desktop.wait_for_permissions(0.2) is False)
    fake.session = {"session_id": 1, "console_session": 1, "input_desktop": True, "interactive": True}

    print("W3 which apps")
    for name, bundle in (("Settings", "systemsettings.exe"), ("a password prompt", "credentialuibroker.exe"),
                         ("1Password", "1password.exe"), ("Bitwarden", "bitwarden.exe")):
        r = desktop.check_app({"name": name, "bundle": bundle})
        check(f"{name} is never used", r and r.get("refused"), str(r))
    r = desktop.check_app({"name": "Settings", "bundle": ""})
    check("Settings by name is never used either", r and r.get("refused"), str(r))
    r = desktop.check_app(fake.front)
    check("another app needs your OK the first time", r and r.get("needs_app_ok") and r.get("grant"), str(r))
    desktop.use_grant("not-a-real-token")
    check("a made-up approval key does nothing", "notepad.exe" not in desktop.allowed_apps())
    desktop.use_grant(r["grant"])
    check("the card's key allows exactly that app", "notepad.exe" in desktop.allowed_apps())
    desktop._allow("calc.exe")
    desktop._allow("windowsterminal.exe")

    print("W4 look: screenshot, controls, negative-origin display")
    seen = await desktop.look()
    check("look works", seen["success"] and seen["app"] == "Notepad" and seen["count"] == 2, str(seen)[:200])
    check("screenshot scaled to SHOT_WIDTH", seen["size"] == [1280, 720], str(seen["size"]))
    lines = desktop.element_lines(desktop._state["elements"], desktop._state["scale"])
    check("element centres are in screenshot pixels", "[1] Button \"Save\" at (313,80)" in lines, lines)

    print("W5 click, drag, scroll at the Win32 boundary")
    api.events.clear()
    r = await desktop.click(element=1)
    me = [e for e in api.events if e[0] == "mouse_event"]
    check("click posts left down/up at the element centre", r["success"] and ("mouse_event", 0x0002, 0) in me and ("mouse_event", 0x0004, 0) in me
          and ("cursor", -1450, 120) in api.events, str(api.events))
    api.events.clear()
    r = await desktop.drag(x1=0, y1=0, x2=10, y2=10)
    me = [e[1] for e in api.events if e[0] == "mouse_event"]
    check("drag is a down, moves, and an up", me[0] == 0x0002 and me[-1] == 0x0004, str(me))
    api.events.clear()
    r = await desktop.scroll(direction="down", amount=5)
    me = [e for e in api.events if e[0] == "mouse_event"]
    check("scroll down is a negative wheel delta, 120 a line", me == [("mouse_event", 0x0800, (-600) & 0xFFFFFFFF)], str(me))

    print("W6 typing: by paste, clipboard put back, never into a password")
    api.events.clear()
    r = await desktop.type_text("hello windows")
    kb = [e for e in api.events if e[0] == "keybd"]
    check("typed by paste", r["success"] and "by paste" in r["did"], str(r))
    check("paste is ctrl+v: VK_CONTROL and VK_V go down and up", ("keybd", 0x11, 0) in kb and ("keybd", 0x56, 0) in kb and ("keybd", 0x56, 2) in kb, str(kb))
    check("your clipboard is put back", fake.clipboard == "your own clipboard", fake.clipboard)
    fake.password_focus = True
    r = await desktop.type_text("hunter2")
    check("a password field is refused", r.get("refused") is True, str(r))
    fake.password_focus = False

    print("W7 keys: dangerous ones only when asked; cmd is the Windows key")
    r = await desktop.key("alt+f4")
    check("alt+f4 without asking is refused", r.get("refused") is True, str(r))
    api.events.clear()
    r = await desktop.key("alt+f4", asked="close this app")
    kb = [e for e in api.events if e[0] == "keybd"]
    check("alt+f4 when asked for close posts VK_MENU + VK_F4", r["success"] and ("keybd", 0x12, 0) in kb and ("keybd", 0x73, 0) in kb, str(kb))
    r = await desktop.key("win+l")
    check("win+l without asking is refused", r.get("refused") is True, str(r))
    api.events.clear()
    await desktop.key("cmd+t")
    kb = [e for e in api.events if e[0] == "keybd"]
    check("cmd posts the left Windows key", ("keybd", 0x5B, 0) in kb, str(kb))
    pt = desktop._cursor()
    check("cursor reads back through GetCursorPos", pt == api.cursor, str(pt))

    print("W8 open_app")
    r = await desktop.open_app("Settings")
    check("Settings never opens", r.get("refused") is True, str(r))
    r = await desktop.open_app("Calculator")
    check("a new app is launched by name and comes to the front", r["success"] and "Calculator" in fake.launched, str(r) + str(fake.launched))
    fake.front = {"name": "Notepad", "bundle": "notepad.exe", "pid": 42}

    print("W9 terminals on Windows")
    wins = [{"app": "Windows Terminal", "title": "PowerShell", "x": 0, "y": 0, "w": 900, "h": 600,
             "_hwnd": 555, "_pid": 77, "_bundle": "windowsterminal.exe"},
            {"app": "Google Chrome", "title": "Example", "x": 0, "y": 0, "w": 900, "h": 600,
             "_hwnd": 556, "_pid": 78, "_bundle": "chrome.exe"}]
    desktop._win_windows_raw = lambda: wins
    ts = desktop.terminals()
    check("only the terminal window is listed, by its handle", len(ts["terminals"]) == 1 and ts["terminals"][0]["window"] == 555 and ts["terminals"][0]["tab"] == 1, str(ts))
    try:
        desktop.terminal_read()
        check("reading exact text says not supported yet", False)
    except DesktopError as e:
        check("reading exact text says not supported yet", "desktop_look" in str(e), str(e))
    sent = []
    desktop._type_now = lambda text, enter, app=None: sent.append((text, enter))
    desktop._bring_forward = lambda name, bundle, seconds=6.0: True
    r = await desktop.terminal_send("echo hello", window=555)
    check("send types into the named window", r["success"] and sent == [("echo hello", True)], str(r) + str(sent))

    print("W10 UIA failure degrades to no controls, never a crash")
    desktop._win_elements = real_win_elements
    desktop._win_hwnd_for_app = lambda app=None: 123

    def boom():
        raise RuntimeError("no UIA")
    desktop._win_uia_open = boom
    got = desktop._win_elements({"name": "X", "bundle": "x.exe", "pid": 1})
    check("elements degrade", got == ([], []), repr(got))


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
raise SystemExit(1 if failed else 0)
