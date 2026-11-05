"""
SSO control: one identity, one enrollment, one gate (2026-11-03).

The Interop Principle: one identity (SSO via rdrive, GitHub
first), one enrollment (the signed family registry), one pairing
story. This module is the desktop half of that principle — the
aggregator behind the Family panel's control surface and the ONE
gate every family control decision checks:

  LEG 1, REGISTRY: this device is a TRUSTED member of the signed
  family registry (services/family_registry.py — record signature
  verified, approval signature covers the set, no verified
  revocation names it). Unsigned/display data never feeds this.

  LEG 2, SESSION: a properly logged-in SSO session for the SAME
  account, provider GitHub — proven, never asserted. Providers,
  in order: the GitHub device flow (services/github_device.py;
  GitHub itself states the login, no client_secret anywhere), then
  an rdrive session read through GET /api/status (which today
  proves the account but NOT the method, so on its own it stays
  unproven and the gate refuses — fail-closed, reason named).

Either leg missing, mismatched, revoked, or merely unproven ->
control is refused with plain reasons, and nothing is written.

Direction (2026-11-03): no secret courier. The one secret the
fleet genuinely needs — the rdrive server's GitHub client secret
for its WEB OAuth exchange — travels as a vault service secret:
staged once through secure entry under a namespaced field
("service:rdrive_github_client_secret"), published inside the
Family Link vault bundle (per-recipient ciphertext, the shipped
pattern), and landed in each trusted member's keystore by the
existing family_vault.apply(). Presence is reported; a value is
never returned, masked, or logged by this module. The server-side
read (rdrive consuming the applied field as its env fallback) is
the named Lane A follow-on.

Secrets discipline: session/GitHub tokens live only in the OS
keychain; control_state() returns account names, provider names,
presence booleans and masked key summaries (secret_store.status)
— never a token or a key value.
"""
import json
import re
import os
import time
import urllib.request
import urllib.error
from typing import Any, Callable, Dict, List, Optional

from app.services import family_registry as fr
from app.services import github_device

RDRIVE_TOKEN_ACCOUNT = "rdrive_session_token"
SERVICE_SECRET_FIELDS = {
    "rdrive_github_client_secret": "service:rdrive_github_client_secret",
}
_GITHUB_CACHE: Dict[str, Any] = {}
_GITHUB_TTL = 60.0


# ---------------------------------------------------------------- config

def _setting(name: str, default: str = "") -> str:
    try:
        from app.services.storage import get_setting
        return get_setting(name, default) or default
    except Exception:
        return default


def rdrive_url() -> str:
    return (os.environ.get("XOD_RDRIVE_URL") or _setting("rdrive_url", "")).strip().rstrip("/")


def github_client_id() -> str:
    return (os.environ.get("XOD_GITHUB_CLIENT_ID") or _setting("github_client_id", "")).strip()


_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def set_github_client_id(cid: str) -> Dict[str, Any]:
    """Save Xod's GitHub app Client ID (public, not a secret) for device sign-in here and on your phones."""
    cid = (cid or "").strip()
    if not _CLIENT_ID_RE.match(cid):
        return {"success": False, "error": "That doesn't look like a GitHub Client ID (letters and digits, like Ov23li…)."}
    from app.services.storage import set_setting
    set_setting("github_client_id", cid)
    return {"success": True, "github_client_id": cid}


def _keychain():
    from app.services import keychain
    return keychain, fr.FAMILY_SERVICE


def rdrive_token() -> str:
    try:
        keychain, service = _keychain()
        return keychain.get_password(service, RDRIVE_TOKEN_ACCOUNT) or ""
    except Exception:
        return ""


def store_rdrive_token(token: str) -> bool:
    """The handoff slot: software (the rdrive desktop app, or the
    device-flow follow-on on the server) hands Xod a session token
    without the user ever touching it. Stored in the keychain;
    never echoed."""
    token = (token or "").strip()
    if not token:
        return False
    keychain, service = _keychain()
    keychain.set_password(service, RDRIVE_TOKEN_ACCOUNT, token)
    return True


# ---------------------------------------------------------------- rdrive probe

def _fetch_json(url: str, headers: Optional[Dict[str, str]] = None) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "xod-family-link", **(headers or {})})
    with urllib.request.urlopen(req, timeout=3) as resp:
        return json.loads(resp.read().decode("utf-8"))


