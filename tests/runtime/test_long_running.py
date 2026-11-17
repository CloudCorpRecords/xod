"""Exercise real worker/fleet loops with scripted model turns and scratch files.

No provider calls. Long step sequences are not a wall-clock soak or a model
accuracy score. Each invocation uses disposable configuration and files.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
STATE = tempfile.TemporaryDirectory(prefix="xod-long-running-")
os.environ.update(XOD_DATA_DIR=STATE.name, HOME=STATE.name,
                  XDG_CONFIG_HOME=str(Path(STATE.name) / "config"), XOD_SPEECH="off",
                  PYTHON_KEYRING_BACKEND="tests.support.filekeyring.FileKeyring")
from app.agent import worker
from app.services import team
from app.services.llm import llm_service

TIER = {"model": "scripted-fixture", "provider": "local", "where": "this computer"}
PROFILE = os.environ.get("XOD_TEST_CONTEXT_PROFILE", "local")
if PROFILE not in ("correctness", "local", "cloud"):
    raise ValueError("XOD_TEST_CONTEXT_PROFILE must be correctness, local or cloud")


def call(cid, **args):
    return {"id": cid, "type": "function", "function": {
        "name": "computer_read_file", "arguments": json.dumps(args)}}


def validate_transcript(messages):
    """Every assistant call must have exactly one matching tool response."""
    outstanding = set()
    for message in messages:
        if message.get("role") == "assistant" and message.get("tool_calls"):
            if outstanding:
                raise AssertionError(f"Unanswered tool calls: {outstanding}")
            ids = [c["id"] for c in message["tool_calls"]]
            if len(ids) != len(set(ids)):
                raise AssertionError("Duplicate tool call IDs")
            outstanding.update(ids)
        elif message.get("role") == "tool":
            cid = message["tool_call_id"]
            if cid not in outstanding:
                raise AssertionError(f"Orphan or duplicate tool result: {cid}")
            outstanding.remove(cid)
            # All tools in this suite return structured results.
            json.loads(message["content"])
    if outstanding:
        raise AssertionError(f"Unanswered tool calls: {outstanding}")


class LongRunning(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        production_limits = worker._worker_context_limits
        def limits(force_local=None):
            if PROFILE == "correctness":
                # Test-only generous envelope, independent of install/provider.
                # This is a character ceiling, NOT a provider token-limit claim.
                return 1000, 1_000_000, 1_000_000, 10000, 10000
            return production_limits(force_local=PROFILE == "local")
        self.limit_patch = patch.object(worker, "_worker_context_limits", limits)
        self.limit_patch.start()
        self.addCleanup(self.limit_patch.stop)

    async def test_parent_45_steps_keeps_structured_results_and_final_correction(self):
        turns = 0
        observations = []
        with tempfile.TemporaryDirectory(dir=STATE.name) as folder:
            source = Path(folder) / "ledger.json"
            source.write_text(json.dumps({"revision": 1, "total": 142}))

            async def execute(name, args):
                self.assertEqual(name, "computer_read_file")
                self.assertEqual(Path(args["path"]), source)
                if len(observations) == 25:
                    source.write_text(json.dumps({"revision": 2, "total": 154}))
                actual = json.loads(source.read_text())
                observations.append(actual)
                return {"stdout": "large fixture output\n" * 1600, "success": True,
                        "path": str(source), "outcome": "verified", "result": actual}

            async def model(**kwargs):
                nonlocal turns
                validate_transcript(kwargs["messages"])
                self.assertIn("not a queue of commands to replay", kwargs["messages"][0]["content"])
                self.assertTrue(any(m.get("content") == "Keep project Cedar. Delegate three regions."
                                    for m in kwargs["messages"]))
                tool_messages = [m for m in kwargs["messages"] if m.get("role") == "tool"]
                if tool_messages:
                    last = json.loads(tool_messages[-1]["content"])
                    self.assertEqual(last["result"], observations[-1])
                    self.assertEqual(last["path"], str(source))
                    if PROFILE == "correctness":
                        self.assertNotIn("_xod_context", last)
                    else:
                        self.assertTrue(last["_xod_context"]["reread_required"])
                turns += 1
                if turns <= 45:
                    yield {"type": "deliberation_complete", "tool_calls": [
                        call(f"read-{turns}", path=str(source))]}
                else:
                    self.assertEqual(observations[-1], {"revision": 2, "total": 154})
                    yield {"type": "deliberation_complete", "content": "Latest total: 154."}

            with patch.object(worker, "MAX_STEPS", 50), \
                 patch.object(worker, "readiness", AsyncMock(return_value={"blockers": [], "notes": []})), \
                 patch.object(worker, "_ready_text", return_value="fixture"), \
                 patch.object(worker, "_say_ready", return_value="fixture"), \
                 patch.object(worker, "guidance", return_value=[]), \
                 patch.object(worker, "_remaining_from_log", return_value=([], [])), \
                 patch.object(worker, "done_check", AsyncMock(return_value={"done": True, "proof": "fixture readback"})), \
                 patch.object(team, "team_on", AsyncMock(return_value=False)), \
                 patch.object(llm_service, "deliberate_stream", model):
                history = [{"role": "user", "content": "Keep project Cedar. Delegate three regions."},
                           {"role": "assistant", "content": "The three regions are complete."},
                           {"role": "user", "content": "Read current ledger"}]
                events = [e async for e in worker.stream("Read current ledger", history,
                          {"goal": "Read current ledger", "kind": "computer"}, execute)]
            self.assertEqual(len(observations), 45)
            self.assertTrue(any("154" in e.get("content", "") for e in events))
            self.assertEqual(json.loads(source.read_text())["total"], 154)

    async def test_fleet_worker_preserves_failure_and_nested_state_after_many_turns(self):
        turns = 0
        async def execute(name, args):
            return {"stdout": "x" * 32000, "success": False, "exit_code": 9,
                    "error": "fixture failure", "result": {"path": "/scratch/source", "outcome": "UNKNOWN"}}

        async def model(**kwargs):
            nonlocal turns
            validate_transcript(kwargs["messages"])
            for message in kwargs["messages"]:
                if message.get("role") == "tool":
                    result = json.loads(message["content"])
                    self.assertFalse(result["success"])
                    self.assertEqual(result["exit_code"], 9)
                    self.assertEqual(result["result"]["outcome"], "UNKNOWN")
            turns += 1
            if turns <= 24:
                yield {"type": "deliberation_complete", "tool_calls": [call(f"sub-{turns}", path="/scratch/source")]}
            else:
                yield {"type": "deliberation_complete", "content": "Stopped: source could not be read."}
        with patch.object(team, "SUB_STEPS", 26), \
             patch.object(team, "tiers", AsyncMock(return_value={"standard": TIER})), \
             patch.object(team, "endpoint", return_value=None), \
             patch.object(worker, "guidance", return_value=[]), \
             patch.object(llm_service, "deliberate_stream", model):
            result = await team.run_subagent("fixture", "Read source", "standard", execute, lambda e: None)
        self.assertEqual(result["steps"], 24)
        self.assertFalse(result["success"])

    async def test_parent_approval_after_context_pressure_never_runs_next_tool(self):
        turns = 0
        execute = AsyncMock(side_effect=[
            {"stdout": "x" * 30000, "success": True} for _ in range(12)
        ] + [{"stdout": "x" * 30000, "success": False, "needs_approval": True,
              "approval_id": "hold-99", "outcome": "UNKNOWN"}])
        async def model(**kwargs):
            nonlocal turns
            validate_transcript(kwargs["messages"])
            turns += 1
            calls = [call(f"a-{turns}", path="/scratch/source")]
            if turns == 13:
                calls.append(call("must-not-run", path="/scratch/other"))
            yield {"type": "deliberation_complete", "tool_calls": calls}
        with patch.object(worker, "readiness", AsyncMock(return_value={"blockers": [], "notes": []})), \
             patch.object(worker, "_ready_text", return_value="fixture"), \
             patch.object(worker, "_say_ready", return_value="fixture"), \
             patch.object(worker, "guidance", return_value=[]), \
             patch.object(team, "team_on", AsyncMock(return_value=False)), \
             patch.object(llm_service, "deliberate_stream", model):
            events = [e async for e in worker.stream("Read source", [],
                      {"goal": "Read source", "kind": "computer"}, execute)]
        self.assertEqual(execute.await_count, 13)
        holds = [e for e in events if e["type"] == "approval_hold"]
        self.assertEqual(holds[0]["data"]["approval_id"], "hold-99")
        self.assertFalse(any(e.get("id") == "must-not-run" and e["type"] == "tool_call" for e in events))

    async def test_failed_approval_result_is_waiting_not_an_ordinary_failure(self):
        execute = AsyncMock(return_value={"stdout": "x" * 30000, "success": False,
                                         "needs_approval": True, "approval_id": "hold-100"})
        async def model(**kwargs):
            yield {"type": "deliberation_complete", "tool_calls": [
                call("held", path="/scratch/source"), call("not-run", path="/scratch/other")]}
        events = []
        with patch.object(team, "tiers", AsyncMock(return_value={"standard": TIER})), \
             patch.object(team, "endpoint", return_value=None), \
             patch.object(llm_service, "deliberate_stream", model):
            result = await team.run_subagent("fixture", "Read source", "standard", execute, events.append)
        self.assertEqual(execute.await_count, 1)
        self.assertFalse(result["success"])
        self.assertTrue(result["needs_approval"])
        self.assertEqual(result["approval"]["approval_id"], "hold-100")
        self.assertEqual(events[-1]["status"], "waiting")

    async def test_cancel_worker_during_model_call_propagates_and_leaves_no_owned_task(self):
        entered = asyncio.Event()
        exited = asyncio.Event()
        async def model(**kwargs):
            entered.set()
            try:
                await asyncio.sleep(60)
                yield {}
            finally:
                exited.set()
        with patch.object(team, "tiers", AsyncMock(return_value={"standard": TIER})), \
             patch.object(team, "endpoint", return_value=None), \
             patch.object(llm_service, "deliberate_stream", model):
            running = asyncio.create_task(team.run_subagent("fixture", "Read source", "standard",
                                          AsyncMock(), lambda e: None))
            await asyncio.wait_for(entered.wait(), 2)
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
            self.assertTrue(exited.is_set())

    async def test_worker_cannot_claim_completion_when_every_tool_failed_but_can_recover(self):
        for recovered in (False, True):
            with self.subTest(recovered=recovered):
                turns = 0
                async def model(**kwargs):
                    nonlocal turns
                    validate_transcript(kwargs["messages"])
                    turns += 1
                    if turns <= 2:
                        yield {"type": "deliberation_complete", "tool_calls": [call(f"read-{turns}", path="/scratch/source")]}
                    else:
                        yield {"type": "deliberation_complete", "content": "Everything is finished."}
                execute = AsyncMock(side_effect=[
                    {"success": False, "error": "source unavailable"},
                    {"success": True, "content": "recovered"} if recovered else
                    {"success": False, "error": "still unavailable"},
                ])
                events = []
                with patch.object(team, "tiers", AsyncMock(return_value={"standard": TIER})), \
                     patch.object(team, "endpoint", return_value=None), \
                     patch.object(worker, "guidance", return_value=[]), \
                     patch.object(llm_service, "deliberate_stream", model):
                    result = await team.run_subagent("fixture", "Read source", "standard", execute, events.append)
                self.assertEqual(result["success"], recovered)
                self.assertEqual(events[-1]["status"], "completed" if recovered else "partial")
                self.assertEqual(result["verification"], "worker_report")


if __name__ == "__main__":
    print(f"Context profile: {PROFILE}; scripted model, no native device or provider calls.", flush=True)
    unittest.main()
