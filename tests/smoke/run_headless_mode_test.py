"""
Headless and lightweight modes (services/run_mode.py, electron/mode.js, app/cli.py).

  H1 one switch: the terminal, Settings (/api/mode) and the desktop tray share desktop.json; saving
     the switch keeps every other desktop preference
  H2 env vars override the saved switch for one process
  H3 a real server started lightweight says so, and skips exactly the optional loops; a full one skips none
  H4 the terminal is the headless way in: `xod task` is acknowledged with an id at once, `xod plan`
     shows the plan, `xod steer ... cancel` is applied and reported honestly
  H5 lightweight closes the idle sandbox browser sooner (3 minutes, unless set explicitly)

Run: .venv/bin/python tests/smoke/run_headless_mode_test.py
"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_headless_"))
os.environ.update({"XOD_DATA_DIR": str(TMP), "XOD_NO_UPDATE_CHECK": "1"})
os.environ.pop("XOD_HEADLESS", None)
os.environ.pop("XOD_LIGHTWEIGHT", None)
sys.path.insert(0, str(ROOT))
from app.services import run_mode

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def cli(*a, url):
    env = {**os.environ, "XOD_DATA_DIR": str(TMP)}
    return subprocess.run([sys.executable, "-m", "app.cli", "--no-color", "--url", url, *a], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=120)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start(light):
    port = free_port()
    env = {**os.environ, "XOD_DATA_DIR": str(TMP), "PYTHONUNBUFFERED": "1"}
    if light:
        env["XOD_LIGHTWEIGHT"] = "1"
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            return proc, url, json.loads(urllib.request.urlopen(url + "/api/mode", timeout=2).read())
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("server did not start")


print("H1 one shared switch")
(TMP / "desktop.json").write_text(json.dumps({"background": True, "lightBackground": True, "toldBackground": True}))
run_mode.set(headless=True)
saved = json.loads((TMP / "desktop.json").read_text())
check("headless saved", saved["headless"] is True and run_mode.get()["headless"] is True)
check("the desktop's other preferences are kept", saved["background"] and saved["lightBackground"] and saved["toldBackground"])
r = subprocess.run(["node", "-e", "const {resolveMode}=require('./electron/mode');"
                    f"const p=JSON.parse(require('fs').readFileSync('{TMP / 'desktop.json'}','utf8'));"
                    "console.log(resolveMode([],{},p).headless)"], cwd=str(ROOT), capture_output=True, text=True)
check("the desktop shell reads the same switch", r.stdout.strip() == "true", r.stderr[:200])
out = cli("mode", "headless", "off", url="http://127.0.0.1:1")
check("the terminal flips it", "headless     off" in out.stdout and run_mode.get()["headless"] is False, out.stdout + out.stderr)

print("H2 env overrides for one process")
os.environ["XOD_LIGHTWEIGHT"] = "1"
g = run_mode.get()
check("XOD_LIGHTWEIGHT=1 wins over the saved switch", g["lightweight"] is True and g["saved"]["lightweight"] is False
      and g["overridden_by_env"] == ["XOD_LIGHTWEIGHT"])
os.environ.pop("XOD_LIGHTWEIGHT")

print("H3 a lightweight server skips exactly the optional loops")
proc, url, m = start(light=True)
try:
    check("it reports running lightweight", m["server_lightweight"] is True)
    check("and names what it skipped", sorted(m["skipped_now"]) == sorted(run_mode.OPTIONAL_LOOPS))
    print("H4 the terminal as the headless way in")
    out = cli("task", "--no-wait", "Write a haiku about lighthouses", url=url)
    tid = next((w.strip(":") for w in out.stdout.split() if w.startswith("task-")), None)
    check("xod task is acknowledged with an id at once", "Acknowledged" in out.stdout and tid, out.stdout + out.stderr)
    out = cli("steer", tid, "cancel", url=url)
    check("xod steer cancel is reported honestly (applied or queued)", "Applied" in out.stdout or "Queued, not applied yet" in out.stdout,
          out.stdout + out.stderr)
    time.sleep(2)
    out = cli("plan", tid, url=url)
    check("xod plan shows the task and its steer", tid in out.stdout and "steer cancel" in out.stdout, out.stdout + out.stderr)
    out = cli("mode", url=url)
    check("xod mode sees the running server's mode", "lightweight" in out.stdout)
finally:
    proc.terminate()
    proc.wait(10)
proc, url, m = start(light=False)
try:
    check("a full server skips nothing", m["server_lightweight"] is False and m["skipped_now"] == [])
finally:
    proc.terminate()
    proc.wait(10)

print("H5 the idle browser closes sooner when lightweight")
code = ("import os;os.environ['XOD_DATA_DIR']=%r;" % str(TMP)
        + "from app.services import browser_sandbox as b;print(b.browser_idle_minutes())")
light = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env={**os.environ, "XOD_LIGHTWEIGHT": "1"}, capture_output=True, text=True)
full = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env={**os.environ, "XOD_LIGHTWEIGHT": "0"}, capture_output=True, text=True)
explicit = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                          env={**os.environ, "XOD_LIGHTWEIGHT": "1", "XOD_BROWSER_IDLE_MINUTES": "25"}, capture_output=True, text=True)
check("3 minutes when lightweight, 10 otherwise", light.stdout.strip() == "3" and full.stdout.strip() == "10", light.stdout + full.stdout + light.stderr[-200:])
check("an explicit setting still wins", explicit.stdout.strip() == "25")

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