def rdrive_status(fetch: Callable = _fetch_json) -> Dict[str, Any]:
    """What the rdrive server can prove right now. Never raises;
    an unreachable server is UNKNOWN, never 'signed out'."""
    url = rdrive_url()
    if not url:
        return {"configured": False, "reachable": False, "user": None,
                "github_configured": None, "method": None, "github_login": None,
                "github_client_id": None}
    headers = {}
    token = rdrive_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        out = fetch(f"{url}/api/status", headers)
    except Exception as e:
        return {"configured": True, "reachable": False, "user": None,
                "github_configured": None, "method": None, "github_login": None,
                "github_client_id": None,
                "problem": f"rdrive could not be reached ({type(e).__name__})"}
    if not isinstance(out, dict):
        return {"configured": True, "reachable": False, "user": None,
                "github_configured": None, "method": None, "github_login": None,
                "github_client_id": None, "problem": "rdrive answered in an unexpected shape"}
    return {"configured": True, "reachable": True,
            "user": out.get("user"),
            "github_configured": bool(out.get("github")) if "github" in out else None,
            "method": out.get("method"),
            "github_login": out.get("github_login") or out.get("githubLogin"),
            "github_client_id": out.get("github_client_id") or out.get("githubClientId")}


# ---------------------------------------------------------------- session leg

def session(fetch: Callable = _fetch_json, get_json: Callable = None) -> Dict[str, Any]:
    """The current session claim, best provider first. A claim is
    only ever as strong as its proof (module docstring)."""
    # A. GitHub device flow: GitHub itself states the login.
    if github_device.stored_token():
        now = time.time()
        cached = _GITHUB_CACHE.get("claim")
        if cached and now - _GITHUB_CACHE.get("at", 0) < _GITHUB_TTL:
            return cached
        claim = github_device.verify(get_json=get_json) if get_json else github_device.verify()
        if claim.get("state") == "signed_in":
            _GITHUB_CACHE.update({"claim": claim, "at": now})
            return claim
        if claim.get("state") == "unknown":
            return claim     # unreachable GitHub: UNKNOWN, never a clean sign-out
        _GITHUB_CACHE.clear()
        github_device.forget_token()   # a token GitHub rejects is not kept
    # B. rdrive session via /api/status (account proven, method not).
    st = rdrive_status(fetch)
    base = {"github_configured": st.get("github_configured"), "rdrive": st}
    if not st["configured"]:
        return {"state": "unconfigured", "account": None, "provider": None,
                "source": "none", **base,
                "message": "Sign-in isn't set up yet: no GitHub sign-in and no rdrive pointed to."}
    if not st["reachable"]:
        return {"state": "unknown", "account": None, "provider": None,
                "source": "rdrive-status", **base,
                "message": st.get("problem") or "rdrive could not be reached."}
    if not st["user"]:
        return {"state": "signed_out", "account": None, "provider": None,
                "source": "rdrive-status", **base}
    provider = "github" if st.get("method") == "github" else None
    return {"state": "signed_in", "account": st["user"], "provider": provider,
            "method": st.get("method"), "github_login": st.get("github_login"),
            "source": "rdrive-status", **base}


def client_id(fetch: Callable = _fetch_json) -> str:
    """The public GitHub client id for device flow: configured
    first, then discovered from rdrive (a client id is not a
    secret)."""
    cid = github_client_id()
    if cid:
        return cid
    st = rdrive_status(fetch)
    return (st.get("github_client_id") or "").strip()


# ---------------------------------------------------------------- the gate

def _registry_github_login(view: Dict[str, Any]) -> Optional[str]:
    """The registry's optional top-level github_login claim (see the
    design doc: not yet under the approval signature — a binding
    hint for THIS device's session leg, never a trust source)."""
    try:
        data = json.loads(fr.registry_path().read_text(encoding="utf-8"))
        v = data.get("github_login")
        return v if isinstance(v, str) and v else None
    except Exception:
        return None


def _account_matches(claim: Dict[str, Any], view: Dict[str, Any]) -> bool:
    want = {(view.get("account") or "").lower()}
    gh = _registry_github_login(view)
    if gh:
        want.add(gh.lower())
    have = {(claim.get("account") or "").lower(),
            (claim.get("github_login") or "").lower()}
    have |= {str(e).lower() for e in (claim.get("emails") or []) if e}   # verified by GitHub (github_device)
    return bool(want & (have - {""}))


