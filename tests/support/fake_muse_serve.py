"""
A stand-in for `muse serve` (Muse's MSP protocol, newline-delimited JSON-RPC) for Xod's Crew tests. It does what a
prompt asks, by keyword:
  APPROVE <command>   asks to run <command> (approval/request), runs it if approved, says so if denied
  ASK                 asks a question with options red / blue (userInput/request) and repeats the answer
  EDIT <path>         edits a file (a toolCall with a patch summary: +3 −1)
  FAIL                the turn fails
  SLOW                works for 4 seconds (interruptible)
  anything else       answers "echo: <text>", streamed in pieces
It requires `initialized` before sessions, UUIDv7 command ids, and records what it got in $FAKE_STATE/muse-serve.log.
"""
import json
import os
import re
import sys
import threading
import time
import uuid

STATE = os.environ.get("FAKE_STATE", "/tmp")
out_lock = threading.Lock()
pending = {}                 # approval / question id -> threading.Event and result
interrupted = set()
initialized = {"v": False}
sessions = {}
cursor = {"n": 0}


def log(*a):
    with open(os.path.join(STATE, "muse-serve.log"), "a") as f:
        f.write(" ".join(str(x) for x in a) + "\n")


def send(obj):
    with out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def note(method, params):
    cursor["n"] += 1
    send({"jsonrpc": "2.0", "method": method, "params": {**params, "viewCursor": f"v:{cursor['n']}"}})


def is_uuid7(s):
    try:
        return uuid.UUID(s).version == 7
    except Exception:
        return False


def item(sid, turn, kind, status, **kw):
    return {"sessionId": sid, "item": {"itemId": str(uuid.uuid4()), "kind": kind, "status": status, "revision": 1, "turnId": turn, **kw}}


def say(sid, turn, text):
    it = item(sid, turn, "agentMessage", "inProgress", text="")
    note("item/started", it)
    for part in [text[i:i + 6] for i in range(0, len(text), 6)]:
        note("item/delta", {"sessionId": sid, "itemId": it["item"]["itemId"], "delta": part})
        time.sleep(0.02)
    done = dict(it)
    done["item"] = {**it["item"], "status": "completed", "text": text, "revision": 2}
    note("item/completed", done)


