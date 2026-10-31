"""
macOS permission self-checks: is Xod's Accessibility access actually in effect?

The lesson of 2026-10-30: after the installed app's files changed, macOS kept
showing Xod's Accessibility switch ON while the grant underneath had gone
stale, so the Muse auto-approver silently could not press anything. The
Settings switch shows that an entry exists, not that it works for this
binary. This module checks the real thing (AXIsProcessTrusted), explains a
stale grant in plain words, and offers the two repairs a person can trigger:
open the exact Settings pane, or reset Xod's OWN stale entry and ask again
so one switch flip finishes it.

Nothing here repairs on its own. A reset clears an existing grant, so it
only ever runs when the user asks for it, and it names only Xod's own
bundle id, resolved from the running app bundle. When that cannot be
determined (a dev interpreter, a bare python), the reset refuses instead
of guessing: resetting the wrong app's entry would be worse than none.
"""
import ctypes
import logging
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("xod.permissions")

SERVICE = "Accessibility"   # the only TCC service this module will ever reset
SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
_BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*[A-Za-z0-9]$")

STALE_NOTICE = ("macOS has blocked Xod's Accessibility access, so it cannot press approval buttons. "
                "The switch in System Settings can still look on: after an app update or a change to "
                "the app's files, the old grant goes stale. Repair it here: open the Accessibility "
                "settings and flip Xod off and back on, or reset Xod's stale entry and let macOS ask "
                "again.")


# ------------------------------------------------------------------ the macOS calls (one seam)
class _CTypesAX:
    """AXIsProcessTrusted and AXIsProcessTrustedWithOptions via ctypes, so tests
    can replace the whole seam with a fake. Any load failure leaves the methods
    answering None (unknown), never raising into callers."""

    def __init__(self) -> None:
        self._plain = None
        self._prompt = None
        self._cf = None
        try:
            svc = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
            cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            plain = svc.AXIsProcessTrusted
            plain.restype = ctypes.c_bool
            plain.argtypes = []
            prompt = svc.AXIsProcessTrustedWithOptions
            prompt.restype = ctypes.c_bool
            prompt.argtypes = [ctypes.c_void_p]
            self._plain, self._prompt, self._cf = plain, prompt, cf
        except Exception as e:
            logger.debug(f"Accessibility calls unavailable: {e}")

    def trusted(self) -> Optional[bool]:
        if self._plain is None:
            return None
        try:
            return bool(self._plain())
        except Exception:
            return None

    def trusted_with_prompt(self) -> Optional[bool]:
        """Ask macOS to show the trust prompt, so the user flips one switch."""
        if self._prompt is None or self._cf is None:
            return None
        cf = self._cf
        key = options = None
        try:
            cf.CFStringCreateWithCString.restype = ctypes.c_void_p
            cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
            cf.CFDictionaryCreate.restype = ctypes.c_void_p
            cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                              ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
                                              ctypes.c_void_p, ctypes.c_void_p]
            cf.CFRelease.argtypes = [ctypes.c_void_p]
            # kAXTrustedCheckOptionPrompt's string value is its own name; building the
            # CFString directly avoids chasing the constant across sub-frameworks.
            key = cf.CFStringCreateWithCString(None, b"AXTrustedCheckOptionPrompt", 0x08000100)
            value = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
            if not key or not value:
                return None
            keys = (ctypes.c_void_p * 1)(key)
            values = (ctypes.c_void_p * 1)(value)
            options = cf.CFDictionaryCreate(None, keys, values, 1, None, None)
            if not options:
                return None
            return bool(self._prompt(options))
        except Exception as e:
            logger.debug(f"trust prompt unavailable: {e}")
            return None
        finally:
            for ref in (options, key):
                if ref:
                    try:
                        cf.CFRelease(ref)
                    except Exception:
                        pass


_backend: Optional[_CTypesAX] = None


def _get_backend() -> _CTypesAX:
    global _backend
    if _backend is None:
        _backend = _CTypesAX()
    return _backend


def trusted() -> Optional[bool]:
    """Is THIS process's Accessibility access in effect right now?
    True or False, or None when macOS cannot be asked (not macOS, calls missing)."""
    try:
        return _get_backend().trusted()
    except Exception:
        return None


def trusted_with_prompt() -> Optional[bool]:
    """Re-ask for trust with the system prompt showing. Same answers as trusted()."""
    try:
        return _get_backend().trusted_with_prompt()
    except Exception:
        return None


