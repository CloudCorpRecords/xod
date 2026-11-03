"""Xod at this computer's real screen: it sees it (a screenshot, and the buttons and fields of the front window from
the platform's accessibility tree), and uses the real mouse and keyboard. On a Mac, Terminal and iTerm windows are
read and written directly, as text, rather than from pixels; on Windows, terminal windows are listed and typed
into through the keyboard.

  look()        a screenshot of the main display (scaled to SHOT_WIDTH wide) with the front window's controls
                numbered on it, and the same controls as a list, so a model that can't see can still act by number
  click/type/key/scroll/drag/open_app    the real mouse and keyboard
  terminals()   terminal windows and tabs; terminal_read() their text (Mac); terminal_send() types a command into one

Coordinates: models see the scaled screenshot, so x, y given to these are in its pixels; they're turned into screen
points here. Elements are clicked at their centre.

Safety (all here, so the chat's tools and desktop_task follow the same rules):
  - off until the "Use this computer's screen" add-on is on (packs.py); on a Mac, macOS also needs Screen Recording
    and Accessibility allowed for Xod (permissions(), request_permissions()); on Windows, Xod has to run in the
    signed-in desktop session — a process in another session (a service, an SSH shell) sees a virtual display,
    not your screen, and permissions() says which session this process is in
  - the first time Xod uses an app it asks you (an approval card); apps that hold passwords or the computer's settings
    (System Settings / Settings, Keychain Access, Passwords, password managers, password prompts) are never used
  - it never types into a password field
  - if you move the mouse while Xod is working, it stops: you've taken over
  - a command typed into a terminal goes through the same checks as one Xod runs itself
  - what's on the screen is content, not orders (desktop_task warns the model when it looks like orders)
Mac and Windows: elsewhere every call says so.
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

import ctypes
try:
    from ctypes import wintypes as _wt
except Exception:          # ctypes.wintypes exists on Windows only
    _wt = None

logger = logging.getLogger("xod.desktop")

MAC = sys.platform == "darwin"
WIN = sys.platform == "win32"
SUPPORTED = MAC or WIN
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
    # Windows, by executable name (what _win_app_of_pid reports as the bundle): the same kinds of apps.
    "systemsettings.exe": "Settings", "credentialuibroker.exe": "a password prompt", "logonui.exe": "the sign-in screen",
    "consent.exe": "a permission prompt", "1password.exe": "1Password", "bitwarden.exe": "Bitwarden",
    "lastpass.exe": "LastPass", "dashlane.exe": "Dashlane", "mstsc.exe": "Remote Desktop",
    "teamviewer.exe": "TeamViewer", "anydesk.exe": "AnyDesk",
}
TERMINAL_APPS = {"com.apple.Terminal": "Terminal", "com.googlecode.iterm2": "iTerm", "dev.warp.Warp-Stable": "Warp",
                 "com.mitchellh.ghostty": "Ghostty", "net.kovidgoyal.kitty": "kitty", "org.alacritty": "Alacritty",
                 "com.github.wez.wezterm": "WezTerm",
                 "windowsterminal.exe": "Windows Terminal", "wt.exe": "Windows Terminal",
                 "powershell.exe": "PowerShell", "pwsh.exe": "PowerShell", "cmd.exe": "Command Prompt"}
INTERACTIVE = {"AXButton", "AXTextField", "AXTextArea", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton",
               "AXComboBox", "AXSlider", "AXLink", "AXMenuItem", "AXMenuBarItem", "AXTab", "AXTabButton", "AXDisclosureTriangle",
               "AXIncrementor", "AXColorWell", "AXSearchField", "AXCell", "AXRow", "AXSegmentedControl", "AXSwitch", "AXToggle"}
# Windows UI Automation control types (UIA_*ControlTypeId) in the shape elements() returns on a Mac.
WIN_CONTROL_ROLES = {50000: "Button", 50002: "CheckBox", 50003: "ComboBox", 50004: "Edit", 50008: "ListItem",
                     50011: "MenuItem", 50013: "RadioButton", 50014: "ScrollBar", 50015: "Slider", 50017: "TabItem",
                     50018: "TextField", 50019: "ToolBar", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
                     50031: "SplitButton", 50032: "Tab", 50036: "Thumb", 50037: "DataItem", 50038: "HeaderItem"}
WIN_TEXT_TYPES = {50020, 50030}          # UIA Text / Document: their words are read as screen text, like AXStaticText
WIN_TERMINAL_EXES = {"windowsterminal.exe", "wt.exe", "powershell.exe", "pwsh.exe", "cmd.exe"}
WIN_RISKY_KEYS = {"alt+f4", "ctrl+shift+escape", "ctrl+shift+esc", "win+l", "windows+l", "ctrl+alt+delete", "ctrl+alt+del"}
WIN_MODS = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "option": 0x12,
            "win": 0x5B, "windows": 0x5B, "cmd": 0x5B, "command": 0x5B}
WIN_KEYCODES = {**{chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)}, **{str(i): ord(str(i)) for i in range(10)},
                ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0, "[": 0xDB,
                "\\": 0xDC, "]": 0xDD, "'": 0xDE, "return": 0x0D, "enter": 0x0D, "tab": 0x09, "space": 0x20,
                "backspace": 0x08, "delete": 0x2E, "forwarddelete": 0x2E, "escape": 0x1B, "esc": 0x1B,
                "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26,
                "right": 0x27, "down": 0x28, **{f"f{i}": 0x70 + i - 1 for i in range(1, 13)}}

_state: Dict[str, Any] = {"left_at": None, "left_time": 0.0, "elements": [], "shot": None, "stop": False,
                          "task": None, "log": []}
_grants: Dict[str, Dict[str, str]] = {}      # approval token → the app it lets Xod use


# ------------------------------------------------------------------ Windows: session, display, apps, windows, input
# Pure ctypes (user32/kernel32/ole32/oleaut32) plus Pillow for the screenshot: no new dependencies. Every
# signature is declared with pointer-sized types in _win_api() — a handle passed through a default C int
# truncates on 64-bit Windows. UI Automation is driven through its COM vtable; the indices used here
# (IUIAutomation ElementFromHandle 6, ControlViewWalker 14, GetFocusedElement 8; IUIAutomationElement
# GetCurrentPropertyValue 10; TreeWalker GetFirstChildElement 4 / GetNextSiblingElement 6) were verified
# empirically against the live desktop before this code was written.
_win_native: Optional[Dict[str, Any]] = None


def _win_api() -> Dict[str, Any]:
    global _win_native
    if _win_native is not None:
        return _win_native
    if not WIN or _wt is None:
        raise DesktopError("Windows desktop control is only available on Windows.")
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    ole32, oleaut = ctypes.windll.ole32, ctypes.windll.oleaut32
    V, B, D, U = ctypes.c_void_p, _wt.BOOL, _wt.DWORD, ctypes.c_uint
    kernel32.GetCurrentProcessId.restype, kernel32.GetCurrentProcessId.argtypes = D, []
    kernel32.ProcessIdToSessionId.restype, kernel32.ProcessIdToSessionId.argtypes = B, [D, ctypes.POINTER(D)]
    kernel32.WTSGetActiveConsoleSessionId.restype, kernel32.WTSGetActiveConsoleSessionId.argtypes = D, []
    kernel32.OpenProcess.restype, kernel32.OpenProcess.argtypes = V, [D, B, D]
    kernel32.QueryFullProcessImageNameW.restype, kernel32.QueryFullProcessImageNameW.argtypes = B, [V, D, _wt.LPWSTR, ctypes.POINTER(D)]
    kernel32.CloseHandle.restype, kernel32.CloseHandle.argtypes = B, [V]
    kernel32.GlobalAlloc.restype, kernel32.GlobalAlloc.argtypes = V, [U, ctypes.c_size_t]
    kernel32.GlobalLock.restype, kernel32.GlobalLock.argtypes = V, [V]
    kernel32.GlobalUnlock.restype, kernel32.GlobalUnlock.argtypes = B, [V]
    kernel32.GlobalFree.restype, kernel32.GlobalFree.argtypes = V, [V]
    kernel32.GetTickCount64.restype, kernel32.GetTickCount64.argtypes = ctypes.c_ulonglong, []
    user32.OpenInputDesktop.restype, user32.OpenInputDesktop.argtypes = V, [D, B, D]
    user32.CloseDesktop.restype, user32.CloseDesktop.argtypes = B, [V]
    user32.GetForegroundWindow.restype, user32.GetForegroundWindow.argtypes = V, []
    user32.GetWindowThreadProcessId.restype, user32.GetWindowThreadProcessId.argtypes = D, [V, ctypes.POINTER(D)]
    user32.GetWindowTextLengthW.restype, user32.GetWindowTextLengthW.argtypes = ctypes.c_int, [V]
    user32.GetWindowTextW.restype, user32.GetWindowTextW.argtypes = ctypes.c_int, [V, _wt.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.restype, user32.GetWindowRect.argtypes = B, [V, V]
    user32.EnumWindows.restype, user32.EnumWindows.argtypes = B, [V, _wt.LPARAM]
    user32.IsWindowVisible.restype, user32.IsWindowVisible.argtypes = B, [V]
    user32.MonitorFromPoint.restype, user32.MonitorFromPoint.argtypes = V, [_wt.POINT, D]
    user32.GetMonitorInfoW.restype, user32.GetMonitorInfoW.argtypes = B, [V, V]
    user32.GetCursorPos.restype, user32.GetCursorPos.argtypes = B, [V]
    user32.SetCursorPos.restype, user32.SetCursorPos.argtypes = B, [ctypes.c_int, ctypes.c_int]
    user32.WindowFromPoint.restype, user32.WindowFromPoint.argtypes = V, [_wt.POINT]
    user32.GetAncestor.restype, user32.GetAncestor.argtypes = V, [V, U]
    user32.mouse_event.restype, user32.mouse_event.argtypes = None, [D, D, D, D, ctypes.c_size_t]
    user32.keybd_event.restype, user32.keybd_event.argtypes = None, [_wt.BYTE, _wt.BYTE, D, ctypes.c_size_t]
    user32.GetLastInputInfo.restype, user32.GetLastInputInfo.argtypes = B, [V]
    user32.GetGUIThreadInfo.restype, user32.GetGUIThreadInfo.argtypes = B, [D, V]
    user32.GetWindowLongW.restype, user32.GetWindowLongW.argtypes = ctypes.c_long, [V, ctypes.c_int]
    user32.OpenClipboard.restype, user32.OpenClipboard.argtypes = B, [V]
    user32.CloseClipboard.restype, user32.CloseClipboard.argtypes = B, []
    user32.EmptyClipboard.restype, user32.EmptyClipboard.argtypes = B, []
    user32.GetClipboardData.restype, user32.GetClipboardData.argtypes = V, [U]
    user32.SetClipboardData.restype, user32.SetClipboardData.argtypes = V, [U, V]
    user32.ShowWindow.restype, user32.ShowWindow.argtypes = B, [V, ctypes.c_int]
    user32.SetForegroundWindow.restype, user32.SetForegroundWindow.argtypes = B, [V]
    user32.IsIconic.restype, user32.IsIconic.argtypes = B, [V]
    oleaut.SysStringLen.restype, oleaut.SysStringLen.argtypes = U, [V]
    oleaut.VariantClear.restype, oleaut.VariantClear.argtypes = ctypes.c_long, [V]
    oleaut.SafeArrayAccessData.restype, oleaut.SafeArrayAccessData.argtypes = ctypes.c_long, [V, ctypes.POINTER(V)]
    oleaut.SafeArrayUnaccessData.restype, oleaut.SafeArrayUnaccessData.argtypes = ctypes.c_long, [V]
    ole32.CLSIDFromString.restype, ole32.CLSIDFromString.argtypes = ctypes.c_long, [ctypes.c_wchar_p, V]
    ole32.CoInitializeEx.restype, ole32.CoInitializeEx.argtypes = ctypes.c_long, [V, D]
    ole32.CoUninitialize.restype, ole32.CoUninitialize.argtypes = None, []
    ole32.CoCreateInstance.restype, ole32.CoCreateInstance.argtypes = ctypes.c_long, [V, V, D, V, ctypes.POINTER(V)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", D), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", D)]

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", U), ("dwTime", D)]

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", D), ("flags", D), ("hwndActive", V), ("hwndFocus", V), ("hwndCapture", V),
                    ("hwndMenuOwner", V), ("hwndMoveSize", V), ("hwndCaret", V), ("rcCaret", RECT)]

    class VARIANT(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                    ("data", V), ("pad", V)]

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

    _win_native = {"user32": user32, "kernel32": kernel32, "ole32": ole32, "oleaut": oleaut,
                   "RECT": RECT, "MONITORINFO": MONITORINFO, "LASTINPUTINFO": LASTINPUTINFO,
                   "GUITHREADINFO": GUITHREADINFO, "VARIANT": VARIANT, "GUID": GUID}
    return _win_native


def _win_session() -> Dict[str, Any]:
    # Windows keeps each sign-in in its own session: a process in session 0 (a service, an SSH shell) sees a
    # virtual display, not the person's screen. Saying which session this process is in is the honest answer.
    w = _win_api()
    kernel32 = w["kernel32"]
    sid = _wt.DWORD(0)
    kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(sid))
    console = kernel32.WTSGetActiveConsoleSessionId()
    console = None if console == 0xFFFFFFFF else int(console)
    input_ok = False
    try:
        h = w["user32"].OpenInputDesktop(0, False, 0x0001)          # DESKTOP_READOBJECTS
        if h:
            input_ok = True
            w["user32"].CloseDesktop(h)
    except Exception:
        pass
    return {"session_id": int(sid.value), "console_session": console, "input_desktop": input_ok,
            "interactive": bool(input_ok and sid.value != 0 and (console is None or int(sid.value) == console))}


def _win_host_path() -> str:
    path = sys.executable or ""
    parts = path.replace("/", "\\").split("\\")
    for i, part in enumerate(parts):
        if part.lower() == "xod-desktop":
            candidate = "\\".join(parts[:i + 1]) + "\\Xod.exe"
            if os.path.exists(candidate):
                return candidate
    return path


def _win_permissions() -> Dict[str, Any]:
    info = _win_session()
    ok = bool(info["interactive"])
    message = ("Windows is letting Xod use the signed-in desktop session." if ok else
               f"Xod is running outside the signed-in Windows desktop session (this process is in session "
               f"{info['session_id']}; the console session is {info['console_session']}), so it can't see or "
               "control your real screen. Start Xod from that desktop session.")
    return {"supported": True, "platform": "windows", "screen": ok, "accessibility": ok, "enabled": enabled(),
            "ready": ok and enabled(), "app": "Xod", "app_path": _win_host_path(), "message": message, **info}


def _win_display() -> Dict[str, float]:
    w = _win_api()
    mi = w["MONITORINFO"]()
    mi.cbSize = ctypes.sizeof(mi)
    hmon = w["user32"].MonitorFromPoint(_wt.POINT(0, 0), 2)          # MONITOR_DEFAULTTOPRIMARY
    if not hmon or not w["user32"].GetMonitorInfoW(hmon, ctypes.byref(mi)):
        raise DesktopError("Windows didn't give Xod the display.")
    r = mi.rcMonitor
    return {"x": float(r.left), "y": float(r.top), "w": float(r.right - r.left), "h": float(r.bottom - r.top)}


_WIN_APP_NAMES = {"chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "explorer.exe": "File Explorer",
                  "windowsterminal.exe": "Windows Terminal", "wt.exe": "Windows Terminal", "notepad.exe": "Notepad",
                  "xod.exe": "Xod", "code.exe": "Visual Studio Code", "firefox.exe": "Firefox",
                  "powershell.exe": "PowerShell", "pwsh.exe": "PowerShell", "cmd.exe": "Command Prompt",
                  "systemsettings.exe": "Settings", "winword.exe": "Word", "excel.exe": "Excel",
                  "powerpnt.exe": "PowerPoint", "outlook.exe": "Outlook", "teams.exe": "Teams", "slack.exe": "Slack",
                  "spotify.exe": "Spotify", "discord.exe": "Discord", "telegram.exe": "Telegram"}


def _win_app_of_pid(pid: int) -> Optional[Dict[str, Any]]:
    if not pid:
        return None
    w = _win_api()
    kernel32 = w["kernel32"]
    path = ""
    h = kernel32.OpenProcess(0x1000, False, int(pid))                # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = _wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                path = buf.value
        finally:
            kernel32.CloseHandle(h)
    exe = os.path.basename(path or "").lower()
    if not exe:
        return {"name": "", "bundle": "", "pid": int(pid), "path": path}
    return {"name": _WIN_APP_NAMES.get(exe, os.path.splitext(exe)[0].replace("_", " ").title()),
            "bundle": exe, "pid": int(pid), "path": path}


def _win_windows_raw() -> List[Dict[str, Any]]:
    w = _win_api()
    user32 = w["user32"]
    RECT = w["RECT"]
    out: List[Dict[str, Any]] = []
    try:
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
        dwmapi.DwmGetWindowAttribute.argtypes = [ctypes.c_void_p, _wt.DWORD, ctypes.c_void_p, _wt.DWORD]
    except Exception:
        dwmapi = None

    @ctypes.WINFUNCTYPE(_wt.BOOL, _wt.HWND, _wt.LPARAM)
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if dwmapi is not None:
            cloaked = ctypes.c_int(0)
            if dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
                return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        if rect.right - rect.left < 60 or rect.bottom - rect.top < 40:
            return True
        pid = _wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app = _win_app_of_pid(int(pid.value)) or {"name": "", "bundle": ""}
        out.append({"app": app["name"], "title": buf.value[:120], "x": int(rect.left), "y": int(rect.top),
                    "w": int(rect.right - rect.left), "h": int(rect.bottom - rect.top),
                    "_hwnd": int(hwnd), "_pid": int(pid.value), "_bundle": app.get("bundle", "")})
        return True

    user32.EnumWindows(cb, 0)
    return out


def _win_hwnd_for_pid(pid: int) -> int:
    wins = [w for w in _win_windows_raw() if w["_pid"] == int(pid or 0)]
    if not wins:
        return 0
    return max(wins, key=lambda w: w["w"] * w["h"])["_hwnd"]


def _win_hwnd_for_app(app: Optional[Dict[str, Any]] = None) -> int:
    app = app or front_app()
    fg = _win_api()["user32"].GetForegroundWindow()
    if fg:
        pid = _wt.DWORD(0)
        _win_api()["user32"].GetWindowThreadProcessId(fg, ctypes.byref(pid))
        if int(pid.value) == int(app.get("pid") or 0):
            return int(fg)
    return _win_hwnd_for_pid(int(app.get("pid") or 0))


def _win_screenshot() -> Tuple[Any, float]:
    from PIL import Image, ImageGrab
    try:
        img = ImageGrab.grab().convert("RGB")
    except Exception as e:
        raise DesktopError(f"Windows didn't give Xod the screen: {e}")
    if img.width <= 0 or img.height <= 0:
        raise DesktopError("Windows didn't give Xod the screen.")
    d = _win_display()
    if img.width > SHOT_WIDTH:
        img = img.resize((SHOT_WIDTH, round(img.height * SHOT_WIDTH / img.width)), Image.LANCZOS)
    return img, d["w"] / img.width


def _win_focus_window(hwnd: int) -> bool:
    if not hwnd:
        return False
    user32 = _win_api()["user32"]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                                   # SW_RESTORE
    else:
        user32.ShowWindow(hwnd, 5)                                   # SW_SHOW
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    return int(user32.GetForegroundWindow() or 0) == int(hwnd)


def _win_clipboard_open(user32) -> bool:
    for _ in range(20):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.025)
    return False


def _win_clipboard_get() -> Optional[str]:
    w = _win_api()
    user32, kernel32 = w["user32"], w["kernel32"]
    if not _win_clipboard_open(user32):
        return None
    try:
        h = user32.GetClipboardData(13)                              # CF_UNICODETEXT
        if not h:
            return None
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def _win_clipboard_set(text: str) -> None:
    w = _win_api()
    user32, kernel32 = w["user32"], w["kernel32"]
    if not _win_clipboard_open(user32):
        raise DesktopError("Xod couldn't use the clipboard: another app is holding it.")
    h = None
    try:
        user32.EmptyClipboard()
        data = (str(text) + "\0").encode("utf-16-le")
        h = kernel32.GlobalAlloc(0x0002, len(data))                  # GMEM_MOVEABLE
        if not h:
            raise DesktopError("Xod couldn't use the clipboard.")
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            raise DesktopError("Xod couldn't use the clipboard.")
        try:
            ctypes.memmove(ptr, data, len(data))
        finally:
            kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(13, h):
            raise DesktopError("Xod couldn't use the clipboard.")
        h = None                                                     # Windows owns it now
    finally:
        if h:
            kernel32.GlobalFree(h)
        user32.CloseClipboard()


def _win_clipboard_clear() -> None:
    user32 = _win_api()["user32"]
    if _win_clipboard_open(user32):
        try:
            user32.EmptyClipboard()
        finally:
            user32.CloseClipboard()


def _win_guid(text: str):
    w = _win_api()
    g = w["GUID"]()
    w["ole32"].CLSIDFromString(text, ctypes.byref(g))
    return g


def _win_vcall(obj, index: int, restype, argtypes, args):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])
    return fn(obj, *args)


def _win_release(obj) -> None:
    if obj:
        try:
            _win_vcall(obj, 2, ctypes.c_ulong, [], [])
        except Exception:
            pass


def _win_uia_open():
    w = _win_api()
    ole32 = w["ole32"]
    hr = ole32.CoInitializeEx(None, 2)                               # COINIT_APARTMENTTHREADED
    uninit = hr in (0, 1)
    if hr < 0 and hr != -2147417850:                                 # RPC_E_CHANGED_MODE: already in an apartment
        raise DesktopError("Windows accessibility (UI Automation) didn't start.")
    uia = ctypes.c_void_p()
    clsid = _win_guid("{ff48dba4-60ef-4201-aa87-54103eef594e}")
    iid = _win_guid("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")
    hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(uia))
    if hr != 0 or not uia:
        if uninit:
            ole32.CoUninitialize()
        raise DesktopError("Windows accessibility (UI Automation) didn't start.")
    return uia, uninit


def _win_uia_prop(el, prop_id: int):
    w = _win_api()
    oleaut = w["oleaut"]
    var = w["VARIANT"]()
    try:
        hr = _win_vcall(el, 10, ctypes.c_long, [ctypes.c_int, ctypes.POINTER(w["VARIANT"])], [prop_id, ctypes.byref(var)])
        if hr != 0:
            return None
        vt, data = var.vt, var.data
        if vt == 8 and data:                                         # BSTR
            return ctypes.wstring_at(data, oleaut.SysStringLen(data))
        if vt == 3:
            return ctypes.cast(ctypes.byref(var, 8), ctypes.POINTER(ctypes.c_int)).contents.value
        if vt == 19:
            return ctypes.cast(ctypes.byref(var, 8), ctypes.POINTER(ctypes.c_uint)).contents.value
        if vt == 11:
            return ctypes.cast(ctypes.byref(var, 8), ctypes.POINTER(ctypes.c_short)).contents.value != 0
        if vt == 0x2005 and data:                                    # SAFEARRAY of doubles: x, y, width, height
            ptr = ctypes.c_void_p()
            if oleaut.SafeArrayAccessData(data, ctypes.byref(ptr)) == 0 and ptr.value:
                try:
                    arr = (ctypes.c_double * 4).from_address(ptr.value)
                    return [float(arr[i]) for i in range(4)]
                finally:
                    oleaut.SafeArrayUnaccessData(data)
        return None
    except Exception:
        return None
    finally:
        try:
            oleaut.VariantClear(ctypes.byref(var))
        except Exception:
            pass


def _win_elements(app: Optional[Dict[str, Any]] = None, budget: float = 2.0) -> Tuple[List[Dict[str, Any]], List[str]]:
    # The front window's controls from UI Automation, in the same shape elements() returns on a Mac.
    # Any failure degrades to no controls: the screenshot and window list still work.
    found: List[Dict[str, Any]] = []
    texts: List[str] = []
    hwnd = _win_hwnd_for_app(app)
    if not hwnd:
        return found, texts
    uia = walker = root = None
    owned: List[Any] = []
    uninit = False
    try:
        uia, uninit = _win_uia_open()
        root = ctypes.c_void_p()
        hr = _win_vcall(uia, 6, ctypes.c_long, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                        [hwnd, ctypes.byref(root)])                  # ElementFromHandle (index verified live)
        if hr != 0 or not root:
            return found, texts
        owned.append(root)
        walker = ctypes.c_void_p()
        hr = _win_vcall(uia, 14, ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)], [ctypes.byref(walker)])
        if hr != 0 or not walker:                                    # ControlViewWalker (index verified live)
            return found, texts
        deadline = time.time() + budget
        stack = [(root, 0)]
        seen = 0
        while stack and seen < 4000 and time.time() < deadline:
            el, depth = stack.pop()
            seen += 1
            if el != root and _win_uia_prop(el, 30022) is True:      # IsOffscreen
                continue
            rect = _win_uia_prop(el, 30001)                          # BoundingRectangle
            ctype = _win_uia_prop(el, 30003)                         # ControlType
            name = _win_uia_prop(el, 30005) or ""
            name = re.sub(r"\s+", " ", str(name).strip())[:80]
            frame = None
            if isinstance(rect, list) and len(rect) == 4 and rect[2] > 1 and rect[3] > 1:
                frame = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
            if frame is not None:
                if ctype in WIN_TEXT_TYPES:
                    value = _win_uia_prop(el, 30045)                 # Value pattern value
                    text = name or (str(value).strip()[:300] if ctype == 50030 and value else "")
                    if text and len(texts) < 80:
                        texts.append(text)
                role = WIN_CONTROL_ROLES.get(int(ctype or 0))
                if role and len(found) < MAX_ELEMENTS:
                    label = name
                    if not label:
                        value = _win_uia_prop(el, 30045)
                        if isinstance(value, str) and len(value) <= 80:
                            label = re.sub(r"\s+", " ", value.strip())
                    if not label:
                        label = str(_win_uia_prop(el, 30013) or "")[:80]     # HelpText
                    found.append({"n": len(found) + 1, "role": role, "label": label,
                                  "password": _win_uia_prop(el, 30019) is True, "frame": frame, "_el": None,
                                  "enabled": _win_uia_prop(el, 30010) is not False})
            if depth < 30 and len(found) < MAX_ELEMENTS:
                child = ctypes.c_void_p()
                _win_vcall(walker, 4, ctypes.c_long, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                           [el, ctypes.byref(child)])
                kids = []
                while child:
                    kids.append(child)
                    owned.append(child)
                    nxt = ctypes.c_void_p()
                    _win_vcall(walker, 6, ctypes.c_long, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                               [child, ctypes.byref(nxt)])
                    child = nxt
                for kid in reversed(kids):
                    stack.append((kid, depth + 1))
    except Exception:
        return [], []
    finally:
        for obj in reversed(owned):
            _win_release(obj)
        _win_release(walker)
        _win_release(uia)
        if uninit:
            try:
                _win_api()["ole32"].CoUninitialize()
            except Exception:
                pass
    return found, texts


def _win_focused_is_password() -> bool:
    # UI Automation's focused element first (index 8 verified live); a classic Win32 password edit
    # (ES_PASSWORD style) as the fallback.
    uia = None
    uninit = False
    try:
        uia, uninit = _win_uia_open()
        el = ctypes.c_void_p()
        hr = _win_vcall(uia, 8, ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)], [ctypes.byref(el)])
        if hr == 0 and el:
            try:
                return _win_uia_prop(el, 30019) is True              # IsPassword
            finally:
                _win_release(el)
    except Exception:
        pass
    finally:
        _win_release(uia)
        if uninit:
            try:
                _win_api()["ole32"].CoUninitialize()
            except Exception:
                pass
    try:
        w = _win_api()
        user32 = w["user32"]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = _wt.DWORD(0)
        tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        info = w["GUITHREADINFO"]()
        info.cbSize = ctypes.sizeof(info)
        if tid and user32.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus:
            style = user32.GetWindowLongW(info.hwndFocus, -16)       # GWL_STYLE; ES_PASSWORD = 0x20
            return bool(style & 0x20)
    except Exception:
        pass
    return False


_WIN_APP_EXES = {"notepad": "notepad.exe", "calculator": "calc.exe", "paint": "mspaint.exe",
                 "file explorer": "explorer.exe", "explorer": "explorer.exe", "google chrome": "chrome.exe",
                 "chrome": "chrome.exe", "microsoft edge": "msedge.exe", "edge": "msedge.exe",
                 "windows terminal": "wt.exe", "terminal": "wt.exe", "powershell": "powershell.exe",
                 "command prompt": "cmd.exe", "visual studio code": "code.exe", "word": "winword.exe",
                 "excel": "excel.exe", "powerpoint": "powerpnt.exe", "outlook": "outlook.exe",
                 "spotify": "spotify.exe", "settings": "systemsettings.exe"}


def _win_launch_app(name: str) -> Tuple[bool, str]:
    key = name.strip().lower()
    exe = _WIN_APP_EXES.get(key) or (os.path.basename(name).lower() if key.endswith(".exe") else "")
    if exe:
        try:
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
            return True, ""
        except FileNotFoundError:
            pass
        except Exception as e:
            return False, str(e)
    # Start-menu and Store apps by their registered name; the name travels in an environment variable,
    # never inside the script text.
    script = ("$q = $env:XOD_APP_QUERY; $a = Get-StartApps | Where-Object { $_.Name -eq $q } | Select-Object -First 1; "
              "if ($a) { Start-Process ('shell:AppsFolder\\' + $a.AppID); exit 0 }; exit 2")
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                           env={**os.environ, "XOD_APP_QUERY": name}, capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            return True, ""
        return False, (r.stderr or "no app by that name").strip()
    except Exception as e:
        return False, str(e)


async def _win_open_app(name: str) -> Dict[str, Any]:
    bundle = _WIN_APP_EXES.get(name.strip().lower(), "")
    match = next((a for a in running_apps() if a["name"].lower() == name.lower()), None)
    app = {"name": (match or {}).get("name") or name, "bundle": bundle or (match or {}).get("bundle", "")}
    held = _gate(app)
    if held:
        return held
    if match:
        _win_focus_window(_win_hwnd_for_pid(int(match["pid"])))
    else:
        ok, err = _win_launch_app(name)
        if not ok:
            return {"success": False, "error": f"Couldn't open {name}: {err or 'no app by that name'}"}
        deadline = time.time() + 12
        while time.time() < deadline:                        # a big app can take a while to make a window
            f = front_app()
            if f.get("name", "").lower() == name.lower() or (bundle and f.get("bundle") == bundle):
                break
            pid = _first_pid(bundle)
            if pid and _win_focus_window(_win_hwnd_for_pid(pid)):
                break
            await asyncio.sleep(0.4)
    _mark_left()
    f = front_app()
    if not ((bundle and f.get("bundle") == bundle) or f.get("name", "").lower() == name.lower()):
        return {"success": False, "error": f"Opened {name}, but it didn't come to the front (it's {f.get('name')}). Click its window, or try again."}
    return _did(f"opened {name}", app)


def _first_pid(bundle: str) -> int:
    wins = [w for w in _win_windows_raw() if bundle and w["_bundle"] == bundle]
    return wins[0]["_pid"] if wins else 0


def _win_terminals() -> Dict[str, Any]:
    fg = _win_api()["user32"].GetForegroundWindow()
    out = []
    for w in _win_windows_raw():
        if w["_bundle"] not in WIN_TERMINAL_EXES:
            continue
        out.append({"app": w["app"], "window": w["_hwnd"], "front": int(fg or 0) == w["_hwnd"], "tab": 1,
                    "busy": False, "running": w["_bundle"], "processes": w["_bundle"], "selected": True,
                    "title": w["title"], "bundle": w["_bundle"], "pid": w["_pid"]})
    return {"success": True, "terminals": out,
            "note": "" if out else "No Windows Terminal, PowerShell or Command Prompt window is open."}


class DesktopError(Exception):
    pass


# ------------------------------------------------------------------ switched on, allowed by macOS, allowed by you
def enabled() -> bool:
    from app.services import packs
    return PACK in packs.enabled()


def permissions() -> Dict[str, Any]:
    """What this computer allows Xod: on a Mac, Screen Recording (to see) and Accessibility (to use the mouse and
    keyboard); on Windows, running inside the signed-in desktop session, which allows both at once."""
    if WIN:
        return _win_permissions()
    if not MAC:
        return {"supported": False, "screen": False, "accessibility": False,
                "message": "Using the screen, mouse and keyboard works on a Mac or Windows for now."}
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
    if not MAC:
        return
    subprocess.Popen(["open", f"x-apple.systempreferences:com.apple.preference.security?{PANES[which]}"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _asked[which] = time.time()


def request_permissions(force: bool = True) -> Dict[str, Any]:
    """Asks for what's missing. On a Mac: its own prompts first, then the exact Settings page, one at a time (the
    screen first), so you only have to flip a switch. On Windows there is no switch: either Xod runs in your
    desktop session or it doesn't, and this says which."""
    if WIN:
        p = permissions()
        need = [n for n, ok in (("Desktop session", p["screen"]), ("Mouse and keyboard", p["accessibility"])) if not ok]
        return {**p, "opened": [], "missing": need,
                "message": "Everything's allowed." if not need else p["message"]}
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
           f"I opened System Settings \u2192 Privacy & Security \u2192 {need[0]} for you: switch on {p['app']} there"
           + (f" (if it isn't listed, press + and pick {p['app_path']})" if p['app_path'] else "") + ". "
           + ("Then the same for Accessibility. " if len(need) > 1 else "") + "Xod notices by itself and carries on."
           + (f" (This Xod was started from {p['app']}, so macOS asks for {p['app']}; in the Xod app it's listed as Xod.)" if p["app"] != "Xod" else ""))
    return {**p, "opened": opened, "missing": need, "message": msg}


