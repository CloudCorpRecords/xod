"""
Media Studio's libraries as a one-time download, in two groups:

  "media": PyTorch, diffusers… for making images and video with AI (about 600 MB installed).
  "video": ffmpeg (via imageio-ffmpeg) for trimming/joining video and saving downloads (~25 MB).
           Its ffmpeg build is GPL, so Xod.app doesn't ship it; it's fetched from PyPI on first use.

The Mac app ships without them to keep the download small and the license simple. The first
time something needs them, they're installed with the app's own pip into the data folder, at the
exact versions pinned in requirements.txt, and put on the path. From source, .venv has them all.
"""
import importlib.util
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import APP_ROOT, DATA_DIR

logger = logging.getLogger("xod.media")

# The libraries left out of the app, plus what only they depend on. Versions come from requirements.txt.
GROUPS: Dict[str, Dict[str, Any]] = {
    "media": {"packages": ["torch", "diffusers", "accelerate", "peft", "sympy", "networkx", "mpmath"],
              "probe": ["torch", "diffusers"], "download_mb": 230, "needs": ["video"]},
    "video": {"packages": ["imageio-ffmpeg"], "probe": ["imageio_ffmpeg"], "download_mb": 25, "needs": []},
}
MEDIA_PACKAGES = [p for g in GROUPS.values() for p in g["packages"]]
BASE = DATA_DIR / "packages"
PACKAGES_DIR = BASE / "media"
DOWNLOAD_MB = GROUPS["media"]["download_mb"]

_states: Dict[str, Dict[str, Any]] = {g: {"status": "idle", "error": None, "line": ""} for g in GROUPS}
_lock = threading.Lock()


def _requirements_file() -> Path:
    for p in (APP_ROOT / "requirements.txt", Path(sys.prefix) / "XOD_REQUIREMENTS"):
        if p.exists():
            return p
    raise FileNotFoundError("requirements.txt not found")


def pinned(group: str = "media") -> List[str]:
    """The pinned requirement lines for a group, e.g. ['torch==2.14.1', …]."""
    want = GROUPS[group]["packages"]
    out = []
    for line in _requirements_file().read_text().splitlines():
        spec = line.split("#")[0].strip()
        name = re.split(r"[=<>!~\[ ]", spec, 1)[0].lower() if spec else ""
        if name in want:
            out.append(spec)
    return out


def _dir(group: str) -> Path:
    return BASE / group


def _on_path() -> None:
    for g in GROUPS:
        d = str(_dir(g))
        if _dir(g).exists() and d not in sys.path:
            sys.path.append(d)


def installed(group: str = "media") -> bool:
    _on_path()
    return all(installed(n) for n in GROUPS[group]["needs"]) and \
        all(importlib.util.find_spec(m) is not None for m in GROUPS[group]["probe"])


def ffmpeg_exe() -> Optional[str]:
    """ffmpeg from imageio-ffmpeg (in .venv, or downloaded), else one already on this Mac, else None."""
    _on_path()
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        return shutil.which("ffmpeg") or next((p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg") if os.path.exists(p)), None)


def worker_env(env: Dict[str, str]) -> Dict[str, str]:
    """Environment for the media worker: adds the downloaded libraries to its path."""
    dirs = [str(_dir(g)) for g in GROUPS if _dir(g).exists()]
    if dirs:
        env = dict(env)
        env["PYTHONPATH"] = os.pathsep.join([p for p in [env.get("PYTHONPATH")] if p] + dirs)
    return env


def status(group: str = "media") -> Dict[str, Any]:
    ready = installed(group)
    st = _states[group]
    if any(_states[n]["status"] == "installing" for n in GROUPS[group]["needs"]):
        st = {**st, "status": "installing"}
    return {"installed": ready, "status": "done" if ready and st["status"] != "installing" else st["status"],
            "error": st["error"], "progress": st["line"],
            "download_mb": GROUPS[group]["download_mb"] + sum(GROUPS[n]["download_mb"] for n in GROUPS[group]["needs"] if not installed(n))}


def _install(group: str) -> None:
    _state = _states[group]
    try:
        for need in GROUPS[group]["needs"]:
            if not installed(need):
                _states[need]["status"] = "installing"
                _install(need)
                if not installed(need):
                    raise RuntimeError(_states[need]["error"] or f"{need} didn't install")
        target = _dir(group)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f"{group}-", dir=target.parent))
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("\n".join(pinned(group)) + "\n")
            reqs = f.name
        # --no-deps: everything else these need is already inside the app, at the right versions.
        cmd = [sys.executable, "-m", "pip", "install", "--no-deps", "--only-binary=:all:",
               "--disable-pip-version-check", "--no-input", "--target", str(tmp), "-r", reqs]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            if line.strip():
                _state["line"] = line.strip()[:160]
        code = proc.wait()
        os.unlink(reqs)
        if code != 0:
            raise RuntimeError(f"pip exited with {code}: {_state['line']}")
        # Swap in only once complete, so a cancelled download never leaves a half-installed folder.
        if target.exists():
            import shutil
            shutil.rmtree(target)
        tmp.rename(target)
        importlib.invalidate_caches()
        _state.update(status="done", error=None, line="")
        logger.info(f"Media Studio libraries installed ({group})")
    except Exception as e:
        _state.update(status="error", error=str(e)[:300])
        logger.warning(f"Installing Media Studio libraries ({group}) failed: {e}")


def start_install(group: str = "media") -> Dict[str, Any]:
    """Starts the one-time download in the background (no-op if already there or running)."""
    with _lock:
        if installed(group):
            return status(group)
        if _states[group]["status"] != "installing":
            _states[group].update(status="installing", error=None, line="Starting download…")
            threading.Thread(target=_install, args=(group,), daemon=True, name=f"packages-{group}").start()
    return status(group)


async def ensure(group: str = "media", timeout: float = 1800) -> None:
    """Waits for the libraries, starting the download if needed. Raises with a clear message on failure."""
    import asyncio
    if installed(group):
        return
    start_install(group)
    waited = 0.0
    while _states[group]["status"] == "installing" and waited < timeout:
        await asyncio.sleep(1)
        waited += 1
    if not installed(group):
        err = _states[group]["error"]
        raise RuntimeError("Media Studio's tools couldn't be installed"
                           + (f": {err}" if err else " in time") + ". Check your connection and try again.")
