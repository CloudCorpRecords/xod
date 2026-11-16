"""Subprocess half of the approval journal test. Fake AX only, never a desktop."""
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.services import muse_app
from app.services.storage import set_setting
from tests.partner.run_partner_ax_test import FakeAx, card_tree, El

scenario, folder = sys.argv[1], Path(sys.argv[2])
set_setting(muse_app.SETTING, "off" if scenario == "switch-off" else "on")
labels = ["Always allow for this recipient", "Send", "Don't send"]
if scenario == "unscoped-card":
    labels = ["Always allow everything", "Deny"]
texts = ("From: sender@example.test", "To: approved@example.test", "Subject: Disposable approval")
if scenario == "expired-card":
    texts += ("This approval has expired.",)
tree = card_tree(labels, texts)
if scenario == "absence":
    tree = El("AXApplication", "", [El("AXWindow", "", [])])


class JournalAx(FakeAx):
    def perform(self, el, action):
        with (folder / "presses").open("a") as log:
            log.write(el.label + "\n")
        if scenario == "lost-response":
            raise RuntimeError("simulated restart after press before response")
        return super().perform(el, action)


muse_app._ax_api = lambda: JournalAx(tree)
muse_app._muse_pid = lambda: 4242
muse_app._partner_delegated = lambda: False
muse_app._partner_scan_press = lambda *args: None
muse_app._bring_forward = lambda: None
muse_app._put_back = lambda prev: None
try:
    cards = asyncio.run(muse_app.scan_once())
    pressed = any(card.get("pressed") for card in cards)
except RuntimeError:
    if scenario != "lost-response":
        raise
    pressed = False
print(json.dumps({"pressed": pressed, "pending": sorted(muse_app._pending_presses())}))
