"""
Fleet bridge (AGE-84, services/fleet_bridge.py): the harness can call Tree's fleet gate tools.

The five gate tools (cidiff, reprocheck, testdelta, skillcheck, contextbudget) are published
npm packages that also speak MCP over stdio. The harness already routes mcp__<server>__<tool>
calls to services/mcp_client.py's hub; what was missing is discovery + registration. This
suite proves the bridge against the REAL installed tools, through the same execute_tool the
chat loop calls:

  F1 discovery finds all five CLIs in the fleet-tools install, and reports an honest miss
     when pointed at an empty folder (no faked registrations)
  F2 registration writes the five servers into the MCP config with trust "ask", and leaves
     a server the person added themselves untouched
  F3 once the hub connects, the model-facing definitions include all five servers' tools
  F4 with the default trust, a call is held for approval (nothing runs silently); approving
     it round-trips testdelta: a planted regression comes back as a REGRESSED verdict
  F5 with trust raised (the person's own setting), execute_tool round-trips contextbudget,
     skillcheck, and reprocheck's runner detection directly

Prerequisite: the fleet tools installed (~/workspace/fleet-tools, or $XOD_FLEET_TOOLS_DIR).
Without them there is nothing real to prove against, so the suite prints SKIP and exits 0;
it is deliberately not in the CI suite list until the fleet is packaged into the test
environment. On a machine with the fleet it is strict: every check must pass.

Run: .venv/bin/python tests/fleet_bridge/run_fleet_bridge_test.py
"""
import asyncio, json, os, sys, tempfile
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_fleet_"))
os.environ.update({"XOD_DATA_DIR": str(TMP), "XOD_NO_UPDATE_CHECK": "1",
                   "XOD_MODEL_BUDGET": "off"})
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


from app.services import fleet_bridge, mcp_client  # noqa: E402

FLEET_DIR = Path(os.environ.get(fleet_bridge.ENV_DIR) or fleet_bridge.DEFAULT_DIR)
if not all(fleet_bridge._shim_in(FLEET_DIR, t) for t in fleet_bridge.FLEET_TOOLS):
    print(f"SKIP: fleet tools not installed under {FLEET_DIR}; nothing real to prove against.")
    sys.exit(0)
os.environ[fleet_bridge.ENV_DIR] = str(FLEET_DIR)

print("F1 discovery")
found = fleet_bridge.find_fleet_tools()
check("all five CLIs discovered", all(found[t] for t in fleet_bridge.FLEET_TOOLS),
      str({t: bool(found[t]) for t in fleet_bridge.FLEET_TOOLS}))
check("every discovery is the MCP mode of the CLI",
      all(found[t]["args"] == ["mcp"] for t in fleet_bridge.FLEET_TOOLS))
EMPTY = TMP / "empty"
EMPTY.mkdir()
os.environ[fleet_bridge.ENV_DIR] = str(EMPTY)
miss = fleet_bridge.find_fleet_tools()
check("empty folder reports an honest miss", all(miss[t] is None for t in fleet_bridge.FLEET_TOOLS),
      str({t: (miss[t] or {}).get("source") for t in fleet_bridge.FLEET_TOOLS}))
os.environ[fleet_bridge.ENV_DIR] = str(FLEET_DIR)

print("F2 registration")
mcp_client.add_servers({"mcpServers": {"personalsrv": {"command": "true", "args": []}}})
reg = fleet_bridge.register_fleet_tools()
check("registration reports all five", sorted(reg["registered"]) == sorted(fleet_bridge.FLEET_TOOLS),
      str(reg))
check("registration reports nothing missing", reg["missing"] == [], str(reg["missing"]))
cfg = mcp_client.load_config()
check("config holds the five fleet servers", all(t in cfg for t in fleet_bridge.FLEET_TOOLS))
check("fleet servers are registered at trust ask",
      all(cfg[t].get("trust") == "ask" for t in fleet_bridge.FLEET_TOOLS),
      str({t: cfg[t].get("trust") for t in fleet_bridge.FLEET_TOOLS}))
check("a person's own server is untouched", cfg.get("personalsrv", {}).get("command") == "true")


async def main():
    print("F3 hub definitions")
    await mcp_client.mcp_hub.start()
    names = {d["function"]["name"] for d in mcp_client.mcp_hub.tool_definitions()}
    want = {"mcp__cidiff__diff_pr_ci", "mcp__cidiff__diff_ci_runs",
            "mcp__reprocheck__run_repro_check", "mcp__reprocheck__detect_test_runners",
            "mcp__testdelta__diff_test_results", "mcp__skillcheck__check_skill",
            "mcp__contextbudget__budget_path"}
    check("all five servers offer their tools to the model", want <= names,
          f"missing {sorted(want - names)}")

    from app.agent.tools import execute_tool

    print("F4 approval hold, then a testdelta round trip")
    td = TMP / "td"
    td.mkdir()
    (td / "base.json").write_text(json.dumps({"test_a": "pass", "test_b": "pass", "test_c": "fail"}))
    (td / "head.json").write_text(json.dumps({"test_a": "pass", "test_b": "fail",
                                              "test_c": "pass", "test_d": "pass"}))
    held = await execute_tool("mcp__testdelta__diff_test_results", {"path": str(td)})
    check("default trust holds the call for approval", held.get("needs_approval") is True,
          str(held)[:200])
    done = await mcp_client.mcp_hub.approve(held.get("approval_id", ""))
    check("the approved call succeeds", done.get("success") is True, str(done)[:200])
    report = json.loads(done.get("content") or "{}")
    check("testdelta verdict is REGRESSED with the planted regression counted",
          report.get("verdict") == "REGRESSED" and report.get("counts", {}).get("regressions") == 1
          and report.get("counts", {}).get("fixes") == 1, str(report)[:200])

    print("F5 direct round trips at raised trust")
    for server in ("contextbudget", "skillcheck", "reprocheck"):
        mcp_client.update_server(server, trust="always")
    cb = TMP / "cb"
    (cb / "sub").mkdir(parents=True)
    (cb / "a.txt").write_text("hello world\n")
    out = await execute_tool("mcp__contextbudget__budget_path", {"path": str(cb)})
    report = json.loads(out.get("content") or "{}") if out.get("success") else {}
    check("contextbudget round-trips", out.get("success") is True
          and report.get("tool") == "contextbudget" and report.get("verdict") == "WITHIN-BUDGET",
          str(out)[:200])
    sk = TMP / "sk"
    sk.mkdir()
    (sk / "SKILL.md").write_text("---\nname: probe-skill\ndescription: A tiny probe skill used "
                                 "to check the skillcheck round trip end to end.\n---\n"
                                 "# Probe skill\nDo the thing.\n")
    out = await execute_tool("mcp__skillcheck__check_skill", {"path": str(sk)})
    report = json.loads(out.get("content") or "{}") if out.get("success") else {}
    check("skillcheck round-trips", out.get("success") is True and report.get("tool") == "skillcheck"
          and "verdict" in report, str(out)[:200])
    out = await execute_tool("mcp__reprocheck__detect_test_runners", {"repoPath": str(ROOT)})
    report = json.loads(out.get("content") or "{}") if out.get("success") else {}
    check("reprocheck round-trips (runner detection on this repo)",
          out.get("success") is True and any(r.get("id") == "pytest" for r in report.get("runners", [])),
          str(out)[:200])
    for conn in list(mcp_client.mcp_hub.conns.values()):
        await conn.stop()


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
