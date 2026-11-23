"""
The shared password vault: Xod pulls a login from Tree (owner directive,
2026-11-22). Xod's own Keychain vault (services/credential_vault.py) holds
the logins Xod itself saved. When a site has no login there, this broker
asks Tree, the family's Muse, who holds the family's saved logins through
its own vault: if Tree has the site, the credential crosses once, is used
for the one sign-in, and is gone. Grant once and Xod acts, passwords
included; nothing here asks the owner anything.

RESOLUTION (credential_vault.get_login, on a local miss):
  local Keychain vault -> this broker -> 1Password -> the existing
  Take Over sign-in note. A broker miss changes nothing downstream.

TRANSPORT, two seams, in order:
  1. An injected provider (set_provider): a callable fn(site) ->
     (username, password) | None. This is the Xod Connect seam: a live
     Connect session to Tree registers a provider and answers in-process.
     Tests inject fakes here, exactly like muse_escalate's _send_fn.
  2. The tree-inbox route (services/tree_inbox.py, the route
     muse_escalate already rides): the request goes out as a kind "task"
     message whose text carries the ask in plain words and whose
     structured "credential_request" field names the request id and
     site. Tree's side answers by writing one response file per request
     into the response directory (a sibling of the tree-inbox folder
     named "tree-vault-responses"; env XOD_TREE_VAULT_DIR overrides):
       {"request_id", "site", "found": true, "username", "password"}
       {"request_id", "site", "found": false}
     Xod polls for that file until the deadline, reads it, and DELETES
     it immediately. If no shared inbox is reachable there is no
     transport: the broker returns None at once rather than queueing a
     request nobody can answer in time.

THE VAULT CONTRACT:
  - A brokered password is never persisted by Xod: not the Keychain
    vault, not the vault index, not any file, not any log. It lives in
    the caller's memory for the one sign-in, like a 1Password value.
  - The audit (DATA_DIR/muse_vault/audit.jsonl, the muse_review.audit
    pattern) records only timestamp, site, source and outcome. Never a
    username, never a password, never a fragment of either.
  - Fail closed: unknown site, Tree unreachable, timeout, malformed
    response, provider raising -- all return None. The broker never
    guesses, generates, or reuses a password for a different site.
  - A short negative cache (NEGATIVE_TTL) stops a mission paying the
    full timeout again for a site Tree just said it does not have.

This module never raises into the vault or the browser: a broken broker
path must not break a sign-in that would have worked locally.
"""
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger("xod.muse_vault")

DEFAULT_TIMEOUT = 20.0     # seconds to wait for Tree's response file
POLL_INTERVAL = 0.25
NEGATIVE_TTL = 60.0        # seconds a not_found / unreachable answer is cached per site

_provider: Optional[Callable] = None   # tests / Xod Connect inject a provider here
_send_fn: Optional[Callable] = None    # tests inject a fake tree_inbox.send_message here
_negative: Dict[str, float] = {}       # site -> time.time() when the miss expires
_stats: Dict[str, int] = {"supplied": 0, "not_found": 0, "unreachable": 0,
                          "failed": 0, "no_transport": 0}


def set_provider(fn: Optional[Callable]) -> None:
    """Register (or clear, with None) the in-process credential provider:
    fn(site) -> (username, password) | None. The Xod Connect seam."""
    global _provider
    _provider = fn


# ------------------------------------------------------------------ audit
def _dir() -> Path:
    from app.paths import DATA_DIR
    d = DATA_DIR / "muse_vault"
    d.mkdir(parents=True, exist_ok=True)
    return d


def audit_path() -> Path:
    return _dir() / "audit.jsonl"


