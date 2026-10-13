"""
More life in the companion (js/mascot.js) and keeping its own life local (services/soul.py):
juggling, reading a book, blowing a bubble you can pop, dreams while it sleeps, how it feels changing
how it acts, and: its thoughts only use a model on this Mac, web reading is opt-in (and switched off
for setups from before), and with no local model it still writes a thought without any model.

Throwaway data folder, no model needed:
  .venv/bin/python tests/smoke/run_life_test.py [screenshot-folder]
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "media"))
DATA = tempfile.mkdtemp(prefix="xod-life-")
os.environ["XOD_DATA_DIR"] = DATA
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None

from studio import Studio  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def unit():
    from app.config import settings
    from app.services import soul
    # A setup from before, where web reading had been switched off: it's back on, now through Xod's own browser.
    old = soul._default()
    old.pop("web_v")
    old["settings"]["explore_web"] = False
    soul.PATH.write_text(json.dumps(old))
    check("setups from before: reading up on the web is on again (in its own browser)", soul.load()["settings"]["explore_web"] is True)
    check("new ones start with it on", soul._default()["settings"]["explore_web"] is True)
    s = soul.load()
    s["settings"]["explore_web"] = False
    soul.save(s)
    check("…and turning it off sticks", soul.load()["settings"]["explore_web"] is False)
    saved = (settings.llm_provider, settings.llm_base_url)
    try:
        settings.llm_provider, settings.llm_base_url = "openai", "https://api.openai.com/v1"
        ep = soul.local_endpoint()      # None, or LM Studio on this Mac if it's running with a model loaded
        check("a cloud model is never used for its own thoughts",
              ep is None or (ep["provider"] == "lm_studio" and ep["base_url"].startswith("http://127.0.0.1")), str(ep))
        s = soul.load()
        s["moments"] = [{"ts": time.time(), "kind": "built", "text": "We built Neon Dodge together."}]
        soul.save(s)
        entry = await soul.muse("daydream")
        check("…it still has a thought, without any model", bool(entry and entry.get("text")), json.dumps(entry))
        settings.llm_provider, settings.llm_base_url = "lm_studio", "http://127.0.0.1:1234/v1"
        check("the model on this Mac is used", soul.local_endpoint() is not None)
    finally:
        settings.llm_provider, settings.llm_base_url = saved


async def main():
    await unit()
    from app.services import soul
    s = soul._default()
    s.update(stage=2, xp=210, bond=41, name="Sam", topics={"glaciers": 6, "jazz": 4})
    soul.save(s)
    port = free_port()
    env = {**os.environ, "XOD_DATA_DIR": DATA, "XOD_NO_UPDATE_CHECK": "1"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(240):
            try:
                urllib.request.urlopen(base + "/api/soul", timeout=2)
                break
            except Exception:
                time.sleep(0.25)
        async with Studio(width=1280, height=820, scale=2 if SHOTS else 1, port=free_port(), cursor=False) as b:
            async def shot(name, clip=True):
                if not SHOTS:
                    return
                SHOTS.mkdir(parents=True, exist_ok=True)
                box = await b.js("(() => { const r = document.querySelector('.xod-mascot--hero').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()")
                x, y, w, h = box
                await b.shot(str(SHOTS / f"{name}.png"), clip={"x": max(0, x - 90), "y": max(0, y - 90), "width": w + 180, "height": h + 140} if clip else None)

            async def wait(expr, secs=6.0):
                end = time.time() + secs
                while time.time() < end:
                    if await b.js(expr):
                        return True
                    await asyncio.sleep(0.12)
                return False

            await b.goto(base + "/", wait=4)
            await b.js("document.querySelectorAll('.fixed.inset-0').forEach(e => { if (/Pick Xod|Welcome/.test(e.textContent)) e.remove(); }); localStorage.setItem('xod.greetDay', JSON.stringify(new Date().toISOString().slice(0,10))); true")
            await asyncio.sleep(1.5)
            hero = ".xod-mascot--hero"
            check("how it feels reaches the animation", await wait("XodMascot.temperament() === XodSoul.state().soul.feeling", 4),
                  await b.js("XodMascot.temperament() + ' / ' + (XodSoul.state().soul||{}).feeling"))

            await b.js("XodMascot.fidget('juggle'); true")
            check("it juggles three little stars", await wait(f"document.querySelector('{hero} .xm-juggle').getAttribute('opacity') === '1'", 2))
            await asyncio.sleep(0.6)
            await shot("1-juggle")
            await asyncio.sleep(3)

            await b.js("XodMascot.fidget('read'); true")
            check("it reads a little book", await wait(f"document.querySelector('{hero}').dataset.prop === 'book'", 2))
            await asyncio.sleep(1)
            await shot("2-read")
            await asyncio.sleep(3.5)

            await b.js("XodMascot.fidget('bubble'); true")
            check("it blows a bubble", await wait("!!document.querySelector('.xm-bubble')", 3))
            await asyncio.sleep(0.9)
            await shot("3-bubble", clip=False)
            await b.js("document.querySelector('.xm-bubble').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true})); true")
            check("…and popping it makes it happy", await wait(f"document.querySelector('{hero}').dataset.mood === 'happy'", 2))
            await asyncio.sleep(2)

            await b.js("XodMascot.demo('sleepy', 12000); true")
            check("asleep, it dreams (a thought cloud)", await wait("!!document.querySelector('.xm-dream span.is-in')", 4))
            await asyncio.sleep(1.2)
            await shot("4-dream", clip=False)
            await b.js("XodMascot.demo('idle', 100); document.dispatchEvent(new KeyboardEvent('keydown', {key: 'a'})); true")
            check("…and the dream goes when it wakes", await wait("!document.querySelector('.xm-dream')", 4))

            await b.js("XodMascot.setTemperament('delighted'); true")
            check("delighted: a bigger smile at rest", await wait(f"(() => {{ const d = document.querySelector('{hero} .xm-mouth').getAttribute('d'); return parseFloat(d.split(' ')[0].slice(1)) < 29.4; }})()", 3),
                  await b.js(f"document.querySelector('{hero} .xm-mouth').getAttribute('d')"))
            errs = await b.js("(window.__xodErrors || []).length")
            check("no errors on the page", not errs, str(errs))
    finally:
        server.terminate()
    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
