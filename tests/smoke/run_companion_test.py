"""
The companion in a real chat: plays a scripted agent run through the chat code (scripts/media/chat_demo.js)
in headless Chrome, once per look, and checks that the companion leaves its perch, conjures tools, waits
by an approval, writes the answer, signs it and flies home, with no errors along the way.

Uses a throwaway data folder, never yours:
  .venv/bin/python tests/smoke/run_companion_test.py
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "media"))
from studio import Studio  # noqa: E402

LOOKS = ["moon", "cat", "ghost", "robot", "owl", "sprout"]
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SAMPLER = """
window.__samples = [];
window.__sampler = setInterval(() => {
  const s = XodPowers.state();
  window.__samples.push({ out: s.out, target: s.target || '', sig: document.querySelectorAll('.xp-signature').length,
    away: document.querySelectorAll('.xod-mascot--chat.is-away').length, lasso: document.querySelectorAll('.xp-lasso').length,
    wobble: document.querySelectorAll('.xp-wobble').length });
}, 150);
true
"""


async def main():
    port = free_port()
    data = tempfile.mkdtemp(prefix="xod-companion-")
    env = {**os.environ, "XOD_DATA_DIR": data, "XOD_NO_UPDATE_CHECK": "1"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/api/companion", timeout=2)
                break
            except Exception:
                time.sleep(0.25)
        async with Studio(width=1280, height=820, scale=1, port=free_port(), cursor=False) as s:
            await s.cmd("Runtime.enable")
            errors = []

            async def reader():
                async for raw in s.ws:
                    msg = json.loads(raw)
                    if "id" in msg and msg["id"] in s._pending:
                        s._pending.pop(msg["id"]).set_result(msg)
                    elif msg.get("method") == "Runtime.exceptionThrown":
                        d = msg["params"]["exceptionDetails"]
                        errors.append("exception: " + (d.get("exception", {}).get("description") or d.get("text", ""))[:300])
                    elif msg.get("method") == "Runtime.consoleAPICalled":
                        text = " ".join(str(a.get("value", a.get("description", ""))) for a in msg["params"]["args"])
                        if msg["params"]["type"] == "error" or "Xod powers" in text or "Xod companion" in text:
                            errors.append("console: " + text[:300])
            s.reader.cancel()
            s.reader = asyncio.create_task(reader())

            await s.goto(base + "/", wait=4)
            await s.js("document.querySelectorAll('.fixed.inset-0').forEach(e => { if (e.textContent.includes('Pick Xod')) e.remove(); }); true")
            check("powers loaded", bool(await s.js("!!window.XodPowers && !!window.XodMascot.puppet")))

            # Home screen: a question gets a "?", and opening a panel casts a spell.
            await s.js("const i = document.getElementById('chatInput'); i.focus(); i.value = 'what is this?'; i.dispatchEvent(new Event('input')); true")
            await asyncio.sleep(0.3)
            check("asking a question shows a ?", bool(await s.js("!!document.querySelector('.xm-heart.is-ask')")))
            await s.js("document.getElementById('chatInput').value = ''; openPacksPanel(); true")
            await asyncio.sleep(0.6)
            check("opening a panel casts a beam", bool(await s.js("!!document.querySelector('.xp-beam')")))
            await s.js("document.querySelector('[data-close-packs]').click(); true")
            await asyncio.sleep(0.5)

            await s.js((ROOT / "scripts" / "media" / "chat_demo.js").read_text())
            for look in LOOKS:
                before = len(errors)
                await s.js(f"XodMascot.setCharacter('{look}', {{ save: false, greet: false }}); true")
                await asyncio.sleep(0.5)
                await s.js(SAMPLER)
                await s.js("XodChatDemo.run({ approval: true, speed: 1.6 }).then(() => true)")
                await asyncio.sleep(3.5)
                samples = await s.js("window.__samples")
                targets = [x["target"] for x in samples if x["out"]]
                check(f"{look}: leaves its perch while working", any(x["out"] and x["away"] for x in samples))
                check(f"{look}: visits the tool", any("tool-pill" in t for t in targets))
                check(f"{look}: peeks at the browser snapshot", any("rounded-2xl" in t for t in targets))
                check(f"{look}: waits by the approval, lassoed", any("bg-brand-500" in t for t in targets) and any(x["lasso"] for x in samples))
                check(f"{look}: writes the answer", "writing" in targets)
                check(f"{look}: signs the answer", any("xp-signature" in t for t in targets))
                last = samples[-1]
                check(f"{look}: goes back to the approval after signing", last["out"] and "bg-brand-500" in last["target"], json.dumps(last))
                await s.js("document.querySelector('[data-mcp=\"decline\"]:not(:disabled)')?.click(); true")
                await asyncio.sleep(6)
                last = (await s.js("window.__samples"))[-1]
                check(f"{look}: flies home once you've answered", not last["out"] and not last["away"], json.dumps(last))

                # an error: it wobbles the reply and still comes home
                await s.js("window.__samples = []; true")
                await s.js("XodChatDemo.run({ error: true, speed: 2 }).then(() => true)")
                await asyncio.sleep(4)
                samples = await s.js("clearInterval(window.__sampler); window.__samples")
                check(f"{look}: an error wobbles the reply", any(x["wobble"] for x in samples))
                check(f"{look}: home after an error", not samples[-1]["out"] and not samples[-1]["away"], json.dumps(samples[-1]))
                check(f"{look}: no errors", len(errors) == before, "; ".join(errors[before:before + 3]))

            # Every reply's companion in the chat matches the look and has a face.
            faces = await s.js("""[...document.querySelectorAll('#chatMessages .xod-mascot--chat')].map(m => {
                const svg = m.querySelector('svg'); return [svg && svg.dataset.character, !!(m.querySelector('.xm-mouth') || {}).getAttribute?.('d')]; })""")
            check("chat companions all have the current look", all(f[0] == "sprout" for f in faces), str(faces[:4]))
            check("chat companions all have a mouth", all(f[1] for f in faces))
            await s.js("XodMascot.setCharacter('cat', { save: false, greet: false }); true")
            await asyncio.sleep(0.8)
            faces = await s.js("""[...document.querySelectorAll('#chatMessages .xod-mascot--chat')].map(m => [m.querySelector('svg').dataset.character, !!m.querySelector('.xm-mouth').getAttribute('d')])""")
            check("switching looks mid-chat redraws every reply's companion", all(f == ["cat", True] for f in faces), str(faces[:4]))

            # Smoothness while it writes.
            await s.js("XodChatDemo.run({ speed: 1 }); true")
            await asyncio.sleep(9)
            fps = await s.js("new Promise(r => { let n = 0; const t0 = performance.now(); const f = () => { n++; if (performance.now() - t0 < 2000) requestAnimationFrame(f); else r(n / 2); }; requestAnimationFrame(f); })")
            check(f"animation stays smooth while writing ({fps:.0f} fps)", fps >= 30)
            await asyncio.sleep(5)
            check("no errors at all", not errors, "; ".join(errors[:3]))
    finally:
        server.terminate()

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
