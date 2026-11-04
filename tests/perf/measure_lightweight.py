"""
Idle cost of a full vs a lightweight Xod server (services/run_mode.py), measured, not assumed.
Starts each on a fresh data folder, lets it settle, then samples RSS, threads and CPU time over a window.

Run: .venv/bin/python tests/perf/measure_lightweight.py [--settle 30] [--window 60]
"""
import argparse, json, os, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--settle", type=int, default=30)
ap.add_argument("--window", type=int, default=60)
a = ap.parse_args()


def ps(pid):
    out = subprocess.run(["ps", "-o", "rss=,time=", "-p", str(pid)], capture_output=True, text=True).stdout.split()
    rss_kb, t = int(out[0]), out[1]
    parts = [float(x) for x in t.replace("-", ":").split(":")]
    secs = sum(v * 60 ** i for i, v in enumerate(reversed(parts)))
    threads = len(subprocess.run(["ps", "-M", "-p", str(pid)], capture_output=True, text=True).stdout.splitlines()) - 1
    return rss_kb / 1024, secs, threads


def tree_cpu(pid):
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    return len(out)


def run(light):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    data = tempfile.mkdtemp(prefix="xod_perf_")
    env = {**os.environ, "XOD_DATA_DIR": data, "XOD_NO_UPDATE_CHECK": "1", "XOD_LIGHTWEIGHT": "1" if light else "0"}
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                         cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/mode", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    time.sleep(a.settle)
    rss0, cpu0, th0 = ps(p.pid)
    time.sleep(a.window)
    rss1, cpu1, th1 = ps(p.pid)
    kids = tree_cpu(p.pid)
    p.terminate(); p.wait(10)
    return {"mode": "lightweight" if light else "full", "rss_mb": round(rss1, 1), "threads": th1, "child_processes": kids,
            "idle_cpu_pct": round(100 * (cpu1 - cpu0) / a.window, 2)}


rows = [run(False), run(True)]
for r in rows:
    print(json.dumps(r))
f, l = rows
print(f"lightweight vs full: RSS {l['rss_mb'] - f['rss_mb']:+.1f} MB, idle CPU {l['idle_cpu_pct'] - f['idle_cpu_pct']:+.2f} points, "
      f"threads {l['threads'] - f['threads']:+d}, child processes {l['child_processes'] - f['child_processes']:+d}")
