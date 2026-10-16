"""
The worker (agent/worker.py), with a scripted model and real commands: commands written the bash way running in
Windows PowerShell, code written on one line with \\n, which requests are work on the computer, what it never runs,
the hints picked from what just happened (and dropped after one call), the readiness check (a model that can't call
tools says so), the done-check sending it back until something proves it works, "do it yourself" instead of telling
the person to, pausing when out of steps and "continue" picking it up. No real model needed.

  python tests/smoke/run_worker_test.py
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="xod-worker-")
os.environ["XOD_DATA_DIR"] = os.path.join(TMP, "data")
WIN = sys.platform == "win32"

from app.agent import worker  # noqa: E402
from app.agent import tools as agent_tools  # noqa: E402
from app.services import capabilities  # noqa: E402

failures, passed = [], 0


def check(name, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failures.append(name)
        print(f"  ✗ {name}  {detail}")


# ------------------------------------------------------------------ a scripted model
class Script:
    """Each model call takes the next reply: ("tool", name, args) or ("say", text). The done-check takes verdicts."""
    replies, verdicts, seen = [], [], []


def call(name, args):
    return [{"id": f"c{len(Script.seen)}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]


async def fake_deliberate(messages, tools=None, **kw):
    Script.seen.append([dict(m) for m in messages])
    r = Script.replies.pop(0) if Script.replies else ("say", "All done.")
    if r[0] == "tool":
        yield {"type": "deliberation_complete", "tool_calls": call(r[1], r[2]), "content": ""}
    else:
        yield {"type": "deliberation_complete", "tool_calls": [], "content": r[1]}


async def fake_chat(messages, **kw):
    v = Script.verdicts.pop(0) if Script.verdicts else {"done": True, "proof": "ok"}
    return {"content": json.dumps(v)}


worker.llm_service.deliberate_stream = fake_deliberate
worker.llm_service.chat_completion = fake_chat
MODEL = {"model": "scripted", "server": "test", "tools": True, "problem": None}


async def fake_model():
    return dict(MODEL)


capabilities._chat_model = fake_model


async def run(query, history=None):
    msgs = [*(history or []), {"role": "user", "content": query}]
    decision = worker.route(query, msgs) or {"goal": query, "resume": "", "extra": ""}
    evs = []
    async for ev in worker.stream(query, msgs, decision, agent_tools.execute_tool):
        evs.append(ev)
    answer = "".join(e.get("content", "") for e in evs if e["type"] == "content")
    return evs, answer


def results(evs, name=None):
    return [e for e in evs if e["type"] == "tool_result" and (name is None or e["name"] == name)]


def fresh(replies, verdicts=()):
    Script.replies, Script.verdicts, Script.seen = list(replies), list(verdicts), []


# ------------------------------------------------------------------ W1 bash-style commands in PowerShell
print("W1 commands written the bash way")
from app.services.system_control_win import ps_compat  # noqa: E402
check("&& becomes run-if-it-worked", ps_compat("npm init -y && npm install chalk") == "npm init -y; if ($?) { npm install chalk }")
check("&& inside quotes stays", ps_compat('echo "a && b"') == 'echo "a && b"')
check("|| becomes run-if-it-failed", ps_compat("a || b") == "a; if (-not $?) { b }")
check("a chain nests", ps_compat("a && b && c") == "a; if ($?) { b; if ($?) { c } }")
check("venv activate becomes Activate.ps1", ps_compat(r"C:\p\venv\Scripts\activate && pip -V") == r". C:\p\venv\Scripts\Activate.ps1; if ($?) { pip -V }")
check("`where jq` is where.exe, not Where-Object", ps_compat("where jq") == "where.exe jq" and ps_compat("x | where { $_ }") == "x | where { $_ }")
if WIN:
    from app.services.system_control_win import shell_argv
    p = subprocess.run(shell_argv("where definitely-not-a-program-xyz"), capture_output=True, text=True)
    check("in PowerShell: `where` a missing program fails", p.returncode != 0, p.stdout + p.stderr)
    p = subprocess.run(shell_argv("cmd /c exit 1 && echo SHOULD-NOT-RUN"), capture_output=True, text=True)
    check("in PowerShell: a failure stops the chain", "SHOULD-NOT-RUN" not in p.stdout and p.returncode != 0, p.stdout)
    p = subprocess.run(shell_argv("echo one && echo two"), capture_output=True, text=True)
    check("in PowerShell: both run when the first works", p.stdout.split() == ["one", "two"], p.stdout + p.stderr)

# ------------------------------------------------------------------ W2 code on one line
print("W2 code written on one line with \\n")
nl = agent_tools._real_newlines
check("JS statements get real lines", nl("x.js", "const a = require('a');\\n\\nconsole.log(a);").count("\n") == 2)
check("\\n inside a string stays", nl("x.py", 'print("a\\nb")') == 'print("a\\nb")')
check("an apostrophe in a comment opens no string", nl("x.py", "# don't\\nprint(1)") == "# don't\nprint(1)")
check("notes still become lines", nl("n.txt", "buy bread\\nwalk the dog") == "buy bread\nwalk the dog")

# ------------------------------------------------------------------ W3 which requests are work on the computer
print("W3 routing")
for q in ("Install jq", "set up a python virtual environment in C:\\x", "Make a Node project and install chalk",
          "serve the folder on port 8765 and check it answers", "is ffmpeg installed?", "git clone the repo and run its tests"):
    check(f"worker: {q}", bool(worker.route(q, [{"role": "user", "content": q}])))
for q in ("install the Notion connector", "download a new model", "what's the weather tomorrow", "build me a timer app",
          "update my dentist reminder", "continue"):
    check(f"not the worker: {q}", worker.route(q, [{"role": "user", "content": q}]) is None)
hist = [{"role": "user", "content": "install jq"},
        {"role": "assistant", "content": 'Paused. <!-- xod-work:{"goal": "install jq", "left": "check the version"} -->'},
        {"role": "user", "content": "continue"}]
r = worker.route("continue", hist)
check("continue after a paused job picks it up", r and r["goal"] == "install jq" and r["resume"] == "check the version", r)

# ------------------------------------------------------------------ W4 what it never runs
print("W4 never on its own")
drive = "C:" + "\\"
for c in ("Remove-Item " + drive + "Users -Recurse -Force", "rm -rf /", "format d:", "Stop-Computer", "diskpart",
          "reg delete HKLM\\Software\\Thing /f", "Set-MpPreference -DisableRealtimeMonitoring $true"):
    check(f"refused: {c}", bool(worker.refused("computer_run_command", {"command": c})))
for c in ("Remove-Item " + drive + "XodWork\\build -Recurse", "rm -rf ./build", "winget install --id jqlang.jq -e", "npm install"):
    check(f"allowed: {c}", worker.refused("computer_run_command", {"command": c}) is None)

# ------------------------------------------------------------------ W5 hints from what just happened
print("W5 decision-time hints")


def entry(cmd, out, failed=True):
    return {"name": "computer_run_command", "args": {"command": cmd}, "fp": cmd, "failed": failed, "out": out, "exit": 1 if failed else 0, "new": True}


h = worker.guidance([entry("jq --version", "The term 'jq' is not recognized as the name of a cmdlet")])
check("'not recognized' → it isn't installed", any("isn't installed" in x for x in h), h)
h = worker.guidance([entry("winget install --id x.jq", "No package found matching input criteria.")])
check("winget 'No package found' → search for the Id", any("winget search" in x for x in h), h)
h = worker.guidance([entry("python -c \"x", "TerminatorExpectedAtEndOfString")])
check("an unclosed quote → fix the quoting", any("quote" in x for x in h), h)
h = worker.guidance([entry("node a.js", "boom"), entry("node a.js", "boom")])
check("the same failure twice → change approach", any("failed twice" in x for x in h), h)
h = worker.guidance([entry("dir", "a b", False), entry("dir", "a b", False)])
check("the same command again → use what it showed", any("won't change anything" in x for x in h), h)
h = worker.guidance([], "You can run `winget install jq` in a terminal.")
check("'you can run…' → do it yourself", any("do that yourself" in x for x in h), h)
h = worker.guidance([], "I need your administrator password: you can run it as admin.")
check("a real blocker isn't pushed back", not any("do that yourself" in x for x in h), h)
told = {}
for _ in range(4):
    last = worker.guidance([entry("jq", "is not recognized as the name of a cmdlet")], "", told)
check("a hint is given at most twice", told.get("missing") == 2 and not last, told)

# ------------------------------------------------------------------ W6 ready first
print("W6 readiness")
MODEL["tools"] = False
fresh([("tool", "computer_run_command", {"command": "echo hi"})])
evs, answer = asyncio.run(run("Install jq"))
check("a model that can't call tools: says so", "can't call tools" in answer and "AI models" in answer, answer)
check("…and runs nothing", not results(evs, "computer_run_command"))
MODEL["tools"] = True
ready = results(evs, "xod_readiness")
check("the check is shown as a step", ready and "package managers" in ready[0]["summary"], ready)

# ------------------------------------------------------------------ W7 the done-check sends it back
print("W7 done-check")
work = Path(TMP) / "proj"
work.mkdir()
script = work / "hello.py"
fresh([("tool", "computer_write_file", {"filepath": str(script), "content": "print('hello from xod')"}),
       ("say", "Done: I wrote hello.py."),
       ("tool", "computer_run_command", {"command": "python hello.py", "cwd": str(work)}),
       ("say", "Done: it prints hello from xod.")],
      [{"done": False, "missing": "Run hello.py and show it works.", "needs_person": False},
       {"done": True, "proof": "hello from xod", "missing": ""}])
evs, answer = asyncio.run(run("Make a python script that prints hello and make sure it works"))
checks = results(evs, "xod_self_check")
check("checked twice: not yet, then works", [c["summary"][:7] for c in checks] == ["Not yet", "✓ Works"], [c["summary"] for c in checks])
ran = results(evs, "computer_run_command")
check("it went back and ran it", ran and ran[0]["data"].get("exit_code") == 0 and "hello from xod" in ran[0]["data"].get("stdout", ""), ran)
check("the answer says what proved it", "**Checked:** hello from xod" in answer, answer)
told_missing = [m for m in Script.seen[2] if "Not finished: Run hello.py" in str(m.get("content"))]
check("the worker was told what's missing", bool(told_missing))

# ------------------------------------------------------------------ W8 hints are for one call only
print("W8 hints last one call")
fresh([("tool", "computer_run_command", {"command": "definitely-not-a-program-xyz --version"}),
       ("tool", "computer_run_command", {"command": "echo fine"}),
       ("say", "Done.")])
evs, answer = asyncio.run(run("Install definitely-not-a-program-xyz"))
second, third = Script.seen[1], Script.seen[2]
hint_in = lambda msgs: any("Note from Xod" in str(m.get("content")) and "isn't installed" in str(m.get("content")) for m in msgs)
check("after a 'not recognized' failure the next call gets the hint", hint_in(second))
check("…and the call after that doesn't carry it", not hint_in(third))

# ------------------------------------------------------------------ W9 "you can run…" → it does it
print("W9 do it yourself")
fresh([("say", "You can run `echo installed` in PowerShell to install it."),
       ("tool", "computer_run_command", {"command": "echo installed"}),
       ("say", "Installed.")],
      [{"done": True, "proof": "installed"}])
evs, answer = asyncio.run(run("install the thing"))
check("it was pushed to do it, and did", results(evs, "computer_run_command") and "You can run" not in answer, answer)

# ------------------------------------------------------------------ W10 refused in the loop
print("W10 a dangerous command in the loop")
fresh([("tool", "computer_run_command", {"command": "Stop-Computer -Force"}), ("say", "I couldn't do that.")],
      [{"done": False, "needs_person": True, "why": "The person has to decide to shut down; it needs their choice."}])
evs, answer = asyncio.run(run("install updates and shut down the computer"))
r = results(evs, "computer_run_command")
check("refused, not run", r and r[0]["data"].get("refused"), r)

# ------------------------------------------------------------------ W11 'the person must review it' isn't a blocker
print("W11 needs the person?")
Script.verdicts = [{"done": False, "needs_person": True, "why": "The person must review index.js for syntax errors."}]
v = asyncio.run(worker.done_check("make it work", [entry("node a.js", "SyntaxError")], "Please review it."))
check("'review the file' goes back to the worker", v["needs_person"] is False and v["missing"], v)
Script.verdicts = [{"done": False, "needs_person": True, "why": "It needs the administrator password to install."}]
v = asyncio.run(worker.done_check("install it", [entry("winget install x", "Access is denied")], ""))
check("an admin password is a real blocker", v["needs_person"] is True, v)

# ------------------------------------------------------------------ W12 out of steps → paused → continue
print("W12 pause and continue")
worker.MAX_STEPS = 3
fresh([("tool", "computer_run_command", {"command": f"echo step{i}"}) for i in range(5)])
evs, answer = asyncio.run(run("set up a python virtual environment and install everything"))
check("pauses with what to say next", "Paused after 3 steps" in answer and "continue" in answer, answer)
history = [{"role": "user", "content": "set up a python virtual environment and install everything"},
           {"role": "assistant", "content": answer}, {"role": "user", "content": "continue"}]
r = worker.route("continue", history)
check("'continue' picks the same job up", r and r["goal"].startswith("set up a python virtual environment"), r)
worker.MAX_STEPS = 30

# ------------------------------------------------------------------ W14 calls written as text
print("W14 tool calls written as text")
txt = 'Let\'s install it.\n```json\n{"name": "computer_run_command", "arguments": {"command": "npm install chalk", "cwd": "C:/x"}}\n```\nThen {"not": "a call"}'
got = worker.calls_in_text(txt, {"computer_run_command"})
check("a call written in the reply is found", len(got) == 1 and json.loads(got[0]["function"]["arguments"])["command"] == "npm install chalk", got)
got = worker.calls_in_text("Let's check the page.\n```powershell\n# fetch it\n(Invoke-WebRequest http://127.0.0.1:8765/).Content\n```", {"computer_run_command"})
check("a command written as a code block it means to run is run", got and "Invoke-WebRequest" in got[0]["function"]["arguments"], got)
check("an example block in an explanation isn't", worker.calls_in_text("For example:\n```bash\nls\n```", {"computer_run_command"}) == [])
check("JSON that isn't a call is left alone", worker.calls_in_text('{"name": "rm_rf", "arguments": {}}', {"computer_run_command"}) == [])
fresh([("say", '```json\n{"name": "computer_run_command", "arguments": {"command": "echo from-text"}}\n```'), ("say", "Done: it printed from-text.")],
      [{"done": True, "proof": "from-text"}])
evs, answer = asyncio.run(run("install the from-text tool"))
r = results(evs, "computer_run_command")
check("…and the worker runs it", r and "from-text" in (r[0]["data"].get("stdout") or ""), r)

# ------------------------------------------------------------------ W13 stopping a job stops what it started
print("W13 stopping a background job")
import socket  # noqa: E402
from app.services.system_control import system_control_service as sc  # noqa: E402


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


async def server_job():
    port = free_port()
    py = "python" if WIN else "python3"
    j = await sc.start_background_job("srv", f"{py} -m http.server {port} --bind 127.0.0.1", cwd=TMP)
    for _ in range(40):
        if listening(port):
            break
        await asyncio.sleep(0.5)
    up = listening(port)
    await sc.stop_job(j["job_id"])
    await asyncio.sleep(1.5)
    return up, listening(port)


up, still = asyncio.run(server_job())
check("the server came up", up)
check("…and stopping the job frees its port (the program it ran is gone too)", up and not still)

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print("FAILED: " + "; ".join(failures))
    sys.exit(1)
