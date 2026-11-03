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

Saving changes the item in place (SecItemUpdate). keyring's own save deletes the item and makes a new one, and a new
item trusts only the app that made it: after an update (a new Python inside Xod.app), or with Xod run from source
and installed side by side, every save locked the other copy out, and that copy then saw no keys at all. A changed
item keeps the apps it trusted, and macOS adds one more on "Always Allow".

A vault that can't be read (macOS asked and the answer was Deny, or nobody saw the prompt) is never written over: the
read is tried again next time, problem() says what happened, and saves fail loudly instead of replacing your keys.

On Windows the vault lives in Credential Manager (keyring's Windows backend, protected per user by Windows),
which holds at most 2,560 bytes per item: there it's split into pieces ("_vault.1", "_vault.2", …) and the
"_vault" item only says how many there are.
"""
import ctypes
import json
import logging
import os
import sys
import threading
from typing import Dict, Optional

import keyring
from keyring import errors  # noqa: F401  (callers catch keyring.errors via this module)

logger = logging.getLogger("xod.secrets")

DEFAULT_SERVICE = "Xod API keys"
SERVICE = os.environ.get("XOD_SECRETS_SERVICE", DEFAULT_SERVICE)
ACCOUNT = "_vault"
CHUNK = 1000 if sys.platform == "win32" else 0   # characters per piece (Windows stores them as UTF-16)
LEGACY = SERVICE == DEFAULT_SERVICE  # only the real vault looks at the old one-per-key items

_lock = threading.RLock()
_vault: Optional[Dict[str, object]] = None
_problem: Optional[str] = None


class Unreadable(errors.KeyringError):
    """The vault is there but Xod can't read it now; nothing may be written over it."""


def _key(service: str, account: str) -> str:
    return f"{service}\n{account}"


def _load() -> Dict[str, object]:
    global _vault, _problem
    if _vault is None:
        try:
            raw = keyring.get_password(SERVICE, ACCOUNT)
            if raw and raw.startswith('{"pieces":'):
                n = int(json.loads(raw)["pieces"])
                raw = "".join(keyring.get_password(SERVICE, f"{ACCOUNT}.{i}") or "" for i in range(1, n + 1))
        except Exception as e:
            _problem = (f"macOS didn't let Xod read its saved keys ({type(e).__name__}). Choose Try again, then "
                        "Always Allow when macOS asks.")
            logger.warning(f"Keychain read failed: {type(e).__name__}: {e}")
            raise Unreadable(_problem) from e
        try:
            vault = json.loads(raw) if raw else {}
        except ValueError:
            # Kept under another name before starting over, so nothing is lost for good.
            logger.error("Xod's Keychain vault is unreadable; kept it as _vault.unreadable and starting a new one")
            try:
                set_item(SERVICE, f"{ACCOUNT}.unreadable", raw)
            except Exception as e:
                logger.error(f"Could not keep the unreadable vault copy either: {type(e).__name__}: {e}")
            vault = {}
        vault.setdefault("secrets", {})
        vault.setdefault("checked", [])
        _vault, _problem = vault, None
    return _vault


def problem() -> Optional[str]:
    """Why the saved keys can't be read right now, or None (reads the vault if it hasn't been yet)."""
    with _lock:
        try:
            _load()
        except Unreadable:
            pass
        return _problem


def set_item(service: str, account: str, value: str) -> None:
    """keyring.set_password, except that on a Mac an existing item is changed in place (see the top of this file)."""
    if sys.platform != "darwin" or not hasattr(keyring.get_keyring(), "keychain"):
        keyring.set_password(service, account, value)
        return
    from keyring.backends.macOS import api
    update = api._sec.SecItemUpdate
    update.restype, update.argtypes = api.OS_status, (ctypes.c_void_p, ctypes.c_void_p)
    q = api.create_query(kSecClass=api.k_("kSecClassGenericPassword"), kSecAttrService=service, kSecAttrAccount=account)
    raw = value.encode("utf-8")
    make = api._found.CFDataCreate
    make.restype, make.argtypes = ctypes.c_void_p, (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long)
    status = update(q, api.create_query(kSecValueData=ctypes.c_void_p(make(None, raw, len(raw)))))   # update wants data, not a string
    if status == api.error.item_not_found:
        status = api.SecItemAdd(api.create_query(kSecClass=api.k_("kSecClassGenericPassword"), kSecAttrService=service,
                                                 kSecAttrAccount=account, kSecValueData=value), None)
    api.Error.raise_for_status(status)


def _store() -> None:
    global _problem
    try:
        _write_vault()
    except Unreadable:
        raise
    except Exception as e:
        # Every unavailability failure (a refused save, no default keychain, interaction
        # not allowed) explains itself through problem() instead of escaping raw.
        _problem = (f"macOS didn't let Xod save its keys ({type(e).__name__}). Your previously "
                    "saved keys were not changed. Choose Try again, then Always Allow when macOS asks.")
        logger.warning(f"Keychain write failed: {type(e).__name__}: {e}")
        raise Unreadable(_problem) from e


def _write_vault() -> None:
    data = json.dumps(_vault)
    if not CHUNK:
        set_item(SERVICE, ACCOUNT, data)
        return
    pieces = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [""]
    for i, piece in enumerate(pieces, 1):
        keyring.set_password(SERVICE, f"{ACCOUNT}.{i}", piece)
    try:
        old = json.loads(keyring.get_password(SERVICE, ACCOUNT) or "{}").get("pieces", 0)
    except (ValueError, AttributeError):
        old = 0
    keyring.set_password(SERVICE, ACCOUNT, json.dumps({"pieces": len(pieces)}))
    for i in range(len(pieces) + 1, int(old) + 1):     # a shorter vault than before: remove the leftover pieces
        try:
            keyring.delete_password(SERVICE, f"{ACCOUNT}.{i}")
        except Exception:
            pass


def get_password(service: str, account: str) -> Optional[str]:
    with _lock:
        try:
            v = _load()
        except Unreadable:
            return None
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
    """Forget what's in memory so the next call reads the Keychain again (Try again, and tests)."""
    global _vault, _problem
    with _lock:
        _vault, _problem = None, None
