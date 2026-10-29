"""
Shared memory across your Xods (services/memory_sync.py, user_memory's change log and merge): two computers, "desk"
and "laptop", in one process. Each has its own facts file, change log, sync keys and settings; the beacon and
`tailscale whois` are replaced by direct calls, so nothing touches a network.
  - off by default; asking needs it on at both ends, and an approval card on the other computer
  - refused when the caller isn't on your Tailscale account, isn't signed by the approved keys, or is a reinstall
  - add, edit, conflict (newest wins), one-per-key (the old one goes to the timeline), the same fact said twice
  - forget and forget-everything reach a computer that was offline, and a forgotten fact never comes back
  - secrets are refused on receive; facts kept before sharing was on go over on the first pull
  - nothing echoes back; "Remove what came from it" drops them here only
  - chats from the phone are tagged, and what they teach says it came from the phone
  .venv/bin/python tests/smoke/run_memory_sync_test.py
"""
import asyncio
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-memsync-"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


from app.services import memory_sync, sentinel, storage, user_memory  # noqa: E402
from app.services.mcp_client import mcp_hub  # noqa: E402


class Computer:
    def __init__(self, name, ip):
        self.name, self.ip, self.settings = name, ip, {}
        self.dir = TMP / name
        self.dir.mkdir()
        self.account = "me"


COMPUTERS = {}
_real = {"get": storage.get_setting, "set": storage.set_setting}
_now = {"c": None}


@contextmanager
def on(c):
    """Everything inside runs as computer c: its files, settings, host name and keys."""
    prev = _now["c"]
    _now["c"] = c
    user_memory.PATH = c.dir / "about_you.json"
    user_memory.CHANGES_PATH = c.dir / "about_you_changes.json"
    memory_sync.KEY_PATH = c.dir / "memory_sync_key.json"
    storage.get_setting = lambda k, d=None: c.settings.get(k, d)
    storage.set_setting = lambda k, v: c.settings.__setitem__(k, v)
    user_memory._origin = lambda: c.name
    memory_sync._me = lambda: c.name
    try:
        yield c
    finally:
        _now["c"] = prev
        if prev is not None:
            user_memory.PATH = prev.dir / "about_you.json"
            user_memory.CHANGES_PATH = prev.dir / "about_you_changes.json"
            memory_sync.KEY_PATH = prev.dir / "memory_sync_key.json"
            storage.get_setting = lambda k, d=None: prev.settings.get(k, d)
            storage.set_setting = lambda k, v: prev.settings.__setitem__(k, v)
            user_memory._origin = lambda: prev.name
            memory_sync._me = lambda: prev.name


def caller_by_ip(ip):
    return next(c for c in COMPUTERS.values() if c.ip == ip)


def fake_whois(ip):
    caller, here = caller_by_ip(ip), _now["c"]
    return {"host": caller.name, "same_account": caller.account == here.account}


async def fake_target(machine):
    c = COMPUTERS[machine]
    return {"name": c.name, "host": c.name, "ip": c.ip, "online": True, "same_account": True}


async def fake_call(method, ip, path, body=None):
    """The other computer's beacon, called directly (as that computer), with this one as the caller."""
    me, there = _now["c"], caller_by_ip(ip)
    from app.services import xod_network
    with on(there):
        try:
            return memory_sync.receive_hello(me.ip, body)
        except PermissionError as e:
            raise xod_network.NetworkError(str(e))


async def fake_fetch(x, since):
    me, there = _now["c"], caller_by_ip(x["ip"])
    headers = {k.lower(): v for k, v in memory_sync._signed("changes", str(since)).items()}
    with on(there):
        try:
            return memory_sync.serve_changes(me.ip, headers, since)
        except PermissionError as e:
            raise memory_sync.SyncError(str(e))


def approve_card(c):
    """Press Approve on the newest memory card on computer c (as the chat or phone would)."""
    pid = next(k for k, v in reversed(list(mcp_hub.pending.items())) if v["tool"] == "memory_sync_accept")
    card = mcp_hub.pending.pop(pid)
    with on(c):
        return memory_sync.accept(card["args"]["key"], card["args"]["_grant"])


def texts(c):
    with on(c):
        return sorted(f["text"] for f in user_memory.facts())


async def pull_all(c):
    with on(c):
        await memory_sync.sync_once()


