"""
Xod Infra OpenAI-Compatible Acceleration Gateway + Live Dashboard.

Runs at http://127.0.0.1:8790 (OpenAI base URL: http://127.0.0.1:8790/v1).
Connect Xod Desktop (Settings -> AI Brain -> Local OpenAI-compatible) or any
client to automatically route to the fastest available Mac backend (MLX -> LM Studio -> Ollama),
track live tok/s & TTFT, monitor prefix-cache hit rates, and run 1-click Mac tuning.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from xod_infra import __version__
from xod_infra.bench import benchmark_endpoint
from xod_infra.hardware import detect_hardware
from xod_infra.tuner import apply_ollama_launchctl_env, build_tuning_plan

app = FastAPI(title="Xod Infra", version=__version__)

_recent_runs: Deque[Dict[str, Any]] = deque(maxlen=50)
_seen_prefixes: Deque[str] = deque(maxlen=256)
_prefix_stats = {"requests": 0, "hits": 0}


def _pick_upstream() -> Dict[str, str]:
    hw = detect_hardware()
    b = hw["backends"]
    for name in ("mlx", "lmstudio", "ollama", "llama_cpp"):
        if b.get(name, {}).get("running"):
            return {"name": name, "url": b[name]["url"]}
    return {"name": "ollama", "url": "http://127.0.0.1:11434/v1"}


def _prefix_hash(messages: List[Dict[str, Any]]) -> str:
    if not messages:
        return ""
    head = messages[:2]
    raw = json.dumps(head, sort_keys=True, ensure_ascii=False)[:4000]
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@app.get("/api/status")
async def api_status():
    hw = detect_hardware()
    plan = build_tuning_plan(hw)
    upstream = _pick_upstream()
    hit_rate = round((_prefix_stats["hits"] / _prefix_stats["requests"]) * 100, 1) if _prefix_stats["requests"] else 0.0
    avg_tps = (
        round(sum(r["decode_tps"] for r in _recent_runs if r.get("decode_tps")) / max(1, sum(1 for r in _recent_runs if r.get("decode_tps"))), 1)
        if _recent_runs else 0.0
    )
    avg_ttft = (
        round(sum(r["ttft_ms"] for r in _recent_runs if r.get("ttft_ms")) / max(1, sum(1 for r in _recent_runs if r.get("ttft_ms"))), 1)
        if _recent_runs else 0.0
    )
    return {
        "version": __version__,
        "upstream": upstream,
        "hardware": hw,
        "tuning": plan,
        "telemetry": {
            "requests": _prefix_stats["requests"],
            "prefix_cache_hit_rate": hit_rate,
            "avg_decode_tps": avg_tps,
            "avg_ttft_ms": avg_ttft,
            "recent": list(_recent_runs)[-12:],
        },
    }


@app.post("/api/tune/ollama")
async def api_tune_ollama():
    return apply_ollama_launchctl_env()


@app.post("/api/benchmark")
async def api_benchmark(req: Request):
    body = await req.json() if await req.body() else {}
    upstream = _pick_upstream()
    base = str(body.get("base_url") or upstream["url"])
    model = str(body.get("model") or "")
    res = await benchmark_endpoint(base, model=model)
    if res.get("ok"):
        _recent_runs.append({**res, "backend": upstream["name"], "ts": time.time(), "kind": "benchmark"})
    return res


@app.get("/v1/models")
async def v1_models():
    upstream = _pick_upstream()
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(f"{upstream['url']}/models")
            return JSONResponse(status_code=r.status_code, content=r.json())
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"No local backend reachable ({upstream['url']}): {e}")


@app.post("/v1/chat/completions")
async def v1_chat_completions(req: Request):
    payload = await req.json()
    upstream = _pick_upstream()
    messages = payload.get("messages") or []
    ph = _prefix_hash(messages)
    _prefix_stats["requests"] += 1
    cache_hit = bool(ph and ph in _seen_prefixes)
    if cache_hit:
        _prefix_stats["hits"] += 1
    elif ph:
        _seen_prefixes.append(ph)

    # Inject Apple Silicon KV-cache & keep-alive hints for Ollama upstreams when not overridden
    if upstream["name"] == "ollama":
        payload.setdefault("keep_alive", "30m")

    t0 = time.perf_counter()
    url = f"{upstream['url']}/chat/completions"

    if payload.get("stream"):
        async def _stream():
            first_at = None
            chunks = 0
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream("POST", url, json=payload) as resp:
                    async for chunk in resp.aiter_bytes():
                        if first_at is None:
                            first_at = time.perf_counter()
                        chunks += 1
                        yield chunk
            t_end = time.perf_counter()
            ttft = round(((first_at or t_end) - t0) * 1000, 1)
            gen_s = max(0.001, t_end - (first_at or t0))
            _recent_runs.append({
                "model": payload.get("model", ""),
                "backend": upstream["name"],
                "ttft_ms": ttft,
                "decode_tps": round(chunks / gen_s, 1),
                "prefix_hit": cache_hit,
                "ts": time.time(),
                "kind": "chat",
            })

        return StreamingResponse(_stream(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(url, json=payload)
        data = r.json()
        t_end = time.perf_counter()
        comp_toks = ((data.get("usage") or {}).get("completion_tokens")) or 0
        dur = max(0.001, t_end - t0)
        _recent_runs.append({
            "model": payload.get("model", ""),
            "backend": upstream["name"],
            "ttft_ms": round(dur * 1000, 1),
            "decode_tps": round(comp_toks / dur, 1) if comp_toks else 0.0,
            "prefix_hit": cache_hit,
            "ts": time.time(),
            "kind": "chat",
        })
        return JSONResponse(status_code=r.status_code, content=data)


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Xod Infra — Apple Silicon Acceleration</title>
<style>
  body{margin:0;background:#0d0f14;color:#e8ecf2;font-family:-apple-system,BlinkMacSystemFont,Inter,sans-serif;padding:28px}
  .wrap{max-width:920px;margin:0 auto}
  h1{margin:0 0 6px;font-size:26px} .sub{color:#94a3b8;font-size:14px;margin-bottom:20px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin-bottom:16px}
  .card{background:#161a23;border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:16px}
  .k{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#94a3b8}
  .v{font-size:22px;font-weight:700;margin-top:4px;color:#fff}
  .pill{display:inline-block;padding:3px 8px;border-radius:999px;font-size:11px;font-weight:600;background:#1e293b;color:#cbd5e1;margin-right:6px}
  .ok{background:rgba(16,185,129,.16);color:#6ee7b7}
  button{background:#f59e0b;color:#111;border:0;border-radius:10px;padding:9px 14px;font-weight:700;cursor:pointer;margin-right:8px}
  button.sec{background:#252c3b;color:#e2e8f0}
  code{background:#0b0d12;padding:2px 6px;border-radius:6px;font-size:12px;color:#93c5fd}
  pre{background:#0b0d12;padding:12px;border-radius:10px;overflow:auto;font-size:12px;color:#cbd5e1}
</style></head><body><div class="wrap">
  <h1>⚡ Xod Infra</h1>
  <div class="sub">Open-source Apple Silicon LLM speed &amp; efficiency engine · Connect Xod to <code>http://127.0.0.1:8790/v1</code></div>
  <div class="grid" id="stats"></div>
  <div class="card" style="margin-bottom:14px">
    <div class="k">Actions</div>
    <div style="margin-top:10px">
      <button onclick="runBench()">Run Live Tok/s Benchmark</button>
      <button class="sec" onclick="tuneOllama()">Apply Mac Ollama Tuning (Flash Attn + Q8 KV)</button>
    </div>
    <pre id="out" style="margin-top:12px;display:none"></pre>
  </div>
  <div class="card">
    <div class="k">Recommended MLX Speculative Decoding Command</div>
    <pre id="mlxCmd" style="margin-top:8px">Loading…</pre>
  </div>
</div>
<script>
async function load(){
  const d = await (await fetch('/api/status')).json();
  const h = d.hardware, t = d.telemetry;
  document.getElementById('stats').innerHTML = `
    <div class="card"><div class="k">Hardware</div><div class="v">${h.chip}</div><div class="sub" style="margin:4px 0 0">${h.ram_gb} GB Unified · ~${h.memory_bandwidth_gbps} GB/s</div></div>
    <div class="card"><div class="k">Active Upstream</div><div class="v">${d.upstream.name.toUpperCase()}</div><div class="sub" style="margin:4px 0 0">${d.upstream.url}</div></div>
    <div class="card"><div class="k">Live Speed</div><div class="v">${t.avg_decode_tps || h.estimated_7b_4bit_tps} tok/s</div><div class="sub" style="margin:4px 0 0">${t.avg_ttft_ms ? 'Avg TTFT ' + t.avg_ttft_ms + ' ms' : 'Est. 7B 4-bit peak'} · Cache ${t.prefix_cache_hit_rate}%</div></div>
  `;
  document.getElementById('mlxCmd').textContent = d.tuning.mlx_speculative_command;
}
async function runBench(){
  const o = document.getElementById('out'); o.style.display='block'; o.textContent='Running live token-speed benchmark…';
  const r = await (await fetch('/api/benchmark',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
  o.textContent = JSON.stringify(r, null, 2); load();
}
async function tuneOllama(){
  const o = document.getElementById('out'); o.style.display='block'; o.textContent='Applying Flash Attention + Q8_0 KV cache…';
  const r = await (await fetch('/api/tune/ollama',{method:'POST'})).json();
  o.textContent = JSON.stringify(r, null, 2);
}
load();
</script></body></html>"""
