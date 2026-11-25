"""
The Muse app watcher as a QUEUE worker (services/muse_app.py, owner directive 2026-11-24):
the approvals panel stacks cards, and an expired card stays visible with no controls at all
(no Allow, no Deny, no dismiss — a Muse app bug). The watcher must enumerate the whole
queue, classify every card (actionable / expired / stuck / waiting), press the actionable
ones one at a time with a fresh enumeration after each press, step over expired and stuck
cards without ever blocking on them, and keep one card's failure from reaching the others.

Here the AX API and the Partner client are faked: no real Muse app, no real Partner,
nothing real is pressed.
  .venv/bin/python tests/smoke/run_muse_app_queue_test.py
"""
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
import asyncio
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-musequeue-"))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


class El:
    def __init__(self, role, label="", children=()):
        self.role, self.label, self.children = role, label, list(children)
        self.pressed = False
        self.presses = 0
        self.parent = None
        for c in self.children:
            c.parent = self


class FakeAX:
    """The slice of ApplicationServices muse_app uses, over El trees. With
    vanish=True a successful press removes the pressed card from the tree,
    the way the real panel drops an approved card."""

    def __init__(self, root, trusted=True, vanish=False):
        self.root, self._trusted, self.vanish = root, trusted, vanish
        self.walks = 0

    def trusted(self):
        return self._trusted

    def create_app(self, pid):
        self.walks += 1
        return self.root

    def attr(self, el, name):
        if name == "AXParent":
            return getattr(el, "parent", None)
        if name == "AXRole":
            return el.role
        if name in ("AXTitle", "AXDescription", "AXValue"):
            return el.label
        if name == "AXChildren":
            return el.children
        return None

    def perform(self, el, action):
        if action != "AXPress":
            return -1
        el.pressed, el.presses = True, el.presses + 1
        if self.vanish:
            card = el
            while card is not None and not (card.role == "AXGroup" and "approval request" in card.label.lower()):
                card = card.parent
            if card is not None and card.parent is not None:
                card.parent.children.remove(card)
        return 0


def panel(*cards):
    return El("AXApplication", "Muse", [El("AXWindow", "Muse", [El("AXGroup", "", list(cards))])])


def reset_state(muse_app):
    muse_app._state["press_marks"].clear()
    muse_app._state["pressed_ids"].clear()
    muse_app._state["fail_logged"].clear()
    muse_app._state["reviews"].clear()
    muse_app._state["present"] = set()
    muse_app._state["present_ids"] = set()
    muse_app._state["no_button_ids"] = set()
    muse_app._state["stuck_fronted"] = set()
    muse_app._state["fronted"] = set()
    muse_app._state["press_one"] = False
    muse_app._save_pending(set())


