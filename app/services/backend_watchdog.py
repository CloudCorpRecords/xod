"""
Backend watchdog: stall supervision that lives OUTSIDE the watched
process (Fix Round 6, BOARD-001 Lane D).

BOARD-001 finding: during the board's own mission the backend
stalled for roughly seventeen minutes (about 09:30-09:47 PT) with
nothing watching it: the task queue's watchdog supervises tasks, but
it runs inside the same process, so a wedged backend takes its own
supervisor down with it. This module is the outside watcher, in the
pattern the codebase already trusts for the partner process
(partner_client.PartnerWatchdog): an injected probe, an injected
restart, an injected clock, state persisted to disk so a replacement
watcher resumes the count instead of starting from zero.

It is deliberately not wired into the app process. Run it as its own
process beside the app:

    python -m app.services.backend_watchdog

which probes the local /api/config endpoint (a route the production
backend actually serves — /api/health is a 404 there) and restarts the app the
platform way (launchctl on macOS, the scheduled task / service on
Windows) when a stall is proven. The restart action there is
install-time wiring: this module ships the supervision logic and the
entry point; the ship step registers it with the OS scheduler, the
same posture as the partner watchdog's own registration.

Rules, mirroring PartnerWatchdog:
- One dead probe is a hiccup, not a stall. A stall is
  stall_after_sec of continuous dead probes.
- A proven stall buys ONE restart. Recovery is recorded when the
  probe answers again.
- A backend that keeps dying is not restarted forever: after
  max_restarts_per_hour restarts inside an hour the watchdog holds
  (records the hold, stops restarting) instead of looping restarts
  against a backend that cannot stay up.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.backend_watchdog")

# Probed route: /api/config answers 200 on the production backend on both
# platforms (verified live at the 0.9.23 install and again in the 0.9.24 fix
# lane); /api/health returns 404 there, which is how the shipped watchdog
# came to read a healthy backend as dead.
HEALTH_URL = "http://127.0.0.1:8000/api/config"
DEFAULT_STATE = DATA_DIR / "backend_watchdog.json"


class BackendWatchdog:
    """Decides, tick by tick, whether the backend is stalled and what
    to do about it. Pure logic over injected callables: probe() ->
    bool (the backend answered), restart() -> bool (the restart was
    issued), clock() -> float seconds."""

    def __init__(self, probe: Callable[[], bool], restart: Callable[[], bool],
                 clock: Callable[[], float] = time.time,
                 state_path: Optional[Path] = None,
                 stall_after_sec: float = 120.0,
                 max_restarts_per_hour: int = 3) -> None:
        self._probe = probe
        self._restart = restart
        self._clock = clock
        self._state_path = Path(state_path) if state_path else DEFAULT_STATE
        self.stall_after_sec = stall_after_sec
        self.max_restarts_per_hour = max_restarts_per_hour
        self._dead_since: Optional[float] = None
        self._restart_times: List[float] = []
        self._held = False
        self._last_stall_at: Optional[float] = None
        self._load()

    # ------------------------------------------------------------ state
    def _load(self) -> None:
        try:
            saved = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._restart_times = [float(t) for t in saved.get("restart_times") or []]
            self._held = bool(saved.get("held"))
            self._last_stall_at = saved.get("last_stall_at")
        except Exception:
            pass   # a missing or unreadable state file starts clean, never crashes the watcher

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps({
                "restart_times": self._restart_times,
                "restarts": len(self._restart_times),
                "held": self._held,
                "last_stall_at": self._last_stall_at,
                "dead_since": self._dead_since,
                "updated_at": self._clock(),
            }), encoding="utf-8")
        except Exception:
            logger.exception("backend watchdog: state save failed")

    def status(self) -> Dict[str, Any]:
        return {"healthy": self._dead_since is None and not self._held,
                "held": self._held,
                "restarts": len(self._restart_times),
                "restart_times": list(self._restart_times),
                "dead_since": self._dead_since,
                "last_stall_at": self._last_stall_at}

    # ------------------------------------------------------------ the tick
    def tick(self) -> str:
        """One supervision tick. Returns what happened: "healthy",
        "dead" (inside the stall window, not yet proven), "restarted",
        "held" (restart budget spent; no further restarts), or
        "recovered" (first live probe after a dead spell)."""
        now = self._clock()
        try:
            alive = bool(self._probe())
        except Exception:
            alive = False
        if alive:
            was_dead = self._dead_since is not None
            self._dead_since = None
            if was_dead:
                self._save()
                return "recovered"
            return "healthy"
        if self._dead_since is None:
            self._dead_since = now
            self._save()
            return "dead"
        if now - self._dead_since < self.stall_after_sec:
            return "dead"
        # A proven stall.
        self._last_stall_at = now
        hour_ago = now - 3600
        self._restart_times = [t for t in self._restart_times if t > hour_ago]
        if len(self._restart_times) >= self.max_restarts_per_hour:
            if not self._held:
                self._held = True
                logger.error("backend watchdog: restart budget spent "
                             "(%d restarts in an hour); holding instead of "
                             "restart-looping a backend that cannot stay up",
                             len(self._restart_times))
            self._save()
            return "held"
        issued = False
        try:
            issued = bool(self._restart())
        except Exception:
            logger.exception("backend watchdog: restart action failed")
        if issued:
            self._restart_times.append(now)
            self._dead_since = None   # the next dead spell is measured fresh
            self._save()
            return "restarted"
        self._save()
        return "held"


# ------------------------------------------------------------ standalone run
def _http_probe(url: str = HEALTH_URL) -> bool:
    """Alive = the backend ANSWERED. Any HTTP status below 500 proves a
    live backend — including a 404, which urllib raises as HTTPError
    instead of returning a response (the shipped probe let that
    exception fall into the blanket except and read "dead", so a
    healthy backend looked permanently stalled). Only a connection
    failure (nothing listening) or a 5xx answer is dead."""
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return 200 <= r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


def _platform_restart() -> bool:
    """Restart the backend the platform way. Install-time wiring: the
    ship step registers the app's service/launch agent; this asks that
    registration to bounce. Returns False (and the watchdog records a
    hold rather than pretending) when no registration answers."""
    import subprocess
    import sys
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["launchctl", "kickstart", "-k",
                                f"gui/{os.getuid()}/com.cloudcorp.xod"],
                               capture_output=True, timeout=30)
            return r.returncode == 0
        if sys.platform == "win32":
            r = subprocess.run(["schtasks", "/Run", "/TN", "XodBackend"],
                               capture_output=True, timeout=30)
            return r.returncode == 0
    except Exception:
        logger.exception("backend watchdog: platform restart failed")
    return False


def main(poll_sec: float = 15.0) -> None:
    logging.basicConfig(level=logging.INFO)
    wd = BackendWatchdog(probe=_http_probe, restart=_platform_restart)
    logger.info("backend watchdog running (state: %s)", wd._state_path)
    while True:
        try:
            outcome = wd.tick()
        except Exception:
            # A watcher that dies silently supervises nothing (the first
            # install's loop went quiet after one tick, unexplained).
            logger.exception("backend watchdog: tick failed")
            time.sleep(poll_sec)
            continue
        if outcome not in ("healthy", "dead"):
            logger.warning("backend watchdog: %s", outcome)
        time.sleep(poll_sec)


if __name__ == "__main__":
    main()
