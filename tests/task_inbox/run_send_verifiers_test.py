"""
Real send tools are read back at their destination (services/outcomes.py verifiers), so an interrupted
send is checked, never blindly repeated (orchestrator scope §9; acceptance report finding 4).

Agent Mail (agent_mail_send drafts a message that waits for the person's OK; the store is the destination):
  M1 a draft is CONFIRMED by reading it back from Agent Mail's store
  M2 the same draft again in the same task is not made twice
  M3 a call that breaks off after the draft was written is CONFIRMED, and the retry makes no second draft
  M4 a draft that was refused is FAILED

Phone (the paired phone's signed reply to the request id is the destination):
  P1 a message the phone confirms is CONFIRMED
  P2 an SMS the phone has not answered is UNKNOWN, not FAILED; the retry is not sent; the phone's late
     reply settles it CONFIRMED with exactly one request sent
  P3 an SMS the phone declines is FAILED
  P4 a request that never left (refused before sending) is FAILED
  P5 reads, and iPhone Mirroring, are not judged at the phone

Run: .venv/bin/python tests/task_inbox/run_send_verifiers_test.py [--repeat N]
"""
import asyncio, json, os, sys, tempfile, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_sendverify_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1", "XOD_SPEECH": "off",
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_SECRETS_SERVICE": "Xod test keys"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

from app.agent import tools
from app.services import activity, agent_mail, packs, phone_mcp, resources
from app.services import outcomes as OC

results = []
PHONE = "phone@post.example.com"


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


packs.tool_allowed = lambda name: True
agent_mail._notify = lambda *a, **k: None
phone_mcp.paired_phones = lambda: [{"address": PHONE, "capabilities": {c: True for c in phone_mcp.CAPABILITIES}}]


def phone_reply(rid, ok=True, **extra):
    """The phone's reply arriving in Agent Mail, as the real transport stores it."""
    with agent_mail._db() as c:
        c.execute("INSERT INTO mail_messages (id, msg_id, thread, direction, peer, recipients, kind, subject, text, status, created_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (f"in-{uuid.uuid4().hex[:10]}", uuid.uuid4().hex, "t", "in", PHONE, "[]", "message", "phone",
                   json.dumps({"request_id": rid, "ok": ok, **extra}), "new", time.time()))


class FakePhone:
    """Records every request; answers (or not) as scripted, storing the answer where the real one lands."""

    def __init__(self, answer=True, ok=True, error=None):
        self.sent, self.answer, self.ok, self.error = [], answer, ok, error

    async def send(self, address, payload):
        if self.error:
            raise RuntimeError(self.error)
        self.sent.append(payload)
        return payload["request_id"]

    async def wait_reply(self, address, rid, timeout):
        if not self.answer:
            return None
        phone_reply(rid, ok=self.ok, **({} if self.ok else {"error": "the person tapped No"}))
        return {"request_id": rid, "ok": self.ok, **({} if self.ok else {"error": "the person tapped No"})}


async def call(name, args, origin):
    tok = activity.origin.set(origin)
    try:
        return await tools.execute_tool(name, dict(args), reviewed=True)
    finally:
        activity.origin.reset(tok)


def end_task(origin):
    resources.release_holder(origin)      # a task's claims end with the task (task_queue does this)


def drafts(text):
    with agent_mail._db() as c:
        return c.execute("SELECT COUNT(*) FROM mail_messages WHERE direction='out' AND text=?", (text,)).fetchone()[0]


