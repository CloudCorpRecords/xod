"""
Partner update guardian tests: snapshot-and-guardian around Xod's own
self-updater, fully simulated.

Everything runs against throwaway state: a fake Xod.app tree (a directory
with a Contents/Info.plist carrying a version plus marker files), a fake
updates dir holding a staged payload in the real layout (Xod.app beside
SHA256SUMS.txt, as electron/updater.js stages it), and a fake backend (a
tiny HTTP server on a throwaway port implementing the health, chat, task
and auto-approve settings endpoints, switchable healthy and unhealthy).
Nothing touches the installed Xod, the real updates cache, or any real
port. Covered: payload appears and the snapshot is taken and verified
with no battery while the version is unchanged; a swap to a healthy new
version passes the battery and is accepted with the snapshot retained;
a swap to an unhealthy version fails the battery and rolls back byte for
byte with a block marker naming the version; the same bad version
staging again does not overwrite the good snapshot and a second rollback
escalates to BLOCKED; the disk-space refusal; snapshot rotation keeping
exactly one snapshot.

Run: python3 tests/partner/run_partner_updates_test.py
"""
import hashlib
import json
import os
import plistlib
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Standing rule (2026-10-31): test processes never reach for the real
# vault, so every suite runner names the file keyring backend explicitly,
# in the same shape the smoke suites use. This suite's own paths (the
# Partner modules and a fake HTTP backend) never load vault code; the
# setting is set anyway so the rule holds for anything it imports.
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "partner"))
import partner_updates as PU


