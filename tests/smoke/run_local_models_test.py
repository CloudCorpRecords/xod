"""
Local models through Ollama: the edge cases. A brand-new data folder and a stand-in Ollama that behaves like the
real one where it matters (it cuts prompts longer than the model's context from the front, refuses tools for a
model that can't call them, puts a thinking model's thoughts in "reasoning", answers 404 for a missing model), so
no GPU, Ollama or download is needed. Checks:

  - Xod gives each model enough context (a secret at the start of a long prompt survives), making the copy once
  - the header and the model picker see Ollama on whatever port it's on, and LM Studio is looked for on its own
  - a thinking model's answer isn't its thoughts; a model that can't call tools still answers when given tools
  - Xod's long-context copy deleted outside Xod is made again; one left over after its model was deleted is removed
  - the chosen model missing, Ollama not running, Ollama dying mid-answer: a clear message, and Xod keeps working
  - Ollama coming back: chats work again without restarting Xod
  - this computer, whatever it is: GPUs from NVIDIA, AMD, Intel and Apple read right (from their tools' output)
  - Xod plugging itself in: a new install connects to the best model it finds (Ollama, or another local server
    like llama.cpp), replaces a model it chose itself when that one goes, and never replaces the person's choice

  python tests/smoke/run_local_models_test.py
  python tests/smoke/run_local_models_test.py --real     also against the real Ollama on this computer: downloads two
                                                         small models (qwen3:0.6b, smollm2:135m, ~0.8 GB) if they're
                                                         missing and removes the ones it downloaded at the end
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


# ---------------------------------------------------------------- the stand-in Ollama

GB = 1024 ** 3
INFO = {"general.architecture": "llama", "llama.context_length": 32768, "llama.block_count": 32,
        "llama.attention.head_count": 32, "llama.attention.head_count_kv": 8, "llama.embedding_length": 4096}


class Ollama:
    """State shared by the stand-in's requests; survives the server being stopped and started again."""
    def __init__(self):
        self.models = {   # name -> what /api/show says
            "stand-in:7b": {"size": int(4.5 * GB), "caps": ["completion", "tools"]},
            "thinker:4b": {"size": int(2.5 * GB), "caps": ["completion", "tools", "thinking"]},
            "notools:1b": {"size": int(0.7 * GB), "caps": ["completion"]},
        }
        self.variants = {}          # name -> {"from", "num_ctx"}
        self.creates = []           # every /api/create, in order
        self.chats = []             # (model, had tools) per chat request
        self.die_mid_stream = False
        self.lock = threading.Lock()

    def exists(self, name):
        return name in self.models or name in self.variants

    def num_ctx(self, name):
        return (self.variants.get(name) or {}).get("num_ctx", 4096)    # Ollama's default

    def base(self, name):
        return (self.variants.get(name) or {}).get("from", name)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    @property
    def o(self) -> Ollama:
        return self.server.ollama

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")

    def do_GET(self):
        if self.path == "/api/version":
            return self._json(200, {"version": "0.0-stand-in"})
        if self.path == "/api/tags":
            with self.o.lock:
                tags = [{"name": n, "model": n, "size": m["size"]} for n, m in self.o.models.items()]
                tags += [{"name": n, "model": n, "size": self.o.models.get(v["from"], {}).get("size", GB)} for n, v in self.o.variants.items()]
            return self._json(200, {"models": tags})
        if self.path == "/api/ps":
            return self._json(200, {"models": []})
        self._json(404, {"error": "not found"})

    def do_DELETE(self):
        name = self._body().get("model")
        with self.o.lock:
            if self.o.models.pop(name, None) or self.o.variants.pop(name, None):
                return self._json(200, {})
        self._json(404, {"error": f"model '{name}' not found"})

    def do_POST(self):
        req = self._body()
        if self.path == "/api/show":
            name = req.get("model")
            with self.o.lock:
                if not self.o.exists(name):
                    return self._json(404, {"error": f"model '{name}' not found"})
                base = self.o.base(name)
                caps = self.o.models.get(base, {"caps": ["completion"]})["caps"]
                params = f"num_ctx                        {self.o.variants[name]['num_ctx']}" if name in self.o.variants else ""
            return self._json(200, {"model_info": INFO, "capabilities": caps, "parameters": params,
                                    "details": {"parent_model": base if name in self.o.variants else ""}})
        if self.path == "/api/create":
            with self.o.lock:
                self.o.creates.append(req)
                if req.get("from") not in self.o.models:
                    return self._json(404, {"error": f"model '{req.get('from')}' not found"})
                self.o.variants[req["model"]] = {"from": req["from"], "num_ctx": (req.get("parameters") or {}).get("num_ctx", 4096)}
            return self._json(200, {"status": "success"})
        if self.path == "/v1/chat/completions":
            return self._chat(req)
        self._json(404, {"error": "not found"})

    def _chat(self, req):
        name = req.get("model")
        with self.o.lock:
            if not self.o.exists(name):
                return self._json(404, {"error": {"message": f"model '{name}' not found", "type": "not_found_error"}})
            caps = self.o.models.get(self.o.base(name), {"caps": []})["caps"]
            self.o.chats.append((name, bool(req.get("tools"))))
            ctx = self.o.num_ctx(name)
        if req.get("tools") and "tools" not in caps:
            return self._json(400, {"error": {"message": f"registry.ollama.ai/library/{name} does not support tools", "type": "invalid_request_error"}})
        # Like Ollama: a prompt longer than the context loses its beginning.
        text = "\n".join(str(m.get("content") or "") for m in req.get("messages", []))
        kept = text[-ctx * 4:]                      # ~4 characters a token
        secret = kept.split("SECRET=", 1)[1].split(".", 1)[0] if "SECRET=" in kept else None
        answer = f"The secret is {secret}." if secret else "Hello from the stand-in Ollama."
        thinks = "thinking" in caps and req.get("reasoning_effort") != "none"
        if not req.get("stream"):
            msg = {"role": "assistant", "content": answer}
            if thinks:
                msg["reasoning"] = "Let me think about this carefully."
            return self._json(200, {"choices": [{"message": msg, "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": min(len(text) // 4, ctx), "completion_tokens": 8}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        def send(delta):
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': delta}]})}\n\n".encode())
            self.wfile.flush()
        if thinks:
            send({"reasoning": "Let me think about this carefully."})
        send({"content": answer[:12]})
        if self.o.die_mid_stream:
            self.connection.shutdown(socket.SHUT_RDWR)   # crashed: the answer just stops
            return
        send({"content": answer[12:]})
        self.wfile.write(b"data: [DONE]\n\n")


class OtherServer(BaseHTTPRequestHandler):
    """Another OpenAI-compatible model server on this computer, like llama.cpp's."""
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"object": "list", "data": [{"id": "gemma-local.gguf", "object": "model", "owned_by": "llamacpp"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        answer = "Hello from the stand-in llama.cpp."
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': answer}}]})}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StandIn:
    """A stand-in server on a fixed port, so it can be stopped ("Ollama quit") and started again."""
    def __init__(self, handler=None):
        self.ollama = Ollama()
        self.handler = handler or Handler
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.server = None
        self.start()

    def start(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), self.handler)
        self.server.daemon_threads = True
        self.server.ollama = self.ollama
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------- talking to Xod

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(method, url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def chat(base, text, timeout=180):
    """One chat turn; returns (everything shown to the person: answer and errors, seconds taken)."""
    t = time.time()
    req = urllib.request.Request(base + "/api/chat", data=json.dumps({"messages": [{"role": "user", "content": text}]}).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    shown = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except ValueError:
                    continue
                if ev.get("type") in ("content", "error"):
                    shown += str(ev.get("content") or "")
    except Exception as e:
        shown += f" [request failed: {e}]"
    return shown, time.time() - t


def choose(base, model):
    return http("POST", base + "/api/setup/choose", {"provider": "ollama", "model": model})


def header(base):
    return (json.loads(http("GET", base + "/api/lmstudio/status")[1]).get("active") or {})


def in_process(py, env, code, timeout=120):
    """Runs Python against the same settings as the server (for the parts a chat can't reach directly)."""
    out = subprocess.run([py, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=timeout)
    return out.stdout + out.stderr


LONG = "SECRET={word}. " + "The quick brown fox jumps over the lazy dog. " * 1100 + "What was SECRET? Reply with the word only."


def stand_in_tests(py, base, env, s: StandIn):
    o = s.ollama
    st, body = http("GET", base + "/api/ollama", timeout=60)
    ov = json.loads(body) if st == 200 else {}
    check("the model picker finds Ollama on its port and lists its models", ov.get("running") and str(s.port) in ov.get("root", "")
          and {m["name"] for m in ov.get("models", [])} == set(o.models), body[:300])
    tools = {m["name"]: m["tools"] for m in ov.get("models", [])}
    check("…and says which ones can call tools", tools.get("stand-in:7b") is True and tools.get("notools:1b") is False, str(tools))
    st, body = http("GET", base + "/api/lmstudio/status")
    s_json = json.loads(body)
    check("the header shows Ollama as ready", (s_json.get("active") or {}).get("ready") is True, body[:300])
    check("…and looks for LM Studio on LM Studio's port, not Ollama's", str(s.port) not in s_json.get("server_url", ""), s_json.get("server_url"))

    # Context: a secret at the very start of a ~11,000-token prompt survives.
    shown, secs = chat(base, LONG.format(word="pineapple"))
    check("a long prompt reaches the model whole (its first line is still there)", "pineapple" in shown, shown[:300])
    made = [c for c in o.creates if c.get("from") == "stand-in:7b"]
    ctx = (made[0].get("parameters") or {}).get("num_ctx", 0) if made else 0
    check("…through a long-context copy made once", len(made) == 1 and ctx >= 8192, f"{len(made)} copies, num_ctx {ctx}")
    used = {m for m, _ in o.chats}
    check("…and every request went to that copy, not the 4k-context original", used and all(m.startswith("xod/") for m in used), str(used))

    # A thinking model: the answer is the answer, not its thoughts.
    choose(base, "thinker:4b")
    shown, _ = chat(base, "Say hello.")
    check("a thinking model's answer comes back without its thoughts in it", "stand-in Ollama" in shown and "think about this" not in shown, shown[:300])
    check("…its copy was made once even with several requests in the turn", len([c for c in o.creates if c.get("from") == "thinker:4b"]) == 1,
          str([c["model"] for c in o.creates]))

    # A model that can't call tools, handed tools (the operator and swarm do that): it answers in text instead of failing.
    out = in_process(py, env, "import asyncio; from app.services.llm import llm_service as l; "
                     "t=[{'type':'function','function':{'name':'web_search','description':'search','parameters':{'type':'object','properties':{}}}}]; "
                     "m=asyncio.run(l.chat_completion([{'role':'user','content':'hi'}], tools=t, endpoint_override={'provider':'ollama','base_url':'%s','model':'notools:1b','api_key':'ollama'})); "
                     "print('ANSWER', m.get('content'))" % f"http://127.0.0.1:{s.port}/v1")
    check("a model that can't call tools still answers when given tools", "ANSWER Hello from the stand-in" in out, out[-400:])

    # Xod's copy deleted outside Xod (`ollama rm xod/...`): made again, the chat works.
    choose(base, "stand-in:7b")
    copy = next(n for n in o.variants if o.variants[n]["from"] == "stand-in:7b")
    http("DELETE", f"http://127.0.0.1:{s.port}/api/delete", {"model": copy})
    shown, _ = chat(base, LONG.format(word="walrus"))
    check("Xod's long-context copy deleted outside Xod is made again", "walrus" in shown and copy in o.variants, shown[:300])

    # A model deleted outside Xod leaves its copy behind holding the disk space: the model picker removes it.
    thinker_copy = next(n for n in o.variants if o.variants[n]["from"] == "thinker:4b")
    http("DELETE", f"http://127.0.0.1:{s.port}/api/delete", {"model": "thinker:4b"})
    http("GET", base + "/api/ollama", timeout=60)
    check("a copy left over after its model was deleted is removed (frees the space)", thinker_copy not in o.variants, str(list(o.variants)))

    # The chosen model isn't installed.
    choose(base, "ghost:3b")
    h = header(base)
    check("the header says when the chosen model isn't installed", h.get("ready") is False and "isn't installed" in (h.get("problem") or ""), str(h))
    shown, _ = chat(base, "Say hello.")
    check("…and a chat says so plainly, with what to do", "isn't installed in Ollama" in shown and "Setup" in shown, shown[:300])
    choose(base, "stand-in:7b")

    # Ollama quits.
    s.stop()
    h = header(base)
    check("the header says when Ollama isn't running", h.get("ready") is False and "isn't running" in (h.get("problem") or ""), str(h))
    shown, secs = chat(base, "Say hello.")
    check("a chat while Ollama is off says so plainly and quickly", "Ollama isn't answering" in shown and secs < 60, f"{secs:.0f}s: {shown[:300]}")
    st, _ = http("GET", base + "/")
    check("…and Xod itself keeps running", st == 200)

    # Ollama comes back.
    s.start()
    shown, _ = chat(base, "Say hello.")
    check("when Ollama is back, chats work again without restarting Xod", "stand-in Ollama" in shown, shown[:300])

    # Ollama dies in the middle of an answer.
    o.die_mid_stream = True
    shown, secs = chat(base, "Say hello.")
    o.die_mid_stream = False
    check("Ollama dying mid-answer ends the turn with a clear message", "stopped in the middle" in shown and secs < 90, f"{secs:.0f}s: {shown[:300]}")
    shown, _ = chat(base, "Say hello.")
    check("…and the next chat works", "stand-in Ollama" in shown, shown[:300])

    # How much context for which computer (pure function).
    out = in_process(py, env, "from app.services.ollama_context import pick_context as p; "
                     "q={'general.architecture':'qwen2','qwen2.context_length':32768,'qwen2.block_count':48,'qwen2.attention.head_count':40,'qwen2.attention.head_count_kv':8,'qwen2.embedding_length':5120}; "
                     "old={'general.architecture':'llama','llama.context_length':4096,'llama.block_count':32,'llama.attention.head_count':32,'llama.embedding_length':4096}; "
                     "print('CTX', p(q, 8.4, 14.5), p(q, 8.4, 10.5), p(q, 8.4, 6.5), p(old, 3.8, 14.5), p({}, 4, 14.5))")
    check("context fits the GPU: 32k on 16 GB, 8k on 12 GB, 8k when it spills; never past what the model was trained on",
          "CTX 32768 8192 8192 4096 8192" in out, out[-300:])


# ---------------------------------------------------------------- this computer, whatever it is

MACHINES = r'''
import json
from app.services import machine as m
GB = 1024 ** 3
out = {}
# NVIDIA, from nvidia-smi
out["nvidia"] = m.parse_nvidia_smi("NVIDIA GeForce RTX 4070, 12282, 900, 560.94\n")
# Windows: an AMD card, Intel's integrated graphics and an Intel Arc card (driver registry entries)
reg = m.parse_windows_registry([
    {"DriverDesc": "AMD Radeon RX 7800 XT", "MatchingDeviceId": "PCI\\VEN_1002&DEV_747E", "HardwareInformation.qwMemorySize": 16 * GB},
    {"DriverDesc": "Intel(R) UHD Graphics 770", "MatchingDeviceId": "pci\\ven_8086&dev_4680", "HardwareInformation.qwMemorySize": 128 * 1024 ** 2},
    {"DriverDesc": "Intel(R) Arc(TM) A770 Graphics", "MatchingDeviceId": "pci\\ven_8086&dev_56a0", "HardwareInformation.qwMemorySize": (16 * GB).to_bytes(8, "little")},
    {"MatchingDeviceId": "no description: skipped"}])
out["windows"] = [m.classify(g) for g in reg]
# Linux: an AMD card through the kernel's files
out["linux"] = [m.classify(g) for g in m.parse_linux_drm({"card0": {"vendor": "0x1002", "mem_info_vram_total": str(8 * GB), "mem_info_vram_used": str(GB), "product_name": "Radeon RX 6600"}})]
out["counters"] = m.parse_gpu_counters("\n 1073741824 \n 536870912\n")
amd, intel_igpu, arc = out["windows"]
apple = m.classify({"name": "Apple M3 Pro", "vendor": "Apple", "memory_gb": 36})
out["mem_amd"] = m.ai_memory([amd], 32, 20)
out["mem_intel"] = m.ai_memory([intel_igpu, arc], 32, 20)
out["mem_apple"] = m.ai_memory([apple], 36, 20)
out["mem_none"] = m.ai_memory([], 16, 10)
out["summary_apple"] = m.summary([apple], out["mem_apple"], 36)
out["this"] = m.profile(fresh=True)
print("MACHINES" + json.dumps(out))
'''


def machine_tests(py, env):
    out = in_process(py, env, MACHINES)
    try:
        r = json.loads(out.split("MACHINES", 1)[1].strip().splitlines()[0])
    except Exception:
        check("this computer's hardware can be read", False, out[-600:])
        return
    nv = r["nvidia"][0]
    check("NVIDIA: name, memory and what's in use, from nvidia-smi", nv["name"] == "NVIDIA GeForce RTX 4070" and nv["memory_gb"] == 12.0 and nv["used_gb"] == 0.9, str(nv))
    amd, igpu, arc = r["windows"]
    check("Windows: an AMD card's real memory (past WMI's 4 GB limit), dedicated, usable through ROCm",
          amd["vendor"] == "AMD" and amd["memory_gb"] == 16.0 and amd["dedicated"] and amd["accel"] == "rocm", str(amd))
    check("…Intel's integrated graphics share the computer's memory", igpu["vendor"] == "Intel" and not igpu["dedicated"], str(igpu))
    check("…an Intel Arc card is dedicated but Ollama can't use it", arc["dedicated"] and arc["accel"] == "none" and arc["memory_gb"] == 16.0, str(arc))
    lin = r["linux"][0]
    check("Linux: an AMD card from the kernel's files", lin["name"] == "Radeon RX 6600" and lin["memory_gb"] == 8.0 and lin["used_gb"] == 1.0, str(lin))
    check("Windows GPU memory in use, any vendor (performance counters)", r["counters"] == 1.5, str(r["counters"]))
    check("models get the AMD GPU's memory less headroom", r["mem_amd"]["runs_on"] == "gpu" and r["mem_amd"]["ai_memory_gb"] == 14.5, str(r["mem_amd"]))
    check("…with only Intel graphics they run on the processor, with half the memory",
          r["mem_intel"]["runs_on"] == "cpu" and r["mem_intel"]["ai_memory_gb"] == 16.0, str(r["mem_intel"]))
    check("…on a Mac they share its memory (about two-thirds of it)", r["mem_apple"]["runs_on"] == "unified" and 21 < r["mem_apple"]["ai_memory_gb"] < 24
          and "36 GB shared" in r["summary_apple"], f"{r['mem_apple']} {r['summary_apple']}")
    check("…with no graphics found at all, still a sensible answer", r["mem_none"]["runs_on"] == "cpu" and r["mem_none"]["ai_memory_gb"] == 8.0, str(r["mem_none"]))
    this = r["this"]
    check(f"this computer reads too: {this.get('summary')}", this.get("ai_memory_gb", 0) > 0 and this.get("cpu") and this.get("ram_gb", 0) > 0, str(this)[:300])


# ---------------------------------------------------------------- Xod plugging itself in

def autoconnect_tests(py):
    ollama, other = StandIn(), StandIn(OtherServer)
    xod = Xod(py, OLLAMA_HOST=f"127.0.0.1:{ollama.port}", XOD_LOCAL_SERVERS=f"http://127.0.0.1:{other.port}")
    o = ollama.ollama
    try:
        st, body = http("POST", xod.base + "/api/autoconnect/check", timeout=60)
        s = json.loads(body) if st == 200 else {}
        names = {x["name"] for x in s.get("servers", [])}
        check("a new install finds the model servers on this computer (Ollama and llama.cpp)", {"Ollama", "llama.cpp"} <= names, body[:400])
        cur = s.get("current", {})
        check("…and connects itself to the best model (one that calls tools and fits)",
              cur.get("provider") == "ollama" and cur.get("model") == "stand-in:7b" and cur.get("chosen_by") == "auto", str(cur))
        check("…saying so", any(e["kind"] == "connected" and "stand-in:7b" in e["message"] for e in s.get("events", [])), str(s.get("events")))
        shown, _ = chat(xod.base, "Say hello.")
        check("…and chats work straight away", "stand-in Ollama" in shown, shown[:300])

        # The model Xod chose goes away: it picks the next best one by itself.
        http("DELETE", f"http://127.0.0.1:{ollama.port}/api/delete", {"model": "stand-in:7b"})
        s = json.loads(http("POST", xod.base + "/api/autoconnect/check", timeout=60)[1])
        check("when the model Xod chose is deleted, it switches to the next best by itself",
              s["current"]["model"] == "thinker:4b" and s["current"]["chosen_by"] == "auto", str(s["current"]))

        # The person picks the other server's model: theirs from now on.
        st, body = http("POST", xod.base + "/api/setup/choose", {"provider": "local", "model": "gemma-local.gguf", "base_url": f"http://127.0.0.1:{other.port}/v1"})
        shown, _ = chat(xod.base, "Say hello.")
        check("a model on another local server (llama.cpp) can be picked and chats go to it", "stand-in llama.cpp" in shown, f"{body} {shown[:300]}")
        h = header(xod.base)
        check("…and the header shows it ready, by the server's name", h.get("ready") is True and h.get("server") == "llama.cpp", str(h))
        st, body = http("POST", xod.base + "/api/setup/choose", {"provider": "local", "model": "x", "base_url": "http://example.com/v1"})
        check("a 'local' server somewhere else on the internet is refused", '"success":false' in body.replace(" ", ""), body)

        # The person's server stops: Xod says so and what else there is, but doesn't switch.
        other.stop()
        s = json.loads(http("POST", xod.base + "/api/autoconnect/check", timeout=60)[1])
        problem = [e["message"] for e in s["events"] if e["kind"] == "problem"]
        check("when the person's choice stops working, Xod doesn't swap it", s["current"]["provider"] == "local" and s["current"]["chosen_by"] == "you", str(s["current"]))
        check("…it says what's wrong and what else is ready", problem and "isn't running" in problem[-1] and "thinker:4b" in problem[-1], str(problem))
        s = json.loads(http("POST", xod.base + "/api/autoconnect/check", timeout=60)[1])
        check("…once, not on every check", len([e for e in s["events"] if e["kind"] == "problem"]) == len(problem), str(s["events"]))
    finally:
        xod.stop()
        ollama.stop()
        try:
            other.stop()
        except Exception:
            pass


# ---------------------------------------------------------------- the real Ollama (--real)

REAL = "http://localhost:11434"


def real_tests(py, base, env):
    st, body = http("GET", REAL + "/api/tags", timeout=5)
    if st != 200:
        check("the real Ollama is running (needed for --real)", False, "start Ollama and run again")
        return
    have = {m["name"] for m in json.loads(body)["models"]}
    pulled = []
    for m in ("qwen3:0.6b", "smollm2:135m"):
        if m not in have:
            print(f"      (downloading {m} for the test)", flush=True)
            st, _ = http("POST", REAL + "/api/pull", {"model": m, "stream": False}, timeout=1800)
            if st == 200:
                pulled.append(m)
    try:
        choose(base, "qwen3:0.6b")
        shown, secs = chat(base, LONG.format(word="pineapple"), timeout=300)
        check("real: a long prompt reaches the model whole", "pineapple" in shown.lower(), f"{secs:.0f}s: {shown[:300]}")
        shown, _ = chat(base, "What is 17 times 3? Reply with just the number.", timeout=300)
        check("real: a thinking model gives its answer, not its thoughts", "51" in shown and len(shown) < 400, shown[:400])

        out = in_process(py, env, "import asyncio; from app.services.llm import llm_service as l; "
                         "t=[{'type':'function','function':{'name':'web_search','description':'search','parameters':{'type':'object','properties':{}}}}]; "
                         "m=asyncio.run(l.chat_completion([{'role':'user','content':'Say hi.'}], tools=t, max_tokens=30, endpoint_override={'provider':'ollama','base_url':'%s/v1','model':'smollm2:135m','api_key':'ollama'})); "
                         "print('ANSWER', bool(m.get('content')))" % REAL, timeout=300)
        check("real: a model that can't call tools still answers when given tools", "ANSWER True" in out, out[-400:])

        # `ollama rm` of a model leaves Xod's copy holding its files; the model picker removes it.
        copies = [m["name"] for m in json.loads(http("GET", REAL + "/api/tags")[1])["models"] if m["name"].startswith("xod/smollm2-135m")]
        http("DELETE", REAL + "/api/delete", {"model": "smollm2:135m"})
        http("GET", base + "/api/ollama", timeout=60)
        left = [m["name"] for m in json.loads(http("GET", REAL + "/api/tags")[1])["models"] if m["name"].startswith("xod/smollm2-135m")]
        check("real: a copy left over after `ollama rm` is removed", copies and not left, f"before {copies}, after {left}")

        # Downloading from the model picker: start, stop, pick up where it stopped, finish, delete.
        st, body = http("POST", base + "/api/ollama/pull", {"name": "smollm2:135m"})
        time.sleep(1.5)
        http("POST", base + "/api/ollama/pull/cancel", {"name": "smollm2:135m"})
        time.sleep(1)
        stopped = json.loads(http("GET", base + "/api/ollama/pull?name=smollm2:135m")[1])
        http("POST", base + "/api/ollama/pull", {"name": "smollm2:135m"})
        for _ in range(300):
            s = json.loads(http("GET", base + "/api/ollama/pull?name=smollm2:135m")[1])
            if s["status"] != "downloading":
                break
            time.sleep(1)
        check("real: a download can be stopped and picked up again", stopped["status"] in ("cancelled", "done") and s["status"] == "done", f"{stopped} → {s}")
        st, body = http("POST", base + "/api/ollama/delete", {"name": "smollm2:135m"})
        left = [m["name"] for m in json.loads(http("GET", REAL + "/api/tags")[1])["models"] if "smollm2-135m" in m["name"] or m["name"] == "smollm2:135m"]
        check("real: deleting a model removes it and Xod's copy", json.loads(body).get("success") and not left, f"{body} {left}")
        if "smollm2:135m" not in pulled and "smollm2:135m" in have:
            http("POST", REAL + "/api/pull", {"model": "smollm2:135m", "stream": False}, timeout=1800)   # it was yours: put it back
    finally:
        choose(base, "none-chosen:0b")      # so the models it downloaded aren't "in use" and can be deleted
        for m in pulled:
            http("POST", base + "/api/ollama/delete", {"name": m})


class Xod:
    """A Xod server from scratch: its own throwaway data folder, using Ollama at ollama_url with model; with
    neither, it's a new install that hasn't picked a model. Its background checks are off (tests ask for them)."""
    def __init__(self, py, ollama_url=None, model=None, **extra_env):
        self.data = tempfile.mkdtemp(prefix="xod-local-models-")
        kr = Path(self.data) / "test-keyring.json"
        kr.write_text("{}")
        self.env = {k: v for k, v in os.environ.items() if k not in ("OLLAMA_HOST", "LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL")}
        self.env.update({"XOD_DATA_DIR": self.data, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT),
                         "XOD_AUTOCONNECT": "off", "XOD_SECRETS_SERVICE": "Xod local models test keys",
                         "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring", "XOD_TEST_KEYRING_FILE": str(kr), **extra_env})
        if ollama_url:
            self.env.update({"LLM_PROVIDER": "ollama", "LLM_BASE_URL": f"{ollama_url}/v1", "LLM_MODEL": model})
        port = free_port()
        self.base = f"http://127.0.0.1:{port}"
        self.log = open(Path(self.data) / "server.log", "w")
        self.proc = subprocess.Popen([py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                                     cwd=str(ROOT), env=self.env, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(240):
            try:
                if http("GET", self.base + "/", timeout=2)[0] == 200:
                    return
            except Exception:
                pass
            if self.proc.poll() is not None:
                return
            time.sleep(0.5)

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()
        logtext = (Path(self.data) / "server.log").read_text(errors="replace")
        if "Traceback" in logtext:          # show the first one whole
            i = logtext.index("Traceback")
            print("      server log, first error:\n        " + "\n        ".join(logtext[i:i + 3000].splitlines()[:40]))
        # Stopping the server doesn't stop the browser it opened (its profile is in the data folder): end it, then tidy up.
        import psutil
        for p in psutil.process_iter(["cmdline"]):
            try:
                if any(self.data in part for part in (p.info["cmdline"] or [])):
                    p.kill()
            except psutil.Error:
                pass
        time.sleep(1)
        import shutil
        shutil.rmtree(self.data, ignore_errors=True)


def main():
    py = sys.executable
    s = StandIn()
    xod = Xod(py, f"http://127.0.0.1:{s.port}", "stand-in:7b")
    print(f"      (stand-in Ollama on :{s.port}, Xod at {xod.base}, data in {xod.data})", flush=True)
    try:
        stand_in_tests(py, xod.base, xod.env, s)
        machine_tests(py, xod.env)
    finally:
        xod.stop()
        s.stop()
    autoconnect_tests(py)
    if "--real" in sys.argv:
        xod = Xod(py, REAL, "qwen3:0.6b")
        print(f"      (real Ollama, Xod at {xod.base}, data in {xod.data})", flush=True)
        try:
            real_tests(py, xod.base, xod.env)
        finally:
            xod.stop()

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
