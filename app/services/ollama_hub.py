"""
Getting local models without leaving Xod (Windows mainly, where the built-in MLX engine doesn't run).

Ollama does the running and downloading; this is the part a person sees:
  - install Ollama with one click if it's missing (Windows: the official installer, signature checked,
    installed silently for this user; no admin needed), and start it if it's installed but not running
  - a short list of recommended models, each marked by whether it fits this computer's GPU
  - download any of them, or any model from ollama.com or Hugging Face (a GGUF repo), with progress
  - list and delete installed models

Downloads run in the background in Xod's server, so closing the setup window doesn't stop them; an
interrupted download resumes where it stopped (Ollama keeps the finished parts).
"""
import asyncio
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.services import ollama_context

logger = logging.getLogger("xod.ollama")

WINDOWS = platform.system() == "Windows"
REGISTRY = "https://registry.ollama.ai/v2/library"
INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"

# Recommended models, smallest first. All call tools (Xod needs that); size_gb is a fallback, the real
# size comes from Ollama's registry. pick: in the running for "Recommended" (the coder model is a
# specialist, so it's offered but never the default).
CATALOG: List[Dict[str, Any]] = [
    {"name": "qwen3.5:4b", "label": "Qwen 3.5 4B", "size_gb": 3.1, "pick": True,
     "blurb": "Small and quick. For GPUs with 6–8 GB, or computers without one."},
    {"name": "qwen3.5:9b", "label": "Qwen 3.5 9B", "size_gb": 6.1, "pick": True,
     "blurb": "Careful on long, multi-step jobs. The best all-rounder for 10–16 GB GPUs."},
    {"name": "gemma4:12b", "label": "Gemma 4 12B", "size_gb": 7.5, "pick": False,
     "blurb": "Google's open model. Good at writing and summaries."},
    {"name": "gpt-oss:20b", "label": "gpt-oss 20B", "size_gb": 12.8, "pick": True,
     "blurb": "OpenAI's open model. Strong reasoning; wants a GPU with 16 GB or more."},
    {"name": "qwen3.5:27b", "label": "Qwen 3.5 27B", "size_gb": 16.3, "pick": True,
     "blurb": "Close to cloud quality on research and planning. Needs a 20 GB+ GPU to be quick."},
    {"name": "qwen3-coder:30b", "label": "Qwen3 Coder 30B", "size_gb": 17.3, "pick": False,
     "blurb": "For building apps and code. Mixture-of-experts, so it stays usable even partly on the CPU."},
]
FIT_HEADROOM_GB = 2.5     # room for the context and the desktop when calling a model "fits"


def root() -> str:
    """Ollama's address: Xod's setting when Ollama is the provider, else OLLAMA_HOST, else the default."""
    from app.config import settings
    if settings.llm_provider == "ollama" and settings.llm_base_url:
        return re.sub(r"/v1/?$", "", settings.llm_base_url.rstrip("/"))
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if host:
        host = host if "://" in host else f"http://{host}"
        return host.replace("0.0.0.0", "localhost").rstrip("/")
    return "http://localhost:11434"


# ---------------------------------------------------------------- Ollama itself

def _exe() -> Optional[str]:
    found = shutil.which("ollama")
    if found:
        return found
    for p in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
              Path("/usr/local/bin/ollama"), Path("/Applications/Ollama.app/Contents/Resources/ollama")):
        if p.is_file():
            return str(p)
    return None


async def _running() -> Optional[str]:
    """Ollama's version if it's answering, else None."""
    try:
        async with httpx.AsyncClient(timeout=1.5) as c:
            return (await c.get(f"{root()}/api/version")).json().get("version") or "?"
    except Exception:
        return None


_install: Dict[str, Any] = {"status": "idle", "error": None, "percent": 0}


async def start_ollama() -> Dict[str, Any]:
    """Starts an installed Ollama (its tray app on Windows, which keeps the server running) and waits for it."""
    if await _running():
        return {"success": True}
    exe = _exe()
    if not exe:
        return {"success": False, "error": "Ollama isn't installed."}
    app = Path(exe).with_name("ollama app.exe")
    cmd = [str(app)] if WINDOWS and app.is_file() else [exe, "serve"]
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
                     start_new_session=not WINDOWS)
    for _ in range(40):
        await asyncio.sleep(0.5)
        if await _running():
            return {"success": True}
    return {"success": False, "error": "Ollama didn't start. Try opening it from the Start menu."}


