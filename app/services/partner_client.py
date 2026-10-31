"""
Talking to the Xod Partner: the small companion app (bundle dev.xod.partner)
that holds the Accessibility grant and does the physical acting for the Muse
approval cards since Partner v1.

The Partner serves an authenticated loopback API (the pairing token lives in
its state dir, the same one its command inbox uses). Xod asks it three things:
its status (is it running, is macOS honoring its grant), a read-only scan of
the Muse app's cards, and a press of one card's approving button under the
frozen rules the Partner carries. Every decision stays here in Xod (modes,
away, pause, which cards may be pressed, the record); the Partner only reads
trees and presses what it is told to press.

Everything here fails soft: any transport error, refusal, or odd answer comes
back as None, and the caller falls back to Xod's own in-process walk. A
Partner that vanished between the check and the call must never take the
watcher down with it.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("xod.partner_client")

DEFAULT_URL = "http://127.0.0.1:8795"
STATE_DIR = Path.home() / "Library" / "Application Support" / "XodPartner"
BUNDLE_ID = "dev.xod.partner"

PARTNER_NOTICE = ("Xod Partner needs its Accessibility switch on; it is the app that presses "
                  "approval buttons now. Open the Accessibility settings and flip Xod Partner "
                  "on. (Xod's own switch no longer matters for approvals.)")

_cache: Dict[str, Any] = {"at": 0.0, "status": None}


def _base() -> str:
    return os.environ.get("XOD_PARTNER_URL", DEFAULT_URL).rstrip("/")


def _token() -> Optional[str]:
    tok = os.environ.get("XOD_PARTNER_TOKEN")
    if tok:
        return tok.strip()
    state = Path(os.environ.get("XOD_PARTNER_STATE_DIR", str(STATE_DIR)))
    try:
        return (state / "token").read_text().strip()
    except Exception:
        return None


def _call(method: str, path: str, body: Optional[Dict[str, Any]] = None,
          timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """One API call. The parsed JSON body, or None on any transport failure.
    A refusal (401/403) returns the body's dict too: it carries the trust
    state the caller needs, marked with the HTTP status under '_status'."""
    tok = _token()
    if not tok:
        return None
    req = urllib.request.Request(_base() + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + tok,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read())
            out["_status"] = e.code
            return out
        except Exception:
            return None
    except Exception:
        return None


def status(force: bool = False) -> Optional[Dict[str, Any]]:
    """The Partner's own /status (watchdog summary + trust), cached for 5s:
    the watcher asks on every scan and must not hammer the Partner for it."""
    now = time.time()
    if not force and now - _cache["at"] < 5.0:
        return _cache["status"]
    out = _call("GET", "/status", timeout=2.0)
    _cache.update({"at": now, "status": out})
    return out


def summary() -> Dict[str, Any]:
    """What the auto-approve settings surface shows about the Partner:
    running or not, whether macOS honors its grant, and the plain notice
    when it is up but untrusted (the repair is one switch flip, and the
    switch is named Xod Partner, not Xod)."""
    st = status()
    if not st or st.get("_status"):
        return {"running": False, "trusted": None, "version": None, "bundle_id": BUNDLE_ID,
                "notice": None}
    trusted = st.get("partner_trusted")
    return {"running": True, "trusted": trusted, "version": st.get("version"),
            "bundle_id": st.get("bundle_id") or BUNDLE_ID,
            "notice": PARTNER_NOTICE if trusted is False else None}


def scan(front: bool = False) -> Optional[Dict[str, Any]]:
    """A read-only look at Muse's cards through the Partner. None when the
    Partner cannot serve it (down, or a transport error); a dict with
    trusted False when it refuses because its grant is not in effect."""
    return _call("POST", "/ax/scan", {"front": bool(front)}, timeout=20.0)


def press(card_id: str, front: bool = False, away: bool = False) -> Optional[Dict[str, Any]]:
    """Ask the Partner to press the approving button on one named card.
    Same answer discipline as scan()."""
    return _call("POST", "/ax/press",
                 {"card_id": card_id, "front": bool(front), "away": bool(away)}, timeout=25.0)
