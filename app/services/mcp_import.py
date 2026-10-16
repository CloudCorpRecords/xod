"""
Paste any MCP server, in whatever form its website gives it, and Xod works out the rest.

What it reads:
  JSON        Claude Desktop / Cursor / Windsurf / Cline ({"mcpServers": …}), VS Code ({"servers": …} or
              {"mcp": {"servers": …}}, with "type" and ${input:…}), Zed ({"context_servers": …}), Gemini ("httpUrl"),
              one server on its own ({"command": …} / {"url": …} / {"name": …, "command": …}), a fragment
              ("name": {…}) and JSON with comments or trailing commas
  TOML        Codex ([mcp_servers.name])
  YAML        Continue (mcpServers: - name: …)
  commands    claude mcp add …, claude mcp add-json …, codex mcp add …, gemini mcp add …, code --add-mcp '{…}',
              or just the command that starts the server (npx -y …, uvx …, docker run …)
  links       cursor://…/mcp/install?config=…, vscode:mcp/install?{…}, vscode.dev redirect links, any link with config=
  addresses   https://…/mcp (and …/sse for older servers)

What it works out:
  - placeholders (<YOUR_API_KEY>, ${input:token}, "your-key-here", /path/to/folder) become questions, and secrets
    go to the Keychain / Credential Manager, never the file
  - npx mcp-remote https://… becomes a direct connection (Xod signs in itself)
  - for an address: whether it answers as is, needs a sign-in Xod can do in one click (OAuth with self-registration),
    needs a key, or speaks the older SSE protocol; it asks the server (an MCP "initialize")
  - for a program: whether what starts it (npx, uvx, docker…) is on this computer, and what to install if not
"""
import asyncio
import base64
import json
import logging
import os
import re
import shlex
import shutil
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger("xod.mcp_import")

SECRETISH = re.compile(r"(key|token|secret|password|passwd|auth|credential|cookie|bearer|pat\b)", re.I)
_PLACEHOLDER = re.compile(
    r"<[^<>\n]{1,60}>|\$\{(?:input:|env:)?[\w.-]{1,60}\}|\{\{[\w.-]{1,60}\}\}|\[[A-Z][A-Z0-9_ ]{2,40}\]|"
    r"\b(?:YOUR|MY)[_-][A-Z0-9_-]{2,60}\b|\byour[-_ ](?:own[-_ ])?[\w-]{2,40}(?:[-_ ]here)?\b|"
    r"\b(?:replace[-_]?me|change[-_]?me|insert[-_ ][\w-]+[-_ ]here|[\w-]+[-_]here)\b|^x{4,}$|^\*{3,}$|^\.\.\.$|"
    r"^(?:sk|ghp|xoxb|pk)[-_](?:\.\.\.|x{3,}|\*{3,})$",
    re.I)
_PATHISH = re.compile(r"^(?:/path/to/|/Users/(?:username|you|your-?name|USER)/|C:\\\\?Users\\\\?(?:username|you|YOU)\\|~?/?your[-_/])", re.I)
_RUNTIMES = {
    "npx": ("Node.js", "Install Node.js LTS"), "node": ("Node.js", "Install Node.js LTS"), "npm": ("Node.js", "Install Node.js LTS"),
    "pnpm": ("pnpm", "Install pnpm"), "bunx": ("Bun", "Install Bun"), "bun": ("Bun", "Install Bun"),
    "uvx": ("uv (for Python tools)", "Install uv"), "uv": ("uv (for Python tools)", "Install uv"),
    "python": ("Python", "Install Python 3"), "python3": ("Python", "Install Python 3"), "pipx": ("pipx", "Install pipx"),
    "docker": ("Docker", "Install Docker Desktop"), "deno": ("Deno", "Install Deno"), "java": ("Java", "Install a Java runtime"),
    "go": ("Go", "Install Go"), "dotnet": (".NET", "Install the .NET SDK"),
}


# ------------------------------------------------------------------ reading what was pasted
def _strip_json_comments(text: str) -> str:
    out, i, quote = [], 0, ""
    while i < len(text):
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < len(text):
                out.append(text[i + 1]); i += 2; continue
            if c == quote:
                quote = ""
        elif c == '"':
            quote = c; out.append(c)
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = len(text) if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _loads_lenient(text: str) -> Optional[Any]:
    t = text.strip()
    for attempt in (t, _strip_json_comments(t)):
        try:
            return json.loads(attempt)
        except ValueError:
            pass
    clean = _strip_json_comments(t).strip().rstrip(",")
    if clean.startswith('"'):                    # "name": {…} on its own
        try:
            return json.loads("{" + clean + "}")
        except ValueError:
            pass
    # Text around a JSON block ("Add this to your config: {…}"): the outermost braces.
    a, b = t.find("{"), t.rfind("}")
    if 0 <= a < b and (a, b) != (0, len(t) - 1):
        return _loads_lenient(t[a:b + 1])
    return None


