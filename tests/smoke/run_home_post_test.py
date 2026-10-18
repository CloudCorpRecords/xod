"""
Agent Mail with no shared post office (services/home_post.py, post_office.py, agent_mail's outbox): two whole Xods,
each hosting its own post office on its own port, set up from the chat's tool and the app's API.
  - each claims an address on its own computer's post office
  - mail goes from one to the other and back
  - mail sent while the other Xod is off waits in the outbox, and arrives once it's back (its post office opens again
    by itself when Xod starts)
  - nobody but this computer can claim names on its post office
  - a message nobody can take is given up on after the outbox's time, and says why
  - the networks it offers: this network (<name>.local), Tailscale when it's on
  - auto-reply, per contact: only for contacts you know, never task requests, never a reply (no endless loop)
Throwaway data folders and keyrings; addresses on 127.0.0.1 (as two computers on a network would be).
  .venv/bin/python tests/smoke/run_home_post_test.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-homepost-"))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Xod:
    def __init__(self, who: str):
        self.who, self.port, self.post = who, free_port(), free_port()
        self.env = {**os.environ, "XOD_DATA_DIR": str(TMP / who), "XOD_HOME_POST_PORT": str(self.post), "PYTHONUTF8": "1",
                    "XOD_NO_UPDATE_CHECK": "1", "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                    "XOD_TEST_KEYRING_FILE": str(TMP / f"{who}-keys.json"), "PYTHONPATH": str(ROOT),
                    "XOD_TEST_AUTO_REPLY": "1"}     # auto-replies written without a model
        (TMP / who).mkdir(exist_ok=True)
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
                                     cwd=ROOT, env=self.env, stdout=open(TMP / f"{self.who}.log", "a"), stderr=subprocess.STDOUT)
        for _ in range(240):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/mail/status", timeout=2)
                return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError(f"{self.who}'s Xod didn't start")

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(20)
            self.proc = None

    def api(self, method, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return {"_status": e.code, **json.loads(e.read() or b"{}")}

    def py(self, code: str) -> dict:
        out = subprocess.run([sys.executable, "-c", "import asyncio, json\n" + code], cwd=ROOT, env=self.env,
                             capture_output=True, text=True, timeout=120)
        line = next((l for l in reversed(out.stdout.splitlines()) if l.startswith("{")), "")
        if not line:
            print(out.stdout[-600:], out.stderr[-1200:])
        return json.loads(line or "{}")


def inbox(x: Xod) -> list:
    x.api("POST", "/api/mail/check", {})
    msgs = []
    for box in ("inbox", "requests"):
        for t in x.api("GET", f"/api/mail/threads?box={box}").get("threads", []):
            msgs.append(t["last"])
    return msgs


def main():
    rene, dana = Xod("rene"), Xod("dana")
    try:
        rene.start()
        dana.start()

        print("H1 each Xod hosts its own post office")
        r = rene.api("POST", "/api/mail/setup", {"name": "rene", "relay": "this computer", "network": f"127.0.0.1:{rene.post}"})
        check("rene's address is on rene's own computer", r.get("success") and r.get("address") == f"rene@127.0.0.1:{rene.post}"
              and r.get("self_hosted"), str(r)[:300])
        about = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{rene.post}/.well-known/xod-relay", timeout=5).read())
        check("…its post office answers on its own port, as a post office", about.get("service") == "xod-relay" and about.get("self_hosted"), str(about))
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{rene.post}/api/mail/status", timeout=5)
            leak = True
        except urllib.error.HTTPError:
            leak = False
        check("…and serves nothing else of Xod there", not leak)
        st = rene.api("GET", "/api/mail/status")
        check("the status says it's hosted here and running", st.get("self_hosted") and (st.get("home") or {}).get("running"), str(st)[:300])
        r = dana.py(f"""
