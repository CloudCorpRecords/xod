"""
Partner v0 regression test: the watchdog's restart discipline.

The failure this guards against is a watchdog that makes an outage worse:
restarting Xod on a single dropped probe, double-restarting a Xod that is
already on its way back, or restart-looping all night against a Xod that
cannot stay up. The checks drive the decision core with a fake probe, a
fake restarter, and a hand-moved clock, so every path runs without
touching the real Xod. Covered: the 3-failure / 30-second gate, recovery
resetting the streak, backoff between attempts, the 3-per-hour cap and
its HOLD, release_hold resetting the budget, the HOLD file switch, and
the inbox token rules (bad token, unknown command, and malformed files
are rejected and never executed). The v1.1 blocks add the planned /
unplanned split: Xod's planned-shutdown marker and the authenticated
note_planned_restart command excuse a revival from the hourly cap without
ever suppressing the revival itself, an expired marker excuses nothing,
and three unplanned deaths still land the HOLD.

Run: python3 tests/partner/run_partner_test.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "partner"))
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
import xod_partner as XP


class _FakeProbe:
    def __init__(self, ok=True):
        self.ok = ok

    def __call__(self):
        return self.ok


class _FakeRestart:
    def __init__(self, ok=False):
        self.ok = ok
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.ok


def _core(tmp, probe, restart, lines):
    cfg = dict(XP.DEFAULTS)
    return XP.Core(cfg, Path(tmp), probe, restart, lines.append)


def main():
    # a single dropped probe is noise: no restart
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(False), _FakeRestart(), []
        core = _core(tmp, probe, restart, lines)
        core.cycle(1000.0)
        assert restart.calls == 0 and core.failures == 1, (restart.calls, core.failures)
        print("PASS: one failed probe triggers no restart", flush=True)

    # two failures are still noise
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(False), _FakeRestart(), []
        core = _core(tmp, probe, restart, lines)
        core.cycle(1000.0)
        core.cycle(1015.0)
        assert restart.calls == 0 and core.failures == 2, (restart.calls, core.failures)
        print("PASS: two failed probes trigger no restart", flush=True)

    # three failures inside 30 seconds are still noise; the gate needs the span too
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(False), _FakeRestart(), []
        core = _core(tmp, probe, restart, lines)
        core.cycle(1000.0)
        core.cycle(1010.0)
        core.cycle(1020.0)
        assert restart.calls == 0, restart.calls
        core.cycle(1031.0)  # 4th failure, streak now spans 31s
        assert restart.calls == 1, restart.calls
        print("PASS: the gate needs 3 failures AND a 30 second span", flush=True)

    # a success resets the streak, so scattered failures never reach the gate
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(False), _FakeRestart(), []
        core = _core(tmp, probe, restart, lines)
        core.cycle(1000.0)
        core.cycle(1015.0)
        probe.ok = True
        core.cycle(1030.0)
        assert core.failures == 0, core.failures
        probe.ok = False
        core.cycle(1045.0)
        core.cycle(1060.0)
        assert restart.calls == 0, restart.calls
        print("PASS: a healthy probe resets the failure streak", flush=True)

    # backoff, the hourly cap, HOLD, and release_hold resetting the budget
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(False), _FakeRestart(ok=False), []
        core = _core(tmp, probe, restart, lines)
        token = (Path(tmp) / "token").read_text().strip()
        core.cycle(0.0)
        core.cycle(15.0)
        core.cycle(30.0)  # gate met: restart 1, backoff 30s
        assert restart.calls == 1, restart.calls
        core.cycle(45.0)  # gate still met but backoff has not elapsed
        assert restart.calls == 1, restart.calls
        core.cycle(61.0)  # restart 2, backoff grows to 120s
        assert restart.calls == 2, restart.calls
        core.cycle(200.0)  # restart 3, backoff 300s
        assert restart.calls == 3, restart.calls
        core.cycle(600.0)  # a 4th restart would break the cap: HOLD instead
        assert restart.calls == 3 and core.cap_hold, (restart.calls, core.cap_hold)
        assert any("HOLD" in line for line in lines), lines[-3:]
        core.cycle(700.0)  # still held
        assert restart.calls == 3, restart.calls
        print("PASS: backoff spaces attempts and the 3-per-hour cap lands a HOLD", flush=True)

        cmd = {"token": token, "cmd": "release_hold"}
        (Path(tmp) / "inbox").mkdir(exist_ok=True)
        (Path(tmp) / "inbox" / "release.json").write_text(json.dumps(cmd))
        core.cycle(800.0)  # release processed, gate met again, budget was reset
        assert not core.cap_hold and restart.calls == 4, (core.cap_hold, restart.calls)
        st = core.status(800.0)
        assert st["hold"] is None and st["restarts_last_hour"] == 1, st
        print("PASS: release_hold clears the hold and resets the restart budget", flush=True)

    # the HOLD file suppresses restarts even with the gate fully met
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(False), _FakeRestart(), []
        core = _core(tmp, probe, restart, lines)
        (Path(tmp) / "HOLD").write_text("")
        core.cycle(0.0)
        core.cycle(15.0)
        core.cycle(30.0)
        assert restart.calls == 0, restart.calls
        assert core.status(30.0)["hold"] == "file", core.status(30.0)
        (Path(tmp) / "HOLD").unlink()
        core.cycle(45.0)
        assert restart.calls == 1, restart.calls
        print("PASS: the HOLD file suppresses restarts until a human removes it", flush=True)

    # inbox: a valid restart_xod restarts a healthy Xod on command
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(True), _FakeRestart(ok=True), []
        core = _core(tmp, probe, restart, lines)
        token = (Path(tmp) / "token").read_text().strip()
        (Path(tmp) / "inbox").mkdir(exist_ok=True)
        (Path(tmp) / "inbox" / "go.json").write_text(json.dumps({"token": token, "cmd": "restart_xod"}))
        core.cycle(100.0)
        assert restart.calls == 1, restart.calls
        assert not (Path(tmp) / "inbox" / "go.json").exists()
        print("PASS: an authenticated restart_xod command restarts Xod", flush=True)

    # inbox: verify is accepted without any restart
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(True), _FakeRestart(), []
        core = _core(tmp, probe, restart, lines)
        token = (Path(tmp) / "token").read_text().strip()
        (Path(tmp) / "inbox").mkdir(exist_ok=True)
        (Path(tmp) / "inbox" / "v.json").write_text(json.dumps({"token": token, "cmd": "verify"}))
        core.cycle(100.0)
        assert restart.calls == 0, restart.calls
        assert not (Path(tmp) / "inbox" / "v.json").exists()
        assert not (Path(tmp) / "rejected" / "v.json").exists()
        print("PASS: an authenticated verify command is accepted, nothing restarted", flush=True)

    # inbox: bad token, unknown command, and malformed JSON are rejected, never executed
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(True), _FakeRestart(), []
        core = _core(tmp, probe, restart, lines)
        inbox = Path(tmp) / "inbox"
        inbox.mkdir(exist_ok=True)
        (inbox / "bad.json").write_text(json.dumps({"token": "wrong", "cmd": "restart_xod"}))
        (inbox / "unk.json").write_text(json.dumps({"token": core.token, "cmd": "self_destruct"}))
        (inbox / "junk.json").write_text("{not json")
        core.cycle(100.0)
        assert restart.calls == 0, restart.calls
        rejected = sorted(p.name for p in (Path(tmp) / "rejected").glob("*.json"))
        assert rejected == ["bad.json", "junk.json", "unk.json"], rejected
        assert not list(inbox.glob("*.json"))
        print("PASS: bad-token, unknown, and malformed inbox files are rejected unexecuted", flush=True)

    # status reflects a down episode honestly
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(False), _FakeRestart(ok=True), []
        core = _core(tmp, probe, restart, lines)
        core.cycle(0.0)
        core.cycle(15.0)
        st = core.status(15.0)
        assert st["xod_state"] == "down" and st["consecutive_failures"] == 2, st
        assert st["xod_down_since"] is not None and st["restarts_last_hour"] == 0, st
        core.cycle(30.0)  # gate: restart fires and the fake says healthy
        st = core.status(30.0)
        assert st["xod_state"] == "up" and st["restarts_last_hour"] == 1, st
        assert st["last_outcome"] == "healthy", st
        print("PASS: status tracks the down episode, the restart, and the recovery", flush=True)

    # v1.1: a planned shutdown (Xod's marker) is revived but spends no cap,
    # and three unplanned deaths afterwards still fit before the HOLD
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(False), _FakeRestart(ok=False), []
        core = _core(tmp, probe, restart, lines)
        (Path(tmp) / "planned-shutdown.json").write_text(json.dumps({"at": 25.0, "reason": "quit"}))
        core.cycle(0.0)
        core.cycle(15.0)
        core.cycle(30.0)  # gate: revival 1, planned by the marker
        assert restart.calls == 1, restart.calls
        st = core.status(30.0)
        assert st["planned_revivals_last_hour"] == 1 and st["unplanned_revivals_last_hour"] == 0, st
        assert st["cap_spent"] == 0 and st["restarts_last_hour"] == 1, st
        assert st["restart_history"][-1]["planned"] is True, st
        assert not (Path(tmp) / "planned-shutdown.json").exists()  # the signal was consumed
        core.cycle(61.0)   # revival 2, unplanned 1 (backoff from revival 1 ends at 60)
        core.cycle(200.0)  # revival 3, unplanned 2
        core.cycle(600.0)  # revival 4, unplanned 3: the planned one spent nothing
        assert restart.calls == 4 and not core.cap_hold, (restart.calls, core.cap_hold)
        st = core.status(600.0)
        assert st["planned_revivals_last_hour"] == 1 and st["unplanned_revivals_last_hour"] == 3, st
        assert st["cap_spent"] == 3, st
        core.cycle(950.0)  # a 5th revival would be unplanned 4: HOLD instead
        assert restart.calls == 4 and core.cap_hold, (restart.calls, core.cap_hold)
        print("PASS: a planned revival spends no cap; three unplanned deaths still HOLD", flush=True)

    # v1.1: an expired marker excuses nothing
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(False), _FakeRestart(ok=False), []
        core = _core(tmp, probe, restart, lines)
        (Path(tmp) / "planned-shutdown.json").write_text(json.dumps({"at": 0.0, "reason": "quit"}))
        core.cycle(1000.0)
        core.cycle(1015.0)
        core.cycle(1030.0)  # marker is 1030s old, past the 600s window
        assert restart.calls == 1, restart.calls
        st = core.status(1030.0)
        assert st["planned_revivals_last_hour"] == 0 and st["cap_spent"] == 1, st
        assert st["restart_history"][-1]["planned"] is False, st
        print("PASS: an expired planned-shutdown marker counts the revival unplanned", flush=True)

    # v1.1: note_planned_restart excuses revivals inside its window only
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(False), _FakeRestart(ok=False), []
        core = _core(tmp, probe, restart, lines)
        (Path(tmp) / "inbox").mkdir(exist_ok=True)
        (Path(tmp) / "inbox" / "note.json").write_text(json.dumps({"token": core.token, "cmd": "note_planned_restart"}))
        core.cycle(0.0)    # the note is processed; window runs to t=600
        core.cycle(15.0)
        core.cycle(30.0)   # revival 1, planned by the note
        assert restart.calls == 1, restart.calls
        st = core.status(30.0)
        assert st["planned_revivals_last_hour"] == 1 and st["cap_spent"] == 0, st
        core.cycle(631.0)  # the note has expired: revival 2 counts unplanned
        assert restart.calls == 2, restart.calls
        st = core.status(631.0)
        assert st["unplanned_revivals_last_hour"] == 1 and st["cap_spent"] == 1, st
        print("PASS: note_planned_restart excuses inside its 10 minute window only", flush=True)

    # v1.1: a note with a bad token is rejected and excuses nothing
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(False), _FakeRestart(ok=False), []
        core = _core(tmp, probe, restart, lines)
        (Path(tmp) / "inbox").mkdir(exist_ok=True)
        (Path(tmp) / "inbox" / "note.json").write_text(json.dumps({"token": "wrong", "cmd": "note_planned_restart"}))
        core.cycle(0.0)
        core.cycle(15.0)
        core.cycle(30.0)
        assert restart.calls == 1, restart.calls
        assert (Path(tmp) / "rejected" / "note.json").exists()
        st = core.status(30.0)
        assert st["planned_revivals_last_hour"] == 0 and st["cap_spent"] == 1, st
        print("PASS: a bad-token note_planned_restart is rejected and excuses nothing", flush=True)

    print("ALL PARTNER TESTS PASSED", flush=True)


if __name__ == "__main__":
    main()
