"""Long-mode tests: no credentials, real models or native-device permissions."""
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
TEMP = tempfile.TemporaryDirectory(prefix="xod-token-runs-")
os.environ.update(HOME=TEMP.name, XOD_DATA_DIR=TEMP.name,
                  XDG_CONFIG_HOME=str(Path(TEMP.name) / "config"), XOD_SPEECH="off",
                  PYTHON_KEYRING_BACKEND="tests.support.filekeyring.FileKeyring")
from app.agent import worker, long_context as context
from app.services import long_execution as execution, run_checkpoint as store
from app.services import activity, run_observability as obs, team
from app.services.llm import llm_service

INFO = {"provider": "openai", "model": "fixture-cloud", "base_url": "https://fixture.invalid/v1",
        "api_key": "DO-NOT-PERSIST-FIXTURE"}
CFG = {**execution.DEFAULTS, "enabled": True, "provider": "openai",
       "model": "fixture-cloud", "context_tokens": 200000}


def transcript(count=45, size=5500):
    messages = [{"role": "system", "content": "Do not send anything. Preserve project Cedar."},
                {"role": "user", "content": "Original requirements: exact recipients, never guess."}]
    for i in range(count):
        messages.extend([
            {"role": "assistant", "tool_calls": [{"id": str(i), "type": "function",
              "function": {"name": "computer_read_file", "arguments": '{"path":"/fixture"}'}}]},
            {"role": "tool", "tool_call_id": str(i), "content": json.dumps({
                "stdout": "x" * size, "success": True, "path": f"/fixture/{i}"})}])
        if i == 20:
            messages.append({"role": "user", "content": "Correction: ceiling is 42, not 90."})
    return messages


