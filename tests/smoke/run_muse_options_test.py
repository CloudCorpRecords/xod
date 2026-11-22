"""
Muse chat option widgets (services/muse_options.py): the dotted-outline choice
buttons an assistant message offers in Muse chat (muse.create_options). The fixture
case is the real screenshot, 2026-11-22: the 7-DMs question and its two options.
Policy (owner, 2026-11-22): understand the question and pick the MOST EXPANSIVE option.

The AX/UIA API is faked in the same shape as run_muse_chrome_test.py; the fake's
perform() counts calls and, when told to, removes the pressed widget from the tree so
the read-back path can be proven both ways.
  .venv/bin/python tests/smoke/run_muse_options_test.py
"""
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-museoptions-"))
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
        self.role, self.label = role, label
        self.children = list(children)
        self.parent = None
        for c in self.children:
            c.parent = self


class FakeAX:
    def __init__(self, root, remove_on_press=False):
        self.root, self.remove_on_press = root, remove_on_press
        self.perform_calls = 0
        self.pressed_labels = []

    def trusted(self):
        return True

    def create_app(self, pid):
        return self.root

    def attr(self, el, name):
        if name == "AXParent":
            return el.parent
        if name == "AXRole":
            return el.role
        if name in ("AXTitle", "AXDescription", "AXValue"):
            return el.label
        if name == "AXChildren":
            return el.children
        return None

    def perform(self, el, action):
        self.perform_calls += 1
        self.pressed_labels.append(el.label)
        if self.remove_on_press:
            # The tap sends the chosen reply: the widget leaves the chat flow.
            p = el.parent
            while p is not None and not any(c.role == "AXButton" for c in p.children):
                p = p.parent
            if p is not None and p.parent is not None:
                p.parent.children.remove(p)
        return 0


def widget(question, *options):
    return El("AXGroup", "", children=[
        El("AXStaticText", question),
        *[El("AXButton", o) for o in options]])


def chat(*children):
    return El("AXWindow", "Muse", children=[El("AXGroup", "", children=list(children))])


SCREENSHOT_Q = ("Want me to send those 7 queued DMs through the standing LinkedIn tab "
                "in your Mac main Chrome? That's the route that clears the guard.")
SCREENSHOT_YES = "Send the 7 queued DMs via my Mac main Chrome LinkedIn tab"
SCREENSHOT_NO = "Keep building the employer list first, no DMs yet"


