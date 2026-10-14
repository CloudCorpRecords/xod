"""
Xod plugs itself in: it keeps finding the AI on this computer and keeps it working, without being asked.

Every CHECK_EVERY seconds (and whenever the model picker opens) it looks at:
  - model servers on this computer: Ollama, LM Studio, and any other OpenAI-compatible one on the usual ports
    (llama.cpp, Jan, KoboldCpp, GPT4All, text-generation-webui) or listed in XOD_LOCAL_SERVERS
    (comma-separated, e.g. http://localhost:9000), with their models
  - model apps that are installed but not running (Ollama, LM Studio, Jan)
  - this computer (services/machine.py): GPU, memory, power

and then fixes what it safely can:
  - the chosen server is installed but not running: start it (at most once every RESTART_EVERY seconds)
  - a crashed Ollama left its model processes behind, holding GPU memory: end them
  - no model chosen yet (a new install), or the one Xod chose for itself stopped working: connect to the best
    model it can find. A model the person picked is never replaced; if it stops working Xod says what else there is.

What it did and what it noticed go to events() (the page shows them as notices) and the log.
"""
import asyncio
import logging
import os
import platform
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import psutil

logger = logging.getLogger("xod.autoconnect")

CHECK_EVERY = 30
RESTART_EVERY = 300
PROBE_TIMEOUT = 0.8
# Other model servers, by their usual port. Xod's own port is skipped.
OTHER_SERVERS = [(8080, "llama.cpp"), (1337, "Jan"), (5001, "KoboldCpp"), (4891, "GPT4All"), (5000, "text-generation-webui")]
# The processes Ollama runs models in: llama-server (0.40+), ollama_llama_server and `ollama runner` (older).
RUNNER_NAMES = {"llama-server", "ollama_llama_server", "ollama"}
OWNERS = {"llamacpp": "llama.cpp", "koboldcpp": "KoboldCpp", "jan": "Jan", "gpt4all": "GPT4All"}

_events: List[Dict[str, Any]] = []
_notes: List[str] = []
_servers: List[Dict[str, Any]] = []
_apps: Dict[str, Optional[str]] = {}
_last_try: Dict[str, float] = {}
_last_check = 0.0
_last_problem: Optional[str] = None
_lock: Optional[asyncio.Lock] = None
_seq = 0


def _event(kind: str, message: str) -> None:
    global _seq
    _seq += 1
    _events.append({"id": _seq, "at": time.time(), "kind": kind, "message": message})
    del _events[:-30]
    logger.info(message)


def events(since: int = 0) -> List[Dict[str, Any]]:
    return [e for e in _events if e["id"] > since]


# ---------------------------------------------------------------- finding servers and apps

async def _models(c: httpx.AsyncClient, base: str) -> Optional[List[Dict[str, Any]]]:
    """An OpenAI-compatible server's models, or None if nothing answers there."""
    try:
        r = await c.get(f"{base}/v1/models")
        if r.status_code != 200:
            return None
        return [m for m in r.json().get("data", []) if isinstance(m, dict) and m.get("id")]
    except Exception:
        return None


async def discover() -> List[Dict[str, Any]]:
    """Every model server answering on this computer right now."""
    from app.config import settings
    from app.services import ollama_hub
    own_port = int(getattr(settings, "port", 8000) or 8000)
    found: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as c:
        async def ollama():
            try:
                version = (await c.get(f"{ollama_hub.root()}/api/version")).json().get("version")
            except Exception:
                return
            try:
                models = await ollama_hub.installed()
            except Exception:
                models = []
            found.append({"provider": "ollama", "name": "Ollama", "version": version, "base_url": f"{ollama_hub.root()}/v1",
                          "models": [{"id": m["name"], "tools": m["tools"], "size_gb": m["size_gb"]} for m in models if not m["embedding"]]})

        async def lm_studio():
            from app.services.llm import llm_service
            if await _models(c, llm_service.get_lmstudio_native_base()) is None:
                return                  # a quick look first: its full status waits ~2 s for a port nobody listens on
            s = await llm_service.get_lmstudio_status()
            if s.get("online"):
                chat = [m for m in s.get("models", []) if m.get("type") != "embedding"]
                found.append({"provider": "lm_studio", "name": "LM Studio", "base_url": f"{s['server_url']}/v1",
                              "loaded": (s.get("loaded_model") or {}).get("key"),
                              "models": [{"id": m["key"], "tools": bool((m.get("capabilities") or {}).get("trained_for_tool_use")),
                                          "size_gb": m.get("size_gb")} for m in chat]})

        async def other(base: str, label: str):
            if base.rstrip("/").endswith(f":{own_port}"):
                return
            models = await _models(c, base)
            if models is None:
                return
            owner = next((OWNERS[m.get("owned_by", "").lower()] for m in models if m.get("owned_by", "").lower() in OWNERS), label)
            found.append({"provider": "local", "name": owner, "base_url": f"{base}/v1",
                          "models": [{"id": m["id"], "tools": None, "size_gb": None} for m in models]})

        extra = [u.strip().rstrip("/").removesuffix("/v1") for u in os.environ.get("XOD_LOCAL_SERVERS", "").split(",") if u.strip()]
        await asyncio.gather(ollama(), lm_studio(), *[other(f"http://127.0.0.1:{p}", n) for p, n in OTHER_SERVERS],
                             *[other(u, "Local server") for u in extra if u.startswith(("http://127.0.0.1", "http://localhost"))],
                             return_exceptions=True)
    return found


