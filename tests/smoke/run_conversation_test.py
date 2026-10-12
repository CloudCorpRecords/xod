"""
Hands-free conversation, end to end: headless Chrome with a fake microphone that plays recorded
speech (made with macOS `say`), the real local Whisper, the real chat model and the real voice.

  1. Wake word: "Hey Xod, how far away is the moon?" in one breath starts a conversation, the
     question goes out as a spoken turn, the answer is spoken, it waits for a follow-up, and ends
     after a quiet spell (back to listening for "Hey Xod"). Timings are printed.
  2. Turn-taking (silent mic, utterances injected): "stop" interrupts, talking over Xod with a new
     question replaces the old one, "say that again", "thanks, that's all" says goodbye and ends.
  3. Wake phrases and near misses through Whisper.

Uses a throwaway data folder, never yours. Needs macOS, Chrome and the chat model:
  .venv/bin/python tests/smoke/run_conversation_test.py
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
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "media"))
sys.path.insert(0, str(ROOT))
from studio import Studio  # noqa: E402

results = []
TMP = Path(tempfile.mkdtemp(prefix="xod-convo-"))


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def spoken(text, voice="Samantha", rate=48000):
    f = TMP / f"say-{abs(hash((text, voice)))}.wav"
    subprocess.run(["say", "-v", voice, "-o", str(f), "--file-format=WAVE", f"--data-format=LEI16@{rate}", text], check=True)
    with wave.open(str(f)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16)


def compose(name, parts, rate=48000):
    """parts: seconds of silence (float) or text to say. A little room noise so it isn't digital silence."""
    out = []
    for p in parts:
        out.append(np.zeros(int(p * rate), np.int16) if isinstance(p, (int, float)) else spoken(p, rate=rate))
    x = np.concatenate(out).astype(np.int32)
    x += np.random.default_rng(1).integers(-25, 25, x.size)
    path = TMP / f"{name}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(np.clip(x, -32768, 32767).astype(np.int16).tobytes())
    return path


def mic_args(path):
    return ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", f"--use-file-for-fake-audio-capture={path}",
            "--disable-features=AudioServiceOutOfProcess,AudioServiceSandbox"]


SPY = r"""
window.__t0 = performance.now();
window.__states = [];
window.__chats = [];
window.__said = [];
window.__net = [];
const _f = window.fetch;
window.fetch = function (url, opts) {
  const u = String(url);
  if (u.includes('/api/voice/') || u.includes('/api/llm/warm')) {
    const t = performance.now();
    const p = _f.apply(this, arguments);
    p.then(() => window.__net.push({ u: u.replace(/^.*\/api\//, ''), at: Math.round(t), took: Math.round(performance.now() - t) })).catch(() => {});
    return p;
  }
  if (u.includes('/api/chat')) { try { const b = JSON.parse(opts.body); window.__chats.push({ at: performance.now(), voice: b.voice, q: b.messages[b.messages.length - 1].content }); } catch (e) {} }
  if (u.includes('/api/speech/say')) { try { window.__said.push({ at: performance.now(), text: JSON.parse(opts.body).text }); } catch (e) {} }
  return _f.apply(this, arguments);
};
setInterval(() => {
  const s = XodConversation.state();
  const last = window.__states[window.__states.length - 1];
  const key = s.state + (s.active ? '*' : '');
  if (!last || last.key !== key) window.__states.push({ key, at: performance.now() });
}, 50);
true
"""


async def page(base, chrome_args, errors):
    s = Studio(width=1280, height=820, scale=1, port=free_port(), cursor=False, chrome_args=chrome_args)
    await s.__aenter__()
    await s.cmd("Runtime.enable")

    async def reader():
        async for raw in s.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in s._pending:
                s._pending.pop(msg["id"]).set_result(msg)
            elif msg.get("method") == "Runtime.exceptionThrown":
                errors.append(str(msg["params"]["exceptionDetails"].get("exception", {}).get("description", ""))[:300])
            elif msg.get("method") == "Runtime.consoleAPICalled":
                text = " ".join(str(a.get("value", "")) for a in msg["params"]["args"])
                if msg["params"]["type"] == "error" or "Xod conversation" in text:
                    errors.append(text[:300])
    s.reader.cancel()
    s.reader = asyncio.create_task(reader())
    await s.goto(base + "/", wait=4)
    await s.js("document.querySelectorAll('.fixed.inset-0').forEach(e => { if (e.textContent.includes('Pick Xod')) e.remove(); }); true")
    await s.js(SPY)
    return s


async def wait_for(s, expr, timeout, step=0.25):
    end = time.time() + timeout
    while time.time() < end:
        if await s.js(expr):
            return True
        await asyncio.sleep(step)
    return False


