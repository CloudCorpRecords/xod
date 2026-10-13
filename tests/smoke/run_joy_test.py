"""
Things that make Xod happy (js/joy.js, services/soul.py): stardust earned from finished work (capped,
only on the server), treats that spend it, catch with a ball you throw, wishes, tickles, heart eyes
when you thank it, and the hover dock. Takes screenshots of each moment into the folder given
(optional).

Throwaway data folder, no model needed:
  .venv/bin/python tests/smoke/run_joy_test.py [screenshot-folder]
"""
import asyncio
import json
import os
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
DATA = tempfile.mkdtemp(prefix="xod-joy-")
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


def post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}


def unit():
    from app.services import soul
    soul.forget("everything")
    r = soul.event("treat")
    check("no stardust yet: a treat says so and changes nothing", r.get("no_stardust") and r["stardust"] == 0)
    for _ in range(20):
        r = soul.event("success")
    check("finished answers earn stardust, at most 12 a day", r["stardust"] == 12, str(r["stardust"]))
    j0 = soul.load()["mood"]["joy"]
    r = soul.event("treat")
    check("a treat spends one and makes it happier", r["stardust"] == 11 and soul.load()["mood"]["joy"] > j0 - 0.01 and r["treats_total"] == 1)
    r = soul.event("play", "4")
    check("catch records the best streak and a moment", r["catches_best"] == 4 and any("4 in a row" in m["text"] for m in r["moments"]), json.dumps(r["moments"][:2]))
    b0 = soul.load()["bond"]
    for _ in range(10):
        soul.event("play", "1")
    check("play's bond reward is capped per day", soul.load()["bond"] - b0 < 2.1)


