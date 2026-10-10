"""
End-to-end check of "Learn from mistakes" (preference training) in the sandbox: teacher corrections
of failed steps + the student's own mistakes -> DPO on top of the current student -> graded on the
same held-out test as before. Run tests/model_lab/train_from_test_runs.py first.
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
F.TRAINING_DIR, F.MODELS_DIR, F.JOBS_PATH = SANDBOX / "training", SANDBOX / "models", SANDBOX / "models" / "jobs.json"
SP.MODELS_DIR, SP.STATE_PATH = SANDBOX / "models", SANDBOX / "models" / "specialists.json"


async def main() -> None:
    svc = F.FineTuningService()
    t0 = time.time()
    r = await svc.start_preference_job("browser_navigator")
    print("START", json.dumps(r), flush=True)
    if not r.get("success"):
        return
    last = None
    while True:
        await asyncio.sleep(10)
        j = svc.get_job_status(r["job_id"])["job"]
        line = (f"[{int(time.time() - t0)}s] {j['status']} step {j.get('current_iter')}/{j.get('total_iters')} "
                f"loss={j.get('loss')} val_pref_acc={j.get('val_pref_acc')} free={j.get('memory', {}).get('available_now_gb')}GB")
        if line != last:
            print(line, flush=True)
            last = line
        if j["status"] not in ("preparing", "training", "evaluating"):
            break
    print("PAIRS", json.dumps(j.get("pairs_made")), "total saved:", j.get("pairs_total"), flush=True)
    for n in j.get("preference_notes", []):
        print("NOTE", n, flush=True)
    if j.get("stop_reason"):
        print("STOPPED", j["stop_reason"], flush=True)
    base, ev = j.get("baseline_eval") or {}, j.get("eval") or {}
    print("BEFORE", json.dumps({k: base.get(k) for k in ("action_match_rate", "valid_json_rate", "avg_latency_ms")}), flush=True)
    print("AFTER ", json.dumps({k: ev.get(k) for k in ("action_match_rate", "valid_json_rate", "avg_latency_ms")}), flush=True)
    for smp in ev.get("samples", [])[:6]:
        print("  sample", json.dumps(smp)[:200], flush=True)
    print("DONE", j["status"], "improvement", j.get("improvement"), "kept_previous", j.get("kept_previous"),
          "peak", j.get("memory", {}).get("peak_gb"), flush=True)


asyncio.run(main())
