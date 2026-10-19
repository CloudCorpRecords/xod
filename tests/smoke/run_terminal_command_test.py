"""
The `xod` command (services/terminal_command.py, the npm package in npm/, app/cli.py finding the app's server), in a
throwaway home folder and data folder:
  - Install puts `xod` where a new terminal looks, adding the folder to PATH in the shell profile only if it isn't
    already there; the installed command really runs Xod in the terminal
  - it never replaces an `xod` that isn't Xod's own; Remove deletes only its own file and its PATH line
  - `xod` finds the app's server even when it isn't on port 8000, and doesn't start a second Xod on the same data
  - the npm launcher runs a checkout's Xod (XOD_HOME), and without the app says where to get it
  .venv/bin/python tests/smoke/run_terminal_command_test.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-termcmd-"))
HOMEDIR = TMP / "home"
HOMEDIR.mkdir()
DATA = TMP / "data"
DATA.mkdir()
WIN = sys.platform == "win32"
ENV = {**os.environ, "XOD_PYTHON": sys.executable, "HOME": str(HOMEDIR), "USERPROFILE": str(HOMEDIR), "LOCALAPPDATA": str(HOMEDIR / "AppData" / "Local"),
       "XOD_DATA_DIR": str(DATA), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
       "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1", "SHELL": "/bin/zsh", "PYTHONUTF8": "1"}
ENV.pop("XOD_PORT", None)
os.environ.update({k: v for k, v in ENV.items() if k in ("HOME", "USERPROFILE", "LOCALAPPDATA", "XOD_DATA_DIR", "PYTHON_KEYRING_BACKEND",
                                                         "XOD_TEST_KEYRING_FILE", "XOD_NO_UPDATE_CHECK", "SHELL")})
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    from app.services import terminal_command as tc
    # Only folders inside this test's home: one on PATH (as /opt/homebrew/bin is on many Macs) and one that isn't.
    onpath, offpath = HOMEDIR / "onpath-bin", HOMEDIR / ".local" / "bin"
    tc.CANDIDATES = [offpath, onpath]

    print("T1 installing the command")
    s = tc.status()
    check("not installed to begin with", not s["installed"], str(s))
    r = tc.install()
    f = Path(r["path"])
    check("Install writes xod where a terminal looks", r["success"] and f.exists(), str(r))
    if not WIN:
        check("…executable", os.access(f, os.X_OK))
        prof = HOMEDIR / ".zprofile"
        on_path = r["on_path"]
        check("…and, since that folder wasn't on PATH, one line in the shell profile adds it", r["added_to_profile"] and
              tc.PROFILE_LINE in prof.read_text(), str(r))
        r2 = tc.install()
        check("installing again doesn't add the line twice", prof.read_text().count(tc.PROFILE_LINE) == 1 and r2["success"])
        out = subprocess.run(["/bin/zsh", "-lc", "command -v xod && xod --help"], capture_output=True, text=True, env=ENV, timeout=60)
        check("a new terminal finds xod and it runs Xod", str(f) in out.stdout and "xod" in out.stdout.lower() and out.returncode == 0,
              (out.stdout + out.stderr)[-400:])
    else:
        check("on Windows it goes in WindowsApps (already on PATH)", f.name == "xod.cmd" and "WindowsApps" in str(f))
    check("the app says it's installed", tc.status()["installed"] and tc.status()["current"])

    if not WIN:
        tc.uninstall()
        onpath.mkdir()
        real_path = tc.shell_path
        tc.shell_path = lambda: real_path() + [str(onpath)]
        r = tc.install()
        check("a folder already on PATH is used as is, with no profile line", r["success"] and r["path"] == str(onpath / "xod")
              and not r["added_to_profile"] and tc.PROFILE_LINE not in (HOMEDIR / ".zprofile").read_text(), str(r))
        tc.uninstall()
        tc.shell_path = real_path
        onpath.rmdir()
        r = tc.install()
        f = Path(r["path"])

    print("T2 not someone else's")
    r = tc.uninstall()
    check("Remove deletes it", r["removed"] and not f.exists())
    if not WIN:
        prof = HOMEDIR / ".zprofile"
        check("…and its PATH line", not prof.exists() or tc.PROFILE_LINE not in prof.read_text())
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("#!/bin/sh\necho some other tool called xod\n")
    r = tc.install()
    check("an xod that isn't Xod's is never replaced", r["success"] is False and "leaves it alone" in r["error"] and "other tool" in f.read_text(), str(r))
    r = tc.uninstall()
    check("…nor removed", r["success"] is False and f.exists())
    f.unlink()

    print("T3 finding the app's server on another port")
    port = free_port()
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=ROOT, env=ENV, stdout=open(TMP / "server.log", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(240):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
                break
            except Exception:
                time.sleep(0.25)
        ann = json.loads((DATA / "server.json").read_text())
        check("the server writes down where it is", ann["url"] == f"http://127.0.0.1:{port}", str(ann))
        cli = [str(ROOT / ("xod.cmd" if WIN else "xod"))]
        out = subprocess.run(cli + ["status", "--json"], capture_output=True, text=True, env=ENV, timeout=120, cwd=TMP)
        try:
            st = json.loads(out.stdout)
        except ValueError:
            st = {}
        check("xod finds it there, though it isn't on 8000", out.returncode == 0 and str(port) in json.dumps(st), (out.stdout + out.stderr)[-500:])
        check("…and didn't start a second Xod", not (DATA / "cli-server.json").exists() or str(port) in (DATA / "cli-server.json").read_text())
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except Exception:
            srv.kill()

    print("T4 the npm package")
    node = shutil.which("node")
    if not node:
        print("  (skipped: no node)")
        return
    out = subprocess.run([node, str(ROOT / "npm" / "bin" / "xod.js"), "--help"], capture_output=True, text=True,
                         env={**ENV, "XOD_HOME": str(ROOT)}, timeout=60)
    check("with XOD_HOME it runs that checkout's Xod", out.returncode == 0 and "xod" in out.stdout.lower(), (out.stdout + out.stderr)[-300:])
    out = subprocess.run([node, str(ROOT / "npm" / "bin" / "xod.js"), "status"], capture_output=True, text=True,
                         env={**ENV, "XOD_HOME": ""}, timeout=60)
    app_here = any(Path(p).exists() for p in ("/Applications/Xod.app/Contents/Resources/backend/app/cli.py",))
    if not app_here:
        check("without the app it says where to get it", out.returncode == 1 and "releases/latest" in out.stderr, out.stderr[-300:])
    pkg = json.loads((ROOT / "npm" / "package.json").read_text())
    from app.version import VERSION
    check("the npm package's version is the app's", pkg["version"] == VERSION, f"{pkg['version']} vs {VERSION}")
    check("it installs a command called xod", pkg["bin"] == {"xod": "bin/xod.js"})


if __name__ == "__main__":
    main()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)