async def wait_for_permissions(seconds: float, need_screen: bool = True, need_input: bool = True, on_wait=None) -> bool:
    """Opens what's missing and waits (up to seconds) for you to switch it on; the next page opens as each one is done."""
    if WIN:
        # A running Windows process can't move itself into the desktop session, so waiting can't help.
        p = permissions()
        return all(ok for _w, ok, need in (("screen", p["screen"], need_screen), ("accessibility", p["accessibility"], need_input)) if need)
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
    if not SUPPORTED:
        raise DesktopError("Using the screen, mouse and keyboard works on a Mac or Windows for now.")
    if not enabled():
        raise DesktopError("Using this computer's screen is off. Turn on \"Use this computer's screen\" in Add-ons first.")
    p = permissions()
    missing = [n for n, ok, need in (("Screen Recording", p["screen"], need_screen), ("Accessibility", p["accessibility"], need_input)) if need and not ok]
    if missing:
        if WIN:
            raise DesktopError(p.get("message") or "Xod isn't running in your signed-in Windows desktop session.")
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
    if not bundle or bundle in allowed_apps() or (bundle == "dev.xod.desktop" or (WIN and bundle == "xod.exe")):
        return None
    token = uuid.uuid4().hex
    _grants[token] = {"bundle": bundle, "name": name}
    return {"needs_app_ok": True, "grant": token, "app": name, "bundle": bundle}


