#!/usr/bin/env python3
"""
Voice input end to end, with no human and no real microphone: Chrome gets a fake microphone that
plays a sentence spoken by macOS `say`. Xod's recorder must hear it, stop on the pause, send a
WAV to /api/voice/transcribe, and the on-device Whisper must write back the right words.

  .venv/bin/python tests/smoke/run_voice_test.py
"""
import asyncio
import os
import re
import shutil
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
from studio import Studio  # noqa: E402

SENTENCE = "What is the weather like in Kyoto this weekend"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_wav(path: Path):
    """The sentence as 16-bit mono WAV, with silence after it so the recorder notices the pause."""
    aiff = path.with_suffix(".aiff")
    subprocess.run(["say", "-o", str(aiff), SENTENCE], check=True)
    import imageio_ffmpeg
    raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-i", str(aiff), "-f", "s16le",
                          "-ac", "1", "-ar", "48000", "pipe:1"], capture_output=True, check=True).stdout
    speech = np.frombuffer(raw, np.int16)
    silence = np.zeros(48000 * 4, np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(np.concatenate([np.zeros(48000 // 2, np.int16), speech, silence]).tobytes())


async def run(base, wav):
    # Chrome's sandboxed audio service can't open the file, so it runs in-process for this test.
    args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", f"--use-file-for-fake-audio-capture={wav}",
            "--disable-features=AudioServiceOutOfProcess,AudioServiceSandbox"]
    async with Studio(width=1280, height=800, scale=1, port=free_port(), cursor=False, chrome_args=args) as s:
        await s.goto(base + "/", wait=3)
        # Catch the transcript instead of sending it as a chat message.
        await s.js("window.__heard = null; handleJarvisVoiceCommand = (t) => { window.__heard = t; }")
        await s.js("startJarvisVoice()")
        t0 = time.time()
        mood_seen = False
        while time.time() - t0 < 120:
            heard = await s.js("window.__heard")
            if not mood_seen:
                mood_seen = await s.js("window.XodMascot && XodMascot.mood() === 'listening'")
            if heard:
                return heard, mood_seen, time.time() - t0
            await asyncio.sleep(0.5)
        return None, mood_seen, time.time() - t0


def main():
    tmp = Path(tempfile.mkdtemp(prefix="xod-voice-", dir="/tmp")).resolve()
    wav = tmp / "speech.wav"
    make_wav(wav)
    port = free_port()
    env = dict(os.environ, XOD_DATA_DIR=str(tmp / "data"), XOD_SECRETS_SERVICE="Xod test keys")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = False
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/")
                break
            except Exception:
                time.sleep(0.5)
        heard, mood, secs = asyncio.run(run(base, wav))
        norm = lambda t: re.sub(r"[^a-z ]", "", (t or "").lower()).split()
        want, got = norm(SENTENCE), norm(heard)
        hits = sum(1 for w in want if w in got)
        ok = hits >= len(want) - 1
        print(f"{'PASS' if mood else 'FAIL'}  the moon listened while recording")
        print(f"{'PASS' if ok else 'FAIL'}  transcribed on this Mac in {secs:.0f}s: {heard!r} ({hits}/{len(want)} words)")
        ok = ok and mood
    finally:
        server.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nSUMMARY {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
