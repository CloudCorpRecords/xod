"""
The Chrome extension approval watcher (services/muse_chrome.py): the Muse extension's side
panel and Chrome's permission bubbles, read through the Windows UIA tree. The probe
(~/workspace/xod-eval/CHROME-EXTENSION-APPROVAL-PROBE-2026-11-20.md) proved both surfaces
live in that tree with Invoke-capable buttons.

This build is DRY RUN ONLY: the watcher must find cards, apply the auto-approve policy, log
what it would press, and never press anything. Here the UIA API is faked with a small element
tree in the AX shape muse_backends.WinAX hands the core: no real Chrome, and the fake's
perform() counts its calls so the suite can prove it stayed at zero.
  .venv/bin/python tests/smoke/run_muse_chrome_test.py
"""
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-musechrome-"))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


class El:
    def __init__(self, role, label="", children=(), cls="", rect=None):
        self.role, self.label, self.cls, self.rect = role, label, cls, rect
        self.children = list(children)
        self.parent = None
        for c in self.children:
            c.parent = self


class FakeAX:
    """The attr surface muse_chrome walks, over El trees. create_app serves queued roots
    (the warm-up test needs a cold tree first, then the warm one)."""

    def __init__(self, roots):
        self.roots = list(roots)
        self.perform_calls = 0

    def trusted(self):
        return True

    def create_app(self, pid):
        return self.roots.pop(0) if len(self.roots) > 1 else self.roots[0]

    def attr(self, el, name):
        if name == "AXParent":
            return el.parent
        if name == "AXRole":
            return el.role
        if name in ("AXTitle", "AXDescription", "AXValue"):
            return el.label
        if name == "AXChildren":
            return el.children
        if name == "UIAClassName":
            return el.cls
        if name == "UIABoundingRectangle":
            return el.rect
        return None

    def perform(self, el, action):
        self.perform_calls += 1
        return 0


def card_group(text, *extra_texts):
    return El("AXGroup", "Approval request for Browser", [
        El("AXStaticText", text), *[El("AXStaticText", t) for t in extra_texts],
        El("AXButton", "Allow"), El("AXButton", "Deny")])


def panel_with(*panel_children):
    return El("AXWindow", "", cls="SidePanel", children=[
        El("AXDocument", "Muse", children=list(panel_children))])


def chrome_window(*window_children):
    return El("AXWindow", "A page - Google Chrome", children=list(window_children))


