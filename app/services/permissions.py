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
        self._prompt_key = None
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
            self._plain, self._cf = plain, cf
            # The prompt key constant is read on its own: a machine where
            # it cannot be read must not cost this process its plain trust
            # check. The options dictionary built in trusted_with_prompt
            # compares its keys by pointer, and the framework looks the
            # prompt option up under its own exported constant, so the key
            # must BE that constant: a same-text CFString misses the
            # lookup, the framework reads NULL for the value, and it
            # dereferences the NULL (EXC_BAD_ACCESS inside
            # AXIsProcessTrustedWithOptions; the 2026-10-30 20:05 crash
            # report, python3.12-2026-10-30-200539.ips). If the constant
            # cannot be read, the ask stays unavailable (_prompt None)
            # rather than dangerous. The Partner's identical fix is
            # partner/partner_ax.py commit 3e2987d, proven live.
            try:
                prompt_key = ctypes.c_void_p.in_dll(svc, "kAXTrustedCheckOptionPrompt")
                if not prompt_key:
                    raise ValueError("kAXTrustedCheckOptionPrompt unreadable")
                self._prompt_key, self._prompt = prompt_key, prompt
            except Exception:
                self._prompt = None
                self._prompt_key = None
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
        prompt = getattr(self, "_prompt", None)
        key = getattr(self, "_prompt_key", None)
        if prompt is None or self._cf is None or not key:
            return None
        cf = self._cf
        options = None
        try:
            cf.CFDictionaryCreate.restype = ctypes.c_void_p
            cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                              ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
                                              ctypes.c_void_p, ctypes.c_void_p]
            cf.CFRelease.argtypes = [ctypes.c_void_p]
            value = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
            if not value:
                return None
            # The key is the framework's own kAXTrustedCheckOptionPrompt
            # constant, read in __init__: the dictionary compares keys by
            # pointer, so only the constant itself is found by the
            # framework's lookup (see the note at the registration site).
            # The constant is framework-owned; it is never released here.
            keys = (ctypes.c_void_p * 1)(key)
            values = (ctypes.c_void_p * 1)(value)
            options = cf.CFDictionaryCreate(None, keys, values, 1, None, None)
            if not options:
                return None
            return bool(prompt(options))
        except Exception as e:
            logger.debug(f"trust prompt unavailable: {e}")
            return None
        finally:
            if options:
                try:
                    cf.CFRelease(options)
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


PARTNER_BUNDLE_ID = "dev.xod.partner"   # the Xod Partner app: since Partner v1 it holds the grant


def reset_partner_grant(runner: Callable = subprocess.run) -> Dict[str, Any]:
    """USER-TRIGGERED ONLY: clear the XOD PARTNER's stale Accessibility entry.

    The repair asymmetry the Partner design rests on: Xod may reset the
    Partner's grant because Xod is not the process that dies for it. macOS
    kills the grant holder when its entry is reset, so the Partner goes
    down; launchd's KeepAlive brings it straight back, and the user then
    flips the fresh Xod Partner switch once. Xod never re-asks for the
    Partner here (a prompt from this process would ask about Xod, the
    wrong app). The bundle id is a constant, validated by the same strict
    shape as Xod's own; only Accessibility is ever reset."""
    cmd = reset_command(PARTNER_BUNDLE_ID)
    logger.warning(f"User asked to reset the Xod Partner's Accessibility grant: {' '.join(cmd)}")
    try:
        r = runner(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        logger.warning(f"Partner Accessibility reset could not run: {e}")
        return {"ok": False, "bundle_id": PARTNER_BUNDLE_ID, "message": f"The reset could not run: {e}"}
    if r.returncode != 0:
        logger.warning(f"Partner Accessibility reset failed: {(r.stderr or '').strip()[:200]}")
        return {"ok": False, "bundle_id": PARTNER_BUNDLE_ID,
                "message": f"macOS refused the reset: {(r.stderr or 'unknown error').strip()[:200]}"}
    logger.warning("Partner Accessibility grant reset at the user's request; launchd revives the "
                   "Partner, and the user flips Xod Partner on to finish")
    return {"ok": True, "bundle_id": PARTNER_BUNDLE_ID,
            "message": "Done: the Partner's stale entry is cleared and the Partner is restarting. "
                       "Flip Xod Partner on in the Accessibility settings to finish."}


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
