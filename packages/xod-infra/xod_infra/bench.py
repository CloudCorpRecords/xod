"""
Live local token-speed benchmarker (TTFT, prefill tok/s, decode tok/s).
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List

import httpx

BENCH_PROMPT = (
    "Explain in 6 concise bullet points how Apple Silicon unified memory bandwidth "
    "determines local LLM token generation speed and why speculative decoding helps."
)


async def benchmark_endpoint(
    base_url: str,
    model: str = "",
    max_tokens: int = 160,
    timeout: float = 90.0,
) -> Dict[str, Any]:
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=timeout) as client:
        # Discover first available model if not specified
        if not model:
            r = await client.get(f"{base}/models")
            r.raise_for_status()
            models = [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
            if not models:
                return {"ok": False, "base_url": base, "error": "No models loaded on this backend."}
            model = models[0]

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": BENCH_PROMPT}],
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "stream": True,
        }
        t0 = time.perf_counter()
        first_token_at = None
        chunks = 0
        text_parts: List[str] = []

        async with client.stream("POST", f"{base}/chat/completions", json=payload) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="ignore")[:300]
                return {"ok": False, "base_url": base, "model": model, "error": f"HTTP {resp.status_code}: {body}"}
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                d_obj = (ev.get("choices") or [{}])[0].get("delta") or {}
                delta = d_obj.get("content") or d_obj.get("reasoning_content") or d_obj.get("reasoning") or ""
                if delta:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    chunks += 1
                    text_parts.append(delta)

        t_end = time.perf_counter()
        ttft_ms = round(((first_token_at or t_end) - t0) * 1000, 1)
        gen_sec = max(0.001, t_end - (first_token_at or t0))
        total_sec = max(0.001, t_end - t0)
        # Approximate token count when streaming (chunks ~= tokens for OpenAI-compatible streams)
        est_tokens = max(chunks, max(1, len("".join(text_parts)) // 4))
        decode_tps = round(est_tokens / gen_sec, 1)

        return {
            "ok": True,
            "base_url": base,
            "model": model,
            "ttft_ms": ttft_ms,
            "decode_tps": decode_tps,
            "tokens": est_tokens,
            "total_sec": round(total_sec, 2),
        }
