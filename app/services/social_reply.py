"""
Social reply: Xod's own queue for replying to people on X and LinkedIn.

Why this exists: an agent's X and LinkedIn work otherwise rides the owner's
Chrome by hand, one shared window, dropped sessions, cards timing out. This
module makes replying a first-class Xod capability instead. The queue, the
drafts, the copy rules and the audit live here on the machine that holds the
signed-in browser session; the browser work is one supervised executor step.

Shape:
  - A JSON store in DATA_DIR (social_reply.json, written atomically) holds
    one item per inbound thing worth a reply: platform, kind, target id and
    URL, author, the inbound text, the draft, its computed thread parts, and
    a state (draft, awaiting_approval, approved, posting, posted, verified,
    unknown, failed) with a full history.
  - The copy rules are code, not prompts: validate_draft refuses em dashes
    and en dashes, the owner's reserved signature (the sign-off that means the owner typed it personally), and any email
    or phone number shape (a public reply never needs either, and no private
    identifier of the owner's is stored here to check against). split_for_x
    enforces the free account's 280 characters by splitting only at
    paragraph boundaries, words unedited; a single paragraph over the limit
    is refused, never cut. reaction_photo_allowed encodes the LinkedIn
    exclusions (Mariane Bekker, Founders Bay, You.com, 1Password stay
    text-only and professional).
  - Posting a reply is a consequential send. The default is human approval
    on the app's own surface; a per-platform auto mode exists behind a
    setting and defaults off; a dry-run setting defaults ON for v1, running
    the full path and stopping before the final post click.
  - A post whose outcome is not known is state unknown, never failed and
    never blindly retried: the runner verifies at the destination first and
    reposts only when the reply is proven absent (confirm-before-relaunch).
  - Executors sit behind one seam. FixtureExecutor replays fixture pages
    for tests. DryRunExecutor wraps any executor and intercepts the post.
    MacChromeExecutor drives the owner's own signed-in Chrome through the
    desktop control service (AppleScript and AX): no cloud browser, no
    credential handling, no API token posting route for X, per the owner's
    standing rule that X posts go through his Mac Chrome only.
  - Every transition emits an event log record under the "social" kind
    prefix with the actor named, and the harness snapshot and the
    social_read tool read the queue summary from here.

Nothing in this module holds a secret: no credentials, no tokens, no
cookies. The browser session never leaves the device.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.paths import DATA_DIR
from app.services import event_log

logger = logging.getLogger("xod.social_reply")

PLATFORMS = ("x", "linkedin")
KINDS = ("mention", "comment", "dm", "notification")
STATES = ("draft", "awaiting_approval", "approved", "posting", "posted",
          "verified", "unknown", "failed")
X_LIMIT = 280
STORE_NAME = "social_reply.json"

# Conservative per platform posting limits, persisted in the store's
# post_log so a restart does not reset them.
RATE_LIMITS = {
    "x": {"min_gap_sec": 120, "per_day": 20},
    "linkedin": {"min_gap_sec": 180, "per_day": 15},
}

# LinkedIn authors and contexts where replies stay text-only and
# professional; reaction photos are refused for these keys.
REACTION_EXCLUDED = ("Mariane Bekker", "Founders Bay", "You.com", "1Password")

NOTIFICATIONS_URL = {
    "x": "https://x.com/notifications",
    "linkedin": "https://www.linkedin.com/notifications/",
}

# Frontmost apps that mean the machine is in a call or a share; the Mac
# executor reports busy and touches nothing while one of these is up.
CALL_APPS = {"zoom.us", "Microsoft Teams", "FaceTime", "Cisco Webex Meetings"}
MEETING_URL_HINTS = ("meet.google.com", "zoom.us/j/", "teams.microsoft.com")

# The owner's reserved sign-off ("- <name> the human") is matched by shape, never by a stored
# name: this file ships publicly, and the tag must stay refused in any casing and for any name.
_RESERVED_SIGN_OFF_RE = re.compile(r"-\s*\S+\s+the\s+human\b", re.I)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(\+?1[\s.\-]?)?(\(\d{3}\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}")
_EM_DASH = "—"   # the em dash character itself, refused in drafts
_EN_DASH = "–"   # the en dash character itself, refused in drafts


class ConstraintError(ValueError):
    """A draft or a split breaks one of the encoded copy rules."""


class QueueError(ValueError):
    """A queue operation the store cannot allow (duplicate, bad state)."""


class ExecutorUnavailable(Exception):
    """The browser route cannot run this step right now; the item is not
    at fault and goes back to approved with the reason recorded."""


class ExecutorBusy(ExecutorUnavailable):
    """The machine is in a call or a share; nothing was touched."""


class PostUnknown(Exception):
    """The post click's outcome is not known (timeout, drop). The reply
    may or may not be live; only a destination read may decide."""


# ------------------------------------------------------------ copy rules

def validate_draft(platform: str, text: str) -> str:
    """The encoded copy rules, checked at attach time. Returns the text
    unchanged when it passes; raises ConstraintError naming the rule."""
    if platform not in PLATFORMS:
        raise ConstraintError(f"unknown platform {platform!r}")
    if not (text or "").strip():
        raise ConstraintError("the draft is empty")
    if _EM_DASH in text or _EN_DASH in text:
        raise ConstraintError("reply text may not contain an em dash or en dash")
    if _RESERVED_SIGN_OFF_RE.search(text):
        raise ConstraintError("replies are never signed with the owner's reserved tag; that tag means the owner typed it personally")
    if _EMAIL_RE.search(text):
        raise ConstraintError("a public reply never carries an email address; private data stays out")
    if _PHONE_RE.search(text):
        raise ConstraintError("a public reply never carries a phone number; private data stays out")
    if platform == "x":
        split_for_x(text)   # raises when a single paragraph cannot fit
    return text


def split_for_x(text: str) -> List[str]:
    """Split a reply for the free account's 280 character posts: only at
    paragraph boundaries, words unedited, paragraphs packed in order. A
    single paragraph over the limit raises ConstraintError; it is never
    cut mid-paragraph and no word is ever trimmed or numbered."""
    body = (text or "").strip()
    if len(body) <= X_LIMIT:
        return [body] if body else []
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    parts: List[str] = []
    cur = ""
    for para in paras:
        if len(para) > X_LIMIT:
            raise ConstraintError(
                "one paragraph is over 280 characters and words are never cut; "
                "break it into paragraphs first")
        candidate = para if not cur else cur + "\n\n" + para
        if len(candidate) <= X_LIMIT:
            cur = candidate
        else:
            parts.append(cur)
            cur = para
    if cur:
        parts.append(cur)
    return parts


def normalize_author(name: str) -> str:
    """A comparison key for an author or context name: lowercased, accents
    folded, punctuation turned to spaces, whitespace collapsed."""
    folded = unicodedata.normalize("NFKD", str(name or ""))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = re.sub(r"[^a-z0-9]+", " ", folded.lower())
    return " ".join(folded.split())


_EXCLUDED_KEYS = tuple(normalize_author(n) for n in REACTION_EXCLUDED)


def reaction_photo_allowed(platform: str, author_key: str,
                           context_key: str = "") -> Tuple[bool, str]:
    """Whether a reaction photo may ride a reply. LinkedIn only, and never
    for the excluded authors or contexts, which stay text-only."""
    if platform != "linkedin":
        return False, "reaction photos are a LinkedIn lane only in v1"
    for raw in (author_key, context_key):
        key = normalize_author(raw)
        if not key:
            continue
        for excluded in _EXCLUDED_KEYS:
            if key == excluded or excluded in key or key in excluded:
                return False, (f"{excluded} is on the text-only list: no reaction "
                               "photos, professional text replies only")
    return True, ""


def parse_notifications_title(platform: str, title: str) -> Optional[int]:
    """The unread count a notifications page title carries ("(4) ..."), 0
    when the page is the notifications page with no badge, and None when
    the title is not the notifications page at all (unknown, never 0)."""
    t = (title or "").strip()
    if "notification" not in t.lower():
        return None
    m = re.match(r"\((\d+)\)", t)
    if m:
        return int(m.group(1))
    if t.lower().startswith("notification"):
        return 0
    return None


# ------------------------------------------------------------ settings seam

def _setting(key: str) -> str:
    """A storage setting, or "" when storage is not reachable (tests run
    against a bare store; the app always has its database)."""
    try:
        from app.services import storage
        return storage.get_setting(key, "") or ""
    except Exception:
        return ""


def auto_mode(platform: str) -> bool:
    return _setting(f"social_auto_{platform}").strip().lower() in ("1", "on", "true", "yes")


def dry_run_default() -> bool:
    """Dry-run is the v1 default posture: anything but an explicit off
    keeps it on, so a fresh install cannot post live by accident."""
    return _setting("social_dry_run").strip().lower() not in ("0", "off", "false", "no")


# ------------------------------------------------------------ the store

class SocialStore:
    """The queue, persisted as one JSON file written atomically. One live
    item per platform plus target id, ever: a target gets one reply item,
    and its whole story lives in that item's history."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DATA_DIR / STORE_NAME
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {"items": [], "post_log": {p: [] for p in PLATFORMS}}
        self._load()

    # -- persistence -------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("items"), list):
                self._data = raw
                self._data.setdefault("post_log", {p: [] for p in PLATFORMS})
        except Exception:
            pass    # a missing or unreadable store starts empty, never crashes the lane

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- helpers ------------------------------------------------------
    def _find(self, item_id: str) -> Dict[str, Any]:
        for it in self._data["items"]:
            if it["id"] == item_id:
                return it
        raise QueueError(f"no queue item {item_id!r}")

    def _emit(self, event: str, item: Dict[str, Any], actor: str, detail: str = "") -> None:
        event_log.emit(f"social.{event}", actor=actor or "",
                       ids={"social_id": item["id"]},
                       payload={"platform": item["platform"],
                                "target_id": item["target_id"],
                                "state": item["state"],
                                "detail": str(detail)[:200]})

    def _record(self, item: Dict[str, Any], event: str, actor: str,
                detail: str = "", state: Optional[str] = None) -> None:
        if state is not None:
            item["state"] = state
        item["updated_at"] = time.time()
        item["history"].append({"ts": time.time(), "event": event,
                                "actor": actor or "", "detail": str(detail)[:300]})
        self._save()
        self._emit(event, item, actor, detail)

    # -- items --------------------------------------------------------
    def add_item(self, platform: str, kind: str, target_id: str, target_url: str,
                 author: str, text_in: str, actor: str = "") -> Dict[str, Any]:
        if platform not in PLATFORMS:
            raise QueueError(f"unknown platform {platform!r}")
        if kind not in KINDS:
            raise QueueError(f"unknown kind {kind!r}")
        with self._lock:
            for it in self._data["items"]:
                if it["platform"] == platform and it["target_id"] == str(target_id):
                    raise QueueError(
                        f"a live item already covers {platform} target {target_id}")
            item = {
                "id": "soc-" + uuid.uuid4().hex[:12],
                "platform": platform, "kind": kind,
                "target_id": str(target_id), "target_url": str(target_url or ""),
                "author": str(author or ""), "author_key": normalize_author(author),
                "text_in": str(text_in or "")[:2000],
                "draft": "", "parts": [], "state": "draft",
                "created_at": time.time(), "updated_at": time.time(),
                "approved_by": "", "posted_ref": "", "attempts": 0,
                "last_error": "", "history": [],
            }
            self._data["items"].append(item)
            self._record(item, "item_added", actor, f"{kind} from {author}")
            return dict(item)

    def get(self, item_id: str) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._find(item_id)))

    def list(self, state: Optional[str] = None,
             platform: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            out = [json.loads(json.dumps(it)) for it in self._data["items"]
                   if (state is None or it["state"] == state)
                   and (platform is None or it["platform"] == platform)]
        return sorted(out, key=lambda it: it["created_at"])

    def set_draft(self, item_id: str, text: str, actor: str = "") -> Dict[str, Any]:
        with self._lock:
            item = self._find(item_id)
            validate_draft(item["platform"], text)
            item["draft"] = text
            item["parts"] = split_for_x(text) if item["platform"] == "x" else [text.strip()]
            self._record(item, "draft_set", actor, f"{len(item['parts'])} part(s)")
            if auto_mode(item["platform"]) and item["state"] in ("draft", "awaiting_approval"):
                item["approved_by"] = "auto"
                self._record(item, "auto_approved", actor,
                             f"per-platform auto mode is on for {item['platform']}",
                             state="approved")
            return dict(item)

    def request_approval(self, item_id: str, actor: str = "") -> Dict[str, Any]:
        with self._lock:
            item = self._find(item_id)
            if item["state"] != "draft":
                raise QueueError(f"item is {item['state']}, not draft")
            if not item["draft"]:
                raise QueueError("the item has no draft to approve")
            self._record(item, "approval_requested", actor, "", state="awaiting_approval")
            return dict(item)

    def approve(self, item_id: str, actor: str = "") -> Dict[str, Any]:
        with self._lock:
            item = self._find(item_id)
            if item["state"] not in ("draft", "awaiting_approval"):
                raise QueueError(f"item is {item['state']}; only a draft can be approved")
            if not item["draft"]:
                raise QueueError("the item has no draft to approve")
            item["approved_by"] = actor or ""
            self._record(item, "approved", actor, "", state="approved")
            return dict(item)

    # -- posting lifecycle ---------------------------------------------
    def claim_post(self, item_id: str, actor: str = "") -> Dict[str, Any]:
        with self._lock:
            item = self._find(item_id)
            if item["state"] != "approved":
                raise QueueError(f"item is {item['state']}; only an approved item can post")
            item["attempts"] += 1
            self._record(item, "posting", actor, f"attempt {item['attempts']}", state="posting")
            return dict(item)

    def mark_posted(self, item_id: str, actor: str = "", posted_ref: str = "") -> Dict[str, Any]:
        with self._lock:
            item = self._find(item_id)
            if posted_ref:
                item["posted_ref"] = posted_ref
            self._record(item, "posted", actor, posted_ref, state="posted")
            return dict(item)

    def mark_verified(self, item_id: str, actor: str = "", posted_ref: str = "") -> Dict[str, Any]:
        with self._lock:
            item = self._find(item_id)
            if posted_ref:
                item["posted_ref"] = posted_ref
            item["last_error"] = ""
            self._record(item, "verified", actor, posted_ref or item["posted_ref"],
                         state="verified")
            return dict(item)

    def mark_unknown(self, item_id: str, actor: str = "", detail: str = "") -> Dict[str, Any]:
        with self._lock:
            item = self._find(item_id)
            item["last_error"] = str(detail)[:300]
            self._record(item, "unknown", actor, detail, state="unknown")
            return dict(item)

    def revert_to_approved(self, item_id: str, actor: str = "", detail: str = "") -> Dict[str, Any]:
        with self._lock:
            item = self._find(item_id)
            item["last_error"] = str(detail)[:300]
            self._record(item, "post_refused", actor, detail, state="approved")
            return dict(item)

    def note(self, item_id: str, event: str, actor: str = "", detail: str = "") -> Dict[str, Any]:
        with self._lock:
            item = self._find(item_id)
            self._record(item, event, actor, detail)
            return dict(item)

    # -- rate limiting ---------------------------------------------------
    def record_post_ts(self, platform: str, ts: Optional[float] = None) -> None:
        with self._lock:
            self._data["post_log"].setdefault(platform, []).append(ts or time.time())
            self._save()

    def rate_limit_wait(self, platform: str) -> Tuple[int, str]:
        """Seconds to wait before the next post on this platform, and why.
        (0, "") means a post may go now."""
        limits = RATE_LIMITS[platform]
        now = time.time()
        log = [float(t) for t in self._data.get("post_log", {}).get(platform, [])]
        if log:
            gap = limits["min_gap_sec"] - (now - max(log))
            if gap > 0:
                return int(gap) + 1, f"minimum gap between {platform} posts"
        today = [t for t in log if time.localtime(t)[:3] == time.localtime(now)[:3]]
        if len(today) >= limits["per_day"]:
            midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, 0, 0, 0, -1)) + 86400
            return max(1, int(midnight - now)), f"daily cap of {limits['per_day']} {platform} posts"
        return 0, ""

    # -- reads -----------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        with self._lock:
            items = self._data["items"]
            by_state: Dict[str, int] = {}
            by_platform: Dict[str, int] = {}
            oldest_awaiting: Optional[float] = None
            for it in items:
                by_state[it["state"]] = by_state.get(it["state"], 0) + 1
                by_platform[it["platform"]] = by_platform.get(it["platform"], 0) + 1
                if it["state"] == "awaiting_approval":
                    oldest_awaiting = it["updated_at"] if oldest_awaiting is None \
                        else min(oldest_awaiting, it["updated_at"])
            out: Dict[str, Any] = {"total": len(items), "by_state": by_state,
                                   "by_platform": by_platform}
            if oldest_awaiting is not None:
                out["oldest_awaiting_age_sec"] = round(time.time() - oldest_awaiting)
            return out


