"""
Work that blocks never runs on the server's event loop (agent/researcher.py, services/orchestrator.py, workers.py).

Live 2026-11-04 (final count i, W1: the server paused 1.35 s; reruns 1.27-1.65 s): a stall watchdog that dumps the
loop's stack whenever it is stuck over 0.4 s caught two causes, under twelve research workers at once:
  - every research turn ran ps and lsof for the system telemetry on the loop (0.5-1.5 s each)
  - every running step and worker polled its steer inbox twice a second, opening the database on the loop (0.6 s)

Each is made slow on purpose here (1.2 s), and a ticker on the loop measures the longest gap.

  F1 a research turn's telemetry leaves the loop free
  F2 a worker's steer-inbox poll leaves the loop free
  F3 a task step's steer-inbox poll leaves the loop free
  F4 the inbox still answers (the steers are still seen)

Run: .venv/bin/python tests/task_inbox/run_loop_free_test.py
"""
import asyncio, os, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = tempfile.mkdtemp(prefix="xod_loopfree_")
os.environ.update({"XOD_DATA_DIR": TMP, "XOD_NO_UPDATE_CHECK": "1", "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(Path(TMP) / "keys.json"), "XOD_SECRETS_SERVICE": "Xod test keys"})
sys.path.insert(0, str(ROOT))

from app.services import orchestrator as O, workers as W
from app.services.system_control import system_control_service as SC
from app.services import llm as LLM

SLOW = 1.2
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


async def longest_gap(coro):
    gaps, stop = [], asyncio.Event()

    async def tick():
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.05)
            now = time.monotonic()
            gaps.append(now - last)
            last = now
    t = asyncio.ensure_future(tick())
    await asyncio.sleep(0.12)
    try:
        out = await coro
    finally:
        stop.set()
        await t
    return out, max(gaps or [0])


async def main():
    # F1: the research turn's telemetry
    real_t = SC.get_system_telemetry
    SC.get_system_telemetry = lambda *a, **k: (time.sleep(SLOW), {})[1]

    async def no_model(*a, **k):
        raise RuntimeError("stop here")
        yield  # noqa
    real_ds, real_cc = LLM.llm_service.deliberate_stream, LLM.llm_service.chat_completion
    real_sc = LLM.llm_service.stream_chat_completion
    LLM.llm_service.deliberate_stream = no_model
    LLM.llm_service.stream_chat_completion = no_model

    async def no_chat(*a, **k):
        raise RuntimeError("stop here")
    LLM.llm_service.chat_completion = no_chat
    from app.agent.researcher import AgenticResearcher

    async def first_events():
        n = 0
        try:
            async for _ in AgenticResearcher().run_research_stream([{"role": "user", "content": "What is 2+2?"}]):
                n += 1
                if n >= 3:
                    break
        except Exception:
            pass
        return n
    try:
        slow_t = SC.get_system_telemetry
        SC.get_system_telemetry = real_t
        await asyncio.wait_for(first_events(), 60)           # warm-up: first imports and caches, not measured
        SC.get_system_telemetry = slow_t
        _, gap = await asyncio.wait_for(longest_gap(first_events()), 60)
    finally:
        SC.get_system_telemetry = real_t
        LLM.llm_service.deliberate_stream, LLM.llm_service.chat_completion = real_ds, real_cc
        LLM.llm_service.stream_chat_completion = real_sc
    check("F1 a research turn's telemetry leaves the loop free", gap < 0.4, f"gap {gap:.2f}s")

    # F2 / F3 / F4: the steer inbox, made slow
    real_inbox = O.inbox

    def slow_inbox(tid, state=None):
        time.sleep(SLOW)
        return real_inbox(tid, state)
    O.inbox = slow_inbox
    try:
        c = O._conn()
        c.execute("INSERT INTO steer_inbox (id, target_type, target_id, kind, body, source, urgent, state, created_at) "
                  "VALUES ('s1','task','t-loop','steer','x','user',0,'queued',?)", (time.time(),))
        c.commit()
        c.close()
        _, gap = await longest_gap(_worker_poll())
        check("F2 a worker's steer-inbox poll leaves the loop free", gap < 0.4, f"gap {gap:.2f}s")
        rows, gap = await longest_gap(O.ainbox("t-loop", state="queued"))
        check("F3 a task step's steer-inbox poll leaves the loop free", gap < 0.4, f"gap {gap:.2f}s")
        check("F4 the inbox still answers", [r["id"] for r in rows] == ["s1"], str(rows))
    finally:
        O.inbox = real_inbox


async def _worker_poll():
    """The worker's own poll, as _turn runs it: a turn that is still going, checked once for urgent steers."""
    sink = {"content": [], "evidence": [], "failures": []}

    async def slow_agent(prompt, wid):
        await asyncio.sleep(1.0)
        yield {"type": "content", "content": "ok"}
    real = W.agent
    W.agent = slow_agent
    try:
        return await W._turn("t-loop", "hi", sink)
    finally:
        W.agent = real


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:", *failed, sep="\n  ")
    sys.exit(1)
