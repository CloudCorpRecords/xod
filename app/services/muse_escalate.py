"""
Escalate-to-Tree: the fallback half of the approver (owner directive,
2026-11-22). Xod presses what it can (approval cards are a rubber stamp,
services/muse_review.py; chat option widgets get an AI review,
services/muse_options.py). What Xod cannot itself resolve must not wait
on the owner: it escalates to Tree, the family's Muse, and Tree resolves
it on the owner's Muse credits. Nothing in this module ever asks the
owner anything.

ESCALATION EVENTS (one per showing of a widget or card, never more):
  option_widget_held        an option widget in the Muse app whose AI
                            review failed (review unavailable) or returned
                            no clear pick, so Xod held it unanswered.
  option_widget_unpressable an option widget Xod reviewed and picked, but
                            the press itself failed (AXPress error): Xod
                            can see it and knows the answer, its hands
                            cannot land it.
  card_press_unverified     an approval card that was stamped and pressed,
                            but the panel re-read (or the watcher's retry
                            cap) shows the card still there: the press may
                            not have taken effect.
  chrome_card_unpressable   BEST EFFORT, Chrome extension surface: the
                            muse_chrome watcher is dry-run by design, so
                            every card it decides it WOULD press is one
                            it can see but cannot press. Those escalate;
                            cards it holds under its own policy do not
                            (see the Part 6 record for the named gaps,
                            including option widgets inside Chrome web
                            pages, which no Xod watcher can see at all).

NOT escalations: the auto-approve switch off, paused, or away-mode with
the owner present (that is the owner's own intent, not a failure), a
decide-mode stamp (the approver's deliberate pause), and dry runs.

TRANSPORT: the existing Xod to Tree route, services/tree_inbox.py
(kind "task"). The Tree-side watcher that exists today delivers only a
message's text into the main conversation, so the text carries the whole
escalation in plain words: kind, question or card text, the options or
button, what Xod tried, why it is stuck, and how to resolve it. The same
payload also rides as a structured "escalation" field on the message
(tree_inbox.send_message's extra parameter) for the next consumer; the
current watcher ignores unknown fields, so nothing about today's
delivery changes. If the shared inbox is unreachable the message queues
in the local outbox like any other Tree message and drains later; the
escalation still counts as sent (it will land), and the audit says so.

DEDUPE AND EXPIRY: an escalation id is a hash of (kind, target id), and
the live set holds one entry per showing. The watchers call expire()
when a showing ends (the same prune points where they drop their
per-showing review caches); expiry closes the audit row as expired and
re-arms the id, so an identical widget or card that appears again later
is a new showing and can escalate again. A send that raises is NOT
recorded, so the next scan retries it; a send that lands (inbox or
queued) is recorded and never repeated for that showing.

AUDIT: every escalation opens a row in the approvals history
(agent_log, kind "muse_escalation") that stays "waiting" while it is
with Tree and is decided "expired by Xod: ..." if the showing ends
first. Card escalations also write a phase "escalation" row (and
"escalation_expired" on expiry) into the approver audit
(DATA_DIR/muse_approver/audit.jsonl), next to the stamp and outcome
rows for the same card. Tree's resolution is logged on Tree's side
against the escalation id (the consumer spec is in the Part 6 record).

This module never raises into a watcher: a broken escalation path must
not break pressing, scanning, or logging.
"""
import hashlib
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("xod.muse_escalate")

KINDS = ("option_widget_held", "option_widget_unpressable",
         "card_press_unverified", "chrome_card_unpressable")

_SURFACE_APPS = {"muse_app": "Muse app", "muse_app_options": "Muse app",
                 "chrome_extension": "Muse (Chrome extension)"}
_SURFACE_LABELS = {"muse_app": "Muse app approval card",
                   "muse_app_options": "Muse app chat (option widget)",
                   "chrome_extension": "Chrome extension side panel (Windows UIA)"}

_state: Dict[str, Any] = {"live": {},
                          "stats": {"sent": 0, "queued": 0, "failed": 0, "expired": 0}}
_send_fn: Optional[Callable] = None    # tests inject a fake transport here
_shot_fn: Optional[Callable] = None    # tests inject a fake capture here