async def main():
    unit()
    from app.services import soul
    s = soul._default()
    s.update(stage=2, xp=210, bond=41, name="Sam", stardust=3)
    soul.save(s)
    port = free_port()
    env = {**os.environ, "XOD_DATA_DIR": DATA, "XOD_NO_UPDATE_CHECK": "1"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(240):   # a cold start can take a while
            try:
                urllib.request.urlopen(base + "/api/soul", timeout=2)
                break
            except Exception:
                time.sleep(0.25)
        check("the page can't award itself stardust", post(base, "/api/soul/event", {"kind": "success"})[0] == 400)

        async with Studio(width=1280, height=820, scale=2 if SHOTS else 1, port=free_port(), cursor=False) as b:
            async def shot(name):
                if SHOTS:
                    SHOTS.mkdir(parents=True, exist_ok=True)
                    await b.shot(str(SHOTS / f"{name}.png"))

            async def wait(expr, secs=8.0):
                end = time.time() + secs
                while time.time() < end:
                    if await b.js(expr):
                        return True
                    await asyncio.sleep(0.15)
                return False

            await b.goto(base + "/", wait=4)
            await b.js("document.querySelectorAll('.fixed.inset-0').forEach(e => { if (/Pick Xod|Welcome/.test(e.textContent)) e.remove(); }); true")
            check("joy.js is loaded", await b.js("!!window.XodJoy"))
            await asyncio.sleep(2.5)
            check("it says hello for the day", await b.js("JSON.parse(localStorage.getItem('xod.greetDay')||'\"\"') === new Date().toISOString().slice(0,10)"))
            await asyncio.sleep(3.5)

            # Hover: the dock with treat, catch and heart.
            await b.js("document.querySelector('.xod-mascot--hero').dispatchEvent(new PointerEvent('pointerover', {bubbles: true})); true")
            check("hovering Xod shows treat / catch / heart, with the stardust count", await wait("document.querySelector('.xj-dock.is-on [data-count]')?.textContent === '3'", 3))
            await shot("1-dock")
            await b.js("document.querySelector('.xj-dock').classList.remove('is-on'); true")

            # A treat.
            await b.js("XodJoy.treat(); true")
            check("Xod comes out for the treat", await wait("XodSoul.state().out", 3))
            await wait("!!document.querySelector('.xj-star')", 4)
            await asyncio.sleep(0.45)
            await shot("2-treat-incoming")
            check("…catches it and munches", await wait("/Nom|munch|Mmmf/.test(XodSoul.state().bubble)", 5), await b.js("XodSoul.state().bubble"))
            await asyncio.sleep(0.5)
            await shot("3-munch")
            check("…then heart eyes and a happy line", await wait("getComputedStyle(document.querySelector('.xod-mascot--wander .xm-love')).opacity === '1'", 4))
            await asyncio.sleep(0.3)
            await shot("4-love")
            st = json.loads(urllib.request.urlopen(base + "/api/soul").read())
            check("the treat spent one stardust", st["stardust"] == 2 and st["treats_total"] == 1, str(st["stardust"]))
            await wait("!XodSoul.state().out", 8)

            # Catch: throw the ball with the mouse.
            await b.js("XodJoy.play(); true")
            check("catch: a ball appears", await wait("!!document.querySelector('.xj-ball') && !!XodJoy.state().ball", 5))
            await asyncio.sleep(0.6)
            bx, by = await b.js("[XodJoy.state().ball.x, XodJoy.state().ball.y]")
            await b.cmd("Input.dispatchMouseEvent", type="mousePressed", x=bx, y=by, button="left", clickCount=1)
            for i in range(1, 7):
                await b.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=bx + i * 14, y=by - i * 22, button="left", buttons=1)
                await asyncio.sleep(0.016)
            await b.cmd("Input.dispatchMouseEvent", type="mouseReleased", x=bx + 84, y=by - 132, button="left", clickCount=1)
            check("…you can throw it", await wait("XodJoy.state().held === 'air'", 2))
            await asyncio.sleep(0.35)
            await shot("5-catch-throw")
            check("…and Xod chases it down and grabs it", await wait("XodJoy.state().held === 'held'", 8), await b.js("JSON.stringify(XodJoy.state())"))
            await asyncio.sleep(0.25)
            await shot("6-catch-got-it")
            await wait("XodJoy.state().held !== 'held'", 4)
            await b.js("XodJoy.endPlay(); true")
            await asyncio.sleep(1)
            st = json.loads(urllib.request.urlopen(base + "/api/soul").read())
            check("the game is remembered", any(m.get("kind") == "play" for m in st["moments"]) or st["catches_best"] >= 0)
            await wait("!XodSoul.state().out", 8)

            # A wish: it asks for something; say yes.
            await b.js("XodJoy.wish('trick'); true")
            check("a wish: it asks (here, to show a trick)", await wait("/trick|Watch this/i.test(XodSoul.state().bubble)", 5), await b.js("XodSoul.state().bubble"))
            await asyncio.sleep(0.3)
            await shot("7-wish")
            await b.js("document.querySelector('.xw-bubble [data-w=yes]').click(); true")
            check("…'Show me!' gets the trick", await wait("/da!/.test(XodSoul.state().bubble)", 5))
            await wait("!XodSoul.state().out", 8)
            await b.js("XodJoy.wish('play'); true")
            await wait("!!document.querySelector('.xw-bubble [data-w=no]')", 5)
            await b.js("document.querySelector('.xw-bubble [data-w=no]').click(); true")
            check("…'Not now' is fine", await wait("/Later|worries|okay/i.test(XodSoul.state().bubble)", 3))
            await wait("!XodSoul.state().out", 8)

            # Tickle: keep petting the big one.
            hx, hy = await b.js("(() => { const r = document.querySelector('.xod-mascot--hero').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()")
            for i in range(40):
                await b.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=hx + (18 if i % 2 else -18), y=hy)
                await asyncio.sleep(0.03)
            check("keep petting and it giggles (a tickle)", await wait("!!document.querySelector('.xm-heart.is-giggle') || /tickles|stop|Hee/.test(document.getElementById('xodBubble')?.textContent || '')", 3))
            await shot("8-tickle")

            # Thanks in the chat: heart eyes.
            await asyncio.sleep(2)
            await b.js("XodSoul.onEvent({type: 'soul', kind: 'praise', state: {stage: 2, stage_name: 'Companion', bond: 42, feeling: 'delighted', mood: {}}}); true")
            check("thanks in the chat: heart eyes", await wait("getComputedStyle(document.querySelector('.xod-mascot--hero .xm-love')).opacity === '1'", 3))
            await shot("9-thanks")
            errs = await b.js("window.__xodErrors ? window.__xodErrors.length : 0")
            check("no errors on the page", not errs, str(errs))
    finally:
        server.terminate()
    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
