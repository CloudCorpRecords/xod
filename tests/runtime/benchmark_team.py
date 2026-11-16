"""Isolated scheduler probe; fake workers, NOT a model-quality benchmark.
Run from the repository: python tests/runtime/benchmark_team.py [--check]
"""
import asyncio
import argparse
import json
import os
import statistics
import sys
import tempfile
import time
import subprocess
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
TMP = tempfile.TemporaryDirectory(prefix="xod-runtime-")
os.environ["XOD_DATA_DIR"] = TMP.name
os.environ["HOME"] = TMP.name
from app.services import team

parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
parser.add_argument("--baseline-ref", help="Read team.py from this git revision without changing the checkout")
parser.add_argument("--batches", type=int, default=2)
parser.add_argument("--delay", type=float, default=.2)
args = parser.parse_args()
if args.baseline_ref:
    source = subprocess.check_output(["git", "show", f"{args.baseline_ref}:app/services/team.py"], text=True)
    team = types.ModuleType("baseline_team")
    exec(compile(source, "baseline_team.py", "exec"), team.__dict__)


async def main():
    active = peak = calls = 0
    waits, lag = [], []
    started = time.perf_counter()
    first = None
    team.load = lambda: {"max_parallel": 3}

    async def tiers():
        return {"standard": {"where": "online", "model": "fake", "provider": "fake"}}

    team.tiers = tiers

    async def worker(label, task, tier, execute, emit, agent=None):
        nonlocal active, peak, calls, first
        now = time.perf_counter()
        waits.append((now - started) * 1000)
        first = first or now
        calls += 1
        active += 1
        peak = max(peak, active)
        try:
            emit({"type": "subagent_status", "agent_id": label, "status": "running"})
            await asyncio.sleep(args.delay)
            return {"success": True, "agent": label, "summary": "fixture result"}
        finally:
            active -= 1

    team.run_subagent = worker

    async def batch():
        async for _ in team.delegate_many([{"task": "slow"}] * 3, None):
            pass

    async def heartbeat():
        for _ in range(70):
            at = time.perf_counter()
            await asyncio.sleep(.01)
            lag.append(max(0, (time.perf_counter() - at - .01) * 1000))

    pulse = asyncio.create_task(heartbeat())
    await asyncio.gather(*(batch() for _ in range(args.batches)))
    elapsed = (time.perf_counter() - started) * 1000
    await pulse
    # Closing a stream while workers run must clean them up synchronously.
    stream = team.delegate_many([{"task": "slow"}] * 3, None)
    await anext(stream)  # plan
    await anext(stream)  # progress (may be queued)
    await asyncio.sleep(.01)
    await stream.aclose()
    await asyncio.sleep(.01)
    orphans = active
    await asyncio.sleep(args.delay + .05)  # baseline's leaked fixture workers eventually end
    result = {
        "fixture": f"{args.batches} simultaneous three-worker batches; {args.delay*1000:g}ms fake calls",
        "peak_workers": peak, "orphan_workers_after_close": orphans,
        "completion_ms": round(elapsed, 2),
        "first_worker_ms": round((first - started) * 1000, 2),
        "queue_wait_p95_ms": round(max(waits[:3*args.batches]), 2),
        "loop_lag_p50_ms": round(statistics.median(lag), 2),
        "loop_lag_p95_ms": round(sorted(lag)[int(len(lag)*.95)], 2),
        "simulated_calls_including_cancel_probe": calls,
        "real_model_calls": 0, "real_tokens": None,
    }
    print(json.dumps(result, indent=2))
    if args.check:
        assert peak <= 3, result
        assert orphans == 0, result


asyncio.run(main())
