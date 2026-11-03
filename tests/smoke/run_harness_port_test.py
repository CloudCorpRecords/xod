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
  - the observability tier (surface v2): a task's lifecycle, a model call, a tool call and
    approval cards land on the timeline through the real choke points; deep reads return a
    seeded wedged mission's plan, claimed vs verified and pause clock; unknown ids are 404s;
    the snapshot carries presence booleans only; the timeline pages by cursor
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
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
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
    print("H9 the tool surface")
    from app.services import crew as crew_mod

    async def aerr_status(coro_fn):
        try:
            await coro_fn()
        except harness.HarnessError as e:
            return e.status
        return None

    check("the manifest is refused without the token",
          err_status(lambda: harness.manifest(IP, {})) == 401)
    check("invoke is refused without the token",
          await aerr_status(lambda: harness.invoke(IP, {}, {"tool": "config_read", "args": {}})) == 401)
    _, man = harness.manifest(IP, HDRS)
    names = [t["name"] for t in man["tools"]]
    check("the manifest lists the curated surface, each tool versioned and kinded",
          names == ["config_read", "family_read", "usage_read", "locate", "task_submit", "task_status",
                    "crew_start", "crew_status", "browser_open", "browser_state",
                    "timeline", "task_read", "mission_read", "crew_read", "snapshot", "social_read"]
          and man["version"] == 2
          and all(t["version"] == 1 and t["kind"] in ("read", "write") for t in man["tools"]), str(names))
    check("an unknown tool is a machine-readable 404",
          await aerr_status(lambda: harness.invoke(IP, HDRS, {"tool": "nope", "args": {}})) == 404)

    code, out = await harness.invoke(IP, HDRS, {"tool": "config_read", "args": {}})
    res = out.get("result") or {}
    check("config_read answers with the configuration, presence only",
          code == 200 and out["state"] == "ok" and "llm_provider" in res and "llm_model" in res
          and "version" in res and "keys_present" in res
          and "masked" not in json.dumps(out) and TOKEN not in json.dumps(out), str(out)[:300])
    code, out = await harness.invoke(IP, HDRS, {"tool": "usage_read", "args": {}})
    check("usage_read answers with the ledger summary",
          code == 200 and out["state"] == "ok" and isinstance(out.get("result"), dict), str(out)[:200])
    check("a bogus usage range is a 400, not a guess",
          await aerr_status(lambda: harness.invoke(IP, HDRS, {"tool": "usage_read", "args": {"range": "bogus"}})) == 400)
    code, out = await harness.invoke(IP, HDRS, {"tool": "family_read", "args": {}})
    check("family_read answers with the registry's active members",
          code == 200 and [m["id"] for m in out["result"]["members"]] == ["macbook-pro", "razer", "phone"],
          str(out)[:200])

    real_locate = harness._locate_impl
    harness._locate_impl = lambda req: {"status": "not_found", "candidates": [], "tried": [{"route": "filename", "hits": 0}]}
    code, out = await harness.invoke(IP, HDRS, {"tool": "locate", "args": {"request": "the zebrafish deck"}})
    check("locate answers through the surface with the hunt's own shape",
          code == 200 and out["state"] == "ok" and out["result"]["status"] == "not_found", str(out)[:200])
    harness._locate_impl = real_locate

    before_tasks, before_cards = len(task_queue.list()), len(mcp_hub.pending)
    code, out = await harness.invoke(IP, HDRS, {"tool": "task_submit", "args": {"request": "surface task"}})
    srec = out.get("result") or {}
    check("task_submit through the surface still only makes a card",
          out["state"] == "waiting_for_ok" and srec.get("ref", "").startswith("h-")
          and len(task_queue.list()) == before_tasks and len(mcp_hub.pending) == before_cards + 1, str(out)[:300])
    code, out = await harness.invoke(IP, HDRS, {"tool": "task_status", "args": {"ref": srec["ref"]}})
    check("…and task_status reads it back, claimed apart from verified",
          out["state"] == "waiting_for_ok" and out["claimed"] is None
          and (out.get("verified") or {}).get("verified_status") == "not_checked", str(out)[:300])
    card = next(p for p in mcp_hub.pending.values() if (p.get("args") or {}).get("ref") == srec.get("beacon_ref"))
    await mcp_hub.approve(card["id"])
    code, out = await harness.invoke(IP, HDRS, {"tool": "task_status", "args": {"ref": srec["ref"]}})
    check("…following it to queued after Approve", out["state"] == "queued", str(out)[:200])

    real_crew_start, real_crew_job = crew_mod.start, crew_mod.job

    async def fake_crew_start(task, folder="", title="", approval_mode=None, agent="", isolate=None, kind=""):
        return {"id": "fakejob1", "status": "working", "agent": agent or "claude", "kind": kind}

    crew_mod.start = fake_crew_start
    crew_mod.job = lambda jid: {"id": jid, "status": "done", "steps": [], "approvals": {}, "questions": {},
                                "seat": {"model_requested": "sonnet", "model_actual": "the-model-that-ran"}}
    code, out = await harness.invoke(IP, HDRS, {"tool": "crew_start", "args": {"task": "board brief", "kind": "board"}})
    check("crew_start starts the job and hands back its view",
          code == 200 and out["state"] == "ok" and out["result"]["id"] == "fakejob1"
          and out["result"]["kind"] == "board", str(out)[:300])
    code, out = await harness.invoke(IP, HDRS, {"tool": "crew_status", "args": {"job": "fakejob1"}})
    check("crew_status reads the job back, seat record included",
          code == 200 and out["result"]["seat"]["model_actual"] == "the-model-that-ran", str(out)[:200])
    crew_mod.start, crew_mod.job = real_crew_start, real_crew_job

    class FakeBrowser:
        proc = object()
        current_url = "about:blank"
        current_title = "Empty Tab"

        async def navigate(self, url):
            self.current_url = url
            self.current_title = "Example Domain"
            return {"success": True}

        async def extract_content(self, max_length=10000):
            return {"text": "a page of text"}

    fake_browser = FakeBrowser()
    real_browser = harness._browser_service
    harness._browser_service = lambda: fake_browser
    code, out = await harness.invoke(IP, HDRS, {"tool": "browser_open", "args": {"url": "https://example.com/"}})
    check("browser_open navigates and reads back where it landed",
          code == 200 and out["state"] == "ok" and out["claimed"] == "https://example.com/"
          and (out.get("verified") or {}).get("url") == "https://example.com/", str(out)[:300])
    code, out = await harness.invoke(IP, HDRS, {"tool": "browser_state", "args": {}})
    check("browser_state reports the browser's own state",
          code == 200 and out["result"]["running"] is True
          and out["result"]["url"] == "https://example.com/", str(out)[:200])
    harness._browser_service = real_browser

    print("H10 the observability tier")
    import time as _time
    from app.services import activity as activity_mod, agent_log as agent_log_mod
    from app.services import event_log, llm_routing, missions as missions_mod

    check("timeline is refused without the token",
          await aerr_status(lambda: harness.invoke(IP, {}, {"tool": "timeline", "args": {}})) == 401)

    # A task through the queue's real choke points: created, waiting on a question, answered.
    t = task_queue.create("observability probe task")
    tid = t["id"]
    task_queue._update(tid, note="Need a folder", status="waiting", waiting_kind="question",
                       waiting_detail={"message": "which folder?"})
    task_queue.answer(tid, "the blue one")

    code, out = await harness.invoke(IP, HDRS, {"tool": "timeline", "args": {"task_id": tid}})
    evs = (out.get("result") or {}).get("events") or []
    kinds = [e["kind"] for e in evs]
    check("the task lifecycle is on the timeline, in order",
          code == 200 and kinds == ["task.created", "task.status", "task.status", "task.action"], str(kinds))
    check("events carry the correlation shape and a stable order",
          bool(evs) and all(set(("seq", "ts", "kind", "actor", "ids", "payload")) <= set(e) for e in evs)
          and all(e["ids"].get("task_id") == tid for e in evs)
          and [e["seq"] for e in evs] == sorted(e["seq"] for e in evs), str(evs)[:200])
    waiting_ev = next((e for e in evs if e["kind"] == "task.status" and (e["payload"] or {}).get("to") == "waiting"), None)
    check("the waiting event carries the question text",
          waiting_ev is not None
          and (waiting_ev["payload"].get("waiting_detail") or {}).get("message") == "which folder?",
          str(waiting_ev))
    action_ev = next((e for e in evs if e["kind"] == "task.action"), None)
    check("the answer is an action by the user",
          action_ev is not None and action_ev["payload"].get("action") == "answer"
          and action_ev["actor"] == "user", str(action_ev))

    # A model call, a tool call and an approval card, all under the task's origin.
    tok = activity_mod.origin.set(f"task:{tid}")
    try:
        llm_routing.record("openai", "gpt-6.1-terra", {"prompt_tokens": 10, "completion_tokens": 5},
                           task="", kind="", key_profile="")
        agent_log_mod.step("read_file", {"path": "notes.md"}, {"success": True}, duration_ms=7)
        agent_log_mod.approval_opened("h10-card", "tool", "Files", "Write notes.md?")
        agent_log_mod.approval_decided("h10-card", "allowed")
    finally:
        activity_mod.origin.reset(tok)

    code, out = await harness.invoke(IP, HDRS, {"tool": "timeline", "args": {"kinds": "model", "task_id": tid}})
    mevs = (out.get("result") or {}).get("events") or []
    check("a model call lands with provider, model, tokens and the ledger price",
          len(mevs) == 1 and mevs[0]["payload"]["provider"] == "openai"
          and mevs[0]["payload"]["model"] == "gpt-6.1-terra"
          and mevs[0]["payload"]["total_tokens"] == 15
          and abs((mevs[0]["payload"]["cost_usd"] or 0) - 0.000035) < 1e-9, str(mevs)[:300])
    code, out = await harness.invoke(IP, HDRS, {"tool": "timeline", "args": {"kinds": "tool", "task_id": tid}})
    tevs = (out.get("result") or {}).get("events") or []
    check("a tool call lands with its duration and outcome",
          len(tevs) == 1 and tevs[0]["payload"]["tool"] == "read_file"
          and tevs[0]["payload"]["ok"] is True and tevs[0]["payload"]["duration_ms"] == 7,
          str(tevs)[:200])
    code, out = await harness.invoke(IP, HDRS, {"tool": "timeline", "args": {"kinds": "card"}})
    cevs = (out.get("result") or {}).get("events") or []
    ck = [e["kind"] for e in cevs]
    answered = [e for e in cevs if e["kind"] == "card.answered" and (e["payload"] or {}).get("card_id") == "h10-card"]
    check("approval cards open and are answered on the timeline",
          "card.created" in ck and len(answered) == 1
          and answered[0]["payload"]["decision"] == "allowed", str(ck))

    code, out = await harness.invoke(IP, HDRS, {"tool": "task_read", "args": {"task_id": tid}})
    tres = out.get("result") or {}
    check("task_read returns the row, the steps, the wedge state and the events",
          code == 200 and tres.get("task", {}).get("id") == tid
          and any(s.get("tool") == "read_file" for s in tres.get("steps") or [])
          and isinstance(tres.get("wedge"), dict) and "events" in tres, str(out)[:300])
    check("task_read of an unknown id is a 404",
          await aerr_status(lambda: harness.invoke(IP, HDRS, {"tool": "task_read", "args": {"task_id": "task_nope"}})) == 404)

    # A seeded mission with a wedged step: subtask B waits on the probe task, paused 15 min ago.
    now = _time.time()
    mc = missions_mod._conn()
    mc.execute("INSERT INTO missions (id, goal, state, continue_on_failure, plan_json, created_at, updated_at, started_at) "
               "VALUES (?,?,?,?,?,?,?,?)",
               ("m-h10", "seeded grading mission", "paused", 0,
                json.dumps({"goal": "seeded grading mission", "subtasks": [
                    {"title": "first step", "instruction": "do the first thing", "expected_effects": []},
                    {"title": "second step", "instruction": "do the second thing", "expected_effects": []}]}),
                now - 3600, now - 900, now - 3500))
    mc.execute("INSERT INTO mission_subtasks (id, mission_id, idx, title, instruction, status, verified_status, "
               "task_id, claimed, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               ("s-h10-a", "m-h10", 0, "first step", "do the first thing", "done", "verified",
                None, "the first thing is done", now - 3500, now - 3000))
    mc.execute("INSERT INTO mission_subtasks (id, mission_id, idx, title, instruction, status, verified_status, "
               "task_id, claimed, paused_at, pause_resume_attempts, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               ("s-h10-b", "m-h10", 1, "second step", "do the second thing", "waiting", None,
                tid, None, now - 900, 1, now - 3400))
    mc.commit()
    mc.close()

    code, out = await harness.invoke(IP, HDRS, {"tool": "mission_read", "args": {"mission_id": "m-h10"}})
    mres = out.get("result") or {}
    subs = mres.get("subtasks") or []
    wedged = next((s for s in subs if s.get("id") == "s-h10-b"), {})
    check("mission_read returns the plan, claimed vs verified, and the wedged subtask's clocks",
          code == 200 and len((mres.get("mission", {}).get("plan") or {}).get("subtasks") or []) == 2
          and subs and subs[0].get("verified_status") == "verified"
          and subs[0].get("claimed") == "the first thing is done"
          and wedged.get("linked_task", {}).get("status") == "queued"
          and wedged.get("paused_at") is not None and wedged.get("pause_resume_attempts") == 1
          and "usage" in mres and "events" in mres, str(out)[:400])
    check("mission_read of an unknown id is a 404",
          await aerr_status(lambda: harness.invoke(IP, HDRS, {"tool": "mission_read", "args": {"mission_id": "m-nope"}})) == 404)

    crew_mod._jobs["job-h10"] = {
        "id": "job-h10", "title": "board seat probe", "agent": "claude", "kind": "board",
        "status": "working", "cwd": "/tmp", "started_at": now - 60, "ended_at": None, "error": None,
        "seat": {"model_requested": "sonnet", "model_actual": "the-model-that-ran",
                 "model_proof": "init event", "limitation": None},
        "usage": {"cost_usd": 0.01}, "answer": None,
        "steps": [{"kind": "tool", "tool": "Read", "summary": "read a file", "input": {"path": "x"}}],
        "approvals": [{"id": "a1", "summary": "write a file", "state": "waiting", "auto": False,
                       "decided_at": None, "input": {"cmd": "raw-shape"}},
                      {"id": "a2", "summary": "run tests", "state": "allowed", "auto": True,
                       "decided_at": now - 30}],
        "questions": [{"id": "q1", "question": "which folder?", "choices": ["a", "b"], "state": "waiting",
                       "answer": None, "answered_at": None, "suggestions": ["a"]}]}
    code, out = await harness.invoke(IP, HDRS, {"tool": "crew_read", "args": {"job": "job-h10"}})
    cres = out.get("result") or {}
    check("crew_read returns every approval state, the question and the seat, raw shapes hidden",
          code == 200 and cres.get("seat", {}).get("model_actual") == "the-model-that-ran"
          and [a["state"] for a in cres.get("approvals") or []] == ["waiting", "allowed"]
          and (cres.get("questions") or [{}])[0].get("question") == "which folder?"
          and "raw-shape" not in json.dumps(cres) and "suggestions" not in json.dumps(cres),
          str(out)[:300])
    check("crew_read of an unknown job is a 404",
          await aerr_status(lambda: harness.invoke(IP, HDRS, {"tool": "crew_read", "args": {"job": "job-nope"}})) == 404)
    del crew_mod._jobs["job-h10"]

    code, out = await harness.invoke(IP, HDRS, {"tool": "snapshot", "args": {}})
    snap = out.get("result") or {}
    check("snapshot answers with the app's own view, presence only",
          code == 200 and bool(snap.get("version"))
          and isinstance(snap.get("queues", {}).get("tasks"), dict)
          and isinstance(snap.get("family"), list) and len(snap.get("family") or []) == 3
          and isinstance(snap.get("config", {}).get("keys_present"), dict)
          and "partner" in snap and "event_log" in snap
          and (snap.get("usage_today") or {}).get("calls")
          and TOKEN not in json.dumps(out), str(out)[:300])

    for i in range(5):
        event_log.emit("test.ping", actor="h10", payload={"i": i})
    code, p1 = await harness.invoke(IP, HDRS, {"tool": "timeline", "args": {"kinds": "test", "limit": 2}})
    r1 = p1.get("result") or {}
    code, p2 = await harness.invoke(IP, HDRS, {"tool": "timeline",
                                                "args": {"kinds": "test", "limit": 2, "cursor": r1.get("next_cursor")}})
    r2 = p2.get("result") or {}
    seqs = [e["seq"] for e in (r1.get("events") or [])] + [e["seq"] for e in (r2.get("events") or [])]
    check("the timeline pages by cursor without overlap",
          len(seqs) == 4 and len(set(seqs)) == 4 and seqs == sorted(seqs), str(seqs))
    code, out = await harness.invoke(IP, HDRS, {"tool": "timeline", "args": {"kinds": "task"}})
    tk = [e["kind"] for e in (out.get("result") or {}).get("events") or []]
    check("kind prefixes filter exactly",
          bool(tk) and all(k.startswith("task.") for k in tk), str(tk)[:200])

    set_setting(harness.TOKEN_SETTING, "")
    check("with no token configured, invoke closes too (503)",
          await aerr_status(lambda: harness.invoke(IP, HDRS, {"tool": "config_read", "args": {}})) == 503)
    set_setting(harness.TOKEN_SETTING, TOKEN)



asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
