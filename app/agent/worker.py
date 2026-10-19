"""
The worker: jobs done on this computer (install something, set up a project, build it, run it, fix it), in one loop
that keeps going until the job checks out, instead of a plan written up front and run blind.

Ideas taken from how Replit's agent works (their posts on decision-time guidance, self-testing and harness design):

  ready first     what this computer has (package managers, runtimes, admin rights, disk, internet) and whether the
                  chat model can call tools at all, checked before starting and shown as a step. The model plans
                  around what's really there; when the model can't do this kind of work, it says so and why.
  one loop        the model sees every result and picks the next step itself.
  guidance        short hints picked from what just happened ("not recognized" → it isn't installed; winget "No
                  package found" → search for the Id; the same failure twice → change approach), added to the next
                  call only and then dropped, so they never pile up.
  done-check      when it says it's finished, a separate look at the goal and the actual outputs decides whether it
                  is (recognizing done is easier than doing). Not done: it's told what's missing and goes on.
  honest stop     out of steps or truly blocked (a password, an admin prompt nobody approved, a sign-in): what's
                  done, what isn't, and exactly why. "continue" picks it up again.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import socket
import sys
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.services.llm import llm_service, parse_llm_json, JSON_MIN_TOKENS

logger = logging.getLogger(__name__)

WIN = sys.platform == "win32"
MARK = "<!-- xod-work:"
MAX_STEPS = 30            # model calls in one go
MAX_SECONDS = 25 * 60
MAX_CHECKS = 3            # times the done-check may send it back
KEEP_FULL = 6             # tool results kept whole; older ones shrink so a long job fits the context
TOOLS = ("computer_run_command", "computer_start_job", "computer_wait_for", "computer_check_job", "computer_stop_job",
         "computer_read_file", "computer_write_file", "computer_edit_file", "computer_list_directory",
         "computer_find_files", "web_search")

# ------------------------------------------------------------------ which requests are work on the computer
_INSTALL_RE = re.compile(r"\b(?:install|uninstall|reinstall)(?:ed|ing|s)?\b", re.I)
_DEV_RE = re.compile(r"\b(?:npm|npx|pnpm|yarn|pip3?|pipx|uv|winget|choco(?:latey)?|scoop|brew|homebrew|apt(?:-get)?|conda|"
                     r"venv|virtual ?env(?:ironment)?|git clone|clone (?:the |this |a )?repo|docker|node(?:\.js)? project|"
                     r"python project|dependencies|compile|cargo|dotnet|requirements\.txt|package\.json|dev server|"
                     r"set ?up (?:a |the |my )?(?:project|repo|environment|dev|server|database))\b"
                     # "serve it on port 8765 and check it answers": a real server, not a page in the preview
                     r"|\bon port \d{2,5}\b|\blocalhost:\d{2,5}\b|\b(?:serve|host|run)\b[^.?!]{0,60}\bport\b"
                     r"|\b(?:web|http|local|dev|api|flask|express|fastapi|django) ?server\b", re.I)
# Installing a model, a connector or an add-on has its own place in Xod.
_NOT_HERE_RE = re.compile(r"\b(?:model|llm|connector|connection|add-?on|plugin|extension for xod|xod update|update xod)\b", re.I)
_CONTINUE_RE = re.compile(r"^\s*(?:ok(?:ay)?[,.!]?\s*)?(?:please\s+)?(?:continue|keep going|go on|carry on|resume|finish (?:it|that|the job)|"
                          r"try again|do it|go ahead|yes,? (?:continue|keep going|go ahead)|it didn'?t work)\b", re.I)


def _last_job(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for m in reversed(messages[:-1]):
        c = m.get("content")
        if m.get("role") == "assistant" and isinstance(c, str):
            if MARK in c:
                try:
                    return json.loads(c.split(MARK, 1)[1].split("-->", 1)[0])
                except ValueError:
                    return None
            return None
    return None


def route(query: str, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """{"goal": …} when this is a job to do on the computer, or "continue" after one that stopped."""
    q = query or ""
    if _CONTINUE_RE.search(q) and len(q) < 120:
        prev = _last_job(messages)
        if prev and prev.get("goal"):
            return {"goal": prev["goal"], "resume": prev.get("left") or "", "extra": "" if _CONTINUE_RE.fullmatch(q.strip()) else q}
    if _NOT_HERE_RE.search(q):
        return None
    if _INSTALL_RE.search(q) or _DEV_RE.search(q):
        return {"goal": q.strip(), "resume": "", "extra": ""}
    return None


# ------------------------------------------------------------------ ready first
_PROGRAMS = {
    "package managers": ["winget", "choco", "scoop"] if WIN else ["brew", "port", "apt-get", "dnf"],
    "languages": ["python", "py", "node", "npm", "uv", "pipx", "git", "java", "go", "cargo", "dotnet", "docker"] if WIN else
                 ["python3", "node", "npm", "uv", "pipx", "git", "java", "go", "cargo", "dotnet", "docker"],
}


def _fresh_path() -> str:
    """PATH as it is now (an install a minute ago added to it), not as it was when Xod started."""
    if not WIN:
        return os.environ.get("PATH", "")
    try:
        import winreg
        parts = []
        for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                          (winreg.HKEY_CURRENT_USER, r"Environment")):
            try:
                with winreg.OpenKey(root, key) as k:
                    parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
            except OSError:
                pass
        return ";".join(parts + [os.environ.get("PATH", "")])
    except Exception:
        return os.environ.get("PATH", "")


def _is_admin() -> bool:
    try:
        if WIN:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


def _online() -> bool:
    for host in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            with socket.create_connection(host, timeout=2):
                return True
        except OSError:
            continue
    return False


def _readiness_now() -> Dict[str, Any]:
    path = _fresh_path()
    found = {group: [p for p in names if shutil.which(p, path=path)] for group, names in _PROGRAMS.items()}
    if WIN and "py" in found["languages"] and "python" in found["languages"]:
        found["languages"].remove("py")
    home = os.path.expanduser("~")
    try:
        free_gb = round(shutil.disk_usage(home).free / 1024 ** 3)
    except OSError:
        free_gb = None
    return {"os": "Windows" if WIN else ("macOS" if sys.platform == "darwin" else "Linux"),
            "shell": "Windows PowerShell" if WIN else os.path.basename(os.environ.get("SHELL", "zsh")),
            "home": home, "admin": _is_admin(), "online": _online(), "free_gb": free_gb, **found}


async def readiness() -> Dict[str, Any]:
    """What this computer has and what the chat model can do, for the job about to start."""
    ready = await asyncio.to_thread(_readiness_now)
    model = {"name": "", "tools": None, "problem": None}
    try:
        from app.services import capabilities
        chat = await capabilities._chat_model()
        model = {"name": f"{chat['model']} ({chat['server']})", "tools": chat.get("tools"), "problem": chat.get("problem")}
    except Exception as e:
        logger.debug(f"model check skipped: {e}")
    ready["model"] = model
    blockers = []
    if model.get("problem"):
        blockers.append(f"the chat model isn't working: {model['problem']}")
    elif model.get("tools") is False:
        blockers.append(f"the chat model, {model['name']}, can't call tools, so it can't run commands or change files. "
                        "Pick one that can in Tools → AI models (the “calls tools” badge)")
    ready["blockers"] = blockers
    notes = []
    if not ready["online"]:
        notes.append("no internet connection, so nothing can be downloaded")
    if not ready["package managers"]:
        notes.append("no package manager" + (" (winget comes with Windows' App Installer)" if WIN else " (Homebrew is the usual one)"))
    if ready["free_gb"] is not None and ready["free_gb"] < 5:
        notes.append(f"only {ready['free_gb']} GB free on the disk")
    if not ready["admin"]:
        notes.append("running without admin rights: an install that needs them shows a Windows permission prompt (UAC) on screen"
                     if WIN else "running without admin rights: installs that need sudo need the person's password")
    ready["notes"] = notes
    return ready


def _ready_text(r: Dict[str, Any]) -> str:
    def lst(xs):
        return ", ".join(xs) if xs else "none"
    return (f"- {r['os']}, commands run in {r['shell']}; home folder {r['home']}\n"
            f"- Package managers: {lst(r['package managers'])}\n"
            f"- Installed: {lst(r['languages'])}\n"
            f"- Internet: {'yes' if r['online'] else 'NO'}; free disk: {r['free_gb']} GB; admin: {'yes' if r['admin'] else 'no'}")


def _ready_summary(r: Dict[str, Any]) -> str:
    bits = [f"{r['os']}", f"package managers: {', '.join(r['package managers']) or 'none'}",
            f"has {', '.join(r['languages'][:8]) or 'no languages'}", "online" if r["online"] else "offline"]
    return "; ".join(bits)


# ------------------------------------------------------------------ the prompt
SYSTEM = """You are Xod, doing a job directly on this person's computer: you run the commands and change the files yourself.
It's {now}.

