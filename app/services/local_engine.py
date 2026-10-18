"""
Xod's built-in AI engine: runs a chat model on this Mac with MLX, so Xod works without
LM Studio or Ollama.

It's mlx_lm.server (OpenAI-compatible, with tool calls and a thinking switch) running as a
child process on a private localhost port, started the first time a chat needs it. The memory
broker stops it to make room for image/video jobs and Xod starts it again on the next message,
the same way it pauses a model in LM Studio.

Models come from Hugging Face (mlx-community builds) and are kept in the normal Hugging Face
cache (~/.cache/huggingface), so other MLX tools can share them. CATALOG lists the ones Xod
recommends, by how much memory the Mac has.
"""
import asyncio
import logging
import os
import platform
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.paths import APP_ROOT, DATA_DIR

logger = logging.getLogger("xod.engine")

INTEL_MESSAGE = ("The built-in engine needs a Mac with Apple silicon (M1 or newer). "
                 "On this Mac, use Ollama or LM Studio with a local model, or a cloud model — "
                 "pick one under Settings → AI model.")


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def require_apple_silicon(what: str = "This") -> None:
    if not is_apple_silicon():
        raise RuntimeError(f"{what} needs a Mac with Apple silicon (M1 or newer). "
                           "Use Ollama or LM Studio with a local model, or a cloud model instead.")

# Recommended models, smallest first. need_gb: memory while running (weights + working space).
CATALOG: List[Dict[str, Any]] = [
    {"id": "qwen2.5-1.5b", "repo": "mlx-community/Qwen2.5-1.5B-Instruct-4bit", "label": "Qwen 2.5 1.5B",
     "download_gb": 0.9, "need_gb": 2.0, "min_ram_gb": 8, "thinks": False,
     "blurb": "Tiny and quick. Fine for chat and simple tasks; struggles with long multi-step jobs."},
    {"id": "qwen3.5-4b", "repo": "mlx-community/Qwen3.5-4B-MLX-4bit", "label": "Qwen 3.5 4B",
     "download_gb": 3.1, "need_gb": 4.0, "min_ram_gb": 8, "thinks": True,
     "blurb": "Best pick for 8–16 GB Macs. Fast: a researched answer in about 10 seconds on an M1 Pro.",
     "recommended_up_to_gb": 23},
    {"id": "qwen3.5-9b", "repo": "mlx-community/Qwen3.5-9B-MLX-4bit", "label": "Qwen 3.5 9B",
     "download_gb": 6.0, "need_gb": 7.0, "min_ram_gb": 16, "thinks": True,
     "blurb": "More careful on long, multi-step jobs and details. About twice as slow as 4B; best on 24 GB+ Macs.",
     "recommended_up_to_gb": 31},
    {"id": "qwen3.8-27b", "repo": "mlx-community/Qwen3.8-27B-4bit", "label": "Qwen 3.8 27B",
     "download_gb": 16.1, "need_gb": 18.0, "min_ram_gb": 32, "thinks": True,
     "blurb": "For 32 GB+ Macs. Close to cloud quality on research and planning."},
]


def total_ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / 1024 ** 3)
    except Exception:
        return 16.0


def recommend(ram_gb: Optional[float] = None) -> Dict[str, Any]:
    """The model to suggest for this Mac: the smallest good one for its memory class. On 16 GB the 4B
    model leaves room for the browser and everything else (a 9B or 12B model there pushes the Mac
    into swap, and every answer waits on the disk); bigger Macs get the bigger models."""
    ram = ram_gb or total_ram_gb()
    fits = [m for m in CATALOG if m["min_ram_gb"] <= ram and m["id"] != "qwen2.5-1.5b"]
    for m in fits:
        if ram <= m.get("recommended_up_to_gb", 10 ** 6):
            return m
    return fits[-1] if fits else CATALOG[0]


def catalog_entry(repo_or_id: str) -> Optional[Dict[str, Any]]:
    return next((m for m in CATALOG if repo_or_id in (m["id"], m["repo"])), None)


# ---------------------------------------------------------------- downloads (Hugging Face cache)

