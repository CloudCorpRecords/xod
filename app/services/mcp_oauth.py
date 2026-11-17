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
import sys
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
GOOGLE_HOSTS = {"gmailmcp.googleapis.com", "drivemcp.googleapis.com", "calendarmcp.googleapis.com"}


class KeychainTokenStorage:
    """The SDK's TokenStorage, kept in the Keychain under "<server>:oauth_tokens" / ":oauth_client"."""

    def __init__(self, server: str, redirect_uri: Optional[str] = None):
        self.server, self.redirect_uri = server, redirect_uri
        self.provider = None

    def _get(self, kind: str) -> Optional[dict]:
        raw = keychain.get_password(KEYCHAIN, f"{self.server}:{kind}")
        try:
            value = json.loads(raw) if raw else None
            if keychain.problem():
                raise keychain.Unreadable("Unlock the key store and try connecting again.")
            return value if isinstance(value, dict) else None
        except ValueError:
            return None

    def _set(self, kind: str, value: Any) -> None:
        keychain.set_password(KEYCHAIN, f"{self.server}:{kind}", json.dumps(value))

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        if self.provider and getattr(self.provider, "_refresh_failed", False):
            return None  # do not reload a rejected grant on each anonymous discovery RPC
        data = self._get("oauth_tokens")
        if not data:
            return None
        if self.provider and data.get("_server_url") not in (None, self.provider.context.server_url):
            return None
        try:
            return OAuthToken.model_validate(data)
        except ValueError:
            return None

    async def set_tokens(self, tokens) -> None:
        data = tokens.model_dump(mode="json", exclude_none=True)
        if tokens.expires_in is not None:
            data["_expires_at"] = time.time() + tokens.expires_in
        if self.provider:
            context = self.provider.context
            data["_server_url"] = context.server_url
            if context.oauth_metadata:
                data["_oauth_metadata"] = context.oauth_metadata.model_dump(mode="json", exclude_none=True)
        self._set("oauth_tokens", data)
        if self.provider:
            self.provider._refresh_failed = False

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        data = self._get("oauth_client")
        if not data:
            return None
        info = OAuthClientInformationFull.model_validate(data)
        # Registered for another address (Xod's port changed): register again rather than fail.
        if self.redirect_uri and info.redirect_uris and self.redirect_uri not in [str(u) for u in info.redirect_uris]:
            if data.get("_byo"):
                raise ValueError("OAuth callback changed. Register the current callback URI with the provider, "
                                 "then enter your client details again.")
            return None
        return info

    async def set_client_info(self, client_info) -> None:
        old = self._get("oauth_client") or {}
        data = client_info.model_dump(mode="json", exclude_none=True)
        if old.get("_byo"):
            data["_byo"] = True
        self._set("oauth_client", data)


def has_tokens(server: str) -> bool:
    from mcp.shared.auth import OAuthToken
    data = KeychainTokenStorage(server)._get("oauth_tokens")
    try:
        return bool(data and OAuthToken.model_validate(data).access_token)
    except ValueError:
        return False


