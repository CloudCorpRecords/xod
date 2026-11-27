"""CLI entrypoint for Xod Infra (`xod-infra doctor|tune|bench|serve`)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from xod_infra.bench import benchmark_endpoint
from xod_infra.hardware import detect_hardware
from xod_infra.tuner import apply_ollama_launchctl_env, build_tuning_plan


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="xod-infra", description="Apple Silicon local LLM acceleration engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="Inspect Apple Silicon chip, RAM, bandwidth, and local runtimes")
    tp = sub.add_parser("tune", help="Show or apply optimal Mac tuning (Flash Attention, Q8 KV cache, MLX speculative)")
    tp.add_argument("--apply", action="store_true", help="Apply Ollama launchctl env tuning on macOS")

    bp = sub.add_parser("bench", help="Run a live tok/s & TTFT benchmark against a local backend")
    bp.add_argument("--url", default="http://127.0.0.1:11434/v1", help="OpenAI-compatible base URL")
    bp.add_argument("--model", default="", help="Model ID (defaults to first loaded model)")

    sp = sub.add_parser("serve", help="Start the Xod Infra OpenAI-compatible gateway + dashboard")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8790)

    args = p.parse_args(argv)

    if args.cmd == "doctor":
        print(json.dumps(detect_hardware(), indent=2))
        return 0
    if args.cmd == "tune":
        if args.apply:
            print(json.dumps(apply_ollama_launchctl_env(), indent=2))
        else:
            print(json.dumps(build_tuning_plan(), indent=2))
        return 0
    if args.cmd == "bench":
        res = asyncio.run(benchmark_endpoint(args.url, model=args.model))
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1
    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("xod_infra.proxy:app", host=args.host, port=args.port, log_level="info")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
