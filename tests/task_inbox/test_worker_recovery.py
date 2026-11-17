"""Real SQLite recovery regressions; scripted agents, no model or desktop calls."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
HOME = tempfile.TemporaryDirectory(prefix="xod-worker-recovery-")
os.environ.update(HOME=HOME.name, XOD_DATA_DIR=HOME.name,
                  XDG_CONFIG_HOME=str(Path(HOME.name) / "config"),
                  XDG_DATA_HOME=str(Path(HOME.name) / "share"), XOD_SPEECH="off",
                  PYTHON_KEYRING_BACKEND="tests.support.filekeyring.FileKeyring")
from app.services import workers as W, resources as R, task_queue as TQ, orchestrator as O


class WorkerRecovery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HOME.name)
        self.db = patch.object(TQ, "DB_PATH", Path(self.tmp.name) / "tasks.db")
        self.db.start()
        self.calls = []
        self.original_agent, self.original_synth = W.agent, W.synthesize
        self.original_events, self.original_runs = W._done_events, W._runs
        self.original_changed = R._changed
        W._done_events, W._runs, R._changed = {}, {}, None

        async def agent(brief, wid):
            self.calls.append(wid)
            yield {"type": "tool_result", "name": "web_search",
                   "data": {"success": True, "content": "fixture source"}}
            yield {"type": "content", "content": "Confirmed value: 42, source https://example.test"}

        async def synth(job, results):
            return " | ".join(r["report"] for r in results)

        W.agent, W.synthesize = agent, synth

    async def asyncTearDown(self):
        for run in W._runs.values():
            if not run.done():
                run.cancel()
        await asyncio.gather(*W._runs.values(), return_exceptions=True)
        W.agent, W.synthesize = self.original_agent, self.original_synth
        W._done_events, W._runs = self.original_events, self.original_runs
        R._changed = self.original_changed
        self.db.stop()
        self.tmp.cleanup()

    async def until(self, predicate, seconds=1.5):
        async with asyncio.timeout(seconds):
            while not predicate():
                await asyncio.sleep(.01)

    async def coordinated(self, spec, reuse=None, parent="parent"):
        return await W.coordinate(parent, "Check the source", [spec], timeout=2, reuse_from=reuse)

    def seed(self, state="done", result=None):
        wid = "wrk-persisted"
        now = time.time()
        c = W._conn()
        c.execute("INSERT INTO workers "
                  "(id,parent_id,parent_type,role,brief,envelope,state,created_at,last_activity_at,result_json) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (wid, "parent", "task", "worker", "fixture", "read_only", state, now, now,
                   json.dumps(result) if result else None))
        c.commit()
        c.close()
        return wid

    async def test_identical_complete_brief_reuses_once(self):
        spec = {"task": "Check source", "outcome": "A sourced value",
                "facts": ["region alpha"], "constraints": ["Use supplied source"], "needs": []}
        first = await self.coordinated(spec)
        second = await self.coordinated(copy.deepcopy(spec), first["coordinator"])
        self.assertEqual(second["reused"], [first["workers"][0]["id"]])
        self.assertEqual(len(self.calls), 1)

    async def test_changed_brief_fields_do_not_reuse(self):
        spec = {"task": "Check source", "outcome": "A sourced value",
                "facts": ["region alpha"], "constraints": ["Use supplied source"], "needs": []}
        first = await self.coordinated(spec)
        for field, value in (("task", "Check another source"), ("outcome", "A quoted value"),
                             ("facts", ["region beta"]), ("constraints", ["Use latest source"]),
                             ("needs", ["research-slot"])):
            with self.subTest(field=field):
                changed = {**spec, field: value}
                result = await self.coordinated(changed, first["coordinator"])
                self.assertEqual(result["reused"], [])
                self.assertNotEqual(result["workers"][0]["id"], first["workers"][0]["id"])

    async def test_legacy_or_unverifiable_reuse_is_refused(self):
        spec = {"task": "Check source"}
        for changes in ({"job_key": "Check source"}, {"brief": "partial legacy brief"},
                        {"needs": None}, {"needs": "not-json"},
                        {"result_json": {"outcome": "done", "report": ""}},
                        {"envelope": "full"}):
            with self.subTest(changes=changes):
                first = await self.coordinated(spec)
                W._save(first["workers"][0]["id"], **changes)
                result = await self.coordinated(spec, first["coordinator"])
                self.assertEqual(result["reused"], [])

    async def test_reuse_cannot_cross_parent_ownership(self):
        first = await self.coordinated({"task": "Check source"})
        result = await self.coordinated({"task": "Check source"}, first["coordinator"], parent="other")
        self.assertEqual(result["reused"], [])

    async def test_duplicate_subtasks_do_not_share_one_reused_worker(self):
        spec = {"task": "Check source"}
        first = await self.coordinated(spec)
        result = await W.coordinate("parent", "Two checks", [spec, spec], timeout=2,
                                    reuse_from=first["coordinator"])
        ids = [w["id"] for w in result["workers"]]
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(len(result["reused"]), 1)

    async def test_steered_result_is_not_reused_as_original_brief(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def agent(brief, wid):
            started.set()
            await finish.wait()
            yield {"type": "content", "content": "Confirmed value 42."}
        W.agent = agent
        original = asyncio.create_task(self.coordinated({"task": "Check source"}))
        await started.wait()
        worker = next(w for w in W.list_workers() if w["role"] == "worker")
        W.steer(worker["id"], "Use revised input instead")
        finish.set()
        first = await original
        result = await self.coordinated({"task": "Check source"}, first["coordinator"])
        self.assertEqual(result["reused"], [])

    async def test_terminal_persisted_worker_without_event_returns_promptly(self):
        for state in ("done", "failed", "interrupted"):
            with self.subTest(state=state):
                if W.get("wrk-persisted"):
                    W._save("wrk-persisted", state=state)
                    wid = "wrk-persisted"
                else:
                    wid = self.seed(state)
                result = await asyncio.wait_for(W.wait([wid]), .25)
                self.assertEqual(result[wid]["state"], state)

    async def test_missing_worker_and_empty_wait_return_promptly(self):
        self.assertEqual(await asyncio.wait_for(W.wait([]), .25), {})
        self.assertEqual(await asyncio.wait_for(W.wait(["missing"]), .25), {"missing": {}})

    async def test_lost_event_during_active_work_is_recreated(self):
        gate = asyncio.Event()
        async def agent(brief, wid):
            await gate.wait()
            yield {"type": "content", "content": "The value is 42."}
        W.agent = agent
        wid = W.spawn("parent", "Check source")
        W._done_events.pop(wid)
        waiter = asyncio.create_task(W.wait([wid]))
        await asyncio.sleep(.02)
        gate.set()
        result = await asyncio.wait_for(waiter, .5)
        self.assertEqual(result[wid]["state"], "done")

    async def test_running_persisted_worker_reconciles_without_event_notification(self):
        wid = self.seed("running")
        waiter = asyncio.create_task(W.wait([wid]))
        await asyncio.sleep(.02)
        W._save(wid, state="interrupted")
        result = await asyncio.wait_for(waiter, 1.5)
        self.assertEqual(result[wid]["state"], "interrupted")

    async def test_timeout_does_not_stop_worker_or_poison_later_wait(self):
        gate = asyncio.Event()
        async def agent(brief, wid):
            await gate.wait()
            yield {"type": "content", "content": "The value is 42."}
        W.agent = agent
        wid = W.spawn("parent", "Check source")
        result = await W.wait([wid], timeout=.02)
        self.assertEqual(result[wid]["state"], "running")
        self.assertFalse(W._runs[wid].done())
        gate.set()
        self.assertEqual((await W.wait([wid], timeout=1))[wid]["state"], "done")

    async def test_stale_signalled_event_does_not_spin(self):
        wid = self.seed("running")
        W._done_events[wid] = asyncio.Event()
        W._done_events[wid].set()
        with patch.object(W, "get", wraps=W.get) as get:
            result = await W.wait([wid], timeout=.05)
            self.assertEqual(result[wid]["state"], "running")
            self.assertLess(get.call_count, 6)

    async def test_cancelled_waiter_does_not_stop_worker(self):
        gate = asyncio.Event()
        async def agent(brief, wid):
            await gate.wait()
            yield {"type": "content", "content": "The value is 42."}
        W.agent = agent
        wid = W.spawn("parent", "Check source")
        waiter = asyncio.create_task(W.wait([wid]))
        await asyncio.sleep(.02)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(W._runs[wid].done())
        gate.set()
        self.assertEqual((await W.wait([wid], timeout=1))[wid]["state"], "done")

    async def test_cancel_while_resource_queued_never_starts_tools(self):
        R.acquire("busy", "other-holder")
        wid = W.spawn("parent", "Check source", needs=["first", "busy"])
        holder = f"worker:{wid}"
        await self.until(lambda: any(c["holder_id"] == holder for c in R.status("busy")[0]["queue"]))
        reply = W.steer(wid, "Stop now", kind="cancel")
        await self.until(lambda: W.get(wid)["state"] == "interrupted")
        self.assertEqual(self.calls, [])
        self.assertEqual(O.inbox(wid, state="applied")[0]["id"], reply["steer_id"])
        for name in ("first", "busy"):
            status = R.status(name)[0]
            self.assertFalse(any(c["holder_id"] == holder for c in status["holders"] + status["queue"]))
        R.release_holder("other-holder")
        await asyncio.sleep(.02)
        self.assertEqual(self.calls, [])

    async def test_cancel_before_resource_grant_never_starts_agent(self):
        wid = W.spawn("parent", "Check source", needs=["available"])
        W.steer(wid, "Stop now", kind="cancel")
        result = await W.wait([wid], timeout=1)
        self.assertEqual(result[wid]["state"], "interrupted")
        self.assertEqual(self.calls, [])

    async def test_direct_interrupt_cleans_resource_wait_and_prior_claims(self):
        R.acquire("busy", "other-holder")
        wid = W.spawn("parent", "Check source", needs=["first", "busy"])
        holder = f"worker:{wid}"
        await self.until(lambda: any(c["holder_id"] == holder for c in R.status("busy")[0]["queue"]))
        self.assertTrue(await W.interrupt(wid))
        self.assertEqual(W.get(wid)["state"], "interrupted")
        for name in ("first", "busy"):
            status = R.status(name)[0]
            self.assertFalse(any(c["holder_id"] == holder for c in status["holders"] + status["queue"]))
        self.assertEqual(self.calls, [])

    async def test_cancel_while_waiting_for_worker_slot_never_starts_agent(self):
        from app.services import runtime_limits
        with patch.object(runtime_limits, "worker_limit", return_value=1):
            async with runtime_limits.slot("workers", 1):
                wid = W.spawn("parent", "Check source")
                await self.until(lambda: W.get(wid)["last_activity"] == "queued for a worker slot")
                W.steer(wid, "Stop now", kind="cancel")
                result = await W.wait([wid], timeout=1.5)
                self.assertEqual(result[wid]["state"], "interrupted")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
