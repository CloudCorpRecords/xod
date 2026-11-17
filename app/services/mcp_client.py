"""
Outside tools over MCP (Model Context Protocol): Gmail, Notion, GitHub, databases… any MCP server.

Servers are listed in <data>/mcp_servers.json (the same shape as Claude Desktop's "mcpServers",
so a block can be pasted in from there). Secret values in "env" (API keys, tokens) are moved
into the macOS Keychain ("Xod MCP" service) and the file keeps only the names.

Each enabled server runs as a long-lived connection (a stdio subprocess or a Streamable HTTP
URL). Its tools are offered to the model as mcp__<server>__<tool>, only when they're relevant to
the request, so a small local model isn't flooded.

Safety: a tool runs straight away only if the server says it's read-only (readOnlyHint) and
the server is set to "read_only" or "always", or the server is set to "always". Everything else
is held as a pending approval: the chat shows exactly what it wants to do, and it runs only
when you press Approve.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import re
import shutil
import sys
import time
import threading
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.services import keychain

from app.paths import DATA_DIR
from app.services import mcp_oauth

logger = logging.getLogger("xod.mcp")

CONFIG = DATA_DIR / "mcp_servers.json"
KEYCHAIN = "Xod MCP"
TRUST_LEVELS = ("ask", "read_only", "always")
SECRETISH = re.compile(r"(key|token|secret|password|passwd|auth|credential|cookie)", re.I)
_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]")
_CONFIG_LOCK = threading.RLock()
GOOGLE_ACCOUNT_PROBES = {
    "gmailmcp.googleapis.com": ("list_labels", {}),
    "calendarmcp.googleapis.com": ("list_calendars", {"pageSize": 1}),
    "drivemcp.googleapis.com": ("list_recent_files", {"pageSize": 1, "excludeContentSnippets": True}),
}


def _attr(obj: Any, snake: str, camel: str, default: Any = None) -> Any:
    """MCP SDK 2.x uses snake_case fields (read_only_hint), 1.x camelCase (readOnlyHint)."""
    if obj is None:
        return default
    v = getattr(obj, snake, None)
    return v if v is not None else getattr(obj, camel, default)


# Picking a connected app's tools for a request: words that say nothing, and everyday words for what tools call things.
_STOP = {"the", "and", "for", "you", "your", "can", "please", "with", "that", "this", "what", "whats", "from", "are", "have",
         "has", "was", "will", "about", "into", "just", "now", "get", "let", "know", "tell", "want", "need", "how", "who", "any"}
_SAME = {"text": {"message", "messages"}, "texts": {"message", "messages"}, "imessage": {"messages"}, "sms": {"messages"},
         "lights": {"shortcut", "home"}, "light": {"shortcut", "home"}, "lamp": {"shortcut", "home"}, "scene": {"shortcut", "home"},
         "thermostat": {"shortcut", "home"}, "lock": {"shortcut", "home"}, "unlock": {"security", "shortcut"},
         "garage": {"security", "shortcut"}, "near": {"places", "nearby"}, "nearby": {"places"}, "closest": {"places", "nearby"},
         "remind": {"reminders"}, "reminder": {"reminders"}, "schedule": {"calendar", "events"}, "meeting": {"calendar", "event"},
         "appointment": {"calendar", "event"}, "number": {"contacts", "phone"}, "phone": {"contacts"}, "contact": {"contacts"},
         "email": {"mail"}, "inbox": {"mail"}, "where": {"location"}}


def _read_only(tool: Any) -> bool:
    return bool(_attr(getattr(tool, "annotations", None), "read_only_hint", "readOnlyHint", False))


def _destructive(tool: Any) -> bool:
    """Deletes and the like. These always wait for the user's OK, whatever the server's trust setting.
    (MCP's default for a tool that isn't read-only is destructive, so unknown tools count too.)"""
    ann = getattr(tool, "annotations", None)
    if _read_only(tool):
        return False
    hint = _attr(ann, "destructive_hint", "destructiveHint", None)
    if hint is not None:
        return bool(hint)
    return bool(re.search(r"(^|_)(delete|remove|destroy|drop|erase|purge|trash|archive|cancel|close)(_|$)", getattr(tool, "name", "") or "", re.I))


def _find_exc(e: BaseException, kind: type) -> Optional[BaseException]:
    if isinstance(e, kind):
        return e
    for sub in getattr(e, "exceptions", None) or []:
        hit = _find_exc(sub, kind)
        if hit:
            return hit
    return _find_exc(e.__cause__, kind) if e.__cause__ else None


def _first_leaf(e: BaseException) -> BaseException:
    subs = getattr(e, "exceptions", None)
    return _first_leaf(subs[0]) if subs else e


def _clean(name: str) -> str:
    return _SAFE_NAME.sub("_", name)[:40] or "server"


# ---------------------------------------------------------------- config

def load_config() -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"mcp_servers.json unreadable: {e}")
        return {}
    return data.get("servers") or data.get("mcpServers") or {}


def _save_config(servers: Dict[str, Dict[str, Any]]) -> None:
    if CONFIG.exists():
        try:
            existing = json.loads(CONFIG.read_text())
            saved = existing.get("servers", existing.get("mcpServers", {}))
            if not isinstance(saved, dict):
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError("Connections configuration is unreadable. Restore or repair the file before saving; "
                             "the original file was not overwritten.") from None
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CONFIG.parent, delete=False) as f:
            pending = Path(f.name)
            os.chmod(pending, 0o600)
            f.write(json.dumps({"servers": servers}, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(pending, CONFIG)
    finally:
        if pending and pending.exists():
            pending.unlink()


def _env_for(name: str, spec: Dict[str, Any]) -> Dict[str, str]:
    env = {k: str(v) for k, v in (spec.get("env") or {}).items()}
    for key in spec.get("secret_env") or []:
        val = keychain.get_password(KEYCHAIN, f"{name}:{key}")
        if val:
            env[key] = val
    return env


def stdio_env(name: str, spec: Dict[str, Any]):
    """Builds the (env, command, cwd) for a stdio MCP server.

    A child running Xod's own python must never write bytecode: inside the
    packaged app the bundle is sealed, and a rewritten .pyc invalidates the
    seal, which makes the running app a different identity to TCC than the
    one the user granted (AGE-78). The backend carries
    PYTHONDONTWRITEBYTECODE from electron serverEnv; every python child
    spawned from here needs the same protection. Foreign servers keep their
    own environment, except inside a packaged run where the flag is set for
    every stdio server (a server may itself shell out to the bundled
    python)."""
    from mcp.client.stdio import get_default_environment
    from app.paths import APP_ROOT
    env = {**get_default_environment(),
           "PATH": os.environ.get("PATH", "")}
    for k in ("LANG", "TMPDIR", "SHELL", "TMP"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env.update(_env_for(name, spec))
    command = str(spec["command"])
    cwd = spec.get("cwd")
    own_python = command == "{xod_python}" or command == sys.executable
    if command == "{xod_python}":
        command = sys.executable
        env = {**{k: v for k, v in os.environ.items() if k.startswith("XOD_")}, **env,
               # The host may inject dependencies outside sys.prefix (Replit,
               # for example). Own-Python children need the same import paths,
               # not just the repository; foreign MCP servers stay isolated.
               "PYTHONPATH": os.pathsep.join(dict.fromkeys(
                   [str(APP_ROOT), *(p for p in sys.path if p)]))}
        cwd = cwd or str(APP_ROOT)
    elif shutil.which(command):
        command = shutil.which(command)
    try:
        if Path(command).resolve() == Path(sys.executable).resolve() or Path(sys.prefix).resolve() in Path(command).resolve().parents:
            own_python = True
    except Exception:
        pass
    if own_python or os.environ.get("XOD_PACKAGED") == "1":
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env, command, cwd


def add_servers(block: Dict[str, Any], overwrite: bool = True) -> Dict[str, Any]:
    """Explicit user imports may replace; automatic bridges must opt out."""
    with _CONFIG_LOCK:
        return _add_servers(block, overwrite)


def _add_servers(block: Dict[str, Any], overwrite: bool) -> Dict[str, Any]:
    """Adds servers from a Claude-Desktop-style block ({"mcpServers": {...}} or {name: spec}).
    Secret-looking env values go to the Keychain."""
    servers = block.get("mcpServers") or block.get("servers") or block
    if not isinstance(servers, dict) or not servers:
        return {"success": False, "error": "Paste a JSON block like {\"mcpServers\": {\"name\": {\"command\": …}}}."}
    current = load_config()
    added, preserved = [], []
    for raw_name, spec in servers.items():
        if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
            continue
        name = _clean(raw_name)
        if not overwrite and name in current:
            preserved.append(name)
            continue
        env, secret_env = {}, []
        for k, v in (spec.get("env") or {}).items():
            if SECRETISH.search(k) and v:
                keychain.set_password(KEYCHAIN, f"{name}:{k}", str(v))
                secret_env.append(k)
            else:
                env[k] = str(v)
        current[name] = {k: v for k, v in {
            "command": spec.get("command"), "args": [str(a) for a in spec.get("args") or []],
            "url": spec.get("url"), "cwd": spec.get("cwd"), "env": env, "secret_env": secret_env,
            "enabled": True, "trust": spec.get("trust") if spec.get("trust") in TRUST_LEVELS else "ask",
        }.items() if v is not None}
        added.append(name)
    if added:
        _save_config(current)
    return {"success": bool(added or preserved), "added": added, "preserved": preserved,
            "error": None if added or preserved else "No servers with a command or url found."}


def update_server(name: str, enabled: Optional[bool] = None, trust: Optional[str] = None) -> bool:
    cfg = load_config()
    if name not in cfg:
        return False
    if enabled is not None:
        cfg[name]["enabled"] = bool(enabled)
    if trust in TRUST_LEVELS:
        cfg[name]["trust"] = trust
    _save_config(cfg)
    return True


def remove_server(name: str) -> bool:
    cfg = load_config()
    spec = cfg.pop(name, None)
    if spec is None:
        return False
    if spec.get("auth") == "oauth":
        mcp_oauth.forget(name)
    else:
        mcp_oauth.cancel(name)
    if spec.get("auth") == "bearer":
        mcp_oauth._delete(name, "bearer_token")
    for key in [*(spec.get("secret_env") or []), *(f"header:{h}" for h in spec.get("secret_headers") or [])]:
        mcp_oauth._delete(name, key)
    _save_config(cfg)
    return True


# ---------------------------------------------------------------- connections


_VERBS = {"send", "add", "create", "run", "delete", "remove", "update", "edit", "move", "set", "start", "stop", "post", "reply",
          "book", "buy", "pay", "write", "rename", "archive", "close", "merge", "open", "complete", "unlock", "lock"}


def _action(tool: str) -> str:
    """A tool's name as what it does: messages_send → "send messages", create_note → "create note"."""
    words = tool.replace("-", "_").split("_")
    if len(words) > 1 and words[-1].lower() in _VERBS and words[0].lower() not in _VERBS:
        words = [words[-1]] + words[:-1]
    return " ".join(words)


