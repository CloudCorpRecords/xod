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
API reports. Also covered: the request-access ask (token-gated like every
route, answered while untrusted because that is the state it exists for,
the prompt option genuinely passed into the framework call at the seam,
the framework's answer surfaced verbatim, and a clean error, never a
crash, when the framework cannot be asked). Also covered: the
task-scoped approvals the Muse app puts on computer control cards
("Allow for this task" is picked and pressed; "Allow all web access
for this task" is picked over an unscoped "Allow always", which is
never picked), and the fronted retry: a press that fails on a stale
element (-25202) and a card the unfronted walk cannot see at all are
each retried once with Muse in front when the caller allows
fronting, and stay single-attempt clean failures when it does not.

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
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
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

    def __init__(self, root, trusted=True, access_result=False):
        self.root, self._trusted = root, trusted
        self.pressed = []
        self._access_result = access_result
        self.access_calls = 0

    def trusted(self):
        return self._trusted

    def request_access(self):
        self.access_calls += 1
        return self._access_result

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


def cc_tree(buttons, text="Allow Tree to proceed with this Mac?"):
    kids = [El("AXStaticText", text)] + [El("AXButton", b) for b in buttons]
    card = El("AXGroup", "Approval request for Computer control", kids)
    window = El("AXWindow", "", [card])
    return El("AXApplication", "", [window])


