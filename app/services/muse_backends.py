"""
Platform backends for the Muse approval-card watcher.

muse_app.py is the platform-neutral core: which card counts, which button
may be pressed (and which never), one-row-ever logging, the re-press cap,
the 8 AM summary. Everything that touches a real window lives here:

  mac_ax   macOS Accessibility in-process (pyobjc ApplicationServices):
           walk the Muse app's AX tree, AXPress the button, and an
           AXObserver that says the moment a window changes.
  win_uia  Windows UI Automation (comtypes over UIAutomationCore): find
           Muse's window by process, walk its UIA tree, Invoke the
           button with InvokePattern; structure-changed events when the
           handler can be registered, the core's 30s backstop otherwise.

Both backends hand the core the same tiny surface (trusted / create_app /
attr / perform over opaque elements, pid(), and an observer factory), so
the core and its tests run against a fake tree on either OS.
"""
import logging
import subprocess
import sys
import threading
from typing import Any, Callable, List, Optional

logger = logging.getLogger("xod.muse_backends")


# ------------------------------------------------------------------ macOS (Accessibility)

class MacAX:
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

    def element_at(self, x: float, y: float):
        """The element under a screen point (iPhone Mirroring taps), or None."""
        try:
            from ApplicationServices import AXUIElementCopyElementAtPosition, AXUIElementCreateSystemWide
            err, el = AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), float(x), float(y), None)
            return el if err == 0 else None
        except Exception:
            return None


def _mac_pid() -> Optional[int]:
    r = subprocess.run(["pgrep", "-x", "Muse"], capture_output=True, text=True, timeout=5)
    pids = (r.stdout or "").split()
    return int(pids[0]) if pids else None


class MacObserver:
    """An AXObserver on Muse's pid, in its own thread with a CFRunLoop. Listen-only: it never
    presses; it just calls on_event, and the scan that follows does the pressing."""

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


class MacBackend:
    name = "mac_ax"

    def pid(self) -> Optional[int]:
        return _mac_pid()

    def ax_api(self):
        return MacAX()

    def observer(self, pid: int, on_event: Callable[[], None]):
        return MacObserver(pid, on_event)


# ------------------------------------------------------------------ Windows (UI Automation)

# UIA control types -> the AX roles the core walks by.
_UIA_ROLES = {50000: "AXButton", 50026: "AXGroup", 50020: "AXStaticText", 50032: "AXWindow",
              50033: "AXWindow", 50004: "AXStaticText", 50008: "AXGroup", 50009: "AXGroup",
              50011: "AXGroup", 50037: "AXApplication"}
UIA_InvokePattern = 10000
UIA_LegacyIAccessiblePattern = 10018
UIA_ValuePattern = 10002
UIA_ProcessIdProperty = 30010


class _WinEl:
    """One UIA element wrapped to look like the AX elements the core walks."""

    def __init__(self, ax: "WinAX", raw):
        self._ax, self.raw = ax, raw

    def role(self) -> str:
        try:
            return _UIA_ROLES.get(int(self.raw.CurrentControlType), "AXGroup")
        except Exception:
            return "AXGroup"

    def label(self) -> str:
        try:
            return str(self.raw.CurrentName or "")
        except Exception:
            return ""

    def children(self) -> List["_WinEl"]:
        return self._ax.children_of(self)

    def parent(self) -> Optional["_WinEl"]:
        return self._ax.parent_of(self)


