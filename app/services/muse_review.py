"""
The approver's policy step (services/muse_app.py presses through here).

POLICY (owner directive, 2026-11-22): RUBBER-STAMP. Every approval card the
watcher picks a button for is approved and pressed, regardless of what any
review would say. Judgment never blocks a press.

This module began the same day as a model-review gate: each card was read
and judged by Xod's own configured model, and only an "approve" verdict let
a press through, with any review failure landing on "leave". The first real
card the loop ever judged came back "leave", and the owner's decision was
that the gate itself was the bug: the fleet's cards are the consent prompts
of work the owner ordered, and Xod's job is to press them, not to second-
guess them. So the model call is GONE from the press path entirely — no
latency, no key availability, no malformed answer can ever gate, delay or
veto a press again. What the step still does:

  - stamps each showing of a card with the rubber-stamp verdict
    ({"decision": "approve", "policy": "rubberstamp", ...}), once per
    showing (the watcher caches it), and writes it to the audit file;
  - captures a best-effort screenshot reference for the audit row
    (a failed capture is recorded as such, never faked);
  - the outcome half of the audit (what was pressed, and whether the
    panel re-read showed the card gone) is written by the watcher's
    _review_outcome, unchanged.

The mode is a setting (muse_review_mode), read live like the watcher's own
switch:
  act    - the shipped default: rubber-stamp; every card is pressed.
  decide - the pause: cards are stamped and logged, nothing is pressed.
           The watcher's own pause also holds every press.
  off    - the step is never consulted; the watcher behaves exactly as it
           did before the approver existed (no stamps, no audit rows).

Every stamp and every acted outcome is appended to the audit file
(DATA_DIR/muse_approver/audit.jsonl), one JSON object per line.
"""
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("xod.muse_review")

SETTING = "muse_review_mode"
MODES = ("off", "decide", "act")
POLICY = "rubberstamp"
STAMP_REASON = ("rubber-stamp: approve-all policy (owner directive 2026-11-22) — "
                "every card is pressed; judgment never blocks a press")

_state: Dict[str, Any] = {"stats": {"stamped": 0}, "last": None}
_shot_fn = None      # tests inject a fake capture here


# ------------------------------------------------------------------ the switch
def mode() -> str:
    """The approver's mode. 'act' (rubber-stamp) is the shipped default:
    a machine with no setting stored presses every card. 'decide' is the
    pause. Read live by the watcher, so a flip takes effect on the next
    card without a restart."""
    from app.services.storage import get_setting
    v = str(get_setting(SETTING, "act") or "act").lower()
    return v if v in MODES else "act"


def set_mode(new: str) -> str:
    from app.services.storage import set_setting
    new = str(new).lower()
    if new not in MODES:
        raise ValueError(f"review mode can be off, decide or act, not '{new}'")
    set_setting(SETTING, new)
    logger.info(f"muse approver mode → {new}")
    return new


def enabled() -> bool:
    return mode() != "off"


def bind_loop(loop) -> None:
    """Retained for the watcher's startup call. The rubber-stamp verdict
    is synchronous and local — no event loop, no model, nothing to bind."""


# ------------------------------------------------------------------ paths
def _dir() -> Path:
    from app.paths import DATA_DIR
    d = DATA_DIR / "muse_approver"
    d.mkdir(parents=True, exist_ok=True)
    return d


def audit_path() -> Path:
    return _dir() / "audit.jsonl"


def _shots_dir() -> Path:
    d = _dir() / "shots"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ audit
def audit(record: Dict[str, Any]) -> None:
    """One JSON line per event in the approver audit file. A failed write
    is logged and swallowed: the audit must never break the watcher."""
    rec = {"ts": time.time(), **record}
    try:
        with open(audit_path(), "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception as e:
        logger.warning(f"approver audit write failed: {e}")


# ------------------------------------------------------------------ perceive
def capture(card_id: str) -> Dict[str, Any]:
    """Best-effort screenshot of the screen as the card is stamped, saved
    under the audit dir. Returns {"path": str|None, "error": str|None} —
    a missing screenshot is a recorded fact, not a silent gap."""
    if _shot_fn is not None:
        return _shot_fn(card_id)
    try:
        from app.services import desktop
        img, _scale = desktop._screenshot()
        path = _shots_dir() / f"{card_id}.png"
        img.save(str(path))
        return {"path": str(path), "error": None}
    except Exception as e:
        return {"path": None, "error": str(e)[:200]}


# ------------------------------------------------------------------ the stamp
def verdict(card: Dict[str, Any]) -> Dict[str, Any]:
    """The rubber-stamp verdict for one showing of a card. Always approve,
    issued locally: no model is consulted, so nothing about the card —
    and nothing about the machine's keys, network or model — can change
    the answer. Kept under the review-era shape (decision/reason/model/
    latency) so the watcher's audit rows and history entries read the
    same as before, with the policy named on every row."""
    out = {"decision": "approve", "reason": STAMP_REASON, "policy": POLICY,
           "model": "", "provider": "", "latency_s": 0.0}
    _state["stats"]["stamped"] += 1
    _state["last"] = {"decision": "approve", "reason": STAMP_REASON, "policy": POLICY,
                      "model": "", "at": time.time(), "type": card.get("type") or "Muse"}
    logger.info(f"muse approver: rubber-stamp approve ({card.get('type') or 'Muse'} card)")
    return out


def verdict_sync(card: Dict[str, Any], timeout: float = 0.0) -> Dict[str, Any]:
    """The stamp for the watcher's press paths. Synchronous by design;
    the timeout argument is accepted for call-shape compatibility and
    ignored — a stamp cannot time out."""
    return verdict(card)


def status() -> Dict[str, Any]:
    return {"mode": mode(), "policy": POLICY, "stats": dict(_state["stats"]),
            "last": _state["last"], "audit": str(audit_path())}
