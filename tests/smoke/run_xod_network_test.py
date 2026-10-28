"""
Your Xods (services/xod_network.py, /api/xods, the xod_machines tool), with a stand-in `tailscale` program and a beacon on
127.0.0.1 playing another Xod:
  - not signed in: status says so, and sign-in opens the link Tailscale prints
  - signed in: the beacon answers only "I'm a Xod" with the machine name, on the address it was given
  - find() lists online devices that answer as Xods, marks other accounts' as shared, counts the rest, and remembers
    a Xod that's offline later
  - a task from another of your Xods only becomes an approval card; Approve puts it in the task inbox, Decline drops it
  - another account's device is refused, and a made-up grant does nothing
  - not findable: the beacon stops
  .venv/bin/python tests/smoke/run_xod_network_test.py
"""
import asyncio
import json
import os
import socket
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-network-"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    os.environ["XOD_BEACON_PORT"] = str(s.getsockname()[1])
sys.path.insert(0, str(ROOT))
results = []

STATE = TMP / "state.json"     # what the stand-in reports; the test rewrites it
FAKE = TMP / "tailscale"
FAKE.write_text(f"""#!{sys.executable}
import sys
a = sys.argv[1:]
if a[:1] == ["status"]:
    print(open({str(STATE)!r}).read())
elif a[:1] == ["whois"]:
    print(open({str(TMP / "who.json")!r}).read())
elif a[:1] == ["login"]:
    print("To authenticate, visit:\\n\\n\\thttps://login.tailscale.com/a/abc123\\n", flush=True)
    import time; time.sleep(30)
""")
FAKE.chmod(FAKE.stat().st_mode | stat.S_IEXEC)


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def state(running=True, peers=()):
    me = {"DNSName": "desk.tail0.ts.net.", "HostName": "desk", "UserID": 1, "TailscaleIPs": ["127.0.0.1", "fd7a::1"]}
    STATE.write_text(json.dumps({"BackendState": "Running" if running else "NeedsLogin", "Self": me if running else {},
                                 "User": {"1": {"LoginName": "someone@example.com"}},
                                 "Peer": {str(i): p for i, p in enumerate(peers)}}))


def peer(host, ip, online=True, user=1, os_="linux"):
    return {"DNSName": f"{host}.tail0.ts.net.", "HostName": host, "UserID": user, "TailscaleIPs": [ip], "Online": online, "OS": os_}


