"""
Partner v1 AX service test: the contract Xod relies on.

The failure this guards against is a Partner that presses the wrong thing:
a Deny, a button on a card Xod did not name, anything at all while macOS
is not honoring its grant, or anything at all for a caller without the
pairing token. The checks drive the real HTTP API and the real tree walk
against a fake AX tree (the same shape Xod's own tests use), so no real
card is ever touched. Covered: token refusal, trust refusal, scan
shaping (card found, labels listed, the frozen pick), press choosing the
always-allow for a recipient over a plain Allow, Allow chosen when that
is what the card offers, Deny never pressed (a Deny-only card is a clean
refusal), no card is a clean refusal, and the watchdog status fields the
API reports.

Run: python3 tests/partner/run_partner_ax_test.py
"""
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "partner"))
import partner_ax as AX
import xod_partner as XP


# ------------------------------------------------------------------ a fake AX tree
class El:
    def __init__(self, role, label="", children=(), texts=()):
        self.role, self.label = role, label
        self.kids = list(children)
        self.parent = None
        for k in self.kids:
            k.parent = self


class FakeAx:
    """The surface partner_ax.Walk uses, over El nodes. perform() records."""

    def __init__(self, root, trusted=True):
        self.root, self._trusted = root, trusted
        self.pressed = []

    def trusted(self):
        return self._trusted

    def create_app(self, pid):
        return self.root

    def attr(self, el, name):
        if name == "AXRole":
            return el.role
        if name in ("AXTitle", "AXDescription"):
            return el.label or None
        if name == "AXChildren":
            return el.kids
        if name == "AXParent":
            return el.parent
        return None

    def perform(self, el, action):
        assert action == "AXPress", action
        self.pressed.append(el.label)
        return 0

    def set_attr(self, el, name, value):
        return 0


def card_tree(buttons, texts=("From: ada@example.com", "To: bob@example.com", "Subject: Hi")):
    kids = [El("AXStaticText", t) for t in texts] + [El("AXButton", b) for b in buttons]
    card = El("AXGroup", "Approval request for Gmail", kids)
    window = El("AXWindow", "", [card])
    return El("AXApplication", "", [window])


def _core(tmp, trust):
    cfg = dict(XP.DEFAULTS)
    cfg["api_port"] = 0  # ephemeral; read back from the server
    core = XP.Core(cfg, Path(tmp), lambda: True, lambda: True, lambda msg: None,
                   trust=lambda: trust)
    return cfg, core


