"""
The `xod` command for your terminal, installed from the app (Tools → Terminal command), so typing `xod` anywhere
starts Xod in the terminal (app/cli.py) with the same chats, models and keys as the app.

What it puts where:
  Mac / Linux  a small `xod` script in a folder your terminal already looks in (~/.local/bin, /opt/homebrew/bin or
               /usr/local/bin, the first that's on your PATH and writable). If none is, ~/.local/bin, and one line in
               your shell's profile (~/.zprofile or ~/.bash_profile) adds it to PATH; new terminals see it.
  Windows      xod.cmd in %LOCALAPPDATA%\\Microsoft\\WindowsApps, which Windows keeps on everyone's PATH.
The script runs Xod with the app's own Python (inside Xod.app, or the checkout's .venv), so nothing else is installed.
It never replaces an `xod` that isn't Xod's own; removing it deletes only the file it wrote (and its PATH line).
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import APP_ROOT

MARK = "Installed by Xod"
WIN = sys.platform == "win32"
HOME = Path.home()
PROFILE_LINE = 'export PATH="$HOME/.local/bin:$PATH"  # added by Xod for the xod command'
CANDIDATES = [HOME / ".local" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")]     # first on PATH and writable wins


def _packaged() -> bool:
    return os.environ.get("XOD_PACKAGED") == "1" or "/Xod.app/Contents/Resources/" in str(APP_ROOT) or \
        "\\resources\\backend" in str(APP_ROOT).lower()


def _script() -> str:
    """What the installed `xod` runs: the app's Python and code, or the checkout's own ./xod."""
    py = sys.executable
    if WIN:
        return ("@echo off\r\nrem " + MARK + " (Tools > Terminal command). Runs Xod in the terminal.\r\n"
                "set PYTHONUTF8=1\r\n" + f'set "PYTHONPATH={APP_ROOT}"\r\n' + f'"{py}" -m app.cli %*\r\n')
    if not _packaged() and (APP_ROOT / "xod").exists():
        return f'#!/bin/sh\n# {MARK} (Tools > Terminal command). Runs Xod in the terminal from this checkout.\nexec "{APP_ROOT / "xod"}" "$@"\n'
    return (f'#!/bin/sh\n# {MARK} (Tools > Terminal command). Runs Xod in the terminal with the app\'s own Python.\n'
            f'PYTHONUTF8=1 PYTHONPATH="{APP_ROOT}" exec "{py}" -m app.cli "$@"\n')


def shell_path() -> List[str]:
    """The PATH a new terminal gets (your login shell's), not this app's (which is much shorter)."""
    if WIN:
        return os.environ.get("PATH", "").split(os.pathsep)
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        r = subprocess.run([shell, "-lic", 'printf %s "$PATH"'], capture_output=True, text=True, timeout=8, stdin=subprocess.DEVNULL)
        parts = [p for p in r.stdout.strip().split(":") if p.startswith("/")]
        if parts:
            return parts
    except Exception:
        pass
    return os.environ.get("PATH", "").split(os.pathsep)


def _profile() -> Path:
    shell = os.path.basename(os.environ.get("SHELL") or "/bin/zsh")
    return HOME / (".zprofile" if shell == "zsh" else ".bash_profile" if shell == "bash" else ".profile")


def _where() -> Dict[str, Any]:
    """Where `xod` goes, and whether that's already on PATH."""
    if WIN:
        d = Path(os.environ.get("LOCALAPPDATA") or (HOME / "AppData" / "Local")) / "Microsoft" / "WindowsApps"
        return {"dir": d, "file": d / "xod.cmd", "on_path": True}
    path = [os.path.realpath(p) for p in shell_path()]
    for d in CANDIDATES:
        if os.path.realpath(str(d)) in path and d.is_dir() and os.access(d, os.W_OK):
            return {"dir": d, "file": d / "xod", "on_path": True}
    d = HOME / ".local" / "bin"
    return {"dir": d, "file": d / "xod", "on_path": False}


def _ours(f: Path) -> bool:
    try:
        return MARK in f.read_text(errors="replace")[:400]
    except OSError:
        return False


def _found_elsewhere() -> Optional[str]:
    """An `xod` on PATH that isn't the one Xod installs (a checkout's, or something else called xod)."""
    hit = shutil.which("xod", path=os.pathsep.join(shell_path()))
    if hit and not _ours(Path(hit)) and os.path.realpath(hit) != os.path.realpath(str(APP_ROOT / "xod")):
        return hit
    return None


def status() -> Dict[str, Any]:
    w = _where()
    f: Path = w["file"]
    installed = f.exists() and _ours(f)
    current = installed and f.read_text(errors="replace").replace("\r\n", "\n") == _script().replace("\r\n", "\n")
    return {"installed": installed, "current": current, "path": str(f), "on_path": w["on_path"] or _profile_has_line(),
            "other": _found_elsewhere(), "command": "xod",
            "how": "Open a new terminal window and type xod (xod \"a question\", xod status)."}


def _profile_has_line() -> bool:
    try:
        return PROFILE_LINE in _profile().read_text()
    except OSError:
        return False


def install() -> Dict[str, Any]:
    w = _where()
    f: Path = w["file"]
    if f.exists() and not _ours(f):
        return {"success": False, "error": f"There's already a different `xod` at {f}. Xod leaves it alone: remove or rename it first."}
    w["dir"].mkdir(parents=True, exist_ok=True)
    f.write_text(_script(), newline="" if WIN else None)
    if not WIN:
        f.chmod(0o755)
    added_line = False
    if not w["on_path"] and not _profile_has_line():
        prof = _profile()
        with open(prof, "a") as fh:
            fh.write(("\n" if prof.exists() and prof.read_text()[-1:] not in ("", "\n") else "") + PROFILE_LINE + "\n")
        added_line = True
    msg = f"Installed: open a new terminal window and type xod. (It's {f}"
    msg += f"; Xod also added {w['dir']} to your PATH in {_profile().name})" if added_line else ")"
    other = _found_elsewhere()
    if other and os.path.realpath(other) != os.path.realpath(str(f)):
        msg += f" Note: another xod at {other} comes first on your PATH."
    return {"success": True, **status(), "added_to_profile": added_line, "message": msg}


def uninstall() -> Dict[str, Any]:
    w = _where()
    f: Path = w["file"]
    removed = False
    if f.exists():
        if not _ours(f):
            return {"success": False, "error": f"The xod at {f} isn't the one Xod installed, so Xod leaves it alone."}
        f.unlink()
        removed = True
    prof = _profile()
    if _profile_has_line():
        prof.write_text("".join(l for l in prof.read_text().splitlines(True) if PROFILE_LINE not in l))
    return {"success": True, "removed": removed, "message": "Removed the xod command." if removed else "It wasn't installed."}
