"""
Apple Silicon tuning recommendations & one-command optimizations.

Generates exact launchctl/shell tuning for Ollama (Flash Attention, Q8_0 KV cache,
Keep-Alive, optimal num_ctx/num_batch) and MLX speculative decoding server commands.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any, Dict, List

from xod_infra.hardware import detect_hardware


def build_tuning_plan(hw: Dict[str, Any] | None = None) -> Dict[str, Any]:
    hw = hw or detect_hardware()
    rec = hw["recommended"]
    ram_gb = hw.get("ram_gb") or 16.0

    ollama_env = {
        "OLLAMA_FLASH_ATTENTION": "1",
        "OLLAMA_KV_CACHE_TYPE": rec["kv_cache_type"],
        "OLLAMA_KEEP_ALIVE": rec["keep_alive"],
        "OLLAMA_NUM_PARALLEL": "2" if ram_gb >= 24 else "1",
        "OLLAMA_MAX_LOADED_MODELS": "2" if ram_gb >= 32 else "1",
    }

    mlx_cmd = [
        "mlx_lm.server",
        "--model", rec["target_model"],
        "--draft-model", rec["draft_model"],
        "--num-draft-tokens", "4",
        "--host", "127.0.0.1",
        "--port", "8080",
    ]

    tips: List[Dict[str, str]] = [
        {
            "id": "kv_cache_q8",
            "title": "Quantize KV Cache to Q8_0 + Flash Attention",
            "impact": "Cuts KV-cache RAM by ~50% and boosts long-context prompt eval 15–30%",
            "detail": "Sets OLLAMA_FLASH_ATTENTION=1 and OLLAMA_KV_CACHE_TYPE=q8_0 so 8k–32k contexts stay entirely in unified GPU memory without macOS swap.",
        },
        {
            "id": "mlx_speculative",
            "title": "MLX Native + Speculative Decoding",
            "impact": "1.6x–2.4x faster token/sec on Apple Silicon with identical outputs",
            "detail": f"Pairs {rec['draft_model']} (draft) with {rec['target_model']} (target) directly on Metal Unified Memory.",
        },
        {
            "id": "prefix_cache",
            "title": "Prefix-Stable Prompt Caching",
            "impact": "Cuts Time-To-First-Token (TTFT) from ~3s down to ~120ms",
            "detail": "Xod Infra hashes system+tool prefixes and tracks cache hits so multi-turn agent loops reuse KV state instead of re-prefilling.",
        },
    ]
    if hw.get("apple_silicon") and ram_gb >= 32:
        wired_target = int(ram_gb * 1024 * 0.80)
        tips.append({
            "id": "wired_limit",
            "title": f"Raise Metal Wired Memory Limit to {wired_target} MB",
            "impact": "Unlocks ~80% of unified RAM for larger 32B–70B models",
            "detail": f"Optional (requires sudo): `sudo sysctl iogpu.wired_limit_mb={wired_target}`",
        })

    return {
        "hardware": hw,
        "ollama_env": ollama_env,
        "mlx_speculative_command": " ".join(mlx_cmd),
        "tips": tips,
    }


def apply_ollama_launchctl_env(hw: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Applies Flash Attention + Q8_0 KV cache + Keep-Alive via macOS launchctl setenv."""
    if sys.platform != "darwin":
        return {"ok": False, "error": "launchctl tuning is macOS-only."}
    plan = build_tuning_plan(hw)
    applied = {}
    errors = []
    for k, v in plan["ollama_env"].items():
        r = subprocess.run(["launchctl", "setenv", k, v], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            applied[k] = v
        else:
            errors.append(f"{k}: {(r.stderr or r.stdout).strip()}")
    return {
        "ok": len(errors) == 0,
        "applied": applied,
        "errors": errors,
        "note": "Restart Ollama (menu bar → Quit → reopen) for launchctl env vars to take effect.",
    }