def _signed_by_ollama(path: Path) -> bool:
    """The installer's Authenticode signature is valid and Ollama's (Windows checks the whole chain)."""
    ps = ("$s = Get-AuthenticodeSignature -LiteralPath $env:XOD_CHECK; "
          "\"$($s.Status)|$($s.SignerCertificate.Subject)\"")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                       timeout=60, env={**os.environ, "XOD_CHECK": str(path)})
    status, _, subject = r.stdout.strip().partition("|")
    return status == "Valid" and subject.startswith("CN=Ollama Inc.,")


async def install_ollama() -> Dict[str, Any]:
    """Windows: downloads the official installer, checks its signature and installs silently for this user."""
    if not WINDOWS:
        return {"success": False, "error": "Get Ollama from ollama.com/download.", "url": "https://ollama.com/download"}
    if _install["status"] in ("downloading", "installing", "starting"):
        return {"success": True, **_install}
    if _exe():
        return {"success": True, **(await start_ollama())}
    _install.update(status="downloading", error=None, percent=0)
    asyncio.get_running_loop().create_task(_install_work())
    return {"success": True, **_install}


async def _install_work() -> None:
    folder = Path(tempfile.mkdtemp(prefix="xod-ollama-"))
    setup = folder / "OllamaSetup.exe"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=120), follow_redirects=True) as c:
            async with c.stream("GET", INSTALLER_URL) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0)
                got = 0
                with open(setup, "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 20):
                        f.write(chunk)
                        got += len(chunk)
                        _install["percent"] = int(got / total * 100) if total else 0
        _install["status"] = "installing"
        if not await asyncio.to_thread(_signed_by_ollama, setup):
            raise RuntimeError("The downloaded installer isn't signed by Ollama, so Xod didn't run it.")
        proc = await asyncio.to_thread(subprocess.run, [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                                       capture_output=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(f"The Ollama installer stopped (code {proc.returncode}).")
        _install["status"] = "starting"
        res = await start_ollama()
        if not res["success"]:
            raise RuntimeError(res["error"])
        _install.update(status="done", percent=100)
    except Exception as e:
        logger.warning(f"Ollama install failed: {e}")
        _install.update(status="error", error=str(e)[:300])
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def install_status() -> Dict[str, Any]:
    return dict(_install)


# ---------------------------------------------------------------- models

_sizes: Dict[str, float] = {}


async def _registry_size_gb(client: httpx.AsyncClient, name: str) -> Optional[float]:
    """Download size of an ollama.com library model, None if there's no such model."""
    if name in _sizes:
        return _sizes[name]
    model, _, tag = name.partition(":")
    path = model if "/" in model else f"library/{model}"
    r = await client.get(f"https://registry.ollama.ai/v2/{path}/manifests/{tag or 'latest'}",
                         headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
    if r.status_code != 200:
        return None
    _sizes[name] = round(sum(l.get("size", 0) for l in r.json().get("layers", [])) / 1024 ** 3, 1)
    return _sizes[name]


async def _drop_orphans(c: httpx.AsyncClient, tags: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deletes Xod's long-context copies whose model was removed outside Xod (`ollama rm`): the copy shares the
    model's files, so until it goes the space isn't freed. Returns the tags still there."""
    gone = set()
    for t in tags:
        if not ollama_context.is_variant(t["name"]):
            continue
        try:
            parent = ((await c.post(f"{root()}/api/show", json={"model": t["name"]})).json().get("details") or {}).get("parent_model") or ""
        except Exception:
            continue
        if parent and not any(ollama_context._same(parent, other) for other in tags):
            await c.request("DELETE", f"{root()}/api/delete", json={"model": t["name"]})
            ollama_context._variants.pop(parent, None)
            gone.add(t["name"])
            logger.info(f"Ollama: removed {t['name']}, left over after {parent} was deleted ({t.get('size', 0) / 1024 ** 3:.1f} GB freed)")
    return [t for t in tags if t["name"] not in gone]


async def installed() -> List[Dict[str, Any]]:
    """Models in Ollama (not Xod's long-context copies), with what they can do."""
    from app.config import settings
    async with httpx.AsyncClient(timeout=5) as c:
        tags = await _drop_orphans(c, (await c.get(f"{root()}/api/tags")).json().get("models", []))
        out = []
        for t in tags:
            if ollama_context.is_variant(t["name"]):
                continue
            try:
                shown = (await c.post(f"{root()}/api/show", json={"model": t["name"]})).json()
            except Exception:
                shown = {}
            caps = shown.get("capabilities") or []
            context = next((v for k, v in (shown.get("model_info") or {}).items() if k.endswith(".context_length") and isinstance(v, int)), None)
            out.append({"name": t["name"], "size_gb": round(t.get("size", 0) / 1024 ** 3, 1), "context": context,
                        "tools": "tools" in caps, "thinking": "thinking" in caps, "vision": "vision" in caps,
                        "embedding": "embedding" in caps and "completion" not in caps,
                        "in_use": settings.llm_provider == "ollama" and ollama_context._same(settings.llm_model or "", t)})
    return out


async def room_now(pc: Dict[str, Any]) -> float:
    """Memory a model could use right now: the computer's AI memory less what *other* programs hold on the GPU
    (a game, another AI app). Ollama's own loaded models don't count: it unloads them to load another."""
    if pc.get("ai_free_gb") is None:
        return pc["ai_memory_gb"]
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            loaded = (await c.get(f"{root()}/api/ps")).json().get("models", [])
        key = "size_vram" if pc["runs_on"] == "gpu" else "size"
        ours = sum(m.get(key, 0) for m in loaded) / 1024 ** 3
    except Exception:
        ours = 0
    return round(min(pc["ai_free_gb"] + ours, pc["ai_memory_gb"]), 1)


async def overview() -> Dict[str, Any]:
    """Everything the model picker shows."""
    from app.services import machine
    version = await _running()
    pc = await asyncio.to_thread(machine.profile)
    budget = pc["ai_memory_gb"]
    room = await room_now(pc) if version else budget
    have = await installed() if version else []
    names = {m["name"] for m in have}
    async with httpx.AsyncClient(timeout=6) as c:
        sizes = await asyncio.gather(*[_registry_size_gb(c, m["name"]) for m in CATALOG], return_exceptions=True)
    catalog = []
    for m, size in zip(CATALOG, sizes):
        size = size if isinstance(size, float) else m["size_gb"]
        catalog.append({**m, "size_gb": size, "fits": size + FIT_HEADROOM_GB <= budget, "fits_now": size + FIT_HEADROOM_GB <= room,
                        "installed": m["name"] in names, "pull": pull_status(m["name"])})
    picks = [m for m in catalog if m["pick"] and m["fits"]]
    rec = picks[-1]["name"] if picks else catalog[0]["name"]
    for m in catalog:
        m["recommended"] = m["name"] == rec
    return {
        "installed_app": bool(_exe()), "running": bool(version), "version": version, "root": root(),
        "can_install": WINDOWS, "install": install_status(),
        "machine": pc, "budget_gb": round(budget, 1), "room_now_gb": round(room, 1),
        "models": have, "catalog": catalog,
        "pulls": [{"name": n, **pull_status(n)} for n in _pulls],
    }


# ---------------------------------------------------------------- finding a model by name or link

_HF = re.compile(r"^(?:https?://)?(?:www\.)?(?:huggingface\.co|hf\.co)/([\w.-]+/[\w.-]+)(?:/(?:blob|resolve|tree)/[^/]+/(.+))?/?$", re.I)
_QUANT = re.compile(r"(I?Q\d(?:_[A-Z0-9]+)*|F16|BF16|F32)", re.I)


async def resolve(query: str) -> Dict[str, Any]:
    """Turns what a person pasted (an ollama.com name or link, a Hugging Face link or user/repo) into a
    name Ollama can download, with its size. Says plainly why not when it can't."""
    q = query.strip().strip('"\'')
    if not q:
        return {"success": False, "error": "Paste a model name or link."}
    q = re.sub(r"^ollama\s+(run|pull)\s+", "", q)
    m = re.match(r"^(?:https?://)?(?:www\.)?ollama\.com/(?:library/)?([\w.-]+(?:/[\w.-]+)?)(?::([\w.-]+))?/?$", q, re.I)
    if m:
        q = m.group(1) + (f":{m.group(2)}" if m.group(2) else "")
    hf = _HF.match(q)
    if hf or q.lower().startswith("hf.co/"):
        return await _resolve_hf(hf.group(1) if hf else q[6:].split(":")[0],
                                 (hf.group(2) if hf else (q.split(":", 1)[1] if ":" in q[6:] else None)))
    if re.fullmatch(r"[\w.-]+/[\w.-]+", q) and not q.startswith("library/"):
        hf_try = await _resolve_hf(q, None)               # user/repo: Hugging Face first, then an ollama.com user model
        if hf_try["success"] or (hf_try.get("exists") and await _ollama_has(q) is None):
            return hf_try                                 # (on Hugging Face but can't run, e.g. no GGUF: say why)
    if not re.fullmatch(r"[\w./-]+(:[\w.-]+)?", q):
        return {"success": False, "error": "That doesn't look like a model name or a Hugging Face / ollama.com link."}
    size = await _ollama_has(q)
    if size == -1:
        return {"success": False, "error": "Couldn't reach ollama.com to check that model. Are you online?"}
    if size is None:
        return {"success": False, "error": f"No model called {q} on ollama.com. Check the name and size tag (for example llama3.1:8b)."}
    return {"success": True, "name": q, "source": "ollama", "size_gb": size}


async def _ollama_has(name: str) -> Optional[float]:
    """Size of an ollama.com model; None if there's no such model, -1 if ollama.com can't be reached."""
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            return await _registry_size_gb(c, name)
    except Exception:
        return -1


async def _resolve_hf(repo: str, file_or_quant: Optional[str]) -> Dict[str, Any]:
    quant = None
    if file_or_quant:
        qm = _QUANT.search(Path(file_or_quant).name if "." in file_or_quant else file_or_quant)
        quant = qm.group(1).upper() if qm else None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://huggingface.co/api/models/{repo}", params={"blobs": "true"})
    except Exception:
        return {"success": False, "error": "Couldn't reach Hugging Face. Are you online?"}
    if r.status_code in (401, 403):
        return {"success": False, "exists": True,
                "error": f"{repo} is gated or private on Hugging Face. Accept its license there first, or pick another."}
    if r.status_code != 200:
        return {"success": False, "error": f"No Hugging Face repository called {repo}."}
    ggufs = [s for s in r.json().get("siblings", []) if s.get("rfilename", "").lower().endswith(".gguf")
             and "mmproj" not in s["rfilename"].lower()]
    if not ggufs:
        return {"success": False, "exists": True, "error": f"{repo} has no GGUF files, and Ollama can only run GGUF. "
                                           f"Search Hugging Face for \"{repo.split('/')[-1]} GGUF\"."}
    def q_of(s):
        hit = _QUANT.search(Path(s["rfilename"]).name)
        return hit.group(1).upper() if hit else ""
    quants = sorted({q_of(s) for s in ggufs if q_of(s)})
    if quant and quant not in quants:
        return {"success": False, "error": f"{repo} has no {quant} file. It has: {', '.join(quants) or 'unlabelled files'}."}
    # Ollama's own choice when no quant is named is Q4_K_M; say which file that is so the size is right.
    chosen = quant or ("Q4_K_M" if "Q4_K_M" in quants else (quants[0] if quants else None))
    files = [s for s in ggufs if not chosen or q_of(s) == chosen]
    size = round(sum(s.get("size") or 0 for s in files) / 1024 ** 3, 1) or None
    name = f"hf.co/{repo}" + (f":{quant}" if quant else "")
    return {"success": True, "name": name, "source": "huggingface", "size_gb": size, "quant": chosen, "quants": quants}


# ---------------------------------------------------------------- downloads

_pulls: Dict[str, Dict[str, Any]] = {}


def _models_dir() -> Path:
    """Where Ollama keeps models (OLLAMA_MODELS, else ~/.ollama/models), or the nearest folder that exists."""
    p = Path(os.environ.get("OLLAMA_MODELS") or Path.home() / ".ollama" / "models")
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def pull_status(name: str) -> Dict[str, Any]:
    p = _pulls.get(name)
    if not p:
        return {"status": "none"}
    total = sum(p["totals"].values())
    done = sum(p["done"].values())
    return {"status": p["status"], "error": p["error"], "phase": p["phase"],
            "downloaded_gb": round(done / 1024 ** 3, 2), "total_gb": round(total / 1024 ** 3, 2),
            "percent": 100 if p["status"] == "done" else (int(done / total * 100) if total else 0),
            "mb_per_s": p.get("rate", 0)}


async def start_pull(name: str) -> Dict[str, Any]:
    """Starts downloading `name` (anything resolve() takes); the result's name is the one to poll."""
    if not await _running():
        return {"success": False, "error": "Ollama isn't running."}
    found = await resolve(name)
    if not found["success"] and "online" not in found["error"]:   # offline: let Ollama try anyway
        return found
    name = found.get("name") or name
    cur = _pulls.get(name)
    if cur and cur["status"] == "downloading":
        return {"success": True, "name": name, **pull_status(name)}
    need = found.get("size_gb") or 0
    free = shutil.disk_usage(_models_dir()).free / 1024 ** 3
    if need and free < need + 2:
        return {"success": False, "error": f"Not enough disk space: {name} needs {need:.1f} GB and {free:.1f} GB is free."}
    _pulls[name] = {"status": "downloading", "error": None, "phase": "Starting", "totals": {}, "done": {}, "cancel": False}
    asyncio.get_running_loop().create_task(_pull_work(name))
    return {"success": True, "name": name, **pull_status(name)}


def cancel_pull(name: str) -> Dict[str, Any]:
    p = _pulls.get(name)
    if p and p["status"] == "downloading":
        p["cancel"] = True
    return {"success": True}


async def _pull_work(name: str) -> None:
    p = _pulls[name]
    last_t, last_b = time.time(), 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=600)) as c:
            async with c.stream("POST", f"{root()}/api/pull", json={"model": name, "stream": True}) as r:
                async for line in r.aiter_lines():
                    if p["cancel"]:
                        p.update(status="cancelled", phase="Stopped. Downloading again picks up where it left off.")
                        return     # leaving the stream makes Ollama stop the download; finished parts are kept
                    if not line.strip():
                        continue
                    ev = json.loads(line)
                    if ev.get("error"):
                        raise RuntimeError(ev["error"])
                    d = ev.get("digest")
                    if d and ev.get("total"):
                        p["totals"][d] = ev["total"]
                        p["done"][d] = ev.get("completed", 0)
                    p["phase"] = "Downloading" if (ev.get("status") or "").startswith("pulling") else (ev.get("status") or "").capitalize()
                    now = time.time()
                    if now - last_t >= 2:
                        got = sum(p["done"].values())
                        p["rate"] = round((got - last_b) / (now - last_t) / 1024 ** 2, 1)
                        last_t, last_b = now, got
                    if ev.get("status") == "success":
                        p.update(status="done", phase="Done")
                        return
        raise RuntimeError("Ollama ended the download without finishing it.")
    except Exception as e:
        msg = str(e)
        if "file does not exist" in msg or "manifest unknown" in msg:
            msg = f"{name} isn't available to download (check the name)."
        logger.warning(f"Pull of {name} failed: {e}")
        p.update(status="error", error=msg[:300], phase="Failed")


async def delete(name: str) -> Dict[str, Any]:
    """Removes a model and Xod's long-context copies of it. The one Xod is using can't be removed."""
    from app.config import settings
    if settings.llm_provider == "ollama" and ollama_context._same(settings.llm_model or "", {"name": name}):
        return {"success": False, "error": "Xod is using this model. Switch to another one first."}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.request("DELETE", f"{root()}/api/delete", json={"model": name})
        if r.status_code != 200:
            return {"success": False, "error": r.text[:200] or f"HTTP {r.status_code}"}
        slug = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-")
        for t in (await c.get(f"{root()}/api/tags")).json().get("models", []):
            if t["name"].startswith(f"{ollama_context.PREFIX}{slug}:ctx"):
                await c.request("DELETE", f"{root()}/api/delete", json={"model": t["name"]})
    ollama_context._variants.pop(name, None)
    _pulls.pop(name, None)
    return {"success": True}
