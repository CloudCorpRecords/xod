"""
Reasoning effort with function tools, round 3: EVERY call path.

Fix round 1 (tests/smoke/run_tools_reasoning_effort_test.py) proved the
rule on the plain chat path and on deliberate_stream with the exact model
id "gpt-6.1-sol". The re-run 2 measurement (ESCALATION-T1-RERUN2) showed a
mission subtask still dying on the provider 400 ("Function tools with
reasoning_effort are not supported for gpt-6.1-sol in
/v1/chat/completions. To use function tools, use /v1/responses or set
reasoning_effort to 'none'."), so this suite inventories every LLM call
path in the app and asserts the wire parameters on each:

  llm.chat_completion        plain chat, and the mission planner
                             (services/missions.py _default_ask rides it,
                             tested below through missions itself)
  llm.deliberate_stream      the agent/tool stream: the mission subtask
                             worker (agent/worker.py), the team subagent
                             loop (services/team.py), the operator and
                             the swarm all call it with tools
  llm.stream_chat_completion streaming chat: the researcher planner
                             (deliberate_and_plan), the fast lane, the
                             builder, evals. On main this entry takes no
                             tools parameter at all, so its tools/effort
                             handlers could never fire.
  model_scores._ask          a direct httpx call that bypasses llm.py;
                             round 1 gave it the up-front half only, and
                             a naming 400 there was returned as an error
                             with no remedy and no retry.

Two gaps this suite pins down, both invisible to the round 1 suite:

- The up-front rule matched the model id EXACTLY. Model ids reach this
  layer in routed forms: _build_endpoint prefixes a Fireworks model with
  "accounts/fireworks/models/", other routes carry "provider/model"
  ids, and pricing.lookup_price attests dated snapshot ids
  ("gpt-4o-2024-08-06" shares the "gpt-4o" row). Under any of those
  forms the evidenced model silently missed the up-front "none", and on
  a path with no retry loop the call died. The rule now follows the
  base model id (last path segment, dated suffix stripped), the same
  alias rule the pricing table uses.
- The stub below models the live API faithfully for the evidenced
  model: a tools call for gpt-6.1-sol is accepted only with an explicit
  reasoning_effort of "none" (omission died live in the baseline: the
  subtask call carried no effort and was still refused). For the
  unevidenced stand-ins (terra, luna) a carried effort is refused and
  omission or "none" is accepted, so the reactive remedy (drop a
  carried effort, remember it) is what those cases exercise.

Run: .venv/bin/python tests/smoke/run_tools_effort_paths_test.py
"""
import asyncio, json, os, re, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_tools_effort_paths_"))
import os
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import httpx

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


