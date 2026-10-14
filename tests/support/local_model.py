"""The model on this computer that tests needing one should use.

LLM_PROVIDER, LLM_BASE_URL and LLM_MODEL when they're set (e.g. Ollama on a PC:
LLM_PROVIDER=ollama LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=qwen2.5:14b), else LM Studio with Gemma 4 12B,
which is what these tests were written against on a Mac.
"""
import os

API_KEYS = {"lm_studio": "lm-studio", "ollama": "ollama"}


def local_model():
    """(provider, base_url, model)"""
    provider = os.environ.get("LLM_PROVIDER") or "lm_studio"
    base = os.environ.get("LLM_BASE_URL") or ("http://localhost:11434/v1" if provider == "ollama" else "http://127.0.0.1:1234/v1")
    model = os.environ.get("LLM_MODEL") or ("qwen2.5:14b" if provider == "ollama" else "google/gemma-4-12b-qat")
    return provider, base, model


def local_endpoint():
    """The same, as an endpoint dict (llm.py's endpoint_override shape)."""
    provider, base, model = local_model()
    return {"provider": provider, "base_url": base, "model": model, "api_key": API_KEYS.get(provider, "local")}
