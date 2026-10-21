"""
Other AI agents that live in the terminal (Muse, Claude Code, Codex, Gemini CLI, Aider, opencode, Goose, Cursor,
Amp): Xod finds them, signs you in to them, and uses them, straight from a terminal.

  find()       which are installed, where, and whether you're signed in. Looks in your login shell's PATH as well as
               the usual install folders: the desktop app starts with a short PATH that misses ~/.local/bin and npm's.
  run()        one prompt, headless (muse exec, claude -p, codex exec …), in a folder; returns the answer
  login()      starts the agent's own sign-in, shows you its link and code and opens the page in your browser; you
               approve there. Xod never types a password: these all sign in with the browser or a device code.
  sessions     an agent left running for a back-and-forth (start / send / read / stop), in a terminal Xod holds, or
               in a Terminal window you can watch (visible=True, with the screen add-on)

Safety: these agents run commands on this computer with their own permission prompts. Xod never passes the flags
that switch those off (--dangerously-skip-permissions, --yolo, --yes-always …), and what it asks them goes through
the same safety check as a command Xod runs itself (sentinel: run).
"""
import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xod.agent_clis")

WIN = sys.platform == "win32"
HOME = Path.home()

# What each agent is called, how to give it one prompt headless, how it signs in, and where its sign-in is kept
# (only whether the file exists is checked; it's never read). "check" is a word its --help must show for the headless
# form to be used, so a version without it is told plainly instead of hanging.
AGENTS: Dict[str, Dict[str, Any]] = {
    "muse": {"name": "Muse (Meta)", "bins": ["muse"], "run": ["exec", "{prompt}"], "check": "exec", "workspace_flag": "--workspace",
             "login": ["login"], "signed_in": [".config/muse/auth.json"], "key_env": "META_API_KEY",
             "auth_hosts": ("meta.com", "meta.ai", "facebook.com"), "install": "curl -fsSL https://muse.meta.ai/install.sh | bash"},
    "claude": {"name": "Claude Code", "bins": ["claude"], "run": ["-p", "{prompt}"], "check": "--print", "login": None,
               "signed_in": [".claude/.credentials.json"], "key_env": "ANTHROPIC_API_KEY",
               "auth_hosts": ("claude.ai", "anthropic.com", "claude.com"), "install": "npm install -g @anthropic-ai/claude-code"},
    "codex": {"name": "Codex (OpenAI)", "bins": ["codex"], "run": ["exec", "{prompt}"], "check": "exec", "login": ["login"],
              "signed_in": [".codex/auth.json"], "key_env": "OPENAI_API_KEY",
              "auth_hosts": ("openai.com", "chatgpt.com"), "install": "npm install -g @openai/codex"},
    "gemini": {"name": "Gemini CLI", "bins": ["gemini"], "run": ["-p", "{prompt}"], "check": "--prompt", "login": None,
               "signed_in": [".gemini/oauth_creds.json"], "key_env": "GEMINI_API_KEY",
               "auth_hosts": ("google.com",), "install": "npm install -g @google/gemini-cli"},
    "aider": {"name": "Aider", "bins": ["aider"], "run": ["--message", "{prompt}"], "check": "--message", "login": None,
              "signed_in": [], "key_env": "", "auth_hosts": (), "install": "python -m pip install aider-install && aider-install"},
    "opencode": {"name": "opencode", "bins": ["opencode"], "run": ["run", "{prompt}"], "check": "run", "login": ["auth", "login"],
                 "signed_in": [".local/share/opencode/auth.json"], "key_env": "", "auth_hosts": (), "install": "npm install -g opencode-ai"},
    "goose": {"name": "Goose", "bins": ["goose"], "run": ["run", "-t", "{prompt}"], "check": "run", "login": ["configure"],
              "signed_in": [".config/goose/config.yaml"], "key_env": "", "auth_hosts": (), "install": "brew install block-goose-cli"},
    "cursor": {"name": "Cursor Agent", "bins": ["cursor-agent"], "run": ["-p", "{prompt}"], "check": "--print", "login": ["login"],
               "signed_in": [], "key_env": "CURSOR_API_KEY", "auth_hosts": ("cursor.com",), "install": "curl https://cursor.com/install -fsS | bash"},
    "amp": {"name": "Amp", "bins": ["amp"], "run": ["-x", "{prompt}"], "check": "-x", "login": ["login"],
            "signed_in": [".local/share/amp/secrets.json"], "key_env": "AMP_API_KEY",
            "auth_hosts": ("ampcode.com",), "install": "npm install -g @sourcegraph/amp"},
}
# Flags that switch an agent's own safety prompts off: never passed, whoever asks.
UNSAFE_FLAGS = re.compile(r"--dangerously|--yolo|--yes-always|--full-auto|--bypass|--no-sandbox|--skip-permissions|"
                          r"--allow-all|-y\b|--auto-approve|--trust-all", re.I)
