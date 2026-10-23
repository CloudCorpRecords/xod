"""
Saved website logins for browser missions.

Passwords live in the macOS Keychain (via `keyring`), never in files, logs, or LLM
prompts. A small index in data/vault_index.json records which sites have a login
and the username, so the agent can be told "a saved login exists" without seeing it.
"""
import json
import logging
import os
import secrets
import string
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import keyring
from app.services import keychain
from app.paths import DATA_DIR

logger = logging.getLogger("xod.vault")

INDEX_PATH = os.environ.get("XOD_VAULT_INDEX", str(DATA_DIR / "vault_index.json"))
# Keychain items show up as "Xod: indeed.com" in Keychain Access.
SERVICE_PREFIX = os.environ.get("XOD_VAULT_PREFIX", "Xod")

_lock = threading.Lock()


def site_key(url_or_domain: str) -> str:
    """'https://secure.indeed.com/auth?x' -> 'indeed.com'; 'localhost:8799' -> 'localhost:8799'."""
    raw = (url_or_domain or "").strip().lower()
    if "://" not in raw:
        raw = "https://" + raw
    host = urllib.parse.urlparse(raw).netloc or raw
    if "@" in host:
        host = host.split("@", 1)[1]
    name, _, port = host.partition(":")
    if name in ("localhost", "127.0.0.1") or name.replace(".", "").isdigit():
        return f"{name}:{port}" if port else name
    parts = name.split(".")
    if parts[0] == "www":
        parts = parts[1:]
    # Keep three labels for two-part public suffixes like co.uk / com.au.
    if len(parts) >= 3 and len(parts[-2]) <= 3 and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _service(site: str) -> str:
    return f"{SERVICE_PREFIX}: {site}"


def _read_index() -> Dict[str, Any]:
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"Vault index unreadable: {e}")
        return {}


def _write_index(index: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
    os.replace(tmp, INDEX_PATH)


def generate_password(length: int = 20) -> str:
    """Strong password that satisfies the usual 'upper, lower, digit, symbol' rules."""
    symbols = "!@#$%^&*-_"
    alphabet = string.ascii_letters + string.digits + symbols
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in symbols for c in pw)):
            return pw


def save_login(url_or_domain: str, username: str, password: str, source: str = "manual") -> Dict[str, Any]:
    site = site_key(url_or_domain)
    if not site or not username or not password:
        raise ValueError("site, username and password are required")
    with _lock:
        index = _read_index()
        old = index.get(site)
        if old and old.get("username") != username:
            try:
                keyring.delete_password(_service(site), old["username"])
            except Exception:
                pass
        keychain.set_item(_service(site), username, password)   # in place: other copies of Xod keep access
        index[site] = {"username": username, "saved_at": time.strftime("%Y-%m-%d %H:%M"), "source": source}
        _write_index(index)
    logger.info(f"Saved login for {site} ({source})")
    return {"site": site, "username": username}


def get_login(url_or_domain: str) -> Optional[Tuple[str, str]]:
    """(username, password) for the site, or None. Only browser code should call this."""
    site = site_key(url_or_domain)
    entry = _read_index().get(site)
    if not entry:
        from app.services import onepassword     # none saved here: the site's login in 1Password, if that's on
        return onepassword.get_login(site)
    try:
        pw = keyring.get_password(_service(site), entry["username"])
    except Exception as e:
        logger.warning(f"Keychain read failed for {site}: {e}")
        return None
    return (entry["username"], pw) if pw else None


def describe_login(url_or_domain: str) -> Optional[Dict[str, Any]]:
    """Safe-to-show metadata (no password)."""
    site = site_key(url_or_domain)
    entry = _read_index().get(site)
    if not entry:
        from app.services import onepassword
        return onepassword.describe_login(site)
    return {"site": site, **entry}


def list_logins() -> List[Dict[str, Any]]:
    return [{"site": k, **v} for k, v in sorted(_read_index().items())]


def delete_login(url_or_domain: str) -> bool:
    site = site_key(url_or_domain)
    with _lock:
        index = _read_index()
        entry = index.pop(site, None)
        if not entry:
            return False
        try:
            keyring.delete_password(_service(site), entry["username"])
        except Exception:
            pass
        _write_index(index)
    return True
