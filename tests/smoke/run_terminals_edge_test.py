"""
Edge cases for Xod's terminals (services/agent_clis.py), with stand-in agents and real programs in a throwaway home:
  conversations   Claude Code continues with --resume and Muse with --session-id (the stand-ins remember by id);
                  two at once; two messages to one go one after the other; they survive a restart; a thread is one
                  agent's; not signed in; an error answer; agents that can't continue say so
  terminals       Ctrl+C interrupts what's running; a program that exits at once; a flood of output; a character
                  split across reads; progress bars; a password prompt is never typed into; two messages at once
                  don't interleave; emoji and long input; a program killed from outside; a missing folder; odd names;
                  8 at once and the 9th refused; stopping twice
  .venv/bin/python tests/smoke/run_terminals_edge_test.py
"""
import asyncio
import json
import os
import signal
import stat
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-termedge-"))
HOMEDIR = TMP / "home"
BIN = HOMEDIR / ".local" / "bin"
BIN.mkdir(parents=True)
os.environ.update({"HOME": str(HOMEDIR), "XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1", "FAKE_STATE": str(TMP / "state"),
                   # only the stand-ins: none of this computer's real agents (in ~/.local/bin, npm's folder …)
                   "PATH": os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                                           if ".local/bin" not in p and "npm" not in p and "node" not in p)})
(TMP / "data").mkdir()
(TMP / "state").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


# Claude Code: -p PROMPT --output-format json [--resume ID] -> {"result", "session_id", "is_error"}; remembers per id.
FAKE_CLAUDE = r'''
import sys, json, os, uuid, time
a = sys.argv[1:]
if a[:1] == ["--help"]:
    print("claude  -p, --print  --output-format  --resume"); sys.exit(0)
state = os.environ["FAKE_STATE"]
p = a[a.index("-p") + 1] if "-p" in a else ""
sid = a[a.index("--resume") + 1] if "--resume" in a else str(uuid.uuid4())
f = os.path.join(state, "claude-" + sid)
said = open(f).read().splitlines() if os.path.exists(f) else []
if "NOLOGIN" in p:
    print("Invalid API key · Please run /login", file=sys.stderr); sys.exit(1)
if "BOOM" in p:
    print(json.dumps({"type": "result", "is_error": True, "result": "Something broke", "session_id": sid})); sys.exit(1)
if "SLOW" in p:
    open(os.path.join(state, "times"), "a").write(f"start {time.time()}\n"); time.sleep(1.5)
    open(os.path.join(state, "times"), "a").write(f"end {time.time()}\n")
open(f, "a").write(p + "\n")
print(json.dumps({"type": "result", "is_error": False, "result": f"turn {len(said) + 1}; before: {' | '.join(said) or '-'}", "session_id": sid}))
'''
# Muse: exec --workspace W --session-id ID PROMPT; remembers per id (the id is ours).
FAKE_MUSE = r'''
import sys, os
a = sys.argv[1:]
if a[:1] == ["--help"]:
    print("muse  Commands: exec login"); sys.exit(0)
state = os.environ["FAKE_STATE"]
sid = a[a.index("--session-id") + 1] if "--session-id" in a else "none"
p = a[-1]
f = os.path.join(state, "muse-" + sid)
said = open(f).read().splitlines() if os.path.exists(f) else []
open(f, "a").write(p + "\n")
print("muse: workspace trust: trusted", file=sys.stderr)
print(f"muse turn {len(said) + 1} in {sid[:8]}; before: {' | '.join(said) or '-'}")
'''
FAKE_CODEX = r'''
import sys
if sys.argv[1:2] == ["--help"]:
    print("codex  exec  run a prompt"); sys.exit(0)
print("codex says: " + sys.argv[-1])
'''


def install(name, code):
    script = BIN / f"{name}_impl.py"
    script.write_text(code)
    exe = BIN / name
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)


