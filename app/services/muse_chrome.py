"""
The Muse Chrome extension's approval cards: a second target for the Muse auto-approve
watcher (services/muse_app.py), watched through the Windows UI Automation tree.

The probe (~/workspace/xod-eval/CHROME-EXTENSION-APPROVAL-PROBE-2026-11-20.md, AGE-91) proved
the surfaces on Windows: the extension side panel is a first-class UIA subtree (a Pane whose
class is SidePanel, holding a Document named Muse with ordinary labeled buttons), and
Chrome's own permission bubbles hang inside the window tree as a PermissionPromptBubbleBaseView
subtree with Invoke-capable Allow and Block buttons.

DRY RUN IS ABSOLUTE in this build: the watcher reads cards, applies the same policy the Muse
app watcher applies (the switch's mode, the safety check, protected-action holds, the button
preference order), logs every card once with the decision it WOULD have taken, and never
presses anything. There is no code path here that calls perform(). Pressing is a separate,
later step, and it stays off until the card matcher has been checked against one real card:
the extension is signed out in the Razer ChromeTikTok profile, so no real approval card has
been captured yet. TODO (AGE-91): capture the real card wording from a signed-in session and
tune CARD_PATTERNS against it before any pressing build.

Scope is structural, not textual: the walk only ever looks inside the SidePanel subtree for
cards. Page content is pruned during the search walk, so a fake approval card drawn by a web
page can never be seen by the matcher, let alone acted on. Permission bubbles are a separate
card class with a stricter rule: an Allow press there is a durable site grant, so a bubble is
held for the person unless its origin is on the allowlist (the muse_chrome_bubble_origins
setting, a JSON list, empty by default). Even an allowlisted bubble is only logged in this
dry-run build.

The Mac route is not built here: AGE-57 blocks Mac pressing regardless, and the Mac side
panel's AX shape is still unprobed. On any platform without the Windows UIA backend this
module stays quiet and its status says why.

Escalation (2026-11-22, best effort): a card this watcher decides it WOULD press is one
it can see but, being dry-run, cannot press, so it escalates to Tree through
services/muse_escalate.py (kind chrome_card_unpressable, once per showing, expiring
when a completed scan no longer sees the card). Cards held under this watcher's own
policy do not escalate. Option widgets inside Chrome web pages stay invisible here by
construction (page Documents are pruned), so they can never escalate from this surface;
that gap is named in the Part 6 record of the approval watcher update file.
"""
import asyncio
import hashlib
import json
import logging
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xod.muse_chrome")

DRY_RUN = True  # absolute in this build; see the module docstring. There is no override.
BACKSTOP_SEC = 30.0
SCAN_TIMEOUT = 10.0
ALLOWLIST_SETTING = "muse_chrome_bubble_origins"
PANEL_CLASS = "SidePanel"
PANEL_DOCUMENT_NAME = "Muse"
BUBBLE_CLASS = "PermissionPromptBubbleBaseView"
WARM_MIN_NODES = 50  # a cold Chrome web tree walks ~45 nodes; a warm one walks hundreds

# TODO (AGE-91): placeholder patterns from the probe and the Muse app card wording. A real
# signed-in extension card has never been captured, so these stay configurable in one place
# and the watcher ships dry-run until they are tuned against that capture.
CARD_PATTERNS: Dict[str, Any] = {
    "group_label_res": [r"approval request"],
    "text_res": [r"approval request", r"needs your approval", r"wants your permission",
                 r"allow .{0,60} to .{0,80}\?"],
    "signed_out_res": [r"sign in to Muse"],
}
# Actions that always wait for the person, mirroring the protected-write hold in crew.py.
PROTECTED_RES = [r"password", r"passkey", r"credit card", r"payment", r"credential",
                 r"two.factor", r"authenticator"]

_state: Dict[str, Any] = {"task": None, "cards_seen": 0, "would_press": 0, "held": 0,
                          "bubbles_seen": 0, "last": None, "error": None, "signed_out": False,
                          "warmed": False, "logged_ids": set(), "loop": None, "backstop_at": 0.0,
                          "present_ids": set()}


