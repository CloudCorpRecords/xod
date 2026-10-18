"""
Which AI model servers are running on this computer, what they hold in memory, and whether Xod is using them.

Xod can answer with a cloud model (an API key) and never touch a local one. Then a local model left running is only
memory used for nothing, so the header says so (report()) and offers to stop it (stop()):
  builtin    Xod's own engine (services/local_engine.py): Xod started it, so Xod stops it. It also stops by itself
             when you switch to another model (after_switch()) and after IDLE_MINUTES with no requests (idle_loop());
             the next request that needs it starts it again.
  lm_studio  LM Studio's loaded models: "stop" unloads them (LM Studio itself keeps running).
  ollama     Ollama's loaded models: "stop" unloads them (keep_alive 0; Ollama itself keeps running).
Xod never unloads LM Studio's or Ollama's models by itself: other apps may be using them.
"""
import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("xod.local_servers")

PROBE_TIMEOUT = 0.8
IDLE_DEFAULT = 15            # minutes; the setting "engine_idle_minutes" (0 = never) or XOD_ENGINE_IDLE_MINUTES
CHECK_EVERY = 60
LOCAL_PROVIDERS = ("xod", "lm_studio", "ollama", "local")


def idle_minutes() -> int:
    from app.services.storage import get_setting
    raw = get_setting("engine_idle_minutes", "") or os.environ.get("XOD_ENGINE_IDLE_MINUTES", "")
    try:
        return max(0, int(raw)) if raw != "" else IDLE_DEFAULT
    except ValueError:
        return IDLE_DEFAULT


def _family_rss_gb(pid: int) -> Optional[float]:
    """Memory held by a process and its children (the engine may run under a launcher)."""
    try:
        import psutil
        p = psutil.Process(pid)
        total = sum(x.memory_info().rss for x in [p] + p.children(recursive=True) if x.is_running())
        return round(total / 1024 ** 3, 1)
    except Exception:
        return None


def _builtin(answering: str) -> Dict[str, Any]:
    from app.services.local_engine import local_engine, catalog_entry
    on = local_engine.running()
    repo = local_engine.repo or ""
    label = (catalog_entry(repo) or {}).get("label") or repo.split("/")[-1]
    used = local_engine.last_used
    return {"id": "builtin", "name": "Built-in engine", "running": on, "in_use": answering == "xod",
            "models": [{"id": repo, "label": label}] if on and repo else [],
            "memory_gb": _family_rss_gb(local_engine.proc.pid) if on and local_engine.proc else None,
            "idle_seconds": round(time.time() - used) if on and used else None, "can_stop": on}


async def _lm_studio(c: httpx.AsyncClient, answering: str) -> Dict[str, Any]:
    from app.services.llm import llm_service
    out = {"id": "lm_studio", "name": "LM Studio", "running": False, "in_use": answering == "lm_studio", "models": [],
           "memory_gb": None, "can_stop": False}
    try:
        r = await c.get(f"{llm_service.get_lmstudio_native_base()}/api/v1/models")
        if r.status_code != 200:
            return out
        loaded = [m for m in r.json().get("models", []) if m.get("loaded_instances")]
    except Exception:
        return out
    out["running"] = True                    # the server answers; "models" says whether anything is loaded
    out["models"] = [{"id": m.get("key"), "label": m.get("display_name") or m.get("key"), "embedding": m.get("type") == "embedding",
                      "instances": [i.get("id") for i in m.get("loaded_instances") or []]} for m in loaded]
    gb = sum(m.get("size_bytes", 0) for m in loaded) / 1024 ** 3
    out["memory_gb"] = round(gb, 1) if loaded else 0
    out["can_stop"] = bool(loaded)
    return out


async def _ollama(c: httpx.AsyncClient, answering: str) -> Dict[str, Any]:
    from app.services.ollama_hub import root
    out = {"id": "ollama", "name": "Ollama", "running": False, "in_use": answering == "ollama", "models": [],
           "memory_gb": None, "can_stop": False}
    try:
        r = await c.get(f"{root()}/api/ps")
        if r.status_code != 200:
            return out
        loaded = r.json().get("models", []) or []
    except Exception:
        return out
    out["running"] = True
    out["models"] = [{"id": m.get("name"), "label": m.get("name"), "embedding": "embed" in (m.get("name") or "")} for m in loaded]
    out["memory_gb"] = round(sum(m.get("size", 0) for m in loaded) / 1024 ** 3, 1) if loaded else 0
    out["can_stop"] = bool(loaded)
    return out


