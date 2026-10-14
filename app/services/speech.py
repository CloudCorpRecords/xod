"""
Spoken replies: Xod reads its answers aloud with a voice that runs on this Mac.

It uses macOS's own speech engine (the `say` tool), so nothing is downloaded or sent anywhere. The
voices are the ones in System Settings → Accessibility → Spoken Content; the downloadable
"Premium" and "Enhanced" ones are neural voices and sound far more natural, so Xod picks one of
those when it's there and suggests getting one when it isn't.

The chat sends the reply a sentence or so at a time as it streams in (static/js/speech.js); each
piece is cleaned of Markdown here, turned into a short WAV and played in the page, which also lets
the companion's mouth follow the actual sound.
"""
import asyncio
import hashlib
import json
import logging
import os
import sys
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.speech")

SAY = shutil.which("say") or "/usr/bin/say"
WIN = sys.platform == "win32"
# Windows: the built-in System.Speech engine through PowerShell. Values go in as environment variables (XOD_ARG0…),
# never into the script text: anything after -Command would be pasted into the script.
_PS = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]
_WIN_VOICES = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
               "$s.GetInstalledVoices() | Where-Object Enabled | ForEach-Object { $_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture.Name }")
_WIN_SPEAK = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "try { $s.SelectVoice($env:XOD_ARG0) } catch {}; $s.Rate = [int]$env:XOD_ARG1; "
              "if ($env:XOD_ARG2) { $s.SetOutputToWaveFile($env:XOD_ARG2) } else { $s.SetOutputToDefaultAudioDevice() }; "
              "$s.Speak([IO.File]::ReadAllText($env:XOD_ARG3, [Text.Encoding]::UTF8)); $s.Dispose()")


def _win_env(*values: str) -> dict:
    return {**os.environ, **{f"XOD_ARG{i}": str(v) for i, v in enumerate(values)}}


def _win_rate(wpm: int) -> str:
    """Words per minute (140-280, like macOS say) to System.Speech's -10..10."""
    return str(max(-10, min(10, round((int(wpm) - 175) / 12))))
CACHE = DATA_DIR / "cache" / "speech"
MAX_CHARS = 1200
RATE_MIN, RATE_MAX, RATE_DEFAULT = 140, 280, 190

# Novelty and very old voices that nobody wants reading a research answer.
NOVELTY = {
    "Albert", "Bad News", "Bahh", "Bells", "Boing", "Bubbles", "Cellos", "Wobble", "Fred", "Good News", "Jester",
    "Junior", "Kathy", "Organ", "Superstar", "Ralph", "Trinoids", "Whisper", "Zarvox", "Deranged", "Hysterical",
    "Pipe Organ", "Princess", "Agnes", "Bruce", "Vicki", "Victoria",
}
# Good standard voices, best first, for when no Premium/Enhanced voice is installed.
PREFERRED = ["Samantha", "Daniel", "Karen", "Moira", "Tessa", "Rishi", "Serena", "Alex"]

DEFAULTS = {"mode": "voice", "voice": "", "rate": RATE_DEFAULT, "wake": False}   # wake: listen for "Hey Xod"
MODES = ("off", "voice", "always")   # voice: reply aloud when you asked by talking


# ---------------------------------------------------------------- voices

_voices_cache: Dict[str, Any] = {"at": 0.0, "list": []}


def _parse_voices(out: str) -> List[Dict[str, str]]:
    voices = []
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s{2,}([a-z]{2,3}_[A-Za-z0-9]{2,4})\s+#", line)
        if not m:
            continue
        name, lang = m.group(1).strip(), m.group(2)
        base = re.sub(r"\s*\(.*\)$", "", name)
        if base in NOVELTY:
            continue
        quality = "premium" if "(Premium)" in name else "enhanced" if "(Enhanced)" in name else "standard"
        voices.append({"name": name, "lang": lang, "quality": quality})
    return voices


