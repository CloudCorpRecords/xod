"""Measure idle process trees without changing product services or loading models.

Launch an isolated Linux dry run, or attach read-only to an existing desktop
backend. Output contains process names and metrics, never environment or argv.
"""
import argparse
import json
import math
import os
from pathlib import Path
import platform
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

import psutil

MIB = 1024 ** 2
ROOT = Path(__file__).resolve().parents[2]
CHILD = """
import sys, json
from pathlib import Path
sys.path[:] = json.loads(sys.argv[3])
from app import paths
paths.DEV_ENV_PATH = Path(sys.argv[2]) / "absent.env"
from app.services.storage import set_setting
set_setting("muse_auto_approve", "on")
import uvicorn
uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[1]),
            log_level="warning", access_log=False)
"""


def percentile(values, q):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def describe(values):
    if not values:
        return None
    return {
        "min": min(values), "median": statistics.median(values),
        "mean": statistics.mean(values), "p95": percentile(values, .95),
        "max": max(values), "stdev": statistics.pstdev(values),
    }


def descendants(pid, errors=None):
    try:
        parent = psutil.Process(pid)
        return [parent, *parent.children(recursive=True)]
    except psutil.Error as exc:
        if errors is not None:
            errors.append(type(exc).__name__)
        return []


def api(port, path):
    # Ignore proxy environment and use only the known loopback API.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=.5) as response:
        return json.load(response)


def host_metadata():
    limits = {}
    for name in ("cpu.max", "memory.max"):
        try:
            limits[name] = (Path("/sys/fs/cgroup") / name).read_text().strip()
        except OSError:
            limits[name] = None
    return {
        "platform": platform.platform(), "machine": platform.machine(),
        "processor": platform.processor(), "python": platform.python_version(),
        "logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(logical=False),
        "host_memory_mib": psutil.virtual_memory().total / MIB,
        "cgroup_limits": limits, "psutil": psutil.__version__,
    }


