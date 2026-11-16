"""
Computer-control bridge (AGE-85, services/computer_bridge.py + computer_control.py):
the harness can look, screenshot, click, type and press on a computer-control surface.

Xod's vendored browser-ops surface stops at tab registry plus screenshots, and its
desktop tools act only on the local machine when the screen add-on is ready. Tree's
bops surface (look, click by selector or text, type, press, over CDP on a signed-in
browser) is the parity target. This suite proves the first slice end to end through
the same execute_tool the chat loop calls, against the deterministic fake adapter
(no device exists in this sandbox; the bops adapter's real-device run is unproven
and the suite says so):

  C1 the fake adapter: look returns the element map, click by selector lands and is
     recorded, an unknown element fails honestly, type read-back verifies, press
     records the key, and a bops adapter with a missing CLI fails honestly
  C2 registration writes the computer-control server into the MCP config at trust
     ask, as Xod's own python module, and leaves a person's own server untouched
  C3 once the hub connects, the model-facing definitions include all five tools
  C4 with the default trust, a chat-path click is held for approval (nothing runs
     silently); approving it reaches the stub adapter and returns its result
  C5 with trust raised (the person's own setting), look, type and press round-trip
     directly through execute_tool

Run: .venv/bin/python tests/computer_control/run_computer_control_test.py
"""
import asyncio, json, os, sys, tempfile
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_cc_"))
os.environ.update({"XOD_DATA_DIR": str(TMP), "XOD_NO_UPDATE_CHECK": "1",
                   "XOD_MODEL_BUDGET": "off", "XOD_COMPUTER_CONTROL_ADAPTER": "fake"})
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def payload(out):
    if isinstance(out, list) and out:
        out = out[0]
    if not isinstance(out, dict) or not out.get("success"):
        return {}
    data = out.get("data")
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        return data["result"]
    if isinstance(data, dict):
        return data
    try:
        parsed = json.loads(out.get("content") or "{}")
    except ValueError:
        return {}
    if isinstance(parsed, dict) and isinstance(parsed.get("result"), dict):
        return parsed["result"]
    return parsed if isinstance(parsed, dict) else {}


from app.services import computer_bridge, computer_control, mcp_client  # noqa: E402

print("C1 fake adapter, unit level")
fake = computer_control.FakeAdapter()
look = fake.look()
check("look returns the fake page element map",
      look.get("success") is True and any(e.get("selector") == "#save" for e in look.get("elements", []))
      and look.get("fake") is True, str(look)[:200])
shot = fake.screenshot()
check("screenshot is honest about being fake", shot.get("success") is True and shot.get("bytes") == 0)
clicked = fake.click(selector="#save")
check("click by selector lands and is recorded",
      clicked.get("success") is True and clicked.get("clicked", {}).get("index") == 1, str(clicked)[:200])
missed = fake.click(selector="#nope")
check("an unknown element fails honestly, nothing clicked",
      missed.get("success") is False and len(fake.actions) == 1, str(missed)[:200])
typed = fake.type_text(selector="#q", value="hello")
check("type read-back verifies the value",
      typed.get("success") is True and typed.get("value_now") == "hello"
      and typed.get("read_back_verified") is True, str(typed)[:200])
pressed = fake.press(key="Enter")
check("press records the key", pressed.get("success") is True and pressed.get("pressed") == "Enter")
check("press without a key fails honestly", fake.press().get("success") is False)
bops = computer_control.BopsAdapter(command="/nonexistent/bops")
check("a bops adapter with a missing CLI fails honestly",
      bops.look("t1").get("success") is False, str(bops.look("t1"))[:200])
check("adapter selection honors the fake env choice",
      isinstance(computer_control.get_adapter(), computer_control.FakeAdapter))

print("C2 registration")
mcp_client.add_servers({"mcpServers": {"personalsrv": {"command": "true", "args": []}}})
reg = computer_bridge.register_computer_control()
check("registration reports the server", reg.get("registered") == ["computer-control"], str(reg))
cfg = mcp_client.load_config()
spec = cfg.get("computer-control", {})
check("config holds the computer-control server", bool(spec), str(cfg)[:200])
check("server registered at trust ask", spec.get("trust") == "ask", str(spec))
check("server runs Xod's own python module",
      spec.get("command") == "{xod_python}" and spec.get("args") == ["-m", "app.computer_control_mcp"],
      str(spec))
check("a person's own server is untouched", cfg.get("personalsrv", {}).get("command") == "true")


async def main():
    print("C3 hub definitions")
    await mcp_client.mcp_hub.start()
    names = {d["function"]["name"] for d in mcp_client.mcp_hub.tool_definitions()}
    want = {"mcp__computer-control__computer_look", "mcp__computer-control__computer_screenshot",
            "mcp__computer-control__computer_click", "mcp__computer-control__computer_type",
            "mcp__computer-control__computer_press"}
    check("all five computer-control tools are offered to the model", want <= names,
          f"missing {sorted(want - names)}")

    from app.agent.tools import execute_tool

    print("C4 approval hold, then a click round trip")
    held = await execute_tool("mcp__computer-control__computer_click",
                              {"target": "fake-target", "selector": "#save"})
    check("default trust holds the click for approval", held.get("needs_approval") is True,
          str(held)[:200])
    done = await mcp_client.mcp_hub.approve(held.get("approval_id", ""))
    data = payload(done)
    check("the approved click succeeds", done.get("success") is True, str(done)[:200])
    check("the stub adapter result comes back (clicked Save, adapter fake)",
          data.get("clicked", {}).get("selector") == "#save" and data.get("adapter") == "fake",
          str(data)[:200])

    print("C5 direct round trips at raised trust")
    mcp_client.update_server("computer-control", trust="always")
    out = await execute_tool("mcp__computer-control__computer_look", {"target": "fake-target"})
    data = payload(out)
    check("look round-trips with the element map",
          out.get("success") is True and data.get("title") == "Fake Page"
          and any(e.get("selector") == "#q" for e in data.get("elements", [])), str(out)[:200])
    out = await execute_tool("mcp__computer-control__computer_type",
                             {"target": "fake-target", "selector": "#q", "value": "agent"})
    data = payload(out)
    check("type round-trips with verified read-back",
          out.get("success") is True and data.get("value_now") == "agent"
          and data.get("read_back_verified") is True, str(out)[:200])
    out = await execute_tool("mcp__computer-control__computer_press",
                             {"target": "fake-target", "key": "Enter"})
    data = payload(out)
    check("press round-trips", out.get("success") is True and data.get("pressed") == "Enter",
          str(out)[:200])
    out = await execute_tool("mcp__computer-control__computer_screenshot", {"target": "fake-target"})
    data = payload(out)
    check("screenshot round-trips and stays honest",
          out.get("success") is True and data.get("fake") is True, str(out)[:200])
    for conn in list(mcp_client.mcp_hub.conns.values()):
        await conn.stop()


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
