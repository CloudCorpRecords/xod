"""
Xod's inner life (services/soul.py, js/soul.js): learning from what you say (name, style, interests,
thanks, corrections), rewards with daily caps, growing up through stages, no penalty for being away,
forgetting, journal reactions that change what it does on its own, and on the page: wandering off
and coming home, postcards, the "Xod's heart" panel, and the growing-up moment.

Throwaway data folder, no model needed:
  .venv/bin/python tests/smoke/run_soul_test.py
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
DATA = tempfile.mkdtemp(prefix="xod-soul-")
os.environ["XOD_DATA_DIR"] = DATA

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
    r = soul.learn_from_message("hi, my name is Sam and I'm into glaciers")
    check("it learns your name", soul.load()["name"] == "Sam")
    b0 = r["bond"]
    r = soul.learn_from_message("thanks, that's perfect!")
    check("thanks raises the bond and joy", r["bond"] > b0 and r["mood"]["joy"] > 0.6, json.dumps(r["mood"]))
    soul.learn_from_message("no, that's wrong, the dates are off")
    check("a correction becomes a lesson", any("dates are off" in l["text"] for l in soul.load()["lessons"]))
    soul.learn_from_message("keep it short please")
    soul.learn_from_message("too long, tl;dr")
    for _ in range(3):
        soul.learn_from_message("more about glaciers in greenland")
    note = soul.prompt_note()
    check("the prompt note carries what it learned", "Sam" in note and "short" in note and "glaciers" in note, note)
    for _ in range(50):
        soul.event("pet")
    day = soul.load()["daily"]
    check("petting has a daily cap (no farming the bond)", day["bond_from_pets"] <= 3.0 + 1e-6, str(day))
    # No penalty for being away: an old last_seen and a return only add.
    s = soul.load()
    before = s["bond"]
    soul._last_activity = time.time() - 5 * 86400
    soul.presence()
    check("being away never lowers the bond (coming back is a happy thing)", soul.load()["bond"] >= before)
    # Growing up
    s = soul.load()
    s.update(xp=39, bond=12.5, stage=0)
    soul.save(s)
    out = soul.event("built", "Neon Dodge")
    check("enough experience and bond: it grows to Sprout", out.get("evolved", {}).get("name") == "Sprout" and out["stage"] == 1, json.dumps(out.get("evolved")))
    # Journal reactions are its reward
    s = soul.load()
    s["journal"] = [{"id": "jx", "ts": time.time(), "activity": "wonder", "kind": "found", "text": "x", "seen": False}]
    soul.save(s)
    w0 = soul.load()["activity_weights"]["wonder"]
    soul.react("jx", "liked")
    check("'Thanks' on a find makes it wonder more", soul.load()["activity_weights"]["wonder"] > w0)
    soul.react("jx", "dismissed")
    check("'Not my thing' makes it wonder less", soul.load()["activity_weights"]["wonder"] < w0 * 1.35)
    check("forgetting an interest works", "glaciers" not in soul.forget("interest", "glaciers")["interests"])
    check("starting over resets it", soul.forget("everything")["stage"] == 0 and not soul.load()["name"])


async def main():
    unit()
    from app.services import soul
    now = time.time()
    s = soul._default()
    s.update(stage=2, xp=210, bond=41, name="Sam", topics={"glaciers": 6, "3d games": 4},
             journal=[{"id": "j1", "ts": now - 600, "activity": "wonder", "kind": "found", "topic": "glaciers", "seen": False,
                       "text": "I went looking into glaciers: some slide faster in summer.", "sources": [{"title": "Glaciers", "url": "https://example.com"}]}])
    soul.save(s)
    port = free_port()
    env = {**os.environ, "XOD_DATA_DIR": DATA, "XOD_NO_UPDATE_CHECK": "1"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/api/soul", timeout=2)
                break
            except Exception:
                time.sleep(0.25)
        st = json.loads(urllib.request.urlopen(base + "/api/soul").read())
        check("the API shows its state", st["stage_name"] == "Companion" and st["unseen"] == 1)
        check("the page can't fake praise (only pet/spoke/liked/dismissed)", post(base, "/api/soul/event", {"kind": "praise"})[0] == 400)
        check("petting from the page works", post(base, "/api/soul/event", {"kind": "pet"})[0] == 200)

        async with Studio(width=1440, height=860, scale=1, port=free_port(), cursor=False) as b:
            await b.goto(base + "/", wait=4)
            await b.js("document.querySelectorAll('.fixed.inset-0').forEach(e => { if (/Pick Xod|Welcome/.test(e.textContent)) e.remove(); }); true")
            check("its stage shows on the page", await b.js("document.documentElement.dataset.soulStage") == "2")
            got = False
            for _ in range(30):
                await asyncio.sleep(0.5)
                stt = await b.js("XodSoul.state()")
                if stt["postcard"] and await b.js("!!document.querySelector('.xw-bubble.is-postcard [data-pc=like]')"):
                    got = True
                    break
            check("it brings a postcard from its time alone", got)
            check("…leaving home to do it (the home moon dims)", await b.js("!!document.querySelector('.xod-mascot--hero.is-wandering')"))
            await b.js("document.querySelector('.xw-bubble [data-pc=like]').click(); true")
            await asyncio.sleep(1)
            j = json.loads(urllib.request.urlopen(base + "/api/soul").read())["journal"][0]
            check("'Thanks!' is recorded as its reward", j.get("reaction") == "liked" and j.get("seen"), json.dumps(j))
            await asyncio.sleep(2.5)
            await b.js("XodSoul.home(); true")
            await asyncio.sleep(2.5)
            await b.js("XodSoul.wander(); true")
            moved = False
            x0 = (await b.js("XodSoul.state()"))["x"]
            for _ in range(12):
                await asyncio.sleep(0.5)
                stt = await b.js("XodSoul.state()")
                if stt["out"] and abs(stt["x"] - x0) > 20:
                    moved = True
                    break
            check("it wanders around the window", moved)
            await b.js("window.dispatchEvent(new KeyboardEvent('keydown', {key: 'a'})); true")
            await asyncio.sleep(2.5)
            check("…and goes home when you're back", not (await b.js("XodSoul.state()"))["out"])
            await b.js("XodSoul.openHeart(); true")
            await asyncio.sleep(1.2)
            text = await b.js("document.querySelector('.xod-heart').innerText")
            check("Xod's heart shows stage, bond, mood and what it learned", all(w in text for w in ("Companion", "BOND", "Joy", "Your name: Sam", "glaciers")), text[:300])
            await b.js("document.querySelector('.xod-heart [data-forget=interest][data-v=glaciers]').click(); true")
            await asyncio.sleep(1)
            check("forgetting from the panel works", "glaciers" not in json.loads(urllib.request.urlopen(base + "/api/soul").read())["interests"])
            await b.js("XodSoul.closeHeart(); XodSoul.onEvent({state: {stage: 3, stage_name: 'Kindred', bond: 56, feeling: 'delighted', mood: {}}, evolved: {name: 'Kindred', blurb: 'x'}}); true")
            await asyncio.sleep(0.8)
            check("growing up gets its moment", await b.js("!!document.querySelector('.xod-evolve.is-on') && document.documentElement.dataset.soulStage === '3'"))
    finally:
        server.terminate()
    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
