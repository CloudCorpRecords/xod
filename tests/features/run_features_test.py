"""
The launch features, end to end, in a throwaway data folder (never your real data or Keychain).

  A  activity log + undo for every kind of file change, and the "changed since" guard
  R  routines: schedules, learning steps from a chat, the request a run sends
  M  model routing: ask/auto escalation, cost tracking, budget stop
  P  add-on packs: tools of a pack that's off are hidden and refused
  D  reports: page with sources, hostile HTML stripped, PDF printed
  C  MCP: Xod connects to its own MCP server; read-only detection; approval gate

  .venv/bin/python tests/features/run_features_test.py
"""
import asyncio
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-features-", dir="/tmp")).resolve()   # /var/folders is a protected path for file tools
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
sys.path.insert(0, str(ROOT))

results = []


def report(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


async def test_activity():
    from app.agent.tools import execute_tool
    from app.services import activity
    d = TMP / "files"
    d.mkdir()
    (d / "notes.md").write_text("original\n")
    for n in ("a.jpg", "b.pdf"):
        (d / n).write_bytes(b"x")
    activity.origin.set("chat:test")
    await execute_tool("computer_write_file", {"filepath": str(d / "new.md"), "content": "new"})
    await execute_tool("computer_edit_file", {"filepath": str(d / "notes.md"), "target_content": "original", "replacement_content": "CHANGED"})
    await execute_tool("computer_batch_organize", {"source_dir": str(d)})
    items = activity.recent()
    report("A1 actions are logged", len(items) == 3 and all(i["undoable"] for i in items), [i["title"] for i in items])
    for it in items:
        activity.undo(it["id"])
    names = sorted(p.name for p in d.iterdir())
    report("A2 undo puts everything back", names == ["a.jpg", "b.pdf", "notes.md"] and (d / "notes.md").read_text() == "original\n", names)
    await execute_tool("computer_write_file", {"filepath": str(d / "keep.md"), "content": "xod"})
    (d / "keep.md").write_text("edited by the user")
    r = activity.undo(activity.recent()[0]["id"])
    report("A3 undo won't clobber a later edit", r.get("conflict") and (d / "keep.md").read_text() == "edited by the user", r.get("detail"))


def test_routines():
    from app.services import routines
    base = dt.datetime(2026, 10, 10, 10, 0).timestamp()   # Saturday 10:00
    nxt = lambda s: dt.datetime.fromtimestamp(routines.next_run(s, base)).strftime("%a %H:%M")
    ok = (nxt({"kind": "daily", "time": "09:00"}) == "Sun 09:00" and nxt({"kind": "daily", "time": "18:30"}) == "Sat 18:30"
          and nxt({"kind": "weekly", "time": "08:00", "days": ["mon", "tue", "wed", "thu", "fri"]}) == "Mon 08:00")
    report("R1 schedules", ok)
    r = routines.create("Notes", "Write the notes file", {"kind": "daily", "time": "09:00"}, "chat:test")
    report("R2 steps learned from the chat", len(r["steps"]) >= 1 and "Wrote new.md" in r["steps"][0], r["steps"][:2])
    report("R3 run request carries the steps", "Steps that worked last time" in routines.compose_request(r))


def test_routing():
    from app.config import settings
    from app.services import llm_routing as R
    settings.anthropic_api_key = "sk-test-in-memory-only"   # never saved anywhere
    R.save_settings(mode="auto", provider="anthropic", model="claude-sonnet-5-5", monthly_budget_usd=1)
    t = R.start_turn()
    first, second = R.note_failure("x"), R.note_failure("y")
    report("M1 auto escalates after two failures", first is None and second and t.endpoint["model"] == "claude-sonnet-5-5")
    R.record("anthropic", "claude-sonnet-5-5", {"prompt_tokens": 100000, "completion_tokens": 10000})
    R.record("lm_studio", "local", {"prompt_tokens": 10 ** 6, "completion_tokens": 10 ** 5})
    report("M2 cloud cost tracked, local free", abs(R.month_spend() - 0.45) < 1e-6, R.month_spend())
    R.record("anthropic", "claude-sonnet-5-5", {"prompt_tokens": 300000, "completion_tokens": 0})
    R.start_turn(); R.note_failure("x")
    report("M3 no escalation over budget", R.note_failure("y") is None)
    settings.anthropic_api_key = ""


async def test_packs():
    from app.agent.tools import execute_tool, select_tools_for_query
    from app.services import packs
    packs.set_enabled("startup-intel", False)
    offered = [t["function"]["name"] for t in select_tools_for_query("trustmrr revenue for shipfast")]
    r = await execute_tool("trustmrr_get_startup", {"slug": "x"})
    report("P1 pack off: hidden and refused", not any("trustmrr" in n for n in offered) and "turned off" in r.get("error", ""))


def test_reports():
    from app.services import reports
    r = reports.make_report("Test report", "q?", '<p>See <a href="https://example.org/a">A</a></p><img src=x onerror=alert(1)><script>x()</script>'
                            '<a href="javascript:alert(2)">bad</a>')
    page = Path(r["html_path"]).read_text()
    report("D1 sources + hostile HTML stripped", r["sources"] == 1 and not any(b in page for b in ("onerror", "<script", "javascript:")))
    report("D2 PDF printed", bool(r["pdf_path"]) and Path(r["pdf_path"]).stat().st_size > 1000, r["pdf_path"] and Path(r["pdf_path"]).name)


async def test_mcp():
    """Xod's MCP client talks to Xod's MCP server (which needs a Xod API; a tiny stand-in serves /api/routines)."""
    import threading
    import uvicorn
    from fastapi import FastAPI
    from app.services import mcp_client
    api = FastAPI()

    @api.get("/api/routines")
    async def _r():
        return {"routines": [{"id": "rtn-1", "name": "Demo", "schedule_text": "Every day", "enabled": True}]}

    @api.post("/api/tasks")
    async def _t():
        return {"task": {"id": "task-1", "status": "queued", "title": "t"}}
    srv = uvicorn.Server(uvicorn.Config(api, host="127.0.0.1", port=8931, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    await asyncio.sleep(1.5)
    mcp_client.CONFIG.write_text('{"servers": {"xod-self": {"command": "%s", "args": ["-m", "app.mcp_server"], "cwd": "%s", '
                                 '"env": {"XOD_URL": "http://127.0.0.1:8931"}, "enabled": true, "trust": "read_only"}}}'
                                 % (sys.executable, ROOT))
    hub = mcp_client.mcp_hub
    await hub.start()
    st = hub.status()[0]
    ro = {t["name"] for t in st["tools"] if t["read_only"]}
    report("C1 connected, read-only detected", st["connected"] and "list_routines" in ro and "start_task" not in ro, sorted(ro))
    r1 = await hub.call("mcp__xod-self__list_routines", {})
    report("C2 read-only runs when trusted for reads", r1.get("success") and "Demo" in r1.get("content", ""))
    r2 = await hub.call("mcp__xod-self__start_task", {"request": "hi"})
    r3 = await hub.approve(r2.get("approval_id", ""))
    report("C3 action held, then runs on approval", r2.get("needs_approval") and r3.get("success") and "task-1" in r3.get("content", ""))
    for c in hub.conns.values():
        await c.stop()
    srv.should_exit = True


async def main():
    await test_activity()
    test_routines()
    test_routing()
    await test_packs()
    test_reports()
    await test_mcp()
    print("SUMMARY", "PASS" if all(ok for _, ok in results) else "FAIL", [n for n, ok in results if not ok], flush=True)

try:
    asyncio.run(main())
finally:
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)
