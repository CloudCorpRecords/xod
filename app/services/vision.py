"""
Eyes for models that can't see.

Most good local chat models (Qwen 2.5 14B, Llama 3.1…) read text only, so Xod's browser worked blind and an image
you attached went unanswered. A small vision model looks instead: it describes the screenshot or the picture in
words, and the chat model works from that.

  which model    the chat model itself if it can see; else a vision model already in Ollama; else one Xod fetches
                 (autoconnect.py: Qwen VL sized to the GPU, only with room on the disk)
  describe()     an image (data: URI or path) + what to look for → a short, factual description
"""
import base64
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("xod.vision")

# Small first: it runs next to the chat model. The first one Ollama's library has is used.
CANDIDATES_SMALL = ["qwen3-vl:4b", "qwen2.5vl:3b", "gemma3:4b"]
CANDIDATES_LARGE = ["qwen3-vl:8b", "qwen2.5vl:7b", "gemma3:12b"]

_cache: Dict[str, Any] = {"at": 0.0, "model": None, "main_sees": None, "key": ""}


def _root() -> str:
    from app.services.ollama_hub import root
    return root()


async def main_sees() -> Optional[bool]:
    """Can the chat model take images? True/False when Xod can tell, None when it can't (then it's tried)."""
    from app.services.llm import llm_service
    info = llm_service.get_endpoint_info()
    provider, model = info.get("provider"), info.get("model") or ""
    if provider in ("openai", "anthropic", "gemini", "openrouter", "fireworks", "groq"):
        return None
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            if provider == "ollama":
                caps = (await c.post(f"{_root()}/api/show", json={"model": model})).json().get("capabilities") or []
                return "vision" in caps
            if provider == "lm_studio":
                base = re.sub(r"/v1/?$", "", info.get("base_url") or "http://localhost:1234")
                for m in (await c.get(f"{base}/api/v0/models")).json().get("data", []):
                    if m.get("id") == model:
                        return m.get("type") == "vlm"
    except Exception:
        pass
    return None


async def model() -> Optional[str]:
    """A vision model in Ollama that can describe images (the smallest one), or None. Cached for a minute."""
    if time.time() - _cache["at"] < 60:
        return _cache["model"]
    found = None
    try:
        from app.services import ollama_hub
        from app.services.storage import get_setting
        seeing = [m for m in await ollama_hub.installed() if m.get("vision") and not m.get("embedding")]
        chosen = get_setting("vision_model", "")         # picked in the models panel (services/model_hub.py)
        if chosen and any(m["name"] == chosen for m in seeing):
            found = chosen
        elif seeing:
            found = min(seeing, key=lambda m: m.get("size_gb") or 99)["name"]
    except Exception:
        pass
    _cache.update(at=time.time(), model=found)
    return found


def forget() -> None:
    _cache["at"] = 0.0


async def needed() -> bool:
    """True when the chat model can't see (or can't be asked) and a vision model would help."""
    return (await main_sees()) is False


def _b64(image: str) -> Optional[str]:
    if image.startswith("data:"):
        return image.split(",", 1)[1] if "," in image else None
    if image.startswith(("http://", "https://")):
        return None
    try:
        with open(os.path.expanduser(image), "rb") as f:
            return base64.b64encode(f.read()).decode()
    except OSError:
        return None


async def describe(image: str, focus: str = "", timeout: float = 120) -> Optional[str]:
    """What the image shows, in words, from the vision model (None if there's none, or it failed)."""
    vm = await model()
    data = _b64(image or "")
    if not vm or not data:
        return None
    prompt = ("Describe this image precisely and factually for someone who can't see it. Read out any visible text "
              "exactly (headings, buttons, labels, error messages, prices, numbers). Say what's in front: dialogs, "
              "pop-ups, cookie banners, captchas. Don't guess about what isn't visible.")
    if focus:
        prompt += f"\nWhat matters most here: {focus[:500]}"
    body = {"model": vm, "stream": False, "keep_alive": "10m",
            "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": 600},
            "messages": [{"role": "user", "content": prompt, "images": [data]}]}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(f"{_root()}/api/chat", json=body)
            if r.status_code >= 500:
                # Usually no room next to the chat model on the GPU: let the others go (they load again when
                # next needed) and look once more.
                logger.info(f"vision model {vm}: {r.status_code} {r.text[:200]}; freeing the GPU and trying again")
                await _unload_others(c, vm)
                r = await c.post(f"{_root()}/api/chat", json=body)
            if r.status_code >= 400:
                logger.warning(f"vision model {vm}: {r.status_code} {r.text[:300]}")
                return None
            text = ((r.json().get("message") or {}).get("content") or "").strip()
            return text or None
    except Exception as e:
        logger.warning(f"vision model {vm}: {e}")
        return None


async def _unload_others(c: httpx.AsyncClient, keep: str) -> None:
    try:
        for m in (await c.get(f"{_root()}/api/ps")).json().get("models", []):
            if m.get("name") != keep and m.get("model") != keep:
                await c.post(f"{_root()}/api/generate", json={"model": m.get("name") or m.get("model"), "keep_alive": 0})
    except Exception as e:
        logger.debug(f"unloading: {e}")


async def as_text(text: str, images: List[str], focus: str = "") -> Optional[str]:
    """The text plus a description of each image, for a model that can't see. None if no vision model could help."""
    parts = []
    for i, img in enumerate(images, 1):
        d = await describe(img, focus or text)
        if not d:
            return None
        parts.append(f"IMAGE {i} (described by Xod's vision model, since the chat model can't see images):\n{d}")
    return f"{text}\n\n" + "\n\n".join(parts) if parts else None


async def pick_to_fetch(ai_memory_gb: float) -> Optional[Dict[str, Any]]:
    """The vision model to download for this computer: name and size, from what Ollama's library has."""
    from app.services import ollama_hub
    names = CANDIDATES_LARGE + CANDIDATES_SMALL if ai_memory_gb >= 20 else CANDIDATES_SMALL
    async with httpx.AsyncClient(timeout=8) as c:
        for n in names:
            try:
                size = await ollama_hub._registry_size_gb(c, n)
            except Exception:
                size = None
            if size:
                return {"name": n, "size_gb": size}
    return None