async def local_status(base_url: str, model: str) -> Dict[str, Any]:
    """For the header, when the model is on another local server: is it answering, with this model?"""
    server = next((s["name"] for s in _servers if s["base_url"] == base_url), "Local server")
    async with httpx.AsyncClient(timeout=2) as c:
        models = await _models(c, base_url[:-3] if base_url.endswith("/v1") else base_url)
    if models is None:
        return {"cloud": False, "provider": "local", "server": server, "model": model, "ready": False, "problem": f"{server} isn't running"}
    ids = [m["id"] for m in models]
    ok = not ids or model in ids
    return {"cloud": False, "provider": "local", "server": server, "model": model, "ready": ok,
            "problem": None if ok else f"{model} isn't loaded in {server}"}


def installed_apps() -> Dict[str, Optional[str]]:
    """Model apps on this computer and where (None: not installed)."""
    from app.services.ollama_hub import _exe as ollama_exe
    home, local = Path.home(), Path(os.environ.get("LOCALAPPDATA", ""))
    def first(*paths):
        return next((str(p) for p in paths if p and Path(p).exists()), None)
    return {
        "ollama": ollama_exe(),
        "lm_studio": first(home / ".lmstudio" / "bin" / ("lms.exe" if platform.system() == "Windows" else "lms"),
                           local / "Programs" / "LM Studio" / "LM Studio.exe", "/Applications/LM Studio.app"),
        "jan": first(local / "Programs" / "Jan" / "Jan.exe", "/Applications/Jan.app"),
    }


# ---------------------------------------------------------------- fixing

def ollama_orphans() -> List[psutil.Process]:
    """Model processes a crashed Ollama left running: inside Ollama's own folder, whose parent is gone.
    (A llama.cpp server you started yourself lives elsewhere and is never touched.)"""
    from app.services.ollama_hub import _exe
    exe = _exe()
    if not exe:
        return []
    home = Path(exe).resolve().parent
    out = []
    for p in psutil.process_iter(["name"]):       # names are cheap; parents and paths are not (~3 s for all on Windows)
        try:
            name = (p.info["name"] or "").lower()
            if name.removesuffix(".exe") not in RUNNER_NAMES:
                continue
            if name.startswith("ollama") and "runner" not in p.cmdline():
                continue                                     # the server itself, not a model process
            if Path(p.exe()).resolve().is_relative_to(home) and not psutil.pid_exists(p.ppid()):
                out.append(p)
        except (psutil.Error, OSError, ValueError):
            continue
    return out


def free_ollama_orphans() -> Optional[str]:
    """Ends Ollama's orphaned model processes; returns how much memory that freed, for people (None: there were none).
    Most of what they hold is GPU memory, which the process's own size doesn't show, so that's measured around it."""
    from app.services import machine
    orphans = ollama_orphans()
    if not orphans:
        return None
    def gpu_used():
        pc = machine.profile(fresh=True)
        return next((g.get("used_gb") for g in pc["gpus"] if g["name"] == pc.get("gpu")), None)
    before, ram = gpu_used(), 0.0
    for p in orphans:
        try:
            ram += p.memory_info().rss / 1024 ** 3
            p.kill()
        except psutil.Error:
            continue
    time.sleep(1.5)                     # the driver takes a moment to hand the memory back
    after = gpu_used()
    if before is not None and after is not None and before - after > ram:
        return f"{before - after:.1f} GB of GPU memory"
    return f"{ram:.1f} GB of memory"


def _may_try(what: str) -> bool:
    if time.time() - _last_try.get(what, 0) < RESTART_EVERY:
        return False
    _last_try[what] = time.time()
    return True


