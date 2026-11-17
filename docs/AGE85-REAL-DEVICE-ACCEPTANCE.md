# Real-device computer-control acceptance

**Current native acceptance: UNKNOWN on macOS and Windows.** This procedure
has not been run on either physical OS in Replit. Linux fixtures, stdio MCP
connection, a CLI exit code and `dispatched: true` are not native acceptance.
Do not replace UNKNOWN with PASS until the corresponding observations below
are saved from an actual device. No model key or live model call is needed.

## Prerequisites and safety

- A real Mac, a checked-out Xod revision, Python 3.12, installed pinned
  `requirements.txt`, and Google Chrome at its normal Applications path.
  Verify `python3 -c 'import websocket, psutil; print(websocket.__version__, psutil.__version__)'`.
  `websocket-client==1.9.0` is a direct dependency because screenshots,
  the window guard and the legacy live fixture import `websocket`.
  The separate `websockets` distribution does not supply that module.
- Use a disposable Chrome profile and fixture only. Never expose CDP beyond
  loopback, use a personal signed-in profile, or open third-party websites
  while accepting this change. Anyone with CDP access controls that profile.
- Save/close personal TextEdit work before testing its allowlisted launch.
  Do not grant global auto-approval or change Muse/Partner policy. Browser
  control approval is a separate gate, not Muse's site/recipient Always Allow.
- For installed Xod screen/Partner checks, grant Accessibility/Screen Recording
  only through macOS to the installed app. A CDP pass does not prove those
  permissions, Electron signing or the native Partner watcher.
- Run from the repository root in one Terminal shell. Generated evidence is
  local and excluded from Git. Review it for private paths before sharing.
  Use bash and enable `set -o pipefail` so `tee` cannot hide a failed command.

## 1. Start a disposable real browser

```sh
export XOD_DEVICE_EVIDENCE="$PWD/generated-artifacts/native-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$XOD_DEVICE_EVIDENCE"
export BOPS_CONFIG="$XOD_DEVICE_EVIDENCE/device.json"
export XOD_COMPUTER_CONTROL_ADAPTER=bops
export XOD_COMPUTER_CONTROL_BOPS="$XOD_DEVICE_EVIDENCE/bops-local"
python3 - <<'PY'
import json, os, pathlib, sys
root = pathlib.Path.cwd()
out = pathlib.Path(os.environ["XOD_DEVICE_EVIDENCE"])
(out / "device.json").write_text(json.dumps({
    "cdp_host": "127.0.0.1", "cdp_port": 9223, "last_tab_guard": True,
    "registry_path": str(out / "registry.json"),
    "screenshots_dir": str(out / "screenshots"),
    "sweep_log_path": str(out / "sweep.log"),
    "allowed_apps": {"editor": {"bundle_id": "com.apple.TextEdit"}}
}, indent=2))
script = root / "app/vendor/browser_ops/src/browser_ops.py"
wrapper = pathlib.Path(os.environ["XOD_COMPUTER_CONTROL_BOPS"])
wrapper.write_text("#!"+sys.executable+"\nimport os,runpy,sys\n"
                  "os.environ['BOPS_CONFIG']="+repr(str(out / "device.json"))+"\n"
                  "sys.path.insert(0,"+repr(str(script.parent))+")\n"
                  "runpy.run_path("+repr(str(script))+",run_name='__main__')\n")
wrapper.chmod(0o700)
PY
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir="$XOD_DEVICE_EVIDENCE/chrome-profile" \
  --remote-debugging-address=127.0.0.1 --remote-debugging-port=9223 \
  --no-first-run --no-default-browser-check --window-size=1000,900 \
  "file://$PWD/tests/fixtures/computer_control_acceptance.html" \
  >"$XOD_DEVICE_EVIDENCE/chrome.log" 2>&1 &
export XOD_ACCEPTANCE_CHROME_PID=$!
sleep 3
"$XOD_COMPUTER_CONTROL_BOPS" status | tee "$XOD_DEVICE_EVIDENCE/status.json"
python3 - <<'PY'
import json, os, pathlib, urllib.request
with urllib.request.urlopen("http://127.0.0.1:9223/json/list", timeout=5) as r:
    tabs=json.load(r)
targets=[t["id"] for t in tabs if t.get("type")=="page"
         and t.get("title")=="Xod disposable native acceptance"]
assert len(targets)==1, "Expected exactly one disposable target, stop instead of guessing"
(pathlib.Path(os.environ["XOD_DEVICE_EVIDENCE"])/"target.txt").write_text(targets[0])
PY
export TARGET="$(cat "$XOD_DEVICE_EVIDENCE/target.txt")"
```

