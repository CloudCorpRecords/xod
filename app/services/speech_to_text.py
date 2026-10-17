"""
Speech to text on this Mac (Whisper on the GPU via MLX). Audio never leaves the computer,
unlike the browser's built-in speech recognition, which sends it to Google and doesn't
exist at all inside the desktop app.

The model (Whisper large-v3 turbo, 4-bit, ~460 MB) downloads from Hugging Face on first use and is
cached in ~/.cache/huggingface. XOD_WHISPER_MODEL picks another one, e.g. mlx-community/whisper-small.en-mlx.

Whisper makes things up when it's given noise or silence: "no, no, no, no…", "Thanks for watching!",
one sentence repeated twenty times. Each segment carries Whisper's own doubts (how likely it was
silence, how sure it was of the words, how repetitive the text is); clean() drops what it doubts, so
background sound becomes nothing instead of a question Xod answers.
"""
import asyncio
import io
import logging
import os
import subprocess
import re
import time
import wave
from typing import Any, Dict

import numpy as np

logger = logging.getLogger("xod.speech")

MODEL = os.environ.get("XOD_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo-q4")
LANGUAGE = os.environ.get("XOD_WHISPER_LANGUAGE", "en")   # "auto" lets it detect the language
MAX_BYTES = 25 * 1024 * 1024      # ~25 MB of compressed audio
MAX_SECONDS = 120
_lock = asyncio.Lock()


def _decode_wav(audio: bytes) -> np.ndarray:
    """16-bit PCM WAV (what Xod's recorder sends) -> 16 kHz mono float32, without ffmpeg."""
    with wave.open(io.BytesIO(audio)) as w:
        if w.getsampwidth() != 2:
            raise ValueError("expected 16-bit audio")
        rate, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(min(w.getnframes(), rate * MAX_SECONDS)), np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != 16000 and x.size:
        n = int(x.size * 16000 / rate)
        x = np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float32)
    return x


