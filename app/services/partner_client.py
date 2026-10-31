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
    switch is named Xod Partner, not Xod). The mirrored watchdog counts
    ride along (see PartnerWatchdog below); a watchdog hold outranks the
    trust notice, because it is the thing a human must act on."""
    st = status()
    try:
        wd = get_watchdog().status(time.time())
    except Exception:
        wd = {"planned_revivals_last_hour": 0, "unplanned_revivals_last_hour": 0,
              "cap_spent": 0, "hold": False, "last_revival": None}
    if not st or st.get("_status"):
        out: Dict[str, Any] = {"running": False, "trusted": None, "version": None,
                               "bundle_id": BUNDLE_ID, "notice": None}
    else:
        trusted = st.get("partner_trusted")
        out = {"running": True, "trusted": trusted, "version": st.get("version"),
               "bundle_id": st.get("bundle_id") or BUNDLE_ID,
               "notice": PARTNER_NOTICE if trusted is False else None}
    out.update(wd)
    if wd.get("hold"):
        out["notice"] = HOLD_NOTICE
    return out


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


# -- reciprocal supervision: Xod watches the Partner back -------------------
# The Partner watches Xod; since Partner v1.1, Xod watches the Partner with
# the same discipline, because launchd KeepAlive alone cannot see a wedge
# (process alive, port dead). Probe on the Partner's own cadence (~15s),
# act only on 3 consecutive failures spanning ~30s, one restart per failure
# episode, and the mirrored accounting: a revival of a planned Partner
# shutdown (its marker file, or an operator note through Xod) does not
# spend the cap; unplanned deaths and wedges do, 3 per hour, after which
# Xod holds and says so in the settings notice instead of looping.

WATCH_PROBE_INTERVAL_S = 15
WATCH_FAIL_THRESHOLD = 3
WATCH_FAIL_SPAN_S = 30
WATCH_MAX_UNPLANNED_PER_HOUR = 3
WATCH_PLANNED_WINDOW_S = 600
PARTNER_MARKER = "partner-planned-shutdown.json"
HOLD_NOTICE = ("Xod Partner died 3 times in the last hour without a planned shutdown, so Xod "
               "has stopped restarting it for now. Restart Xod Partner yourself, or note a "
               "planned restart here, and Xod will watch it again.")


def _iso(ts: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _state_dir() -> Path:
    return Path(os.environ.get("XOD_PARTNER_STATE_DIR", str(STATE_DIR)))


class PartnerWatchdog:
    """Decision core, the mirror of the Partner's own Core: probe, restart,
    and the clock are injected, so the tests drive every path without
    touching the real Partner."""

    def __init__(self, probe, restart, log, state_dir):
        self.probe = probe
        self.restart = restart
        self.log = log
        self.state_dir = Path(state_dir)
        self.failures = 0
        self.first_fail_at: Optional[float] = None
        self.partner_up: Optional[bool] = None
        self.episode_acted = False
        self.revivals = []  # [(ts, planned)]
        self.cap_hold = False
        self.planned_until = 0.0
        self.last_revival: Optional[Dict[str, Any]] = None

    # -- planned signals, mirrored from the Partner's accounting ---------
    def _marker_path(self) -> Path:
        return self.state_dir / PARTNER_MARKER

    def _planned_signal(self, now: float) -> bool:
        if now <= self.planned_until:
            return True
        try:
            data = json.loads(self._marker_path().read_text())
            at = float(data.get("at"))
        except Exception:
            return False
        return 0 <= now - at <= WATCH_PLANNED_WINDOW_S

    def _consume_planned_signal(self) -> None:
        self.planned_until = 0.0
        try:
            self._marker_path().unlink()
        except Exception:
            pass

    def _unplanned_in_window(self, now: float):
        return [t for t, p in self.revivals if not p and now - t < 3600]

    def _planned_in_window(self, now: float):
        return [t for t, p in self.revivals if p and now - t < 3600]

    def note_planned(self, now: float, reason: str = "") -> None:
        self.planned_until = now + WATCH_PLANNED_WINDOW_S
        if self.cap_hold:
            self.cap_hold = False
            self.log("partner watchdog: a planned restart was noted by an operator; the hold lifts")
        else:
            self.log("partner watchdog: planned restart noted (%s); revivals in the next %ds "
                     "count as planned" % (reason or "no reason given", WATCH_PLANNED_WINDOW_S))

    # -- one cycle ---------------------------------------------------------
    def cycle(self, now: float) -> Dict[str, Any]:
        try:
            ok = bool(self.probe())
        except Exception:
            ok = False  # a probe that dies (a wedged Partner holds the port) is a failure
        if ok:
            if self.partner_up is False:
                self.log("partner watchdog: the Partner is answering again")
                # That shutdown ended without our revival (launchd or a human
                # brought it back): its planned marker has served its purpose.
                try:
                    self._marker_path().unlink()
                except Exception:
                    pass
            self.failures = 0
            self.first_fail_at = None
            self.partner_up = True
            self.episode_acted = False
            if self.cap_hold and len(self._unplanned_in_window(now)) < WATCH_MAX_UNPLANNED_PER_HOUR:
                self.cap_hold = False
                self.log("partner watchdog: the hold lifts (the unplanned window has drained)")
            return self.status(now)
        self.failures += 1
        if self.first_fail_at is None:
            self.first_fail_at = now
        self.partner_up = False
        gate = (self.failures >= WATCH_FAIL_THRESHOLD and self.first_fail_at is not None
                and now - self.first_fail_at >= WATCH_FAIL_SPAN_S)
        if gate and not self.episode_acted:
            self.episode_acted = True
            self._act(now)
        return self.status(now)

    def _act(self, now: float) -> None:
        unplanned = len(self._unplanned_in_window(now))
        if unplanned >= WATCH_MAX_UNPLANNED_PER_HOUR:
            self.cap_hold = True
            self.log("partner watchdog: HOLD, %d unplanned Partner deaths in the last hour; "
                     "not restarting it again until the window drains or an operator "
                     "notes a planned restart" % unplanned)
            return
        planned = self._planned_signal(now)
        if planned:
            self._consume_planned_signal()
        self.log("partner watchdog: restarting the Partner (%s)"
                 % ("planned shutdown" if planned else "unplanned death"))
        try:
            ok = bool(self.restart())
        except Exception as exc:  # a broken restart must never kill the watcher
            ok = False
            self.log("partner watchdog: the restart raised: %s" % exc)
        self.revivals.append((now, planned))
        self.last_revival = {"at": _iso(now), "planned": planned,
                             "outcome": "restart issued" if ok else "restart failed"}

    def status(self, now: float) -> Dict[str, Any]:
        return {
            "planned_revivals_last_hour": len(self._planned_in_window(now)),
            "unplanned_revivals_last_hour": len(self._unplanned_in_window(now)),
            "cap_spent": len(self._unplanned_in_window(now)),
            "hold": self.cap_hold,
            "last_revival": self.last_revival,
        }


def _probe_partner() -> bool:
    """Alive means answering at all: a refusal dict still proves the process
    serves its port, while a wedge (alive, port deaf) answers nothing."""
    return status(force=True) is not None


def _restart_partner() -> bool:
    """Restart the Partner's LaunchAgent job. If the job cannot be kicked
    (not loaded), open the bundle instead."""
    import subprocess
    try:
        r = subprocess.run(["launchctl", "kickstart", "-k", "gui/%d/%s" % (os.getuid(), BUNDLE_ID)],
                           capture_output=True, timeout=30)
        if r.returncode == 0:
            return True
        logger.warning("partner watchdog: kickstart answered %s: %s", r.returncode,
                       (r.stderr or b"").decode("utf-8", "ignore")[:200])
    except Exception as exc:
        logger.warning("partner watchdog: kickstart failed: %s", exc)
    try:
        r = subprocess.run(["open", str(Path.home() / "Applications" / "Xod Partner.app")],
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


_watchdog: Optional[PartnerWatchdog] = None


def get_watchdog() -> PartnerWatchdog:
    global _watchdog
    if _watchdog is None:
        _watchdog = PartnerWatchdog(_probe_partner, _restart_partner,
                                    lambda m: logger.info(m), _state_dir())
    return _watchdog


def note_planned_restart(reason: str = "") -> Dict[str, Any]:
    """The operator note: a Partner restart in the next ten minutes is
    planned (a rebuild, an install, a manual restart), so the revival it
    needs does not spend the mirrored cap."""
    wd = get_watchdog()
    wd.note_planned(time.time(), reason)
    return wd.status(time.time())


def start_watchdog() -> None:
    """Start the reciprocal-supervision loop. Only the installed app runs
    it: a dev instance must never fight the production Xod over restarting
    the Partner (the same guard the planned-shutdown marker uses)."""
    from app.services import permissions
    if not permissions.own_bundle_id():
        logger.info("partner watchdog: this process is not the installed app; the watchdog stays off")
        return
    import asyncio

    async def _loop():
        wd = get_watchdog()
        logger.info("partner watchdog: watching the Partner every %ss", WATCH_PROBE_INTERVAL_S)
        while True:
            try:
                await asyncio.to_thread(wd.cycle, time.time())
            except Exception as exc:
                logger.warning("partner watchdog cycle failed: %s", exc)
            await asyncio.sleep(WATCH_PROBE_INTERVAL_S)

    asyncio.create_task(_loop())
