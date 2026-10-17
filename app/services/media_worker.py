"""
Media worker: a separate process that owns all image/video model memory.

Why a process: killing it gives back 100% of its memory (PyTorch on Apple GPUs keeps large
caches), and an out-of-memory error here can't take down Xod. The server side
(media_runtime.py) starts it on demand, restarts it after crashes, and stops it when idle.

Protocol: one JSON object per line on stdin, one JSON reply per line on stdout.
    {"id": 1, "op": "load", "model_dir": "...", "kind": "image", "engine": "diffusers|mflux|animatediff", "meta": {...}}
    {"id": 2, "op": "set_loras", "loras": [{"name": "...", "path": "...", "weight": 0.8}]}
    {"id": 3, "op": "generate_image", "prompt": "...", "out_path": "...", ...}
    {"id": 4, "op": "generate_video", "prompt": "...", "frames_dir": "...", "num_frames": 16, ...}
    {"id": 4, "op": "status"} | {"op": "unload"} | {"op": "shutdown"}

Memory guard: XOD_MEDIA_GPU_FRACTION caps this process's share of unified memory (PyTorch), and
XOD_MEDIA_MLX_LIMIT_GB caps MLX (FLUX), so a job that wouldn't fit fails with a normal error
instead of swapping the whole Mac.
"""
import gc
import json
import os
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch  # noqa: E402

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
if DEVICE == "mps" and os.environ.get("XOD_MEDIA_GPU_FRACTION"):
    torch.mps.set_per_process_memory_fraction(float(os.environ["XOD_MEDIA_GPU_FRACTION"]))

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"

state = {"pipe": None, "model_dir": None, "kind": None, "engine": None, "meta": {}, "loras": {}, "active_loras": []}


def reply(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _mlx():
    """MLX is only imported once a FLUX model is used."""
    if "mlx.core" not in sys.modules:
        return None
    import mlx.core as mx
    return mx


def gpu_memory():
    out = {}
    if DEVICE == "mps":
        out = {"allocated_gb": round(torch.mps.current_allocated_memory() / 1e9, 2),
               "driver_gb": round(torch.mps.driver_allocated_memory() / 1e9, 2)}
    mx = _mlx()
    if mx is not None:
        out["mlx_active_gb"] = round(mx.get_active_memory() / 1e9, 2)
        out["mlx_peak_gb"] = round(mx.get_peak_memory() / 1e9, 2)
    return out


def trim():
    """Give cached GPU memory back to the system after each job."""
    gc.collect()
    if DEVICE == "mps":
        torch.mps.empty_cache()
    mx = _mlx()
    if mx is not None:
        mx.clear_cache()


def unload():
    state.update(pipe=None, model_dir=None, kind=None, engine=None, meta={}, loras={}, active_loras=[],
                 split_inference=False)
    trim()


def _tidy(pipe):
    # Decode large images in tiles to cap memory. NOT attention slicing: on Apple GPUs in fp16
    # it turned every sd-turbo image pure black (measured), and it saved no memory at 512 px.
    vae = getattr(pipe, "vae", None)
    for fn in ("enable_slicing", "enable_tiling"):
        try:
            getattr(vae, fn)()
        except Exception:
            pass
    if hasattr(pipe, "set_progress_bar_config"):
        pipe.set_progress_bar_config(disable=True)


def _load_mflux(model_dir, meta):
    from app.services.local_engine import require_apple_silicon
    require_apple_silicon("On-device image generation")
    import mlx.core as mx
    if os.environ.get("XOD_MEDIA_MLX_LIMIT_GB"):
        mx.set_memory_limit(int(float(os.environ["XOD_MEDIA_MLX_LIMIT_GB"]) * 1e9))
    from app.services.flux_runner import FluxRunner
    return FluxRunner(model_dir, meta.get("variant", "schnell"))


def _load_animatediff(model_dir, meta):
    from diffusers import AnimateDiffPipeline, EulerDiscreteScheduler, MotionAdapter
    from safetensors.torch import load_file
    base = Path(model_dir).parent / meta["base"]
    adapter = MotionAdapter().to(DEVICE, torch.float16)
    adapter.load_state_dict(load_file(str(Path(model_dir) / meta["adapter"]), device="cpu"))
    pipe = AnimateDiffPipeline.from_pretrained(str(base), motion_adapter=adapter, torch_dtype=torch.float16,
                                               local_files_only=True, safety_checker=None, feature_extractor=None)
    # AnimateDiff-Lightning is distilled for this exact scheduler setup.
    pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config, timestep_spacing="trailing",
                                                        beta_schedule="linear")
    return pipe