def _eid(kind: str, target_id: str) -> str:
    return "museesc-" + hashlib.sha256(f"{kind}\n{target_id}".encode()).hexdigest()[:16]


def _device() -> str:
    try:
        from app.services import tree_inbox
        return tree_inbox.from_device()
    except Exception:
        return ""


def capture_for(target_id: str) -> Dict[str, Any]:
    """Best-effort screenshot for an escalation, through the approver's
    own capture seam: {"path": str|None, "error": str|None}. A failed
    capture is a recorded fact, never faked and never fatal."""
    if _shot_fn is not None:
        return _shot_fn(target_id)
    try:
        from app.services import muse_review
        return muse_review.capture(target_id)
    except Exception as e:
        return {"path": None, "error": str(e)[:200]}


def _render_text(p: Dict[str, Any]) -> str:
    """The escalation as plain words. This text is the payload for the
    Tree watcher that exists today: it delivers exactly this into the
    main conversation, so everything Tree needs must be here."""
    lines = [f"XOD ESCALATION [{p['kind']}] id {p['escalation_id']}",
             "Xod met something it cannot resolve itself. This is yours (Tree) to "
             "resolve; nothing is waiting on the owner and he is not to be asked.",
             f"Surface: {_SURFACE_LABELS.get(p['surface'], p['surface'])} "
             f"on {p.get('device') or 'this device'}"]
    if p.get("title"):
        lines.append(f"Card: {p['title']}")
    lines.append(f"What it asks: {p.get('text') or '(no text read)'}")
    if p.get("options"):
        lines.append("Options:")
        lines += [f"{i + 1}. {o}" for i, o in enumerate(p["options"])]
    if p.get("button"):
        lines.append(f"The approving button Xod identified: {p['button']}")
    if p.get("origin"):
        lines.append(f"Origin: {p['origin']}")
    if p.get("permission"):
        lines.append(f"Permission: {p['permission']}")
    if p.get("tried"):
        lines.append("What Xod tried: " + "; ".join(p["tried"]))
    lines.append(f"Why it is stuck: {p.get('reason') or 'not recorded'}")
    if p.get("screenshot"):
        lines.append(f"Screenshot: {p['screenshot']}")
    elif p.get("screenshot_error"):
        lines.append(f"Screenshot: unavailable ({p['screenshot_error']})")
    lines.append(
        "To resolve: act on it the way the owner would (most expansive option; an "
        "action over a deferral; the approval he already granted stands for cards). "
        "For a widget, answer it in the Muse app chat where it appears (match by "
        "the exact question text above). For a card, first confirm the underlying "
        "action did not already happen, then press it or complete the action. Log "
        f"the resolution against escalation id {p['escalation_id']} in the "
        "tree-inbox delivery log. If it is gone when you look, the showing ended "
        "and the escalation expired on its own; no action needed.")
    return "\n".join(lines)


def _audit_open(p: Dict[str, Any], transport: str, message_id: str) -> None:
    try:
        from app.services import agent_log
        lines = [f"kind: {p['kind']}", f"surface: {p['surface']}",
                 f"target: {p['target_id']}",
                 f"handed to Tree via tree-inbox: message {message_id or '(no id)'} "
                 f"(state {transport})"]
        if p.get("options"):
            lines.append("options: " + " | ".join(p["options"]))
        if p.get("button"):
            lines.append(f"button: {p['button']}")
        if p.get("tried"):
            lines.append("tried: " + "; ".join(p["tried"]))
        if p.get("reason"):
            lines.append(f"reason: {p['reason']}")
        if p.get("screenshot"):
            lines.append(f"screenshot: {p['screenshot']}")
        elif p.get("screenshot_error"):
            lines.append(f"screenshot unavailable: {p['screenshot_error']}")
        lines += ["---", p.get("text") or p.get("title") or "an escalation"]
        headline = f"Escalation {p['kind']}: {(p.get('title') or p.get('text') or '')[:100]}"
        agent_log.approval_opened(p["escalation_id"], "muse_escalation",
                                  _SURFACE_APPS.get(p["surface"], "Muse"),
                                  headline, "\n".join(lines))
    except Exception as e:
        logger.debug(f"escalation not recorded in the approvals history: {e}")
    if p["kind"] == "card_press_unverified":
        try:
            from app.services import muse_review
            muse_review.audit({"phase": "escalation", "card_id": p["target_id"],
                               "escalation_id": p["escalation_id"], "kind": p["kind"],
                               "reason": p.get("reason"), "transport": transport,
                               "message_id": message_id})
        except Exception:
            pass


