import re
import time
import asyncio
import json
import httpx
import logging
from typing import List, Dict, Any, Optional, AsyncGenerator, Tuple
from app.config import settings

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


class LLMService:
    def __init__(self):
        pass

    def _build_endpoint(self, provider: str, model: Optional[str] = None) -> Dict[str, Any]:
        base_url = settings.llm_base_url or "http://localhost:1234/v1"
        api_key = "local"
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
        elif provider == "openai":
            base_url = "https://api.openai.com/v1"
            api_key = settings.openai_api_key or "missing"
            m = model or "gpt-4o"
        elif provider == "gemini":
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
            api_key = settings.gemini_api_key or "missing"
            m = model or "gemini-2.5-flash"
        elif provider == "groq":
            base_url = "https://api.groq.com/openai/v1"
            api_key = settings.groq_api_key or "missing"
            m = model or "llama-3.3-70b-versatile"
        elif provider == "openrouter":
            base_url = "https://openrouter.ai/api/v1"
            api_key = settings.openrouter_api_key or "missing"
            m = model or "meta-llama/llama-3.3-70b-instruct"
        elif provider == "anthropic":
            # Anthropic's OpenAI-compatible endpoint (chat/completions with a Bearer key).
            base_url = "https://api.anthropic.com/v1"
            api_key = settings.anthropic_api_key or "missing"
            m = model or "claude-opus-5-5"
        elif provider == "fireworks":
            base_url = settings.llm_base_url if (settings.llm_base_url and "fireworks" in settings.llm_base_url) else "https://api.fireworks.ai/inference/v1"
            api_key = settings.fireworks_api_key or "missing"
            m = model or settings.llm_model
            if not m or m == "google/gemma-4-12b-qat":
                m = "accounts/fireworks/models/glm-5p3-flash"
            elif not m.startswith("accounts/"):
                m = f"accounts/fireworks/models/{m}"

        return {
            "provider": provider,
            "base_url": base_url.rstrip("/"),
            "model": m,
            "api_key": api_key
        }

    def get_endpoint_info(self) -> Dict[str, Any]:
        return self._build_endpoint(settings.llm_provider, settings.llm_model)

    def get_available_tiers(self) -> Dict[str, Any]:
        """Returns readiness status for both local and cloud model tiers."""
        cloud_options = []
        if settings.fireworks_api_key and settings.fireworks_api_key.strip():
            fw_model = settings.llm_model if (settings.llm_provider == "fireworks" and settings.llm_model) else "accounts/fireworks/models/glm-5p3-flash"
            cloud_options.append({"provider": "fireworks", "model": fw_model, "label": "Fireworks AI (GLM 5.3 Flash)"})
        if settings.gemini_api_key and settings.gemini_api_key.strip():
            cloud_options.append({"provider": "gemini", "model": "gemini-2.5-flash", "label": "Google Gemini"})
        if settings.openai_api_key and settings.openai_api_key.strip():
            cloud_options.append({"provider": "openai", "model": "gpt-4o", "label": "OpenAI GPT-4o"})
        if settings.groq_api_key and settings.groq_api_key.strip():
            cloud_options.append({"provider": "groq", "model": "llama-3.3-70b-versatile", "label": "Groq Ultra-Fast"})
        if settings.openrouter_api_key and settings.openrouter_api_key.strip():
            cloud_options.append({"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct", "label": "OpenRouter"})
        if settings.anthropic_api_key and settings.anthropic_api_key.strip():
            cloud_options.append({"provider": "anthropic", "model": "claude-opus-5-5", "label": "Anthropic Claude Opus"})

        local_configured = settings.llm_provider in ("lm_studio", "ollama", "xod")
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
        url = settings.llm_base_url or "http://localhost:1234/v1"
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
    async def list_models(self, provider_override: Optional[str] = None) -> List[Dict[str, Any]]:
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
            info = self._build_endpoint("fireworks")
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

        info = self._build_endpoint(active_provider)
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

        if info["provider"] in ("lm_studio", "xod"):
            from app.services.memory_broker import memory_broker
            await memory_broker.ensure_teacher()  # reloads the chat model if it was paused for an image/video job
        if info["provider"] == "xod":
            from app.services.local_engine import local_engine
            await local_engine.ensure_running(info["model"])   # built-in engine starts on first use
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
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        if think is False and info["provider"] in ("lm_studio", "ollama"):
            payload["reasoning_effort"] = "none"
        if think is False and info["provider"] == "xod":
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        last_err: Optional[Exception] = None
        started = time.time()
        is_specialist = info["provider"] == "specialist"
        timeout = httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0) if is_specialist else LLM_TIMEOUT
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(1, (1 if is_specialist else MAX_ATTEMPTS) + 1):
                try:
                    res = await client.post(url, json=payload, headers=headers)
                    if res.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS and not is_specialist:
                        logger.warning(f"LLM HTTP {res.status_code}, retrying ({attempt}/{MAX_ATTEMPTS})")
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    if res.status_code != 200:
                        raise Exception(f"LLM API returned HTTP {res.status_code}: {res.text}")
                    data = res.json()
                    from app.services import llm_routing
                    llm_routing.record(info["provider"], info["model"], data.get("usage"),
                                       prompt_chars=len(json.dumps(messages)), completion_chars=len(str(data)))
                    choice = data["choices"][0]
                    msg = choice.get("message", {})
                    answered = bool(msg.get("content"))
                    if msg.get("reasoning") and not msg.get("reasoning_content"):
                        msg["reasoning_content"] = msg["reasoning"]   # mlx_lm.server's name for it
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
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                    last_err = e
                    if attempt < MAX_ATTEMPTS and not is_specialist:
                        logger.warning(f"LLM {type(e).__name__}, retrying ({attempt}/{MAX_ATTEMPTS})")
                        await asyncio.sleep(1.5 * attempt)
                        continue
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
        if info["provider"] not in ("lm_studio", "xod", "ollama"):
            return False
        try:
            from app.services.memory_broker import memory_broker
            if memory_broker.teacher_paused:
                return False                   # unloaded for an image/video job: don't load it just to warm it
        except Exception:
            pass
        payload: Dict[str, Any] = {"model": info["model"], "messages": messages, "temperature": 0.0, "max_tokens": 1}
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
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Streams text chunks and reasoning tokens directly. task: see chat_completion.
        think=False skips a local reasoning model's thinking phase (much faster first word)."""
        streamed: List[str] = []
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

        if info["provider"] in ("lm_studio", "xod"):
            from app.services.memory_broker import memory_broker
            await memory_broker.ensure_teacher()  # reloads the chat model if it was paused for an image/video job
        if info["provider"] == "xod":
            from app.services.local_engine import local_engine
            await local_engine.ensure_running(info["model"])   # built-in engine starts on first use
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
        if info["provider"] in ("openai", "openrouter", "groq", "fireworks", "gemini"):
            payload["stream_options"] = {"include_usage": True}   # token counts for cost tracking
        _usage: Optional[Dict[str, Any]] = None
        if think is False and info["provider"] in ("lm_studio", "ollama"):
            payload["reasoning_effort"] = "none"
        if think is False and info["provider"] == "xod":
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    async with client.stream("POST", url, json=payload, headers=headers) as response:
                        if response.status_code != 200:
                            err_body = await response.aread()
                            if response.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS:
                                logger.warning(f"LLM HTTP {response.status_code}, retrying ({attempt}/{MAX_ATTEMPTS})")
                                await asyncio.sleep(1.5 * attempt)
                                continue
                            yield {"type": "error", "content": f"LLM error {response.status_code}: {err_body.decode('utf-8')}"}
                            return

                        async for line in response.aiter_lines():
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
                    from app.services import llm_routing
                    llm_routing.record(info["provider"], info["model"], _usage,
                                       prompt_chars=len(json.dumps(messages)), completion_chars=sum(len(x) for x in streamed))
                    if task and streamed:
                        from app.services import dataset_recorder
                        ex_id = dataset_recorder.capture(
                            task=task, messages=messages, response="".join(streamed),
                            model=info["model"], provider=info["provider"], think=True,
                            latency_ms=int((time.time() - started) * 1000), group_id=group_id,
                        )
                        if ex_id:  # lets the caller label this decision once its outcome is known
                            yield {"type": "example_id", "id": ex_id}
                    return
                except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                    if attempt < MAX_ATTEMPTS:
                        logger.warning(f"LLM {type(e).__name__}, retrying ({attempt}/{MAX_ATTEMPTS})")
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    yield {"type": "error", "content": f"Could not connect to {info['provider']} at {info['base_url']}. Is your local LLM running?"}
                    return
                except httpx.ReadTimeout:
                    yield {"type": "error", "content": f"{info['provider']} stopped responding (no output for {LLM_TIMEOUT.read:.0f}s)."}
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
        if not endpoint_override and not tier:
            from app.services import llm_routing
            route = llm_routing.current()
            if route and route.endpoint:   # this turn runs on the stronger (cloud) model
                endpoint_override = route.endpoint
        info = endpoint_override or (self.resolve_endpoint_for_tier(tier) if tier else self.get_endpoint_info())
        if info["provider"] in ("openai", "gemini", "groq", "openrouter", "fireworks", "anthropic") and info["api_key"] == "missing":
            yield {"type": "error", "content": f"Missing API key for {info['provider'].upper()}. Please open Settings (⚙️) and enter your {info['provider'].replace('_', ' ').title()} API key."}
            return

        if info["provider"] in ("lm_studio", "xod"):
            from app.services.memory_broker import memory_broker
            await memory_broker.ensure_teacher()  # reloads the chat model if it was paused for an image/video job
        if info["provider"] == "xod":
            from app.services.local_engine import local_engine
            await local_engine.ensure_running(info["model"])   # built-in engine starts on first use
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
        if info["provider"] in ("openai", "openrouter", "groq", "fireworks", "gemini"):
            payload["stream_options"] = {"include_usage": True}   # token counts for cost tracking
        _usage: Optional[Dict[str, Any]] = None
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        tool_calls_map: Dict[int, Dict[str, Any]] = {}
        full_reasoning = ""
        full_content = ""

        async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    async with client.stream("POST", url, json=payload, headers=headers) as response:
                        if response.status_code != 200:
                            err_body = await response.aread()
                            if response.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS:
                                logger.warning(f"LLM HTTP {response.status_code}, retrying ({attempt}/{MAX_ATTEMPTS})")
                                await asyncio.sleep(1.5 * attempt)
                                continue
                            yield {"type": "error", "content": f"LLM error {response.status_code}: {err_body.decode('utf-8')}"}
                            return

                        async for line in response.aiter_lines():
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

                    break
                except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                    if attempt < MAX_ATTEMPTS:
                        logger.warning(f"LLM {type(e).__name__}, retrying ({attempt}/{MAX_ATTEMPTS})")
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    yield {"type": "error", "content": f"Could not connect to {info['provider']} at {info['base_url']}. Is your local LLM running?"}
                    return
                except httpx.ReadTimeout:
                    yield {"type": "error", "content": f"{info['provider']} stopped responding (no output for {LLM_TIMEOUT.read:.0f}s)."}
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
                           prompt_chars=len(json.dumps(messages)), completion_chars=len(full_content) + len(full_reasoning))
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