Expected status: `ok: true`, browser/version, `control_protocol:
"xod-control-v1"` and at least one page. `native_verified: false` and
`verification: "UNKNOWN"` remain correct: this reports connectivity only.
If Chrome failed, the port is occupied, the title is wrong or CDP is
unreachable, stop. Do not choose another person's tab.

## 2. Exercise the actual adapter and all nine MCP tools

Run the following with the variables above still set. It calls the real MCP
tool functions, not a fake adapter. This is the **device layer**: calling a
tool function directly does not demonstrate the app's approval UI. Step 3
tests that separately. There are no automatic action retries.

```sh
python3 - <<'PY' | tee "$XOD_DEVICE_EVIDENCE/tools.jsonl"
import asyncio, json, os
from app import computer_control_mcp as m
t=os.environ["TARGET"]
async def call(name, **args):
    result=await getattr(m, name)(**args)
    print(json.dumps({"tool":name,"result":result}), flush=True)
    assert result.get("success") is True and result.get("fake") is not True
async def main():
    await call("computer_look", target=t)
    await call("computer_type", target=t, selector="#field", value="Xod native 123", clear=True)
    await call("computer_press", target=t, key="Enter")
    await call("computer_click", target=t, selector="#button")
    await call("computer_hover", target=t, selector="#panel")
    await call("computer_drag", target=t, start_x=30, start_y=240, end_x=130, end_y=300, steps=8)
    await call("computer_look", target=t)
    await call("computer_screenshot", target=t)
    await call("computer_scroll", target=t, delta_y=400)
    await asyncio.sleep(.5)
    await call("computer_look", target=t)
    await call("computer_screenshot", target=t)
    await call("computer_open_app", application="editor")
asyncio.run(main())
PY
```

Observe the actual Mac screen as well as the returned data. Save screenshots
or a recording manually, without personal windows visible. A Python assertion
only confirms driver success, not the visual checklist:

| Tool | Required observed effect |
| --- | --- |
| look | Correct disposable title, URL, visible field/button/panel; no click count change |
| type | Field visibly equals `Xod native 123`; returned field read-back agrees |
| press | Fixture `keys` increases with the field focused; no duplicate submission |
| click | `clicks` changes from 0 to exactly 1 |
| hover | `hovers` increases on entering the panel |
| drag | Blue marker moves and `lastDrag` records `[30,240,130,300]`, mouse released |
| screenshot | Open the returned PNG on this Mac; actual fixture pixels, not a fake path or stale image |
| scroll | Browser visibly moves; fixed status reports positive `scrollY` |
| open_app | TextEdit becomes visible on this Mac; `launch_requested: true` alone is not sufficient |

The tool response deliberately retains `native_verified: false`.
`read_back_verified` is scoped evidence for one field. Record manual native
acceptance separately; never promote arbitrary driver data into acceptance.
Desktop-wide `computer_screenshot()` uses the Windows scheduled-task path and
is **not supported/accepted on Mac** by this CDP driver. Its expected Mac
result is failure, not a made-up screenshot. Do not conflate tab capture with
desktop capture. Native screen capture/Partner acceptance remains separate.

## 3. Verify approval and missing-bridge behavior in Xod

