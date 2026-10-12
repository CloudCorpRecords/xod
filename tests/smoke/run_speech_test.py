"""
Spoken replies: the speech API, that text can never run as a command, and, in headless Chrome, what
gets said while a reply streams in (scripts/media/chat_demo.js through the real chat code).

Uses a throwaway data folder, never yours. Needs macOS (the `say` voice) and Chrome:
  .venv/bin/python tests/smoke/run_speech_test.py
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
sys.path.insert(0, str(ROOT / "scripts" / "media"))
from studio import Studio  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


SPY = """
window.__said = [];
window.__mouth = 0;
const _fetch = window.fetch;
window.fetch = function (url, opts) {
  if (String(url).includes('/api/speech/say')) window.__said.push(JSON.parse(opts.body).text);
  return _fetch.apply(this, arguments);
};
const _voice = XodMascot.voice;
XodMascot.voice = (l) => { if (l > 0.01) window.__mouth++; return _voice(l); };
true
"""


async def main():
    port = free_port()
    data = tempfile.mkdtemp(prefix="xod-speech-")
    env = {**os.environ, "XOD_DATA_DIR": data, "XOD_NO_UPDATE_CHECK": "1"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    pwned = Path(tempfile.gettempdir()) / f"xod-speech-pwned-{port}"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/api/speech", timeout=2)
                break
            except Exception:
                time.sleep(0.25)

        # ---- API
        info = json.load(urllib.request.urlopen(base + "/api/speech"))
        check("voices are listed", len(info["voices"]) > 0 and bool(info["voice"]), str(info.get("voice")))
        check("no novelty voices (Bubbles, Bad News…)", not any(v["name"] in ("Bubbles", "Bad News", "Zarvox") for v in info["voices"]))
        check("default is 'answer aloud when I talk'", info["settings"]["mode"] == "voice")
        code, ctype, body = post(base, "/api/speech/say", {"text": "Here's **the answer**, with [a link](https://example.com)."})
        check("a sentence becomes WAV audio", code == 200 and "audio/wav" in ctype and body[:4] == b"RIFF", f"{code} {ctype}")
        code, _, _ = post(base, "/api/speech/say", {"text": "```\nprint('only code')\n```"})
        check("nothing to say gives an empty answer", code == 204, str(code))
        check("bad mode is refused", post(base, "/api/speech/settings", {"mode": "loud"})[0] == 400)
        check("a voice that isn't installed is refused", post(base, "/api/speech/settings", {"voice": "Nobody (Premium)"})[0] == 400)
        for path, body in (("/api/speech/say", {"text": f"hi $(touch {pwned}) `touch {pwned}` ; touch {pwned}"}),
                           ("/api/voice/tts", {"text": f"hi \"$(touch {pwned})\"", "voice": f"Samantha\"; touch {pwned}; echo \""})):
            post(base, path, body)
        await asyncio.sleep(2)
        check("text can't run as a command", not pwned.exists())

        # ---- in the page
        async with Studio(width=1280, height=820, scale=1, port=free_port(), cursor=False) as s:
            await s.cmd("Runtime.enable")
            errors = []

            async def reader():
                async for raw in s.ws:
                    msg = json.loads(raw)
                    if "id" in msg and msg["id"] in s._pending:
                        s._pending.pop(msg["id"]).set_result(msg)
                    elif msg.get("method") == "Runtime.exceptionThrown":
                        errors.append(str(msg["params"]["exceptionDetails"].get("exception", {}).get("description", ""))[:300])
                    elif msg.get("method") == "Runtime.consoleAPICalled" and msg["params"]["type"] == "error":
                        errors.append(" ".join(str(a.get("value", "")) for a in msg["params"]["args"])[:300])
            s.reader.cancel()
            s.reader = asyncio.create_task(reader())

            await s.goto(base + "/", wait=4)
            await s.js("document.querySelectorAll('.fixed.inset-0').forEach(e => { if (e.textContent.includes('Pick Xod')) e.remove(); }); true")
            check("speaker button is in the composer", bool(await s.js("!!document.getElementById('chatSpeakBtn')")))
            await s.js("document.getElementById('chatSpeakBtn').click(); true")
            await asyncio.sleep(0.4)
            check("clicking it opens the voice menu", bool(await s.js("!!document.getElementById('speechMenu')")))
            await s.js("document.querySelector('#speechMenu [data-mode=\"always\"]').click(); true")
            await asyncio.sleep(0.6)
            check("'Read every reply' is saved", json.load(urllib.request.urlopen(base + "/api/speech"))["settings"]["mode"] == "always")
            await s.js("document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })); true")

            await s.js(SPY)
            await s.js((ROOT / "scripts" / "media" / "chat_demo.js").read_text())
            # A typed question: the reply is read as it streams in.
            await s.js("document.getElementById('chatForm').dispatchEvent(new Event('submit', { cancelable: true })); true")
            await asyncio.sleep(0.2)
            await s.js("XodChatDemo.run({ speed: 2 }).then(() => true)")
            for _ in range(60):
                await asyncio.sleep(0.5)
                if not await s.js("XodSpeech.speaking()"):
                    break
            said = await s.js("window.__said")
            joined = " ".join(said)
            check("the reply is spoken in pieces, in order", len(said) >= 3 and said[0].startswith("Here's what happened"), json.dumps(said[:3]))
            check("first piece is short so the voice starts quickly", len(said[0]) < 120, said[0][:120])
            check("links and markup aren't read out", "http" not in joined and "](" not in joined)
            check("the source list isn't read out", "Source links" not in joined)
            check("the companion's mouth follows the voice", (await s.js("window.__mouth")) > 10, str(await s.js("window.__mouth")))
            check("it finishes", not await s.js("XodSpeech.speaking()"))

            # Typing stops it.
            await s.js("window.__said = []; document.getElementById('chatForm').dispatchEvent(new Event('submit', { cancelable: true })); true")
            await asyncio.sleep(0.2)
            await s.js("XodChatDemo.run({ speed: 2 }); true")
            for _ in range(40):
                await asyncio.sleep(0.25)
                if await s.js("XodSpeech.state().playing"):
                    break
            was = await s.js("XodSpeech.state().playing")
            await s.js("const i = document.getElementById('chatInput'); i.value = 'x'; i.dispatchEvent(new Event('input')); true")
            await asyncio.sleep(0.4)
            st = await s.js("XodSpeech.state()")
            check("typing stops the voice", was and not st["playing"] and st["queued"] == 0, json.dumps(st))
            await asyncio.sleep(6)

            # 'When I talk': a typed question stays quiet, a spoken one is answered aloud.
            await s.js("fetch('/api/speech/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: 'voice' }) }).then(() => XodSpeech.reload()).then(() => true)")
            await s.js("window.__said = []; document.getElementById('chatInput').value = ''; document.getElementById('chatForm').dispatchEvent(new Event('submit', { cancelable: true })); true")
            await asyncio.sleep(0.2)
            await s.js("XodChatDemo.run({ speed: 3 }).then(() => true)")
            await asyncio.sleep(1)
            check("'When I talk': typed questions aren't read", len(await s.js("window.__said")) == 0)
            await s.js("window.__said = []; document.getElementById('chatInput').value = ''; quickSendPrompt(''); true")
            await asyncio.sleep(0.2)
            await s.js("XodChatDemo.run({ speed: 3 }).then(() => true)")
            await asyncio.sleep(1)
            check("'When I talk': a spoken question is answered aloud", len(await s.js("window.__said")) >= 2)
            await s.js("XodSpeech.stop(); true")

            # Listen on a finished reply reads it, skipping the table marker text itself.
            await s.js("window.__said = []; document.querySelector('.btn-listen').click(); true")
            await asyncio.sleep(1.5)
            said = await s.js("window.__said")
            check("Listen reads a finished reply", len(said) >= 1, json.dumps(said[:2]))
            await s.js("XodSpeech.stop(); true")
            check("no page errors", not errors, "; ".join(errors[:3]))
    finally:
        server.terminate()
        pwned.unlink(missing_ok=True)

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
