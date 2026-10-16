"""
Paste any MCP server (services/mcp_import.py), against stand-in servers on this computer: reading every format a
server's page hands out (JSON for each app, TOML, YAML, commands, install links, addresses, a command inside a
sentence), placeholders turned into questions, mcp-remote turned into a direct connection, asking an address how to
connect (open, a key, a one-click sign-in, a retired …/sse with …/mcp next to it), connecting a program with its key
(the key in the Keychain, not the file), a missing program named with what to install, and pasting it into the chat.
No real model or internet needed.

  python tests/smoke/run_mcp_import_test.py
"""
import asyncio
import base64
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="xod-mcpimport-")
os.environ["XOD_DATA_DIR"] = os.path.join(TMP, "data")

import uvicorn  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402

from app.services import mcp_import, mcp_client, keychain  # noqa: E402

failures, passed = [], 0


def check(name, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failures.append(name)
        print(f"  ✗ {name}  {str(detail)[:300]}")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


# ------------------------------------------------------------------ stand-in servers
def mcp_app():
    srv = MCPServer("standin")

    @srv.tool()
    def shout(text: str) -> str:
        """Says it louder."""
        return text.upper() + "!"
    return srv.streamable_http_app()


def open_server():
    """/mcp answers; /sse is retired (410), like DeepWiki's."""
    inner = mcp_app()

    async def app(scope, receive, send):
        if scope["type"] == "http" and scope["path"].rstrip("/") == "/sse":
            await send({"type": "http.response.start", "status": 410, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"error":"gone"}'})
            return
        await inner(scope, receive, send)
    return app


def key_server():
    """Answers only with X-Api-Key: k123 (no sign-in metadata: it wants a key)."""
    inner = mcp_app()

    async def app(scope, receive, send):
        if scope["type"] == "http" and dict(scope["headers"]).get(b"x-api-key") != b"k123":
            await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"error":"key"}'})
            return
        await inner(scope, receive, send)
    return app


def oauth_server(port):
    """Asks for a sign-in and publishes OAuth metadata with self-registration."""
    base = f"http://127.0.0.1:{port}"
    docs = {"/.well-known/oauth-protected-resource/mcp": {"resource": base + "/mcp", "authorization_servers": [base]},
            "/.well-known/oauth-authorization-server": {"issuer": base, "authorization_endpoint": base + "/authorize",
                                                        "token_endpoint": base + "/token", "registration_endpoint": base + "/register"}}

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                m = await receive()
                if m["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif m["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"}); return
        doc = docs.get(scope["path"])
        if doc:
            body = json.dumps(doc).encode()
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"www-authenticate", f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource/mcp"'.encode())]})
        await send({"type": "http.response.body", "body": b""})
    return app


servers = []


def serve(app, port):
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on")
    s = uvicorn.Server(cfg)
    servers.append(s)
    threading.Thread(target=s.run, daemon=True).start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.1)


P_OPEN, P_KEY, P_OAUTH = free_port(), free_port(), free_port()
serve(open_server(), P_OPEN)
serve(key_server(), P_KEY)
serve(oauth_server(P_OAUTH), P_OAUTH)
OPEN, KEY, OAUTH = (f"http://127.0.0.1:{p}" for p in (P_OPEN, P_KEY, P_OAUTH))

# A program server: reads its key from the environment.
STDIO = Path(TMP) / "keyed_server.py"
STDIO.write_text(
    "import os\nfrom mcp.server.mcpserver import MCPServer\nsrv = MCPServer('keyed')\n\n"
    "@srv.tool()\ndef whoami() -> str:\n    \"\"\"Which key it was given.\"\"\"\n    return 'key=' + os.environ.get('DEMO_API_KEY', '(none)')\n\n"
    "srv.run('stdio')\n", encoding="utf-8")

# ------------------------------------------------------------------ R reading every format
print("R reading what sites hand out")
cursor_cfg = base64.b64encode(json.dumps({"command": "npx", "args": ["-y", "@upstash/context7-mcp"]}).encode()).decode()
CASES = [
    ("Claude Desktop JSON", '{"mcpServers": {"brave-search": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"], "env": {"BRAVE_API_KEY": "YOUR_API_KEY_HERE"}}}}',
     "brave-search", "program", ["env:BRAVE_API_KEY"]),
    ("VS Code with ${input:…}", '{"inputs": [], "servers": {"perplexity": {"type": "stdio", "command": "npx", "args": ["-y", "server-perplexity-ask"], "env": {"PERPLEXITY_API_KEY": "${input:perplexity-key}"}}}}',
     "perplexity", "program", ["env:PERPLEXITY_API_KEY"]),
    ("a fragment with comments", '"stripe": {\n // Stripe\n "command": "npx",\n "args": ["-y", "@stripe/mcp", "--api-key=<STRIPE_SECRET_KEY>"],\n}',
     "stripe", "program", ["arg:2"]),
    ("Windsurf serverUrl", '{"mcpServers": {"wiki": {"serverUrl": "https://mcp.example.org/mcp"}}}', "wiki", "web", []),
    ("Zed context_servers", '{"context_servers": {"ctx7": {"command": {"path": "npx", "args": ["-y", "@upstash/context7-mcp"]}}}}', "ctx7", "program", []),
    ("Codex TOML", '[mcp_servers.context7]\ncommand = "npx"\nargs = ["-y", "@upstash/context7-mcp"]\n', "context7", "program", []),
    ("Continue YAML", "name: x\nmcpServers:\n  - name: SQLite\n    command: npx\n    args: [\"-y\", \"mcp-sqlite\", \"/path/to/db.sqlite\"]\n", "SQLite", "program", ["arg:2"]),
    ("claude mcp add (http)", "claude mcp add --transport http notion https://mcp.notion.com/mcp", "notion", "web", []),
    ("claude mcp add (program, -e)", "claude mcp add airtable --env AIRTABLE_API_KEY=YOUR_KEY -- npx -y airtable-mcp-server", "airtable", "program", ["env:AIRTABLE_API_KEY"]),
    ("gemini mcp add with a header", 'gemini mcp add -t http -H "Authorization: Bearer <token>" github https://api.githubcopilot.com/mcp/', "github", "web", ["header:Authorization"]),
    ("code --add-mcp", "code --add-mcp '{\"name\":\"fetch\",\"command\":\"uvx\",\"args\":[\"mcp-server-fetch\"]}'", "fetch", "program", []),
    ("Cursor install link", f"cursor://anysphere.cursor-deeplink/mcp/install?name=context7&config={cursor_cfg}", "context7", "program", []),
    ("VS Code install link", "vscode:mcp/install?" + urllib.parse.quote(json.dumps({"name": "gh", "type": "http", "url": "https://api.githubcopilot.com/mcp/"})), "gh", "web", []),
    ("just the address", "https://mcp.deepwiki.com/mcp", "deepwiki", "web", []),
    ("just the npx command", "npx -y @modelcontextprotocol/server-everything", "everything", "program", []),
    ("docker with a passed-through key", "docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server", "github", "program", ["env:GITHUB_PERSONAL_ACCESS_TOKEN"]),
    ("a command inside a sentence", "hey can you add this: claude mcp add --transport http wiki2 https://mcp.deepwiki.com/mcp please", "wiki2", "web", []),
    ("an address inside a sentence", "connect this mcp for me https://mcp.deepwiki.com/mcp thanks", "deepwiki", "web", []),
]
for label, text, name, kind, needs in CASES:
    r = mcp_import.parse(text)
    s = (r.get("servers") or [{}])[0]
    got = (s.get("name"), s.get("kind"), [n["id"] for n in s.get("needs", [])])
    check(f"{label} → {name}", got == (name, kind, needs), got)
r = mcp_import.parse('{"mcpServers": {"linear": {"command": "npx", "args": ["-y", "mcp-remote", "https://mcp.linear.app/sse"]}}}')
s = r["servers"][0]
check("mcp-remote becomes a direct connection", s["kind"] == "web" and s["spec"]["url"] == "https://mcp.linear.app/sse", s["spec"])
r = mcp_import.parse('{"mcpServers": {"fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/username/Desktop"]}}}')
n = r["servers"][0]["needs"]
check("an example folder becomes a question", n and n[0]["label"] == "Folder" and not n[0]["secret"], n)
check("a header placeholder is a secret question", mcp_import.parse(CASES[9][1])["servers"][0]["needs"][0]["secret"])
check("not an MCP server: says what to paste", mcp_import.parse("hello there")["error"])
check("chat: a config or 'add this mcp …' is noticed", all(mcp_import.looks_like(t) for t in
      (CASES[0][1], CASES[7][1], CASES[11][1], "please connect this mcp https://x.example/mcp")))
check("chat: ordinary messages aren't", not any(mcp_import.looks_like(t) for t in
      ("what's the weather", "what is MCP? see https://modelcontextprotocol.io", "install jq")))

# ------------------------------------------------------------------ P asking the address
print("P asking an address how to connect")
p = asyncio.run(mcp_import.probe(OPEN + "/mcp"))
check("open: it answers", p["ok"] and p["auth"] is None, p)
p = asyncio.run(mcp_import.probe(OPEN + "/sse"))
check("a retired …/sse: the …/mcp next to it is used", p["ok"] and p.get("url") == OPEN + "/mcp", p)
p = asyncio.run(mcp_import.probe(KEY + "/mcp"))
check("wants a key: says so", not p["ok"] and p["auth"] == "key", p)
p = asyncio.run(mcp_import.probe(KEY + "/mcp", {"X-Api-Key": "k123"}))
check("…and answers with the key", p["ok"], p)
p = asyncio.run(mcp_import.probe(OAUTH + "/mcp"))
check("one-click sign-in (OAuth with self-registration)", p["ok"] and p["auth"] == "oauth", p)
res = asyncio.run(mcp_import.check(KEY + "/mcp"))
check("check: a key server gets a box for the key", [x["id"] for x in res["servers"][0]["needs"]] == ["header:Authorization"], res)


# ------------------------------------------------------------------ A adding and connecting
async def adding():
    out = {}
    out["open"] = await mcp_import.add(OPEN + "/sse")
    keyed = json.dumps({"mcpServers": {"keyed": {"command": sys.executable, "args": [str(STDIO)], "env": {"DEMO_API_KEY": "<YOUR_DEMO_KEY>"}}}})
    out["no_key"] = await mcp_import.add(keyed)
    out["keyed"] = await mcp_import.add(keyed, {"keyed": {"env:DEMO_API_KEY": "secret-123"}})
    out["whoami"] = await mcp_client.mcp_hub.call("mcp__keyed__whoami", {}, approved=True)
    out["shout"] = await mcp_client.mcp_hub.call("mcp__127__shout", {"text": "hi"}, approved=True)
    out["header"] = await mcp_import.add(json.dumps({"mcpServers": {"kserver": {"url": KEY + "/mcp", "headers": {"X-Api-Key": "<YOUR_KEY>"}}}}),
                                         {"kserver": {"header:X-Api-Key": "k123"}})
    out["missing"] = await mcp_import.add("npx-definitely-missing-xyz -y some-server" if False else
                                          json.dumps({"mcpServers": {"ghost": {"command": "definitely-missing-runtime-xyz", "args": ["serve"]}}}))
    events = []
    async for ev in mcp_import.stream_chat(f"please add this mcp server {OPEN}/mcp"):
        events.append(ev)
    out["chat"] = "".join(e.get("content", "") for e in events if e["type"] == "content")
    for c in list(mcp_client.mcp_hub.conns.values()):
        await c.stop()
    return out


print("A adding and connecting")
out = asyncio.run(adding())
r = out["open"]["results"][0]
check("a retired …/sse address connects through …/mcp", r["success"] and r["tools"] == 1 and r["tool_names"] == ["shout"], out["open"])
check("…and its tool works", out["shout"].get("content") == "HI!", out["shout"])
r = out["no_key"]["results"][0]
check("without the key: asks for it, adds nothing", not r["success"] and "DEMO_API_KEY" in r["error"], r)
r = out["keyed"]["results"][0]
check("with the key: a program server connects", r["success"] and r["tools"] == 1, r)
check("…and the server got the key", out["whoami"].get("content") == "key=secret-123", out["whoami"])
cfg_text = (Path(TMP) / "data" / "mcp_servers.json").read_text()
check("the key is in the Keychain, not the file", "secret-123" not in cfg_text and
      keychain.get_password(mcp_client.KEYCHAIN, "keyed:DEMO_API_KEY") == "secret-123", cfg_text[:300])
r = out["header"]["results"][0]
check("a server that wants a header key connects with it", r["success"], r)
check("…and the header value isn't in the file", "k123" not in (Path(TMP) / "data" / "mcp_servers.json").read_text())
r = out["missing"]["results"][0]
check("a program that isn't here: says what's missing", not r["success"] and r.get("runtime") and "definitely-missing-runtime-xyz" in r["error"], r)
check("imported servers start as 'read-only runs, the rest asks'", json.loads(cfg_text)["servers"]["keyed"]["trust"] == "read_only")
check("pasted into the chat: connected, with its tools", "connected" in out["chat"] and "shout" in out["chat"], out["chat"])

for n in ("keyed:DEMO_API_KEY", "kserver:header:X-Api-Key"):
    try:
        keychain.delete_password(mcp_client.KEYCHAIN, n)
    except Exception:
        pass

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print("FAILED: " + "; ".join(failures))
# The stand-in servers run in daemon threads: stop them and leave without the interpreter's shutdown, where a
# server still logging crashed the exit ("Fatal Python error: _enter_buffered_busy") after every check had passed.
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
for s in servers:
    s.should_exit = True
time.sleep(1.0)
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