class FlakyAx(FakeAx):
    """perform() fails with -25202 (kAXErrorInvalidElement: the element
    went stale between the walk and the press) on the first call, then works."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.perform_calls = 0

    def perform(self, el, action):
        self.perform_calls += 1
        if self.perform_calls == 1:
            return -25202
        return super().perform(el, action)


class RevealAx(FakeAx):
    """The card is absent from the tree until reveal() (what bringing Muse
    to the front does for a card the unfronted walk cannot see at all)."""

    def __init__(self, empty_root, card_root, **kw):
        super().__init__(empty_root, **kw)
        self._card_root = card_root

    def reveal(self):
        self.root = self._card_root


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


def _seam_request_access():
    """The real CtypesAx request_access with only its output half faked: a
    stand-in prompt function receives the options dictionary the seam built
    and inspects it with real CoreFoundation calls, so the prompt option is
    proven to reach the framework call, not assumed. The dictionary the
    callback receives is released when request_access returns, so every
    observation is captured inside the callback."""
    import ctypes
    real = AX.CtypesAx()
    assert real.ok, "the real AX seam must load on this Mac"
    cf = real._cf
    cf.CFDictionaryGetCount.restype = ctypes.c_long
    cf.CFDictionaryGetCount.argtypes = [ctypes.c_void_p]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    asv = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    real_key = ctypes.c_void_p.in_dll(asv, "kAXTrustedCheckOptionPrompt")
    assert real_key, "the framework must export the prompt key constant"

    for answer in (False, True):
        ax = AX.CtypesAx.__new__(AX.CtypesAx)
        ax.ok, ax._strings, ax._cf = True, {}, cf
        ax._prompt_key = real_key
        seen = {}

        def fake_prompt(options, _answer=answer):
            # The framework looks the option up under its own exported
            # constant and the dictionary compares keys by pointer, so
            # look it up under that very constant: finding the value here
            # is exactly what the real call does inside
            # AXIsProcessTrustedWithOptions. (Before the fix the seam
            # keyed the dictionary with a same-text CFString; this lookup
            # then found nothing, which is the 2026-10-30 crash.)
            seen["count"] = cf.CFDictionaryGetCount(options)
            seen["value"] = cf.CFDictionaryGetValue(options, real_key)
            return _answer

        ax._prompt = fake_prompt
        result = ax.request_access()
        assert result is answer, (result, answer)
        assert seen["count"] == 1, seen
        assert seen["value"] == ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue").value, seen
    print("PASS: the seam passes the prompt option into the framework call and surfaces its answer verbatim", flush=True)

    # Without the real key constant the seam must refuse to ask at all:
    # asking with a same-text lookalike key is the 2026-10-30 crash.
    ax = AX.CtypesAx.__new__(AX.CtypesAx)
    ax.ok, ax._cf = True, cf
    calls = []
    ax._prompt = lambda options: calls.append(options) or True
    ax._prompt_key = None
    assert ax.request_access() is None
    assert calls == [], calls
    print("PASS: no real key constant, no ask: the seam answers None instead of risking the framework", flush=True)

    ax = AX.CtypesAx.__new__(AX.CtypesAx)
    ax.ok, ax._prompt = False, None
    assert ax.request_access() is None
    ax.ok, ax._prompt = True, None
    assert ax.request_access() is None
    print("PASS: an unavailable framework answers None from the seam, never a crash", flush=True)


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
            assert st["version"] == XP.VERSION == "1.2" and st["xod_state"] in ("up", "unknown"), st
            assert st["cap_spent"] == 0 and st["planned_revivals_last_hour"] == 0, st
            assert st["unplanned_revivals_last_hour"] == 0, st
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

    # request-access: token-gated, answered while untrusted (the state it
    # exists for), exactly one ask per call, the answer surfaced verbatim
    with tempfile.TemporaryDirectory() as tmp:
        ax = FakeAx(card_tree(["Allow", "Deny"]), trusted=False, access_result=False)
        walk = AX.Walk(ax, pid_of=lambda: 4242)
        cfg, core = _core(tmp, False)
        server, base = _serve(cfg, core, walk)
        try:
            code, _ = _call(base, "/ax/request-access", body={})
            assert code == 401 and ax.access_calls == 0, (code, ax.access_calls)
            code, _ = _call(base, "/ax/request-access", token="not-the-token", body={})
            assert code == 401 and ax.access_calls == 0, (code, ax.access_calls)
            code, out = _call(base, "/ax/request-access", token=core.token, body={})
            assert code == 200 and out == {"trusted": False, "prompted": True}, (code, out)
            assert ax.access_calls == 1, ax.access_calls
            print("PASS: /ax/request-access asks once, answers while untrusted, surfaces the answer", flush=True)
        finally:
            server.shutdown()

    # request-access when the framework's answer is yes: verbatim again
    with tempfile.TemporaryDirectory() as tmp:
        ax = FakeAx(card_tree(["Allow"]), trusted=True, access_result=True)
        walk = AX.Walk(ax, pid_of=lambda: 4242)
        cfg, core = _core(tmp, True)
        server, base = _serve(cfg, core, walk)
        try:
            code, out = _call(base, "/ax/request-access", token=core.token, body={})
            assert code == 200 and out == {"trusted": True, "prompted": True}, (code, out)
            assert ax.access_calls == 1, ax.access_calls
            print("PASS: /ax/request-access surfaces a yes verbatim too", flush=True)
        finally:
            server.shutdown()

    # request-access when the framework cannot be asked: a clean error,
    # never a crash, and the API keeps serving afterwards
    with tempfile.TemporaryDirectory() as tmp:
        ax = FakeAx(card_tree(["Allow"]), trusted=False, access_result=None)
        walk = AX.Walk(ax, pid_of=lambda: 4242)
        cfg, core = _core(tmp, False)
        server, base = _serve(cfg, core, walk)
        try:
            code, out = _call(base, "/ax/request-access", token=core.token, body={})
            assert code == 500 and "error" in out and out["prompted"] is False, (code, out)
            assert ax.access_calls == 1, ax.access_calls
            code, st = _call(base, "/status", token=core.token)
            assert code == 200, code
            print("PASS: an unaskable framework is a clean error and the API survives it", flush=True)
        finally:
            server.shutdown()

    # the pick rules, directly: task-scoped approvals are one-time approvals;
    # an unscoped "Allow always" is never one of them
    assert AX.pick_button(["Allow always"]) is None
    assert AX.pick_button(["Allow always", "Deny"]) is None
    assert AX.pick_button(["See details", "Allow for this task"]) == "Allow for this task"
    assert AX.pick_button(["Allow", "Allow for this task"]) == "Allow for this task"
    assert AX.pick_button(["Allow all web access for this task", "Allow"]) == "Allow"
    assert AX.pick_button(["Allow all web access for this task", "Allow always"]) == \
        "Allow all web access for this task"
    assert AX.pick_button(["Don't send", "Allow for this task"]) == "Allow for this task"
    print("PASS: pick_button takes the task-scoped approvals and never an unscoped Allow always", flush=True)

    # a computer control card whose only approval is "Allow for this task"
    # (the shape that timed out three times on 2026-11-01): picked and pressed
    with tempfile.TemporaryDirectory() as tmp:
        ax = FakeAx(cc_tree(["See details", "Allow for this task", "Deny"]))
        walk = AX.Walk(ax, pid_of=lambda: 4242)
        cfg, core = _core(tmp, True)
        server, base = _serve(cfg, core, walk)
        try:
            code, out = _call(base, "/ax/scan", token=core.token, body={})
            assert code == 200 and len(out["cards"]) == 1, out
            card = out["cards"][0]
            assert card["type"] == "Computer control", card
            assert card["button"] == "Allow for this task", card
            code, out = _call(base, "/ax/press", token=core.token, body={"card_id": card["card_id"]})
            assert code == 200 and out["pressed"] is True and out["button"] == "Allow for this task", out
            assert ax.pressed == ["Allow for this task"], ax.pressed
            print("PASS: a card offering only 'Allow for this task' is picked and pressed", flush=True)
        finally:
            server.shutdown()

    # the web-access variant: the task-scoped grant beats "Allow always"
    with tempfile.TemporaryDirectory() as tmp:
        ax = FakeAx(cc_tree(["See details", "Allow all web access for this task",
                             "Allow always", "Deny"]))
        walk = AX.Walk(ax, pid_of=lambda: 4242)
        cfg, core = _core(tmp, True)
        server, base = _serve(cfg, core, walk)
        try:
            code, out = _call(base, "/ax/scan", token=core.token, body={})
            card = out["cards"][0]
            assert card["button"] == "Allow all web access for this task", card
            code, out = _call(base, "/ax/press", token=core.token, body={"card_id": card["card_id"]})
            assert code == 200 and out["pressed"] is True, out
            assert ax.pressed == ["Allow all web access for this task"], ax.pressed
            print("PASS: 'Allow all web access for this task' beats 'Allow always' and is pressed", flush=True)
        finally:
            server.shutdown()

    # fronted retries through the real Walk: a press that fails on a stale
    # element and a card the unfronted walk cannot see each get exactly one
    # fronted retry when the caller allows fronting, and neither retries
    # when it does not. Fronting itself is faked at the module seam.
    real_frontmost, real_activate = AX._frontmost_pid, AX._activate
    holder = {}

    def fake_activate(pid):
        ax = holder.get("ax")
        if ax is not None and hasattr(ax, "reveal"):
            ax.reveal()

    try:
        AX._frontmost_pid = lambda: 999999
        AX._activate = fake_activate
        cid = AX.Walk(FakeAx(card_tree(["Allow", "Deny"])),
                      pid_of=lambda: 4242).scan()["cards"][0]["card_id"]

        ax = FlakyAx(card_tree(["Allow", "Deny"]))
        holder["ax"] = ax
        out = AX.Walk(ax, pid_of=lambda: 4242).press(cid, front=True)
        assert out["pressed"] is True and ax.pressed == ["Allow"], out
        assert ax.perform_calls == 2, ax.perform_calls
        print("PASS: a press failing on a stale element (-25202) is retried fronted and lands", flush=True)

        ax = FlakyAx(card_tree(["Allow", "Deny"]))
        holder["ax"] = ax
        out = AX.Walk(ax, pid_of=lambda: 4242).press(cid, front=False)
        assert out["pressed"] is False and "-25202" in out["reason"], out
        assert ax.perform_calls == 1 and ax.pressed == [], (ax.perform_calls, ax.pressed)
        print("PASS: without fronting, a stale-element press stays one attempt", flush=True)

        empty = El("AXApplication", "", [El("AXWindow", "", [])])
        ax = RevealAx(empty, card_tree(["Allow", "Deny"]))
        holder["ax"] = ax
        out = AX.Walk(ax, pid_of=lambda: 4242).press(cid, front=True)
        assert out["pressed"] is True and ax.pressed == ["Allow"], out
        print("PASS: a card the unfronted walk cannot see is pressed on the fronted retry", flush=True)

        ax = RevealAx(empty, card_tree(["Allow", "Deny"]))
        holder["ax"] = ax
        out = AX.Walk(ax, pid_of=lambda: 4242).press(cid, front=False)
        assert out["pressed"] is False and out["reason"] == "card not showing", out
        assert ax.pressed == [], ax.pressed
        print("PASS: without fronting, an unseen card stays a clean not-showing refusal", flush=True)
    finally:
        AX._frontmost_pid, AX._activate = real_frontmost, real_activate

    _seam_request_access()

    print("ALL PARTNER AX TESTS PASSED", flush=True)


if __name__ == "__main__":
    main()
