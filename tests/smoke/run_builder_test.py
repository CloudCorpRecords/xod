"""
The builder and the preview pane: routing ("make me a game" vs. "what's the weather"), exact edits,
the preview sandbox (secret path, no API access, no path tricks), saved localStorage, the headless
run check (errors, missing files, blank pages), and the pane itself in Chrome (Preview / Files /
Console, the console bridge, the error badge, collapsing and reopening).

Uses a throwaway data folder and fixture apps, never yours, and no model:
  .venv/bin/python tests/smoke/run_builder_test.py
Add --model to also build a small game with the model that's loaded (slow on a laptop).
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
DATA = tempfile.mkdtemp(prefix="xod-builder-")
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


def get(base, path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def fixture(name, files):
    d = Path(DATA) / "apps" / name
    d.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body)
    (d / ".xod-app.json").write_text(json.dumps({"title": name.title(), "entry": "index.html"}))
    return d


GOOD = {"index.html": "<!doctype html><html><head><title>t</title></head><body><h1 id=s>0</h1>"
                      "<script type=module src=main.js></script></body></html>",
        "main.js": "import * as THREE from 'three';\nconsole.log('hello from the game', THREE.REVISION);\n"
                   "const n = Number(localStorage.getItem('best') || 0) + 1; localStorage.setItem('best', n);\n"
                   "document.getElementById('s').textContent = 'runs ' + n;\n"}
BAD = {"index.html": "<html><head></head><body><canvas></canvas><script src=game.js></script><script src=oops.js></script></body></html>",
       "oops.js": "const x = ;\n"}
BLANK = {"index.html": "<html><body><script>let a = 1</script></body></html>"}
EVIL = {"index.html": "<html><body><p>hi</p><script>fetch('/api/preview/apps').then(r => console.log('API status ' + r.status))"
                      ".catch(e => console.log('API blocked'))</script></body></html>"}


def unit():
    from app.agent import builder as b
    from app.services import previews
    r = lambda q, msgs=None, ws=None: (b.route(q, msgs or [], ws) or {}).get("mode")
    check("'make me a 3d game' goes to the builder", r("make me a 3d game in this folder") == "new")
    check("'build a pomodoro timer app' goes to the builder", r("build a pomodoro timer app") == "new")
    check("questions don't", r("what's the weather tomorrow?") is None and r("how do games make money?") is None)
    check("images and essays don't", r("make me an image of a penguin") is None and r("write an essay about chess") is None)
    app_dir = fixture("prior", {"index.html": "<p>x</p>"})
    hist = [{"role": "user", "content": "make a game"},
            {"role": "assistant", "content": "**I built it.**\n\n" + b.MARK + json.dumps({"path": str(app_dir), "title": "Prior"}) + " -->"},
            {"role": "user", "content": "x"}]
    check("a follow-up change goes to the same app", r("make the enemies faster", hist) == "change")
    check("'fix the errors in the preview' too", r("Fix the errors in the preview of Prior:\n- x", hist) == "change")
    check("'launch it' / 'run it' / 'play it' launch it", all(r(q, hist) == "launch" for q in ("launch it", "run it", "play it", "open it in its own window", "can you launch the game")))
    check("'test it' runs the error check", r("test it", hist) == "run" and r("check it for errors", hist) == "run")
    check("'make the player orange' changes the app (not a new one)", r("make the player sphere orange and show the high score", hist) == "change")
    check("'make me a snake game' in the same chat starts a new one", r("make me a snake game", hist) == "new")
    check("'build another game' starts a new one", r("build another game, a racing one", hist) == "new")
    check("a question in the same chat isn't a change", r("how does the scoring work?", hist) is None)
    check("a change in a project folder with an app", r("add a high score", None, str(app_dir.parent)) == "change")

    src = "function a() {\n    let speed = 1;\n    if (x) {\n        go();\n    }\n}\n"
    edits = b.extract_edits("FILE: main.js\n<<<<<<< SEARCH\n  let speed = 1;\n=======\nlet speed = 3;\n>>>>>>> REPLACE\n")
    out = b.apply_edit(src, *edits["main.js"][0])
    check("search/replace edits apply, keeping the file's indentation", "    let speed = 3;\n" in out and out.count("speed") == 1, out)
    check("an edit that doesn't match is refused (falls back to a rewrite)", b.apply_edit(src, "nope()", "x") is None)
    check("unsafe file names are refused", all(b.safe_rel(p) is None for p in ("../x.js", "/etc/passwd.js", ".env.js", "a/../../b.js", "x.sh")))
    check("code is pulled out of a reply", b.extract_code("Here:\n```js\nconst a = 1;\n```\nDone") == "const a = 1;\n")

    rid = previews.register(app_dir)
    check("preview paths can't escape the app folder", previews.resolve(rid, "../../xod.db") is None and previews.resolve(rid, ".xod-app.json") is None)
    check("unregistered folders can't be previewed", previews.resolve("deadbeef0000", "index.html") is None)
    html = previews.inject("<html><head></head><body><script src=a.js></script></body></html>")
    check("pages get the import map, the console bridge and crossorigin scripts",
          "importmap" in html and "__xodPreview" in html and "<script crossorigin src=a.js>" in html)


async def main():
    unit()
    port = free_port()
    env = {**os.environ, "XOD_DATA_DIR": DATA, "XOD_NO_UPDATE_CHECK": "1"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/api/preview/apps", timeout=2)
                break
            except Exception:
                time.sleep(0.25)
        apps = {}
        for name, files in (("good", GOOD), ("bad", BAD), ("blank", BLANK), ("evil", EVIL)):
            d = fixture(name, files)
            apps[name] = json.loads(get(base, f"/api/preview/apps?folder={d}")[2])["apps"][0]
        url = apps["good"]["url"]

        # ---- serving and the sandbox
        code, headers, body = get(base, url)
        csp = headers.get("content-security-policy", "")
        check("a preview is served", code == 200 and b"hello" not in body and b"importmap" in body)
        check("…sandboxed, without same-origin", "sandbox allow-scripts" in csp and "allow-same-origin" not in csp)
        bad_token = url.replace(url.split("/")[2], "wrongtoken")
        check("a wrong token gets nothing", get(base, bad_token)[0] == 404)
        check("three.js is served offline", get(base, url.rsplit("/", 2)[0] + "/__lib/three/three.module.min.js")[0] == 200)
        check("the API refuses a sandboxed page (Origin: null)", get(base, "/api/preview/apps", {"Origin": "null"})[0] == 403)
        check("a missing file is a 404 the page can see", get(base, url.replace("index.html", "nope.js"))[0] == 404)
        check("the main UI can frame previews (frame-src 'self')", "frame-src 'self'" in dict(get(base, "/")[1]).get("content-security-policy", ""))

        # ---- the headless run check
        r = post(base, "/api/preview/check", {"url": url})
        check("check: a working three.js app passes", r["ran"] and r["ok"] and any("hello from the game" in l for l in r["logs"]), json.dumps(r)[:300])
        r = post(base, "/api/preview/check", {"url": apps["bad"]["url"]})
        check("check: a syntax error is caught with file and line", any("SyntaxError" in e and "oops.js:1" in e for e in r["errors"]), json.dumps(r)[:300])
        check("check: a missing file is caught", any("game.js" in f and "404" in f for f in r["failed"]), json.dumps(r)[:300])
        r = post(base, "/api/preview/check", {"url": apps["blank"]["url"]})
        check("check: a blank page is caught", r["blank"] and not r["ok"], json.dumps(r)[:300])
        r = post(base, "/api/preview/check", {"url": apps["evil"]["url"]})
        check("check: a preview can't call Xod's API", any("API status 403" in l or "API blocked" in l for l in r["logs"]), json.dumps(r)[:300])

        # ---- the pane in Chrome
        async with Studio(width=1440, height=860, scale=1, port=free_port(), cursor=False,
                          chrome_args=("--use-angle=swiftshader", "--enable-unsafe-swiftshader")) as s:
            await s.goto(base + "/", wait=4)
            await s.js("document.querySelectorAll('.fixed.inset-0').forEach(e => { if (/Pick Xod|Welcome/.test(e.textContent)) e.remove(); }); true")
            g = apps["good"]
            await s.js(f"XodPreview.open({json.dumps({k: g[k] for k in ('url', 'root', 'title', 'path')})}); true")
            for _ in range(40):
                await asyncio.sleep(0.25)
                st = await s.js("XodPreview.state()")
                if st["logs"]:
                    break
            check("the pane opens beside the chat", st["open"] and await s.js("!document.getElementById('previewPane').classList.contains('hidden')"))
            check("the app's console.log reaches the Console tab", any("hello from the game" in l["text"] for l in st["logs"]), json.dumps(st["logs"])[:300])
            # localStorage: first run saved 1; after a reload it should say 2.
            await asyncio.sleep(0.6)
            await s.js("XodPreview.reload(); true")
            store = Path(g["path"]) / ".xod-storage.json"
            for _ in range(40):
                await asyncio.sleep(0.25)
                if store.exists() and json.loads(store.read_text()).get("best") == "2":
                    break
            saved = json.loads((Path(g["path"]) / ".xod-storage.json").read_text()) if (Path(g["path"]) / ".xod-storage.json").exists() else {}
            check("localStorage works in the sandbox and is kept between runs", saved.get("best") == "2", json.dumps(saved))
            await s.js("document.querySelector('#previewPane [data-tab=files]').click(); true")
            await asyncio.sleep(1.2)
            names = await s.js("[...document.querySelectorAll('#previewPane [data-file]')].map(b => b.dataset.file)")
            check("Files lists what's really on disk", sorted(names) == ["index.html", "main.js"], json.dumps(names))
            code_txt = await s.js("document.querySelector('#previewPane [data-pv=code]').textContent")
            check("…and shows the code", "hello from the game" in code_txt)
            b_ = apps["bad"]
            await s.js(f"XodPreview.open({json.dumps({k: b_[k] for k in ('url', 'root', 'title', 'path')})}); true")
            await asyncio.sleep(2.5)
            st = await s.js("XodPreview.state()")
            badge = await s.js("document.querySelector('#previewPane [data-pv=badge]').textContent")
            check("errors are counted on the Console tab", st["errors"] >= 1 and badge == str(st["errors"]), f"{st['errors']} / {badge}")
            check("…with an 'Ask Xod to fix' button", await s.js("!document.querySelector('#previewPane .pv-fix').classList.contains('hidden')"))
            await s.js("document.querySelector('#previewPane [data-act=close]').click(); true")
            await asyncio.sleep(0.4)
            check("collapsing hides the pane and stops the app", await s.js("document.getElementById('previewPane').classList.contains('hidden') && !document.querySelector('#previewPane iframe')"))
            check("…and leaves a Preview tab on the edge to reopen it", await s.js("!document.getElementById('previewEdgeBtn').classList.contains('hidden')"))
            await s.js("document.getElementById('previewEdgeBtn').click(); true")
            await asyncio.sleep(0.6)
            check("the edge tab reopens it", await s.js("XodPreview.state().open"))
            await s.js("window.__opened = []; window.open = (u) => { window.__opened.push(u); return {}; }; true")
            await s.js(f"XodPreview.onLaunch({json.dumps({k: g[k] for k in ('url', 'root', 'title', 'path')})}); true")
            opened = await s.js("window.__opened")
            check("'launch it' opens the app on its own (a window in the desktop app, a tab in a browser)", opened == [g["url"]], json.dumps(opened))

        # ---- optional: a real build with the loaded model
        if "--model" in sys.argv:
            body = {"messages": [{"role": "user", "content": "make me a simple clicker game"}], "session_id": None}
            req = urllib.request.Request(base + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            done, content = None, ""
            with urllib.request.urlopen(req, timeout=1800) as resp:
                for line in resp:
                    line = line.decode().strip()
                    if line.startswith("data: "):
                        ev = json.loads(line[6:])
                        if ev.get("type") == "build_done":
                            done = ev
                        elif ev.get("type") == "content":
                            content += ev["content"]
            check("model: a game is built with real files", bool(done and done["files"] and all((Path(done["path"]) / f["path"]).exists() for f in done["files"])))
            check("model: it was run, and the answer names only real files", bool(done and done["check"].get("ran")) and "I built" in content)
    finally:
        server.terminate()

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