def base_id(model):
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", str(model or "").lower().rsplit("/", 1)[-1])


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
        base = base_id(body.get("model"))
        effort = body.get("reasoning_effort")
        if body.get("tools") and base == "gpt-6.1-sol" and effort != "none":
            return self._reply(400, {"error": {"message": effort_text(body.get("model"))}})
        if body.get("tools") and base in ("gpt-6.1-terra", "gpt-6.1-luna") \
                and effort not in (None, "none"):
            return self._reply(400, {"error": {"message": effort_text(body.get("model"))}})
        if body.get("stream"):
            chunks = [{"choices": [{"delta": {"content": "stub answer"}}]},
                      {"choices": [{"delta": {}, "finish_reason": "stop"}]}]
            payload = b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"
            return self._reply(200, payload, "text/event-stream")
        self._reply(200, {"choices": [{"message": {"role": "assistant", "content": "stub answer"},
                                       "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}})


async def _collect(gen):
    final, errs, content = None, [], ""
    async for ch in gen:
        if ch.get("type") == "deliberation_complete":
            final = ch
        elif ch.get("type") == "error":
            errs.append(ch["content"])
        elif ch.get("type") == "content":
            content += ch.get("content") or ""
    return final, errs, content


async def _stub_checks(base):
    from app.services.llm import llm_service
    from app.services import model_scores
    msgs = [{"role": "user", "content": "write the file"}]

    def ep(provider, model):
        return {"provider": provider, "base_url": base, "model": model, "api_key": "test"}

    # (A) Plain chat, routed id form: the Fireworks route prefixes the
    # model id, so the first attempt must already send "none".
    SEEN.clear()
    msg = await llm_service.chat_completion(
        messages=msgs, tools=TOOLS, think=False,
        endpoint_override=ep("fireworks", "accounts/fireworks/models/gpt-6.1-sol"))
    check("wire (chat, routed id): gpt-6.1-sol under a route prefix sends none on the first attempt",
          len(SEEN) == 1 and SEEN[0].get("reasoning_effort") == "none"
          and msg.get("content") == "stub answer", str(SEEN))

    # (B) The mission subtask worker call (agent/worker.py: deliberate_stream
    # with tools, tool_choice auto, temperature 0.1, max_tokens 1500), with a
    # dated snapshot id of the evidenced model.
    SEEN.clear()
    final, errs, _ = await _collect(llm_service.deliberate_stream(
        messages=msgs, tools=TOOLS, tool_choice="auto", temperature=0.1, max_tokens=1500,
        endpoint_override=ep("openai", "gpt-6.1-sol-2026-10-31")))
    check("wire (mission subtask worker, dated id): none on the first attempt and completes",
          len(SEEN) == 1 and SEEN[0].get("reasoning_effort") == "none"
          and final is not None and final.get("content") == "stub answer" and not errs,
          f"{SEEN} {errs}")

    # (C) The team subagent call (services/team.py: deliberate_stream with an
    # endpoint_override), exact id: coverage that already held, kept here so
    # the subtask family is asserted in one place.
    SEEN.clear()
    final, errs, _ = await _collect(llm_service.deliberate_stream(
        messages=msgs, tools=TOOLS, tool_choice="auto", temperature=0.1, max_tokens=1500,
        endpoint_override=ep("openai", "gpt-6.1-sol")))
    check("wire (team subagent, exact id): none on the first attempt and completes",
          len(SEEN) == 1 and SEEN[0].get("reasoning_effort") == "none"
          and final is not None and not errs, f"{SEEN} {errs}")

    # (D) Streaming chat with tools (the researcher planner's entry): the
    # evidenced pair sends "none" first time.
    SEEN.clear()
    err = None
    try:
        _, errs, content = await _collect(llm_service.stream_chat_completion(
            messages=msgs, tools=TOOLS, endpoint_override=ep("openai", "gpt-6.1-sol")))
    except TypeError as e:
        err, errs, content = str(e), ["TypeError"], ""
    check("wire (streaming chat, tools): gpt-6.1-sol sends none on the first attempt",
          err is None and len(SEEN) == 1 and SEEN[0].get("reasoning_effort") == "none"
          and content == "stub answer" and not errs, f"{SEEN} {errs} {err or ''}")

    # (E) Streaming chat, reactive remedy on a model the up-front rule does
    # not name, carrying an effort (Fireworks think=False carries "low"):
    # the naming 400 is answered by dropping it, and the remedy is
    # remembered for the next tools call on that pair.
    SEEN.clear()
    err = None
    try:
        _, errs, content = await _collect(llm_service.stream_chat_completion(
            messages=msgs, tools=TOOLS, think=False,
            endpoint_override=ep("fireworks", "gpt-6.1-terra")))
    except TypeError as e:
        err, errs, content = str(e), ["TypeError"], ""
    check("wire (streaming chat, reactive): a carried effort refused with tools is dropped and retried",
          err is None and len(SEEN) == 2 and SEEN[0].get("reasoning_effort") == "low"
          and "reasoning_effort" not in SEEN[1] and content == "stub answer" and not errs,
          f"{SEEN} {errs} {err or ''}")
    SEEN.clear()
    try:
        await _collect(llm_service.stream_chat_completion(
            messages=msgs, tools=TOOLS, think=False,
            endpoint_override=ep("fireworks", "gpt-6.1-terra")))
    except TypeError:
        pass
    check("wire (streaming chat, reactive): the remedy is remembered, omitted up front next time",
          len(SEEN) == 1 and "reasoning_effort" not in SEEN[0], str(SEEN))

    # (F) Streaming chat without tools: unchanged, no effort key appears.
    SEEN.clear()
    _, errs, content = await _collect(llm_service.stream_chat_completion(
        messages=msgs, endpoint_override=ep("openai", "gpt-6.1-sol")))
    check("wire (streaming chat, no tools): no reasoning_effort, as before",
          len(SEEN) == 1 and "reasoning_effort" not in SEEN[0] and "tools" not in SEEN[0]
          and content == "stub answer" and not errs, f"{SEEN} {errs}")

    # (G) model_scores._ask, up front: the evidenced pair with its weather
    # tool sends "none" first time (held since round 1; asserted here so
    # this bypass path is covered in the per-path suite).
    SEEN.clear()
    async with httpx.AsyncClient() as c:
        r = await model_scores._ask(c, ep("openai", "gpt-6.1-sol"), "What's the weather in Paris? Use the tool.",
                                    tools=model_scores.WEATHER_TOOL)
    check("wire (model_scores, up front): gpt-6.1-sol with tools sends none on the first attempt",
          len(SEEN) == 1 and SEEN[0].get("reasoning_effort") == "none" and not r.get("error"),
          f"{SEEN} {r}")

    # (H) model_scores._ask, reactive: an unevidenced model carrying "low"
    # (Fireworks think=False in _ask) is refused; the remedy drops it, the
    # call is retried once, and the remedy is remembered for later asks.
    SEEN.clear()
    async with httpx.AsyncClient() as c:
        r = await model_scores._ask(c, ep("fireworks", "gpt-6.1-luna"), "What's the weather in Paris? Use the tool.",
                                    tools=model_scores.WEATHER_TOOL)
    check("wire (model_scores, reactive): a naming 400 is remedied and retried, not returned as an error",
          len(SEEN) == 2 and SEEN[0].get("reasoning_effort") == "low"
          and "reasoning_effort" not in SEEN[1] and not r.get("error"),
          f"{SEEN} {r}")
    SEEN.clear()
    async with httpx.AsyncClient() as c:
        r = await model_scores._ask(c, ep("fireworks", "gpt-6.1-luna"), "What's the weather in Paris? Use the tool.",
                                    tools=model_scores.WEATHER_TOOL)
    check("wire (model_scores, reactive): the remedy is remembered, omitted up front next time",
          len(SEEN) == 1 and "reasoning_effort" not in SEEN[0] and not r.get("error"),
          f"{SEEN} {r}")

    # (I) The mission planner itself (services/missions.py _default_ask,
    # which rides chat_completion with think=False and task mission_plan):
    # it attaches no tools, so the wire carries no tools and no effort,
    # and the answer comes back. This proves which covered entry the
    # planner uses; a tools refusal cannot arise on a no-tools call.
    from app.services import missions
    SEEN.clear()
    original = llm_service.get_endpoint_info
    llm_service.get_endpoint_info = lambda: ep("openai", "gpt-6.1-sol")
    try:
        text = await missions._default_ask("Plan this goal as JSON.")
    finally:
        llm_service.get_endpoint_info = original
    check("wire (mission planner): rides chat_completion, no tools, no reasoning_effort, answer returned",
          len(SEEN) == 1 and "tools" not in SEEN[0] and "reasoning_effort" not in SEEN[0]
          and text == "stub answer", f"{SEEN} {text!r}")


def main():
    mode_fn = getattr(llm, "_tools_effort_mode", None)

    def mode(info, has_tools):
        return mode_fn(info, has_tools) if mode_fn else "MISSING"

    check("unit: the evidenced model under a route prefix gets none up front",
          mode({"provider": "fireworks", "model": "accounts/fireworks/models/gpt-6.1-sol"}, True) == "none")
    check("unit: the evidenced model as a dated snapshot gets none up front",
          mode({"provider": "openai", "model": "gpt-6.1-sol-2026-10-31"}, True) == "none")
    check("unit: the evidenced model under a provider/model route gets none up front",
          mode({"provider": "openrouter", "model": "openai/gpt-6.1-sol"}, True) == "none")
    check("unit: a routed evidenced id without tools is untouched",
          mode({"provider": "fireworks", "model": "accounts/fireworks/models/gpt-6.1-sol"}, False) is None)
    check("unit: an unevidenced routed model gets nothing hard-coded",
          mode({"provider": "openrouter", "model": "openai/gpt-6.1-terra"}, True) is None)
    check("unit: a different model that merely ends in sol is not matched",
          mode({"provider": "openai", "model": "gpt-6-sol"}, True) is None)

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
