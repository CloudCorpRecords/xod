"""
Talk voice redesign, Phase 3: the neural voice behind the spoken replies.

  .venv/bin/python tests/smoke/run_speech_kokoro_test.py

speech.py gains a local Kokoro-82M engine behind the same /api/speech/say
contract (services/speech.py synthesize). No cloud TTS. On a machine without
Kokoro (this test rig, a Linux VM), a fake engine stands in through
XOD_KOKORO_FAKE=1: it renders a tone at 24 kHz through the exact code path
the real pipeline uses, so the integration is proven here and only the
on-Mac sound of the real model is still owed. Checks:

  1. With the fake engine present, synthesize returns a 24 kHz WAV and the
     engine reports itself as kokoro, with the named default voice af_heart.
  2. The Kokoro voices are listed as neural voices and the default voice is
     the Kokoro one when the engine is present.
  3. The fallback rate is 175 wpm (it was 190, which made the system voice
     sound rushed on top of robotic).
  4. With no engine present and speech off, synthesize still answers with
     the silent WAV (the off switch path is unchanged) and nothing crashes.
"""
import asyncio
import io
import os
import sys
import wave
from pathlib import Path

ROOT = Path(os.environ.get("XOD_TEST_ROOT") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(ROOT))
os.environ["XOD_DATA_DIR"] = os.environ.get("XOD_DATA_DIR") or str(Path(__file__).resolve().parent / ".tmp-kokoro-data")

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


def wav_rate(data):
    with wave.open(io.BytesIO(data)) as w:
        return w.getframerate(), w.getnframes()


async def main():
    from app.services import speech

    check("the fallback rate is 175 wpm", speech.RATE_DEFAULT == 175, str(speech.RATE_DEFAULT))

    # 1 and 2: the fake Kokoro engine, through the real code path
    os.environ["XOD_KOKORO_FAKE"] = "1"
    os.environ.pop("XOD_SPEECH", None)
    speech._kokoro_cache.update(pipelines={})
    try:
        data = await speech.synthesize("Hello, this is the new voice of Xod.")
        rate, frames = wav_rate(data) if data else (0, 0)
        check("fake Kokoro renders a 24 kHz WAV", rate == 24000 and frames > 2400, f"rate={rate} frames={frames}")
        st = await speech.status()
        check("status reports the kokoro engine", st.get("engine") == "kokoro", str(st.get("engine")))
        check("the named default voice is af_heart", st.get("kokoro", {}).get("default_voice") == "af_heart", str(st.get("kokoro")))
        voices = await speech.list_voices()
        neural = [v for v in voices if v.get("quality") == "neural"]
        check("Kokoro voices are listed as neural", any(v["name"] == "af_heart" for v in neural), str(neural[:3]))
        check("the best voice is the neural one when Kokoro is present", (await speech.best_voice()) == "af_heart", await speech.best_voice())
    finally:
        os.environ.pop("XOD_KOKORO_FAKE", None)
        speech._kokoro_cache.update(pipelines={})

    # 4: no engine, speech off: the silent answer, unchanged
    os.environ["XOD_SPEECH"] = "off"
    try:
        data = await speech.synthesize("Anything at all.")
        check("speech off still answers with a silent WAV", bool(data) and data[:4] == b"RIFF")
        st = await speech.status()
        check("status still answers with speech off", "settings" in st, str(list(st)[:5]))
    finally:
        os.environ.pop("XOD_SPEECH", None)

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)} passed; failing: {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
