#!/usr/bin/env python3
"""
Agent Mail end to end: a real relay and three separate Xods (alice, bob, carol) on this Mac, each
with its own data folder and a throwaway keychain file. Nothing touches your real data or Keychain.

  M1  each Xod claims an address; a taken name is refused
  M2  alice -> bob arrives, verified, in bob's Requests (alice isn't a contact yet)
  M3  the relay never holds readable text, and deletes mail once it's picked up
  M4  bob accepts alice: the thread moves to his Inbox; his reply reaches alice's Inbox
  M5  Xod's own tool only drafts: nothing is sent until the draft is approved
  M6  one message to two agents (bob and carol) reaches both, same thread
  M7  a forged message (someone else's keys claiming to be alice) is held, not shown as hers
  M8  if the relay swaps alice's keys, bob refuses to send to her until he checks
  M9  a task request becomes a Xod task only when bob approves it
  M10 auto-reply fires for a known contact with the toggle on, and is activity-logged
  M11 auto-reply can't be switched on for a stranger, and never fires for one
  M12 no auto-reply with the toggle off
  S1  a self-host registers a challenge-verified directory record on the public relay
  S2  a forged directory registration (and a key-stealing one) is rejected
  S3  a directory record goes stale after missed heartbeats; stale falls back to the public relay
  S4  direct delivery: mail goes straight to the self-host endpoint, not the public relay
  S5  fallback: with the self-host down, mail lands at the public relay and is picked up on poll
  .venv/bin/python tests/features/run_agent_mail_test.py
  XOD_TEST_RELAY=127.0.0.1:8787 .venv/bin/python tests/features/run_agent_mail_test.py   # any running relay,
      e.g. the Cloudflare Worker under `wrangler dev` (M3 and M8 look inside the Python relay's database, so they're skipped)
"""
import json
import os
import uuid
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod_mail_", dir="/tmp")).resolve()
results, procs = [], []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""), flush=True)


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _no_proxy_env():
    """Spawned servers only ever talk to 127.0.0.1, so drop proxy variables: some sandboxes
    set NO_PROXY entries (like a bare and a bracketed ::1) that httpx 0.28 fails to parse
    when it builds a client, even though no request would use a proxy."""
    return {k: v for k, v in os.environ.items()
            if k.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}


def wait(url):
    for _ in range(120):
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"{url} didn't start")


def call(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, **(json.loads(e.read() or b"{}") if e.code < 500 else {})}


# ---------------------------------------------------------------- start a relay and three Xods
EXTERNAL = os.environ.get("XOD_TEST_RELAY")
RELAY = EXTERNAL or f"127.0.0.1:{port()}"
if not EXTERNAL:
    procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", RELAY.split(":")[1]],
                                  cwd=ROOT / "relay", env={**_no_proxy_env(), "XOD_RELAY_DOMAIN": RELAY, "XOD_RELAY_DB": str(TMP / "relay.db"),
                                                           "XOD_RELAY_DIR_STALE": "4"},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
# A second relay that plays the self-host endpoint (carol's own post office) in S1-S5.
SELF = None
self_proc = None
if not EXTERNAL:
    SELF = f"127.0.0.1:{port()}"
    self_proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", SELF.split(":")[1]],
                                 cwd=ROOT / "relay", env={**_no_proxy_env(), "XOD_RELAY_DOMAIN": SELF, "XOD_RELAY_DB": str(TMP / "self.db")},
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(self_proc)
RUN = uuid.uuid4().hex[:5]          # fresh names each run, so a long-lived relay can be reused
X = {}
for who in ("alice", "bob", "carol"):
    p = port()
    env = {**_no_proxy_env(), "XOD_DATA_DIR": str(TMP / who), "XOD_TEST_KEYRING_FILE": str(TMP / f"{who}-keys.json"),
           "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring", "XOD_SECRETS_SERVICE": "Xod test keys",
           "PYTHONPATH": str(ROOT), "XOD_DEFAULT_RELAY": RELAY, "XOD_TEST_AUTO_REPLY": "1", "XOD_DIR_STALE_AFTER": "4"}
    procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(p)],
                                  cwd=ROOT, env=env, stdout=open(TMP / f"{who}.log", "w"), stderr=subprocess.STDOUT))
    X[who] = f"http://127.0.0.1:{p}"
