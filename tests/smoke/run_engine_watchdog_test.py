"""
The built-in engine stops answering (services/local_engine.py, the watchdog in services/llm.py): Xod notices within
seconds, restarts it once, retries, says so in plain words, and fails cleanly if the restart doesn't help. A stand-in
engine plays the part of mlx_lm.server; no model, no network. T0 checks the Apple-silicon-only pieces (built-in
engine, voice, MLX images) refuse plainly on Intel.
  .venv/bin/python tests/smoke/run_engine_watchdog_test.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-engine-"))
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
(TMP / "data").mkdir()

from app.services import llm, local_engine as le  # noqa: E402
from app.services.llm import llm_service  # noqa: E402
from app.services.memory_broker import memory_broker  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


# The stand-in engine. Each start takes the next mode from the plan file:
#   ok       answers everything
#   hang     answers the warm-up, then never answers a chat (but still lists its models, and sits idle)
#   frozen   answers the warm-up, then answers nothing at all
#   midway   answers the warm-up, then streams two words of a chat and goes silent
#   slow     streams a word a second for 6 seconds (longer than the quiet limit, but never quiet that long)
#   busy     works for 6 seconds without sending anything (a long tool call), then answers
#   chatty   writes 200 KB to its log on every request (a full pipe froze the real engine)
STUB = r'''
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
port, plan, count = int(sys.argv[1]), sys.argv[2], sys.argv[3]
try:
    n = int(open(count).read() or 0)
except OSError:
    n = 0
open(count, "w").write(str(n + 1))
modes = open(plan).read().split(",")
mode = modes[min(n, len(modes) - 1)].strip()
chats = [0]

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a):
        if mode == "chatty":
            sys.stderr.write("x" * 200_000 + "\n"); sys.stderr.flush()
    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if mode == "frozen" and chats[0] > 0:
            time.sleep(3600)
        self._json({"data": [{"id": "stub"}]})
    def _chunk(self, delta, finish=None):
        self.wfile.write(("data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]}) + "\n\n").encode())
        self.wfile.flush()
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        chats[0] += 1
        first = chats[0] == 1
        if mode in ("hang", "frozen") and not first:
            time.sleep(3600)
        if mode == "busy" and not first:
            end = time.time() + 6
            while time.time() < end:
                pass
        if not req.get("stream"):
            return self._json({"choices": [{"message": {"role": "assistant", "content": "fine answer"}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        self.wfile.write(b": keepalive 1/1\n\n"); self.wfile.flush()
        words = ["fine ", "answer"]
        if mode == "slow":
            words = ["word "] * 6
        for w in words:
            self._chunk({"content": w})
            if mode == "slow":
                time.sleep(1)
        if mode == "midway" and not first:
            time.sleep(3600)
        self._chunk({}, "stop")
        self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()

ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
'''
STUB_PATH = TMP / "stub_engine.py"
STUB_PATH.write_text(STUB)
PLAN = TMP / "plan.txt"
COUNT = TMP / "count.txt"


async def no_room_needed(*a, **k):
    return {"ok": True}


le._command = lambda repo, port: [sys.executable, str(STUB_PATH), str(port), str(PLAN), str(COUNT)]
le.is_downloaded = lambda repo: True
memory_broker.make_room = no_room_needed
llm.XOD_QUIET = 2.0
engine = le.local_engine
EP = {"provider": "xod", "model": "stub/model", "base_url": engine.base_url, "api_key": "x"}
MSG = [{"role": "user", "content": "hello"}]


async def fresh(plan: str):
    """A newly started stand-in engine following this plan."""
    await engine.stop()
    PLAN.write_text(plan)
    COUNT.write_text("0")
    await engine.ensure_running("stub/model")


async def ask(notes):
    tok = llm.engine_notice.set(notes.append)
    try:
        return await llm_service.chat_completion(messages=MSG, endpoint_override=EP)
    finally:
        llm.engine_notice.reset(tok)


async def stream(fn="stream"):
    out = []
    gen = (llm_service.stream_chat_completion(messages=MSG, endpoint_override=EP) if fn == "stream"
           else llm_service.deliberate_stream(messages=MSG, endpoint_override=EP))
    async for ev in gen:
        out.append(ev)
    return out


async def intel_guards():
    """Apple-silicon-only pieces refuse plainly on Intel (pretend to be an Intel Mac)."""
    import platform as _plat
    from unittest import mock
    check("apple-silicon detection matches this Mac",
          le.is_apple_silicon() == (_plat.system() == "Darwin" and _plat.machine() == "arm64"))
    with mock.patch.object(_plat, "machine", return_value="x86_64"), \
            mock.patch.object(_plat, "system", return_value="Darwin"):
        check("not Apple silicon under the patch", not le.is_apple_silicon())
        r = le.start_download("mlx-community/Qwen2.5-1.5B-Instruct-4bit")
        check("built-in downloads refused on Intel", not r["success"] and "Apple silicon" in r["error"], r.get("error", "")[:80])
        try:
            await le.LocalEngine().ensure_running("mlx-community/Qwen2.5-1.5B-Instruct-4bit")
            check("built-in engine won't start on Intel", False)
        except RuntimeError as e:
            check("built-in engine won't start on Intel", "Apple silicon" in str(e), str(e)[:80])
        from app.services import setup_wizard
        r = setup_wizard.choose("xod", "mlx-community/Qwen2.5-1.5B-Instruct-4bit")
        check("setup won't switch to built-in on Intel", not r["success"] and "Apple silicon" in r["error"],
              r.get("error", "")[:80])
        from app.services import speech_to_text
        r = await speech_to_text.transcribe(b"x" * 32000)
        check("voice says plainly it needs Apple silicon", not r["success"] and "Apple silicon" in r["error"],
              r.get("error", ""))
        from app.services import media_worker
        try:
            media_worker._load_mflux("/nonexistent", {})
            check("MLX images refused on Intel", False)
        except RuntimeError as e:
            check("MLX images refused on Intel", "Apple silicon" in str(e), str(e)[:80])


async def main():
    print("T0 Apple-silicon-only pieces refuse plainly on Intel")
    await intel_guards()
    print("T1 an engine that stops answering is restarted, and the answer still comes")
    await fresh("hang,ok")
    notes, before, t0 = [], engine.restarts, time.time()
    msg = await ask(notes)
    took = time.time() - t0
    check("the answer arrives", msg.get("content") == "fine answer", str(msg))
    check("restarted exactly once", engine.restarts == before + 1, str(engine.restarts - before))
    check("caught in seconds, not minutes", took < 20, f"{took:.1f}s")
    check("the task hears it in plain words",
          notes == [llm.ENGINE_RESTARTING, llm.ENGINE_RESTARTED], str(notes))

    print("T2 an engine that won't answer even after a restart fails cleanly")
    await fresh("hang,hang")
    notes, before, t0 = [], engine.restarts, time.time()
    try:
        await ask(notes)
        err = None
    except llm.EngineUnresponsive as e:
        err = str(e)
    took = time.time() - t0
    check("fails with a plain message", bool(err) and "stopped answering" in err and "restarted it" in err, str(err))
    check("restarts only once", engine.restarts == before + 1, str(engine.restarts - before))
    check("gives up in well under a minute", took < 30, f"{took:.1f}s")

    print("T3 a completely frozen engine (not even listing models) is caught too")
    await fresh("frozen,ok")
    before = engine.restarts
    msg = await ask([])
    check("restarted and answered", msg.get("content") == "fine answer" and engine.restarts == before + 1)

    print("T4 a slow but steady answer, or a long silent tool call, is left alone")
    await fresh("slow")
    before, t0 = engine.restarts, time.time()
    evs = await stream()
    text = "".join(e["content"] for e in evs if e["type"] == "content")
    check("a 6-second streamed answer finishes", text.count("word") == 6 and not any(e["type"] == "error" for e in evs), str(evs[-1:]))
    check("no restart for it", engine.restarts == before)
    await fresh("busy")
    msg = await ask([])
    check("an engine busy for 6 quiet seconds isn't restarted", msg.get("content") == "fine answer" and engine.restarts == before,
          str(engine.restarts - before))

    print("T5 streamed chats recover the same way")
    await fresh("hang,ok")
    before = engine.restarts
    evs = await stream()
    check("the stream restarts the engine and answers",
          "".join(e["content"] for e in evs if e["type"] == "content") == "fine answer" and engine.restarts == before + 1, str(evs))
    await fresh("hang,ok")
    evs = await stream("deliberate")
    done = [e for e in evs if e["type"] == "deliberation_complete"]
    check("tool-using turns too", bool(done) and done[0]["content"] == "fine answer", str(evs))
    await fresh("hang,hang")
    evs = await stream()
    errs = [e["content"] for e in evs if e["type"] == "error"]
    check("a stream that can't recover ends with a plain error", len(errs) == 1 and "stopped answering" in errs[0], str(evs))
    await fresh("midway,ok")
    before = engine.restarts
    evs = await stream()
    errs = [e["content"] for e in evs if e["type"] == "error"]
    check("a stall halfway through isn't retried (no repeated words), but the engine is restarted",
          len(errs) == 1 and "middle of the answer" in errs[0] and engine.restarts == before + 1, str(evs))
    msg = await ask([])
    check("and the next message works", msg.get("content") == "fine answer")

    print("T6 the engine's log never fills up and freezes it")
    await fresh("chatty")
    before = engine.restarts
    answers = [(await ask([])).get("content") for _ in range(3)]
    check("answers keep coming with a busy log", answers == ["fine answer"] * 3 and engine.restarts == before, str(answers))
    check("the log goes to a file", le.ENGINE_LOG.exists() and le.ENGINE_LOG.stat().st_size > 500_000)

    await engine.stop()


try:
    asyncio.run(main())
finally:
    asyncio.run(engine.stop())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
