"""
Xod on your phone (services/phone.py), against a stand-in Telegram: connecting a bot, pairing with the code, only
the paired account being listened to, messages becoming tasks, Approve / Cancel buttons, answering a question by
replying, results coming back as plain text, and connected-app approvals. No model and no real Telegram needed.

  python tests/smoke/run_phone_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="xod-phone-")
os.environ.update(XOD_DATA_DIR=TMP, PYTHON_KEYRING_BACKEND="tests.support.filekeyring.FileKeyring",
                  XOD_TEST_KEYRING_FILE=os.path.join(TMP, "keys.json"), XOD_SECRETS_SERVICE="Xod test keys")

TOKEN = "123456789:AAH" + "x" * 32
SENT = []          # what Xod sent: (method, params)
UPDATES = []       # what "Telegram" hands Xod next (getUpdates)
msg_id = [100]


class FakeTelegram(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        token, _, method = self.path.strip("/").partition("/")
        params = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if token != f"bot{TOKEN}":
            return self._reply({"ok": False, "description": "Unauthorized"}, 401)
        SENT.append((method, params))
        result = {"getMe": {"id": 1, "username": "my_xod_bot"}, "getUpdates": []}.get(method, True)
        if method in ("sendMessage", "editMessageText"):
            msg_id[0] += 1
            result = {"message_id": msg_id[0], "text": params.get("text")}
        if method == "getUpdates":
            import time
            off = params.get("offset") or 0
            result = [u for u in UPDATES if u["update_id"] >= off]
            if not result:
                time.sleep(0.5)
        self._reply({"ok": True, "result": result})

    def _reply(self, body, code=200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeTelegram)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["XOD_TELEGRAM_API"] = f"http://127.0.0.1:{srv.server_address[1]}"

from app.services import phone                       # noqa: E402  (after the environment is set)
from app.services.task_queue import task_queue      # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


def sent(method="sendMessage", since=0):
    return [p for m, p in SENT[since:] if m == method]


def msg(chat, text, reply_to=None):
    m = {"message_id": 5, "chat": {"id": chat, "type": "private"}, "from": {"first_name": "Sam" if chat == 111 else "Mallory"}, "text": text}
    if reply_to:
        m["reply_to_message"] = {"message_id": reply_to}
    return m


async def main():
    bad = await phone.connect("not a token")
    check("a token that isn't one is refused", not bad["success"])
    wrong = await phone.connect("987654321:" + "y" * 35)
    check("…and so is one Telegram doesn't accept", not wrong["success"] and "didn't accept" in wrong["error"], str(wrong))

    r = await phone.connect(TOKEN)
    code = r.get("pair_code") or ""
    check("connecting checks the bot and makes a 6-digit pairing code", r["success"] and r["bot"] == "my_xod_bot" and len(code) == 6, str(r))
    from app.services import secret_store
    check("…the token is kept in the Keychain, not in a file", secret_store.get(phone.TOKEN_KEY) == TOKEN
          and TOKEN not in Path(TMP, "phone.json").read_text())
    if phone._loop_task:
        phone._loop_task.cancel()        # updates are handed in directly below

    n = len(SENT)
    await phone._on_message(msg(222, "hello"))
    check("a stranger without the code isn't linked", not phone.status()["paired"] and "send it the code" in sent(since=n)[0]["text"])
    await phone._on_message(msg(111, f"/start {code}"))
    st = phone.status()
    check("the code links the phone (from the /start link too)", st["paired"] and st["who"] == "Sam" and st["pair_code"] is None, str(st))
    await phone._on_message(msg(222, code))
    check("…once: the same code from someone else does nothing", phone._load()["chat_id"] == 111)

    n = len(SENT)
    await phone._on_message(msg(222, "delete my files"))
    check("another Telegram account is ignored (and no task is made)", "someone's private Xod" in sent(since=n)[0]["text"]
          and not any(t["request"] == "delete my files" for t in task_queue.list()))

    n = len(SENT)
    await phone._on_message(msg(111, "find me a cheap flight to Lisbon in May"))
    task = next((t for t in task_queue.list() if t["request"].startswith("find me a cheap flight")), None)
    check("a message from the phone becomes a task", task is not None and task["id"] in phone._load()["from_phone"])
    check("…and the phone hears it's on it", any("On it" in p["text"] for p in sent(since=n)))

    # Waiting for an OK: Approve / Cancel buttons; only the paired account can press them.
    task_queue._update(task["id"], status="waiting", waiting_kind="approval",
                       waiting_detail={"label": "Book flight", "page": "TAP Air Portugal", "message": "€89, May 12"})
    n = len(SENT)
    await phone._tell(task_queue.get(task["id"]), "approval", True)
    m = sent(since=n)[-1]
    kb = m.get("reply_markup", {}).get("inline_keyboard", [[]])[0]
    check("an approval comes with Approve / Cancel buttons", "Book flight" in m["text"] and [b["callback_data"] for b in kb] == [f"ok:{task['id']}", f"no:{task['id']}"], json.dumps(m)[:300])
    await phone._on_button({"id": "q1", "data": f"ok:{task['id']}", "message": {"message_id": 7, "chat": {"id": 222}, "text": "x"}})
    check("…a button pressed by someone else does nothing", task_queue.get(task["id"])["status"] == "waiting")
    await phone._on_button({"id": "q2", "data": f"ok:{task['id']}", "message": {"message_id": 7, "chat": {"id": 111}, "text": m["text"]}})
    check("…Approve from the phone approves it", task_queue.get(task["id"])["status"] == "queued")
    check("…and the buttons are replaced by what happened", any("Approved" in p.get("text", "") for p in sent("editMessageText")))

    # A question: reply to the message to answer.
    task_queue._update(task["id"], status="waiting", waiting_kind="question", waiting_detail={"message": "Window or aisle?"})
    n = len(SENT)
    await phone._tell(task_queue.get(task["id"]), "question", True)
    asked = msg_id[0]
    check("a question is asked on the phone", "Window or aisle?" in sent(since=n)[-1]["text"])
    await phone._on_message(msg(111, "aisle please", reply_to=asked))
    t = task_queue.get(task["id"])
    check("…replying to it answers that task", t["status"] == "queued" and (t.get("next_action") or {}).get("text") == "aisle please", str(t.get("next_action")))

    # Done: the result as plain text, cut to fit.
    n = len(SENT)
    await phone._tell({**task_queue.get(task["id"]), "result": "**Booked** TAP 1234 <!-- xod-apps:[] -->\n### Details\n" + "x" * 6000}, "done", True)
    out = sent(since=n)[0]["text"]
    check("the result comes back as plain text that fits a message", out.startswith("✓") and "**" not in out and "<!--" not in out
          and "###" not in out and len(out) < 4096, out[:120])

    # Connected apps: a change waiting for an OK is asked about on the phone too.
    from app.services.mcp_client import mcp_hub
    import time
    mcp_hub.pending["abc123"] = {"id": "abc123", "tool": "mcp__mac__reminders_delete", "server": "mac", "tool_name": "reminders_delete",
                                 "args": {"reminder_id": "x-apple-reminder://1"}, "created_at": time.time()}
    n = len(SENT)
    await phone._apps_waiting(since=time.time() - 5)
    await phone._apps_waiting(since=time.time() - 5)
    asks = [p for p in sent(since=n) if "reminders delete" in p["text"]]
    check("a connected app's change waiting for an OK is asked on the phone, once", len(asks) == 1
          and asks[0]["reply_markup"]["inline_keyboard"][0][1]["callback_data"] == "mno:abc123")
    await phone._on_button({"id": "q3", "data": "mno:abc123", "message": {"message_id": 9, "chat": {"id": 111}, "text": "x"}})
    check("…Decline from the phone declines it", "abc123" not in mcp_hub.pending)

    phone.set_notify_all(False)
    n = len(SENT)
    other = task_queue.create("something started on the computer")
    phone.on_task(other, "done")
    await asyncio.sleep(0.3)
    check("with 'every task' off, only tasks from the phone are reported", not sent(since=n))

    # The real listening loop: an update from "Telegram" is handled once (the offset moves past it).
    UPDATES.append({"update_id": 500, "message": msg(111, "water the plants at 6")})
    phone.start()
    for _ in range(40):
        await asyncio.sleep(0.25)
        if any(t["request"] == "water the plants at 6" for t in task_queue.list()):
            break
    await asyncio.sleep(1.5)       # a second poll: the same update must not come back
    made = [t for t in task_queue.list() if t["request"] == "water the plants at 6"]
    check("listening: a message sent to the bot is picked up, once", len(made) == 1 and phone._load()["offset"] == 501, f"{len(made)} {phone._load().get('offset')}")

    phone.disconnect()
    check("unlinking forgets the token and the pairing", not secret_store.get(phone.TOKEN_KEY) and not phone.status()["connected"]
          and not Path(TMP, "phone.json").exists())

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    srv.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    # The stand-in Telegram's handler threads can still be writing to stderr as Python shuts down, which Windows turned
    # into a crash after every check had passed: leave at once with the result instead.
    os._exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
