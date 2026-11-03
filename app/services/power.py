"""
Keeping this computer awake, and knowing whether its screen is locked.

While "Approve everything" is on, or a Crew job is running, Xod holds a
"keep the display awake" assertion so the computer doesn't lock from idle
halfway through the night (which leaves someone approving cards by hand all night).
The assertion is ref-counted by reason: the last reason to go away releases
it. It shows in Crew (through muse_app.status) and in phone_list.

  Mac      caffeinate -d -i (a child process; killed to release), or an
           IOPMAssertion when caffeinate is missing.
  Windows  SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED |
           ES_SYSTEM_REQUIRED) on a dedicated thread (the call is
           per-thread), cleared with ES_CONTINUOUS when released.

Lock state is read, never changed: Xod never types a login password
anywhere. On a Mac it comes from the console session (ioreg's
CGSSessionScreenIsLocked, which reads correctly even while locked); on
Windows from whether the input desktop can be opened.
"""
import logging
import plistlib
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("xod.power")

_state: Dict[str, Any] = {"reasons": {}, "backend": None, "lock": threading.Lock()}


class _AwakeBackend:
    """One held assertion. start()/stop() are idempotent."""

    def __init__(self):
        self.held = False

    def start(self) -> bool:
        return False

    def stop(self) -> None:
        pass


class _MacAwake(_AwakeBackend):
    """An IOPMAssertion held in-process (dies with Xod, so tests can't leak it);
    caffeinate is the fallback if IOKit won't talk."""

    def __init__(self):
        super().__init__()
        self._assertion = 0
        self._proc = None

    def start(self) -> bool:
        if self.held:
            return True
        try:
            import ctypes
            iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
            cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

            cf.CFStringCreateWithCString.restype = ctypes.c_void_p
            cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
            iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int32
            iokit.IOPMAssertionCreateWithName.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                                          ctypes.POINTER(ctypes.c_uint32)]

            def cfstr(s):
                return cf.CFStringCreateWithCString(None, s.encode(), 0x08000100)  # kCFStringEncodingUTF8

            aid = ctypes.c_uint32(0)
            # kIOPMAssertionTypePreventUserIdleDisplaySleep, kIOPMAssertionLevelOn = 255
            r = iokit.IOPMAssertionCreateWithName(cfstr("PreventUserIdleDisplaySleep"), 255,
                                                  cfstr("Xod is approving cards / running Crew jobs"), ctypes.byref(aid))
            if r != 0 or not aid.value:
                raise RuntimeError(f"IOPMAssertionCreateWithName -> {r}")
            self._assertion = aid.value
            self.held = True
            return True
        except Exception as e:
            logger.warning(f"power: IOPMAssertion failed ({e}); trying caffeinate")
        try:
            self._proc = subprocess.Popen(["/usr/bin/caffeinate", "-d", "-i"],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import atexit
            atexit.register(self.stop)  # never leave a caffeinate behind when Xod exits
            self.held = True
            return True
        except Exception as e:
            logger.warning(f"power: caffeinate failed ({e}); display may sleep")
            return False

    def stop(self) -> None:
        if self._assertion:
            try:
                import ctypes
                iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
                iokit.IOPMAssertionRelease.restype = ctypes.c_int32
                iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
                iokit.IOPMAssertionRelease(self._assertion)
            except Exception:
                pass
            self._assertion = 0
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        self.held = False


class _WinAwake(_AwakeBackend):
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002

    def __init__(self):
        super().__init__()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        if self.held:
            return True
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState  # noqa: B018 - presence check
        except Exception as e:
            logger.warning(f"power: SetThreadExecutionState unavailable ({e})")
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="xod-keep-awake")
        self._thread.start()
        self.held = True
        return True

    def _run(self) -> None:
        import ctypes
        flags = self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED | self.ES_DISPLAY_REQUIRED
        try:
            while not self._stop.is_set():
                ctypes.windll.kernel32.SetThreadExecutionState(flags)
                self._stop.wait(30)
        finally:
            ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)

    def stop(self) -> None:
        self._stop.set()
        self.held = False


def _make_backend() -> _AwakeBackend:
    if sys.platform == "darwin":
        return _MacAwake()
    if sys.platform == "win32":
        return _WinAwake()
    return _AwakeBackend()


