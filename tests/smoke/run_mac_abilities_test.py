"""
Everyday abilities on this Mac (app/connectors/mac.py, services/abilities.py): Messages, Contacts, places nearby, Home
(Shortcuts), and the Abilities screen. Sealed: AppleScript, the Messages database, Shortcuts, Apple Maps and System
Settings are all stand-ins, so nothing real is read, sent, run, opened or asked:
  - Contacts: everyone read in one go; found by name, company, number or email; added with values passed as arguments
  - Messages: latest conversations with names from Contacts and unread counts, newer messages' text (attributedBody);
    a conversation by chat id, number or name; sending to a number, a name, a group chat; two people with the name:
    asks which; sending always waits for your OK (and the card doesn't say "deleted"); no Full Disk Access: says how
  - places: sorted by distance, with phone, website and a Maps link; near a place you name; Location off: says how
  - Home: your shortcuts listed and run; one that unlocks or opens something refuses and points to the one that
    always asks; an unknown name lists what there is
  - the Abilities screen: where each stands, never prompting; Allow switches This Mac on and asks macOS once;
    Messages without Full Disk Access opens that page; Location off opens its page
  - the chat finds these tools for everyday words, and the quick router hands such requests to the planner
  .venv/bin/python tests/smoke/run_mac_abilities_test.py   (Mac only)
"""
import asyncio
import os
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-macab-"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


S, R, U = "\x1f", "\x1e", "\x1d"
PEOPLE_OUT = (f"p1{S}Sam Rivera{S}Acme{S}+1 (415) 555-0101{U}{S}sam@example.com{U}{R}"
              f"p2{S}Mom{S}{S}415-555-0199{U}{S}{R}"
              f"p3{S}Alex Kim{S}{S}+14155550123{U}{S}alex@example.com{U}{R}"
              f"p4{S}Alex Moreno{S}{S}+14155550124{U}{S}{R}")


def blob(text: str) -> bytes:
    """An attributedBody as newer macOS writes it: the text after NSString, '+' and its length."""
    b = text.encode()
    head = bytes([0x81]) + len(b).to_bytes(2, "little") if len(b) > 127 else bytes([len(b)])
    return b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + head + b + b"\x86\x84"