EXTRA_DIRS = [".local/bin", ".npm-global/bin", ".bun/bin", ".cargo/bin", ".deno/bin", "go/bin", ".volta/bin", "bin",
              "Library/pnpm", ".yarn/bin"]
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>]|\r")
URL_RE = re.compile(r"https://[^\s\"'<>)\]]+")
CODE_RE = re.compile(r"(?:code|Code|CODE)[^\n]{0,40}?\n?\s*([A-Z0-9]{4,5}(?:-[A-Z0-9]{4,5})+)|^\s*([A-Z0-9]{4}-[A-Z0-9]{4})\s*$", re.M)

_path_cache: Dict[str, Any] = {"at": 0.0, "path": ""}
_help_cache: Dict[str, str] = {}
_logins: Dict[str, Dict[str, Any]] = {}
_sessions: Dict[str, "Session"] = {}
COLS, ROWS = 140, 45
RETURN_PAUSE = 0.8       # seconds between typing a message into an agent and pressing Return
MAX_SESSIONS = 4         # agent sessions Xod keeps running in the background at once


class CliError(Exception):
    pass


# ------------------------------------------------------------------ finding them
def search_path() -> str:
    """Your login shell's PATH (what a terminal sees) plus the usual install folders, without duplicates."""
    if time.time() - _path_cache["at"] < 300 and _path_cache["path"]:
        return _path_cache["path"]
    parts: List[str] = []
    if not WIN:
        shell = os.environ.get("SHELL") or "/bin/zsh"
        try:
            r = subprocess.run([shell, "-lic", "printf %s \"$PATH\""], capture_output=True, text=True, timeout=8,
                               stdin=subprocess.DEVNULL)
            parts += [p for p in r.stdout.strip().split(":") if p.startswith("/")]
        except Exception:
            pass
    parts += os.environ.get("PATH", "").split(os.pathsep)
    parts += [str(HOME / d) for d in EXTRA_DIRS] + (["/opt/homebrew/bin", "/usr/local/bin"] if not WIN else
                                                    [str(HOME / "AppData/Roaming/npm"), str(HOME / ".local/bin")])
    seen, out = set(), []
    for p in parts:
        if p and p not in seen and os.path.isdir(p):
            seen.add(p)
            out.append(p)
    _path_cache.update(at=time.time(), path=os.pathsep.join(out))
    return _path_cache["path"]


def _env() -> Dict[str, str]:
    env = dict(os.environ, PATH=search_path(), TERM="xterm-256color", NO_COLOR="1", FORCE_COLOR="0", CI="")
    env.pop("CI", None)
    return env


def which(agent: str) -> Optional[str]:
    a = AGENTS[agent]
    for b in a["bins"]:
        p = shutil.which(b, path=search_path())
        if p:
            return p
    return None


def signed_in(agent: str) -> Optional[bool]:
    """True when its sign-in file exists or its API key is set; None when Xod can't tell (it'll say if it isn't)."""
    a = AGENTS[agent]
    if a["key_env"] and os.environ.get(a["key_env"]):
        return True
    if not a["signed_in"]:
        return None
    return any((HOME / f).exists() for f in a["signed_in"]) or None


def find() -> Dict[str, Any]:
    out = []
    for key, a in AGENTS.items():
        p = which(key)
        out.append({"id": key, "name": a["name"], "installed": bool(p), "path": p, "signed_in": signed_in(key) if p else None,
                    "can_login": bool(a["login"]) or a["run"] is not None, "install": a["install"],
                    "login_running": key in _logins and _logins[key].get("state") == "waiting"})
    return {"success": True, "agents": out, "installed": [x["id"] for x in out if x["installed"]]}


