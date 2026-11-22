"""
Partner update guardian HARDENING tests: the 2026-11-22 incident shapes.

On 2026-11-22 a healthy Xod 1.1.14 (health, chat and permission probes
passing) was rolled back by the guardian on ONE failed file-round-trip
probe, and the rollback restored a retained snapshot of 1.1.4 — two
weeks and ten versions old — because that morning's snapshot of the
actually-replaced 1.1.13 had failed verification and the stale snapshot
was silently used instead. The restored 1.1.4 could not start at all
(its bootstrap cannot read its own archive under Electron), failed its
post-rollback battery on every probe, and was simply left installed,
looping a self-repair dialog. These tests pin the three rules that came
out of that incident:

  A. A rollback may only restore the snapshot of the install the payload
     actually replaced. A stale or missing snapshot means: leave the new
     install in place, block the payload, write a guardian report.
  B. One failed battery never rolls anything back: a confirmation
     battery runs first, and a transient failure is treated as such.
  C. If the restored build cannot start at all (health fails) while the
     replaced build had passed health, the replaced build is put back:
     the install is left in its best known state, never a dead one.

Every case FAILS against the pre-hardening guardian and passes against
the hardened one. Fully simulated, same rig shape as
run_partner_updates_test.py: fake app trees, a fake updates dir, a fake
backend with a health budget and a task-tamper counter.

Run: python3 tests/partner/run_partner_updates_hardening_test.py
"""
import hashlib
import json
import os
import plistlib
import re
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
    """health budget: the first `health_passes` GET / calls answer 200,
    later ones 500 (a build that cannot start). chat_mode 'wrong' breaks
    the exact reply. tamper_tasks: the first N task writes are tampered
    (a transient file-round-trip failure), later writes are exact."""

    def __init__(self, health_passes=10 ** 9, chat_mode="healthy", tamper_tasks=0):
        self.health_passes = health_passes
        self.health_calls = 0
        self.chat_mode = chat_mode
        self.tamper_tasks = tamper_tasks
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
                    outer.health_calls += 1
                    if outer.health_calls <= outer.health_passes:
                        return self._send(200, "{}")
                    return self._send(500, "{}")
                if self.path == "/api/cli/hello":
                    return self._send(404, "{}")
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
                    reply = want if outer.chat_mode == "healthy" else "not the exact reply"
                    return self._send(200, 'data: {"type": "content", "content": %s}\n\n'
                                      % json.dumps(reply), "text/event-stream")
                if self.path == "/api/tasks":
                    text = body["request"]
                    m = re.match(r".*file at (\S+) whose entire contents are exactly "
                                 r"this, with no extra words and no extra lines: (.*)$",
                                 text, re.DOTALL)
                    path, content = m.group(1), m.group(2)
                    data = content
                    if outer.tamper_tasks > 0:
                        outer.tamper_tasks -= 1
                        data = content + " TAMPERED"
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
    install_dir = tmp / "installed"
    app_path = make_app(install_dir, "0.9.12", "good-0.9.12")
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
    shutil.rmtree(app_path)
    make_app(app_path.parent, version, marker)
    token["value"] = new_token


def case_a_stale_snapshot_refused():
    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend(chat_mode="wrong", tamper_tasks=10 ** 9)
        guardian, state, app_path, lines, calls, token, server = rig(tmp, backend, free=0)
        # A retained snapshot from an older cycle: 0.9.11, while 0.9.12 is
        # installed. This is the 1.1.4-vs-1.1.13 shape from the incident.
        make_app(state / "snapshots", "0.9.11", "stale-0.9.11")
        (state / "updates-guardian.json").write_text(json.dumps(
            {"snapshot_version": "0.9.11"}))
        guardian.state = guardian._load_state()
        stage_payload(Path(tmp) / "updates", "0.9.13", "bad-0.9.13")
        guardian.tick()  # payload seen; snapshot refused (no free space)
        swap_in(app_path, "0.9.13", "bad-0.9.13", token, "pid2:start2")
        bad_bytes = tree_bytes(app_path)
        guardian.tick()  # battery fails twice; rollback must be REFUSED
        assert tree_bytes(app_path) == bad_bytes, \
            "the guardian restored a stale snapshot over the new install"
        assert calls["quit"] == 0, "the running app was quit for a restore that must not happen"
        marker = json.loads((state / "blocked-update-0.9.13.json").read_text())
        assert marker["restored_version"] is None, marker
        report = json.loads((state / "guardian-report-0.9.13.json").read_text())
        assert report["kind"] == "stale-snapshot-rollback-refused", report
        assert any("ROLLBACK REFUSED" in ln for ln in lines), lines
        assert any("ROLLBACK PROTECTION UNAVAILABLE" in ln for ln in lines), lines
        server.shutdown()
        print("PASS A: stale snapshot is never restored; new install left in place, "
              "payload blocked, guardian report written", flush=True)


def case_b_transient_battery_failure():
    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend(tamper_tasks=1)  # first file task tampered, then exact
        guardian, state, app_path, lines, calls, token, server = rig(tmp, backend)
        stage_payload(Path(tmp) / "updates", "0.9.13", "new-0.9.13")
        guardian.tick()  # snapshot of 0.9.12 taken
        swap_in(app_path, "0.9.13", "new-0.9.13", token, "pid2:start2")
        new_bytes = tree_bytes(app_path)
        guardian.tick()  # battery 1 fails on the tampered task; confirmation passes
        assert tree_bytes(app_path) == new_bytes, "a transient battery failure rolled back"
        assert calls["quit"] == 0, calls
        assert not (state / "blocked-update-0.9.13.json").exists(), "payload was blocked"
        assert any("confirmation battery" in ln for ln in lines), lines
        server.shutdown()
        print("PASS B: one transient battery failure triggers a confirmation battery, "
              "not a rollback", flush=True)


def case_c_restored_build_dead_put_back():
    with tempfile.TemporaryDirectory() as tmp:
        # Health answers for exactly the two batteries run against the new
        # build, then never again: the restored build cannot start at all.
        backend = FakeBackend(health_passes=2, chat_mode="wrong", tamper_tasks=10 ** 9)
        guardian, state, app_path, lines, calls, token, server = rig(tmp, backend)
        stage_payload(Path(tmp) / "updates", "0.9.13", "bad-0.9.13")
        guardian.tick()  # snapshot of 0.9.12 taken and verified
        swap_in(app_path, "0.9.13", "bad-0.9.13", token, "pid2:start2")
        bad_bytes = tree_bytes(app_path)
        guardian.tick()  # batteries fail; rollback restores 0.9.12; its battery
        # fails on health; the 0.9.13 build (which passed health) is put back
        assert tree_bytes(app_path) == bad_bytes, \
            "a restored build that cannot start was left installed"
        report = json.loads((state / "guardian-report-0.9.13.json").read_text())
        assert report["kind"] == "restored-build-failed-put-back", report
        marker = json.loads((state / "blocked-update-0.9.13.json").read_text())
        assert marker["restored_version"] is None, marker
        server.shutdown()
        print("PASS C: a restored build that cannot start is replaced again by the "
              "better-known pre-rollback build, with a report", flush=True)


def main():
    case_a_stale_snapshot_refused()
    case_b_transient_battery_failure()
    case_c_restored_build_dead_put_back()
    print("ALL HARDENING CASES PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
