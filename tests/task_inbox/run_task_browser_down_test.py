"""
Task inbox regression test: a task must not die when the browser cannot start.

TaskQueue._run used to call _ensure_tab unguarded, so a Chrome start failure
("Background Chrome failed to start or open remote debugging port.") failed
reply-only tasks before the agent ran a single step. This test forces
ensure_browser_running to raise that exact error and checks that the task
still finishes with the agent's reply.

Run: .venv/bin/python tests/task_inbox/run_task_browser_down_test.py
"""
import asyncio, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
TMP = Path(tempfile.mkdtemp(prefix="xod_task_nobrowser_"))
os.environ["XOD_DATASET_DB"] = str(_DATA / "test_runs" / "flywheel_test.db")
import app.services.task_queue as TQ
import app.services.browser_sandbox as BS
import app.agent.researcher as RS

TQ.DB_PATH, TQ.SHOTS_DIR = TMP / "tasks.db", TMP / "shots"
TQ.notify = lambda title, msg: print(f"   [notification] {title}: {msg}", flush=True)
if hasattr(TQ, "_phone"):
    TQ._phone = lambda task, kind: None


async def _boom():
    raise RuntimeError("Background Chrome failed to start or open remote debugging port.")


BS.browser_sandbox_service.ensure_browser_running = _boom


class _FakeResearcher:
    async def run_research_stream(self, messages):
        yield {"type": "content", "content": "ok-no-browser"}


RS.AgenticResearcher = _FakeResearcher


async def main():
    q = TQ.TaskQueue()
    tid = q.create("Reply with exactly: ok-no-browser")["id"]
    await q._run(tid)
    t = q.get(tid)
    print(f"   final: status={t['status']} result={t.get('result')!r}", flush=True)
    assert t["status"] == "done", f"expected done, got {t['status']}: {t.get('result')}"
    assert (t.get("result") or "").strip() == "ok-no-browser", t.get("result")
    print("PASS: task completed while the browser was down", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
