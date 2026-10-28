"""
The phone MCP (services/phone_mcp.py, phone_pair.py, post_office pairing codes):
an unpaired phone is refused, a tampered/changed-key phone is refused, an expired
or reused QR code is refused, a capability that's off answers "not allowed" with
nothing sent, a revoke bites on the next call, tools/list matches the spec, and a
Crew/task approval pushed to the phone is applied from its answer.

  .venv/bin/python tests/smoke/run_phone_mcp_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-phonemcp-"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1",
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_SECRETS_SERVICE": "Xod test keys"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


class FakeTransport:
    """A paired phone in a box: records every call, answers with the scripted reply."""

    def __init__(self, reply=None, error=None):
        self.sent, self.reply, self.error = [], reply or {"ok": True, "answer": "Approve"}, error

    async def send(self, address, payload):
        if self.error:
            raise RuntimeError(self.error)
        self.sent.append((address, payload))
        return payload["request_id"]

    async def wait_reply(self, address, request_id, timeout):
        return self.reply


async def main():
    from app.services import agent_mail, phone_mcp, phone_pair

    print("P1 pairing: only on the phone's own screen, QR code one-time and expiring")
    r = phone_mcp.pair_phone("phone@mac.local:8790")
    check("pairing a phone that never paired (no pinned contact) is refused", not r["success"], str(r))

    real_status = agent_mail.status
    agent_mail.status = lambda: {"set_up": True, "address": "sam@mac.local:8790", "safety_number": "123 456"}
    offer = phone_pair.create_offer()
    agent_mail.status = real_status
    check("a QR offer holds post office, computer, safety number and a code",
          offer["success"] and offer["payload"]["computer"] == "sam@mac.local:8790"
          and offer["payload"]["safety"] == "123 456" and bool(offer["payload"]["code"])
          and "<svg" in (offer["qr_svg"] or ""), str(offer)[:200])
    code = offer["payload"]["code"]
    check("the code checks out, is consumed once, then never again",
          phone_pair.check_code(code) and phone_pair.consume_code(code) and not phone_pair.check_code(code)
          and not phone_pair.consume_code(code))
    agent_mail.status = lambda: {"set_up": True, "address": "sam@mac.local:8790", "safety_number": "1"}
    offer2 = phone_pair.create_offer()
    agent_mail.status = real_status
    st = phone_pair._load()
    st["expires_at"] = time.time() - 1
    phone_pair._save(st)
    check("an expired QR code is refused", not phone_pair.check_code(offer2["payload"]["code"])
          and not phone_pair.consume_code(offer2["payload"]["code"]))
    agent_mail.status = lambda: {"set_up": True, "address": "sam@mac.local:8790", "safety_number": "1"}
    offer_race = phone_pair.create_offer()
    agent_mail.status = real_status
    race_code, race_results = offer_race["payload"]["code"], []
    threads = [threading.Thread(target=lambda: race_results.append(phone_pair.consume_code(race_code)))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("two registrations racing for one code: exactly one consumes it",
          race_results.count(True) == 1 and race_results.count(False) == 1, str(race_results))

    # The post office itself: a signed registration from "remote" with the live code gets in, once.
    from fastapi.testclient import TestClient

    from app.services import mail_crypto as mc
    from app.services.post_office import make_app
    agent_mail.status = lambda: {"set_up": True, "address": "sam@mac.local:8790", "safety_number": "1"}
    offer3 = phone_pair.create_offer()
    agent_mail.status = real_status
    app = make_app(str(TMP / "po.db"), lambda: "mac.local:8790", pair_code_ok=phone_pair.consume_code)
    client = TestClient(app, client=("192.168.1.50", 5000))  # not this computer
    ident = mc.Identity.new()
    reg = mc.registration(ident, "phone")
    rr = client.post("/v1/names", json=reg)
    check("claiming a name remotely without a code is refused", rr.status_code == 403, str(rr.status_code))
    rr = client.post("/v1/names", json={**reg, "pair_code": offer3["payload"]["code"]})
    check("…with the live pairing code, the signed registration is accepted", rr.status_code == 200, rr.text[:200])
    ident2 = mc.Identity.new()
    rr = client.post("/v1/names", json={**mc.registration(ident2, "phone2"), "pair_code": offer3["payload"]["code"]})
    check("…and the same code a second time is refused (one-time)", rr.status_code == 403, str(rr.status_code))

    print("P2 the MCP calls: paired only, capability-gated, logged, keys pinned")
    fake = FakeTransport()
    phone_mcp.transport = fake
    out = await phone_mcp.call_tool("phone_open_app", address="phone@mac.local:8790", app="Maps")
    check("an unpaired phone is refused and nothing is sent", not out["success"] and not fake.sent, str(out))

    with agent_mail._db() as c:
        c.execute("INSERT OR REPLACE INTO mail_contacts (address, sign_pub, box_pub, safety, trust, first_seen, updated_at) "
                  "VALUES (?,?,?,?,?,?,?)", ("phone@mac.local:8790", "sig", "box", "123 456", "mine", time.time(), time.time()))
    r = phone_mcp.pair_phone("phone@mac.local:8790", "Sam's phone")
    check("a phone whose keys are pinned as mine pairs", r["success"], str(r))
    phone_mcp.record_hello("phone@mac.local:8790", {"capabilities": {"apps": True, "screen": False}, "battery": 82})
    out = await phone_mcp.call_tool("phone_open_app", address="phone@mac.local:8790", app="Maps")
    check("an allowed call goes over the transport with its request id", out["success"] and len(fake.sent) == 1
          and fake.sent[0][1]["phone_action"] == "phone_open_app", str(out))
    out = await phone_mcp.call_tool("phone_screen_read", address="phone@mac.local:8790")
    check("a capability that's off answers 'not allowed on this phone' and sends nothing",
          not out["success"] and "not allowed on this phone" in out["error"] and len(fake.sent) == 1, str(out))
    out = await phone_mcp.call_tool("phone_location", address="phone@mac.local:8790")
    check("a capability the phone never reported is off until switched on (nothing sent)",
          not out["success"] and "not allowed on this phone" in out["error"] and len(fake.sent) == 1, str(out))
    phone_mcp.record_hello("phone@mac.local:8790", {"capabilities": {"apps": False}})
    out = await phone_mcp.call_tool("phone_open_app", address="phone@mac.local:8790", app="Maps")
    check("a revoke on the phone takes effect on the next call", not out["success"] and len(fake.sent) == 1, str(out))
    phone_mcp.record_hello("phone@mac.local:8790", {"capabilities": {"apps": True}})

    fake.error = "phone@mac.local:8790's keys have changed since you last talked."
    out = await phone_mcp.call_tool("phone_open_app", address="phone@mac.local:8790", app="Maps")
    check("a phone whose keys changed (tampered) is refused", not out["success"] and "keys have changed" in out["error"], str(out))
    fake.error = None

    listed = phone_mcp.phone_list()
    check("phone_list: the phone, its battery, and this computer locked/awake",
          listed["phones"][0]["battery"] == 82 and "locked" in listed["computer"] and "awake_held" in listed["computer"],
          str(listed)[:200])
    check("every call is logged on this side", len(phone_mcp.audit_log()) >= 3
          and all("tool" in e for e in phone_mcp.audit_log()), str(phone_mcp.audit_log()[-1:]))
    check("the tool set matches the spec",
          sorted(phone_mcp.TOOLS) == ["phone_app_run", "phone_ask", "phone_calendar", "phone_contacts",
                                      "phone_fetch", "phone_file_read", "phone_files_list", "phone_location",
                                      "phone_notifications", "phone_open_app",
                                      "phone_screen_read", "phone_screen_tap", "phone_send", "phone_sms_send",
                                      "phone_stop"], str(sorted(phone_mcp.TOOLS)))
    check("TOOLS matches the shared contract in docs/phone_actions.json (every action, capability, needs_tap)",
          phone_mcp.contract_mismatches() == [], str(phone_mcp.contract_mismatches()))
    from app.phone_mcp import server as mcp_server
    names = [t.name for t in await mcp_server.list_tools()]
    check("…and the stdio MCP server's tools/list is exactly those plus phone_list",
          names == ["phone_list", "phone_send", "phone_ask", "phone_open_app", "phone_screen_read",
                    "phone_screen_tap", "phone_location", "phone_sms_send", "phone_notifications",
                    "phone_calendar", "phone_contacts", "phone_fetch", "phone_app_run", "phone_files_list",
                    "phone_file_read", "phone_stop"], str(names))

    print("P4 background phone tools: each mapped to its capability")
    phone_mcp.record_hello("phone@mac.local:8790", {"capabilities": {
        "sms": True, "notifications": True, "calendar": True, "contacts": True,
        "network": True, "background_apps": True, "files": True}})
    before = len(fake.sent)
    cases = [("phone_sms_send", {"to": "+15551234567", "text": "hi"}, "phone_sms_send", True),
             ("phone_notifications", {}, "phone_notifications", False),
             ("phone_calendar", {}, "phone_calendar", False),
             ("phone_contacts", {"query": "Ana"}, "phone_contacts", False),
             ("phone_fetch", {"url": "https://example.com"}, "phone_fetch", False),
             ("phone_app_run", {"app": "Maps"}, "phone_app_run", False),
             ("phone_files_list", {"path": ""}, "phone_files_list", False),
             ("phone_file_read", {"path": "notes.txt"}, "phone_file_read", False),
             ("phone_stop", {}, "phone_stop", False)]
    ok_all = True
    for tool, kw, action, needs in cases:
        out = await phone_mcp.call_tool(tool, address="phone@mac.local:8790", **kw)
        sent = fake.sent[-1][1] if len(fake.sent) > before else {}
        ok_all = ok_all and out["success"] and sent.get("phone_action") == action and sent.get("needs_ok") is needs
        before = len(fake.sent)
    check("sms/notifications/calendar/contacts/fetch/app_run/files/stop send the phone's action name with the right needs_ok", ok_all, str(fake.sent[-9:]))
    phone_mcp.record_hello("phone@mac.local:8790", {"capabilities": {"sms": False, "network": False}})
    n0 = len(fake.sent)
    out = await phone_mcp.call_tool("phone_sms_send", address="phone@mac.local:8790", to="+1", text="x")
    out2 = await phone_mcp.call_tool("phone_stop", address="phone@mac.local:8790")
    check("a switched-off capability is refused, but phone_stop still goes (it only stops)",
          not out["success"] and "not allowed" in out["error"] and out2["success"] and len(fake.sent) == n0 + 1, f"{out} {out2}")

    print("P5 iPhone Mirroring: fake window, off by default, paused says so plainly")
    from app.services import iphone_mirror
    class El:
        def __init__(self, role, label="", kids=()):
            self.role, self.label, self.kids = role, label, list(kids)
    class FakeMirrorAX:
        def __init__(self, root):
            self.root, self.pressed = root, []
        def create_app(self, pid): return self.root
        def attr(self, el, name):
            return {"AXRole": el.role, "AXTitle": el.label, "AXChildren": el.kids}.get(name)
        def element_at(self, x, y): return self.root.kids[1]
        def perform(self, el, action): self.pressed.append((el.label, action)); return 0
    real_enabled = iphone_mirror.enabled
    iphone_mirror.enabled = lambda: False
    out = await phone_mcp.call_tool("phone_screen_read", address="iphone-mirroring")
    check("mirroring off by default: refused, nothing read", not out["success"] and "off" in out["error"], str(out))
    iphone_mirror.enabled = lambda: True
    iphone_mirror._injected.update({"pid": 4242, "screenshot": lambda: "data:image/png;base64,AA=="})
    paused_ax = FakeMirrorAX(El("AXWindow", "", [El("AXStaticText", "iPhone in Use"), El("AXButton", "OK")]))
    out = iphone_mirror.read_screen(ax=paused_ax)
    check("mirroring paused because the iPhone was unlocked: said plainly",
          not out["success"] and out.get("paused") and "unlocked" in out["error"], str(out))
    live_ax = FakeMirrorAX(El("AXWindow", "", [El("AXStaticText", "Messages"), El("AXButton", "Send")]))
    out = iphone_mirror.read_screen(ax=live_ax)
    check("a fake mirroring window is read (texts, buttons, screenshot)",
          out["success"] and out["texts"] == ["Messages"] and out["buttons"] == ["Send"] and out["screenshot_uri"], str(out))
    out = iphone_mirror.tap(10, 20, ax=live_ax)
    check("a tap presses the element under the point via AXPress", out["success"] and live_ax.pressed == [("Send", "AXPress")], str(live_ax.pressed))
    out = await phone_mcp.call_tool("phone_send", address="iphone-mirroring", text="hi")
    check("non-screen tools are refused on the mirrored iPhone", not out["success"] and "isn't available" in out["error"], str(out))
    entry = [p for p in phone_mcp.phone_list()["phones"] if p.get("platform") == "iphone-mirroring"]
    # phone_list builds its own AX; only shape is asserted here (live state depends on the real Mac)
    check("phone_list carries the mirrored iPhone as platform iphone-mirroring", len(entry) == 1 and entry[0]["address"] == "iphone-mirroring", str(entry))
    iphone_mirror.enabled = real_enabled
    iphone_mirror._injected.clear()

    print("P3 approvals from the phone (slice 3 bridge)")
    phone_mcp.record_hello("phone@mac.local:8790", {"capabilities": {"apps": True, "messages": True}})
    from app.services.task_queue import task_queue
    t = task_queue.create("book the flight")
    task_queue._update(t["id"], status="waiting", waiting_kind="approval", waiting_detail={"label": "Book", "message": "TAP 89"})
    n = await phone_mcp.bridge_once()
    check("a waiting approval is pushed to the phone and its Approve is applied",
          n == 1 and task_queue.get(t["id"])["status"] == "queued", f"n={n} {task_queue.get(t['id'])['status']}")
    n = await phone_mcp.bridge_once()
    check("…and never pushed twice", n == 0, str(n))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
