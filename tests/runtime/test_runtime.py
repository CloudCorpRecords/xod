"""Isolated lifecycle regressions. No live models, credentials or desktop actions."""
import asyncio
import json
import os
import sys
import tempfile
import time
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
TMP = tempfile.TemporaryDirectory(prefix="xod-lifecycle-")
os.environ.update(XOD_DATA_DIR=TMP.name, HOME=TMP.name, XOD_SPEECH="off",
                  PYTHON_KEYRING_BACKEND="tests.support.filekeyring.FileKeyring")
from app.services import team, runtime_limits, run_mode


class Runtime(unittest.IsolatedAsyncioTestCase):
    async def test_leaf_cannot_deadlock_by_delegating_again(self):
        from app.services import workers
        token = workers.envelope.set("read_only")
        try:
            self.assertFalse(await team.team_on())
            with self.assertRaises(PermissionError):
                await anext(team.delegate_many([{"task": "nested"}], None))
        finally:
            workers.envelope.reset(token)

    async def test_mixed_inference_capacity_and_cancellation(self):
        counts = {"local": 0, "remote": 0}
        peaks = dict(counts)
        async def call(kind):
            info = {"provider": "ollama" if kind == "local" else "openai"}
            async with runtime_limits.inference(info):
                counts[kind] += 1
                peaks[kind] = max(peaks[kind], counts[kind])
                try:
                    await asyncio.sleep(.02)
                finally:
                    counts[kind] -= 1
        with patch.object(team, "load", return_value={"max_parallel": 2}):
            await asyncio.gather(*(call(k) for k in ["local"] * 4 + ["remote"] * 4))
        self.assertEqual(peaks, {"local": 1, "remote": 2})
        entered = asyncio.Event()
        async def held():
            async with runtime_limits.slot("test", 1):
                entered.set()
                await asyncio.sleep(60)
        task = asyncio.create_task(held())
        await entered.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        async with asyncio.timeout(1):
            async with runtime_limits.slot("test", 1):
                pass

    async def test_team_close_cleans_workers_and_getter(self):
        active = []
        async def worker(label, *args):
            active.append(label)
            try:
                await asyncio.sleep(60)
            finally:
                active.remove(label)
        before = set(asyncio.all_tasks())
        with patch.object(team, "tiers", AsyncMock(return_value={})), \
             patch.object(team, "run_subagent", worker):
            stream = team.delegate_many([{"task": "read"}] * 3, None)
            await anext(stream)
            await anext(stream)
            await asyncio.sleep(.01)
            self.assertTrue(active)
            await stream.aclose()
        self.assertEqual(active, [])
        self.assertFalse([t for t in asyncio.all_tasks() - before if not t.done()])

    async def test_worker_and_team_share_slots(self):
        from app.services import workers
        active = peak = 0
        async def fake_agent(*args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(.04)
                yield {"type": "content", "content": "source verified"}
            finally:
                active -= 1
        async def sub(label, *args):
            async for _ in fake_agent():
                pass
            return {"success": True, "agent": label}
        async def batch():
            async for _ in team.delegate_many([{"task": "read"}] * 3, None):
                pass
        with patch.object(team, "load", return_value={"max_parallel": 2}), \
             patch.object(team, "tiers", AsyncMock(return_value={})), \
             patch.object(team, "run_subagent", sub), \
             patch.object(workers, "agent", fake_agent), patch.object(workers, "_save"):
            await asyncio.gather(batch(), *(workers._turn(str(i), "brief", {"content": [], "evidence": [], "failures": []})
                                           for i in range(3)))
        self.assertEqual(peak, 2)

    async def test_shrink_preserves_constraints_and_pending_effects(self):
        from app.agent import worker
        original = {"role": "user", "content": "Keep the source unchanged. Use the exact recipient."}
        tool = {"role": "tool", "content": json.dumps({"stdout": "x" * 10000, "needs_approval": True,
                    "approval_id": "pending", "outcome": "UNKNOWN", "path": "/fixture/source", "stderr": "z" * 10000})}
        conversation = [original.copy(), tool] + [{"role": "tool", "content": "{}"}] * 20
        worker._shrink(conversation, force_local=True)
        shrunk = json.loads(tool["content"])
        self.assertEqual(conversation[0], original)
        self.assertTrue(shrunk["needs_approval"])
        self.assertEqual(shrunk["outcome"], "UNKNOWN")
        self.assertEqual(shrunk["approval_id"], "pending")
        self.assertEqual(shrunk["path"], "/fixture/source")
        self.assertLess(len(tool["content"]), 2000)

    async def test_crew_atomic_save_preserves_previous_checkpoint(self):
        from app.services import crew
        previous = '[{"id":"old"}]'
        crew.CREW_FILE.write_text(previous)
        with patch.object(crew, "_jobs", {}), patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                crew._save()
        self.assertEqual(crew.CREW_FILE.read_text(), previous)
        self.assertEqual(list(Path(TMP.name).glob(".crew.json.*.tmp")), [])
        crew.CREW_FILE.unlink()

    async def test_long_crew_job_is_not_pruned_on_restart(self):
        from app.services import crew
        jobs = {"old": {"id": "old", "started": 0, "status": "waiting", "session_id": "session",
                        "approvals": {"pending": {"state": "waiting"}}, "questions": {}}}
        jobs.update({str(i): {"id": str(i), "started": i + 1, "status": "done"} for i in range(70)})
        with patch.object(crew, "_jobs", jobs):
            crew._save()
        with patch.object(crew, "_jobs", {}), patch.object(crew, "_by_session", {}):
            crew._load()
            self.assertEqual(crew._jobs["old"]["status"], "interrupted")
            self.assertEqual(crew._jobs["old"]["approvals"]["pending"]["state"], "waiting")
            self.assertEqual(crew._by_session["session"], "old")
        crew.CREW_FILE.unlink()

    async def test_crew_concurrent_start_reserves_before_await(self):
        from app.services import crew, agent_clis
        def prepare(*args):
            time.sleep(.02)
            return None
        with patch.object(crew, "_jobs", {}), patch.object(crew, "_load"), \
             patch.object(crew, "_save"), patch.object(agent_clis, "which", return_value="fake"), \
             patch.object(agent_clis, "_folder", return_value=TMP.name), \
             patch.object(crew.worktrees, "create", prepare), \
             patch.object(crew, "_launch", side_effect=lambda jid, co: co.close()):
            results = await asyncio.gather(*(crew.start("fixture", agent="claude") for _ in range(6)), return_exceptions=True)
            self.assertEqual(sum(isinstance(r, crew.CrewError) for r in results), 2)
            self.assertEqual(len(crew._jobs), 4)

    async def test_crew_stop_cancels_startup(self):
        from app.services import crew
        j = {"id": "fixture", "status": "starting", "agent": "muse", "approvals": {}, "questions": {},
             "steps": [], "started": time.time()}
        with patch.object(crew, "_jobs", {"fixture": j}), patch.object(crew, "_save"), patch.object(crew, "_emit"):
            startup = asyncio.create_task(asyncio.sleep(60))
            crew._starts["fixture"] = startup
            result = await crew.stop("fixture")
            self.assertTrue(startup.cancelled())
            self.assertEqual(result["status"], "stopped")
            crew._starts.clear()

    async def test_stop_during_worktree_creation_waits_and_removes_after_restart(self):
        from app.services import crew, agent_clis, worktrees
        with tempfile.TemporaryDirectory(dir=TMP.name) as folder:
            repo = Path(folder) / "repo"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "seed.txt").write_text("unchanged")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c",
                            "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True)
            entered, finish = threading.Event(), threading.Event()
            created = []
            original_create = worktrees.create
            def slow_create(*args):
                entered.set()
                if not finish.wait(5):
                    raise RuntimeError("fixture timed out")
                wt = original_create(*args)
                created.append(wt)
                return wt
            with patch.object(crew, "_jobs", {}), patch.object(crew, "_by_session", {}), \
                 patch.object(worktrees, "ROOT", Path(folder) / "copies"), \
                 patch.object(worktrees, "create", slow_create), \
                 patch.object(agent_clis, "which", return_value="fake"), \
                 patch.object(agent_clis, "_folder", return_value=str(repo)), \
                 patch.object(crew, "_launch") as launch:
                crew.CREW_FILE.unlink(missing_ok=True)
                starting = asyncio.create_task(crew.start("fixture", agent="claude"))
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                jid = next(iter(crew._jobs))
                stopping = asyncio.create_task(crew.stop(jid))
                await asyncio.sleep(.02)
                self.assertFalse(stopping.done())
                self.assertEqual(crew._jobs[jid]["status"], "starting")
                finish.set()
                result = await asyncio.wait_for(stopping, 5)
                with self.assertRaises(crew.CrewError):
                    await starting
                launch.assert_not_called()
                self.assertEqual(result["status"], "stopped")
                wt = created[0]
                self.assertFalse(Path(wt["path"]).exists())
                self.assertFalse(worktrees._out(["rev-parse", "--verify", "--quiet",
                                                 f"refs/heads/{wt['branch']}"], str(repo)))
                self.assertNotIn(wt["path"], worktrees._out(["worktree", "list", "--porcelain"], str(repo)))
                crew._jobs.clear()
                crew._load()
                saved = crew.job(jid)
                self.assertEqual(saved["status"], "stopped")
                self.assertEqual(saved["worktree"]["closed"], "discarded")
                self.assertFalse(Path(saved["worktree"]["path"]).exists())
                self.assertEqual((repo / "seed.txt").read_text(), "unchanged")
                crew.CREW_FILE.unlink()

    async def test_legacy_workspace_is_never_discarded_as_new(self):
        from app.services import crew, worktrees
        with patch.object(worktrees, "remove") as remove:
            await crew._discard_unstarted_workspace({"id": "legacy", "worktree": {"path": "existing"}})
            remove.assert_not_called()

    async def test_lightweight_keeps_relay_and_supervision(self):
        with patch.object(run_mode, "lightweight_active", return_value=True):
            self.assertFalse(run_mode.skip("muse_app"))
            self.assertFalse(run_mode.skip("partner_watchdog"))
            self.assertTrue(run_mode.skip("autoconnect"))

    async def test_team_approval_stops_further_tools(self):
        from app.services.llm import llm_service
        async def model(**kwargs):
            yield {"type": "deliberation_complete", "tool_calls": [
                {"id": str(i), "function": {"name": "computer_read_file", "arguments": '{"path":"/fixture"}'}}
                for i in range(2)]}
        execute = AsyncMock(return_value={"needs_approval": True, "message": "Fixture approval", "approval_id": "pending"})
        events = []
        with patch.object(team, "tiers", AsyncMock(return_value={"standard": {"model": "fixture", "provider": "local",
                                                                           "where": "this computer"}})), \
             patch.object(team, "endpoint", return_value=None), \
             patch.object(llm_service, "deliberate_stream", model):
            result = await team.run_subagent("fixture", "Read a file", "standard", execute, events.append)
        self.assertEqual(execute.await_count, 1)
        self.assertFalse(result["success"])
        self.assertEqual(result["approval"]["approval_id"], "pending")
        self.assertEqual(events[-1]["status"], "waiting")


unittest.main()
