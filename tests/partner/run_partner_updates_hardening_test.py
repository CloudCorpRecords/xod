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

The same day brought a second incident: a healthy 1.1.16, installed and
cold-launched, was rolled back to 1.1.14 and blocked because the file
round-trip probe's agent task ended with status 'failed' while the
backend was under a burst of mail-endpoint 429s. The confirmation
battery could not save it: it re-runs the same probe through the same
overloaded task system, so the same false failure came back twice. Two
more rules came out of that one:

  D. A file probe whose task system produces no verdict (the task ends
     failed or cancelled, the task API errors, no terminal state in
     time) is INCONCLUSIVE, never a failure. An inconclusive battery
     neither accepts nor rolls back: the verdict waits, and when the
     task system recovers the re-run battery can still accept.
  E. A genuine file failure still rolls back: when both batteries see
     the task complete with the wrong bytes (a content mismatch, the
     one file result that is evidence about the build), the rollback
     happens and the block record carries the full battery history.

Cases A to C FAIL against the pre-hardening guardian and pass against
the hardened one; case D fails against the 1.1.16 guardian (which
counted a task-end 'failed' status as a probe failure) and passes
against the probe-fix guardian. Fully simulated, same rig shape as
run_partner_updates_test.py: fake app trees, a fake updates dir, a fake
backend with a health budget, a task-tamper counter, and a task-failure
counter.

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
    (a transient file-round-trip failure), later writes are exact.
    fail_tasks: the first N created tasks end with status 'failed' and
    write nothing (the task system itself failing to produce a verdict,
    the 1.1.16 incident shape)."""

    def __init__(self, health_passes=10 ** 9, chat_mode="healthy", tamper_tasks=0,
                 fail_tasks=0):
        self.health_passes = health_passes
        self.health_calls = 0
        self.chat_mode = chat_mode
        self.tamper_tasks = tamper_tasks
        self.fail_tasks = fail_tasks
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
                    tid = "task-%d" % (len(outer.tasks) + 1)
                    if outer.fail_tasks > 0:
                        outer.fail_tasks -= 1
                        outer.tasks[tid] = {"id": tid, "status": "failed",
                                            "result": "the task system is overloaded"}
                        return self._send(200, json.dumps({"task": {"id": tid}}))
                    data = content
                    if outer.tamper_tasks > 0:
                        outer.tamper_tasks -= 1
                        data = content + " TAMPERED"
                    Path(path).write_text(data)
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
           "update_task_poll_s": 0.05,
           "update_file_probe_backoff_s": 0.1}
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


def case_d_task_system_failure_is_inconclusive():
    """The 2026-11-22 1.1.16 incident, replayed: the new build is
    healthy (health, chat and permission probes pass) but every file
    task ends with status 'failed' because the task system behind the
    probe is overloaded. That is no verdict on the build: the install
    must be left in place, unaccepted and unblocked, and once the task
    system recovers the re-run battery accepts it. Against the 1.1.16
    guardian this case fails at the first assertion: there a task-end
    'failed' counted as a probe failure, both batteries 'failed' on
    that one probe, and the healthy install was rolled back."""
    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend(fail_tasks=10 ** 9)  # every task ends 'failed'
        guardian, state, app_path, lines, calls, token, server = rig(tmp, backend)
        stage_payload(Path(tmp) / "updates", "0.9.13", "new-0.9.13")
        guardian.tick()  # snapshot of 0.9.12 taken
        swap_in(app_path, "0.9.13", "new-0.9.13", token, "pid2:start2")
        new_bytes = tree_bytes(app_path)
        guardian.tick()  # battery: file probe can get no verdict
        assert tree_bytes(app_path) == new_bytes, \
            "a task-system failure rolled back a healthy install"
        assert calls["quit"] == 0, calls
        assert not (state / "blocked-update-0.9.13.json").exists(), \
            "an inconclusive battery blocked the payload"
        st = guardian.status()
        assert st["pending_version"] == "0.9.13", st
        assert st["last_battery_verdict"] == "inconclusive", st
        checks = guardian.state["last_battery"]["checks"]
        file_check = [c for c in checks if c["name"] == "file round trip"][0]
        assert file_check["verdict"] == "inconclusive", file_check
        assert all(c["verdict"] == "pass" for c in checks
                   if c["name"] != "file round trip"), checks
        assert any("INCONCLUSIVE" in ln for ln in lines), lines
        # an immediate re-tick does not hammer the task system: the
        # retry interval gates the next battery
        battery_at = guardian.state["last_battery"]["at"]
        guardian.tick()
        assert guardian.state["last_battery"]["at"] == battery_at, \
            "the inconclusive retry gate did not hold the battery back"
        assert tree_bytes(app_path) == new_bytes
        # the task system recovers; past the retry interval the re-run
        # battery passes and the update is accepted, still in place
        backend.fail_tasks = 0
        attempt = guardian.state["last_battery_attempt"]
        attempt["at_epoch"] = attempt["at_epoch"] - \
            (guardian.cfg["update_battery_retry_min_s"] + 1)
        guardian.tick()
        st = guardian.status()
        assert st["pending_version"] is None, st
        assert st["last_battery_ok"] is True, st
        assert tree_bytes(app_path) == new_bytes, "the accepted build moved"
        assert not (state / "blocked-update-0.9.13.json").exists()
        server.shutdown()
        print("PASS D: a file task ending 'failed' is inconclusive, never a "
              "rollback; the held update is accepted once the task system "
              "recovers", flush=True)


def case_e_content_mismatch_still_rolls_back():
    """The other side of case D: the file task COMPLETES but writes the
    wrong bytes, in the first battery and in the confirmation battery.
    A content mismatch is evidence about the build, so the rollback
    still happens, and the block record carries the full battery
    history (which battery failed, per-probe verdicts, guardian build)
    so the next field incident is diagnosable from the record alone."""
    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend(tamper_tasks=10 ** 9)  # every write tampered
        guardian, state, app_path, lines, calls, token, server = rig(tmp, backend)
        good_bytes = tree_bytes(app_path)
        stage_payload(Path(tmp) / "updates", "0.9.13", "bad-0.9.13")
        guardian.tick()  # snapshot of 0.9.12 taken
        swap_in(app_path, "0.9.13", "bad-0.9.13", token, "pid2:start2")
        guardian.quit_app = lambda: (calls.__setitem__("quit", calls["quit"] + 1),
                                     setattr(backend, "tamper_tasks", 0))
        guardian.tick()  # both batteries fail the file probe on content
        assert tree_bytes(app_path) == good_bytes, \
            "a two-battery content mismatch did not roll back"
        assert PU.read_version(app_path) == "0.9.12"
        marker = json.loads((state / "blocked-update-0.9.13.json").read_text())
        assert marker["rollbacks"] == 1, marker
        assert marker["restored_version"] == "0.9.12", marker
        assert "file round trip" in marker["failure"], marker
        assert marker["failed_battery"] == "confirmation", marker
        assert [b["which"] for b in marker["batteries"]] == ["first", "confirmation"], \
            marker
        for batt in marker["batteries"]:
            assert batt["verdict"] == "fail", batt
            fc = [c for c in batt["checks"] if c["name"] == "file round trip"][0]
            assert fc["verdict"] == "fail" and "mismatch" in fc["detail"], fc
        assert marker["guardian_build"] == PU.GUARDIAN_BUILD, marker
        st = guardian.status()
        assert st["guardian_build"] == PU.GUARDIAN_BUILD, st
        assert st["blocked_batteries"]["0.9.13"] == marker["batteries"], st
        server.shutdown()
        print("PASS E: a content mismatch in both batteries still rolls back, "
              "and the block record carries the battery history and the "
              "guardian build", flush=True)


def main():
    case_a_stale_snapshot_refused()
    case_b_transient_battery_failure()
    case_c_restored_build_dead_put_back()
    case_d_task_system_failure_is_inconclusive()
    case_e_content_mismatch_still_rolls_back()
    print("ALL HARDENING CASES PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