from app.agent import tools
print(json.dumps(asyncio.run(tools.execute_tool('agent_mail_setup', {{'name': 'dana', 'network': '127.0.0.1:{dana.post}'}}))))""")
        # (that ran in a separate process; dana's running Xod opens her post office from the saved setup)
        dana.stop()
        dana.start()
        check("the chat's setup, with no post office named, hosts on this computer", r.get("success") and r.get("self_hosted")
              and r.get("address") == f"dana@127.0.0.1:{dana.post}" and "no server needed" in r.get("note", ""), str(r)[:300])
        st = dana.api("GET", "/api/mail/status")
        check("…and when Xod starts again, its post office opens again by itself", (st.get("home") or {}).get("running"), str(st)[:300])

        print("H2 mail between the two")
        r = rene.api("POST", "/api/mail/send", {"to": f"dana@127.0.0.1:{dana.post}", "text": "Hi Dana, can your agent send me the notes?"})
        check("rene sends to dana's computer", r.get("success") and r.get("sent_to") and not r.get("queued"), str(r)[:300])
        got = inbox(dana)
        check("dana's Xod has it (in Requests: rene isn't a contact yet)", any(m["peer"] == f"rene@127.0.0.1:{rene.post}" and "the notes" in m["text"] for m in got), str(got)[:300])
        r = dana.api("POST", "/api/mail/send", {"to": f"rene@127.0.0.1:{rene.post}", "text": "Sure, here they are."})
        got = inbox(rene)
        check("…and dana's reply reaches rene", r.get("success") and any("here they are" in m["text"] for m in got), str(got)[:300])

        print("H3 the other Xod is off: the outbox")
        dana.stop()
        r = rene.api("POST", "/api/mail/send", {"to": f"dana@127.0.0.1:{dana.post}", "text": "Sent while your computer was off."})
        check("sending to a Xod that's off doesn't fail: it's queued", r.get("success") and r.get("queued") and "outbox" in r.get("note", ""), str(r)[:300])
        st = rene.api("GET", "/api/mail/status")
        check("…the outbox shows it", st.get("outbox") == 1, str(st.get("outbox")))
        r = rene.api("POST", "/api/mail/outbox/retry", {})
        check("…trying again while it's still off keeps it waiting", r.get("sent") == 0 and r.get("waiting") == 1, str(r))
        dana.start()
        r = rene.api("POST", "/api/mail/outbox/retry", {})
        check("once dana's Xod is back, it's delivered", r.get("sent") == 1 and r.get("waiting") == 0, str(r))
        got = inbox(dana)
        check("…and dana reads it", any("while your computer was off" in m["text"] for m in got), str(got)[:300])
        sent = rene.py("""
from app.services import agent_mail
with agent_mail._db() as c:
    print(json.dumps({'statuses': [r[0] for r in c.execute("SELECT status FROM mail_messages WHERE direction='out' ORDER BY created_at")]}))""")
        check("…and it's marked sent", sent.get("statuses", [])[-1:] == ["sent"], str(sent))

        print("H4 only this computer claims names here")
        from fastapi.testclient import TestClient
        sys.path.insert(0, str(ROOT))
        from app.services.post_office import make_app
        tc = TestClient(make_app(str(TMP / "po-test.db"), lambda: "example.local:8790"))
        r = tc.post("/v1/names", json={"name": "mallory", "sign_pub": "x", "box_pub": "y", "ts": int(time.time()), "sig": "z"})
        check("a claim from another computer is refused", r.status_code == 403 and "only claimed from the computer" in r.json().get("detail", ""), r.text[:200])
        r = tc.post("/v1/mail", json={"to": "nobody@example.local:8790", "envelope": {"eph": "a", "nonce": "b", "ct": "c"}})
        check("…but mail for a name that isn't here gets a plain 404", r.status_code == 404, r.text[:200])

        print("H5 giving up")
        rene.stop()
        r = dana.api("POST", "/api/mail/send", {"to": f"rene@127.0.0.1:{rene.post}", "text": "Nobody's home."})
        old = dana.py("""