def gate(claim: Optional[Dict[str, Any]] = None, view: Optional[Dict[str, Any]] = None,
         fetch: Callable = _fetch_json, get_json: Callable = None) -> Dict[str, Any]:
    """Both legs or refusal. Returns the verdict and the reasons in
    plain words; callers render the reasons, they do not soften them."""
    view = view if view is not None else fr.load_registry()
    claim = claim if claim is not None else session(fetch, get_json)
    reasons: List[str] = []
    my_id = fr.my_device_id() or None
    my_rec = fr.trusted_member(view, my_id) if my_id else None
    revoked = bool(my_id and my_id in (view.get("revoked_by_registry") or []))
    # Leg 1 — registry.
    if view.get("state") != "ok":
        reasons.append("There is no readable family registry yet, so this device can't prove it belongs.")
    elif revoked:
        reasons.append("This device was removed from the family (a signed revocation names it).")
    elif not my_id:
        reasons.append("This device has no family device id yet.")
    elif my_rec is None:
        reasons.append("This device is not a verified family member yet — its record is unsigned, "
                       "unapproved, or not covered by the family approval.")
    # Leg 2 — session.
    st = claim.get("state")
    if st == "unconfigured":
        reasons.append("Sign-in isn't set up yet (no GitHub sign-in, no rdrive).")
    elif st == "unknown":
        reasons.append("Sign-in couldn't be checked just now — that's treated as not signed in, never as a pass.")
    elif st == "signed_out":
        reasons.append("You're not signed in. Sign in with GitHub first.")
    elif st == "signed_in":
        if claim.get("provider") != "github":
            if claim.get("method") == "password":
                reasons.append("This session used a password, not GitHub sign-in.")
            else:
                reasons.append("This session's sign-in method isn't proven to be GitHub yet.")
        elif view.get("account") and not _account_matches(claim, view):
            reasons.append("You're signed in as a different account than this family's.")
    return {"allowed": not reasons, "reasons": reasons,
            "session": {k: claim.get(k) for k in
                        ("state", "account", "provider", "method", "source", "github_login")},
            "registry": {"family": view.get("family"), "account": view.get("account"),
                         "state": view.get("state"), "my_device_id": my_id,
                         "my_trusted": my_rec is not None, "revoked": revoked,
                         "interim_unsigned": bool(view.get("interim_unsigned"))}}


# ---------------------------------------------------------------- service secrets (vault)

def stage_service_secret(name: str, value: str) -> Dict[str, Any]:
    """Secure-entry staging for the ONE whitelisted service secret.
    The value goes straight into the keystore under its namespaced
    vault field; it is never echoed, masked, or logged."""
    field = SERVICE_SECRET_FIELDS.get((name or "").strip())
    if not field:
        return {"success": False, "state": "unknown_field",
                "message": "That is not a service secret this family carries."}
    value = (value or "").strip()
    if not value:
        return {"success": False, "state": "empty", "message": "Nothing to stage."}
    from app.services import secret_store
    secret_store.save(field, value)
    return {"success": True, "state": "staged", "field": field,
            "message": "Staged in this device's keystore. Publish the family vault to share it."}


def _staged_service_secrets() -> Dict[str, str]:
    from app.services import secret_store
    out = {}
    for field in SERVICE_SECRET_FIELDS.values():
        v = secret_store.stored(field)
        if v:
            out[field] = v
    return out


def publish_service_secrets(my_id: str, view=None) -> Dict[str, Any]:
    """Publish the vault bundle carrying provider keys PLUS any
    staged service secrets, through the shipped family_vault
    pattern (per-recipient ciphertext). Returns field NAMES only."""
    from app.services import family_vault, secret_store
    secrets = {}
    for field in secret_store.SECRET_FIELDS:
        v = secret_store.stored(field)
        if v:
            secrets[field] = v
    secrets.update(_staged_service_secrets())
    if not secrets:
        raise family_vault.VaultError("this device holds no keys or service secrets to share")
    return family_vault.publish(secrets, my_id, view=view)


def service_secret_state() -> Dict[str, Any]:
    """Presence only: is the service secret staged/applied in THIS
    device's keystore. Never a value, never a mask."""
    from app.services import secret_store
    out = {}
    for name, field in SERVICE_SECRET_FIELDS.items():
        out[name] = {"field": field, "present": bool(secret_store.stored(field))}
    return out


# ---------------------------------------------------------------- control state