try:
    wait(f"http://{RELAY}/.well-known/xod-relay")
    if SELF:
        wait(f"http://{SELF}/.well-known/xod-relay")
    for b in X.values():
        wait(b + "/api/mail/status")

    # M1
    addrs = {}
    for who, base in X.items():
        r = call(base, "POST", "/api/mail/setup", {"name": f"{who}{RUN}", "relay": RELAY})
        addrs[who] = r.get("address")
    check("M1 each Xod claims an address", all(addrs.values()), str(addrs))
    for base in X.values():   # Agent Mail is a preview add-on, off by default
        call(base, "POST", "/api/packs/agent-mail", {"enabled": True})
    r = call(X["bob"], "POST", "/api/mail/setup", {"name": f"alice{RUN}", "relay": RELAY})
    check("M1 a taken name is refused", not r.get("success") and "taken" in (r.get("error") or ""), r.get("error", ""))
    # bob's failed attempt must not have changed his own address
    call(X["bob"], "POST", "/api/mail/setup", {"name": f"bob{RUN}", "relay": RELAY})

    # M2
    secret = "The launch is on Thursday. Codeword: bluebird-4417."
    r = call(X["alice"], "POST", "/api/mail/send", {"to": addrs["bob"], "subject": "Plans", "text": secret})
    check("M2 alice sends", r.get("success"), str(r)[:200])
    if not EXTERNAL:
        relay_rows = sqlite3.connect(TMP / "relay.db").execute("SELECT envelope FROM mail").fetchall()
        stored = " ".join(r[0] for r in relay_rows)
        check("M3 relay holds only sealed envelopes", relay_rows and "bluebird" not in stored and "alice" not in stored,
              f"{len(relay_rows)} envelope(s)")
    call(X["bob"], "POST", "/api/mail/check")
    req = call(X["bob"], "GET", "/api/mail/threads?box=requests")["threads"]
    inbox = call(X["bob"], "GET", "/api/mail/threads?box=inbox")["threads"]
    got = req and req[0]["last"]
    check("M2 it lands in bob's Requests, verified", bool(got) and got["text"] == secret and got["verified"] == 1 and not inbox,
          f"requests={len(req)} inbox={len(inbox)}")
    if not EXTERNAL:
        left = sqlite3.connect(TMP / "relay.db").execute("SELECT COUNT(*) FROM mail").fetchone()[0]
        check("M3 relay deleted it after pickup", left == 0, f"{left} left")

    # M4
    call(X["bob"], "PATCH", f"/api/mail/contacts/{addrs['alice']}", {"trust": "known"})
    inbox = call(X["bob"], "GET", "/api/mail/threads?box=inbox")["threads"]
    check("M4 accepting alice moves the thread to Inbox", len(inbox) == 1)
    thread = inbox[0]["thread"]
    r = call(X["bob"], "POST", "/api/mail/send", {"to": addrs["alice"], "text": "Got it, see you Thursday.", "thread": thread,
                                                  "kind": "reply", "in_reply_to": got["msg_id"]})
    call(X["alice"], "POST", "/api/mail/check")
    a_inbox = call(X["alice"], "GET", "/api/mail/threads?box=inbox")["threads"]
    msgs = call(X["alice"], "GET", f"/api/mail/thread/{a_inbox[0]['thread']}")["messages"] if a_inbox else []
    check("M4 bob's reply reaches alice in the same thread",
          len(a_inbox) == 1 and a_inbox[0]["thread"] == thread and any(m["direction"] == "in" and "Thursday" in m["text"] for m in msgs),
          f"{len(msgs)} messages")

    # M5: Xod's tool, in alice's process, can only draft
    py = ("import asyncio,json\nfrom app.agent.tools import execute_tool\n"
          f"r=asyncio.run(execute_tool('agent_mail_send', {{'to': '{addrs['bob']}', 'text': 'Drafted by Xod for alice.'}}))\n"
          "print(json.dumps(r))")
    env = {**_no_proxy_env(), "XOD_DATA_DIR": str(TMP / "alice"), "XOD_TEST_KEYRING_FILE": str(TMP / "alice-keys.json"),
           "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring", "PYTHONPATH": str(ROOT)}
    try:
        out = subprocess.run([sys.executable, "-c", py], cwd=ROOT, env={**env, "PYTHONUNBUFFERED": "1"}, capture_output=True,
                             text=True, timeout=90).stdout
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    lines = [l for l in out.strip().splitlines() if l.startswith("{")]
    tool = json.loads(lines[-1]) if lines else {}
    call(X["bob"], "POST", "/api/mail/check")
    before = [m for t in call(X["bob"], "GET", "/api/mail/threads?box=inbox")["threads"] for m in [t["last"]] if "Drafted by Xod" in m["text"]]
    check("M5 Xod's tool makes a draft that needs approval", tool.get("needs_approval") and not before, tool.get("message", ""))
    drafts = call(X["alice"], "GET", "/api/mail/threads?box=drafts")["threads"]
    r = call(X["alice"], "POST", f"/api/mail/drafts/{drafts[0]['id']}/approve") if drafts else {}
    call(X["bob"], "POST", "/api/mail/check")
    after = [t for t in call(X["bob"], "GET", "/api/mail/threads?box=inbox")["threads"] if "Drafted by Xod" in t["last"]["text"]]
    check("M5 approving the draft sends it", r.get("success") and len(after) == 1, str(r)[:120])
    acts = call(X["alice"], "GET", "/api/activity?limit=20").get("items", [])
    check("M5 the send is in alice's Activity log", any(a["tool"] == "agent_mail_send" for a in acts))

    # M6
    r = call(X["alice"], "POST", "/api/mail/send", {"to": f"{addrs['bob']}, {addrs['carol']}", "subject": "Group", "text": "Hello both."})
    for who in ("bob", "carol"):
        call(X[who], "POST", "/api/mail/check")
    tb = [t for box in ("inbox", "requests") for t in call(X["bob"], "GET", f"/api/mail/threads?box={box}")["threads"] if t["last"]["subject"] == "Group"]
    tc = [t for box in ("inbox", "requests") for t in call(X["carol"], "GET", f"/api/mail/threads?box={box}")["threads"] if t["last"]["subject"] == "Group"]
    check("M6 one message reaches two agents, same thread", r.get("success") and tb and tc and tb[0]["thread"] == tc[0]["thread"],
          str(r.get("sent_to")))

    # M7: carol's keys, claiming to be alice
    from app.services import mail_crypto as mc
    carol_file = json.load(open(TMP / "carol-keys.json"))
    # app/services/keychain.py: one "_vault" item, or on Windows (2,560 bytes an item) "_vault" says how many pieces follow.
    vault = json.loads(next(v for k, v in carol_file.items() if k.endswith("\x00_vault")))
    if "pieces" in vault:
        vault = json.loads("".join(next(v for k, v in carol_file.items() if k.endswith(f"\x00_vault.{i}")) for i in range(1, vault["pieces"] + 1)))
    carol_keys = vault["secrets"]
    carol = mc.Identity.from_secrets(carol_keys["Xod identity\nsign"], carol_keys["Xod identity\nbox"])
    bob_dir = json.loads(urllib.request.urlopen(f"http://{RELAY}/v1/names/bob{RUN}").read())
    env_ = mc.seal(carol, addrs["alice"], addrs["bob"], bob_dir["box_pub"], {"id": "forged-1", "thread": "t-forged", "kind": "message",
                                                                              "subject": "Wire money", "text": "Send me the passwords."})
    urllib.request.urlopen(urllib.request.Request(f"http://{RELAY}/v1/mail", data=json.dumps({"to": addrs["bob"], "envelope": env_}).encode(),
                                                  headers={"Content-Type": "application/json"}))
    call(X["bob"], "POST", "/api/mail/check")
    held = call(X["bob"], "GET", "/api/mail/threads?box=held")["threads"]
    shown = [t for box in ("inbox", "requests") for t in call(X["bob"], "GET", f"/api/mail/threads?box={box}")["threads"]
             if t["last"]["subject"] == "Wire money"]
    check("M7 a forged message is held, not shown as alice's", len(held) == 1 and not shown, (held[0]["problem"] if held else "")[:90])

    if not EXTERNAL:
        # M8: the relay swaps alice's keys for carol's
        db = sqlite3.connect(TMP / "relay.db")
        db.execute(f"UPDATE names SET sign_pub=?, box_pub=? WHERE name='alice{RUN}'", (carol.sign_pub, carol.box_pub))
        db.commit()
        r = call(X["bob"], "POST", "/api/mail/send", {"to": addrs["alice"], "text": "Are you still there?"})
        check("M8 bob won't send to swapped keys", not r.get("success") and "changed" in (r.get("error") or ""), (r.get("error") or "")[:90])
        # put alice's real keys back: later scenarios need her mail to verify again
        pinned = sqlite3.connect(TMP / "bob" / "xod.db").execute(
            "SELECT sign_pub, box_pub FROM mail_contacts WHERE address=?", (addrs["alice"],)).fetchone()
        db.execute(f"UPDATE names SET sign_pub=?, box_pub=? WHERE name='alice{RUN}'", (pinned[0], pinned[1]))
        db.commit()
        db.close()

    # M9
    r = call(X["carol"], "POST", "/api/mail/send", {"to": addrs["bob"], "kind": "task_request", "subject": "Quick favor",
                                                    "text": "What is 17 times 23?"})
    call(X["bob"], "POST", "/api/mail/check")
    call(X["bob"], "PATCH", f"/api/mail/contacts/{addrs['carol']}", {"trust": "known"})
    reqs = [t for t in call(X["bob"], "GET", "/api/mail/threads?box=inbox")["threads"] if t["last"]["kind"] == "task_request"]
    tasks_before = len(call(X["bob"], "GET", "/api/tasks")["tasks"])
    check("M9 a task request doesn't run by itself", reqs and tasks_before == 0, f"{tasks_before} tasks")
    r = call(X["bob"], "POST", f"/api/mail/messages/{reqs[0]['last']['id']}/run") if reqs else {}
    tasks = call(X["bob"], "GET", "/api/tasks")["tasks"]
    check("M9 approving it starts a Xod task", r.get("success") and len(tasks) == 1 and "17 times 23" in tasks[0]["request"],
          tasks[0]["title"] if tasks else str(r)[:100])
    if tasks:
        call(X["bob"], "POST", f"/api/tasks/{tasks[0]['id']}/cancel")

    # M10-M12: auto-reply. Bob's Xod runs with XOD_TEST_AUTO_REPLY=1, so the reply text is
    # canned instead of calling the LLM; everything else (toggle, gating, send path, logging)
    # is the real code.
    def auto_replies(who):
        return [t for box in ("inbox", "requests")
                for t in call(X[who], "GET", f"/api/mail/threads?box={box}")["threads"]
                if "Auto reply to:" in t["last"]["text"]]
    r = call(X["bob"], "PATCH", f"/api/mail/contacts/{addrs['alice']}", {"auto_reply": True})
    check("M10 auto-reply switches on for a known contact", r.get("success") is True, str(r))
    sends_before = len([a for a in call(X["bob"], "GET", "/api/activity?limit=50").get("items", [])
                        if a["tool"] == "agent_mail_send"])
    call(X["alice"], "POST", "/api/mail/send", {"to": addrs["bob"], "subject": "Hello?", "text": "Are you there, bob?"})
    call(X["bob"], "POST", "/api/mail/check")
    time.sleep(1)
    call(X["alice"], "POST", "/api/mail/check")
    check("M10 bob auto-replies to a known contact", len(auto_replies("alice")) == 1,
          f"{len(auto_replies('alice'))} auto-replies")
    sends_after = len([a for a in call(X["bob"], "GET", "/api/activity?limit=50").get("items", [])
                       if a["tool"] == "agent_mail_send"])
    check("M10 the auto-reply is in bob's Activity log", sends_after == sends_before + 1,
          f"{sends_before} -> {sends_after}")
    marked = sqlite3.connect(TMP / "bob" / "xod.db").execute(
        "SELECT COUNT(*) FROM mail_messages WHERE direction='out' AND origin='auto-reply'").fetchone()[0]
    check("M10 the auto-reply is marked as automatic", marked == 1, f"{marked} marked")

    # M11: carol back to a stranger; the toggle must refuse her, and her mail gets no reply
    call(X["bob"], "PATCH", f"/api/mail/contacts/{addrs['carol']}", {"trust": "unknown"})
    r = call(X["bob"], "PATCH", f"/api/mail/contacts/{addrs['carol']}", {"auto_reply": True})
    check("M11 auto-reply can't switch on for a stranger", r.get("success") is False, str(r))
    n_before = len(auto_replies("carol"))
    call(X["carol"], "POST", "/api/mail/send", {"to": addrs["bob"], "text": "Hi bob, stranger here."})
    call(X["bob"], "POST", "/api/mail/check")
    time.sleep(1)
    call(X["carol"], "POST", "/api/mail/check")
    check("M11 no auto-reply to a stranger", len(auto_replies("carol")) == n_before,
          f"{len(auto_replies('carol'))} auto-replies")

    # M12: toggle off for alice again
    call(X["bob"], "PATCH", f"/api/mail/contacts/{addrs['alice']}", {"auto_reply": False})
    n_before = len(auto_replies("alice"))
    call(X["alice"], "POST", "/api/mail/send", {"to": addrs["bob"], "text": "Still there?"})
    call(X["bob"], "POST", "/api/mail/check")
    time.sleep(1)
    call(X["alice"], "POST", "/api/mail/check")
    check("M12 no auto-reply with the toggle off", len(auto_replies("alice")) == n_before,
          f"{len(auto_replies('alice'))} auto-replies")

    if not EXTERNAL:
        PUB = f"http://{RELAY}"
        # S1: carol becomes a self-host: a fresh address on her own endpoint, directory on the public relay
        self_addr = call(X["carol"], "POST", "/api/mail/setup", {"name": f"carol{RUN}", "relay": SELF}).get("address")
        check("S1 carol claims an address on her own endpoint", self_addr == f"carol{RUN}@{SELF}", str(self_addr))
        r = call(X["carol"], "POST", "/api/mail/self-host", {"endpoint": f"http://{SELF}", "public_domain": RELAY})
        check("S1 self-host mode enables with a challenge-verified record", r.get("success"), str(r)[:200])
        d = call(PUB, "GET", "/v1/directory/" + quote(self_addr, safe=""))
        check("S1 the public directory lists her record, fresh",
              d.get("endpoint") == f"http://{SELF}" and d.get("stale") is False, str(d)[:200])

        # S2: forgeries
        evil = mc.Identity.new()
        tampered = mc.directory_registration(evil, self_addr, "https://evil.example.com")
        tampered["sig"] = tampered["sig"][:-4] + "AAAA"
        r = call(PUB, "POST", "/v1/directory/register", tampered)
        check("S2 a forged directory registration is rejected", r.get("http_error") == 400, str(r)[:120])
        steal = mc.directory_registration(evil, self_addr, "https://evil.example.com")
        r = call(PUB, "POST", "/v1/directory/register", steal)
        check("S2 a different key can't take her address", r.get("http_error") == 409, str(r)[:120])

        # S4 before S3's sleep: direct delivery while her record is fresh
        r = call(X["bob"], "POST", "/api/mail/send", {"to": self_addr, "subject": "Direct", "text": "Straight to your endpoint."})
        check("S4 bob sends to the self-host", r.get("success"), str(r)[:200])
        time.sleep(0.5)
        pub_waiting = sqlite3.connect(TMP / "relay.db").execute("SELECT COUNT(*) FROM mail").fetchone()[0]
        self_waiting = sqlite3.connect(TMP / "self.db").execute("SELECT COUNT(*) FROM mail").fetchone()[0]
        check("S4 it went straight to her endpoint, not the public relay",
              pub_waiting == 0 and self_waiting == 1, f"public={pub_waiting} self={self_waiting}")
        call(X["carol"], "POST", "/api/mail/check")
        got = [t for box in ("inbox", "requests") for t in call(X["carol"], "GET", f"/api/mail/threads?box={box}")["threads"]
               if t["last"]["subject"] == "Direct"]
        check("S4 carol picks it up from her own endpoint",
              len(got) == 1 and got[0]["last"]["text"] == "Straight to your endpoint.", str(len(got)))

        # S3: staleness. The probe's name is registered on the self endpoint, so a wrongly-fresh
        # record would deliver directly (202); correct stale handling falls back to the public
        # relay, which has no such name (404).
        probe, probe_addr = mc.Identity.new(), f"probe{RUN}@nowhere.example"
        call(f"http://{SELF}", "POST", "/v1/names", mc.registration(probe, f"probe{RUN}"))
        reg = mc.directory_registration(probe, probe_addr, f"http://{SELF}")
        r = call(PUB, "POST", "/v1/directory/register", reg)
        ans = mc.directory_challenge_answer(probe, probe_addr, r["challenge_id"], r["challenge"])
        r = call(PUB, "POST", "/v1/directory/challenge", ans)
        check("S3 answering the challenge activates the record", r.get("registered") is True, str(r)[:120])
        time.sleep(5)   # XOD_RELAY_DIR_STALE=4 on the public relay, XOD_DIR_STALE_AFTER=4 on the clients
        d = call(PUB, "GET", "/v1/directory/" + quote(probe_addr, safe=""))
        check("S3 the record goes stale after missed heartbeats", d.get("stale") is True, str(d)[:160])
        r = call(X["bob"], "POST", "/api/mail/send", {"to": probe_addr, "text": "Hello probe."})
        check("S3b a stale record falls back to the public relay",
              r.get("success") is False and "There's no" in (r.get("error") or ""), (r.get("error") or "")[:120])

        # S5: the self-host endpoint goes down; mail must still land at the public relay
        call(X["carol"], "POST", "/api/mail/self-host/beat")   # the S3 sleep made her record stale too
        self_proc.terminate()
        self_proc.wait(10)
        time.sleep(0.5)
        r = call(X["bob"], "POST", "/api/mail/send", {"to": self_addr, "subject": "Fallback", "text": "Your endpoint was down."})
        check("S5 send still succeeds when the self-host is down", r.get("success"), str(r)[:200])
        pub_waiting = sqlite3.connect(TMP / "relay.db").execute("SELECT COUNT(*) FROM mail").fetchone()[0]
        check("S5 the envelope landed at the public relay", pub_waiting == 1, f"{pub_waiting} waiting")
        call(X["carol"], "POST", "/api/mail/check")
        got = [t for box in ("inbox", "requests") for t in call(X["carol"], "GET", f"/api/mail/threads?box={box}")["threads"]
               if t["last"]["subject"] == "Fallback"]
        check("S5 carol picks the fallback mail up on her next poll", len(got) == 1, str(len(got)))
finally:
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(10)
        except Exception:
            p.kill()
    logs = {w: (TMP / f"{w}.log").read_text() if (TMP / f"{w}.log").exists() else "" for w in ("alice", "bob", "carol")}
    tb_ = [w for w, l in logs.items() if "Traceback" in l]
    check("no tracebacks in the Xod logs", not tb_, ", ".join(tb_))
    if tb_:
        shutil.copytree(TMP, "/tmp/xod_mail_failed", dirs_exist_ok=True)
        print("      logs kept in /tmp/xod_mail_failed")
    shutil.rmtree(TMP, ignore_errors=True)

failed = [n for n, ok in results if not ok]
print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
sys.exit(1 if failed else 0)