async def list_voices(refresh: bool = False) -> List[Dict[str, str]]:
    if not refresh and _voices_cache["list"] and time.time() - _voices_cache["at"] < 300:
        return _voices_cache["list"]
    try:
        if WIN:
            proc = await asyncio.create_subprocess_exec(*_PS, _WIN_VOICES, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            voices = []
            for line in out.decode("utf-8", "replace").splitlines():
                if "|" in line:
                    name, culture = line.strip().split("|", 1)
                    voices.append({"name": name, "lang": culture.replace("-", "_"), "quality": "standard"})
            _voices_cache.update(at=time.time(), list=voices)
            return voices
        proc = await asyncio.create_subprocess_exec(SAY, "-v", "?", stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        voices = _parse_voices(out.decode("utf-8", "replace"))
    except Exception as e:
        logger.warning(f"Couldn't list voices: {e}")
        voices = []
    rank = {"premium": 0, "enhanced": 1, "standard": 2}
    voices.sort(key=lambda v: (not v["lang"].startswith("en"), rank[v["quality"]],
                               PREFERRED.index(re.sub(r"\s*\(.*\)$", "", v["name"])) if re.sub(r"\s*\(.*\)$", "", v["name"]) in PREFERRED else 99,
                               v["name"]))
    _voices_cache.update(at=time.time(), list=voices)
    return voices


def _system_lang() -> str:
    lang = os.environ.get("LANG", "en_US").split(".")[0]
    return lang if re.match(r"^[a-z]{2}_[A-Z]{2}$", lang) else "en_US"


async def best_voice() -> str:
    voices = await list_voices()
    if not voices:
        return "Microsoft Zira Desktop" if WIN else "Samantha"
    sys_lang = _system_lang()
    same = [v for v in voices if v["lang"] == sys_lang] or [v for v in voices if v["lang"][:2] == sys_lang[:2]] or voices
    return same[0]["name"]   # already sorted: premium, enhanced, then the good standard ones


# ---------------------------------------------------------------- settings

def get_settings() -> Dict[str, Any]:
    from app.services.storage import get_setting
    try:
        saved = json.loads(get_setting("speech", "") or "{}")
    except Exception:
        saved = {}
    out = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
    if out["mode"] not in MODES:
        out["mode"] = DEFAULTS["mode"]
    out["rate"] = int(min(RATE_MAX, max(RATE_MIN, int(out.get("rate") or RATE_DEFAULT))))
    out["wake"] = bool(out.get("wake"))
    return out


async def save_settings(mode: Optional[str] = None, voice: Optional[str] = None, rate: Optional[int] = None,
                        wake: Optional[bool] = None) -> Dict[str, Any]:
    from app.services.storage import set_setting
    cur = get_settings()
    if mode is not None:
        if mode not in MODES:
            raise ValueError("Unknown mode.")
        cur["mode"] = mode
    if voice is not None:
        if voice and voice not in {v["name"] for v in await list_voices()}:
            raise ValueError("That voice isn't installed on this Mac.")
        cur["voice"] = voice
    if rate is not None:
        cur["rate"] = int(min(RATE_MAX, max(RATE_MIN, int(rate))))
    if wake is not None:
        cur["wake"] = bool(wake)
    set_setting("speech", json.dumps(cur))
    return cur


# ---------------------------------------------------------------- what to say

_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF️‍]")


def speakable(markdown: str) -> str:
    """Markdown as a person would read it out: no symbols, links read as their words, no URLs."""
    t = markdown or ""
    t = re.sub(r"```.*?(```|$)", " ", t, flags=re.S)                    # code blocks are in the reply, not read
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)                           # images
    t = re.sub(r"\[([^\]]+)\]\((?:https?://|/)[^)]*\)", r"\1", t)          # [words](url) -> words
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"^\s*\|.*\|\s*$", " ", t, flags=re.M)                     # table rows
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)                   # headings
    t = re.sub(r"^\s*>\s?", "", t, flags=re.M)                            # quotes
    t = re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", t, flags=re.M)           # list markers
    t = re.sub(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1", r"\2", t)         # emphasis
    t = re.sub(r"\[\d+(?:\s*[,;]\s*\d+)*\]", "", t)                       # citation numbers
    t = _EMOJI.sub("", t)
    t = t.replace("&", " and ").replace("→", " to ").replace("—", ", ").replace("–", " to ")
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)                                # 1,000 -> 1000 reads better
    t = re.sub(r"[*#_|<>]", " ", t)
    t = re.sub(r"\s*\n\s*", ". ", t.strip())
    t = re.sub(r"\s+([.,;:!?)])", r"\1", t)
    t = re.sub(r"\.(\s*\.)+", ".", t)
    t = re.sub(r"([:;,])\s*\.", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" .")
    return (t + ".") if t and t[-1] not in ".!?:" else t


