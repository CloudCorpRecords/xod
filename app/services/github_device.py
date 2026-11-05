"""
GitHub device authorization flow: sign in with only a client_id.

Direction (2026-11-03): the owner never acts as the secret courier, so
client sign-in must not need a client_secret on any machine. The
device flow is that path: this device asks GitHub for a short
user code, the owner authorizes it at github.com/login/device
from any browser (the phone counts — the approver the owner carries),
and this device polls until GitHub issues an access token. No
client_secret is sent, stored, or logged anywhere in this module;
the rdrive server's RDRIVE_GITHUB_CLIENT_SECRET stays empty and
unused by clients (the vault carries it for the server's own web
OAuth only — services/sso_control.py, service-secret section).

The access token lands ONLY in the OS keychain (family service,
alongside the family device keys). It is never returned by any
function here except internally to verify identity, never logged,
and never written to disk or the synced space.

Identity proof: GET https://api.github.com/user with the token —
GitHub itself states the login, which is what the SSO gate
(services/sso_control.py) consumes as the session claim.

Transport is injectable (post_form / get_json callables) so tests
drive the whole flow without the network.
"""
import json
import time
import urllib.request
import urllib.error
from typing import Any, Callable, Dict, Optional

DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
EMAILS_URL = "https://api.github.com/user/emails"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
SCOPE = "read:user user:email"   # user:email: verified emails, so a family listed by email matches
TOKEN_ACCOUNT = "github_access_token"

_pending: Dict[str, Any] = {}   # one flow at a time, in memory only


# ---------------------------------------------------------------- transport

def _post_form(url: str, data: Dict[str, str]) -> Any:
    body = "&".join(f"{k}={urllib.request.quote(str(v), safe='')}"
                    for k, v in data.items()).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "xod-family-link",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"error": f"http_{e.code}"}


def _get_json(url: str, headers: Dict[str, str]) -> Any:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "xod-family-link", **headers})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _token_keychain():
    from app.services import keychain, family_registry
    return keychain, family_registry.FAMILY_SERVICE


def stored_token() -> str:
    """The GitHub access token from the keystore, or "". Never logged."""
    try:
        keychain, service = _token_keychain()
        return keychain.get_password(service, TOKEN_ACCOUNT) or ""
    except Exception:
        return ""


def _store_token(token: str) -> None:
    keychain, service = _token_keychain()
    keychain.set_password(service, TOKEN_ACCOUNT, token)


def forget_token() -> None:
    try:
        keychain, service = _token_keychain()
        keychain.delete_password(service, TOKEN_ACCOUNT)
    except Exception:
        pass


# ---------------------------------------------------------------- the flow

def begin(client_id: str, post_form: Callable = _post_form) -> Dict[str, Any]:
    """Start a device flow. Returns what the panel shows: the short
    code and where to enter it. The device_code itself stays here,
    in memory; it is not a credential and expires in minutes."""
    client_id = (client_id or "").strip()
    if not client_id:
        return {"state": "unconfigured",
                "message": "No GitHub client id is configured for sign-in yet."}
    out = post_form(DEVICE_CODE_URL, {"client_id": client_id, "scope": SCOPE})
    if not isinstance(out, dict) or not out.get("device_code"):
        return {"state": "failed",
                "message": f"GitHub did not start a device sign-in ({(out or {}).get('error', 'no response')})."}
    _pending.clear()
    _pending.update({
        "device_code": out["device_code"], "client_id": client_id,
        "interval": max(1, int(out.get("interval") or 5)),
        "expires_at": time.time() + int(out.get("expires_in") or 900),
    })
    return {"state": "pending", "user_code": out.get("user_code"),
            "verification_uri": out.get("verification_uri") or "https://github.com/login/device",
            "expires_in": int(out.get("expires_in") or 900),
            "interval": _pending["interval"],
            "message": "Enter this code at github.com/login/device and approve. "
                       "You can do it from your phone."}


def poll(post_form: Callable = _post_form, get_json: Callable = _get_json) -> Dict[str, Any]:
    """One poll of the pending flow. States: pending, slow_down,
    signed_in, denied, expired, none (no flow running), failed.
    On signed_in the token is stored in the keychain and NOT
    returned; the identity summary (login/id only) is."""
    if not _pending:
        return {"state": "none", "message": "No device sign-in is running."}
    if time.time() > _pending["expires_at"]:
        _pending.clear()
        return {"state": "expired", "message": "That code expired. Start sign-in again."}
    out = post_form(ACCESS_TOKEN_URL, {
        "client_id": _pending["client_id"], "device_code": _pending["device_code"],
        "grant_type": GRANT_TYPE})
    if not isinstance(out, dict):
        return {"state": "failed", "message": "GitHub did not answer the sign-in poll."}
    token = out.get("access_token")
    if token:
        _pending.clear()
        _store_token(str(token))
        who = verify(get_json=get_json)
        return {"state": "signed_in", "account": who.get("account"),
                "github_id": who.get("github_id"),
                "message": "Signed in with GitHub."}
    err = out.get("error") or ""
    if err == "authorization_pending":
        return {"state": "pending", "message": "Waiting for the approval on GitHub…"}
    if err == "slow_down":
        _pending["interval"] = int(out.get("interval") or _pending["interval"] + 5)
        return {"state": "slow_down", "interval": _pending["interval"],
                "message": "GitHub asked us to slow down; still waiting."}
    if err == "access_denied":
        _pending.clear()
        return {"state": "denied", "message": "The sign-in was denied on GitHub."}
    if err in ("expired_token", "bad_verification_code"):
        _pending.clear()
        return {"state": "expired", "message": "That code expired. Start sign-in again."}
    return {"state": "failed", "message": f"GitHub sign-in did not complete ({err or 'unknown error'})."}


def verify(token: Optional[str] = None, get_json: Callable = _get_json,
           post_form: Callable = None) -> Dict[str, Any]:
    """The identity claim for a stored token, proven by GitHub:
    {state: signed_in, account: login, github_id, provider: github}
    or an honest non-claim. Never returns the token."""
    token = token if token is not None else stored_token()
    if not token:
        return {"state": "signed_out", "account": None, "provider": None,
                "source": "github-device"}
    try:
        out = get_json(USER_URL, {"Authorization": f"Bearer {token}"})
    except Exception as e:
        return {"state": "unknown", "account": None, "provider": None,
                "source": "github-device",
                "message": f"GitHub could not be reached to check sign-in ({type(e).__name__})."}
    if not isinstance(out, dict) or not out.get("login"):
        return {"state": "signed_out", "account": None, "provider": None,
                "source": "github-device",
                "message": "The saved GitHub sign-in is no longer valid."}
    return {"state": "signed_in", "account": out["login"], "github_id": out.get("id"),
            "emails": verified_emails(token, get_json),
            "provider": "github", "method": "device", "source": "github-device"}


def verified_emails(token: str, get_json: Callable = _get_json) -> list:
    """The account's VERIFIED email addresses, as GitHub states them (lowercased). A family listed under an email
    matches through these. A token from before the user:email scope gets none; that is not an error."""
    try:
        out = get_json(EMAILS_URL, {"Authorization": f"Bearer {token}"})
    except Exception:
        return []
    if not isinstance(out, list):
        return []
    return [str(e.get("email")).lower() for e in out
            if isinstance(e, dict) and e.get("verified") is True and e.get("email")]
