import re
import time
import asyncio
import contextlib
import contextvars
import json
import httpx
import logging
from typing import List, Dict, Any, Optional, AsyncGenerator, Tuple, Callable
from app.config import settings
from app.services import ollama_context

logger = logging.getLogger("llm")

# Local models are slow to produce the first token on big prompts; only the
# connect step should fail fast.
LLM_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3

# Reasoning models (Gemma 4, Qwen 3, DeepSeek R1...) spend part of max_tokens
# thinking before they answer. Structured JSON steps need room for both.
JSON_MIN_TOKENS = 1500


def parse_llm_json(text: str) -> Dict[str, Any]:
    """
    Best-effort extraction of a JSON object from model output.
    Handles ```json fences, prose before/after the object, trailing commas,
    and output that was cut off mid-object (salvages every complete field).
    Returns {} if nothing usable is found.
    """
    if not text:
        return {}
    start = text.find("{")
    if start == -1:
        return {}
    body = text[start:]

    # 1. First balanced object, ignoring braces inside strings.
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(body):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = re.sub(r",\s*([}\]])", r"\1", body[: i + 1])
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    break

    # 2. Truncated output: walk back to the last cleanly finished value and
    #    close whatever brackets are still open.
    for cut in range(len(body), 0, -1):
        if body[cut - 1] not in '}]"0123456789eluf':  # ends of values: } ] " digits true/false/null
            continue
        snippet = body[:cut]
        stack, in_str, esc = [], False, False
        for ch in snippet:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
        if in_str:
            continue
        candidate = re.sub(r",\s*$", "", snippet) + "".join(reversed(stack))
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                logger.warning("Recovered truncated JSON from model output (%d chars dropped)", len(body) - cut)
                return parsed
        except json.JSONDecodeError:
            continue
    return {}


CLOUD_PROVIDERS = ("openai", "gemini", "groq", "openrouter", "fireworks", "anthropic", "custom")   # custom: an online server you added
# Models on this computer. "local": any other OpenAI-compatible server here (llama.cpp, Jan, KoboldCpp, GPT4All…),
# found by services/autoconnect.py.
LOCAL_PROVIDERS = ("xod", "lm_studio", "ollama", "local")
ENDS_WITH_DONE = ("ollama", "lm_studio")   # local servers that always finish a stream with [DONE]
PROVIDER_NAMES = {"ollama": "Ollama", "lm_studio": "LM Studio", "xod": "The built-in model", "local": "The local model server", "openai": "OpenAI",
                  "gemini": "Gemini", "groq": "Groq", "openrouter": "OpenRouter", "fireworks": "Fireworks", "anthropic": "Claude",
                  "custom": "Your model server"}
CLOUD_THINK_HEADROOM = 2048      # tokens a cloud reasoning model may spend thinking before the answer even starts
_no_effort: set = set()          # (provider, model) that rejected reasoning_effort


# The built-in engine (mlx_lm.server) is never quiet for long while it works: it sends a keep-alive line every few
# seconds while it reads the prompt, then the answer as it writes it. After this long with nothing at all, Xod checks
# whether it's still at work (local_engine.responsive) and restarts it if not, instead of waiting out LLM_TIMEOUT.
XOD_QUIET = 45.0
XOD_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)   # waits are timed by _watched instead
ENGINE_RESTARTING = "The built-in model stopped answering, so I'm restarting it…"
ENGINE_RESTARTED = "The built-in model stopped answering, so I restarted it."
# Whoever runs a task can listen for those (the browser mission shows them): a function taking the text.
engine_notice: contextvars.ContextVar[Optional[Callable[[str], None]]] = contextvars.ContextVar("engine_notice", default=None)


class EngineStuck(Exception):
    """The built-in engine went quiet and isn't working on anything (internal: the caller restarts it)."""


class EngineUnresponsive(Exception):
    """The built-in engine stopped answering and a restart didn't help."""


def _notice(text: str) -> None:
    cb = engine_notice.get()
    if cb:
        try:
            cb(text)
        except Exception:
            pass


async def _watched(aw: Any, info: Dict[str, Any]) -> Any:
    """Awaits one wait of a request: the whole answer, a stream's first reply, or its next line. For the built-in
    engine, a quiet stretch of XOD_QUIET seconds raises EngineStuck unless the engine is visibly still at work
    (a long tool call, which it sends all at once, or another request it's serving first)."""
    if info["provider"] != "xod":
        return await aw
    from app.services.local_engine import local_engine
    task = asyncio.ensure_future(aw)
    quiet = 0.0
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=XOD_QUIET)
            if done:
                return task.result()
            quiet += XOD_QUIET
            if quiet >= LLM_TIMEOUT.read or not await local_engine.responsive():
                raise EngineStuck(f"no output for {quiet:.0f}s")
            logger.info(f"Built-in engine quiet for {quiet:.0f}s but still working; waiting")
    finally:
        if not task.done():
            task.cancel()


@contextlib.asynccontextmanager
async def _open_stream(client: httpx.AsyncClient, url: str, payload: Dict[str, Any], headers: Dict[str, str], info: Dict[str, Any]):
    """client.stream("POST", ...), with the wait for the first reply watched (see _watched)."""
    response = await _watched(client.send(client.build_request("POST", url, json=payload, headers=headers), stream=True), info)
    try:
        yield response
    finally:
        await response.aclose()