async def main():
    from app.services import xod_network
    xod_network._caller = fake_whois
    xod_network._target = fake_target
    xod_network._call = fake_call
    memory_sync._fetch = fake_fetch
    desk, laptop = Computer("desk", "100.64.0.1"), Computer("laptop", "100.64.0.2")
    COMPUTERS.update(desk=desk, laptop=laptop)

    print("S1 off by default, and both ends agree")
    with on(desk):
        check("shared memory is off for a new user", not memory_sync.enabled() and memory_sync.status()["on"] is False)
        user_memory.add("Name is Sam", "identity", "name")
        user_memory.add("Works on a robotics startup", "work")
        try:
            await memory_sync.share_with("laptop")
            check("asking to share while it's off here is refused", False)
        except memory_sync.SyncError:
            check("asking to share while it's off here is refused", True)
        memory_sync.set_enabled(True)
        try:
            await memory_sync.share_with("laptop")
            check("…and while it's off on the other computer", False)
        except memory_sync.SyncError as e:
            check("…and while it's off on the other computer", "off on laptop" in str(e), str(e))
    with on(laptop):
        memory_sync.set_enabled(True)
        user_memory.add("Prefers short answers", "preferences")
    with on(desk):
        r = await memory_sync.share_with("laptop")
        check("asking makes an approval card on the other computer, nothing is shared yet",
              r["state"] == "waiting" and any(v["tool"] == "memory_sync_accept" for v in mcp_hub.pending.values()), str(r))
    await pull_all(desk)
    check("before the card is approved, nothing comes over", texts(desk) == ["Name is Sam", "Works on a robotics startup"],
          str(texts(desk)))
    check("the card says facts only, and that forgets reach both",
          any("facts only" in v["description"] and "forgets it on both" in v["description"] for v in mcp_hub.pending.values()))
    r = approve_card(laptop)
    check("approving the card starts sharing there", r["success"], str(r))
    with on(laptop):
        bad = memory_sync.accept(next(iter(memory_sync.peers())), "made-up-grant")
    check("the card's approve can't be faked without its grant", not bad["success"])

    print("S2 facts move both ways, including the ones kept before sharing was on")
    await pull_all(desk)
    check("desk got laptop's facts (a first pull is a full copy)", "Prefers short answers" in texts(desk), str(texts(desk)))
    with on(laptop):
        await memory_sync.share_with("desk")    # laptop asks back: desk already shares with it, so no new card
    await pull_all(laptop)
    check("laptop got desk's facts", {"Name is Sam", "Works on a robotics startup"} <= set(texts(laptop)), str(texts(laptop)))
    with on(laptop):
        f = next(f for f in user_memory.facts() if f["text"] == "Name is Sam")
    check("a fact from another computer says where it came from", f["source"] == "you said, on desk"
          and f["received_from"] == "desk", str(f))
    await pull_all(desk)
    with on(desk):
        n = len(user_memory.facts())
    check("nothing echoes back (desk doesn't get its own facts again)", n == 3, str(texts(desk)))

    print("S3 edits, conflicts, one-per-key and the same fact twice")
    with on(desk):
        fid = next(f["id"] for f in user_memory.facts() if f["text"] == "Works on a robotics startup")
        user_memory.edit(fid, "Works on a drone startup")
    await pull_all(laptop)
    check("an edit reaches the other computer", "Works on a drone startup" in texts(laptop)
          and "Works on a robotics startup" not in texts(laptop), str(texts(laptop)))
    with on(laptop):
        user_memory.add("Name is Samuel", "identity", "name")
    await pull_all(desk)
    with on(desk):
        names = [f["text"] for f in user_memory.facts() if f.get("key") == "name"]
        past = [p["text"] for p in user_memory.load()["past"]]
    check("one-per-key: the newer name wins and the old one goes on the timeline",
          names == ["Name is Samuel"] and "Name is Sam" in past, f"{names} {past}")
    with on(desk):
        user_memory.add("Has a dog called Biscuit", "people")
    with on(laptop):
        user_memory.add("Has a dog called Biscuit", "people")
    await pull_all(desk)
    await pull_all(laptop)
    dogs_d = [t for t in texts(desk) if "Biscuit" in t]
    dogs_l = [t for t in texts(laptop) if "Biscuit" in t]
    with on(desk):
        id_d = next(f["id"] for f in user_memory.facts() if "Biscuit" in f["text"])
    with on(laptop):
        id_l = next(f["id"] for f in user_memory.facts() if "Biscuit" in f["text"])
    check("the same fact said on both is kept once, with one id on both", len(dogs_d) == 1 and len(dogs_l) == 1
          and id_d == id_l, f"{dogs_d} {dogs_l} {id_d} {id_l}")

    print("S4 forgets reach a computer that was offline, and never come back")
    with on(desk):
        user_memory.forget_matching("dog Biscuit")
    await pull_all(laptop)   # laptop was offline during the forget; it catches up now
    check("a forget on one computer forgets it on the other", not any("Biscuit" in t for t in texts(laptop)), str(texts(laptop)))
    with on(desk):           # laptop sends everything again (a full copy), including its older put of the dog
        for k, v in memory_sync.peers().items():
            if v["host"] == "laptop":
                memory_sync._update_peer(k, cursor=0)
    await pull_all(desk)
    check("…and an older copy from the other computer doesn't bring it back", not any("Biscuit" in t for t in texts(desk)),
          str(texts(desk)))
    with on(laptop):
        user_memory.forget_all()
    await pull_all(desk)
    check("forget everything reaches the other computer", texts(desk) == [], str(texts(desk)))
    with on(desk):
        user_memory.add("Lives in Lisbon", "location", "city")
    await pull_all(laptop)
    check("…and facts said after it still go over", texts(laptop) == ["Lives in Lisbon"], str(texts(laptop)))

    print("S5 what's refused")
    with on(desk):
        res = user_memory.merge([{"op": "put", "fact": {"id": "f0123456789", "text": "My password is hunter2", "topic": "other",
                                                         "updated": 1}}], "laptop")
    check("a secret is refused on receive", res["skipped"] == 1 and "My password is hunter2" not in texts(desk), str(res))
    stranger = Computer("stranger", "100.64.0.9")
    stranger.account = "someone-else"
    COMPUTERS["stranger"] = stranger
    with on(stranger):
        memory_sync.set_enabled(True)
        try:
            await memory_sync.share_with("desk")
            check("a computer on someone else's account can't ask", False)
        except memory_sync.SyncError as e:
            check("a computer on someone else's account can't ask", "your own computers" in str(e), str(e))
    with on(desk):
        laptop_key = next(k for k, v in memory_sync.peers().items() if v["host"] == "laptop")
    with on(stranger):
        x = {"ip": desk.ip, "sign_pub": laptop_key, "cursor": 0}
        try:
            await memory_sync._fetch(x, 0)
            check("a request not signed by the approved keys is refused", False)
        except memory_sync.SyncError as e:
            check("a request not signed by the approved keys is refused", "your own computers" in str(e) or "signed" in str(e), str(e))
    (laptop.dir / "memory_sync_key.json").unlink()    # laptop reinstalled: new keys
    await pull_all(laptop)
    with on(laptop):
        err = next(v for v in memory_sync.peers().values() if v["host"] == "desk").get("error") or ""
    check("after a reinstall (new keys) the other computer stops sharing until it's approved again",
          "keys changed" in err or "isn't sharing" in err, err)

    print("S6 stop, remove, and switching off")
    with on(desk):
        n = memory_sync.remove_received(laptop_key)
        memory_sync.stop_sharing(laptop_key)
        gone = laptop_key not in memory_sync.peers()
    check("stop sharing forgets the pairing here", gone)
    with on(laptop):
        user_memory.add("Remove me later", "other")
        key = next(k for k, v in memory_sync.peers().items() if v["host"] == "desk")
        memory_sync.set_enabled(False)
    await pull_all(laptop)
    check("switched off: nothing more is taken", "Remove me later" not in texts(desk))

    print("S7 phone chats are tagged, and what they teach says so")
    with on(desk):
        s = storage.create_session("From the phone", source="phone")
        d = storage.create_session("At the desk")
        rows = {x["id"]: x.get("source") for x in storage.list_sessions()}
        check("a chat started from the phone is stored as source phone; others as desktop",
              rows.get(s["id"]) == "phone" and rows.get(d["id"]) == "desktop", str(rows))
        user_memory.VIA.set("phone")
        f = user_memory.add("Training for a half marathon", "routines",
                            source="you said, from your phone" if user_memory.VIA.get() == "phone" else "you said")
        user_memory.VIA.set("")
    check("a fact learned from a phone chat says it came from your phone", f["via"] == "phone"
          and "phone" in f["source"], str(f))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