def load(model_dir, kind, engine="diffusers", meta=None):
    meta = meta or {}
    if state["pipe"] is not None and state["model_dir"] == model_dir:
        return {"already_loaded": True}
    unload()
    t = time.time()
    if engine == "mflux":
        pipe = _load_mflux(model_dir, meta)
    elif engine == "animatediff":
        pipe = _load_animatediff(model_dir, meta)
        pipe.to(DEVICE)
        _tidy(pipe)
    else:
        from diffusers import AutoPipelineForText2Image
        extra = {"variant": "fp16"} if any(Path(model_dir).rglob("*.fp16.safetensors")) else {}
        if not (Path(model_dir) / "safety_checker").is_dir():
            extra.update(safety_checker=None, requires_safety_checker=False)
        pipe = AutoPipelineForText2Image.from_pretrained(model_dir, torch_dtype=torch.float16,
                                                         local_files_only=True, **extra)
        pipe.to(DEVICE)
        _tidy(pipe)
    state.update(pipe=pipe, model_dir=model_dir, kind=kind, engine=engine, meta=meta)
    trim()
    return {"load_sec": round(time.time() - t, 1), "gpu": gpu_memory()}


def set_loras(loras):
    """Hot-swaps LoRAs on the loaded base model: no base reload, already-seen LoRAs are cached."""
    pipe = state["pipe"]
    if pipe is None:
        raise RuntimeError("No model loaded")
    if state["engine"] == "mflux":
        if loras:
            raise RuntimeError("LoRAs aren't supported with the 4-bit FLUX model yet; use sd-turbo or epiCRealism for LoRAs.")
        return {"active_loras": [], "cached_loras": []}
    names, weights = [], []
    for l in loras or []:
        name = l["name"]
        if name not in state["loras"]:
            pipe.load_lora_weights(l["path"], adapter_name=name)
            state["loras"][name] = l["path"]
        names.append(name)
        weights.append(float(l.get("weight", 1.0)))
    if names:
        pipe.set_adapters(names, adapter_weights=weights)
        pipe.enable_lora()
    elif state["loras"]:
        pipe.disable_lora()
    state["active_loras"] = [{"name": n, "weight": w} for n, w in zip(names, weights)]
    return {"active_loras": state["active_loras"], "cached_loras": list(state["loras"])}


def _is_blank(image):
    import numpy as np
    arr = np.asarray(image, dtype=np.float32)
    return not np.isfinite(arr).all() or arr.std() < 2.0


