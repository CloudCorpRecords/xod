"""
All of Xod's secrets in one Keychain item, read once per run.

Each API key, connector token and identity key used to be its own Keychain item, and macOS asks
for your password separately for every item an app reads (again whenever the Python binary
changes, e.g. after an update). Starting Xod could mean six prompts in a row. Now they live
together in one item (service "Xod API keys", account "_vault", a JSON object), so there's at
most one prompt, and none after "Always Allow".

Same calls as `keyring`: get_password / set_password / delete_password(service, account).
Items from before this are moved in the first time they're asked for and then never read again;
the old items are left alone until that secret is saved or removed (so an older Xod still works).
Tests that set XOD_SECRETS_SERVICE get their own vault and never look at the old items.
"""
import json
import logging
import os
import threading
from typing import Dict, Optional

import keyring
from keyring import errors  # noqa: F401  (callers catch keyring.errors via this module)

logger = logging.getLogger("xod.secrets")

DEFAULT_SERVICE = "Xod API keys"
SERVICE = os.environ.get("XOD_SECRETS_SERVICE", DEFAULT_SERVICE)
ACCOUNT = "_vault"
LEGACY = SERVICE == DEFAULT_SERVICE  # only the real vault looks at the old one-per-key items

_lock = threading.RLock()
_vault: Optional[Dict[str, object]] = None


def _key(service: str, account: str) -> str:
    return f"{service}\n{account}"


def _load() -> Dict[str, object]:
    global _vault
    if _vault is None:
        raw = None
        try:
            raw = keyring.get_password(SERVICE, ACCOUNT)
        except Exception as e:
            logger.warning(f"Keychain read failed: {type(e).__name__}")
        try:
            _vault = json.loads(raw) if raw else {}
        except ValueError:
            logger.error("Xod's Keychain vault is unreadable; starting a new one")
            _vault = {}
        _vault.setdefault("secrets", {})
        _vault.setdefault("checked", [])
    return _vault


def _store() -> None:
    keyring.set_password(SERVICE, ACCOUNT, json.dumps(_vault))


def get_password(service: str, account: str) -> Optional[str]:
    with _lock:
        v = _load()
        k = _key(service, account)
        if k in v["secrets"]:
            return v["secrets"][k]
        if not LEGACY or k in v["checked"]:
            return None
        # First time this one is asked for: bring over the old item if there is one.
        try:
            old = keyring.get_password(service, account)
        except Exception as e:
            logger.warning(f"Keychain read failed for {account}: {type(e).__name__}")
            return None
        if old:
            v["secrets"][k] = old
        v["checked"].append(k)
        try:
            _store()
        except Exception as e:
            logger.warning(f"Keychain write failed: {type(e).__name__}")
        return old


def set_password(service: str, account: str, value: str) -> None:
    with _lock:
        v = _load()
        k = _key(service, account)
        v["secrets"][k] = value
        if k not in v["checked"]:
            v["checked"].append(k)
        _store()
        _drop_legacy(service, account)


def delete_password(service: str, account: str) -> None:
    with _lock:
        v = _load()
        k = _key(service, account)
        had = v["secrets"].pop(k, None) is not None
        if k not in v["checked"]:
            v["checked"].append(k)
        _store()
        if _drop_legacy(service, account) or had:
            return
        raise keyring.errors.PasswordDeleteError(account)


def _drop_legacy(service: str, account: str) -> bool:
    if not LEGACY:
        return False
    try:
        keyring.delete_password(service, account)
        return True
    except Exception:
        return False


def reset_cache() -> None:
    """For tests: forget what's in memory so the next call reads the Keychain again."""
    global _vault
    with _lock:
        _vault = None
