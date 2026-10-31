"""
Partner v1 delegation test: Xod's watcher uses the Partner's hands only
when it should.

The failure this guards against is double agency: Xod pressing through
the Partner AND through its own walk in the same scan, falling back
mid-scan after the Partner already pressed something, or delegating to a
Partner macOS is not honoring. The checks drive muse_app's dispatch with
a fake Partner client and a fake in-process walk, so no real card is
touched. Covered: delegation when the Partner is trusted (scan, ladder,
press), fallback when the Partner is down, fallback when it is
untrusted, NO fallback when a press is refused mid-scan (the card just
carries the reason), the already-pressed skip, and the settings surface
carrying the partner block with its notice.

Run with the repo venv: .venv/bin/python tests/partner/run_partner_delegation_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from app.services import muse_app, partner_client

CARD = {"card_id": "museapp-testcard", "title": "Approval request for Gmail",
        "what": "From a@x.com To b@y.com Subject Hi", "buttons": ["Allow", "Deny"],
        "button": "Allow", "expired": False, "type": "Gmail",
        "from": "a@x.com", "to": "b@y.com", "subject": "Hi", "attachments": []}

_calls = {"scan": 0, "own_scan": 0, "press": []}


class FakePartner:
    def __init__(self, trusted=True, down=False, press_result=None):
        self.trusted, self.down = trusted, down
        self.press_result = press_result if press_result is not None else \
            {"pressed": True, "button": "Allow", "reason": "", "trusted": True}

    def status(self, force=False):
        if self.down:
            return None
        return {"partner_trusted": self.trusted, "version": 1, "bundle_id": "dev.xod.partner"}

    def scan(self, front=False):
        _calls["scan"] += 1
        return {"running": True, "trusted": True, "cards": [dict(CARD)]}

    def press(self, card_id, front=False, away=False):
        _calls["press"].append(card_id)
        return dict(self.press_result)


def _fake_own_scan(press, skip_ids=None):
    _calls["own_scan"] += 1
    return {"running": True, "approved": [], "via": "own"}


def _install(fake):
    partner_client.status, partner_client.scan, partner_client.press = \
        fake.status, fake.scan, fake.press
    muse_app._scan = _fake_own_scan
    _calls.update({"scan": 0, "own_scan": 0, "press": []})


def main():
    orig = (partner_client.status, partner_client.scan, partner_client.press, muse_app._scan)
    try:
        # trusted Partner: the whole scan goes through it, Xod's walk never runs
        _install(FakePartner(trusted=True))
        res = muse_app._scan_dispatch(set(), False)
        assert _calls["own_scan"] == 0 and _calls["scan"] == 1, _calls
        assert muse_app._state["last_path"] == "partner", muse_app._state.get("last_path")
        entry = res["approved"][0]
        assert entry["pressed"] is True and entry["button"] == "Allow", entry
        assert _calls["press"] == ["museapp-testcard"], _calls
        print("PASS: a trusted Partner takes the scan and the press; Xod's own walk stays idle", flush=True)

        # an already-pressed card is skipped by Xod's ladder; the Partner never hears about it
        _install(FakePartner(trusted=True))
        res = muse_app._scan_dispatch({"museapp-testcard"}, False)
        entry = res["approved"][0]
        assert entry["pressed"] is False and "already pressed" in entry["reason"], entry
        assert _calls["press"] == [], _calls
        print("PASS: the skip set is honored before any press is requested", flush=True)

        # Partner down: the whole scan falls back to Xod's walk, nothing half-delegated
        _install(FakePartner(down=True))
        res = muse_app._scan_dispatch(set(), False)
        assert _calls["own_scan"] == 1 and res.get("via") == "own", (_calls, res)
        assert muse_app._state["last_path"] == "own"
        print("PASS: with the Partner down, Xod's own walk takes the scan", flush=True)

        # Partner up but untrusted: same fallback (its hands cannot be used)
        _install(FakePartner(trusted=False))
        res = muse_app._scan_dispatch(set(), False)
        assert _calls["own_scan"] == 1 and res.get("via") == "own", (_calls, res)
        print("PASS: with the Partner untrusted, Xod's own walk takes the scan", flush=True)

        # Partner read fine, press refused mid-scan: NO fallback press by Xod's hands
        _install(FakePartner(trusted=True,
                             press_result={"pressed": False, "button": None,
                                           "reason": "", "trusted": False, "_status": 403}))
        res = muse_app._scan_dispatch(set(), False)
        entry = res["approved"][0]
        assert entry["pressed"] is False and "refused" in entry["reason"], entry
        assert _calls["own_scan"] == 0, _calls
        print("PASS: a mid-scan press refusal stays a refusal; no second pair of hands", flush=True)

        # the settings surface carries the partner block, with its own notice
        _install(FakePartner(trusted=False))
        partner_client._cache.update({"at": 0.0, "status": None})
        block = partner_client.summary()
        assert block["running"] is True and block["trusted"] is False, block
        assert block["notice"] and "Xod Partner" in block["notice"], block
        print("PASS: the partner block reports untrusted with the partner-specific notice", flush=True)
    finally:
        partner_client.status, partner_client.scan, partner_client.press, muse_app._scan = orig

    print("ALL PARTNER DELEGATION TESTS PASSED", flush=True)


if __name__ == "__main__":
    main()