async def main():
    from app.services import xod_network as xn
    from app.services.storage import set_setting
    xn.cli = lambda: str(FAKE)
    opened = []
    xn.webbrowser.open = lambda url: opened.append(url)

    print("N1 not signed in")
    state(running=False)
    st = xn.status()
    check("status says installed, not signed in", st["installed"] and not st["signed_in"], str(st))
    r = await asyncio.to_thread(xn.sign_in)
    check("sign-in opens the link Tailscale prints", r.get("url") == "https://login.tailscale.com/a/abc123" and opened == [r.get("url")], str(r))
    xn._login["proc"].terminate()
    check("no beacon while signed out", await xn.sync_beacon() is False)
    xn.cli = lambda: ""
    check("without Tailscale, sign-in points at the download", (await asyncio.to_thread(xn.sign_in)).get("download") == xn.DOWNLOAD_URL)
    xn.cli = lambda: str(FAKE)

    print("N2 signed in: the beacon")
    state(running=True)
    check("status has the name and account", xn.status()["name"] == "desk" and xn.status()["account"] == "someone@example.com")
    check("the beacon starts on this computer's address", await xn.sync_beacon() is True and xn._beacon["ip"] == "127.0.0.1")
    about = await xn._probe("127.0.0.1")
    check("it answers as a Xod with only its name and OS", about and set(about) == {"service", "version", "machine", "os"}, str(about))

    print("N3 finding the others")
    state(running=True, peers=[peer("laptop", "127.0.0.1"), peer("friend-pc", "127.0.0.1", user=2),
                               peer("phone", "127.0.0.2", os_="iOS"), peer("old-box", "127.0.0.3", online=False)])
    f = await xn.find()
    names = sorted(x["host"] for x in f["xods"])
    check("online Xods are found", names == ["friend-pc", "laptop"], str(f))
    check("…another account's is marked shared", [x["same_account"] for x in f["xods"] if x["host"] == "friend-pc"] == [False])
    check("…devices without Xod are only counted", f["others"] == 2, str(f["others"]))
    state(running=True, peers=[peer("laptop", "127.0.0.1", online=False)])
    f = await xn.find()
    check("a Xod seen before shows as offline later", [(x["host"], x["online"]) for x in f["xods"]] == [("laptop", False)], str(f))

    print("N4 the tool and the API")
    from app.agent import tools
    res = await tools.execute_tool("xod_machines", {"action": "list"})
    check("xod_machines lists them", res.get("status", {}).get("signed_in") is True and "xods" in res, str(res)[:300])
    check("the tool is defined", any(t["function"]["name"] == "xod_machines" for t in tools.TOOL_DEFINITIONS))

    print("N5 handing over a task")
    from app.services.task_queue import task_queue
    from app.services.mcp_client import mcp_hub
    task_queue._kick = lambda: None           # queue it, don't run it
    state(running=True, peers=[peer("laptop", "127.0.0.1")])
    who = TMP / "who.json"
    who.write_text(json.dumps({"Node": {"ComputedName": "intruder"}, "UserProfile": {"ID": 2}}))
    r = await tools.execute_tool("xod_machines", {"action": "send_task", "machine": "laptop", "task": "list my downloads"})
    check("another account's device is refused", r.get("success") is False and "own devices" in r.get("error", ""), str(r))
    check("…and no card was made", not mcp_hub.pending)
    who.write_text(json.dumps({"Node": {"ComputedName": "laptop"}, "UserProfile": {"ID": 1}}))
    before = len(task_queue.list())
    r = await tools.execute_tool("xod_machines", {"action": "send_task", "machine": "laptop", "task": "list my downloads"})
    ref = r.get("ref")
    check("your own device's task waits for your OK", r.get("success") and r.get("status") == "waiting_for_ok" and len(mcp_hub.pending) == 1, str(r))
    check("…nothing is in the task inbox yet", len(task_queue.list()) == before)
    st = await tools.execute_tool("xod_machines", {"action": "task_status", "machine": "laptop", "ref": ref})
    check("its status says waiting for your OK", st.get("status") == "waiting_for_ok", str(st))
    forged = await tools.execute_tool("xod_network_accept", {"ref": ref, "_grant": "made-up"})
    check("a made-up grant does nothing", forged.get("success") is False and len(task_queue.list()) == before)
    card = next(iter(mcp_hub.pending.values()))
    check("the card says who wants what", "on desk" in card["why"] and "list my downloads" in card["why"], card["why"])
    done = await mcp_hub.approve(card["id"])
    check("Approve puts it in the task inbox", done.get("success") and len(task_queue.list()) == before + 1, str(done))
    st = await tools.execute_tool("xod_machines", {"action": "task_status", "machine": "laptop", "ref": ref})
    check("…and the sender sees it queued", st.get("status") == "queued" and st.get("title") == "list my downloads", str(st))
    r2 = await tools.execute_tool("xod_machines", {"action": "send_task", "machine": "laptop", "task": "second one"})
    mcp_hub.decline(next(iter(mcp_hub.pending)))
    st = await tools.execute_tool("xod_machines", {"action": "task_status", "machine": "laptop", "ref": r2.get("ref")})
    check("Decline drops it, and the sender sees that", st.get("status") == "declined" and len(task_queue.list()) == before + 1, str(st))
    who.write_text(json.dumps({"Node": {"ComputedName": "intruder"}, "UserProfile": {"ID": 2}}))
    st = await tools.execute_tool("xod_machines", {"action": "task_status", "machine": "laptop", "ref": ref})
    check("another account can't read it", st.get("success") is False, str(st))

    print("N6 not findable")
    set_setting(xn.FINDABLE_SETTING, "off")
    check("turning findable off stops the beacon", await xn.sync_beacon() is False and await xn._probe("127.0.0.1") is None)


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