from app.services import agent_mail
import time
with agent_mail._db() as c:
    c.execute("UPDATE mail_outbox SET created_at=?", (time.time() - 8 * 86400,))
print(json.dumps(asyncio.run(agent_mail.retry_outbox(force=True))))""")
        check("after the outbox's 7 days, it gives up", r.get("queued") and old.get("gave_up") == 1 and old.get("waiting") == 0, f"{r} {old}")
        why = dana.py("""
from app.services import agent_mail
with agent_mail._db() as c:
    r = c.execute("SELECT status, problem FROM mail_messages WHERE direction='out' ORDER BY created_at DESC").fetchone()
print(json.dumps({'status': r[0], 'problem': r[1]}))""")
        check("…and the message says it wasn't delivered and why", why.get("status") == "failed" and "7 days" in (why.get("problem") or ""), str(why))

        print("H7 auto-reply")
        rene.start()
        R, D = f"rene@127.0.0.1:{rene.post}", f"dana@127.0.0.1:{dana.post}"
        rene.api("PATCH", f"/api/mail/contacts/{urllib.parse.quote(D)}", {"trust": "unknown"})    # (writing to dana made her known)
        r = rene.api("PATCH", f"/api/mail/contacts/{urllib.parse.quote(D)}", {"auto_reply": True})
        check("auto-reply can't be turned on for someone who isn't a known contact", r.get("success") is False, str(r))
        rene.api("PATCH", f"/api/mail/contacts/{urllib.parse.quote(D)}", {"trust": "known"})
        r = rene.api("PATCH", f"/api/mail/contacts/{urllib.parse.quote(D)}", {"auto_reply": True})
        check("…and can for a known contact", r.get("success") is True, str(r))
        dana.api("PATCH", f"/api/mail/contacts/{urllib.parse.quote(R)}", {"trust": "known"})
        dana.api("PATCH", f"/api/mail/contacts/{urllib.parse.quote(R)}", {"auto_reply": True})     # both auto-reply: no loop allowed
        inbox(dana)
        dana.api("POST", "/api/mail/send", {"to": R, "text": "Are you around Friday?"})
        inbox(rene)                                        # rene's Xod receives it and answers by itself
        got = inbox(dana)
        check("rene's Xod answers a known contact by itself", any(m["text"].startswith("Auto reply to: Are you around Friday") for m in got), str([m['text'][:50] for m in got]))
        inbox(dana)
        rene_got = inbox(rene)
        check("…and dana's Xod doesn't auto-answer that reply (no endless loop)",
              not any(m["text"].startswith("Auto reply to: Auto reply") for m in rene_got), str([m['text'][:50] for m in rene_got]))
        dana.api("POST", "/api/mail/send", {"to": R, "text": "Please run my report.", "kind": "task_request"})
        before = len([m for m in inbox(dana) if m["text"].startswith("Auto reply to: Please run")])
        inbox(rene)
        after = len([m for m in inbox(dana) if m["text"].startswith("Auto reply to: Please run")])
        check("…and never to a task request (those still wait for your OK)", before == after == 0)

        print("H6 how others reach this computer")
        nets = dana.api("GET", "/api/mail/networks")
        kinds = [n["kind"] for n in nets.get("networks", [])]
        check("it offers this network (<name>.local)", any(n["kind"] == "network" and n["domain"].endswith(f".local:{dana.post}") for n in nets.get("networks", [])), str(nets))
        check("…and Tailscale, or how to get it", "tailscale" in kinds or "tailscale_missing" in kinds, str(kinds))
        from app.services import home_post
        check("private networks use plain HTTP; the internet doesn't", all(home_post.is_private_host(h) for h in
              ("macbook.local", "mac.tail1234.ts.net", "192.168.1.20", "10.0.0.5", "100.101.102.103"))
              and not any(home_post.is_private_host(h) for h in ("post.example.com", "8.8.8.8")))
    finally:
        rene.stop()
        dana.stop()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