def _app_name(server: str) -> str:
    """The connection's name as Xod shows it ("This Mac" for mac), or the server's own name."""
    try:
        from app.services.connectors import CATALOG
        return next((x["name"] for x in CATALOG if x.get("id") == server), server)
    except Exception:
        return server

class ServerConn:
    def __init__(self, name: str, spec: Dict[str, Any], interactive: bool = False, redirect_uri: Optional[str] = None):
        self.name, self.spec = name, spec
        self.interactive, self.redirect_uri = interactive, redirect_uri
        self.client = None
        self.tools: List[Any] = []
        self.error: Optional[str] = None
        self.needs_sign_in = False
        self.ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._oauth_provider = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    @asynccontextmanager
    async def _http_target(self) -> Any:
        """A Streamable HTTP connection (or SSE for older servers), signed in with OAuth, a bearer token or the
        server's own headers (an API key), from the Keychain."""
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
        url, auth_kind = self.spec["url"], self.spec.get("auth")
        headers, auth = dict(self.spec.get("headers") or {}), None
        for k in self.spec.get("secret_headers") or []:
            v = keychain.get_password(KEYCHAIN, f"{self.name}:header:{k}")
            if v:
                headers[k] = v
        if auth_kind == "bearer":
            token = keychain.get_password(KEYCHAIN, f"{self.name}:bearer_token")
            if not token:
                raise mcp_oauth.SignInNeeded(self.name)
            headers["Authorization"] = f"Bearer {token}"
        elif auth_kind == "oauth":
            if not self.interactive and not mcp_oauth.has_tokens(self.name):
                raise mcp_oauth.SignInNeeded(self.name)
            auth = mcp_oauth.provider(self.name, url, self.redirect_uri, self.interactive, self.spec.get("scope"))
            self._oauth_provider = auth
        if self.spec.get("type") == "sse":
            from mcp.client.sse import sse_client
            async with sse_client(url, headers=headers or None, timeout=15, auth=auth) as streams:
                yield streams
        else:
            # A caller-provided HTTP client is NOT closed by the MCP transport.
            async with create_mcp_http_client(headers=headers or None, auth=auth) as http:
                async with streamable_http_client(url, http_client=http) as streams:
                    yield streams

    async def _verify_account_access(self, client: Any, tools: List[Any]) -> None:
        """Google exposes anonymous discovery; verify a minimal authenticated read.

        Connect explicitly requests this check. Its contents are discarded, not
        persisted, logged, or returned to the agent. Never probe a write tool.
        """
        probe = GOOGLE_ACCOUNT_PROBES.get(urlparse(self.spec.get("url") or "").hostname)
        if not probe:
            return
        if self.spec.get("auth") != "oauth" or not self._oauth_provider:
            raise ValueError("Google account check requires OAuth. Use the official Google connector in Connections.")
        name, args = probe
        tool = next((t for t in tools if t.name == name), None)
        if not tool or not _read_only(tool) or _destructive(tool):
            raise ValueError("Google account check could not find its expected read-only tool. Check the provider setup.")
        # This request triggers the SDK's existing OAuth/PKCE flow if discovery
        # was anonymous. Keep interactive sign-in enabled until it completes.
        result = await client.call_tool(name, dict(args))
        if _attr(result, "is_error", "isError", False):
            raise ValueError("Google account check failed. Check consent, enabled APIs and OAuth client configuration.")
        tokens = self._oauth_provider.context.current_tokens
        if not tokens or not tokens.access_token:
            raise mcp_oauth.SignInNeeded(self.name)

    async def _run(self) -> None:
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters
        try:
            if self.spec.get("url"):
                target: Any = self._http_target()
            else:
                # What a program needs to start (on Windows: APPDATA, SYSTEMROOT, PATHEXT… or npx can't run), PATH as
                # it is now (Node installed a minute ago counts), then the server's own settings. stdio_env also
                # guarantees PYTHONDONTWRITEBYTECODE for Xod's own python children (AGE-78).
                from app.agent.worker import _fresh_path
                env, command, cwd = stdio_env(self.name, self.spec)
                env["PATH"] = _fresh_path()
                if command not in (sys.executable,) and shutil.which(command, path=env["PATH"]):
                    command = shutil.which(command, path=env["PATH"])      # npx → …\nodejs\npx.cmd
                try:
                    if Path(command).resolve() == Path(sys.executable).resolve() or Path(sys.prefix).resolve() in Path(command).resolve().parents:
                        env["PYTHONDONTWRITEBYTECODE"] = "1"
                except Exception:
                    pass
                target = StdioServerParameters(command=command, args=self.spec.get("args") or [], env=env,
                                                cwd=cwd or None)
            async with Client(target, read_timeout_seconds=120) as client:
                tools = (await client.list_tools()).tools
                if not tools:
                    raise ValueError("The server connected but returned no usable tools. Check account access and enabled APIs.")
                await self._verify_account_access(client, tools)
                if self._oauth_provider:
                    # The user's Connect click authorizes initial sign-in only,
                    # not surprise browser windows from later agent tool calls.
                    self._oauth_provider.allow_interactive = False
                self.client, self.tools = client, tools
                self.error, self.needs_sign_in = None, False
                self.ready.set()
                logger.info(f"MCP {self.name}: {len(self.tools)} tools")
                await self._stop.wait()
        except BaseException as e:   # the SDK wraps failures in exception groups
            if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            sign_in = _find_exc(e, mcp_oauth.SignInNeeded)
            if sign_in:
                self.needs_sign_in, self.error = True, "Sign in again to restore this connection."
            else:
                inner = _first_leaf(e)
                self.error = connection_error(inner)
                from app.services.fleet_policy import PACKAGES
                if self.name in PACKAGES:
                    self.error += (" Fleet startup never downloads packages. Check Node.js 20+ and "
                                   "run npm ci --ignore-scripts in the Xod checkout, or configure a local CLI.")
                logger.warning(f"MCP {self.name} failed: {self.error}")
        finally:
            self.client = None
            self.tools = []
            self.ready.set()

    async def stop(self) -> None:
        self._stop.set()
        mcp_oauth.cancel(self.name)
        if self._task and self._task is not asyncio.current_task():
            if not self._task.done():
                self._task.cancel()
            try:
                await asyncio.wait_for(self._task, 10)
            except asyncio.CancelledError:
                if not self._task.cancelled():
                    raise


