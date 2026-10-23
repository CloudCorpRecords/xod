"""
1Password, for people who keep their keys and passwords there (through 1Password's own `op` command).

- API keys: a key can be a 1Password reference (op://Vault/Item/field) instead of the key itself. Xod keeps only the
  reference in its vault and asks 1Password for the key when it needs it, so a key you rotate in 1Password is the
  one Xod uses, and the key is never stored anywhere by Xod.
- Website logins: with logins turned on, a site that has no login saved in Xod uses the matching Login item in
  1Password (by its website), typed by the browser code exactly like a saved login; the model never sees it.

Nothing here turns on by itself: `op` must be installed and you connect it once in Settings (1Password asks you to
approve, with Touch ID when its app is set up for the command line). What 1Password returns stays in memory only,
never in logs, files or prompts. Item lists Xod shows hold titles, websites and field names, never values.
"""
import json
import logging
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("xod.1password")

ENABLED_KEY = "onepassword_enabled"
LOGINS_KEY = "onepassword_logins"
INSTALL_URL = "https://developer.1password.com/docs/cli/get-started/"
CACHE_SECONDS = 600
CATEGORIES = "API Credential,Password,Login,Secure Note,Database,Server"

_lock = threading.Lock()
_values: Dict[str, Tuple[float, str]] = {}
_items: Dict[str, Any] = {"at": 0.0, "items": None}


class OnePasswordError(Exception):
    pass


def op_path() -> Optional[str]:
    from app.services.agent_clis import search_path
    return shutil.which("op", path=search_path())