class WinAX:
    """UI Automation with the same surface as MacAX. `automation`/`walker` can be
    injected (tests use a fake tree); otherwise comtypes' CUIAutomation is made lazily."""

    def __init__(self, automation=None, walker=None, app_root=None):
        self._automation, self._walker, self._app_root = automation, walker, app_root

    def _uia(self):
        if self._automation is None:
            self._automation = self._create_automation()
            self._walker = self._automation.ControlViewWalker
        return self._automation

    @staticmethod
    def _create_automation():
        import comtypes.client
        from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation
        return comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)

    def trusted(self) -> bool:
        return True  # UIA has no Accessibility grant; a higher-integrity Muse may still refuse (attr -> None)

    def create_app(self, pid: int):
        if self._app_root is not None:
            return self._app_root if isinstance(self._app_root, _WinEl) else _WinEl(self, self._app_root)
        uia = self._uia()
        root = uia.GetRootElement()
        cond = uia.CreatePropertyCondition(UIA_ProcessIdProperty, pid)
        found = root.FindFirst(2, cond)  # TreeScope_Children
        if found is None:
            raise RuntimeError(f"No Muse window found for pid {pid}")
        return _WinEl(self, found)

    def children_of(self, el: _WinEl) -> List[_WinEl]:
        self._uia()
        out, child = [], None
        try:
            child = self._walker.GetFirstChildElement(el.raw)
            while child is not None:
                out.append(_WinEl(self, child))
                child = self._walker.GetNextSiblingElement(child)
        except Exception:
            pass
        return out

    def parent_of(self, el: _WinEl) -> Optional[_WinEl]:
        self._uia()
        try:
            raw = self._walker.GetParentElement(el.raw)
            return _WinEl(self, raw) if raw is not None else None
        except Exception:
            return None

    def attr(self, el, name):
        if not isinstance(el, _WinEl):
            return None
        if name == "AXRole":
            return el.role()
        if name in ("AXTitle", "AXDescription", "AXValue"):
            return el.label()
        if name == "AXChildren":
            return el.children()
        if name == "AXParent":
            return el.parent()
        return None

    def perform(self, el, action) -> int:
        if action != "AXPress" or not isinstance(el, _WinEl):
            return -1
        for pattern_id, iface_name, method in (
                (UIA_InvokePattern, "IUIAutomationInvokePattern", "Invoke"),
                (UIA_LegacyIAccessiblePattern, "IUIAutomationLegacyIAccessiblePattern", "DoDefaultAction")):
            try:
                pat = el.raw.GetCurrentPattern(pattern_id)
                if pat is None:
                    continue
                fn = getattr(pat, method, None)
                if fn is None:  # a raw comtypes pattern pointer: ask it for the typed interface
                    from comtypes.gen import UIAutomationClient
                    fn = getattr(pat.QueryInterface(getattr(UIAutomationClient, iface_name)), method)
                fn()
                return 0
            except Exception:
                continue
        return -1


def _win_pid() -> Optional[int]:
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Claude.exe", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, timeout=5)
        for line in (r.stdout or "").splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    except Exception:
        pass
    return None


class WinObserver:
    """Structure-changed events on Muse's window, best effort: if the comtypes
    handler can't be registered, the core's 30s backstop scan still runs."""

    TreeScope_Subtree = 7

    def __init__(self, pid: int, on_event: Callable[[], None]):
        self.pid, self.failed = pid, False
        self._automation, self._element, self._handler = None, None, None
        try:
            ax = WinAX()
            self._element = ax.create_app(pid)
            self._automation = ax._uia()
            handler = _make_structure_handler(on_event)
            self._handler = handler
            self._automation.AddStructureChangedEventHandler(
                self._element.raw, self.TreeScope_Subtree, None, handler)
        except Exception as e:
            self.failed = True
            logger.warning(f"couldn't watch the Muse app on Windows ({e}); the backstop scan still runs")

    def stop(self) -> None:
        try:
            if self._automation is not None and self._element is not None:
                self._automation.RemoveStructureChangedEventHandler(self._element.raw, None, self._handler)
        except Exception:
            pass


def _make_structure_handler(on_event):
    import comtypes
    from comtypes.gen.UIAutomationClient import IUIAutomationStructureChangedEventHandler

    class Handler(comtypes.COMObject):
        _com_interfaces_ = [IUIAutomationStructureChangedEventHandler]

        def StructureChanged(self, sender, changeType):
            on_event()

    return Handler()


class WinBackend:
    name = "win_uia"

    def pid(self) -> Optional[int]:
        return _win_pid()

    def ax_api(self):
        return WinAX()

    def observer(self, pid: int, on_event: Callable[[], None]):
        obs = WinObserver(pid, on_event)
        return None if obs.failed else obs


# ------------------------------------------------------------------ picking one

def backend(platform: Optional[str] = None):
    """The backend for this OS, or None where Xod can't watch cards (Linux)."""
    platform = platform if platform is not None else sys.platform
    if platform == "darwin":
        return MacBackend()
    if platform == "win32":
        return WinBackend()
    return None