class LongExecution(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=TEMP.name)
        self.addCleanup(self.folder.cleanup)
        self.root = patch.object(store, "ROOT", Path(self.folder.name) / "checkpoints")
        self.root.start()
        self.addCleanup(self.root.stop)
        token = activity.origin.set("chat:token-test")
        self.addCleanup(activity.origin.reset, token)

    def run_state(self, **changes):
        run = execution.Run({**CFG, **changes}, "chat:token-test", {"goal": "Read source"})
        self.addCleanup(execution.active.reset, execution.active.set(run))
        return run

    def test_full_request_estimate_includes_tool_schemas_and_unicode(self):
        messages = [{"role": "user", "content": "雪" * 20}]
        plain = context.estimate(messages)
        self.assertGreater(context.estimate(messages, [{"description": "x" * 5000}]), plain + 4000)
        self.assertGreater(plain, 60)
        with self.assertRaises(context.ContextFull):
            context.estimate([{"role": "user", "content": [{"type": "image_url"}]}])

    def test_near_200k_compaction_preserves_all_instructions_and_pairs(self):
        messages = transcript()
        originals = [m for m in messages if m["role"] in ("system", "user")]
        archives = []
        compacted, event = context.compact(messages, [], 130000,
                                          lambda value: archives.append(value) or "/private/archive.json")
        self.assertGreater(event["before_estimated_tokens"], 200000)
        self.assertLessEqual(event["after_estimated_tokens"], 130000)
        for message in originals:
            self.assertIn(message, compacted)
        context.groups(compacted)
        self.assertEqual(archives[0], messages)
        self.assertIn("NOT a success verdict", compacted[0]["content"])

    def test_compaction_never_drops_approval_or_uncertain_exchange(self):
        for state in ({"needs_approval": True, "approval_id": "pending"},
                      {"outcome": "UNKNOWN", "path": "/uncertain/send"}):
            messages = transcript(10)
            messages[3]["content"] = json.dumps({"stdout": "x" * 5000, **state})
            result, _ = context.compact(messages, [], 30000, lambda _: "/private/archive")
            self.assertIn(messages[3], result)
            context.groups(result)

    def test_pinned_context_overflow_is_explicit_not_instruction_loss(self):
        with self.assertRaises(context.ContextFull):
            context.compact([{"role": "user", "content": "x" * 20000}], [], 8000, lambda _: "archive")

    def test_orphan_and_missing_tool_results_are_refused(self):
        for messages in ([{"role": "tool", "tool_call_id": "bad", "content": "{}"}],
                         transcript(1)[:-1]):
            with self.assertRaises(context.ContextFull):
                context.groups(messages)

    def test_atomic_checkpoint_and_private_permissions(self):
        run = self.run_state()
        run.persist()
        self.assertEqual(store.load(run.state["id"])["owner"], "chat:token-test")
        path = store.folder(run.state["id"]) / "checkpoint.json"
        before = path.read_bytes()
        with patch.object(store.os, "replace", side_effect=OSError("fixture disk full")):
            with self.assertRaises(OSError):
                run.persist()
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn(INFO["api_key"], path.read_text())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_only_one_process_lease_can_own_a_checkpoint(self):
        run = self.run_state()
        with store.lease(run.state["id"]):
            with self.assertRaises(ValueError):
                with store.lease(run.state["id"]):
                    pass
        with store.lease(run.state["id"]):
            pass  # OS lock was released.

    async def test_workers_and_retries_share_request_admission(self):
        run = self.run_state(max_requests=2)
        payload = {"messages": [{"role": "user", "content": "fixture"}], "max_tokens": 1024}
        async def attempt():
            self.assertIs(execution.active.get(), run)
            obs.request_started(INFO, payload, 0)
        await asyncio.gather(attempt(), attempt())
        with self.assertRaises(execution.Pause):
            await attempt()
        self.assertEqual(run.state["requests"], 2)
        self.assertEqual(store.load(run.state["id"])["requests"], 2)

    def test_input_budget_and_output_growth_are_refused_before_dispatch(self):
        run = self.run_state(max_input_tokens=1)
        payload = {"messages": [{"role": "user", "content": "fixture"}], "max_tokens": 1024}
        with self.assertRaises(execution.Pause):
            run.admit(INFO, payload)
        self.assertEqual(run.state["requests"], 0)
        run.cfg["max_input_tokens"] = 2000000
        payload["max_tokens"] = 16000
        with self.assertRaises(execution.Pause):
            run.admit(INFO, payload)

    def test_model_mismatch_and_local_endpoints_never_use_cloud_allowance(self):
        run = self.run_state()
        for info in ({**INFO, "model": "other-model"}, {**INFO, "base_url": "http://127.0.0.1:1234/v1"}):
            with self.assertRaises(execution.Pause):
                run.admit(info, {"messages": [], "max_tokens": 1024})
        self.assertEqual(run.state["requests"], 0)

    async def test_closed_run_refuses_late_child_dispatch(self):
        run = self.run_state()
        run.state["status"] = "completed"
        async def late_child():
            self.assertIs(execution.active.get(), run)
            with self.assertRaises(execution.Pause):
                run.admit(INFO, {"messages": [], "max_tokens": 1024})
            with self.assertRaises(execution.Pause):
                run.tool_started("computer_write_file", {}, "late")
        await asyncio.create_task(late_child())
        self.assertEqual(run.state["requests"], 0)

    async def test_storage_failure_before_effect_refuses_execution(self):
        run = self.run_state()
        execute = AsyncMock()
        with patch.object(store, "write", side_effect=OSError("fixture disk full")):
            with self.assertRaises(OSError):
                await obs.execute_tool("computer_write_file", {"path": "/fixture"}, "one", execute)
        execute.assert_not_awaited()
        with self.assertRaises(execution.Pause):
            run.check()

    async def test_storage_failure_after_effect_latches_uncertainty(self):
        run = self.run_state()
        execute = AsyncMock(return_value={"success": True})
        original = store.write
        calls = 0
        def write(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("fixture disk full")
            return original(*args)
        with patch.object(store, "write", write):
            with self.assertRaises(OSError):
                await obs.execute_tool("computer_write_file", {}, "one", execute)
        self.assertEqual(execute.await_count, 1)
        self.assertTrue(any(v["state"] == "uncertain" for v in run.state["pending"].values()))
        with self.assertRaises(execution.Pause):
            run.check()

    async def test_cancelled_effect_keeps_uncommitted_intent(self):
        run = self.run_state()
        entered = asyncio.Event()
        async def execute(*args):
            entered.set()
            await asyncio.sleep(60)
        task = asyncio.create_task(obs.execute_tool("computer_write_file", {}, "one", execute))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        saved = store.load(run.state["id"])
        self.assertEqual(next(iter(saved["pending"].values()))["state"], "started")

    def test_settings_reject_unknown_capacity_invalid_numbers_and_local_provider(self):
        from app.services import storage
        with patch.object(execution, "load", return_value=dict(execution.DEFAULTS)), \
             patch.object(storage, "set_setting") as save:
            for changes in (
                {"enabled": True, "provider": "openai", "model": "fixture-cloud"},
                {"enabled": True, "provider": "ollama", "model": "fixture-cloud", "context_tokens": 200000},
                {"max_steps": True}, {"max_requests": -1}, {"anything_else": 1},
                {"enabled": True, "provider": "openai", "model": "fixture-cloud", "context_tokens": 8192},
            ):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    execution.save(changes)
            save.assert_not_called()

    async def test_cross_chat_pending_and_completed_checkpoints_cannot_resume(self):
        run = execution.Run(CFG, "chat:token-test", {"goal": "Read source"})
        async def probe(*args):
            self.fail("Unsafe resume executed")
            yield {}
        for change in ({"owner": "chat:other"}, {"pending": {"effect": {"state": "started"}}},
                       {"status": "completed"}, {"status": "waiting_for_approval"}):
            run.state.update(owner="chat:token-test", pending={}, status="paused")
            run.state.update(change)
            run.persist()
            with patch.object(execution, "load", return_value=CFG), patch.object(execution, "endpoint", return_value=INFO):
                events = [e async for e in execution.managed(probe)("continue", [],
                          {"goal": "Read source", "checkpoint_id": run.state["id"]}, AsyncMock())]
            self.assertIn("Paused, not verified complete", events[0]["content"])

    async def test_hard_deadline_closes_waiting_model_generator(self):
        closed = asyncio.Event()
        async def probe(*args):
            try:
                await asyncio.sleep(60)
                yield {}
            finally:
                closed.set()
        with patch.object(execution, "load", return_value={**CFG, "max_seconds": .02}), \
             patch.object(execution, "endpoint", return_value=INFO):
            events = [e async for e in execution.managed(probe)(
                "fixture", [], {"goal": "Read source"}, AsyncMock())]
        self.assertTrue(closed.is_set())
        self.assertIn("time allowance", events[0]["content"])
        self.assertEqual(store.recent()[0]["status"], "paused")

    def test_resume_command_is_explicit_and_identifiers_are_validated(self):
        run_id = "a" * 32
        self.assertEqual(worker.route(f"resume checkpoint {run_id}", [])["checkpoint_id"], run_id)
        with self.assertRaises(ValueError):
            store.folder("../../private")

    def test_checkpoint_quota_failure_leaves_previous_state_readable(self):
        run = self.run_state()
        run.persist()
        with patch.object(store, "MAX_FILE_BYTES", 10):
            with self.assertRaises(OSError):
                store.write(run.state["id"], "checkpoint.json", {"too_big": "x" * 100})
        self.assertEqual(store.load(run.state["id"])["id"], run.state["id"])

    def test_process_crash_preserves_state_and_releases_lease(self):
        code = """
import os, sys
from pathlib import Path
from app.services import long_execution as execution, run_checkpoint as store
store.ROOT = Path(sys.argv[1])
cfg = {**execution.DEFAULTS, 'enabled': True, 'provider': 'openai',
       'model': 'fixture-cloud', 'context_tokens': 200000}
run = execution.Run(cfg, 'chat:token-test', {'goal': 'Recover checkpoint'})
with store.lease(run.state['id']):
    run.state.update(steps=2, requests=7)
    run.sync([{'role':'user', 'content':'Retain exact constraints'}], [])
    print(run.state['id'], flush=True)
    os._exit(23)
"""
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.folder.name,
               "XOD_DATA_DIR": self.folder.name, "XDG_CONFIG_HOME": self.folder.name,
               "XOD_SPEECH": "off", "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring"}
        result = subprocess.run([sys.executable, "-c", code, str(store.ROOT)],
                                cwd=Path(__file__).resolve().parents[2], env=env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 23)
        run_id = result.stdout.strip().splitlines()[-1]
        with store.lease(run_id):  # The crashed process cannot retain its OS lease.
            saved = store.load(run_id)
            resumed = execution.Run(CFG, "chat:token-test", {"goal": "Recover"}, saved)
            self.assertEqual(resumed.state["steps"], 2)
            self.assertEqual(resumed.state["requests"], 7)
            self.assertEqual(resumed.state["conversation"][0]["content"], "Retain exact constraints")
            self.assertGreaterEqual(resumed.elapsed, saved["elapsed_seconds"])

    def worker_patches(self, cfg, model):
        stack = ExitStack()
        for target, name, value in (
            (execution, "load", lambda: cfg), (execution, "endpoint", lambda: INFO),
            (worker, "readiness", AsyncMock(return_value={"blockers": [], "notes": []})),
            (worker, "_ready_text", lambda r: "fixture"), (worker, "_say_ready", lambda r: "fixture"),
            (worker, "guidance", lambda *a: []), (worker, "_remaining_from_log", lambda *a: ([], [])),
            (worker, "done_check", AsyncMock(return_value={"done": True, "proof": "fixture"})),
            (team, "team_on", AsyncMock(return_value=False)), (llm_service, "deliberate_stream", model),
        ):
            stack.enter_context(patch.object(target, name, value))
        return stack

    async def test_parent_can_continue_past_30_iterations_with_explicit_mode(self):
        turns = 0
        async def model(**kwargs):
            nonlocal turns
            turns += 1
            if turns <= 35:
                yield {"type": "deliberation_complete", "tool_calls": [{
                    "id": str(turns), "function": {"name": "computer_read_file", "arguments": '{"path":"/fixture"}'}}]}
            else:
                yield {"type": "deliberation_complete", "content": "Finished after 35 reads."}
        execute = AsyncMock(return_value={"success": True, "content": "fixture"})
        with self.worker_patches(CFG, model):
            events = [e async for e in worker.stream("Read source", [], {"goal": "Read source"}, execute)]
        self.assertEqual(execute.await_count, 35)
        self.assertTrue(any("Finished after 35" in e.get("content", "") for e in events))
        self.assertEqual(store.recent()[0]["status"], "completed")
        answer = next(e["content"] for e in events if "Finished after 35" in e.get("content", ""))
        decision = worker.route("continue", [{"role": "user", "content": "Read source"},
                                {"role": "assistant", "content": answer},
                                {"role": "user", "content": "continue"}])
        self.assertEqual(decision["checkpoint_id"], store.recent()[0]["id"])

    async def test_paused_checkpoint_restores_transcript_without_repeating_effects(self):
        cfg = {**CFG, "max_steps": 2}
        journal = Path(self.folder.name) / "effects.json"
        journal.write_text("[]")
        async def execute(name, args):
            records = json.loads(journal.read_text())
            records.append(args["content"])
            journal.write_text(json.dumps(records))
            return {"success": True, "path": str(journal)}
        async def model(**kwargs):
            # Infer progress from the RESTORED tool transcript, not a hidden counter.
            done = len([m for m in kwargs["messages"] if m.get("role") == "tool"])
            if done < 3:
                yield {"type": "deliberation_complete", "tool_calls": [{
                    "id": f"effect-{done}", "function": {"name": "computer_write_file",
                    "arguments": json.dumps({"filepath": str(journal), "content": str(done)})}}]}
            else:
                yield {"type": "deliberation_complete", "content": "All three effects recorded."}
        with self.worker_patches(cfg, model):
            first = [e async for e in worker.stream("Write records", [], {"goal": "Write records"}, execute)]
        self.assertIn("step allowance", first[-2]["content"])
        saved = store.recent()[0]
        self.assertEqual(json.loads(journal.read_text()), ["0", "1"])
        with self.worker_patches({**cfg, "max_steps": 5}, model):
            second = [e async for e in worker.stream("continue", [], {
                "goal": "Write records", "checkpoint_id": saved["id"]}, execute)]
        self.assertEqual(json.loads(journal.read_text()), ["0", "1", "2"])
        self.assertEqual(store.recent()[0]["steps"], 4)
        self.assertEqual(store.recent()[0]["status"], "completed")
        self.assertTrue(any("three effects" in e.get("content", "") for e in second))

    async def test_approval_and_uncertainty_stop_the_next_effect(self):
        for result in ({"success": False, "needs_approval": True, "approval_id": "held"},
                       {"success": False, "outcome": "UNKNOWN"}):
            async def model(**kwargs):
                yield {"type": "deliberation_complete", "tool_calls": [
                    {"id": str(i), "function": {"name": "computer_write_file",
                     "arguments": '{"filepath":"/fixture","content":"fixture"}'}} for i in range(2)]}
            execute = AsyncMock(return_value=result)
            with self.worker_patches(CFG, model):
                events = [e async for e in worker.stream("Write records", [], {"goal": "Write records"}, execute)]
            self.assertEqual(execute.await_count, 1)
            self.assertTrue(any(e["type"] == "approval_hold" for e in events) if result.get("needs_approval")
                            else any("uncertain outcome" in e.get("content", "") for e in events))


if __name__ == "__main__":
    unittest.main()