def _run(args: List[str], timeout: int = 60) -> str:
    """Runs `op`; its output may hold a secret, so it's returned, never logged."""
    path = op_path()
    if not path:
        raise OnePasswordError("The 1Password command line tool (op) isn't installed.")
    from app.services.agent_clis import search_path
    import os
    env = dict(os.environ, PATH=search_path())
    try:
        r = subprocess.run([path, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired:
        raise OnePasswordError("1Password didn't answer in time (was its approval left waiting?).")
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        raise OnePasswordError(_friendly(err[-1] if err else f"op exited with {r.returncode}"))
    return r.stdout


def _friendly(err: str) -> str:
    e = err.lower()
    if "not currently signed in" in e or "no accounts configured" in e or "authorization" in e or "signin" in e:
        return ("1Password isn't signed in for the command line. In the 1Password app open Settings → Developer and turn "
                "on Integrate with 1Password CLI, then Connect again.")
    if "isn't an item" in e or "could not find" in e or "not found" in e:
        return "1Password has no item or field by that name."
    return err.split("] ", 1)[-1][:300]


def _setting(key: str) -> bool:
    from app.services.storage import get_setting
    return get_setting(key, "") == "1"


def enabled() -> bool:
    return _setting(ENABLED_KEY) and bool(op_path())


def status() -> Dict[str, Any]:
    """What Settings shows. Never asks 1Password anything that needs your approval."""
    path = op_path()
    accounts: List[Dict[str, str]] = []
    if path:
        try:
            accounts = [{"email": a.get("email", ""), "url": a.get("url", "")}
                        for a in json.loads(_run(["account", "list", "--format", "json"], timeout=15) or "[]")]
        except (OnePasswordError, ValueError):
            pass
    return {"installed": bool(path), "accounts": accounts, "connected": _setting(ENABLED_KEY) and bool(path),
            "logins": _setting(LOGINS_KEY), "install_url": INSTALL_URL}


def connect() -> Dict[str, Any]:
    """Asks 1Password once (it shows its own approval) and turns the integration on."""
    from app.services.storage import set_setting
    me = json.loads(_run(["whoami", "--format", "json"]) or "{}")
    set_setting(ENABLED_KEY, "1")
    return {"success": True, "email": me.get("email", ""), "url": me.get("url", "")}


def disconnect() -> Dict[str, Any]:
    from app.services.storage import set_setting
    set_setting(ENABLED_KEY, "")
    set_setting(LOGINS_KEY, "")
    with _lock:
        _values.clear()
        _items.update(at=0.0, items=None)
    return {"success": True}


def set_logins(on: bool) -> Dict[str, Any]:
    from app.services.storage import set_setting
    set_setting(LOGINS_KEY, "1" if on else "")
    return {"success": True, "logins": on}


# ------------------------------------------------------------------ keys
def is_reference(value: str) -> bool:
    return (value or "").startswith("op://")


def read(reference: str) -> str:
    """The value a reference points to ("" if 1Password can't give it now); kept in memory for a few minutes."""
    with _lock:
        hit = _values.get(reference)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    if not enabled():
        return ""
    try:
        value = _run(["read", "--no-newline", reference]).strip()
    except OnePasswordError as e:
        logger.warning(f"1Password couldn't read a key: {e}")
        return ""
    with _lock:
        _values[reference] = (time.time(), value)
    return value


def items(query: str = "") -> List[Dict[str, Any]]:
    """Your items that can hold a key or a login: title, vault, kind and websites (no values)."""
    if not enabled():
        raise OnePasswordError("Connect 1Password first.")
    with _lock:
        cached = _items["items"] if time.time() - _items["at"] < 120 else None
    if cached is None:
        raw = json.loads(_run(["item", "list", "--categories", CATEGORIES, "--format", "json"]) or "[]")
        cached = [{"id": i.get("id"), "title": i.get("title", ""), "vault": (i.get("vault") or {}).get("name", ""),
                   "category": i.get("category", ""), "urls": [u.get("href", "") for u in i.get("urls") or []]} for i in raw]
        with _lock:
            _items.update(at=time.time(), items=cached)
    q = query.strip().lower()
    return [i for i in cached if not q or q in i["title"].lower() or any(q in u.lower() for u in i["urls"])][:50]


def fields(item_id: str) -> List[Dict[str, str]]:
    """An item's fields as name + op:// reference, without their values (to pick which one is the key)."""
    if not enabled():
        raise OnePasswordError("Connect 1Password first.")
    item = json.loads(_run(["item", "get", item_id, "--format", "json"]) or "{}")
    return [{"label": f.get("label") or f.get("id", ""), "type": f.get("type", ""), "reference": f["reference"]}
            for f in item.get("fields") or [] if f.get("reference") and (f.get("value") or f.get("type") == "CONCEALED")]


# ------------------------------------------------------------------ website logins
def _login_item(site: str) -> Optional[Dict[str, Any]]:
    if not (enabled() and _setting(LOGINS_KEY)):
        return None
    from app.services.credential_vault import site_key
    try:
        for i in items():
            if i["category"] == "LOGIN" and any(u and site_key(u) == site for u in i["urls"]):
                return i
    except (OnePasswordError, ValueError) as e:
        logger.warning(f"1Password logins: {e}")
    return None


def describe_login(site: str) -> Optional[Dict[str, Any]]:
    """That 1Password has a login for the site (its title, not the username or password)."""
    i = _login_item(site)
    return {"site": site, "username": f"from 1Password ({i['title']})", "source": "1Password"} if i else None


def get_login(site: str) -> Optional[Tuple[str, str]]:
    """(username, password) from the site's Login item in 1Password; only browser code calls this."""
    i = _login_item(site)
    if not i:
        return None
    try:
        got = json.loads(_run(["item", "get", i["id"], "--fields", "label=username,label=password", "--reveal",
                               "--format", "json"]) or "[]")
    except (OnePasswordError, ValueError) as e:
        logger.warning(f"1Password login for {site}: {e}")
        return None
    by = {(f.get("label") or f.get("id") or "").lower(): f.get("value") or "" for f in (got if isinstance(got, list) else [got])}
    return (by["username"], by["password"]) if by.get("username") and by.get("password") else None
