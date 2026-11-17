"""Isolated connector/OAuth contracts; no accounts, network, or native apps."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
HOME = tempfile.TemporaryDirectory(prefix="xod-connectors-unit-")
os.environ.update(HOME=HOME.name, XOD_DATA_DIR=HOME.name,
                  XDG_CONFIG_HOME=str(Path(HOME.name) / "config"),
                  XDG_DATA_HOME=str(Path(HOME.name) / "share"),
                  XOD_SPEECH="off", XOD_NO_UPDATE_CHECK="1",
                  PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring")
from app.services import connectors as C, mcp_client as M, mcp_oauth as O, keychain

REDIRECT = "http://127.0.0.1:8765/oauth/callback"
HTTP_TARGET = M.ServerConn._http_target
ACCOUNT_CHECK = M.ServerConn._verify_account_access


class ConnectorLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HOME.name)
        self.secrets = {}
        self.gate, self.entered = asyncio.Event(), asyncio.Event()
        self.gate.set()
        self.active = 0
        self.peak = 0
        self.started = 0
        self.tools = [NS(name="search_threads", description="Search Gmail threads",
                         inputSchema={"type": "object"}, annotations=NS(readOnlyHint=True))]
        test = self

        class Client:
            def __init__(self, *args, **kwargs):
                pass
            async def __aenter__(self):
                test.started += 1
                test.entered.set()
                await test.gate.wait()
                test.active += 1
                test.peak = max(test.peak, test.active)
                return self
            async def __aexit__(self, *args):
                test.active -= 1
            async def list_tools(self):
                return NS(tools=test.tools)
            async def call_tool(self, name, args):
                return NS(content=[NS(text="synthetic mailbox read")], isError=False)

        def delete(service, account):
            self.secrets.pop((service, account), None)

        self.patches = [
            patch.object(M, "CONFIG", Path(self.tmp.name) / "servers.json"),
            patch.object(M, "mcp_hub", M.MCPHub()),
            patch.object(C, "_tasks", {}),
            patch.object(C, "_locks", {}), patch.object(C, "_errors", {}),
            patch.object(C, "gh_account", return_value=None),
            patch.object(C, "share_with_agents", return_value=False),
            patch.object(O, "_waiting", {}), patch.object(O, "sign_in_pages", {}),
            patch.object(keychain, "get_password", side_effect=lambda s, a: self.secrets.get((s, a))),
            patch.object(keychain, "set_password", side_effect=lambda s, a, v: self.secrets.__setitem__((s, a), v)),
            patch.object(keychain, "delete_password", side_effect=delete),
            patch.object(keychain, "problem", return_value=None),
            patch.object(M.ServerConn, "_http_target", return_value=object()),
            patch.object(M.ServerConn, "_verify_account_access", new=AsyncMock()),
            patch("mcp.Client", Client),
        ]
        for p in self.patches:
            p.start()
        self.hub = M.mcp_hub

    async def asyncTearDown(self):
        self.gate.set()
        for task in C._tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*C._tasks.values(), return_exceptions=True)
        for conn in list(self.hub.conns.values()):
            await conn.stop()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    async def settle(self):
        await asyncio.gather(*list(C._tasks.values()), return_exceptions=True)

    async def disconnect(self, cid):
        result = C.disconnect(cid)
        return await result if hasattr(result, "__await__") else result

    async def test_all_catalog_kinds_save_only_nonsecret_configuration(self):
        self.assertEqual({c["kind"] for c in C.CATALOG}, {"oauth", "own", "token", "open", "local"})
        for entry in C.CATALOG:
            cid = entry["id"]
            with self.subTest(connector=cid):
                if entry["kind"] == "local":
                    with patch.object(C.sys, "platform", "linux"):
                        result = await C.connect(cid, REDIRECT)
                    self.assertFalse(result["success"])
                    continue
                result = await C.connect(cid, REDIRECT, token="fixture-bearer-value",
                                         client_id="fixture-client", client_secret="fixture-client-secret")
                self.assertTrue(result["success"], result)
                await self.settle()
                spec = M.load_config()[cid]
                self.assertEqual(spec["url"], entry["url"])
                self.assertEqual(spec["trust"], entry["trust"])
                raw = M.CONFIG.read_text()
                self.assertNotIn("fixture-bearer-value", raw)
                self.assertNotIn("fixture-client-secret", raw)
                self.assertEqual(next(c for c in C.catalog() if c["id"] == cid)["state"], "connected")

    async def test_repeated_connect_is_single_flight(self):
        self.gate.clear()
        await C.connect("gmail", REDIRECT, client_id="fixture-client", client_secret="fixture-secret")
        await self.entered.wait()
        await C.connect("gmail", REDIRECT)
        await asyncio.sleep(.02)
        self.gate.set()
        await self.settle()
        self.assertEqual(self.started, 1)
        self.assertEqual(self.peak, 1)

    async def test_disconnect_cancels_connect_and_removes_live_session(self):
        self.gate.clear()
        await C.connect("notion", REDIRECT)
        await self.entered.wait()
        await self.disconnect("notion")
        self.gate.set()
        await self.settle()
        await asyncio.sleep(.02)
        self.assertNotIn("notion", self.hub.conns)
        self.assertNotIn("notion", M.load_config())
        self.assertEqual(self.active, 0)

    async def test_zero_tools_is_not_connected(self):
        self.tools = []
        await C.connect("mslearn", REDIRECT)
        await self.settle()
        state = next(c for c in C.catalog() if c["id"] == "mslearn")
        self.assertEqual(state["state"], "error")
        self.assertIn("tools", state["error"].lower())

    async def test_disabled_connector_is_off_and_cannot_dispatch(self):
        await C.connect("mslearn", REDIRECT)
        await self.settle()
        M.update_server("mslearn", enabled=False)
        self.assertEqual(next(c for c in C.catalog() if c["id"] == "mslearn")["state"], "off")
        result = await self.hub.call("mcp__mslearn__search_threads", {})
        self.assertFalse(result["success"])

    async def test_missing_google_client_explains_setup(self):
        result = await C.connect("gmail", REDIRECT)
        self.assertFalse(result["success"])
        self.assertTrue(result["needs_client"])
        self.assertIn("Google", result["error"])
        self.assertNotIn("gmail", M.load_config())

    async def test_secret_store_failure_does_not_save_connector(self):
        with patch.object(keychain, "set_password", side_effect=keychain.Unreadable("fixture locked store")):
            result = await C.connect("github", REDIRECT, token="fixture")
        self.assertFalse(result["success"])
        self.assertIn("key", result["error"].lower())
        self.assertNotIn("github", M.load_config())

    async def test_new_google_client_invalidates_old_tokens(self):
        O.set_own_client("gmail", "old-client", "old-secret", REDIRECT)
        self.secrets[(O.KEYCHAIN, "gmail:oauth_tokens")] = '{"access_token":"old","token_type":"Bearer"}'
        O.set_own_client("gmail", "new-client", "new-secret", REDIRECT)
        self.assertFalse(O.has_tokens("gmail"))

    async def test_corrupt_tokens_require_signin(self):
        self.secrets[(O.KEYCHAIN, "gmail:oauth_tokens")] = "corrupt"
        self.assertFalse(O.has_tokens("gmail"))

    async def test_google_authorization_requests_offline_readonly_access(self):
        p = O.provider("gmail", C._BY_ID["gmail"]["url"], REDIRECT, True, C._BY_ID["gmail"]["scope"])
        with patch.object(O, "open_in_browser"):
            await p.context.redirect_handler(
                "https://accounts.google.com/o/oauth2/v2/auth?state=fixture-state&code_challenge=fixture&scope=broad")
        query = parse_qs(urlparse(O.sign_in_pages["gmail"]["url"]).query)
        self.assertEqual(query.get("access_type"), ["offline"])
        self.assertEqual(query.get("prompt"), ["consent"])
        self.assertEqual(query.get("scope"), [C._BY_ID["gmail"]["scope"]])

    async def test_cancel_pending_signin_rejects_late_callback(self):
        p = O.provider("notion", C._BY_ID["notion"]["url"], REDIRECT, True)
        with patch.object(O, "open_in_browser"):
            await p.context.redirect_handler("https://provider.test/auth?state=fixture-state")
        await self.disconnect("notion")
        self.assertFalse(O.finish("late-code", "fixture-state"))
        self.assertNotIn("notion", O.sign_in_pages)

    async def test_empty_state_is_never_registered(self):
        p = O.provider("notion", C._BY_ID["notion"]["url"], REDIRECT, True)
        with patch.object(O, "open_in_browser"), self.assertRaises(ValueError):
            await p.context.redirect_handler("https://provider.test/auth")
        self.assertNotIn("", O._waiting)

    async def test_denied_and_replayed_callback(self):
        p = O.provider("notion", C._BY_ID["notion"]["url"], REDIRECT, True)
        with patch.object(O, "open_in_browser"):
            await p.context.redirect_handler("https://provider.test/auth?state=fixture-state")
        self.assertFalse(O.finish("code", "wrong-state"))
        self.assertTrue(O.fail("fixture-state", "access_denied"))
        with self.assertRaisesRegex(Exception, "refused|denied"):
            await p.context.callback_handler()
        self.assertFalse(O.finish("code", "fixture-state"))
        self.assertNotIn("notion", O.sign_in_pages)

    async def test_background_reconnect_never_opens_browser(self):
        p = O.provider("notion", C._BY_ID["notion"]["url"], REDIRECT, False)
        with patch.object(O, "open_in_browser") as browser, self.assertRaises(O.SignInNeeded):
            await p.context.redirect_handler("https://provider.test/auth?state=fixture")
        browser.assert_not_called()

    async def test_configuration_replace_failure_preserves_previous_file(self):
        M._save_config({"mslearn": {"url": "https://fixture.test/mcp"}})
        before = M.CONFIG.read_bytes()
        with patch.object(M.os, "replace", side_effect=OSError("fixture disk failure")):
            with self.assertRaises(OSError):
                M._save_config({"gmail": {"url": "https://fixture.test/other"}})
        self.assertEqual(M.CONFIG.read_bytes(), before)
        self.assertEqual(list(M.CONFIG.parent.iterdir()), [M.CONFIG])
        self.assertEqual(M.CONFIG.stat().st_mode & 0o777, 0o600)

    async def test_corrupt_configuration_is_never_overwritten(self):
        M.CONFIG.write_text("{broken")
        result = await C.connect("mslearn", REDIRECT)
        self.assertFalse(result["success"])
        self.assertIn("not overwritten", result["error"])
        self.assertEqual(M.CONFIG.read_text(), "{broken")

    async def test_google_changed_callback_requires_explicit_setup_update(self):
        O.set_own_client("gmail", "fixture-client", "fixture-secret", REDIRECT)
        changed = O.KeychainTokenStorage("gmail", "http://127.0.0.1:9876/oauth/callback")
        with self.assertRaisesRegex(ValueError, "callback changed"):
            await changed.get_client_info()

    async def test_oauth_timeout_and_browser_failure_clear_pending_state(self):
        p = O.provider("notion", C._BY_ID["notion"]["url"], REDIRECT, True)
        with patch.object(O, "open_in_browser"):
            await p.context.redirect_handler("https://provider.test/auth?state=fixture-state")
        with patch.object(O, "SIGN_IN_TIMEOUT", .01), self.assertRaises(TimeoutError):
            await p.context.callback_handler()
        self.assertEqual(O._waiting, {})
        self.assertEqual(O.sign_in_pages, {})
        with patch.object(O, "open_in_browser", side_effect=OSError("fixture")), self.assertRaises(OSError):
            await p.context.redirect_handler("https://provider.test/auth?state=another-state")
        self.assertEqual(O._waiting, {})
        self.assertEqual(O.sign_in_pages, {})

    async def test_callback_does_not_claim_connected_before_token_exchange(self):
        from fastapi.testclient import TestClient
        from app.main import app
        state = "fixture-callback"
        O._waiting[state] = asyncio.get_running_loop().create_future()
        # ASGI transport in a TestClient thread cannot resolve this loop's Future;
        # patch only the delivery boundary; the OAuth handler is tested above.
        with patch.object(O, "finish", return_value=True):
            client = TestClient(app, base_url="http://127.0.0.1:8765")
            result = client.get("/oauth/callback?code=fixture&state=" + state,
                                headers={"sec-fetch-site": "cross-site"})
            self.assertEqual(result.status_code, 200)
            self.assertIn("Finishing sign-in", result.text)
            self.assertNotIn("<h1>Connected", result.text)
            self.assertEqual(client.get("/api/connectors", headers={"sec-fetch-site": "cross-site"}).status_code, 403)
            self.assertEqual(client.get("/oauth/callback?code=fixture&state=x",
                                        headers={"origin": "https://evil.invalid"}).status_code, 403)
        O._waiting.pop(state).cancel()

    async def test_probe_rejects_jsonrpc_error_as_working_server(self):
        import runpy
        probe = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/check_connectors.py"))
        validate = probe["initialize_result"]
        self.assertFalse(validate('{"jsonrpc":"2.0","id":1,"error":{"code":-32601}}'))
        self.assertFalse(validate("<html>event: jsonrpc</html>"))
        valid = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "fixture"}}})
        self.assertTrue(validate(valid))
        self.assertTrue(validate("event: message\ndata: " + valid + "\n\n"))

    async def test_failed_secret_deletion_does_not_claim_success(self):
        await C.connect("github", REDIRECT, token="fixture")
        await self.settle()
        with patch.object(keychain, "delete_password", side_effect=keychain.Unreadable("locked")):
            result = await self.disconnect("github")
        self.assertFalse(result["success"])
        self.assertFalse(M.load_config()["github"]["enabled"])
        self.assertNotIn("github", self.hub.conns)

    async def test_public_disconnect_never_requires_a_key_store(self):
        await C.connect("mslearn", REDIRECT)
        await self.settle()
        with patch.object(keychain, "delete_password", side_effect=keychain.Unreadable("no backend")):
            result = await self.disconnect("mslearn")
        self.assertTrue(result["success"])
        self.assertNotIn("mslearn", M.load_config())

    async def test_account_change_stops_old_session_before_saving_credentials(self):
        await C.connect("github", REDIRECT, token="fixture-old")
        await self.settle()
        self.hub.pending["old-account"] = {"id": "old-account", "server": "github"}
        def save(service, account, value):
            self.assertNotIn("github", self.hub.conns)
            self.assertNotIn("old-account", self.hub.pending)
            self.secrets[(service, account)] = value
        with patch.object(keychain, "set_password", side_effect=save), \
             patch("app.services.agent_log.approval_decided"):
            result = await C.connect("github", REDIRECT, token="fixture-new")
        self.assertTrue(result["success"])
        await self.settle()

    async def test_unknown_and_unsupported_connectors_never_start(self):
        self.assertFalse((await C.connect("missing", REDIRECT))["success"])
        with patch.object(C.sys, "platform", "linux"):
            self.assertFalse((await C.connect("mac", REDIRECT))["success"])
        self.assertEqual(self.started, 0)

    async def test_stale_approvals_removed_on_reconnect_and_disconnect(self):
        await C.connect("notion", REDIRECT)
        await self.settle()
        self.hub.pending["old"] = {"id": "old", "server": "notion"}
        with patch("app.services.agent_log.approval_decided"):
            await self.hub.connect("notion")
        self.assertNotIn("old", self.hub.pending)
        self.hub.pending["other"] = {"id": "other", "server": "notion"}
        with patch("app.services.agent_log.approval_decided"):
            await self.disconnect("notion")
        self.assertNotIn("other", self.hub.pending)

    async def test_http_client_is_closed_after_transport_exit(self):
        import httpx2
        from mcp.shared import _httpx_utils
        from mcp.client import streamable_http
        client = httpx2.AsyncClient()
        @asynccontextmanager
        async def transport(*args, **kwargs):
            yield ("fixture-read", "fixture-write")
        conn = M.ServerConn("public", {"url": "https://fixture.test/mcp"})
        with patch.object(M.ServerConn, "_http_target", HTTP_TARGET), \
             patch.object(_httpx_utils, "create_mcp_http_client", return_value=client), \
             patch.object(streamable_http, "streamable_http_client", transport):
            async with conn._http_target():
                self.assertFalse(client.is_closed)
        self.assertTrue(client.is_closed)

    async def test_sse_transport_remains_supported(self):
        from mcp.client import sse
        closed = []
        @asynccontextmanager
        async def transport(*args, **kwargs):
            try:
                yield ("fixture-read", "fixture-write")
            finally:
                closed.append(True)
        conn = M.ServerConn("legacy", {"url": "https://fixture.test/sse", "type": "sse"})
        with patch.object(M.ServerConn, "_http_target", HTTP_TARGET), patch.object(sse, "sse_client", transport):
            async with conn._http_target() as streams:
                self.assertEqual(streams[0], "fixture-read")
        self.assertEqual(closed, [True])

    async def test_gmail_protocol_read_and_restart_refresh(self):
        await self.gmail_protocol()

    async def test_google_readiness_probes_are_minimal_readonly_and_discarded(self):
        for host, (name, args) in M.GOOGLE_ACCOUNT_PROBES.items():
            with self.subTest(host=host):
                conn = M.ServerConn("google", {"url": f"https://{host}/mcp/v1", "auth": "oauth"})
                conn._oauth_provider = NS(context=NS(current_tokens=NS(access_token="fixture")))
                tool = NS(name=name, annotations=NS(readOnlyHint=True))
                client = NS(call_tool=AsyncMock(return_value=NS(is_error=False)))
                self.assertIsNone(await ACCOUNT_CHECK(conn, client, [tool]))
                client.call_tool.assert_awaited_once_with(name, args)
                self.assertEqual(conn.tools, [])
                self.assertIsNone(conn.client)

    async def test_google_failed_account_read_is_not_accepted(self):
        conn = M.ServerConn("gmail", {"url": C._BY_ID["gmail"]["url"], "auth": "oauth"})
        conn._oauth_provider = NS(context=NS(current_tokens=NS(access_token="fixture")))
        client = NS(call_tool=AsyncMock(return_value=NS(is_error=True)))
        tool = NS(name="list_labels", annotations=NS(readOnlyHint=True))
        with self.assertRaisesRegex(ValueError, "Google account check failed"):
            await ACCOUNT_CHECK(conn, client, [tool])
        self.assertIsNone(conn.client)

    async def test_google_probe_success_without_oauth_tokens_is_not_accepted(self):
        conn = M.ServerConn("gmail", {"url": C._BY_ID["gmail"]["url"], "auth": "oauth"})
        conn._oauth_provider = NS(context=NS(current_tokens=None))
        client = NS(call_tool=AsyncMock(return_value=NS(is_error=False)))
        tool = NS(name="list_labels", annotations=NS(readOnlyHint=True))
        with self.assertRaises(O.SignInNeeded):
            await ACCOUNT_CHECK(conn, client, [tool])

    async def test_google_imported_as_public_cannot_report_connected(self):
        M._save_config({"gmail": {"url": C._BY_ID["gmail"]["url"], "enabled": True}})
        with patch.object(M.ServerConn, "_verify_account_access", ACCOUNT_CHECK):
            result = await self.hub.connect("gmail")
        self.assertFalse(result["success"])
        self.assertIn("requires OAuth", result["error"])
        self.assertFalse(self.hub.status()[0]["connected"])
        self.assertEqual(self.hub.status()[0]["tools"], [])

    async def test_automatic_oauth_registration_uses_sdk_pkce_and_state(self):
        import httpx2
        url, issuer = "https://provider.test/mcp", "https://auth.provider.test"
        registered = []
        p = O.provider("notion", url, REDIRECT, True)
        def handler(request):
            address = str(request.url)
            if address == url:
                if request.headers.get("authorization") == "Bearer fixture-auto-token":
                    return httpx2.Response(200, json={"fixture": "protected read succeeded"})
                return httpx2.Response(401, headers={"www-authenticate":
                    'Bearer resource_metadata="https://provider.test/.well-known/oauth-protected-resource/mcp"'})
            if address == "https://provider.test/.well-known/oauth-protected-resource/mcp":
                return httpx2.Response(200, json={"resource": url, "authorization_servers": [issuer],
                                                  "scopes_supported": ["fixture.read"]})
            if address == issuer + "/.well-known/oauth-authorization-server":
                return httpx2.Response(200, json={
                    "issuer": issuer, "authorization_endpoint": issuer + "/authorize",
                    "token_endpoint": issuer + "/token", "registration_endpoint": issuer + "/register",
                    "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"]})
            if address == issuer + "/register":
                metadata = json.loads(request.content)
                self.assertEqual(metadata["redirect_uris"], [REDIRECT])
                registered.append(True)
                return httpx2.Response(201, json={
                    "client_id": "fixture-dynamic-client", "redirect_uris": [REDIRECT],
                    "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"]})
            if address == issuer + "/token":
                form = parse_qs(request.content.decode())
                self.assertEqual(form["code"], ["fixture-auto-code"])
                self.assertGreaterEqual(len(form["code_verifier"][0]), 43)
                return httpx2.Response(200, json={"access_token": "fixture-auto-token", "token_type": "Bearer",
                                                  "refresh_token": "fixture-auto-refresh", "expires_in": 3600})
            raise AssertionError(address)
        def browser(address):
            params = parse_qs(urlparse(address).query)
            self.assertEqual(params["client_id"], ["fixture-dynamic-client"])
            self.assertEqual(params["code_challenge_method"], ["S256"])
            self.assertGreaterEqual(len(params["code_challenge"][0]), 43)
            self.assertFalse(O.finish("wrong-code", "wrong-state"))
            self.assertTrue(O.finish("fixture-auto-code", params["state"][0], issuer))
            self.assertFalse(O.finish("replayed-code", params["state"][0], issuer))
        with patch.object(O, "open_in_browser", side_effect=browser):
            async with httpx2.AsyncClient(auth=p, transport=httpx2.MockTransport(handler)) as http:
                response = await http.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(registered, [True])
        self.assertTrue(O.has_tokens("notion"))
        client = await O.KeychainTokenStorage("notion", REDIRECT).get_client_info()
        self.assertEqual(client.client_id, "fixture-dynamic-client")
        self.assertEqual(client.issuer, issuer)
        self.assertNotIn("notion", O.sign_in_pages)

    async def test_gmail_revoked_refresh_requires_signin_without_browser(self):
        await self.gmail_protocol(revoke=True)

    async def gmail_protocol(self, revoke=False):
        """Actual MCP Client + actual OAuth provider over an in-process HTTP transport."""
        import httpx2
        from mcp import Client
        from mcp.shared import _httpx_utils
        # The module attribute is patched by setUp; import the real implementation directly.
        from mcp.client.client import Client as RealClient
        requests, http_clients, refreshes, account_checks, methods = [], [], [], [], []
        issuer = "https://accounts.google.com"
        token_url = "https://oauth2.googleapis.com/token"
        url = C._BY_ID["gmail"]["url"]
        scope = C._BY_ID["gmail"]["scope"]
        refuse_refresh = False

        def handler(request):
            requests.append((request.method, str(request.url)))
            address = str(request.url)
            if address == token_url:
                form = parse_qs(request.content.decode())
                self.assertEqual(form["client_secret"], ["fixture-secret"])
                if form["grant_type"] == ["refresh_token"]:
                    refreshes.append(True)
                    if refuse_refresh:
                        return httpx2.Response(400, json={"error": "invalid_grant"})
                    return httpx2.Response(200, json={"access_token": "fixture-refreshed",
                                                      "token_type": "Bearer", "expires_in": 3600})
                self.assertEqual(form["code"], ["fixture-code"])
                self.assertTrue(form["code_verifier"][0])
                return httpx2.Response(200, json={"access_token": "fixture-access", "token_type": "Bearer",
                                                  "refresh_token": "fixture-refresh", "expires_in": 3600,
                                                  "scope": scope})
            if ".well-known/oauth-protected-resource" in address:
                return httpx2.Response(200, json={"resource": url, "authorization_servers": [issuer],
                                                  "scopes_supported": [scope]})
            if address.startswith(issuer) and ".well-known/" in address:
                return httpx2.Response(200, json={
                    "issuer": issuer, "authorization_endpoint": issuer + "/o/oauth2/v2/auth",
                    "token_endpoint": token_url, "response_types_supported": ["code"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["client_secret_post"],
                    "scopes_supported": [scope]})
            if address == url:
                if request.method == "GET":
                    return httpx2.Response(405)
                if request.method == "DELETE":
                    return httpx2.Response(200)
                message = json.loads(request.content)
                method = message["method"]
                methods.append(method)
                # Match Google: discovery works anonymously, account reads don't.
                if (method == "tools/call" and
                        request.headers.get("authorization") not in ("Bearer fixture-access", "Bearer fixture-refreshed")):
                    return httpx2.Response(401, headers={"www-authenticate":
                        'Bearer resource_metadata="https://gmailmcp.googleapis.com/.well-known/oauth-protected-resource/mcp/v1"'})
                if "id" not in message:
                    return httpx2.Response(202)
                if method == "server/discover":
                    # SDK 2.x probes stateless discovery before legacy initialize.
                    return httpx2.Response(200, json={"jsonrpc": "2.0", "id": message["id"],
                                                      "error": {"code": -32601, "message": "Method not found"}})
                elif method == "initialize":
                    result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "synthetic-gmail", "version": "1"}}
                elif method == "tools/list":
                    result = {"tools": [
                        {"name": "search_threads", "description": "Search Gmail threads",
                         "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
                        {"name": "list_labels", "description": "List Gmail labels",
                         "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
                        {"name": "create_draft", "description": "Create a draft",
                         "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": False}}]}
                elif method == "tools/call":
                    if message["params"]["name"] == "list_labels":
                        account_checks.append(True)
                        result = {"content": [{"type": "text", "text": "Synthetic labels only"}]}
                    else:
                        self.assertEqual(message["params"]["name"], "search_threads")
                        result = {"content": [{"type": "text", "text": "Synthetic thread only"}]}
                else:
                    raise AssertionError(method)
                return httpx2.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})
            raise AssertionError(address)

        def factory(**kwargs):
            client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler), **kwargs)
            http_clients.append(client)
            return client

        def browser(auth_url):
            query = parse_qs(urlparse(auth_url).query)
            self.assertEqual(query["access_type"], ["offline"])
            self.assertEqual(query["scope"], [scope])
            self.assertIn("tools/list", methods)  # anonymous discovery preceded consent
            self.assertEqual(methods[-1], "tools/call")
            self.assertIsNone(self.hub.conns["gmail"].client)
            self.assertEqual(self.hub.conns["gmail"].tools, [])
            self.assertFalse(next(s for s in self.hub.status() if s["name"] == "gmail")["connected"])
            self.assertTrue(O.finish("fixture-code", query["state"][0], issuer))

        with patch("mcp.Client", RealClient), patch.object(M.ServerConn, "_http_target", HTTP_TARGET), \
             patch.object(M.ServerConn, "_verify_account_access", ACCOUNT_CHECK), \
             patch.object(_httpx_utils, "create_mcp_http_client", side_effect=factory), \
             patch.object(O, "open_in_browser", side_effect=browser) as opened:
            result = await C.connect("gmail", REDIRECT, client_id="fixture-client", client_secret="fixture-secret")
            self.assertTrue(result["success"])
            await self.settle()
            self.assertIsNotNone(self.hub.conns["gmail"].client,
                                 f"{self.hub.conns['gmail'].error}; requests={requests}")
            self.assertEqual(account_checks, [True])
            self.assertEqual(opened.call_count, 1)
            read = await self.hub.call("mcp__gmail__search_threads", {"query": "synthetic"})
            self.assertTrue(read["success"], read)
            self.assertIn("Synthetic thread", read["content"])
            write = await self.hub.call("mcp__gmail__create_draft", {})
            self.assertTrue(write["needs_approval"])
            self.assertFalse(any("fixture-secret" in u for _, u in requests))
            await self.hub.disconnect("gmail")  # process/session restart, not credential removal
            self.assertTrue(all(client.is_closed for client in http_clients))
            saved = json.loads(self.secrets[(O.KEYCHAIN, "gmail:oauth_tokens")])
            saved["_expires_at"] = 1
            self.secrets[(O.KEYCHAIN, "gmail:oauth_tokens")] = json.dumps(saved)
            refuse_refresh = revoke
            opened.reset_mock()
            with patch("app.services.agent_log.approval_decided"):
                again = await self.hub.connect("gmail", interactive=False, redirect_uri=REDIRECT)
            self.assertEqual(len(refreshes), 1)
            opened.assert_not_called()
            if revoke:
                self.assertFalse(again["success"])
                self.assertTrue(again["needs_sign_in"], again)
            else:
                self.assertTrue(again["success"], again)
                stored = json.loads(self.secrets[(O.KEYCHAIN, "gmail:oauth_tokens")])
                self.assertEqual(stored["access_token"], "fixture-refreshed")
                self.assertEqual(stored["refresh_token"], "fixture-refresh")
            await self.hub.disconnect("gmail")
            self.assertTrue(all(client.is_closed for client in http_clients))


if __name__ == "__main__":
    unittest.main()
