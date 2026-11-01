"""
The harness port (services/harness.py, the /v1/harness/* beacon routes), with a stand-in
`tailscale` program and a fixture family registry:
  - both locks: another account is refused even with the token; no token and a wrong token are
    refused; with no token configured the port is closed (503), never open
  - the canary token is refused and the presentation lands in the security events
  - members answers with exactly the registry's active members and their capabilities
  - a self submission (task or mission) only becomes an approval card; Approve is what creates
    the work, through the beacon's own handoff; status keeps claimed apart from verified
  - naming this machine by its registry id is still a self submission
  - a relay is formed to the member's beacon with the remote ref recorded; polling maps the
    remote state back; an unreachable member is an honest state on the record, never a success;
    a member with no endpoint queues for relay; an unknown target is refused
  - only the submitting caller can read a record back, and records survive a cache drop
    (they are read back from the store on disk)
  .venv/bin/python tests/smoke/run_harness_port_test.py
"""
import asyncio
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-harness-"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []

REGISTRY = TMP / "registry.json"
REGISTRY.write_text(json.dumps({
    "family": "Test Family", "version": 0, "updated": "2026-10-31T00:00:00Z",
    "devices": [
        {"id": "macbook-pro", "name": "Test Mac", "platform": "macOS", "endpoint": "127.0.0.1",
         "capabilities": ["cloud model: test"], "public_key": "PLACEHOLDER-F0-no-key-enrolled",
         "signature": None, "last_seen": "2026-10-31T00:00:00Z", "status": "active", "note": None},
        {"id": "razer", "name": "Test Razer", "platform": "Windows 11", "endpoint": "100.80.212.74",
         "capabilities": ["local model: test", "Windows hands"], "public_key": "PLACEHOLDER-F0-no-key-enrolled",
         "signature": None, "last_seen": "2026-10-31T00:00:00Z", "status": "active", "note": None},
        {"id": "phone", "name": "Test Phone", "platform": "Android", "endpoint": None,
         "capabilities": ["approvals", "camera"], "public_key": "PLACEHOLDER-F0-no-key-enrolled",
         "signature": None, "last_seen": "2026-10-31T00:00:00Z", "status": "active", "note": None},
        {"id": "old-box", "name": "Old Box", "platform": "macOS", "endpoint": "100.80.212.99",
         "capabilities": [], "public_key": "PLACEHOLDER-F0-no-key-enrolled",
         "signature": None, "last_seen": "2026-10-31T00:00:00Z", "status": "revoked", "note": None},
    ]}))
os.environ["XOD_FAMILY_REGISTRY_PATH"] = str(REGISTRY)

STATE = TMP / "state.json"     # what the stand-in reports; the test rewrites it
FAKE = TMP / "tailscale"
FAKE.write_text(f"""#!{sys.executable}
import sys
a = sys.argv[1:]
if a[:1] == ["status"]:
    print(open({str(STATE)!r}).read())
elif a[:1] == ["whois"]:
    print(open({str(TMP / "who.json")!r}).read())
""")
FAKE.chmod(FAKE.stat().st_mode | stat.S_IEXEC)
STATE.write_text(json.dumps({"BackendState": "Running",
                             "Self": {"DNSName": "desk.tail0.ts.net.", "HostName": "desk", "UserID": 1,
                                      "TailscaleIPs": ["127.0.0.1", "fd7a::1"]},
                             "User": {"1": {"LoginName": "someone@example.com"}}, "Peer": {}}))
WHO = TMP / "who.json"
IP = "127.0.0.1"
TOKEN = "test-harness-token"
CANARY = "test-canary-token"
HDRS = {"X-Harness-Token": TOKEN}


def who(host="laptop", uid=1):
    WHO.write_text(json.dumps({"Node": {"ComputedName": host}, "UserProfile": {"ID": uid}}))


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def err_status(fn):
    """The HarnessError status fn raises, or None when it does not raise."""
    from app.services import harness
    try:
        fn()
    except harness.HarnessError as e:
        return e.status
    return None


