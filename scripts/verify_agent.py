#!/usr/bin/env python3
"""Run isolated, deterministic agent checks and optionally export a JSON report.

No live-model evaluation is launched by this runner. Run from any directory:
  python scripts/verify_agent.py --output generated-artifacts/agent-verification.json
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CHECKS = (
    ("verifier negative controls", "tests/runtime/test_verification_runner.py"),
    ("tool context", "tests/runtime/test_tool_context.py"),
    ("long step sequences", "tests/runtime/test_long_running.py"),
    ("worker and crew lifecycle", "tests/runtime/test_runtime.py"),
    ("task stop boundaries", "tests/runtime/test_task_stop.py"),
    ("run observability", "tests/runtime/test_run_observability.py"),
    ("fleet scheduling", "tests/runtime/benchmark_team.py", "--check"),
    ("Muse approval policy", "tests/smoke/run_muse_app_test.py"),
    ("approval reliability", "tests/partner/run_approval_reliability_test.py"),
    ("Partner delegation", "tests/partner/run_partner_delegation_test.py"),
)


def run_check(check, timeout, profile=None):
    name, path, *args = check
    started = time.monotonic()
    try:
        env = dict(os.environ)
        if profile:
            env["XOD_TEST_CONTEXT_PROFILE"] = profile
        proc = subprocess.run([sys.executable, str(ROOT / path), *args], cwd=ROOT,
                              capture_output=True, text=True, timeout=timeout, env=env)
        output = proc.stdout + proc.stderr
        status = "passed" if proc.returncode == 0 else "failed"
        code = proc.returncode
    except subprocess.TimeoutExpired:
        output = "Check exceeded its per-process time limit."
        status, code = "timed_out", None
    except OSError as exc:
        output = f"Could not launch check: {type(exc).__name__}"
        status, code = "unavailable", None
    counts = re.findall(r"Ran (\d+) tests?", output)
    return {"name": name, "path": path, "profile": profile, "status": status, "exit_code": code,
            "duration_seconds": round(time.monotonic() - started, 3),
            "unittest_count": int(counts[-1]) if counts else None,
            "output": output[-16000:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=int, default=180, help="Seconds per check process")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat in fresh processes, 1-20")
    parser.add_argument("--profile", choices=("correctness", "local", "cloud", "all"),
                        default="all", help="Context envelope for long-sequence checks")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 20 or not 1 <= args.timeout <= 600:
        parser.error("repeat must be 1-20 and timeout must be 1-600")
    report = {"schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
              "python": platform.python_version(), "platform": platform.system(),
              "evidence_kind": "deterministic_scripted_models",
              "live_model_tested": False, "native_device_verified": False,
              "hours_long_soak": False, "checks": []}
    profiles = ("correctness", "cloud", "local") if args.profile == "all" else (args.profile,)
    for round_number in range(1, args.repeat + 1):
        for check in CHECKS:
            for profile in profiles if check[1] == "tests/runtime/test_long_running.py" else (None,):
                result = run_check(check, args.timeout, profile)
                result["round"] = round_number
                report["checks"].append(result)
                print(f"[{round_number}/{args.repeat}] {result['status'].upper()}: {result['name']} "
                      f"{profile or ''} ({result['duration_seconds']}s)", flush=True)
                if result["status"] != "passed":
                    print(result["output"], flush=True)
    report["passed"] = all(c["status"] == "passed" for c in report["checks"])
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Report: {output}")
    print("PASS" if report["passed"] else "FAIL: inspect failing checks; do not treat this build as verified.")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
