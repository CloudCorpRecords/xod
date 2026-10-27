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
       ("tool", "computer_run_command", {"command": ("python" if WIN else "python3") + " hello.py", "cwd": str(work)}),
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

# ------------------------------------------------------------------ W15 an empty write to a folder path
print("W15 'make the folder' written as an empty file")
proj = Path(TMP) / "usage-demo"
r = asyncio.run(agent_tools.execute_tool("computer_write_file", {"filepath": str(proj), "content": ""}))
check("an empty write to a path with no file name makes the folder", r.get("made_folder") and proj.is_dir(), r)
r = asyncio.run(agent_tools.execute_tool("computer_write_file", {"filepath": str(proj / "hello.py"), "content": "print('hi')"}))
check("…and the file then goes inside it", r.get("success") and (proj / "hello.py").exists(), r)
for name in (".gitkeep", "Makefile"):
    r = asyncio.run(agent_tools.execute_tool("computer_write_file", {"filepath": str(proj / name), "content": ""}))
    check(f"an empty {name} is still a file", (proj / name).is_file(), r)
stray = Path(TMP) / "stray"
stray.write_text("")
r = asyncio.run(agent_tools.execute_tool("computer_write_file", {"filepath": str(stray / "a.py"), "content": "x"}))
check("a file where a folder should be: says so plainly", not r.get("success") and "is a file, not a folder" in r.get("error", ""), r)
h = worker.guidance([entry("write", r["error"])])
check("…and the worker gets the hint to delete it or pick another name", any("FILE where a folder should be" in x for x in h), h)

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

# ------------------------------------------------------------------ W16 jobs said in plain words, and replies that carry one on
print("W16 plain-words jobs, offers, answers")


def kind(q, hist=()):
    r = worker.route(q, [*hist, {"role": "user", "content": q}])
    return r and r.get("kind")


for q in ("in ~/x make a python script that gets the top 5 Hacker News stories and saves them to hn.csv, then run it",
          "write a cron job that backs up my Documents every night"):
    check(f"a job on the computer: {q[:50]}", kind(q) == "computer", kind(q))
for q in ("go look for things thats trending and try to go thru websites about ti and bring me the info afeter",
          "dig into the links from that article and tell me what they say"):
    check(f"reading the web: {q[:50]}", kind(q) == "web", kind(q))
for q in ("make me a 3d game in this folder", "how do I write a script that renames files?", "hi", "what's trending today?"):
    check(f"not the worker: {q}", worker.route(q, [{"role": "user", "content": q}]) is None)
asked = [{"role": "user", "content": "make a scraper"},
         {"role": "assistant", "content": 'Which site? <!-- xod-work:{"goal": "make a scraper", "left": "which site", "waiting": true} -->'}]
r = worker.route("hacker news", [*asked, {"role": "user", "content": "hacker news"}])
check("the answer to its question carries the job on", r and r["goal"] == "make a scraper" and r["extra"] == "hacker news", r)
r = worker.route("start!", [*asked, {"role": "user", "content": "start!"}])
check("'start!' carries it on too", r and r["goal"] == "make a scraper" and r["extra"] == "", r)
finished = [{"role": "user", "content": "make a scraper"},
            {"role": "assistant", "content": 'Done. <!-- xod-work:{"goal": "make a scraper", "left": ""} -->'}]
check("'start!' after a finished job isn't a re-run", worker.route("start!", [*finished, {"role": "user", "content": "start!"}]) is None)
check("…but 'try again' still is", bool(worker.route("try again", [*finished, {"role": "user", "content": "try again"}])))
offer = [{"role": "user", "content": "can you install the github cli"},
         {"role": "assistant", "content": "Homebrew isn't installed, so `brew install gh` failed. Want me to install Homebrew first?"}]
r = worker.route("yes", [*offer, {"role": "user", "content": "yes"}])
check("'yes' to its own offer does the job instead of asking again", r and "install the github cli" in r["goal"]
      and "install Homebrew first" in r["goal"] and r["kind"] == "computer", r)
