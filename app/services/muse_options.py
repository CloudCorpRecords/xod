"""
Muse CHAT option widgets: the choice buttons an assistant message offers in Muse chat
(the muse.create_options widget — a question, then two or more dotted-outline buttons,
each labelled with a full-sentence reply the person can tap). the screenshot,
2026-11-22: "Want me to send those 7 queued DMs through the standing LinkedIn tab in
your Mac main Chrome?" with buttons "Send the 7 queued DMs via my Mac main Chrome
LinkedIn tab" and "Keep building the employer list first, no DMs yet".

WHY THE EXISTING WATCHERS MISS THESE (diagnosis, AGE-91 follow-on):
- services/muse_app.py only treats a group whose label contains "approval request" as a
  card, and its _pick_button only recognises Allow / Always allow / Send / Deny style
  labels. An option widget lives in ordinary chat flow, its container is not named
  "Approval request for ...", and its button labels are sentences, so _pick_button
  returns None and the walk passes it by entirely.
- services/muse_chrome.py scopes its walk to the Chrome extension SidePanel subtree and
  prunes page Documents on purpose, so a widget rendered in a Muse chat page is out of
  scope by construction; it is also dry-run absolute.
- The dotted/dashed outline that marks these buttons visually is a CSS border: neither
  the macOS AX tree nor Windows UIA exposes border style as an attribute Xod can read, so
  detection here is STRUCTURAL, not stylistic: a container holding 2+ sibling buttons
  with sentence-length labels, plus question text in the same container that is not a
  button label, and none of the buttons being an approval/navigation button. If a tree
  element ever does expose a style attribute mentioning dotted/dashed, that is accepted
  as a confirming signal, never required.

THE POLICY (owner rule, 2026-11-22): "make xod capable of understanding the
question and just picking the most expansive one." First shipped as keyword scoring;
the owner then split Xod's two press systems explicitly (2026-11-22, later the same
day): approval cards are a pure rubber stamp with no judgment, but these question
widgets "are the ones that should be using the AI to review and click the right
ones" — a widget is an instruction to an agent, and a wrong press is worse than a
hold. So the pick here is made by an AI REVIEW: Xod's own configured model is
called in-process (the llm_service route the live backend answers chat with, no
tools), reads the question and every option, and answers one strict JSON verdict
{"pick": <0-based index or null>, "reason": "<one short line>"}. The prompt decides
as the owner's proxy: prefer the option that authorizes the most work / broadest
scope when options differ in scope, and never pick a deferral or no-op option
(not yet, later, keep ... first, wait, skip) when an action option exists. The
heuristic scorer below still runs, but only for the log: the record keeps the
question, every option with its heuristic score and factors, the model's reason,
and the read-back.

SAFETY POSTURE:
- Only under the muse_auto_approve switch (enabled() and active() in muse_app): off,
  paused, or away-mode-while-present means held, never pressed. The switch is
  checked before the model is ever consulted.
- A failed review HOLDS, always: model error, timeout, malformed answer, or the
  model itself returning no clear pick (pick null) means nothing is pressed and
  the log says why. There is no fallback to the heuristic score as a blind guess.
- No category exclusions: the old blanket hold on protected topics (passwords,
  payment, contracts, credentials, 2FA) is gone per the owner's permissionless
  stance for Xod — the model judges those widgets on their merits like any
  other, and its reason is logged. The old ambiguity hold is gone too: resolving
  ambiguity is the model's job; a hold happens only when the model returns no
  clear pick.
- One review per showing: a verdict (pick or explicit no-pick) is cached by
  widget id while that showing lasts, so the backstop scan does not re-ask the
  model every few seconds. A review that FAILED (unavailable) is not cached —
  the next scan retries it.
- A widget is pressed at most once per showing: pressed ids are kept in state (and the
  log id is a hash of question + options), so a widget that stays on screen after a
  press is never pressed again by this watcher.
- A widget Xod cannot resolve escalates to Tree (services/muse_escalate.py), once per
  showing: a held review (unavailable or no clear pick) escalates as
  option_widget_held, a failed press as option_widget_unpressable. Switch-off, away
  and dry-run holds are the owner's intent or a test mode and never escalate. The
  escalation expires when the showing ends (the review-cache prune above).
- Read-back: after a press the tree is re-walked; verification is recorded as confirmed
  only when the widget is gone or the surrounding text changed. A widget still showing
  unchanged is logged as pressed-but-unconfirmed, never as verified.
- XOD_MUSE_OPTIONS_DRY_RUN=1 makes scans read-only (tests, click-testing the UI);
  the review still runs, so a dry run shows the pick the model would press.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("xod.muse_options")

BACKSTOP_SEC = 5.0
MIN_OPTION_WORDS = 2
MAX_OPTIONS = 6
REVIEW_TIMEOUT_S = 60.0     # the model call itself
GATE_TIMEOUT_S = 75.0       # how long a scan waits for the review
MAX_REASON_CHARS = 300

_SYSTEM = (
    "You are Xod, answering a multiple-choice question widget on behalf of the owner in his "
    "Muse chat. His agents ask these questions because they need his call to proceed; he has "
    "directed you to answer them as he would, so he never has to. Decide the way he decides: "
    "he wants the work done. When the options differ in scope, pick the one that authorizes "
    "the most work and the broadest scope. Never pick a deferral or no-op option (not yet, "
    "later, keep doing something else first, wait, skip, do nothing) when an action option "
    "exists. Judge every topic on its merits; there is no category you refuse outright — "
    "payments, passwords, contracts and identity items are his to authorize and yours to "
    "judge like everything else. If no option is a clear pick — the options do not really "
    "differ, or the question cannot be understood from what is shown — answer pick null and "
    "say why. Answer with exactly one JSON object and nothing else: "
    "{\"pick\": <0-based option index or null>, \"reason\": \"<one short line>\"}."
)

_review_fn: Optional[Callable] = None    # tests inject a fake reviewer here

# Buttons that mean this is NOT an option widget (approvals are muse_app/muse_chrome's
# job; navigation/composer controls are the chat UI's own furniture).
NOT_OPTION_LABELS = {
    "allow", "deny", "allow once", "allow all once", "don't send", "deny all",
    "always allow for this recipient", "always allow for this site", "block",
    "send", "copy", "retry", "edit", "like", "dislike", "thumbs up", "thumbs down",
    "new chat", "stop generating", "regenerate", "share", "download", "upload",
    "attach", "record", "submit", "cancel", "close", "back", "next", "menu",
    "settings", "sign in", "sign out", "log in", "log out",
}

# Expansive cues: (regex, weight, factor name). Applied to the option label, lowercased.
# These scores are computed for the LOG only since the AI review upgrade (2026-11-22):
# they show what the old heuristic would have picked, next to the model's actual pick.
EXPAND_RES: List[Tuple[str, float, str]] = [
    (r"\b(send|post|publish|apply|submit|book|create|run|start|launch|open|enable|allow|"
     r"approve|invite|message|email|call|schedule|add|upgrade|fix|build|deploy|ship|merge|"
     r"proceed|continue|go ahead|do it|connect|install|download|upload|share|follow up)\b",
     3.0, "action verb"),
    (r"\b(all|every|everything|both|full|complete|entire|also|plus|including|more|most|"
     r"maximum|broadest|entire batch|whole)\b", 2.0, "scope expander"),
    (r"\band\b", 1.0, "adds a second action"),
]
# Restrictive / deferral cues.
RESTRICT_RES: List[Tuple[str, float, str]] = [
    (r"\bno\b[^.]{0,40}\byet\b", 5.0, "explicit not-yet"),
    (r"\b(stop|cancel|skip|never|nothing|none|neither|wait|later|hold|pause|delay)\b",
     4.0, "stops or defers"),
    (r"\b(only|just|merely)\b", 3.0, "limits scope"),
    (r"\bfirst\b", 2.0, "does something else first"),
    (r"\bkeep\b", 2.0, "keeps the status quo"),
    (r"\b(don't|do not|not yet|no dms|no messages)\b", 4.0, "negation"),
]

_state: Dict[str, Any] = {"task": None, "seen": 0, "pressed": 0, "held": 0, "would_press": 0,
                          "last": None, "error": None, "logged_ids": set(), "pressed_ids": set(),
                          "loop": None, "review_cache": {},
                          "reviews": {"reviewed": 0, "picked": 0, "no_pick": 0, "unavailable": 0},
                          "last_review": None}

from app.services import muse_app as _core  # noqa: E402
from app.services import muse_backends as _backends  # noqa: E402


def dry_run() -> bool:
    return bool(os.environ.get("XOD_MUSE_OPTIONS_DRY_RUN"))


# ------------------------------------------------------------------ scoring
def score_option(label: str) -> Dict[str, Any]:
    """How much work/scope one option authorizes, with the factors that decided it."""
    low = " " + re.sub(r"\s+", " ", (label or "").lower().strip()) + " "
    score, factors = 0.0, []
    for rx, weight, name in EXPAND_RES:
        hits = len(re.findall(rx, low))
        if hits:
            score += weight * hits
            factors.append(f"+{weight * hits:g} {name}")
    for rx, weight, name in RESTRICT_RES:
        hits = len(re.findall(rx, low))
        if hits:
            score -= weight * hits
            factors.append(f"-{weight * hits:g} {name}")
    nums = [float(n.replace(",", "")) for n in re.findall(r"\b(\d[\d,]*)\b", low)]
    if nums:
        bonus = min(2.0, max(nums) / 10.0)
        score += bonus
        factors.append(f"+{bonus:g} concrete count {int(max(nums))}")
    words = len(low.split())
    if words >= 4:
        bonus = min(1.0, words / 20.0)
        score += bonus
        factors.append(f"+{bonus:g} specific, fully described action")
    return {"label": label, "score": round(score, 2), "factors": factors}


def heuristic_pick(labels: List[str]) -> Dict[str, Any]:
    """The original keyword scorer, kept for the LOG only: it ranks the options so
    the record shows what the heuristic would have picked next to the model's pick.
    It never decides a press any more and it never holds anything."""
    scored = [score_option(l) for l in labels]
    ranked = sorted(scored, key=lambda s: -s["score"])
    top = ranked[0] if ranked else None
    out: Dict[str, Any] = {"picked": None, "scores": scored, "why": ""}
    if top is not None:
        out["picked"] = top["label"]
        others = "; ".join(f"'{s['label']}' scored {s['score']}" for s in ranked[1:])
        out["why"] = (f"heuristic top: '{top['label']}' at {top['score']} "
                      f"({', '.join(top['factors']) or 'no factors'}); the rest: {others}")
    return out


# ------------------------------------------------------------------ the AI review
def _widget_prompt(question: str, labels: List[str]) -> str:
    lines = [f"Question: {question}", "Options:"]
    lines += [f"{i}. {label}" for i, label in enumerate(labels)]
    return "\n".join(lines)


def validate_pick(obj: Any, count: int) -> Optional[Dict[str, Any]]:
    """(pick_index|None, reason) from a parsed answer, or None when the shape is
    wrong in any way — a malformed answer is a malfunction, never a decision."""
    if not isinstance(obj, dict):
        return None
    pick, reason = obj.get("pick"), obj.get("reason")
    if pick is not None:
        if isinstance(pick, bool) or not isinstance(pick, int):
            return None
        if not (0 <= pick < count):
            return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    return {"pick_index": pick, "reason": " ".join(reason.split())[:MAX_REASON_CHARS]}


async def _model_review(question: str, labels: List[str]) -> Dict[str, Any]:
    """One real review through Xod's own configured model (the llm_service the
    live backend answers with), in-process, no tools. Raises on transport/config
    failure or a malformed answer; review() turns any raise into a hold."""
    from app.services.llm import llm_service, parse_llm_json
    t0 = time.time()
    res = await asyncio.wait_for(
        llm_service.chat_completion(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": _widget_prompt(question, labels)}],
            temperature=0.1, max_tokens=300, think=False),
        timeout=REVIEW_TIMEOUT_S)
    text = (res or {}).get("content") or ""
    try:
        info = llm_service.get_endpoint_info()
    except Exception:
        info = {}
    parsed = validate_pick(parse_llm_json(text), len(labels))
    if parsed is None:
        raise ValueError(f"malformed review answer: {text[:160]!r}")
    return {**parsed, "model": info.get("model") or "", "provider": info.get("provider") or "",
            "latency_s": round(time.time() - t0, 2), "raw": text[:500]}


async def review(question: str, labels: List[str]) -> Dict[str, Any]:
    """The REVIEW step. Always answers a verdict dict:
      {pick_index: int, reason, model, ...}   — press labels[pick_index]
      {pick_index: None, held_reason: str}    — hold; never press
    A review that could not happen (error, timeout, garbage) holds with the
    failure as its reason: a widget is never pressed on a review that did not
    happen, and never on a heuristic guess in its place."""
    t0 = time.time()
    try:
        if _review_fn is not None:
            out = await _review_fn(question, labels)
        else:
            out = await _model_review(question, labels)
        out = dict(out)
        out.setdefault("latency_s", round(time.time() - t0, 2))
        parsed = validate_pick({"pick": out.get("pick_index"), "reason": out.get("reason")},
                               len(labels))
        if "pick_index" not in out or parsed is None:
            raise ValueError("reviewer answered outside the verdict shape")
        out["reason"] = parsed["reason"]
    except Exception as e:
        _state["reviews"]["unavailable"] += 1
        logger.warning(f"muse option review unavailable: {e}")
        return {"pick_index": None, "model": "", "provider": "",
                "latency_s": round(time.time() - t0, 2),
                "held_reason": f"review unavailable: {str(e)[:180]}"}
    _state["reviews"]["reviewed"] += 1
    _state["last_review"] = {"reason": out["reason"], "model": out.get("model") or "",
                             "pick_index": out["pick_index"], "at": time.time()}
    if out["pick_index"] is None:
        _state["reviews"]["no_pick"] += 1
        out["held_reason"] = f"model returned no clear pick: {out['reason']}"
    else:
        _state["reviews"]["picked"] += 1
    logger.info(f"muse option review: pick={out['pick_index']} — {out['reason']}")
    return out


def review_sync(question: str, labels: List[str], timeout: float = GATE_TIMEOUT_S) -> Dict[str, Any]:
    """The review for scan paths, which run in worker threads: the coroutine
    runs on the watcher's bound backend loop and this waits for it. With no
    loop bound (a test, a stray caller) it runs on a throwaway loop in a
    helper thread, so a caller that itself sits on an event loop can never
    deadlock against it. Mirrors muse_review.review_sync."""
    loop = _state.get("loop")
    if loop is not None and getattr(loop, "is_running", lambda: False)():
        fut = asyncio.run_coroutine_threadsafe(review(question, labels), loop)
        return fut.result(timeout=timeout)
    box: Dict[str, Any] = {}

    def run():
        try:
            box["out"] = asyncio.run(review(question, labels))
        except Exception as e:  # review() already fails soft; this is the last net
            box["out"] = {"pick_index": None, "model": "", "provider": "", "latency_s": 0.0,
                          "held_reason": f"review unavailable: {str(e)[:180]}"}

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive() or "out" not in box:
        return {"pick_index": None, "model": "", "provider": "", "latency_s": timeout,
                "held_reason": "review unavailable: timed out"}
    return box["out"]


# ------------------------------------------------------------------ tree access
def _role(ax, el) -> str:
    return str(ax.attr(el, "AXRole") or "")


def _children(ax, el) -> list:
    return ax.attr(el, "AXChildren") or []


def _label(ax, el) -> str:
    return _core._label(ax, el).strip()


def _style_hint(ax, el) -> str:
    for name in ("AXStyle", "AXDescription", "UIAClassName"):
        v = ax.attr(el, name)
        if v and re.search(r"dotted|dashed", str(v), re.I):
            return "dotted/dashed outline exposed by the tree"
    return ""


def _walk(ax, el, depth=0):
    if depth > 40:
        return
    yield el, depth
    for c in _children(ax, el):
        yield from _walk(ax, c, depth + 1)


def _subtree(ax, el, depth=0, out=None):
    if out is None:
        out = {"buttons": {}, "texts": []}
    if depth > 12:
        return out
    role, label = _role(ax, el), _label(ax, el)
    if role == "AXButton" and label:
        out["buttons"].setdefault(label, el)
    elif label and role in ("AXStaticText", "AXGroup", "AXDocument"):
        out["texts"].append(label)
    for c in _children(ax, el):
        _subtree(ax, c, depth + 1, out)
    return out


def find_widgets(ax, root) -> List[Dict[str, Any]]:
    """Option widgets in a Muse chat tree: innermost containers with 2-6 sibling
    sentence-labelled buttons (none of them approval/navigation buttons) plus
    question text that is not itself a button label."""
    candidates = []
    for el, _depth in _walk(ax, root):
        sub = _subtree(ax, el)
        labels = list(sub["buttons"])
        if not (2 <= len(labels) <= MAX_OPTIONS):
            continue
        if any(l.casefold() in NOT_OPTION_LABELS for l in labels):
            continue
        if any(len(l.split()) < MIN_OPTION_WORDS and len(l) < 8 for l in labels):
            continue
        qtexts = [t for t in sub["texts"] if t not in labels]
        if not qtexts:
            continue
        question = max(qtexts, key=len)
        if "?" not in question and not re.search(
                r"\b(want|should|shall|which|pick|choose|prefer)\b", question, re.I):
            continue
        candidates.append({"el": el, "question": " ".join(question.split()),
                           "buttons": sub["buttons"], "style": _style_hint(ax, el)})
    # Innermost only: an ancestor contains the same buttons because its subtree does.
    def contains(a, b):
        return any(node is b for node, _ in _walk(ax, a))
    widgets = []
    for c in candidates:
        if any(o is not c and contains(c["el"], o["el"]) for o in candidates):
            continue
        widgets.append({k: c[k] for k in ("question", "buttons", "style")})
    return widgets


# ------------------------------------------------------------------ the record
def _widget_id(question: str, labels: List[str]) -> str:
    full = question + "\n" + "\n".join(labels)
    return "museopt-" + hashlib.sha256(full.encode()).hexdigest()[:16]


def _log(entry: Dict[str, Any]) -> bool:
    wid = entry["id"]
    if wid in _state["logged_ids"]:
        return False
    try:
        from app.services import agent_log
        if any(r["id"] == wid for r in agent_log.approvals(500) if r.get("kind") == "muse_options"):
            _state["logged_ids"].add(wid)
            return False
        lines = [f"question: {entry['question']}"]
        for s in entry["scores"]:
            mark = " <= picked" if s["label"] == entry.get("picked") else ""
            lines.append(f"option [{s['score']}] {s['label']} "
                         f"({', '.join(s['factors']) or 'no factors'}){mark}")
        if entry.get("why"):
            lines.append(entry["why"])
        if entry.get("model"):
            lines.append(f"model: {entry['model']}"
                         + (f" via {entry['provider']}" if entry.get("provider") else ""))
        if entry.get("model_reason"):
            lines.append(f"model reason: {entry['model_reason']}")
        if entry.get("held_reason"):
            lines.append(f"held: {entry['held_reason']}")
        if entry.get("escalation"):
            lines.append(f"escalated to Tree: {entry['escalation']} "
                         f"({entry.get('escalation_note') or 'sent'})")
        if entry.get("reason"):
            lines.append(f"reason: {entry['reason']}")
        lines.append(f"read-back: {entry.get('readback') or 'not pressed'}")
        lines.append("route: Muse chat option widget (AX/UIA tree), AI review pick")
        agent_log.approval_opened(wid, "muse_options", "Muse chat options",
                                  entry["question"][:120], "\n".join(lines))
        agent_log.approval_decided(
            wid, "picked by AI review: %s" % entry["picked"] if entry.get("pressed")
            else ("would press (dry run): %s" % entry["picked"] if entry.get("decision") == "would_press"
                  else f"not pressed: {entry.get('held_reason') or entry.get('reason') or 'held'}"))
    except Exception as e:
        logger.debug(f"muse option widget not logged: {e}")
        return False
    _state["logged_ids"].add(wid)
    _state["last"] = {"question": entry["question"], "at": time.time(),
                      "picked": entry.get("picked"), "pressed": bool(entry.get("pressed"))}
    logger.info(f"Muse option widget {'pressed ' + str(entry.get('picked')) if entry.get('pressed') else 'held'}: "
                f"{entry['question'][:120]}")
    return True


# ------------------------------------------------------------------ one scan
def scan_tree(ax, root, press: bool = True) -> List[Dict[str, Any]]:
    """Find widgets in one tree, ask the AI review for the pick, press when
    allowed. ax/root are injectable for tests, in the FakeAX shape of the
    chrome suite. The switch is checked before the model is consulted, and a
    failed or unclear review holds — the heuristic scores are log-only."""
    entries: List[Dict[str, Any]] = []
    widgets = find_widgets(ax, root)
    live_ids = {_widget_id(w["question"], list(w["buttons"])) for w in widgets}
    from app.services import muse_escalate
    for stale in [k for k in _state["review_cache"] if k not in live_ids]:
        del _state["review_cache"][stale]   # showing ended: forget its verdict
        muse_escalate.expire(stale)         # and close any escalation it had open
    for w in widgets:
        labels = list(w["buttons"])
        heur = heuristic_pick(labels)
        wid = _widget_id(w["question"], labels)
        entry: Dict[str, Any] = {"id": wid, "question": w["question"], "options": labels,
                                 "scores": heur["scores"], "picked": None,
                                 "why": heur["why"], "held_reason": "",
                                 "model_reason": "", "model": "", "provider": "",
                                 "pressed": False, "decision": "held", "reason": "",
                                 "readback": "", "style": w["style"]}
        if wid in _state["pressed_ids"]:
            entry["reason"] = "already pressed: not pressing again while it still shows"
        elif not _core.enabled():
            entry["reason"] = "Muse approvals are off or paused"
        elif not _core.active():
            entry["reason"] = "away mode: you're at this computer"
        else:
            verdict = _state["review_cache"].get(wid)
            if verdict is None:
                verdict = review_sync(w["question"], labels)
                if "held_reason" not in verdict or not str(verdict.get("held_reason") or "") \
                        .startswith("review unavailable"):
                    _state["review_cache"][wid] = verdict   # one review per showing
            entry["model"] = verdict.get("model") or ""
            entry["provider"] = verdict.get("provider") or ""
            entry["model_reason"] = verdict.get("reason") or ""
            if verdict.get("held_reason"):
                entry["held_reason"] = verdict["held_reason"]
                entry["reason"] = verdict["held_reason"]
                # A held widget is an unanswered instruction to an agent:
                # escalate it to Tree (once per showing) instead of letting
                # it wait on the owner. Switch-off and away holds never
                # reach this branch; they are the owner's intent.
                esc = muse_escalate.escalate(
                    "option_widget_held", wid, surface="muse_app_options",
                    title=w["question"], text=w["question"], options=labels,
                    tried=[f"AI review by {entry['model'] or 'the configured model'}",
                           f"heuristic ranking (log only) topped: {heur['picked'] or '(none)'}"],
                    reason=verdict["held_reason"], capture=True)
                if esc:
                    entry["escalation"] = esc["escalation_id"]
                    entry["escalation_note"] = (f"message {esc.get('message_id') or '(no id)'} "
                                                 f"({esc.get('transport') or 'sent'})")
            else:
                entry["picked"] = labels[verdict["pick_index"]]
                if dry_run() or not press:
                    entry["decision"] = "would_press"
                    entry["reason"] = "dry run: would press %s" % entry["picked"]
                else:
                    el = w["buttons"][entry["picked"]]
                    err = ax.perform(el, "AXPress")
                    if err == 0:
                        entry["pressed"] = True
                        entry["decision"] = "pressed"
                        _state["pressed_ids"].add(wid)
                        # Read-back: re-walk the same tree. Confirmed only if the widget is
                        # gone or its question text changed; still-showing is unconfirmed.
                        try:
                            after = find_widgets(ax, root)
                            still = any(_widget_id(a["question"], list(a["buttons"])) == wid
                                        for a in after)
                            entry["readback"] = (
                                "unconfirmed: widget still showing unchanged after the press"
                                if still else "confirmed: widget no longer showing after the press")
                        except Exception as e:
                            entry["readback"] = f"unconfirmed: read-back walk failed ({str(e)[:120]})"
                    else:
                        entry["reason"] = f"press failed: AXPress returned {err}"
                        entry["readback"] = "not verified: the press did not report success"
                        # Xod can see this widget and knows the answer, but
                        # its press did not land: Tree's hands take it from
                        # here (once per showing).
                        esc = muse_escalate.escalate(
                            "option_widget_unpressable", wid, surface="muse_app_options",
                            title=w["question"], text=w["question"], options=labels,
                            button=entry["picked"],
                            tried=[f"AI review picked: {entry['picked']} "
                                   f"({entry['model_reason']})",
                                   f"AXPress on that option, which returned {err}"],
                            reason=f"the press failed: AXPress returned {err}",
                            capture=True)
                        if esc:
                            entry["escalation"] = esc["escalation_id"]
                            entry["escalation_note"] = (
                                f"message {esc.get('message_id') or '(no id)'} "
                                f"({esc.get('transport') or 'sent'})")
        entries.append(entry)
        _log(entry)
    _state["seen"] += len(entries)
    _state["pressed"] += sum(1 for e in entries if e["pressed"])
    _state["would_press"] += sum(1 for e in entries if e["decision"] == "would_press")
    _state["held"] += sum(1 for e in entries if e["decision"] == "held")
    return entries


def scan(ax=None, pid: Optional[int] = None) -> Dict[str, Any]:
    """One read of the Muse app's tree (the chat surface Xod already watches)."""
    ax = ax or _ax_api()
    if pid is None:
        pid = _core._muse_pid()
    if pid is None:
        return {"running": False, "widgets": []}
    root = ax.create_app(pid)
    return {"running": True, "widgets": scan_tree(ax, root, press=True)}