def make_app(base, version, marker):
    app = Path(base) / "Xod.app"
    (app / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    with open(app / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump({"CFBundleShortVersionString": version,
                       "CFBundleIdentifier": "dev.xod.desktop",
                       "CFBundleExecutable": "Xod"}, fh)
    (app / "Contents" / "marker.txt").write_text(marker)
    (app / "Contents" / "MacOS" / "Xod").write_text("fake-binary-" + version)
    return app


def stage_payload(updates_dir, version, marker):
    updates = Path(updates_dir)
    updates.mkdir(parents=True, exist_ok=True)
    app = make_app(updates, version, marker)
    (updates / "SHA256SUMS.txt").write_text(
        "%s  Xod-%s-arm64.dmg\n" % (hashlib.sha256(marker.encode()).hexdigest(), version))
    (updates / "install.sh").write_text("#!/bin/bash\n# fake staged swap script\n")
    return app


def tree_bytes(root):
    root = Path(root)
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


class FakeBackend:
    """mode healthy: chat echoes the demanded exact reply, the task writes
    the exact bytes it was told to write. mode unhealthy: chat replies
    wrong and the task writes the wrong bytes."""

    def __init__(self):
        self.mode = "healthy"
        self.tasks = {}

    def handler(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/":
                    return self._send(200, "{}")
                if self.path == "/api/tasks":
                    return self._send(200, json.dumps({"tasks": list(outer.tasks.values())}))
                if self.path == "/api/crew/settings/auto-approve":
                    return self._send(200, json.dumps({
                        "permissions": {"trusted": True, "bundle_id": "dev.xod.desktop"},
                        "partner": {"running": True, "trusted": False,
                                    "bundle_id": "dev.xod.partner"}}))
                return self._send(404, "{}")

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/api/chat":
                    prompt = body["messages"][0]["content"]
                    want = prompt.rsplit(":", 1)[-1].strip()
                    reply = want if outer.mode == "healthy" else "not the exact reply"
                    return self._send(200, 'data: {"type": "content", "content": %s}\n\n'
                                      % json.dumps(reply), "text/event-stream")
                if self.path == "/api/tasks":
                    text = body["request"]
                    m = re.match(r".*file at (\S+) whose entire contents are exactly "
                                 r"this, with no extra words and no extra lines: (.*)$",
                                 text, re.DOTALL)
                    path, content = m.group(1), m.group(2)
                    data = content if outer.mode == "healthy" else content + " TAMPERED"
                    Path(path).write_text(data)
                    tid = "task-%d" % (len(outer.tasks) + 1)
                    outer.tasks[tid] = {"id": tid, "status": "done", "result": "ok"}
                    return self._send(200, json.dumps({"task": {"id": tid}}))
                if self.path.startswith("/api/tasks/") and self.path.endswith("/approve"):
                    return self._send(200, "{}")
                return self._send(404, "{}")

        return H


def rig(tmp, backend, free=10 ** 12):
    tmp = Path(tmp)
    state = tmp / "state"
    state.mkdir(parents=True, exist_ok=True)
    installed = make_app(tmp / "installed", "0.9.12", "good-0.9.12")
    install_dir = tmp / "installed"
    app_path = install_dir / "Xod.app"
    lines, calls = [], {"quit": 0, "relaunch": 0}
    token = {"value": "pid1:start1"}
    server = ThreadingHTTPServer(("127.0.0.1", 0), backend.handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = {"update_updates_dir": str(tmp / "updates"),
           "update_app_path": str(app_path),
           "update_api_base": "http://127.0.0.1:%d" % server.server_address[1],
           "update_health_timeout_s": 5, "update_health_poll_s": 0.05,
           "update_chat_timeout_s": 10, "update_task_timeout_s": 10,
           "update_task_poll_s": 0.05}
    guardian = PU.UpdateGuardian(
        cfg, state, lines.append,
        quit_app=lambda: calls.__setitem__("quit", calls["quit"] + 1),
        relaunch_app=lambda: calls.__setitem__("relaunch", calls["relaunch"] + 1),
        app_start_token=lambda: token["value"],
        free_bytes=lambda: free)
    return guardian, state, app_path, lines, calls, token, server


def swap_in(app_path, version, marker, token, new_token):
    import shutil
    shutil.rmtree(app_path)
    make_app(app_path.parent, version, marker)
    token["value"] = new_token


def main():
    # payload appears: snapshot taken and verified, no battery yet
    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend()
        g, state, app_path, lines, calls, token, server = rig(tmp, backend)
        before = tree_bytes(app_path)
        stage_payload(Path(tmp) / "updates", "0.9.13", "staged-0.9.13")
        g.tick(1000.0)
        snap = state / "snapshots" / "Xod.app"
        assert snap.exists(), "snapshot missing"
        assert tree_bytes(snap) == before, "snapshot contents differ from the installed app"
        assert [p.name for p in (state / "snapshots").iterdir()] == ["Xod.app"]
        st = g.status()
        assert st["snapshot_version"] == "0.9.12", st
        assert st["pending_version"] == "0.9.13", st
        assert st["last_battery_ok"] is None, st
        saved = json.loads((state / "updates-guardian.json").read_text())
        assert saved["pending"]["pre_version"] == "0.9.12", saved
        assert saved["pending"]["payload_version"] == "0.9.13", saved
        assert any("snapshot taken" in ln for ln in lines), lines
        # same payload still sitting there: no second snapshot, no battery
        g.tick(1015.0)
        assert sum("snapshot taken" in ln for ln in lines) == 1, lines
        assert g.status()["last_battery_ok"] is None
        server.shutdown()
        print("PASS: payload appearance snapshots the current app, verified, no battery yet", flush=True)

    # swap to a healthy new version: battery passes, accepted, snapshot kept
    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend()
        g, state, app_path, lines, calls, token, server = rig(tmp, backend)
        stage_payload(Path(tmp) / "updates", "0.9.13", "staged-0.9.13")
        g.tick(1000.0)
        swap_in(app_path, "0.9.13", "good-0.9.13", token, "pid2:start2")
        g.tick(1015.0)
        st = g.status()
        assert st["last_battery_ok"] is True, (st, lines)
        assert st["last_battery_for"] == "0.9.13", st
        assert st["pending_version"] is None, st
        assert (state / "snapshots" / "Xod.app").exists(), "snapshot must be retained"
        assert PU.read_version(state / "snapshots" / "Xod.app") == "0.9.12"
        assert any("update accepted" in ln and "0.9.12" in ln and "0.9.13" in ln
                   for ln in lines), lines
        assert calls["quit"] == 0 and calls["relaunch"] == 0, calls
        server.shutdown()
        print("PASS: healthy swap passes the battery and is accepted, snapshot retained", flush=True)

    # version stamp changes but no restart yet: battery waits
    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend()
        g, state, app_path, lines, calls, token, server = rig(tmp, backend)
        stage_payload(Path(tmp) / "updates", "0.9.13", "staged-0.9.13")
        g.tick(1000.0)
        import shutil
        shutil.rmtree(app_path)
        make_app(app_path.parent, "0.9.13", "good-0.9.13")  # same start token
        g.tick(1015.0)
        assert g.status()["last_battery_ok"] is None, g.status()
        assert g.status()["pending_version"] == "0.9.13", g.status()
        assert any("has not restarted yet" in ln for ln in lines), lines
        server.shutdown()
        print("PASS: a version change without a restart does not run the battery", flush=True)

    # swap to an unhealthy version: rollback restores byte for byte, marker lands
    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend()
        g, state, app_path, lines, calls, token, server = rig(tmp, backend)
        good_bytes = tree_bytes(app_path)
        stage_payload(Path(tmp) / "updates", "0.9.13", "staged-0.9.13")
        g.tick(1000.0)
        backend.mode = "unhealthy"
        swap_in(app_path, "0.9.13", "bad-0.9.13", token, "pid2:start2")
        # The bad build misbehaves while it is installed; once the guardian
        # quits it for the rollback, the restored build is the healthy one.
        # The quit hook models exactly that moment.
        g.quit_app = lambda: (calls.__setitem__("quit", calls["quit"] + 1),
                              setattr(backend, "mode", "healthy"))
        g.tick(1015.0)
        assert tree_bytes(app_path) == good_bytes, "rollback did not restore byte for byte"
        assert PU.read_version(app_path) == "0.9.12"
        assert calls["quit"] == 1 and calls["relaunch"] == 1, calls
        marker = state / "blocked-update-0.9.13.json"
        assert marker.exists(), "block marker missing"
        m = json.loads(marker.read_text())
        assert m["version"] == "0.9.13" and m["rollbacks"] == 1, m
        assert "chat exact reply" in m["failure"] or "file round trip" in m["failure"], m
        planned = json.loads((state / "planned-shutdown.json").read_text())
        assert "rollback" in planned["reason"], planned
        st = g.status()
        assert st["blocked_versions"] == ["0.9.13"], st
        assert st["pending_version"] is None, st
        assert any("rolling back" in ln for ln in lines), lines
        server.shutdown()
        print("PASS: unhealthy swap fails the battery, rolls back byte for byte, block marker written", flush=True)

        # the same bad version stages again: snapshot NOT overwritten, a
        # second rollback escalates to BLOCKED
        stage_payload(Path(tmp) / "updates", "0.9.13", "staged-0.9.13-again")
        g.tick(2000.0)
        snap = state / "snapshots" / "Xod.app"
        assert tree_bytes(snap) == good_bytes, "blocked restage overwrote the good snapshot"
        assert any("NOT overwritten" in ln for ln in lines), lines
        assert g.status()["pending_version"] == "0.9.13", g.status()
        backend.mode = "unhealthy"
        swap_in(app_path, "0.9.13", "bad-0.9.13-again", token, "pid3:start3")
        g.tick(2015.0)
        assert tree_bytes(app_path) == good_bytes, "second rollback did not restore"
        m = json.loads(marker.read_text())
        assert m["rollbacks"] == 2, m
        assert any("BLOCKED:" in ln and "0.9.13" in ln for ln in lines), lines
        assert g.status()["blocked"] == {"0.9.13": 2}, g.status()
        server.shutdown()
        print("PASS: blocked restage keeps the good snapshot; second rollback escalates to BLOCKED", flush=True)

    # disk-space refusal: no snapshot, a clear log line, nothing staged over
    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend()
        g, state, app_path, lines, calls, token, server = rig(tmp, backend, free=1024)
        stage_payload(Path(tmp) / "updates", "0.9.13", "staged-0.9.13")
        g.tick(1000.0)
        assert not (state / "snapshots" / "Xod.app").exists(), "refusal still snapshotted"
        assert any("snapshot REFUSED" in ln and "cannot fit" in ln for ln in lines), lines
        assert g.status()["snapshot_present"] is False, g.status()
        server.shutdown()
        print("PASS: low disk space refuses the snapshot with a clear log line", flush=True)

    # snapshot rotation keeps exactly one
    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend()
        g, state, app_path, lines, calls, token, server = rig(tmp, backend)
        stage_payload(Path(tmp) / "updates", "0.9.13", "staged-0.9.13")
        g.tick(1000.0)
        assert PU.read_version(state / "snapshots" / "Xod.app") == "0.9.12"
        # accept 0.9.13, then a new payload for 0.9.14 stages
        swap_in(app_path, "0.9.13", "good-0.9.13", token, "pid2:start2")
        g.tick(1015.0)
        assert g.status()["last_battery_ok"] is True, lines
        import shutil
        shutil.rmtree(Path(tmp) / "updates")
        stage_payload(Path(tmp) / "updates", "0.9.14", "staged-0.9.14")
        g.tick(2000.0)
        snaps = [p.name for p in (state / "snapshots").iterdir()]
        assert snaps == ["Xod.app"], snaps
        assert PU.read_version(state / "snapshots" / "Xod.app") == "0.9.13"
        assert g.status()["snapshot_version"] == "0.9.13", g.status()
        server.shutdown()
        print("PASS: snapshot rotation keeps exactly one snapshot, of the newest good version", flush=True)

    # the hook: the guardian state rides in the Partner /status output
    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend()
        g, state, app_path, lines, calls, token, server = rig(tmp, backend)
        stage_payload(Path(tmp) / "updates", "0.9.13", "staged-0.9.13")
        g.tick(1000.0)
        import xod_partner as XP
        cfg = dict(XP.DEFAULTS)
        core = XP.Core(cfg, state, lambda: True, lambda: True, lines.append)
        core.updates_guardian = g
        st = XP.api_status(core)
        assert st["updates"]["snapshot_version"] == "0.9.12", st["updates"]
        assert st["updates"]["pending_version"] == "0.9.13", st["updates"]
        assert st["updates"]["blocked_versions"] == [], st["updates"]
        server.shutdown()
        print("PASS: the guardian state is visible as an updates block in the Partner /status output", flush=True)

    print("ALL PARTNER UPDATE GUARDIAN TESTS PASSED", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
