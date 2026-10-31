#!/usr/bin/env python3
"""
xod-partner: the small companion that watches Xod so Xod never has to
watch itself.

Charter (v0): watch Xod's health, restart Xod when it dies, keep the log.
The Partner is deliberately tiny and frozen; new features belong in Xod,
never here. Permissions and update-swapping arrive in later versions,
only once this skeleton is proven. Design record: the "Partner app"
addendum in XOD-BRAIN-ARCHITECTURE.md.

Run: python3 xod_partner.py [state-dir]
State dir default: ~/Library/Application Support/XodPartner
"""
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_STATE_DIR = Path.home() / "Library" / "Application Support" / "XodPartner"

DEFAULTS = {
    "probe_url": "http://127.0.0.1:8000/",
    "probe_interval_s": 15,
    "probe_timeout_s": 5,
    "fail_threshold": 3,
    "fail_span_s": 30,
    "quit_wait_s": 20,
    "restart_verify_timeout_s": 90,
    "max_restarts_per_hour": 3,
    "backoff_s": [30, 120, 300],
    "app_name": "Xod",
}

KNOWN_COMMANDS = ("restart_xod", "verify", "release_hold")


def iso(ts=None):
    ts = time.time() if ts is None else ts
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def load_config(state_dir):
    cfg = dict(DEFAULTS)
    beside = Path(__file__).resolve().parent / "config.json"
    for path in (beside, Path(state_dir) / "config.json"):
        try:
            cfg.update(json.loads(Path(path).read_text()))
        except Exception:
            pass
    return cfg


class Log:
    """Append-only partner.log plus stdout (launchd captures both)."""

    def __init__(self, path):
        self.path = Path(path)

    def __call__(self, msg):
        line = "%s %s" % (iso(), msg)
        try:
            with open(self.path, "a") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
        print(line, flush=True)


class Core:
    """Decision core. Probe, restart, and the log are injected, and every
    cycle is handed the current time, so the tests can drive each path
    without touching the real Xod or the real clock."""

    def __init__(self, cfg, state_dir, probe, restart, log):
        self.cfg = cfg
        self.state_dir = Path(state_dir)
        self.probe = probe
        self.restart = restart
        self.log = log
        self.token = self._load_token()
        self.failures = 0
        self.first_fail_at = None
        self.down_since = None
        self.xod_up = None
        self.last_probe_at = None
        self.last_probe_ok = None
        self.restarts = []  # [(ts, outcome)] attempts, oldest first
        self.cap_hold = False
        self.backoff_until = 0.0
        self.last_action = None
        self.last_outcome = None

    def _load_token(self):
        path = self.state_dir / "token"
        try:
            return path.read_text().strip()
        except Exception:
            pass
        token = secrets.token_hex(16)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(token)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return token

    def _hold_file(self):
        return (self.state_dir / "HOLD").exists()

    def _in_window(self, now):
        return [t for t, _ in self.restarts if now - t < 3600]

    # -- one cycle ---------------------------------------------------
    def cycle(self, now):
        forced = self._process_inbox(now)
        ok = bool(self.probe())
        self.last_probe_at = now
        self.last_probe_ok = ok
        if ok:
            if self.xod_up is False:
                self.log("probe ok: Xod is answering again")
            self.failures = 0
            self.first_fail_at = None
            self.down_since = None
            self.xod_up = True
        else:
            self.failures += 1
            if self.first_fail_at is None:
                self.first_fail_at = now
                self.down_since = now
            self.xod_up = False
            self.log("probe failed (%d consecutive)" % self.failures)
        gate = (
            not ok
            and self.failures >= self.cfg["fail_threshold"]
            and self.first_fail_at is not None
            and now - self.first_fail_at >= self.cfg["fail_span_s"]
        )
        if forced or gate:
            self._maybe_restart(now, "inbox command" if forced else "health gate")
        return self.status(now)

    # -- restart discipline -------------------------------------------
    def _maybe_restart(self, now, why):
        if self._hold_file():
            self.last_action, self.last_outcome = "restart skipped", "HOLD file present"
            self.log("restart skipped (%s): HOLD file present" % why)
            return
        if self.cap_hold:
            self.last_action, self.last_outcome = "restart skipped", "cap hold active"
            self.log("restart skipped (%s): cap hold active, waiting for a human or release_hold" % why)
            return
        window = self._in_window(now)
        if len(window) >= self.cfg["max_restarts_per_hour"]:
            self.cap_hold = True
            self.last_action, self.last_outcome = "HOLD", "restart cap reached"
            self.log(
                "HOLD: restart cap reached (%d restarts in the last hour); "
                "no more restarts until a human or a release_hold command" % len(window)
            )
            return
        if now < self.backoff_until:
            self.last_action, self.last_outcome = "restart deferred", "backoff"
            return
        self.last_action = "restart Xod (%s)" % why
        self.log("restarting Xod (%s)" % why)
        try:
            ok = bool(self.restart())
        except Exception as exc:  # a broken restart must never kill the watcher
            ok = False
            self.log("restart raised: %s" % exc)
        outcome = "healthy" if ok else "still down"
        self.restarts.append((now, outcome))
        self.last_outcome = outcome
        idx = max(0, min(len(self._in_window(now)) - 1, len(self.cfg["backoff_s"]) - 1))
        self.backoff_until = now + self.cfg["backoff_s"][idx]
        self.log(
            "restart outcome: %s (restarts in last hour: %d, next attempt not before +%ds)"
            % (outcome, len(self._in_window(now)), self.cfg["backoff_s"][idx])
        )
        if ok:
            self.failures = 0
            self.first_fail_at = None
            self.down_since = None
            self.xod_up = True
            self.last_probe_ok = True

    # -- inbox ---------------------------------------------------------
    def _process_inbox(self, now):
        forced = False
        inbox = self.state_dir / "inbox"
        rejected = self.state_dir / "rejected"
        try:
            files = sorted(inbox.glob("*.json"))
        except Exception:
            return False
        for path in files:
            try:
                data = json.loads(path.read_text())
            except Exception:
                data = None
            cmd = data.get("cmd") if isinstance(data, dict) else None
            if not isinstance(data, dict) or data.get("token") != self.token or cmd not in KNOWN_COMMANDS:
                rejected.mkdir(parents=True, exist_ok=True)
                dest = rejected / path.name
                if dest.exists():
                    dest = rejected / ("%s.%d" % (path.name, int(now)))
                try:
                    path.rename(dest)
                except Exception:
                    pass
                self.log("inbox: rejected %s (bad token or unknown command)" % path.name)
                continue
            try:
                path.unlink()
            except Exception:
                pass
            self.log("inbox: command %s accepted" % cmd)
            if cmd == "restart_xod":
                forced = True
            elif cmd == "release_hold":
                if self.cap_hold:
                    self.cap_hold = False
                    self.restarts = []  # a human decision resets the budget
                    self.backoff_until = 0.0
                    self.log("cap hold released by command; restart budget reset")
                else:
                    self.log("release_hold: no cap hold was active")
            elif cmd == "verify":
                self.log("verify command: fresh probe and status this cycle")
        return forced

    # -- status ----------------------------------------------------------
    def status(self, now):
        hold = "file" if self._hold_file() else ("cap" if self.cap_hold else None)
        return {
            "partner": "xod-partner",
            "version": 0,
            "partner_pid": os.getpid(),
            "updated_at": iso(now),
            "last_probe_at": iso(self.last_probe_at) if self.last_probe_at is not None else None,
            "last_probe_ok": self.last_probe_ok,
            "consecutive_failures": self.failures,
            "xod_state": "up" if self.xod_up else ("down" if self.xod_up is False else "unknown"),
            "xod_down_since": iso(self.down_since) if self.down_since is not None else None,
            "restarts_last_hour": len(self._in_window(now)),
            "restart_history": [{"at": iso(t), "outcome": o} for t, o in self.restarts[-5:]],
            "hold": hold,
            "last_action": self.last_action,
            "last_outcome": self.last_outcome,
        }


