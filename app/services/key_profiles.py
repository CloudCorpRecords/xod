"""
Named API keys per provider: "key profiles".

Until now each cloud provider had exactly one key (the <provider>_api_key entry in the
Keychain vault; see secret_store.py). A profile is a name plus its own key in the same
vault, plus the list of models that key was last seen to allow (Detect Models fills it).

The first profile is always "Default", and its key IS the provider's long-standing single
key: the Default profile reads and writes the plain <provider>_api_key vault entry, so
everything that worked before profiles (the Settings key field, the Swarm key fields,
secret_store.migrate) keeps working unchanged, and the existing key becomes the Default
profile with no copying. Extra profiles keep their keys under <provider>_api_key__<id>.

Profile metadata (names, which one is active, allowed models) is ordinary settings data
and lives in the settings table as one JSON value; only the keys themselves are secrets,
and they never leave the vault: this module hands out masked forms for display and the
real value only to the LLM layer at call time.
"""
import json
import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.services import secret_store
from app.services.storage import get_setting, set_setting

logger = logging.getLogger("xod.key_profiles")

SETTING_KEY = "key_profiles_v1"
KEY_PROVIDERS = ("openai", "anthropic", "gemini", "groq", "openrouter", "fireworks")
DEFAULT_ID = "default"
_lock = threading.RLock()


def is_key_provider(provider: str) -> bool:
    return provider in KEY_PROVIDERS


def _slot(provider: str, profile_id: str) -> str:
    """The vault entry a profile's key lives in. Default keeps the historic single-key entry."""
    return f"{provider}_api_key" if profile_id == DEFAULT_ID else f"{provider}_api_key__{profile_id}"


# ------------------------------------------------------------------ metadata store

