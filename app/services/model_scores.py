"""
How good a model is at Xod's work, measured on this computer.

A test is a handful of short requests with a known right answer, sent the way Xod sends them:

  speed          words per second on a paragraph, after the model is loaded (and how long loading took)
  tool calling   calls get_weather(city="Paris") when asked about the weather in Paris (Xod's tools work this way)
  restraint      answers 7 × 6 itself instead of calling a tool it doesn't need
  structured     replies with a JSON object that parses, with the right values (plans and browser steps are JSON)
  reasoning      a small word problem (12 pens at 3 for $2 = 8)
  instructions   one word, lowercase, nothing else
  seeing         reads a number off a picture (only for models that say they can see; not part of the score)

The score is out of 100: tool calling 25, restraint 10, structured 15, reasoning 15, instructions 10, speed 25
(full marks at 40 words a second). Results are kept per model (data/model_scores.json) and shown in the models panel;
autoconnect prefers a better-scoring model when it picks one itself.
"""
import asyncio
import base64
import io
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from app.paths import DATA_DIR

logger = logging.getLogger("xod.scores")

PATH = DATA_DIR / "model_scores.json"
WEIGHTS = {"tools": 15, "choosing": 10, "restraint": 10, "structured": 15, "reasoning": 8, "reasoning_hard": 12, "instructions": 5}
WEIGHTS_SPEED = 25
ROOM = 1200          # tokens for each answer: a thinking model needs room to think before it answers
FULL_SPEED_TPS = 40.0
_jobs: Dict[str, Dict[str, Any]] = {}

WEATHER_TOOL = [{"type": "function", "function": {
    "name": "get_weather", "description": "Current weather for a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string", "description": "City name"}}, "required": ["city"]}}}]
THREE_TOOLS = WEATHER_TOOL + [
    {"type": "function", "function": {"name": "find_files", "description": "Search the person's computer for files by name or words.",
                                      "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "send_email", "description": "Send an email.",
                                      "parameters": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"},
                                                                                      "body": {"type": "string"}}, "required": ["to", "subject", "body"]}}}]


def all_scores() -> Dict[str, Dict[str, Any]]:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(k: str, result: Dict[str, Any]) -> None:
    data = all_scores()
    data[k] = result
    PATH.write_text(json.dumps(data, indent=1), encoding="utf-8")


def score_of(provider: str, model: str) -> Optional[int]:
    r = all_scores().get(f"{provider}:{model}")
    return r.get("score") if r else None


def _endpoint(provider: str, model: str, base_url: str = "") -> Dict[str, Any]:
    from app.services import model_sources
    from app.services.llm import llm_service
    info = dict(llm_service._build_endpoint(provider, model))
    if base_url:
        info["base_url"] = base_url.rstrip("/")
        info["api_key"] = model_sources.key_for(base_url) or info.get("api_key") or "local"
    info["model"] = model if provider not in ("fireworks",) else info["model"]
    return info


async def _ask(c: httpx.AsyncClient, info: Dict[str, Any], content: Any, tools=None, max_tokens: int = ROOM) -> Dict[str, Any]:
    from app.services.llm import _quiet_thinking, _apply_tools_effort
    payload: Dict[str, Any] = {"model": info["model"], "messages": [{"role": "user", "content": content}],
                               "temperature": 0, "max_tokens": max_tokens, "stream": False}
    if tools:
        payload["tools"], payload["tool_choice"] = tools, "auto"
    _quiet_thinking(payload, info, False)
    _apply_tools_effort(payload, info)   # up-front half of the tools/effort rule; this path has no retry loop
    t = time.time()
    r = await c.post(f"{info['base_url']}/chat/completions", json=payload,
                     headers={"Authorization": f"Bearer {info.get('api_key') or 'local'}"})
    secs = time.time() - t
    if r.status_code >= 400:
        return {"error": f"{r.status_code}: {r.text[:200]}", "secs": secs}
    data = r.json()
    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    thought = msg.get("reasoning") or msg.get("reasoning_content") or ""
    return {"text": (msg.get("content") or "").strip(), "calls": msg.get("tool_calls") or [], "usage": data.get("usage") or {},
            "secs": secs, "only_thought": bool(thought) and not (msg.get("content") or "").strip() and not msg.get("tool_calls")}


