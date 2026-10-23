"""
Xod in the terminal: chat with the same agent, give it tasks, answer its approval cards, see what's running.

  xod                         a conversation (/help inside for commands; piped lines are sent one by one)
  xod "summarise ~/Downloads/report.pdf"     one question, answered and done
  cat notes.txt | xod "turn this into a to-do list"     piped text goes along with the question
  xod -c "and the second one?"               carry on from the last terminal chat
  xod task "find three flights to Lisbon in May"         a background task, followed until it's done
  xod tasks | xod approvals | xod status | xod stop

One brain: the terminal never runs a second copy of the agent. It talks to the Xod server that already serves the
desktop app (127.0.0.1:8000, or XOD_PORT / --url), and only one that uses the same data folder. With none running it
starts one in the background on that port, which the desktop app then uses too when it opens. Two servers on one
database would both run the task queue, routines and Agent Mail, both start the built-in engine and fight over the
browser profile; one server means one set of chats, memories, approvals and loaded models. `xod stop` stops a server
the terminal started (never the desktop app's).

Approvals: anything the safety check (services/sentinel.py) or a connected app holds is asked as y/N here, exactly
like the card in the app. --yes approves only ordinary actions of connected apps you set to ask first; it never
approves a safety-check hold, a delete, or a task about to press Submit. With nobody to ask (no input left), a hold
is left waiting for you in the app and in `xod approvals`.
"""
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.paths import APP_ROOT, DATA_DIR
from app.version import VERSION

STATE_FILE = DATA_DIR / "cli-server.json"        # the server the terminal started (url, pid)
CHAT_FILE = DATA_DIR / "cli-chat.json"           # the last terminal chat, for --continue
SERVER_LOG = DATA_DIR / "logs" / "cli-server.log"
START_WAIT = 180                                 # seconds; a first start loads a lot

COMMANDS = {"status", "stop", "start", "task", "tasks", "approvals", "ask", "help"}
HELP = f"""Xod {VERSION} in the terminal

  xod                       chat (Ctrl-D or /exit to leave, /help for chat commands)
  xod "question"            ask once and print the answer
  xod ask "status"          ask once (when the question is a command word)
  xod task "job"            add a background task and follow it (--no-wait: just add it)
  xod tasks                 your tasks and what they're doing
  xod approvals             answer what's waiting for your OK
  xod status                which model answers, model servers on this computer, add-ons
  xod start | xod stop      start / stop the background server the terminal uses

Options
  -c, --continue            carry on from the last terminal chat
  -y, --yes                 approve ordinary actions of connected apps without asking (never safety-check
                            holds, deletes, or a task pressing Submit: those always ask)
  --cloud                   answer this on your cloud model (needs an API key in Settings)
  --url URL                 use the Xod server at URL
  --json                    status as JSON
  --no-color                plain text
  -V, --version             print the version
"""
CHAT_HELP = """  /new            start a new chat
  /cloud QUESTION  ask this one on your cloud model
  /status          model, model servers, add-ons
  /tasks           your tasks
  /approvals       what's waiting for your OK
  /exit            leave (or Ctrl-D)"""


