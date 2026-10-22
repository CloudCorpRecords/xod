"""
A stand-in for `claude -p --input-format stream-json --output-format stream-json --permission-prompt-tool stdio` (Claude
Code's SDK protocol) for Xod's Crew tests. It does what a message asks, by keyword:
  RUN <command>   asks to run it (can_use_tool); runs it if allowed (saying whether a rule was kept for the session)
  ASK             asks a question with options red / blue (AskUserQuestion) and repeats the answer
  EDIT <path>     edits a file without asking (+2 −1 lines)
  FAIL            the turn fails
  SLOW            works for 4 seconds (interruptible)
  MCP             says which shared connectors it got and whether their token reached it (never the token)
  anything else   answers "echo: <text> (message N in this session)"; --resume keeps the count
It records its arguments in $FAKE_STATE/claude.log.
"""
import json
import os
import re
import sys
import threading
import time
import uuid

STATE = os.environ.get("FAKE_STATE", "/tmp")
args = sys.argv[1:]
out_lock = threading.Lock()
replies = {}                  # request id -> threading.Event and the response
interrupted = threading.Event()
resume = args[args.index("--resume") + 1] if "--resume" in args else ""
session = resume or str(uuid.uuid4())
counts_file = os.path.join(STATE, "claude-sessions.json")
with open(os.path.join(STATE, "claude.log"), "a") as f:
    f.write(json.dumps(args) + "\n")


def send(obj):
    with out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def ask(request):
    rid = str(uuid.uuid4())
    ev = threading.Event()
    replies[rid] = {"event": ev, "response": None}
    send({"type": "control_request", "request_id": rid, "request": request})
    ev.wait(30)
    return replies.pop(rid)["response"] or {}


def say(text):
    send({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}, "session_id": session})


def tool(name, inp):
    tid = "toolu_" + uuid.uuid4().hex[:12]
    send({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}, "session_id": session})
    return tid


def result_of(tid, text, error=False):
    send({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}]}, "session_id": session})


def done(text, ok=True, errors=None):
    send({"type": "result", "subtype": "success" if ok else "error_during_execution", "is_error": not ok, "result": text if ok else "",
          "errors": errors or [], "session_id": session, "duration_ms": 1200, "total_cost_usd": 0.0123,
          "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5}})


def turn(text):
    interrupted.clear()
    send({"type": "system", "subtype": "init", "session_id": session, "cwd": os.getcwd(), "model": "fake"})
    m = re.match(r"RUN (.+)", text)
    if m:
        cmd = m.group(1)
        tid = tool("Bash", {"command": cmd})
        r = ask({"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": cmd}, "description": f"Run {cmd}",
                 "permission_suggestions": [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": cmd}], "behavior": "allow", "destination": "localSettings"},
                                            {"type": "addDirectories", "directories": [os.getcwd()], "destination": "session"}],
                 "tool_use_id": tid})
        if r.get("behavior") == "allow":
            kept = r.get("updatedPermissions") or []
            result_of(tid, f"ran {r['updatedInput']['command']}")
            note = " (rule kept for the session)" if kept and all(p.get("destination") == "session" for p in kept) else ""
            say(f"I ran {cmd}{note}.")
            return done(f"I ran {cmd}{note}.")
        result_of(tid, "The user doesn't want to proceed with this tool use. " + r.get("message", ""), error=True)
        say(f"OK, I won't run {cmd}.")
        return done(f"OK, I won't run {cmd}.")
    if text.startswith("ASK"):
        qs = [{"question": "Which color?", "header": "Color", "options": [{"label": "red"}, {"label": "blue"}], "multiSelect": False}]
        tid = tool("AskUserQuestion", {"questions": qs})
        r = ask({"subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": {"questions": qs}, "tool_use_id": tid})
        ans = (r.get("updatedInput") or {}).get("answers", {}).get("Which color?")
        result_of(tid, f"answered {ans}")
        say(f"You chose {ans}.")
        return done(f"You chose {ans}.")
    m = re.match(r"EDIT (.+)", text)
    if m:
        tid = tool("Edit", {"file_path": m.group(1), "old_string": "a", "new_string": "b\nc"})
        result_of(tid, "edited")
        say("Edited it.")
        return done("Edited it.")
    if text.startswith("FAIL"):
        return done("", ok=False, errors=["The model provider is down"])
    if text.startswith("SLOW"):
        for _ in range(40):
            if interrupted.is_set():
                return done("", ok=False, errors=["interrupted"])
            time.sleep(0.1)
        say("slow done")
        return done("slow done")
    if text.startswith("MCP"):
        cfg = {}
        if "--mcp-config" in args:
            cfg = json.load(open(args[args.index("--mcp-config") + 1])).get("mcpServers", {})
        parts = []
        for name, spec in cfg.items():
            header = spec.get("headers", {}).get("Authorization", "")
            var = re.search(r"\$\{(\w+)\}", header)
            parts.append(f"{name} {'token-set' if var and os.environ.get(var.group(1)) else 'no-token'} {'in-file' if 'ghp_' in header else 'not-in-file'}")
        say("mcp: " + ("; ".join(parts) or "none"))
        return done("mcp: " + ("; ".join(parts) or "none"))
    try:
        counts = json.load(open(counts_file))
    except (OSError, ValueError):
        counts = {}
    counts[session] = counts.get(session, 0) + 1
    json.dump(counts, open(counts_file, "w"))
    say(f"echo: {text} (message {counts[session]} in this session)")
    done(f"echo: {text} (message {counts[session]} in this session)")


def main():
    work = []
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        t = msg.get("type")
        if t == "control_request":
            sub = (msg.get("request") or {}).get("subtype")
            if sub == "interrupt":
                interrupted.set()
            send({"type": "control_response", "response": {"subtype": "success", "request_id": msg.get("request_id"), "response": {}}})
        elif t == "control_response":
            r = msg.get("response") or {}
            slot = replies.get(r.get("request_id"))
            if slot:
                slot["response"] = r.get("response")
                slot["event"].set()
        elif t == "user":
            text = "".join(c.get("text", "") for c in msg["message"]["content"] if isinstance(c, dict))
            th = threading.Thread(target=turn, args=(text,), daemon=True)
            work.append(th)
            # one turn at a time, as Claude Code queues messages: wait for the one before
            prev = work[-2] if len(work) > 1 else None
            threading.Thread(target=lambda p=prev, x=th: (p.join() if p else None, x.start()), daemon=True).start()
    for th in work:
        th.join(5)


if __name__ == "__main__":
    if "--input-format" not in args or "--permission-prompt-tool" not in args:
        print("fake claude: only the stream-json mode", file=sys.stderr)
        sys.exit(2)
    main()