def answering() -> Dict[str, Any]:
    """The model that answers chats right now."""
    from app.services.llm import llm_service, PROVIDER_NAMES, CLOUD_PROVIDERS
    info = llm_service.get_endpoint_info()
    p = info.get("provider") or ""
    online = p in CLOUD_PROVIDERS
    return {"provider": p, "name": PROVIDER_NAMES.get(p, p), "model": str(info.get("model") or "").split("/")[-1],
            "where": "online" if online else "this computer",
            "ready": info.get("api_key") not in ("", "missing", None) if online else None}


async def report() -> Dict[str, Any]:
    """What answers chats, and every model server on this computer with what it holds in memory."""
    a = answering()
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as c:
        lm, ol = await asyncio.gather(_lm_studio(c, a["provider"]), _ollama(c, a["provider"]))
    servers = [_builtin(a["provider"]), lm, ol]
    # Loaded but not what answers chats: memory a cloud (or other) model doesn't need. Small embedding models don't count.
    idle = [s for s in servers if s["can_stop"] and not s["in_use"] and any(not m.get("embedding") for m in s["models"])]
    return {"answering": a, "servers": servers, "unused": [s["id"] for s in idle],
            "unused_gb": round(sum(s["memory_gb"] or 0 for s in idle), 1), "idle_minutes": idle_minutes()}


async def stop(server: str) -> Dict[str, Any]:
    """Frees what a server holds: stops the built-in engine, or unloads LM Studio's / Ollama's models."""
    if server == "builtin":
        from app.services.local_engine import local_engine
        was = local_engine.running()
        await local_engine.stop()
        return {"success": True, "stopped": was, "message": "Stopped the built-in engine. It starts again when a chat needs it."
                if was else "The built-in engine wasn't running."}
    async with httpx.AsyncClient(timeout=30) as c:
        if server == "lm_studio":
            from app.services.llm import llm_service
            s = await _lm_studio(c, "")
            n = 0
            for m in s["models"]:
                for iid in m["instances"]:
                    r = await c.post(f"{llm_service.get_lmstudio_native_base()}/api/v1/models/unload", json={"instance_id": iid})
                    n += r.status_code < 300
            return {"success": True, "stopped": n, "message": f"Unloaded {n} model{'s' if n != 1 else ''} from LM Studio."}
        if server == "ollama":
            from app.services.ollama_hub import root
            s = await _ollama(c, "")
            n = 0
            for m in s["models"]:
                r = await c.post(f"{root()}/api/generate", json={"model": m["id"], "keep_alive": 0})
                n += r.status_code < 300
            return {"success": True, "stopped": n, "message": f"Unloaded {n} model{'s' if n != 1 else ''} from Ollama."}
    return {"success": False, "error": f"Unknown server {server}"}


def after_switch(provider: str) -> None:
    """The chat model changed: if it's no longer the built-in engine, stop that engine now (it holds 3–16 GB)."""
    if provider == "xod":
        return
    from app.services.local_engine import local_engine
    if not local_engine.running():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    logger.info(f"Switched to {provider}: stopping the built-in engine")
    loop.create_task(local_engine.stop())


async def _busy(pid: int) -> bool:
    """Is the engine working on something (a long answer started before the idle limit)?"""
    try:
        import psutil
        fam = [psutil.Process(pid)]
        fam += fam[0].children(recursive=True)
        for p in fam:
            p.cpu_percent(None)
        await asyncio.sleep(1.5)
        return sum(p.cpu_percent(None) for p in fam if p.is_running()) >= 2.0
    except Exception:
        return True                 # can't tell: leave it


async def check_idle() -> Optional[str]:
    """Stops the built-in engine when nothing has needed it for idle_minutes(). Returns what it did."""
    from app.services.local_engine import local_engine
    limit = idle_minutes()
    if not limit or not local_engine.running() or not local_engine.last_used:
        return None
    quiet = time.time() - local_engine.last_used
    if quiet < limit * 60 or (local_engine.proc and await _busy(local_engine.proc.pid)):
        return None
    await local_engine.stop()
    msg = f"Stopped the built-in engine after {limit} minutes unused, to free its memory. It starts again when needed."
    logger.info(msg)
    return msg


async def idle_loop() -> None:
    while True:
        await asyncio.sleep(CHECK_EVERY)
        try:
            await check_idle()
        except Exception as e:
            logger.warning(f"Idle check failed: {e}")
