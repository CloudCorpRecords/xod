"""
Live: the real desktop shell (Electron, from source) launched headless and then with a window, from the
one saved switch, on an isolated data folder and port. Never touches an installed Xod: its own XOD_DATA_DIR,
its own Electron folder inside it (electron/main.js), its own port; the installed app's server is checked
unchanged before and after.

  D1 `xod mode headless on` (the terminal) saves the switch; the desktop shell started from it opens no
     window (no renderer page at all) and starts its server, which reports headless and lightweight
  D2 the terminal drives that headless Xod: a task is acknowledged at once and its effect lands on disk
  D3 quitting the headless shell stops the server it started
  D4 `xod mode headless off`: the next launch opens a window (one renderer page on the app)
  D5 the installed Xod on port 8000, if running, was not touched (same process before and after)

Run: .venv/bin/python tests/smoke/live_headless_desktop.py --data <scratch data dir> [--port 8221]
     (the data dir should hold a model key in .env for D2)
"""
import argparse, json, os, signal, subprocess, sys, time, urllib.request, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--data", required=True)
ap.add_argument("--port", type=int, default=8221)
ap.add_argument("--debug-port", type=int, default=9333)
args = ap.parse_args()
DATA = Path(args.data)
URL = f"http://127.0.0.1:{args.port}"
ELECTRON = ROOT / "electron" / "node_modules" / ".bin" / "electron"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def get(url, timeout=3):
    try:
        return json.loads(urllib.request.urlopen(url, timeout=timeout).read())
    except Exception:
        return None


def installed_pid():
    r = subprocess.run(["lsof", "-tiTCP:8000", "-sTCP:LISTEN"], capture_output=True, text=True)
    return r.stdout.strip() or None


def env():
    e = {**os.environ, "XOD_DATA_DIR": str(DATA), "XOD_PORT": str(args.port), "XOD_NO_UPDATE_CHECK": "1"}
    for k in ("XOD_HEADLESS", "XOD_LIGHTWEIGHT"):
        e.pop(k, None)      # the saved switch decides, as for a person
    return e


def cli(*a):
    return subprocess.run([sys.executable, "-m", "app.cli", "--no-color", "--url", URL, *a], cwd=str(ROOT), env=env(),
                          capture_output=True, text=True, timeout=180)


def launch():
    log = open(DATA / "electron.log", "a")
    p = subprocess.Popen([str(ELECTRON), ".", f"--remote-debugging-port={args.debug_port}"], cwd=str(ROOT / "electron"),
                         env=env(), stdout=log, stderr=log, start_new_session=True)
    for _ in range(180):
        if get(URL + "/api/mode"):
            break
        time.sleep(1)
    time.sleep(4)          # a window, if any, has had time to open
    return p


def pages():
    targets = get(f"http://127.0.0.1:{args.debug_port}/json/list") or []
    return [t for t in targets if t.get("type") == "page"]


def quit_shell(p):
    os.killpg(p.pid, signal.SIGTERM)
    try:
        p.wait(30)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
    for _ in range(30):
        if not get(URL + "/api/mode", timeout=1):
            return True
        time.sleep(1)
    return False


DATA.mkdir(parents=True, exist_ok=True)
before = installed_pid()
print(f"installed Xod on 8000: {before or 'not running'}")

print("D1 headless from the saved switch")
out = cli("mode", "headless", "on")
out2 = cli("mode", "light", "on")
saved = json.loads((DATA / "desktop.json").read_text())
check("the terminal saved the switch", saved.get("headless") is True and saved.get("lightweight") is True, out.stdout + out2.stdout)
shell = launch()
try:
    m = get(URL + "/api/mode")
    check("the shell started its server", m is not None)
    check("the server reports headless and lightweight", m and m["headless"] and m["server_lightweight"], json.dumps(m)[:300])
    pg = pages()
    check("no window: no renderer page at all", get(f"http://127.0.0.1:{args.debug_port}/json/version") is not None and pg == [],
          json.dumps(pg)[:300])
    check("its Electron folder is inside the test data folder", (DATA / "Electron").is_dir())

    print("D2 the terminal drives it")
    target = DATA / f"headless-{uuid.uuid4().hex[:6]}.txt"
    t0 = time.time()
    out = cli("task", "--no-wait", f"Write a file at {target} containing exactly the word lighthouse")
    tid = next((w.strip(":") for w in out.stdout.split() if w.startswith("task-")), None)
    check("xod task is acknowledged with an id", "Acknowledged" in out.stdout and tid, out.stdout + out.stderr)
    check("within a few seconds", time.time() - t0 < 10, f"{time.time() - t0:.1f}s")
    while tid and time.time() - t0 < 300:
        p = get(f"{URL}/api/tasks/{tid}/plan", timeout=10) or {}
        if p.get("status") in ("done", "failed", "cancelled"):
            break
        time.sleep(2)
    check("the task finished and its file is on disk", target.exists() and "lighthouse" in target.read_text().lower(),
          f"status {p.get('status')}")
    out = cli("plan", tid or "")
    check("xod plan shows it done", "done" in out.stdout.lower(), out.stdout[-300:])
finally:
    print("D3 quitting the headless shell")
    check("the server it started stopped", quit_shell(shell))

print("D4 window mode from the same switch")
cli("mode", "headless", "off")
check("the switch is off", json.loads((DATA / "desktop.json").read_text()).get("headless") is False)
shell = launch()
try:
    pg = pages()
    check("a window opened on the app", any(str(args.port) in (t.get("url") or "") for t in pg), json.dumps(pg)[:300])
finally:
    quit_shell(shell)
cli("mode", "light", "off")

print("D5 the installed Xod")
check("the installed Xod on port 8000 was not touched", installed_pid() == before, f"before {before} after {installed_pid()}")
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} live checks passed")
sys.exit(1 if failed else 0)