def _from_json(data: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """(name, raw spec) pairs from any of the JSON shapes."""
    if isinstance(data, list):
        out = []
        for x in data:
            out += _from_json(x)
        return out
    if not isinstance(data, dict):
        return []
    for key in ("mcpServers", "mcp_servers", "servers", "context_servers"):
        if isinstance(data.get(key), dict):
            return [(n, s) for n, s in data[key].items() if isinstance(s, dict)]
        if isinstance(data.get(key), list):
            return [(s.get("name") or "", s) for s in data[key] if isinstance(s, dict)]
    if isinstance(data.get("mcp"), dict):
        return _from_json(data["mcp"])
    if _is_spec(data):
        return [(data.get("name") or "", data)]
    # {"name": {spec}} with no wrapper
    pairs = [(n, s) for n, s in data.items() if isinstance(s, dict) and _is_spec(s)]
    return pairs


def _is_spec(d: Dict[str, Any]) -> bool:
    return any(k in d for k in ("command", "url", "serverUrl", "httpUrl", "uri")) or (isinstance(d.get("command"), dict))


def _from_toml(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    if "[mcp_servers" not in text and "[mcp.servers" not in text:
        return []
    try:
        import tomllib
        data = tomllib.loads(text)
    except Exception:
        return []
    block = data.get("mcp_servers") or (data.get("mcp") or {}).get("servers") or {}
    return [(n, s) for n, s in block.items() if isinstance(s, dict)]


def _from_yaml(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    if not re.search(r"^\s*(mcpServers|mcp_servers|servers)\s*:", text, re.M):
        return []
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception:
        return []
    return _from_json(data)


def _from_link(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    t = text.strip()
    m = re.search(r"(?:vscode(?:-insiders)?:/?/?mcp/install\?)(.+)$", unquote(t)) if "mcp/install" in t else None
    if m and m.group(1).lstrip().startswith("{"):
        data = _loads_lenient(m.group(1))
        return _from_json(data) if data else []
    if not re.match(r"^[a-z][\w+.-]*://", t, re.I):
        return []
    u = urlparse(t)
    q = parse_qs(u.query)
    if "url" in q and "mcp/install" in q["url"][0]:          # vscode.dev/redirect?url=vscode:mcp/install?{…}
        return _from_link(q["url"][0])
    if "config" in q:
        raw = q["config"][0]
        data = None
        for decode in (lambda s: base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8"),
                       lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8"), lambda s: s):
            try:
                data = _loads_lenient(decode(raw))
            except Exception:
                data = None
            if data:
                break
        if isinstance(data, dict):
            name = (q.get("name") or [""])[0]
            pairs = _from_json(data)
            if pairs:
                return pairs
            return [(name, data)] if _is_spec(data) else []
    return []


def _clean_cmdline(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^\s*(?:\$|>|PS [^>]*>)\s*", "", t)
    return re.sub(r"\s*\\\r?\n\s*|\s*`\r?\n\s*|\s*\^\r?\n\s*", " ", t)      # line continuations (bash, PowerShell, cmd)


def _split(cmd: str) -> List[str]:
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        return cmd.split()


def _kv(s: str) -> Tuple[str, str]:
    k, _, v = s.partition("=")
    return k.strip(), v.strip()


def _header(s: str) -> Tuple[str, str]:
    k, _, v = s.partition(":")
    return k.strip(), v.strip()


def _from_command(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    t = _clean_cmdline(text)
    if "\n" in t.strip():
        return []
    parts = _split(t)
    if not parts:
        return []
    low = [p.lower() for p in parts]
    # code --add-mcp '{"name": …}'
    if low[0] in ("code", "code-insiders", "cursor") and "--add-mcp" in low:
        data = _loads_lenient(parts[low.index("--add-mcp") + 1]) if low.index("--add-mcp") + 1 < len(parts) else None
        return _from_json(data) if data else []
    # claude / codex / gemini / qwen mcp add …
    if len(parts) >= 3 and low[1] == "mcp" and low[2] in ("add", "add-json"):
        rest = parts[3:]
        if low[2] == "add-json":
            names = [p for p in rest if not p.startswith("-")]
            if len(names) >= 2:
                data = _loads_lenient(names[1])
                if isinstance(data, dict):
                    return [(names[0], data)]
            return []
        env, headers, transport, positional, cmd = {}, {}, None, [], None
        i = 0
        while i < len(rest):
            p = rest[i]
            if p == "--":
                cmd = rest[i + 1:]
                break
            if p in ("-e", "--env") and i + 1 < len(rest):
                k, v = _kv(rest[i + 1]); env[k] = v; i += 2; continue
            if p.startswith("--env="):
                k, v = _kv(p[6:]); env[k] = v; i += 1; continue
            if p in ("-H", "--header") and i + 1 < len(rest):
                k, v = _header(rest[i + 1]); headers[k] = v; i += 2; continue
            if p in ("-t", "--transport") and i + 1 < len(rest):
                transport = rest[i + 1].lower(); i += 2; continue
            if p.startswith("--transport="):
                transport = p.split("=", 1)[1].lower(); i += 1; continue
            if p in ("-s", "--scope", "--client-id", "--client-secret", "--callback-port", "--timeout", "--trust") and i + 1 < len(rest):
                i += 2; continue
            if p.startswith("-") and not cmd:
                i += 1; continue
            positional.append(p)
            i += 1
        if not positional:
            return []
        name = positional[0]
        target = cmd or positional[1:]
        if not target:
            return []
        if re.match(r"^https?://", target[0]):
            spec: Dict[str, Any] = {"url": target[0], "headers": headers}
            if transport in ("sse",):
                spec["type"] = "sse"
        else:
            spec = {"command": target[0], "args": target[1:]}
        if env:
            spec["env"] = env
        return [(name, spec)]
    # Just the command that starts the server.
    first = parts[0].lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    first = re.sub(r"\.(exe|cmd|bat)$", "", first)
    if first in ("npx", "uvx", "bunx", "pnpx", "docker", "pipx", "uv", "node", "python", "python3", "deno", "pnpm", "bun"):
        if first in ("pnpm", "bun") and (len(parts) < 2 or parts[1] not in ("dlx", "x")):
            return []
        return [("", {"command": parts[0], "args": parts[1:]})]
    return []


def _from_url(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    t = text.strip().strip("<>").strip()
    if re.match(r"^https?://\S+$", t):
        spec: Dict[str, Any] = {"url": t}
        if urlparse(t).path.rstrip("/").endswith("/sse"):
            spec["type"] = "sse"
        return [("", spec)]
    return []


# ------------------------------------------------------------------ one tidy spec per server
_VALUE_FLAGS = {"-e", "--env", "-v", "--volume", "-p", "--publish", "--name", "--network", "--env-file", "-w", "--workdir",
                "--mount", "--from", "--with", "--python", "-u", "--user", "--entrypoint"}


def _name_for(name: str, spec: Dict[str, Any]) -> str:
    from app.services.mcp_client import _clean
    if name:
        return _clean(name.strip())
    if spec.get("url"):
        host = urlparse(spec["url"]).hostname or "server"
        parts = [p for p in host.split(".") if p not in ("www", "mcp", "api", "app", "com", "io", "dev", "ai", "net", "org", "co", "sh", "so", "run")]
        return _clean(parts[0] if parts else host.split(".")[0])
    args, skip = [], False
    for a in spec.get("args") or []:
        if skip:
            skip = False
            continue
        if a in _VALUE_FLAGS:            # docker -e NAME, -v a:b, --name x …: the value isn't the package
            skip = True
            continue
        if not a.startswith("-"):
            args.append(a)
    pkg = next((a for a in args if a not in ("run", "dlx", "x", "tool")), spec.get("command") or "server")
    pkg = pkg.split("@latest")[0]
    base = re.sub(r"^@[^/]+/", "", pkg).rsplit("/", 1)[-1].split(":")[0]
    base = re.sub(r"^(?:mcp[-_]server[-_]|server[-_]|mcp[-_])|(?:[-_]mcp[-_]server|[-_]mcp|[-_]server)$", "", base, flags=re.I)
    return _clean(base or "server")


def _normalize(name: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """Any shape → {command, args, env, cwd} or {url, type, headers}."""
    spec: Dict[str, Any] = {}
    cmd = raw.get("command")
    if isinstance(cmd, dict):                                   # Zed: {"command": {"path": …, "args": …, "env": …}}
        spec.update(command=cmd.get("path") or cmd.get("command"), args=cmd.get("args") or [], env=cmd.get("env") or {})
    elif isinstance(cmd, list):                                 # some write ["npx", "-y", "pkg"]
        spec.update(command=cmd[0] if cmd else None, args=list(cmd[1:]) + list(raw.get("args") or []))
    elif isinstance(cmd, str) and cmd.strip():
        c = cmd.strip()
        if " " in c and not raw.get("args") and not re.match(r'^[A-Za-z]:\\|^/', c):   # "npx -y pkg" in one string
            bits = _split(c)
            spec.update(command=bits[0], args=bits[1:])
        else:
            spec.update(command=c, args=list(raw.get("args") or []))
    url = raw.get("url") or raw.get("serverUrl") or raw.get("httpUrl") or raw.get("uri")
    if url and not spec.get("command"):
        spec["url"] = str(url).strip()
        t = str(raw.get("type") or raw.get("transport") or "").lower()
        if t == "sse" or (not t and urlparse(spec["url"]).path.rstrip("/").endswith("/sse")):
            spec["type"] = "sse"
        hdrs = raw.get("headers")
        if not hdrs and isinstance(raw.get("requestInit"), dict):        # mcp-remote style {"requestInit": {"headers": …}}
            hdrs = raw["requestInit"].get("headers")
        if isinstance(hdrs, dict) and hdrs:
            spec["headers"] = {str(k): str(v) for k, v in hdrs.items()}
    if spec.get("command"):
        spec["args"] = [str(a) for a in spec.get("args") or []]
        env = raw.get("env") if isinstance(raw.get("env"), dict) else spec.get("env") or {}
        spec["env"] = {str(k): "" if v is None else str(v) for k, v in (env or {}).items()}
        if raw.get("cwd"):
            spec["cwd"] = str(raw["cwd"])
        spec = _unwrap_remote(spec)
    return spec


def _unwrap_remote(spec: Dict[str, Any]) -> Dict[str, Any]:
    """npx -y mcp-remote https://… [--header "Authorization: Bearer ${TOKEN}"]: Xod connects to the address itself (and
    signs in with OAuth itself), so the Node bridge isn't needed."""
    args = spec.get("args") or []
    idx = next((i for i, a in enumerate(args) if re.sub(r"@.*$", "", a) in ("mcp-remote", "@modelcontextprotocol/mcp-remote", "supergateway")), None)
    if idx is None:
        return spec
    rest = args[idx + 1:]
    url = next((a for a in rest if re.match(r"^https?://", a)), None)
    if not url and "--sse" in rest:
        i = rest.index("--sse")
        url = rest[i + 1] if i + 1 < len(rest) else None
    if not url:
        return spec
    headers = {}
    env = spec.get("env") or {}
    for i, a in enumerate(rest):
        if a == "--header" and i + 1 < len(rest):
            k, v = _header(rest[i + 1])
            v = re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1), m.group(0)), v)
            headers[k] = v
    out: Dict[str, Any] = {"url": url}
    if headers:
        out["headers"] = headers
    if "--transport" in rest and rest.index("--transport") + 1 < len(rest) and rest[rest.index("--transport") + 1].startswith("sse") \
            or urlparse(url).path.rstrip("/").endswith("/sse"):
        out["type"] = "sse"
    return out


def _is_placeholder(value: str) -> bool:
    v = (value or "").strip()
    return bool(v) and bool(_PLACEHOLDER.search(v) or _PATHISH.search(v))


def _needs(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What the person still has to fill in: placeholders, and empty secrets."""
    out: List[Dict[str, Any]] = []
    for k, v in (spec.get("env") or {}).items():
        if _is_placeholder(v) or (not v and SECRETISH.search(k)):
            out.append({"id": f"env:{k}", "label": k, "secret": bool(SECRETISH.search(k)), "where": "env", "key": k,
                        "hint": v if v and not SECRETISH.search(k) else ""})
    for k, v in (spec.get("headers") or {}).items():
        if _is_placeholder(v):
            prefix = "Bearer " if v.lower().startswith("bearer ") else ""
            inner = re.search(r"<([^<>]+)>|\$\{(?:input:|env:)?([\w.-]+)\}|\{\{([\w.-]+)\}\}|\b((?:YOUR|MY)[_-][A-Z0-9_-]+)", v)
            name = next((g for g in (inner.groups() if inner else ()) if g), "")
            out.append({"id": f"header:{k}", "label": name or ("API key or token" if prefix else f"{k} (header)"), "secret": True,
                        "where": "header", "key": k, "prefix": prefix})
    args = spec.get("args") or []
    for i, a in enumerate(args):
        if _is_placeholder(a):
            flag = (args[i - 1] if i > 0 and args[i - 1].startswith("-") else "")
            m = re.match(r"^(--?[\w-]+=)(.*)$", a)
            value = m.group(2) if m else a
            label = (m.group(1).strip("-=") if m else flag.strip("-")) or re.sub(r"[<>${}]|input:", "", a).strip() or "value"
            secret = bool(SECRETISH.search(label + a))
            pathish = bool(_PATHISH.search(value)) or bool(re.search(r"path|dir|folder|file", label, re.I))
            if pathish and not (m or flag):
                is_file = bool(re.search(r"\.\w{1,6}$", value))
                label = "File" if is_file else "Folder"
            out.append({"id": f"arg:{i}", "label": label.replace("_", " "), "secret": secret, "where": "arg", "index": i,
                        "prefix": m.group(1) if m else "",
                        "hint": (f"on this computer (the example was {value})" if pathish else "")})
        elif i > 0 and args[i - 1] in ("-e", "--env") and "=" not in a and spec.get("command", "").lower().rsplit("/", 1)[-1].startswith("docker") \
                and not (spec.get("env") or {}).get(a):
            # docker run -e GITHUB_TOKEN …: Docker passes the value through from Xod, so Xod needs it.
            out.append({"id": f"env:{a}", "label": a, "secret": bool(SECRETISH.search(a)), "where": "env", "key": a, "hint": ""})
    url = spec.get("url") or ""
    for ph in re.findall(r"<[^<>]{1,40}>|\$\{[\w:.-]{1,40}\}|\{\{[\w.-]{1,40}\}\}|YOUR[_-][A-Z0-9_-]+", url):
        out.append({"id": f"url:{ph}", "label": re.sub(r"[<>${}]|input:", "", ph).strip() or "value", "secret": bool(SECRETISH.search(ph)),
                    "where": "url", "key": ph})
    return out


def _runtime(spec: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """What starts it, when that isn't on this computer."""
    cmd = spec.get("command")
    if not cmd:
        return None
    from app.agent.worker import _fresh_path
    if shutil.which(cmd, path=_fresh_path()) or (("/" in cmd or "\\" in cmd) and shutil.which(cmd)):
        return None
    base = re.sub(r"\.(exe|cmd|bat)$", "", cmd.lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
    what, ask = _RUNTIMES.get(base, (cmd, f"Install {cmd}"))
    return {"program": cmd, "what": what, "ask": ask}


def parse(text: str) -> Dict[str, Any]:
    """Every server in what was pasted, tidied, with what's missing. Nothing is saved."""
    text = (text or "").strip()
    if not text:
        return {"servers": [], "error": "Paste what the server's page says to add: a JSON block, a command, a link or an address."}
    pairs: List[Tuple[str, Dict[str, Any]]] = []
    fmt = ""
    for kind, reader in (("link", _from_link), ("address", _from_url), ("json", lambda t: _from_json(_loads_lenient(t))),
                         ("toml", _from_toml), ("yaml", _from_yaml), ("command", _from_command)):
        try:
            pairs = reader(text)
        except Exception as e:
            logger.debug(f"{kind} reader: {e}")
            pairs = []
        if pairs:
            fmt = kind
            break
    if not pairs:
        # Several commands, one per line ("claude mcp add …" for each)
        for line in text.splitlines():
            try:
                pairs += _from_command(line)
            except Exception:
                pass
        fmt = "command" if pairs else ""
    if not pairs:
        # "Can you add this: claude mcp add … / npx -y …" — the command inside a sentence.
        m = re.search(r"(?:\b(?:claude|codex|gemini|qwen)\s+mcp\s+add(?:-json)?|\b(?:npx|uvx|bunx|pnpx)\s+(?:-y\s+)?@?[\w./-]+|\bcode\s+--add-mcp)[^\n]*", text)
        if m:
            pairs = _from_command(m.group(0))
            fmt = "command" if pairs else fmt
    if not pairs:
        # "Connect this MCP: https://mcp.example.com/mcp" — the address in a sentence.
        m = re.search(r"https?://[^\s<>\"'`)]+", text)
        if m:
            pairs, fmt = _from_url(m.group(0).rstrip(".,;")), "address"
    servers, seen = [], set()
    for name, raw in pairs:
        spec = _normalize(name, raw)
        if not (spec.get("command") or spec.get("url")):
            continue
        n = _name_for(name, spec)
        while n in seen:
            n += "_2"
        seen.add(n)
        servers.append({"name": n, "spec": spec, "kind": "web" if spec.get("url") else "program",
                        "needs": _needs(spec), "runtime": _runtime(spec), "summary": _summary(spec)})
    if not servers:
        return {"servers": [], "error": "I couldn't find an MCP server in that. Paste the JSON block, the install command, "
                                        "the install link or the server's address from its page."}
    return {"servers": servers, "format": fmt, "error": None}


def _summary(spec: Dict[str, Any]) -> str:
    if spec.get("url"):
        return spec["url"] + (" (SSE)" if spec.get("type") == "sse" else "")
    return " ".join([spec["command"], *spec.get("args", [])])[:200]


# ------------------------------------------------------------------ asking the server
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "xod", "version": "1"}}}


async def _json_at(c, url: str) -> Optional[Dict[str, Any]]:
    try:
        r = await c.get(url, headers={"Accept": "application/json"})
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


async def probe(url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """_probe, and when an older …/sse address doesn't answer (many servers retired SSE: DeepWiki's says 410 Gone),
    the …/mcp address next to it. A working one comes back as "url"."""
    first = await _probe(url, headers)
    path = urlparse(url).path.rstrip("/")
    if first.get("ok") or first.get("auth") or not path.endswith("/sse"):
        return first
    newer = url.split("?")[0].rstrip("/")[:-4] + "/mcp" + (("?" + url.split("?", 1)[1]) if "?" in url else "")
    second = await _probe(newer, headers)
    if second.get("ok") or second.get("auth"):
        return {**second, "url": newer, "why": second["why"] + " (The …/sse address is retired; using …/mcp.)"}
    return first


async def _probe(url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """How to connect to an address: {"auth": None | "oauth" | "key", "type": "http" | "sse", "ok": bool, "why": …}.
    open: it answered as is. oauth: it asks for a sign-in Xod can do in one click (OAuth with self-registration).
    key: it wants a key or token. sse: an older server that speaks SSE."""
    import httpx
    hdrs = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **(headers or {})}
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as c:
        try:
            r = await c.post(url, json=INIT, headers=hdrs)
        except Exception as e:
            return {"ok": False, "auth": None, "type": "http", "why": f"Couldn't reach it ({type(e).__name__}). Check the address and your internet."}
        if r.status_code == 200 and ("jsonrpc" in r.text or "event:" in r.text or "data:" in r.text):
            return {"ok": True, "auth": "headers" if headers else None, "type": "http", "why": "It answered."}
        if r.status_code in (401, 403):
            p = urlparse(url)
            origin = f"{p.scheme}://{p.netloc}"
            m = re.search(r'resource_metadata="([^"]+)"', r.headers.get("www-authenticate", ""))
            prm = await _json_at(c, m.group(1)) if m else None
            prm = prm or await _json_at(c, f"{origin}/.well-known/oauth-protected-resource{p.path}") or \
                await _json_at(c, f"{origin}/.well-known/oauth-protected-resource")
            meta = None
            for iss in (prm or {}).get("authorization_servers") or [origin]:
                ip = urlparse(iss)
                io, path = f"{ip.scheme}://{ip.netloc}", ip.path.rstrip("/")
                meta = (await _json_at(c, f"{io}/.well-known/oauth-authorization-server{path}") or
                        await _json_at(c, f"{io}/.well-known/openid-configuration{path}"))
                if meta:
                    break
            if headers and r.status_code in (401, 403):
                return {"ok": False, "auth": "key", "type": "http", "why": "It didn't accept that key or token."}
            if meta and meta.get("registration_endpoint"):
                return {"ok": True, "auth": "oauth", "type": "http",
                        "why": f"It needs you to sign in ({urlparse(meta.get('authorization_endpoint', '')).netloc or p.netloc}); Xod opens the page."}
            return {"ok": False, "auth": "key", "type": "http",
                    "why": "It needs an API key or token (it has no one-click sign-in). Get one from its website and paste it here."}
        # Older servers: GET the address and an event stream comes back.
        try:
            async with c.stream("GET", url, headers={"Accept": "text/event-stream", **(headers or {})}) as g:
                if g.status_code == 200 and "text/event-stream" in g.headers.get("content-type", ""):
                    return {"ok": True, "auth": "headers" if headers else None, "type": "sse", "why": "It answered (older SSE protocol)."}
                if g.status_code in (401, 403):
                    return {"ok": False, "auth": "key", "type": "sse", "why": "It needs an API key or token."}
        except Exception:
            pass
        return {"ok": False, "auth": None, "type": "http", "why": f"It didn't answer as an MCP server (HTTP {r.status_code}). Check the address."}


async def check(text: str) -> Dict[str, Any]:
    """parse, then ask each address how to connect, all at once."""
    res = parse(text)
    web = [s for s in res["servers"] if s["kind"] == "web" and not any(n["where"] in ("url", "header") for n in s["needs"])]
    probes = await asyncio.gather(*(probe(s["spec"]["url"], s["spec"].get("headers")) for s in web), return_exceptions=True)
    for s, p in zip(web, probes):
        s["probe"] = p if isinstance(p, dict) else {"ok": False, "why": str(p)}
        if s["probe"].get("url"):
            s["spec"]["url"] = s["probe"]["url"]
            s["spec"].pop("type", None)
            s["summary"] = _summary(s["spec"])
        if s["probe"].get("type") == "sse":
            s["spec"]["type"] = "sse"
        if s["probe"].get("auth") == "key" and not s["spec"].get("headers"):
            s["needs"].append({"id": "header:Authorization", "label": "API key or token", "secret": True, "where": "header",
                               "key": "Authorization", "prefix": "Bearer "})
    return res


# ------------------------------------------------------------------ adding it
def _fill(server: Dict[str, Any], values: Dict[str, str]) -> Tuple[Dict[str, Any], List[str]]:
    spec = json.loads(json.dumps(server["spec"]))
    missing = []
    for n in server["needs"]:
        v = (values.get(n["id"]) or "").strip()
        if not v:
            missing.append(n["label"])
            continue
        if n["where"] == "env":
            spec.setdefault("env", {})[n["key"]] = v
        elif n["where"] == "header":
            pre = n.get("prefix") or ""
            spec.setdefault("headers", {})[n["key"]] = v if (not pre or v.lower().startswith(pre.lower().strip())) else pre + v
        elif n["where"] == "arg":
            spec["args"][n["index"]] = (n.get("prefix") or "") + v
        elif n["where"] == "url":
            spec["url"] = spec["url"].replace(n["key"], v)
    return spec, missing


def save(name: str, spec: Dict[str, Any], auth: Optional[str]) -> str:
    """Into mcp_servers.json, secrets (env and header values that look secret) into the Keychain."""
    from app.services import keychain, mcp_client
    cfg = mcp_client.load_config()
    base, n = name, 2
    while name in cfg and (cfg[name].get("url") or cfg[name].get("command")) != (spec.get("url") or spec.get("command")):
        name = f"{base}_{n}"
        n += 1
    # Like the one-click connectors: what the server marks read-only runs, anything else waits for an OK (and deleting
    # always does).
    entry: Dict[str, Any] = {"enabled": True, "trust": (cfg.get(name) or {}).get("trust", "read_only"), "imported": True}
    if spec.get("url"):
        entry["url"] = spec["url"]
        if spec.get("type") == "sse":
            entry["type"] = "sse"
        headers, secret_headers = {}, []
        for k, v in (spec.get("headers") or {}).items():
            if SECRETISH.search(k) or SECRETISH.search(v[:20]) or len(v) > 24:
                keychain.set_password(mcp_client.KEYCHAIN, f"{name}:header:{k}", v)
                secret_headers.append(k)
            else:
                headers[k] = v
        if headers:
            entry["headers"] = headers
        if secret_headers:
            entry["secret_headers"] = secret_headers
        if auth == "oauth":
            entry["auth"] = "oauth"
    else:
        entry.update(command=spec["command"], args=spec.get("args") or [])
        if spec.get("cwd"):
            entry["cwd"] = spec["cwd"]
        env, secret_env = {}, []
        for k, v in (spec.get("env") or {}).items():
            if SECRETISH.search(k) and v:
                keychain.set_password(mcp_client.KEYCHAIN, f"{name}:{k}", v)
                secret_env.append(k)
            else:
                env[k] = v
        entry.update(env=env, secret_env=secret_env)
    cfg[name] = entry
    mcp_client._save_config(cfg)
    return name


_tasks: Dict[str, Any] = {}


async def add(text: str, values: Optional[Dict[str, Dict[str, str]]] = None, redirect_uri: str = "",
              only: Optional[List[str]] = None) -> Dict[str, Any]:
    """Parses again (the page's copy isn't trusted), fills in the answers, saves and connects each server.
    A sign-in opens in the browser and finishes in the background; the others are connected before this returns."""
    from app.services.mcp_client import mcp_hub
    res = await check(text)
    if res.get("error"):
        return {"success": False, "error": res["error"], "results": []}
    values = values or {}
    results = []
    for s in res["servers"]:
        if only and s["name"] not in only:
            continue
        spec, missing = _fill(s, values.get(s["name"]) or {})
        if missing:
            results.append({"name": s["name"], "success": False, "needs": s["needs"], "error": f"Fill in: {', '.join(missing)}."})
            continue
        if s.get("runtime"):
            rt = s["runtime"]
            results.append({"name": s["name"], "success": False, "runtime": rt,
                            "error": f"It runs with {rt['program']}, which needs {rt['what']} on this computer."})
            continue
        auth = None
        if s["kind"] == "web":
            p = s.get("probe") or await probe(spec["url"], spec.get("headers"))
            if spec.get("headers") and s["needs"]:          # a key was just filled in: ask again with it
                p = await probe(spec["url"], spec.get("headers"))
                if p.get("url"):
                    spec["url"] = p["url"]
                    spec.pop("type", None)
                if p.get("type") == "sse":
                    spec["type"] = "sse"
            if not p.get("ok") and p.get("auth") != "oauth":
                results.append({"name": s["name"], "success": False, "error": p.get("why") or "It didn't answer."})
                continue
            auth = p.get("auth")
        name = save(s["name"], spec, auth)
        if auth == "oauth":
            _tasks[name] = asyncio.create_task(mcp_hub.connect(name, interactive=True, redirect_uri=redirect_uri))
            results.append({"name": name, "success": True, "signing_in": True,
                            "message": "A sign-in page opened in your browser; it connects when you finish."})
            continue
        r = await mcp_hub.connect(name)
        if r.get("success"):
            results.append({"name": name, "success": True, "tools": r.get("tools", 0),
                            "tool_names": [t.name for t in (mcp_hub.conns[name].tools if name in mcp_hub.conns else [])][:12]})
        else:
            results.append({"name": name, "success": False, "saved": True, "error": friendly(r.get("error") or "", spec)})
    ok = [r for r in results if r["success"]]
    return {"success": bool(ok), "results": results}


def friendly(error: str, spec: Dict[str, Any]) -> str:
    low = error.lower()
    if "filenotfound" in low or "no such file" in low or "cannot find the file" in low:
        return f"{spec.get('command')} isn't on this computer."
    if "401" in low or "unauthorized" in low:
        return "It didn't accept the key or token."
    if "connecterror" in low or "name resolution" in low or "getaddrinfo" in low:
        return "Couldn't reach it. Check the address and your internet."
    if "timed out" in low or "didn't answer in time" in low:
        return ("It didn't start in time. The first start downloads it, which can take a minute: press Try again."
                if spec.get("command") else "It didn't answer in time.")
    if "connection closed" in low or "eof" in low or "exited" in low:
        return "The server started and then stopped. It may need a setting or key it wasn't given; check its page."
    return error[:300] or "Not connected."


# ------------------------------------------------------------------ pasted into the chat
_LOOKS_RE = re.compile(r'"mcpServers"|"mcp_servers"|"context_servers"|\[mcp_servers\.|\bmcp add(?:-json)?\b|--add-mcp|'
                       r"mcp/install\?|cursor-deeplink|\bmcp-remote\b|\"(?:command|url|serverUrl)\"\s*:", re.I)
_ASK_RE = re.compile(r"\b(?:add|connect|install|set ?up|hook up|integrate|plug in)\b[^.?!]{0,40}\b(?:mcp|mcp server|tool server)\b|"
                     r"\bmcp\b[^.?!]{0,30}\b(?:add|connect|install|set ?up|integrate)\b", re.I)


async def stream_chat(text: str):
    """The same, pasted into the chat: what can connect now is connected; what needs a key or a program says so.
    Keys aren't asked for in the chat (they'd stay in the history): the panel has a box for them."""
    yield {"type": "status", "content": "Reading the MCP server and trying it…"}
    yield {"type": "tool_call", "name": "xod_add_mcp", "args": {}, "id": "mcp_add"}
    res = await check(text)
    if res.get("error"):
        yield {"type": "tool_result", "name": "xod_add_mcp", "id": "mcp_add", "summary": "Nothing to add", "data": res}
        yield {"type": "content", "content": res["error"]}
        yield {"type": "done"}
        return
    ready = [s["name"] for s in res["servers"] if not s["needs"] and not s.get("runtime")
             and (s["kind"] == "program" or (s.get("probe") or {}).get("ok"))]
    from app.services import previews
    base = previews.SERVER_BASE or "http://127.0.0.1:" + os.environ.get("XOD_PORT", "8000")
    out = await add(text, {}, base.rstrip("/") + "/oauth/callback", only=ready) if ready else {"results": []}
    done = {r["name"]: r for r in out.get("results", [])}
    lines = []
    for s in res["servers"]:
        r = done.get(s["name"])
        if r and r.get("success") and r.get("signing_in"):
            lines.append(f"**{s['name']}**: a sign-in page opened in your browser. When you finish, its tools are ready to use.")
        elif r and r.get("success"):
            tools = ", ".join(r.get("tool_names") or [])
            lines.append(f"**{s['name']}** is connected: {r['tools']} tool{'s' if r['tools'] != 1 else ''}" + (f" ({tools})" if tools else "")
                         + ". Ask for anything that needs it.")
        elif s.get("runtime"):
            rt = s["runtime"]
            lines.append(f"**{s['name']}** runs with `{rt['program']}`, which needs {rt['what']} on this computer. "
                         f"Say “{rt['ask'].lower()}” and I'll install it, then paste this again.")
        elif s["needs"]:
            what = ", ".join(n["label"] for n in s["needs"])
            lines.append(f"**{s['name']}** needs {what}. Paste the same thing in Tools → Connected apps → Add any MCP server: "
                         "there's a box for each, and keys go into your Keychain / Credential Manager, not the chat.")
        else:
            why = (r or {}).get("error") or (s.get("probe") or {}).get("why") or "it didn't connect"
            lines.append(f"**{s['name']}**: {why}")
    ok = sum(1 for r in done.values() if r.get("success"))
    yield {"type": "tool_result", "name": "xod_add_mcp", "id": "mcp_add", "data": out,
           "summary": f"{ok} of {len(res['servers'])} connected" if res["servers"] else "Nothing to add"}
    yield {"type": "content", "content": "\n\n".join(lines)}
    yield {"type": "done"}


def looks_like(text: str) -> bool:
    """A message that is (or contains) an MCP server to add: a config block, an install command or link, or
    "connect this MCP: https://…"."""
    t = text or ""
    if _LOOKS_RE.search(t):
        return True
    return bool(_ASK_RE.search(t) and re.search(r"https?://\S+", t))
