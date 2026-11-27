"""
Bridge between Xod Desktop and the standalone open-source `packages/xod-infra` package.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("xod.infra_bridge")

_PKG_ROOT = Path(__file__).resolve().parents[2] / "packages" / "xod-infra"


def _ensure_importable() -> bool:
    if _PKG_ROOT.is_dir() and str(_PKG_ROOT) not in sys.path:
        sys.path.insert(0, str(_PKG_ROOT))
    try:
        import xod_infra  # noqa: F401
        return True
    except Exception as e:
        logger.debug("xod-infra package unavailable: %s", e)
        return False


def _gateway_running(port: int = 8790) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=0.6) as r:
            return r.status == 200
    except Exception:
        return False


def status() -> Dict[str, Any]:
    if not _ensure_importable():
        return {"available": False, "error": "packages/xod-infra not found"}
    from xod_infra.hardware import detect_hardware
    from xod_infra.tuner import build_tuning_plan

    hw = detect_hardware()
    plan = build_tuning_plan(hw)
    gw_up = _gateway_running(8790)
    return {
        "available": True,
        "gateway_running": gw_up,
        "gateway_url": "http://127.0.0.1:8790/v1",
        "dashboard_url": "http://127.0.0.1:8790",
        "hardware": hw,
        "ollama_env": plan["ollama_env"],
        "mlx_speculative_command": plan["mlx_speculative_command"],
        "tips": plan["tips"],
    }


def tune_ollama() -> Dict[str, Any]:
    if not _ensure_importable():
        return {"ok": False, "error": "packages/xod-infra not found"}
    from xod_infra.tuner import apply_ollama_launchctl_env
    return apply_ollama_launchctl_env()


async def benchmark(model: str = "") -> Dict[str, Any]:
    if not _ensure_importable():
        return {"ok": False, "error": "packages/xod-infra not found"}
    from xod_infra.bench import benchmark_endpoint
    from xod_infra.hardware import detect_hardware

    hw = detect_hardware()
    b = hw["backends"]
    base = "http://127.0.0.1:11434/v1"
    for name in ("mlx", "lmstudio", "ollama"):
        if b.get(name, {}).get("running"):
            base = b[name]["url"]
            break
    return await benchmark_endpoint(base, model=model)


def start_gateway(port: int = 8790) -> Dict[str, Any]:
    if not _ensure_importable():
        return {"ok": False, "error": "packages/xod-infra not found"}
    if _gateway_running(port):
        return {
            "ok": True,
            "already_running": True,
            "gateway_url": f"http://127.0.0.1:{port}/v1",
            "dashboard_url": f"http://127.0.0.1:{port}",
        }
    env = dict(**__import__("os").environ)
    pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{_PKG_ROOT}:{pp}" if pp else str(_PKG_ROOT)
    kwargs: Dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
        "start_new_session": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([sys.executable, "-m", "xod_infra.cli", "serve", "--port", str(port)], **kwargs)
    return {
        "ok": True,
        "started": True,
        "gateway_url": f"http://127.0.0.1:{port}/v1",
        "dashboard_url": f"http://127.0.0.1:{port}",
    }