# ---------------------------------------------------------------- synthesis

async def synthesize(text: str, voice: Optional[str] = None, rate: Optional[int] = None) -> Optional[bytes]:
    """WAV audio of `text` (Markdown is cleaned first), or None if there's nothing to say."""
    words = speakable(text)[:MAX_CHARS]
    if not re.search(r"[A-Za-z0-9]", words):
        return None
    settings = get_settings()
    voice = voice or settings["voice"] or await best_voice()
    if voice not in {v["name"] for v in await list_voices()}:
        voice = await best_voice()
    rate = int(min(RATE_MAX, max(RATE_MIN, int(rate or settings["rate"]))))
    key = hashlib.sha256(f"{voice}|{rate}|{words}".encode()).hexdigest()[:32]
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{key}.wav"
    if cached.exists():
        return cached.read_bytes()
    with tempfile.TemporaryDirectory(prefix="xod-say-") as tmp:
        src, out = Path(tmp) / "in.txt", Path(tmp) / "out.wav"
        src.write_text(words, encoding="utf-8")
        # Arguments, never a shell: the text is read from a file, so nothing in it can run.
        if WIN:
            proc = await asyncio.create_subprocess_exec(*_PS, _WIN_SPEAK, env=_win_env(voice, _win_rate(rate), out, src),
                                                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        else:
            proc = await asyncio.create_subprocess_exec(
                SAY, "-v", voice, "-r", str(rate), "--file-format=WAVE", "--data-format=LEI16@22050",
                "-o", str(out), "-f", str(src), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("The voice took too long.")
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"The voice failed: {err.decode('utf-8', 'replace')[:200]}")
        data = out.read_bytes()
    try:
        cached.write_bytes(data)
        _trim_cache()
    except Exception:
        pass
    return data


def _trim_cache(keep: int = 300) -> None:
    files = sorted(CACHE.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    for f in files[:-keep]:
        f.unlink(missing_ok=True)


async def speak_out_loud(text: str, voice: Optional[str] = None) -> bool:
    """Plays text through the Mac's speakers (for the hands-free voice commands, which have no page to play in)."""
    words = speakable(text)[:MAX_CHARS]
    if not words:
        return False
    voice = voice if voice and voice in {v["name"] for v in await list_voices()} else (get_settings()["voice"] or await best_voice())
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(words)
        path = f.name
    if WIN:
        proc = await asyncio.create_subprocess_exec(*_PS, _WIN_SPEAK, env=_win_env(voice, _win_rate(get_settings()["rate"]), "", path),
                                                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    else:
        proc = await asyncio.create_subprocess_exec(SAY, "-v", voice, "-r", str(get_settings()["rate"]), "-f", path,
                                                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)

    async def _cleanup():
        try:
            await asyncio.wait_for(proc.wait(), timeout=120)
        except Exception:
            proc.kill()
        os.unlink(path)
    asyncio.create_task(_cleanup())
    return True


VOICE_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.universalaccess?SpokenContent"


async def open_voice_settings() -> bool:
    if WIN:
        try:
            os.startfile("ms-settings:speech")     # Settings → Time & language → Speech (more voices)
            return True
        except Exception:
            return False
    proc = await asyncio.create_subprocess_exec("/usr/bin/open", VOICE_SETTINGS_URL,
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    return (await proc.wait()) == 0


async def status() -> Dict[str, Any]:
    voices = await list_voices()
    best = await best_voice()
    s = get_settings()
    return {"settings": s, "voice": s["voice"] or best, "best": best, "voices": voices,
            "has_natural": any(v["quality"] != "standard" and v["lang"].startswith("en") for v in voices),
            "rate": {"min": RATE_MIN, "max": RATE_MAX, "default": RATE_DEFAULT}}


_preparing: Optional[asyncio.Task] = None


def prepare_lines(texts: List[str]) -> int:
    """Makes the audio for short lines it may need to say instantly ("Let me look that up.") in the
    background, one at a time, so the first time it says one there's no wait."""
    global _preparing
    todo = [t for t in texts if isinstance(t, str) and 0 < len(t) <= 200][:20]

    async def run():
        for t in todo:
            try:
                await synthesize(t)
            except Exception:
                pass
    if _preparing and not _preparing.done():
        return 0
    _preparing = asyncio.create_task(run())
    return len(todo)
