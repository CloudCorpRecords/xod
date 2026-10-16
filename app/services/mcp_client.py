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
import json
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from app.services import keychain

from app.paths import DATA_DIR
from app.services import mcp_oauth

logger = logging.getLogger("xod.mcp")

CONFIG = DATA_DIR / "mcp_servers.json"
KEYCHAIN = "Xod MCP"
TRUST_LEVELS = ("ask", "read_only", "always")
SECRETISH = re.compile(r"(key|token|secret|password|passwd|auth|credential|cookie)", re.I)
_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]")


def _attr(obj: Any, snake: str, camel: str, default: Any = None) -> Any:
    """MCP SDK 2.x uses snake_case fields (read_only_hint), 1.x camelCase (readOnlyHint)."""
    if obj is None:
        return default
    v = getattr(obj, snake, None)
    return v if v is not None else getattr(obj, camel, default)


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
    CONFIG.write_text(json.dumps({"servers": servers}, indent=2))
    os.chmod(CONFIG, 0o600)


def _env_for(name: str, spec: Dict[str, Any]) -> Dict[str, str]:
    env = {k: str(v) for k, v in (spec.get("env") or {}).items()}
    for key in spec.get("secret_env") or []:
        val = keychain.get_password(KEYCHAIN, f"{name}:{key}")
        if val:
            env[key] = val
    return env


def add_servers(block: Dict[str, Any]) -> Dict[str, Any]:
    """Adds servers from a Claude-Desktop-style block ({"mcpServers": {...}} or {name: spec}).
    Secret-looking env values go to the Keychain."""
    servers = block.get("mcpServers") or block.get("servers") or block
    if not isinstance(servers, dict) or not servers:
        return {"success": False, "error": "Paste a JSON block like {\"mcpServers\": {\"name\": {\"command\": …}}}."}
    current = load_config()
    added = []
    for raw_name, spec in servers.items():
        if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
            continue
        name = _clean(raw_name)
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
    _save_config(current)
    return {"success": bool(added), "added": added, "error": None if added else "No servers with a command or url found."}


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
    mcp_oauth.forget(name)
    try:
        keychain.delete_password(KEYCHAIN, f"{name}:bearer_token")
    except Exception:
        pass
    for key in [*(spec.get("secret_env") or []), *(f"header:{h}" for h in spec.get("secret_headers") or [])]:
        try:
            keychain.delete_password(KEYCHAIN, f"{name}:{key}")
        except Exception:
            pass
    _save_config(cfg)
    return True


# ---------------------------------------------------------------- connections

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

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def _http_target(self) -> Any:
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
        if self.spec.get("type") == "sse":
            from mcp.client.sse import sse_client
            return sse_client(url, headers=headers or None, timeout=15, auth=auth)
        return streamable_http_client(url, http_client=create_mcp_http_client(headers=headers or None, auth=auth))

    async def _run(self) -> None:
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters
        try:
            if self.spec.get("url"):
                target: Any = self._http_target()
            else:
                # What a program needs to start (on Windows: APPDATA, SYSTEMROOT, PATHEXT… or npx can't run), PATH as
                # it is now (Node installed a minute ago counts), then the server's own settings.
                from mcp.client.stdio import get_default_environment
                from app.agent.worker import _fresh_path
                env = {**get_default_environment(), "PATH": _fresh_path(),
                       **{k: v for k, v in os.environ.items() if k in ("LANG", "TMPDIR", "SHELL", "TMP")},
                       **_env_for(self.name, self.spec)}
                command = self.spec["command"]
                if command == "{xod_python}":          # Xod's own built-in connectors
                    import sys
                    from app.paths import APP_ROOT
                    command = sys.executable
                    env.update({k: v for k, v in os.environ.items() if k.startswith("XOD_") or k == "PYTHONPATH"})
                    self.spec = {**self.spec, "cwd": str(APP_ROOT)}
                else:
                    import shutil
                    command = shutil.which(command, path=env["PATH"]) or command      # npx → …\nodejs\npx.cmd
                target = StdioServerParameters(command=command, args=self.spec.get("args") or [], env=env,
                                                cwd=self.spec.get("cwd") or None)
            async with Client(target, read_timeout_seconds=120) as client:
                self.client = client
                self.tools = (await client.list_tools()).tools
                self.error, self.needs_sign_in = None, False
                self.ready.set()
                logger.info(f"MCP {self.name}: {len(self.tools)} tools")
                await self._stop.wait()
        except BaseException as e:   # the SDK wraps failures in exception groups
            sign_in = _find_exc(e, mcp_oauth.SignInNeeded)
            if sign_in:
                self.needs_sign_in, self.error = True, None
            else:
                inner = _first_leaf(e)
                self.error = f"{type(inner).__name__}: {inner}"[:300]
                logger.warning(f"MCP {self.name} failed: {self.error}")
            if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
        finally:
            self.client = None
            self.ready.set()

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, 10)
            except Exception:
                self._task.cancel()