def audit(record: Dict[str, Any]) -> None:
    """One JSON line per broker event: site, source, outcome, and nothing
    else. A failed write is logged and swallowed: the audit must never
    break a sign-in."""
    rec = {"ts": time.time(), **record}
    try:
        with open(audit_path(), "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception as e:
        logger.warning(f"vault audit write failed: {e}")


def _record(site: str, source: str, outcome: str) -> None:
    if outcome in _stats:
        _stats[outcome] += 1
    audit({"site": site, "source": source, "outcome": outcome})


# ------------------------------------------------------------------ transport
def responses_dir() -> Optional[Path]:
    """Where Tree's response files land, or None when no shared space is
    reachable. A sibling of the tree-inbox folder, so it inherits the
    inbox's own resolution (env, setting, rdrive space, family space)."""
    override = os.environ.get("XOD_TREE_VAULT_DIR")
    if override:
        return Path(override)
    try:
        from app.services import tree_inbox
        inbox = tree_inbox.inbox_dir()
    except Exception:
        return None
    if inbox is None:
        return None
    return inbox.parent / "tree-vault-responses"


def _request_id(site: str) -> str:
    raw = f"{site}\n{time.time()}\n{uuid.uuid4()}"
    return "vaultreq-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _render_text(site: str, request_id: str) -> str:
    """The request as plain words. The tree-inbox watcher delivers exactly
    this text to Tree, so everything Tree needs must be here -- except
    the credential itself, which never travels in text."""
    return "\n".join([
        f"XOD CREDENTIAL REQUEST id {request_id}",
        "Xod needs a saved login it does not have, for a sign-in it is "
        "performing right now. This is yours (Tree) to answer from your "
        "own saved logins; the owner is not to be asked (he already "
        "granted this: Xod pulls what Tree has).",
        f"Site: {site}",
        "To answer: write one JSON file named "
        f"{request_id}.json into the tree-vault-responses folder next "
        "to this tree-inbox, containing {\"request_id\": "
        f"\"{request_id}\", \"site\": \"{site}\", \"found\": true, "
        "\"username\": \"...\", \"password\": \"...\"} -- or \"found\": "
        "false if you have no login for this site. Never put the "
        "credential in chat text, an email, or an inbox message; the "
        "response file is the only channel, and Xod deletes it the "
        "moment it reads it.",
    ])


def _ask_tree(site: str, timeout: float) -> Optional[Tuple[str, str]]:
    """One tree-inbox round trip. Returns (username, password), or None
    for not-found / unreachable / timeout / malformed (the caller audits
    the distinction through _last_outcome)."""
    global _last_outcome
    rdir = responses_dir()
    if rdir is None:
        _last_outcome = "no_transport"
        return None
    request_id = _request_id(site)
    payload = {"version": 1, "request_id": request_id, "site": site,
               "ts": time.time()}
    try:
        if _send_fn is not None:
            res = _send_fn("task", _render_text(site, request_id), payload)
        else:
            from app.services import tree_inbox
            res = tree_inbox.send_message(
                "task", _render_text(site, request_id),
                extra={"credential_request": payload})
    except Exception as e:
        logger.warning(f"credential request for {site} could not be sent: {e}")
        _last_outcome = "failed"
        return None
    if (res or {}).get("state") != "inbox":
        # Queued in the local outbox: nobody can answer in time, and a
        # stale request landing later must not be waited on.
        _last_outcome = "unreachable"
        return None
    path = rdir / f"{request_id}.json"
    deadline = time.time() + max(0.0, timeout)
    while True:
        try:
            if path.is_file():
                raw = path.read_text(encoding="utf-8")
                try:
                    path.unlink()
                except OSError:
                    pass
                data = json.loads(raw)
                if data.get("request_id") != request_id or data.get("site") != site:
                    _last_outcome = "failed"
                    return None
                if not data.get("found"):
                    _last_outcome = "not_found"
                    return None
                username = str(data.get("username") or "")
                password = str(data.get("password") or "")
                if not username or not password:
                    _last_outcome = "failed"
                    return None
                _last_outcome = "supplied"
                return (username, password)
        except Exception as e:
            logger.warning(f"credential response for {site} unreadable: {e}")
            _last_outcome = "failed"
            return None
        if time.time() >= deadline:
            _last_outcome = "unreachable"
            return None
        time.sleep(POLL_INTERVAL)


_last_outcome = ""


# ------------------------------------------------------------------ the broker
def get_login(url_or_domain: str, timeout: float = DEFAULT_TIMEOUT
              ) -> Optional[Tuple[str, str]]:
    """(username, password) for the site from Tree, or None. Called by
    credential_vault.get_login on a local miss; browser code never calls
    this directly. Never raises, never persists, never logs a value."""
    try:
        from app.services import credential_vault
        site = credential_vault.site_key(url_or_domain)
    except Exception:
        return None
    if not site:
        return None
    until = _negative.get(site, 0.0)
    if until and time.time() < until:
        return None
    if _provider is not None:
        try:
            got = _provider(site)
        except Exception as e:
            logger.warning(f"credential provider failed for {site}: {e}")
            _record(site, "provider", "failed")
            return None
        if got:
            _record(site, "provider", "supplied")
            return (str(got[0]), str(got[1]))
        _record(site, "provider", "not_found")
        _negative[site] = time.time() + NEGATIVE_TTL
        return None
    got = _ask_tree(site, timeout)
    outcome = _last_outcome or "failed"
    _record(site, "tree-inbox", outcome)
    if got:
        return got
    if outcome in ("not_found", "unreachable"):
        _negative[site] = time.time() + NEGATIVE_TTL
    return None


def status() -> Dict[str, Any]:
    return {"provider": _provider is not None,
            "transport": responses_dir() is not None,
            "stats": dict(_stats),
            "negative_cached": sorted(s for s, t in _negative.items()
                                      if t > time.time())}