async def _start_lm_studio(lms: str) -> bool:
    if not lms or not Path(lms).name.lower().startswith("lms"):
        return False          # only the command-line tool can start its server without opening a window
    proc = await asyncio.create_subprocess_exec(lms, "server", "start", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(proc.wait(), 60)
    except asyncio.TimeoutError:
        return False
    return proc.returncode == 0


def rank(servers: List[Dict[str, Any]], budget_gb: float) -> List[Dict[str, Any]]:
    """Every model on offer, best first: one that can call tools, fits this computer, then the biggest that fits.
    (A model already loaded in LM Studio comes before others there: nothing to load.)"""
    out = []
    for s in servers:
        for m in s["models"]:
            size = m.get("size_gb") or 0
            fits = not size or size + 2.5 <= budget_gb
            score = (m.get("tools") is not False) * 4 + fits * 2 + (s.get("loaded") == m["id"]) * 1
            out.append({"provider": s["provider"], "server": s["name"], "base_url": s["base_url"], "model": m["id"],
                        "tools": m.get("tools"), "size_gb": size or None, "fits": fits, "_key": (score, size if fits else -size)})
    out.sort(key=lambda x: x["_key"], reverse=True)
    for x in out:
        x.pop("_key")
    return out


async def _usable(provider: str, model: str, servers: List[Dict[str, Any]]) -> Optional[str]:
    """None if the current choice can answer; else why not."""
    if provider not in ("ollama", "lm_studio", "local"):
        return None            # cloud models and the built-in engine look after themselves
    from app.services.llm import PROVIDER_NAMES
    s = next((x for x in servers if x["provider"] == provider), None)
    if not s:
        return f"{PROVIDER_NAMES[provider]} isn't running"
    if provider == "ollama":
        from app.services.ollama_context import _same
        if not any(_same(model, {"name": m["id"]}) for m in s["models"]):
            return f"{model} isn't installed in Ollama"
    elif model not in [m["id"] for m in s["models"]] and s["models"]:
        return f"{model} isn't available in {s['name']}"
    return None


async def check() -> Dict[str, Any]:
    """One look around, fixing what can be fixed. Safe to call any time; calls don't overlap."""
    global _lock, _servers, _apps, _notes, _last_check, _last_problem
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        from app.config import settings
        from app.services import machine, ollama_hub, setup_wizard
        from app.services.storage import get_setting
        _apps = await asyncio.to_thread(installed_apps)
        notes: List[str] = []

        freed = await asyncio.to_thread(free_ollama_orphans)
        if freed:
            _event("fixed", f"Freed {freed} that a crashed Ollama had left in use.")

        provider, model = settings.llm_provider, settings.llm_model
        servers = await discover()
        problem = await _usable(provider, model, servers)

        # The chosen server is installed but not running: start it.
        if problem and "isn't running" in problem:
            started = False
            if provider == "ollama" and _apps.get("ollama") and _may_try("ollama"):
                started = (await ollama_hub.start_ollama()).get("success", False)
            elif provider == "lm_studio" and _may_try("lm_studio"):
                started = await _start_lm_studio(_apps.get("lm_studio") or "")
            if started:
                _event("fixed", f"Started {'Ollama' if provider == 'ollama' else 'LM Studio'}, which wasn't running.")
                servers = await discover()
                problem = await _usable(provider, model, servers)

        pc = await asyncio.to_thread(machine.profile)
        ranked = rank(servers, pc["ai_memory_gb"])
        never_chosen = not get_setting("llm_provider", "")
        mine = never_chosen or setup_wizard.chosen_by() == "auto"
        if problem or never_chosen:
            best = next((r for r in ranked if r["tools"] is not False and r["fits"]), ranked[0] if ranked else None)
            if best and mine and (best["provider"], best["model"]) != (provider, model):
                res = setup_wizard.choose(best["provider"], best["model"], best["base_url"], how="auto")
                if res.get("success"):
                    # On a new install the "problem" is only the built-in default (LM Studio) not being there: not news.
                    _event("connected", f"Connected to {best['server']}: {best['model']}." + (f" ({problem}.)" if problem and not never_chosen else ""))
                    problem = None
            elif problem and not mine:
                alt = f" {best['server']} has {best['model']} ready: pick it in Tools → Setup & AI model." if best else ""
                if problem != _last_problem:          # once per problem, not every check
                    _event("problem", f"{problem}.{alt}")
            elif problem and not best and provider in ("ollama", "lm_studio", "local"):
                notes.append(f"{problem}, and no other model was found on this computer. Open Tools → Setup & AI model to get one.")

        # Another program filling the GPU: answers slow down, so say why.
        if pc["runs_on"] == "gpu" and provider == "ollama" and not problem:
            room = await ollama_hub.room_now(pc)
            if room < pc["ai_memory_gb"] * 0.4:
                notes.append(f"Other programs are using most of your GPU's memory (only {room:g} GB free for models), so answers may be slower.")
        if pc.get("power", {}).get("battery") and not pc["power"]["plugged_in"] and provider in ("ollama", "lm_studio", "local", "xod"):
            notes.append("On battery: local models use a lot of power.")

        _servers, _notes, _last_check, _last_problem = servers, notes, time.time(), problem
        return state()


def state() -> Dict[str, Any]:
    from app.config import settings
    from app.services import machine, setup_wizard
    return {"machine": machine.profile(), "servers": _servers, "apps": _apps, "notes": _notes, "events": _events[-10:],
            "current": {"provider": settings.llm_provider, "model": settings.llm_model, "base_url": settings.llm_base_url,
                        "chosen_by": setup_wizard.chosen_by()},
            "checked_at": _last_check}


async def loop() -> None:
    if os.environ.get("XOD_AUTOCONNECT", "").lower() == "off":
        return                      # checks then only run when asked (POST /api/autoconnect/check); tests use this
    await asyncio.sleep(3)          # let the server finish starting
    while True:
        try:
            await check()
        except Exception as e:
            logger.warning(f"Autoconnect check failed: {e}")
        await asyncio.sleep(CHECK_EVERY)
