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
    from app.services.local_engine import is_apple_silicon, total_ram_gb
    ram = total_ram_gb()
    free = shutil.disk_usage(DATA_DIR).free / 1024 ** 3
    apple = is_apple_silicon()
    issues: List[str] = []
    windows = platform.system() == "Windows"
    if windows:
        issues.append("On this PC Xod runs local models with Ollama (Xod can install it below) or LM Studio, or uses a cloud "
                      "model. On-device images, video and voice need a Mac with Apple silicon for now.")
    elif not apple:
        issues.append("On-device images, video, voice and the built-in model need a Mac with Apple silicon (M1 or newer).")
    if ram < 8:
        issues.append("Less than 8 GB of memory: use a cloud model, local models won't fit.")
    if free < 10:
        issues.append(f"Only {free:.0f} GB of disk space free; models need 3–16 GB.")
    os_name = f"macOS {platform.mac_ver()[0]}" if platform.mac_ver()[0] else f"{platform.system()} {platform.release()}".strip()
    return {"apple_silicon": apple, "windows": windows, "ram_gb": ram, "disk_free_gb": round(free, 1),
            "macos": platform.mac_ver()[0] or platform.system(), "os": os_name, "issues": issues}


async def _ollama() -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=1.5) as c:
            r = await c.get("http://localhost:11434/api/tags")
        from app.services.ollama_context import is_variant
        models = [m["name"] for m in r.json().get("models", []) if not is_variant(m["name"])]   # not Xod's own long-context copies
        return {"online": True, "models": models}
    except Exception:
        return {"online": False, "models": []}


async def _lm_studio() -> Dict[str, Any]:
    try:
        from app.services.llm import llm_service
        s = await llm_service.get_lmstudio_status()
        if not s or s.get("online") is False or "models" not in s:
            return {"online": False, "models": [], "loaded": None}
        chat_models = [m for m in s.get("models", []) if m.get("type") != "embedding" and "embed" not in (m.get("key") or "")]
        chat = [m["key"] for m in chat_models]
        loaded = (s.get("loaded_model") or {}).get("key")
        sizes = {m["key"]: m.get("size_gb") for m in chat_models}
        return {"online": True, "models": chat, "loaded": loaded, "sizes": sizes}
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
    # The built-in engine is MLX: Apple silicon only, so other computers don't see it.
    builtin = [{**m, "downloaded": is_downloaded(m["repo"]), "recommended": m["id"] == rec["id"],
                "fits": m["min_ram_gb"] <= mac["ram_gb"], "download": download_status(m["repo"])} for m in CATALOG] if mac["apple_silicon"] else []
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


def choose(provider: str, model: str, base_url: str = "", how: str = "you") -> Dict[str, Any]:
    """Switches Xod's model. base_url: where a "local" server is (autoconnect found it). how: "you" when the person
    picked it, "auto" when Xod did; Xod only changes a model on its own while the choice is still its own."""
    allowed = {"xod", "lm_studio", "ollama", "local", "custom", "anthropic", "openai", "gemini", "groq", "openrouter", "fireworks"}
    if provider not in allowed:
        return {"success": False, "error": f"Unknown provider {provider}"}
    if provider == "xod":
        # Before anything else: the built-in engine is Apple-silicon-only.
        from app.services.local_engine import INTEL_MESSAGE, is_apple_silicon
        if not is_apple_silicon():
            return {"success": False, "error": INTEL_MESSAGE}
    from app.config import update_env_variable
    from app.services.local_engine import catalog_entry, is_downloaded
    # Checked before anything is saved (a refused address used to leave the provider switched anyway).
    from app.services import model_sources
    if provider == "local" and not (base_url.startswith(("http://localhost", "http://127.0.0.1")) or model_sources.is_mine(base_url)):
        return {"success": False, "error": "A local server's address must be on this computer, or one you added under Model servers."}
    if provider == "custom" and not model_sources.is_mine(base_url):
        return {"success": False, "error": "Add that server under Model servers first."}
    if provider == "xod":
        if not catalog_entry(model):
            return {"success": False, "error": "Pick one of the built-in models."}
        if not is_downloaded(model):
            return {"success": False, "error": "That model isn't downloaded yet."}
    # Ollama's address before switching: the one in use if Ollama is already the provider, else OLLAMA_HOST or the default.
    from app.services.ollama_hub import root as ollama_root
    ollama_url = f"{ollama_root()}/v1"
    update_env_variable("LLM_PROVIDER", provider)
    update_env_variable("LLM_MODEL", model)
    if provider == "ollama":
        update_env_variable("LLM_BASE_URL", ollama_url)
    elif provider == "lm_studio":
        update_env_variable("LLM_BASE_URL", base_url or "http://localhost:1234/v1")
    elif provider in ("local", "custom"):
        update_env_variable("LLM_BASE_URL", base_url)
    from app.services.storage import set_setting
    set_setting(CHOSEN_BY_KEY, how)
    return {"success": True, "provider": provider, "model": model}


CHOSEN_BY_KEY = "model_chosen_by"


def chosen_by() -> str:
    """ "you" or "auto". Settings from before this existed count as the person's own."""
    from app.services.storage import get_setting
    return get_setting(CHOSEN_BY_KEY, "") or "you"


def complete() -> Dict[str, Any]:
    from app.services.storage import set_setting
    set_setting(SETUP_KEY, "1")
    return {"success": True}