def escalate(kind: str, target_id: str, *, surface: str, title: str = "",
             text: str = "", options: Optional[List[str]] = None,
             button: Optional[str] = None, tried: Optional[List[str]] = None,
             reason: str = "", screenshot: Optional[str] = None,
             screenshot_error: Optional[str] = None,
             capture: bool = False, extra: Optional[Dict[str, Any]] = None
             ) -> Optional[Dict[str, Any]]:
    """Emit one escalation for one showing of a widget or card. Returns
    the payload when it was sent (or queued for sending), None when this
    showing already escalated, the kind is unknown, or the send failed
    (a failed send is not recorded, so the next scan retries it)."""
    if kind not in KINDS:
        logger.warning(f"unknown escalation kind {kind!r}; not sent")
        return None
    eid = _eid(kind, target_id)
    if eid in _state["live"]:
        return None
    if capture and screenshot is None and screenshot_error is None:
        shot = capture_for(target_id)
        screenshot, screenshot_error = shot.get("path"), shot.get("error")
    payload: Dict[str, Any] = {
        "version": 1, "escalation_id": eid, "kind": kind, "surface": surface,
        "target_id": target_id, "title": title, "text": text,
        "options": list(options or []), "button": button,
        "tried": list(tried or []), "reason": reason,
        "screenshot": screenshot, "screenshot_error": screenshot_error,
        "device": _device(), "ts": time.time(),
    }
    if extra:
        for k, v in extra.items():
            payload.setdefault(k, v)
    body = _render_text(payload)
    try:
        if _send_fn is not None:
            res = _send_fn("task", body, payload)
        else:
            from app.services import tree_inbox
            res = tree_inbox.send_message("task", body, extra={"escalation": payload})
    except Exception as e:
        _state["stats"]["failed"] += 1
        logger.warning(f"escalation {eid} could not be sent (will retry on a later scan): {e}")
        return None
    transport = (res or {}).get("state") or "inbox"
    payload["message_id"] = (res or {}).get("id") or ""
    payload["transport"] = transport
    _state["live"][eid] = payload
    _state["stats"]["queued" if transport == "queued" else "sent"] += 1
    _audit_open(payload, transport, payload["message_id"])
    logger.info(f"escalated to Tree [{kind}] {eid}: {(title or text)[:120]}")
    return payload


def expire(target_id: str) -> int:
    """Close every live escalation for a target whose showing has ended.
    The audit row is decided expired (it only updates while the row is
    still waiting on Tree) and the id re-arms for a future showing.
    Returns how many escalations expired."""
    done = [eid for eid, p in _state["live"].items() if p["target_id"] == target_id]
    for eid in done:
        p = _state["live"].pop(eid)
        _state["stats"]["expired"] += 1
        try:
            from app.services import agent_log
            agent_log.approval_decided(
                eid, "expired by Xod: the showing ended before Tree resolved it")
        except Exception:
            pass
        if p["kind"] == "card_press_unverified":
            try:
                from app.services import muse_review
                muse_review.audit({"phase": "escalation_expired", "card_id": target_id,
                                   "escalation_id": eid, "kind": p["kind"]})
            except Exception:
                pass
        logger.info(f"escalation {eid} expired: the showing of {target_id} ended")
    return len(done)


def live() -> List[Dict[str, Any]]:
    return [dict(p) for p in _state["live"].values()]


def status() -> Dict[str, Any]:
    return {"live": len(_state["live"]), "stats": dict(_state["stats"]),
            "escalations": [{"escalation_id": p["escalation_id"], "kind": p["kind"],
                             "surface": p["surface"], "target_id": p["target_id"],
                             "transport": p.get("transport")}
                            for p in _state["live"].values()]}
