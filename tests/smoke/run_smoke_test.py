#!/usr/bin/env python3
"""
Smoke test for the whole app: starts its own Xod on a throwaway data folder, then

  1. calls every GET endpoint that takes no parameters and fails on any 5xx,
  2. opens the UI in headless Chrome, opens every panel and tool, and fails on any
     JavaScript error or console error,
  3. checks the security guard still refuses requests from other websites.

Needs Google Chrome. No model is needed (nothing here asks the AI anything).
  .venv/bin/python tests/smoke/run_smoke_test.py
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "media"))

# Endpoints that start long jobs, talk to outside services or open apps on this Mac, so a blind GET isn't right.
SKIP_GET = {"/api/voice/transcribe", "/api/doctor/run", "/api/system/screenshot"}

PANELS = [
    "openSettingsModal()", "openActivityPanel()", "openRoutinesPanel()", "openRoutingPanel()", "openMcpPanel()",
    "openPacksPanel()", "openHealthPanel()", "openTasksPanel()", "openSetupWizard()", "openMemoryVaultModal()",
    "openMediaStudioModal()", "openModelLabModal()", "openObservabilityModal()", "openKnowledgeGraphModal()",
    "openComputerFileManagerModal()", "openRoadmapModal()", "openLMStudioModal()", "openCreateProjectModal()",
    "openAutonomousCockpitModal()", "openDealsModal()", "openHunterSuiteModal()", "openTechAuditModal()",
    "openMailPanel()", "openSlashCommandPalette()", "toggleToolsMenu(new MouseEvent('click'))", "toggleSidebar()",
]

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


def api_routes():
    os.environ.setdefault("XOD_DATA_DIR", tempfile.mkdtemp(prefix="xod-routes-"))
    from app.main import app
    out = []
    for r in app.routes:
        path = getattr(r, "path", "")
        if "GET" in (getattr(r, "methods", None) or set()) and path.startswith("/api/") and "{" not in path:
            out.append(path)
    return sorted(set(out) - SKIP_GET)


async def ui_checks(base):
    from studio import Studio
    async with Studio(width=1440, height=900, scale=1, port=free_port()) as s:
        await s.cmd("Runtime.enable")
        errors = []

        # Collect page errors and console errors as they happen.
        async def reader():
            async for raw in s.ws:
                msg = json.loads(raw)
                if "id" in msg and msg["id"] in s._pending:
                    s._pending.pop(msg["id"]).set_result(msg)
                elif msg.get("method") == "Runtime.exceptionThrown":
                    d = msg["params"]["exceptionDetails"]
                    errors.append("exception: " + (d.get("exception", {}).get("description") or d.get("text", ""))[:300])
                elif msg.get("method") == "Runtime.consoleAPICalled" and msg["params"]["type"] == "error":
                    errors.append("console: " + " ".join(str(a.get("value", a.get("description", ""))) for a in msg["params"]["args"])[:300])
        s.reader.cancel()
        s.reader = asyncio.create_task(reader())

        await s.goto(base + "/", wait=4)
        check("UI loads with the Xod title", (await s.js("document.title")) == "Xod")
        check("UI: no errors on load", not errors, "; ".join(errors[:3]))
        check("UI: the moon is drawn", bool(await s.js("!!document.querySelector('.xod-mascot--hero svg .xm-rig')")))

        for call in PANELS:
            before = len(errors)
            fn = call.split("(")[0]
            exists = await s.js(f"typeof {fn} === 'function'")
            if not exists:
                check(f"panel {fn} exists", False, "function missing")
                continue
            try:
                await s.js(f"Promise.resolve({call}).then(() => true)")
                await asyncio.sleep(1.2)
                # Close whatever opened, the way a person would.
                await s.key("Escape", "Escape", 27)
                await s.js("document.querySelectorAll('.fixed:not(.hidden) [onclick*=\"close\"]').forEach(b => { try { if (b.offsetParent) b.click(); } catch (_) {} })")
                await asyncio.sleep(0.3)
                new = errors[before:]
                check(f"panel {fn}", not new, "; ".join(new[:2]))
            except Exception as e:
                check(f"panel {fn}", False, str(e)[:200])

        # Chat box takes text and the slash palette doesn't throw.
        await s.goto(base + "/", wait=3)
        await s.click("#chatInput")
        await s.type("/", cps=50)
        await asyncio.sleep(0.6)
        await s.js("document.getElementById('chatInput').value = ''")
        check("UI: typing in the chat box", not errors, "; ".join(errors[-2:]))


def main():
    data = Path(tempfile.mkdtemp(prefix="xod-smoke-", dir="/tmp"))
    port = free_port()
    env = dict(os.environ, XOD_DATA_DIR=str(data), XOD_SECRETS_SERVICE="Xod test keys", PYTHONUNBUFFERED="1")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            if get(base + "/")[0] == 200:
                break
            time.sleep(0.5)
        check("server starts", get(base + "/")[0] == 200)

        # 1. every simple GET endpoint
        os.environ["XOD_DATA_DIR"] = str(data)
        routes = api_routes()
        bad = []
        for path in routes:
            code, body = get(base + path)
            if code >= 500 or code == 0:
                bad.append(f"{path} → {code} {body[:120]!r}")
        check(f"{len(routes)} GET endpoints answer without server errors", not bad, "\n      ".join(bad))

        # 2. local-only guard
        code, _ = get(base + "/api/sessions", {"Origin": "https://evil.example"})
        check("refuses requests from other websites", code == 403, f"got {code}")
        code, _ = get(base + "/api/sessions", {"Host": "evil.example"})
        check("refuses other host names", code in (400, 403), f"got {code}")
        csp = urllib.request.urlopen(base + "/").headers.get("Content-Security-Policy", "")
        check("UI is served with a Content-Security-Policy", "script-src" in csp)

        # 3. the UI
        asyncio.run(ui_checks(base))
    finally:
        server.terminate()
        try:
            out = server.communicate(timeout=10)[0]
        except Exception:
            server.kill()
            out = ""
        tracebacks = [l for l in (out or "").splitlines() if "Traceback" in l]
        check("no tracebacks in the server log", not tracebacks, f"{len(tracebacks)} tracebacks; see log")
        if tracebacks:
            (data.parent / "xod-smoke-server.log").write_text(out)
            print(f"      server log: {data.parent / 'xod-smoke-server.log'}")
        shutil.rmtree(data, ignore_errors=True)

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