def resolve(agent: str) -> str:
    key = (agent or "").strip().lower().replace("claude code", "claude").replace("cursor-agent", "cursor").replace("gemini cli", "gemini")
    key = {"meta": "muse", "openai": "codex", "claude-code": "claude"}.get(key, key)
    if key not in AGENTS:
        raise CliError(f"Xod doesn't know an agent called {agent!r}. It knows: {', '.join(AGENTS)}.")
    return key


def _help(agent: str, path: str) -> str:
    if agent not in _help_cache:
        try:
            r = subprocess.run([path, "--help"], capture_output=True, text=True, timeout=20, env=_env(), stdin=subprocess.DEVNULL)
            _help_cache[agent] = (r.stdout or "") + (r.stderr or "")
        except Exception:
            _help_cache[agent] = ""
    return _help_cache[agent]


def clean(text: str) -> str:
    text = ANSI.sub("", text or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def _safe_extra(extra: Optional[List[str]]) -> List[str]:
    extra = [str(x) for x in (extra or [])]
    bad = [x for x in extra if UNSAFE_FLAGS.search(x)]
    if bad:
        raise CliError(f"Xod doesn't pass {' '.join(bad)}: it switches off the agent's own safety prompts.")
    return extra


def _folder(folder: str) -> str:
    f = os.path.expanduser(folder or "") if folder else str(HOME)
    if not os.path.isdir(f):
        raise CliError(f"There's no folder {folder}.")
    return f


# ------------------------------------------------------------------ one prompt, headless
async def run(agent: str, prompt: str, folder: str = "", extra: Optional[List[str]] = None, timeout: float = 600) -> Dict[str, Any]:
    key = resolve(agent)
    a = AGENTS[key]
    path = which(key)
    if not path:
        return {"success": False, "installed": False, "error": f"{a['name']} isn't installed. It installs with: {a['install']}"}
    if not str(prompt or "").strip():
        raise CliError("What should it do?")
    if a["check"] and a["check"] not in _help(key, path):
        return {"success": False, "error": f"This version of {a['name']} has no headless mode ({a['check']}). Start a session with it instead."}
    cwd = _folder(folder)
    args = [x.replace("{prompt}", prompt) for x in a["run"]] + _safe_extra(extra)
    if a.get("workspace_flag") and folder:
        args = args[:1] + [a["workspace_flag"], cwd] + args[1:] if a["run"][0] == "exec" else args
    started = time.time()
    proc = await asyncio.create_subprocess_exec(path, *args, cwd=cwd, env=_env(), stdin=subprocess.DEVNULL,
                                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                start_new_session=not WIN)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        _kill(proc.pid)
        return {"success": False, "agent": a["name"], "error": f"{a['name']} was still working after {int(timeout // 60)} minutes, so Xod stopped it. "
                "For long jobs, start a session with it or hand Xod a background task."}
    text, errs = clean(out.decode("utf-8", "replace")), clean(err.decode("utf-8", "replace"))
    needs_login = bool(re.search(r"not (?:logged|signed) in|please (?:log|sign) in|run .{0,20}\blogin\b|unauthori[sz]ed|401\b|"
                                 r"no (?:api key|credentials)|authentication (?:required|failed)|invalid api key", text + errs, re.I))
    res = {"success": proc.returncode == 0 and not (needs_login and not text.strip()), "agent": a["name"], "exit_code": proc.returncode,
           "answer": text.strip()[-12000:], "seconds": round(time.time() - started, 1), "folder": cwd}
    if errs.strip() and (proc.returncode != 0 or not text.strip()):
        res["errors"] = errs.strip()[-3000:]
    if needs_login and not res["success"]:
        res["needs_login"] = True
        res["error"] = f"{a['name']} needs you to sign in first. Say \"log in to {key}\" and Xod starts its sign-in."
    return res


def _kill(pid: int) -> None:
    try:
        if WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        pass


# ------------------------------------------------------------------ a terminal Xod holds (sessions and sign-ins)
class Session:
    """A program running in a pseudo-terminal (so it acts as it would in a real one), with its output kept as text."""

    def __init__(self, agent: str, argv: List[str], cwd: str):
        self.id = uuid.uuid4().hex[:8]
        self.agent, self.argv, self.cwd = agent, argv, cwd
        self.buf: List[str] = []
        self.started = time.time()
        # A terminal screen of its own (pyte), so full-screen agents (Muse, Claude Code…) read as you'd see them,
        # not as a stream of cursor moves.
        try:
            import pyte
            self.vt = pyte.HistoryScreen(COLS, ROWS, history=3000)
            self.vt.set_mode(pyte.modes.LNM)
            self.feed = pyte.Stream(self.vt)
        except Exception:
            self.vt = self.feed = None
        self.exit: Optional[int] = None
        self._lock = threading.Lock()
        if WIN:
            self.proc = subprocess.Popen(argv, cwd=cwd, env=_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            self.fd = None
            threading.Thread(target=self._pump_pipe, daemon=True).start()
        else:
            import pty
            import termios
            import struct
            import fcntl
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
            self.proc = subprocess.Popen(argv, cwd=cwd, env=_env(), stdin=slave, stdout=slave, stderr=slave,
                                         start_new_session=True, close_fds=True)
            os.close(slave)
            self.fd = master
            threading.Thread(target=self._pump_pty, daemon=True).start()

    def _add(self, data: str) -> None:
        with self._lock:
            if self.feed is not None:
                try:
                    self.feed.feed(data)
                except Exception:
                    pass
            self.buf.append(data)
            joined = "".join(self.buf)
            if len(joined) > 200_000:
                joined = joined[-150_000:]
            self.buf = [joined]

    def _pump_pty(self) -> None:
        while True:
            try:
                data = os.read(self.fd, 4096)
            except OSError:
                break
            if not data:
                break
            self._add(data.decode("utf-8", "replace"))
        self.exit = self.proc.wait()

    def _pump_pipe(self) -> None:
        for chunk in iter(lambda: self.proc.stdout.read1(4096) if hasattr(self.proc.stdout, "read1") else self.proc.stdout.read(1), b""):
            self._add(chunk.decode("utf-8", "replace"))
        self.exit = self.proc.wait()

    def text(self) -> str:
        """Everything it printed, as plain text (the stream, for programs that print line by line)."""
        with self._lock:
            return clean("".join(self.buf))

    def screen(self) -> str:
        """What its terminal shows right now, with what scrolled off above (as a person would see it)."""
        if self.vt is None:
            return self.text()[-6000:]
        with self._lock:
            above = ["".join(ch.data for ch in (row[x] for x in range(COLS))).rstrip() for row in list(self.vt.history.top)[-200:]]
            now = [line.rstrip() for line in self.vt.display]
        while now and not now[-1]:
            now.pop()
        while now and not now[0]:
            now.pop(0)
        return "\n".join([l for l in above if l] + now)

    def write(self, data: str) -> None:
        if self.exit is not None or self.proc.poll() is not None:
            raise CliError(f"{AGENTS[self.agent]['name']} has stopped (exit code {self.proc.poll()}).")
        raw = data.encode()
        if self.fd is not None:
            os.write(self.fd, raw)
        else:
            self.proc.stdin.write(raw)
            self.proc.stdin.flush()

    def running(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        if self.running():
            _kill(self.proc.pid)
            try:
                self.proc.wait(5)
            except Exception:
                self.proc.kill()
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


async def _settle(s: Session, quiet: float = 1.5, most: float = 30.0) -> str:
    """Waits until it has printed something and then gone quiet for a moment (or stopped), and returns the text."""
    start, last_len, last_change = time.time(), -1, time.time()
    while time.time() - start < most:
        await asyncio.sleep(0.25)
        n = len(s.text()) + hash(s.screen()) % 997
        if n != last_len:
            last_len, last_change = n, time.time()
        elif n and time.time() - last_change >= quiet:
            break
        if not s.running():
            await asyncio.sleep(0.3)
            break
    return s.text()


async def session_start(agent: str, folder: str = "", extra: Optional[List[str]] = None, visible: bool = False) -> Dict[str, Any]:
    key = resolve(agent)
    a = AGENTS[key]
    path = which(key)
    if not path:
        return {"success": False, "installed": False, "error": f"{a['name']} isn't installed. It installs with: {a['install']}"}
    cwd = _folder(folder)
    argv = [path] + _safe_extra(extra)
    if visible:
        from app.services import desktop
        cmd = f"cd {shlex.quote(cwd)} && {' '.join(shlex.quote(x) for x in argv)}"
        w = await asyncio.to_thread(desktop.open_terminal_with, cmd, key, cwd)
        wid = w["window"]
        how = ("is already running in Xod's window" if w.get("already_running") else
               "is running in one of Xod's windows (reused)" if w.get("reused") else "is running in a new Terminal window")
        return {"success": True, "visible": True, "agent": a["name"], "app": "Terminal", "window": wid, "tab": 1, "reused": w.get("reused", False),
                "message": f"{a['name']} {how} (window {wid}, titled \"Xod · {key}\"). Xod reads and types there with terminal_read / "
                           f"terminal_send, window {wid}; stop it with stop and window {wid}."}
    same = next((x for x in _sessions.values() if x.agent == key and x.cwd == cwd and x.running()), None)
    if same:
        return {"success": True, "session": same.id, "agent": a["name"], "folder": cwd, "running": True, "reused": True,
                "screen": same.screen()[-4000:], "note": "It was already running here: Xod carries on with that one."}
    running = [x for x in _sessions.values() if x.running()]
    if len(running) >= MAX_SESSIONS:
        raise CliError(f"Xod already has {len(running)} agent sessions running ("
                       + ", ".join(f"{x.id}: {AGENTS[x.agent]['name']}" for x in running) + "). Stop one first.")
    s = Session(key, argv, cwd)
    _sessions[s.id] = s
    await _settle(s, quiet=2.0, most=25)
    return {"success": True, "session": s.id, "agent": a["name"], "folder": cwd, "running": s.running(), "screen": s.screen()[-4000:],
            "note": "If it asks whether to trust the folder or to allow something, ask the person unless they already said so; answer with key (1, y, enter…)."}


def _session(sid: str) -> Session:
    s = _sessions.get(sid or "")
    if not s:
        live = [f"{x.id} ({AGENTS[x.agent]['name']})" for x in _sessions.values() if x.running()]
        raise CliError(f"No session {sid}." + (f" Running: {', '.join(live)}." if live else " None is running: start one."))
    return s


QUESTION_RE = re.compile(r"do you trust|trust (?:this|the) (?:workspace|folder|directory)|trust and continue|\(y/n\)|\[y/n\]|"
                         r"allow (?:this|once|always)|do you want to (?:allow|proceed|run)|approve\b.*\?|press enter to continue", re.I)


async def session_send(sid: str, text: str, enter: bool = True, wait: float = 60) -> Dict[str, Any]:
    s = _session(sid)
    tail = s.screen()[-1500:]
    m = QUESTION_RE.search(tail)
    if m:
        # The agent is asking something (trust this folder? allow this command?): typing the message now would answer
        # it. That answer is the person's: show the question and let them say (then answer with key: 1, y, enter…).
        return {"success": False, "session": s.id, "needs_answer": True, "screen": tail,
                "error": f"{AGENTS[s.agent]['name']} is asking something first (\"{m.group(0)}\"): ask the person what to answer, then "
                         "answer it with action key, and send the message after."}
    s.write(text)
    if enter:
        # Full-screen agents take text that arrives at once as a paste, and a Return right after it as part of the paste
        # (a new line, not "send"): Muse 1.4.11 needs about half a second between them.
        await asyncio.sleep(RETURN_PAUSE)
        s.write("\r")
    await _settle(s, quiet=3.0, most=max(5.0, min(float(wait or 60), 600)))
    return {"success": True, "session": s.id, "running": s.running(), "screen": s.screen()[-6000:], "exit_code": s.exit}


def latest_session(agent: str = "") -> str:
    """The newest running session (of that agent, if named), for a send that didn't say which."""
    live = [x for x in _sessions.values() if x.running() and (not agent or x.agent == agent)]
    return max(live, key=lambda x: x.started).id if live else ""


def session_read(sid: str, chars: int = 4000) -> Dict[str, Any]:
    s = _session(sid or latest_session())
    return {"success": True, "session": s.id, "running": s.running(), "screen": s.screen()[-max(500, min(int(chars or 4000), 20000)):], "exit_code": s.exit}


def session_key(sid: str, key: str) -> Dict[str, Any]:
    """Special keys for a session: ctrl+c, ctrl+d, escape, up, down, enter, tab."""
    codes = {**{str(i): str(i) for i in range(10)}, "ctrl+c": "\x03", "ctrl+d": "\x04", "escape": "\x1b", "esc": "\x1b", "enter": "\r", "return": "\r", "tab": "\t",
             "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D", "backspace": "\x7f", "y": "y", "n": "n"}
    k = (key or "").lower().strip()
    if k not in codes:
        raise CliError(f"Xod doesn't know the key {key}. It knows: {', '.join(codes)}.")
    _session(sid).write(codes[k])
    return {"success": True, "pressed": k}


def session_stop(sid: str = "", window: Any = None) -> Dict[str, Any]:
    """Stops a session; with window, closes one of Xod's own Terminal windows (and what runs in it)."""
    if window not in (None, ""):
        from app.services import desktop
        return desktop.close_terminal_window(int(window))
    s = _session(sid)
    s.stop()
    _sessions.pop(s.id, None)
    return {"success": True, "stopped": s.id}


def sessions() -> List[Dict[str, Any]]:
    """Xod's agent sessions: in the background, and in Terminal windows Xod opened."""
    out = [{"session": s.id, "agent": AGENTS[s.agent]["name"], "running": s.running(), "folder": s.cwd,
            "minutes": round((time.time() - s.started) / 60, 1)} for s in _sessions.values()]
    try:
        from app.services import desktop
        if desktop.MAC and desktop.enabled():
            out += [{"window": m["window"], "agent": AGENTS.get(m.get("agent") or "", {}).get("name", m.get("agent") or "shell"),
                     "running": bool(m.get("busy")), "folder": m.get("folder"), "visible": True} for m in desktop.owned_windows()]
    except Exception:
        pass
    return out


# ------------------------------------------------------------------ signing in
def _login_argv(key: str, path: str) -> List[str]:
    a = AGENTS[key]
    return [path] + (a["login"] or [])          # agents with no login command sign in on their first start


async def login(agent: str) -> Dict[str, Any]:
    """Starts the agent's sign-in, finds its link and code, and opens the page in your browser for you to approve."""
    key = resolve(agent)
    a = AGENTS[key]
    path = which(key)
    if not path:
        return {"success": False, "installed": False, "error": f"{a['name']} isn't installed. It installs with: {a['install']}"}
    old = _logins.get(key)
    if old and old.get("state") == "waiting" and old["session"].running():
        return {"success": True, **{k: v for k, v in old.items() if k != "session"}, "message": "Its sign-in is already waiting for you."}
    s = Session(key, _login_argv(key, path), str(HOME))
    entry = {"agent": a["name"], "state": "waiting", "url": None, "code": None, "opened": False, "started": time.time(), "session": s}
    _logins[key] = entry
    deadline = time.time() + 40
    while time.time() < deadline and s.running():
        await asyncio.sleep(0.5)
        text = s.text()
        if re.search(r"press enter|hit enter|press return", text, re.I) and not entry.get("entered"):
            entry["entered"] = True
            s.write("\r")                   # "Press Enter to open it in your browser": the agent opens the page itself
            entry["opened"] = True
        urls = [u.rstrip(".,") for u in URL_RE.findall(text)]
        ok = [u for u in urls if not a["auth_hosts"] or any(re.search(rf"(?:^|\.){re.escape(h)}(?::|/|$)", u.split("//", 1)[1].split("/")[0] + "/") for h in a["auth_hosts"])]
        m = CODE_RE.search(text)
        if m:
            entry["code"] = m.group(1) or m.group(2)
        if ok:
            entry["url"] = ok[-1]
        if entry["url"] and (entry["code"] or time.time() - entry["started"] > 6):
            break
        if re.search(r"signed in|logged in|already (?:logged|signed)|success", text, re.I) and not entry["url"]:
            break
    text = s.text()
    if entry["url"] and not entry["opened"]:
        open_url(entry["url"])
        entry["opened"] = True
    threading.Thread(target=_watch_login, args=(key,), daemon=True).start()
    if not entry["url"]:
        done = not s.running() and s.exit == 0
        entry["state"] = "done" if done else entry["state"]
        return {"success": done or s.running(), "agent": a["name"], "state": entry["state"], "screen": text[-2000:],
                "message": (f"{a['name']} says you're signed in." if done else
                            f"{a['name']}'s sign-in is running but showed no link yet; here's what it shows. Say what it asks and Xod answers it.")}
    return {"success": True, "agent": a["name"], "state": "waiting", "url": entry["url"], "code": entry["code"],
            "message": f"Opened {a['name']}'s sign-in page in your browser"
                       + (f": check it shows the code {entry['code']}" if entry["code"] else "") + ", then approve it there. "
                       "Xod finishes by itself when you do (nothing to type here)."}


def open_url(url: str) -> None:
    """The agent's own sign-in page, in your browser."""
    subprocess.Popen(["open" if not WIN else "explorer", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _watch_login(key: str) -> None:
    e = _logins.get(key)
    if not e:
        return
    s = e["session"]
    start = time.time()
    while s.running() and time.time() - start < 1800:
        time.sleep(1)
        if re.search(r"signed in\.?$|logged in|success", s.text()[-400:], re.I | re.M):
            break
    time.sleep(1)
    ok = (s.exit == 0) or bool(re.search(r"signed in|logged in|success", s.text()[-600:], re.I))
    e["state"] = "done" if ok else ("expired" if time.time() - start >= 1800 else "failed")
    if s.running() and ok:
        s.stop()
    logger.info(f"{AGENTS[key]['name']} sign-in: {e['state']}")


def login_status(agent: str) -> Dict[str, Any]:
    key = resolve(agent)
    e = _logins.get(key)
    base = {"agent": AGENTS[key]["name"], "signed_in": signed_in(key)}
    if not e:
        return {"success": True, **base, "state": "none"}
    return {"success": True, **base, "state": e["state"], "url": e["url"], "code": e["code"], "screen": e["session"].text()[-1500:]}


def context_note(query: str) -> str:
    """For the planner, when a request is about a terminal agent: which are installed, where, signed in or not, and
    which tools use them (the model otherwise tried `muse` as a shell command, or took Meta's Muse desktop app for it)."""
    from app.agent.tools import _wants_cli_agent
    if not _wants_cli_agent(query):
        return ""
    try:
        found = [a for a in find()["agents"] if a["installed"]]
    except Exception:
        return ""
    if not found:
        return ("(On this computer: no terminal AI agent (Muse, Claude Code, Codex, Gemini CLI …) is installed. Say so, "
                "and how to install the one they mean: cli_agents lists the install commands.)")
    home = str(HOME)
    rows = [f"{a['name']} ({a['id']}) at {str(a['path']).replace(home, '~')}, "
            + ("signed in" if a["signed_in"] else "not signed in yet" if a["signed_in"] is False else "sign-in unknown") for a in found]
    note = ("(On this computer, terminal AI agents installed: " + "; ".join(rows) + ". To use one, call cli_agent_run "
            "(one job, headless, in a folder) or cli_agent_session (start it, then send messages and read its screen; "
            "visible=true opens it in a Terminal window the person can watch). Don't run them with computer_run_command. "
            "If they ask whether Xod can use or control one of these, the answer is yes: say so plainly, say how (Xod "
            "starts it, types to it, reads its answers), and offer to start it now, or do it if they asked. ")
    typos = {"claude": r"claude?(?:\s*code)?", "gemini": r"gemini(?:\s*cli)?", "cursor": r"cursor[- ]?agent"}
    missing = [k for k, info in AGENTS.items() if k not in {a["id"] for a in found}
               and re.search(rf"\b(?:{typos.get(k, re.escape(k))}|{re.escape(info['name'].split(' (')[0])})\b", query or "", re.I)]
    for k in missing:
        note += f"{AGENTS[k]['name']} isn't installed (it installs with: {AGENTS[k]['install']}). "
    if any(a["id"] == "muse" for a in found):
        note += "Muse.app in Applications is a different app (Meta's desktop chat): this means the muse command. "
    if re.search(r"--yolo|--dangerously|skip[- ]permissions", query or "", re.I):
        note += ("Xod doesn't start agents with --yolo or other flags that switch off their own permission prompts: it "
                 "starts them normally and passes their questions to the person. Say that, and that everything else works. ")
    return note + ")"
