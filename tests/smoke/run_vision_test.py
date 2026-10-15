"""
Eyes for models that can't see (services/vision.py), against a stand-in Ollama: knowing the chat model can't see,
finding the vision model, describing an image (and freeing the GPU when it doesn't fit), an attached image turned into
words before the turn is planned, and an honest answer when there's no vision model. No real model needed.

  python tests/smoke/run_vision_test.py
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="xod-vision-")
GB = 1024 ** 3


class State:
    models = {"texty:7b": {"size": 5 * GB, "caps": ["completion", "tools"]},
              "eyes:4b": {"size": 3 * GB, "caps": ["completion", "vision"]},
              "bigeyes:8b": {"size": 6 * GB, "caps": ["completion", "vision"]}}
    fail_next_vision = False
    vision_calls, unloads, chat_texts = [], [], []


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
        if self.path == "/api/tags":
            return self._json(200, {"models": [{"name": n, "model": n, "size": m["size"]} for n, m in State.models.items()]})
        if self.path == "/api/ps":
            return self._json(200, {"models": [{"name": "texty:7b", "model": "texty:7b"}]})
        if self.path == "/api/version":
            return self._json(200, {"version": "stand-in"})
        self._json(404, {})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/api/show":
            m = State.models.get(req.get("model"))
            return self._json(200, {"capabilities": m["caps"], "model_info": {}}) if m else self._json(404, {"error": "not found"})
        if self.path == "/api/generate":
            State.unloads.append(req.get("model"))
            return self._json(200, {"done": True})
        if self.path == "/api/chat":
            State.vision_calls.append(req)
            if State.fail_next_vision:
                State.fail_next_vision = False
                return self._json(500, {"error": "model requires more system memory than is available"})
            return self._json(200, {"message": {"role": "assistant", "content":
                                    'An invoice from "Northwind Plumbing". Total due: $325.50, by Nov 3, 2026.'}})
        if self.path == "/v1/chat/completions":
            State.chat_texts.append(json.dumps(req.get("messages")))
            answer = "It's from Northwind Plumbing: $325.50, due Nov 3, 2026."
            if not req.get("stream"):
                return self._json(200, {"choices": [{"message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': answer}}]})}\n\ndata: [DONE]\n\n".encode())
            return
        self._json(404, {})


srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}"
os.environ.update(XOD_DATA_DIR=TMP, LLM_PROVIDER="ollama", LLM_BASE_URL=f"{URL}/v1", LLM_MODEL="texty:7b", XOD_AUTOCONNECT="0",
                  XOD_FAST_LANE="0", PYTHON_KEYRING_BACKEND="tests.support.filekeyring.FileKeyring",
                  XOD_TEST_KEYRING_FILE=os.path.join(TMP, "keys.json"), XOD_SECRETS_SERVICE="Xod test keys")

from app.services import vision      # noqa: E402

results = []
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 64).decode()


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


async def turn(text):
    from app.agent.researcher import AgenticResearcher
    out, statuses = "", []
    msgs = [{"role": "user", "content": [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": PNG}}]}]
    async for ev in AgenticResearcher().run_research_stream(msgs):
        if ev.get("type") == "content":
            out += ev.get("content", "")
        elif ev.get("type") == "status":
            statuses.append(ev.get("content"))
    return out, statuses


async def main():
    check("Xod can tell the chat model can't see", await vision.main_sees() is False and await vision.needed())
    check("…and finds the smallest vision model in Ollama", await vision.model() == "eyes:4b", str(await vision.model()))

    d = await vision.describe(PNG, "who sent it")
    sent = State.vision_calls[-1]["messages"][0]
    check("an image is described by the vision model (sent as an image, with what matters)", d and "Northwind" in d
          and sent["images"] == [PNG.split(",", 1)[1]] and "who sent it" in sent["content"], str(d))

    State.fail_next_vision = True
    n = len(State.vision_calls)
    d2 = await vision.describe(PNG)
    check("no room on the GPU: the other models are let go and it looks again", d2 and len(State.vision_calls) == n + 2
          and "texty:7b" in State.unloads, f"{d2} {State.unloads}")

    out, statuses = await turn("Who is this invoice from, and what's the total?")
    check("an attached image becomes words before the turn is planned", "Looking at the image…" in statuses
          and any("Northwind Plumbing" in t and "described by Xod's vision model" in t for t in State.chat_texts), str(statuses))
    check("…and no image is sent to the chat model that can't see", not any("image_url" in t for t in State.chat_texts))
    check("…the answer comes from it", "Northwind" in out, out[:200])

    for name in ("eyes:4b", "bigeyes:8b"):
        State.models.pop(name)
    vision.forget()
    out, _ = await turn("What's in this picture?")
    check("no vision model: it says so plainly (no guessing, no browser)", "can't look at images" in out and "vision model" in out, out[:200])

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    srv.shutdown()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