# ------------------------------------------------------------------ the screen
def _display() -> Dict[str, float]:
    if WIN:
        return _win_display()
    import Quartz
    b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    return {"x": b.origin.x, "y": b.origin.y, "w": b.size.width, "h": b.size.height}


def _app_of_pid(pid: int) -> Optional[Dict[str, Any]]:
    if WIN:
        return _win_app_of_pid(pid)
    from AppKit import NSRunningApplication
    a = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if a is None:
        return None
    return {"name": str(a.localizedName() or ""), "bundle": str(a.bundleIdentifier() or ""), "pid": int(pid)}


def front_app() -> Dict[str, Any]:
    """The app in front right now. NSWorkspace's answer goes stale in a program with no window of its own (it only
    updates on its run loop, which Xod's server doesn't run), so ask Accessibility, then the window list."""
    if WIN:
        user32 = _win_api()["user32"]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"name": "", "bundle": "", "pid": 0}
        pid = _wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return _win_app_of_pid(int(pid.value)) or {"name": "", "bundle": "", "pid": int(pid.value)}
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
    if WIN:
        out, seen = [], set()
        for w in _win_windows_raw():
            if w["_pid"] in seen:
                continue
            seen.add(w["_pid"])
            a = _win_app_of_pid(w["_pid"])
            if a and a["name"]:
                out.append(a)
        return out
    from AppKit import NSWorkspace
    out = []
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        if a.activationPolicy() == 0:          # regular apps, with windows and a Dock icon
            out.append({"name": str(a.localizedName() or ""), "bundle": str(a.bundleIdentifier() or ""), "pid": int(a.processIdentifier())})
    return out