def _load_all() -> Dict[str, Any]:
    try:
        data = json.loads(get_setting(SETTING_KEY, "") or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _save_all(data: Dict[str, Any]) -> None:
    set_setting(SETTING_KEY, json.dumps(data))


def has_metadata(provider: str) -> bool:
    return provider in _load_all()


def _meta(data: Dict[str, Any], provider: str) -> Dict[str, Any]:
    """One provider's metadata, with the Default profile always present."""
    meta = data.get(provider) or {}
    profiles = meta.get("profiles") or {}
    if DEFAULT_ID not in profiles:
        profiles = {DEFAULT_ID: {"name": "Default", "models": [], "models_at": None}, **profiles}
    active = meta.get("active") or DEFAULT_ID
    if active not in profiles:
        active = DEFAULT_ID
    return {"active": active, "profiles": profiles}


def _summary(provider: str, profile_id: str, entry: Dict[str, Any], active_id: str) -> Dict[str, Any]:
    stored = secret_store.stored(_slot(provider, profile_id))
    return {
        "id": profile_id,
        "name": entry.get("name") or ("Default" if profile_id == DEFAULT_ID else profile_id),
        "active": profile_id == active_id,
        "has_key": bool(stored),
        "masked": secret_store.mask(stored),
        "models": list(entry.get("models") or []),
        "models_at": entry.get("models_at"),
    }


# ------------------------------------------------------------------ reading

def list_profiles(provider: str) -> Dict[str, Any]:
    """Everything the Settings UI may see: names, which is active, masked keys, allowed models. Never a full key."""
    with _lock:
        meta = _meta(_load_all(), provider)
        profiles = [_summary(provider, pid, entry, meta["active"]) for pid, entry in meta["profiles"].items()]
        return {"provider": provider, "active": meta["active"], "profiles": profiles}


def active_profile(provider: str) -> Dict[str, str]:
    with _lock:
        meta = _meta(_load_all(), provider)
        entry = meta["profiles"][meta["active"]]
        return {"id": meta["active"], "name": entry.get("name") or "Default"}


def _key(provider: str, profile_id: str) -> str:
    try:
        return secret_store.get(_slot(provider, profile_id)) or ""
    except Exception as e:
        logger.warning(f"Could not read the key for {provider} profile {profile_id}: {type(e).__name__}")
        return ""


def key_for_profile(provider: str, profile_id: str) -> str:
    """The real key for one profile (LLM layer and Detect Models only; never for display)."""
    return _key(provider, profile_id)


def active_key(provider: str) -> Tuple[str, str]:
    """(key, profile name) the LLM layer should call with right now."""
    prof = active_profile(provider)
    return _key(provider, prof["id"]), prof["name"]


def provider_has_key(provider: str) -> bool:
    """Any profile of this provider holds a key (used for cloud readiness)."""
    with _lock:
        meta = _meta(_load_all(), provider)
        return any(secret_store.stored(_slot(provider, pid)) for pid in meta["profiles"])


def models_for(provider: str, profile_id: str) -> List[str]:
    with _lock:
        meta = _meta(_load_all(), provider)
        entry = meta["profiles"].get(profile_id) or {}
        return list(entry.get("models") or [])


# ------------------------------------------------------------------ writing

def _check_name(data: Dict[str, Any], provider: str, name: str, ignore_id: Optional[str] = None) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("A profile needs a name.")
    if len(name) > 40:
        raise ValueError("That name is too long (40 characters at most).")
    meta = _meta(data, provider)
    for pid, entry in meta["profiles"].items():
        if pid != ignore_id and (entry.get("name") or "").lower() == name.lower():
            raise ValueError(f"A profile named {name} already exists for {provider}.")
    return name


def _mutate(provider: str, fn) -> Dict[str, Any]:
    with _lock:
        data = _load_all()
        meta = _meta(data, provider)
        fn(data, meta)
        data[provider] = meta
        _save_all(data)
        return list_profiles(provider)


def create_profile(provider: str, name: str, key: str = "") -> Dict[str, Any]:
    if not is_key_provider(provider):
        raise ValueError(f"{provider} does not use API keys.")
    with _lock:
        data = _load_all()
        name = _check_name(data, provider, name)
        profile_id = uuid.uuid4().hex[:10]
        if key:
            secret_store.save(_slot(provider, profile_id), key.strip())
        data.setdefault(provider, _meta(data, provider))
        meta = _meta(data, provider)
        meta["profiles"][profile_id] = {"name": name, "models": [], "models_at": None}
        data[provider] = meta
        _save_all(data)
        return list_profiles(provider)


def rename_profile(provider: str, profile_id: str, name: str) -> Dict[str, Any]:
    def fn(data, meta):
        if profile_id not in meta["profiles"]:
            raise ValueError("No such profile.")
        meta["profiles"][profile_id]["name"] = _check_name(data, provider, name, ignore_id=profile_id)
    return _mutate(provider, fn)


def set_profile_key(provider: str, profile_id: str, key: str) -> Dict[str, Any]:
    with _lock:
        meta = _meta(_load_all(), provider)
        if profile_id not in meta["profiles"]:
            raise ValueError("No such profile.")
        secret_store.save(_slot(provider, profile_id), (key or "").strip())
        return list_profiles(provider)


def delete_profile(provider: str, profile_id: str) -> Dict[str, Any]:
    if profile_id == DEFAULT_ID:
        raise ValueError("The Default profile cannot be deleted (it is the provider's original key).")
    def fn(data, meta):
        if profile_id not in meta["profiles"]:
            raise ValueError("No such profile.")
        del meta["profiles"][profile_id]
        if meta["active"] == profile_id:
            meta["active"] = DEFAULT_ID
    out = _mutate(provider, fn)
    try:
        secret_store.save(_slot(provider, profile_id), "")
    except Exception as e:
        logger.warning(f"Profile deleted but its key stayed in the vault: {type(e).__name__}")
    return out


def set_active(provider: str, profile_id: str) -> Dict[str, Any]:
    def fn(data, meta):
        if profile_id not in meta["profiles"]:
            raise ValueError("No such profile.")
        meta["active"] = profile_id
    return _mutate(provider, fn)


def set_models(provider: str, profile_id: str, models: List[str]) -> Dict[str, Any]:
    """Records what Detect Models found for this key: its allowed models."""
    ids = [str(m) for m in (models or []) if m]
    def fn(data, meta):
        if profile_id not in meta["profiles"]:
            raise ValueError("No such profile.")
        meta["profiles"][profile_id]["models"] = ids
        meta["profiles"][profile_id]["models_at"] = time.time()
    return _mutate(provider, fn)