def generate_image(req):
    pipe = state["pipe"]
    if pipe is None or state["kind"] != "image":
        raise RuntimeError("No image model loaded")
    seed = int(req.get("seed") or int(time.time()) % 1_000_000)
    width, height = int(req.get("width") or 512), int(req.get("height") or 512)
    t = time.time()
    if state["engine"] == "mflux":
        steps = int(req.get("steps") or state["meta"].get("default_steps", 4))
        # FLUX works in 16 px patches.
        width, height = max(256, width // 16 * 16), max(256, height // 16 * 16)
        image, _ = pipe.generate(req["prompt"], seed=seed, steps=steps, width=width, height=height)
    else:
        is_turbo = "turbo" in (state["model_dir"] or "").lower()
        steps = int(req.get("steps") or (1 if is_turbo else 25))
        guidance = float(req.get("guidance") if req.get("guidance") is not None else (0.0 if is_turbo else 7.0))
        with torch.inference_mode():
            image = pipe(
                prompt=req["prompt"], negative_prompt=req.get("negative_prompt") or None,
                num_inference_steps=steps, guidance_scale=guidance, width=width, height=height,
                generator=torch.Generator("cpu").manual_seed(seed),
            ).images[0]
    # Never report a blank image as a success (fp16 overflow / NaNs produce flat black output).
    if _is_blank(image):
        trim()
        raise RuntimeError("The model produced a blank image (numeric overflow). Try another seed, size, or model.")
    image.convert("RGB").save(req["out_path"], quality=92)
    peak = gpu_memory()
    trim()
    return {"output_path": req["out_path"], "seed": seed, "steps": steps, "sec": round(time.time() - t, 1),
            "width": image.size[0], "height": image.size[1], "gpu_peak": peak}


def generate_video(req):
    """Text to video frames (AnimateDiff). Frames are written as PNGs; the server encodes the MP4."""
    pipe = state["pipe"]
    if pipe is None or state["kind"] != "video":
        raise RuntimeError("No video model loaded")
    seed = int(req.get("seed") or int(time.time()) % 1_000_000)
    num_frames = max(8, min(int(req.get("num_frames") or 16), 32))
    width, height = int(req.get("width") or 512) // 8 * 8, int(req.get("height") or 512) // 8 * 8
    steps = int(req.get("steps") or state["meta"].get("default_steps", 4))
    if not state.get("split_inference"):
        # Run spatial layers 4 frames at a time instead of all 16 at once: the full batch needed
        # ~9.5 GB on the GPU at 512 px (measured OOM); split, it fits next to everything else.
        # FreeNoise also lets clips run past the model's 16-frame window (in overlapping windows).
        pipe.enable_free_noise(context_length=16, context_stride=4)
        pipe.enable_free_noise_split_inference(spatial_split_size=256, temporal_split_size=4)
        state["split_inference"] = True
    t = time.time()
    with torch.inference_mode():
        frames = pipe(prompt=req["prompt"], negative_prompt=req.get("negative_prompt") or None,
                      num_frames=num_frames, guidance_scale=1.0, num_inference_steps=steps, decode_chunk_size=2,
                      width=width, height=height, generator=torch.Generator("cpu").manual_seed(seed)).frames[0]
    if _is_blank(frames[len(frames) // 2]):
        trim()
        raise RuntimeError("The video model produced blank frames (numeric overflow). Try another seed or size.")
    out_dir = Path(req["frames_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(frames):
        f.convert("RGB").save(out_dir / f"{i:04d}.png")
    peak = gpu_memory()
    trim()
    return {"frames_dir": str(out_dir), "num_frames": len(frames), "seed": seed, "steps": steps,
            "sec": round(time.time() - t, 1), "width": frames[0].size[0], "height": frames[0].size[1], "gpu_peak": peak}


MEMORY_FLOOR_GB = 0.8   # below this, macOS is about to swap hard (a 16 GB Mac has hard-reset before)
PROTECT_EXIT_CODE = 70


def _memory_watchdog():
    """Kills this worker (and only it) if the whole Mac is about to run out of memory.
    The server sees the exit code and reports it; Xod and macOS stay up."""
    import threading
    import psutil

    def watch():
        low = 0
        while True:
            low = low + 1 if psutil.virtual_memory().available / 1e9 < MEMORY_FLOOR_GB else 0
            if low >= 3:
                sys.stderr.write("media worker: system memory critically low; stopping to protect the Mac\n")
                os._exit(PROTECT_EXIT_CODE)
            time.sleep(0.25)

    threading.Thread(target=watch, daemon=True).start()


def main():
    _memory_watchdog()
    reply({"ready": True, "device": DEVICE, "pid": os.getpid()})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        rid, op = req.get("id"), req.get("op")
        try:
            if op == "ping":
                out = {}
            elif op == "status":
                out = {"model_dir": state["model_dir"], "kind": state["kind"], "engine": state["engine"],
                   "active_loras": state["active_loras"],
                       "cached_loras": list(state["loras"]), "gpu": gpu_memory()}
            elif op == "load":
                out = load(req["model_dir"], req.get("kind", "image"), req.get("engine") or "diffusers", req.get("meta"))
            elif op == "unload":
                unload()
                out = {"gpu": gpu_memory()}
            elif op == "set_loras":
                out = set_loras(req.get("loras"))
            elif op == "generate_image":
                out = generate_image(req)
            elif op == "generate_video":
                out = generate_video(req)
            elif op == "shutdown":
                reply({"id": rid, "ok": True})
                break
            else:
                raise ValueError(f"unknown op {op}")
            reply({"id": rid, "ok": True, **out})
        except Exception as e:
            trim()
            msg = str(e).lower()
            oom = "out of memory" in msg or "[metal::malloc]" in msg or "memory limit" in msg
            reply({"id": rid, "ok": False, "error": str(e)[:500], "oom": oom, "trace": traceback.format_exc()[-800:]})


if __name__ == "__main__":
    main()
