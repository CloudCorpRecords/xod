"""
Models, what works, and connecting (services/model_hub.py, model_scores.py, model_sources.py, capabilities.py,
connectors.py), against a stand-in Ollama: every model on one list with what it can do, swapping the model for a job,
scoring a model (one that does everything right, one that can't call tools), the "what works and why not" report and
the agent answering from it, tools left out when they can't work, model servers on your network vs online, and the
connector catalog and routing. No real model needed.

  python tests/smoke/run_models_test.py
"""
import asyncio
import json
import os
import re
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="xod-models-")
GB = 1024 ** 3

MODELS = {"smart:7b": {"size": 5 * GB, "caps": ["completion", "tools"], "ctx": 32768},
          "notools:1b": {"size": 1 * GB, "caps": ["completion"], "ctx": 4096},
          "eyes:4b": {"size": 3 * GB, "caps": ["completion", "vision", "tools"], "ctx": 32768}}


def answer(model, msgs, tools):
    """What a model that gets everything right says (notools:1b can't take tools at all)."""
    last = msgs[-1]["content"]
    text = " ".join(p.get("text", "") for p in last if isinstance(p, dict)) if isinstance(last, list) else str(last)
    names = [t["function"]["name"] for t in tools or []]
    if "weather in Paris" in text and "get_weather" in names:
        return None, [{"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": json.dumps({"city": "Paris"})}}]
    if "tax return" in text and "find_files" in names:
        return None, [{"id": "c2", "type": "function", "function": {"name": "find_files", "arguments": json.dumps({"query": "2023 tax return pdf"})}}]
    for key, reply in (("Say OK", "OK"), ("lighthouses", "Lighthouses " + "shine light far out to sea. " * 30), ("7 times 6", "42"),
                       ("Ana", '{"name": "Ana", "age": 31}'), ("Pens cost", "8"), ("train leaves", "17:40"), ("banana", "yellow"),
                       ("number is written", "47")):
        if key in text:
            return reply, []
    return "Hello from the stand-in.", []


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/version":
            return self._json(200, {"version": "stand-in"})
        if self.path == "/api/tags":
            return self._json(200, {"models": [{"name": n, "model": n, "size": m["size"]} for n, m in MODELS.items()]})
        if self.path == "/api/ps":
            return self._json(200, {"models": []})
        if self.path == "/v1/models":
            return self._json(200, {"object": "list", "data": [{"id": n, "object": "model"} for n in MODELS]})
        self._json(404, {})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/api/show":
            m = MODELS.get(req.get("model"))
            if not m:
                return self._json(404, {"error": "not found"})
            return self._json(200, {"capabilities": m["caps"], "model_info": {"llama.context_length": m["ctx"]}})
        if self.path == "/api/chat":       # the vision model describing a picture
            return self._json(200, {"message": {"role": "assistant", "content": "A picture."}})
        if self.path == "/api/embed":
            return self._json(404, {"error": "no embedding model"})
        if self.path == "/v1/chat/completions":
            model, tools = req.get("model"), req.get("tools")
            if tools and "tools" not in MODELS.get(model, {}).get("caps", []):
                return self._json(400, {"error": {"message": f"{model} does not support tools"}})
            text, calls = answer(model, req.get("messages", []), tools)
            if req.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': text or ''}}]})}\n\ndata: [DONE]\n\n".encode())
                return
            return self._json(200, {"choices": [{"message": {"role": "assistant", "content": text, "tool_calls": calls or None}, "finish_reason": "stop"}],
                                    "usage": {"completion_tokens": len((text or "x").split()) * 2}})
        self._json(404, {})


srv = ThreadingHTTPServer(("0.0.0.0", 0), FakeOllama)     # all addresses: it also stands in for a server on your network
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]
URL = f"http://127.0.0.1:{PORT}"
os.environ.update(XOD_DATA_DIR=TMP, LLM_PROVIDER="ollama", LLM_BASE_URL=f"{URL}/v1", LLM_MODEL="smart:7b", OLLAMA_HOST=URL,
                  XOD_AUTOCONNECT="0", PYTHON_KEYRING_BACKEND="tests.support.filekeyring.FileKeyring",
                  XOD_TEST_KEYRING_FILE=os.path.join(TMP, "keys.json"), XOD_SECRETS_SERVICE="Xod test keys")

from app.config import settings                                                  # noqa: E402
from app.services import capabilities, model_hub, model_scores, model_sources    # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


def item(rep, cid):
    return next(i for i in rep["items"] if i["id"] == cid)