class MCPHub:
    def __init__(self) -> None:
        self.conns: Dict[str, ServerConn] = {}
        self.pending: Dict[str, Dict[str, Any]] = {}

    async def start(self) -> None:
        """(Re)connects every enabled server in the config."""
        cfg = load_config()
        for name in list(self.conns):
            await self.conns.pop(name).stop()
        for name, spec in cfg.items():
            if spec.get("enabled", True):
                conn = ServerConn(name, spec)
                self.conns[name] = conn
                conn.start()
        await asyncio.gather(*(asyncio.wait_for(c.ready.wait(), 30) for c in self.conns.values()), return_exceptions=True)

    async def connect(self, name: str, interactive: bool = False, redirect_uri: Optional[str] = None) -> Dict[str, Any]:
        """(Re)connects one server. interactive=True may open a sign-in page in the browser."""
        spec = load_config().get(name)
        if not spec:
            return {"success": False, "error": "No such server."}
        if name in self.conns:
            await self.conns.pop(name).stop()
        conn = ServerConn(name, spec, interactive=interactive, redirect_uri=redirect_uri)
        self.conns[name] = conn
        conn.start()
        try:
            await asyncio.wait_for(conn.ready.wait(), mcp_oauth.SIGN_IN_TIMEOUT + 30 if interactive else 45)
        except asyncio.TimeoutError:
            return {"success": False, "error": "It didn't answer in time."}
        if conn.client:
            return {"success": True, "tools": len(conn.tools)}
        return {"success": False, "needs_sign_in": conn.needs_sign_in, "error": conn.error or "Not connected."}

    def status(self) -> List[Dict[str, Any]]:
        out = []
        for name, spec in load_config().items():
            c = self.conns.get(name)
            out.append({"name": name, "enabled": spec.get("enabled", True), "trust": spec.get("trust", "ask"),
                        "command": " ".join([spec.get("command") or "", *spec.get("args", [])]).strip() or spec.get("url"),
                        "secret_env": spec.get("secret_env", []), "connector": spec.get("connector"),
                        "auth": spec.get("auth"), "needs_sign_in": bool(c and c.needs_sign_in),
                        "signing_in": name in mcp_oauth.sign_in_pages,
                        "connected": bool(c and c.client), "error": c.error if c else None,
                        "tools": [{"name": t.name, "description": (t.description or "")[:160],
                                   "read_only": _read_only(t)}
                                  for t in (c.tools if c else [])]})
        return out

    # ---------------------------------------------------------- tools for the model
    def tool_definitions(self) -> List[Dict[str, Any]]:
        defs = []
        for name, c in self.conns.items():
            if not c.client:
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
        words = set(re.findall(r"[a-z0-9]{3,}", (query or "").lower()))
        def score(d):
            text = (d["function"]["name"] + " " + d["function"]["description"]).lower()
            return sum(1 for w in words if w in text)
        ranked = sorted(defs, key=score, reverse=True)
        return [d for d in ranked[:limit] if score(d) > 0]

    def _find(self, full_name: str):
        _, server, tool = full_name.split("__", 2)
        c = self.conns.get(server)
        t = next((x for x in (c.tools if c else []) if x.name == tool or f"mcp__{server}__{x.name}"[:64] == full_name), None)
        return c, t

    # ---------------------------------------------------------- calling
    async def call(self, full_name: str, args: Dict[str, Any], approved: bool = False) -> Dict[str, Any]:
        try:
            c, t = self._find(full_name)
        except ValueError:
            return {"success": False, "error": f"Bad MCP tool name {full_name}"}
        if not c or not c.client or not t:
            return {"success": False, "error": f"The {full_name.split('__')[1]} tool server isn't connected (Tools → Connections → MCP)."}
        trust = (load_config().get(c.name) or {}).get("trust", "ask")   # current setting, not the one at connect time
        read_only = _read_only(t)
        destructive = _destructive(t)
        if not approved and (destructive or not (trust == "always" or (trust == "read_only" and read_only))):
            pid = uuid.uuid4().hex[:10]
            self.pending[pid] = {"id": pid, "tool": full_name, "server": c.name, "tool_name": t.name, "args": args,
                                 "description": (t.description or "")[:300], "created_at": time.time()}
            from app.services import agent_log
            agent_log.approval_opened(pid, "app", c.name, t.name.replace("_", " ").capitalize(),
                                      ", ".join(f"{k}: {str(v)[:60]}" for k, v in list((args or {}).items())[:4]))
            return {"success": False, "needs_approval": True, "approval_id": pid, "tool": full_name, "destructive": destructive,
                    "message": (f"Waiting for your OK to {t.name.replace('_', ' ')} on {c.name}. Nothing has been deleted yet."
                                if destructive else f"Waiting for your OK to run {t.name} on {c.name}. It hasn't run yet."),
                    "args": args}
        try:
            res = await c.client.call_tool(t.name, args or {})
        except Exception as e:
            return {"success": False, "error": f"{type(e).__name__}: {e}"[:400]}
        texts = [getattr(x, "text", None) or json.dumps(getattr(x, "model_dump", lambda: str(x))(), default=str)[:2000]
                 for x in (res.content or [])]
        is_error = bool(_attr(res, "is_error", "isError", False))
        out = {"success": not is_error, "server": c.name, "tool": t.name, "content": "\n".join(texts)[:20000]}
        structured = _attr(res, "structured_content", "structuredContent")
        if structured:
            out["data"] = structured
        if is_error:
            out["error"] = out["content"][:500] or "The tool reported an error."
            logger.warning(f"MCP {full_name} failed: {out['error'][:200]} (args: {json.dumps(args, default=str)[:300]})")
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
