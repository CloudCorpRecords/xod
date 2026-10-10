"""Media runtime end-to-end: memory broker with the chat model loaded, warm reuse, LoRA hot-swap,
clear errors for unusable models, and the chat model coming back afterwards."""
import asyncio, os, sys, time, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np
from PIL import Image
from app.services.media_runtime import media_runtime, list_models
from app.services.memory_broker import memory_broker, available_gb
from app.services.llm import llm_service

shots = []
def content(path):
    a = np.asarray(Image.open(path).convert("RGB"), dtype=float)
    return round(a.mean(), 1), round(a.std(), 1)

def diff(a, b):
    return float(np.abs(np.asarray(Image.open(a), dtype=int) - np.asarray(Image.open(b), dtype=int)).mean())

async def gen(label, **kw):
    t = time.time()
    r = await media_runtime.generate_image(progress=lambda m: print(f"     · {m}", flush=True), **kw)
    print(f"{label}: success={r.get('success')} total={time.time()-t:.1f}s gen={r.get('seconds')}s "
          f"freed={ (r.get('memory') or {}).get('freed') } free_now={available_gb()}GB {r.get('error','')}", flush=True)
    if r.get("success"):
        shots.append(r["output_path"])
        m, sd = content(r["output_path"])
        print(f"     · image brightness {m}, contrast {sd}  ({'OK' if sd > 10 else 'BLANK!'})", flush=True)
    return r

async def main():
    teacher = await memory_broker._teacher_status()
    print("models:", [(m["id"], m["usable"], m.get("need_gb"), m.get("reason", "")[:50]) for m in list_models()], flush=True)
    print(f"start: chat model loaded={bool(teacher)} free={available_gb()}GB", flush=True)
    a = await gen("1 cold start (chat model loaded)", prompt="a red fox in a snowy forest, photo", seed=42)
    b = await gen("2 warm", prompt="a red fox in a snowy forest, photo", seed=42)
    c = await gen("3 with LoRA", prompt="a red fox in a snowy forest, photo", seed=42, loras=[{"name": "xod_test_lora", "weight": 1.0}])
    d = await gen("4 LoRA off again", prompt="a red fox in a snowy forest, photo", seed=42)
    e = await gen("5 unusable model", prompt="x", model="argmaxinc/mlx-FLUX.1-schnell")
    if all(x.get("success") for x in (a, b, c, d)):
        print(f"same seed, no LoRA vs warm: pixel diff {diff(a['output_path'], b['output_path']):.2f} (expect ~0)")
        print(f"LoRA on vs off:             pixel diff {diff(b['output_path'], c['output_path']):.2f} (expect clearly > 0)")
        print(f"LoRA off again vs original: pixel diff {diff(a['output_path'], d['output_path']):.2f} (expect ~0)")
    print("runtime:", json.dumps(media_runtime.status()), flush=True)
    print("broker:", json.dumps({k: v for k, v in memory_broker.status().items() if k != 'recent'}), flush=True)
    for ev in memory_broker.status()["recent"]: print("   broker log:", ev["text"], flush=True)
    t = time.time()
    reply = await llm_service.chat_completion(messages=[{"role": "user", "content": "Reply with just the word ready."}], max_tokens=50, think=False)
    print(f"6 chat model afterwards: answered {reply.get('content','')[:40]!r} in {time.time()-t:.1f}s; "
          f"chat model loaded={bool(await memory_broker._teacher_status())} free={available_gb()}GB", flush=True)
    await media_runtime.release(full=True)
    print("SHOTS", json.dumps(shots), flush=True)

asyncio.run(main())
