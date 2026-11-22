"""
The approver's REVIEW step (services/muse_review.py + the gate in services/muse_app.py):
a card is judged by a model before any hand moves. The brain is faked here —
no real model, no real Muse app, nothing is pressed. What is proven: the
mode switch (off = old behavior, decide = review + log + never press, act =
press only on approve), the strict decision validation (a malformed or failed
review is a "leave", never an approval), one review per showing of a card,
the audit rows, the pause behaving as decide-only, and the Partner's frozen
picker taking the connector action verbs while still refusing permanent
grants and Deny.
  .venv/bin/python tests/smoke/run_muse_review_test.py
"""
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
import asyncio
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-musereview-"))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


from app.services import muse_app, muse_review  # noqa: E402
from app.services.storage import set_setting  # noqa: E402

CARD = {"type": "gmail", "title": "Muse wants your approval",
        "what": "Tree wants to send a Gmail message with your follow-up nudge for reticle issue #2094.",
        "button": "Reply", "buttons": ["Reply", "Don't send"]}

calls = {"n": 0, "reply": {"decision": "approve", "reason": "The nudge matches the named issue follow-up."},
         "raise": False, "cards": []}


async def fake_brain(card):
    calls["n"] += 1
    calls["cards"].append(dict(card))
    if calls["raise"]:
        raise RuntimeError("no key configured")
    return dict(calls["reply"])


muse_review._review_fn = fake_brain
muse_review._shot_fn = lambda cid: {"path": f"/tmp/shots/{cid}.png", "error": None}


def fresh_cid():
    return f"card-{calls['n']}-{time.time_ns()}"


def gate(card=None):
    entry = dict(card or CARD)
    return muse_app._review_allows(entry, fresh_cid()), entry


def main():
    # ---- mode off: the old behavior exactly, the brain never consulted
    set_setting(muse_review.SETTING, "off")
    muse_app._state["reviews"].clear()
    ok, entry = gate()
    check("mode off: the gate allows without reviewing", ok and "review" not in entry and calls["n"] == 0)

    # ---- decide: reviewed, logged, never allowed to press
    set_setting(muse_review.SETTING, "decide")
    ok, entry = gate()
    check("decide: an approving review still does not allow a press", not ok)
    check("decide: the reason says decide-only and carries the judgment",
          entry["reason"].startswith("decide-only: review approve"), entry.get("reason", ""))
    check("decide: the review rode along on the entry",
          entry["review"]["decision"] == "approve" and entry["review"]["screenshot"].endswith(".png"))
    n_after_first = calls["n"]
    cid = fresh_cid()
    e1, e2 = dict(CARD), dict(CARD)
    muse_app._review_allows(e1, cid)
    muse_app._review_allows(e2, cid)
    check("one review per showing: a second scan of the same card reuses it", calls["n"] == n_after_first + 1)

    rows = [json.loads(l) for l in open(muse_review.audit_path()) if l.strip()]
    rev = [r for r in rows if r.get("phase") == "review"]
    check("audit: the review row landed with decision, reason, screenshot",
          bool(rev) and rev[-1]["decision"] == "approve" and rev[-1]["screenshot"].endswith(".png")
          and rev[-1]["mode"] == "decide")

    # ---- act: approve presses, deny and leave do not
    set_setting(muse_review.SETTING, "act")
    ok, entry = gate()
    check("act + approve: the gate allows the press", ok)
    calls["reply"] = {"decision": "deny", "reason": "Recipient does not match the named task."}
    ok, entry = gate()
    check("act + deny: refused, with the review's reason",
          not ok and entry["reason"] == "review deny: Recipient does not match the named task.")
    calls["reply"] = {"decision": "leave", "reason": "The card does not say who receives the code."}
    ok, entry = gate()
    check("act + leave: refused", not ok and entry["reason"].startswith("review leave:"))

    # ---- a review that did not happen is never an approval
    calls["raise"] = True
    ok, entry = gate()
    check("brain failure: refused as a leave, failure in the reason",
          not ok and entry["review"]["decision"] == "leave" and "review unavailable" in entry["review"]["reason"])
    calls["raise"] = False
    calls["reply"] = {"decision": "definitely", "reason": "trust me"}
    out = asyncio.run(muse_review.review(dict(CARD)))
    check("a decision outside the enum becomes a leave", out["decision"] == "leave")
    bad = muse_review.validate({"decision": "approve"})
    check("validate: a missing reason is malformed, not a decision", bad is None)
    good = muse_review.validate({"decision": "approve", "reason": "  matches   the task "})
    check("validate: a well-formed answer passes, reason tidied",
          good == {"decision": "approve", "reason": "matches the task"})

    # ---- the prompt carries the card and the owner's standing context
    prompt = muse_review._card_prompt(CARD)
    check("the review prompt carries the card text and the button Xod would press",
          "reticle issue #2094" in prompt and "Reply" in prompt)
    check("the system prompt judges as the owner's proxy with no category refusals",
          "owner's proxy" in muse_review._SYSTEM and "no category of" in muse_review._SYSTEM)

    # ---- the watcher's pause is decide-only even in act mode
    calls["reply"] = {"decision": "approve", "reason": "Matches the task."}
    muse_app.set_mode("pause", pause_minutes=5)
    try:
        ok, entry = gate()
        check("paused watcher: act mode still does not press", not ok and entry["reason"].startswith("decide-only:"))
    finally:
        muse_app.set_mode("resume")

    # ---- status surfaces the reviewer
    st = muse_review.status()
    check("review status: mode, stats, audit path",
          st["mode"] == "act" and st["stats"]["reviewed"] >= 5 and st["audit"].endswith("audit.jsonl"))
    wst = muse_app.status()
    check("watcher status carries the review section", bool(wst.get("review")) and wst["review"]["mode"] == "act")

    # ---- the Partner's frozen picker: action verbs in, permanent grants out
    spec = importlib.util.spec_from_file_location("partner_ax", ROOT / "partner" / "partner_ax.py")
    pax = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pax)
    check("partner picker: a Gmail card's Reply is the pick",
          pax.pick_button(["Reply", "Don't send"]) == "Reply")
    check("partner picker: a calendar card's Create is the pick",
          pax.pick_button(["Create", "Cancel"]) == "Create")
    check("partner picker: a lone Allow always is still never picked",
          pax.pick_button(["Allow always"]) is None)
    check("partner picker: scoped Always allow still wins under always-allow-v1",
          pax.pick_button(["Send", "Always allow for this recipient"]) == "Always allow for this recipient")
    check("partner picker: one-time-v1 skips the scoped Always allow",
          pax.pick_button(["Send", "Always allow for this recipient"], policy="one-time-v1") == "Send")
    check("partner picker: Pay and Sign are picks, their Don't twins are not",
          pax.pick_button(["Pay $25", "Don't pay"]) == "Pay $25" and pax.pick_button(["Sign", "Don't sign"]) == "Sign")
    check("partner picker: the card body text is never the pick",
          pax.pick_button(["Tree wants to send a Gmail reply with your nudge message. "
                           "cloudtheboy@gmail.com • Reticle #2094", "Reply", "Don't send"]) == "Reply")

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
