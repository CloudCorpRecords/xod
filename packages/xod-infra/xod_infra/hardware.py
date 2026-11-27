"""
Apple Silicon hardware & local-inference runtime inspector.

Reads macOS sysctl, GPU wired-memory limit, unified RAM, estimated memory
bandwidth, and available local inference backends (MLX, Ollama, LM Studio, llama.cpp).
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import urllib.request
from typing import Any, Dict, List


# Approximate unified memory bandwidth (GB/s) by Apple Silicon family.
_BANDWIDTH_GBPS = [
    ("M4 Max", 546),
    ("M4 Pro", 273),
    ("M4", 120),
    ("M3 Ultra", 800),
    ("M3 Max", 400),
    ("M3 Pro", 150),
    ("M3", 100),
    ("M2 Ultra", 800),
    ("M2 Max", 400),
    ("M2 Pro", 200),
    ("M2", 100),
    ("M1 Ultra", 800),
    ("M1 Max", 400),
    ("M1 Pro", 200),
    ("M1", 68),
]


def _sysctl(key: str) -> str:
    try:
        r = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _http_ok(url: str, timeout: float = 0.8) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _estimate_bandwidth(brand: str) -> int:
    for name, bw in _BANDWIDTH_GBPS:
        if name.lower() in brand.lower():
            return bw
    return 100 if "apple" in brand.lower() else 50


def detect_hardware() -> Dict[str, Any]:
    is_mac = platform.system() == "Darwin"
    arch = platform.machine().lower()
    brand = _sysctl("machdep.cpu.brand_string") if is_mac else platform.processor() or "Unknown CPU"
    mem_bytes = int(_sysctl("hw.memsize") or 0) if is_mac else 0
    ram_gb = round(mem_bytes / (1024 ** 3), 1) if mem_bytes else 0.0
    p_cores = int(_sysctl("hw.perflevel0.physicalcpu") or 0) if is_mac else (os.cpu_count() or 0)
    e_cores = int(_sysctl("hw.perflevel1.physicalcpu") or 0) if is_mac else 0
    wired_mb_raw = _sysctl("iogpu.wired_limit_mb") if is_mac else ""
    wired_limit_mb = int(wired_mb_raw) if wired_mb_raw.isdigit() else 0
    bw_gbps = _estimate_bandwidth(brand)

    # Check available local runtimes
    ollama_bin = shutil.which("ollama") or ""
    mlx_server_bin = shutil.which("mlx_lm.server") or ""
    llama_server_bin = shutil.which("llama-server") or ""

    # Check if Python mlx_lm is importable in current environment
    mlx_python = False
    try:
        import importlib.util
        mlx_python = importlib.util.find_spec("mlx_lm") is not None
    except Exception:
        pass

    ollama_running = _http_ok("http://127.0.0.1:11434/api/tags")
    lmstudio_running = _http_ok("http://127.0.0.1:1234/v1/models")
    mlx_running = _http_ok("http://127.0.0.1:8080/v1/models")

    # Recommended safe GPU budget (~72% of unified RAM on 16GB, ~78% on 32GB+)
    safe_gpu_gb = round(ram_gb * (0.72 if ram_gb <= 24 else 0.78), 1) if ram_gb else 8.0

    # Tiered model & speculative decoding pairings
    if ram_gb >= 64:
        tier = "studio_ultra"
        rec_target = "mlx-community/Qwen2.5-32B-Instruct-4bit"
        rec_draft = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
        rec_ctx = 32768
    elif ram_gb >= 32:
        tier = "pro_max"
        rec_target = "mlx-community/Qwen2.5-14B-Instruct-4bit"
        rec_draft = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
        rec_ctx = 16384
    elif ram_gb >= 16:
        tier = "pro_16gb"
        rec_target = "mlx-community/Qwen2.5-7B-Instruct-4bit"
        rec_draft = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"
        rec_ctx = 8192
    else:
        tier = "compact"
        rec_target = "mlx-community/Qwen2.5-3B-Instruct-4bit"
        rec_draft = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"
        rec_ctx = 4096

    # Theoretical peak 4-bit decode tok/s for a 7B model (~4.2 GB weights)
    est_7b_tps = round(min(bw_gbps / 4.5, bw_gbps * 0.85 / 4.2), 1)

    return {
        "os": platform.system(),
        "arch": arch,
        "apple_silicon": is_mac and arch == "arm64",
        "chip": brand,
        "ram_gb": ram_gb,
        "safe_gpu_gb": safe_gpu_gb,
        "p_cores": p_cores,
        "e_cores": e_cores,
        "wired_limit_mb": wired_limit_mb,
        "memory_bandwidth_gbps": bw_gbps,
        "estimated_7b_4bit_tps": est_7b_tps,
        "tier": tier,
        "backends": {
            "mlx": {"installed": bool(mlx_server_bin or mlx_python), "running": mlx_running, "url": "http://127.0.0.1:8080/v1"},
            "ollama": {"installed": bool(ollama_bin), "running": ollama_running, "url": "http://127.0.0.1:11434/v1"},
            "lmstudio": {"installed": os.path.isdir("/Applications/LM Studio.app"), "running": lmstudio_running, "url": "http://127.0.0.1:1234/v1"},
            "llama_cpp": {"installed": bool(llama_server_bin), "running": False, "url": "http://127.0.0.1:8081/v1"},
        },
        "recommended": {
            "target_model": rec_target,
            "draft_model": rec_draft,
            "speculative_decoding": True,
            "num_ctx": rec_ctx,
            "kv_cache_type": "q8_0",
            "flash_attention": True,
            "keep_alive": "30m",
        },
    }