def _decode(audio: bytes) -> np.ndarray:
    """A recording -> 16 kHz mono float32, which Whisper expects. WAV is read directly; other
    formats (webm/ogg/mp4) need ffmpeg, which comes with Media Studio's tools."""
    if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        return _decode_wav(audio)
    from app.services import media_packages
    exe = media_packages.ffmpeg_exe()
    if not exe:
        raise ValueError("this recording format needs ffmpeg; Xod's recorder sends WAV, which doesn't")
    p = subprocess.run(
        [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-t", str(MAX_SECONDS), "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
        input=audio, capture_output=True, timeout=60,
    )
    if p.returncode != 0:
        raise ValueError(f"couldn't read the recording: {p.stderr.decode(errors='replace')[:200]}")
    return np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768.0


def _transcribe_sync(audio: bytes) -> Dict[str, Any]:
    import mlx_whisper
    t = time.time()
    samples = _decode(audio)
    if samples.size < 16000 * 0.3:
        return {"success": True, "text": "", "seconds": 0}
    # temperature 0 only: Whisper's retries at higher temperatures are where it invents sentences
    # out of a short, noisy clip.
    if not _has_voice(samples):
        return {"success": True, "text": "", "seconds": round(samples.size / 16000, 1), "dropped": "no voice"}
    kw = {} if LANGUAGE == "auto" or MODEL.endswith(".en-mlx") else {"language": LANGUAGE}
    out = mlx_whisper.transcribe(samples, path_or_hf_repo=MODEL, condition_on_previous_text=False,
                                 no_speech_threshold=0.6, temperature=0.0, verbose=None, **kw)
    text, why, conf = clean(out)
    return {"success": True, "text": text, "seconds": round(samples.size / 16000, 1), "confidence": conf,
            **({"dropped": why} if why else {}), "took_ms": int((time.time() - t) * 1000), "model": MODEL}


# What Whisper writes when there's nothing to hear (it learned them from video subtitles).
STOCK_LINES = {
    "you", "thank you", "thanks", "thank you very much", "thanks for watching", "thank you for watching",
    "thanks for watching!", "please subscribe", "subscribe", "like and subscribe", "bye", "bye bye", "okay",
    "so", "uh", "um", "hmm", "oh", "the end", "i'm going to make a video about this video",
    "subtitles by the amara.org community", "transcription by castingwords", "i'll see you next time",
}


def _has_voice(x) -> bool:
    """Is there speech-like sound at all? Speech comes in bursts well above the room's level;
    a fan, hum or silence doesn't. (Whisper invents words for those.)"""
    frame = 480   # 30 ms
    n = x.size // frame
    if n < 8:
        return False
    rms = np.sqrt((x[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
    floor = np.percentile(rms, 20)
    loud = rms > max(floor * 3.0, 0.006)
    if int(loud.sum()) >= 8:             # at least ~0.25 s clearly above the background
        return True
    # A clip that's speech from start to end has no quiet part to compare with, but speech still rises
    # and falls syllable by syllable; a fan or hiss stays flat.
    hi, mid = np.percentile(rms, 95), np.percentile(rms, 30)
    return bool(hi > 0.01 and hi > 2.2 * max(mid, 1e-6))


def _repetitive(text: str) -> bool:
    """'no, no, no, no…' or one phrase over and over."""
    import zlib
    words = re.findall(r"[a-z0-9']+", text.lower())
    if len(words) >= 8:
        if len(set(words)) / len(words) < 0.3:
            return True
        b = " ".join(words).encode()
        if len(b) / max(1, len(zlib.compress(b))) > 2.4:
            return True
    sents = [x for x in (re.sub(r"[^a-z0-9' ]", "", y.lower()).strip() for y in re.split(r"[.!?]+", text)) if x]
    if len(sents) >= 2 and len(set(sents)) == 1 and len(sents[0].split()) >= 3:
        return True                      # one sentence said again: "I'm going to make a video… I'm going to make a video…"
    for n in range(1, 7):                # the same 1–6 words, 4+ times in a row
        if re.search(r"\b((?:[a-z0-9']+\W+){%d})(?:\1){3,}" % n, text.lower() + " "):
            return True
    return False


def clean(out: Dict[str, Any]):
    """Whisper's result -> (text you said, why it was dropped or "", confidence 0–1)."""
    segs = out.get("segments") or []
    kept, probs = [], []
    for s in segs:
        t = (s.get("text") or "").strip()
        if not t:
            continue
        nsp, lp, cr = s.get("no_speech_prob", 0.0), s.get("avg_logprob", 0.0), s.get("compression_ratio", 1.0)
        if (nsp > 0.6 and lp < -0.5) or lp < -1.1 or cr > 2.4:
            continue                      # Whisper itself doubts it
        kept.append(t)
        probs.append(float(np.exp(lp)))
    text = " ".join(" ".join(kept).split()) if segs else " ".join((out.get("text") or "").split())
    if not text:
        return "", ("doubtful" if segs else ""), 0.0
    if _repetitive(text):
        return "", "repetitive", 0.0
    bare = re.sub(r"[^a-z0-9' ]", "", text.lower()).strip()
    conf = round(min(probs), 2) if probs else 0.5
    if bare in STOCK_LINES and conf < 0.75:
        return "", "stock phrase", conf
    return text, "", conf


async def transcribe(audio: bytes, partial: bool = False) -> Dict[str, Any]:
    if not audio:
        return {"success": False, "error": "No audio received."}
    from app.services.local_engine import is_apple_silicon
    if not is_apple_silicon():
        return {"success": False, "error": "Voice transcription needs a Mac with Apple silicon (M1 or newer)."}
    if len(audio) > MAX_BYTES:
        return {"success": False, "error": "Recording is too long."}
    if partial and _lock.locked():
        return {"success": True, "text": "", "busy": True}
    async with _lock:   # one at a time: the model is shared
        try:
            return await asyncio.to_thread(_transcribe_sync, audio)
        except Exception as e:
            logger.warning(f"transcription failed: {e}")
            return {"success": False, "error": f"Transcription failed: {e}"}


# ---------------------------------------------------------------- "Hey Xod"
# Whisper has never heard of "Xod": it writes "Hey Zod", "Hey Saud", "Hazod"… A prompt hint fixes
# the spelling but sometimes drops the phrase altogether, so wake detection uses the plain
# transcript and accepts the ways Whisper hears it. Near misses ("Hey Siri", "hey there",
# "I said hey to Todd") don't count. Anything after the name is the question: "Hey Xod, what's
# the weather tomorrow?" in one breath works.
import re as _re

_GREETING = r"(?:hey|hay|hi|hiya|a|eh|ey|ok|okay|yo|hello|oi)"
# The name by its sound: an s/z/x sound, an "o" or "aw" vowel, a d or t. Zod, Sod, Saud, Sott,
# "S got", Zahd, Xod… but not "sad" or "sat".
_NAME = r"(?:x|ex|z|s|s\s?g)\s?(?:o|oa|au|aa|ah|aw)h?(?:d|t)(?:d|t)?s?"
_WAKE_RE = _re.compile(rf"^(?:{_GREETING}[\s,]*)?{_NAME}\b|^(?:hazod|heyzod|hayzod|hey-zod|hazodd)\b", _re.I)
_WAKE_ALONE_RE = _re.compile(rf"^(?:{_GREETING}[\s,]*(?:god|odd)|facade|face odd|he'?s odd)$", _re.I)


def wake_match(text: str, seconds: float = 0.0) -> Dict[str, Any]:
    """{"wake": bool, "rest": the words after the wake phrase}."""
    t = " ".join((text or "").replace("…", " ").split())
    norm = _re.sub(r"[^\w\s,'-]", " ", t).strip().lower()
    norm = " ".join(norm.split())
    m = _WAKE_RE.match(norm)
    if m:
        rest = norm[m.end():].lstrip(" ,.-")
        # Keep the original casing and punctuation of the rest.
        words = len(norm[:m.end()].replace(",", " ").split())
        orig_words = _re.sub(r"[^\w\s'-]", " ", t).split()
        rest_orig = t
        for w in orig_words[:words]:
            rest_orig = rest_orig.split(w, 1)[-1] if w in rest_orig else rest_orig
        rest_orig = rest_orig.lstrip(" ,.!?-")
        return {"wake": True, "rest": (rest_orig[:1].upper() + rest_orig[1:]) if rest else ""}
    # Short clips Whisper turns into other words ("Facade.", "Hey God.") only count on their own.
    if seconds <= 2.4 and _WAKE_ALONE_RE.match(norm.strip(" ,")):
        return {"wake": True, "rest": ""}
    return {"wake": False, "rest": ""}


async def wake(audio: bytes) -> Dict[str, Any]:
    r = await transcribe(audio)
    if not r.get("success"):
        return {**r, "wake": False, "rest": ""}
    return {**r, **wake_match(r.get("text", ""), r.get("seconds", 0))}


_prepared = False


def _prepare_sync() -> None:
    """Loads Whisper now (a second of silence), so the first thing you say isn't waiting for it."""
    global _prepared
    import mlx_whisper
    mlx_whisper.transcribe(np.zeros(16000, np.float32), path_or_hf_repo=MODEL, verbose=None)
    _prepared = True


async def prepare() -> Dict[str, Any]:
    if _prepared:
        return {"ready": True}
    async with _lock:
        if not _prepared:
            try:
                await asyncio.to_thread(_prepare_sync)
            except Exception as e:
                return {"ready": False, "error": str(e)}
    return {"ready": True}
