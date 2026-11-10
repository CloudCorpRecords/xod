"""
Inbox status: which mail threads are waiting on the owner, answered with proof.

The question this exists for is "have I done all my emails?" Asked against a
slow, flaky mail route, that question used to get answered in pieces: a search
that timed out looked exactly like an empty inbox. This service owns the route,
the retries and a per-source digest cache, so one call returns a complete,
honest answer:

  ok      the source was read; here is its checked_at and its threads.
  stale   the last refresh failed; the last good digest is served with the
          error attached, never presented as current.
  unread  the source has never been read successfully. It is listed under
          unread_sources and contributes NO items: a failed source is never
          reported as empty (the degraded-evidence rule).

Read-only by construction: the only mail operations anywhere in this module
are Gmail `users.messages.list`, Gmail `users.messages.get` (metadata) and
Muse Mail `messages list`. No send, label, modify or delete code path exists.

ROUTE AND ITS LIMIT, stated plainly: v1 reads through the host's existing
signed-in mail CLIs (hatch_gws_cli for Gmail, muse-mail for Muse Mail),
located by XOD_GWS_CLI / XOD_MUSE_MAIL_CLI or the default paths below. Auth
lives inside those CLIs; this service holds no credentials and logs none.
A host without those CLIs gets every source reported unread with the reason,
not a crash and not a silent empty. Per-user OAuth connection is the later
step that replaces this route; it is deliberately not built here. The source
list itself is per-instance config: DATA_DIR/inbox_sources.json (a JSON list
of source objects) overrides the built-in defaults, so another instance can
track other mailboxes without a code change.

The Gmail route discipline (raw single calls, small pages, sequential per
account, retry a code 5 once or twice) comes from the inbox-mcp prototype
this ports (~/workspace/inbox-mcp/DESIGN.md): multi-call shortcut flows die
mid-chain on this route, so they are never used. A full refresh takes
minutes, which is why the cache exists and why a background warmer
(start(), skipped in lightweight mode as the "inbox_warm" loop) keeps it
fresh: steady-state answers come from the cache in milliseconds.
"""
import asyncio
import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.inbox_status")

CACHE_DIR = DATA_DIR / "inbox-status"
SOURCES_PATH = DATA_DIR / "inbox_sources.json"
DEFAULT_DAYS = 7
MAX_THREADS = 12                    # metadata reads per Gmail account per refresh (bounded work)
WARM_MAX_AGE_SECONDS = 1800         # the warmer refreshes a source older than this
WARM_CHECK_SECONDS = 60

GWS_CLI = os.environ.get("XOD_GWS_CLI", "/opt/hatch/bin/hatch_gws_cli")
MUSE_MAIL_CLI = os.environ.get("XOD_MUSE_MAIL_CLI", "/opt/hatch/bin/muse-mail")

# The built-in source set: this instance's mailboxes, read through the host's
# signed-in routes. inbox_sources.json in the data folder replaces this list.
DEFAULT_SOURCES: List[Dict[str, Any]] = [
    {"key": "gmail-cloudtheboy", "kind": "gmail",
     "account_id": "992606166ca84388ac4e34ab6f1d0497",
     "label": "cloudtheboy@gmail.com",
     "self_addresses": ["cloudtheboy@gmail.com"]},
    {"key": "gmail-foundersbay", "kind": "gmail",
     "account_id": "01298b0cbdfa45bd9374d34aeabe2f96",
     "label": "owner@example.com",
     "self_addresses": ["owner@example.com"]},
    {"key": "muse-mail", "kind": "muse_mail",
     "label": "tree@muse.ai",
     "self_addresses": ["tree@muse.ai"]},
]

# Conservative automated-sender filter, ported from the prototype. A false
# negative (an automated mail listed as needing a reply) is acceptable; a
# false positive (a real person's mail hidden) is not, so this stays narrow.
# Filtered threads are counted in stats.automated_filtered, never silently
# dropped. LinkedIn is deliberately NOT filtered: recruiter mail matters.
AUTOMATED_SENDER = re.compile(
    r"no[-_.]?reply|do[-_.]?not[-_.]?reply|notifications?@|mailer-daemon|postmaster"
    r"|notifications@github\.com|amazonses\.com|sendgrid\.net|mailchimp|mandrillapp",
    re.IGNORECASE)


class RouteError(Exception):
    """One source's route failed; the source is reported, never faked."""