# ------------------------------------------------------------------ output
class Out:
    color = False

    @classmethod
    def setup(cls, no_color: bool) -> None:
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")     # Windows consoles default to cp1252
            except Exception:
                pass
        cls.color = sys.stdout.isatty() and not no_color and not os.environ.get("NO_COLOR")
        if cls.color and sys.platform == "win32":
            os.system("")                                            # turns on ANSI colours in the Windows console

    @classmethod
    def paint(cls, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if cls.color else text


def dim(t: str) -> str:
    return Out.paint("2", t)


def yellow(t: str) -> str:
    return Out.paint("33", t)


def red(t: str) -> str:
    return Out.paint("31", t)


def green(t: str) -> str:
    return Out.paint("32", t)


def bold(t: str) -> str:
    return Out.paint("1", t)


def say(text: str = "", err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    stream.write(text + "\n")
    stream.flush()


def short(v: Any, n: int = 80) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def pretty_tool(name: str) -> str:
    """mcp__notes__create_note → "create note (notes)"; web_search → "web search"."""
    if name.startswith("mcp__") and name.count("__") >= 2:
        _, server, tool = name.split("__", 2)
        return f"{tool.replace('_', ' ')} ({server})"
    return name.replace("_", " ")


class Answers:
    """Where y/N answers come from: the input, or the terminal itself once piped input was used up as the question's text."""
    stream = None

    @classmethod
    def use_terminal(cls) -> None:
        try:
            cls.stream = open("CONIN$" if sys.platform == "win32" else "/dev/tty", encoding="utf-8")
        except OSError:
            cls.stream = False          # no terminal: nobody to ask


def ask_line(prompt: str) -> Optional[str]:
    """One line from the person; None when there's nobody to ask (no input left)."""
    src = sys.stdin if Answers.stream is None else Answers.stream
    if src is False:
        return None
    try:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        line = src.readline()
    except (KeyboardInterrupt, EOFError, OSError):
        say()
        return None
    if not line:
        say()
        return None
    if not src.isatty():
        say(line.rstrip("\n"))                                       # piped: show what was answered
    return line.strip()


# ------------------------------------------------------------------ finding the one Xod server
def _port() -> int:
    try:
        return int(os.environ.get("XOD_PORT") or 8000)
    except ValueError:
        return 8000


def _same_dir(a: str) -> bool:
    try:
        return Path(a).expanduser().resolve() == DATA_DIR
    except Exception:
        return False


def hello(url: str, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    """{"app": "xod", "data_dir", "version", "pid"} when a Xod server answers at url; {"legacy": True} for one too old
    to say which data folder it uses; None for anything else."""
    try:
        r = httpx.get(url + "/api/cli/hello", timeout=timeout)
        if r.status_code == 200 and r.json().get("app") == "xod":
            return r.json()
        if r.status_code == 404:
            page = httpx.get(url + "/", timeout=timeout)
            if page.status_code == 200 and re.search(r"<title>\s*Xod", page.text, re.I):
                return {"app": "xod", "legacy": True}
    except Exception:
        pass
    return None


def _ours(h: Optional[Dict[str, Any]]) -> bool:
    """A server using this data folder. One too old to say counts only for the default folder (the desktop app's)."""
    if not h:
        return False
    if h.get("legacy"):
        return not os.environ.get("XOD_DATA_DIR")
    return _same_dir(h.get("data_dir") or "")


def _state() -> Dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def find_server(explicit: Optional[str] = None) -> Optional[str]:
    """The URL of the Xod server for this data folder, or None when none is running."""
    if explicit:
        url = explicit.rstrip("/")
        h = hello(url)
        if not h:
            raise SystemExit(red(f"No Xod server answers at {url}."))
        if not h.get("legacy") and not _same_dir(h.get("data_dir") or ""):
            say(dim(f"(Xod at {url} uses {h.get('data_dir')}, not {DATA_DIR})"), err=True)
        return url
    st = _state()
    if st.get("url") and _ours(hello(st["url"])):
        return st["url"]
    # The desktop app says where its server is (it uses another port when 8000 is taken).
    try:
        announced = json.loads((DATA_DIR / "server.json").read_text()).get("url")
    except (OSError, ValueError, AttributeError):
        announced = None
    if announced and _ours(hello(announced)):
        return announced
    url = f"http://127.0.0.1:{_port()}"
    if _ours(hello(url)):
        return url
    return None


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server() -> str:
    """Starts Xod's server in the background, the way the desktop app does, and waits until it answers."""
    port = _port() if _port_free(_port()) else _free_port()   # the usual port when free, so the desktop app finds it
    url = f"http://127.0.0.1:{port}"
    SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "XOD_DATA_DIR": str(DATA_DIR), "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
    kw: Dict[str, Any] = {}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True          # survives this terminal; `xod stop` ends it and what it started
    say(dim(f"Starting Xod in the background on {url} (the first start can take a minute)…"), err=True)
    with open(SERVER_LOG, "w", encoding="utf-8") as log:
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                                cwd=str(APP_ROOT), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **kw)
    STATE_FILE.write_text(json.dumps({"url": url, "pid": proc.pid, "started_at": time.time()}), encoding="utf-8")
    deadline = time.time() + START_WAIT
    while time.time() < deadline:
        h = hello(url, timeout=1.0)
        if h and h.get("pid") == proc.pid:
            say(dim(f"Xod is running. It stays up for your next command and for the desktop app; `xod stop` stops it."), err=True)
            return url
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    tail = SERVER_LOG.read_text(encoding="utf-8", errors="replace")[-1500:] if SERVER_LOG.exists() else ""
    if proc.poll() is None:
        _kill(proc.pid)
    STATE_FILE.unlink(missing_ok=True)
    raise SystemExit(red("Xod's server didn't start.") + f"\n{tail}\n(log: {SERVER_LOG})")


def server(explicit: Optional[str] = None) -> str:
    return find_server(explicit) or start_server()


def _kill(pid: int) -> None:
    if sys.platform == "win32":     # no signals on Windows: end the server and anything it started (browser, engine)
        subprocess.run(["taskkill", "/pid", str(pid), "/T", "/F"], capture_output=True)
        return
    try:
        os.killpg(pid, signal.SIGTERM)                  # its own session: the engine and browser it started go too
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.25)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def cmd_stop(opts: Dict[str, Any]) -> int:
    st = _state()
    h = hello(st["url"]) if st.get("url") else None
    if h and h.get("pid") == st.get("pid") and _ours(h):
        _kill(st["pid"])
        STATE_FILE.unlink(missing_ok=True)
        say(f"Stopped Xod at {st['url']}.")
        return 0
    STATE_FILE.unlink(missing_ok=True)
    other = find_server(opts.get("url"))
    if other:
        say(f"Xod at {other} wasn't started from the terminal (the desktop app or ./run.sh started it), so it's left running. "
            "Quit it there.")
        return 1
    say("Xod isn't running.")
    return 0


def cmd_start(opts: Dict[str, Any]) -> int:
    url = find_server(opts.get("url"))
    if url:
        say(f"Xod is already running at {url}.")
        return 0
    start_server()
    return 0


# ------------------------------------------------------------------ the API
class Xod:
    def __init__(self, url: str):
        self.url = url
        self.http = httpx.Client(base_url=url, timeout=httpx.Timeout(30.0, read=300.0))

    def get(self, path: str) -> Any:
        r = self.http.get(path)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: Optional[Dict[str, Any]] = None, timeout: float = 300.0) -> Any:
        r = self.http.post(path, json=body if body is not None else {}, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def chat(self, body: Dict[str, Any]):
        """The chat's events as they stream (the same /api/chat the app uses)."""
        with self.http.stream("POST", "/api/chat", json=body, timeout=httpx.Timeout(30.0, read=None)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line.startswith("data: "):
                    try:
                        yield json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue


# ------------------------------------------------------------------ one chat turn, shown as it streams
class Turn:
    """Prints the answer as it streams, tool steps as short lines, and collects approval cards for afterwards."""

    def __init__(self):
        self.content = ""
        self.session_id: Optional[str] = None
        self.approvals: List[Dict[str, Any]] = []
        self.notes: List[str] = []
        self.mid_line = False           # the answer text so far doesn't end in a newline
        self.status_shown = False

    def _clear_status(self) -> None:
        if self.status_shown:
            sys.stdout.write("\r\033[2K")
            self.status_shown = False

    def _line(self, text: str) -> None:
        self._clear_status()
        if self.mid_line:
            sys.stdout.write("\n")
            self.mid_line = False
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    def on(self, ev: Dict[str, Any]) -> None:
        t = ev.get("type")
        if t == "content":
            text = ev.get("content") or ""
            if not text:
                return
            self._clear_status()
            self.content += text
            sys.stdout.write(text)
            sys.stdout.flush()
            self.mid_line = not text.endswith("\n")
        elif t == "status":
            if sys.stdout.isatty() and Out.color and not self.content:
                self._clear_status()
                sys.stdout.write(dim(short(ev.get("content") or "", 100)))
                sys.stdout.flush()
                self.status_shown = True
        elif t in ("tool_call", "subagent_tool_call"):
            name = ev.get("name") or ev.get("tool") or "tool"
            args = ev.get("args") or {}
            first = next((v for v in args.values() if v not in (None, "", [], {})), "") if isinstance(args, dict) else args
            pad = "    " if t.startswith("subagent") else "  "
            self._line(dim(f"{pad}› {pretty_tool(name)}" + (f"  {short(first, 70)}" if first != "" else "")))
        elif t in ("tool_result", "subagent_tool_result"):
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            pad = "    " if t.startswith("subagent") else "  "
            if data.get("needs_approval") and data.get("approval_id"):
                self.approvals.append(data)
                self._line(yellow(f"{pad}⏸ waiting for your OK: {short(data.get('message') or data.get('tool') or '', 110)}"))
            elif data.get("success") is False or data.get("error"):
                self._line(red(f"{pad}✗ {short(ev.get('summary') or data.get('error') or 'failed', 110)}"))
            elif ev.get("summary"):
                self._line(dim(f"{pad}✓ {short(ev['summary'], 110)}"))
        elif t == "session_info":
            self.session_id = ev.get("session_id")
        elif t == "understood":
            self._line(dim(f"  (understood as: {short(ev.get('content') or '', 110)})"))
        elif t == "error":
            self._line(red(f"Error: {ev.get('content') or 'something went wrong'}"))
        elif t == "build_done":
            proj = ev.get("project") or {}
            self.notes.append(f"Built {ev.get('title') or proj.get('name') or 'it'}" + (f" in {proj['path']}" if proj.get("path") else ""))
        elif t == "memory":
            got = [x if isinstance(x, str) else (x.get("fact") or x.get("text") or "") for x in ev.get("learned") or []]
            if any(got):
                self.notes.append("Remembered: " + "; ".join(g for g in got if g)[:200])
            if ev.get("forgot"):
                self.notes.append("Forgot: " + "; ".join(str(x) for x in ev["forgot"])[:200])
        elif t == "route_info":
            cost = ev.get("cost_usd")
            self.notes.append(f"Answered by {ev.get('model')}" + (f" (${cost:.4f})" if isinstance(cost, (int, float)) and cost else ""))
        elif t == "escalation_offer":
            est = ev.get("est_cost_usd")
            self.notes.append(f"{ev.get('model')} could do better here: ask again with --cloud (or /cloud)"
                              + (f", about ${est:.3f}" if isinstance(est, (int, float)) else ""))

    def end(self) -> None:
        self._clear_status()
        if self.mid_line:
            sys.stdout.write("\n")
            self.mid_line = False
        for n in self.notes:
            say(dim(f"  {n}"))
        sys.stdout.flush()


def _approvable_by_yes(rec: Dict[str, Any]) -> bool:
    """--yes is for ordinary actions of connected apps that are set to ask first. Never a safety-check hold or a delete."""
    return not rec.get("sentinel") and not rec.get("destructive") and str(rec.get("tool") or "").startswith("mcp__")


def settle_approvals(x: Xod, cards: List[Dict[str, Any]], yes: bool) -> None:
    """Each held action, as a y/N question (what the app shows as a card)."""
    if not cards:
        return
    try:
        pending = {p["id"]: p for p in x.get("/api/mcp").get("pending", [])}
    except Exception:
        pending = {}
    for card in cards:
        pid = card.get("approval_id")
        rec = {**card, **(pending.get(pid) or {})}
        if pid not in pending:
            say(dim(f"  ({short(rec.get('tool_name') or rec.get('tool') or 'that action', 60)} was already answered.)"))
            continue
        rec["destructive"] = card.get("destructive") or rec.get("destructive")
        _ask_one(x, rec, yes)


def _ask_one(x: Xod, rec: Dict[str, Any], yes: bool) -> None:
    pid = rec["id"] if "id" in rec else rec["approval_id"]
    from app.services.mcp_client import _action, _app_name
    what = _action(str(rec.get("tool_name") or str(rec.get("tool") or "").split("__")[-1]))
    where = _app_name(rec.get("server") or "")
    say()
    say(yellow(bold(f"Xod wants to {what}" + (f" ({where})" if where else ""))))
    why = rec.get("why") or rec.get("message") or rec.get("description")
    if why:
        say(yellow(f"  {short(why, 300)}"))
    args = json.dumps(rec.get("args") or {}, indent=2, ensure_ascii=False, default=str)
    for line in args.splitlines()[:20]:
        say(dim(f"  {line[:160]}"))
    if yes and _approvable_by_yes(rec):
        say(dim("  Approved (--yes)."))
        answer = "y"
    else:
        if yes:
            say(dim("  --yes doesn't cover this one" + (" (the safety check held it)" if rec.get("sentinel") else
                                                       " (it deletes something)" if rec.get("destructive") else "") + "."))
        answer = ask_line(bold("Approve and run it? [y/N] "))
    if answer is None:
        say(dim("  Left waiting for your OK: approve or decline it in the app, or with `xod approvals`."))
        return
    if answer.lower() in ("y", "yes"):
        try:
            r = x.post(f"/api/mcp/approve/{pid}")
        except httpx.HTTPError as e:
            say(red(f"  Couldn't run it: {e}"))
            return
        if r.get("success"):
            say(green("  Done.") + (f"\n{short(r.get('content') or r.get('message') or r.get('output') or '', 1500)}"
                                    if (r.get("content") or r.get("message") or r.get("output")) else ""))
        else:
            say(red(f"  It didn't work: {short(r.get('error') or r, 400)}"))
    else:
        x.post(f"/api/mcp/decline/{pid}")
        say("  Declined. Nothing was run.")


def run_turn(x: Xod, history: List[Dict[str, Any]], session_id: Optional[str], yes: bool, route: Optional[str] = None) -> Optional[str]:
    """Sends the conversation, shows the reply, asks about anything held. Returns the chat's id."""
    body: Dict[str, Any] = {"messages": history, "session_id": session_id}
    if route:
        body["route"] = route
    turn = Turn()
    try:
        for ev in x.chat(body):
            turn.on(ev)
    except KeyboardInterrupt:
        turn.end()
        say(dim("  (stopped)"))
    except httpx.HTTPError as e:
        turn.end()
        say(red(f"Lost the connection to Xod: {e}"))
    else:
        turn.end()
    if turn.content:
        history.append({"role": "assistant", "content": turn.content})
    sid = turn.session_id or session_id
    if sid:
        try:
            CHAT_FILE.write_text(json.dumps({"session_id": sid, "url": x.url}), encoding="utf-8")
        except OSError:
            pass
    settle_approvals(x, turn.approvals, yes)
    return sid


def _last_chat(x: Xod) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    try:
        sid = json.loads(CHAT_FILE.read_text(encoding="utf-8")).get("session_id")
        msgs = x.get(f"/api/sessions/{sid}").get("messages", [])
    except Exception:
        return None, []
    return sid, [{"role": m["role"], "content": m.get("content") or ""} for m in msgs if m.get("role") in ("user", "assistant")]


def cmd_ask(x: Xod, prompt: str, opts: Dict[str, Any]) -> int:
    sid, history = _last_chat(x) if opts.get("continue") else (None, [])
    if opts.get("continue") and not sid:
        say(dim("(no earlier terminal chat; starting a new one)"), err=True)
    history.append({"role": "user", "content": prompt})
    run_turn(x, history, sid, opts.get("yes", False), "cloud" if opts.get("cloud") else None)
    return 0


def cmd_chat(x: Xod, opts: Dict[str, Any]) -> int:
    sid, history = _last_chat(x) if opts.get("continue") else (None, [])
    piped = not sys.stdin.isatty()
    if not piped:
        try:
            import readline  # noqa: F401  (line editing and history where it exists)
        except ImportError:
            pass
        say(bold(f"Xod {VERSION}") + dim(f" · {_answering_line(x)} · /help for commands, Ctrl-D to leave"))
    while True:
        try:
            line = input(Out.paint("36", "you › ")) if not piped else _piped_line()
        except EOFError:
            say()
            return 0
        except KeyboardInterrupt:
            say()
            continue
        if line is None:
            return 0
        line = line.strip()
        if not line:
            continue
        route = "cloud" if opts.get("cloud") else None
        if line.startswith("/"):
            cmd, _, rest = line[1:].partition(" ")
            cmd = cmd.lower()
            if cmd in ("exit", "quit", "q"):
                return 0
            if cmd == "help":
                say(CHAT_HELP)
                continue
            if cmd == "new":
                sid, history = None, []
                say(dim("New chat."))
                continue
            if cmd == "status":
                show_status(x, opts)
                continue
            if cmd == "tasks":
                show_tasks(x)
                continue
            if cmd == "approvals":
                cmd_approvals(x, opts)
                continue
            if cmd == "cloud" and rest.strip():
                line, route = rest.strip(), "cloud"
            elif cmd != "cloud":
                say(dim(f"Unknown command /{cmd}. /help lists them."))
                continue
            else:
                say(dim("/cloud QUESTION asks one question on your cloud model."))
                continue
        elif line.lower() in ("exit", "quit"):
            return 0
        history.append({"role": "user", "content": line})
        sid = run_turn(x, history, sid, opts.get("yes", False), route)
        say()


def _piped_line() -> Optional[str]:
    line = sys.stdin.readline()
    if not line:
        return None
    say(Out.paint("36", "you › ") + line.rstrip("\n"))
    return line


# ------------------------------------------------------------------ tasks
_TASK_MARK = {"done": "✓", "failed": "✗", "cancelled": "–", "waiting": "⏸", "working": "…", "queued": "·"}


def show_tasks(x: Xod) -> None:
    tasks = x.get("/api/tasks").get("tasks", [])
    if not tasks:
        say("No tasks yet. Add one with: xod task \"what to do\"")
        return
    for t in tasks[:15]:
        mark = _TASK_MARK.get(t.get("status"), "·")
        detail = t.get("now") if t.get("status") in ("working", "waiting", "queued") else (t.get("result") or "")
        line = f"{mark} {short(t.get('title') or '', 60)}  {dim(t.get('status') or '')}  {dim(short(detail or '', 70))}"
        say((yellow(line) if t.get("status") == "waiting" else line) + dim(f"  {t.get('id')}"))


def _task(x: Xod, tid: str) -> Optional[Dict[str, Any]]:
    return next((t for t in x.get("/api/tasks").get("tasks", []) if t.get("id") == tid), None)


def follow_task(x: Xod, tid: str) -> int:
    """Shows a task's progress until it finishes or needs something only the person can give."""
    seen_notes, last_now = 0, None
    try:
        while True:
            t = _task(x, tid)
            if not t:
                say(red("The task is gone."))
                return 1
            for h in (t.get("history") or [])[seen_notes:]:
                say(dim(f"  · {short(h.get('text') if isinstance(h, dict) else h, 120)}"))
            seen_notes = len(t.get("history") or [])
            if t.get("now") and t["now"] != last_now and t.get("status") == "working":
                say(dim(f"  › {short(t['now'], 120)}"))
            last_now = t.get("now")
            st = t.get("status")
            if st == "done":
                say(green("Done.") + (f"\n{t['result']}" if t.get("result") else ""))
                return 0
            if st in ("failed", "cancelled"):
                say(red(f"{st.capitalize()}.") + (f"\n{t['result']}" if t.get("result") else ""))
                return 1
            if st == "waiting":
                if not _task_waiting(x, t):
                    return 0
            time.sleep(1.5)
    except KeyboardInterrupt:
        say()
        say(dim(f"It keeps going in Xod. See it with `xod tasks` ({tid})."))
        return 0


def _task_waiting(x: Xod, t: Dict[str, Any]) -> bool:
    """Asks what the task is waiting for. True when it can carry on."""
    d = t.get("waiting_detail") or {}
    kind = t.get("waiting_kind")
    if d.get("message"):
        say(d["message"])
    if kind == "approval":
        say(yellow(bold(f"The task wants to press “{d.get('label') or 'Submit'}”" + (f" on {d['page']}" if d.get("page") else ""))))
        a = ask_line(bold("Approve? [y/N] "))
        if a and a.lower() in ("y", "yes"):
            x.post(f"/api/tasks/{t['id']}/approve")
            say(dim("  Approved; it carries on."))
            return True
        say(dim(f"  Left waiting for your OK (approve it in the app, with `xod approvals`, or cancel it there). {t['id']}"))
        return False
    if kind == "blocked":
        say(yellow("It's stuck at a verification check on a web page: open the desktop app to clear it (Take over)."))
        return False
    a = ask_line(bold("Your answer (empty to leave it waiting): "))
    if a:
        x.post(f"/api/tasks/{t['id']}/answer", {"text": a})
        return True
    say(dim(f"  Left waiting for your answer. {t['id']}"))
    return False


def cmd_task(x: Xod, request: str, opts: Dict[str, Any]) -> int:
    r = x.post("/api/tasks", {"request": request})
    t = r.get("task") or {}
    say(f"Added task: {t.get('title')}  {dim(t.get('id') or '')}")
    if opts.get("no_wait"):
        return 0
    return follow_task(x, t["id"])


def cmd_approvals(x: Xod, opts: Dict[str, Any]) -> int:
    pending = x.get("/api/mcp").get("pending", [])
    waiting = [t for t in x.get("/api/tasks").get("tasks", []) if t.get("status") == "waiting" and t.get("waiting_kind") == "approval"]
    if not pending and not waiting:
        say("Nothing is waiting for your OK.")
        return 0
    for p in pending:
        _ask_one(x, p, opts.get("yes", False))
    for t in waiting:
        say()
        say(bold(t.get("title") or ""))
        _task_waiting(x, t)
    return 0


# ------------------------------------------------------------------ status
def _answering_line(x: Xod) -> str:
    try:
        a = x.get("/api/local-servers").get("answering") or {}
    except Exception:
        return "model unknown"
    s = f"{a.get('name') or a.get('provider') or 'no model'}" + (f" · {a['model']}" if a.get("model") else "")
    return s + (" (no API key yet)" if a.get("ready") is False else "")


def gather_status(x: Xod) -> Dict[str, Any]:
    out: Dict[str, Any] = {"url": x.url, "data_dir": str(DATA_DIR), "cli_version": VERSION}
    h = hello(x.url) or {}
    out["server"] = {"version": h.get("version"), "pid": h.get("pid"),
                     "started_by": "terminal" if h.get("pid") and h.get("pid") == _state().get("pid") else "desktop app or ./run.sh"}
    for key, path in (("local", "/api/local-servers"), ("packs", "/api/packs"), ("mcp", "/api/mcp"), ("tasks", "/api/tasks/counts")):
        try:
            out[key] = x.get(path)
        except Exception as e:
            out[key] = {"error": str(e)}
    if not (out["local"] or {}).get("answering"):        # an older Xod without the Local panel: its settings say
        try:
            cfg = x.get("/api/config")
            out["local"] = {"answering": {"name": cfg.get("llm_provider"), "model": str(cfg.get("llm_model") or "").split("/")[-1],
                                          "where": "from settings"}}
        except Exception:
            pass
    return out


def show_status(x: Xod, opts: Dict[str, Any]) -> int:
    s = gather_status(x)
    if opts.get("json"):
        say(json.dumps(s, indent=2, default=str))
        return 0
    srv = s["server"]
    say(bold(f"Xod {srv.get('version') or '(older version)'}") + f" at {s['url']}" + dim(f"  started by the {srv['started_by']}"))
    say(dim(f"Data: {s['data_dir']}"))
    local = s.get("local") or {}
    a = local.get("answering") or {}
    if a:
        ready = "" if a.get("ready") is not False else red("  (no API key yet: add one in Settings)")
        say(f"Answering chats: {bold(a.get('name') or a.get('provider') or '?')}" + (f" · {a['model']}" if a.get("model") else "")
            + dim(f" ({a.get('where')})") + ready)
    if local.get("servers"):
        say("Model servers on this computer:")
        for sv in local["servers"]:
            if not sv.get("running"):
                say(dim(f"  {sv['name']:<16} off"))
                continue
            models = ", ".join(m.get("label") or m.get("id") or "" for m in sv.get("models") or []) or "nothing loaded"
            mem = f" · {sv['memory_gb']} GB" if sv.get("memory_gb") else ""
            use = green("  answering") if sv.get("in_use") else (yellow("  loaded, not used") if sv["id"] in (local.get("unused") or []) else "")
            say(f"  {sv['name']:<16} on · {models}{mem}{use}")
    packs = (s.get("packs") or {}).get("packs") or []
    if packs:
        on = [p["name"] for p in packs if p.get("enabled")]
        say(f"Add-ons on ({len(on)} of {len(packs)}): " + (", ".join(on) if on else dim("none")))
    mcp = s.get("mcp") or {}
    apps = [m for m in mcp.get("servers") or [] if m.get("enabled", True)]
    if apps:
        say("Connected apps: " + ", ".join(m["name"] + ("" if m.get("connected") else dim(" (not connected)")) for m in apps))
    if mcp.get("pending"):
        say(yellow(f"Waiting for your OK: {len(mcp['pending'])} (xod approvals)"))
    counts = s.get("tasks") or {}
    if isinstance(counts, dict) and "error" not in counts:
        busy = {k: v for k, v in counts.items() if k in ("working", "waiting", "queued") and v}
        say("Tasks: " + (", ".join(f"{v} {k}" for k, v in busy.items()) if busy else dim("nothing running")))
    return 0


# ------------------------------------------------------------------ arguments
def parse(argv: List[str]) -> Dict[str, Any]:
    opts: Dict[str, Any] = {"words": []}
    flags = {"-y": "yes", "--yes": "yes", "-c": "continue", "--continue": "continue", "--cloud": "cloud", "--json": "json",
             "--no-wait": "no_wait", "--no-color": "no_color", "-h": "help", "--help": "help", "-V": "version", "--version": "version"}
    it = iter(argv)
    for a in it:
        if a == "--":
            opts["words"].extend(it)
            break
        if a in flags:
            opts[flags[a]] = True
        elif a == "--url" or a.startswith("--url="):
            opts["url"] = a.split("=", 1)[1] if "=" in a else next(it, None)
            if not opts["url"]:
                raise SystemExit("--url needs an address, e.g. --url http://127.0.0.1:8000")
        elif a.startswith("-") and len(a) > 1 and not opts["words"]:
            raise SystemExit(f"Unknown option {a}. xod --help lists them.")
        else:
            opts["words"].append(a)
    return opts


def main(argv: Optional[List[str]] = None) -> int:
    opts = parse(sys.argv[1:] if argv is None else argv)
    Out.setup(opts.get("no_color", False))
    if opts.get("version"):
        say(f"Xod {VERSION}")
        return 0
    words = opts["words"]
    cmd = words[0] if words and words[0] in COMMANDS else None
    rest = " ".join(words[1:] if cmd else words).strip()
    if opts.get("help") or cmd == "help":
        say(HELP)
        return 0
    if cmd == "stop":
        return cmd_stop(opts)
    if cmd == "start":
        return cmd_start(opts)
    try:
        x = Xod(server(opts.get("url")))
        if cmd == "status":
            return show_status(x, opts)
        if cmd == "tasks":
            show_tasks(x)
            return 0
        if cmd == "approvals":
            return cmd_approvals(x, opts)
        if cmd == "task":
            if not rest:
                say('Say what to do: xod task "find three flights to Lisbon in May"')
                return 2
            return cmd_task(x, rest, opts)
        if rest:
            if not sys.stdin.isatty():
                piped = sys.stdin.read()
                if piped.strip():
                    rest = f"{rest}\n\n```\n{piped.strip()}\n```"
                Answers.use_terminal()                 # the input is used up: ask y/N on the terminal, if there is one
            return cmd_ask(x, rest, opts)
        if cmd == "ask":
            say('Say what to ask: xod ask "status of my order"')
            return 2
        return cmd_chat(x, opts)
    except httpx.HTTPStatusError as e:
        say(red(f"Xod answered {e.response.status_code}: {short(e.response.text, 300)}"), err=True)
        return 1
    except httpx.HTTPError as e:
        say(red(f"Couldn't reach Xod: {e}"), err=True)
        return 1
    except KeyboardInterrupt:
        say()
        return 130


if __name__ == "__main__":
    sys.exit(main())
