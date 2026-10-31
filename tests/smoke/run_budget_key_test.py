"""
Completion-budget parameter: OpenAI's GPT-5 family and o-series refuse
"max_tokens" (HTTP 400: use "max_completion_tokens" instead). Auto mode picked
gpt-5-nano in the Brain v1 hardening round (2026-10-30) and a mission failed in
planning on exactly this. The LLM layer now picks the key each model accepts,
renames reactively when an endpoint teaches it otherwise, and grows whichever
key is present.

Temperature parameter (H5, found in the 0.9.13 promotion attempt, 2026-10-31):
the same families also refuse a set "temperature" (HTTP 400: "Unsupported
value: 'temperature' does not support 0.1 with this model. Only the default
(1) value is supported."). The layer now omits temperature for those families
up front, drops it reactively when an endpoint names it in a 400, remembers
the pair, and retries, in all three send paths. A stub OpenAI server below
asserts what actually goes over the wire.

Run: .venv/bin/python tests/smoke/run_budget_key_test.py
"""
import asyncio, json, os, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_budget_key_"))
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


# -- a stub OpenAI that refuses temperature exactly the way GPT-5 does ------

SEEN = []
TEMP_REFUSERS = ("gpt-5", "o1", "o3", "o4")
TEMP_MESSAGE = ("Unsupported value: 'temperature' does not support 0.1 with this model. "
                "Only the default (1) value is supported.")


class _FakeOpenAI(BaseHTTPRequestHandler):
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
        refuses = model.startswith(TEMP_REFUSERS) or model in ("gpt-7-pro", "gpt-8-pro")
        if refuses and "temperature" in body:
            return self._reply(400, {"error": {"message": TEMP_MESSAGE}})
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
    msgs = [{"role": "user", "content": "say the stub answer"}]

    def ep(model):
        return {"provider": "openai", "base_url": base, "model": model, "api_key": "test"}

    SEEN.clear()
    msg = await llm_service.chat_completion(messages=msgs, endpoint_override=ep("gpt-5-nano"))
    check("wire: gpt-5 is sent no temperature, budget under the completion key",
          len(SEEN) == 1 and "temperature" not in SEEN[0] and "max_completion_tokens" in SEEN[0]
          and msg.get("content") == "stub answer", str(SEEN))

    SEEN.clear()
    msg = await llm_service.chat_completion(messages=msgs, endpoint_override=ep("gpt-4o"))
    check("wire: gpt-4o is still sent its temperature",
          len(SEEN) == 1 and SEEN[0].get("temperature") == 0.2 and "max_tokens" in SEEN[0]
          and msg.get("content") == "stub answer", str(SEEN))

    SEEN.clear()
    out = ""
    async for ch in llm_service.stream_chat_completion(messages=msgs, think=False,
                                                       endpoint_override=ep("gpt-5-nano")):
        if ch.get("type") == "content":
            out += ch["content"]
    check("wire (stream): gpt-5 is sent no temperature",
          len(SEEN) == 1 and "temperature" not in SEEN[0] and out == "stub answer", str(SEEN))

    SEEN.clear()
    msg = await llm_service.chat_completion(messages=msgs, endpoint_override=ep("gpt-7-pro"))
    check("wire: an unknown model that refuses temperature is retried without it",
          len(SEEN) == 2 and "temperature" in SEEN[0] and "temperature" not in SEEN[1]
          and msg.get("content") == "stub answer", str(SEEN))
    SEEN.clear()
    await llm_service.chat_completion(messages=msgs, endpoint_override=ep("gpt-7-pro"))
    check("wire: the refusal is remembered, temperature omitted up front next time",
          len(SEEN) == 1 and "temperature" not in SEEN[0], str(SEEN))

    SEEN.clear()
    final, errs = None, []
    async for ch in llm_service.deliberate_stream(messages=msgs, endpoint_override=ep("gpt-8-pro")):
        if ch.get("type") == "deliberation_complete":
            final = ch
        elif ch.get("type") == "error":
            errs.append(ch["content"])
    check("wire (deliberate): a temperature refusal is retried without it",
          len(SEEN) == 2 and "temperature" in SEEN[0] and "temperature" not in SEEN[1]
          and final is not None and final.get("content") == "stub answer" and not errs,
          f"{SEEN} {errs}")


