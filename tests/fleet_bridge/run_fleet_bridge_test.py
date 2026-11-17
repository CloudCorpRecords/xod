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

Prerequisite: local fleet tools or the pinned packages installed with npm ci.
CI passes --require-tools so missing packages are a failure, not a silent skip.

Run: .venv/bin/python tests/fleet_bridge/run_fleet_bridge_test.py
"""
import asyncio, json, os, sys, tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_fleet_"))
os.environ.update({"XOD_DATA_DIR": str(TMP), "XOD_NO_UPDATE_CHECK": "1",
                   "XOD_MODEL_BUDGET": "off", "HOME": str(TMP / "home"),
                   "XDG_CONFIG_HOME": str(TMP / "config"), "XDG_DATA_HOME": str(TMP / "share"),
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keyring.json"),
                   "XOD_SECRETS_SERVICE": "Xod fleet synthetic fixture"})
for key in ("OPENAI_API_KEY", "AI_INTEGRATIONS_OPENAI_API_KEY", "FIREWORKS_API_KEY",
            "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
    os.environ.pop(key, None)
sys.path.insert(0, str(ROOT))
from app import paths
paths.DEV_ENV_PATH = TMP / "absent.env"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


from app.services import fleet_bridge, mcp_client  # noqa: E402

if not all(fleet_bridge.find_fleet_tools().values()):
    print("Fleet tools missing; install the locked packages with npm ci --ignore-scripts.")
    sys.exit(1 if "--require-tools" in sys.argv else 0)

print("F1 discovery")
found = fleet_bridge.find_fleet_tools()
check("all five CLIs discovered", all(found[t] for t in fleet_bridge.FLEET_TOOLS),
      str({t: bool(found[t]) for t in fleet_bridge.FLEET_TOOLS}))
check("every discovery is the MCP mode of the CLI",
       all(found[t]["args"][-1] == "mcp" for t in fleet_bridge.FLEET_TOOLS))
EMPTY = TMP / "empty"
EMPTY.mkdir()
os.environ[fleet_bridge.ENV_DIR] = str(EMPTY)
with patch("app.services.fleet_bridge.shutil.which", return_value=None), \
     patch("app.services.fleet_policy.PACKAGE_ROOT", EMPTY):
    miss = fleet_bridge.find_fleet_tools()
check("empty folder reports an honest miss", all(miss[t] is None for t in fleet_bridge.FLEET_TOOLS),
      str({t: (miss[t] or {}).get("source") for t in fleet_bridge.FLEET_TOOLS}))
os.environ.pop(fleet_bridge.ENV_DIR, None)

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
    if out.get("needs_approval"):
        out = await mcp_client.mcp_hub.approve(out["approval_id"])
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
    if out.get("needs_approval"):
        out = await mcp_client.mcp_hub.approve(out["approval_id"])
    report = json.loads(out.get("content") or "{}") if out.get("success") else {}
    check("skillcheck round-trips", out.get("success") is True and report.get("tool") == "skillcheck"
          and "verdict" in report, str(out)[:200])
    out = await execute_tool("mcp__reprocheck__detect_test_runners", {"repoPath": str(ROOT)})
    if out.get("needs_approval"):
        out = await mcp_client.mcp_hub.approve(out["approval_id"])
    report = json.loads(out.get("content") or "{}") if out.get("success") else {}
    check("reprocheck round-trips (runner detection on this repo)",
          out.get("success") is True and any(r.get("id") == "pytest" for r in report.get("runners", [])),
          str(out)[:200])
    print("F6 effective read-only policy in the actual tool dispatcher")
    from app.services import workers
    from app.services.fleet_policy import verified_read_only
    conn = mcp_client.mcp_hub.conns["testdelta"]
    if verified_read_only("testdelta", conn.spec, "diff_test_results"):
        mcp_client.update_server("testdelta", trust="read_only")
        token = workers.envelope.set("read_only")
        try:
            out = await execute_tool("mcp__testdelta__diff_test_results", {"path": str(td)})
            check("verified published read works in a read-only worker without approval",
                  out.get("success") is True and not out.get("needs_approval"), str(out)[:200])
            out = await execute_tool("mcp__reprocheck__run_repro_check", {
                "repoPath": str(ROOT), "baseRef": "HEAD", "headRef": "HEAD",
                "command": "echo must-not-run"})
            check("command execution is refused by a read-only worker before dispatch",
                  out.get("outside_envelope") is True, str(out)[:200])
        finally:
            workers.envelope.reset(token)
    elif "--require-tools" in sys.argv:
        check("CI must exercise verified published read-only policy", False, "Unverified local launcher shadowed package")
    for conn in list(mcp_client.mcp_hub.conns.values()):
        await conn.stop()


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