def windows() -> List[Dict[str, Any]]:
    """Windows on screen, front first: their app, title and where they are."""
    if WIN:
        return [{k: w[k] for k in ("app", "title", "x", "y", "w", "h")} for w in _win_windows_raw()][:20]
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
    if WIN:
        return _win_screenshot()
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
    """The front window's controls (numbered from 1) and some of its text, from the platform accessibility tree."""
    if WIN:
        return _win_elements(app, budget)
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
    if WIN:
        pt = _wt.POINT()
        _win_api()["user32"].GetCursorPos(ctypes.byref(pt))
        return (float(pt.x), float(pt.y))
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
    if WIN:
        user32 = _win_api()["user32"]
        user32.SetCursorPos(int(round(pt[0])), int(round(pt[1])))
        if kind in ("move", "drag"):
            return          # a drag's button is already down from the "down" call before it
        flags = {("down", "left"): 0x0002, ("up", "left"): 0x0004,
                 ("down", "right"): 0x0008, ("up", "right"): 0x0010}[(kind, button)]
        user32.mouse_event(flags, 0, 0, 0, 0)
        return
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


def _post_scroll(dy: float, dx: float) -> None:
    if WIN:
        user32 = _win_api()["user32"]
        if dy:
            user32.mouse_event(0x0800, 0, 0, int(dy * 120) & 0xFFFFFFFF, 0)          # MOUSEEVENTF_WHEEL
        if dx:
            user32.mouse_event(0x1000, 0, 0, int(-dx * 120) & 0xFFFFFFFF, 0)         # MOUSEEVENTF_HWHEEL
        return
    import Quartz
    ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 2, int(dy), int(dx))
    _post(ev)


