"""
A cloud reasoning model (like the ones on Fireworks) must never leave a chat with thoughts and no answer.

A stand-in "Fireworks" server on this Mac (no key, no cost) behaves like the failure: with a short budget it only
thinks (reasoning_content) until max_tokens runs out; one model also rejects reasoning_effort. Checks that Xod sends
reasoning_effort=low with room to spare, drops the parameter for a model that rejects it, and, when a reply is
still all thinking, asks once more and gets the answer, streaming and not.
  .venv/bin/python tests/smoke/run_cloud_thinking_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-cloudthink-")
os.environ["XOD_DATASET_DB"] = str(Path(os.environ["XOD_DATA_DIR"]) / "dataset.db")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"

results = []
SEEN = []
NEEDS = {"budget": 3000}          # below this many max_tokens the model only thinks


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        if body["model"] == "picky" and "reasoning_effort" in body:
            msg = json.dumps({"error": {"message": "reasoning_effort is not supported for this model"}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        thinks_only = body.get("max_tokens", 0) < NEEDS["budget"]
        thought = "The user asks about X. I should be careful and cite [1]. " * 3
        answer = "Here is the answer [1]."
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [{"choices": [{"delta": {"reasoning_content": thought}}]}]
            chunks += [{"choices": [{"delta": {}, "finish_reason": "length"}]}] if thinks_only else \
                      [{"choices": [{"delta": {"content": answer}, "finish_reason": "stop"}]}]
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        msg = {"role": "assistant", "content": None if thinks_only else answer, "reasoning_content": thought}
        out = json.dumps({"choices": [{"message": msg, "finish_reason": "length" if thinks_only else "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


async def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    from app.services.llm import llm_service
    ep = {"provider": "fireworks", "base_url": base, "model": "glm-like", "api_key": "test"}

    async def stream(ep, max_tokens=600):
        out, thoughts, errs = "", "", []
        async for ch in llm_service.stream_chat_completion(messages=[{"role": "user", "content": "who is X?"}], max_tokens=max_tokens,
                                                           think=False, endpoint_override=ep):
            if ch.get("type") == "content":
                out += ch["content"]
            elif ch.get("type") == "reasoning":
                thoughts += ch["content"]
            elif ch.get("type") == "error":
                errs.append(ch["content"])
        return out, thoughts, errs

    SEEN.clear()
    out, thoughts, errs = await stream(ep)
    first = SEEN[0]
    check("a fast answer asks Fireworks to keep thinking low", first.get("reasoning_effort") == "low", str(first.get("reasoning_effort")))
    check("…with room for its thinking on top of the answer", first["max_tokens"] >= 600 + 2000, str(first["max_tokens"]))
    check("a reply that was all thinking still ends with the answer (streaming)", "Here is the answer" in out and not errs, f"{out!r} {errs}")
    check("…by asking once more, not looping", len(SEEN) == 2, str(len(SEEN)))

    SEEN.clear()
    NEEDS["budget"] = 0
    out, _, errs = await stream({**ep, "model": "picky"})
    check("a model that rejects reasoning_effort gets the request again without it", out and not errs and "reasoning_effort" not in SEEN[-1],
          f"{out!r} {errs} {[('reasoning_effort' in s) for s in SEEN]}")
    SEEN.clear()
    await stream({**ep, "model": "picky"})
    check("…and isn't sent it again", len(SEEN) == 1 and "reasoning_effort" not in SEEN[0])

    NEEDS["budget"] = 3000
    SEEN.clear()
    msg = await llm_service.chat_completion(messages=[{"role": "user", "content": "who is X?"}], max_tokens=600, think=False, endpoint_override=ep)
    check("non-streaming: all thinking means one more try with room, and the answer (not the thoughts) comes back",
          msg.get("content") == "Here is the answer [1]." and len(SEEN) == 2, f"{msg.get('content')!r} {len(SEEN)}")

    SEEN.clear()
    await stream({"provider": "lm_studio", "base_url": base, "model": "gemma", "api_key": "x"}, max_tokens=5000)
    check("local models still switch thinking off entirely, budget unchanged",
          SEEN[0].get("reasoning_effort") == "none" and SEEN[0]["max_tokens"] == 5000, str(SEEN[0]))

    srv.shutdown()
    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