def make_chat_db(path: Path) -> None:
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT, display_name TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, date INTEGER, is_from_me INTEGER,
                              is_read INTEGER, handle_id INTEGER);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        INSERT INTO handle VALUES (1, '+14155550199'), (2, '+14155550101'), (3, '+19995550000');
        INSERT INTO chat VALUES (1, 'iMessage;-;+14155550199', '+14155550199', ''), (2, 'iMessage;-;+14155550101', '+14155550101', ''),
                                (3, 'iMessage;+;chat777', 'chat777', 'Weekend plans'), (4, 'SMS;-;+19995550000', '+19995550000', '');
    """)
    ns = lambda s: int(s * 1e9)
    rows = [(1, "Did you eat?", None, ns(800000000), 0, 1, 1, 1), (2, None, blob("Call me when you land"), ns(800000100), 0, 0, 1, 1),
            (3, "On my way", None, ns(800000050), 1, 1, 2, 2), (4, "Saturday?", None, ns(800000200), 0, 0, 3, 3),
            (5, None, blob("x" * 200), ns(800000010), 0, 1, 3, 4)]
    for rid, text, b, date, mine, read, h, chat in rows:
        c.execute("INSERT INTO message VALUES (?,?,?,?,?,?,?)", (rid, text, b, date, mine, read, h))
        c.execute("INSERT INTO chat_message_join VALUES (?,?)", (chat, rid))
    c.commit()
    c.close()


async def main():
    if sys.platform != "darwin":
        print("Mac only")
        return
    from app.connectors import mac as m
    sent = []

    def fake_osa(script, *args, timeout=60):
        sent.append((script, args))
        if script is m.PEOPLE:
            return PEOPLE_OUT
        if script is m.CONTACT_ADD:
            return f"{args[0]} {args[1]}".strip()
        if script is m.SEND_TO:
            return "iMessage"
        return ""
    m._osa = fake_osa

    print("M1 Contacts")
    found = m.contacts_find("sam")
    check("found by name, with phones and emails (everyone read in one go)", [p["name"] for p in found] == ["Sam Rivera"]
          and found[0]["phones"] == ["+1 (415) 555-0101"] and found[0]["emails"] == ["sam@example.com"], str(found))
    check("…by company, by part of a number, by email", m.contacts_find("acme")[0]["name"] == "Sam Rivera"
          and m.contacts_find("555-0199")[0]["name"] == "Mom" and m.contacts_find("alex@example")[0]["name"] == "Alex Kim")
    n = len(sent)
    m.contacts_find("alex")
    check("…kept for a while (Contacts isn't read again for each search)", len(sent) == n)
    r = m.contacts_add("Jordan", "Lee", phone="555-0134", email='j"; do shell script "x', company="Co")
    s_, args = sent[-1]
    check("adding a contact: the values go to AppleScript as arguments, never into the script",
          r == "Added Jordan Lee to Contacts." and args[:3] == ("Jordan", "Lee", "555-0134") and "do shell script" not in s_, str(args))

    print("M2 Messages")
    m.CHAT_DB = TMP / "nothing.db"
    try:
        m.messages_recent()
        ok = False
    except RuntimeError as e:
        ok = "Full Disk Access" in str(e) and "Sending works without it" in str(e)
    check("no Full Disk Access: says so, and how, and that sending still works", ok)
    m.CHAT_DB = TMP / "chat.db"
    make_chat_db(m.CHAT_DB)
    rec = m.messages_recent()
    check("latest conversations first, with names from Contacts", [x["with"] for x in rec] == ["Weekend plans", "Mom", "Sam Rivera", "+19995550000"], str(rec))
    check("…the last message, also when only attributedBody has it (newer macOS)", rec[1]["last"] == "Call me when you land", rec[1]["last"])
    check("…and how many are unread", rec[1]["unread"] == 1 and rec[2]["unread"] == 0 and rec[0]["unread"] == 1)
    check("…your own last message says it's yours", rec[2]["last"] == "You: On my way")
    check("unread only", [x["with"] for x in m.messages_recent(unread_only=True)] == ["Weekend plans", "Mom"])
    conv = m.messages_read("Mom")
    check("a conversation by name: oldest first, who said it", [(x["from"], x["text"]) for x in conv] ==
          [("Mom", "Did you eat?"), ("Mom", "Call me when you land")], str(conv))
    check("…by number and by chat id, the same", m.messages_read("+1 415 555 0199") == conv and m.messages_read("iMessage;-;+14155550199") == conv)
    check("…a long message read whole from attributedBody", m.messages_read("SMS;-;+19995550000")[0]["text"] == "x" * 200)
    try:
        m.messages_read("Nobody Here")
        ok = False
    except ValueError:
        ok = True
    check("…someone you've never texted: says so", ok)
    r = m.messages_send("+1 415 555 0101", "running late")
    check("sending to a number: Messages gets the number and the text as arguments", r.startswith("Sent to +1 415 555 0101 by iMessage")
          and sent[-1][0] is m.SEND_TO and sent[-1][1] == ("+1 415 555 0101", "running late"))
    r = m.messages_send("Sam", "hi")
    check("…to a name from Contacts: their number", sent[-1][1] == ("+1 (415) 555-0101", "hi") and "Sam Rivera" in r, r)
    try:
        m.messages_send("Alex", "hi")
        ok = False
    except ValueError as e:
        ok = "Alex Kim" in str(e) and "Alex Moreno" in str(e)
    check("…two people with that name: asks which, sends nothing", ok and sent[-1][1] != ("+14155550123", "hi"))
    m.messages_send("iMessage;+;chat777", "Saturday works")
    check("…to a group chat by its id", sent[-1][0] is m.SEND_CHAT and sent[-1][1] == ("iMessage;+;chat777", "Saturday works"))
    tools = {t.name: t for t in await m.server.list_tools()}
    from app.services import mcp_client
    check("sending a message always waits for your OK (whatever the trust setting)", mcp_client._destructive(tools["messages_send"])
          and mcp_client._read_only(tools["messages_recent"]) and mcp_client._read_only(tools["messages_read"]))

    print("M3 places nearby")
    import CoreLocation

    class Mark:
        def __init__(self, title, lat, lon):
            self._t, self._c = title, CoreLocation.CLLocationCoordinate2DMake(lat, lon)

        def title(self):
            return self._t

        def coordinate(self):
            return self._c

    class Item:
        def __init__(self, name, title, lat, lon, phone="", url=""):
            self._n, self._pm, self._ph, self._u = name, Mark(title, lat, lon), phone, url

        def name(self):
            return self._n

        def placemark(self):
            return self._pm

        def phoneNumber(self):
            return self._ph

        def url(self):
            return self._u
    searched = []

    def fake_search(q, center=None, radius_m=3000, limit=8):
        searched.append((q, center is not None, radius_m))
        if q == "Austin, TX":
            return [Item("Austin", "Austin, TX", 30.2672, -97.7431)]
        if q == "Nowhereville":
            return []
        return [Item("Far Cafe", "9 Far St", 30.30, -97.70, "+1 512 555 0001", "https://far.example"),
                Item("Near Cafe", "1 Near St", 30.268, -97.744, "+1 512 555 0002")]
    m._search = fake_search
    m._here = lambda: {"lat": 30.2672, "lon": -97.7431, "place": "Austin, TX, United States"}
    out = await m.places_nearby("coffee")
    check("places around you, nearest first, with address, distance, phone, website and a Maps link",
          [x["name"] for x in out] == ["Near Cafe", "Far Cafe"] and out[0]["km"] < out[1]["km"] and out[1]["website"] == "https://far.example"
          and out[0]["maps"].startswith("https://maps.apple.com/?q=Near%20Cafe") and out[0]["near"].startswith("Austin"), str(out)[:300])
    m._here = lambda: (_ for _ in ()).throw(RuntimeError("Location Services is off for Xod. Turn it on in System Settings"))
    out = await m.places_nearby("coffee", near="Austin, TX", radius_km=1)
    check("…near a place you name (no location needed)", out and out[0]["near"] == "Austin, TX" and searched[-1] == ("coffee", True, 1000))
    try:
        await m.places_nearby("coffee")
        ok = False
    except RuntimeError as e:
        ok = "Location Services is off" in str(e)
    check("Location off: says how to turn it on", ok)
    try:
        await m.places_nearby("coffee", near="Nowhereville")
        ok = False
    except ValueError:
        ok = True
    check("a place Apple Maps doesn't know: says so", ok)
    out = await m.places_nearby(query="coffee shops near Austin, TX")
    check("a whole request sent as `query` (small models do) still finds them, near the place it names",
          out and out[0]["near"] == "Austin, TX" and searched[-1][0] == "coffee shops", str(searched[-1]))
    out = await m.places_nearby(query="coffee shop", location="Austin, TX")
    check("…and the place sent as `location` (another name models use) counts as `near`",
          out and out[0]["near"] == "Austin, TX" and searched[-1][0] == "coffee shop", str(searched[-1]))
    import inspect
    check("the location tools run on the main thread, where Apple Maps and Location Services answer",
          inspect.iscoroutinefunction(m.places_nearby) and inspect.iscoroutinefunction(m.location_here))
    try:
        await m.server.call_tool("places_nearby", {"arg_name": "query"})
        said = ""
    except Exception as e:
        said = str(e)
    check("what went wrong reaches the model, not just \"Error executing tool\"", "Say what to look for" in said, said)

    print("M4 Home, through your Shortcuts")
    fake = TMP / "shortcuts"
    log = TMP / "shortcuts.log"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nif [ "$1" = list ]; then printf "Lights off\\nMovie time\\nUnlock front door\\nOpen garage\\n"; fi\n'
                    'if [ "$1" = run ]; then echo "done: $2"; fi\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    m.SHORTCUTS = str(fake)
    check("your shortcuts, by name", m.shortcuts_list() == ["Lights off", "Movie time", "Unlock front door", "Open garage"])
    check("…found by a word", m.shortcuts_list("lights") == ["Lights off"])
    r = m.shortcuts_run("Lights off")
    check("running one: lights off", "Ran “Lights off”" in r and "run Lights off" in log.read_text(), r)
    for name in ("Unlock front door", "Open garage"):
        try:
            m.shortcuts_run(name)
            ok = False
        except ValueError as e:
            ok = "home_security_shortcut" in str(e)
        check(f"…one that unlocks or opens something ({name}) refuses, pointing to the one that always asks", ok
              and f"run {name}" not in log.read_text())
    try:
        m.shortcuts_run("Lights on")
        ok = False
    except ValueError as e:
        ok = "Lights off" in str(e)
    check("an unknown name: lists the shortcuts there are", ok)
    check("unlocking always waits for your OK", mcp_client._destructive(tools["home_security_shortcut"])
          and not mcp_client._destructive(tools["shortcuts_run"]))
    r = m.home_security_shortcut("Unlock front door")
    check("…and once you've said OK, it runs", "run Unlock front door" in log.read_text())

    print("M5 the approval card")
    from types import SimpleNamespace
    hub = mcp_client.MCPHub()
    hub.conns["mac"] = SimpleNamespace(name="mac", client=True, tools=[tools["messages_send"]])
    r = await hub.call("mcp__mac__messages_send", {"to": "Sam", "text": "hi"})
    check("the card says what will happen, not \"deleted\"", r.get("needs_approval") and "deleted" not in r["message"]
          and "send messages with This Mac" in r["message"], r.get("message"))
    first = r
    r = await hub.call("mcp__mac__messages_send", {"to": "Sam", "text": "hi", "approval_id": first["approval_id"]})
    check("the same call again (even with the approval id copied in) is the card already waiting, not a second one",
          r.get("approval_id") == first["approval_id"] and len(hub.pending) == 1, str(r)[:200])
    waiting = len(hub.pending)
    r = await hub.call("mcp__mac__messages_send", {"someone": "Sam", "text": "hi"})
    check("a call missing what the tool needs goes back to the model to fix, never to you to approve",
          not r.get("needs_approval") and "`to`" in (r.get("error") or "") and "`someone`" in r["error"] and len(hub.pending) == waiting, str(r)[:200])

    print("M6 the Abilities screen")
    from app.services import abilities as ab
    opened, ran = [], []
    ab._open = lambda url: opened.append(url)
    ab.automation = lambda bundle: {"com.apple.iCal": "allowed", "com.apple.reminders": "ask", "com.apple.AddressBook": "denied",
                                    "com.apple.MobileSMS": "allowed"}[bundle]
    ab.messages_readable = lambda: False
    ab.location_status = lambda: "ask"
    ab.shortcut_names = lambda: ["Lights off"]
    on = {"v": False}
    ab._connector_on = lambda: on["v"]
    st = {a["id"]: a for a in ab.status()["abilities"]}
    check("six abilities, like a phone's: Messages, Location, Calendar, Home, Reminders, Contacts",
          list(st) == ["messages", "location", "calendar", "home", "reminders", "contacts"])
    check("This Mac off: even an app macOS allowed shows one click away", st["calendar"]["state"] == "ask")
    on["v"] = True
    st = {a["id"]: a for a in ab.status()["abilities"]}
    check("on: allowed, asked-for and denied as macOS has them", st["calendar"]["state"] == "allowed" and st["reminders"]["state"] == "ask"
          and st["contacts"]["state"] == "denied")
    check("…Messages: sending allowed, reading needs Full Disk Access", st["messages"]["state"] == "partly" and "Full Disk Access" in st["messages"]["note"])
    check("…Home: how many shortcuts, and how Home control works", "1 shortcut" in st["home"]["note"])
    check("checking never made macOS ask anything, or opened anything", not opened)

    class Run:
        def __init__(self, code, err=""):
            self.returncode, self.stderr, self.stdout = code, err, "3"

    class FakeSub:
        @staticmethod
        def run(argv, **kw):
            ran.append(argv)
            return Run(0)
        Popen = None
    ab.subprocess = FakeSub
    r = await ab.allow("reminders")
    check("Allow: macOS asks its own question once (a harmless read), and it's remembered",
          r["success"] and ran[-1][-1] == 'tell application "Reminders" to count lists' and ab._remembered().get("reminders") == "allowed")
    r = await ab.allow("contacts")
    check("…told no before: opens the Automation page instead of asking again", not r["success"] and "Privacy_Automation" in opened[-1])
    r = await ab.allow("messages")
    check("Messages: sending allowed, then the Full Disk Access page opens for reading", r["success"] and r["state"] == "partly"
          and "Privacy_AllFiles" in opened[-1])
    ab._ask_location = lambda: "denied"
    r = await ab.allow("location")
    check("Location refused: opens its page, and says Xod can still search near a place you name", not r["success"]
          and "Privacy_LocationServices" in opened[-1] and "place you name" in r["error"])
    on["v"] = False
    connected = []

    async def fake_connect(cid, redirect, **kw):
        connected.append(cid)
        on["v"] = True
        return {"success": True}
    from app.services import connectors
    connectors.connect = fake_connect
    r = await ab.allow("home")
    check("Allow switches This Mac on first", connected == ["mac"] and r["success"])

    print("M7 the chat finds them")
    defs = [{"type": "function", "function": {"name": f"mcp__mac__{t.name}", "description": f"[mac] {t.description}", "parameters": {}}}
            for t in tools.values()]
    hub2 = mcp_client.mcp_hub
    saved = hub2.tool_definitions
    hub2.tool_definitions = lambda: defs
    try:
        def top(q):
            return [d["function"]["name"].split("__")[-1] for d in hub2.tools_for_query(q, limit=4)]
        for q, want in (("text mom that I'm on my way", "messages_send"), ("turn off the living room lights", "shortcuts_run"),
                        ("find a coffee shop near me", "places_nearby"), ("what's Alex's phone number", "contacts_find"),
                        ("remind me to call the dentist", "reminders_add"), ("unlock the garage", "home_security_shortcut")):
            check(f"\"{q}\" → {want} among the first", want in top(q), str(top(q)))
    finally:
        hub2.tool_definitions = saved
    from app.agent.researcher import AgenticResearcher
    a = AgenticResearcher()
    check("the quick router hands these to the planner (it searched the web for \"did anyone text me today\")",
          not any(a._fast_lane_ok(q, False, False, None) for q in ("did anyone text me today?", "where am I right now?",
                  "any pharmacies near downtown Austin?", "turn off the lights")) and a._fast_lane_ok("how tall is everest?", False, False, None))

    print("M8 the screen's API")
    import httpx
    from app.main import app as xod_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=xod_app), base_url="http://127.0.0.1:8000") as cl:
        r = (await cl.get("/api/abilities")).json()
        check("the Abilities screen's API lists the six", r["supported"] and len(r["abilities"]) == 6)
        r = (await cl.post("/api/abilities/nonsense/allow")).json()
        check("…an unknown one says so", r["success"] is False)


if __name__ == "__main__":
    asyncio.run(main())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)
