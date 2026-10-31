"""
Reasoning effort with function tools: OpenAI's gpt-6.1-sol refuses a
chat/completions request that carries function tools together with a
reasoning effort, HTTP 400: "Function tools with reasoning_effort are not
supported for gpt-6.1-sol in /v1/chat/completions. To use function tools,
use /v1/responses or set reasoning_effort to 'none'." (captured live in the
fix round 1 baseline, 2026-10-31: an Auto-mode subtask on gpt-6.1-sol died
on exactly this, the file it was writing was never created, and the work
was lost).

Where the effort can come from in this layer, and why the fix is shaped
this way:
- _quiet_thinking (think=False) attaches reasoning_effort="low" for
  Fireworks and "none" for LM Studio / Ollama. A tools payload built that
  way carries a thinking effort the evidenced model refuses.
- deliberate_stream, the agent/tool stream the subtask actually ran on,
  attaches no reasoning_effort at all and, unlike the other two send
  paths, had no reactive handling for this refusal either. Omission was
  already the status quo on that path and the call still died, so for a
  payload carrying no effort the remedy cannot be to drop a parameter
  that is not there: it is the API's own instruction, an explicit
  reasoning_effort="none".

The rule (services/llm.py, all three send paths plus the model_scores
scoring path for its up-front half):
- Up front: the one evidenced model, gpt-6.1-sol (the refusal names the
  model, not the endpoint, so this follows the model id on any route),
  sends reasoning_effort="none" whenever the request carries function
  tools.
  A request without tools is untouched, so its current behaviour is
  unchanged. No other model is hard-coded: there is no error-text
  evidence for the other pricing-table rows (gpt-6-astra, gpt-6.1-terra,
  gpt-6-luna, gpt-6-sol, the 5.6 / 5.5 / 5.4 rows).
- Reactive: for ANY model, a 400 whose text names reasoning_effort
  together with function tools is answered once: a carried thinking
  effort is dropped (the H2/H5 shape), a payload with no effort gets
  reasoning_effort="none" (the API's own instruction). The remedy that
  was applied is remembered for the (provider, model) pair, scoped to
  tools calls only, so later tools calls apply it up front and later
  no-tools calls keep their previous behaviour. (The older
  _effort_rejected memory is not tools-scoped, so a tools-only refusal
  used to strip the effort from later no-tools calls too.)

A stub provider below asserts what actually goes over the wire. The
Fireworks + think=False cases are the wire vehicle for a carried effort,
because that is the one cloud path in this layer that attaches one to a
tools payload today; the deliberate cases run on the OpenAI pair the
live failure used.

Run: .venv/bin/python tests/smoke/run_tools_reasoning_effort_test.py
"""
import asyncio, json, os, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_tools_effort_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

from app.services import llm

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


TOOLS = [{"type": "function",
          "function": {"name": "write_file", "description": "write a file",
                       "parameters": {"type": "object", "properties": {}}}}]


def effort_text(model):
    return (f"Function tools with reasoning_effort are not supported for {model} in "
            f"/v1/chat/completions. To use function tools, use /v1/responses or "
            f"set reasoning_effort to 'none'.")


SEEN = []