This computer, checked just now:
{ready}

How you work:
- Do the job yourself with the tools. Never tell the person to run a command or install something: you can, so do it.
- One step at a time: run something, read the result, then decide the next step.
- Before installing, check whether it's already there. Use the package manager this computer has. Prefer installing for this user only.
- Long things (big installs, builds, servers that keep running): computer_start_job, then computer_wait_for.
- Give every command in a project folder its cwd (or use full paths): each command starts fresh.
- When something fails, read the error and change the approach. Never run the same failing command again unchanged.
  If you don't know the fix, look it up with web_search.
- Finish by proving it works: run it, import it, print its version, or call it, and read the output.
- Stop and ask the person only for what you truly can't do: their password, an admin prompt nobody approved, signing in,
  paying, or a choice only they can make. Then say exactly what's needed and why.
- When it's done, reply briefly: what you did, how you checked it works, and where things are.
{shell}"""

SHELL_WIN = """
Windows notes:
- Commands run in Windows PowerShell 5.1. `&&` and `||` work (Xod converts them).
- winget: find the exact Id with `winget search <name>`, then
  `winget install --id <Id> -e --scope user --accept-source-agreements --accept-package-agreements --disable-interactivity`
  (drop --scope user if that package can't install per user). A new program is on PATH for the next command.
- A Python virtual environment: call its own python, e.g. `<venv>\\Scripts\\python.exe -m pip install <pkg>`.
- npm: `npm init -y` before `npm install` in a new folder; for "require" use a package version that supports CommonJS
  (chalk 5+ is ESM only: use chalk@4 with require, or write index.mjs with import)."""

SHELL_MAC = """
Mac notes:
- Commands run in zsh. Homebrew: `brew install <name>`; `brew search <name>` when unsure of the name.
- A Python virtual environment: call its own python, e.g. `<venv>/bin/python -m pip install <pkg>`."""


# ------------------------------------------------------------------ decision-time guidance
def _out(res: Any) -> str:
    if not isinstance(res, dict):
        return str(res or "")
    return "\n".join(str(res.get(k) or "") for k in ("stdout", "stderr", "error", "output", "new_output", "content") if res.get(k))


def _failed(res: Any) -> bool:
    if not isinstance(res, dict):
        return False
    if res.get("success") is False or res.get("error"):
        return True
    code = res.get("exit_code")
    return code not in (None, 0) or res.get("status") in ("failed", "timed_out", "error")


def _fp(name: str, args: Dict[str, Any]) -> str:
    return name + json.dumps(args, sort_keys=True, default=str)


_HINTS = [
    ("missing", re.compile(r"is not recognized as (?:the name of )?a cmdlet|is not recognized as an internal or external command|"
                           r"command not found|No such file or directory: '[\w.-]+'$|CommandNotFoundException", re.I | re.M),
     "That program isn't installed here (or isn't on PATH). Install it with this computer's package manager, or do it another way. "
     "After an install, the next command sees the new PATH."),
    ("winget_id", re.compile(r"No package found matching input criteria|No installed package found", re.I),
     "winget doesn't know that Id. Run `winget search <name>` and install with the exact Id from its list."),
    ("winget_scope", re.compile(r"No applicable installer found", re.I),
     "That package has no per-user installer: run the same winget install again without --scope user."),
    ("file_not_folder", re.compile(r"is a file, not a folder|WinError 183|Not a directory|NotADirectoryError|Path is not a directory", re.I),
     "Something in that path is a FILE where a folder should be (often an empty file made by mistake). List the parent "
     "folder, delete that file (or use another folder name), then make the folder with computer_manage_file action mkdir."),
    ("rights", re.compile(r"Access is denied|requires elevation|run as administrator|administrator privileges|EACCES|"
                          r"Permission denied|UnauthorizedAccess|0x80070005", re.I),
     "That needs more rights. Try a per-user install (winget --scope user, pip install --user, npm without -g) or a folder this "
     "user owns. If it really needs admin, say so plainly instead of guessing."),
    ("timeout", re.compile(r"timed out after \d+ seconds", re.I),
     "That was stopped for taking too long. Run it with computer_start_job and wait with computer_wait_for."),
    ("wrong_place", re.compile(r"Cannot find module '[A-Z]:\\|ENOENT|Could not find a part of the path|cannot find path|"
                               r"no such file or directory", re.I),
     "It ran in the wrong folder, or the file isn't where you think. List the folder, then pass cwd or use full paths."),
    ("esm", re.compile(r"ERR_REQUIRE_ESM|require\(\) of ES Module|Cannot use import statement outside a module", re.I),
     "That package is ESM-only or the file uses import: use `import` in a .mjs file, or install an older CommonJS version."),
    ("network", re.compile(r"getaddrinfo|Could not resolve host|ENOTFOUND|Temporary failure in name resolution|"
                           r"No connection could be made|ECONNREFUSED|SSL: CERTIFICATE", re.I),
     "That's a network problem, not your command. Check the address; if the internet is down, say so."),
    ("quotes", re.compile(r"TerminatorExpectedAtEndOfString|missing the terminator|unexpected EOF while looking for matching|"
                          r"unterminated (?:string|quoted)", re.I),
     "A quote in that command is never closed. Fix the quoting, or put the code in a file and run the file."),
    ("prompted",re.compile(r"\[Y\]es|\(y/n\)|\[y/N\]|Press any key|Do you agree|Are you sure", re.I),
     "It waited for a yes/no answer that nobody can give. Use its non-interactive flag (-y, --yes, --accept-*, -Force)."),
]
_TELLS_USER_RE = re.compile(r"\b(?:you (?:can|could|should|need to|will need to|'ll need to|may need to) (?:run|install|open|type|execute|download)|"
                            r"please (?:run|install|open|execute|download)|run the following|open (?:a |the )?(?:terminal|powershell|command prompt)|"
                            r"manually (?:install|run|download))\b", re.I)
_REAL_BLOCKER_RE = re.compile(r"password|admin(?:istrator)?|UAC|elevat|sign[- ]?in|log ?in|account|pay|credit card|license key|"
                              r"2fa|captcha|api key|token|choose|choice|which one|decide|prefer|physical|plug", re.I)


def guidance(log: List[Dict[str, Any]], content: str = "", told: Optional[Dict[str, int]] = None) -> List[str]:
    """Short hints for the next model call, from what just happened. Each one at most twice in a job."""
    told = told if told is not None else {}
    hints: List[str] = []

    def add(key: str, text: str):
        if told.get(key, 0) < 2 and text not in hints:
            told[key] = told.get(key, 0) + 1
            hints.append(text)

    recent = [e for e in log[-4:] if e.get("new")]
    for e in recent:
        if not e["failed"]:
            continue
        text = e["out"]
        for key, rx, hint in _HINTS:
            if rx.search(text):
                add(key, hint)
                break
    if log:
        fails = {}
        for e in log:
            if e["failed"]:
                fails[e["fp"]] = fails.get(e["fp"], 0) + 1
        starts = [e for e in log if e["name"] == "computer_start_job"]
        if len(starts) >= 2 and log[-1]["name"] == "computer_start_job" and log[-1].get("new"):
            add("restart", "That background job was already started; starting it again just fights over the same port. "
                           "Check it with computer_check_job, stop the extra one, and prove it works by fetching the page: "
                           + ("`(Invoke-WebRequest http://127.0.0.1:<port>/ -UseBasicParsing).Content`" if WIN else "`curl -s http://127.0.0.1:<port>/`") + ".")
        if len(log) >= 2 and log[-1]["fp"] == log[-2]["fp"] and not log[-1]["failed"] and log[-1].get("new"):
            add("again", "You just ran exactly that and got the same result; running it again won't change anything. "
                         "Use what it showed and take the next step.")
        if any(n >= 2 for n in fails.values()):
            add("stuck", "The same thing has failed twice. Don't repeat it: check your assumptions (list the folder, check "
                         "versions, read the error again) and try a different way, or look the error up with web_search.")
        streak = 0
        for e in reversed(log):
            if not e["failed"]:
                break
            streak += 1
        if streak >= 3:
            add("lookup", f"The last {streak} steps failed. Look up this exact error with web_search before trying again.")
    if content and _TELLS_USER_RE.search(content) and not _REAL_BLOCKER_RE.search(content):
        add("do_it", "You have a shell on this computer: do that yourself with the tools instead of asking the person to.")
    return hints


# ------------------------------------------------------------------ safety: what it never runs on its own
_NEVER_RE = re.compile(
    r"\bformat(?:\.com)?\s+[a-z]:|\bdiskpart\b|\bbcdedit\b|\bFormat-Volume\b|\bClear-Disk\b|\bInitialize-Disk\b|"
    r"\bvssadmin\s+delete\b|\bcipher\s+/w\b|\breg(?:\.exe)?\s+delete\s+HK(?:LM|EY_LOCAL_MACHINE|CR|U\\Software\\Microsoft\\Windows)\b|"
    r"\b(?:Stop|Restart)-Computer\b|\bshutdown(?:\.exe)?\s|\bRemove-Item\b[^;|]*\s(?:-Path\s+)?['\"]?(?:[A-Z]:\\?|[A-Z]:\\Windows|"
    r"[A-Z]:\\Users|[A-Z]:\\Program Files[^'\"\s]*|~|\$env:USERPROFILE|\$HOME)['\"]?(?:\s|$)[^;|]*-Recurse|"
    r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+['\"]?(?:/|~/?|\$HOME/?|/\*|~/\*|\$HOME/\*)['\"]?(?:\s|$)|\bmkfs\b|\bdd\s+if=.*\bof=/dev/|"
    r"\bSet-ExecutionPolicy\s+Unrestricted\s+-Scope\s+LocalMachine\b|\bDisable-ComputerRestore\b|\bSet-MpPreference\b.*-Disable|"
    r"\bnetsh\s+advfirewall\s+set\s+\w+\s+state\s+off\b|\bcsrutil\s+disable\b|\bspctl\s+--master-disable\b",
    re.I)


def refused(name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A command that could wipe a disk, the whole home folder or the system, or switch off the computer's protection."""
    if name not in ("computer_run_command", "computer_start_job"):
        return None
    cmd = str(args.get("command") or "")
    if _NEVER_RE.search(cmd):
        return {"success": False, "refused": True,
                "error": "Xod doesn't run this on its own: it could wipe a disk or the system, shut the computer down, or switch "
                         "off its protection. If the person really wants it, they need to ask for exactly this and run it themselves."}
    return None


# ------------------------------------------------------------------ the done-check
CHECK = """You check whether a job on a computer is really finished. Judge only from the outputs below, not from what the
worker says. Finished means: everything the person asked for is done, AND something was run whose output proves it works
(a version printed, an import that worked, the program's own output, a page that answered).
For a server or web page, proof is the page's content fetched and shown in an output; "the port is open" or "it's
listening" is not proof. A claim in the worker's message is never proof.

The person asked: {goal}

What was run, with the results (oldest first):
{steps}

The worker's last message: {final}

Answer with JSON only:
{{"done": true or false, "proof": "the output that shows it works, quoted briefly (empty if none)",
 "missing": "what isn't done or isn't proven yet, as an instruction for the worker (empty if done)",
 "needs_person": true or false, "why": "only when needs_person: what exactly the person must do and why"}}"""


def _steps_text(log: List[Dict[str, Any]], limit: int = 5000) -> str:
    rows = []
    for i, e in enumerate(log, 1):
        what = e["args"].get("command") or e["args"].get("filepath") or e["args"].get("query") or json.dumps(e["args"], default=str)[:160]
        status = "FAILED" if e["failed"] else "ok"
        if e.get("exit") is not None:
            status += f", exit {e['exit']}"
        out = re.sub(r"\s+\n", "\n", e["out"]).strip()
        rows.append(f"{i}. {e['name']}: {str(what)[:200]} [{status}]\n   {out[-500:] if out else '(no output)'}")
    text = "\n".join(rows)
    return text[-limit:]


async def done_check(goal: str, log: List[Dict[str, Any]], final: str) -> Dict[str, Any]:
    if not log:
        return {"done": False, "missing": "Nothing has been run yet. Do the job with the tools.", "needs_person": False}
    prompt = CHECK.format(goal=goal, steps=_steps_text(log), final=(final or "(none)")[:800])
    try:
        reply = await llm_service.chat_completion(messages=[{"role": "user", "content": prompt}], temperature=0.0,
                                                  max_tokens=JSON_MIN_TOKENS, think=False, task="work_check")
        verdict = parse_llm_json(reply.get("content", "")) or {}
    except Exception as e:
        logger.warning(f"done-check failed: {e}")
        verdict = {}
    if not isinstance(verdict, dict) or "done" not in verdict:
        # No verdict: trust a clean finish, but not one whose last step failed.
        last_ok = not log[-1]["failed"]
        return {"done": last_ok, "proof": "", "missing": "" if last_ok else "The last step failed; fix it and check again.",
                "needs_person": False}
    verdict["done"] = verdict.get("done") in (True, "true", "yes")
    verdict["needs_person"] = verdict.get("needs_person") in (True, "true", "yes")
    if verdict["needs_person"] and not _REAL_BLOCKER_RE.search(str(verdict.get("why") or "")):
        # "The person must review index.js for syntax errors": that's the worker's job, not a reason to stop.
        verdict.update(needs_person=False, missing=str(verdict.get("missing") or verdict.get("why") or
                                                         "It isn't proven to work yet; find out why and fix it."))
    if verdict["done"] and log[-1]["failed"] and not any(not e["failed"] for e in log[-3:]):
        verdict.update(done=False, missing="The last steps failed, so it isn't proven to work. Fix that and check again.")
    return verdict


# ------------------------------------------------------------------ the loop
def calls_in_text(content: str, allowed: set) -> List[Dict[str, Any]]:
    """Tool calls the model wrote out as text (```json {"name": "computer_run_command", "arguments": {…}}```) instead of
    making them: Qwen does this now and then, and the job stalled with every step "not done yet"."""
    out, dec, i = [], json.JSONDecoder(), 0
    text = content or ""
    while True:
        i = text.find("{", i)
        if i < 0:
            break
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        i = end
        if isinstance(obj, dict) and obj.get("name") in allowed:
            args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            out.append({"id": f"text_{len(out)}", "type": "function",
                        "function": {"name": obj["name"], "arguments": json.dumps(args if isinstance(args, dict) else {})}})
    if not out and "computer_run_command" in allowed:
        # "Let's create the file… ```powershell\nSet-Content …\n```": a command it means to run, written as a code block.
        m = re.search(r"```(?:powershell|pwsh|ps1|bash|sh|zsh|shell|cmd|console)\s*\n(.+?)```", text, re.S | re.I)
        if m and re.search(r"\b(?:let'?s|we'll|we will|i'll|i will|next|first|now)\b", text[:m.start()], re.I):
            cmd = "\n".join(l for l in m.group(1).strip().splitlines() if l.strip() and not l.lstrip().startswith("#"))
            if cmd and len(cmd) < 2000:
                out.append({"id": "text_cmd", "type": "function",
                            "function": {"name": "computer_run_command", "arguments": json.dumps({"command": cmd.replace("\n", "; ")})}})
    return out[:4]


def _shrink(convo: List[Dict[str, Any]]) -> None:
    """Older tool results get short, so thirty steps still fit a local model's context."""
    tool_msgs = [m for m in convo if m.get("role") == "tool"]
    for m in tool_msgs[:-KEEP_FULL]:
        c = m.get("content") or ""
        if len(c) > 600:
            m["content"] = c[:250] + " … " + c[-300:]


def _summary(name: str, res: Any) -> str:
    if not isinstance(res, dict):
        return str(res)[:140]
    if res.get("refused"):
        return "Not run: too risky to do on its own"
    if res.get("error"):
        return "✗ " + str(res["error"])[:140]
    if "exit_code" in res:
        tail = (res.get("stdout") or res.get("stderr") or "").strip().splitlines()
        return f"exit {res['exit_code']}" + (f" · {tail[-1][:110]}" if tail else "")
    if res.get("job_id") and res.get("status"):
        return f"{res.get('status')} ({res['job_id']})"
    return str(res.get("summary") or res.get("message") or "Done")[:140]


def _say_ready(r: Dict[str, Any]) -> str:
    parts = [_ready_summary(r)]
    if r.get("notes"):
        parts.append("; ".join(r["notes"]))
    return " — ".join(parts)


def _final_text(answer: str, verdict: Optional[Dict[str, Any]], goal: str, left: str, stopped: str) -> str:
    text = (answer or "").strip()
    if stopped:
        text = (text + "\n\n" if text else "") + stopped
    elif verdict and verdict.get("done") and verdict.get("proof"):
        text += f"\n\n**Checked:** {str(verdict['proof']).strip()[:300]}"
    return text + "\n\n" + MARK + json.dumps({"goal": goal[:600], "left": left[:600]}) + " -->"


async def stream(query: str, messages: List[Dict[str, Any]], decision: Dict[str, Any], execute_tool,
                 spoken: bool = False) -> AsyncGenerator[Dict[str, Any], None]:
    goal = decision["goal"]
    started = time.time()

    # Ready first: what's here, and can this model do it at all?
    yield {"type": "status", "content": "Checking what this computer has…"}
    yield {"type": "tool_call", "name": "xod_readiness", "args": {}, "id": "work_ready"}
    ready = await readiness()
    yield {"type": "tool_result", "name": "xod_readiness", "id": "work_ready", "summary": _say_ready(ready), "data": ready}
    if ready["blockers"]:
        why = "; ".join(ready["blockers"])
        yield {"type": "content", "content": f"I can't do this one yet: {why}."}
        yield {"type": "done"}
        return

    from app.agent.tools import TOOL_DEFINITIONS
    tools = [t for t in TOOL_DEFINITIONS if t["function"]["name"] in TOOLS]
    try:
        from app.services import capabilities
        if not capabilities._browser():
            tools = [t for t in tools if t["function"]["name"] != "web_search"]
    except Exception:
        pass
    # The team (services/team.py): with more than one brain (or a teammate agent), pieces can be handed off.
    team_note = ""
    try:
        from app.services import team
        if await team.team_on():
            tools = tools + [team.DELEGATE_TOOL]
            t = await team.tiers()
            mates = [a["server"] for a in team.outside_agents() if a["teammate"]]
            team_note = ("\nYour team: team_delegate hands a self-contained piece to a subagent (small: "
                         f"{t['small']['model']}, standard: {t['standard']['model']}, large: {t['large']['model']})"
                         + (f"; outside teammates: {', '.join(mates)}" if mates else "")
                         + ". Use it for independent pieces that can run side by side, or a hard piece for the large tier. "
                           "Small jobs: just do them yourself.")
    except Exception as e:
        logger.debug(f"team off: {e}")
    allowed = {t["function"]["name"] for t in tools}
    from app import platform_info
    system = SYSTEM.format(now=time.strftime("%A %B %d, %Y, %I:%M %p"), ready=_ready_text(ready),
                           shell=(SHELL_WIN + "\n" + platform_info.SHELL_NOTE) if WIN else SHELL_MAC) + team_note
    if ready["notes"]:
        system += "\nKeep in mind: " + "; ".join(ready["notes"]) + "."
    if spoken:
        system += "\nYour final reply will be read aloud: two short sentences."
    convo: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages[-6:-1]:
        c = m.get("content")
        if m.get("role") in ("user", "assistant") and isinstance(c, str) and c.strip():
            convo.append({"role": m["role"], "content": re.sub(r"<!--.*?-->", "", c, flags=re.S)[:1500]})
    ask = goal
    if decision.get("resume"):
        ask += f"\n\n(This continues earlier work. Where it stopped: {decision['resume']})"
    if decision.get("extra"):
        ask += f"\n\nThe person adds: {decision['extra']}"
    convo.append({"role": "user", "content": ask})

    log: List[Dict[str, Any]] = []
    told: Dict[str, int] = {}
    checks = 0
    answer, verdict, stopped, left = "", None, "", ""
    nudged_empty = 0
    held: Optional[Dict[str, Any]] = None
    subs = 0
    from app.services import usage as usage_log
    usage_log.purpose.set("worker")          # the Usage tab: these calls are the worker's
    yield {"type": "status", "content": "Working on it…"}

    for step in range(MAX_STEPS):
        if time.time() - started > MAX_SECONDS:
            break
        hints = guidance(log, "", told)
        call_msgs = convo + ([{"role": "user", "content": "(Note from Xod, not from the person) " + " ".join(hints)}] if hints else [])
        for e in log:
            e["new"] = False
        calls, content = [], ""
        async for ev in llm_service.deliberate_stream(messages=call_msgs, tools=tools, tool_choice="auto", temperature=0.1, max_tokens=1500):
            if ev.get("type") == "deliberation_complete":
                calls, content = ev.get("tool_calls") or [], ev.get("content") or ""
            elif ev.get("type") == "error":
                stopped = f"The model stopped: {ev.get('content') or 'no reason given'}."
                break
        if stopped:
            break
        if not calls:
            calls = calls_in_text(content, allowed)
            if calls:
                content = ""          # the text was the calls; nothing to say yet

        if not calls:
            # It says it's done (or that the person should do something). Is it, really?
            nudge = guidance(log, content, told)
            if nudge and any("do that yourself" in n for n in nudge) and step < MAX_STEPS - 1:
                convo.append({"role": "assistant", "content": content})
                convo.append({"role": "user", "content": "(Note from Xod, not from the person) " + " ".join(nudge)})
                continue
            if not log and nudged_empty < 1:
                nudged_empty += 1
                convo.append({"role": "assistant", "content": content})
                convo.append({"role": "user", "content": "(Note from Xod, not from the person) Nothing has been run yet, so nothing "
                                                         "is done. Use the tools to do the job now."})
                continue
            yield {"type": "status", "content": "Checking it really works…"}
            ccid = f"work_check_{checks}"
            yield {"type": "tool_call", "name": "xod_self_check", "args": {"goal": goal[:200]}, "id": ccid}
            verdict = await done_check(goal, log, content)
            yield {"type": "tool_result", "name": "xod_self_check", "id": ccid, "data": verdict,
                   "summary": ("✓ Works: " + str(verdict.get("proof") or "the steps succeeded")[:120]) if verdict.get("done") else
                              ("Needs you: " + str(verdict.get("why") or "")[:120]) if verdict.get("needs_person") else
                              ("Not yet: " + str(verdict.get("missing") or "")[:120])}
            if verdict.get("done") or verdict.get("needs_person"):
                answer = content
                if verdict.get("needs_person") and verdict.get("why") and not verdict.get("done"):
                    left = str(verdict.get("why"))
                    answer = (content.strip() + "\n\n" if content.strip() else "") + f"**What I need from you:** {verdict['why']}"
                break
            checks += 1
            left = str(verdict.get("missing") or "")
            if checks > MAX_CHECKS:
                answer = content
                stopped = f"**Not finished yet:** {left or 'it never proved it works'}. Say **continue** and I'll keep going."
                break
            yield {"type": "status", "content": f"Not done yet: {left[:120]}"}
            convo.append({"role": "assistant", "content": content})
            convo.append({"role": "user", "content": f"(Note from Xod, not from the person) Not finished: {left} Keep going with the tools."})
            continue

        convo.append({"role": "assistant", "content": content or "", "tool_calls": calls})
        # Pieces handed to subagents in this reply run at the same time (services/team.py).
        handoffs = [(i, tc) for i, tc in enumerate(calls[:6]) if tc.get("function", {}).get("name") == "team_delegate"
                    and "team_delegate" in allowed]
        if handoffs:
            from app.services import team
            jobs = []
            for i, tc in handoffs:
                try:
                    a = json.loads(tc["function"].get("arguments") or "{}")
                except ValueError:
                    a = {}
                jobs.append(a if isinstance(a, dict) else {})
            yield {"type": "status", "content": f"Handing {len(jobs)} piece{'s' if len(jobs) != 1 else ''} to the team…"}
            results = []
            async for ev in team.delegate_many(jobs, execute_tool, start_index=subs + 1):
                if ev.get("type") == "_results":
                    results = ev["results"]
                else:
                    yield ev
            subs += len(jobs)
            for (i, tc), a, r in zip(handoffs, jobs, results):
                cid = tc.get("id") or f"work_{step}_{i}"
                log.append({"name": "team_delegate", "args": a, "fp": _fp("team_delegate", a), "failed": not r.get("success"),
                            "out": (r.get("summary") or r.get("error") or "")[-3000:], "exit": None, "new": True})
                from app.services import agent_log
                agent_log.step("team_delegate", a, {**r, "success": r.get("success")})
                convo.append({"role": "tool", "tool_call_id": cid, "name": "team_delegate",
                              "content": json.dumps({k: r.get(k) for k in ("agent", "brain", "success", "summary", "error")}, default=str)[:4000]})
            calls = [tc for tc in calls if tc.get("function", {}).get("name") != "team_delegate"]
        for i, tc in enumerate(calls[:4]):
            name = tc.get("function", {}).get("name", "")
            raw = tc.get("function", {}).get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except ValueError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            cid = tc.get("id") or f"work_{step}_{i}"
            if name not in allowed:
                res: Any = {"success": False, "error": f"{name} isn't a tool here. Use one of: {', '.join(sorted(allowed))}."}
            else:
                yield {"type": "tool_call", "name": name, "args": args, "id": cid}
                res = refused(name, args) or await execute_tool(name, args)
                yield {"type": "tool_result", "name": name, "id": cid, "summary": _summary(name, res), "data": res}
            out = _out(res)
            log.append({"name": name, "args": args, "fp": _fp(name, args), "failed": _failed(res), "out": out[-3000:],
                        "exit": res.get("exit_code") if isinstance(res, dict) else None, "new": True})
            body = json.dumps(res, default=str) if not isinstance(res, str) else res
            convo.append({"role": "tool", "tool_call_id": cid, "name": name, "content": body[:4000]})
            if isinstance(res, dict) and res.get("needs_approval"):
                held = res
                break
        _shrink(convo)
        if held:
            # The safety check (or a connected app) wants the person's OK: stop here, with the card showing.
            answer = (f"**Waiting for your OK** on the card above: {held.get('message') or 'this step needs your approval.'} "
                      "After you approve it, say **continue** and I'll carry on.")
            left = f"waited for approval of {held.get('tool')}"
            break

    if not answer and not stopped:
        # Out of steps or time while still working.
        worked = sum(1 for e in log if not e["failed"])
        left = left or (f"last step: {log[-1]['name']} {str(log[-1]['args'].get('command') or '')[:120]}" if log else "")
        mins = max(1, round((time.time() - started) / 60))
        stopped = (f"**Paused after {len(log)} steps ({mins} min)**, before it was finished and checked"
                   + (f" ({worked} of them worked)" if log else "") + ". Say **continue** and I'll pick up from here.")
    elif stopped and log:
        left = left or "it stopped partway"

    if not answer and log:
        answer = "Here's where it got to:\n" + "\n".join(
            f"- {'✗' if e['failed'] else '✓'} `{str(e['args'].get('command') or e['args'].get('filepath') or e['name'])[:120]}`"
            for e in log[-8:])
    yield {"type": "content", "content": _final_text(answer, verdict, goal, left, stopped)}
    yield {"type": "done"}