def _glide(to: Tuple[float, float], steps: int = 12) -> None:
    """Moves the pointer there in a short visible glide, so you can see what Xod is doing."""
    x0, y0 = _cursor()
    for i in range(1, steps + 1):
        _post_mouse("move", (x0 + (to[0] - x0) * i / steps, y0 + (to[1] - y0) * i / steps))
        time.sleep(0.012)


def _app_at(pt: Tuple[float, float]) -> Dict[str, Any]:
    """The app whose window is under that point (what a click there would use)."""
    if WIN:
        user32 = _win_api()["user32"]
        hwnd = user32.WindowFromPoint(_wt.POINT(int(pt[0]), int(pt[1])))
        if hwnd:
            root = user32.GetAncestor(hwnd, 2) or hwnd          # GA_ROOT
            pid = _wt.DWORD(0)
            user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
            a = _win_app_of_pid(int(pid.value))
            if a:
                return a
        return front_app()
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
        _post_scroll(dy, dx)
        _mark_left()
    await asyncio.to_thread(go)
    return _did(f"scrolled {direction}", app)


def _focused_is_password() -> bool:
    if WIN:
        return _win_focused_is_password()
    import ApplicationServices as AS
    el = _ax(AS.AXUIElementCreateSystemWide(), "AXFocusedUIElement")
    return el is not None and (_ax(el, "AXSubrole") == "AXSecureTextField")