async def main():
    port = free_port()
    data = tempfile.mkdtemp(prefix="xod-convo-data-")
    env = {**os.environ, "XOD_DATA_DIR": data, "XOD_NO_UPDATE_CHECK": "1"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/api/speech", timeout=2)
                break
            except Exception:
                time.sleep(0.25)

        # ---- 3. wake phrases through Whisper
        from app.services import speech_to_text as stt
        cases = [("Hey Xod, how far away is the moon?", "Samantha", True, "how far away"), ("Hey Xod", "Daniel", True, ""),
                 ("Hey Xod, what time is it in Tokyo?", "Karen", True, "tokyo"), ("Hey Siri, what's the weather", "Samantha", False, ""),
                 ("Hey there, how are you doing", "Daniel", False, ""), ("I told Todd the facade was odd", "Samantha", False, "")]
        hits = 0
        for text, voice, want, rest in cases:
            f = compose(f"w{hits}{voice}", [0.3, text, 0.3], rate=16000)
            heard = (await stt.transcribe(f.read_bytes()))
            r = stt.wake_match(heard["text"], heard.get("seconds", 2.0))
            ok = r["wake"] == want and (not rest or rest in r["rest"].lower())
            hits += ok
            check(f"wake phrase: {text!r} ({voice}) → {'wakes' if want else 'ignored'}", ok, f"heard {heard['text']!r} → {json.dumps(r)}")

        # ---- 1. the whole thing, from the wake word
        errors = []
        audio = compose("wake_ask", [2.0, "Hey Xod, how far away is the moon?", 40.0])
        s = await page(base, mic_args(audio), errors)
        try:
            await s.js("XodConversation.setWake(true).then(() => true)")
            ok = await wait_for(s, "XodConversation.state().active", 15)
            check("'Hey Xod, …' starts a conversation", ok, json.dumps(await s.js("window.__states")))
            ok = await wait_for(s, "window.__chats.length > 0", 15)
            chats = await s.js("window.__chats")
            check("the rest of the sentence is asked, as a spoken turn",
                  ok and "moon" in str(chats[0]["q"]).lower() and chats[0]["voice"] is True, json.dumps(chats))
            check("the dock is open", bool(await s.js("document.getElementById('talkDock')?.classList.contains('is-open')")))
            ok = await wait_for(s, "XodConversation.state().state === 'speaking'", 60)
            check("Xod answers out loud", ok and len(await s.js("window.__said")) > 0)
            ok = await wait_for(s, "XodConversation.state().state === 'listening' && XodConversation.state().active", 90)
            check("then it listens for a follow-up, no wake word needed", ok)
            ok = await wait_for(s, "!XodConversation.state().active && XodConversation.state().state === 'wake'", 16)
            check("after a quiet spell it ends and goes back to listening for 'Hey Xod'", ok, json.dumps(await s.js("XodConversation.state()")))
            st = await s.js("window.__states")
            said = await s.js("window.__said")
            chat = (await s.js("window.__chats"))[0]
            active_at = next((x["at"] for x in st if x["key"].endswith("*")), None)
            first_said = next((x for x in said if x["at"] > chat["at"]), None)
            if active_at and first_said:
                print(f"      timings: wake word heard → question sent {(chat['at'] - active_at) / 1000:.1f}s; "
                      f"question sent → first words spoken requested {(first_said['at'] - chat['at']) / 1000:.1f}s ({first_said['text']!r})")
            print("      voice requests:", json.dumps(await s.js("window.__net"))[:900])
            answer = await s.js("[...document.querySelectorAll('#chatMessages .prose-custom')].pop().innerText")
            check("the answer is short and spoken-style", len(answer.split("Source")[0]) < 500 and "|" not in answer, answer[:200])
            check("no page errors (wake test)", not errors, "; ".join(errors[:3]))
        finally:
            await s.__aexit__()

        # ---- 4. a microphone the test can play into: barge-in through the real path, latency, captions
        errors = []
        s = await page(base, [], errors)
        try:
            import base64
            clips = {}
            for key, text in (("ask", "Hey Xod, tell me about the history of the Roman Empire"), ("stop", "Stop."),
                              ("long", "What are three good places to visit in Japan in the spring"), ("bye", "Thanks, that's all.")):
                f = compose(f"clip_{key}", [0.2, text, 0.2])
                clips[key] = base64.b64encode(f.read_bytes()).decode()
            await s.js("""
              (() => {
                const ctx = new AudioContext();
                const dest = ctx.createMediaStreamDestination();
                // a whisper of room noise, so it isn't digital silence
                const noise = ctx.createBufferSource(), nb = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate), d = nb.getChannelData(0);
                for (let i = 0; i < d.length; i++) d[i] = (Math.random() - 0.5) * 0.0015;
                noise.buffer = nb; noise.loop = true; noise.connect(dest); noise.start();
                navigator.mediaDevices.getUserMedia = async () => dest.stream;
                window.__micCtx = ctx;
                window.__play = async (b64) => {
                  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
                  const buf = await ctx.decodeAudioData(bytes.buffer);
                  const src = ctx.createBufferSource(); src.buffer = buf; src.connect(dest);
                  await ctx.resume();
                  return new Promise((r) => { src.onended = () => { window.__micEndedAt = performance.now(); r(true); }; src.start(); });
                };
                window.__xodTalkLog = [];
                window.__pieces = [];
                window.addEventListener('xod:speech', (e) => { if (e.detail.type === 'piece') window.__pieces.push({ at: performance.now(), text: e.detail.text }); });
                window.__partial = false;
                new MutationObserver(() => { if (document.querySelector('.talk-caption--you.is-partial')) window.__partial = true; })
                  .observe(document.body, { subtree: true, attributes: true, attributeFilter: ['class'], childList: true });
                return true;
              })()""")
            await s.js("XodConversation.setWake(true).then(() => true)")
            await asyncio.sleep(1.0)
            await s.js(f"__play('{clips['ask']}')")
            ok = await wait_for(s, "window.__pieces.length > 0", 60)
            lat = await s.js("window.__pieces.length ? (window.__pieces[0].at - window.__micEndedAt) / 1000 : null")
            check("spoken question → Xod's first words", ok, f"{lat}")
            print(f"      latency: end of your sentence → Xod starts talking: {lat:.1f}s ({(await s.js('window.__pieces'))[0]['text']!r})" if lat else "")
            ok = await wait_for(s, "XodSpeech.audioPlaying() && XodConversation.state().state === 'speaking'", 60)
            await asyncio.sleep(1.0)
            await s.js(f"__play('{clips['stop']}')")
            ok2 = await wait_for(s, "!XodSpeech.audioPlaying() && XodConversation.state().state === 'listening'", 8)
            check("saying 'stop' into the mic while it talks stops it", ok and ok2,
                  json.dumps(await s.js("({ c: XodConversation.state(), s: XodSpeech.state(), log: window.__xodTalkLog.slice(-8) })")))
            await asyncio.sleep(0.8)
            await s.js(f"__play('{clips['long']}')")
            ok = await wait_for(s, "window.__chats.length >= 2", 15)
            check("a follow-up needs no wake word", ok and "japan" in str((await s.js("window.__chats"))[-1]["q"]).lower(), json.dumps(await s.js("window.__chats")))
            check("live captions while you talk", bool(await s.js("window.__partial")))
            await wait_for(s, "XodConversation.state().state === 'listening' && !isStreaming && !XodSpeech.speaking()", 90)
            await s.js(f"__play('{clips['bye']}')")
            ok = await wait_for(s, "!XodConversation.state().active", 15)
            check("'thanks, that's all' into the mic ends it", ok)
            check("no page errors (mic test)", not errors, "; ".join(errors[:3]))
        finally:
            await s.__aexit__()

        # ---- 2. turn-taking, with a silent mic and utterances injected
        errors = []
        s = await page(base, mic_args(compose("silence", [60.0])), errors)
        try:
            await s.js("XodConversation.start({ source: 'button' }).then(() => true)")
            check("the mic button starts a conversation", bool(await s.js("XodConversation.state().active && XodConversation.state().state === 'listening'")))
            await s.js("XodConversation.__heard('Tell me a little about the history of the city of Rome'); true")
            ok = await wait_for(s, "XodConversation.state().state === 'speaking'", 90)
            check("a question gets a spoken answer", ok)
            await s.js("XodConversation.__heard('stop'); true")
            await asyncio.sleep(0.6)
            st = await s.js("({ c: XodConversation.state(), s: XodSpeech.state(), streaming: isStreaming })")
            check("'stop' silences it and it listens again", st["c"]["state"] == "listening" and not st["s"]["playing"] and not st["streaming"], json.dumps(st))

            await s.js("window.__chats = []; XodConversation.__heard('What is the tallest mountain in Africa?'); true")
            ok = await wait_for(s, "XodConversation.state().state === 'speaking'", 90)
            await s.js("XodConversation.__heard('Actually, what about the tallest mountain in Europe?'); true")
            ok2 = await wait_for(s, "window.__chats.length >= 2", 10)
            chats = await s.js("window.__chats")
            check("talking over it with a new question replaces the old one", ok and ok2 and "europe" in str(chats[-1]["q"]).lower(), json.dumps(chats))
            ok = await wait_for(s, "XodConversation.state().state === 'listening' && !isStreaming && !XodSpeech.speaking()", 90)
            answer = await s.js("[...document.querySelectorAll('#chatMessages .prose-custom')].pop().innerText")
            check("…and answers that one", ok and "elbrus" in answer.lower(), answer[:200])

            await s.js("window.__said = []; XodConversation.__heard('Say that again'); true")
            ok = await wait_for(s, "window.__said.length > 0", 10)
            check("'say that again' repeats the last answer", ok and "elbrus" in " ".join(x["text"] for x in await s.js("window.__said")).lower())
            await wait_for(s, "XodConversation.state().state === 'listening'", 30)

            await s.js("window.__said = []; XodConversation.__heard(\"Thanks, that's all\"); true")
            ok = await wait_for(s, "!XodConversation.state().active", 15)
            said = [x["text"] for x in await s.js("window.__said")]
            check("'thanks, that's all' says goodbye and ends", ok and len(said) >= 1, json.dumps(said))
            check("the mic is released when it's over (no wake word)", await s.js("XodConversation.state().state") == "off")
            check("no page errors (turn-taking test)", not errors, "; ".join(errors[:3]))
        finally:
            await s.__aexit__()
    finally:
        server.terminate()

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
