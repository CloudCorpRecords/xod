"""Base-model swapping across engines + real video, with the chat model loaded at the start.

FLUX (MLX, 4-bit) -> sd-turbo (diffusers) -> epiCRealism (diffusers) -> AnimateDiff video -> FLUX again,
then the chat model has to come back. Checks every output for real content, not just success flags.
Outputs are copied to data/test_runs/media_swap/ for a look.
"""
import asyncio, json, shutil, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
import sys as _sys; _sys.path.insert(0, str(ROOT)); from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
sys.path.insert(0, str(ROOT))
import numpy as np
from PIL import Image
from app.services.media_runtime import media_runtime, list_models, OUTPUT_DIR
from app.services.memory_broker import memory_broker, available_gb
from app.services.llm import llm_service

OUT = _DATA / "test_runs" / "media_swap"
OUT.mkdir(parents=True, exist_ok=True)
results = []


def say(m):
    print(f"     · {m}", flush=True)


def keep(path, name):
    dst = OUT / name
    shutil.move(path, dst)
    return dst


def video_check(path):
    from app.services.media_studio import FFMPEG_BIN
    tmp = OUT / "_frames"
    shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir()
    subprocess.run([FFMPEG_BIN, "-v", "error", "-i", str(path), str(tmp / "%03d.png")], check=True)
    files = sorted(tmp.glob("*.png"))
    picks = [files[0], files[len(files) // 2], files[-1]]
    frames = [np.asarray(Image.open(p).convert("RGB"), dtype=float) for p in picks]
    shutil.rmtree(tmp)
    motion = float(np.abs(frames[0] - frames[-1]).mean())
    return {"frames": len(files), "duration": round(len(files) / 24, 2)}, [round(f.std(), 1) for f in frames], round(motion, 1)


async def step(label, fn, name, **kw):
    t = time.time()
    r = await fn(progress=say, **kw)
    ok = bool(r.get("success"))
    line = f"{label}: success={ok} total={time.time()-t:.1f}s gen={r.get('seconds')}s free_now={available_gb()}GB"
    if ok:
        dst = keep(r["output_path"], name)
        if r.get("poster_url"):
            (OUTPUT_DIR / r["poster_url"].split("/")[-1]).unlink(missing_ok=True)
        if name.endswith(".mp4"):
            probe, stds, motion = video_check(dst)
            ok = min(stds) > 10 and motion > 3
            line += f" frames={probe['frames']} (~{probe['duration']}s @24fps) frames_contrast={stds} motion={motion} {'OK' if ok else 'BAD'}"
        else:
            sd = np.asarray(Image.open(dst).convert("RGB"), dtype=float).std()
            ok = sd > 10
            line += f" size={Image.open(dst).size} contrast={sd:.1f} {'OK' if ok else 'BLANK!'}"
        line += f" peak={r.get('memory', {}).get('gpu_peak')} freed={r.get('memory', {}).get('freed')}"
    else:
        line += f" ERROR {r.get('error')}"
    print(line, flush=True)
    results.append((label, ok))
    return r


async def main():
    print("usable:", [(m["id"], m["kind"], m.get("engine"), m.get("need_gb")) for m in list_models() if m["usable"]], flush=True)
    print(f"start: chat model loaded={bool(await memory_broker._teacher_status())} free={available_gb()}GB", flush=True)
    prompt = "a cozy cabin in a snowy pine forest at dusk, warm light in the windows, photo"
    img = media_runtime.generate_image
    await step("1 FLUX 4-bit (cold, chat model loaded)", img, "1_flux.jpg", prompt=prompt, model="flux-schnell-4bit", seed=7)
    await step("2 swap -> sd-turbo", img, "2_sdturbo.jpg", prompt=prompt, model="sd-turbo", seed=7)
    await step("3 swap -> epiCRealism", img, "3_epicrealism.jpg", prompt=prompt, model="epiCRealism", seed=7, steps=20)
    await step("4 video (AnimateDiff-Lightning)", media_runtime.generate_video, "4_video.mp4",
               prompt="ocean waves crashing on a rocky shore at sunset, cinematic", seed=7, seconds=2.0)
    await step("5 swap back -> FLUX", img, "5_flux_again.jpg", prompt="a red fox sitting in fresh snow, photo", model="flux-schnell-4bit", seed=3)
    await step("6 unusable model", img, "x.jpg", prompt="x", model="argmaxinc/mlx-FLUX.1-schnell")
    results[-1] = (results[-1][0], not results[-1][1])  # expected to fail cleanly
    print("runtime:", json.dumps(media_runtime.status()), flush=True)
    for ev in memory_broker.status()["recent"]:
        print("   broker log:", ev["text"], flush=True)
    t = time.time()
    reply = await llm_service.chat_completion(messages=[{"role": "user", "content": "Reply with just the word ready."}], max_tokens=50, think=False)
    back = bool(await memory_broker._teacher_status())
    print(f"7 chat model afterwards: {reply.get('content','')[:30]!r} in {time.time()-t:.1f}s loaded={back} free={available_gb()}GB", flush=True)
    results.append(("7 chat model back", back))
    await media_runtime.release(full=True)
    print("SUMMARY", "PASS" if all(ok for _, ok in results) else "FAIL", [l for l, ok in results if not ok], flush=True)

asyncio.run(main())
