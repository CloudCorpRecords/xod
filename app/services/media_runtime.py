"""
Media runtime: runs image/video models in the media worker process, with the memory broker
deciding what has to make room.

- The worker starts on demand, restarts itself after a crash, and shuts down after a few idle
  minutes so its memory goes back to the chat model.
- Base models and LoRAs can be switched per request: LoRAs hot-swap on the loaded model in
  seconds; changing the base model swaps it inside the worker.
- Out of memory: free more room (up to pausing the chat model) and retry once. Never fake it:
  if the AI model can't run, the result says so and why.
"""
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.paths import DATA_DIR

logger = logging.getLogger("xod.media")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = DATA_DIR / "models"
LORA_DIR = DATA_DIR / "loras"
OUTPUT_DIR = DATA_DIR / "media_studio"
VENV_PYTHON = Path(sys.executable)   # the worker runs on the same Python as the server (.venv, or the app's own)

IDLE_SHUTDOWN_SEC = 180           # stop the worker (freeing all its memory) after this long unused
REQUEST_TIMEOUT_SEC = 900
GPU_WORKING_SET_GB = 10.5         # roughly what macOS lets one process use on the GPU on a 16 GB Mac

VIDEO_CLASSES = ("Video", "Wan", "AnimateDiff", "LTX", "CogVideo", "Mochi", "Hunyuan")


def _dir_size_gb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink()) / 1e9