async def main(repeat):
    for i in range(repeat):
        print(f"=== run {i + 1}/{repeat}")
        tag = uuid.uuid4().hex[:6]

        print("M1-M2 an Agent Mail draft is read back, and not made twice")
        origin = f"task:m-{tag}"
        args = {"to": "sam@post.example.com", "text": f"Lunch at noon? {tag}"}
        r1 = await call("agent_mail_send", args, origin)
        check("CONFIRMED by reading the draft back", r1.get("outcome") == OC.CONFIRMED and "waits for the person's OK" in r1.get("outcome_evidence", ""), str(r1)[:300])
        r2 = await call("agent_mail_send", args, origin)
        check("the same draft again is not made twice", r2.get("already_done") and drafts(args["text"]) == 1, f"{r2} drafts={drafts(args['text'])}")

        print("M3 a draft call that breaks off after writing")
        origin = f"task:m3-{tag}"
        args = {"to": "sam@post.example.com", "text": f"Report attached {tag}"}
        real_draft = agent_mail.draft

        def draft_then_drop(*a, **k):
            real_draft(*a, **k)
            raise TimeoutError("connection dropped after the write")
        agent_mail.draft = draft_then_drop
        try:
            r1 = await call("agent_mail_send", args, origin)
        finally:
            agent_mail.draft = real_draft
        check("it landed, so CONFIRMED", r1.get("outcome") == OC.CONFIRMED and "landed" in r1.get("evidence", ""), str(r1)[:300])
        r2 = await call("agent_mail_send", args, origin)
        check("the retry makes no second draft", r2.get("already_done") and drafts(args["text"]) == 1, f"{r2} drafts={drafts(args['text'])}")

        print("M4 a refused draft")
        r = await call("agent_mail_send", {"to": "not an address", "text": f"x {tag}"}, f"task:m4-{tag}")
        check("FAILED", r.get("outcome") == OC.FAILED, str(r)[:300])

        print("P1 a message the phone confirms")
        phone_mcp.transport = fp = FakePhone()
        r = await call("phone", {"action": "message", "text": f"Your build finished {tag}"}, f"task:p1-{tag}")
        check("CONFIRMED by the phone's reply", r.get("outcome") == OC.CONFIRMED and "phone confirmed" in r.get("outcome_evidence", ""), str(r)[:300])
        end_task(f"task:p1-{tag}")

        print("P2 an SMS the phone has not answered")
        origin = f"task:p2-{tag}"
        phone_mcp.transport = fp = FakePhone(answer=False)
        args = {"action": "sms", "to": "+15550100", "text": f"Running late {tag}"}
        r1 = await call("phone", args, origin)
        check("UNKNOWN, not FAILED", r1.get("outcome") == OC.UNKNOWN, str(r1)[:300])
        r2 = await call("phone", args, origin)
        check("the retry is not sent", r2.get("outcome") == OC.UNKNOWN and len(fp.sent) == 1, f"{r2} sent={len(fp.sent)}")
        phone_reply(fp.sent[0]["request_id"])
        r3 = await call("phone", args, origin)
        check("the late reply settles it CONFIRMED, one request sent", r3.get("outcome") == OC.CONFIRMED and r3.get("already_done")
              and len(fp.sent) == 1, f"{r3} sent={len(fp.sent)}")
        end_task(origin)

        print("P3 an SMS the phone declines")
        phone_mcp.transport = FakePhone(ok=False)
        r = await call("phone", {"action": "sms", "to": "+15550100", "text": f"no {tag}"}, f"task:p3-{tag}")
        check("FAILED, saying so", r.get("outcome") == OC.FAILED and "answered no" in r.get("outcome_evidence", ""), str(r)[:300])
        end_task(f"task:p3-{tag}")

        print("P4 a request that never left")
        phone_mcp.transport = FakePhone(error="the phone's keys changed")
        r = await call("phone", {"action": "message", "text": f"never {tag}"}, f"task:p4-{tag}")
        check("FAILED", r.get("outcome") == OC.FAILED, str(r)[:300])
        end_task(f"task:p4-{tag}")

        print("P5 reads and Mirroring are not judged at the phone")
        check("a screen read is not", OC.verifier_for("phone", {"action": "screen_read"}) is None)
        check("iPhone Mirroring is not", OC.verifier_for("phone", {"action": "tap", "x": 1, "y": 2, "phone": phone_mcp.MIRROR_ADDRESS}) is None)
        check("a phone SMS is", OC.verifier_for("phone", {"action": "sms", "to": "1", "text": "x"}) is not None)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--repeat") + 1]) if "--repeat" in sys.argv else 1
    asyncio.run(main(n))
