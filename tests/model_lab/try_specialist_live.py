"""
Switches the sandbox Browser Navigator on (overriding its test result, sandbox only) and runs the
price-comparison task, to prove routing works: the student takes the fast steps, the teacher
handles thinking/escalations, and the student's own calls are labelled for live stats.
"""
import asyncio, json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys as _sys; _sys.path.insert(0, str(ROOT)); from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
os.environ.setdefault("XOD_DATASET_DB", str(_DATA / "test_runs" / "flywheel_test.db"))
sys.path.insert(0, str(ROOT))
import app.services.specialists as SP

SANDBOX = _DATA / "test_runs" / "lab_sandbox"
SP.MODELS_DIR, SP.STATE_PATH = SANDBOX / "models", SANDBOX / "models" / "specialists.json"


async def main() -> None:
    state = SP.load_state()
    entry = state["browser_navigator"]
    entry["eval"]["promotable"] = True
    entry["eval"]["forced_for_routing_test"] = True
    SP.save_state(state)
    t = time.time()
    print("enable:", await SP.set_enabled("browser_navigator", True), f"({time.time() - t:.0f}s)", flush=True)

    subprocess.run("lsof -ti:8799 | xargs kill -9", shell=True, stderr=subprocess.DEVNULL)
    mock = subprocess.Popen([sys.executable, str(ROOT / "tests" / "browser_missions" / "mock_jobs.py"), "8799"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    from app.agent.researcher import AgenticResearcher
    from app.services.browser_sandbox import browser_sandbox_service as sb
    q = "Compare the price of the Trail Runner 2 shoes on http://localhost:8799/shopa and http://localhost:8799/shopb and tell me which shop is cheaper"
    t = time.time()
    async for ev in AgenticResearcher().run_research_stream([{"role": "user", "content": q}]):
        if ev["type"] == "status" and ev["content"][:1] in "✓✗⏸•":
            print("  ", ev["content"][:150], flush=True)
        if ev["type"] == "content":
            print("REPLY:", ev["content"].split("<details>")[0].strip().replace("\n", " ")[:300], flush=True)
    m = sb.latest_mission or {}
    print(f"RESULT status={m.get('status')} steps={len(m.get('steps', []))} student_steps={m.get('specialist_steps')} "
          f"thinking_steps={m.get('thinking_steps')} escalations={m.get('escalations')} time={time.time() - t:.0f}s", flush=True)
    print("LIVE:", json.dumps([s["live"] for s in SP.overview() if s["name"] == "browser_navigator"]), flush=True)
    await SP.set_enabled("browser_navigator", False)
    mock.kill()


asyncio.run(main())
