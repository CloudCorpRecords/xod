#!/usr/bin/env python3
"""
xod-partner: the small companion that watches Xod so Xod never has to
watch itself.

Charter: watch Xod's health, restart Xod when it dies, keep the log (v0),
and since v1 hold the Accessibility grant and do the physical acting on
Xod's request: an authenticated loopback API offers a read-only scan of
the Muse app's approval cards and a press of the approving button,
under the frozen rules ported in partner_ax. The Partner stays dumb:
every decision (modes, away, pause, whether to press at all) is Xod's.
The Partner is deliberately tiny and frozen; new features belong in Xod,
never here. Design record: the "Partner app" addendum in
XOD-BRAIN-ARCHITECTURE.md.

v1.1 (2026-10-31) changes only the restart accounting, plus one signal
write: the hourly restart cap is spent by unplanned revivals only. A
planned shutdown (Xod's marker file in the state dir, or an authenticated
note_planned_restart inbox command, each good for ten minutes) still gets
its revival; it just does not spend the cap. Status reports the split.
And on a graceful exit the Partner writes its own planned-shutdown
marker, so Xod's watchdog can keep the same accounts in the other
direction (reciprocal supervision).

Run: python3 xod_partner.py [state-dir]
State dir default: ~/Library/Application Support/XodPartner
"""
import hmac
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    "planned_window_s": 600,
    "backoff_s": [30, 120, 300],
    "app_name": "Xod",
    "api_host": "127.0.0.1",
    "api_port": 8795,
}

VERSION = "1.1"
BUNDLE_ID = "dev.xod.partner"