def _truncate(s: Any, n: int = 300) -> str:
    one = re.sub(r"\s+", " ", str(s)).strip()
    return one[:n] + "..." if len(one) > n else one


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_age_seconds(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return max(0, int((datetime.now(timezone.utc) - then).total_seconds()))


# --------------------------------------------------------------------- config

def load_sources() -> List[Dict[str, Any]]:
    """This instance's source list: inbox_sources.json when present and sane,
    else the built-in defaults. A broken config file falls back to defaults
    with a warning rather than taking the whole capability down."""
    try:
        raw = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, list) and raw and all(
                isinstance(s, dict) and s.get("key") and s.get("kind") in ("gmail", "muse_mail")
                for s in raw):
            return raw
        logger.warning("inbox_sources.json has an unexpected shape; using defaults")
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"inbox_sources.json unreadable ({e}); using defaults")
    return [dict(s) for s in DEFAULT_SOURCES]


def source_keys() -> List[str]:
    return [s["key"] for s in load_sources()]


# ---------------------------------------------------------------------- cache

def _digest_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def read_digest(key: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(_digest_path(key).read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_digest(digest: Dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CACHE_DIR, 0o700)
    except OSError:
        pass
    tmp = CACHE_DIR / f"{digest['source']}.json.tmp-{os.getpid()}"
    tmp.write_text(json.dumps(digest, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_digest_path(digest["source"]))


def _empty_digest(key: str) -> Dict[str, Any]:
    return {"source": key, "checked_at": None, "last_attempt_at": None,
            "ok": False, "error": None, "items": [], "stats": None}


def _state_of(digest: Optional[Dict[str, Any]]) -> str:
    if not digest or not digest.get("checked_at"):
        return "unread"
    return "stale" if digest.get("error") else "ok"


# ------------------------------------------------------------------ CLI route

def _run_cli(argv: List[str], timeout: float) -> str:
    """One CLI call. Kept as a single seam so tests can replace the route.
    Raises RouteError with a truncated, single-line message: no stack, no
    environment, nothing credential-shaped ever rides into results or logs."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise RouteError(f"route unavailable: {argv[0]} not found on this host")
    except subprocess.TimeoutExpired:
        raise RouteError(f"route timed out after {int(timeout)}s: {argv[0]}")
    if p.returncode != 0:
        raise RouteError(_truncate(f"exit {p.returncode}: {p.stderr or p.stdout}"))
    return p.stdout


def _cli_json(argv: List[str], timeout: float, retries: int) -> Any:
    """One CLI call that must return JSON, with bounded retries. The retries
    exist for the intermittent gws code 5 (VM egress slowness, not auth);
    backoff 3s, then 8s."""
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return json.loads(_run_cli(argv, timeout))
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(3 if attempt == 0 else 8)
    raise RouteError(_truncate(last) if last else "route failed")


# -------------------------------------------------------------------- fetchers

def _header(meta: Dict[str, Any], name: str) -> str:
    for h in (meta.get("payload") or {}).get("headers") or []:
        if str(h.get("name", "")).lower() == name.lower():
            return h.get("value") or ""
    return ""


def _email_of(from_value: str) -> str:
    m = re.search(r"<([^>]+)>", from_value or "")
    return (m.group(1) if m else (from_value or "")).strip().lower()


def _gmail_list(account_id: str, q: str, counter: Dict[str, int]) -> List[Dict[str, str]]:
    counter["calls"] += 1
    out = _cli_json([GWS_CLI, "gmail", "users", "messages", "list",
                     "--account", account_id,
                     "--params", json.dumps({"userId": "me", "q": q, "maxResults": 60})],
                    timeout=150, retries=2)
    return out.get("messages") or []


def _gmail_get_meta(account_id: str, mid: str, counter: Dict[str, int]) -> Dict[str, Any]:
    counter["calls"] += 1
    return _cli_json([GWS_CLI, "gmail", "users", "messages", "get",
                      "--account", account_id,
                      "--params", json.dumps({"userId": "me", "id": mid, "format": "metadata",
                                              "metadataHeaders": ["From", "Subject", "Date"]})],
                     timeout=150, retries=1)


def _newest_per_thread(msgs: List[Dict[str, str]]) -> Dict[str, str]:
    """Newest message id per thread (list results arrive newest first)."""
    out: Dict[str, str] = {}
    for m in msgs:
        out.setdefault(m.get("threadId") or "", m.get("id") or "")
    return out


def fetch_gmail(src: Dict[str, Any], days: int) -> Dict[str, Any]:
    """One Gmail source's needs-reply items. A thread counts when its newest
    inbox message in the window is inbound and no sent message in the thread
    is at or after it. Window-bounded by construction: a thread whose newest
    activity falls outside the list window is judged on what the window
    shows, and its reason string says so."""
    started = time.time()
    counter = {"calls": 0}
    q = f"newer_than:{days}d"
    inbox = _gmail_list(src["account_id"], f"in:inbox {q}", counter)
    sent = _gmail_list(src["account_id"], f"in:sent {q}", counter)
    inbox_newest = _newest_per_thread(inbox)
    sent_newest = _newest_per_thread(sent)
    self_addrs = {a.lower() for a in src.get("self_addresses") or []}

    items: List[Dict[str, Any]] = []
    automated = 0
    for thread_id in list(inbox_newest.keys())[:MAX_THREADS]:
        meta = _gmail_get_meta(src["account_id"], inbox_newest[thread_id], counter)
        from_value = _header(meta, "From")
        if _email_of(from_value) in self_addrs:
            continue                    # the owner mailed himself; not a reply debt
        if AUTOMATED_SENDER.search(from_value):
            automated += 1
            continue
        inbound_ms = int(meta.get("internalDate") or 0)
        sent_id = sent_newest.get(thread_id)
        if sent_id:
            sent_meta = _gmail_get_meta(src["account_id"], sent_id, counter)
            if int(sent_meta.get("internalDate") or 0) >= inbound_ms:
                continue                # already replied after this message
        items.append({
            "account": src["key"], "thread_id": thread_id, "message_id": meta.get("id") or "",
            "sender": from_value, "subject": _header(meta, "Subject"),
            "date": datetime.fromtimestamp(inbound_ms / 1000, timezone.utc).isoformat(),
            "snippet": _truncate(meta.get("snippet") or "", 200),
            "reason": "inbound_after_last_sent" if sent_id
                      else "latest_message_inbound_no_reply_in_window",
        })
    items.sort(key=lambda i: i["date"], reverse=True)
    return {"items": items,
            "stats": {"window_days": days, "threads_seen": len(inbox_newest),
                      "automated_filtered": automated,
                      "duration_ms": int((time.time() - started) * 1000),
                      "cli_calls": counter["calls"]}}


def fetch_muse_mail(src: Dict[str, Any], days: int) -> Dict[str, Any]:
    """The Muse Mail source: one list call; a thread counts when its newest
    message in the window is inbound."""
    started = time.time()
    out = _cli_json([MUSE_MAIL_CLI, "messages", "list", "--limit", "60"],
                    timeout=60, retries=1)
    all_msgs = out.get("data") or []
    cutoff = time.time() - days * 86400
    newest: Dict[str, Dict[str, Any]] = {}
    for m in all_msgs:
        if (m.get("created_at") or 0) < cutoff:
            continue
        cur = newest.get(m.get("thread_id") or "")
        if cur is None or (m.get("created_at") or 0) > (cur.get("created_at") or 0):
            newest[m.get("thread_id") or ""] = m
    self_addrs = {a.lower() for a in src.get("self_addresses") or []}
    items: List[Dict[str, Any]] = []
    for m in newest.values():
        if str(m.get("direction") or "").upper() != "INBOUND":
            continue
        if (m.get("header_from") or "").lower() in self_addrs:
            continue
        when = m.get("date") or datetime.fromtimestamp(
            m.get("created_at") or 0, timezone.utc).isoformat()
        sender = (f"{m['header_from_name']} <{m['header_from']}>"
                  if m.get("header_from_name") else (m.get("header_from") or ""))
        items.append({
            "account": src["key"], "thread_id": m.get("thread_id") or "",
            "message_id": m.get("id") or "", "sender": sender,
            "subject": m.get("subject") or "", "date": when,
            "snippet": _truncate(m.get("preview") or "", 200),
            "reason": "latest_message_inbound_no_reply_in_window",
        })
    items.sort(key=lambda i: i["date"], reverse=True)
    return {"items": items,
            "stats": {"window_days": days, "threads_seen": len(newest),
                      "automated_filtered": 0,
                      "duration_ms": int((time.time() - started) * 1000),
                      "cli_calls": 1}}


_FETCHERS: Dict[str, Callable[[Dict[str, Any], int], Dict[str, Any]]] = {
    "gmail": fetch_gmail,
    "muse_mail": fetch_muse_mail,
}


# -------------------------------------------------------------------- refresh

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_background: set = set()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def refresh_source(src: Dict[str, Any], days: int = DEFAULT_DAYS,
                   skip_if_younger_than: float = 0.0) -> Dict[str, Any]:
    """One source's refresh, serialized per source. On success the digest is
    replaced; on failure the last good digest is kept and only the attempt
    and the error are recorded. Returns the resulting digest.

    skip_if_younger_than is the background dedup guard: the warmer and the
    read-path kick can both decide a source needs a refresh in the same
    minute, and on a slow route a redundant second refresh is not free -- it
    burns minutes of route budget and, if it hits a bad patch, flips a fresh
    ok digest to stale for no new information (observed live 2026-11-07).
    Background callers pass a few minutes; an explicit refresh (refresh_all)
    passes 0 and always re-reads."""
    key = src["key"]
    with _lock_for(key):
        if skip_if_younger_than > 0:
            cached = read_digest(key)
            age = _iso_age_seconds((cached or {}).get("checked_at"))
            if cached and cached.get("ok") and not cached.get("error") \
                    and age is not None and age < skip_if_younger_than:
                return cached
        attempt_at = _now_iso()
        cached = read_digest(key)
        try:
            result = _FETCHERS[src["kind"]](src, days)
        except Exception as e:
            error = _truncate(e)
            digest = dict(cached) if cached else _empty_digest(key)
            digest.update(last_attempt_at=attempt_at, error=error,
                          ok=bool(digest.get("checked_at")))
            _write_digest(digest)
            logger.warning(f"inbox source {key} refresh failed: {error}")
            return digest
        digest = {"source": key, "checked_at": _now_iso(), "last_attempt_at": attempt_at,
                  "ok": True, "error": None, "items": result["items"],
                  "stats": result["stats"]}
        _write_digest(digest)
        return digest


def refresh_all(days: int = DEFAULT_DAYS) -> Dict[str, Dict[str, Any]]:
    """Every source, sequentially: the route degrades under bursts, and one
    slow account must not starve the others of their own honest result."""
    return {s["key"]: refresh_source(s, days) for s in load_sources()}


def _background_refresh(keys: List[str], days: int) -> None:
    """Fire-and-forget refresh on a daemon thread (read paths use this so a
    stale answer returns now and the cache is fresh for the next caller)."""
    todo = []
    with _locks_guard:
        for k in keys:
            if k not in _background:
                _background.add(k)
                todo.append(k)
    if not todo:
        return
    by_key = {s["key"]: s for s in load_sources()}

    def run() -> None:
        try:
            for k in todo:
                if k in by_key:
                    refresh_source(by_key[k], days, skip_if_younger_than=180)
        finally:
            with _locks_guard:
                _background.difference_update(todo)

    threading.Thread(target=run, name="xod-inbox-refresh", daemon=True).start()


# ------------------------------------------------------------------ answering

def status() -> Dict[str, Any]:
    """Cache-only: how much is actually known right now, per source. No
    network, returns in milliseconds. last success, digest age, cached
    counts, last error, and which sources are stale or unread."""
    sources: Dict[str, Any] = {}
    unread: List[str] = []
    for src in load_sources():
        d = read_digest(src["key"])
        state = _state_of(d)
        if state != "ok":
            unread.append(src["key"])
        sources[src["key"]] = {
            "label": src.get("label") or src["key"],
            "state": state,
            "last_success_at": (d or {}).get("checked_at"),
            "last_attempt_at": (d or {}).get("last_attempt_at"),
            "digest_age_seconds": _iso_age_seconds((d or {}).get("checked_at")),
            "cached_item_count": len((d or {}).get("items") or []),
            "threads_seen": ((d or {}).get("stats") or {}).get("threads_seen"),
            "automated_filtered": ((d or {}).get("stats") or {}).get("automated_filtered"),
            "last_refresh_duration_ms": ((d or {}).get("stats") or {}).get("duration_ms"),
            "last_error": (d or {}).get("error"),
        }
    return {"generated_at": _now_iso(), "sources": sources, "unread_sources": unread}


def needs_reply(days: int = DEFAULT_DAYS, refresh: bool = False,
                max_age_minutes: int = 15) -> Dict[str, Any]:
    """The needs-reply answer. Default: serve the cache (fresh from the
    warmer) and kick a background refresh for any source that is unread or
    older than max_age_minutes. refresh=True instead refreshes inline,
    sequentially: minutes on this route, which is the documented cost, and
    the reason the warm cache is the steady state. A source that has never
    been read contributes no items and is named in unread_sources; "nothing
    needs a reply" is only ever claimed for sources in state ok."""
    days = max(1, min(30, int(days)))
    max_age_minutes = max(0, min(1440, int(max_age_minutes)))
    srcs = load_sources()
    if refresh:
        refresh_all(days)

    items: List[Dict[str, Any]] = []
    sources: Dict[str, Any] = {}
    unread: List[Dict[str, Any]] = []
    kick: List[str] = []
    for src in srcs:
        key = src["key"]
        d = read_digest(key)
        state = _state_of(d)
        age = _iso_age_seconds((d or {}).get("checked_at"))
        fresh = bool(d and state == "ok" and age is not None
                     and age <= max_age_minutes * 60
                     and ((d.get("stats") or {}).get("window_days") == days))
        if not refresh and not fresh:
            kick.append(key)
        sources[key] = {
            "label": src.get("label") or key,
            "status": state,
            "checked_at": (d or {}).get("checked_at"),
            "from_cache": not refresh,
            "item_count": 0 if state == "unread" else len((d or {}).get("items") or []),
            "threads_seen": ((d or {}).get("stats") or {}).get("threads_seen"),
            "automated_filtered": ((d or {}).get("stats") or {}).get("automated_filtered"),
            "error": (d or {}).get("error"),
        }
        if state != "ok":
            unread.append({"source": key, "error": (d or {}).get("error"),
                           "last_good_at": (d or {}).get("checked_at")})
        if state == "unread":
            continue                    # never present an unread source as empty
        for item in (d or {}).get("items") or []:
            items.append({**item, "source_checked_at": (d or {}).get("checked_at"),
                          "from_cache": not refresh, "source_status": state})
    if kick:
        _background_refresh(kick, days)
    items.sort(key=lambda i: i.get("date") or "", reverse=True)
    return {"generated_at": _now_iso(), "window_days": days,
            "complete": not unread, "needs_reply": items, "sources": sources,
            "unread_sources": unread,
            "refresh_started": kick if not refresh else []}


def manifest_section() -> Dict[str, Any]:
    """The capability manifest's inbox entry (services/capability_manifest.py):
    which sources are tracked, each one's last verified read, and the
    route's limits. Counts and timestamps only; no message content, no
    sender, no subject ever enters the manifest."""
    st = status()
    return {
        "available": True,
        "read_only": True,
        "endpoints": ["/api/inbox/status", "/api/inbox/needs-reply"],
        "sources": [{"source": k, "label": v["label"], "state": v["state"],
                     "checked_at": v["last_success_at"],
                     "needs_reply_count": v["cached_item_count"]}
                    for k, v in st["sources"].items()],
        "unread_sources": st["unread_sources"],
        "route": "this host's signed-in mail CLIs (hatch_gws_cli, muse-mail)",
        "limits": ("Reads through this host's existing signed-in routes; there is no "
                   "per-user OAuth connection yet. A full refresh takes minutes on a "
                   "slow route, so answers are served from a per-source cache with "
                   "checked_at timestamps, kept warm by a background loop (skipped "
                   "in lightweight mode). A source that cannot be read is reported "
                   "unread or stale with its error, never as empty."),
    }


# ---------------------------------------------------------------------- warmer

_task: Optional[asyncio.Task] = None


async def warmer_loop() -> None:
    """Keeps every source's digest younger than WARM_MAX_AGE_SECONDS so the
    endpoints answer from cache. Sequential refreshes via a thread: a full
    pass is minutes of CLI time and must never block the server."""
    if os.environ.get("XOD_INBOX", "").lower() == "off":
        return                          # checks then only run when asked; tests use this
    await asyncio.sleep(20)             # let the server finish starting
    while True:
        try:
            for src in load_sources():
                d = read_digest(src["key"])
                age = _iso_age_seconds((d or {}).get("checked_at"))
                if age is None or age > WARM_MAX_AGE_SECONDS:
                    await asyncio.to_thread(refresh_source, src, DEFAULT_DAYS, 180)
        except Exception as e:
            logger.warning(f"inbox warmer pass failed: {e}")
        await asyncio.sleep(WARM_CHECK_SECONDS)


def start() -> None:
    """Starts the cache warmer (called from server startup unless lightweight
    mode skips the inbox_warm loop)."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(warmer_loop())