def _said(r: Dict[str, Any]) -> str:
    """What a failed check got, in a few words."""
    if r.get("error"):
        return r["error"][:100]
    if r.get("only_thought"):
        return "it only thought, and never gave an answer"
    return f"said: {(r.get('text') or str(r.get('calls') or '') or '(nothing)')[:80]}"


def _picture() -> Optional[str]:
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (480, 200), "white")
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 96)
        except OSError:
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 96)
            except OSError:
                font = ImageFont.load_default()
        d.text((60, 40), "47", fill="black", font=font)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _json_in(text: str) -> Any:
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", t, re.S)
    return json.loads(m.group(0)) if m else None


async def run(provider: str, model: str, base_url: str = "", sees: Optional[bool] = None,
              progress=lambda step: None) -> Dict[str, Any]:
    """Tests one model; returns (and keeps) the result."""
    info = _endpoint(provider, model, base_url)
    checks: Dict[str, Dict[str, Any]] = {}
    notes: List[str] = []
    async with httpx.AsyncClient(timeout=240) as c:
        progress("Loading the model")
        warm = await _ask(c, info, "Say OK.", max_tokens=5)
        if warm.get("error"):
            result = {"score": None, "error": f"It didn't answer: {warm['error']}", "at": time.time()}
            _save(f"{provider}:{model}", result)
            return result
        load_s = round(warm["secs"], 1)

        progress("Speed")
        sp = await _ask(c, info, "Write about 120 words on how lighthouses work.", max_tokens=220)
        n = (sp.get("usage") or {}).get("completion_tokens") or max(1, len(sp.get("text", "")) // 4)
        tps = round(n / max(sp["secs"], 0.05), 1) if not sp.get("error") else 0.0

        progress("Tool calling")
        tc = await _ask(c, info, "What's the weather in Paris right now? Use the tool.", tools=WEATHER_TOOL)
        call = (tc.get("calls") or [{}])[0].get("function") or {}
        args = call.get("arguments")
        try:
            args = json.loads(args) if isinstance(args, str) else (args or {})
        except ValueError:
            args = {}
        ok = call.get("name") == "get_weather" and "paris" in str(args.get("city", "")).lower()
        checks["tools"] = {"ok": ok, "detail": "Called get_weather for Paris" if ok else
                           ("Can't call tools: " + tc["error"][:120]) if tc.get("error") else f"Didn't call the tool right ({_said(tc)})"}
        no_tools = bool(tc.get("error"))
        if no_tools:
            notes.append("This model can't call tools, so Xod plans its steps as text with it: slower and less reliable.")

        progress("Choosing the right tool")
        ch = await _ask(c, info, "Find my 2023 tax return PDF on this computer.", tools=None if no_tools else THREE_TOOLS)
        call = (ch.get("calls") or [{}])[0].get("function") or {}
        args = call.get("arguments")
        try:
            args = json.loads(args) if isinstance(args, str) else (args or {})
        except ValueError:
            args = {}
        ok = call.get("name") == "find_files" and "tax" in str(args.get("query", "")).lower()
        checks["choosing"] = {"ok": ok, "detail": "Picked find_files out of three" if ok else
                              f"Picked {call.get('name') or 'nothing'} ({_said(ch)})" if not no_tools else "Can't call tools"}

        progress("Restraint")
        rs = await _ask(c, info, "What is 7 times 6? Answer with just the number.", tools=None if no_tools else WEATHER_TOOL)
        ok = not rs.get("calls") and "42" in rs.get("text", "")
        checks["restraint"] = {"ok": ok, "detail": "Answered 42 without a tool" if ok else _said(rs)}

        progress("Structured answers")
        js = await _ask(c, info, "Reply with only a JSON object and no other text: the keys name (a string) and age (a number), "
                                 "for Ana, who is 31.")
        try:
            obj = _json_in(js.get("text", ""))
        except ValueError:
            obj = None
        ok = isinstance(obj, dict) and str(obj.get("name", "")).lower() == "ana" and str(obj.get("age")) == "31"
        checks["structured"] = {"ok": ok, "detail": "Valid JSON with the right values" if ok else _said(js)}

        progress("Reasoning")
        rq = await _ask(c, info, "Pens cost $2 for 3. How much do 12 pens cost, in dollars? Reply with just the number.")
        nums = re.findall(r"\d+(?:\.\d+)?", rq.get("text", ""))
        ok = bool(nums) and float(nums[-1]) == 8
        checks["reasoning"] = {"ok": ok, "detail": "8, right" if ok else _said(rq)}
        rh = await _ask(c, info, "A train leaves at 14:40. The trip takes 2 hours 35 minutes, and it waits 25 minutes at one station "
                                 "on the way. When does it arrive? Reply with just the time as HH:MM (24-hour).")
        ok = "17:40" in rh.get("text", "")
        checks["reasoning_hard"] = {"ok": ok, "detail": "17:40, right" if ok else _said(rh)}

        progress("Following instructions")
        ins = await _ask(c, info, "Reply with exactly one word, in lowercase: the color of a ripe banana.")
        ok = ins.get("text", "").strip().strip(".!").strip() == "yellow"
        checks["instructions"] = {"ok": ok, "detail": "yellow" if ok else _said(ins)}
        if sum(1 for r in (rs, js, rq, rh, ins) if r.get("only_thought")) >= 2:
            notes.append("It often thinks without getting to the answer: Xod asks it again when that happens, which is slower.")

        seeing = None
        if sees:
            progress("Seeing")
            pic = _picture()
            if pic:
                vs = await _ask(c, info, [{"type": "text", "text": "What number is written in this picture? Reply with just the number."},
                                          {"type": "image_url", "image_url": {"url": pic}}])
                seeing = {"ok": "47" in vs.get("text", ""), "detail": "Read 47 off the picture" if "47" in vs.get("text", "") else _said(vs)}

    speed_pts = min(WEIGHTS_SPEED, tps * WEIGHTS_SPEED / FULL_SPEED_TPS)
    total = round(sum(WEIGHTS[k] for k, v in checks.items() if v["ok"]) + speed_pts)
    if tps and tps < 5:
        notes.append(f"Slow on this computer ({tps:g} words a second): it may not fit in the GPU's memory.")
    result = {"score": total, "speed_tps": tps, "load_s": load_s, "checks": checks, "seeing": seeing, "notes": notes,
              "at": time.time(), "provider": provider, "model": model}
    _save(f"{provider}:{model}", result)
    return result


def start(provider: str, model: str, base_url: str = "", sees: Optional[bool] = None) -> Dict[str, Any]:
    """Runs a test in the background; poll status()."""
    k = f"{provider}:{model}"
    job = _jobs.get(k)
    if job and job["status"] == "running":
        return {"success": True, "key": k, **job}
    job = _jobs[k] = {"status": "running", "step": "Starting", "started": time.time()}

    async def go():
        try:
            job["result"] = await run(provider, model, base_url, sees, progress=lambda s: job.update(step=s))
            job["status"] = "done"
        except Exception as e:
            logger.exception("model test failed")
            job.update(status="failed", error=str(e)[:300])
    job["task"] = asyncio.get_running_loop().create_task(go())
    return {"success": True, "key": k, "status": "running"}


def status(k: str) -> Dict[str, Any]:
    job = _jobs.get(k)
    if not job:
        r = all_scores().get(k)
        return {"status": "done", "result": r} if r else {"status": "none"}
    return {k2: v for k2, v in job.items() if k2 != "task"}