# ------------------------------------------------------------------ tree access
# Same surface as muse_app's walk (attr over opaque elements), so tests stand in a fake tree.
from app.services import muse_app as _core  # noqa: E402
from app.services import muse_backends as _backends  # noqa: E402


def _ax_api():
    b = _backends.backend()
    if b is None or b.name != "win_uia":
        raise RuntimeError("The Chrome extension watcher needs the Windows UIA backend in this build.")
    return b.ax_api()


def _chrome_pid() -> Optional[int]:
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, timeout=5)
        for line in (r.stdout or "").splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    except Exception:
        pass
    return None


def _role(ax, el) -> str:
    return str(ax.attr(el, "AXRole") or "")


def _children(ax, el) -> list:
    return ax.attr(el, "AXChildren") or []


def _class_name(ax, el) -> str:
    return str(ax.attr(el, "UIAClassName") or "")


def _rect(ax, el):
    return ax.attr(el, "UIABoundingRectangle")


def _subtree_text(ax, el, depth: int = 0, out: Optional[List[str]] = None) -> List[str]:
    """Every static text and label under el, in walk order (the card's own words)."""
    if out is None:
        out = []
    if depth > 12:
        return out
    label = _core._label(ax, el).strip()
    if label and _role(ax, el) in ("AXStaticText", "AXGroup", "AXDocument"):
        out.append(label)
    for child in _children(ax, el):
        _subtree_text(ax, child, depth + 1, out)
    return out