def collect(args, backend, started):
    roots = {"backend": backend}
    for name in ("partner", "model", "shell"):
        if getattr(args, name + "_pid"):
            roots[name] = getattr(args, name + "_pid")
    previous, inventory, samples, root_identities = {}, {}, [], {}
    ready_at = started if args.pid else None
    next_sample = started
    initial_state = None
    while True:
        now = time.monotonic()
        if ready_at is None:
            try:
                initial_state = api(args.port, "/api/mode")
                ready_at = time.monotonic()
            except Exception:
                if now - started > args.startup_timeout:
                    raise RuntimeError("Backend did not become ready within startup timeout.")
        phase = "startup" if ready_at is None else (
            "settling" if now < ready_at + args.settle else "idle")
        groups, seen, trees = {}, set(), {}
        for group in ("partner", "model", "shell", "backend"):
            if group not in roots:
                continue
            errors = []
            tree = descendants(roots[group], errors)
            if not tree and not errors:
                errors.append("RootUnavailable")
            # Pin root identity, not just PID. Never start measuring a different
            # program when the OS reuses a PID during a long collection.
            if tree:
                try:
                    identity = (tree[0].pid, tree[0].create_time())
                    if root_identities.setdefault(group, identity) != identity:
                        errors.append("RootPIDReused")
                        tree = []
                except psutil.Error as exc:
                    errors.append(type(exc).__name__)
                    tree = []
            trees[group] = (tree, errors)
        # Count nested explicit roots before their ancestors. Electron may own
        # the backend, or the backend may own a model/Partner: fixed group
        # priority misattributes one of those two layouts.
        order = sorted(trees, key=lambda g: sum(
            any(p.pid == roots[g] for p in tree)
            for other, (tree, _) in trees.items() if other != g), reverse=True)
        for group in order:
            tree, errors = trees[group]
            rss = cpu_percent = count = 0
            cpu_errors = []
            for proc in tree:
                try:
                    if proc.pid == os.getpid():
                        if proc.pid == roots[group]:
                            errors.append("CollectorRootExcluded")
                        continue
                    identity = (proc.pid, proc.create_time())
                    if identity in seen:
                        continue
                    seen.add(identity)
                    if proc.status() == psutil.STATUS_ZOMBIE:
                        errors.append("ZombieProcess")
                        continue
                    mem = proc.memory_info().rss
                    cpu = proc.cpu_times()
                    total = cpu.user + cpu.system
                    old = previous.get(identity)
                    if old and now > old[1]:
                        cpu_percent += 100 * max(0, total - old[0]) / (now - old[1])
                    else:
                        cpu_errors.append("CPUBaselineUnavailable")
                    previous[identity] = (total, now)
                    rss += mem
                    count += 1
                    inventory[str(identity)] = {
                        "pid": proc.pid, "ppid": proc.ppid(), "name": proc.name(),
                        "group": group, "create_time": identity[1],
                        "first_seen_s": inventory.get(str(identity), {}).get("first_seen_s", now - started),
                        "last_seen_s": now - started,
                    }
                except psutil.Error as exc:
                    errors.append(type(exc).__name__)
            groups[group] = {
                "rss_mib": rss / MIB if not errors else None, "processes": count,
                "cpu_percent_one_core": cpu_percent if not errors and not cpu_errors and samples else None,
                "status": "UNAVAILABLE" if errors else "OBSERVED",
                "unavailable_reasons": sorted(set(errors)),
                "cpu_unavailable_reasons": sorted(set(errors + cpu_errors)),
            }
        samples.append({
            "elapsed_s": now - started, "monotonic": now, "phase": phase,
            "groups": groups,
        })
        if ready_at is not None and now >= ready_at + args.settle + args.duration:
            break
        next_sample += args.interval
        time.sleep(max(0, next_sample - time.monotonic()))

    summaries = {}
    for group in roots:
        idle = [s["groups"][group] for s in samples if s["phase"] == "idle"]
        peaks = [s["groups"][group]["rss_mib"] for s in samples
                 if s["phase"] != "idle" and s["groups"][group]["rss_mib"] is not None]
        summaries[group] = {
            "idle_samples": len(idle),
            "unavailable_samples": sum(s["status"] == "UNAVAILABLE" for s in idle),
            "rss_mib": describe([s["rss_mib"] for s in idle if s["rss_mib"] is not None]),
            "cpu_percent_one_core": describe([
                s["cpu_percent_one_core"] for s in idle
                if s["cpu_percent_one_core"] is not None]),
            "startup_and_settle_peak_rss_mib": max(peaks) if peaks else None,
        }
    totals = [s for s in samples if s["phase"] == "idle"]
    def total_series(metric):
        return [sum(g[metric] for g in s["groups"].values()) for s in totals
                if all(g[metric] is not None for g in s["groups"].values())]
    summaries["observed_total"] = {
        "rss_mib": describe(total_series("rss_mib")),
        "cpu_percent_one_core": describe(total_series("cpu_percent_one_core")),
        "rss_valid_samples": len(total_series("rss_mib")),
        "cpu_valid_samples": len(total_series("cpu_percent_one_core")),
    }
    summaries["whole_application"] = {
        "verification": "UNKNOWN", "rss_mib": None, "cpu_percent_one_core": None,
        "reason": "Observed roots are not proof of complete native application coverage. Review real-device evidence.",
    }
    final_state = {}
    for name, path in (("mode", "/api/mode"), ("approvals", "/api/crew/settings/auto-approve")):
        try:
            final_state[name] = api(args.port, path)
        except Exception:
            final_state[name] = {"unavailable": True}
    # Whitelist service evidence; do not export arbitrary server payloads.
    approvals = final_state.get("approvals", {})
    return {
        "schema": 1, "label": args.label,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(
            time.time() - (time.monotonic() - started))),
        "host": host_metadata(),
        "configuration": {
            "mode": "attach" if args.pid else "isolated-linux-dry-run",
            "settle_s": args.settle, "idle_duration_s": args.duration,
            "interval_s": args.interval, "model_loaded_by_collector": False,
            "synthetic_backend": False, "api_polling_during_idle": False,
            "explicit_process_roots": roots,
            "collector_pid_excluded": os.getpid(),
            "missing_components": [g for g in ("partner", "model", "shell") if g not in roots],
        },
        "ready_s": ready_at - started if not args.pid else None,
        "initial_mode": initial_state,
        "final_mode": final_state.get("mode"),
        "approval_state": {k: approvals.get(k) for k in (
            "on", "mode", "dry_run", "backend", "watcher_running", "watcher_notice")},
        "summary": summaries, "process_inventory": list(inventory.values()),
        "samples": [{k: v for k, v in s.items() if k != "monotonic"} for s in samples],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, help="Attach to an existing backend, without launching or changing it")
    for name in ("partner", "model", "shell"):
        parser.add_argument("--" + name + "-pid", type=int)
    parser.add_argument("--port", type=int, default=5012)
    parser.add_argument("--settle", type=float, default=30)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--startup-timeout", type=float, default=60)
    parser.add_argument("--label", default="lightweight-idle")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if any(pid is not None and pid <= 0 for pid in (
            args.pid, args.partner_pid, args.model_pid, args.shell_pid)):
        parser.error("Process IDs must be positive.")
    if args.pid == os.getpid():
        parser.error("The collector cannot be its own backend root.")
    if (not all(math.isfinite(n) for n in (
            args.duration, args.interval, args.startup_timeout, args.settle))
            or min(args.duration, args.interval, args.startup_timeout) <= 0 or args.settle < 0):
        parser.error("Duration, interval and timeout must be positive; settling must be nonnegative.")
    if not args.pid and sys.platform != "linux":
        parser.error("Isolated launch is Linux-only. Use --pid to observe a native installation.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    child = None
    with tempfile.TemporaryDirectory(prefix="xod-footprint-") as temp:
        log = args.output.with_suffix(".log")
        try:
            with log.open("w") as stream:
                started = time.monotonic()
                if not args.pid:
                    # No inherited API keys, desktop identity, developer config or integration credentials.
                    env = {k: os.environ[k] for k in (
                        "PATH", "PYTHONPATH", "LD_LIBRARY_PATH", "SSL_CERT_FILE", "SSL_CERT_DIR",
                        "LANG", "NIX_SSL_CERT_FILE") if k in os.environ}
                    env.update({
                        "HOME": temp, "XDG_CONFIG_HOME": temp + "/config",
                        "XDG_DATA_HOME": temp + "/share", "XOD_DATA_DIR": temp + "/data",
                        "XOD_PARTNER_STATE_DIR": temp + "/partner",
                        "XOD_LIGHTWEIGHT": "1", "XOD_MUSE_APP_DRY_RUN": "1",
                        "XOD_NO_UPDATE_CHECK": "1", "XOD_SPEECH": "off",
                        "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
                    })
                    child = subprocess.Popen(
                        [sys.executable, "-c", CHILD, str(args.port), temp,
                         json.dumps([str(ROOT), *[p for p in sys.path if p]])],
                        cwd=ROOT, env=env, stdout=stream, stderr=stream,
                        start_new_session=True,
                    )
                result = collect(args, args.pid or child.pid, started)
                args.output.write_text(json.dumps(result, indent=2) + "\n")
                print(json.dumps({k: result[k] for k in (
                    "label", "ready_s", "host", "summary", "approval_state")}, indent=2))
        finally:
            if child:
                # Only our isolated session, never an attached user's process.
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(child.pid, sig)
                    except ProcessLookupError:
                        break
                    try:
                        child.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        continue
                    if sig == signal.SIGKILL:
                        break


if __name__ == "__main__":
    main()