def _serve(cfg, core, walk):
    server = XP.make_server(cfg, core, walk, lambda msg: None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def _call(base, path, token=None, body=None):
    req = urllib.request.Request(base + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def main():
    # no token and wrong token are refused everywhere, nothing is scanned or pressed
    with tempfile.TemporaryDirectory() as tmp:
        ax = FakeAx(card_tree(["Allow", "Deny"]))
        walk = AX.Walk(ax, pid_of=lambda: 4242)
        cfg, core = _core(tmp, True)
        server, base = _serve(cfg, core, walk)
        try:
            code, _ = _call(base, "/status")
            assert code == 401, code
            code, _ = _call(base, "/status", token="wrong")
            assert code == 401, code
            code, _ = _call(base, "/ax/scan", token="wrong", body={})
            assert code == 401, code
            code, _ = _call(base, "/ax/press", token="wrong", body={"card_id": "x"})
            assert code == 401, code
            assert ax.pressed == [], ax.pressed
            print("PASS: missing and wrong tokens are refused on every route", flush=True)

            # status carries the watchdog fields plus the v1 trust fields
            code, st = _call(base, "/status", token=core.token)
            assert code == 200 and st["bundle_id"] == "dev.xod.partner", st
            assert st["partner_trusted"] is True and st["ax"]["trusted"] is True, st
            assert st["version"] == 1 and st["xod_state"] in ("up", "unknown"), st
            print("PASS: /status reports bundle id, trust, and the watchdog summary", flush=True)

            # scan is read-only and shaped: card present, labels listed, frozen pick
            code, out = _call(base, "/ax/scan", token=core.token, body={})
            assert code == 200 and out["running"] is True and len(out["cards"]) == 1, out
            card = out["cards"][0]
            assert sorted(card["buttons"]) == ["Allow", "Deny"], card
            assert card["button"] == "Allow" and card["expired"] is False, card
            assert card["type"] == "Gmail" and card["to"] == "bob@example.com", card
            assert card["card_id"].startswith("museapp-"), card
            assert ax.pressed == [], ax.pressed
            print("PASS: /ax/scan reads the card, lists its buttons, picks Allow, presses nothing", flush=True)

            # press follows the scan: the named card's approving button, never Deny
            code, out = _call(base, "/ax/press", token=core.token,
                              body={"card_id": card["card_id"]})
            assert code == 200 and out["pressed"] is True and out["button"] == "Allow", out
            assert ax.pressed == ["Allow"], ax.pressed
            print("PASS: /ax/press presses the named card's Allow and nothing else", flush=True)

            # a card_id that is not showing is a clean refusal
            code, out = _call(base, "/ax/press", token=core.token,
                              body={"card_id": "museapp-doesnotexist"})
            assert code == 200 and out["pressed"] is False, out
            assert ax.pressed == ["Allow"], ax.pressed
            print("PASS: pressing a card that is not showing presses nothing", flush=True)
        finally:
            server.shutdown()

    # the always-allow for a recipient beats a plain Allow (the frozen pick order)
    with tempfile.TemporaryDirectory() as tmp:
        ax = FakeAx(card_tree(["Allow", "Always allow for this recipient", "Deny"]))
        walk = AX.Walk(ax, pid_of=lambda: 4242)
        cfg, core = _core(tmp, True)
        server, base = _serve(cfg, core, walk)
        try:
            code, out = _call(base, "/ax/scan", token=core.token, body={})
            card = out["cards"][0]
            assert card["button"] == "Always allow for this recipient", card
            code, out = _call(base, "/ax/press", token=core.token, body={"card_id": card["card_id"]})
            assert out["pressed"] is True and ax.pressed == ["Always allow for this recipient"], out
            print("PASS: press prefers Always allow for this recipient over Allow", flush=True)
        finally:
            server.shutdown()

    # a Deny-only card: scan reports it, press refuses, nothing is ever pressed
    with tempfile.TemporaryDirectory() as tmp:
        ax = FakeAx(card_tree(["Deny"]))
        walk = AX.Walk(ax, pid_of=lambda: 4242)
        cfg, core = _core(tmp, True)
        server, base = _serve(cfg, core, walk)
        try:
            code, out = _call(base, "/ax/scan", token=core.token, body={})
            card = out["cards"][0]
            assert card["button"] is None and card["buttons"] == ["Deny"], card
            code, out = _call(base, "/ax/press", token=core.token, body={"card_id": card["card_id"]})
            assert code == 200 and out["pressed"] is False, out
            assert "Deny" in out["reason"], out
            assert ax.pressed == [], ax.pressed
            print("PASS: a Deny-only card is never pressed", flush=True)
        finally:
            server.shutdown()

    # an untrusted Partner refuses scan and press outright, and says so
    with tempfile.TemporaryDirectory() as tmp:
        ax = FakeAx(card_tree(["Allow", "Deny"]), trusted=False)
        walk = AX.Walk(ax, pid_of=lambda: 4242)
        cfg, core = _core(tmp, False)
        server, base = _serve(cfg, core, walk)
        try:
            code, out = _call(base, "/ax/scan", token=core.token, body={})
            assert code == 403 and out["trusted"] is False, (code, out)
            code, out = _call(base, "/ax/press", token=core.token, body={"card_id": "x"})
            assert code == 403 and out["trusted"] is False, (code, out)
            assert ax.pressed == [], ax.pressed
            print("PASS: an untrusted Partner refuses scan and press and reports its trust state", flush=True)
        finally:
            server.shutdown()

    # no Muse app: scan and press both answer cleanly
    with tempfile.TemporaryDirectory() as tmp:
        ax = FakeAx(card_tree(["Allow"]))
        walk = AX.Walk(ax, pid_of=lambda: None)
        cfg, core = _core(tmp, True)
        server, base = _serve(cfg, core, walk)
        try:
            code, out = _call(base, "/ax/scan", token=core.token, body={})
            assert code == 200 and out["running"] is False and out["cards"] == [], out
            code, out = _call(base, "/ax/press", token=core.token, body={"card_id": "x"})
            assert code == 200 and out["pressed"] is False and out["running"] is False, out
            print("PASS: with Muse not running, scan and press answer cleanly", flush=True)
        finally:
            server.shutdown()

    print("ALL PARTNER AX TESTS PASSED", flush=True)


if __name__ == "__main__":
    main()