def main():
    from app.services import muse_app, partner_client
    from app.services.storage import set_setting
    set_setting(muse_app.SETTING, "on")
    muse_app.FRONT_SETTLE = 0.01
    fronts = []
    muse_app._bring_forward = lambda: fronts.append("forward") or 99
    muse_app._put_back = lambda prev: fronts.append(("back", prev))
    muse_app._muse_pid = lambda: 4242

    print("Q1 classification: actionable, expired, stuck, waiting")
    check("a card with a picked button is actionable",
          muse_app._classify({"button": "Allow", "reason": ""}) == "actionable")
    check("a card whose text says expired is expired, button or not",
          muse_app._classify({"button": "Allow", "reason": "expired before Xod got to it"}) == "expired"
          and muse_app._classify({"button": None, "reason": "", "expired": True}) == "expired")
    check("no button and no fronted look yet is waiting, not stuck",
          muse_app._classify({"button": None, "reason": "no button Xod may press"}) == "waiting")
    check("no button after a fronted look confirmed it is stuck",
          muse_app._classify({"button": None, "reason": "no button Xod may press"}, front_confirmed=True) == "stuck")

    print("Q2 (a) a queue of actionable cards: all pressed, one at a time, re-enumerated between presses")
    reset_state(muse_app)
    btns, cards = [], []
    for i in (1, 2, 3):
        b = El("AXButton", "Allow")
        btns.append(b)
        cards.append(El("AXGroup", f"Approval request for Browser", [
            El("AXStaticText", f"Allow Tree to upload queue-file-{i}.txt?"), b, El("AXButton", "Deny")]))
    ax = FakeAX(panel(*cards), vanish=True)
    muse_app._ax_api = lambda: ax
    out = asyncio.run(muse_app.scan_once())
    by_what = {c["what"]: c for c in out}
    check("all three cards were pressed exactly once",
          all(b.presses == 1 for b in btns) and all(c.get("pressed") for c in out), str(out)[:300])
    check("the pass re-enumerated between presses (a fresh walk per press, not one snapshot)",
          ax.walks >= 4, f"walks={ax.walks}")
    check("every card in the pass carries its queue class",
          all(c.get("queue_class") == "actionable" for c in out), str([c.get("queue_class") for c in out]))

    print("Q3 (b) expired and stuck cards ahead of actionable ones: stepped over, never blocking")
    reset_state(muse_app)
    fronts.clear()
    exp_allow = El("AXButton", "Allow")
    expired = El("AXGroup", "Approval request for Gmail", [
        El("AXStaticText", "This approval has expired. Allow Tree to send this email in Gmail?"), exp_allow])
    stuck = El("AXGroup", "Approval request for Gmail", [
        El("AXStaticText", "Allow Tree to send this email in Gmail?"),
        El("AXStaticText", "To: wrigleyville@movementgyms.com Subject: A check-in idea")])
    a1_btn, a2_btn = El("AXButton", "Reply"), El("AXButton", "Allow")
    a1 = El("AXGroup", "Approval request for Gmail", [
        El("AXStaticText", "Allow Tree to reply in Gmail? To: a@b.co"), a1_btn, El("AXButton", "Don't reply")])
    a2 = El("AXGroup", "Approval request for Browser", [
        El("AXStaticText", "Allow Tree to upload behind-stuck.txt?"), a2_btn])
    tree = panel(expired, stuck, a1, a2)
    ax3 = FakeAX(tree, vanish=True)
    muse_app._ax_api = lambda: ax3
    out1 = asyncio.run(muse_app.scan_once())
    cls1 = {c["what"][:30]: c.get("queue_class") for c in out1}
    check("both actionable cards behind the stuck pair were pressed in the first pass",
          a1_btn.presses == 1 and a2_btn.presses == 1, f"{a1_btn.presses} {a2_btn.presses}")
    check("the expired card was never pressed and is classified expired",
          exp_allow.presses == 0 and "expired" in str(cls1), str(cls1))
    check("the buttonless card is only 'waiting' before any fronted look",
          any(v == "waiting" for v in cls1.values()), str(cls1))
    out2 = asyncio.run(muse_app.scan_once())
    stuck_cls = [c.get("queue_class") for c in out2 if "wrigleyville" in c["what"]]
    check("after one fronted look the buttonless card is classified stuck",
          stuck_cls == ["stuck"] and len(fronts) == 2, f"{stuck_cls} fronts={fronts}")
    asyncio.run(muse_app.scan_once())
    check("a stuck card never earns a second fronted look",
          len(fronts) == 2, f"fronts={fronts}")
    late_btn = El("AXButton", "Allow")
    late = El("AXGroup", "Approval request for Browser", [
        El("AXStaticText", "Allow Tree to upload late-arrival.txt?"), late_btn])
    tree.children[0].children[0].children.append(late)
    late.parent = tree.children[0].children[0]
    out4 = asyncio.run(muse_app.scan_once())
    check("a new actionable card is still pressed while the stuck card sits in the panel",
          late_btn.presses == 1 and any(c.get("pressed") for c in out4), str(out4)[:200])

    print("Q4 (c) every card expired or stuck: nothing pressed, everything classified, no error")
    reset_state(muse_app)
    fronts.clear()
    e1 = El("AXGroup", "Approval request for Gmail", [
        El("AXStaticText", "This approval has expired. Allow Tree to send this email in Gmail?"), El("AXButton", "Allow")])
    e2 = El("AXGroup", "Approval request for Calendar", [
        El("AXStaticText", "This approval has expired. Allow Tree to create an event?"), El("AXButton", "Create")])
    s1 = El("AXGroup", "Approval request for Gmail", [
        El("AXStaticText", "Allow Tree to send this email in Gmail? To: neworleans@hiusa.org")])
    muse_app._ax_api = lambda: FakeAX(panel(e1, e2, s1), vanish=True)
    out = asyncio.run(muse_app.scan_once())
    classes = sorted(c.get("queue_class") or "" for c in out)
    check("no card was pressed and the pass returned all three, classified",
          len(out) == 3 and not any(c.get("pressed") for c in out)
          and classes == ["expired", "expired", "stuck"], f"{classes} {str(out)[:200]}")
    check("the expired cards' buttons were never touched",
          e1.children[1].presses == 0 and e2.children[1].presses == 0)

    print("Q5 (d) an empty queue: a clean no-op")
    reset_state(muse_app)
    muse_app._ax_api = lambda: FakeAX(panel())
    out = asyncio.run(muse_app.scan_once())
    check("scan_once over an empty panel returns no cards and raises nothing", out == [], str(out))
    check("a direct read of an empty panel is an empty queue",
          muse_app._scan(True) == {"running": True, "approved": []})

    print("Q6 one card's failure is isolated: the queue behind it still gets pressed (own walk)")
    reset_state(muse_app)
    bad_btn, good_btn = El("AXButton", "Allow"), El("AXButton", "Allow")
    bad = El("AXGroup", "Approval request for Browser", [
        El("AXStaticText", "Allow Tree to upload poison.txt?"), bad_btn])
    good = El("AXGroup", "Approval request for Browser", [
        El("AXStaticText", "Allow Tree to upload fine.txt?"), good_btn])
    muse_app._ax_api = lambda: FakeAX(panel(bad, good))
    real_review = muse_app._review_allows

    def poisoned(entry, cid):
        if "poison" in (entry.get("what") or ""):
            raise RuntimeError("review blew up")
        return real_review(entry, cid)
    muse_app._review_allows = poisoned
    try:
        got = muse_app._scan(True)["approved"]
    finally:
        muse_app._review_allows = real_review
    bad_e = next(c for c in got if "poison" in c["what"])
    good_e = next(c for c in got if "fine" in c["what"])
    check("the poisoned card carries its own failure and was not pressed",
          bad_e.get("press_error") is True and "press failed for this card" in bad_e["reason"]
          and bad_btn.presses == 0, str(bad_e)[:200])
    check("the card behind it was pressed anyway",
          good_e.get("pressed") is True and good_btn.presses == 1, str(good_e)[:200])

    print("Q7 one card's failure is isolated on the Partner path too")
    reset_state(muse_app)
    raw1 = {"card_id": "museapp-q7-one", "title": "Approval request for Gmail",
            "what": "From a@x.com To b@y.com Subject One", "buttons": ["Allow", "Deny"],
            "button": "Allow", "expired": False, "type": "Gmail",
            "from": "a@x.com", "to": "b@y.com", "subject": "One", "attachments": []}
    raw2 = {"card_id": "museapp-q7-two", "title": "Approval request for Gmail",
            "what": "From a@x.com To c@z.com Subject Two", "buttons": ["Reply", "Don't reply"],
            "button": "Reply", "expired": False, "type": "Gmail",
            "from": "a@x.com", "to": "c@z.com", "subject": "Two", "attachments": []}
    saved = (partner_client.status, partner_client.scan, partner_client.press)
    partner_client.status = lambda force=False: {"partner_trusted": True, "version": 1,
                                                  "bundle_id": "dev.xod.partner",
                                                  "approval_policy": "always-allow-v1"}
    partner_client.scan = lambda front=False: {"running": True, "trusted": True,
                                                "cards": [dict(raw1), dict(raw2)]}

    def flaky_press(card_id, front=False, away=False, button=None):
        if card_id == "museapp-q7-one":
            raise RuntimeError("partner press blew up")
        return {"pressed": True, "button": button, "reason": "", "trusted": True}
    partner_client.press = flaky_press
    try:
        res = muse_app._scan_dispatch(set(), False)
    finally:
        partner_client.status, partner_client.scan, partner_client.press = saved
    e1 = next(c for c in res["approved"] if c["_id"] == "museapp-q7-one")
    e2 = next(c for c in res["approved"] if c["_id"] == "museapp-q7-two")
    check("the failing card carries its own failure on the Partner path",
          e1.get("press_error") is True and "press failed for this card" in e1["reason"], str(e1)[:200])
    check("the card behind it was pressed with its own verb (Reply, read from the card)",
          e2.get("pressed") is True and e2.get("button") == "Reply", str(e2)[:200])

    print("Q8 the Partner queue pass: several cards, pressed in order, re-enumerated between presses")
    reset_state(muse_app)
    live = []
    for i, verb in ((1, "Allow"), (2, "Reply"), (3, "Create")):
        live.append({"card_id": f"museapp-q8-{i}", "title": "Approval request for Gmail",
                     "what": f"From a@x.com To u{i}@y.com Subject Q{i}", "buttons": [verb, "Deny"],
                     "button": verb, "expired": False, "type": "Gmail",
                     "from": "a@x.com", "to": f"u{i}@y.com", "subject": f"Q{i}", "attachments": []})
    scans, presses = [], []
    partner_client.status = lambda force=False: {"partner_trusted": True, "version": 1,
                                                  "bundle_id": "dev.xod.partner",
                                                  "approval_policy": "always-allow-v1"}
    partner_client.scan = lambda front=False: scans.append(1) or {"running": True, "trusted": True,
                                                                  "cards": [dict(c) for c in live]}

    def press_and_drop(card_id, front=False, away=False, button=None):
        presses.append((card_id, button))
        live[:] = [c for c in live if c["card_id"] != card_id]
        return {"pressed": True, "button": button, "reason": "", "trusted": True}
    partner_client.press = press_and_drop
    try:
        out = asyncio.run(muse_app.scan_once())
    finally:
        partner_client.status, partner_client.scan, partner_client.press = saved
    check("all three Partner cards pressed, in queue order, each with its own button",
          presses == [("museapp-q8-1", "Allow"), ("museapp-q8-2", "Reply"), ("museapp-q8-3", "Create")],
          str(presses))
    check("the pass re-enumerated through the Partner between presses",
          len(scans) >= 4, f"scans={len(scans)}")
    check("the merged pass result reports all three as pressed",
          len(out) == 3 and all(c.get("pressed") for c in out), str(out)[:200])

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
