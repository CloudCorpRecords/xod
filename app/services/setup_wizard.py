"""
First-run setup: checks the Mac, finds an AI model Xod can use, and gets one if there isn't any.

Order of preference shown to the user:
  1. An API key you already saved (a cloud model: nothing to download, no local model running)
  2. A model already running in LM Studio or Ollama (nothing to download)
  3. Xod's built-in engine with the model recommended for this Mac's memory (one download)
A cloud key can be added any time in Settings; while Xod still picks the model itself, a saved key wins (prefer_key()).
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
    cloud = saved_keys()
    return {
        "complete": is_complete(),
        "show_on_start": not is_complete() and not _has_chats(),
        "mac": mac,
        "current": {"provider": settings.llm_provider, "model": settings.llm_model},
        "lm_studio": lm, "ollama": ol,
        "builtin": builtin, "recommended": rec["repo"],
        "cloud_keys": cloud,
        "cloud": [key_choice(p) for p in cloud],
    }


KEY_ORDER = ("anthropic", "openai", "gemini", "openrouter", "groq", "fireworks")


def saved_keys() -> List[str]:
    """Cloud providers with an API key saved, best first."""
    from app.config import settings
    return [p for p in KEY_ORDER if (getattr(settings, f"{p}_api_key", "") or "").strip()]


def key_choice(provider: str) -> Dict[str, str]:
    from app.services.llm import PROVIDER_NAMES
    from app.services.model_hub import CLOUD_DEFAULTS
    model = CLOUD_DEFAULTS[provider]
    return {"provider": provider, "model": model, "label": f"{PROVIDER_NAMES.get(provider, provider)}: {model.split('/')[-1]}"}


def prefer_key() -> Dict[str, Any]:
    """With an API key saved, use it instead of a local model, as long as the model is still Xod's own pick (a new
    install, or one autoconnect chose) and you haven't set local-first routing (cloud only for hard steps). A model
    you picked yourself is never changed."""
    from app.config import settings
    from app.services.llm import CLOUD_PROVIDERS
    from app.services.storage import get_setting
    from app.services import llm_routing
    keys = saved_keys()
    if not keys or settings.llm_provider in CLOUD_PROVIDERS:
        return {"switched": False}
    never_chosen = not get_setting("llm_provider", "")
    if not (never_chosen or chosen_by() == "auto") or llm_routing.settings()["mode"] != "off":
        return {"switched": False}
    c = key_choice(keys[0])
    res = choose(c["provider"], c["model"], how="auto")
    return {"switched": bool(res.get("success")), **c}


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
    from app.services.local_servers import after_switch
    after_switch(provider)        # not the built-in engine any more: free its memory now
    return {"success": True, "provider": provider, "model": model}


async def quick() -> Dict[str, Any]:
    """Everything the model switcher at the top of the page offers: online models with a saved key, models in Ollama
    and LM Studio when they're running, built-in models that are downloaded. Each says whether it's the one in use."""
    from app.config import settings
    from app.services import secret_store
    from app.services.llm import PROVIDER_NAMES
    from app.services.model_hub import CLOUD_DEFAULTS
    s = await status()
    cur_p, cur_m = settings.llm_provider, settings.llm_model
    out: List[Dict[str, Any]] = []

    def add(group: str, provider: str, model: str, label: str, **extra):
        out.append({"group": group, "provider": provider, "model": model, "label": label,
                    "current": provider == cur_p and (model == cur_m or provider in CLOUD_DEFAULTS), **extra})

    for p in s["cloud_keys"]:
        m = cur_m if p == cur_p and cur_m else CLOUD_DEFAULTS[p]
        add("Online", p, m, f"{PROVIDER_NAMES.get(p, p)} · {m.split('/')[-1]}")
    for m in s["ollama"]["models"]:
        add("Ollama", "ollama", m, m)
    for m in s["lm_studio"]["models"]:
        add("LM Studio", "lm_studio", m, m.split("/")[-1], loaded=m == s["lm_studio"].get("loaded"))
    for m in s["builtin"]:
        if m["downloaded"]:
            add("Built-in", "xod", m["repo"], m.get("label") or m["repo"].split("/")[-1])
    if not any(c["current"] for c in out):     # in use but not offered above (its key or server is missing): still shown
        add("In use", cur_p, cur_m, f"{PROVIDER_NAMES.get(cur_p, cur_p)} · {str(cur_m).split('/')[-1]}", missing=True)
    return {"choices": out, "keys": secret_store.health(), "lm_studio_online": s["lm_studio"]["online"],
            "ollama_online": s["ollama"]["online"], "chosen_by": chosen_by()}


CHOSEN_BY_KEY = "model_chosen_by"


def chosen_by() -> str:
    """ "you" or "auto". Settings from before this existed count as the person's own."""
    from app.services.storage import get_setting
    return get_setting(CHOSEN_BY_KEY, "") or "you"


def complete() -> Dict[str, Any]:
    from app.services.storage import set_setting
    set_setting(SETUP_KEY, "1")
    return {"success": True}