# ------------------------------------------------------------------ which app is this process?
def bundle_id_for_executable(executable: Optional[str] = None) -> Optional[str]:
    """The bundle id of the .app a python executable belongs to, from its
    Info.plist. None when the executable is not inside an app bundle, or the
    plist's identifier is not a plausible bundle id."""
    exe = Path(executable or sys.executable)
    for candidate in (exe, exe.resolve()):
        for parent in candidate.parents:
            if parent.suffix != ".app":
                continue
            try:
                with open(parent / "Contents" / "Info.plist", "rb") as f:
                    info = plistlib.load(f)
                bid = str(info.get("CFBundleIdentifier") or "").strip()
            except Exception:
                return None
            return bid if _BUNDLE_ID_RE.match(bid) else None
    return None


_bundle_id_cache: Any = "unset"


def own_bundle_id() -> Optional[str]:
    """Xod's own bundle id, resolved once from the running process. None when
    this process does not run inside an app bundle (the dev venv, a bare
    interpreter): callers must treat that as 'cannot reset', never guess."""
    global _bundle_id_cache
    if _bundle_id_cache == "unset":
        _bundle_id_cache = bundle_id_for_executable()
    return _bundle_id_cache


# ------------------------------------------------------------------ status and repairs
def accessibility_status(auto_approve_on: bool = False) -> Dict[str, Any]:
    """What the auto-approve settings surface shows about Accessibility: whether
    the access is really in effect, the plain notice when approvals are on but
    macOS is not honoring the grant, and which repairs are available from here."""
    t, bid = trusted(), own_bundle_id()
    return {"trusted": t, "bundle_id": bid, "can_reset": bid is not None,
            "settings_url": SETTINGS_URL,
            "notice": STALE_NOTICE if (auto_approve_on and t is False) else None}


def reset_command(bundle_id: str) -> List[str]:
    """The exact command that clears Xod's own stale Accessibility entry. The
    service is this module's constant, never an input; the bundle id must be a
    plain reverse-DNS identifier or no command is built at all."""
    if not bundle_id or not _BUNDLE_ID_RE.match(str(bundle_id)):
        raise ValueError(f"Refusing to build an Accessibility reset for an unrecognized bundle id: {bundle_id!r}")
    return ["tccutil", "reset", SERVICE, str(bundle_id)]


def reset_own_grant(runner: Callable = subprocess.run) -> Dict[str, Any]:
    """USER-TRIGGERED ONLY: clear Xod's own stale Accessibility entry, then ask
    macOS to prompt again so one switch flip finishes the repair. Refuses when
    the running process cannot prove which app it is."""
    bid = own_bundle_id()
    if not bid:
        logger.warning("Accessibility reset refused: this process does not run inside an app bundle, "
                       "so Xod's bundle id cannot be determined")
        return {"ok": False, "bundle_id": None,
                "message": "Xod cannot tell which app it is from here (this process is not running "
                           "inside the Xod app), so it will not reset any grant. Open the "
                           "Accessibility settings and flip Xod off and back on instead."}
    cmd = reset_command(bid)
    logger.warning(f"User asked to reset Xod's Accessibility grant: {' '.join(cmd)}")
    try:
        r = runner(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        logger.warning(f"Accessibility reset for {bid} could not run: {e}")
        return {"ok": False, "bundle_id": bid, "message": f"The reset could not run: {e}"}
    if r.returncode != 0:
        logger.warning(f"Accessibility reset for {bid} failed: {(r.stderr or '').strip()[:200]}")
        return {"ok": False, "bundle_id": bid,
                "message": f"macOS refused the reset: {(r.stderr or 'unknown error').strip()[:200]}"}
    now = trusted_with_prompt()
    logger.warning(f"Accessibility grant for {bid} reset at the user's request; trust after asking again: {now}")
    return {"ok": True, "bundle_id": bid, "trusted": now,
            "message": ("Done: Xod's Accessibility access is working again." if now is True else
                        "Done: the stale entry is cleared and macOS is asking again. Flip Xod on in "
                        "the prompt (or in the Accessibility settings) to finish.")}


def open_accessibility_settings(runner: Callable = subprocess.run) -> Dict[str, Any]:
    """USER-TRIGGERED ONLY: open the exact Settings pane where the grant lives."""
    logger.info("Opening the Accessibility settings pane at the user's request")
    try:
        r = runner(["open", SETTINGS_URL], capture_output=True, text=True, timeout=15)
        ok = r.returncode == 0
    except Exception as e:
        logger.warning(f"Could not open the Accessibility settings pane: {e}")
        return {"ok": False, "settings_url": SETTINGS_URL, "message": f"Could not open Settings: {e}"}
    return {"ok": ok, "settings_url": SETTINGS_URL,
            "message": ("Opened System Settings at Privacy and Security, Accessibility. Find Xod in "
                        "the list and flip it off and back on." if ok else "Opening Settings failed.")}
