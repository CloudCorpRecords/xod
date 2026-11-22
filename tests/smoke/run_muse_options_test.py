"""
Muse chat option widgets (services/muse_options.py): the dotted-outline choice
buttons an assistant message offers in Muse chat (muse.create_options). The fixture
case is the real screenshot, 2026-11-22: the 7-DMs question and its two options.
Policy (owner, 2026-11-22): the AI reviews the question and the options and clicks
the right one — "right" means the MOST EXPANSIVE option (authorizes the most work /
broadest scope), never a deferral when an action option exists. A failed or unclear
review HOLDS; there is no heuristic fallback guess. The heuristic scorer survives
only as log context, and one test below proves the model's pick beats it.

The model is faked through muse_options._review_fn (the same injection seam the
approver's reviewer used): each fake answers the strict verdict shape, raises, or
answers garbage, per test. The AX/UIA API is faked in the same shape as
run_muse_chrome_test.py; the fake's perform() counts calls and, when told to,
removes the pressed widget from the tree so the read-back path can be proven
both ways.
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

    def install_fake_model(fn):
        """fn(question, labels) -> (pick_index|None, reason); may raise. Returns a
        call counter so tests can prove whether the model was consulted at all."""
        calls = {"n": 0}

        async def fake(question, labels):
            calls["n"] += 1
            pick, reason = fn(question, labels)
            return {"pick_index": pick, "reason": reason, "model": "fake-model",
                    "provider": "fake", "latency_s": 0.01, "raw": "{}"}
        muse_options._review_fn = fake
        return calls

    def pick_label(substr, reason):
        def fn(question, labels):
            return next(i for i, l in enumerate(labels) if substr in l), reason
        return fn

    print("O1 the screenshot widget: the model's pick is pressed")
    install_fake_model(pick_label("Send the 7 queued DMs",
                                  "sending the DMs is the action asked about; the other defers it"))
    tree = chat(widget(SCREENSHOT_Q, SCREENSHOT_YES, SCREENSHOT_NO))
    ax = FakeAX(tree, remove_on_press=True)
    out = muse_options.scan_tree(ax, tree)
    check("one widget found, the model's send pick taken over the no-DMs-yet option",
          len(out) == 1 and out[0]["picked"] == SCREENSHOT_YES, str(out))
    check("pressed once, model reason and model name ride the entry",
          ax.perform_calls == 1 and ax.pressed_labels == [SCREENSHOT_YES]
          and out[0]["model"] == "fake-model"
          and "defers it" in out[0]["model_reason"], str(out[0]))
    check("read-back confirmed: the widget is gone after the press",
          out[0]["readback"].startswith("confirmed"), out[0]["readback"])

    print("O2 a widget that stays on screen is never pressed twice; read-back says unconfirmed")
    install_fake_model(pick_label("Run the full gate", "shipping is the action; waiting is a deferral"))
    tree2 = chat(widget("Should I run the full gate and ship the release?",
                        "Run the full gate and ship it", "Wait and do nothing yet"))
    ax2 = FakeAX(tree2)
    first = muse_options.scan_tree(ax2, tree2)
    second = muse_options.scan_tree(ax2, tree2)
    check("first press lands, read-back honestly unconfirmed while it still shows",
          first[0]["pressed"] and first[0]["readback"].startswith("unconfirmed"), str(first))
    check("second scan of the same showing does not press again",
          ax2.perform_calls == 1 and "already pressed" in second[0]["reason"], str(second))

    print("O3 the heuristic scorer still ranks for the log, and decides nothing")
    heur = muse_options.heuristic_pick(
        ["Apply to this one job only", "Apply to all 50 matching jobs and send the follow ups"])
    check("all-50-and-follow-ups tops the heuristic ranking, with factors logged",
          heur["picked"] == "Apply to all 50 matching jobs and send the follow ups"
          and "scope expander" in heur["why"]
          and heur["scores"][0]["score"] < heur["scores"][1]["score"], str(heur))

    print("O4 an approval card is not an option widget")
    card = El("AXGroup", "Approval request for Gmail", children=[
        El("AXStaticText", "Allow Tree to send this email?"),
        El("AXButton", "Allow"), El("AXButton", "Deny")])
    check("approval buttons are excluded, so the card yields no widget",
          muse_options.find_widgets(FakeAX(chat(card)), chat(card)) == [])
    nav = widget("What would you like to do?", "Copy", "Retry")
    check("composer/navigation buttons are not options either",
          muse_options.find_widgets(FakeAX(chat(nav)), chat(nav)) == [])

    print("O5 the model returning no clear pick holds: nothing pressed, no guess")
    install_fake_model(lambda q, labels: (None, "the two files do not differ in scope"))
    tree5 = chat(widget("Which file should I open?", "Open the red file", "Open the blue file"))
    ax5 = FakeAX(tree5)
    out5 = muse_options.scan_tree(ax5, tree5)
    check("held with the model's no-pick reason, zero presses",
          len(out5) == 1 and out5[0]["decision"] == "held"
          and "no clear pick" in out5[0]["held_reason"]
          and "do not differ in scope" in out5[0]["held_reason"]
          and ax5.perform_calls == 0, str(out5))

    print("O6 protected topics are the model's call now: no blanket hold")
    install_fake_model(pick_label("Pay it and save", "the invoice is due; paying is the action asked about"))
    tree6 = chat(widget("Want me to pay the invoice and save the card?",
                        "Pay it and save the credit card for next time", "Not yet"))
    ax6 = FakeAX(tree6)
    out6 = muse_options.scan_tree(ax6, tree6)
    check("payment widget decided by the model and pressed, reason logged",
          out6[0]["pressed"] and ax6.pressed_labels == ["Pay it and save the credit card for next time"]
          and "invoice is due" in out6[0]["model_reason"]
          and "protected" not in out6[0]["held_reason"], str(out6))

    print("O7 the switch off holds a clean widget, and the model is never consulted")
    calls7 = install_fake_model(pick_label("Send the report", "unused"))
    set_setting(muse_app.SETTING, "off")
    tree7 = chat(widget("Should I send the report to the whole team?",
                        "Send the report to everyone", "Keep it in drafts for now"))
    ax7 = FakeAX(tree7)
    out7 = muse_options.scan_tree(ax7, tree7)
    check("approvals off: held with the reason, zero presses, zero model calls",
          out7[0]["decision"] == "held" and "off or paused" in out7[0]["reason"]
          and ax7.perform_calls == 0 and calls7["n"] == 0, str(out7))
    set_setting(muse_app.SETTING, "on")

    print("O8 dry run: the review runs, decision is would_press, perform() never called")
    install_fake_model(pick_label("Post the release notes",
                                  "posting everywhere is the broadest option"))
    os.environ["XOD_MUSE_OPTIONS_DRY_RUN"] = "1"
    tree8 = chat(widget("Should I post the release notes everywhere?",
                        "Post the release notes on all socials", "Skip posting for now"))
    ax8 = FakeAX(tree8)
    out8 = muse_options.scan_tree(ax8, tree8)
    check("would_press with the model's pick, zero perform calls",
          out8[0]["decision"] == "would_press" and out8[0]["picked"].startswith("Post the release")
          and out8[0]["model_reason"] == "posting everywhere is the broadest option"
          and ax8.perform_calls == 0, str(out8))
    del os.environ["XOD_MUSE_OPTIONS_DRY_RUN"]

    print("O9 the record: logged once with question, scored options and the model's reason")
    from app.services import agent_log
    rows = [r for r in agent_log.approvals(100) if r["kind"] == "muse_options"
            and SCREENSHOT_Q[:40] in r["detail"]]
    check("one muse_options row for the screenshot widget, decision names the pick",
          len(rows) == 1 and SCREENSHOT_YES in rows[0]["decision"]
          and "picked by AI review" in rows[0]["decision"]
          and "model reason: sending the DMs is the action asked about" in rows[0]["detail"]
          and "model: fake-model" in rows[0]["detail"]
          and "option [" in rows[0]["detail"]
          and "read-back: confirmed" in rows[0]["detail"], str(rows)[:400])
    st = muse_options.status()
    app_st = muse_app.status()
    check("status surfaces carry the chat_options target, its policy and review counts",
          st["target"] == "chat_options" and "most expansive" in st["policy"]
          and st["reviews"]["picked"] >= 1
          and (app_st.get("chat_options") or {}).get("target") == "chat_options", str(st))

    print("O10 a failed or garbage review holds: never a press, never a heuristic guess")
    def boom(question, labels):
        raise RuntimeError("model route down")
    install_fake_model(boom)
    tree10 = chat(widget("Should I email the whole list the update?",
                         "Email all 900 subscribers the update", "Hold off for now"))
    ax10 = FakeAX(tree10)
    out10 = muse_options.scan_tree(ax10, tree10)
    check("model exception: held as review unavailable, zero presses despite a huge heuristic top",
          out10[0]["decision"] == "held" and "review unavailable" in out10[0]["held_reason"]
          and "model route down" in out10[0]["held_reason"]
          and ax10.perform_calls == 0, str(out10))
    install_fake_model(lambda q, labels: (99, "out of range on purpose"))
    tree10b = chat(widget("Should I merge the branch?", "Merge it now", "Leave it open"))
    ax10b = FakeAX(tree10b)
    out10b = muse_options.scan_tree(ax10b, tree10b)
    check("out-of-range verdict: held as review unavailable, zero presses",
          out10b[0]["decision"] == "held" and "review unavailable" in out10b[0]["held_reason"]
          and ax10b.perform_calls == 0, str(out10b))

    print("O11 the model's pick beats the heuristic, and the prompt carries the policy")
    heur11 = muse_options.heuristic_pick(
        ["Send all 100 emails now and share everything", "Send the 3 queued emails to the client"])
    install_fake_model(lambda q, labels: (1, "the task named 3 queued emails; the blast is not the ask"))
    tree11 = chat(widget("The follow-ups are staged. How should I send them?",
                         "Send all 100 emails now and share everything",
                         "Send the 3 queued emails to the client"))
    ax11 = FakeAX(tree11)
    out11 = muse_options.scan_tree(ax11, tree11)
    check("heuristic tops the blast, the model picks the scoped send, the scoped send is pressed",
          heur11["picked"].startswith("Send all 100")
          and out11[0]["picked"] == "Send the 3 queued emails to the client"
          and ax11.pressed_labels == ["Send the 3 queued emails to the client"], str(out11))
    check("the review prompt states most-expansive and never-deferral, options numbered",
          "broadest scope" in muse_options._SYSTEM and "deferral" in muse_options._SYSTEM
          and "0. Send all 100" in muse_options._widget_prompt("Q?", ["Send all 100 emails now and share everything",
                                                                      "Send the 3 queued emails to the client"]),
          muse_options._SYSTEM[:80])

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
