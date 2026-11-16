"""Real queue/SQLite cancellation regressions. No models, browser, or desktop."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
HOME = tempfile.TemporaryDirectory(prefix="xod-stop-home-")
os.environ.update(HOME=HOME.name, XOD_DATA_DIR=HOME.name,
                  XDG_CONFIG_HOME=str(Path(HOME.name) / "config"), XOD_SPEECH="off",
                  PYTHON_KEYRING_BACKEND="tests.support.filekeyring.FileKeyring")
from app.services import task_queue as tq, orchestrator


class StopRecovery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HOME.name)
        self.db = patch.object(tq, "DB_PATH", Path(self.tmp.name) / "tasks.db")
        self.db.start()
        self.q = tq.TaskQueue()
        self.q._wake = asyncio.Event()
        self.q._loop_ref = asyncio.get_running_loop()
        self.q._close_tab = AsyncMock()
        self.q._sweep_tool_children = AsyncMock()
        self.started, self.cleaning, self.finish, self.next_ran = (
            asyncio.Event() for _ in range(4))
        self.first = self.q.create("first")
        self.second = self.q.create("second")
        self.runs = []

        async def run(tid):
            self.runs.append(tid)
            self.q._running_id = tid
            self.q._update(tid, status="working")
            if tid == self.first["id"]:
                self.started.set()
                try:
                    await asyncio.Future()
                finally:
                    self.cleaning.set()
                    await self.finish.wait()
            else:
                self.q._update(tid, status="done")
                self.q._running_id = None
                self.next_ran.set()
        self.q._run = run
        self.q._worker = asyncio.create_task(self.q._loop())
        self.old_worker = self.q._worker
        await asyncio.wait_for(self.started.wait(), 2)

    async def asyncTearDown(self):
        self.finish.set()
        await asyncio.gather(*list(self.q._stops.values()), return_exceptions=True)
        worker = self.q._worker
        self.q.stop()
        await asyncio.gather(worker, return_exceptions=True)
        self.db.stop()
        self.tmp.cleanup()

    async def force(self):
        return await self.q._force_cancel(self.first["id"], "fixture")

    async def assert_finished(self):
        await asyncio.wait_for(self.next_ran.wait(), 2)
        self.assertEqual(self.q.get(self.first["id"])["status"], "cancelled")
        self.assertEqual(self.q.get(self.second["id"])["status"], "done")
        self.assertEqual(self.runs, [self.first["id"], self.second["id"]])
        self.assertIsNot(self.q._worker, self.old_worker)
        self.assertFalse(self.q._worker.done())
        self.assertEqual(self.q._sweep_tool_children.await_args_list.count(
            unittest.mock.call(self.first["id"])), 1)
        self.q._close_tab.assert_awaited_once()

    async def test_cleanup_over_five_seconds_settles_and_runs_next_once(self):
        started = time.monotonic()
        self.assertFalse(await self.force())
        self.assertGreaterEqual(time.monotonic() - started, 5)
        self.assertEqual(self.q.get(self.first["id"])["status"], "working")
        self.assertTrue(self.q.get(self.first["id"])["stalled"])
        self.assertFalse(self.next_ran.is_set())
        # A concurrent watchdog must not cancel cleanup or start another consumer.
        await self.q.run_watchdog(now=time.time() + 10000, stall_sec=0, recover_sec=0)
        repeated = asyncio.create_task(self.force())
        self.assertEqual(len(self.q._stops), 1)
        self.finish.set()
        self.assertTrue(await repeated)
        await self.assert_finished()

    async def test_disconnected_cancel_caller_does_not_abandon_settlement(self):
        caller = asyncio.create_task(self.force())
        await self.cleaning.wait()
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertEqual(len(self.q._stops), 1)
        self.finish.set()
        await self.assert_finished()

    async def test_shutdown_during_cleanup_does_not_respawn(self):
        caller = asyncio.create_task(self.force())
        await self.cleaning.wait()
        # Disable queue restart as shutdown does, without re-cancelling cleanup.
        self.q._wake = None
        self.finish.set()
        self.assertTrue(await caller)
        self.assertIs(self.q._worker, self.old_worker)
        self.assertTrue(self.old_worker.done())
        self.assertFalse(self.next_ran.is_set())

    async def test_redrive_waits_for_exit_and_user_cancel_wins(self):
        with patch.object(tq, "STOP_CLEANUP_WAIT_SEC", .02):
            self.assertFalse(await self.q._redrive(self.first["id"], time.time()))
            self.assertEqual(self.q.get(self.first["id"])["status"], "working")
            self.assertFalse(self.next_ran.is_set())
            cancel = asyncio.create_task(self.force())
            await asyncio.sleep(0)
            self.finish.set()
            await cancel  # the shortened response timeout may precede settlement
            await asyncio.gather(*list(self.q._stops.values()))
        await self.assert_finished()

    async def test_orchestrated_cleanup_settles_without_replacing_queue_worker(self):
        self.finish.set()
        self.old_worker.cancel()
        await asyncio.gather(self.old_worker, return_exceptions=True)
        self.q._running_id = None
        ready, finish = asyncio.Event(), asyncio.Event()
        async def run():
            try:
                ready.set()
                await asyncio.Future()
            finally:
                await finish.wait()
        native = asyncio.create_task(run())
        await ready.wait()
        with patch.object(orchestrator, "_pool", {self.first["id"]: native}), \
             patch.object(tq, "STOP_CLEANUP_WAIT_SEC", .02):
            self.assertFalse(await self.force())
            self.assertFalse(native.done())
            finish.set()
            await asyncio.gather(*list(self.q._stops.values()))
        self.assertEqual(self.q.get(self.first["id"])["status"], "cancelled")
        self.assertIs(self.q._worker, self.old_worker)


if __name__ == "__main__":
    unittest.main()
