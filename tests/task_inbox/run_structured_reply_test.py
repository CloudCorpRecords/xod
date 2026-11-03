"""
Task inbox regression test: structured (dict) agent output must not crash.

Baseline evidence (suite run installed-20261030183907, test T3): a task that
asked for a JSON object back failed with
"Something went wrong: 'dict' object has no attribute 'strip'" because the
deliberation model returned direct_response as a dict and the reply path
called .strip() on it. Two guards now cover this: _as_text() coerces
model-provided text fields in the researcher, and the task queue serializes
non-string content events instead of breaking on "".join.

Run: .venv/bin/python tests/task_inbox/run_structured_reply_test.py
"""
import asyncio, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
TMP = Path(tempfile.mkdtemp(prefix="xod_task_struct_"))
os.environ["XOD_DATASET_DB"] = str(_DATA / "test_runs" / "flywheel_test.db")
import app.services.task_queue as TQ
import app.services.browser_sandbox as BS
import app.agent.researcher as RS

TQ.DB_PATH, TQ.SHOTS_DIR = TMP / "tasks.db", TMP / "shots"
TQ.notify = lambda title, msg: print(f"   [notification] {title}: {msg}", flush=True)
if hasattr(TQ, "_phone"):
    TQ._phone = lambda task, kind: None

PAYLOAD = {"eval": "xod-proving-loop", "nonce": "TESTNONCE", "answer": 42}


def test_as_text():
    assert RS._as_text("hello") == "hello"
    assert RS._as_text("") == ""
    assert RS._as_text(None) == ""
    assert RS._as_text(42) == "42"
    assert json.loads(RS._as_text(PAYLOAD)) == PAYLOAD
    assert json.loads(RS._as_text([1, 2])) == [1, 2]
    print("PASS: _as_text coerces dict/list/int/None and passes strings through", flush=True)


async def _no_browser_needed():
    return None


BS.browser_sandbox_service.ensure_browser_running = _no_browser_needed


class _DictResearcher:
    async def run_research_stream(self, messages):
        yield {"type": "content", "content": PAYLOAD}


RS.AgenticResearcher = _DictResearcher


async def test_queue_dict_content():
    q = TQ.TaskQueue()
    tid = q.create("Reply with a JSON object")[ "id"]
    await q._run(tid)
    t = q.get(tid)
    print(f"   final: status={t['status']} result={t.get('result')!r}", flush=True)
    assert t["status"] == "done", f"expected done, got {t['status']}: {t.get('result')}"
    assert json.loads(t["result"]) == PAYLOAD, t.get("result")
    print("PASS: task completed with a dict content event, result is the JSON text", flush=True)


async def main():
    await test_queue_dict_content()
    test_as_text()


if __name__ == "__main__":
    asyncio.run(main())
