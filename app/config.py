import os
from pathlib import Path
from dotenv import load_dotenv, set_key
from pydantic import BaseModel
from typing import Optional

from app.paths import ENV_PATH, DEV_ENV_PATH

# Settings the app saved, then (for developers) a .env in the source checkout.
load_dotenv(dotenv_path=ENV_PATH)
load_dotenv(dotenv_path=DEV_ENV_PATH)

from app.services.storage import get_setting, set_setting, get_all_settings

class Settings(BaseModel):
    trustmrr_api_key: str = ""
    you_api_key: str = ""
    llm_provider: str = "lm_studio"
    llm_base_url: str = "http://localhost:1234/v1"
    llm_model: str = "google/gemma-4-12b-qat"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    fireworks_api_key: str = ""
    youtube_api_key: str = ""
    tavily_api_key: str = ""
    huggingface_token: str = ""
    host: str = "127.0.0.1"
    port: int = 8000
    cache_ttl_seconds: int = 300

def load_synced_settings() -> Settings:
    """API keys come from the macOS Keychain (moved there from .env / SQLite on first run);
    ordinary settings sync between .env and SQLite as before."""
    from app.services import secret_store
    try:
        secret_store.migrate()
    except Exception as e:
        import logging
        logging.getLogger("xod.secrets").error(f"API key migration failed: {e}")
    s = Settings()
    for field in secret_store.SECRET_FIELDS:
        setattr(s, field, secret_store.get(field))
    for field in ["llm_provider", "llm_base_url", "llm_model"]:
        env_val = os.getenv(field.upper(), "")
        db_val = get_setting(field, "")
        final_val = db_val if db_val else env_val
        if final_val:
            setattr(s, field, final_val)
        if final_val and not db_val:
            set_setting(field, final_val)

    s.host = os.getenv("HOST", "127.0.0.1")
    s.port = int(os.getenv("PORT", "8000"))
    s.cache_ttl_seconds = int(os.getenv("CACHE_TTL_SECONDS", "300"))
    return s

settings = load_synced_settings()

def update_env_variable(key: str, value: str):
    """Updates a setting. API keys go to the Keychain only; other settings to SQLite and .env."""
    global settings
    from app.services import secret_store
    key_lower = key.lower()
    key_upper = key.upper()

    if secret_store.is_secret(key_lower):
        field = secret_store.canonical(key_lower)
        secret_store.save(field, value)
        if hasattr(settings, field):
            setattr(settings, field, (value or "").strip())
        return

    # 1. Update SQLite
    set_setting(key_lower, value)

    # 2. Update .env file
    try:
        set_key(str(ENV_PATH), key_upper, value)
    except Exception:
        pass
    os.environ[key_upper] = value

    # 3. Update in-memory settings
    if hasattr(settings, key_lower):
        setattr(settings, key_lower, value)
