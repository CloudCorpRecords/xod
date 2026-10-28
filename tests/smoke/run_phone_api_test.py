"""
The phone app's window into Xod (services/phone_api.py): only a paired phone is answered, every answer carries the
request id and fits one message, tasks/routines/goals act like the desktop's, and files are limited to what a
project lists (no paths, no '..').

  .venv/bin/python tests/smoke/run_phone_api_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-phoneapi-"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1",
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_SECRETS_SERVICE": "Xod test keys"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


async def main():
    from app.services import agent_mail, phone_api, phone_mcp

    PHONE, STRANGER = "phone@mac.local:8790", "eve@elsewhere.example"
    sent = []

    async def fake_send(to, text, subject="", kind="message", thread=None, in_reply_to=None, msg_id=None, origin="you"):
        sent.append({"to": to, "text": text, "subject": subject, "kind": kind, "thread": thread, "in_reply_to": in_reply_to})
        return {"success": True, "thread": thread}
    agent_mail.send = fake_send

    def request(op, args=None, rid="r1"):
        return {"id": f"m-{rid}", "thread": "t1", "text": json.dumps({"op": op, "args": args or {}, "request_id": rid})}

    print("Who is answered")
    check("an address that isn't a paired phone is not allowed", not phone_api.allowed(STRANGER))
    await phone_api.handle(STRANGER, request("summary"), "in-x")
    check("…and gets no answer at all", sent == [], str(sent))
    agent_mail._pin(PHONE, "c2lnbg", "Ym94", "mine")
    phone_mcp.pair_phone(PHONE, "Test phone")
    check("a phone paired on its own screen is allowed", phone_api.allowed(PHONE))
    await phone_api.handle(PHONE, request("summary", rid="abc"), "in-y")
    ans = json.loads(sent[-1]["text"]) if sent else {}
    check("its request is answered on the same thread with the same request id",
          sent and sent[-1]["to"] == PHONE and sent[-1]["subject"] == phone_api.REPLY_SUBJECT and sent[-1]["thread"] == "t1"
          and ans.get("request_id") == "abc" and ans.get("ok"), str(sent[-1:]))
    r = await phone_api.run("nope.op", {})
    check("an unknown op says to update Xod, not a crash", not r["ok"] and "doesn't know" in r["error"], str(r))

    print("Tasks")
    r = await phone_api.run("tasks.create", {"text": "find a cheap flight to Lisbon"})
    tid = (r.get("data") or {}).get("task", {}).get("id")
    check("a task started from the phone is a normal task", r["ok"] and tid, str(r))
    r = await phone_api.run("tasks.list", {})
    check("the list has it", r["ok"] and any(t["id"] == tid for t in r["data"]["tasks"]), str(r)[:300])
    r = await phone_api.run("tasks.get", {"id": tid})
    check("its detail has the request", r["ok"] and "Lisbon" in (r["data"]["task"].get("request") or ""), str(r)[:300])
    r = await phone_api.run("tasks.act", {"id": tid, "action": "cancel"})
    check("cancel works", r["ok"] and r["data"]["task"]["status"] == "cancelled", str(r)[:300])
    r = await phone_api.run("tasks.act", {"id": "missing", "action": "approve"})
    check("a missing task says so", not r["ok"] and "No such task" in r["error"], str(r))

    print("Routines and goals")
    from app.services import routines
    rt = routines.create("Morning news", "Summarize the news", {"kind": "daily", "time": "08:00"}, "test")
    r = await phone_api.run("routines.list", {})
    check("routines are listed", r["ok"] and any(x["id"] == rt["id"] for x in r["data"]["routines"]), str(r)[:300])
    r = await phone_api.run("routines.act", {"id": rt["id"], "action": "pause"})
    check("a routine can be paused", r["ok"] and not routines.get(rt["id"])["enabled"], str(r))
    r = await phone_api.run("routines.act", {"id": rt["id"], "action": "resume"})
    check("…and resumed", r["ok"] and routines.get(rt["id"])["enabled"], str(r))

    print("Reports and files")
    from app.services.reports import REPORTS_DIR
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "flights.html").write_text("<html><head><title>Cheap flights</title><style>x{}</style></head>"
                                              "<body><h1>Cheap flights</h1><p>TAP is &euro;89.</p></body></html>", encoding="utf-8")
    r = await phone_api.run("reports.list", {})
    check("reports are listed with their titles", r["ok"] and r["data"]["reports"][0]["title"] == "Cheap flights", str(r))
    r = await phone_api.run("reports.read", {"name": "flights.html"})
    check("a report reads as plain text", r["ok"] and "TAP is €89." in r["data"]["text"] and "<" not in r["data"]["text"], str(r))
    r = await phone_api.run("reports.read", {"name": "../xod.db"})
    check("a path outside reports is refused", not r["ok"], str(r))
    from app.services.project_storage import project_storage_service as ps
    projects = (await phone_api.run("files.projects", {}))["data"]["projects"]
    f = next((x for p in projects for x in p["files"]), None)
    slug = next((p["slug"] for p in projects if p["files"]), None)
    if f:
        r = await phone_api.run("files.read", {"slug": slug, "subfolder": f["subfolder"], "filename": f["filename"]})
        check("a file the project lists can be read", r["ok"] and isinstance(r["data"]["content"], str), str(r)[:300])
    else:
        check("a project with files exists to read from", False, str(projects)[:300])
    r = await phone_api.run("files.read", {"slug": slug or "research", "subfolder": "", "filename": "../../xod.db"})
    check("'..' or any file the project doesn't list is refused", not r["ok"] and "isn't in this project" in r["error"], str(r))

    print("Size")
    big = {"ok": True, "data": {"items": [{"text": "x" * 500} for _ in range(200)]}}
    text = phone_api.encode("big", big)
    check("a big answer is shortened to fit one message, still valid", len(text) <= phone_api.MAX_REPLY and json.loads(text)["ok"], str(len(text)))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