def _ax_api():
    b = _backends.backend()
    if b is None:
        raise RuntimeError("The Muse option watcher doesn't run on this platform.")
    return b.ax_api()


# ------------------------------------------------------------------ status + watcher
def status() -> Dict[str, Any]:
    task = _state.get("task")
    return {"target": "chat_options", "label": "Muse chat options (AI review, most expansive)",
            "watching": bool(task is not None and not task.done()),
            "dry_run": dry_run(), "seen": _state["seen"], "pressed": _state["pressed"],
            "would_press": _state["would_press"], "held": _state["held"],
            "policy": "AI review picks the most expansive option (authorizes the most work / "
                      "broadest scope); a failed or unclear review holds, never guesses",
            "reviews": dict(_state["reviews"]), "last_review": _state["last_review"],
            "last": _state["last"], "error": _state.get("error")}


async def _watch() -> None:
    _state["loop"] = asyncio.get_running_loop()
    while True:
        try:
            if _core.enabled() and _core.active():
                await asyncio.to_thread(scan)
                _state["error"] = None
        except Exception as e:
            _state["error"] = f"Can't reach Muse chat options: {str(e)[:200]}"
            logger.warning(f"Muse option watcher: {e}")
        await asyncio.sleep(BACKSTOP_SEC)


def start() -> None:
    from app.services import run_mode
    if run_mode.skip("muse_app"):
        return
    if _backends.backend() is None:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    t = _state["task"]
    if t is None or t.done():
        _state["task"] = asyncio.create_task(_watch())