async def main():
    from app.services import harness, xod_network as xn
    from app.services.storage import set_setting
    from app.services.task_queue import task_queue
    from app.services.mcp_client import mcp_hub
    xn.cli = lambda: str(FAKE)
    task_queue._kick = lambda: None           # queue it, don't run it
    who()
    set_setting(harness.TOKEN_SETTING, TOKEN)
    set_setting(harness.CANARY_SETTING, CANARY)

    print("H1 the two locks")
    check("no token header is refused", err_status(lambda: harness.members(IP, {})) == 401)
    check("a wrong token is refused", err_status(lambda: harness.members(IP, {"X-Harness-Token": "nope"})) == 401)
    check("the canary token is refused", err_status(lambda: harness.members(IP, {"X-Harness-Token": CANARY})) == 401)
    events = harness.security_events()
    check("…and the canary presentation is in the security events",
          len(events) == 1 and events[0]["kind"] == "canary_token_presented" and events[0]["ip"] == IP, str(events))
    who(uid=2)
    check("another account is refused even with the token", err_status(lambda: harness.members(IP, HDRS)) == 403)
    who()
    set_setting(harness.TOKEN_SETTING, "")
    check("no token configured closes the port (503)",
          err_status(lambda: harness.submit(IP, HDRS, {"request": "anything"})) == 503)
    set_setting(harness.TOKEN_SETTING, TOKEN)

    print("H2 members")
    _, view = harness.members(IP, HDRS)
    check("members are exactly the registry's active members",
          [m["id"] for m in view["members"]] == ["macbook-pro", "razer", "phone"], str(view)[:300])
    razer = next(m for m in view["members"] if m["id"] == "razer")
    check("…with endpoints and capabilities",
          razer["endpoint"] == "100.80.212.74" and "local model: test" in razer["capabilities"], str(razer))
    check("…and the interim state is stated", view["registry_state"] == "ok" and view["interim_unsigned"] is True)

    print("H3 a self task: the card decides")
    before_tasks, before_cards = len(task_queue.list()), len(mcp_hub.pending)
    _, rec = harness.submit(IP, HDRS, {"request": "list my downloads"})
    check("the submission waits for the OK", rec["state"] == "waiting_for_ok" and rec["beacon_ref"], str(rec)[:300])
    check("…a card was made", len(mcp_hub.pending) == before_cards + 1)
    check("…and nothing is in the task inbox yet", len(task_queue.list()) == before_tasks)
    _, st = harness.task_status(IP, HDRS, rec["ref"])
    check("status keeps claimed apart from verified",
          st["state"] == "waiting_for_ok" and st["claimed_result"] is None
          and st["verified_status"] == "not_checked" and st["report"] is None and bool(st["how_to_read"]), str(st)[:300])
    card = next(p for p in mcp_hub.pending.values() if (p.get("args") or {}).get("ref") == rec["beacon_ref"])
    done = await mcp_hub.approve(card["id"])
    check("Approve puts it in the task inbox", done.get("success") and len(task_queue.list()) == before_tasks + 1, str(done)[:200])
    _, st = harness.task_status(IP, HDRS, rec["ref"])
    check("…and the status follows it", st["state"] == "queued" and st["title"] == "list my downloads", str(st)[:300])

    print("H4 this machine by its registry id is still self")
    cards = len(mcp_hub.pending)
    _, rec_self = harness.submit(IP, HDRS, {"request": "second task", "target": "macbook-pro"})
    check("targeting the own member id submits locally",
          rec_self["target"] == "self" and rec_self["beacon_ref"] and len(mcp_hub.pending) == cards + 1, str(rec_self)[:300])
    card = next(p for p in mcp_hub.pending.values() if (p.get("args") or {}).get("ref") == rec_self["beacon_ref"])
    mcp_hub.decline(card["id"])

    print("H5 a self mission: the card decides that too")
    _, rec_m = harness.submit(IP, HDRS, {"request": "organize the test folder", "kind": "mission"})
    card = next(p for p in mcp_hub.pending.values() if (p.get("args") or {}).get("ref") == rec_m["beacon_ref"])
    check("the card says it is a mission", "run a mission" in card["why"], card["why"])
    done = await mcp_hub.approve(card["id"])
    from app.services import missions
    mid = (xn._incoming.get(rec_m["beacon_ref"]) or {}).get("mission_id")
    m = missions.get_mission(mid) if mid else None
    check("Approve creates the mission", done.get("success") and m is not None and m["goal"] == "organize the test folder",
          str(done)[:200])
    _, st = harness.task_status(IP, HDRS, rec_m["ref"])
    check("…and the status maps the mission state",
          st["state"] == (m or {}).get("state") and st["state"] != "waiting_for_ok"
          and st["title"] == "organize the test folder", str(st)[:300])

    print("H6 the relay")
    calls = []

    def fake_relay(method, endpoint, path, body=None):
        calls.append((method, endpoint, path, body))
        if method == "POST":
            return {"ref": "remote-ref-1", "status": "waiting_for_ok"}
        return {"ref": "remote-ref-1", "status": "done", "title": "Remote thing", "result": "all set"}

    harness._relay = fake_relay
    code, rec_r = harness.submit(IP, HDRS, {"request": "check the build", "target": "razer"})
    post = calls[0] if calls else ()
    check("the relay is formed to the member's beacon",
          code == 200 and rec_r["state"] == "waiting_for_ok" and rec_r["remote"]["remote_ref"] == "remote-ref-1"
          and post[0] == "POST" and post[1] == "100.80.212.74" and post[2] == "/v1/tasks"
          and post[3].get("request") == "check the build" and bool(post[3].get("from")), str(calls)[:300])
    _, st = harness.task_status(IP, HDRS, rec_r["ref"])
    check("…and polling maps the remote state back",
          st["state"] == "done" and st["claimed_result"] == "all set" and st["verified_status"] == "not_checked",
          str(st)[:300])
    _, rec_rm = harness.submit(IP, HDRS, {"request": "survey the drives", "target": "razer", "kind": "mission"})
    check("a relayed mission says the member takes it as a task",
          bool((rec_rm["remote"] or {}).get("kind_note")), str(rec_rm)[:300])

    def dead_relay(method, endpoint, path, body=None):
        raise harness.RelayError("could not reach the member's beacon: timed out")

    harness._relay = dead_relay
    code, rec_d = harness.submit(IP, HDRS, {"request": "check the build", "target": "razer"})
    check("an unreachable member is an honest state, not a success",
          code == 202 and rec_d["state"] == "relay_unreachable" and "timed out" in (rec_d["error"] or "")
          and rec_d["remote"]["reachable"] is False, str(rec_d)[:300])
    code, rec_p = harness.submit(IP, HDRS, {"request": "take a photo", "target": "phone"})
    check("a member with no endpoint queues for relay",
          code == 202 and rec_p["state"] == "queued_for_relay" and "endpoint" in (rec_p["error"] or ""), str(rec_p)[:300])
    check("an unknown target is refused", err_status(lambda: harness.submit(IP, HDRS, {"request": "x", "target": "nope"})) == 404)

    print("H7 validation")
    check("an empty request is refused", err_status(lambda: harness.submit(IP, HDRS, {"request": "  "})) == 400)
    check("an unknown kind is refused", err_status(lambda: harness.submit(IP, HDRS, {"request": "x", "kind": "bogus"})) == 400)
    check("an oversized request is refused", err_status(lambda: harness.submit(IP, HDRS, {"request": "x" * 4001})) == 400)

    print("H8 who can read what")
    check("an unknown ref is a 404", err_status(lambda: harness.task_status(IP, HDRS, "h-nope")) == 404)
    who(host="other-box")
    check("another of your devices cannot read your record",
          err_status(lambda: harness.task_status(IP, HDRS, rec["ref"])) == 404)
    who()
    harness._records = None     # forget the cache: the store on disk is the truth
    _, st = harness.task_status(IP, HDRS, rec["ref"])
    check("records are read back from the store", st["ref"] == rec["ref"] and st["state"] == "queued", str(st)[:200])


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
