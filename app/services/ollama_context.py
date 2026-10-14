"""
Gives Ollama models enough context for Xod's prompts.

Ollama's OpenAI-compatible endpoint (/v1, which Xod uses) ignores num_ctx in the request and loads
every model with its small default (4096 tokens), cutting longer prompts from the front without an
error: the system prompt, tool list and early chat simply vanish. So for each model Xod makes a
variant with a bigger num_ctx (Ollama shares the weights, so it costs no disk and takes well under
a second) and sends requests to that instead. The size is the largest that fits the memory this
computer gives models (services/machine.py), never more than the model was trained on.
"""
import asyncio
import logging
import re
from typing import Dict, Optional

import httpx

logger = logging.getLogger("xod.ollama")

PREFIX = "xod/"                 # variants: xod/<model>:ctx16k (shown in `ollama list`; Xod hides them)
SIZES = (32768, 16384, 8192)    # tried largest first

_variants: Dict[str, str] = {}  # base model -> the model name to send requests to
_locks: Dict[str, asyncio.Lock] = {}


def is_variant(name: str) -> bool:
    return name.startswith(PREFIX)


def _same(model: str, tag: Dict) -> bool:
    """Ollama reads "llama3.1" as "llama3.1:latest"."""
    full = model if ":" in model.split("/")[-1] else f"{model}:latest"
    return full in (tag.get("name"), tag.get("model"))


def _native(base_url: str) -> str:
    """http://localhost:11434/v1 -> http://localhost:11434"""
    return re.sub(r"/v1/?$", "", base_url.rstrip("/"))


def memory_budget_gb() -> float:
    """How much memory a model and its context can use on this computer (services/machine.py: any GPU vendor)."""
    from app.services import machine
    return machine.ai_memory_gb()


def pick_context(info: Dict, file_gb: float, budget_gb: float) -> int:
    """The largest of SIZES whose weights + KV cache fit the budget, capped at the trained length.
    KV cache estimate is fp16 (Ollama's default); with flash attention it's smaller, so this errs safe."""
    arch = info.get("general.architecture", "")
    trained = int(info.get(f"{arch}.context_length") or 8192)
    layers = int(info.get(f"{arch}.block_count") or 32)
    heads = int(info.get(f"{arch}.attention.head_count") or 32)
    kv_heads = int(info.get(f"{arch}.attention.head_count_kv") or heads)
    head_dim = int(info.get(f"{arch}.attention.key_length") or (int(info.get(f"{arch}.embedding_length") or 4096) // heads))
    kv_gb_per_token = layers * kv_heads * head_dim * 2 * 2 / 1024 ** 3   # keys + values, 2 bytes each
    for size in SIZES:
        if size <= trained and file_gb + size * kv_gb_per_token <= budget_gb:
            return size
    return min(SIZES[-1], trained)   # doesn't fit the GPU even at 8k: Ollama spills to the CPU, slow but correct


async def retry_after_error(payload: Dict, info: Dict, status_code: int, body: str) -> bool:
    """Fixes what an Ollama error says can be fixed, and returns True to send the request again:
      - our long-context copy was deleted outside Xod (`ollama rm`): make it again
      - the model can't call tools (Ollama refuses the whole request): send it without them; the model answers in text
    Each fix changes the payload, so a second failure of the same kind isn't retried again."""
    if info.get("provider") != "ollama":
        return False
    low = (body or "").lower()
    if status_code == 404 and "not found" in low and is_variant(payload.get("model", "")):
        _variants.pop(info["model"], None)
        payload["model"] = await long_context_model(info["base_url"], info["model"])
        logger.warning(f"Ollama: {info['model']}'s long-context copy was gone; now using {payload['model']}")
        return True
    if status_code == 400 and "does not support tools" in low and payload.get("tools"):
        payload.pop("tools", None)
        payload.pop("tool_choice", None)
        logger.warning(f"Ollama: {info['model']} can't call tools; asking it without them")
        return True
    return False


def friendly_error(info: Dict, status_code: Optional[int] = None, body: str = "") -> Optional[str]:
    """What to tell the person when Ollama fails in a way they can fix. None: not an Ollama problem we recognise.
    status_code None means Ollama couldn't be reached at all."""
    if info.get("provider") != "ollama":
        return None
    if status_code is None:
        return (f"Ollama isn't answering at {_native(info['base_url'])}. Start Ollama (Start menu, or Tools → Setup & AI model) "
                "and try again.")
    if status_code == 404 and "not found" in (body or "").lower():
        return (f"{info['model']} isn't installed in Ollama. Open Tools → Setup & AI model to download it or pick another model.")
    return None


async def status(base_url: str, model: str) -> Dict:
    """For the header: is Ollama answering, and is the chosen model installed?"""
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            tags = (await c.get(f"{_native(base_url)}/api/tags")).json().get("models", [])
    except Exception:
        return {"cloud": False, "provider": "ollama", "model": model, "ready": False, "problem": "Ollama isn't running"}
    installed = any(_same(model, t) for t in tags)
    return {"cloud": False, "provider": "ollama", "model": model, "ready": installed,
            "problem": None if installed else f"{model} isn't installed in Ollama"}


async def long_context_model(base_url: str, model: str) -> str:
    """The model name to send to Ollama for `model`: its long-context variant, made on first use.
    Falls back to `model` itself if anything goes wrong, so a chat never fails because of this."""
    if is_variant(model):
        return model
    if model in _variants:
        return _variants[model]
    async with _locks.setdefault(model, asyncio.Lock()):   # requests arriving together make it once
        if model in _variants:
            return _variants[model]
        return await _make_variant(base_url, model)


async def _make_variant(base_url: str, model: str) -> str:
    root = _native(base_url)
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            show = (await c.post(f"{root}/api/show", json={"model": model})).json()
            if "error" in show:
                return model                       # not installed: let the chat request report it
            m = re.search(r"^num_ctx\s+(\d+)", show.get("parameters") or "", re.M)
            if m and int(m.group(1)) >= SIZES[-1]:
                _variants[model] = model           # someone already gave it a big context: respect that
                return model
            tags = (await c.get(f"{root}/api/tags")).json().get("models", [])
            size = next((t.get("size", 0) for t in tags if _same(model, t)), 0)
            ctx = pick_context(show.get("model_info") or {}, size / 1024 ** 3, memory_budget_gb())
            slug = re.sub(r"[^a-z0-9._-]+", "-", model.lower()).strip("-")
            variant = f"{PREFIX}{slug}:ctx{ctx // 1024}k"
            r = await c.post(f"{root}/api/create", timeout=60,
                             json={"model": variant, "from": model, "parameters": {"num_ctx": ctx}, "stream": False})
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        logger.info(f"Ollama: using {variant} ({ctx} tokens of context) for {model}")
        _variants[model] = variant
        return variant
    except Exception as e:
        logger.warning(f"Ollama: couldn't make a long-context variant of {model} ({e}); its default context may cut long prompts.")
        return model