def set_own_client(server: str, client_id: str, client_secret: str, redirect_uri: str) -> None:
    """For providers without dynamic registration (Google): the user's own OAuth client."""
    if not client_id.strip():
        raise ValueError("Enter your OAuth client ID.")
    old = KeychainTokenStorage(server)._get("oauth_client") or {}
    value = {
        "client_id": client_id.strip(), "client_secret": client_secret.strip() or None,
        "redirect_uris": [redirect_uri], "token_endpoint_auth_method": "client_secret_post" if client_secret else "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "_byo": True}
    if any(old.get(k) != value.get(k) for k in ("client_id", "client_secret", "redirect_uris")):
        _delete(server, "oauth_tokens")
    keychain.set_password(KEYCHAIN, f"{server}:oauth_client", json.dumps(value))


def _delete(server: str, kind: str) -> None:
    from keyring.errors import PasswordDeleteError
    try:
        keychain.delete_password(KEYCHAIN, f"{server}:{kind}")
    except PasswordDeleteError:
        pass  # already absent; a locked/broken store must NOT be silently ignored


def cancel(server: str) -> None:
    page = sign_in_pages.pop(server, None)
    if page:
        future = _waiting.pop(page.get("state", ""), None)
        if future and not future.done():
            future.cancel()


def forget(server: str) -> None:
    cancel(server)
    for kind in ("oauth_tokens", "oauth_client"):
        _delete(server, kind)


def provider(server: str, url: str, redirect_uri: Optional[str], interactive: bool, scope: Optional[str] = None):
    """An httpx auth for this server. Non-interactive (background reconnects) never opens a browser:
    if a sign-in would be needed, it fails with SignInNeeded instead."""
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

    redirect = redirect_uri or "http://127.0.0.1:8000/oauth/callback"
    state_box: Dict[str, str] = {}
    storage = KeychainTokenStorage(server, redirect)

    async def redirect_handler(auth_url: str) -> None:
        if not result.allow_interactive:
            raise SignInNeeded(server)
        parsed = urllib.parse.urlparse(auth_url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            raise ValueError("The provider returned an invalid sign-in address.")
        query = urllib.parse.parse_qs(parsed.query)
        state = query.get("state", [""])[0]
        if not state or state in _waiting:
            raise ValueError("The provider returned a missing or duplicate OAuth state.")
        if (urllib.parse.urlparse(url).hostname in GOOGLE_HOSTS
                and parsed.scheme == "https" and parsed.hostname == "accounts.google.com"):
            # Google's documented refresh-token contract; offline_access is NOT equivalent.
            query.update(access_type=["offline"], prompt=["consent"])
            if scope:
                query["scope"] = [scope]  # never widen Google's configured read scopes
            auth_url = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))
        cancel(server)
        state_box["state"] = state
        _waiting[state] = asyncio.get_running_loop().create_future()
        sign_in_pages[server] = {"url": auth_url, "at": time.time(), "state": state}
        try:
            open_in_browser(auth_url)
        except Exception:
            cancel(server)
            raise

    async def callback_handler() -> AuthorizationCodeResult:
        state = state_box.get("state", "")
        fut = _waiting.get(state)
        if not fut:
            raise SignInNeeded(server)
        try:
            code, got_state, iss = await asyncio.wait_for(fut, SIGN_IN_TIMEOUT)
        finally:
            if _waiting.get(state) is fut:
                _waiting.pop(state, None)
            if sign_in_pages.get(server, {}).get("state") == state:
                sign_in_pages.pop(server, None)
        return AuthorizationCodeResult(code=code, state=got_state, iss=iss)

    metadata = OAuthClientMetadata(
        client_name="Xod", client_uri="https://github.com/CloudCorpRecords/xod",
        redirect_uris=[redirect], grant_types=["authorization_code", "refresh_token"],
        response_types=["code"], token_endpoint_auth_method="none", scope=scope)
    class PersistentProvider(OAuthClientProvider):
        async def _handle_refresh_response(self, response):
            ok = await super()._handle_refresh_response(response)
            # MCP reinitializes after a rejected refresh. Anonymous discovery
            # may succeed between that rejection and the account-read challenge;
            # reloading the same saved grant would retry it for every RPC.
            # Keep persisted credentials for transient provider failures, but
            # do not reuse them in this attempt until fresh consent succeeds.
            self._refresh_failed = not ok
            return ok

        async def _initialize(self):
            # MCP 2.3 loads token values, but does not restore their absolute
            # expiry or AS metadata. Without both a restart can re-authenticate
            # instead of refreshing, or send refresh to the resource's /token.
            await super()._initialize()
            data = storage._get("oauth_tokens") or {}
            if self.context.current_tokens:
                expiry = data.get("_expires_at")
                self.context.token_expiry_time = expiry if isinstance(expiry, (int, float)) else 1.0
                if data.get("_server_url") == url and data.get("_oauth_metadata"):
                    from mcp.shared.auth import OAuthMetadata
                    from mcp.client.auth.utils import credentials_match_issuer
                    saved = OAuthMetadata.model_validate(data["_oauth_metadata"])
                    if self.context.client_info and credentials_match_issuer(
                            self.context.client_info, str(saved.issuer), None):
                        self.context.oauth_metadata = saved
                # Legacy tokens have no persisted expiry/issuer metadata.
                # Let normal authenticated discovery run rather than guessing
                # a token endpoint or extending their lifetime at restart.
                if self.context.oauth_metadata is None:
                    self.context.token_expiry_time = None

    result = PersistentProvider(server_url=url, client_metadata=metadata,
                                storage=storage, redirect_handler=redirect_handler,
                                callback_handler=callback_handler)
    storage.provider = result
    result.allow_interactive = interactive
    return result


class SignInNeeded(Exception):
    def __init__(self, server: str):
        super().__init__(f"Sign in to {server} to connect it.")
        self.server = server


def open_in_browser(url: str) -> None:
    # Only http(s) pages, passed as one argument (no shell).
    if urllib.parse.urlparse(url).scheme in ("http", "https"):
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["/usr/bin/open", url])
            else:
                import webbrowser
                webbrowser.open(url)
        except Exception as e:
            logger.warning("Could not open the browser (%s); use the sign-in link in Connections.", type(e).__name__)


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
    fut.set_exception(RuntimeError("Sign-in was cancelled or refused. Start again from Connections."))
    return True
