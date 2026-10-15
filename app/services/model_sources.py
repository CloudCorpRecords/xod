"""
Model servers you add yourself, beyond the ones Xod finds on this computer (services/autoconnect.py).

  on your network   a PC with a big GPU in the next room, a home server, a NAS, another computer on Tailscale:
                    any OpenAI-compatible server (Ollama, LM Studio, llama.cpp, vLLM…). Used as provider "local":
                    the conversation stays on your network.
  online            an OpenAI-compatible service with a key (Together, DeepInfra, Mistral, xAI, a company endpoint…).
                    Provider "custom", treated like the other cloud providers: shown as online, never picked by itself.

Keys go in the Keychain / Credential Manager. Xod never switches to one of these by itself; you pick it.
"""
import ipaddress
import json
import re
import socket
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.paths import DATA_DIR

PATH = DATA_DIR / "model_sources.json"


def _load() -> List[Dict[str, Any]]:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(sources: List[Dict[str, Any]]) -> None:
    PATH.write_text(json.dumps(sources, indent=1), encoding="utf-8")


def _key_name(sid: str) -> str:
    return f"model_source_{sid}"


def key_for(base_url: str) -> str:
    """The key saved with the source at this address ('' if none)."""
    from app.services import secret_store
    base = _base(base_url)
    for s in _load():
        if s["url"] == base and s.get("has_key"):
            return secret_store.get(_key_name(s["id"]))
    return ""


def _base(url: str) -> str:
    """http://host:port (no /v1, no trailing slash)."""
    u = (url or "").strip().rstrip("/")
    return re.sub(r"/v1$", "", u)


def where(url: str) -> str:
    """'this computer', 'your network' or 'online', from the address."""
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return "this computer"
    if host.endswith((".local", ".lan", ".home", ".internal", ".ts.net")):
        return "your network"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(host))
        except (OSError, ValueError):
            return "online"
    if ip.is_loopback:
        return "this computer"
    if ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10"):     # home networks, and Tailscale
        return "your network"
    return "online"


def is_mine(base_url: str) -> bool:
    """A source the person added (so it may be chosen though it isn't on this computer)."""
    base = _base(base_url)
    return any(s["url"] == base for s in _load())


async def _models(base: str, key: str = "") -> Optional[List[str]]:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get(f"{base}/v1/models", headers=headers)
        if r.status_code in (401, 403):
            return None
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", []) if isinstance(m, dict) and m.get("id")]
    except Exception:
        return None


async def add(url: str, name: str = "", key: str = "") -> Dict[str, Any]:
    """Checks the server answers (with the key), then remembers it. Returns its models."""
    raw = (url or "").strip()
    if raw and "://" not in raw:
        raw = "http://" + raw
    base = _base(raw)
    p = urlparse(base)
    if p.scheme not in ("http", "https") or not p.hostname:
        return {"success": False, "error": "That isn't a web address. It looks like http://192.168.1.20:11434."}
    place = where(base)
    if place == "online" and p.scheme != "https":
        return {"success": False, "error": "An online server must use https, so your conversations aren't sent in the open."}
    if place == "this computer":
        return {"success": False, "error": "Servers on this computer are found by themselves: no need to add them."}
    models = await _models(base, key.strip())
    if models is None:
        return {"success": False, "error": ("It didn't answer as an OpenAI-compatible server"
                                            + (" with that key." if key else ". If it needs a key, add it.")
                                            + " For Ollama on another PC, start it with OLLAMA_HOST=0.0.0.0 so it listens on the network.")}
    sources = [s for s in _load() if s["url"] != base]
    sid = uuid.uuid4().hex[:8]
    if key.strip():
        from app.services import secret_store
        secret_store.save(_key_name(sid), key.strip())
    src = {"id": sid, "url": base, "name": (name or p.hostname)[:60], "where": place, "has_key": bool(key.strip())}
    _save(sources + [src])
    return {"success": True, "source": src, "models": models}


def remove(sid: str) -> bool:
    sources = _load()
    keep = [s for s in sources if s["id"] != sid]
    if len(keep) == len(sources):
        return False
    from app.services import secret_store
    secret_store.save(_key_name(sid), "")
    _save(keep)
    return True


def listing() -> List[Dict[str, Any]]:
    return _load()


async def discover() -> List[Dict[str, Any]]:
    """The added sources that answer now, as servers (the same shape autoconnect.discover() gives)."""
    out = []
    from app.services import secret_store
    for s in _load():
        models = await _models(s["url"], secret_store.get(_key_name(s["id"])) if s.get("has_key") else "")
        out.append({"provider": "local" if s["where"] == "your network" else "custom", "name": s["name"], "base_url": f"{s['url']}/v1",
                    "where": s["where"], "source_id": s["id"], "online": models is not None,
                    "models": [{"id": m, "tools": None, "size_gb": None} for m in (models or [])]})
    return out
