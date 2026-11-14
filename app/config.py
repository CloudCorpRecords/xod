import os
from pathlib import Path
from dotenv import load_dotenv, set_key
from pydantic import BaseModel
from typing import Optional

from app.paths import ENV_PATH, DEV_ENV_PATH

# Settings the app saved, then (for developers) a .env in the source checkout.
load_dotenv(dotenv_path=ENV_PATH)
load_dotenv(dotenv_path=DEV_ENV_PATH)

from app.services.storage import get_setting, set_setting, get_all_settings, has_setting

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
        if has_setting(field):
            # A saved value wins verbatim, even when it is empty. An empty
            # llm_base_url is a real choice (the provider's own endpoint),
            # not "unset": treating it as unset let a stale LM Studio URL
            # from .env (or the Settings class default) resurrect at every
            # launch, which is the 2026-11-01 keys/config drift.
            setattr(s, field, get_setting(field, ""))
            continue
        env_val = os.getenv(field.upper(), "")
        if env_val:
            setattr(s, field, env_val)
            set_setting(field, env_val)

    s.host = os.getenv("HOST", "127.0.0.1")
    s.port = int(os.getenv("PORT", "8000"))
    s.cache_ttl_seconds = int(os.getenv("CACHE_TTL_SECONDS", "300"))
    return s

settings = load_synced_settings()

# Whether that snapshot was built while the OS key store could not be read.
# load_synced_settings reads every key once, at import; if the store's unlock
# prompt outlives the startup timeout (keychain.KEYRING_TIMEOUT), each read
# lands empty and the snapshot says "no keys" even for keys that exist. Until
# now nothing refreshed it, so the model picker and the Settings key cards
# showed every keyed provider missing for the life of the process while
# /api/secrets/health read green (field failure 2026-11-14: keys present in
# the vault, picker empty). maybe_heal_secrets() closes that gap; the read
# paths that serve the snapshot call it.
try:
    from app.services import keychain as _keychain
    _secrets_stale = _keychain.problem() is not None
except Exception:
    _secrets_stale = False


def reload_secrets() -> dict:
    """Reads the Keychain again (after macOS said no, or the prompt went unseen) and puts the keys back in use."""
    global _secrets_stale
    from app.services import keychain, secret_store
    keychain.reset_cache()
    for field in secret_store.SECRET_FIELDS:
        setattr(settings, field, secret_store.get(field))
    health = secret_store.health()
    _secrets_stale = not health["readable"]
    return health


def maybe_heal_secrets() -> bool:
    """Refreshes the settings snapshot when it was built during a key store
    outage and the store answers now. Called from the read paths that serve
    the snapshot (setup_wizard.saved_keys, GET /api/config, GET /api/secrets),
    so a keychain prompt answered seconds after launch heals the picker and
    the key cards on its own: no restart, nobody pressing Try again. While
    the store still doesn't answer, the snapshot is left alone; the check is
    one cached problem() read while stale and free once healed."""
    global _secrets_stale
    if not _secrets_stale:
        return False
    from app.services import keychain
    try:
        if keychain.problem() is not None:
            return False
    except Exception:
        return False
    reload_secrets()
    return not _secrets_stale

def update_env_variable(key: str, value: str):
    """Updates a setting. API keys go to the Keychain only; other settings to SQLite and .env."""
    global settings
    from app.services import secret_store
    key_lower = key.lower()
    key_upper = key.upper()

    if secret_store.is_secret(key_lower):
        field = secret_store.canonical(key_lower)
        secret_store.save(field, value)
        if hasattr(settings, field):     # a 1Password reference (op://…) is in use as the key it points to
            setattr(settings, field, secret_store.get(field) if (value or "").strip().startswith("op://") else (value or "").strip())
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