def use(provider, model, base_url=""):
    settings.llm_provider, settings.llm_model = provider, model
    settings.llm_base_url = base_url or f"{URL}/v1"


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


async def main():
    # ---- every model, what it can do, and its job
    allm = await model_hub.all_models()
    rows = {m["model"]: m for m in allm["models"]}
    check("every model on one list, with what it can do", rows["smart:7b"]["tools"] is True and rows["notools:1b"]["tools"] is False
          and rows["eyes:4b"]["vision"] is True and rows["smart:7b"]["context"] == 32768, json.dumps(list(rows.values()))[:300])
    check("…and which one does which job", rows["smart:7b"]["in_use"] == ["chat"] and rows["eyes:4b"]["in_use"] == ["seeing"], str({k: v["in_use"] for k, v in rows.items()}))

    r = model_hub.use_for("chat", "ollama", "notools:1b")
    check("swapping the chat model is one call", r["success"] and settings.llm_model == "notools:1b", str(r))
    use("ollama", "smart:7b")
    model_hub.use_for("seeing", "ollama", "eyes:4b")
    check("…and the seeing model too", (await model_hub.roles())["seeing"]["model"] == "eyes:4b")
    from app.services import setup_wizard
    before = (settings.llm_provider, settings.llm_model)
    r = setup_wizard.choose("local", "x", "http://203.0.113.9:8080/v1")
    check("a 'local' server elsewhere on the internet is refused, and nothing changes", not r["success"]
          and (settings.llm_provider, settings.llm_model) == before, f"{r} {settings.llm_provider} {settings.llm_model}")

    # ---- scores
    good = await model_scores.run("ollama", "smart:7b", sees=False)
    check("a model that gets everything right scores high", good["score"] >= 75 and all(c["ok"] for c in good["checks"].values()),
          json.dumps(good)[:400])
    bad = await model_scores.run("ollama", "notools:1b")
    check("one that can't call tools loses those points, and says why", not bad["checks"]["tools"]["ok"] and "Can't call tools" in bad["checks"]["tools"]["detail"]
          and any("can't call tools" in n for n in bad["notes"]) and bad["score"] < good["score"], json.dumps(bad)[:400])
    seen = await model_scores.run("ollama", "eyes:4b", sees=True)
    check("a model that sees is checked on a picture", (seen.get("seeing") or {}).get("ok") is True, str(seen.get("seeing")))
    check("…scores are kept and shown with the models", (await model_hub.all_models())["models"][0]["score"] is not None)
    from app.services import autoconnect
    ranked = autoconnect.rank([{"provider": "ollama", "name": "Ollama", "base_url": URL, "models": [
        {"id": "smart:7b", "tools": True, "size_gb": 5}, {"id": "eyes:4b", "tools": True, "size_gb": 3}]}], 16)
    check("picking a model by itself, Xod prefers the better score among equals",
          ranked[0]["model"] == ("smart:7b" if good["score"] >= seen["score"] else "eyes:4b"), str([r["model"] for r in ranked]))

    # ---- what works, and why not
    rep = await capabilities.report()
    check("the report: this model calls tools", item(rep, "tools")["status"] == "ok")
    check("…sees through the vision model (limited, and says how)", item(rep, "vision")["status"] == "limited" and "eyes:4b" in item(rep, "vision")["why"])
    use("ollama", "notools:1b")
    rep2 = await capabilities.report()
    t = item(rep2, "tools")
    check("with a model that can't call tools: limited, why, and the fix", t["status"] == "limited" and "wasn't made to call tools" in t["why"]
          and t["fix"]["action"] == "models")
    check("…and short context is flagged", item(rep2, "context")["status"] == "limited" and "4k" in item(rep2, "context")["why"])
    check("…building apps says it needs a model that calls tools", item(rep2, "build")["status"] == "limited")
    MODELS_SAVED = dict(MODELS)
    MODELS.pop("eyes:4b")
    from app.services import vision
    vision.forget()
    rep3 = await capabilities.report()
    check("no vision model: seeing is off, with 'Get a vision model'", item(rep3, "vision")["status"] == "off"
          and item(rep3, "vision")["fix"]["action"] == "get_vision")
    MODELS.update(MODELS_SAVED)
    vision.forget()
    use("ollama", "smart:7b")
    check("the report in words, for the agent", "NOT AVAILABLE" in capabilities.as_text(rep3) and "Seeing" in capabilities.as_text(rep3))

    from app.agent.researcher import AgenticResearcher as R
    check("'what can't you do?' and 'why can't you see images?' are questions about Xod",
          all(R._ABOUT_SELF_RE.search(q) for q in ("What can't you do?", "Why can't you see images?", "what isn't available right now?"))
          and not any(R._ABOUT_SELF_RE.search(q) for q in ("What can you do about my slow laptop?", "which tools are best for woodworking?")))
    out, tools = "", []
    async for ev in R().run_research_stream([{"role": "user", "content": "What isn't available right now?"}]):
        if ev.get("type") == "content":
            out += ev["content"]
        elif ev.get("type") == "tool_result":
            tools.append(ev.get("name"))
    check("…answered from the report checked just now (not from what the model imagines)", "xod_capabilities" in tools and out.strip(), out[:200])

    from app.agent import tools as T
    real = capabilities._browser
    capabilities._browser = lambda: None
    names = [t["function"]["name"] for t in T._select_builtin_tools("open the website example.com and click sign in")]
    capabilities._browser = real
    check("no Chrome or Edge: browser tools are left out instead of failing", not any(n.startswith("browser_") for n in names), str(names[:8]))

    # ---- model servers you add
    check("addresses are sorted into this computer / your network / online", model_sources.where("http://127.0.0.1:1") == "this computer"
          and model_sources.where("http://192.168.1.20:11434") == "your network" and model_sources.where("http://100.101.102.103:8080") == "your network"
          and model_sources.where("http://nas.local:8080") == "your network" and model_sources.where("https://8.8.8.8") == "online")
    r = await model_sources.add("http://8.8.8.8:8080")
    check("an online server without https is refused", not r["success"] and "https" in r["error"])
    ip = lan_ip()
    if ip and model_sources.where(f"http://{ip}") == "your network":
        r = await model_sources.add(f"http://{ip}:{PORT}", "Big PC", key="secret-key-123")
        check("a server on your network is added, with its models; the key goes in the Keychain", r["success"] and "smart:7b" in r["models"]
              and model_sources.key_for(f"http://{ip}:{PORT}/v1") == "secret-key-123" and "secret-key-123" not in Path(TMP, "model_sources.json").read_text(), str(r))
        sid = r["source"]["id"]
        lan = [m for m in (await model_hub.all_models())["models"] if m["where"] == "your network"]
        check("…its models show with the others", any(m["model"] == "smart:7b" for m in lan))
        r = setup_wizard.choose("local", "smart:7b", f"http://{ip}:{PORT}/v1")
        check("…and one can be chosen for chat", r["success"] and settings.llm_base_url == f"http://{ip}:{PORT}/v1", str(r))
        use("ollama", "smart:7b")
        check("…removing it forgets the key", model_sources.remove(sid) and not model_sources.key_for(f"http://{ip}:{PORT}/v1"))
    else:
        print("(no private network address here: skipped adding a server on the network)")

    # ---- connectors: the catalog, what's not available here and why, routing
    from app.services import connectors
    from app.services.mcp_client import mcp_hub
    cat = connectors.catalog()
    mac = next(c for c in cat if c["id"] == "mac")
    check("the catalog is grouped, and lists 30+ apps", len(cat) >= 30 and len({c["group"] for c in cat}) >= 6, str(len(cat)))
    if sys.platform != "darwin":
        r = await connectors.connect("mac", "http://127.0.0.1/oauth/callback")
        check("'This Mac' isn't offered off a Mac, and says why", mac["unavailable"] and "only works on a Mac" in mac["unavailable"] and not r["success"])
    async def no_connect(*a, **k):
        return None
    mcp_hub.connect = no_connect
    await connectors.connect("deepwiki", "http://127.0.0.1/oauth/callback")
    from app.services import mcp_client
    spec = mcp_client.load_config().get("deepwiki") or {}
    check("a no-account connector connects without sign-in, and its tools run without asking", spec.get("url") == "https://mcp.deepwiki.com/mcp"
          and "auth" not in spec and spec.get("trust") == "always", str(spec))
    from app.agent import operator
    fake = [{"type": "function", "function": {"name": "mcp__todoist__add_tasks", "description": "", "parameters": {"type": "object", "properties": {}}}}]
    mcp_hub.tool_definitions = lambda: fake
    routed = operator.route("add buy milk to my todoist")
    check("a request naming a connected app goes to it", routed and routed["mode"] == "apps" and routed["tools"][0]["function"]["name"] == "mcp__todoist__add_tasks")
    mcp_hub.tool_definitions = lambda: []
    check("…and nothing is routed to an app that isn't connected", operator.route("add buy milk to my todoist") is None)

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    srv.shutdown()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
