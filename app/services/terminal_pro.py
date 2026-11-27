"""Deep Terminal Engine for Xod:
1. Persistent interactive PTY shell sessions (keeps cwd, env vars, virtualenvs, REPLs alive).
2. Smart curl-backed file & archive downloader (retry, resume, SHA-256 verification, auto-extract).
3. CLI Toolchain Inspector & curated Package Installer (brew / uv / npm).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import pty
import re
import select
import shlex
import shutil
import signal
import subprocess
import tarfile
import threading
import time
import urllib.parse
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\r")

# Essential terminal CLI tools Xod checks and can install on demand
TOOLCHAIN_CATALOG: Dict[str, Dict[str, str]] = {
    "curl": {"brew": "curl", "category": "network", "desc": "HTTP/API client & downloader"},
    "wget": {"brew": "wget", "category": "network", "desc": "Recursive file downloader"},
    "http": {"brew": "httpie", "category": "network", "desc": "Human-friendly HTTP CLI (HTTPie)"},
    "git": {"brew": "git", "category": "dev", "desc": "Version control"},
    "gh": {"brew": "gh", "category": "dev", "desc": "GitHub CLI"},
    "jq": {"brew": "jq", "category": "data", "desc": "JSON processor"},
    "yq": {"brew": "yq", "category": "data", "desc": "YAML/JSON/XML processor"},
    "rg": {"brew": "ripgrep", "category": "search", "desc": "Ultra-fast code search (ripgrep)"},
    "fd": {"brew": "fd", "category": "search", "desc": "Fast file finder"},
    "fzf": {"brew": "fzf", "category": "search", "desc": "Fuzzy finder"},
    "tree": {"brew": "tree", "category": "files", "desc": "Directory tree viewer"},
    "bat": {"brew": "bat", "category": "files", "desc": "Syntax-highlighted cat"},
    "rsync": {"brew": "rsync", "category": "files", "desc": "Fast incremental file sync"},
    "node": {"brew": "node", "category": "runtime", "desc": "Node.js runtime"},
    "npm": {"brew": "node", "category": "runtime", "desc": "Node package manager"},
    "bun": {"brew": "oven-sh/bun/bun", "category": "runtime", "desc": "Fast JS/TS runtime"},
    "python3": {"brew": "python", "category": "runtime", "desc": "Python 3 runtime"},
    "uv": {"brew": "uv", "category": "runtime", "desc": "Fast Python package & tool manager"},
    "ffmpeg": {"brew": "ffmpeg", "category": "media", "desc": "Audio/video processing"},
    "yt-dlp": {"brew": "yt-dlp", "category": "media", "desc": "Media downloader"},
    "sqlite3": {"brew": "sqlite", "category": "data", "desc": "SQLite CLI"},
    "docker": {"brew": "docker", "category": "infra", "desc": "Container CLI"},
    "tmux": {"brew": "tmux", "category": "terminal", "desc": "Terminal multiplexer"},
    "adb": {"brew": "android-platform-tools", "category": "mobile", "desc": "Android Debug Bridge"},
    "tailscale": {"brew": "tailscale", "category": "network", "desc": "Tailscale mesh VPN CLI"},
    "brew": {"brew": "", "category": "pkg", "desc": "Homebrew package manager"},
}

_SAFE_PKG_NAME_RE = re.compile(r"^[A-Za-z0-9@._/+-]{1,80}$")


def _tool_path() -> str:
    try:
        from app.services.agent_clis import search_path
        return search_path()
    except Exception:
        return os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s or "")


# ------------------------------------------------------------------ 1. Toolchain Doctor & Installer

def toolchain_status() -> Dict[str, Any]:
    """Fast check of which CLI tools and package managers are installed on this machine."""
    path_val = _tool_path()
    installed: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    for name, meta in TOOLCHAIN_CATALOG.items():
        loc = shutil.which(name, path=path_val)
        if loc:
            installed[name] = {"path": loc, "category": meta["category"], "desc": meta["desc"]}
        else:
            missing.append(name)
    managers = {
        m: bool(shutil.which(m, path=path_val))
        for m in ("brew", "uv", "pip3", "npm", "pnpm", "bun", "cargo")
    }
    return {
        "ok": True,
        "installed_count": len(installed),
        "total_catalog": len(TOOLCHAIN_CATALOG),
        "installed": installed,
        "missing": missing,
        "managers": managers,
        "shell": "/bin/zsh" if os.path.exists("/bin/zsh") else os.environ.get("SHELL", "/bin/sh"),
    }


async def install_cli_tool(tool_name: str, manager: str = "auto") -> Dict[str, Any]:
    """Install a missing CLI tool via Homebrew, uv, or npm cleanly (no unverified curl|sh)."""
    raw = str(tool_name or "").strip()
    if not raw or not _SAFE_PKG_NAME_RE.match(raw):
        return {"ok": False, "error": f"Invalid tool/package name: {raw!r}"}

    path_val = _tool_path()
    env = dict(os.environ, PATH=path_val, HOMEBREW_NO_AUTO_UPDATE="1")

    meta = TOOLCHAIN_CATALOG.get(raw)
    pkg = (meta.get("brew") if meta else "") or raw

    chosen = (manager or "auto").strip().lower()
    if chosen == "auto":
        if shutil.which("brew", path=path_val):
            chosen = "brew"
        elif shutil.which("uv", path=path_val):
            chosen = "uv"
        elif shutil.which("npm", path=path_val):
            chosen = "npm"
        else:
            return {"ok": False, "error": "No supported package manager (brew, uv, npm) found on PATH."}

    if chosen == "brew":
        argv = ["brew", "install", pkg]
    elif chosen == "uv":
        argv = ["uv", "tool", "install", raw]
    elif chosen == "npm":
        argv = ["npm", "install", "-g", raw]
    elif chosen == "pip":
        argv = [shutil.which("pip3", path=path_val) or "pip3", "install", "--user", raw]
    else:
        return {"ok": False, "error": f"Unsupported package manager: {chosen}"}

    t0 = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=300)
        out = _strip_ansi(out_b.decode("utf-8", errors="replace"))[-4000:]
        err = _strip_ansi(err_b.decode("utf-8", errors="replace"))[-4000:]
        loc = shutil.which(raw, path=_tool_path())
        return {
            "ok": proc.returncode == 0,
            "tool": raw,
            "package": pkg,
            "manager": chosen,
            "command": " ".join(shlex.quote(a) for a in argv),
            "exit_code": proc.returncode,
            "installed_path": loc or "",
            "duration_sec": round(time.time() - t0, 2),
            "stdout": out,
            "stderr": err,
        }
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"Install timed out after 300s ({chosen} {pkg})."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ 2. Smart curl Downloader

async def download_file(
    url: str,
    dest_path: str = "",
    resume: bool = True,
    expected_sha256: str = "",
    extract: bool = False,
    headers: Optional[Dict[str, str]] = None,
    timeout_sec: int = 300,
) -> Dict[str, Any]:
    """Download a file or archive via curl (with retry, resume, SHA-256 verification, and optional extract)."""
    u = str(url or "").strip()
    parsed = urllib.parse.urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"ok": False, "error": f"Invalid HTTP(S) URL: {u!r}"}

    # Resolve target destination
    if dest_path and dest_path.strip():
        target = Path(os.path.expanduser(dest_path.strip())).resolve()
        if target.exists() and target.is_dir():
            fname = os.path.basename(parsed.path) or "download.bin"
            target = target / fname
    else:
        fname = os.path.basename(parsed.path) or f"download-{uuid.uuid4().hex[:8]}.bin"
        target = (Path.home() / "Downloads" / fname).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    path_val = _tool_path()
    curl_bin = shutil.which("curl", path=path_val)
    t0 = time.time()

    if curl_bin:
        argv = [curl_bin, "-fSL", "--retry", "3", "--retry-delay", "1", "--connect-timeout", "20"]
        if resume and target.exists():
            argv.extend(["-C", "-"])
        if isinstance(headers, dict):
            for k, v in headers.items():
                if k and v is not None:
                    argv.extend(["-H", f"{k}: {v}"])
        argv.extend(["-o", str(target), u])
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(os.environ, PATH=path_val),
            )
            _, err_b = await asyncio.wait_for(proc.communicate(), timeout=max(30, int(timeout_sec)))
            if proc.returncode != 0:
                err_txt = _strip_ansi(err_b.decode("utf-8", errors="replace"))[-1500:]
                return {"ok": False, "url": u, "exit_code": proc.returncode, "error": err_txt or f"curl failed (code {proc.returncode})"}
        except asyncio.TimeoutError:
            return {"ok": False, "url": u, "error": f"Download timed out after {timeout_sec}s."}
        except Exception as e:
            return {"ok": False, "url": u, "error": str(e)}
    else:
        # Pure-Python fallback via httpx
        try:
            import httpx
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_sec) as client:
                async with client.stream("GET", u, headers=headers or {}) as resp:
                    resp.raise_for_status()
                    with open(target, "wb") as f:
                        async for chunk in resp.aiter_bytes(65536):
                            f.write(chunk)
        except Exception as e:
            return {"ok": False, "url": u, "error": str(e)}

    if not target.exists():
        return {"ok": False, "url": u, "error": "Download completed but file not found on disk."}

    # Compute SHA-256
    h = hashlib.sha256()
    with open(target, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    actual_sha = h.hexdigest()
    size_bytes = target.stat().st_size

    exp = (expected_sha256 or "").strip().lower()
    if exp and actual_sha.lower() != exp:
        return {
            "ok": False,
            "url": u,
            "path": str(target),
            "size_bytes": size_bytes,
            "sha256": actual_sha,
            "error": f"SHA-256 mismatch: expected {exp}, got {actual_sha}",
        }

    extracted_dir = ""
    if extract:
        extracted_dir = _extract_archive(target)

    return {
        "ok": True,
        "url": u,
        "path": str(target),
        "size_bytes": size_bytes,
        "sha256": actual_sha,
        "sha256_verified": bool(exp),
        "extracted_dir": extracted_dir,
        "duration_sec": round(time.time() - t0, 2),
        "engine": "curl" if curl_bin else "httpx",
    }


def _extract_archive(archive: Path) -> str:
    """Safely extract .zip or .tar.* into a sibling directory (blocks path traversal)."""
    name = archive.name.lower()
    stem = archive.name
    for suf in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".zip"):
        if name.endswith(suf):
            stem = archive.name[: -len(suf)]
            break
    out_dir = (archive.parent / stem).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if name.endswith(".zip"):
        with zipfile.ZipFile(archive, "r") as zf:
            for member in zf.namelist():
                dest = (out_dir / member).resolve()
                if not str(dest).startswith(str(out_dir)):
                    raise ValueError(f"Blocked unsafe zip path: {member}")
            zf.extractall(out_dir)
        return str(out_dir)

    if any(name.endswith(s) for s in (".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar")):
        with tarfile.open(archive, "r:*") as tf:
            for m in tf.getmembers():
                dest = (out_dir / m.name).resolve()
                if not str(dest).startswith(str(out_dir)):
                    raise ValueError(f"Blocked unsafe tar path: {m.name}")
            tf.extractall(out_dir)
        return str(out_dir)

    return ""


# ------------------------------------------------------------------ 3. Persistent Interactive PTY Sessions

class _PtySession:
    MAX_BUF = 65536

    def __init__(self, session_id: str, cwd: str, shell: str):
        self.id = session_id
        self.cwd = cwd
        self.shell = shell
        self.created_at = time.time()
        self.last_activity = self.created_at
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._closed = False

        master_fd, slave_fd = pty.openpty()
        self.master_fd = master_fd
        env = dict(
            os.environ,
            PATH=_tool_path(),
            TERM="xterm-256color",
            CLICOLOR="0",
            PYTHONDONTWRITEBYTECODE="1",
        )
        self.proc = subprocess.Popen(
            [shell, "-i"],
            cwd=cwd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave_fd)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        while not self._closed:
            try:
                r, _, _ = select.select([self.master_fd], [], [], 0.2)
                if not r:
                    if self.proc.poll() is not None:
                        break
                    continue
                chunk = os.read(self.master_fd, 4096)
                if not chunk:
                    break
                with self._lock:
                    self._buf.extend(chunk)
                    if len(self._buf) > self.MAX_BUF:
                        del self._buf[: len(self._buf) - self.MAX_BUF]
                    self.last_activity = time.time()
            except OSError:
                break

    def write(self, data: str) -> None:
        if self._closed or self.proc.poll() is not None:
            raise RuntimeError("PTY session is closed.")
        os.write(self.master_fd, data.encode("utf-8"))
        self.last_activity = time.time()

    def read(self, clear: bool = False, strip_ansi: bool = True, max_chars: int = 8000) -> str:
        with self._lock:
            raw = bytes(self._buf)
            if clear:
                self._buf.clear()
        text = raw.decode("utf-8", errors="replace")
        if strip_ansi:
            text = _strip_ansi(text)
        if len(text) > max_chars:
            text = text[-max_chars:]
        return text

    def alive(self) -> bool:
        return (not self._closed) and (self.proc.poll() is None)

    def close(self) -> None:
        self._closed = True
        try:
            if self.proc.poll() is None:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except Exception:
            try:
                self.proc.terminate()
            except Exception:
                pass
        try:
            os.close(self.master_fd)
        except Exception:
            pass

    def info(self) -> Dict[str, Any]:
        return {
            "session_id": self.id,
            "cwd": self.cwd,
            "shell": self.shell,
            "pid": self.proc.pid,
            "alive": self.alive(),
            "created_at": self.created_at,
            "last_activity": self.last_activity,
        }


class PtyManager:
    MAX_SESSIONS = 6

    def __init__(self) -> None:
        self._sessions: Dict[str, _PtySession] = {}
        self._lock = threading.Lock()

    def open(self, cwd: str = "", shell: str = "") -> Dict[str, Any]:
        target_cwd = os.path.expanduser(cwd) if cwd else os.path.expanduser("~")
        if not os.path.isdir(target_cwd):
            target_cwd = os.path.expanduser("~")
        sh = shell.strip() if shell and os.path.exists(shell.strip()) else ("/bin/zsh" if os.path.exists("/bin/zsh") else "/bin/sh")

        with self._lock:
            # Prune dead sessions first
            for sid in [k for k, s in self._sessions.items() if not s.alive()]:
                self._sessions.pop(sid, None)
            if len(self._sessions) >= self.MAX_SESSIONS:
                oldest = min(self._sessions.values(), key=lambda s: s.last_activity)
                oldest.close()
                self._sessions.pop(oldest.id, None)
            sid = f"pty-{uuid.uuid4().hex[:8]}"
            sess = _PtySession(sid, target_cwd, sh)
            self._sessions[sid] = sess
        time.sleep(0.2)
        return {"ok": True, **sess.info(), "output": sess.read(clear=True)}

    def send(self, session_id: str, text: str, press_enter: bool = True, wait_sec: float = 0.8) -> Dict[str, Any]:
        with self._lock:
            sess = self._sessions.get(session_id)
        if not sess:
            return {"ok": False, "error": f"PTY session '{session_id}' not found."}
        if not sess.alive():
            return {"ok": False, "error": f"PTY session '{session_id}' has exited."}
        payload = text + ("\n" if press_enter else "")
        sess.write(payload)
        time.sleep(max(0.1, min(float(wait_sec), 10.0)))
        return {"ok": True, "session_id": session_id, "alive": sess.alive(), "output": sess.read(clear=True)}

    def read(self, session_id: str, clear: bool = False) -> Dict[str, Any]:
        with self._lock:
            sess = self._sessions.get(session_id)
        if not sess:
            return {"ok": False, "error": f"PTY session '{session_id}' not found."}
        return {"ok": True, "session_id": session_id, "alive": sess.alive(), "output": sess.read(clear=clear)}

    def close(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            sess = self._sessions.pop(session_id, None)
        if not sess:
            return {"ok": False, "error": f"PTY session '{session_id}' not found."}
        sess.close()
        return {"ok": True, "session_id": session_id, "closed": True}

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [s.info() for s in self._sessions.values()]


pty_manager = PtyManager()