def main():
    from app.services import muse_app, muse_options
    from app.services.storage import set_setting
    set_setting(muse_app.SETTING, "on")  # always mode: the policy gate is open

    print("O1 the screenshot widget: most expansive option picked and pressed")
    tree = chat(widget(SCREENSHOT_Q, SCREENSHOT_YES, SCREENSHOT_NO))
    ax = FakeAX(tree, remove_on_press=True)
    out = muse_options.scan_tree(ax, tree)
    check("one widget found, the send option picked over the no-DMs-yet option",
          len(out) == 1 and out[0]["picked"] == SCREENSHOT_YES, str(out))
    check("pressed once, and the why names both scores",
          ax.perform_calls == 1 and ax.pressed_labels == [SCREENSHOT_YES]
          and "most expansive" in out[0]["why"] and SCREENSHOT_NO in out[0]["why"], str(out[0]))
    check("read-back confirmed: the widget is gone after the press",
          out[0]["readback"].startswith("confirmed"), out[0]["readback"])

    print("O2 a widget that stays on screen is never pressed twice; read-back says unconfirmed")
    tree2 = chat(widget("Should I run the full gate and ship the release?",
                        "Run the full gate and ship it", "Wait and do nothing yet"))
    ax2 = FakeAX(tree2)
    first = muse_options.scan_tree(ax2, tree2)
    second = muse_options.scan_tree(ax2, tree2)
    check("first press lands, read-back honestly unconfirmed while it still shows",
          first[0]["pressed"] and first[0]["readback"].startswith("unconfirmed"), str(first))
    check("second scan of the same showing does not press again",
          ax2.perform_calls == 1 and "already pressed" in second[0]["reason"], str(second))

    print("O3 scope: the broader option beats the narrower one")
    pick = muse_options.pick_most_expansive(
        "How far should I take the applications?",
        ["Apply to this one job only", "Apply to all 50 matching jobs and send the follow ups"])
    check("all-50-and-follow-ups wins over one-job-only, with factors logged",
          pick["picked"] == "Apply to all 50 matching jobs and send the follow ups"
          and not pick["held_reason"] and "scope expander" in pick["why"], str(pick))

    print("O4 an approval card is not an option widget")
    card = El("AXGroup", "Approval request for Gmail", children=[
        El("AXStaticText", "Allow Tree to send this email?"),
        El("AXButton", "Allow"), El("AXButton", "Deny")])
    check("approval buttons are excluded, so the card yields no widget",
          muse_options.find_widgets(FakeAX(chat(card)), chat(card)) == [])
    nav = widget("What would you like to do?", "Copy", "Retry")
    check("composer/navigation buttons are not options either",
          muse_options.find_widgets(FakeAX(chat(nav)), chat(nav)) == [])

    print("O5 ambiguity holds: no clear most-expansive option, nothing pressed")
    tree5 = chat(widget("Which file should I open?", "Open the red file", "Open the blue file"))
    ax5 = FakeAX(tree5)
    out5 = muse_options.scan_tree(ax5, tree5)
    check("near-tied scores hold with the ambiguity reason, zero presses",
          len(out5) == 1 and out5[0]["decision"] == "held"
          and "ambiguous" in out5[0]["held_reason"] and ax5.perform_calls == 0, str(out5))

    print("O6 protected topics hold even when one option is expansive")
    tree6 = chat(widget("Want me to pay the invoice and save the card?",
                        "Pay it and save the credit card for next time", "Not yet"))
    ax6 = FakeAX(tree6)
    out6 = muse_options.scan_tree(ax6, tree6)
    check("payment widget held as protected, zero presses",
          out6[0]["decision"] == "held" and "protected topic" in out6[0]["held_reason"]
          and ax6.perform_calls == 0, str(out6))

    print("O7 the switch off holds a clean widget")
    set_setting(muse_app.SETTING, "off")
    tree7 = chat(widget("Should I send the report to the whole team?",
                        "Send the report to everyone", "Keep it in drafts for now"))
    ax7 = FakeAX(tree7)
    out7 = muse_options.scan_tree(ax7, tree7)
    check("approvals off: held with the reason, zero presses",
          out7[0]["decision"] == "held" and "off or paused" in out7[0]["reason"]
          and ax7.perform_calls == 0, str(out7))
    set_setting(muse_app.SETTING, "on")

    print("O8 dry run: decision is would_press, perform() never called")
    os.environ["XOD_MUSE_OPTIONS_DRY_RUN"] = "1"
    tree8 = chat(widget("Should I post the release notes everywhere?",
                        "Post the release notes on all socials", "Skip posting for now"))
    ax8 = FakeAX(tree8)
    out8 = muse_options.scan_tree(ax8, tree8)
    check("would_press with the expansive pick, zero perform calls",
          out8[0]["decision"] == "would_press" and out8[0]["picked"].startswith("Post the release")
          and ax8.perform_calls == 0, str(out8))
    del os.environ["XOD_MUSE_OPTIONS_DRY_RUN"]

    print("O9 the record: logged once with question, scored options and the why")
    from app.services import agent_log
    rows = [r for r in agent_log.approvals(100) if r["kind"] == "muse_options"
            and SCREENSHOT_Q[:40] in r["detail"]]
    check("one muse_options row for the screenshot widget, decision names the pick",
          len(rows) == 1 and SCREENSHOT_YES in rows[0]["decision"]
          and "why most expansive" in rows[0]["detail"]
          and "read-back: confirmed" in rows[0]["detail"], str(rows)[:400])
    st = muse_options.status()
    app_st = muse_app.status()
    check("status surfaces carry the chat_options target with its policy",
          st["target"] == "chat_options" and "most expansive" in st["policy"]
          and (app_st.get("chat_options") or {}).get("target") == "chat_options", str(st))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