def main():
    from app.services import muse_app, muse_chrome
    from app.services.storage import set_setting
    set_setting(muse_app.SETTING, "on")  # mode: always, so the policy gate is open

    print("C1 a card in the side panel: found, policy applied, would press, nothing pressed")
    allow_card = card_group("Allow Tree to upload report.pdf using Browser?")
    page_fake = card_group("Allow Tree to upload report.pdf using Browser?")
    tree = chrome_window(
        El("AXDocument", "Some Page", children=[page_fake]),
        panel_with(allow_card))
    ax = FakeAX([tree])
    out = muse_chrome.scan(ax, pid=777)
    check("the panel card is found and the identical page card is not",
          len(out["cards"]) == 1 and out["cards"][0]["decision"] == "would_press"
          and out["cards"][0]["button"] == "Allow", str(out["cards"]))
    check("dry run: perform() was never called", ax.perform_calls == 0)
    check("state counts the would-press", muse_chrome._state["would_press"] == 1
          and muse_chrome._state["cards_seen"] == 1, str(muse_chrome._state))

    print("C2 the record: logged once in the shared approvals history, as a dry run")
    from app.services import agent_log
    rows = [r for r in agent_log.approvals(50) if r["kind"] == "muse_chrome"]
    check("one muse_chrome row, decision says would press (dry run)",
          len(rows) == 1 and rows[0]["decision"] == "would press (dry run): Allow"
          and "windows UIA tree (dry run)" in rows[0]["detail"], str(rows)[:300])
    muse_chrome.scan(ax, pid=777)
    rows = [r for r in agent_log.approvals(50) if r["kind"] == "muse_chrome"]
    check("the same card again is not logged twice", len(rows) == 1, str(len(rows)))

    print("C3 a card only in page content (warm tree, no panel): invisible to the watcher")
    filler = [El("AXStaticText", f"page text {i}") for i in range(60)]
    page_only = chrome_window(El("AXDocument", "Some Page", children=[
        card_group("Allow Tree to upload report.pdf using Browser?"), *filler]))
    ax3 = FakeAX([page_only])
    out3 = muse_chrome.scan(ax3, pid=777)
    check("no panel, warm tree: no cards, not cold, nothing evaluated",
          out3["cards"] == [] and out3["cold"] is False and ax3.perform_calls == 0, str(out3))

    print("C4 safety-flagged and protected cards are held, with the reason")
    danger = card_group("Allow Tree to run this command?", "rm -rf /tmp/xod-test-tree")
    out4 = muse_chrome.scan(FakeAX([chrome_window(panel_with(danger))]), pid=777)
    check("a card whose text trips the safety check is held",
          len(out4["cards"]) == 1 and out4["cards"][0]["decision"] == "held"
          and "safety check" in out4["cards"][0]["reason"], str(out4["cards"]))
    secret = card_group("Allow Tree to enter your password for example.com?")
    out5 = muse_chrome.scan(FakeAX([chrome_window(panel_with(secret))]), pid=777)
    check("a protected action (a password) is held for the person",
          len(out5["cards"]) == 1 and out5["cards"][0]["decision"] == "held"
          and "protected action" in out5["cards"][0]["reason"], str(out5["cards"]))

    print("C5 the switch off: a clean card is held, not would-pressed")
    set_setting(muse_app.SETTING, "off")
    clean = card_group("Allow Tree to upload notes.txt using Browser?")
    out6 = muse_chrome.scan(FakeAX([chrome_window(panel_with(clean))]), pid=777)
    check("approvals off: held with the reason",
          out6["cards"][0]["decision"] == "held" and "off or paused" in out6["cards"][0]["reason"],
          str(out6["cards"]))
    set_setting(muse_app.SETTING, "on")

    print("C6 a permission bubble: parsed, deduped, held by the empty allowlist, logged")
    bubble_pane = El("AXGroup", "", cls="PermissionPromptBubbleBaseView", rect=(300, 100, 634, 270),
                     children=[El("AXStaticText", "Show notifications")])
    bubble_pane_dup = El("AXGroup", "", cls="PermissionPromptBubbleBaseView", rect=(300, 100, 634, 270),
                         children=[El("AXStaticText", "Show notifications")])
    dialog = El("AXGroup", "https://example.com wants to: Show notifications", children=[
        bubble_pane, bubble_pane_dup,
        El("AXGroup", "", children=[El("AXButton", "Allow"), El("AXButton", "Block")])])
    ax6 = FakeAX([chrome_window(dialog)])
    out7 = muse_chrome.scan(ax6, pid=777)
    check("the doubled bubble subtree is one bubble, origin and permission parsed",
          len(out7["bubbles"]) == 1 and out7["bubbles"][0]["origin"] == "https://example.com"
          and out7["bubbles"][0]["permission"] == "Show notifications", str(out7["bubbles"]))
    check("not on the allowlist: held, because Allow would be a durable grant",
          out7["bubbles"][0]["decision"] == "held" and "allowlist" in out7["bubbles"][0]["reason"]
          and ax6.perform_calls == 0, str(out7["bubbles"]))
    rows = [r for r in agent_log.approvals(50) if r["kind"] == "muse_chrome" and "origin: https://example.com" in r["detail"]]
    check("the bubble is in the history with its origin", len(rows) == 1, str(rows)[:200])

    print("C7 an allowlisted origin changes the bubble decision (still never pressed)")
    set_setting(muse_chrome.ALLOWLIST_SETTING, '["https://allowed.example"]')
    dialog2 = El("AXGroup", "https://allowed.example wants to: Show notifications", children=[
        El("AXGroup", "", cls="PermissionPromptBubbleBaseView", rect=(10, 10, 300, 200),
           children=[El("AXStaticText", "Show notifications")]),
        El("AXGroup", "", children=[El("AXButton", "Allow"), El("AXButton", "Block")])])
    ax7 = FakeAX([chrome_window(dialog2)])
    out8 = muse_chrome.scan(ax7, pid=777)
    check("allowlisted: would press, and perform() still never called",
          out8["bubbles"][0]["decision"] == "would_press" and ax7.perform_calls == 0, str(out8["bubbles"]))
    set_setting(muse_chrome.ALLOWLIST_SETTING, "[]")

    print("C8 cold tree: the first walk sees an empty shell, the retry finds the panel")
    cold = chrome_window(El("AXGroup", "", children=[]))
    warm = chrome_window(panel_with(card_group("Allow Tree to upload warmup.txt using Browser?")))
    ax8 = FakeAX([cold, warm])
    first = muse_chrome.scan(ax8, pid=777)
    second = muse_chrome.scan(ax8, pid=777)
    check("cold walk reports cold and no cards; the warm retry finds the card",
          first["cold"] is True and first["cards"] == [] and len(second["cards"]) == 1
          and second["cards"][0]["decision"] == "would_press", f"{first} / {second['cards']}")

    print("C9 the signed-out panel is reported, and the status surface says dry run")
    signed_out_panel = panel_with(El("AXStaticText", "Sign in to Muse to chat here, beside any page."),
                                  El("AXButton", "Sign in"))
    out9 = muse_chrome.scan(FakeAX([chrome_window(signed_out_panel)]), pid=777)
    st = muse_chrome.status()
    check("signed out: no cards, status flags it",
          out9["cards"] == [] and out9["signed_out"] is True and st["signed_out"] is True, str(out9))
    app_st = muse_app.status()
    check("the auto-approve status carries the chrome_extension target, labeled dry run",
          app_st.get("chrome_extension", {}).get("dry_run") is True
          and app_st["chrome_extension"]["target"] == "chrome_extension"
          and st["dry_run"] is True and "dry run" in st["label"].lower(), str(st))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
