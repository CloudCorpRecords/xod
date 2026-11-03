"""
Stale Partner grant detection (the re-pair prompt, Xod fix round 1).

The Partner is ad-hoc signed, so macOS binds its Accessibility row to the
exact build (its CDHash, recorded by install.sh in build.txt in the Partner
state dir). Any Partner rebuild makes the grant stale: the Partner reports
untrusted and approvals quietly go nowhere. Xod records the last build it
saw WITH a trusted reading (a settings row), and the stale condition is:
a build was once trusted, and the Partner now reports untrusted. That
covers both shapes: the same build flipping trusted to untrusted, and a
new build arriving untrusted after an older build was trusted. A fresh
install that was never paired has no trusted build on record, so it keeps
the plain pairing notice and never gets the re-pair walkthrough.

The checks drive summary() with a fake Partner status, a fake build
identity, and an in-memory stand-in for the settings store, the same
monkeypatch style as run_partner_watchdog_test.py. Nothing here touches
the real Partner, the real settings DB, or the real Keychain.

Run: .venv/bin/python tests/partner/run_partner_repair_test.py
"""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from app.services import partner_client as PC

BUILD_A = "aaaa1111buildA"
BUILD_B = "bbbb2222buildB"


class _FakeWatchdog:
    def status(self, now):
        return {"planned_revivals_last_hour": 0, "unplanned_revivals_last_hour": 0,
                "cap_spent": 0, "hold": False, "last_revival": None}


class _Rig:
    """summary() with every outside seam faked: the Partner's /status, the
    build identity reader, the trusted-build settings row, the watchdog."""

    def __init__(self, trusted, build, stored_trusted_build=None):
        self.trusted = trusted
        self.build = build
        self.store = {"v": stored_trusted_build}
        self._saved = []

    def __enter__(self):
        self._orig = (PC.status, PC.build_identity, PC._load_trusted_build,
                      PC._save_trusted_build, PC._watchdog)
        rig = self

        def fake_status(force=False):
            if rig.trusted is None:
                return None
            return {"partner_trusted": rig.trusted, "version": "1.2",
                    "bundle_id": "dev.xod.partner"}

        PC.status = fake_status
        PC.build_identity = lambda state_dir=None: rig.build
        PC._load_trusted_build = lambda: rig.store["v"]

        def fake_save(v):
            rig.store["v"] = v
            rig._saved.append(v)

        PC._save_trusted_build = fake_save
        PC._watchdog = _FakeWatchdog()
        return self

    def __exit__(self, *exc):
        (PC.status, PC.build_identity, PC._load_trusted_build,
         PC._save_trusted_build, PC._watchdog) = self._orig
        return False


def main():
    # the pure trigger, directly
    assert PC.detect_stale_grant(False, BUILD_A, BUILD_A) is True
    assert PC.detect_stale_grant(False, BUILD_B, BUILD_A) is True
    assert PC.detect_stale_grant(False, BUILD_A, None) is False
    assert PC.detect_stale_grant(True, BUILD_B, BUILD_A) is False
    assert PC.detect_stale_grant(None, BUILD_B, BUILD_A) is False
    print("PASS: detect_stale_grant fires only on untrusted with a trusted build on record", flush=True)

    # (a) same build, trusted true then false: stale fires
    with _Rig(trusted=True, build=BUILD_A) as rig:
        block = PC.summary()
        assert block["trusted"] is True and not block.get("stale_grant"), block
        assert rig.store["v"] == BUILD_A, rig.store  # the trusted reading is recorded
        rig.trusted = False
        block = PC.summary()
        assert block["stale_grant"] is True, block
        assert block["notice"] == PC.REPAIR_NOTICE, block
        steps = block["repair_steps"]
        assert [s["n"] for s in steps] == [1, 2, 3], steps
        assert "Reset" in steps[0]["text"] and "ask" in steps[1]["text"].lower(), steps
        assert "flip" in steps[2]["text"] and "Xod Partner" in steps[2]["text"], steps
        print("PASS: (a) same build trusted then untrusted fires the re-pair walkthrough", flush=True)

    # (b) build changes while untrusted, previous build was trusted: stale fires
    with _Rig(trusted=False, build=BUILD_B, stored_trusted_build=BUILD_A) as rig:
        block = PC.summary()
        assert block["stale_grant"] is True, block
        assert block["build"] == BUILD_B and block["trusted_build"] == BUILD_A, block
        assert block["notice"] == PC.REPAIR_NOTICE, block
        print("PASS: (b) a new untrusted build after a trusted one fires stale", flush=True)

    # (c) never-paired fresh build, untrusted: stale does NOT fire
    with _Rig(trusted=False, build=BUILD_A, stored_trusted_build=None) as rig:
        block = PC.summary()
        assert not block.get("stale_grant"), block
        assert block["notice"] == PC.PARTNER_NOTICE, block
        assert not block.get("repair_steps"), block
        print("PASS: (c) a never-paired build keeps the plain pairing notice", flush=True)

    # (d) after re-pair: trusted true on the new build records it and clears
    with _Rig(trusted=False, build=BUILD_B, stored_trusted_build=BUILD_A) as rig:
        assert PC.summary()["stale_grant"] is True
        rig.trusted = True  # the human flipped the fresh row on
        block = PC.summary()
        assert block["trusted"] is True and not block.get("stale_grant"), block
        assert block["notice"] is None, block
        assert rig.store["v"] == BUILD_B, rig.store  # the new build is now the trusted one
        rig.trusted = False  # and a later slip on THIS build is stale again
        assert PC.summary()["stale_grant"] is True
        print("PASS: (d) re-pair records the new trusted build and the notice clears", flush=True)

    # build.txt parsing: cdhash is the identity; no file, no identity
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        assert PC.build_identity(d) is None
        (d / "build.txt").write_text("cdhash=%s\nbuilt=2026-10-31T00:00:00Z\nversion=1.2\n" % BUILD_A)
        assert PC.build_identity(d) == BUILD_A
        (d / "build.txt").write_text("built=2026-10-31T00:00:00Z\nversion=1.2\n")
        assert PC.build_identity(d) == "1.2@2026-10-31T00:00:00Z"
        print("PASS: build_identity reads the CDHash out of build.txt", flush=True)

    print("ALL PARTNER REPAIR TESTS PASSED", flush=True)


if __name__ == "__main__":
    main()
