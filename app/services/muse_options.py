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
question and just picking the most expansive one." Xod reads the question and every
option label, scores each option for how much work / scope it authorizes (action verbs,
scope expanders, concrete counts) against how much it restricts, defers or negates
(no ... yet, only, just, keep ... first, wait, skip, stop), and presses the highest
scoring option. The log records the question, every option with its score and the
factors behind it, and why the picked one is the most expansive.

SAFETY POSTURE, same as the approval watchers:
- Only under the muse_auto_approve switch (enabled() and active() in muse_app): off,
  paused, or away-mode-while-present means held, never pressed.
- Protected topics hold the whole widget for the person: passwords, payment / cards,
  signing / contracts, credentials, two-factor. Picking "most expansive" there could
  authorize a spend or a signature, which are the owner's alone.
- Ambiguity holds: if the top two options score within AMBIGUITY_MARGIN, or no option
  scores positive, nothing is pressed and the log says why.
- A widget is pressed at most once per showing: pressed ids are kept in state (and the
  log id is a hash of question + options), so a widget that stays on screen after a
  press is never pressed again by this watcher.
- Read-back: after a press the tree is re-walked; verification is recorded as confirmed
  only when the widget is gone or the surrounding text changed. A widget still showing
  unchanged is logged as pressed-but-unconfirmed, never as verified.
- XOD_MUSE_OPTIONS_DRY_RUN=1 makes scans read-only (tests, click-testing the UI).
"""
import asyncio
import hashlib
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("xod.muse_options")

BACKSTOP_SEC = 5.0
AMBIGUITY_MARGIN = 1.5
MIN_OPTION_WORDS = 2
MAX_OPTIONS = 6

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

# Topics where even the most expansive option waits for the person (mirrors the
# protected-action hold in muse_chrome, plus spends and signatures).
PROTECTED_RES = [r"password", r"passkey", r"credit card", r"debit card", r"payment",
                 r"\bpay\b", r"purchase", r"\bbuy\b", r"checkout", r"credential",
                 r"two.factor", r"authenticator", r"\bsign\b.{0,30}(contract|agreement|nda)",
                 r"contract", r"bank account"]

# Expansive cues: (regex, weight, factor name). Applied to the option label, lowercased.
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
                          "loop": None}

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


def pick_most_expansive(question: str, labels: List[str]) -> Dict[str, Any]:
    """The policy itself: score every option, pick the most expansive, or hold.
    Returns {picked, scores, why, held_reason} — held_reason set means do not press."""
    scored = [score_option(l) for l in labels]
    ranked = sorted(scored, key=lambda s: -s["score"])
    top = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else None
    out: Dict[str, Any] = {"picked": None, "scores": scored, "why": "", "held_reason": ""}
    joined = (question or "") + " " + " ".join(labels)
    protected = next((rx for rx in PROTECTED_RES if re.search(rx, joined, re.I)), None)
    if protected:
        out["held_reason"] = f"protected topic (matches {protected!r}): waits for you"
        return out
    if top is None or top["score"] <= 0:
        out["held_reason"] = "no option authorizes more work (no positive expansive score)"
        return out
    if second is not None and top["score"] - second["score"] < AMBIGUITY_MARGIN:
        out["held_reason"] = (f"ambiguous: top options score {top['score']} and "
                              f"{second['score']} (margin under {AMBIGUITY_MARGIN})")
        return out
    out["picked"] = top["label"]
    others = "; ".join(f"'{s['label']}' scored {s['score']}" for s in ranked[1:])
    out["why"] = (f"'{top['label']}' is the most expansive at {top['score']} "
                  f"({', '.join(top['factors']) or 'no factors'}); the rest: {others}")
    return out


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
            lines.append(f"why most expansive: {entry['why']}")
        if entry.get("held_reason"):
            lines.append(f"held: {entry['held_reason']}")
        if entry.get("reason"):
            lines.append(f"reason: {entry['reason']}")
        lines.append(f"read-back: {entry.get('readback') or 'not pressed'}")
        lines.append("route: Muse chat option widget (AX/UIA tree)")
        agent_log.approval_opened(wid, "muse_options", "Muse chat options",
                                  entry["question"][:120], "\n".join(lines))
        agent_log.approval_decided(
            wid, "picked most expansive: %s" % entry["picked"] if entry.get("pressed")
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
    """Find widgets in one tree, apply the most-expansive policy, press when allowed.
    ax/root are injectable for tests, in the FakeAX shape of the chrome suite."""
    entries: List[Dict[str, Any]] = []
    for w in find_widgets(ax, root):
        labels = list(w["buttons"])
        pick = pick_most_expansive(w["question"], labels)
        wid = _widget_id(w["question"], labels)
        entry: Dict[str, Any] = {"id": wid, "question": w["question"], "options": labels,
                                 "scores": pick["scores"], "picked": pick["picked"],
                                 "why": pick["why"], "held_reason": pick["held_reason"],
                                 "pressed": False, "decision": "held", "reason": "",
                                 "readback": "", "style": w["style"]}
        if pick["held_reason"]:
            entry["reason"] = pick["held_reason"]
        elif wid in _state["pressed_ids"]:
            entry["reason"] = "already pressed: not pressing again while it still shows"
        elif not _core.enabled():
            entry["reason"] = "Muse approvals are off or paused"
        elif not _core.active():
            entry["reason"] = "away mode: you're at this computer"
        elif dry_run() or not press:
            entry["decision"] = "would_press"
            entry["reason"] = "dry run: would press %s" % pick["picked"]
        else:
            el = w["buttons"][pick["picked"]]
            err = ax.perform(el, "AXPress")
            if err == 0:
                entry["pressed"] = True
                entry["decision"] = "pressed"
                _state["pressed_ids"].add(wid)
                # Read-back: re-walk the same tree. Confirmed only if the widget is
                # gone or its question text changed; still-showing is unconfirmed.
                try:
                    after = find_widgets(ax, root)
                    still = any(_widget_id(a["question"], list(a["buttons"])) == wid for a in after)
                    entry["readback"] = ("unconfirmed: widget still showing unchanged after the press"
                                         if still else "confirmed: widget no longer showing after the press")
                except Exception as e:
                    entry["readback"] = f"unconfirmed: read-back walk failed ({str(e)[:120]})"
            else:
                entry["reason"] = f"press failed: AXPress returned {err}"
                entry["readback"] = "not verified: the press did not report success"
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
    return {"target": "chat_options", "label": "Muse chat options (most expansive)",
            "watching": bool(task is not None and not task.done()),
            "dry_run": dry_run(), "seen": _state["seen"], "pressed": _state["pressed"],
            "would_press": _state["would_press"], "held": _state["held"],
            "policy": "pick the most expansive option (authorizes the most work / broadest scope)",
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
