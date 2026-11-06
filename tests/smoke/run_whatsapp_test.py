"""
WhatsApp messaging (services/whatsapp.py): the send path Xod was missing.

The failure this covers: the WhatsApp connection could surface chats to
read but gave the agent no way to answer anyone. The checks prove the
v1 contract from the spec (XOD-WHATSAPP-MESSAGING-SPEC-2026-11-06):

  - payload validation (empty contact, empty text, oversized text, a
    number as the contact accepted exactly as stated);
  - the approval gate, PROVEN with a spying executor: prepare, a wrong
    grant, and a declined item each produce ZERO executor calls; only
    the approved run sends, exactly once;
  - verification parsing on fixture chat screens (the sent text read
    back outgoing is verified; absent or indistinguishable is not);
  - chat opening resolution (exact, single partial, none, ambiguous);
  - the full phone route end to end over a fake phone transport: open
    WhatsApp, search, open the chat, type, send (meant=true on the
    send tap only), read back verified;
  - manifest states: no phone, switches off, switches on, observed
    not-installed; and the chat tool's own lane (bare send returns the
    approval card unsent; the granted replay sends once).

Runs against a throwaway data folder. No real phone, no real message.

  .venv/bin/python tests/smoke/run_whatsapp_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-whatsapp-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir()

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


# ------------------------------------------------------------------ fixture screens
LIST_SCREEN = """App: WhatsApp
[1] TextView "WhatsApp" @180,150
[2] ImageButton "Search" @950,150
[3] TextView "Mama" @400,350
[4] TextView "Rene Turcios" @420,520
[5] TextView "Aysha Medlin" @430,690"""

SEARCH_SCREEN = """App: WhatsApp
[1] EditText "Search..." @480,150
[2] TextView "Type a name or number" @400,320"""

RESULTS_SCREEN = """App: WhatsApp
[1] EditText "Rene Turcios" @480,150
[2] TextView "Rene Turcios" @420,400
[3] TextView "Rene T. (work)" @400,560"""

CHAT_BEFORE = """App: WhatsApp
[1] TextView "Rene Turcios" @400,150
[2] TextView "Did the build pass?" @320,500
[3] TextView "Yes, it shipped this morning" @760,700
[4] EditText "Type a message" @430,2250
[5] ImageButton "Voice message" @980,2250"""

CHAT_TYPED = """App: WhatsApp
[1] TextView "Rene Turcios" @400,150
[2] TextView "Did the build pass?" @320,500
[3] TextView "Yes, it shipped this morning" @760,700
[4] EditText "On my way, see you at 7" @430,2250
[5] Button "Send" @980,2250"""

CHAT_AFTER = """App: WhatsApp
[1] TextView "Rene Turcios" @400,150
[2] TextView "Did the build pass?" @320,500
[3] TextView "Yes, it shipped this morning" @760,700
[4] TextView "On my way, see you at 7" @740,950
[5] EditText "Type a message" @430,2250
[6] ImageButton "Voice message" @980,2250"""

SENT_TEXT = "On my way, see you at 7"


class FakeTransport:
    """A paired phone in a box, scripted for the WhatsApp send sequence:
    answers each call from the payload, records everything."""

    def __init__(self):
        self.sent = []
        self.reads = 0
        self.acts = 0

    async def send(self, address, payload):
        self.sent.append(payload)
        return payload["request_id"]

    async def wait_reply(self, address, request_id, timeout):
        payload = self.sent[-1]
        action = payload["phone_action"]
        if action == "phone_open_app":
            return {"ok": True, "text": "Opened WhatsApp"}
        if action == "phone_screen_read":
            self.reads += 1
            return {"ok": True, "text": [LIST_SCREEN, RESULTS_SCREEN, CHAT_AFTER][min(self.reads, 3) - 1]}
        if action == "phone_screen_act":
            self.acts += 1
            screens = [SEARCH_SCREEN, RESULTS_SCREEN, CHAT_BEFORE, CHAT_TYPED, ""]
            return {"ok": True, "text": "Done.\n" + screens[min(self.acts, 5) - 1]}
        return {"ok": True, "text": ""}


async def main():
    from app.services import whatsapp as wa

    print("V validation")
    check("empty contact is refused", wa.validate("", "hi") is not None)
    check("empty text is refused", wa.validate("Mama", "   ") is not None)
    check("oversized text is refused", wa.validate("Mama", "x" * (wa.MAX_TEXT + 1)) is not None)
    check("a number as the contact is accepted exactly as stated",
          wa.validate("+1 555 010 2030", "hi") is None)

    print("P screen parsing and verification")
    chat = wa.parse_screen(CHAT_BEFORE)
    msgs = wa.parse_messages(chat["elements"])
    check("fixture chat parses to its two messages",
          [m["text"] for m in msgs] == ["Did the build pass?", "Yes, it shipped this morning"], str(msgs))
    check("directions read from position: left incoming, right outgoing",
          [m["direction"] for m in msgs] == ["incoming", "outgoing"], str(msgs))
    check("the chat list is not a chat screen (no composer, no messages)",
          not wa.is_chat_screen(wa.parse_screen(LIST_SCREEN)["elements"])
          and wa.parse_messages(wa.parse_screen(LIST_SCREEN)["elements"]) == [])
    after = wa.parse_messages(wa.parse_screen(CHAT_AFTER)["elements"])
    ev = wa.verify_sent(msgs, after, SENT_TEXT, excerpt=CHAT_AFTER)
    check("the sent text read back outgoing verifies", ev["verified"] and ev["direction"] == "outgoing", str(ev))
    ev = wa.verify_sent(msgs, msgs, SENT_TEXT)
    check("a text that never appears does not verify", not ev["verified"], str(ev))
    before_same = [{"direction": "incoming", "text": "same words"}]
    after_same = [{"direction": "incoming", "text": "same words"},
                  {"direction": "incoming", "text": "a newer one"}]
    ev = wa.verify_sent(before_same, after_same, "same words")
    check("an identical older incoming text does not verify as our send", not ev["verified"], str(ev))

    print("P chat opening resolution")
    results_screen = wa.parse_screen(RESULTS_SCREEN)["elements"]
    kind, el = wa.find_chat_match(results_screen, "Rene Turcios")
    check("an exact chat name matches its row", kind == "exact" and el["n"] == 2, kind)
    kind, el = wa.find_chat_match(results_screen, "Rene")
    check("several partial matches are ambiguous, never a guess", kind == "ambiguous", kind)
    kind, el = wa.find_chat_match(results_screen, "Rene T.")
    check("one partial match resolves to its row", kind == "partial" and el["label"] == "Rene T. (work)", kind)
    kind, el = wa.find_chat_match(results_screen, "Nobody Here")
    check("no match is none", kind == "none", kind)
    kind, el = wa.find_chat_match(wa.parse_screen(LIST_SCREEN)["elements"], "Aysha")
    check("a single partial match resolves", kind == "partial" and el["label"] == "Aysha Medlin", kind)

    print("G the approval gate, proven with a spying executor")
    store = wa.WhatsAppStore(TMP / "wa-gate.json")
    spy = wa.FixtureExecutor()
    prep = store.prepare("Rene Turcios", "hello there", actor="test")
    check("prepare stores the item awaiting approval", prep["success"] and prep["item"]["state"] == "awaiting_approval")
    item = prep["item"]
    out = await wa.run_send(item["id"], executor=spy, store=store)
    check("an unapproved run_send sends nothing", not out["success"] and spy.calls == [], str(spy.calls))
    bad = store.approve_with_grant(item["id"], "not-the-grant", actor="test")
    out = await wa.run_send(item["id"], executor=spy, store=store)
    check("a wrong grant approves nothing and sends nothing",
          not bad["success"] and not out["success"] and spy.calls == [], str(spy.calls))
    ok = store.approve_with_grant(item["id"], item["grant"], actor="test")
    out = await wa.run_send(item["id"], executor=spy, store=store)
    check("the matching grant approves, and the send runs exactly once",
          ok["success"] and out["success"] and out.get("verified") and spy.calls == [("send_message", item["id"])],
          str(spy.calls))
    check("the item lands in state sent with its verification evidence",
          store.get(item["id"])["state"] == "sent"
          and store.get(item["id"])["verification"]["matched"] == "hello there")
    out = await wa.run_send(item["id"], executor=spy, store=store)
    check("a sent item cannot be sent twice", not out["success"] and len(spy.calls) == 1)
    prep2 = store.prepare("Mama", "call me", actor="test")
    dup = store.prepare("Mama", "call me", actor="test")
    check("an identical live message is a duplicate, not a second item",
          dup["success"] and dup.get("duplicate") is True and dup["item"]["id"] == prep2["item"]["id"],
          str(dup)[:160])
    store.decline(prep2["item"]["id"], actor="test")
    out = await wa.run_send(prep2["item"]["id"], executor=spy, store=store)
    check("a declined item sends nothing", not out["success"] and len(spy.calls) == 1)

    print("M manifest surface states")
    st = wa.surface_state(store=store, phones=[])
    check("no phone paired: cannot read or send, no send route",
          st["whatsapp"]["can_send"] is False and st["whatsapp"]["can_read"] is False
          and st["whatsapp"]["send_route"] is None)
    phone_on = [{"address": "phone@x.local:8790", "nickname": "Poco", "online": True,
                 "capabilities": {"apps": True, "screen": True, "tap": True}}]
    st = wa.surface_state(store=store, phones=phone_on)
    check("switches on: can read and send by the phone route, installed still honestly unknown",
          st["whatsapp"]["can_send"] and st["whatsapp"]["send_route"] == "phone"
          and st["whatsapp"]["phones"][0]["installed"] is None, str(st)[:200])
    phone_notap = [{"address": "phone@x.local:8790", "nickname": "Poco", "online": True,
                    "capabilities": {"apps": True, "screen": True, "tap": False}}]
    st = wa.surface_state(store=store, phones=phone_notap)
    check("tap off: can read, cannot send",
          st["whatsapp"]["can_read"] and not st["whatsapp"]["can_send"])
    store.record_observation("phone@x.local:8790", installed=False)
    st = wa.surface_state(store=store, phones=phone_on)
    check("an observed not-installed phone cannot send",
          not st["whatsapp"]["can_send"] and st["whatsapp"]["phones"][0]["installed"] is False)
    check("the desktop route reports itself not built",
          st["desktop"]["built"] is False and st["desktop"]["can_send"] is False)

    print("T the chat tool lane: bare send waits, granted replay sends")
    try:
        from app.agent import tools as agent_tools
        wa._store = wa.WhatsAppStore(TMP / "wa-tool.json")
        fixture = wa.FixtureExecutor()
        real_executor = wa.PhoneExecutor
        wa.PhoneExecutor = lambda store=None: fixture
        try:
            held = await agent_tools._whatsapp_tool({"action": "send", "contact": "Mama", "text": "dinner at 7?"})
            tool_item = wa.default_store().items()[0]
            check("a bare send returns the approval card and sends nothing",
                  held.get("needs_approval") and fixture.calls == []
                  and tool_item["state"] == "awaiting_approval", str(held)[:160])
            refused = await agent_tools._whatsapp_tool(
                {"action": "send", "item_id": tool_item["id"], "_grant": "wrong"})
            check("a replay with the wrong grant is refused and sends nothing",
                  not refused.get("success") and fixture.calls == [], str(refused)[:160])
            done = await agent_tools._whatsapp_tool(
                {"action": "send", "item_id": tool_item["id"], "_grant": tool_item["grant"]})
            check("the granted replay approves and sends exactly once",
                  done.get("success") and done.get("verified")
                  and fixture.calls == [("send_message", tool_item["id"])], str(done)[:200])
        finally:
            wa.PhoneExecutor = real_executor
    except Exception as e:
        check("the chat tool lane runs", False, f"{type(e).__name__}: {e}")

    print("E the phone route end to end over a fake transport")
    from app.services import agent_mail, phone_mcp
    with agent_mail._db() as c:
        c.execute("INSERT OR REPLACE INTO mail_contacts (address, sign_pub, box_pub, safety, trust, first_seen, updated_at) "
                  "VALUES (?,?,?,?,?,?,?)", ("phone@wa.local:8790", "sig", "box", "123 456", "mine", time.time(), time.time()))
    r = phone_mcp.pair_phone("phone@wa.local:8790", "Test phone")
    check("the fake phone pairs", r["success"], str(r))
    phone_mcp.record_hello("phone@wa.local:8790",
                           {"capabilities": {"apps": True, "screen": True, "tap": True}, "battery": 90})
    fake = FakeTransport()
    phone_mcp.transport = fake
    store2 = wa.WhatsAppStore(TMP / "wa-e2e.json")
    prep = store2.prepare("Rene Turcios", SENT_TEXT, actor="test")
    store2.approve(prep["item"]["id"], actor="test")
    out = await wa.run_send(prep["item"]["id"], actor="test",
                            executor=wa.PhoneExecutor(store2), store=store2)
    check("the end-to-end send verifies in the chat", out["success"] and out.get("verified"), str(out)[:240])
    meant = [p for p in fake.sent if p.get("meant")]
    check("meant=true rides exactly one call: the send tap",
          len(meant) == 1 and meant[0]["phone_action"] == "phone_screen_act" and meant[0].get("do") == "tap",
          str(meant)[:160])
    typed = [p for p in fake.sent if p.get("do") == "type" and p.get("text") == SENT_TEXT]
    check("the approved text is typed exactly once, word for word", len(typed) == 1)
    check("the store recorded WhatsApp as installed and signed in from the run",
          (store2.observations().get("phone@wa.local:8790") or {}).get("installed") is True
          and (store2.observations().get("phone@wa.local:8790") or {}).get("signed_in") is True,
          str(store2.observations()))
    got = await wa.read_chat("Rene Turcios", executor=wa.PhoneExecutor(store2), store=store2)
    check("the read path returns the chat's messages", got["success"] and len(got["messages"]) >= 2, str(got)[:200])

    print("M the manifest carries the messaging section")
    from app.services import capability_manifest as cm
    cm._cache.update(manifest=None, at=0.0)
    m = cm.build_manifest()
    check("build_manifest includes messaging with the whatsapp and desktop surfaces",
          "messaging" in m and "whatsapp" in m["messaging"] and "desktop" in m["messaging"])
    text = cm.summary_text(m)
    check("the summary text answers for WhatsApp from the manifest", "WhatsApp:" in text)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f" -- FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
