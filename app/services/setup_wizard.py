"""
First-run setup: checks the Mac, finds an AI model Xod can use, and gets one if there isn't any.

Order of preference shown to the user:
  1. A model already running in LM Studio or Ollama (nothing to download)
  2. Xod's built-in engine with the model recommended for this Mac's memory (one download)
  3. A cloud provider with an API key (optional, can be added any time in Settings)
"""
import os
import platform
import shutil
from typing import Any, Dict, List

import httpx

from app.paths import DATA_DIR

SETUP_KEY = "setup_complete"


def _mac() -> Dict[str, Any]:
    from app.services.local_engine import total_ram_gb
    ram = total_ram_gb()
    free = shutil.disk_usage(DATA_DIR).free / 1024 ** 3
    apple = platform.system() == "Darwin" and platform.machine() == "arm64"
    issues: List[str] = []
    if not apple:
        issues.append("On-device images, video, voice and the built-in model need a Mac with Apple silicon (M1 or newer).")
    if ram < 8:
        issues.append("Less than 8 GB of memory: use a cloud model, local models won't fit.")
    if free < 10:
        issues.append(f"Only {free:.0f} GB of disk space free; models need 3–16 GB.")
    return {"apple_silicon": apple, "ram_gb": ram, "disk_free_gb": round(free, 1),
            "macos": platform.mac_ver()[0] or platform.system(), "issues": issues}


async def _ollama() -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=1.5) as c:
            r = await c.get("http://localhost:11434/api/tags")
        models = [m["name"] for m in r.json().get("models", [])]
        return {"online": True, "models": models}
    except Exception:
        return {"online": False, "models": []}


async def _lm_studio() -> Dict[str, Any]:
    try:
        from app.services.llm import llm_service
        s = await llm_service.get_lmstudio_status()
        if not s or s.get("online") is False or "models" not in s:
            return {"online": False, "models": [], "loaded": None}
        chat = [m["key"] for m in s.get("models", []) if m.get("type") != "embedding" and "embed" not in (m.get("key") or "")]
        loaded = (s.get("loaded_model") or {}).get("key")
        return {"online": True, "models": chat, "loaded": loaded}
    except Exception:
        return {"online": False, "models": [], "loaded": None}


def is_complete() -> bool:
    from app.services.storage import get_setting
    return get_setting(SETUP_KEY, "") == "1"


def _has_chats() -> bool:
    try:
        from app.services.storage import list_sessions
        return bool(list_sessions())
    except Exception:
        return False


async def status() -> Dict[str, Any]:
    from app.config import settings
    from app.services.local_engine import CATALOG, is_downloaded, recommend, download_status
    mac = _mac()
    rec = recommend(mac["ram_gb"])
    builtin = [{**m, "downloaded": is_downloaded(m["repo"]), "recommended": m["id"] == rec["id"],
                "fits": m["min_ram_gb"] <= mac["ram_gb"], "download": download_status(m["repo"])} for m in CATALOG]
    lm, ol = await _lm_studio(), await _ollama()
    cloud = [p for p in ("anthropic", "openai", "gemini", "groq", "openrouter", "fireworks") if getattr(settings, f"{p}_api_key", "")]
    return {
        "complete": is_complete(),
        "show_on_start": not is_complete() and not _has_chats(),
        "mac": mac,
        "current": {"provider": settings.llm_provider, "model": settings.llm_model},
        "lm_studio": lm, "ollama": ol,
        "builtin": builtin, "recommended": rec["repo"],
        "cloud_keys": cloud,
    }


def choose(provider: str, model: str) -> Dict[str, Any]:
    from app.config import update_env_variable
    from app.services.local_engine import catalog_entry, is_downloaded
    allowed = {"xod", "lm_studio", "ollama", "anthropic", "openai", "gemini", "groq", "openrouter", "fireworks"}
    if provider not in allowed:
        return {"success": False, "error": f"Unknown provider {provider}"}
    if provider == "xod":
        if not catalog_entry(model):
            return {"success": False, "error": "Pick one of the built-in models."}
        if not is_downloaded(model):
            return {"success": False, "error": "That model isn't downloaded yet."}
    update_env_variable("LLM_PROVIDER", provider)
    update_env_variable("LLM_MODEL", model)
    if provider == "ollama":
        update_env_variable("LLM_BASE_URL", "http://localhost:11434/v1")
    elif provider == "lm_studio":
        update_env_variable("LLM_BASE_URL", "http://localhost:1234/v1")
    return {"success": True, "provider": provider, "model": model}


def complete() -> Dict[str, Any]:
    from app.services.storage import set_setting
    set_setting(SETUP_KEY, "1")
    return {"success": True}