def _cache_dir(repo: str) -> Path:
    from huggingface_hub.constants import HF_HUB_CACHE
    return Path(HF_HUB_CACHE) / ("models--" + repo.replace("/", "--"))


def is_downloaded(repo: str) -> bool:
    snaps = _cache_dir(repo) / "snapshots"
    if not snaps.exists():
        return False
    for snap in snaps.iterdir():
        if (snap / "config.json").exists() and any(snap.glob("*.safetensors")):
            return True
    return False


def _bytes_on_disk(repo: str) -> int:
    blobs = _cache_dir(repo) / "blobs"
    if not blobs.exists():
        return 0
    return sum(f.stat().st_size for f in blobs.iterdir() if f.is_file())


_downloads: Dict[str, Dict[str, Any]] = {}


def start_download(repo: str) -> Dict[str, Any]:
    """Downloads a model in the background; poll download_status() for progress."""
    if not is_apple_silicon():
        return {"success": False, "error": INTEL_MESSAGE}
    entry = catalog_entry(repo)
    if not entry:
        return {"success": False, "error": "Only models from Xod's list can be downloaded here."}
    if is_downloaded(repo):
        return {"success": True, "status": "done"}
    cur = _downloads.get(repo)
    if cur and cur["status"] == "downloading":
        return {"success": True, "status": "downloading"}
    state = {"status": "downloading", "error": None, "started": time.time(), "total_gb": entry["download_gb"]}
    _downloads[repo] = state

    def work():
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(repo, allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "*.jinja", "*.py", "*.tiktoken"])
            state["status"] = "done"
        except Exception as e:
            state["status"] = "error"
            state["error"] = str(e)[:300]
            logger.warning(f"Download of {repo} failed: {e}")

    threading.Thread(target=work, daemon=True, name=f"download-{entry['id']}").start()
    return {"success": True, "status": "downloading"}


def download_status(repo: str) -> Dict[str, Any]:
    entry = catalog_entry(repo) or {"download_gb": 0}
    state = _downloads.get(repo) or {"status": "done" if is_downloaded(repo) else "not_started", "error": None}
    got = _bytes_on_disk(repo) / 1024 ** 3
    total = entry["download_gb"] or max(got, 0.1)
    pct = 100 if state["status"] == "done" else min(99, int(got / total * 100))
    return {"repo": repo, "status": state["status"], "error": state.get("error"),
            "downloaded_gb": round(got, 2), "total_gb": total, "percent": pct}


# ---------------------------------------------------------------- the server process

def _command(repo: str, port: int) -> List[str]:
    """The engine's command line (the smoke tests swap in a stand-in engine here)."""
    return [sys.executable, "-m", "app.services.engine_child", "--model", repo, "--host", "127.0.0.1",
            "--port", str(port), "--max-tokens", "8192"]


# The engine's own log (mlx_lm.server logs every request). It goes to a file, not a pipe: nothing reads a pipe
# while the engine runs, and once ~64 KB of log piled up in it the engine froze on its next log line, mid-task.
ENGINE_LOG = DATA_DIR / "engine.log"


