"""
Every shell command the agent runs goes through tools.execute_tool (orchestrator live runs,
2026-11-04: computer_run_command streamed straight to the shell from the researcher, so a step
that wrote a file with a command left no activity record, and no gate ever saw it).

  C1 the researcher's streaming path still streams output live
  C2 and the command is in the activity log under the task's origin
  C3 and it passed through execute_tool (a gate placed there sees it)

Run: .venv/bin/python tests/task_inbox/run_command_provenance_test.py
"""
import asyncio, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
os.environ.update({"XOD_DATA_DIR": tempfile.mkdtemp(prefix="xod_cmdprov_"), "XOD_NO_UPDATE_CHECK": "1"})
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
sys.path.insert(0, str(ROOT))
from app.agent.researcher import AgenticResearcher
from app.agent import tools
from app.services import activity

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""))


async def main():
    seen = []
    real = tools.execute_tool

    async def spy(name, args, *a, **k):
        seen.append(name)
        return await real(name, args, *a, **k)
    import app.agent.researcher as R
    R.execute_tool = spy
    out = Path(tempfile.mkdtemp()) / "first.txt"
    tok = activity.origin.set("task:prov-1")
    try:
        evs = [ev async for ev in AgenticResearcher()._exec_tool_stream(
            "computer_run_command", {"command": f"echo streamed; printf FIRST > {out}"}, "c1", None)]
    finally:
        activity.origin.reset(tok)
        R.execute_tool = real
    check("C1 output streams live", any(e["type"] == "tool_stream" and "streamed" in e["text"] for e in evs))
    check("C1 result intact", evs[-1]["result"].get("exit_code") == 0 and out.read_text() == "FIRST")
    log = activity.entries_for_origin("task:prov-1")
    check("C2 logged under the task's origin", [e["tool"] for e in log] == ["computer_run_command"], str(log))
    check("C3 went through execute_tool", seen == ["computer_run_command"], str(seen))
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)

asyncio.run(main())
