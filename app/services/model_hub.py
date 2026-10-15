"""
Every model Xod can use, in one list, and what each one is for.

  all_models()   models on every server: this computer (Ollama, LM Studio, llama.cpp…), servers you added on your
                 network or online (services/model_sources.py), cloud providers with a key, and the built-in engine.
                 Each with what it can do (call tools, see, think, context length), where it runs, its size and
                 whether it fits, and its score on this computer (services/model_scores.py) if it's been tested.
  roles          the model for each job: chat (everything), seeing (services/vision.py), memory search (fixed:
                 changing it would make what's already indexed unreadable). Swapping is one call.
  catalog()      models to get, by job: chat ones (ollama_hub.CATALOG) and seeing ones, marked by fit
  search_hf()    GGUF models on Hugging Face by name, most downloaded first; Ollama downloads them (hf.co/…)
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("xod.models")

VISION_KEY = "vision_model"       # storage setting: the Ollama model used for seeing ("" = Xod picks)

VISION_CATALOG: List[Dict[str, Any]] = [
    {"name": "qwen3-vl:4b", "label": "Qwen3-VL 4B", "size_gb": 3.1, "blurb": "Reads screens and documents well; small enough to sit next to a chat model."},
    {"name": "qwen3-vl:8b", "label": "Qwen3-VL 8B", "size_gb": 5.7, "blurb": "Sharper on busy pages and small print. For 20 GB+ of AI memory."},
    {"name": "gemma3:4b", "label": "Gemma 3 4B", "size_gb": 3.3, "blurb": "Google's small model that also sees. Good at photos."},
    {"name": "gemma3:12b", "label": "Gemma 3 12B", "size_gb": 8.1, "blurb": "Sees and chats; can be the only model on a 16 GB GPU."},
]
CLOUD_DEFAULTS = {"anthropic": "claude-opus-5-5", "openai": "gpt-4o", "gemini": "gemini-2.5-flash", "groq": "llama-3.3-70b-versatile",
                  "openrouter": "meta-llama/llama-3.3-70b-instruct", "fireworks": "accounts/fireworks/models/glm-5p3-flash"}
CLOUD_SEES = {"anthropic", "openai", "gemini", "openrouter"}


def key(provider: str, model: str) -> str:
    return f"{provider}:{model}"


async def all_models() -> Dict[str, Any]:
    from app.config import settings
    from app.services import autoconnect, machine, model_scores, model_sources, ollama_hub, vision
    pc = await asyncio.to_thread(machine.profile)
    budget = pc["ai_memory_gb"]
    servers, added = await asyncio.gather(autoconnect.discover(), model_sources.discover())
    ollama_caps: Dict[str, Dict[str, Any]] = {}
    if any(s["provider"] == "ollama" for s in servers):
        try:
            ollama_caps = {m["name"]: m for m in await ollama_hub.installed()}
        except Exception:
            pass
    lms = {}
    if any(s["provider"] == "lm_studio" for s in servers):
        try:
            from app.services.llm import llm_service
            lms = {m["key"]: m for m in (await llm_service.get_lmstudio_status()).get("models", [])}
        except Exception:
            pass
    scores = model_scores.all_scores()
    out: List[Dict[str, Any]] = []
    for s in servers + added:
        where = s.get("where") or "this computer"
        for m in s["models"]:
            mid = m["id"]
            row = {"provider": s["provider"], "server": s["name"], "base_url": s["base_url"], "model": mid, "where": where,
                   "size_gb": m.get("size_gb"), "tools": m.get("tools"), "vision": None, "thinking": None, "context": None,
                   "online": s.get("online", True)}
            if s["provider"] == "ollama" and mid in ollama_caps:
                c = ollama_caps[mid]
                row.update(vision=c["vision"], thinking=c["thinking"], context=c.get("context"))
            if s["provider"] == "lm_studio" and mid in lms:
                c = lms[mid]
                row.update(vision=c.get("type") == "vlm", context=c.get("max_context_length"))
            out.append(row)
    for prov, field in (("anthropic", "anthropic_api_key"), ("openai", "openai_api_key"), ("gemini", "gemini_api_key"),
                        ("groq", "groq_api_key"), ("openrouter", "openrouter_api_key"), ("fireworks", "fireworks_api_key")):
        if getattr(settings, field, ""):
            mid = settings.llm_model if settings.llm_provider == prov else CLOUD_DEFAULTS[prov]
            out.append({"provider": prov, "server": prov.title(), "base_url": "", "model": mid, "where": "online", "size_gb": None,
                        "tools": True, "vision": prov in CLOUD_SEES, "thinking": None, "context": None, "online": True})
    seeing = await vision.model()
    for r in out:
        size = r.get("size_gb")
        r["fits"] = None if not size or r["where"] != "this computer" else size + 2.5 <= budget
        r["score"] = scores.get(key(r["provider"], r["model"]))
        r["in_use"] = [j for j, on in (("chat", r["provider"] == settings.llm_provider and _same(r["model"], settings.llm_model)),
                                       ("seeing", r["provider"] == "ollama" and r["model"] == seeing)) if on]
    out.sort(key=lambda r: (not r["in_use"], r["where"] != "this computer", -((r["score"] or {}).get("score") or 0), r["model"]))
    return {"models": out, "roles": await roles(), "machine": {"gpu": pc.get("gpu"), "ai_memory_gb": budget, "runs_on": pc.get("runs_on")},
            "sources": model_sources.listing()}


def _same(a: str, b: str) -> bool:
    a, b = (a or "").lower(), (b or "").lower()
    return a == b or a == f"{b}:latest" or b == f"{a}:latest"


async def roles() -> Dict[str, Any]:
    from app.config import settings
    from app.services import vision
    from app.services.llm import PROVIDER_NAMES
    from app.services.storage import get_setting
    sees = await vision.main_sees()
    helper = await vision.model()
    return {
        "chat": {"provider": settings.llm_provider, "model": settings.llm_model,
                 "server": PROVIDER_NAMES.get(settings.llm_provider, settings.llm_provider)},
        "seeing": {"model": None if sees else helper, "chosen": get_setting(VISION_KEY, "") or None,
                   "how": "the chat model sees" if sees else f"{helper} describes images for the chat model" if helper
                   else "nothing can see images yet"},
        "memory": {"model": "nomic-embed-text", "fixed": True,
                   "why": "Xod's memory is indexed with this model; another would make what's already saved unsearchable."},
    }


def use_for(job: str, provider: str, model: str, base_url: str = "") -> Dict[str, Any]:
    """Swaps the model for a job: "chat" (Xod's model for everything) or "seeing"."""
    if job == "chat":
        from app.services import setup_wizard
        return setup_wizard.choose(provider, model, base_url, how="you")
    if job == "seeing":
        if provider != "ollama":
            return {"success": False, "error": "For now the seeing model runs in Ollama."}
        from app.services import vision
        from app.services.storage import set_setting
        set_setting(VISION_KEY, model or "")
        vision.forget()
        return {"success": True, "job": "seeing", "model": model}
    return {"success": False, "error": f"Unknown job {job}."}


async def catalog() -> Dict[str, Any]:
    """Models to get, by job, each marked by whether it fits this computer."""
    from app.services import ollama_hub
    ov = await ollama_hub.overview()
    budget = ov["budget_gb"]
    have = {m["name"] for m in ov["models"]}
    seeing = [{**m, "fits": m["size_gb"] + 2.5 <= budget, "installed": m["name"] in have, "pull": ollama_hub.pull_status(m["name"]),
               "job": "seeing"} for m in VISION_CATALOG]
    return {"chat": [{**m, "job": "chat"} for m in ov["catalog"]], "seeing": seeing, "running": ov["running"],
            "budget_gb": budget, "can_install": ov["can_install"]}


async def search_hf(query: str, limit: int = 20) -> Dict[str, Any]:
    """GGUF models on Hugging Face matching the words, most downloaded first."""
    q = (query or "").strip()
    if len(q) < 2:
        return {"success": False, "error": "Type a model name, like qwen or llama."}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://huggingface.co/api/models", params={"search": q, "filter": "gguf", "sort": "downloads",
                                                                         "direction": -1, "limit": limit})
            r.raise_for_status()
            rows = r.json()
    except Exception as e:
        return {"success": False, "error": f"Couldn't search Hugging Face ({type(e).__name__}). Are you online?"}
    return {"success": True, "results": [
        {"repo": m["id"], "downloads": m.get("downloads", 0), "likes": m.get("likes", 0),
         "sees": m.get("pipeline_tag") == "image-text-to-text" or "vision" in (m.get("tags") or []),
         "pull": f"hf.co/{m['id']}"} for m in rows if m.get("id")]}