async def main():
    install("claude", FAKE_CLAUDE)
    install("muse", FAKE_MUSE)
    install("codex", FAKE_CODEX)
    from app.services import agent_clis as ac
    from app.agent import tools
    work = TMP / "proj"
    work.mkdir()

    print("E1 conversations")
    r1 = await ac.chat("claude", "first message", folder=str(work))
    check("Claude Code: an exact answer and a conversation name", r1["success"] and r1["thread"] == "claude-chat-1" and r1["answer"].startswith("turn 1"), str(r1)[:200])
    r2 = await ac.chat("claude", "second message", thread=r1["thread"])
    check("…the next message continues it (--resume: it remembers the first)", r2["success"] and "turn 2" in r2["answer"] and "first message" in r2["answer"], r2.get("answer"))
    m1 = await ac.chat("muse", "hello muse", folder=str(work))
    m2 = await ac.chat("muse", "again", thread=m1["thread"])
    check("Muse continues by --session-id the same way", "muse turn 2" in m2["answer"] and "hello muse" in m2["answer"], m2.get("answer"))
    t = time.time()
    a, b = await asyncio.gather(ac.chat("claude", "SLOW one", thread="one"), ac.chat("claude", "SLOW two", thread="two"))
    check("two conversations at once run in parallel", a["success"] and b["success"] and time.time() - t < 2.8 and a["thread"] != b["thread"], f"{time.time() - t:.1f}s")
    open(TMP / "state" / "times", "w").close()
    await asyncio.gather(ac.chat("claude", "SLOW x", thread="same"), ac.chat("claude", "SLOW y", thread="same"))
    marks = [l.split() for l in (TMP / "state" / "times").read_text().splitlines()]
    order = [m[0] for m in sorted(marks, key=lambda m: float(m[1]))]
    check("two messages to one conversation go one after the other", order == ["start", "end", "start", "end"], str(order))
    r = await ac.chat("claude", "and after that?", thread="same")
    check("…and both reached it, in order", "SLOW x" in r["answer"] and "SLOW y" in r["answer"] and "turn 3" in r["answer"], r["answer"])
    ac._threads.clear()
    r = await ac.chat("claude", "after a restart", thread=r1["thread"])
    check("conversations survive a restart (saved in Xod's data folder)", "turn 3" in r["answer"] and "second message" in r["answer"], r.get("answer"))
    try:
        await ac.chat("muse", "wrong one", thread=r1["thread"])
        ok = False
    except ac.CliError as e:
        ok = "Claude Code" in str(e)
    check("a conversation belongs to one agent", ok)
    r = await ac.chat("claude", "NOLOGIN please")
    check("not signed in: says so, and how", not r["success"] and r.get("needs_login") and "cli_agent_login" in r["error"], str(r)[:200])
    r = await ac.chat("claude", "BOOM")
    check("an error answer is reported as one", not r["success"] and "Something broke" in r["error"])
    r = await ac.chat("codex", "hi")
    check("an agent that can't continue headless: answers, and says each message stands alone", r["success"] and "stands alone" in r["note"], str(r)[:200])
    lst = {x["thread"]: x for x in ac.threads()}
    check("threads are listed with their agent and turns", lst[r1["thread"]]["agent"] == "claude" and lst[r1["thread"]]["turns"] == 3, str(lst)[:200])
    res = await tools._cli_agents("cli_agent_chat", {"agent": "claude", "message": "via the tool", "thread": r1["thread"]})
    check("the chat's tool works the same way", res["success"] and "turn 4" in res["answer"])

    if sys.platform == "win32":
        return
    print("E2 terminals")
    sh = await ac.session_start(command="bash --norc --noprofile", folder=str(work), name="shell")
    await ac.session_send("shell", "sleep 30; echo AFTER-SLEEP", wait=1.5)
    t = time.time()
    ac.session_key("shell", "ctrl+c")
    r = await ac.session_send("shell", "echo STILL-HERE", wait=4)
    check("Ctrl+C interrupts what's running (the 30-second sleep ends at once)", "^C" in r["screen"] and "STILL-HERE" in r["screen"].split("echo STILL-HERE")[-1]
          and time.time() - t < 8, r["screen"][-300:])
    check("…and the shell has job control (no warning about it)", "no job control" not in r["screen"])
    q = await ac.session_start(command="ls /no-such-dir-xod", folder=str(work))
    await asyncio.sleep(0.5)
    check("a program that exits at once: finished", ac._session(q["session"]).state() == "finished")
    try:
        await ac.session_send(q["session"], "anything")
        ok = False
    except ac.CliError as e:
        ok = "has stopped" in str(e) and "No such file" in str(e)
    check("…and sending to it says so, with its last lines", ok)
    t = time.time()
    f = await ac.session_start(command="bash -c 'yes LINE | head -n 200000; echo FLOOD-DONE; sleep 30'", folder=str(work))
    for _ in range(60):
        if "FLOOD-DONE" in ac._session(f["session"]).screen():
            break
        await asyncio.sleep(0.5)
    check("a flood of output (200 000 lines) is kept up with", "FLOOD-DONE" in ac._session(f["session"]).screen() and time.time() - t < 30, f"{time.time() - t:.1f}s")
    check("…and only the end is kept", len("".join(ac._session(f["session"]).buf)) <= 400_000 + 4096)
    py = sys.executable
    split = f"""{py} -c "import sys,time; b='héllo 👋🏽 wörld'.encode(); [(sys.stdout.buffer.write(bytes([c])), sys.stdout.flush(), time.sleep(0.01)) for c in b]; print(); time.sleep(30)" """
    u = await ac.session_start(command=split, folder=str(work))
    await asyncio.sleep(1.5)
    check("a character split across reads arrives whole (emoji, accents)", "héllo 👋🏽 wörld" in ac._session(u["session"]).screen(), repr(ac._session(u["session"]).screen()[-80:]))
    prog = f"""{py} -c "import sys,time; [ (sys.stdout.write(chr(13)+'progress %d%%' % i), sys.stdout.flush(), time.sleep(0.05)) for i in (10,50,100)]; print(); print('DONE'); time.sleep(30)" """
    g = await ac.session_start(command=prog, folder=str(work))
    await asyncio.sleep(1.5)
    scr = ac._session(g["session"]).screen()
    check("a progress bar shows where it ended (100%), not every step", "progress 100%" in scr and "progress 10%" not in scr, repr(scr[-120:]))
    pw = await ac.session_start(command=f"""{py} -c "import getpass; x = getpass.getpass('Password: '); print('GOT', len(x))" """, folder=str(work))
    await asyncio.sleep(1.0)
    r = await ac.session_send(pw["session"], "hunter2")
    check("a password prompt: Xod never types into it, and says to type it yourself", r.get("needs_password") and not r["success"] and "Terminals" in r["error"], str(r)[:200])
    check("…nothing was typed", "GOT" not in ac._session(pw["session"]).screen())
    c = await ac.session_start(command="bash --norc --noprofile", folder=str(work), name="echoer")
    A, B = "A" * 300, "B" * 300
    await asyncio.gather(ac.session_send("echoer", f"echo {A}", wait=2), ac.session_send("echoer", f"echo {B}", wait=2))
    scr = ac._session("echoer").screen().replace("\n", "")
    check("two messages at once to one session don't interleave", A in scr and B in scr, scr[-200:])
    r = await ac.session_send("echoer", "echo 'emoji 👋🏽 and ünïcode ✓'", wait=2)
    check("emoji and accents typed in arrive as typed", "emoji 👋🏽 and ünïcode ✓" in r["screen"].split("echo 'emoji")[-1], r["screen"][-160:])
    long = "x" * 3000
    r = await ac.session_send("echoer", f"echo START{long}END | wc -c", wait=3)
    check("a long line (3000 characters) arrives whole", "3009" in r["screen"].split("| wc -c")[-1], r["screen"][-120:])
    k = await ac.session_start(command="sleep 300", folder=str(work))
    os.kill(ac._session(k["session"]).proc.pid, signal.SIGKILL)
    await asyncio.sleep(0.8)
    check("a program killed from outside shows as finished", ac._session(k["session"]).state() == "finished")
    try:
        await ac.session_start(command="bash", folder=str(TMP / "nope"))
        ok = False
    except ac.CliError as e:
        ok = "no folder" in str(e).lower()
    check("a folder that isn't there: says so", ok)
    n = await ac.session_start(command="cat", folder=str(work), name="my session!! (test)")
    check("an odd name becomes a safe one", n["name"] == "my-session-test", n["name"])
    ac.stop_all()
    starts = await asyncio.gather(*(ac.session_start(command="cat", folder=str(work), new=True) for _ in range(8)), return_exceptions=True)
    names = [x["name"] for x in starts if isinstance(x, dict)]
    check("8 started at once: all running, all named differently", len(names) == 8 and len(set(names)) == 8, str(names))
    try:
        await ac.session_start(command="cat", folder=str(work), new=True)
        ok = False
    except ac.CliError as e:
        ok = "already has 8" in str(e)
    check("…the 9th is refused, with which are running", ok)
    one = names[0]
    ac.session_stop(one)
    try:
        ac.session_stop(one)
        ok = False
    except ac.CliError as e:
        ok = "No session" in str(e)
    check("stopping one twice: the second time says it's gone", ok)
    ac.stop_all()
    check("stop_all leaves nothing running", not [x for x in ac.sessions() if x.get("session")])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        from app.services import agent_clis as ac
        ac.stop_all()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)
