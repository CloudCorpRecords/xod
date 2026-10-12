"""
Signing in to hosted MCP servers (Notion, Linear, Google…) with OAuth.

The MCP SDK does the protocol: it discovers the server's authorization server, registers Xod as
a client when the server allows it (dynamic client registration), runs the PKCE code flow,
checks the state, and refreshes tokens. Xod supplies the three things around it:

- storage: tokens and the client registration live in the macOS Keychain ("Xod MCP"), never in files;
- the browser: the sign-in page opens in your normal browser, only when you press Connect, never
  from a background reconnect;
- the callback: the provider sends you back to http://127.0.0.1:<port>/oauth/callback on Xod's own
  server, which hands the code to the waiting sign-in (matched by its one-time state value).
"""
import asyncio
import json
import logging
import subprocess
import time
import urllib.parse
from typing import Any, Dict, Optional

from app.services import keychain

logger = logging.getLogger("xod.mcp")

KEYCHAIN = "Xod MCP"
SIGN_IN_TIMEOUT = 300

# state -> future that receives (code, state, iss) from /oauth/callback
_waiting: Dict[str, asyncio.Future] = {}
# server name -> {"url": the sign-in page, "at": time} while a sign-in is open
sign_in_pages: Dict[str, Dict[str, Any]] = {}


class KeychainTokenStorage:
    """The SDK's TokenStorage, kept in the Keychain under "<server>:oauth_tokens" / ":oauth_client"."""

    def __init__(self, server: str, redirect_uri: Optional[str] = None):
        self.server, self.redirect_uri = server, redirect_uri

    def _get(self, kind: str) -> Optional[dict]:
        raw = keychain.get_password(KEYCHAIN, f"{self.server}:{kind}")
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    def _set(self, kind: str, value: Any) -> None:
        keychain.set_password(KEYCHAIN, f"{self.server}:{kind}", json.dumps(value))

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        data = self._get("oauth_tokens")
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens) -> None:
        self._set("oauth_tokens", tokens.model_dump(mode="json", exclude_none=True))

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        data = self._get("oauth_client")
        if not data:
            return None
        info = OAuthClientInformationFull.model_validate(data)
        # Registered for another address (Xod's port changed): register again rather than fail.
        if self.redirect_uri and info.redirect_uris and self.redirect_uri not in [str(u) for u in info.redirect_uris] \
                and not data.get("_byo"):
            return None
        return info

    async def set_client_info(self, client_info) -> None:
        old = self._get("oauth_client") or {}
        data = client_info.model_dump(mode="json", exclude_none=True)
        if old.get("_byo"):
            data["_byo"] = True
        self._set("oauth_client", data)


def has_tokens(server: str) -> bool:
    return bool(keychain.get_password(KEYCHAIN, f"{server}:oauth_tokens"))


def set_own_client(server: str, client_id: str, client_secret: str, redirect_uri: str) -> None:
    """For providers without dynamic registration (Google): the user's own OAuth client."""
    keychain.set_password(KEYCHAIN, f"{server}:oauth_client", json.dumps({
        "client_id": client_id.strip(), "client_secret": client_secret.strip() or None,
        "redirect_uris": [redirect_uri], "token_endpoint_auth_method": "client_secret_post" if client_secret else "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "_byo": True}))


def forget(server: str) -> None:
    for kind in ("oauth_tokens", "oauth_client"):
        try:
            keychain.delete_password(KEYCHAIN, f"{server}:{kind}")
        except Exception:
            pass


def provider(server: str, url: str, redirect_uri: Optional[str], interactive: bool, scope: Optional[str] = None):
    """An httpx auth for this server. Non-interactive (background reconnects) never opens a browser:
    if a sign-in would be needed, it fails with SignInNeeded instead."""
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

    redirect = redirect_uri or "http://127.0.0.1:8000/oauth/callback"
    state_box: Dict[str, str] = {}

    async def redirect_handler(auth_url: str) -> None:
        if not interactive:
            raise SignInNeeded(server)
        state = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query).get("state", [""])[0]
        state_box["state"] = state
        _waiting[state] = asyncio.get_running_loop().create_future()
        sign_in_pages[server] = {"url": auth_url, "at": time.time()}
        open_in_browser(auth_url)

    async def callback_handler() -> AuthorizationCodeResult:
        state = state_box.get("state", "")
        fut = _waiting.get(state)
        if not fut:
            raise SignInNeeded(server)
        try:
            code, got_state, iss = await asyncio.wait_for(fut, SIGN_IN_TIMEOUT)
        finally:
            _waiting.pop(state, None)
            sign_in_pages.pop(server, None)
        return AuthorizationCodeResult(code=code, state=got_state, iss=iss)

    metadata = OAuthClientMetadata(
        client_name="Xod", client_uri="https://github.com/CloudCorpRecords/xod",
        redirect_uris=[redirect], grant_types=["authorization_code", "refresh_token"],
        response_types=["code"], token_endpoint_auth_method="none", scope=scope)
    return OAuthClientProvider(server_url=url, client_metadata=metadata,
                               storage=KeychainTokenStorage(server, redirect),
                               redirect_handler=redirect_handler, callback_handler=callback_handler)


class SignInNeeded(Exception):
    def __init__(self, server: str):
        super().__init__(f"Sign in to {server} to connect it.")
        self.server = server


def open_in_browser(url: str) -> None:
    # Only http(s) pages, passed as one argument (no shell).
    if urllib.parse.urlparse(url).scheme in ("http", "https"):
        try:
            subprocess.Popen(["/usr/bin/open", url])
        except Exception as e:
            logger.warning(f"couldn't open the browser: {e}")


def finish(code: str, state: str, iss: Optional[str] = None) -> bool:
    """Called by /oauth/callback. True if a sign-in was waiting for this state."""
    fut = _waiting.get(state or "")
    if not fut or fut.done():
        return False
    fut.set_result((code, state, iss))
    return True


def fail(state: str, error: str) -> bool:
    fut = _waiting.get(state or "")
    if not fut or fut.done():
        return False
    fut.set_exception(RuntimeError(f"Sign-in was cancelled or refused: {error}"))
    return True