def _backend() -> _AwakeBackend:
    if _state["backend"] is None:
        _state["backend"] = _make_backend()
    return _state["backend"]


def acquire(reason: str) -> Dict[str, Any]:
    """Hold the display awake for `reason` (ref-counted; same reason twice counts once)."""
    with _state["lock"]:
        is_new = reason not in _state["reasons"]
        _state["reasons"][reason] = time.time()
        if is_new and len(_state["reasons"]) == 1:
            _backend().start()
    return status()


def release(reason: str) -> Dict[str, Any]:
    with _state["lock"]:
        _state["reasons"].pop(reason, None)
        if not _state["reasons"]:
            _backend().stop()
    return status()


def ensure(reason: str, on: bool) -> Dict[str, Any]:
    """Keep `reason`'s hold in step with a switch that may be polled every second."""
    with _state["lock"]:
        held = reason in _state["reasons"]
    if on and not held:
        return acquire(reason)
    if not on and held:
        return release(reason)
    return status()


def held_reasons() -> list:
    with _state["lock"]:
        return sorted(_state["reasons"])


# ------------------------------------------------------------------ lock state (read-only)

def _mac_locked() -> Optional[bool]:
    try:
        out = subprocess.run(["/usr/sbin/ioreg", "-n", "Root", "-d1", "-a"],
                             capture_output=True, timeout=5).stdout
        data = plistlib.loads(out)
        users = data.get("IOConsoleUsers") or []
        for u in users:
            if "CGSSessionScreenIsLocked" in u:
                return bool(u["CGSSessionScreenIsLocked"])
        if "IOConsoleLocked" in data:
            return bool(data["IOConsoleLocked"])
    except Exception:
        pass
    try:  # fallback: the session dictionary (None on some systems, hence ioreg first)
        from Quartz import CGSessionCopyCurrentDictionary
        d = CGSessionCopyCurrentDictionary()
        if d:
            return bool(d.get("CGSSessionScreenIsLocked", False))
    except Exception:
        pass
    return None


def _win_locked() -> Optional[bool]:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        # The input desktop can't be opened from a normal session while locked.
        desk = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if not desk:
            return True
        user32.CloseDesktop(desk)
        return False
    except Exception:
        return None


def is_locked() -> Optional[bool]:
    """True/False when the OS will say, None when it can't be told from here."""
    if sys.platform == "darwin":
        return _mac_locked()
    if sys.platform == "win32":
        return _win_locked()
    return None


def _mac_idle() -> Optional[float]:
    try:
        from Quartz import CGEventSourceSecondsSinceLastEventType
        # HID system state, any input event (kCGAnyInputEventType is ~0): keyboard, mouse, trackpad.
        return float(CGEventSourceSecondsSinceLastEventType(1, 0xFFFFFFFF))
    except Exception:
        pass
    try:  # fallback: IOHIDSystem's HIDIdleTime, in nanoseconds
        out = subprocess.run(["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4", "-a"],
                             capture_output=True, timeout=5).stdout
        stack = [plistlib.loads(out)]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "HIDIdleTime" in node:
                    return node["HIDIdleTime"] / 1e9
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    except Exception:
        pass
    return None


def _win_idle() -> Optional[float]:
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def idle_seconds() -> Optional[float]:
    """Seconds since the last key press, click or mouse move on this computer; None when it can't be read."""
    if sys.platform == "darwin":
        return _mac_idle()
    if sys.platform == "win32":
        return _win_idle()
    return None


def status() -> Dict[str, Any]:
    reasons = held_reasons()
    # The backend's real state, not just that a reason was recorded: if the
    # assertion/caffeinate/SetThreadExecutionState call failed, we are not held.
    held = bool(reasons) and _backend().held
    return {"platform": sys.platform, "awake_held": held, "awake": held,
            "awake_reasons": reasons, "locked": is_locked()}


def sync_crew() -> None:
    """Hold while the auto-approve switch is on or any Crew job is active; release when idle.
    Called from the muse_app watcher's 1s tick, so no new timer is needed."""
    try:
        from app.services import muse_app
        ensure("auto-approve", muse_app.enabled())
    except Exception:
        pass
    try:
        from app.services import crew
        ensure("crew job", bool(crew.jobs(active_only=True)))
    except Exception:
        pass