def finish(sid, turn, terminal="completed", error=None):
    p = {"sessionId": sid, "turnId": turn, "terminal": terminal, "sourceRange": {}, "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}}
    if error:
        p["error"] = {"kind": "provider", "message": error, "retryable": False}
    note("turn/completed", p)
    sessions[sid]["busy"] = False


def ask(rid_kind, params):
    rid = str(uuid.uuid4())
    ev = threading.Event()
    key = params.get("approvalId") or params.get("userInputId")
    pending[key] = {"event": ev, "result": None}
    send({"jsonrpc": "2.0", "id": rid, "method": rid_kind, "params": params})
    ev.wait(30)
    return pending.pop(key)["result"]


def run_turn(sid, turn, text):
    sessions[sid]["busy"] = True
    note("turn/started", {"sessionId": sid, "turnId": turn})
    note("item/completed", item(sid, turn, "userMessage", "completed", text=text))
    m = re.match(r"APPROVE (.+)", text)
    if m:
        cmd = m.group(1)
        aid = str(uuid.uuid4())
        dec = ask("approval/request", {"approvalId": aid, "sessionId": sid, "turnId": turn, "itemId": "x", "toolCallId": "c1",
                                       "toolName": "shell", "currentRequirementId": {"stage": 1},
                                       "subject": {"kind": "shell", "command": cmd},
                                       "availableChoices": [
                                           {"choiceId": "yes-once", "decision": "approved", "label": "Yes", "scope": "once"},
                                           {"choiceId": "yes-session", "decision": "approvedForSession", "label": "Yes for session", "scope": "session"},
                                           {"choiceId": "no", "decision": "denied", "label": "No", "scope": "once", "acceptsFeedback": True}]})
        if dec and dec.startswith("yes"):
            it = item(sid, turn, "toolCall", "inProgress", tool="shell", args=json.dumps({"command": cmd}),
                       displayLabel={"active": f"Running {cmd}", "done": f"Ran {cmd}", "neutral": f"{cmd}"})
            note("item/started", it)
            done = dict(it)
            done["item"] = {**it["item"], "status": "completed", "visibleOutput": f"ran {cmd}", "revision": 2}
            note("item/completed", done)
            say(sid, turn, f"I ran {cmd} ({dec}).")
        else:
            say(sid, turn, f"OK, I won't run {cmd}.")
        return finish(sid, turn)
    if text.startswith("ASK"):
        uid = str(uuid.uuid4())
        ans = ask("userInput/request", {"userInputId": uid, "sessionId": sid, "turnId": turn, "itemId": "x", "toolCallId": "c2",
                                        "toolName": "ask", "viewCursor": "v",
                                        "questions": [{"id": "color", "header": "Color", "question": "Which color?",
                                                       "options": [{"label": "red"}, {"label": "blue"}], "selection": "single"}]})
        say(sid, turn, f"You chose {ans}.")
        return finish(sid, turn)
    m = re.match(r"EDIT (.+)", text)
    if m:
        it = item(sid, turn, "toolCall", "completed", tool="edit", args=json.dumps({"path": m.group(1)}),
                  patchSummary={"files": 1, "added": 3, "removed": 1}, displayLabel={"active": "Editing", "done": f"Edited {m.group(1)}", "neutral": "Edit"})
        note("item/completed", it)
        say(sid, turn, "Edited it.")
        return finish(sid, turn)
    if text.startswith("FAIL"):
        return finish(sid, turn, "failed", "The model provider is down")
    if text.startswith("SLOW"):
        for _ in range(40):
            if sid in interrupted:
                interrupted.discard(sid)
                return finish(sid, turn, "cancelled")
            time.sleep(0.1)
        say(sid, turn, "slow done")
        return finish(sid, turn)
    sessions[sid]["said"].append(text)
    say(sid, turn, f"echo: {text} (message {len(sessions[sid]['said'])} in this session)")
    finish(sid, turn)


def main():
    log("serve started", os.getpid())
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method, p = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if method is None:                       # a receipt for one of our requests
            continue
        log("got", method, json.dumps(p)[:300])

        def ok(result):
            send({"jsonrpc": "2.0", "id": mid, "result": result})

        def err(code, text):
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}})
        if method == "initialize":
            ok({"serverInfo": {"name": "muse", "version": "fake"}})
            continue
        if method == "initialized":
            initialized["v"] = True
            continue
        if not initialized["v"]:
            err(-32002, "Not initialized")
            continue
        if "commandId" in p and not is_uuid7(p["commandId"]):
            err(-32602, f"invalid {method} commandId: expected UUIDv7")
            continue
        if method == "session/start":
            sid = str(uuid.uuid4())
            sessions[sid] = {"said": [], "busy": False, "root": p.get("workspaceRoot")}
            note("session/started", {"session": {"sessionId": sid, "status": "idle"}})
            ok({"session": {"sessionId": sid, "status": "idle", "workspaceRoot": p.get("workspaceRoot")}, "viewCursor": "v0"})
        elif method == "session/resume":
            sessions.setdefault(p["sessionId"], {"said": [], "busy": False})
            ok({"session": {"sessionId": p["sessionId"], "status": "idle"}})
        elif method == "turn/start":
            sid = p["sessionId"]
            text = "".join(x.get("text", "") for x in p.get("input") or [])
            log("ifBusy", p.get("ifBusy"))
            turn = p["commandId"]
            ok({"commandId": turn, "status": "accepted", "turnId": turn, "startedNewTurn": True, "disposition": "started"})
            threading.Thread(target=run_turn, args=(sid, turn, text), daemon=True).start()
        elif method == "turn/interrupt":
            interrupted.add(p["sessionId"])           # that session's running turn stops at its next check
            ok({})
        elif method == "approval/decide":
            ok({})
            slot = pending.get(p["approvalId"])
            if slot:
                slot["result"] = p["choiceId"]
                slot["event"].set()
        elif method == "userInput/answer":
            ok({})
            slot = pending.get(p["userInputId"])
            if slot:
                a = (p.get("answers") or [{}])[0]
                slot["result"] = a.get("selectedLabel") or a.get("freeText")
                slot["event"].set()
        elif method == "approval/listPending":
            ok({"approvals": [], "userInputs": []})
        else:
            ok({})


if __name__ == "__main__":
    if sys.argv[1:2] != ["serve"]:
        print("fake muse: only `serve`", file=sys.stderr)
        sys.exit(2)
    main()