def _log_tail(chars: int = 600) -> str:
    try:
        with open(ENGINE_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - chars))
            return f.read().decode(errors="replace").strip()
    except OSError:
        return ""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalEngine:
    def __init__(self) -> None:
        self.port = _free_port()
        self.proc: Optional[subprocess.Popen] = None
        self.repo: Optional[str] = None
        self._lock: Optional[asyncio.Lock] = None
        self.last_error: Optional[str] = None
        self.started_at: Optional[float] = None
        self.restarts = 0                      # times it was restarted because it stopped answering
        self.last_used: Optional[float] = None  # the last request that needed it (services/local_servers.py stops it when idle)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _lk(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def ensure_running(self, repo: str) -> None:
        """Starts the engine with this model (or switches to it). Raises with a clear message if it can't.
        Ready means started *and* answering (started_at is set then): a request that arrives while
        another is still starting the engine waits for it instead of being refused."""
        if not is_apple_silicon():
            raise RuntimeError(INTEL_MESSAGE)
        self.last_used = time.time()
        if self.running() and self.repo == repo and self.started_at:
            return
        async with self._lk():
            if self.running() and self.repo == repo and self.started_at:
                return
            if not is_downloaded(repo):
                raise RuntimeError(f"The built-in model {repo} isn't downloaded yet. Open Settings → AI model to download it.")
            await self.stop()
            entry = catalog_entry(repo) or {"need_gb": 6.0}
            from app.services.memory_broker import memory_broker
            await memory_broker.make_room(entry["need_gb"], f"chat model {repo.split('/')[-1]}")
            env = dict(os.environ, HF_HUB_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
            ENGINE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(ENGINE_LOG, "wb") as log:     # the child keeps its own handle; a fresh log each start
                self.proc = subprocess.Popen(_command(repo, self.port), stdout=subprocess.DEVNULL, stderr=log,
                                             env=env, cwd=str(APP_ROOT))
            self.repo = repo
            deadline = time.time() + 240
            async with httpx.AsyncClient(timeout=3) as client:
                while time.time() < deadline:
                    if self.proc.poll() is not None:
                        self.last_error = _log_tail() or f"exit code {self.proc.returncode}"
                        self.proc = None
                        raise RuntimeError(f"The built-in model didn't start: {self.last_error}")
                    try:
                        if (await client.get(f"{self.base_url}/models")).status_code == 200:
                            self.started_at = time.time()
                            self.last_error = None
                            logger.info(f"Built-in engine serving {repo} on port {self.port}")
                            # mlx_lm.server loads weights on the first request; warm it now.
                            try:
                                await client.post(f"{self.base_url}/chat/completions", timeout=180, json={
                                    "model": repo, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1})
                            except Exception:
                                pass
                            return
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(1)
            await self.stop()
            raise RuntimeError("The built-in model took too long to start.")

    async def stop(self) -> None:
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                await asyncio.to_thread(p.wait, 8)
            except Exception:
                p.kill()
        self.started_at = None

    async def restart(self, repo: str, why: str, seen_start: Optional[float]) -> None:
        """Stops the engine and starts it again: for one that is running but stopped answering. seen_start is the
        started_at the caller's request went to; if it changed, another request already restarted it."""
        async with self._lk():
            if self.started_at == seen_start:
                logger.warning(f"Restarting the built-in engine: {why}")
                self.restarts += 1
                await self.stop()
        await self.ensure_running(repo)

    async def responsive(self) -> bool:
        """Called after a long quiet stretch on a request: is the engine still at work, or stuck? A working engine
        answers a quick question and keeps the processor busy (it may be writing a long tool call, which it sends
        all at once, or serving another request first). A stuck one fails to answer, or sits idle."""
        if not self.running():
            logger.warning("Engine check: the process isn't running")
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                code = (await client.get(f"{self.base_url}/models")).status_code
            if code != 200:
                logger.warning(f"Engine check: /models answered {code}")
                return False
        except httpx.HTTPError as e:
            logger.warning(f"Engine check: /models didn't answer ({type(e).__name__})")
            return False
        try:
            import psutil
            proc = psutil.Process(self.proc.pid)
            # The engine may run in a child of the process Xod started (a launcher): count the whole family.
            family = [proc] + proc.children(recursive=True)
            for p in family:
                p.cpu_percent(None)
            await asyncio.sleep(1.5)
            busy = sum(p.cpu_percent(None) for p in family if p.is_running())
        except Exception as e:
            logger.warning(f"Engine check: couldn't read its processor use ({type(e).__name__}); assuming it's working")
            return True                        # can't tell: don't restart an engine that answers
        if busy < 2.0:
            logger.warning(f"Engine check: answers but sits idle ({busy:.1f}% processor across {len(family)} process(es))")
        return busy >= 2.0

    def status(self) -> Dict[str, Any]:
        return {"running": self.running(), "model": self.repo, "port": self.port,
                "since": self.started_at, "last_error": self.last_error, "restarts": self.restarts, "last_used": self.last_used}


local_engine = LocalEngine()