def connection_error(exc: BaseException) -> str:
    """Actionable error classes without echoing token-bearing URLs or provider bodies."""
    from mcp.client.auth.exceptions import OAuthFlowError
    if isinstance(exc, keychain.Unreadable):
        return "The key store is locked or unavailable. Unlock it, choose Try again, then reconnect."
    if isinstance(exc, OAuthFlowError):
        return "OAuth sign-in failed. Check client details, the registered callback URI, consent and account access; then sign in again."
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if code in (401, 403):
        return f"Provider refused access (HTTP {code}). Sign in again or check account permissions and enabled services."
    if isinstance(exc, (ValueError, RuntimeError)) and str(exc).startswith((
            "The server connected but returned no", "OAuth callback changed.", "Sign-in was cancelled",
            "Connections configuration is unreadable.", "Google account check")):
        return str(exc)[:300]
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return "The server timed out. Check your connection and try again."
    return f"Connection failed ({type(exc).__name__}). Check the server address, account access and local dependencies."


class MCPHub:
    def __init__(self) -> None:
        self.conns: Dict[str, ServerConn] = {}
        self.pending: Dict[str, Dict[str, Any]] = {}
        self._connecting: Dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        """(Re)connects every enabled server in the config."""
        cfg = load_config()
        for name in set(self.conns) | set(self._connecting):
            await self.disconnect(name)
        await asyncio.gather(*(self.connect(name) for name, spec in cfg.items() if spec.get("enabled", True)),
                             return_exceptions=True)

    async def connect(self, name: str, interactive: bool = False, redirect_uri: Optional[str] = None) -> Dict[str, Any]:
        """(Re)connects one server. interactive=True may open a sign-in page in the browser."""
        existing = self._connecting.get(name)
        if existing and not existing.done():
            return await asyncio.shield(existing)
        task = asyncio.create_task(self._connect(name, interactive, redirect_uri))
        self._connecting[name] = task
        try:
            return await task
        finally:
            if self._connecting.get(name) is task:
                self._connecting.pop(name, None)

    async def _connect(self, name: str, interactive: bool, redirect_uri: Optional[str]) -> Dict[str, Any]:
        spec = load_config().get(name)
        if not spec or not spec.get("enabled", True):
            return {"success": False, "error": "No enabled server with that name."}
        if name in self.conns:
            await self.conns.pop(name).stop()
        for pid, request in list(self.pending.items()):
            if request.get("server") == name:
                self.decline(pid)
        conn = ServerConn(name, spec, interactive=interactive, redirect_uri=redirect_uri)
        self.conns[name] = conn
        conn.start()
        try:
            await asyncio.wait_for(conn.ready.wait(), mcp_oauth.SIGN_IN_TIMEOUT + 30 if interactive else 45)
        except asyncio.TimeoutError:
            await conn.stop()
            conn.error = "It didn't answer in time. Try connecting again."
            return {"success": False, "error": conn.error}
        except asyncio.CancelledError:
            await conn.stop()
            if self.conns.get(name) is conn:
                self.conns.pop(name, None)
            raise
        if conn.client:
            return {"success": True, "tools": len(conn.tools)}
        return {"success": False, "needs_sign_in": conn.needs_sign_in, "error": conn.error or "Not connected."}

    async def disconnect(self, name: str) -> None:
        task = self._connecting.pop(name, None)
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        conn = self.conns.pop(name, None)
        if conn:
            await conn.stop()
        mcp_oauth.cancel(name)
        for pid, request in list(self.pending.items()):
            if request.get("server") == name:
                self.decline(pid)

    def status(self) -> List[Dict[str, Any]]:
        out = []
        for name, spec in load_config().items():
            c = self.conns.get(name)
            out.append({"name": name, "enabled": spec.get("enabled", True), "trust": spec.get("trust", "ask"),
                        "command": " ".join([spec.get("command") or "", *spec.get("args", [])]).strip() or spec.get("url"),
                        "secret_env": spec.get("secret_env", []), "connector": spec.get("connector"),
                        "auth": spec.get("auth"), "needs_sign_in": bool(c and c.needs_sign_in),
                        "signing_in": name in mcp_oauth.sign_in_pages,
                         "connected": bool(spec.get("enabled", True) and c and c.client and c.tools and not c.needs_sign_in),
                         "error": c.error if c else None,
                        "tools": [{"name": t.name, "description": (t.description or "")[:160],
                                    "read_only": self.read_only(f"mcp__{name}__{t.name}")}
                                  for t in (c.tools if c else [])]})
        return out

    # ---------------------------------------------------------- tools for the model
    def tool_definitions(self) -> List[Dict[str, Any]]:
        defs = []
        cfg = load_config()
        for name, c in self.conns.items():
            spec = cfg.get(name)
            if not c.client or c.needs_sign_in or spec is None or not spec.get("enabled", True):
                continue
            for t in c.tools:
                schema = _attr(t, "input_schema", "inputSchema")
                if not isinstance(schema, dict):
                    schema = {"type": "object", "properties": {}}
                defs.append({"type": "function", "function": {
                    "name": f"mcp__{name}__{t.name}"[:64],
                    "description": f"[{name}] {(t.description or t.name)[:400]}",
                    "parameters": schema}})
        return defs

    def tools_for_query(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        defs = self.tool_definitions()
        if len(defs) <= limit:
            return defs
        words = {w for w in re.findall(r"[a-z0-9]{3,}", (query or "").lower()) if w not in _STOP}
        for w in list(words):
            words |= _SAME.get(w, set())

        def score(d):
            name, text = d["function"]["name"].lower(), d["function"]["description"].lower()
            return sum(2 if w in name else 1 for w in words if w in name or w in text)
        ranked = sorted(defs, key=score, reverse=True)
        return [d for d in ranked[:limit] if score(d) > 0]

    def _find(self, full_name: str):
        _, server, tool = full_name.split("__", 2)
        c = self.conns.get(server)
        t = next((x for x in (c.tools if c else []) if x.name == tool or f"mcp__{server}__{x.name}"[:64] == full_name), None)
        return c, t

    # ---------------------------------------------------------- calling
    def read_only(self, full_name: str) -> bool:
        """Effective policy shared by approval, sentinel and worker envelopes."""
        try:
            c, t = self._find(full_name)
        except ValueError:
            return False
        if not c or not t:
            return False
        from app.services.fleet_policy import PACKAGES, verified_read_only
        if c.name in PACKAGES:
            return verified_read_only(c.name, c.spec, t.name)
        from app.services.computer_bridge import SERVER_NAME, verified_look
        if c.name == SERVER_NAME:
            return verified_look(c.spec, t.name)
        return _read_only(t)

    async def call(self, full_name: str, args: Dict[str, Any], approved: bool = False) -> Dict[str, Any]:
        try:
            c, t = self._find(full_name)
        except ValueError:
            return {"success": False, "error": f"Bad MCP tool name {full_name}"}
        if not c or not c.client or not t:
            return {"success": False, "error": f"The {full_name.split('__')[1]} tool server isn't connected (Tools → Connections → MCP)."}
        current = load_config().get(c.name)
        if current is None or not current.get("enabled", True):
            return {"success": False, "error": "This connection is disabled or was removed. Reconnect it before using its tools."}
        if c.needs_sign_in:
            return {"success": False, "needs_sign_in": True, "error": "Sign in again from Connections before using this server."}
        trust = (load_config().get(c.name) or {}).get("trust", "ask")   # current setting, not the one at connect time
        read_only = self.read_only(full_name)
        from app.services.fleet_policy import PACKAGES
        # Even an inaccurate external readOnlyHint or "always" setting cannot
        # turn repro commands / unreviewed fleet operations into read-only work.
        fleet_hold = c.name in PACKAGES and not read_only
        from app.services.computer_bridge import SERVER_NAME
        computer_hold = c.name == SERVER_NAME and not read_only
        destructive = _destructive(t)
        if not approved and (fleet_hold or computer_hold or destructive or not (trust == "always" or (trust == "read_only" and read_only))):
            # A call that can't run as written goes back to the model to fix, not to you to approve first.
            schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {}
            missing = [k for k in schema.get("required", []) if k not in (args or {})]
            if missing:
                known = ", ".join(f"`{k}`" for k in (schema.get("properties") or {}))
                return {"success": False, "error": f"{t.name} needs {', '.join(f'`{k}`' for k in missing)} (its arguments: {known})"
                        + (f"; it was sent {', '.join(f'`{k}`' for k in args)}" if args else "") + ". Call it again with those names."}
            # The same call again (models repeat it, often with the approval_id they were given): the card that's already
            # waiting, not a second one.
            args = {k: v for k, v in (args or {}).items() if k != "approval_id"}
            same = next((p for p in self.pending.values() if p["tool"] == full_name and p["args"] == args), None)
            if same:
                return {"success": False, "needs_approval": True, "approval_id": same["id"], "tool": full_name,
                        "destructive": destructive, "args": args, "for_you": ("The person has to approve or decline this on the card in the chat. Tell them it's ready and waiting for "
                                    "their OK, in one short sentence. Don't call it again or pass the approval_id: only they can approve it."),
                        "message": f"Still waiting for your OK to {_action(t.name)} with {_app_name(c.name)}. It hasn't happened yet."}
            pid = uuid.uuid4().hex[:10]
            self.pending[pid] = {"id": pid, "tool": full_name, "server": c.name, "tool_name": t.name, "args": args,
                                 "description": (t.description or "")[:300], "created_at": time.time()}
            from app.services import agent_log
            agent_log.approval_opened(pid, "app", _app_name(c.name), _action(t.name).capitalize(),
                                      ", ".join(f"{k}: {str(v)[:60]}" for k, v in list((args or {}).items())[:4]))
            return {"success": False, "needs_approval": True, "approval_id": pid, "tool": full_name, "destructive": destructive,
                    "for_you": ("The person has to approve or decline this on the card in the chat. Tell them it's ready and waiting for "
                                 "their OK, in one short sentence. Don't call it again or pass the approval_id: only they can approve it."),
                    "message": (f"Waiting for your OK to {_action(t.name)} with {_app_name(c.name)}. Nothing has been deleted yet."
                                if destructive and re.search(r"(^|_)(delete|remove|destroy|drop|erase|purge|trash)(_|$)", t.name)
                                else f"Waiting for your OK to {_action(t.name)} with {_app_name(c.name)}. It hasn't happened yet."),
                    "args": args}
        try:
            res = await c.client.call_tool(t.name, args or {})
        except Exception as e:
            if _find_exc(e, mcp_oauth.SignInNeeded):
                c.needs_sign_in = True
                c.error = "The saved sign-in expired or was revoked. Sign in again from Connections."
                return {"success": False, "needs_sign_in": True, "error": c.error}
            return {"success": False, "error": connection_error(_first_leaf(e))}
        texts = [getattr(x, "text", None) or json.dumps(getattr(x, "model_dump", lambda: str(x))(), default=str)[:2000]
                 for x in (res.content or [])]
        is_error = bool(_attr(res, "is_error", "isError", False))
        out = {"success": not is_error, "server": c.name, "tool": t.name, "content": "\n".join(texts)[:20000]}
        structured = _attr(res, "structured_content", "structuredContent")
        if structured:
            out["data"] = structured
        if is_error:
            out["error"] = out["content"][:500] or "The tool reported an error."
            logged_args = ({k: ("[redacted]" if k == "value" else v) for k, v in args.items()}
                           if c.name == SERVER_NAME and t.name == "computer_type" else args)
            logger.warning(f"MCP {full_name} failed: {out['error'][:200]} (args: {json.dumps(logged_args, default=str)[:300]})")
        return out

    async def approve(self, pid: str) -> Dict[str, Any]:
        p = self.pending.pop(pid, None)
        if not p:
            return {"success": False, "error": "That request expired or was already handled."}
        from app.services import agent_log
        agent_log.approval_decided(pid, "allowed")
        if p.get("sentinel") and not p["tool"].startswith("mcp__"):
            # Held by the safety check (services/sentinel.py): the person said yes, so it runs as it was.
            from app.agent.tools import execute_tool
            return await execute_tool(p["tool"], p["args"], reviewed=True)
        from app.services import activity
        ctx = activity.before(p["tool"], p["args"])
        res = await self.call(p["tool"], p["args"], approved=True)
        activity.after(ctx, res)
        return res

    def decline(self, pid: str) -> bool:
        gone = self.pending.pop(pid, None) is not None
        if gone:
            from app.services import agent_log
            agent_log.approval_decided(pid, "declined")
        return gone


mcp_hub = MCPHub()