dig = [{"role": "user", "content": "whats trending"}, {"role": "assistant", "content": "Here's what's trending. Want me to dig deeper into any of these?"}]
check("'sure' to 'dig deeper?' reads the web", kind("sure", dig) == "web", kind("sure", dig))
other = [{"role": "user", "content": "make a game"}, {"role": "assistant", "content": 'Want me to add a score? <!-- xod-build:{"path": "/x"} -->'}]
check("an offer from another lane (the builder) stays with it", worker.route("yes", [*other, {"role": "user", "content": "yes"}]) is None)
chat = [{"role": "user", "content": "tell me a joke"}, {"role": "assistant", "content": "Why did the chicken cross the road? Want me to tell another?"}]
check("an offer that isn't work isn't a job", worker.route("yes", [*chat, {"role": "user", "content": "yes"}]) is None)

# Reading the web: only web tools, the done-check sends it back until it has read the pages, and the reply remembers it.
offered, pages = [], {"https://example.com/a": "Story A: chips are getting faster."}


async def web_tool(name, args):
    if name == "web_search":
        return {"results": [{"title": "Story A", "url": "https://example.com/a"}]}
    if name == "web_scrape":
        return {"url": args["url"], "title": "A", "content": pages.get(args["url"], ""), "length": 30}
    return {"success": False, "error": f"{name} wasn't expected"}


async def fake_deliberate_tools(messages, tools=None, **kw):
    offered.append(sorted(t["function"]["name"] for t in tools or []))
    async for ev in fake_deliberate(messages, tools, **kw):
        yield ev


async def run_web(query):
    msgs = [{"role": "user", "content": query}]
    decision = worker.route(query, msgs)
    evs = []
    async for ev in worker.stream(query, msgs, decision, web_tool):
        evs.append(ev)
    return evs, "".join(e.get("content", "") for e in evs if e["type"] == "content")


worker.llm_service.deliberate_stream = fake_deliberate_tools
fresh([("tool", "web_search", {"query": "trending tech"}), ("say", "Story A is trending."),
       ("tool", "web_scrape", {"url": "https://example.com/a"}), ("say", "Chips are getting faster ([A](https://example.com/a)).")],
      [{"done": False, "missing": "Read the story pages, not just the search results."}, {"done": True, "proof": "read example.com/a"}])
evs, answer = asyncio.run(run_web("go through the websites about what's trending in tech and bring me the info"))
worker.llm_service.deliberate_stream = fake_deliberate
check("only web tools are offered", offered and "computer_run_command" not in offered[0] and "web_scrape" in offered[0], offered[:1])
check("no 'checking what this computer has' step", not results(evs, "xod_readiness"))
check("'not done' sent it back to read the page", [e["name"] for e in results(evs) if e["name"] != "xod_self_check"] == ["web_search", "web_scrape"])
check("the answer is what the page said, and the job is remembered as web",
      "Chips are getting faster" in answer and '"kind": "web"' in answer, answer[-200:])
check("…with no 'Checked:' line under the sources", "**Checked:**" not in answer, answer[-200:])

fresh([("tool", "computer_run_command", {"command": "echo ready"}), ("say", "Which site should it scrape?")],
      [{"done": False, "needs_person": True, "why": "choose which site to scrape"}])
evs, answer = asyncio.run(run("write a scraper script and run it", [{"role": "user", "content": "hi"}]))
check("a job waiting on the person's choice is remembered as waiting", '"waiting": true' in answer, answer[-200:])

from app.services.browser_sandbox import _bot_wall  # noqa: E402
check("a site's 403 block page is a bot wall", _bot_wall("adidas", "HTTP 403 Forbidden. Reference Error: 0.911c1602"))
check("a normal page isn't", not _bot_wall("Nike Running Shoes", "Pegasus 42 $145 " * 40))

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


async def where_job():
    py = "python" if WIN else "python3"
    j = await sc.start_background_job("pwd", f"{py} -c \"import os; print('CWD=' + os.getcwd())\"")
    for _ in range(40):
        if sc.background_jobs[j["job_id"]]["status"] != "running":
            break
        await asyncio.sleep(0.25)
    return " ".join(str(x) for x in sc.background_jobs[j["job_id"]]["logs"])


out = asyncio.run(where_job())
check("a job given no folder doesn't run in Xod's own folder", "CWD=" in out and str(ROOT) not in out, out[:200])

up, still = asyncio.run(server_job())
check("the server came up", up)
check("…and stopping the job frees its port (the program it ran is gone too)", up and not still)

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print("FAILED: " + "; ".join(failures))
    sys.exit(1)
