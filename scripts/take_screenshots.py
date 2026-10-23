#!/usr/bin/env python3
"""
README screenshots, taken from a running Xod with headless Chrome.

Use a demo data folder (never your own: screenshots end up public):
  XOD_DATA_DIR=/tmp/xod-demo .venv/bin/python -m uvicorn app.main:app --port 8125
  .venv/bin/python scripts/take_screenshots.py --url http://127.0.0.1:8125 --chat <session id>
  (--only abilities,models takes just those)

Writes docs/screenshots/*.png at 2x for a 1440x900 window.
"""
import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9461


class Page:
    def __init__(self, ws, only=()):
        self.ws, self.n, self.only = ws, 0, only

    async def cmd(self, method, **params):
        self.n += 1
        my = self.n
        await self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr):
        r = await self.cmd("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return (r.get("result") or {}).get("value")

    async def shot(self, name, wait=1.2):
        if self.only and name not in self.only:
            return
        await asyncio.sleep(wait)
        data = (await self.cmd("Page.captureScreenshot", format="png"))["data"]
        (OUT / f"{name}.png").write_bytes(base64.b64decode(data))
        print("saved", name)


async def run(url, chat, only=""):
    OUT.mkdir(parents=True, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="xod-shots-")
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
                               "--use-mock-keychain", "--password-store=basic", "--no-first-run", "--hide-scrollbars",
                               "--force-dark-mode", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                break
            except Exception:
                time.sleep(0.2)
        target = next(t for t in tabs if t["type"] == "page")
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
            p = Page(ws, [n for n in only.split(",") if n])
            await p.cmd("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=2, mobile=False)
            await p.cmd("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
            await p.cmd("Page.enable")

            async def fresh():
                await p.cmd("Page.navigate", url=url)
                await asyncio.sleep(3)

            await fresh()
            await p.shot("home")

            if chat:
                await p.js(f"selectSession('{chat}')")
                await asyncio.sleep(2)
                # Start at the answer, so its source links fit in the shot too.
                await p.js("(() => { const box = document.querySelector('#chatMessages'); const ans = [...box.querySelectorAll('.prose-custom')].pop();"
                           " box.scrollTop = ans ? ans.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 150 : 0; })()")
                await p.shot("answer")
                url_path = await p.js("""(async () => {
                    const prose = [...document.querySelectorAll('.prose-custom')].pop();
                    const q = [...document.querySelectorAll('.user-bubble')].pop()?.innerText || '';
                    const r = await (await fetch('/api/reports', {method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({title: q.slice(0, 90), question: q, html: safeHtml(prose.innerHTML), pdf: false})})).json();
                    return r.html_url; })()""")
                if url_path:
                    await p.cmd("Page.navigate", url=url.rstrip("/") + url_path)
                    await p.shot("report", wait=2)

            await fresh(); await p.js("openActivityPanel()"); await p.shot("activity")
            await fresh(); await p.js("openRoutinesPanel()"); await p.shot("routines")
            await fresh(); await p.js("toggleToolsMenu()"); await p.shot("tools")
            await fresh(); await p.js("openSetupWizard()"); await p.shot("setup", wait=3)
            await fresh(); await p.js("openMcpPanel()")
            await asyncio.sleep(1.2)
            # Screenshots are public: show a generic home folder instead of this Mac's username.
            await p.js("document.querySelectorAll('#mcpBody pre, #mcpBody code').forEach(e => e.textContent = e.textContent.replace(/\\/Users\\/[^/\\s\"]+/g, '/Users/you'))")
            await p.shot("connected-apps", wait=0.3)
            await fresh(); await p.js("openPacksPanel()"); await p.shot("add-ons")
            await fresh(); await p.js("openAbilities()"); await p.shot("abilities", wait=3)
            await fresh(); await p.js("toggleModelSwitcher()"); await p.shot("models", wait=3)
    finally:
        chrome.terminate()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8125")
    ap.add_argument("--chat", default="")
    ap.add_argument("--only", default="", help="comma-separated shot names, e.g. abilities,models")
    a = ap.parse_args()
    asyncio.run(run(a.url, a.chat, a.only))
