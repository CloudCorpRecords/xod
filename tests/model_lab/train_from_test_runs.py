"""
End-to-end check of the Model Lab pipeline: train the Browser Navigator student on the
examples collected by the browser-mission suite, then grade it on held-out examples.

Isolated from your real data: reads data/test_runs/flywheel_test.db and writes models,
datasets and state under data/test_runs/lab_sandbox/.

    XOD_DATASET_DB=data/test_runs/flywheel_test.db .venv/bin/python tests/browser_missions/run_suite.py A D E F
    .venv/bin/python tests/model_lab/train_from_test_runs.py [base_model]
"""
import asyncio, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys as _sys; _sys.path.insert(0, str(ROOT)); from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
os.environ.setdefault("XOD_DATASET_DB", str(_DATA / "test_runs" / "flywheel_test.db"))
sys.path.insert(0, str(ROOT))

import app.services.finetuning as F
import app.services.specialists as SP

SANDBOX = _DATA / "test_runs" / "lab_sandbox"
(SANDBOX / "models").mkdir(parents=True, exist_ok=True)
F.TRAINING_DIR, F.MODELS_DIR, F.JOBS_PATH = SANDBOX / "training", SANDBOX / "models", SANDBOX / "models" / "jobs.json"
SP.MODELS_DIR, SP.STATE_PATH = SANDBOX / "models", SANDBOX / "models" / "specialists.json"


async def main() -> None:
    svc = F.FineTuningService()
    base = sys.argv[1] if len(sys.argv) > 1 else None
    t0 = time.time()
    r = await svc.start_training_job("browser_navigator", base_model=base)
    print("START", json.dumps(r), flush=True)
    if not r.get("success"):
        return
    last = None
    while True:
        await asyncio.sleep(15)
        j = svc.get_job_status(r["job_id"])["job"]
        line = (f"[{int(time.time() - t0)}s] {j['status']} step {j.get('current_iter')}/{j['total_iters']} "
                f"loss={j.get('loss')} val={j.get('val_loss')} free={j.get('memory', {}).get('available_now_gb')}GB")
        if line != last:
            print(line, flush=True)
            last = line
        if j["status"] not in ("training", "evaluating"):
            break
    if j.get("stop_reason"):
        print("STOPPED", j["stop_reason"], flush=True)
    ev = j.get("eval") or {}
    print("EVAL", json.dumps({k: v for k, v in ev.items() if k != "samples"}), flush=True)
    for smp in ev.get("samples", [])[:6]:
        print("  sample", json.dumps(smp)[:260], flush=True)
    print("DONE", j["status"], f"max_seq={j.get('max_seq_length')}", f"teacher_paused={j.get('teacher_paused')}", flush=True)


asyncio.run(main())
