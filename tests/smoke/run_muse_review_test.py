"""
The approver's RUBBER-STAMP policy (services/muse_review.py + the stamp in
services/muse_app.py), owner directive 2026-11-22: you approved Xod once, so
Xod presses every approval card it picks a button for. Judgment never blocks
a press — there is no model in the press path at all. What is proven: the
shipped default is act/rubber-stamp; a card a reviewer would deny, a card a
reviewer would leave, and a card on a machine whose model route is broken
are ALL still pressed; the stamp and audit rows land; one stamp per showing;
decide mode and the watcher pause still hold every press (the deliberate
holds); mode off is the pre-approver behavior exactly; and the Partner's
frozen picker still takes the connector action verbs while refusing
permanent grants and Deny.
  .venv/bin/python tests/smoke/run_muse_review_test.py
"""
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
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
# A card any reviewer would deny: the recipient contradicts the named task.
DENY_CARD = {"type": "gmail", "title": "Muse wants your approval",
             "what": "Send the signed contract for the venue deal to a different person than the task names.",
             "button": "Reply", "buttons": ["Reply", "Don't send"]}
# A card any reviewer would leave: it does not say enough to judge.
LEAVE_CARD = {"type": "browser", "title": "Approval request for Browser",
              "what": "Allow Tree to run this command? rm -rf /tmp/xod-test-tree",
              "button": "Allow", "buttons": ["Allow", "Deny"]}

muse_review._shot_fn = lambda cid: {"path": f"/tmp/shots/{cid}.png", "error": None}


def fresh_cid():
    return f"card-{time.time_ns()}"


def gate(card=None):
    entry = dict(card or CARD)
    return muse_app._review_allows(entry, fresh_cid()), entry


def audit_rows():
    p = muse_review.audit_path()
    if not p.exists():
        return []
    return [json.loads(l) for l in open(p) if l.strip()]


def main():
    # ---- the shipped default: no setting stored means act / rubber-stamp
    check("shipped default mode is act", muse_review.mode() == "act")
    check("the policy is rubberstamp", muse_review.POLICY == "rubberstamp"
          and muse_review.status()["policy"] == "rubberstamp")

    # ---- act: every card is pressed, whatever a review would have said
    set_setting(muse_review.SETTING, "act")
    ok, entry = gate()
    check("act: an ordinary card is allowed", ok)
    check("act: the stamp on the entry is the rubber-stamp approve",
          entry["review"]["decision"] == "approve" and entry["review"]["policy"] == "rubberstamp"
          and "rubber-stamp" in entry["review"]["reason"])
    ok, entry = gate(DENY_CARD)
    check("act: a card a reviewer would DENY is still pressed", ok
          and entry["review"]["decision"] == "approve")
    ok, entry = gate(LEAVE_CARD)
    check("act: a card a reviewer would LEAVE is still pressed", ok
          and entry["review"]["decision"] == "approve")

    # ---- review unavailable changes nothing: no model is even consulted.
    # Poison the model route at the module level: if anything in the press
    # path imported the llm service and called it, this would raise — and
    # the counter proves it was never called at all.
    import types
    calls = {"n": 0}

    async def poisoned(*a, **k):
        calls["n"] += 1
        raise RuntimeError("no key configured")

    fake_llm = types.ModuleType("app.services.llm")
    fake_llm.llm_service = types.SimpleNamespace(chat_completion=poisoned,
                                                 get_endpoint_info=lambda: {})
    fake_llm.parse_llm_json = lambda t: None
    real_llm = sys.modules.get("app.services.llm")
    sys.modules["app.services.llm"] = fake_llm
    try:
        ok, entry = gate()
    finally:
        if real_llm is not None:
            sys.modules["app.services.llm"] = real_llm
        else:
            sys.modules.pop("app.services.llm", None)
    check("act: with the model route broken the card is STILL pressed", ok)
    check("the press path never calls the model", calls["n"] == 0)

    # ---- one stamp per showing of a card
    before = muse_review.status()["stats"]["stamped"]
    cid = fresh_cid()
    e1, e2 = dict(CARD), dict(CARD)
    muse_app._review_allows(e1, cid)
    muse_app._review_allows(e2, cid)
    check("one stamp per showing: a second scan of the same card reuses it",
          muse_review.status()["stats"]["stamped"] == before + 1)

    # ---- the audit carries the stamp, the card, and the policy
    rows = [r for r in audit_rows() if r.get("phase") == "review"]
    check("audit: review rows land with decision, policy, reason, screenshot",
          bool(rows) and rows[-1]["decision"] == "approve" and rows[-1]["policy"] == "rubberstamp"
          and "rubber-stamp" in rows[-1]["reason"] and rows[-1]["screenshot"].endswith(".png")
          and rows[-1]["mode"] == "act")

    # ---- decide: the pause. Stamped and logged, never pressed.
    set_setting(muse_review.SETTING, "decide")
    ok, entry = gate()
    check("decide: the gate holds the press", not ok)
    check("decide: the reason says decide-only and carries the stamp",
          entry["reason"].startswith("decide-only: rubber-stamp would approve"), entry.get("reason", ""))
    check("decide: the stamp still rode along on the entry",
          entry["review"]["decision"] == "approve" and entry["review"]["screenshot"].endswith(".png"))

    # ---- the watcher's own pause holds every press even in act mode
    set_setting(muse_review.SETTING, "act")
    muse_app.set_mode("pause", pause_minutes=5)
    try:
        ok, entry = gate()
        check("paused watcher: act mode still does not press", not ok
              and entry["reason"].startswith("decide-only:"))
    finally:
        muse_app.set_mode("resume")

    # ---- mode off: the pre-approver behavior exactly, no stamp, no audit
    set_setting(muse_review.SETTING, "off")
    muse_app._state["reviews"].clear()
    n_rows = len(audit_rows())
    ok, entry = gate()
    check("mode off: the gate allows with no stamp taken",
          ok and "review" not in entry and len(audit_rows()) == n_rows)

    # ---- status surfaces the approver
    set_setting(muse_review.SETTING, "act")
    st = muse_review.status()
    check("approver status: mode, policy, stats, audit path",
          st["mode"] == "act" and st["policy"] == "rubberstamp"
          and st["stats"]["stamped"] >= 5 and st["audit"].endswith("audit.jsonl"))
    wst = muse_app.status()
    check("watcher status carries the approver section",
          bool(wst.get("review")) and wst["review"]["mode"] == "act")

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
