"""
A hands-free conversation with Xod, recorded from the real app with sound: "Hey Xod" in one breath,
a follow-up with no wake word, talking over it with a new question, and goodbye.

The page's own audio can't be captured from headless Chrome, so the soundtrack is rebuilt from the
same pieces at the moments they happened: the questions (played into a programmable microphone),
each sentence Xod spoke (the server's cached audio for it) and the chimes.

Point it at a Xod running on a demo data folder (never your own):
  XOD_DATA_DIR=/tmp/xod-demo .venv/bin/python -m uvicorn app.main:app --port 8125
  .venv/bin/python scripts/media/make_conversation_video.py --url http://127.0.0.1:8125 --out ~/Desktop/xod-conversation.mp4
"""
import argparse
import asyncio
import base64
import io
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from studio import Studio, ffmpeg, frames_to_mp4  # noqa: E402

W, H, RATE = 1280, 800, 48000
USER_VOICE = "Daniel"

SCRIPT = [
    ("ask", "Hey Xod, what's the weather going to be like in Tokyo tomorrow?"),
    ("follow", "Nice. What should I pack for that?"),
    ("over", "Actually, where's a good place for sushi there?"),
    ("bye", "Thanks, that's all."),
]

MIC = r"""
(() => {
  const ctx = new AudioContext();
  const dest = ctx.createMediaStreamDestination();
  const noise = ctx.createBufferSource(), nb = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate), d = nb.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = (Math.random() - 0.5) * 0.0015;
  noise.buffer = nb; noise.loop = true; noise.connect(dest); noise.start();
  navigator.mediaDevices.getUserMedia = async () => dest.stream;
  window.__tl = [];
  window.__play = async (key, b64) => {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const buf = await ctx.decodeAudioData(bytes.buffer);
    const src = ctx.createBufferSource(); src.buffer = buf; src.connect(dest);
    await ctx.resume();
    window.__tl.push({ at: performance.now(), kind: "mic", key });
    return new Promise((r) => { src.onended = () => r(true); src.start(); });
  };
  window.addEventListener("xod:speech", (e) => { if (e.detail.type === "piece") window.__tl.push({ at: performance.now(), kind: "piece", text: e.detail.text }); });
  window.addEventListener("xod:speech", (e) => { if (e.detail.type === "end") window.__tl.push({ at: performance.now(), kind: "end" }); });
  window.addEventListener("xod:chime", (e) => window.__tl.push({ at: performance.now(), kind: "chime", chime: e.detail }));
  // Stop and silence cut a piece short: note when, so the soundtrack cuts there too.
  const stop = XodSpeech.stop;
  XodSpeech.stop = function () { window.__tl.push({ at: performance.now(), kind: "cut" }); return stop.apply(this, arguments); };
  return true;
})()
"""


def say_wav(text: str, voice: str) -> np.ndarray:
    f = Path(tempfile.mktemp(suffix=".wav"))
    subprocess.run(["say", "-v", voice, "-o", str(f), "--file-format=WAVE", f"--data-format=LEI16@{RATE}", text], check=True)
    return read_wav(f.read_bytes())


