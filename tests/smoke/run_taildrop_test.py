"""
File transfer with Tailscale (services/taildrop.py, the tailscale_files tool), with a stand-in `tailscale` program so
nothing real is sent:
  - devices are read from `file cp --targets`, offline ones marked
  - a send is checked first (file exists, under 4 GB, device known and online) and waits for an approval card
  - only the card's approval sends it; a made-up grant does nothing
  - receive takes waiting files into a folder
  .venv/bin/python tests/smoke/run_taildrop_test.py
"""
import asyncio
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-taildrop-"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []

# The stand-in: prints a fixed device list, records each call, and "receives" one file.
CALLS = TMP / "calls.txt"
FAKE = TMP / "tailscale"
FAKE.write_text(f"""#!{sys.executable}
import sys, pathlib
a = sys.argv[1:]
with open({str(CALLS)!r}, "a") as f:
    f.write(" ".join(a) + "\\n")
if a[:3] == ["file", "cp", "--targets"]:
    print("100.64.0.1\\tphone-one")
    print("100.64.0.2\\tlaptop-two\\toffline; last seen 3h ago")
elif a[:2] == ["file", "get"]:
    pathlib.Path(a[-1], "from-phone.jpg").write_text("x")
""")
FAKE.chmod(FAKE.stat().st_mode | stat.S_IEXEC)


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def calls():
    return CALLS.read_text().splitlines() if CALLS.exists() else []


async def main():
    from app.services import taildrop
    from app.agent import tools
    from app.services.mcp_client import mcp_hub
    taildrop.cli = lambda: str(FAKE)

    print("T1 devices")
    d = taildrop.devices()
    check("both devices are listed", [x["name"] for x in d] == ["phone-one", "laptop-two"], str(d))
    check("…the offline one is marked", d[0]["online"] and not d[1]["online"])

    print("T2 checking a send")
    f = TMP / "report.pdf"
    f.write_bytes(b"%PDF" + b"0" * 2048)
    plan = taildrop.check_send(str(f), "phone-one")
    check("a good send is planned to the device's address", plan["ip"] == "100.64.0.1" and plan["size"] == 2052, str(plan))
    for bad, why in ((str(TMP / "nope.txt"), "missing file"), (str(f), "offline device"), (str(f), "unknown device")):
        dev = {"missing file": "phone-one", "offline device": "laptop-two", "unknown device": "toaster"}[why]
        try:
            taildrop.check_send(bad, dev)
            check(f"{why} is refused", False)
        except taildrop.TaildropError:
            check(f"{why} is refused", True)
    check("checking sends nothing", not any(c.startswith("file cp /") or "100.64" in c for c in calls()), str(calls()))

    print("T3 the tool waits for the person")
    res = await tools._taildrop_tool({"action": "send", "path": str(f), "device": "phone"})
    check("a send comes back as an approval card", res.get("needs_approval") is True and res.get("approval_id"), str(res))
    check("…and nothing was sent yet", not any("100.64.0.1:" in c for c in calls()))
    check("…the card doesn't hand out its grant", "_grant" not in res.get("args", {}))
    forged = await tools._taildrop_tool({"action": "send", "path": str(f), "device": "phone-one", "_grant": "made-up"})
    check("a made-up grant only makes another card", forged.get("needs_approval") is True)
    mcp_hub.pending.pop(forged["approval_id"], None)
    sent = await mcp_hub.approve(res["approval_id"])
    check("approving the card sends it", sent.get("sent") is True and any(c == f"file cp {f.resolve()} 100.64.0.1:" for c in calls()),
          f"{sent} {calls()}")
    check("…and its grant is used up", len(tools._send_grants) == 1, str(tools._send_grants))   # only the forged card's is left

    print("T4 receive and devices through the tool")
    got = await tools._taildrop_tool({"action": "receive", "folder": str(TMP / "inbox")})
    check("waiting files land in the folder", got.get("files") == ["from-phone.jpg"], str(got))
    listed = await tools._taildrop_tool({"action": "devices"})
    check("devices through the tool", listed.get("count") == 2)
    check("the tool is defined", any(t["function"]["name"] == "tailscale_files" for t in tools.TOOL_DEFINITIONS))

    print("T5 a send from the terminal waits too")
    from app.services import sentinel
    sentinel.begin([{"role": "user", "content": "send report.pdf to my phone over tailscale"}])
    cp = "/Applications/Tailscale.app/Contents/MacOS/Tailscale file cp /tmp/report.pdf 100.64.0.1:"
    hit = sentinel.review("computer_run_command", {"command": cp})
    check("`tailscale file cp` in the terminal is held", (hit or {}).get("rule") == "taildrop_send", str(hit))
    check("…on Windows too", (sentinel.review("computer_run_command", {"command": '"C:\\Program Files\\Tailscale\\tailscale.exe" file cp a.txt phone:'})
                              or {}).get("rule") == "taildrop_send")
    check("listing devices isn't held", sentinel.review("computer_run_command", {"command": "tailscale file cp --targets"}) is None)
    check("taking files in isn't held", sentinel.review("computer_run_command", {"command": "tailscale file get ~/Downloads"}) is None)


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
