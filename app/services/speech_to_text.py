"""
Speech to text on this Mac (Whisper on the GPU via MLX). Audio never leaves the computer,
unlike the browser's built-in speech recognition, which sends it to Google and doesn't
exist at all inside the desktop app.

The model (~150 MB for base.en) downloads from Hugging Face on first use and is cached in
~/.cache/huggingface. XOD_WHISPER_MODEL picks another one, e.g. mlx-community/whisper-small.en-mlx.
"""
import asyncio
import logging
import os
import subprocess
import time
from typing import Any, Dict

import numpy as np

logger = logging.getLogger("xod.speech")

MODEL = os.environ.get("XOD_WHISPER_MODEL", "mlx-community/whisper-base.en-mlx")
MAX_BYTES = 25 * 1024 * 1024      # ~25 MB of compressed audio
MAX_SECONDS = 120
_lock = asyncio.Lock()


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _decode(audio: bytes) -> np.ndarray:
    """Any browser recording (webm/ogg/mp4/wav) -> 16 kHz mono float32, which Whisper expects."""
    p = subprocess.run(
        [_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
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
    out = mlx_whisper.transcribe(samples, path_or_hf_repo=MODEL, condition_on_previous_text=False,
                                 no_speech_threshold=0.6, verbose=None)
    text = " ".join((out.get("text") or "").split())
    return {"success": True, "text": text, "seconds": round(samples.size / 16000, 1),
            "took_ms": int((time.time() - t) * 1000), "model": MODEL}


async def transcribe(audio: bytes) -> Dict[str, Any]:
    if not audio:
        return {"success": False, "error": "No audio received."}
    if len(audio) > MAX_BYTES:
        return {"success": False, "error": "Recording is too long."}
    async with _lock:   # one at a time: the model is shared
        try:
            return await asyncio.to_thread(_transcribe_sync, audio)
        except Exception as e:
            logger.warning(f"transcription failed: {e}")
            return {"success": False, "error": f"Transcription failed: {e}"}
