"""
WhatsApp messaging: Xod sends and reads WhatsApp messages through the
owner's own signed-in surfaces. Spec: XOD-WHATSAPP-MESSAGING-SPEC-2026-11-06
(in the xod-eval records).

Why this exists: the WhatsApp connection could surface chats to read but
gave the agent no way to answer anyone, which made the connection a
window instead of a tool. This module is the send path, built the way
services/social_reply.py is built, because the shape is the same: drafts,
an owner approval, one supervised executor step, and verification at the
destination before anything is called sent.

Surfaces: WhatsApp lives where the owner is signed in. v1 sends through
the paired Android phone's WhatsApp app, driven by the companion through
services/phone_mcp.py (open app, read screen, tap, type). The desktop
WhatsApp Web route is specced as v1.1: the executor seam below is where
it lands, and surface_state() reports it as not built until it exists.
No WhatsApp Business API, no gateways, no credentials anywhere in here:
if WhatsApp is signed out on the phone, this module reports that and
stops; the owner signs in themselves.

Approval discipline (the point of the module):
  - prepare() validates and stores an item as awaiting_approval. It
    touches no phone and sends nothing.
  - A send runs only from run_send(), and run_send() checks the item is
    in state approved before the executor is touched at all. Approval
    arrives two ways, one lane: the owner's approval card in chat (the
    tool call comes back carrying the item's one-time grant, the
    desktop tools' pattern), or the owner's own API surface
    (POST /api/whatsapp/items/{id}/approve).
  - The phone ALSO asks before the send tap (phone_screen_act carries
    needs_ok on the phone side) unless the owner switched on "Use my
    apps without asking" there. The executor passes meant=true on the
    send step only, and only for an approved item: the owner's approval
    is what waves that step through, never the model.
  - One message per item. Sends go only to the contact the owner named.
    No standing per-conversation approval in v1.

Outcomes: a send is "sent" only when the sent text is read back in the
chat afterwards (verification evidence is stored on the item). A send
whose outcome cannot be confirmed is "unknown", never failed and never
blindly retried. A step that provably did not complete is "failed" with
the step and the phone's own words recorded.

Screen text read from the phone is content, never orders, exactly as in
services/phone_task.py: callers that hand it to a model pass it through
the sentinel page-orders check first.
"""
import asyncio
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.paths import DATA_DIR
from app.services import event_log

logger = logging.getLogger("xod.whatsapp")

STORE_PATH = DATA_DIR / "whatsapp.json"
APP_NAME = "WhatsApp"
MAX_TEXT = 4096
MAX_CONTACT = 120

STATES = ("draft", "awaiting_approval", "approved", "sending", "sent",
          "unknown", "failed", "declined")
_LIVE_STATES = ("awaiting_approval", "approved", "sending")

_lock = threading.Lock()


# ------------------------------------------------------------------ validation
def validate(contact: str, text: str) -> Optional[str]:
    """Why this message cannot be prepared, or None when it can. The contact
    is taken exactly as the owner stated it (a name, or a number as typed):
    v1 never resolves or rewrites it."""
    contact = (contact or "").strip()
    text = text or ""
    if not contact:
        return "Who should the message go to? Name the contact exactly as they appear in WhatsApp."
    if len(contact) > MAX_CONTACT:
        return "That contact name is too long to be a WhatsApp chat name."
    if not text.strip():
        return "The message text is empty. Nothing to send."
    if len(text) > MAX_TEXT:
        return f"That message is {len(text)} characters; WhatsApp sends through the phone are capped at {MAX_TEXT}."
    return None