This exact command exercises the chat's `execute_tool` path and actual stdio
MCP server with isolated settings, without an LLM or stored provider key.
It asks in Terminal before approving the real look and click. Watch the
fixture: no device effect before approval, exactly one additional click after
approval. Requests in the final gate checks are deliberately never approved.
Before running it, manually scroll the disposable fixture back to the top so
the field and button are visible again.

```sh
python3 - <<'PY' | tee "$XOD_DEVICE_EVIDENCE/approval-path.log"
import asyncio, json, os, pathlib, tempfile
async def main():
    with tempfile.TemporaryDirectory(prefix="xod-native-approval-") as temp:
        os.environ.update(HOME=temp, XDG_CONFIG_HOME=temp, XDG_DATA_HOME=temp,
                          XOD_DATA_DIR=temp, XOD_NO_UPDATE_CHECK="1",
                          PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring")
        from app import paths
        paths.DEV_ENV_PATH=pathlib.Path(temp)/"absent.env"
        from app.services import computer_bridge, mcp_client
        from app.agent.tools import execute_tool
        reg=computer_bridge.register_computer_control()
        assert reg["trust"]=="ask" and reg["native_verified"] is False
        hub=mcp_client.mcp_hub
        t=os.environ["TARGET"]
        async def request(tool, args):
            return await execute_tool("mcp__computer-control__"+tool, args)
        try:
            await hub.start()
            names={d["function"]["name"] for d in hub.tool_definitions()}
            assert len([n for n in names if n.startswith("mcp__computer-control__")])==9
            for tool, args in (("computer_look", {"target":t}),
                               ("computer_click", {"target":t,"selector":"#button"})):
                held=await request(tool,args)
                assert held.get("needs_approval") is True, held
                print(tool, "HELD. Inspect the unchanged page, then type APPROVE:", flush=True)
                with open("/dev/tty") as tty:
                    if tty.readline().strip()!="APPROVE":
                        raise RuntimeError("Stopped without approving; no automatic retry")
                done=await hub.approve(held["approval_id"])
                print(json.dumps(done), flush=True)
                assert done.get("success") is True, done
            mcp_client.update_server("computer-control",trust="read_only")
            look=await request("computer_look", {"target":t})
            assert look.get("success") is True and not look.get("needs_approval"), look
            actions=[
                ("computer_click",{"target":t,"selector":"#button"}),
                ("computer_type",{"target":t,"selector":"#field","value":"NOT APPROVED"}),
                ("computer_press",{"target":t,"key":"Enter"}),
                ("computer_screenshot",{"target":t}),
                ("computer_scroll",{"target":t,"delta_y":100}),
                ("computer_hover",{"target":t,"selector":"#panel"}),
                ("computer_drag",{"target":t,"start_x":30,"start_y":240,"end_x":130,"end_y":300}),
                ("computer_open_app",{"application":"editor"})]
            for tool,args in actions:
                held=await request(tool,args)
                assert held.get("needs_approval") is True, held
                print(tool,"HELD, not approved",flush=True)
            mcp_client.update_server("computer-control",trust="always")
            held=await request("computer_click",{"target":t,"selector":"#button"})
            assert held.get("needs_approval") is True, held
            print("always also HELD. Inspect that there were no further device effects.",flush=True)
        finally:
            await hub.stop()
asyncio.run(main())
PY
```

The app-path command is not an Electron/chat UI test. On the installed
disposable app separately request a look/click at this target in chat, observe
the approval card, approve once and reject another click. Verify trust choices
in connection settings and no count change on rejection. If the installed UI
cannot perform those choices, report approval UI UNKNOWN/FAIL, not covered by
the command above. Personal sites and real sends remain outside this fixture.

Run `python3 tests/computer_control/run_computer_control_test.py` for the
deterministic app-path approval contracts. It is labeled fake, not device proof.

Missing bridge, with the real adapter (expected failure; no fake fallback):

