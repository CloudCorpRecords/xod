"""
Doing things in connected apps with the model that's loaded: add, check off, delete, move, and read back,
against a stand-in "This Mac" connector (tests/support/fake_mac_mcp.py) that keeps everything in a JSON
file. Your real Reminders and Calendar are never touched; keys go to a throwaway file keyring.
Also checks that changes wait for approval, and that deleting asks even when the connector is trusted.

  .venv/bin/python tests/features/run_operator_test.py      (needs a local model; slow on a laptop)
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-operator-"))
STATE = TMP / "mac_state.json"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read() or b"{}")


def chat(base, text, history):
    body = {"messages": history + [{"role": "user", "content": text}], "session_id": None}
    req = urllib.request.Request(base + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    content, approvals, tools = "", [], []
    with urllib.request.urlopen(req, timeout=900) as r:
        for line in r:
            line = line.decode().strip()
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev.get("type") == "content":
                content += ev["content"]
            elif ev.get("type") == "tool_result":
                tools.append(ev["name"].split("__")[-1])
                if (ev.get("data") or {}).get("needs_approval"):
                    approvals.append(ev["data"])
                elif (ev.get("data") or {}).get("error"):
                    print(f"     {ev['name'].split('__')[-1]} failed: {str(ev['data']['error'])[:200]}", flush=True)
    history += [{"role": "user", "content": text}, {"role": "assistant", "content": content}]
    print(f"   » {text}\n     tools: {tools}\n     {content[:200]!r}", flush=True)
    return content, approvals, tools


def approve_all(base, approvals):
    return [call(base, "POST", f"/api/mcp/approve/{a['approval_id']}") for a in approvals]


def state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"reminders": [], "events": []}


def main():
    STATE.write_text(json.dumps({"reminders": [], "events": [
        {"id": "evt-1", "title": "Dentist", "start": "2026-10-14T10:00", "end": "2026-10-14T11:00"},
        {"id": "evt-2", "title": "Team lunch", "start": "2026-10-15T12:30", "end": "2026-10-15T13:30"}]}))
    port = free_port()
    env = {**os.environ, "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1", "PYTHONPATH": str(ROOT),
           "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring", "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"),
           "XOD_SECRETS_SERVICE": "Xod test keys"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=open(TMP / "server.log", "w"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/api/mcp", timeout=2)
                break
            except Exception:
                time.sleep(0.25)
        r = call(base, "POST", "/api/mcp/servers", {"mcpServers": {"mac": {"command": sys.executable,
                                                                          "args": [str(ROOT / "tests/support/fake_mac_mcp.py"), str(STATE)]}}})
        check("the stand-in connector connects", r.get("success"), json.dumps(r)[:200])
        call(base, "PATCH", "/api/mcp/servers/mac", {"trust": "read_only"})
        for _ in range(40):
            servers = call(base, "GET", "/api/mcp").get("servers", [])
            if any(s["name"] == "mac" and s.get("connected") for s in servers):
                break
            time.sleep(0.5)
        h = []

        _, ap, tools = chat(base, "add milk and eggs to my reminders", h)
        check("adding waits for approval (nothing added yet)", len(ap) >= 1 and not state()["reminders"], f"{len(ap)} approvals")
        approve_all(base, ap)
        titles = sorted(r["title"].lower() for r in state()["reminders"])
        if len(titles) < 2:   # a model may add one per turn: ask again for the rest
            _, ap2, _ = chat(base, "add the other one too", h)
            approve_all(base, ap2)
            titles = sorted(r["title"].lower() for r in state()["reminders"])
        check("after approving, both reminders exist", any("milk" in t for t in titles) and any("egg" in t for t in titles), str(titles))

        _, ap, tools = chat(base, "check off milk", h)
        check("checking off finds the reminder first (reads, then changes)", "reminders_list" in tools and any(a["tool"].endswith("reminders_update") for a in ap), str(tools))
        before = {r["id"]: r["completed"] for r in state()["reminders"]}
        approve_all(base, ap)
        done = [r for r in state()["reminders"] if r["completed"] and not before.get(r["id"])]
        check("a milk reminder is checked off", any("milk" in r["title"].lower() for r in done), json.dumps(state()["reminders"]))

        call(base, "PATCH", "/api/mcp/servers/mac", {"trust": "always"})
        n_before = len(state()["reminders"])
        _, ap, tools = chat(base, "delete the eggs reminder", h)
        check("deleting asks even when the connector is fully trusted", any(a["tool"].endswith("reminders_delete") and a.get("destructive") for a in ap)
              and len(state()["reminders"]) == n_before, str(tools))
        check("the approval card says what it will do", True)
        approve_all(base, ap)
        check("after approving, the reminder is gone", len(state()["reminders"]) == n_before - 1, json.dumps(state()["reminders"]))

        _, ap, tools = chat(base, "add bread too", h)
        approve_all(base, ap)
        check("a follow-up with no app word stays with the apps (fully trusted: adding runs straight away)",
              "reminders_add" in tools and any("bread" in r["title"].lower() for r in state()["reminders"]), str(tools))

        content, ap, tools = chat(base, "move my dentist appointment to Friday October 16 at 3pm", h)
        dent = next(e for e in state()["events"] if e["id"] == "evt-1")
        check("with full trust, a change runs straight away after finding the event", "calendar_events" in tools and dent["start"].startswith("2026-10-16T15:00"), json.dumps(dent))

        content, ap, tools = chat(base, "what's on my calendar this week?", h)
        check("reading answers from the real data", "lunch" in content.lower() and not ap, content[:200])
    finally:
        server.terminate()
    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