def main():
    check("gpt-5 family uses max_completion_tokens",
          llm._budget_key({"provider": "openai", "model": "gpt-5-nano"}) == "max_completion_tokens")
    check("o-series uses max_completion_tokens",
          llm._budget_key({"provider": "openai", "model": "o3-mini"}) == "max_completion_tokens")
    check("gpt-4o keeps max_tokens",
          llm._budget_key({"provider": "openai", "model": "gpt-4o"}) == "max_tokens")
    check("other providers keep max_tokens",
          llm._budget_key({"provider": "anthropic", "model": "claude-sonnet-5"}) == "max_tokens")
    check("proxied gpt-5 keeps max_tokens (not the direct API)",
          llm._budget_key({"provider": "openrouter", "model": "openai/gpt-5-nano"}) == "max_tokens")

    body = ('{"error": {"message": "Unsupported parameter: \'max_tokens\' is not supported '
            'with this model. Use \'max_completion_tokens\' instead."}}')
    payload = {"max_tokens": 800}
    info = {"provider": "openai", "model": "gpt-6-pro"}
    check("a naming 400 renames the budget", llm._budget_param_rejected(payload, info, 400, body) is True)
    check("the value moved, the old key is gone", payload == {"max_completion_tokens": 800}, str(payload))
    check("the pair is remembered", llm._budget_key(info) == "max_completion_tokens")

    other = {"max_tokens": 800}
    check("an unrelated 400 changes nothing",
          llm._budget_param_rejected(other, {"provider": "openai", "model": "gpt-4o"}, 400, "bad request") is False
          and other == {"max_tokens": 800})

    payload2 = {"max_completion_tokens": 100}
    llm._quiet_thinking(payload2, {"provider": "openai", "model": "gpt-5-nano"}, False)
    check("quiet thinking grows the completion key, never adds max_tokens",
          payload2 == {"max_completion_tokens": 100 + llm.CLOUD_THINK_HEADROOM}, str(payload2))

    # -- temperature (H5): the sibling refusal --------------------------------
    check("gpt-5 family takes no temperature up front",
          llm._temperature_ok({"provider": "openai", "model": "gpt-5-nano"}) is False)
    check("o-series takes no temperature up front",
          llm._temperature_ok({"provider": "openai", "model": "o3-mini"}) is False)
    check("gpt-4o still takes a temperature",
          llm._temperature_ok({"provider": "openai", "model": "gpt-4o"}) is True)
    check("other providers still take a temperature",
          llm._temperature_ok({"provider": "fireworks", "model": "gpt-5-like"}) is True)

    tbody = json.dumps({"error": {"message": TEMP_MESSAGE}})
    tpayload = {"temperature": 0.1, "max_completion_tokens": 800}
    tinfo = {"provider": "openai", "model": "gpt-6-pro"}
    check("a temperature 400 drops the parameter",
          llm._temperature_rejected(tpayload, tinfo, 400, tbody) is True)
    check("the key is gone, the rest of the payload is untouched",
          tpayload == {"max_completion_tokens": 800}, str(tpayload))
    check("the refusing pair is remembered", llm._temperature_ok(tinfo) is False)

    tpayload2 = {"max_tokens": 800}
    check("no temperature in the payload, no reaction",
          llm._temperature_rejected(tpayload2, {"provider": "openai", "model": "gpt-4o"}, 400, tbody) is False
          and tpayload2 == {"max_tokens": 800})
    tpayload3 = {"temperature": 0.2}
    check("an unrelated 400 changes nothing (temperature)",
          llm._temperature_rejected(tpayload3, {"provider": "openai", "model": "gpt-4o"}, 400, "bad request") is False
          and tpayload3 == {"temperature": 0.2})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAI)
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