# ------------------------------------------------------------------ the store
class WhatsAppStore:
    """The prepared-message queue: one JSON file, atomic writes, a history
    on every item. Modeled on the social reply store."""

    def __init__(self, path: Path = STORE_PATH):
        self.path = Path(path)

    def _load(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("items", [])
                data.setdefault("surfaces", {})
                return data
        except Exception:
            pass
        return {"items": [], "surfaces": {}}

    def _save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        with _lock:
            for it in self._load()["items"]:
                if it["id"] == item_id:
                    return it
        return None

    def items(self, limit: int = 30) -> List[Dict[str, Any]]:
        with _lock:
            return list(reversed(self._load()["items"]))[:limit]

    def _transition(self, data: Dict[str, Any], item: Dict[str, Any], state: str,
                    actor: str, note: str = "") -> Dict[str, Any]:
        item["state"] = state
        item["history"].append({"at": time.time(), "state": state, "actor": actor or "", "note": note})
        self._save(data)
        event_log.emit(f"whatsapp.{state}", actor=actor or "", ids={"item": item["id"]},
                       payload={"contact": item["contact"], "note": note[:200]})
        return item

    def prepare(self, contact: str, text: str, phone: str = "", actor: str = "") -> Dict[str, Any]:
        """Store a message as awaiting_approval. Sends nothing, touches no
        phone. An identical live item (same contact and text still waiting)
        is returned instead of a duplicate."""
        err = validate(contact, text)
        if err:
            return {"success": False, "error": err}
        contact = contact.strip()
        with _lock:
            data = self._load()
            for it in data["items"]:
                if (it["state"] in _LIVE_STATES and it["contact"].lower() == contact.lower()
                        and it["text"] == text and (it.get("phone") or "") == (phone or "")):
                    return {"success": True, "duplicate": True, "item": it}
            item = {"id": uuid.uuid4().hex[:12], "created_at": time.time(),
                    "contact": contact, "text": text, "phone": phone or "",
                    "route": "phone", "state": "draft",
                    "grant": uuid.uuid4().hex, "verification": None,
                    "history": []}
            data["items"].append(item)
            self._transition(data, item, "awaiting_approval", actor, "prepared")
            return {"success": True, "duplicate": False, "item": item}

    def approve(self, item_id: str, actor: str = "") -> Dict[str, Any]:
        with _lock:
            data = self._load()
            for it in data["items"]:
                if it["id"] == item_id:
                    if it["state"] != "awaiting_approval":
                        return {"success": False, "error": f"That message is {it['state']}, not waiting for approval."}
                    self._transition(data, it, "approved", actor, "approved")
                    return {"success": True, "item": it}
        return {"success": False, "error": "No such WhatsApp message."}

    def approve_with_grant(self, item_id: str, grant: str, actor: str = "") -> Dict[str, Any]:
        """The chat approval card's path: the grant minted at prepare time
        must come back with the call. A wrong grant approves nothing."""
        with _lock:
            data = self._load()
            for it in data["items"]:
                if it["id"] == item_id:
                    if not grant or grant != it.get("grant"):
                        return {"success": False, "error": "That approval does not match this message. Nothing was approved."}
                    if it["state"] != "awaiting_approval":
                        return {"success": False, "error": f"That message is {it['state']}, not waiting for approval."}
                    self._transition(data, it, "approved", actor, "approved on the card")
                    return {"success": True, "item": it}
        return {"success": False, "error": "No such WhatsApp message."}

    def decline(self, item_id: str, actor: str = "") -> Dict[str, Any]:
        with _lock:
            data = self._load()
            for it in data["items"]:
                if it["id"] == item_id:
                    if it["state"] not in ("awaiting_approval", "approved"):
                        return {"success": False, "error": f"That message is {it['state']} and cannot be declined now."}
                    self._transition(data, it, "declined", actor, "declined")
                    return {"success": True, "item": it}
        return {"success": False, "error": "No such WhatsApp message."}

    def set_state(self, item_id: str, state: str, actor: str = "", note: str = "",
                  verification: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        with _lock:
            data = self._load()
            for it in data["items"]:
                if it["id"] == item_id:
                    if verification is not None:
                        it["verification"] = verification
                    return self._transition(data, it, state, actor, note)
        return None

    # -------------------------------------------------- surface observations
    def record_observation(self, address: str, installed: Optional[bool] = None,
                           signed_in: Optional[bool] = None) -> None:
        """What a read or send run actually saw on a phone, kept so the
        capability manifest reports observed facts with their time, not
        guesses."""
        if not address:
            return
        with _lock:
            data = self._load()
            surf = data["surfaces"].setdefault(address.lower(), {})
            if installed is not None:
                surf["installed"] = bool(installed)
            if signed_in is not None:
                surf["signed_in"] = bool(signed_in)
            surf["observed_at"] = time.time()
            self._save(data)

    def observations(self) -> Dict[str, Any]:
        with _lock:
            return dict(self._load()["surfaces"])


_store: Optional[WhatsAppStore] = None


def default_store() -> WhatsAppStore:
    global _store
    if _store is None:
        _store = WhatsAppStore()
    return _store


def queue_summary(store: Optional[WhatsAppStore] = None) -> Dict[str, Any]:
    store = store or default_store()
    counts: Dict[str, int] = {}
    for it in store.items(limit=200):
        counts[it["state"]] = counts.get(it["state"], 0) + 1
    return {"counts": counts, "items": store.items(limit=10)}


# ------------------------------------------------------------------ screen parsing (pure)
_ELEMENT_RE = re.compile(r'\[(\d+)\]\s+(\S+)\s+"((?:[^"\\]|\\.)*)"\s*@(\d+)\s*,\s*(\d+)')
_CHROME_LABELS = {"send", "type a message", "message", "search", "whatsapp",
                  "more options", "attach", "camera", "voice message", "back"}
_SIGNED_OUT_MARKERS = ("agree and continue", "enter your phone number",
                       "welcome to whatsapp", "verify your phone number",
                       "enter your number")


def parse_screen(text: str) -> Dict[str, Any]:
    """The phone's screen read as data: the app line plus the numbered
    elements. Same format services/phone_task.py drives on."""
    text = text or ""
    app = ""
    m = re.search(r"App:\s*(.+)", text)
    if m:
        app = m.group(1).strip()
    elements = []
    for em in _ELEMENT_RE.finditer(text):
        elements.append({"n": int(em.group(1)), "kind": em.group(2),
                         "label": em.group(3).replace('\\"', '"'),
                         "x": int(em.group(4)), "y": int(em.group(5))})
    return {"app": app, "elements": elements}


def _screen_of(result: Dict[str, Any]) -> str:
    """A phone call's answer, reduced to its screen part (act answers carry
    what the step did, then the screen, starting with 'App:')."""
    text = str((result or {}).get("text") or "")
    i = text.find("App: ")
    return text[i:] if i >= 0 else text


def is_composer(el: Dict[str, Any]) -> bool:
    lab = (el.get("label") or "").strip().lower()
    return el.get("kind") == "EditText" or lab in ("type a message", "message")


def is_chat_screen(elements: List[Dict[str, Any]]) -> bool:
    """An open conversation has a message composer; the chat list does not."""
    return any(is_composer(el) for el in elements)


def detect_signed_out(elements: List[Dict[str, Any]]) -> bool:
    for el in elements:
        lab = (el.get("label") or "").lower()
        if any(marker in lab for marker in _SIGNED_OUT_MARKERS):
            return True
    return False


def find_chat_match(elements: List[Dict[str, Any]], contact: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Resolve the owner's contact text to one chat row: ("exact", el),
    ("partial", el), ("ambiguous", None) or ("none", None). Never a guess:
    two plausible rows stop the run."""
    want = (contact or "").strip().lower()
    if not want:
        return "none", None
    rows = [el for el in elements if (el.get("label") or "").strip()
            and not is_composer(el) and (el.get("label") or "").strip().lower() not in _CHROME_LABELS]
    exact = [el for el in rows if el["label"].strip().lower() == want]
    if len(exact) == 1:
        return "exact", exact[0]
    if len(exact) > 1:
        return "ambiguous", None
    partial = [el for el in rows if want in el["label"].strip().lower()
               or el["label"].strip().lower() in want]
    if len(partial) == 1:
        return "partial", partial[0]
    if len(partial) > 1:
        return "ambiguous", None
    return "none", None


def parse_messages(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The open chat's visible messages: [{direction, text}], oldest first.
    Direction is best-effort from the bubble's position (WhatsApp puts the
    owner's bubbles on the right); with no usable position it is 'unknown'
    and the text still counts. The parser never invents a sender."""
    composer = next((el for el in elements if is_composer(el)), None)
    if composer is None:
        return []
    top = min((el["y"] for el in elements), default=0)
    width = max((el["x"] for el in elements), default=0)
    width_est = (width / 0.94) if width else 0
    out = []
    for el in elements:
        lab = (el.get("label") or "").strip()
        if not lab or el is composer:
            continue
        if lab.lower() in _CHROME_LABELS or el["y"] >= composer["y"]:
            continue
        if el["y"] <= top + 5:      # the header band: the chat title, status lines
            continue
        if width_est:
            direction = "outgoing" if el["x"] > width_est * 0.52 else "incoming"
        else:
            direction = "unknown"
        out.append({"direction": direction, "text": lab})
    return out


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def verify_sent(before: List[Dict[str, Any]], after: List[Dict[str, Any]],
                sent_text: str, excerpt: str = "") -> Dict[str, Any]:
    """Did the sent text land in the chat? Verified when the exact text
    appears after the send as the last message, as an outgoing bubble, or
    more times than it appeared before. The evidence is returned either
    way; unverified is a state, not an error to retry against."""
    want = _norm(sent_text)
    matches = [m for m in after if _norm(m["text"]) == want]
    if not matches:
        return {"verified": False, "matched": None, "direction": None,
                "reason": "the sent text was not seen in the chat after sending",
                "excerpt": excerpt[:600]}
    before_count = sum(1 for m in before if _norm(m["text"]) == want)
    chosen = matches[-1]
    is_last = bool(after) and chosen is after[-1]
    verified = is_last or chosen["direction"] == "outgoing" or len(matches) > before_count
    return {"verified": bool(verified), "matched": chosen["text"],
            "direction": chosen["direction"],
            "reason": "the sent text was read back in the chat" if verified
                      else "the text is in the chat but could not be told apart from an earlier identical message",
            "excerpt": excerpt[:600]}


# ------------------------------------------------------------------ executors
class Executor:
    """One seam, like social_reply's: the phone executor in v1, a fixture
    executor in tests, the WhatsApp Web executor in v1.1."""

    async def send_message(self, item: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def read_chat(self, contact: str, phone: str = "") -> Dict[str, Any]:
        raise NotImplementedError


class PhoneExecutor(Executor):
    """Drives the paired phone's WhatsApp with deterministic steps over
    phone_mcp (no model in the loop): open the app, open the named chat,
    snapshot it, type the approved text, tap send, read it back."""

    def __init__(self, store: Optional[WhatsAppStore] = None):
        self.store = store or default_store()

    # ------------------------------------------------------------ phone setup
    def _phone(self, address: str = "") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        from app.services import phone_mcp
        phones = phone_mcp.paired_phones()
        if not phones:
            return None, "No phone is paired. Pair one in the Xod app on the phone."
        target = next((p for p in phones if p["address"].lower() == (address or "").lower()), None)
        if address and not target:
            return None, f"{address} isn't a paired phone."
        target = target or phones[0]
        try:
            from app.services import phone_enroll
            if phone_enroll.control_refused(target.get("address") or ""):
                return None, ("That phone was revoked from the family on this computer, so Xod won't "
                              "control it. Re-enroll it from the Phones panel to use it again.")
        except Exception:
            pass
        return target, None

    def _caps_error(self, target: Dict[str, Any], need_tap: bool) -> Optional[str]:
        from app.services import phone_mcp
        caps = target.get("capabilities") or {}
        need = ["apps", "screen"] + (["tap"] if need_tap else [])
        off = [phone_mcp.SWITCH_LABELS[c] for c in need if caps.get(c) is not True]
        if off:
            return (f"To use WhatsApp on the phone, Xod needs these switched on in the Xod app "
                    f"on the phone (Phone tab): {', '.join(off)}.")
        return None

    # ------------------------------------------------------------ chat opening
    async def _open_chat(self, target: Dict[str, Any], contact: str) -> Dict[str, Any]:
        """Open WhatsApp and then the named chat. Returns {success, screen,
        messages} or {success: False, error, stage: 'pre_send', observed}."""
        from app.services import phone_mcp
        address = target["address"]
        observed: Dict[str, Any] = {}
        res = await phone_mcp.call_tool("phone_open_app", address, app=APP_NAME)
        if not res.get("success"):
            err = str(res.get("error") or res.get("text") or "WhatsApp did not open")
            if "not installed" in err.lower() or "not found" in err.lower() or "no app" in err.lower():
                observed["installed"] = False
            return {"success": False, "stage": "pre_send", "observed": observed,
                    "error": f"WhatsApp did not open on the phone: {err}"}
        observed["installed"] = True
        read = await phone_mcp.call_tool("phone_screen_read", address)
        if not read.get("success"):
            return {"success": False, "stage": "pre_send", "observed": observed,
                    "error": read.get("error") or "Couldn't read the phone's screen."}
        screen = parse_screen(_screen_of(read))
        if detect_signed_out(screen["elements"]):
            observed["signed_in"] = False
            return {"success": False, "stage": "pre_send", "observed": observed,
                    "error": "WhatsApp on the phone is signed out. Sign in on the phone first; Xod never handles WhatsApp sign-ins."}
        observed["signed_in"] = True
        if is_chat_screen(screen["elements"]):
            kind, _ = find_chat_match(screen["elements"], contact)
            if kind in ("exact", "partial"):
                return {"success": True, "screen": screen, "observed": observed,
                        "screen_text": _screen_of(read)}
        # From the chat list: search for the chat by the owner's exact words.
        search = next((el for el in screen["elements"]
                       if "search" in (el.get("label") or "").lower()), None)
        if search is None:
            return {"success": False, "stage": "pre_send", "observed": observed,
                    "error": "WhatsApp opened but its search was not on screen, so the chat could not be opened."}
        res = await phone_mcp.call_tool("phone_screen_act", address, do="tap", target=search["n"])
        if not res.get("success"):
            return {"success": False, "stage": "pre_send", "observed": observed,
                    "error": res.get("error") or "Tapping WhatsApp search failed."}
        screen = parse_screen(_screen_of(res))
        field = next((el for el in screen["elements"] if el.get("kind") == "EditText"), None) \
            or next((el for el in screen["elements"] if "search" in (el.get("label") or "").lower()), None)
        if field is None:
            return {"success": False, "stage": "pre_send", "observed": observed,
                    "error": "WhatsApp search opened but its text field was not found."}
        res = await phone_mcp.call_tool("phone_screen_act", address, do="type",
                                        target=field["n"], text=contact)
        if not res.get("success"):
            return {"success": False, "stage": "pre_send", "observed": observed,
                    "error": res.get("error") or "Typing the chat name into WhatsApp search failed."}
        await asyncio.sleep(1.0)          # let the results settle before reading them
        read = await phone_mcp.call_tool("phone_screen_read", address)
        if not read.get("success"):
            return {"success": False, "stage": "pre_send", "observed": observed,
                    "error": read.get("error") or "Couldn't read WhatsApp's search results."}
        screen = parse_screen(_screen_of(read))
        kind, row = find_chat_match(screen["elements"], contact)
        if kind == "none":
            return {"success": False, "stage": "pre_send", "observed": observed,
                    "error": f"No WhatsApp chat matched '{contact}'. Nothing was sent."}
        if kind == "ambiguous":
            return {"success": False, "stage": "pre_send", "observed": observed,
                    "error": f"More than one WhatsApp chat could be '{contact}'. Name the chat more exactly; nothing was sent."}
        res = await phone_mcp.call_tool("phone_screen_act", address, do="tap", target=row["n"])
        if not res.get("success"):
            return {"success": False, "stage": "pre_send", "observed": observed,
                    "error": res.get("error") or "Opening the chat failed."}
        screen = parse_screen(_screen_of(res))
        if not is_chat_screen(screen["elements"]):
            read = await phone_mcp.call_tool("phone_screen_read", address)
            if read.get("success"):
                screen = parse_screen(_screen_of(read))
        if not is_chat_screen(screen["elements"]):
            return {"success": False, "stage": "pre_send", "observed": observed,
                    "error": "The chat did not open to a conversation screen. Nothing was sent."}
        return {"success": True, "screen": screen, "observed": observed,
                "screen_text": _screen_of(res)}

    # ------------------------------------------------------------ the two jobs
    async def read_chat(self, contact: str, phone: str = "") -> Dict[str, Any]:
        target, err = self._phone(phone)
        if err:
            return {"success": False, "error": err}
        err = self._caps_error(target, need_tap=True)
        if err:
            return {"success": False, "error": err}
        opened = await self._open_chat(target, contact)
        self.store.record_observation(target["address"], **opened.get("observed") or {})
        if not opened["success"]:
            return {"success": False, "error": opened["error"]}
        elements = opened["screen"]["elements"]
        return {"success": True, "contact": contact, "phone": target["address"],
                "messages": parse_messages(elements),
                "excerpt": (opened.get("screen_text") or "")[:2000]}

    async def send_message(self, item: Dict[str, Any]) -> Dict[str, Any]:
        from app.services import phone_mcp
        target, err = self._phone(item.get("phone") or "")
        if err:
            return {"stage": "pre_send", "detail": err}
        err = self._caps_error(target, need_tap=True)
        if err:
            return {"stage": "pre_send", "detail": err}
        address = target["address"]
        opened = await self._open_chat(target, item["contact"])
        self.store.record_observation(address, **opened.get("observed") or {})
        if not opened["success"]:
            return {"stage": "pre_send", "detail": opened["error"]}
        elements = opened["screen"]["elements"]
        before = parse_messages(elements)
        composer = next(el for el in elements if is_composer(el))
        res = await phone_mcp.call_tool("phone_screen_act", address, do="type",
                                        target=composer["n"], text=item["text"])
        if not res.get("success"):
            return {"stage": "pre_send", "detail": res.get("error") or "Typing the message failed. Nothing was sent."}
        screen = parse_screen(_screen_of(res))
        send_btn = next((el for el in screen["elements"]
                         if (el.get("label") or "").strip().lower() == "send"), None)
        if send_btn is None:      # the type answer may predate the button swap; read once more
            read = await phone_mcp.call_tool("phone_screen_read", address)
            if read.get("success"):
                screen = parse_screen(_screen_of(read))
                send_btn = next((el for el in screen["elements"]
                                 if (el.get("label") or "").strip().lower() == "send"), None)
        if send_btn is None:
            return {"stage": "pre_send", "detail": "The message was typed but WhatsApp's send button was not on screen. Nothing was sent."}
        # The one step that reaches another person. meant=true is passed only
        # from here, and run_send only gets here for an approved item.
        res = await phone_mcp.call_tool("phone_screen_act", address, do="tap",
                                        target=send_btn["n"], meant=True)
        if not res.get("success"):
            return {"stage": "sent_unverified",
                    "detail": res.get("error") or "The send tap did not answer cleanly; the message may or may not have gone.",
                    "evidence": {"verified": False, "matched": None, "direction": None,
                                 "reason": "the send tap's answer was not a success", "excerpt": ""}}
        read = await phone_mcp.call_tool("phone_screen_read", address)
        if not read.get("success"):
            return {"stage": "sent_unverified",
                    "detail": "The message was sent but the chat could not be read back to verify it.",
                    "evidence": {"verified": False, "matched": None, "direction": None,
                                 "reason": "the verification read failed", "excerpt": ""}}
        after_screen = _screen_of(read)
        after = parse_messages(parse_screen(after_screen)["elements"])
        evidence = verify_sent(before, after, item["text"], excerpt=after_screen)
        if evidence["verified"]:
            return {"stage": "verified", "detail": "Sent and read back in the chat.", "evidence": evidence}
        return {"stage": "sent_unverified",
                "detail": "The send tap completed but the message could not be confirmed in the chat.",
                "evidence": evidence}


class FixtureExecutor(Executor):
    """Replays scripted outcomes for tests and records every call, so the
    approval gate can be PROVEN: an unapproved run must show zero calls."""

    def __init__(self, send_result: Optional[Dict[str, Any]] = None,
                 read_result: Optional[Dict[str, Any]] = None):
        self.calls: List[Tuple[str, Any]] = []
        self.send_result = send_result or {"stage": "verified", "detail": "Sent and read back in the chat.",
                                           "evidence": {"verified": True, "matched": None,
                                                        "direction": "outgoing", "reason": "fixture",
                                                        "excerpt": ""}}
        self.read_result = read_result or {"success": True, "contact": "", "messages": [], "excerpt": ""}

    async def send_message(self, item: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append(("send_message", item["id"]))
        out = dict(self.send_result)
        if out.get("evidence") and out["evidence"].get("matched") is None:
            out["evidence"] = {**out["evidence"], "matched": item["text"]}
        return out

    async def read_chat(self, contact: str, phone: str = "") -> Dict[str, Any]:
        self.calls.append(("read_chat", contact))
        return {**self.read_result, "contact": contact}


# ------------------------------------------------------------------ the flows
async def run_send(item_id: str, actor: str = "", executor: Optional[Executor] = None,
                   store: Optional[WhatsAppStore] = None) -> Dict[str, Any]:
    """The only door to a send. The approval check comes FIRST, before the
    executor exists in this call: an item that is not in state approved
    returns here and no executor method is ever invoked."""
    store = store or default_store()
    item = store.get(item_id)
    if not item:
        return {"success": False, "error": "No such WhatsApp message."}
    if item["state"] != "approved":
        return {"success": False, "sent": False,
                "error": f"That message is {item['state']}, not approved. Nothing was sent."}
    store.set_state(item_id, "sending", actor, "send started")
    executor = executor or PhoneExecutor(store)
    try:
        out = await executor.send_message(item)
    except Exception as e:
        logger.warning(f"WhatsApp send for item {item_id} raised: {e}")
        store.set_state(item_id, "unknown", actor, f"the send raised: {e}")
        return {"success": False, "sent": False, "unknown": True,
                "error": f"The send did not finish cleanly ({e}); its outcome is unknown. Nothing was retried."}
    stage = out.get("stage")
    if stage == "verified":
        store.set_state(item_id, "sent", actor, out.get("detail") or "sent, verified",
                        verification=out.get("evidence"))
        return {"success": True, "sent": True, "verified": True, "item": store.get(item_id)}
    if stage == "sent_unverified":
        store.set_state(item_id, "unknown", actor, out.get("detail") or "sent, unverified",
                        verification=out.get("evidence"))
        return {"success": False, "sent": False, "unknown": True,
                "error": out.get("detail") or "The message may have been sent; it could not be verified. Nothing was retried."}
    store.set_state(item_id, "failed", actor, out.get("detail") or "the send did not start")
    return {"success": False, "sent": False, "error": out.get("detail") or "The send did not start."}


async def read_chat(contact: str, phone: str = "", executor: Optional[Executor] = None,
                    store: Optional[WhatsAppStore] = None) -> Dict[str, Any]:
    """Recent messages from a named chat, for drafting a reply with
    context. Reading sends nothing and needs no approval card."""
    contact = (contact or "").strip()
    if not contact:
        return {"success": False, "error": "Which chat should Xod read? Name the contact."}
    executor = executor or PhoneExecutor(store or default_store())
    return await executor.read_chat(contact, phone)


# ------------------------------------------------------------------ surface state (for the manifest)
def surface_state(store: Optional[WhatsAppStore] = None,
                  phones: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """What the manifest reports about WhatsApp, per surface, from facts:
    pairing and switches from the phones roster, installed and signed_in
    from what runs on those phones actually observed (null until a run
    observes them). can_read and can_send are computed, never asserted."""
    store = store or default_store()
    if phones is None:
        try:
            from app.services import phone_mcp
            phones = phone_mcp.phone_list().get("phones") or []
        except Exception:
            phones = []
    observed = store.observations()
    out_phones = []
    for ph in phones:
        address = (ph.get("address") or "").lower()
        if not address or address == "iphone-mirroring":
            continue
        caps = ph.get("capabilities") or {}
        obs = observed.get(address) or {}
        installed = obs.get("installed")
        signed_in = obs.get("signed_in")
        online = bool(ph.get("online"))
        known_blocked = installed is False or signed_in is False
        can_read = bool(online and caps.get("apps") is True and caps.get("screen") is True
                        and not known_blocked)
        can_send = bool(can_read and caps.get("tap") is True)
        out_phones.append({"address": ph.get("address"),
                           "name": ph.get("nickname") or ph.get("model") or ph.get("address"),
                           "online": online, "installed": installed, "signed_in": signed_in,
                           "observed_at": obs.get("observed_at"),
                           "can_read": can_read, "can_send": can_send, "route": "phone"})
    return {"whatsapp": {
                "route": "phone",
                "send_route": "phone" if any(p["can_send"] for p in out_phones) else None,
                "can_read": any(p["can_read"] for p in out_phones),
                "can_send": any(p["can_send"] for p in out_phones),
                "phones": out_phones},
            "desktop": {"route": "whatsapp_web", "built": False,
                        "can_read": False, "can_send": False}}