KNOWN_COMMANDS = ("restart_xod", "verify", "release_hold", "note_planned_restart")


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

    def __init__(self, cfg, state_dir, probe, restart, log, trust=None):
        self.cfg = cfg
        self.state_dir = Path(state_dir)
        self.probe = probe
        self.restart = restart
        self.log = log
        self.trust = trust  # callable -> True/False/None: is OUR Accessibility grant in effect?
        self.started_at = time.time()
        self.token = self._load_token()
        self.failures = 0
        self.first_fail_at = None
        self.down_since = None
        self.xod_up = None
        self.last_probe_at = None
        self.last_probe_ok = None
        self.restarts = []  # [(ts, outcome, planned)] attempts, oldest first
        self.cap_hold = False
        self.planned_until = 0.0  # an operator note excuses revivals until this time
        self.backoff_until = 0.0
        self.last_action = None
        self.last_outcome = None
        self.updates_guardian = None  # partner_updates.UpdateGuardian, wired in main()

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
        return [t for t, _, _ in self.restarts if now - t < 3600]

    def _unplanned_in_window(self, now):
        return [t for t, _, planned in self.restarts if not planned and now - t < 3600]

    def _planned_in_window(self, now):
        return [t for t, _, planned in self.restarts if planned and now - t < 3600]

    # -- planned-shutdown signals --------------------------------------
    # A planned signal never suppresses a revival; it only keeps that
    # revival from spending the hourly cap. Two sources, each valid for
    # planned_window_s: the marker file Xod's backend writes on its
    # graceful shutdown, and the authenticated note_planned_restart inbox
    # command (operator or promoter kills that cannot write the marker).

    def _marker_path(self):
        return self.state_dir / "planned-shutdown.json"

    def _planned_signal(self, now):
        if now <= self.planned_until:
            return True
        try:
            data = json.loads(self._marker_path().read_text())
            at = float(data.get("at"))
        except Exception:
            return False
        return 0 <= now - at <= self.cfg["planned_window_s"]

    def _consume_planned_signal(self):
        self.planned_until = 0.0
        try:
            self._marker_path().unlink()
        except Exception:
            pass

    # -- one cycle ---------------------------------------------------
    def cycle(self, now):
        forced = self._process_inbox(now)
        ok = bool(self.probe())
        self.last_probe_at = now
        self.last_probe_ok = ok
        if ok:
            if self.xod_up is False:
                self.log("probe ok: Xod is answering again")
                # That shutdown ended without our revival (a human relaunched
                # Xod): its planned marker, if any, has served its purpose.
                try:
                    self._marker_path().unlink()
                except Exception:
                    pass
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
        unplanned = self._unplanned_in_window(now)
        if len(unplanned) >= self.cfg["max_restarts_per_hour"]:
            self.cap_hold = True
            self.last_action, self.last_outcome = "HOLD", "restart cap reached"
            self.log(
                "HOLD: restart cap reached (%d unplanned restarts in the last hour); "
                "no more restarts until a human or a release_hold command" % len(unplanned)
            )
            return
        if now < self.backoff_until:
            self.last_action, self.last_outcome = "restart deferred", "backoff"
            return
        planned = self._planned_signal(now)
        if planned:
            self._consume_planned_signal()
        self.last_action = "restart Xod (%s)" % why
        self.log("restarting Xod (%s%s)" % (why, ", planned shutdown" if planned else ""))
        try:
            ok = bool(self.restart())
        except Exception as exc:  # a broken restart must never kill the watcher
            ok = False
            self.log("restart raised: %s" % exc)
        outcome = "healthy" if ok else "still down"
        self.restarts.append((now, outcome, planned))
        self.last_outcome = outcome
        idx = max(0, min(len(self._in_window(now)) - 1, len(self.cfg["backoff_s"]) - 1))
        self.backoff_until = now + self.cfg["backoff_s"][idx]
        self.log(
            "restart outcome: %s (restarts in last hour: %d, unplanned: %d, next attempt not before +%ds)"
            % (outcome, len(self._in_window(now)), len(self._unplanned_in_window(now)),
               self.cfg["backoff_s"][idx])
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
            elif cmd == "note_planned_restart":
                self.planned_until = now + self.cfg["planned_window_s"]
                self.log("inbox: planned restart noted; revivals in the next %ds "
                         "count as planned" % self.cfg["planned_window_s"])
            elif cmd == "verify":
                self.log("verify command: fresh probe and status this cycle")
        return forced

    # -- status ----------------------------------------------------------
    def status(self, now):
        hold = "file" if self._hold_file() else ("cap" if self.cap_hold else None)
        trusted = self.trust() if self.trust else None
        return {
            "partner": "xod-partner",
            "version": VERSION,
            "partner_pid": os.getpid(),
            "partner_trusted": trusted,
            "ax": {"trusted": trusted, "port": self.cfg.get("api_port")},
            "updated_at": iso(now),
            "last_probe_at": iso(self.last_probe_at) if self.last_probe_at is not None else None,
            "last_probe_ok": self.last_probe_ok,
            "consecutive_failures": self.failures,
            "xod_state": "up" if self.xod_up else ("down" if self.xod_up is False else "unknown"),
            "xod_down_since": iso(self.down_since) if self.down_since is not None else None,
            "restarts_last_hour": len(self._in_window(now)),
            "planned_revivals_last_hour": len(self._planned_in_window(now)),
            "unplanned_revivals_last_hour": len(self._unplanned_in_window(now)),
            "cap_spent": len(self._unplanned_in_window(now)),
            "restart_history": [{"at": iso(t), "outcome": o, "planned": p}
                                for t, o, p in self.restarts[-5:]],
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


# -- the AX API (v1) -------------------------------------------------------
# Authenticated loopback HTTP, the Partner's only network surface. Every
# call needs the pairing token (the same one the inbox uses). The Partner
# stays dumb: /ax/scan only reads, /ax/press applies the frozen rules in
# partner_ax to one card Xod named, and /ax/request-access asks macOS to
# show the Accessibility prompt for this process (the ask that creates
# the row the user flips). Nothing here takes orders from files, web
# content, or task text; the inbox and this API are the only inputs.


def api_status(core):
    st = core.status(time.time())
    st["bundle_id"] = BUNDLE_ID
    st["uptime_s"] = int(time.time() - core.started_at)
    if getattr(core, "updates_guardian", None) is not None:
        try:
            st["updates"] = core.updates_guardian.status()
        except Exception:
            st["updates"] = None
    return st


def make_server(cfg, core, walk, log):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            head = self.headers.get("Authorization") or ""
            return head.startswith("Bearer ") and hmac.compare_digest(head[7:], core.token)

        def _json_body(self):
            try:
                return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except Exception:
                return None

        def _trusted_or_refuse(self):
            t = core.trust() if core.trust else None
            if t is not True:
                self._send(403, {"error": "the Partner's Accessibility access is not in effect",
                                 "trusted": t, "bundle_id": BUNDLE_ID})
                return False
            return True

        def do_GET(self):
            if self.path.split("?")[0] != "/status":
                return self._send(404, {"error": "not found"})
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            self._send(200, api_status(core))

        def do_POST(self):
            path = self.path.split("?")[0]
            if path not in ("/ax/scan", "/ax/press", "/ax/request-access"):
                return self._send(404, {"error": "not found"})
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            body = self._json_body()
            if not isinstance(body, dict):
                return self._send(400, {"error": "a JSON object body is required"})
            if path == "/ax/request-access":
                # The one route that must answer while untrusted: asking
                # is how the grant comes to exist at all. Token-gated like
                # every route, exactly one prompt per call, every ask
                # logged (the ask is a security-relevant event).
                try:
                    result = walk.request_access()
                except Exception as exc:  # a broken ask must never kill the API
                    log("ax request-access failed: %s" % exc)
                    return self._send(500, {"error": "access request failed: %s" % exc,
                                            "prompted": False})
                if result is None:
                    log("ax request-access: the prompt call is unavailable on this system")
                    return self._send(500, {"error": "the Accessibility prompt call is "
                                            "unavailable on this system",
                                            "prompted": False})
                log("ax request-access: prompt shown for this process; "
                    "the call reports trusted=%s" % result)
                return self._send(200, {"trusted": result, "prompted": True})
            if not self._trusted_or_refuse():
                return
            try:
                if path == "/ax/scan":
                    out = walk.scan(front=bool(body.get("front")))
                    out["trusted"] = True
                    self._send(200, out)
                else:
                    want = str(body.get("card_id") or "")
                    if not want:
                        return self._send(400, {"error": "card_id is required"})
                    out = walk.press(want, front=bool(body.get("front")), away=bool(body.get("away")))
                    out["trusted"] = True
                    self._send(200, out)
            except TimeoutError as exc:
                self._send(500, {"error": str(exc)})
            except Exception as exc:  # a broken call must never kill the API or the watcher
                log("ax api error on %s: %s" % (path, exc))
                self._send(500, {"error": "ax call failed: %s" % exc})

    server = ThreadingHTTPServer((cfg["api_host"], int(cfg["api_port"])), Handler)
    server.daemon_threads = True
    return server


def main(argv):
    state_dir = Path(argv[1]).expanduser() if len(argv) > 1 else DEFAULT_STATE_DIR
    (state_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (state_dir / "rejected").mkdir(parents=True, exist_ok=True)
    cfg = load_config(state_dir)
    log = Log(state_dir / "partner.log")
    probe = lambda: http_probe(cfg["probe_url"], cfg["probe_timeout_s"])
    trust, walk = None, None
    try:
        import partner_ax
        ax = partner_ax.CtypesAx()
        trust = ax.trusted
        walk = partner_ax.Walk(ax, log)
    except Exception as exc:  # no AX on this machine: the watchdog still watches
        log("ax service unavailable at startup: %s" % exc)
    core = Core(cfg, state_dir, probe, make_restarter(cfg, probe, log), log, trust=trust)
    try:  # the update guardian (v0.5): a broken guardian must never stop the watchdog
        import partner_updates
        core.updates_guardian = partner_updates.UpdateGuardian(cfg, state_dir, log)
    except Exception as exc:
        log("update guardian unavailable at startup: %s" % exc)
    stopping = {"flag": False}
    graceful = {"flag": False}
    server = None
    if walk is not None:
        def _serve():
            # A previous generation may still hold the port for a few seconds
            # while it notices its launcher is gone; keep trying, briefly.
            nonlocal server
            last = None
            for _ in range(12):
                if stopping["flag"]:
                    return
                try:
                    server = make_server(cfg, core, walk, log)
                    break
                except OSError as exc:
                    last = exc
                    time.sleep(5)
            if server is None:
                log("ax api could not start: %s" % last)
                return
            log("ax api listening on %s:%s" % (cfg["api_host"], cfg["api_port"]))
            server.serve_forever()

        threading.Thread(target=_serve, daemon=True).start()

    def _stop(signum, frame):
        stopping["flag"] = True
        graceful["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log("xod-partner starting (pid %d, probing %s every %ss)"
        % (os.getpid(), cfg["probe_url"], cfg["probe_interval_s"]))
    parented = os.environ.get("XOD_PARTNER_PARENTED") == "1"
    last_status = None
    while not stopping["flag"]:
        if parented and os.getppid() == 1:
            # The bundle launcher is gone (a kickstart replaced it, or it was
            # killed hard): a partner must never outlive its own front door,
            # or two generations end up watching at once.
            log("the launcher is gone; the partner exits with it")
            break
        try:
            core.cycle(time.time())
            if core.updates_guardian is not None:
                core.updates_guardian.tick()
        except Exception as exc:  # the watcher must outlive its own bugs
            log("cycle error: %s" % exc)
        text = json.dumps(core.status(time.time()), indent=1, sort_keys=True)
        if text != last_status:
            tmp = state_dir / "status.json.tmp"
            tmp.write_text(text)
            tmp.replace(state_dir / "status.json")
            last_status = text
        time.sleep(cfg["probe_interval_s"])
    if graceful["flag"]:
        # A signal-driven exit was planned (a quit, an update, an operator
        # restart): leave the marker Xod's watchdog reads, so the revival it
        # performs does not spend its mirrored unplanned cap. The orphan
        # exit above (the launcher died) is NOT planned and writes nothing.
        try:
            tmp = state_dir / "partner-planned-shutdown.json.tmp"
            tmp.write_text(json.dumps({"at": time.time(), "reason": "quit"}))
            tmp.replace(state_dir / "partner-planned-shutdown.json")
            log("planned-shutdown marker written for Xod's watchdog")
        except Exception:
            pass
    log("xod-partner stopping")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