_default_store: Optional[SocialStore] = None
_default_lock = threading.Lock()


def default_store() -> SocialStore:
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = SocialStore()
        return _default_store


def queue_summary() -> Dict[str, Any]:
    """The harness and snapshot read: counts only, never draft text."""
    return default_store().summary()


# ------------------------------------------------------------ executors

class Executor:
    """The browser seam. Every method is a coroutine; a real driver does
    browser work, the fixture driver replays pages. post_reply returns
    {"posted_ref", "evidence"} on a confirmed click, raises PostUnknown
    when the click's outcome is not known, and raises
    ExecutorUnavailable when the route cannot run the step at all.
    verify_post returns {"found", "checked", "posted_ref", "detail"}:
    checked False means the destination could not be read, which is
    unknown, never absent."""

    async def fetch_inbox(self, platform: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def post_reply(self, item: Dict[str, Any], parts: List[str]) -> Dict[str, Any]:
        raise NotImplementedError

    async def verify_post(self, item: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def fetch_counts(self, platform: str) -> Dict[str, Any]:
        return {"state": "unavailable", "platform": platform,
                "reason": "this executor does not read live counts"}


class FixtureExecutor(Executor):
    """Replays fixture pages: an inbox per platform, and a post behavior
    (ok, timeout_found, timeout_absent) so the unknown-post paths are
    exercised without a browser. post_calls counts real post attempts."""

    def __init__(self, inbox: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                 post_behavior: str = "ok"):
        self.inbox = inbox or {}
        self.post_behavior = post_behavior
        self.post_calls = 0
        self._posted: Dict[str, str] = {}

    async def fetch_inbox(self, platform: str) -> List[Dict[str, Any]]:
        return [dict(x) for x in self.inbox.get(platform, [])]

    async def post_reply(self, item: Dict[str, Any], parts: List[str]) -> Dict[str, Any]:
        self.post_calls += 1
        ref = f"fixture://{item['platform']}/{item['target_id']}/reply-1"
        if self.post_behavior == "timeout_found":
            self._posted[item["target_id"]] = ref
            raise PostUnknown("fixture: the post click timed out, the reply landed")
        if self.post_behavior == "timeout_absent":
            raise PostUnknown("fixture: the post click timed out, nothing landed")
        self._posted[item["target_id"]] = ref
        return {"posted_ref": ref, "evidence": "fixture post recorded"}

    async def verify_post(self, item: Dict[str, Any]) -> Dict[str, Any]:
        ref = self._posted.get(item["target_id"], "")
        return {"found": bool(ref), "checked": True, "posted_ref": ref,
                "detail": "fixture destination read"}


class DryRunExecutor(Executor):
    """Wraps any executor: reads delegate, the post is intercepted and
    reported as exactly what would have posted. The inner executor's
    post is never called."""

    def __init__(self, inner: Executor):
        self.inner = inner

    async def fetch_inbox(self, platform: str) -> List[Dict[str, Any]]:
        return await self.inner.fetch_inbox(platform)

    async def verify_post(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return await self.inner.verify_post(item)

    async def fetch_counts(self, platform: str) -> Dict[str, Any]:
        return await self.inner.fetch_counts(platform)

    async def post_reply(self, item: Dict[str, Any], parts: List[str]) -> Dict[str, Any]:
        return {"dry_run": True, "posted_ref": "",
                "would_post_to": item["target_url"], "parts": list(parts),
                "evidence": "dry run: the full path ran, the post click was not made"}


class UnavailableExecutor(Executor):
    """The executor on a machine with no browser route (not a Mac, or the
    driver declined): every step reports unavailable with the reason."""

    def __init__(self, reason: str):
        self.reason = reason

    async def fetch_inbox(self, platform: str) -> List[Dict[str, Any]]:
        raise ExecutorUnavailable(self.reason)

    async def post_reply(self, item: Dict[str, Any], parts: List[str]) -> Dict[str, Any]:
        raise ExecutorUnavailable(self.reason)

    async def verify_post(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {"found": False, "checked": False, "posted_ref": "", "detail": self.reason}


def default_executor() -> Executor:
    if sys.platform == "darwin":
        return MacChromeExecutor()
    return UnavailableExecutor("social reply executes on the Mac member in v1 "
                               "(the signed-in Chrome lives there)")


# ------------------------------------------------------------ the runner

async def _verify(executor: Executor, item: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return await executor.verify_post(item)
    except Exception as e:
        return {"found": False, "checked": False, "posted_ref": "",
                "detail": f"verify raised: {e}"}


async def run_post(item_id: str, actor: str = "", executor: Optional[Executor] = None,
                   dry_run: Optional[bool] = None,
                   store: Optional[SocialStore] = None) -> Dict[str, Any]:
    """Post one approved item and verify it at the destination. Refusals
    (not approved, rate limited, executor unavailable) are results, not
    exceptions, and never change the item out from under the caller."""
    store = store or default_store()
    item = store.get(item_id)
    if item["state"] != "approved":
        return {"state": "refused", "item_id": item_id,
                "reason": f"the item is {item['state']}; only an approved item posts"}
    parts = item["parts"] or ([item["draft"].strip()] if item["draft"] else [])
    if not parts:
        return {"state": "refused", "item_id": item_id, "reason": "the item has no draft"}
    if dry_run is None:
        dry_run = dry_run_default()
    ex = executor or default_executor()
    if dry_run:
        outcome = await DryRunExecutor(ex).post_reply(item, parts)
        store.note(item_id, "dry_run", actor,
                   f"{len(parts)} part(s) would post to {item['target_url']}")
        return {"state": "dry_run", "dry_run": True, "item_id": item_id,
                "parts": outcome["parts"], "would_post_to": outcome["would_post_to"]}
    wait, why = store.rate_limit_wait(item["platform"])
    if wait > 0:
        store.note(item_id, "rate_limited", actor, f"{why}; retry in {wait}s")
        return {"state": "rate_limited", "item_id": item_id,
                "retry_after_sec": wait, "reason": why}
    store.claim_post(item_id, actor)
    item = store.get(item_id)
    try:
        outcome = await ex.post_reply(item, parts)
    except PostUnknown as e:
        store.mark_unknown(item_id, actor, str(e))
        v = await _verify(ex, store.get(item_id))
        if v.get("found"):
            store.record_post_ts(item["platform"])
            store.mark_verified(item_id, actor, v.get("posted_ref") or "")
            return {"state": "verified", "item_id": item_id,
                    "posted_ref": store.get(item_id)["posted_ref"],
                    "note": "the post timed out but the reply is live; no repost was made"}
        if not v.get("checked"):
            return {"state": "unknown", "item_id": item_id,
                    "reason": "the post outcome is unknown and the destination "
                              "could not be read; nothing was reposted"}
        store.revert_to_approved(item_id, actor,
                                 "the timed-out post is proven absent at the destination")
        return {"state": "unknown_absent", "item_id": item_id,
                "reason": "the post timed out and verification proves it absent; "
                          "the item is approved again and may repost"}
    except ExecutorUnavailable as e:
        store.revert_to_approved(item_id, actor, str(e))
        return {"state": "unavailable", "item_id": item_id, "reason": str(e)}
    store.record_post_ts(item["platform"])
    store.mark_posted(item_id, actor, outcome.get("posted_ref") or "")
    v = await _verify(ex, store.get(item_id))
    if v.get("found"):
        store.mark_verified(item_id, actor, v.get("posted_ref") or outcome.get("posted_ref") or "")
        return {"state": "verified", "item_id": item_id,
                "posted_ref": store.get(item_id)["posted_ref"]}
    if not v.get("checked"):
        store.mark_unknown(item_id, actor, "posted, but the destination could not be read back")
        return {"state": "posted_unverified", "item_id": item_id,
                "reason": "the click reported success but verification could not read "
                          "the destination; the item is unknown, not verified"}
    store.mark_unknown(item_id, actor, "posted, but the reply is absent on the verify read")
    return {"state": "posted_unverified", "item_id": item_id,
            "reason": "the click reported success but the reply is absent on the "
                      "verify read; the item is unknown, not verified"}


async def run_verify(item_id: str, actor: str = "",
                     executor: Optional[Executor] = None,
                     store: Optional[SocialStore] = None) -> Dict[str, Any]:
    """Re-read the destination for a posted or unknown item. A found reply
    lands the item verified; a proven absence on an unknown item returns
    it to approved so a repost is allowed."""
    store = store or default_store()
    item = store.get(item_id)
    if item["state"] not in ("posted", "unknown"):
        return {"state": "refused", "item_id": item_id,
                "reason": f"the item is {item['state']}; only posted or unknown items verify"}
    ex = executor or default_executor()
    v = await _verify(ex, item)
    if v.get("found"):
        store.mark_verified(item_id, actor, v.get("posted_ref") or "")
        return {"state": "verified", "item_id": item_id,
                "posted_ref": store.get(item_id)["posted_ref"]}
    if not v.get("checked"):
        return {"state": "unknown", "item_id": item_id,
                "reason": "the destination could not be read; the item stays unknown"}
    if item["state"] == "unknown":
        store.revert_to_approved(item_id, actor,
                                 "verification proves the reply absent; repost allowed")
        return {"state": "unknown_absent", "item_id": item_id,
                "reason": "the reply is proven absent; the item is approved again"}
    store.mark_unknown(item_id, actor, "the posted reply is absent on re-verify")
    return {"state": "unknown", "item_id": item_id,
            "reason": "the posted reply is absent on re-verify; the item is unknown"}


async def fetch(platform: str, actor: str = "", executor: Optional[Executor] = None,
                store: Optional[SocialStore] = None) -> Dict[str, Any]:
    """Pull a platform's inbox into the queue. Duplicates of targets the
    queue already covers are skipped and counted, never re-added."""
    store = store or default_store()
    ex = executor or default_executor()
    raw = await ex.fetch_inbox(platform)
    added, skipped = 0, 0
    for entry in raw:
        try:
            store.add_item(platform=platform,
                           kind=str(entry.get("kind") or "mention"),
                           target_id=str(entry.get("target_id") or ""),
                           target_url=str(entry.get("target_url") or ""),
                           author=str(entry.get("author") or ""),
                           text_in=str(entry.get("text_in") or ""), actor=actor)
            added += 1
        except QueueError:
            skipped += 1
    event_log.emit("social.fetched", actor=actor or "", ids={},
                   payload={"platform": platform, "added": added, "skipped": skipped})
    return {"state": "ok", "platform": platform, "added": added, "skipped": skipped}


# ------------------------------------------------------------ Mac Chrome executor

_JS_HARVEST = {
    "x": ("(() => { const out = []; const seen = new Set(); "
          "document.querySelectorAll('article').forEach(a => { "
          "const link = a.querySelector('a[href*=\"/status/\"]'); if (!link) return; "
          "const m = link.href.match(/status\\/(\\d+)/); if (!m || seen.has(m[1])) return; "
          "seen.add(m[1]); const txt = (a.innerText || '').slice(0, 600); "
          "const lines = txt.split('\\n').filter(Boolean); "
          "out.push({target_id: m[1], target_url: link.href.split('?')[0], "
          "author: lines[0] || '', text_in: txt, kind: 'mention'}); }); "
          "return JSON.stringify(out.slice(0, 20)); })()"),
    "linkedin": ("(() => { const out = []; const seen = new Set(); "
                 "document.querySelectorAll('a[href*=\"/feed/update/\"]').forEach(link => { "
                 "const m = link.href.match(/activity:(\\d+)/); if (!m || seen.has(m[1])) return; "
                 "seen.add(m[1]); const card = link.closest('article') || link.parentElement; "
                 "const txt = ((card && card.innerText) || '').slice(0, 600); "
                 "const lines = txt.split('\\n').filter(Boolean); "
                 "out.push({target_id: 'urn:li:activity:' + m[1], "
                 "target_url: link.href.split('?')[0], author: lines[0] || '', "
                 "text_in: txt, kind: 'notification'}); }); "
                 "return JSON.stringify(out.slice(0, 20)); })()"),
}

_JS_X_COMPOSE = (
    "(() => { const ed = document.querySelector('[data-testid=\"tweetTextarea_0\"]'); "
    "if (!ed) return 'NO_EDITOR'; ed.focus(); "
    "document.execCommand('selectAll', false, null); "
    "document.execCommand('insertText', false, %s); return 'FILLED'; })()")

_JS_X_CLICK = (
    "(() => { const b = document.querySelector('[data-testid=\"tweetButtonInline\"]') "
    "|| document.querySelector('[data-testid=\"tweetButton\"]'); "
    "if (!b) return 'NO_BUTTON'; if (b.getAttribute('aria-disabled') === 'true') "
    "return 'DISABLED'; b.click(); return 'CLICKED'; })()")

_JS_LI_COMPOSE = (
    "(() => { const ed = document.querySelector('.comments-comment-box .ql-editor') "
    "|| document.querySelector('.ql-editor[contenteditable=\"true\"]'); "
    "if (!ed) return 'NO_EDITOR'; ed.focus(); "
    "document.execCommand('selectAll', false, null); "
    "document.execCommand('insertText', false, %s); return 'FILLED'; })()")

_JS_LI_CLICK = (
    "(() => { const box = document.querySelector('.comments-comment-box'); "
    "const scope = box || document; const btns = scope.querySelectorAll('button'); "
    "for (const b of btns) { if ((b.innerText || '').trim() === 'Post') { "
    "b.click(); return 'CLICKED'; } } return 'NO_BUTTON'; })()")

_JS_PAGE_TEXT = "return document.body ? document.body.innerText.slice(0, 12000) : ''"


def _as_quote(text: str) -> str:
    """A string literal safe inside an AppleScript double-quoted string."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MacChromeExecutor(Executor):
    """Drives the owner's own Chrome on the Mac through the desktop
    control service. The session is the owner's, on the device; nothing
    is scraped off the machine and no credential is ever handled. The
    executor never launches Chrome and never creates its first window:
    both are platform limits it reports instead of fighting."""

    # -- plumbing ------------------------------------------------------
    def _svc(self):
        from app.services.system_control import system_control_service
        return system_control_service

    async def _as(self, script: str) -> str:
        res = await self._svc().run_applescript(script)
        if not res.get("success"):
            err = str(res.get("error") or "applescript failed")
            if "javascript" in err.lower():
                raise ExecutorUnavailable(
                    "Chrome refused the page script: enable View, Developer, "
                    "Allow JavaScript from Apple Events in Chrome, then retry")
            raise ExecutorUnavailable(f"desktop control failed: {err[:200]}")
        return str(res.get("output") or "")

    async def _chrome_running(self) -> bool:
        out = await self._as(
            'tell application "System Events" to return '
            '(count of (every process whose name is "Google Chrome")) > 0')
        return out.strip().lower() == "true"

    async def _busy_reason(self) -> str:
        front = await self._as(
            'tell application "System Events" to get name of '
            'first application process whose frontmost is true')
        if front.strip() in CALL_APPS:
            return f"the Mac is in a call (frontmost app: {front.strip()})"
        if await self._chrome_running():
            urls = await self._as(
                'tell application "Google Chrome" to get URL of every tab of every window')
            for hint in MEETING_URL_HINTS:
                if hint in urls:
                    return "a meeting tab is open in Chrome"
        return ""

    async def _require_ready(self) -> None:
        busy = await self._busy_reason()
        if busy:
            raise ExecutorBusy(busy + "; nothing was touched")
        if not await self._chrome_running():
            raise ExecutorUnavailable(
                "Google Chrome is not running; the executor never launches it")

    async def _open_tab(self, url: str) -> int:
        out = await self._as(
            'tell application "Google Chrome"\n'
            'set w to front window\n'
            f'set t to make new tab at end of tabs of w with properties {{URL:{_as_quote(url)}}}\n'
            'return id of t\nend tell')
        try:
            return int(out.strip())
        except ValueError:
            raise ExecutorUnavailable(
                "Chrome has no front window to open a tab in; the executor "
                "never creates the first window")

    async def _tab_field(self, tab_id: int, field: str) -> str:
        return await self._as(
            'tell application "Google Chrome"\n'
            'repeat with w in windows\nrepeat with t in tabs of w\n'
            f'if id of t is {tab_id} then return {field} of t\n'
            'end repeat\nend repeat\nreturn ""\nend tell')

    async def _close_tab(self, tab_id: int) -> None:
        try:
            await self._as(
                'tell application "Google Chrome"\n'
                'repeat with w in windows\nrepeat with t in tabs of w\n'
                f'if id of t is {tab_id} then close t\n'
                'end repeat\nend repeat\nend tell')
        except Exception:
            pass    # closing our own tab is housekeeping; never fail the read over it

    async def _tab_js(self, tab_id: int, js: str) -> str:
        return await self._as(
            'tell application "Google Chrome"\n'
            'repeat with w in windows\nrepeat with t in tabs of w\n'
            f'if id of t is {tab_id} then return execute t javascript {_as_quote(js)}\n'
            'end repeat\nend repeat\nreturn ""\nend tell')

    # -- reads -----------------------------------------------------------
    async def fetch_counts(self, platform: str) -> Dict[str, Any]:
        """The read-only live check: open the platform's notifications
        page in a new tab, read the unread count from its title, close
        the tab. Busy and not-running are reported, never fought."""
        if sys.platform != "darwin":
            return {"state": "unavailable", "platform": platform,
                    "reason": "the Mac executor only runs on macOS"}
        try:
            busy = await self._busy_reason()
            if busy:
                return {"state": "busy", "platform": platform, "reason": busy}
            if not await self._chrome_running():
                return {"state": "unavailable", "platform": platform,
                        "reason": "Google Chrome is not running; the executor "
                                  "never launches it"}
            tab_id = await self._open_tab(NOTIFICATIONS_URL[platform])
            try:
                await asyncio.sleep(5)
                title = await self._tab_field(tab_id, "title")
            finally:
                await self._close_tab(tab_id)
            return {"state": "ok", "platform": platform,
                    "unread": parse_notifications_title(platform, title),
                    "title": title, "method": "tab_title"}
        except ExecutorUnavailable as e:
            return {"state": "unavailable", "platform": platform, "reason": str(e)}

    async def fetch_inbox(self, platform: str) -> List[Dict[str, Any]]:
        await self._require_ready()
        tab_id = await self._open_tab(NOTIFICATIONS_URL[platform])
        try:
            await asyncio.sleep(5)
            raw = await self._tab_js(tab_id, _JS_HARVEST[platform])
        finally:
            await self._close_tab(tab_id)
        try:
            entries = json.loads(raw) if raw else []
        except ValueError:
            raise ExecutorUnavailable("the notifications page did not parse; "
                                      "its structure may have changed")
        return [e for e in entries if isinstance(e, dict) and e.get("target_id")]

    async def verify_post(self, item: Dict[str, Any]) -> Dict[str, Any]:
        try:
            await self._require_ready()
        except ExecutorUnavailable as e:
            return {"found": False, "checked": False, "posted_ref": "",
                    "detail": str(e)}
        tab_id = await self._open_tab(item["target_url"])
        try:
            await asyncio.sleep(4)
            text = await self._tab_js(tab_id, _JS_PAGE_TEXT)
            url = await self._tab_field(tab_id, "URL")
        except ExecutorUnavailable as e:
            return {"found": False, "checked": False, "posted_ref": "",
                    "detail": str(e)}
        finally:
            await self._close_tab(tab_id)
        needle = (item["draft"] or "")[:60].strip()
        found = bool(needle) and needle in text
        return {"found": found, "checked": True,
                "posted_ref": url if found else "",
                "detail": "the reply text is on the destination page" if found
                          else "the reply text is not on the destination page"}

    # -- the post ---------------------------------------------------------
    async def _compose_and_click(self, tab_id: int, platform: str, text: str) -> str:
        compose = _JS_X_COMPOSE if platform == "x" else _JS_LI_COMPOSE
        click = _JS_X_CLICK if platform == "x" else _JS_LI_CLICK
        filled = await self._tab_js(tab_id, compose % json.dumps(text))
        if filled != "FILLED":
            raise ExecutorUnavailable(
                f"structure_changed: the reply editor was not found ({filled}); "
                "the page layout may have changed, nothing was posted")
        await asyncio.sleep(1)
        clicked = await self._tab_js(tab_id, click)
        if clicked != "CLICKED":
            raise ExecutorUnavailable(
                f"structure_changed: the post button was not clickable ({clicked}); "
                "nothing was posted")
        return "clicked"

    async def post_reply(self, item: Dict[str, Any], parts: List[str]) -> Dict[str, Any]:
        await self._require_ready()
        platform = item["platform"]
        tab_id = await self._open_tab(item["target_url"])
        try:
            await asyncio.sleep(4)
            landed = await self._tab_field(tab_id, "URL")
            host = "x.com" if platform == "x" else "linkedin.com"
            if host not in landed:
                raise ExecutorUnavailable(
                    f"navigation landed on {landed[:120]}, not the {platform} target; "
                    "the session may be signed out")
            if len(parts) > 1:
                # A thread part must reply to the previous part, and this
                # route cannot anchor that chain reliably yet. Refuse
                # BEFORE any click: the item stays approved, nothing
                # posts, and the reason is on the item. Chained thread
                # posting is a named next step, not a silent partial.
                raise ExecutorUnavailable(
                    "multi-part thread posting is not drivable on this route yet; "
                    "the item stays approved and nothing was posted")
            await self._compose_and_click(tab_id, platform, parts[0])
            await asyncio.sleep(3)
            current_url = await self._tab_field(tab_id, "URL")
            return {"posted_ref": current_url,
                    "evidence": "the reply clicked post on the target page"}
        finally:
            await self._close_tab(tab_id)