```sh
XOD_COMPUTER_CONTROL_BOPS=/definitely/missing/xod-bops python3 - <<'PY'
from app.services.computer_control import BopsAdapter
r=BopsAdapter().look("disposable-target")
print(r)
assert r["success"] is False and r["availability"]=="UNAVAILABLE"
assert r["verification"]=="UNKNOWN" and not r["native_verified"]
PY
```

Repeat a look after closing only the disposable Chrome process: expect MCP
error/`success: false`, no simulated success. If any action times out, inspect
the real page before retrying; UNKNOWN is not permission to repeat it.

## 4. Record verdicts and cleanup

Save revision (`git rev-parse HEAD`), macOS/Chrome/Python versions, architecture,
time, raw JSON, screenshot paths, each observed value and per-row
PASS/FAIL/UNKNOWN. Approval gating, device actions, installed native screen
permissions and Partner are separate verdicts. Overall PASS requires every
applicable row observed; missing evidence is UNKNOWN, not PASS. Unsupported
Mac desktop capture must be recorded NOT SUPPORTED, not silently omitted.

Stop the disposable Xod you started using its own PID, not `killall`.
Close the disposable Chrome window or:

```sh
kill "$XOD_ACCEPTANCE_CHROME_PID"   # Only the PID launched in step 1
unset BOPS_CONFIG XOD_COMPUTER_CONTROL_ADAPTER XOD_COMPUTER_CONTROL_BOPS TARGET
```

Keep the evidence directory until review, then delete only that timestamped
directory if desired. Do not delete installed Xod data or personal Chrome
profiles. No real device has been accepted by writing this document.

## Windows and resource measurements

Windows requires its own real-device run; substituting mocked `sys.platform`
does not qualify. Use a dedicated Chrome profile, a localhost CDP port,
the same HTML/target/methods, an existing absolute allowlisted `.exe` for
`editor`, and the scheduled desktop-capture task only in the interactive
Windows session. SSH quoting, desktop capture, visible app launch, permissions
and mouse/key behavior remain UNKNOWN until observed there.

For exact read-only PID collection commands on Mac, Windows and Linux see
`docs/LIGHTWEIGHT-FOOTPRINT.md`. Never infer whole-app memory/CPU from a backend
sample or a missing Partner/model/Electron root.

## Verification recorded on November 16, 2026

These were run in the Linux Replit workspace, not on a native desktop:

| Command | Result |
| --- | --- |
| `python tests/computer_control/test_control_contract.py` | 21 tests passed |
| `python tests/computer_control/test_native_limits.py` | 9 tests passed |
| `python tests/computer_control/run_computer_control_test.py` | 45 checks passed, explicit fake stdio MCP |
| `python tests/runtime/test_measure_lightweight.py` | 18 tests passed, includes simulated platform cases |
| `python tests/smoke/run_browser_ops_test.py` | 10 non-CDP checks passed; 1 live CDP fixture skipped (Mac Chrome absent) |
| `python tests/smoke/run_muse_app_test.py` | 62 checks passed, synthetic Accessibility/UIA trees |
| `python tests/partner/run_approval_reliability_test.py` | 16 tests passed |
| `python tests/partner/run_partner_delegation_test.py` | 6 checks passed |
| `python tests/partner/run_partner_ax_test.py` | 19 checks passed; real CoreFoundation prompt skipped off Mac |

Missing-`websocket-client` skipping was separately checked by a synthetic
import guard; the actual installed dependency was 1.9.0. No dependency was
uninstalled to simulate a broken user installation.

A three-second read-only attachment to the real running Linux preview backend
completed, produced backend metrics and excluded the collector PID:
`generated-artifacts/footprint/native-limits-linux-attach.json`. Partner,
model-server and Electron roots were absent; whole-application verification
remained UNKNOWN. This is a collector integration check, not a resource-budget
benchmark or a native measurement.

All five Python heredoc examples above parsed successfully. The workflow was
restarted and the setup page rendered. The Linux keyring warning and absent
installed Partner remained explicit; no plaintext key store was introduced.
No native action, permission prompt, Electron integration, Windows desktop
capture or real-device footprint PASS is claimed by these checks.