def read_wav(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data)) as w:
        rate, x = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    if rate != RATE:
        n = int(len(x) * RATE / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x


def wav_bytes(x: np.ndarray) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return b.getvalue()


def chime(kind: str) -> np.ndarray:
    """The moon's chimes, as conversation.js plays them."""
    b = 660.0
    seq = {"start": [(b, 0, .12), (b * 1.5, .11, .2)], "listen": [(b * 1.5, 0, .09)], "end": [(b * 1.5, 0, .12), (b, .11, .22)],
           "doze": [(b, 0, .18), (b * .84, .17, .18), (b * .67, .34, .32)], "error": [(b * .6, 0, .16), (b * .5, .15, .25)]}.get(kind, [])
    out = np.zeros(int(RATE * 0.8), np.float32)
    for f, at, dur in seq:
        n = int(dur * RATE)
        t = np.arange(n) / RATE
        env = np.minimum(1, t / 0.012) * np.exp(-t / (dur / 4))
        s = int(at * RATE)
        out[s:s + n] += 0.09 * env * np.sin(2 * np.pi * f * t)
    return out


def xod_audio(base: str, text: str) -> np.ndarray:
    req = urllib.request.Request(base + "/api/speech/say", data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return read_wav(r.read()) if r.status == 200 else np.zeros(1, np.float32)


async def wait_for(s, expr, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if await s.js(expr):
            return True
        await asyncio.sleep(0.2)
    return False


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8125")
    ap.add_argument("--look", default="moon")
    ap.add_argument("--out", default=str(Path.home() / "Desktop" / "xod-conversation.mp4"))
    a = ap.parse_args()
    clips = {k: say_wav(t, USER_VOICE) for k, t in SCRIPT}
    b64 = {k: base64.b64encode(wav_bytes(x)).decode() for k, x in clips.items()}

    async with Studio(W, H, scale=2, cursor=False) as s:
        await s.goto(a.url, wait=4)
        await s.js("document.querySelectorAll('.fixed.inset-0').forEach(e => { if (e.textContent.includes('Pick Xod')) e.remove(); }); true")
        await s.js(f"XodMascot.setCharacter('{a.look}', {{ save: false, greet: false }}); true")
        await s.js(MIC)
        await s.js("XodConversation.setWake(true).then(() => true)")
        await asyncio.sleep(2)
        await s.start_recording()
        page_t0 = await s.js("performance.now()")
        py_t0 = time.monotonic()
        await asyncio.sleep(1.5)

        await s.js(f"__play('ask', '{b64['ask']}')")
        await wait_for(s, "XodConversation.state().active && XodConversation.state().state === 'listening' && !isStreaming && !XodSpeech.speaking()", 120)
        await asyncio.sleep(1.2)
        answers = "window.__tl.filter((e) => e.kind === 'piece' && !/^(Let me|One sec|Looking|On it|Give me|Working)/.test(e.text)).length"
        before = await s.js(answers)
        await s.js(f"__play('follow', '{b64['follow']}')")
        await wait_for(s, f"{answers} > {before}", 90)           # its real answer has started
        await asyncio.sleep(2.2)
        await s.js(f"__play('over', '{b64['over']}')")
        await wait_for(s, "XodConversation.state().state === 'listening' && !isStreaming && !XodSpeech.speaking()", 120)
        await asyncio.sleep(1.0)
        await s.js(f"__play('bye', '{b64['bye']}')")
        await wait_for(s, "!XodConversation.state().active", 30)
        await asyncio.sleep(2.0)
        tl = await s.js("window.__tl")
        frames = await s.stop_recording()

    work = Path(tempfile.mkdtemp(prefix="xod-convo-video-"))
    # page time -> seconds into the recording
    offset = py_t0 - frames[0][0]
    rec = lambda page_ms: (page_ms - page_t0) / 1000 + offset  # noqa: E731

    # Everything that makes a sound, in recording seconds, so the quiet waiting in between can be
    # shortened. Sound is never sped up; the video says when time is compressed.
    cuts = [e["at"] for e in tl if e["kind"] == "cut"]
    audio_of = {}
    busy = []
    for e in tl:
        at = rec(e["at"])
        if e["kind"] == "mic":
            busy.append((at, at + len(clips[e["key"]]) / RATE))
        elif e["kind"] == "piece":
            x = audio_of.setdefault(e["text"], xod_audio(a.url, e["text"]))
            nxt = [rec(c) for c in cuts if c > e["at"]]
            busy.append((at, min(at + len(x) / RATE, nxt[0] if nxt else 1e9)))
        elif e["kind"] == "chime":
            busy.append((at, at + 0.6))
    busy.sort()
    total_rec = frames[-1][0] - frames[0][0] + 0.5
    # Quiet gaps longer than MAX_GAP become MAX_GAP long.
    MAX_GAP, PAD = 2.2, 0.6
    marks, cursor = [(0.0, 0.0)], 0.0
    sped = []
    edges = [(max(0.0, s - PAD), e + PAD) for s, e in busy]
    merged = []
    for s, e in edges:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    prev_end = 0.0
    out_t = 0.0
    for s, e in merged + [(total_rec, total_rec)]:
        gap = s - prev_end
        if gap > MAX_GAP * 1.8:          # only worth it (and labelled) when it saves real time
            marks.append((prev_end, out_t))
            sped.append((out_t, out_t + MAX_GAP, gap / MAX_GAP))
            out_t += MAX_GAP
            marks.append((s, out_t))
        else:
            out_t += gap
            marks.append((s, out_t))
        out_t += e - s
        marks.append((e, out_t))
        prev_end = e
    mx, my = np.array([m[0] for m in marks]), np.array([m[1] for m in marks])
    warp = lambda t: float(np.interp(t, mx, my))  # noqa: E731

    t0f = frames[0][0]
    warped, last = [], -1.0
    for t, data in frames:
        wt = warp(t - t0f)
        if wt - last >= 1 / 60:            # drop frames that land on top of each other in sped-up parts
            warped.append((wt, data))
            last = wt
    video = frames_to_mp4(warped, work / "video.mp4", fps=30, size=(W, H), scale=2)
    total = warp(total_rec) + 0.5
    track = np.zeros(int(total * RATE) + RATE, np.float32)

    def put(x, at_s, until_s=None):
        i = int(max(0, at_s) * RATE)
        if until_s is not None:
            x = x[: max(0, int((until_s - at_s) * RATE))]
        x = x[: max(0, len(track) - i)]
        track[i:i + len(x)] += x

    for e in tl:
        at = warp(rec(e["at"]))
        if e["kind"] == "mic":
            put(clips[e["key"]] * 0.9, at)
        elif e["kind"] == "piece":
            nxt = [c for c in cuts if c > e["at"]]
            until = warp(rec(nxt[0])) if nxt else None
            put(audio_of[e["text"]] * 0.9, at, until)
        elif e["kind"] == "chime":
            put(chime(e["chime"]), at)
    peak = np.abs(track).max() or 1
    (work / "audio.wav").write_bytes(wav_bytes(track / max(1.0, peak / 0.95)))
    out = Path(a.out).expanduser()
    # "Sped up while Xod thinks", whenever it is.
    font = "/System/Library/Fonts/Supplemental/Arial.ttf"
    label = ",".join(
        f"drawtext=fontfile='{font}':text='sped up x{k:.0f} while Xod thinks':fontcolor=0xFFFAF0@0.85:fontsize=26:"
        f"box=1:boxcolor=0x0F1222@0.75:boxborderw=14:x=w-tw-36:y=36:enable='between(t,{s0:.2f},{s1:.2f})'"
        for s0, s1, k in sped) or "null"
    try:
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(video), "-i", str(work / "audio.wav"), "-vf", label,
                        "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                        "-shortest", "-movflags", "+faststart", str(out)], check=True)
    except subprocess.CalledProcessError:   # no font support in this ffmpeg: no label
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(video), "-i", str(work / "audio.wav"),
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out)], check=True)
    print("sped-up stretches:", [(round(s0, 1), round(s1, 1), round(k, 1)) for s0, s1, k in sped])
    print(out, out.stat().st_size // 1024, "KB")
    print(json.dumps([{k: (round((v - page_t0) / 1000, 2) if k == "at" else v) for k, v in e.items()} for e in tl], indent=0)[:3000])


if __name__ == "__main__":
    asyncio.run(main())