def control_state(fetch: Callable = _fetch_json, get_json: Callable = None) -> Dict[str, Any]:
    """Everything the Family panel's control surface renders, in one
    honest aggregate. No token or key value appears anywhere in it."""
    from app.services import family_vault, secret_store
    view = fr.load_registry()
    my_id = fr.my_device_id() or None
    claim = session(fetch, get_json)
    verdict = gate(claim, view)
    vault = family_vault.status(view, my_id)
    keys = secret_store.status()
    # Devices: the enroll state already merges registry + links.
    devices, pending, joining = [], [], []
    try:
        from app.services import phone_enroll
        st = phone_enroll.state()
        devices = st.get("members") or []
        pending = st.get("pending") or []
        joining = st.get("joining") or []
    except Exception:
        devices = [{"id": d["id"], "name": d["name"], "platform": d["platform"],
                    "status": d["status"], "verified": bool(d.get("verified")),
                    "trusted": d in (view.get("trusted_members") or []),
                    "capabilities": d.get("capabilities") or [],
                    "last_seen": d.get("last_seen")} for d in view.get("devices") or []]
    bundle_v = int(vault.get("version") or 0)
    applied_v = int(vault.get("applied_version") or 0)
    is_recipient = bool(vault.get("i_am_recipient"))
    missing_keys = [k["name"] for k in keys if not k["saved"]]
    recovery = {
        "needed": bool(is_recipient and bundle_v > applied_v),
        "bundle_version": bundle_v, "applied_version": applied_v,
        "is_recipient": is_recipient, "missing_keys_here": missing_keys,
        "message": ("This device's keys are behind the family vault's — applying the vault "
                    "restores them, no typing." if is_recipient and bundle_v > applied_v else ""),
    }
    steps = _setup_steps(claim, view, verdict, vault, recovery)
    return {
        "success": True,
        "account": {"family": view.get("family"),
                    "account": view.get("account") or claim.get("account"),
                    "provider": claim.get("provider"),
                    "session_state": claim.get("state"),
                    "session_source": claim.get("source"),
                    "github_configured": claim.get("github_configured"),
                    "rdrive_configured": bool(rdrive_url()),
                    "device_flow_configured": bool(client_id(fetch))},
        "gate": verdict,
        "devices": devices,
        "vault": vault,
        "keys": keys,
        "service_secrets": service_secret_state(),
        "approvals": {"phone_pending": len(pending), "phone_pending_items": pending,
                      "joining": len(joining)},
        "recovery": recovery,
        "setup": steps,
        "working": bool(verdict["allowed"] and not recovery["needed"]),
    }


def _setup_steps(claim, view, verdict, vault, recovery) -> List[Dict[str, Any]]:
    """The fresh-install path as computed states: done | next |
    blocked, in order. 'Working' is the last step, and it is the
    gate itself."""
    def s(id_, label, state, detail=""):
        return {"id": id_, "label": label, "state": state, "detail": detail}
    steps = []
    rd = claim.get("rdrive") or {}
    if rd.get("reachable"):
        steps.append(s("rdrive", "rdrive reachable", "done", rdrive_url()))
    elif rd.get("configured"):
        steps.append(s("rdrive", "Reach your rdrive", "next",
                       rd.get("problem") or "Your rdrive didn't answer."))
    else:
        steps.append(s("rdrive", "Point Xod at your rdrive", "blocked",
                       "One setting (rdrive_url). Device sign-in below works without it."))
    if claim.get("state") == "signed_in" and claim.get("provider") == "github":
        steps.append(s("signin", f"Signed in as {claim.get('account')} (GitHub)", "done"))
    elif claim.get("state") == "signed_in":
        steps.append(s("signin", "Prove GitHub sign-in", "next",
                       "This session isn't proven GitHub yet; the device sign-in below proves it."))
    else:
        steps.append(s("signin", "Sign in with GitHub", "next",
                       "Use the device code below — approve it from any browser, phone included."))
    if view.get("state") == "ok":
        steps.append(s("registry", f"Family registry found ({view.get('family')})", "done"))
    else:
        steps.append(s("registry", "Family registry", "blocked",
                       view.get("message") or "No registry yet."))
    if verdict["registry"]["my_trusted"]:
        steps.append(s("enrolled", "This device is a verified family member", "done"))
    elif verdict["registry"]["revoked"]:
        steps.append(s("enrolled", "This device was removed from the family", "blocked",
                       "Re-enroll with a rotated family key."))
    else:
        steps.append(s("enrolled", "Enroll this device", "next",
                       "One approval from a family member (your phone can approve)."))
    if recovery["needed"]:
        steps.append(s("vault", "Apply the family vault", "next", recovery["message"]))
    elif int(vault.get("applied_version") or 0) > 0 or not recovery["missing_keys_here"]:
        steps.append(s("vault", "Keys from the family vault", "done"))
    else:
        steps.append(s("vault", "Keys from the family vault", "next",
                       "No bundle applied yet and some provider keys aren't here."))
    steps.append(s("working", "Working with the family",
                   "done" if verdict["allowed"] and not recovery["needed"] else "next",
                   "" if verdict["allowed"] else " ".join(verdict["reasons"])))
    return steps