class _FakeProvider(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, obj, ctype="application/json"):
        msg = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        model = str(body.get("model") or "")
        effort = body.get("reasoning_effort")
        if body.get("tools") and model in ("gpt-6.1-sol", "gpt-6.1-terra"):
            # The deliberate (streaming) terra case models the live failure as
            # captured: the request carried no reasoning_effort at all and was
            # still refused, so only an explicit "none" is accepted there.
            if body.get("stream") and model == "gpt-6.1-terra" and effort != "none":
                return self._reply(400, {"error": {"message": effort_text(model)}})
            if effort not in (None, "none"):
                return self._reply(400, {"error": {"message": effort_text(model)}})
        if body.get("stream"):
            chunks = [{"choices": [{"delta": {"content": "stub answer"}}]},
                      {"choices": [{"delta": {}, "finish_reason": "stop"}]}]
            payload = b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"
            return self._reply(200, payload, "text/event-stream")
        self._reply(200, {"choices": [{"message": {"role": "assistant", "content": "stub answer"},
                                       "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}})


async def _stub_checks(base):
    from app.services.llm import llm_service
    msgs = [{"role": "user", "content": "write the file"}]

    def ep(provider, model):
        return {"provider": provider, "base_url": base, "model": model, "api_key": "test"}

    # (1) The evidenced pair with tools: "none" on the first attempt.
    SEEN.clear()
    msg = await llm_service.chat_completion(messages=msgs, tools=TOOLS,
                                            endpoint_override=ep("openai", "gpt-6.1-sol"))
    check("wire: gpt-6.1-sol with tools sends reasoning_effort none on the first attempt",
          len(SEEN) == 1 and SEEN[0].get("reasoning_effort") == "none"
          and msg.get("content") == "stub answer", str(SEEN))

    SEEN.clear()
    msg = await llm_service.chat_completion(messages=msgs, tools=TOOLS, think=False,
                                            endpoint_override=ep("fireworks", "gpt-6.1-sol"))
    check("wire: a carried thinking effort is replaced by none for the evidenced model with tools",
          len(SEEN) == 1 and SEEN[0].get("reasoning_effort") == "none"
          and msg.get("content") == "stub answer", str(SEEN))

    SEEN.clear()
    final, errs = None, []
    async for ch in llm_service.deliberate_stream(messages=msgs, tools=TOOLS,
                                                  endpoint_override=ep("openai", "gpt-6.1-sol")):
        if ch.get("type") == "deliberation_complete":
            final = ch
        elif ch.get("type") == "error":
            errs.append(ch["content"])
    check("wire (deliberate): gpt-6.1-sol with tools sends none on the first attempt and completes",
          len(SEEN) == 1 and SEEN[0].get("reasoning_effort") == "none"
          and final is not None and final.get("content") == "stub answer" and not errs,
          f"{SEEN} {errs}")

    # (2) An unevidenced model with tools: the first attempt may carry the
    # effort, the refusal is answered by dropping it, and the pair is
    # remembered for later tools calls.
    SEEN.clear()
    msg = await llm_service.chat_completion(messages=msgs, tools=TOOLS, think=False,
                                            endpoint_override=ep("fireworks", "gpt-6.1-terra"))
    check("wire: an unevidenced model that refuses effort with tools is retried without it",
          len(SEEN) == 2 and SEEN[0].get("reasoning_effort") == "low"
          and "reasoning_effort" not in SEEN[1] and msg.get("content") == "stub answer", str(SEEN))
    SEEN.clear()
    await llm_service.chat_completion(messages=msgs, tools=TOOLS, think=False,
                                      endpoint_override=ep("fireworks", "gpt-6.1-terra"))
    check("wire: the tools refusal is remembered, the effort is omitted up front next time",
          len(SEEN) == 1 and "reasoning_effort" not in SEEN[0], str(SEEN))

    # The memory is scoped to tools calls: a no-tools call for the same pair
    # keeps the effort it had before (the old _no_effort memory did not).
    SEEN.clear()
    await llm_service.chat_completion(messages=msgs, think=False,
                                      endpoint_override=ep("fireworks", "gpt-6.1-terra"))
    check("wire: a no-tools call for a tools-refused pair still carries its effort",
          len(SEEN) == 1 and SEEN[0].get("reasoning_effort") == "low", str(SEEN))

    # (2b) The same refusal on the agent/tool stream, where the payload
    # carries no effort to drop: the retry sets it to "none", and a second
    # call sends "none" up front.
    SEEN.clear()
    final, errs = None, []
    async for ch in llm_service.deliberate_stream(messages=msgs, tools=TOOLS,
                                                  endpoint_override=ep("openai", "gpt-6.1-terra")):
        if ch.get("type") == "deliberation_complete":
            final = ch
        elif ch.get("type") == "error":
            errs.append(ch["content"])
    check("wire (deliberate): a tools refusal with no effort to drop is retried with none",
          len(SEEN) == 2 and "reasoning_effort" not in SEEN[0]
          and SEEN[1].get("reasoning_effort") == "none"
          and final is not None and final.get("content") == "stub answer" and not errs,
          f"{SEEN} {errs}")
    SEEN.clear()
    final = None
    async for ch in llm_service.deliberate_stream(messages=msgs, tools=TOOLS,
                                                  endpoint_override=ep("openai", "gpt-6.1-terra")):
        if ch.get("type") == "deliberation_complete":
            final = ch
    check("wire (deliberate): the remedy is remembered, none is sent up front next time",
          len(SEEN) == 1 and SEEN[0].get("reasoning_effort") == "none" and final is not None, str(SEEN))

    # (3) The evidenced pair without tools: behaviour unchanged.
    SEEN.clear()
    msg = await llm_service.chat_completion(messages=msgs,
                                            endpoint_override=ep("openai", "gpt-6.1-sol"))
    check("wire: gpt-6.1-sol without tools is sent no reasoning_effort, as before",
          len(SEEN) == 1 and "reasoning_effort" not in SEEN[0]
          and msg.get("content") == "stub answer", str(SEEN))
    SEEN.clear()
    await llm_service.chat_completion(messages=msgs, think=False,
                                      endpoint_override=ep("fireworks", "gpt-6.1-sol"))
    check("wire: gpt-6.1-sol without tools keeps the effort think=False asks for, as before",
          len(SEEN) == 1 and SEEN[0].get("reasoning_effort") == "low", str(SEEN))


def main():
    sol = {"provider": "openai", "model": "gpt-6.1-sol"}
    terra = {"provider": "openai", "model": "gpt-6.1-terra"}

    # getattr, so that on code without these helpers every unit check below
    # records a FAIL and the wire checks still run (failing-first evidence).
    mode_fn = getattr(llm, "_tools_effort_mode", None)
    rejected_fn = getattr(llm, "_tools_effort_rejected", None)

    def mode(info, has_tools):
        return mode_fn(info, has_tools) if mode_fn else "MISSING"

    def rejected(payload, info, status, body):
        return rejected_fn(payload, info, status, body) if rejected_fn else "MISSING"

    check("the evidenced pair with tools gets reasoning_effort none up front",
          mode(sol, True) == "none")
    check("the evidenced pair without tools is untouched",
          mode(sol, False) is None)
    check("an unevidenced model with tools gets nothing hard-coded",
          mode(terra, True) is None)
    check("the evidenced model on another route also gets none up front (the refusal names the model)",
          mode({"provider": "fireworks", "model": "gpt-6.1-sol"}, True) == "none")

    body = json.dumps({"error": {"message": effort_text("gpt-6.1-terra")}})
    payload = {"tools": TOOLS, "reasoning_effort": "low", "max_tokens": 800}
    check("a tools 400 naming reasoning_effort drops a carried effort",
          rejected(payload, terra, 400, body) is True)
    check("the effort is gone, the rest of the payload is untouched",
          payload == {"tools": TOOLS, "max_tokens": 800}, str(payload))
    check("the drop remedy is remembered for tools calls only",
          mode(terra, True) == "omit" and mode(terra, False) is None)

    payload2 = {"tools": TOOLS, "max_tokens": 800}
    info2 = {"provider": "openai", "model": "gpt-6.1-luna"}
    check("a tools 400 with no effort to drop sets reasoning_effort to none",
          rejected(payload2, info2, 400, body) is True
          and payload2.get("reasoning_effort") == "none", str(payload2))
    check("the none remedy is remembered for tools calls only",
          mode(info2, True) == "none" and mode(info2, False) is None)

    payload3 = {"tools": TOOLS, "reasoning_effort": "none"}
    check("a payload already sending none is not retried again on the same refusal",
          rejected(payload3, {"provider": "openai", "model": "gpt-6-sol"}, 400, body) is False
          and payload3 == {"tools": TOOLS, "reasoning_effort": "none"})
    payload4 = {"reasoning_effort": "low"}
    check("a naming 400 on a call without tools changes nothing here",
          rejected(payload4, terra, 400, body) is False
          and payload4 == {"reasoning_effort": "low"})
    payload5 = {"tools": TOOLS, "reasoning_effort": "low"}
    check("an unrelated 400 changes nothing",
          rejected(payload5, terra, 400, "bad request") is False
          and payload5 == {"tools": TOOLS, "reasoning_effort": "low"})
    payload6 = {"tools": TOOLS, "reasoning_effort": "low"}
    check("a 400 naming reasoning_effort but not function tools changes nothing here",
          rejected(payload6, terra, 400,
                   "reasoning_effort is not supported for this model") is False
          and payload6 == {"tools": TOOLS, "reasoning_effort": "low"})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeProvider)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        asyncio.run(_stub_checks(f"http://127.0.0.1:{srv.server_address[1]}/v1"))
    finally:
        srv.shutdown()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