def _subtree_buttons(ax, el, depth: int = 0, out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if out is None:
        out = {}
    if depth > 12:
        return out
    if _role(ax, el) == "AXButton":
        label = _core._label(ax, el).strip()
        if label:
            out.setdefault(label, el)
    for child in _children(ax, el):
        _subtree_buttons(ax, child, depth + 1, out)
    return out


def _count_nodes(ax, el, depth: int = 0) -> int:
    if depth > 40:
        return 1
    return 1 + sum(_count_nodes(ax, c, depth + 1) for c in _children(ax, el))


def _find_by_class(ax, el, class_name: str, prune_pages: bool, depth: int = 0,
                   found: Optional[list] = None) -> list:
    """Elements whose UIA class matches. With prune_pages, the search never descends into a
    page Document (role AXDocument outside the panel): page content is out of scope by
    construction, which is what keeps a fake in-page card invisible to this watcher."""
    if found is None:
        found = []
    if depth > 40:
        return found
    if _class_name(ax, el) == class_name:
        found.append(el)
        return found  # no nesting of the same surface inside itself
    if prune_pages and _role(ax, el) == "AXDocument":
        return found
    for child in _children(ax, el):
        _find_by_class(ax, child, class_name, prune_pages, depth + 1, found)
    return found


# ------------------------------------------------------------------ cards in the panel
def _is_card_text(text: str, label: str) -> bool:
    low_label = (label or "").lower()
    if any(re.search(rx, low_label) for rx in CARD_PATTERNS["group_label_res"]):
        return True
    low = (text or "").lower()
    return any(re.search(rx, low) for rx in CARD_PATTERNS["text_res"])


def _panel_cards(ax, panel) -> List[Dict[str, Any]]:
    """Approval cards inside the side panel subtree. The smallest matching group that holds
    a button (or any button at all in the panel, when it is the only card) is the card."""
    groups: List[Any] = []

    def collect(el, depth):
        if depth > 20:
            return
        if _role(ax, el) in ("AXGroup", "AXDocument"):
            groups.append(el)
        for child in _children(ax, el):
            collect(child, depth + 1)

    collect(panel, 0)
    panel_buttons = _subtree_buttons(ax, panel)
    cards: List[Dict[str, Any]] = []
    matched: List[Any] = []
    for g in groups:
        texts = _subtree_text(ax, g)
        label = _core._label(ax, g).strip()
        if _is_card_text(" ".join(texts), label):
            matched.append((g, texts))
    # Keep only innermost matches: an ancestor group matches because its subtree text does.
    matched_els = [g for g, _ in matched]
    innermost = [(g, texts) for g, texts in matched
                 if not any(other is not g and _is_descendant(ax, other, g) for other in matched_els)]
    for g, texts in innermost:
        buttons = _subtree_buttons(ax, g)
        if not _core._pick_button(list(buttons)) and len(innermost) == 1:
            buttons = panel_buttons  # the side-panel layout keeps some cards' buttons outside the group
        what = " ".join(" ".join(texts).split())
        cards.append({"title": _core._label(ax, g).strip() or "Chrome extension approval",
                      "what": what, "buttons": buttons, "kind": "panel"})
    return cards


def _is_descendant(ax, el, ancestor) -> bool:
    cur, hops = el, 0
    while cur is not None and hops < 40:
        if cur is ancestor:
            return True
        cur = ax.attr(cur, "AXParent")
        hops += 1
    return False


# ------------------------------------------------------------------ permission bubbles
_ORIGIN_RE = re.compile(r"(https?://[^\s]+)\s+wants to", re.I)


def _bubbles(ax, root, panes: Optional[list] = None) -> List[Dict[str, Any]]:
    """Chrome permission bubbles anywhere in the window tree, deduped by bounding rectangle
    (the Views tree reports the bubble subtree twice). Origin and permission come from the
    labels around the PermissionPromptBubbleBaseView pane, never from one permission's text."""
    if panes is None:
        panes = _find_by_class(ax, root, BUBBLE_CLASS, prune_pages=False)
    out: List[Dict[str, Any]] = []
    seen_rects = set()
    for pane in panes:
        rect = _rect(ax, pane)
        if rect is not None:
            if rect in seen_rects:
                continue
            seen_rects.add(rect)
        # Climb to the dialog or window element that carries the "X wants to" title.
        host, cur, hops = pane, ax.attr(pane, "AXParent"), 0
        while cur is not None and hops < 6:
            host = cur
            if _role(ax, cur) == "AXWindow" or _ORIGIN_RE.search(_core._label(ax, cur)):
                break
            cur = ax.attr(cur, "AXParent")
            hops += 1
        texts = _subtree_text(ax, host)
        joined = " ".join(texts)
        m = _ORIGIN_RE.search(_core._label(ax, host)) or _ORIGIN_RE.search(joined)
        origin = m.group(1) if m else ""
        permission = ""
        for t in _subtree_text(ax, pane):
            if t and t.lower() not in ("allow", "block"):
                permission = t
                break
        buttons = _subtree_buttons(ax, host)
        out.append({"title": _core._label(ax, host).strip() or "Chrome permission prompt",
                    "what": " ".join(joined.split()), "buttons": buttons, "kind": "bubble",
                    "origin": origin, "permission": permission})
    return out


# ------------------------------------------------------------------ the policy decision
def _safety_warning(what: str) -> str:
    """The same safety check Crew's auto-approve applies (services/crew.py _safety)."""
    try:
        from app.services import crew
        return crew._safety("", what)
    except Exception:
        return ""


def _evaluate(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the auto-approve policy to one seen card. In this build every path ends in a
    log line, never a press: decision is would_press or held, with the reason."""
    what = entry.get("what") or ""
    labels = list(entry.get("buttons") or {})
    button = _core._pick_button(labels)
    entry = {k: v for k, v in entry.items() if k != "buttons"}
    entry.update({"button": button, "pressed": False, "decision": "held", "reason": ""})
    if "approval has expired" in what.lower():
        entry["button"], entry["reason"] = None, "expired before Xod got to it"
    elif entry.get("kind") == "bubble":
        allowlist = _bubble_allowlist()
        if entry.get("origin") and entry["origin"] in allowlist and button:
            entry["decision"] = "would_press"
            entry["reason"] = "dry run: would press %s (origin is on the bubble allowlist)" % button
        else:
            entry["reason"] = ("bubble held: %s is not on the bubble allowlist "
                                "(an Allow press is a durable site grant)"
                                % (entry.get("origin") or "unknown origin"))
    elif button is None:
        entry["reason"] = _core._no_button_reason(labels)
    else:
        warning = _safety_warning(what)
        protected = next((rx for rx in PROTECTED_RES if re.search(rx, what, re.I)), None)
        if warning:
            entry["reason"] = f"safety check: {warning}"
        elif protected:
            entry["reason"] = "protected action (matches %r): waits for you" % protected
        elif not _core.enabled():
            entry["reason"] = "Muse approvals are off or paused"
        elif not _core.active():
            entry["reason"] = "away mode: you're at this computer"
        else:
            entry["decision"] = "would_press"
            entry["reason"] = "dry run: would press %s" % button
    return entry


def _bubble_allowlist() -> List[str]:
    try:
        from app.services.storage import get_setting
        raw = json.loads(get_setting(ALLOWLIST_SETTING, "[]"))
        return [str(x) for x in raw] if isinstance(raw, list) else []
    except Exception:
        return []


# ------------------------------------------------------------------ the record
def _card_id(entry: Dict[str, Any]) -> str:
    full = "%s\n%s\n%s" % (entry.get("kind") or "", entry.get("origin") or "", entry.get("what") or entry.get("title") or "")
    return "musechrome-" + hashlib.sha256(full.encode()).hexdigest()[:16]


def _log(entry: Dict[str, Any]) -> bool:
    """One approvals-history row per card, ever, in the same history as Muse app cards."""
    pid = _card_id(entry)
    if pid in _state["logged_ids"]:
        return False
    try:
        from app.services import agent_log
        if any(r["id"] == pid for r in agent_log.approvals(500) if r.get("kind") == "muse_chrome"):
            _state["logged_ids"].add(pid)
            return False
        lines = [f"kind: {entry.get('kind') or 'panel'}", f"button: {entry.get('button') or '(none)'}",
                 f"decision: {entry.get('decision') or 'held'}", "route: windows UIA tree (dry run)"]
        if entry.get("origin"):
            lines.append(f"origin: {entry['origin']}")
        if entry.get("permission"):
            lines.append(f"permission: {entry['permission']}")
        if entry.get("reason"):
            lines.append(f"reason: {entry['reason']}")
        lines += ["---", entry.get("what") or entry.get("title") or "an approval"]
        headline = (entry.get("what") or entry.get("title") or "Chrome extension approval")[:120]
        agent_log.approval_opened(pid, "muse_chrome", "Muse (Chrome extension)", headline, "\n".join(lines))
        agent_log.approval_decided(pid, "would press (dry run): %s" % entry["button"]
                                   if entry.get("decision") == "would_press"
                                   else f"not pressed: {entry.get('reason') or 'held'}")
    except Exception as e:
        logger.debug(f"chrome extension approval not logged: {e}")
        return False
    _state["logged_ids"].add(pid)
    _state["last"] = {"what": entry.get("what") or entry.get("title"), "at": time.time(),
                      "decision": entry.get("decision"), "button": entry.get("button")}
    logger.info(f"Chrome extension approval {entry.get('decision')}: {(entry.get('what') or '')[:160]}")
    return True


# ------------------------------------------------------------------ one scan
def scan(ax=None, pid: Optional[int] = None) -> Dict[str, Any]:
    """One read of Chrome's window: warm-up check, panel cards, bubbles, decisions, logging.
    Never presses. ax/pid are injectable for tests; the defaults reach the real Chrome."""
    ax = ax or _ax_api()
    if pid is None:
        pid = _chrome_pid()
    if pid is None:
        return {"running": False, "cards": [], "bubbles": []}
    root = ax.create_app(pid)
    nodes = _count_nodes(ax, root)
    panels = _find_by_class(ax, root, PANEL_CLASS, prune_pages=True)
    bubble_panes = _find_by_class(ax, root, BUBBLE_CLASS, prune_pages=False)
    if not panels and not bubble_panes and nodes < WARM_MIN_NODES:
        # Chrome builds its web tree lazily; a cold walk sees an empty shell with neither
        # surface in it. Say so and let the watcher's next look retry, rather than reporting
        # a card-free Chrome.
        _state["warmed"] = False
        return {"running": True, "cold": True, "cards": [], "bubbles": []}
    _state["warmed"] = True
    cards: List[Dict[str, Any]] = []
    signed_out = False
    for panel in panels:
        texts = _subtree_text(ax, panel)
        if any(re.search(rx, t, re.I) for rx in CARD_PATTERNS["signed_out_res"] for t in texts):
            signed_out = True
        cards.extend(_panel_cards(ax, panel))
    _state["signed_out"] = signed_out and not cards
    bubbles = _bubbles(ax, root, bubble_panes)
    decided = [_evaluate(c) for c in cards]
    decided_bubbles = [_evaluate(b) for b in bubbles]
    for entry in decided + decided_bubbles:
        _log(entry)
    # Best effort escalation (2026-11-22): this watcher is dry-run by
    # design, so a card it decides it WOULD press is one it can see but
    # cannot press. Those escalate to Tree, once per showing, through
    # services/muse_escalate.py. Cards held under this watcher's own
    # policy (protected, safety, unlisted bubble origin) do not
    # escalate; they stay logged as before. Expiry runs only on a
    # completed scan like this one: a cold read proves nothing, so it
    # never reaches here and never expires a showing.
    from app.services import muse_escalate
    for raw, entry in zip(cards + bubbles, decided + decided_bubbles):
        if entry.get("decision") == "would_press":
            muse_escalate.escalate(
                "chrome_card_unpressable", _card_id(entry), surface="chrome_extension",
                title=entry.get("title") or "", text=entry.get("what") or "",
                options=list((raw.get("buttons") or {}).keys()), button=entry.get("button"),
                tried=["read the card in the Chrome extension surface (Windows UIA)",
                       f"policy decision: would press {entry.get('button')}"],
                reason="the Chrome extension watcher is dry-run in this build: it can "
                       "see this card and knows the approving button, but it never "
                       "presses; Tree's hands take it from here",
                extra={"origin": entry.get("origin") or "",
                       "permission": entry.get("permission") or ""})
    live_ids = {_card_id(e) for e in decided + decided_bubbles}
    for stale in [i for i in _state["present_ids"] if i not in live_ids]:
        muse_escalate.expire(stale)
    _state["present_ids"] = live_ids
    _state["cards_seen"] += len(decided)
    _state["bubbles_seen"] += len(decided_bubbles)
    _state["would_press"] += sum(1 for e in decided + decided_bubbles if e["decision"] == "would_press")
    _state["held"] += sum(1 for e in decided + decided_bubbles if e["decision"] == "held")
    return {"running": True, "cold": False, "cards": decided, "bubbles": decided_bubbles,
            "signed_out": _state["signed_out"]}


# ------------------------------------------------------------------ status + watcher
def status() -> Dict[str, Any]:
    b = _backends.backend()
    task = _state.get("task")
    out = {"target": "chrome_extension", "label": "Muse Chrome extension (dry run)",
           "watching": bool(task is not None and not task.done()),
           "dry_run": True, "cards_seen": _state["cards_seen"],
           "would_press": _state["would_press"], "held": _state["held"],
           "bubbles_seen": _state["bubbles_seen"], "last": _state["last"],
           "signed_out": _state["signed_out"], "error": _state.get("error")}
    if b is None or b.name != "win_uia":
        out["notice"] = "The Chrome extension watcher runs on Windows in this build; the Mac route waits on AGE-57."
    elif _state["signed_out"]:
        out["notice"] = "The Muse extension is signed out in Chrome, so no approval cards can appear."
    else:
        out["notice"] = "Dry run: extension cards are read and logged, never pressed."
    return out


async def _watch() -> None:
    _state["loop"] = asyncio.get_running_loop()
    while True:
        try:
            if _core.enabled():
                pid = await asyncio.to_thread(_chrome_pid)
                now = time.time()
                if pid is not None and now - _state["backstop_at"] >= BACKSTOP_SEC:
                    _state["backstop_at"] = now
                    await asyncio.to_thread(scan, None, pid)
                    _state["error"] = None
            else:
                _state["error"] = None
        except Exception as e:
            _state["error"] = f"Can't reach Chrome's approval cards: {str(e)[:200]}"
            logger.warning(f"Chrome extension watcher: {e}")
        await asyncio.sleep(1)


def start() -> None:
    from app.services import run_mode
    if run_mode.skip("muse_app"):
        return
    b = _backends.backend()
    if b is None or b.name != "win_uia":
        return  # Windows only in this build; see the module docstring
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    t = _state["task"]
    if t is None or t.done():
        _state["task"] = asyncio.create_task(_watch())