# -- real side effects ---------------------------------------------------


def http_probe(url, timeout):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "xod-partner/0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False


def _app_running(app):
    pat = "/Applications/%s.app/Contents/MacOS/%s" % (app, app)
    out = subprocess.run(["pgrep", "-f", pat], capture_output=True, timeout=10)
    return out.returncode == 0


def make_restarter(cfg, probe, log):
    app = cfg["app_name"]
    pat = "/Applications/%s.app/Contents/MacOS/%s" % (app, app)

    def restart():
        try:
            subprocess.run(["osascript", "-e", 'quit app "%s"' % app],
                           capture_output=True, timeout=30)
        except Exception:
            pass
        deadline = time.time() + cfg["quit_wait_s"]
        while time.time() < deadline and _app_running(app):
            time.sleep(1)
        if _app_running(app):
            log("Xod did not quit in %ss; killing the app process" % cfg["quit_wait_s"])
            subprocess.run(["pkill", "-f", pat], capture_output=True, timeout=15)
            time.sleep(2)
        subprocess.run(["open", "-a", app], capture_output=True, timeout=30)
        deadline = time.time() + cfg["restart_verify_timeout_s"]
        while time.time() < deadline:
            if probe():
                return True
            time.sleep(3)
        return False

    return restart


def main(argv):
    state_dir = Path(argv[1]).expanduser() if len(argv) > 1 else DEFAULT_STATE_DIR
    (state_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (state_dir / "rejected").mkdir(parents=True, exist_ok=True)
    cfg = load_config(state_dir)
    log = Log(state_dir / "partner.log")
    probe = lambda: http_probe(cfg["probe_url"], cfg["probe_timeout_s"])
    core = Core(cfg, state_dir, probe, make_restarter(cfg, probe, log), log)
    stopping = {"flag": False}

    def _stop(signum, frame):
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log("xod-partner starting (pid %d, probing %s every %ss)"
        % (os.getpid(), cfg["probe_url"], cfg["probe_interval_s"]))
    last_status = None
    while not stopping["flag"]:
        try:
            core.cycle(time.time())
        except Exception as exc:  # the watcher must outlive its own bugs
            log("cycle error: %s" % exc)
        text = json.dumps(core.status(time.time()), indent=1, sort_keys=True)
        if text != last_status:
            tmp = state_dir / "status.json.tmp"
            tmp.write_text(text)
            tmp.replace(state_dir / "status.json")
            last_status = text
        time.sleep(cfg["probe_interval_s"])
    log("xod-partner stopping")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