def _command_check(text: str) -> Optional[Dict[str, Any]]:
    """Text going into a terminal is a command: the same refusal Xod's own commands get."""
    from app.agent.worker import refused
    return refused("computer_run_command", {"command": text})


TEXT_ROLES = {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"}
QUIET_SEC = 2.0          # you used the keyboard or mouse this recently: Xod waits before typing
QUIET_WAIT_SEC = 20.0    # ...for at most this long, then says so instead of grabbing the keyboard


def _ax_insert(text: str) -> bool:
    """Puts the text at the cursor of the focused field through Accessibility (its selected text is replaced, as typing
    would), with no key events and no clipboard, so it lands in that field even if another window comes forward. True
    only when the field's value reads back with the text in it; False leaves everything as it was (paste is next)."""
    if WIN:
        return False     # Windows puts text in by typing/pasting, not through the accessibility tree
    import ApplicationServices as AS
    el = _ax(AS.AXUIElementCreateSystemWide(), "AXFocusedUIElement")
    if el is None or _ax(el, "AXRole") not in TEXT_ROLES or _ax(el, "AXSubrole") == "AXSecureTextField":
        return False
    before = _ax(el, "AXValue")
    if not isinstance(before, str):
        return False
    if AS.AXUIElementSetAttributeValue(el, "AXSelectedText", text) != 0:
        return False
    after = _ax(el, "AXValue")
    return isinstance(after, str) and after != before and text in after


def _user_active() -> bool:
    """You (not Xod) pressed a key or moved the mouse in the last QUIET_SEC seconds."""
    if WIN:
        try:
            w = _win_api()
            info = w["LASTINPUTINFO"]()
            info.cbSize = ctypes.sizeof(info)
            if not w["user32"].GetLastInputInfo(ctypes.byref(info)):
                return False
            idle = (w["kernel32"].GetTickCount64() - info.dwTime) / 1000.0
        except Exception:
            return False
        xod_ago = time.time() - (_state.get("left_time") or 0)
        return idle < QUIET_SEC and xod_ago > idle + 0.5          # the last input came after Xod's own last action
    try:
        import Quartz
        idle = Quartz.CGEventSourceSecondsSinceLastEventType(Quartz.kCGEventSourceStateCombinedSessionState,
                                                             Quartz.kCGAnyInputEventType)
    except Exception:
        return False
    xod_ago = time.time() - (_state.get("left_time") or 0)
    return idle < QUIET_SEC and xod_ago > idle + 0.5          # the last input came after Xod's own last action


async def _wait_for_quiet() -> None:
    end = time.time() + QUIET_WAIT_SEC
    while await asyncio.to_thread(_user_active):
        if time.time() > end:
            raise DesktopError("You're using the keyboard or mouse, so Xod didn't type. It goes on when you stop, or say continue.")
        await asyncio.sleep(0.5)


def _same_front(app: Dict[str, Any]) -> None:
    """Typing goes only into the app it was meant for: if another one came to the front, stop before a key leaves Xod."""
    now = front_app()
    if now.get("pid") != app.get("pid"):
        raise DesktopError(f"{now.get('name') or 'Another app'} came to the front, so Xod stopped typing "
                           f"(it was typing into {app.get('name') or 'another app'}). Nothing went into {now.get('name') or 'it'}.")


async def type_text(text: str, element: Any = None, enter: bool = False) -> Dict[str, Any]:
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
    await _wait_for_quiet()
    how = "directly"
    if not text or not await asyncio.to_thread(_ax_insert, text):
        how = "by paste"
        await asyncio.to_thread(_type_now, text, False, app)
    if enter:
        await asyncio.sleep(0.8)          # apart from the text, or full-screen terminal agents take it as part of a paste
        await asyncio.to_thread(_same_front, app)
        await asyncio.to_thread(_press, ["return"])
        _mark_left()
    shown = text if len(text) <= 60 else text[:57] + "…"
    return _did(f"typed \"{shown}\" {how}" + (" and pressed Return" if enter else ""), app)


def _type_now(text: str, enter: bool, app: Optional[Dict[str, Any]] = None) -> None:
    """Types it into what's focused. Pasted (your clipboard is put back right after): apps take a paste reliably, where
    synthetic key events for each letter are dropped by some (Notes did) and arrive as a "paste" in others anyway.
    Return is pressed on its own afterwards, so full-screen terminal agents (Muse, Claude Code) submit instead of
    adding a new line."""
    if text:
        saved = _clipboard_get()
        _clipboard_set(text)
        time.sleep(0.05)
        try:
            if app:
                _same_front(app)
        except DesktopError:
            if saved is not None:
                _clipboard_set(saved)
            elif WIN:
                _win_clipboard_clear()
            raise
        _press(["ctrl", "v"] if WIN else ["cmd", "v"])
        time.sleep(0.35)
        if saved is not None:
            _clipboard_set(saved)
        elif WIN:
            _win_clipboard_clear()
    if enter:
        time.sleep(0.8)          # apart from the paste, or full-screen terminal agents take it as part of it
        if app:
            _same_front(app)
        _press(["return"])
    _mark_left()


def _clipboard_get() -> Optional[str]:
    if WIN:
        try:
            return _win_clipboard_get()
        except Exception:
            return None
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString
        return NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
    except Exception:
        return None


def _clipboard_set(text: str) -> None:
    if WIN:
        _win_clipboard_set(text)
        return
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
    if WIN:
        kb = _win_api()["user32"].keybd_event
        mods = [WIN_MODS[k] for k in keys[:-1] if k in WIN_MODS]
        key_str = keys[-1] if keys else ""
        vk = WIN_KEYCODES.get(key_str.lower()) if key_str else None
        if vk is None and key_str not in WIN_MODS:
            raise DesktopError(f"Xod doesn't know the key {key_str or '(none)'}.")
        for m in mods:
            kb(m, 0, 0, 0)
        if vk is None:                       # a modifier on its own
            kb(WIN_MODS[key_str], 0, 0, 0)
            time.sleep(0.05)
            kb(WIN_MODS[key_str], 0, 0x0002, 0)
        else:
            kb(vk, 0, 0, 0)
            time.sleep(0.05)
            kb(vk, 0, 0x0002, 0)                     # KEYEVENTF_KEYUP
        for m in reversed(mods):
            kb(m, 0, 0x0002, 0)
        return
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
    if combo in (WIN_RISKY_KEYS if WIN else RISKY_KEYS) and not re.search(r"\b(quit|close|force|log ?out|empty|delete|lock|sign ?out|task ?manager)\b", asked or "", re.I):
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
    if WIN:
        return await _win_open_app(name)
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
    if WIN:
        deadline = time.time() + seconds
        while time.time() < deadline:
            f = front_app()
            if (bundle and f["bundle"] == bundle) or f["name"].lower() == name.lower():
                return True
            pid = _first_pid(bundle) if bundle else 0
            if pid and _win_focus_window(_win_hwnd_for_pid(pid)):
                return True
            time.sleep(0.3)
        return False
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
    if WIN:
        return _WIN_APP_EXES.get((name or "").strip().lower(), "")
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
    if not MAC:
        raise DesktopError("That terminal detail is only available on a Mac; look at the screen instead.")
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
    if WIN:
        return []
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
    if WIN:
        raise DesktopError("Opening its own terminal windows is only supported on a Mac for now.")
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
    if WIN:
        raise DesktopError("Closing its own terminal windows is only supported on a Mac for now.")
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
    if WIN:
        return any(w["_bundle"] == (bundle or "").lower() for w in _win_windows_raw())
    from AppKit import NSRunningApplication
    return bool(NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle))


def terminals() -> Dict[str, Any]:
    """Every Terminal and iTerm window and tab that's open, with what runs in it. Never opens either app.
    Windows are named by their id, which stays the same however you rearrange them (their order changes every time
    you click one, so "window 2" could be a different window a second later)."""
    _ready(need_screen=False, need_input=False)
    if WIN:
        return _win_terminals()
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
    if WIN:
        raise DesktopError("Reading a Windows terminal's exact text isn't supported yet; use desktop_look to see it.")
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
    if WIN:
        bundle = next((x.get("bundle") or "" for x in terminals()["terminals"] if x["window"] == w and x["tab"] == t), "")
        held = _gate({"name": a, "bundle": bundle})
        if held:
            return held
        no = _command_check(text)
        if no:
            return no
        if not await asyncio.to_thread(_bring_forward, a, bundle):
            raise DesktopError(f"{a} didn't come to the front, so Xod didn't type anything.")
        await asyncio.sleep(0.3)
        await asyncio.to_thread(_type_now, text, enter)
        return _did(f"sent \"{text[:60]}\" to {a} window {w} tab {t}", {"name": a})
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
