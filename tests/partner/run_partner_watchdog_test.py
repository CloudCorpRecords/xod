"""
Xod-side Partner watchdog (reciprocal supervision, Partner v1.1 era).

The Partner watches Xod; this is the other direction, the one launchd
KeepAlive cannot cover: a wedged Partner (process alive, port dead) looks
fine to launchd and dead to everyone who needs it. Xod's backend probes
the Partner's /status on the Partner's own cadence, restarts its
LaunchAgent job only on a sustained failure, and keeps the mirrored
restart accounting: planned Partner shutdowns (the Partner's marker file,
or an operator note through Xod) do not spend the cap; unplanned deaths
and wedges do, 3 per hour, after which Xod holds and surfaces the hold in
the settings notice. The checks drive the decision core with a fake
probe, a fake restarter, and a hand-moved clock.

Run: .venv/bin/python tests/partner/run_partner_watchdog_test.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from app.services import partner_client as PC


class _FakeProbe:
    def __init__(self, ok=True, raises=False):
        self.ok = ok
        self.raises = raises

    def __call__(self):
        if self.raises:
            raise TimeoutError("the port is held by a process that never answers")
        return self.ok


class _FakeRestart:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.ok


def _wd(tmp, probe, restart, lines):
    return PC.PartnerWatchdog(probe, restart, lines.append, Path(tmp))


def _kill(wd, probe, base):
    """Three failing probes spanning the gate's 30 seconds."""
    probe.ok = False
    wd.cycle(base)
    wd.cycle(base + 15.0)
    wd.cycle(base + 30.0)


def _revive(wd, probe, at):
    probe.ok = True
    wd.cycle(at)


def main():
    # a healthy Partner is never restarted
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(True), _FakeRestart(), []
        wd = _wd(tmp, probe, restart, lines)
        wd.cycle(0.0)
        wd.cycle(15.0)
        wd.cycle(30.0)
        assert restart.calls == 0, restart.calls
        print("PASS: a healthy Partner is never restarted", flush=True)

    # a single dropped probe, and two, are noise
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(False), _FakeRestart(), []
        wd = _wd(tmp, probe, restart, lines)
        wd.cycle(0.0)
        wd.cycle(15.0)
        assert restart.calls == 0 and wd.failures == 2, (restart.calls, wd.failures)
        print("PASS: two failed probes trigger no restart", flush=True)

    # a cold kill: sustained failure restarts the Partner once per episode,
    # and the revival spends the unplanned cap
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(True), _FakeRestart(), []
        wd = _wd(tmp, probe, restart, lines)
        _kill(wd, probe, 1000.0)
        assert restart.calls == 1, restart.calls
        st = wd.status(1030.0)
        assert st["unplanned_revivals_last_hour"] == 1 and st["cap_spent"] == 1, st
        assert st["planned_revivals_last_hour"] == 0 and st["hold"] is False, st
        assert st["last_revival"]["planned"] is False, st
        wd.cycle(1045.0)  # still down, same episode: no second restart
        wd.cycle(1060.0)
        assert restart.calls == 1, restart.calls
        _revive(wd, probe, 1075.0)
        _kill(wd, probe, 2000.0)  # a NEW episode restarts again
        assert restart.calls == 2, restart.calls
        st = wd.status(2030.0)
        assert st["unplanned_revivals_last_hour"] == 2 and st["cap_spent"] == 2, st
        print("PASS: a cold kill is revived once per episode and spends the cap", flush=True)

    # a graceful exit (the Partner's marker) is revived as planned
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(True), _FakeRestart(), []
        wd = _wd(tmp, probe, restart, lines)
        (Path(tmp) / PC.PARTNER_MARKER).write_text(json.dumps({"at": 1025.0, "reason": "quit"}))
        _kill(wd, probe, 1000.0)
        assert restart.calls == 1, restart.calls
        st = wd.status(1030.0)
        assert st["planned_revivals_last_hour"] == 1 and st["cap_spent"] == 0, st
        assert st["last_revival"]["planned"] is True, st
        assert not (Path(tmp) / PC.PARTNER_MARKER).exists()  # consumed
        print("PASS: a graceful Partner exit is revived as planned, marker consumed", flush=True)

    # a wedge: the probe itself dies on a held port; the restart still fires
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(raises=True), _FakeRestart(), []
        wd = _wd(tmp, probe, restart, lines)
        wd.cycle(0.0)
        wd.cycle(15.0)
        wd.cycle(30.0)
        assert restart.calls == 1, restart.calls
        st = wd.status(30.0)
        assert st["unplanned_revivals_last_hour"] == 1 and st["cap_spent"] == 1, st
        print("PASS: a wedged Partner (probe raises) is restarted and counted unplanned", flush=True)

    # an operator note excuses one window; after it, deaths count again
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(True), _FakeRestart(), []
        wd = _wd(tmp, probe, restart, lines)
        wd.note_planned(0.0, "test note")
        _kill(wd, probe, 100.0)
        assert restart.calls == 1, restart.calls
        st = wd.status(130.0)
        assert st["planned_revivals_last_hour"] == 1 and st["cap_spent"] == 0, st
        _revive(wd, probe, 200.0)
        _kill(wd, probe, 1000.0)  # the note (600s from t=0) has expired
        st = wd.status(1030.0)
        assert st["unplanned_revivals_last_hour"] == 1 and st["cap_spent"] == 1, st
        print("PASS: an operator note excuses inside its window only", flush=True)

    # three unplanned deaths land the hold, and the settings notice says so
    with tempfile.TemporaryDirectory() as tmp:
        probe, restart, lines = _FakeProbe(True), _FakeRestart(), []
        wd = _wd(tmp, probe, restart, lines)
        for base in (0.0, 1000.0, 2000.0):
            _kill(wd, probe, base)
            _revive(wd, probe, base + 100.0)
        assert restart.calls == 3, restart.calls
        _kill(wd, probe, 3000.0)  # the fourth death: hold instead of a restart
        assert restart.calls == 3, restart.calls
        st = wd.status(3030.0)
        assert st["hold"] is True and st["cap_spent"] == 3, st
        saved_wd, saved_status, saved_time = PC._watchdog, PC.status, PC.time
        try:
            from types import SimpleNamespace
            PC._watchdog = wd
            PC.status = lambda force=False: None
            PC.time = SimpleNamespace(time=lambda: 3030.0)
            block = PC.summary()
        finally:
            PC._watchdog, PC.status, PC.time = saved_wd, saved_status, saved_time
        assert block["notice"] == PC.HOLD_NOTICE, block
        assert block["hold"] is True and block["cap_spent"] == 3, block
        assert block["last_revival"] is not None, block
        print("PASS: three unplanned Partner deaths land the hold and the notice says so", flush=True)

    print("ALL PARTNER WATCHDOG TESTS PASSED", flush=True)


if __name__ == "__main__":
    main()