def list_models() -> List[Dict[str, Any]]:
    """Every model folder in data/models, whether Xod can run it, and roughly how much memory it needs.

    A folder can describe itself with xod_model.json (engine, kind, need_gb, ...); otherwise a
    diffusers model_index.json is read. Engines: "diffusers" (default), "mflux" (FLUX on MLX),
    "animatediff" (motion adapter on top of an SD1.5 base model in another folder).
    """
    out = []
    if not MODELS_DIR.exists():
        return out
    for d in sorted(p for p in MODELS_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")):
        model_id = d.name.replace("---", "/")
        index = d / "model_index.json"
        marker = d / "xod_model.json"
        size = round(_dir_size_gb(d), 1)
        if marker.exists():
            meta = json.loads(marker.read_text())
            entry = {"id": meta.get("id", model_id), "dir": str(d), "size_gb": size, "usable": True,
                     "pipeline": meta.get("engine"), **meta}
            if meta.get("base") and not (MODELS_DIR / meta["base"]).is_dir():
                entry.update(usable=False, reason=f"base model {meta['base']} is missing")
            out.append(entry)
        elif index.exists():
            cls = json.loads(index.read_text()).get("_class_name", "")
            kind = "video" if any(k in cls for k in VIDEO_CLASSES) else "image"
            if kind == "video":
                out.append({"id": model_id, "dir": str(d), "kind": "video", "size_gb": size, "usable": False,
                            "reason": f"{cls} isn't supported yet; Xod's video engine runs AnimateDiff models"})
                continue
            # fp16 weights on the GPU plus working memory; measured ~5 GB for sd-turbo (2.4 GB on disk).
            # Folders with only fp32 weights get loaded as fp16, so half their size.
            has_fp16 = any(d.rglob("*.fp16.safetensors"))
            weights = size / 2 if (not has_fp16 or any(d.rglob("*[!6].safetensors"))) else size
            out.append({"id": model_id, "dir": str(d), "kind": kind, "engine": "diffusers", "pipeline": cls,
                        "size_gb": size, "need_gb": round(weights * 1.6 + 1.5, 1), "usable": True})
        else:
            reason = ("MLX/DiffusionKit format; Xod's image engine uses the diffusers format"
                      if any(d.glob("*.safetensors")) else "not a diffusers model folder")
            if (d / "Wan2.1_VAE.pth").exists():
                reason = "original Wan release format; the diffusers version (…-Diffusers) is needed"
            out.append({"id": model_id, "dir": str(d), "kind": "unknown", "size_gb": size, "usable": False, "reason": reason})
    return out


def list_loras() -> List[Dict[str, Any]]:
    """LoRAs in data/loras (*.safetensors, with an optional same-name .json: base, trigger, description)."""
    LORA_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(LORA_DIR.glob("*.safetensors")):
        meta = {}
        side = f.with_suffix(".json")
        if side.exists():
            try:
                meta = json.loads(side.read_text())
            except Exception:
                pass
        out.append({"name": f.stem, "path": str(f), "size_mb": round(f.stat().st_size / 1e6, 1), **meta})
    return out


class MediaRuntime:
    def __init__(self) -> None:
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._ids = 0
        self._job_lock: Optional[asyncio.Lock] = None
        self._last_use = 0.0
        self._idle_task: Optional[asyncio.Task] = None
        self.loaded_model: Optional[str] = None
        self.restarts = 0
        self.last_error: Optional[str] = None
        self.gpu_fraction = 0.0
        self.job_started: Optional[float] = None   # for the doctor's hung-job check

    def _lock(self) -> asyncio.Lock:
        if self._job_lock is None:
            self._job_lock = asyncio.Lock()
        return self._job_lock

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    # ------------------------------------------------------------------ process control
    async def _start(self, gpu_fraction: float) -> None:
        from app.services import media_packages
        await media_packages.ensure()   # first use in the Mac app: a one-time download
        env = dict(os.environ, XOD_MEDIA_GPU_FRACTION=f"{gpu_fraction:.2f}", PYTHONUNBUFFERED="1",
                   XOD_MEDIA_MLX_LIMIT_GB=f"{GPU_WORKING_SET_GB:.1f}")
        self.gpu_fraction = gpu_fraction
        self.proc = await asyncio.create_subprocess_exec(
            str(VENV_PYTHON), "-m", "app.services.media_worker", cwd=str(BASE_DIR), env=media_packages.worker_env(env),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=1 << 20,
        )
        hello = await self._read(timeout=120)
        if not hello.get("ready"):
            raise RuntimeError(f"media worker didn't start: {hello}")
        self.loaded_model = None
        if not self._idle_task or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_watch())

    async def _read(self, timeout: float) -> Dict[str, Any]:
        while True:
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=timeout)
            if not line:
                raise ConnectionError("media worker exited")
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue  # library chatter on stdout; ignore

    async def _call(self, op: str, timeout: float = REQUEST_TIMEOUT_SEC, **kw: Any) -> Dict[str, Any]:
        self._ids += 1
        rid = self._ids
        self.proc.stdin.write((json.dumps({"id": rid, "op": op, **kw}) + "\n").encode())
        await self.proc.stdin.drain()
        while True:
            msg = await self._read(timeout)
            if msg.get("id") == rid:
                return msg

    async def _kill(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
                await self.proc.wait()
            except Exception:
                pass
        self.proc, self.loaded_model = None, None

    async def release(self, full: bool = True) -> bool:
        """Stops the worker, giving back all of its memory. Waits for a running job to finish first."""
        if not self.alive:
            return False
        async with self._lock():
            try:
                await self._call("shutdown", timeout=10)
            except Exception:
                pass
            await self._kill()
        logger.info("Media worker stopped; memory released")
        return True

    async def trim(self, keep_model: Optional[str] = None) -> bool:
        """For the broker: drop the loaded model unless it's the one about to be used."""
        if not self.alive or self._lock().locked():
            return False
        if self.loaded_model and self.loaded_model != keep_model:
            return await self.release(full=True)
        return False

    async def _idle_watch(self) -> None:
        while self.alive:
            await asyncio.sleep(15)
            if not self._lock().locked() and time.time() - self._last_use > IDLE_SHUTDOWN_SEC:
                await self.release(full=True)
                from app.services.memory_broker import memory_broker
                memory_broker.media_used()  # lets the broker hand memory back to the chat model
                return

    # ------------------------------------------------------------------ public
    def status(self) -> Dict[str, Any]:
        return {"worker_running": self.alive, "loaded_model": self.loaded_model, "restarts": self.restarts,
                "last_error": self.last_error, "idle_shutdown_sec": IDLE_SHUTDOWN_SEC}

    def _pick(self, kind: str, model: Optional[str]) -> Dict[str, Any]:
        models = [m for m in list_models() if m["kind"] == kind and m["usable"]]
        if not models:
            return {"error": f"No {kind} model is installed in a format Xod can run."}
        chosen = next((m for m in models if model and model in (m["id"], m["id"].split("/")[-1], Path(m["dir"]).name)), None)
        if model and not chosen:
            return {"error": f"{kind.title()} model '{model}' isn't installed or usable.",
                    "available_models": [m["id"] for m in models]}
        if not chosen:
            from app.services.media_model_manager import media_model_manager
            active = media_model_manager.config.get(f"active_{kind}_model")
            chosen = next((m for m in models if m["id"] == active), models[0])
        return {"model": chosen}

    def _lora_specs(self, loras: Optional[List[Any]]) -> Dict[str, Any]:
        specs = []
        known = {l["name"]: l for l in list_loras()}
        for l in loras or []:
            name = l.get("name") if isinstance(l, dict) else str(l)
            if name not in known:
                return {"error": f"LoRA '{name}' isn't in data/loras.", "available_loras": list(known)}
            specs.append({"name": name, "path": known[name]["path"],
                          "weight": float((l.get("weight") if isinstance(l, dict) else None) or 0.8)})
        return {"specs": specs}

    @staticmethod
    def _fraction(need_gb: float) -> float:
        return min(0.95, max(0.3, (need_gb + 0.5) / GPU_WORKING_SET_GB))

    async def _run(self, chosen: Dict[str, Any], lora_specs: List[Dict[str, Any]], op: str,
                   payload: Dict[str, Any], say) -> Dict[str, Any]:
        """Makes room, (re)starts the worker, loads the model + LoRAs, runs one job. Retries once on crash/OOM."""
        from app.services.memory_broker import memory_broker
        short = chosen["id"].split("/")[-1]
        room: Dict[str, Any] = {"freed": []}
        for attempt in (1, 2):
            need = chosen["need_gb"] if self.loaded_model != chosen["dir"] else 1.0
            say(f"Making room for {short}…")
            room = await memory_broker.make_room(need, f"{chosen['kind']} model {chosen['id']}", keep_media_model=chosen["dir"])
            if not room["ok"]:
                return {"ok": False, "error": (f"Not enough free memory for {chosen['id']} (needs about "
                        f"{room['need_gb']} GB, {room['available_gb']} GB free even after making room). Close some apps and try again.")}
            async with self._lock():
                self.job_started = time.time()
                try:
                    # PyTorch's memory cap is fixed when the worker starts: restart it for a bigger model.
                    if self.alive and self.loaded_model != chosen["dir"] and self._fraction(chosen["need_gb"]) > self.gpu_fraction + 0.01:
                        await self._call("shutdown", timeout=10)
                        await self._kill()
                    if not self.alive:
                        say("Starting the media engine…")
                        await self._start(gpu_fraction=self._fraction(chosen["need_gb"]))
                    if self.loaded_model != chosen["dir"]:
                        say(f"Loading {short}…")
                        r = await self._call("load", model_dir=chosen["dir"], kind=chosen["kind"],
                                             engine=chosen.get("engine", "diffusers"), meta=chosen)
                        if not r.get("ok"):
                            raise RuntimeError(r.get("error"))
                        self.loaded_model = chosen["dir"]
                    if lora_specs:
                        say("Applying LoRAs…")
                    r = await self._call("set_loras", loras=lora_specs)
                    if not r.get("ok"):
                        return {"ok": False, "error": f"Couldn't apply LoRAs: {r.get('error')}"}
                    say("Generating…")
                    r = await self._call(op, **payload)
                except (ConnectionError, asyncio.TimeoutError, BrokenPipeError) as e:
                    # The worker died (often the OS killing it for memory): restart and retry once.
                    self.restarts += 1
                    protected = False
                    if self.proc is not None:
                        try:
                            await asyncio.wait_for(self.proc.wait(), timeout=2)
                        except asyncio.TimeoutError:
                            pass
                        protected = self.proc.returncode == 70  # media_worker.PROTECT_EXIT_CODE
                    self.last_error = "stopped to protect the Mac (memory ran out)" if protected else f"worker crashed: {e}"
                    await self._kill()
                    if protected:
                        chosen = {**chosen, "need_gb": chosen["need_gb"] + 2}  # ask the broker for more room
                        if attempt == 1:
                            say("Memory ran short, so the media engine stopped itself; freeing more and retrying…")
                            continue
                        return {"ok": False, "error": (f"{short} needs more free memory than this Mac has right now, "
                                                       "so Xod stopped it before macOS ran out. Close some apps, or use a smaller model.")}
                    if attempt == 1:
                        say("The media engine crashed; restarting it…")
                        continue
                    return {"ok": False, "error": "The media engine crashed twice. Check free memory and try again."}
                except RuntimeError as e:
                    r = {"ok": False, "error": str(e), "oom": "memory" in str(e).lower()}
                finally:
                    self._last_use = time.time()
                    self.job_started = None
            if r.get("ok"):
                memory_broker.media_used()
                r["freed"] = room["freed"]
                return r
            if r.get("oom") and attempt == 1:
                # Didn't fit: restart clean with more room and try once more.
                say("Ran out of memory; freeing more and retrying…")
                await self.release(full=True)
                chosen = {**chosen, "need_gb": chosen["need_gb"] + 2}
                continue
            self.last_error = r.get("error")
            return r
        return {"ok": False, "error": "failed"}

    async def generate_image(self, prompt: str, model: Optional[str] = None, loras: Optional[List[Dict[str, Any]]] = None,
                             width: int = 512, height: int = 512, seed: Optional[int] = None,
                             negative_prompt: Optional[str] = None, steps: Optional[int] = None,
                             progress=None) -> Dict[str, Any]:
        say = progress or (lambda _msg: None)
        pick = self._pick("image", model)
        if "error" in pick:
            return {"success": False, **pick}
        chosen = pick["model"]
        loras_r = self._lora_specs(loras)
        if "error" in loras_r:
            return {"success": False, **loras_r}
        lora_specs = loras_r["specs"]
        max_side = chosen.get("max_side", 768)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUTPUT_DIR / f"ai_gen_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
        r = await self._run(chosen, lora_specs, "generate_image", dict(
            prompt=prompt, out_path=str(out_path), width=min(width, max_side), height=min(height, max_side),
            seed=seed, negative_prompt=negative_prompt, steps=steps), say)
        if not r.get("ok"):
            return {"success": False, "error": f"Image generation failed: {r.get('error')}", **{k: r[k] for k in ("available_models",) if k in r}}
        return {"success": True, "action": "generate_ai_image", "engine": f"{chosen.get('label', chosen['id'])} (on this Mac)",
                "active_model": chosen["id"], "loras": lora_specs and [{"name": l["name"], "weight": l["weight"]} for l in lora_specs],
                "output_path": r["output_path"], "filename": out_path.name, "relative_url": f"/media/{out_path.name}",
                "prompt": prompt, "width": r["width"], "height": r["height"], "seed": r["seed"],
                "seconds": r["sec"], "markdown": f"![{prompt}](/media/{out_path.name})",
                "memory": {"freed": r["freed"], "gpu_peak": r.get("gpu_peak")}}

    async def generate_video(self, prompt: str, model: Optional[str] = None, loras: Optional[List[Dict[str, Any]]] = None,
                             seconds: float = 2.0, fps: int = 8, width: int = 512, height: int = 512,
                             seed: Optional[int] = None, negative_prompt: Optional[str] = None,
                             smooth: bool = True, progress=None) -> Dict[str, Any]:
        """Real text-to-video. AnimateDiff makes 8 fps frames; `smooth` interpolates them to 24 fps."""
        say = progress or (lambda _msg: None)
        pick = self._pick("video", model)
        if "error" in pick:
            return {"success": False, **pick}
        chosen = pick["model"]
        loras_r = self._lora_specs(loras)
        if "error" in loras_r:
            return {"success": False, **loras_r}
        num_frames = max(8, min(int(round(seconds * fps)), 32))
        stamp = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
        frames_dir = OUTPUT_DIR / f".frames_{stamp}"
        r = await self._run(chosen, loras_r["specs"], "generate_video", dict(
            prompt=prompt, frames_dir=str(frames_dir), num_frames=num_frames,
            width=min(width, 768), height=min(height, 768), seed=seed, negative_prompt=negative_prompt), say)
        if not r.get("ok"):
            return {"success": False, "error": f"Video generation failed: {r.get('error')}"}
        say("Encoding the video…")
        out_path = OUTPUT_DIR / f"ai_video_{stamp}.mp4"
        vf = "minterpolate=fps=24:mi_mode=mci:mc_mode=aobmc:vsbmc=1," if smooth else ""
        from app.services.media_studio import ffmpeg_bin
        proc = await asyncio.create_subprocess_exec(
            await ffmpeg_bin(), "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(frames_dir / "%04d.png"),
            "-vf", f"{vf}format=yuv420p", "-c:v", "libx264", "-crf", "18", "-movflags", "+faststart", str(out_path),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        import shutil
        shutil.rmtree(frames_dir, ignore_errors=True)
        if proc.returncode != 0:
            return {"success": False, "error": f"Encoding the video failed: {err.decode(errors='replace')[-300:]}"}
        return {"success": True, "action": "generate_ai_video", "engine": f"{chosen.get('label', chosen['id'])} (on this Mac)",
                "active_model": chosen["id"], "output_path": str(out_path), "filename": out_path.name,
                "media_url": f"/media/{out_path.name}", "relative_url": f"/media/{out_path.name}",
                "prompt": prompt,
                "frames": r["num_frames"], "duration_sec": round(r["num_frames"] / fps, 2), "fps": 24 if smooth else fps,
                "width": r["width"], "height": r["height"], "seed": r["seed"], "seconds": r["sec"],
                "markdown": f"![{prompt}](/media/{out_path.name})",
                "memory": {"freed": r["freed"], "gpu_peak": r.get("gpu_peak")}}


media_runtime = MediaRuntime()