async def _lines(response: httpx.Response, info: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """response.aiter_lines(), with each wait watched (see _watched)."""
    it = response.aiter_lines()
    while True:
        try:
            line = await _watched(it.__anext__(), info)
        except StopAsyncIteration:
            return
        yield line


async def _restart_engine(info: Dict[str, Any], why: str, seen_start: Optional[float]) -> None:
    """Restarts the built-in engine after it stopped answering; EngineUnresponsive if it won't come back."""
    from app.services.local_engine import local_engine
    _notice(ENGINE_RESTARTING)
    try:
        await local_engine.restart(info["model"], why, seen_start)
    except Exception as e:
        raise EngineUnresponsive(f"The built-in model stopped answering, and restarting it didn't work ({e}).")
    _notice(ENGINE_RESTARTED)


def _gave_up() -> EngineUnresponsive:
    return EngineUnresponsive("The built-in model stopped answering, and it still didn't answer after I restarted it. "
                              "Quitting other apps to free memory, or picking a smaller model in Settings, may help.")


async def _after_stall(info: Dict[str, Any], e: Exception, engine_start: Optional[float], restarted: bool, said: bool) -> Optional[str]:
    """A stream from the built-in engine stalled. Returns None after restarting it (retry the request), or the error
    to show: it was already restarted once, it won't restart, or half an answer is out (a retry would repeat it)."""
    if restarted:
        return str(_gave_up())
    try:
        await _restart_engine(info, str(e), engine_start)
    except EngineUnresponsive as gone:
        return str(gone)
    if said:
        return "The built-in model stopped answering in the middle of the answer, so I restarted it. Ask again for the rest."
    return None


def _quiet_thinking(payload: Dict[str, Any], info: Dict[str, Any], think: Optional[bool]) -> None:
    """think=False: as little thinking as the model allows, so the answer comes quickly and fits its budget.
    Local servers can switch it off. Fireworks can only turn it down (reasoning_effort low), and a reasoning model
    there still thinks first, which used to use up a short answer's whole budget (an answer that was all thinking):
    cloud requests get room for that on top."""
    if think is not False:
        return
    p = info["provider"]
    if p in ("lm_studio", "ollama"):
        payload["reasoning_effort"] = "none"
    elif p in ("xod", "local"):     # mlx_lm.server, llama.cpp and most other servers read the chat template's switch
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    elif p in CLOUD_PROVIDERS:
        if p == "fireworks" and (p, info["model"]) not in _no_effort:
            payload["reasoning_effort"] = "low"
        payload["max_tokens"] = int(payload.get("max_tokens") or 1024) + CLOUD_THINK_HEADROOM


def _effort_rejected(payload: Dict[str, Any], info: Dict[str, Any], status: int, body: str) -> bool:
    """A model that doesn't take reasoning_effort: remember that, drop it, and the caller retries."""
    if status == 400 and "reasoning_effort" in payload and "reason" in (body or "").lower():
        _no_effort.add((info["provider"], info["model"]))
        payload.pop("reasoning_effort", None)
        return True
    return False


def plain_error(info: Dict[str, Any], status: Optional[int] = None, body: str = "") -> str:
    """What went wrong with the model, in words a person can act on (not "LLM error 401: {json}")."""
    who = PROVIDER_NAMES.get(info.get("provider", ""), info.get("provider") or "The model")
    if status is None:
        if info.get("provider") in CLOUD_PROVIDERS:
            return f"Xod couldn't reach {who}. Check the internet connection, or pick another model from the top bar."
        return f"{who} isn't running on this computer. Open it, or pick another model from the top bar."
    if status in (401, 403):
        return (f"{who} refused the API key (it may be wrong or expired). Paste a new one in Settings, or pick another "
                "model from the top bar.")
    if status in (402, 429):
        return f"{who} says the account is over its limit or out of credit right now. Try again later, or pick another model."
    try:
        said = json.loads(body).get("error")
        said = said.get("message") if isinstance(said, dict) else said
    except Exception:
        said = None
    return f"{who} answered with an error ({status}): {str(said or body).strip()[:200]}"


class LLMService:
    def __init__(self):
        pass

    def _cloud_key(self, provider: str, key_profile_id: Optional[str] = None) -> Tuple[str, Optional[str]]:
        """The API key a cloud call should use, and the name of the key profile it belongs to.

        Key profiles (services/key_profiles.py): the chosen profile when one is named (Detect
        Models against a specific key), otherwise the active profile. A provider with no
        profile metadata resolves to its single saved key, which is the Default profile, so
        behavior is unchanged for anyone who never made a second profile."""
        try:
            from app.services import key_profiles
            if key_profile_id:
                name = next((p["name"] for p in key_profiles.list_profiles(provider)["profiles"]
                             if p["id"] == key_profile_id), None)
                return key_profiles.key_for_profile(provider, key_profile_id), name
            value, name = key_profiles.active_key(provider)
            if value or key_profiles.has_metadata(provider):
                return value, name
        except Exception:
            pass
        return (getattr(settings, f"{provider}_api_key", "") or ""), "Default"

    def _tier_key_ready(self, provider: str) -> bool:
        """A usable key for this provider: the single saved key, or any key profile holding one."""
        if (getattr(settings, f"{provider}_api_key", "") or "").strip():
            return True
        try:
            from app.services import key_profiles
            return key_profiles.provider_has_key(provider)
        except Exception:
            return False

    def _build_endpoint(self, provider: str, model: Optional[str] = None,
                        key_profile_id: Optional[str] = None) -> Dict[str, Any]:
        base_url = settings.llm_base_url or "http://localhost:1234/v1"
        api_key = "local"
        key_profile_name: Optional[str] = None
        m = model or settings.llm_model

        if provider == "lm_studio":
            base_url = settings.llm_base_url or "http://localhost:1234/v1"
            api_key = "lm-studio"
            cand = model or settings.llm_model
            if not cand or cand.startswith("accounts/") or cand.startswith("gpt-") or cand.startswith("claude-") or "fireworks" in cand:
                m = "google/gemma-4-12b-qat"
            else:
                m = cand
        elif provider == "xod":
            from app.services.local_engine import local_engine, recommend
            base_url = local_engine.base_url
            api_key = "local"
            cand = model or settings.llm_model or ""
            m = cand if cand.startswith("mlx-community/") or "/" in cand else recommend()["repo"]
        elif provider == "ollama":
            base_url = settings.llm_base_url or "http://localhost:11434/v1"
            api_key = "ollama"
            m = model or settings.llm_model or "llama3.1"
        elif provider in ("local", "custom"):
            # A server on this computer, or one you added (services/model_sources.py) with its key, if it has one.
            from app.services import model_sources
            base_url = settings.llm_base_url or "http://localhost:8080/v1"
            api_key = model_sources.key_for(base_url) or ("local" if provider == "local" else "missing")
            m = model or settings.llm_model or "default"
        elif provider == "openai":
            base_url = "https://api.openai.com/v1"
            api_key, key_profile_name = self._cloud_key("openai", key_profile_id)
            api_key = api_key or "missing"
            m = model or "gpt-4o"
        elif provider == "gemini":
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
            api_key, key_profile_name = self._cloud_key("gemini", key_profile_id)
            api_key = api_key or "missing"
            m = model or "gemini-2.5-flash"
        elif provider == "groq":
            base_url = "https://api.groq.com/openai/v1"
            api_key, key_profile_name = self._cloud_key("groq", key_profile_id)
            api_key = api_key or "missing"
            m = model or "llama-3.3-70b-versatile"
        elif provider == "openrouter":
            base_url = "https://openrouter.ai/api/v1"
            api_key, key_profile_name = self._cloud_key("openrouter", key_profile_id)
            api_key = api_key or "missing"
            m = model or "meta-llama/llama-3.3-70b-instruct"
        elif provider == "anthropic":
            # Anthropic's OpenAI-compatible endpoint (chat/completions with a Bearer key).
            base_url = "https://api.anthropic.com/v1"
            api_key, key_profile_name = self._cloud_key("anthropic", key_profile_id)
            api_key = api_key or "missing"
            m = model or "claude-opus-5-5"
        elif provider == "fireworks":
            base_url = settings.llm_base_url if (settings.llm_base_url and "fireworks" in settings.llm_base_url) else "https://api.fireworks.ai/inference/v1"
            api_key, key_profile_name = self._cloud_key("fireworks", key_profile_id)
            api_key = api_key or "missing"
            m = model or settings.llm_model
            if not m or m == "google/gemma-4-12b-qat":
                m = "accounts/fireworks/models/glm-5p3-flash"
            elif not m.startswith("accounts/"):
                m = f"accounts/fireworks/models/{m}"

        info = {
            "provider": provider,
            "base_url": base_url.rstrip("/"),
            "model": m,
            "api_key": api_key
        }
        if key_profile_name:
            info["key_profile"] = key_profile_name
        return info

    def get_endpoint_info(self) -> Dict[str, Any]:
        return self._build_endpoint(settings.llm_provider, settings.llm_model)

    def get_available_tiers(self) -> Dict[str, Any]:
        """Returns readiness status for both local and cloud model tiers."""
        cloud_options = []
        if self._tier_key_ready("fireworks"):
            fw_model = settings.llm_model if (settings.llm_provider == "fireworks" and settings.llm_model) else "accounts/fireworks/models/glm-5p3-flash"
            cloud_options.append({"provider": "fireworks", "model": fw_model, "label": "Fireworks AI (GLM 5.3 Flash)"})
        if self._tier_key_ready("gemini"):
            cloud_options.append({"provider": "gemini", "model": "gemini-2.5-flash", "label": "Google Gemini"})
        if self._tier_key_ready("openai"):
            cloud_options.append({"provider": "openai", "model": "gpt-4o", "label": "OpenAI GPT-4o"})
        if self._tier_key_ready("groq"):
            cloud_options.append({"provider": "groq", "model": "llama-3.3-70b-versatile", "label": "Groq Ultra-Fast"})
        if self._tier_key_ready("openrouter"):
            cloud_options.append({"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct", "label": "OpenRouter"})
        if self._tier_key_ready("anthropic"):
            cloud_options.append({"provider": "anthropic", "model": "claude-opus-5-5", "label": "Anthropic Claude Opus"})

        local_configured = settings.llm_provider in LOCAL_PROVIDERS
        return {
            "local_ready": True,
            "local_provider": settings.llm_provider if local_configured else "lm_studio",
            "local_model": settings.llm_model,
            "cloud_ready": len(cloud_options) > 0,
            "cloud_options": cloud_options,
            "active_provider": settings.llm_provider,
            "active_model": settings.llm_model
        }

    def resolve_endpoint_for_tier(
        self,
        tier: str = "auto",
        model_override: Optional[str] = None,
        provider_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Dynamically resolves the optimal LLM endpoint based on requested tier.
        Tiers:
          - 'local' / 'local_fast': LM Studio or Ollama (localhost, zero token cost, private, low latency)
          - 'cloud' / 'cloud_reasoning': Cloud models via user-provided API keys
          - 'auto': Cloud if API keys are configured and available, otherwise Local.
        """
        if provider_override:
            return self._build_endpoint(provider_override, model_override)

        tiers_info = self.get_available_tiers()
        wants_cloud = tier in ("cloud", "cloud_reasoning")
        wants_local = tier in ("local", "local_fast")

        if wants_cloud:
            if tiers_info["cloud_ready"]:
                top_cloud = tiers_info["cloud_options"][0]
                ep = self._build_endpoint(top_cloud["provider"], model_override or top_cloud["model"])
                return {
                    **ep,
                    "tier_requested": "cloud",
                    "tier_resolved": "cloud",
                    "tier_display": f"Cloud ({top_cloud['label']})",
                    "is_cloud": True
                }
            else:
                # Graceful fallback: Cloud was requested but no API key configured yet
                local_prov = tiers_info["local_provider"]
                ep = self._build_endpoint(local_prov, model_override or settings.llm_model)
                return {
                    **ep,
                    "tier_requested": "cloud",
                    "tier_resolved": "local_fallback",
                    "tier_display": f"Local ({local_prov.replace('_', ' ').title()}) [Cloud Fallback]",
                    "is_cloud": False,
                    "notice": "No cloud API key configured; running on local model."
                }

        if wants_local:
            local_prov = tiers_info["local_provider"]
            ep = self._build_endpoint(local_prov, model_override or settings.llm_model)
            return {
                **ep,
                "tier_requested": "local",
                "tier_resolved": "local",
                "tier_display": f"Local Fast ({local_prov.replace('_', ' ').title()})",
                "is_cloud": False
            }

        # Auto tier
        if tiers_info["cloud_ready"] and settings.llm_provider in ("openai", "gemini", "groq", "openrouter", "fireworks"):
            top_cloud = tiers_info["cloud_options"][0]
            ep = self._build_endpoint(top_cloud["provider"], model_override or top_cloud["model"])
            return {
                **ep,
                "tier_requested": "auto",
                "tier_resolved": "cloud",
                "tier_display": f"Cloud ({top_cloud['label']})",
                "is_cloud": True
            }

        base_info = self.get_endpoint_info()
        is_cloud = base_info["provider"] in ("openai", "gemini", "groq", "openrouter", "fireworks")
        return {
            **base_info,
            "tier_requested": tier,
            "tier_resolved": "cloud" if is_cloud else "local",
            "tier_display": f"{'Cloud' if is_cloud else 'Local'} ({base_info['provider'].replace('_', ' ').title()})",
            "is_cloud": is_cloud
        }

    # ------------------------------------------------------------------
    # LM Studio v1 REST API Integration (/api/v1/*)
    # ------------------------------------------------------------------
    def get_lmstudio_native_base(self) -> str:
        """Derives root URL for LM Studio native /api/v1 endpoints."""
        # LLM_BASE_URL is LM Studio's only when LM Studio is the provider (with Ollama it's Ollama's port).
        url = (settings.llm_base_url if settings.llm_provider == "lm_studio" else "") or "http://localhost:1234/v1"
        return url.replace("/v1", "").rstrip("/")

    async def get_lmstudio_status(self) -> Dict[str, Any]:
        """Queries LM Studio native /api/v1/models for comprehensive status."""
        root_url = self.get_lmstudio_native_base()
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.get(f"{root_url}/api/v1/models")
                if res.status_code == 200:
                    data = res.json()
                    models_raw = data.get("models", [])
                    models = []
                    loaded_model = None

                    for m in models_raw:
                        m_type = m.get("type", "llm")
                        instances = m.get("loaded_instances", [])
                        is_loaded = len(instances) > 0
                        size_gb = round(m.get("size_bytes", 0) / (1024**3), 2)
                        ctx_len = instances[0].get("config", {}).get("context_length") if is_loaded else m.get("max_context_length", 8192)

                        model_info = {
                            "key": m.get("key"),
                            "display_name": m.get("display_name"),
                            "type": m_type,
                            "params": m.get("params_string"),
                            "architecture": m.get("architecture"),
                            "quantization": m.get("quantization", {}).get("name", "N/A"),
                            "format": m.get("format"),
                            "size_gb": size_gb,
                            "is_loaded": is_loaded,
                            "context_length": ctx_len,
                            "capabilities": m.get("capabilities", {}),
                            "instance_id": instances[0].get("id") if is_loaded else None
                        }
                        models.append(model_info)
                        if is_loaded and m_type != "embedding":
                            loaded_model = model_info

                    # Sort models so loaded LLMs come first, followed by unloaded LLMs, then embeddings
                    models.sort(key=lambda x: (
                        not x.get("is_loaded", False),
                        x.get("type") == "embedding",
                        x.get("display_name") or ""
                    ))

                    return {
                        "online": True,
                        "server_url": root_url,
                        "loaded_model": loaded_model,
                        "models": models,
                        "total_models": len(models)
                    }
        except Exception as e:
            logger.debug(f"LM Studio status check error: {e}")

        return {
            "online": False,
            "server_url": root_url,
            "loaded_model": None,
            "models": [],
            "total_models": 0
        }

    async def load_lmstudio_model(self, model_key: str) -> Dict[str, Any]:
        """Loads a model into LM Studio memory, safely unloading others first."""
        root_url = self.get_lmstudio_native_base()
        status = await self.get_lmstudio_status()

        # Check if already loaded
        if status.get("loaded_model") and status["loaded_model"]["key"] == model_key:
            return {"success": True, "message": f"{model_key} is already loaded in memory."}

        async with httpx.AsyncClient(timeout=120.0) as client:
            # Safely unload any currently active model to protect system memory
            if status.get("loaded_model") and status["loaded_model"].get("instance_id"):
                active_id = status["loaded_model"]["instance_id"]
                try:
                    await client.post(
                        f"{root_url}/api/v1/models/unload",
                        json={"instance_id": active_id}
                    )
                except Exception as e:
                    logger.warning(f"Error unloading previous model {active_id}: {e}")

            # Now load the requested model
            try:
                res = await client.post(
                    f"{root_url}/api/v1/models/load",
                    json={"model": model_key}
                )
                if res.status_code == 200:
                    settings.llm_model = model_key
                    return {"success": True, "message": f"Successfully loaded {model_key}!"}
                else:
                    err = res.json().get("error", {}).get("message", res.text)
                    return {"success": False, "message": f"Load failed: {err}"}
            except Exception as e:
                return {"success": False, "message": f"Connection error loading model: {str(e)}"}

    async def unload_lmstudio_model(self, instance_id: Optional[str] = None) -> Dict[str, Any]:
        """Unloads model to free RAM/VRAM."""
        root_url = self.get_lmstudio_native_base()
        if not instance_id:
            status = await self.get_lmstudio_status()
            if status.get("loaded_model"):
                instance_id = status["loaded_model"].get("instance_id")

        if not instance_id:
            return {"success": False, "message": "No active model instance found to unload."}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(
                    f"{root_url}/api/v1/models/unload",
                    json={"instance_id": instance_id}
                )
                if res.status_code == 200:
                    return {"success": True, "message": "Model unloaded successfully. System memory freed."}
                else:
                    return {"success": False, "message": f"Unload failed: {res.text}"}
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}"}

    # ------------------------------------------------------------------
    # Universal Inference with Custom Tools Support
    # ------------------------------------------------------------------
    async def list_models(self, provider_override: Optional[str] = None,
                          key_profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
        active_provider = provider_override or settings.llm_provider
        if active_provider == "xod":
            from app.services.local_engine import CATALOG, is_downloaded
            return [{"id": m["repo"], "name": f"{m['label']} (built-in){'' if is_downloaded(m['repo']) else ' (not downloaded)'}",
                     "is_loaded": False, "context_length": None} for m in CATALOG]
        # If LM Studio, use the enhanced native v1 API
        if active_provider == "lm_studio":
            status = await self.get_lmstudio_status()
            if status.get("online"):
                # Exclude embedding models for chat completions
                llm_models = [
                    m for m in status.get("models", [])
                    if m.get("type") != "embedding" and "embed" not in (m.get("key") or "").lower()
                ]
                # Ensure active loaded model is always at index 0
                llm_models.sort(key=lambda x: (not x.get("is_loaded", False), x.get("display_name") or ""))
                return [
                    {
                        "id": m["key"],
                        "name": f"{m['display_name']} ({m['params']}, {m['quantization']}){' ✓ Loaded in RAM' if m['is_loaded'] else ''}",
                        "is_loaded": m["is_loaded"],
                        "context_length": m["context_length"]
                    }
                    for m in llm_models
                ]

        if active_provider == "fireworks":
            info = self._build_endpoint("fireworks", key_profile_id=key_profile_id)
            if info["api_key"] != "missing":
                headers = {
                    "Authorization": f"Bearer {info['api_key']}",
                    "Content-Type": "application/json"
                }
                url = f"{info['base_url']}/models"
                try:
                    async with httpx.AsyncClient(timeout=6.0) as client:
                        res = await client.get(url, headers=headers)
                        if res.status_code == 200:
                            data = res.json()
                            models = data.get("data", [])
                            if models:
                                return [{"id": m.get("id"), "name": m.get("id"), "is_loaded": True} for m in models]
                except Exception as e:
                    logger.warning(f"Could not fetch Fireworks models: {e}")

            # Return curated serverless catalog if offline or pre-key
            return [
                {"id": "accounts/fireworks/models/glm-5p3-flash", "name": "GLM 5.3 Flash (320B, 1M Context, Vision, Fast) - Recommended", "is_loaded": True},
                {"id": "accounts/fireworks/models/llama-v3p3-70b-instruct", "name": "Llama 3.3 70B Instruct", "is_loaded": True},
                {"id": "accounts/fireworks/models/deepseek-v3", "name": "DeepSeek V3 (671B MoE)", "is_loaded": True},
                {"id": "accounts/fireworks/models/deepseek-r1", "name": "DeepSeek R1 (Reasoning)", "is_loaded": True},
                {"id": "accounts/fireworks/models/qwen2p5-72b-instruct", "name": "Qwen 2.5 72B Instruct", "is_loaded": True},
                {"id": "accounts/fireworks/models/llama-v3p2-11b-vision-instruct", "name": "Llama 3.2 11B Vision", "is_loaded": True}
            ]

        info = self._build_endpoint(active_provider, key_profile_id=key_profile_id)
        headers = {
            "Authorization": f"Bearer {info['api_key']}",
            "Content-Type": "application/json"
        }
        url = f"{info['base_url']}/models"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(url, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    models = data.get("data", [])
                    chat_models = [m for m in models if "embed" not in (m.get("id") or "").lower()]
                    return [{"id": m.get("id"), "name": m.get("id"), "is_loaded": True} for m in (chat_models or models)]
        except Exception as e:
            logger.warning(f"Could not fetch models from {url}: {e}")
        return [{"id": info["model"], "name": info["model"], "is_loaded": True}]

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        tier: Optional[str] = None,
        endpoint_override: Optional[Dict[str, Any]] = None,
        think: Optional[bool] = None,
        task: Optional[str] = None,
        group_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        think=False asks local reasoning models (Gemma 4, Qwen 3...) to answer without a
        thinking phase: several times faster for small, routine decisions.

        task tags the call for the training data flywheel (see dataset_recorder): the
        exchange is stored and its id returned as msg["_example_id"] so the caller can
        label it once the outcome is known. If a trained specialist is active for this
        task, fast (think=False) calls go to it instead of the big model.
        """
        if not endpoint_override and not tier:
            from app.services import llm_routing
            route = llm_routing.current()
            if route and route.endpoint:   # this turn runs on the stronger (cloud) model
                endpoint_override = route.endpoint
        try:
            from app.services import warmup
            warmup.note_prompt(messages, task)
        except Exception:
            pass
        info = endpoint_override or (self.resolve_endpoint_for_tier(tier) if tier else self.get_endpoint_info())
        if task and think is False and not endpoint_override:
            from app.services.specialists import active_endpoint_for
            specialist_ep = active_endpoint_for(task)
            if specialist_ep:
                # Students are text models trained on text-only prompts: send exactly that format.
                from app.services.dataset_recorder import _text_only
                try:
                    return await self.chat_completion(
                        messages=_text_only(messages), tools=tools, tool_choice=tool_choice,
                        temperature=temperature, max_tokens=max_tokens, endpoint_override=specialist_ep,
                        think=False, task=task, group_id=group_id, meta=meta,
                    )
                except Exception as e:
                    # A broken or stalled student must never block Xod: fall back to the teacher.
                    logger.warning(f"Specialist for '{task}' failed ({e}); using the main model instead.")
        if info["provider"] in ("openai", "gemini", "groq", "openrouter", "fireworks", "anthropic") and info["api_key"] == "missing":
            raise Exception(f"No API key configured for {info['provider'].upper()}. Please configure your {info['provider'].replace('_', ' ').title()} API key in Settings!")

        engine_start: Optional[float] = None
        if info["provider"] in ("lm_studio", "xod"):
            from app.services.memory_broker import memory_broker
            await memory_broker.ensure_teacher()  # reloads the chat model if it was paused for an image/video job
        if info["provider"] == "xod":
            from app.services.local_engine import local_engine
            await local_engine.ensure_running(info["model"])   # built-in engine starts on first use
            engine_start = local_engine.started_at
        url = f"{info['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {info['api_key']}",
            "Content-Type": "application/json"
        }
        payload: Dict[str, Any] = {
            "model": info["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        if info["provider"] == "ollama":
            payload["model"] = await ollama_context.long_context_model(info["base_url"], info["model"])
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        _quiet_thinking(payload, info, think)

        last_err: Optional[Exception] = None
        grown = False
        started = time.time()
        is_specialist = info["provider"] == "specialist"
        timeout = (httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0) if is_specialist
                   else XOD_TIMEOUT if info["provider"] == "xod" else LLM_TIMEOUT)
        restarted = False
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(1, (1 if is_specialist else MAX_ATTEMPTS) + 1):
                try:
                    res = await _watched(client.post(url, json=payload, headers=headers), info)
                    if res.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS and not is_specialist:
                        logger.warning(f"LLM HTTP {res.status_code}, retrying ({attempt}/{MAX_ATTEMPTS})")
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    if res.status_code != 200 and _effort_rejected(payload, info, res.status_code, res.text):
                        continue
                    if res.status_code != 200 and await ollama_context.retry_after_error(payload, info, res.status_code, res.text):
                        continue
                    if res.status_code != 200:
                        raise Exception(ollama_context.friendly_error(info, res.status_code, res.text)
                                        or f"LLM API returned HTTP {res.status_code}: {res.text}")
                    data = res.json()
                    from app.services import llm_routing
                    llm_routing.record(info["provider"], info["model"], data.get("usage"),
                                       prompt_chars=len(json.dumps(messages)), completion_chars=len(str(data)),
                                       kind="answer", task=task, started=started, key_profile=info.get("key_profile"))
                    choice = data["choices"][0]
                    msg = choice.get("message", {})
                    answered = bool(msg.get("content"))
                    if msg.get("reasoning") and not msg.get("reasoning_content"):
                        msg["reasoning_content"] = msg["reasoning"]   # mlx_lm.server's name for it
                    if (not msg.get("content") and msg.get("reasoning_content") and choice.get("finish_reason") == "length"
                            and not grown and info["provider"] in CLOUD_PROVIDERS):
                        grown = True              # all thinking, no answer: it ran out of room; once more with plenty
                        payload["max_tokens"] = int(payload["max_tokens"]) * 3
                        continue
                    if not msg.get("content") and msg.get("reasoning_content"):
                        if choice.get("finish_reason") == "length":
                            logger.warning(
                                f"Model ran out of tokens while thinking (max_tokens={max_tokens}); "
                                "no answer was produced."
                            )
                        msg["content"] = msg["reasoning_content"]
                    msg["_provider"] = info["provider"]
                    if task and answered and not tools:
                        from app.services import dataset_recorder
                        msg["_example_id"] = dataset_recorder.capture(
                            task=task, messages=messages, response=msg["content"],
                            model=info["model"], provider=info["provider"], think=think is not False,
                            latency_ms=int((time.time() - started) * 1000), group_id=group_id, meta=meta,
                        )
                    return msg
                except EngineStuck as e:
                    last_err = e
                    if restarted:
                        raise _gave_up()
                    restarted = True
                    await _restart_engine(info, str(e), engine_start)
                    engine_start = local_engine.started_at
                    continue
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ReadError) as e:
                    last_err = e
                    if attempt < MAX_ATTEMPTS and not is_specialist:
                        logger.warning(f"LLM {type(e).__name__}, retrying ({attempt}/{MAX_ATTEMPTS})")
                        await asyncio.sleep(1.5 * attempt)
                        continue
            if isinstance(last_err, EngineStuck):
                raise _gave_up()
            if isinstance(last_err, httpx.ConnectError) and ollama_context.friendly_error(info):
                raise Exception(ollama_context.friendly_error(info))
            raise Exception(
                f"Could not get a response from {info['provider']} at {info['base_url']} "
                f"after {MAX_ATTEMPTS} tries ({type(last_err).__name__}). "
                "Make sure your connection or local LLM is running!"
            )

    async def prefill(self, messages: List[Dict[str, Any]]) -> bool:
        """Has the local model read these messages ahead of time, without answering (one token, thrown away).
        The model server keeps what it read, so a later request that starts with the same messages only has
        to read what's new: reading the prompt is most of the wait on a Mac (~120 tokens/s for a 12B model).
        Local servers only; never recorded as training data. Returns False if it didn't happen."""
        from app.services import llm_routing
        route = llm_routing.current()
        if route and route.endpoint:
            return False                       # this turn runs on a cloud model: nothing to warm
        info = self.get_endpoint_info()
        if info["provider"] not in LOCAL_PROVIDERS:
            return False
        try:
            from app.services.memory_broker import memory_broker
            if memory_broker.teacher_paused:
                return False                   # unloaded for an image/video job: don't load it just to warm it
        except Exception:
            pass
        payload: Dict[str, Any] = {"model": info["model"], "messages": messages, "temperature": 0.0, "max_tokens": 1}
        if info["provider"] == "ollama":
            payload["model"] = await ollama_context.long_context_model(info["base_url"], info["model"])
        if info["provider"] in ("lm_studio", "ollama"):
            payload["reasoning_effort"] = "none"
        else:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=90.0, write=10.0, pool=3.0)) as client:
                res = await client.post(f"{info['base_url']}/chat/completions", json=payload,
                                        headers={"Authorization": f"Bearer {info['api_key']}", "Content-Type": "application/json"})
            return res.status_code == 200
        except Exception:
            return False

    async def stream_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        tier: Optional[str] = None,
        endpoint_override: Optional[Dict[str, Any]] = None,
        task: Optional[str] = None,
        group_id: Optional[str] = None,
        think: Optional[bool] = None,
        _rescue: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Streams text chunks and reasoning tokens directly. task: see chat_completion.
        think=False skips a local reasoning model's thinking phase (much faster first word)."""
        streamed: List[str] = []
        reasoned = 0
        started = time.time()
        if not endpoint_override and not tier:
            from app.services import llm_routing
            route = llm_routing.current()
            if route and route.endpoint:   # this turn runs on the stronger (cloud) model
                endpoint_override = route.endpoint
        try:
            from app.services import warmup
            warmup.note_prompt(messages, task)
        except Exception:
            pass
        info = endpoint_override or (self.resolve_endpoint_for_tier(tier) if tier else self.get_endpoint_info())
        if info["provider"] in ("openai", "gemini", "groq", "openrouter", "fireworks", "anthropic") and info["api_key"] == "missing":
            yield {"type": "error", "content": f"Missing API key for {info['provider'].upper()}. Please open Settings (⚙️) and enter your {info['provider'].replace('_', ' ').title()} API key."}
            return

        engine_start: Optional[float] = None
        if info["provider"] in ("lm_studio", "xod"):
            from app.services.memory_broker import memory_broker
            await memory_broker.ensure_teacher()  # reloads the chat model if it was paused for an image/video job
        if info["provider"] == "xod":
            from app.services.local_engine import local_engine
            await local_engine.ensure_running(info["model"])   # built-in engine starts on first use
            engine_start = local_engine.started_at
        url = f"{info['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {info['api_key']}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": info["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }
        if info["provider"] == "ollama":
            payload["model"] = await ollama_context.long_context_model(info["base_url"], info["model"])
        if info["provider"] in ("openai", "openrouter", "groq", "fireworks", "gemini", "ollama", "lm_studio", "local", "custom"):
            payload["stream_options"] = {"include_usage": True}   # token counts (cost, and the Usage tab for local models too)
        _usage: Optional[Dict[str, Any]] = None
        _quiet_thinking(payload, info, think)

        restarted = False
        async with httpx.AsyncClient(timeout=XOD_TIMEOUT if info["provider"] == "xod" else LLM_TIMEOUT) as client:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    async with _open_stream(client, url, payload, headers, info) as response:
                        if response.status_code != 200:
                            err_body = await response.aread()
                            if _effort_rejected(payload, info, response.status_code, err_body.decode("utf-8", "ignore")):
                                continue
                            if await ollama_context.retry_after_error(payload, info, response.status_code, err_body.decode("utf-8", "ignore")):
                                continue
                            if response.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS:
                                logger.warning(f"LLM HTTP {response.status_code}, retrying ({attempt}/{MAX_ATTEMPTS})")
                                await asyncio.sleep(1.5 * attempt)
                                continue
                            yield {"type": "error", "content": ollama_context.friendly_error(info, response.status_code, err_body.decode("utf-8", "ignore"))
                                   or plain_error(info, response.status_code, err_body.decode('utf-8', 'ignore'))}
                            return

                        async for line in _lines(response, info):
                            if not line:
                                continue
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data_str)
                                    if chunk.get("error"):
                                        err_info = chunk["error"]
                                        err_msg = err_info.get("message", str(err_info)) if isinstance(err_info, dict) else str(err_info)
                                        logger.error(f"LLM streaming error: {err_msg}")
                                        yield {"type": "error", "content": f"LLM error: {err_msg}"}
                                        return
                                    if chunk.get("usage"):
                                        _usage = chunk["usage"]
                                    choice = chunk.get("choices", [{}])[0]
                                    delta = choice.get("delta", {})

                                    # Reasoning / thought tokens
                                    if delta.get("reasoning") and not delta.get("reasoning_content"):
                                        delta["reasoning_content"] = delta["reasoning"]
                                    if delta.get("reasoning_content"):
                                        reasoned += len(delta["reasoning_content"])
                                        yield {"type": "reasoning", "content": delta["reasoning_content"]}
                                    # Final content tokens
                                    if delta.get("content"):
                                        c_tok = delta["content"]
                                        if "<|tool_call" in c_tok or "<tool_call" in c_tok:
                                            continue
                                        streamed.append(c_tok)
                                        yield {"type": "content", "content": c_tok}
                                    if choice.get("finish_reason") == "length":
                                        logger.warning(f"LLM stream hit max_tokens={max_tokens}; output was cut off.")
                                except Exception:
                                    continue
                        else:
                            if info["provider"] in ENDS_WITH_DONE:   # no [DONE]: the server went away mid-answer
                                raise httpx.RemoteProtocolError("the stream ended without [DONE]")
                    from app.services import llm_routing
                    llm_routing.record(info["provider"], info["model"], _usage,
                                       prompt_chars=len(json.dumps(messages)), completion_chars=sum(len(x) for x in streamed) + reasoned,
                                       kind="stream", task=task, started=started, key_profile=info.get("key_profile"))
                    if task and streamed:
                        from app.services import dataset_recorder
                        ex_id = dataset_recorder.capture(
                            task=task, messages=messages, response="".join(streamed),
                            model=info["model"], provider=info["provider"], think=True,
                            latency_ms=int((time.time() - started) * 1000), group_id=group_id,
                        )
                        if ex_id:  # lets the caller label this decision once its outcome is known
                            yield {"type": "example_id", "id": ex_id}
                    if not streamed and reasoned and not _rescue:
                        # It thought but never answered (a reasoning model that used up its budget thinking): ask
                        # once more for just the answer, with more room, so the chat never ends on thoughts alone.
                        logger.warning(f"{info['provider']} returned only reasoning ({reasoned} chars); asking for the answer")
                        async for ch in self.stream_chat_completion(
                                messages=messages + [{"role": "user", "content": "Now write your final answer directly, without thinking out loud."}],
                                temperature=temperature, max_tokens=max(int(max_tokens) * 3, 1500), tier=tier,
                                endpoint_override=endpoint_override, task=task, group_id=group_id, think=False, _rescue=True):
                            if ch.get("type") != "reasoning":
                                yield ch
                    return
                except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                    if attempt < MAX_ATTEMPTS:
                        logger.warning(f"LLM {type(e).__name__}, retrying ({attempt}/{MAX_ATTEMPTS})")
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    yield {"type": "error", "content": ollama_context.friendly_error(info)
                           or plain_error(info)}
                    return
                except EngineStuck as e:
                    err = await _after_stall(info, e, engine_start, restarted, said=bool(streamed or reasoned))
                    if err:
                        yield {"type": "error", "content": err}
                        return
                    restarted, engine_start = True, local_engine.started_at
                    continue
                except httpx.ReadTimeout:
                    yield {"type": "error", "content": f"{info['provider']} stopped responding (no output for {LLM_TIMEOUT.read:.0f}s)."}
                    return
                except (httpx.RemoteProtocolError, httpx.ReadError):
                    # The model server went away in the middle of the answer (crashed, ran out of memory, was quit).
                    yield {"type": "error", "content": f"{PROVIDER_NAMES.get(info['provider'], info['provider'])} stopped in the middle of the "
                           "answer (it may have crashed or run out of memory). Try again; if it keeps happening, pick a smaller model."}
                    return

    async def deliberate_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        tier: Optional[str] = None,
        endpoint_override: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Streams reasoning tokens live, detects whether tool calls or conversational answers
        are being formulated, and yields live tokens + final deliberation structure.
        """
        _started = time.time()
        if not endpoint_override and not tier:
            from app.services import llm_routing
            route = llm_routing.current()
            if route and route.endpoint:   # this turn runs on the stronger (cloud) model
                endpoint_override = route.endpoint
        info = endpoint_override or (self.resolve_endpoint_for_tier(tier) if tier else self.get_endpoint_info())
        if info["provider"] in ("openai", "gemini", "groq", "openrouter", "fireworks", "anthropic") and info["api_key"] == "missing":
            yield {"type": "error", "content": f"Missing API key for {info['provider'].upper()}. Please open Settings (⚙️) and enter your {info['provider'].replace('_', ' ').title()} API key."}
            return

        engine_start: Optional[float] = None
        if info["provider"] in ("lm_studio", "xod"):
            from app.services.memory_broker import memory_broker
            await memory_broker.ensure_teacher()  # reloads the chat model if it was paused for an image/video job
        if info["provider"] == "xod":
            from app.services.local_engine import local_engine
            await local_engine.ensure_running(info["model"])   # built-in engine starts on first use
            engine_start = local_engine.started_at
        url = f"{info['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {info['api_key']}",
            "Content-Type": "application/json"
        }
        payload: Dict[str, Any] = {
            "model": info["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }
        if info["provider"] == "ollama":
            payload["model"] = await ollama_context.long_context_model(info["base_url"], info["model"])
        if info["provider"] in ("openai", "openrouter", "groq", "fireworks", "gemini", "ollama", "lm_studio", "local", "custom"):
            payload["stream_options"] = {"include_usage": True}   # token counts (cost, and the Usage tab for local models too)
        _usage: Optional[Dict[str, Any]] = None
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        tool_calls_map: Dict[int, Dict[str, Any]] = {}
        full_reasoning = ""
        full_content = ""

        restarted = False
        async with httpx.AsyncClient(timeout=XOD_TIMEOUT if info["provider"] == "xod" else LLM_TIMEOUT) as client:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    async with _open_stream(client, url, payload, headers, info) as response:
                        if response.status_code != 200:
                            err_body = await response.aread()
                            if await ollama_context.retry_after_error(payload, info, response.status_code, err_body.decode("utf-8", "ignore")):
                                continue
                            if response.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS:
                                logger.warning(f"LLM HTTP {response.status_code}, retrying ({attempt}/{MAX_ATTEMPTS})")
                                await asyncio.sleep(1.5 * attempt)
                                continue
                            yield {"type": "error", "content": ollama_context.friendly_error(info, response.status_code, err_body.decode("utf-8", "ignore"))
                                   or plain_error(info, response.status_code, err_body.decode('utf-8', 'ignore'))}
                            return

                        async for line in _lines(response, info):
                            if not line:
                                continue
                            if line.startswith("event: error"):
                                continue
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data_str)
                                    if chunk.get("error"):
                                        err_info = chunk["error"]
                                        err_msg = err_info.get("message", str(err_info)) if isinstance(err_info, dict) else str(err_info)
                                        logger.error(f"LLM streaming error from provider: {err_msg}")
                                        yield {"type": "error", "content": f"LLM error: {err_msg}"}
                                        return
                                    if chunk.get("usage"):
                                        _usage = chunk["usage"]

                                    choices = chunk.get("choices", [])
                                    if not choices:
                                        continue
                                    delta = choices[0].get("delta", {})

                                    # Live reasoning stream
                                    if delta.get("reasoning") and not delta.get("reasoning_content"):
                                        delta["reasoning_content"] = delta["reasoning"]
                                    if delta.get("reasoning_content"):
                                        r_tok = delta["reasoning_content"]
                                        full_reasoning += r_tok
                                        yield {"type": "reasoning", "content": r_tok}

                                    # Live content stream (if not accumulating tool calls or tool tags)
                                    if delta.get("content"):
                                        c_tok = delta["content"]
                                        full_content += c_tok
                                        if not tool_calls_map and "<|tool_call" not in full_content and "<tool_call" not in full_content and "call:" not in full_content:
                                            yield {"type": "content", "content": c_tok}

                                    # Accumulate streamed tool calls
                                    if delta.get("tool_calls"):
                                        for tc in delta["tool_calls"]:
                                            idx = tc.get("index", 0)
                                            if idx not in tool_calls_map:
                                                tool_calls_map[idx] = {
                                                    "id": tc.get("id", ""),
                                                    "name": "",
                                                    "arguments": ""
                                                }
                                            if tc.get("id"):
                                                tool_calls_map[idx]["id"] = tc["id"]
                                            fn = tc.get("function", {})
                                            if fn.get("name"):
                                                tool_calls_map[idx]["name"] += fn["name"]
                                            if fn.get("arguments"):
                                                tool_calls_map[idx]["arguments"] += fn["arguments"]

                                except Exception:
                                    continue
                        else:
                            if info["provider"] in ENDS_WITH_DONE:   # no [DONE]: the server went away mid-answer
                                raise httpx.RemoteProtocolError("the stream ended without [DONE]")

                    break
                except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                    if attempt < MAX_ATTEMPTS:
                        logger.warning(f"LLM {type(e).__name__}, retrying ({attempt}/{MAX_ATTEMPTS})")
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    yield {"type": "error", "content": ollama_context.friendly_error(info)
                           or plain_error(info)}
                    return
                except EngineStuck as e:
                    err = await _after_stall(info, e, engine_start, restarted, said=bool(full_content or full_reasoning))
                    if err:
                        yield {"type": "error", "content": err}
                        return
                    restarted, engine_start = True, local_engine.started_at
                    continue
                except httpx.ReadTimeout:
                    yield {"type": "error", "content": f"{info['provider']} stopped responding (no output for {LLM_TIMEOUT.read:.0f}s)."}
                    return
                except (httpx.RemoteProtocolError, httpx.ReadError):
                    # The model server went away in the middle of the answer (crashed, ran out of memory, was quit).
                    yield {"type": "error", "content": f"{PROVIDER_NAMES.get(info['provider'], info['provider'])} stopped in the middle of the "
                           "answer (it may have crashed or run out of memory). Try again; if it keeps happening, pick a smaller model."}
                    return

        formatted_tool_calls = []
        for idx in sorted(tool_calls_map.keys()):
            item = tool_calls_map[idx]
            if item.get("name"):
                formatted_tool_calls.append({
                    "id": item.get("id") or f"call_{idx}",
                    "type": "function",
                    "function": {
                        "name": item["name"],
                        "arguments": item["arguments"]
                    }
                })

        # Fallback: Parse raw tool calls in full_content if model outputted tags directly
        if not formatted_tool_calls and ("<|tool_call" in full_content or "<tool_call" in full_content or "call:" in full_content):
            raw_calls, cleaned_content = self._extract_raw_tool_calls(full_content)
            for i, rc in enumerate(raw_calls):
                formatted_tool_calls.append({
                    "id": f"call_raw_{i}",
                    "type": "function",
                    "function": {
                        "name": rc["name"],
                        "arguments": rc["arguments"]
                    }
                })
            full_content = cleaned_content

        from app.services import llm_routing
        llm_routing.record(info["provider"], info["model"], _usage,
                           prompt_chars=len(json.dumps(messages)), completion_chars=len(full_content) + len(full_reasoning),
                           kind="deliberate", started=_started, key_profile=info.get("key_profile"))
        yield {
            "type": "deliberation_complete",
            "tool_calls": formatted_tool_calls,
            "content": full_content,
            "reasoning": full_reasoning
        }

    def _extract_raw_tool_calls(self, text: str) -> Tuple[List[Dict[str, str]], str]:
        calls = []
        p1 = re.compile(
            r'(?:<\|?tool_call\|?>)?\s*(?:call:)?([a-zA-Z0-9_]+)\s*(\{.*?\})\s*(?:<\|?/?tool_call\|?>)?',
            re.DOTALL
        )
        for m in p1.finditer(text):
            name = m.group(1).strip()
            raw_args = m.group(2).strip()
            if not name or name in ("call", "tool_call"):
                continue
            fixed = re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', raw_args)
            fixed = fixed.replace("'", '"')
            try:
                parsed = json.loads(fixed)
                arg_str = json.dumps(parsed)
            except Exception:
                arg_str = raw_args
            calls.append({"name": name, "arguments": arg_str})

        clean_text = p1.sub("", text).strip()
        clean_text = re.sub(r'<\|?/?tool_call\|?>', '', clean_text).strip()
        return calls, clean_text

llm_service = LLMService()
