"""Deterministic regressions; all provider replies here are synthetic, not live proof."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = tempfile.TemporaryDirectory(prefix="xod-observability-")
os.environ.update(HOME=TMP.name, XDG_CONFIG_HOME=TMP.name, XDG_DATA_HOME=TMP.name,
                  XOD_DATA_DIR=TMP.name, PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
                  XOD_SPEECH="off", XOD_NO_UPDATE_CHECK="1")
for key in list(os.environ):
    if key.endswith(("_API_KEY", "_TOKEN", "_SECRET")):
        os.environ.pop(key, None)
from app import paths
paths.DEV_ENV_PATH = Path(TMP.name) / "absent"
from app.services import run_observability as obs, event_log, team, activity, agent_log
from app.services import llm
from app.agent import worker
import httpx


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        event_log.LOG.unlink(missing_ok=True)
        activity.origin.set("chat:synthetic")
        team.FILE.unlink(missing_ok=True)

    def events(self, **kwargs):
        return event_log.query(limit=1000, **kwargs)["events"]

    async def test_actual_llm_dispatch_records_retries_usage_and_request(self):
        sent = []

        async def handler(request):
            sent.append(json.loads(request.content))
            if len(sent) == 1:
                return httpx.Response(400, headers={"x-request-id": "req_retry"}, json={
                    "error": {"message": "use max_completion_tokens instead of max_tokens"}})
            return httpx.Response(200, headers={"x-request-id": "req_fixture"}, json={
                "model": "synthetic-model", "usage": {"prompt_tokens": 123, "completion_tokens": 9, "total_tokens": 132},
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "PRIVATE-ANSWER"}}]})

        factory = httpx.AsyncClient
        with patch.object(llm.httpx, "AsyncClient", side_effect=lambda **kw: factory(
                transport=httpx.MockTransport(handler), **kw)):
            with obs.scope(run_id="run1", worker_id="sub2"):
                reply = await llm.llm_service.chat_completion(
                    [{"role": "user", "content": "PRIVATE-PROMPT"}], endpoint_override={
                        "provider": "openai", "model": "synthetic-model", "base_url": "https://fixture.invalid/v1",
                        "api_key": "SYNTHETIC-KEY"}, max_tokens=100)
        self.assertEqual(reply["_usage"]["prompt_tokens"], 123)
        self.assertEqual(reply["_request_id"], "req_fixture")
        rows = self.events(run_id="run1", worker_id="sub2")
        final = next(e for e in rows if e["kind"] == "model.finished")
        self.assertEqual(final["payload"]["attempts"], 2)
        self.assertEqual(final["payload"]["response_model"], "synthetic-model")
        self.assertEqual(final["ids"]["chat_id"], "synthetic")
        self.assertTrue(final["ids"]["model_call_id"])
        self.assertEqual(self.events(run_id="run1", actor="another-agent"), [])
        text = json.dumps(rows)
        for private in ("PRIVATE-PROMPT", "PRIVATE-ANSWER", "SYNTHETIC-KEY", "fixture.invalid"):
            self.assertNotIn(private, text)

    async def test_streaming_provider_metadata_and_usage(self):
        chunks = [
            {"model": "synthetic-stream", "choices": [{"delta": {"content": "answer"}, "finish_reason": None}]},
            {"model": "synthetic-stream", "choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10}}]
        body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
        factory = httpx.AsyncClient
        with patch.object(llm.httpx, "AsyncClient", side_effect=lambda **kw: factory(
                transport=httpx.MockTransport(lambda request: httpx.Response(
                    200, headers={"x-request-id": "req_stream"}, text=body)), **kw)):
            events = [e async for e in llm.llm_service.deliberate_stream(
                [{"role": "user", "content": "secret input"}],
                endpoint_override={"provider": "openai", "model": "synthetic-stream",
                                   "base_url": "https://fixture.invalid/v1", "api_key": "synthetic"})]
        self.assertEqual(events[-1]["content"], "answer")
        final = self.events(kinds=["model.finished"])[-1]["payload"]
        self.assertEqual(final["usage"]["total_tokens"], 10)
        self.assertEqual(final["response_model"], "synthetic-stream")
        self.assertEqual(final["request_id"], "req_stream")

    async def test_error_event_is_not_success_and_scope_resets(self):
        @obs.observed_model("synthetic")
        async def broken():
            yield {"type": "error", "content": "PRIVATE-ERROR"}
        self.assertEqual(len([x async for x in broken()]), 1)
        self.assertEqual(self.events()[-1]["payload"]["outcome"], "error")
        self.assertEqual(obs.ids(), {})
        self.assertNotIn("PRIVATE-ERROR", json.dumps(self.events()))

    async def test_cancelled_model_is_recorded(self):
        waiting = asyncio.Event()

        @obs.observed_model("synthetic")
        async def wait():
            waiting.set()
            await asyncio.sleep(100)
        task = asyncio.create_task(wait())
        await waiting.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.events()[-1]["payload"]["outcome"], "cancelled")

    async def test_tool_ids_and_approval_without_payload(self):
        async def held(name, params):
            agent_log.step(name, {}, {"needs_approval": True})
            return {"needs_approval": True, "private": "PRIVATE-RESULT"}
        with obs.scope(run_id="r", worker_id="w"):
            await obs.execute_tool("synthetic_tool", {"secret": "PRIVATE-ARG"}, "call_1", held)
        rows = self.events(tool_call_id="call_1")
        self.assertEqual(rows[-1]["payload"]["outcome"], "waiting_for_approval")
        self.assertTrue(all(e["ids"]["worker_id"] == "w" for e in rows))
        self.assertNotIn("PRIVATE-", json.dumps(rows))

    async def test_tool_cancellation_does_not_claim_no_effect(self):
        async def cancelled(*args):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await obs.execute_tool("synthetic_tool", {}, "call2", cancelled)
        self.assertEqual(self.events()[-1]["payload"]["outcome"], "cancelled_unknown_effect")

    async def test_logging_failure_does_not_break_work(self):
        with patch.object(event_log, "emit", side_effect=OSError("fixture")):
            @obs.observed_model("synthetic")
            async def answer():
                return 42
            self.assertEqual(await answer(), 42)

    async def test_single_model_workers_explicit_and_leaf_cannot_recurse(self):
        from app.services import workers
        self.assertFalse(team.load()["same_model_workers"])
        team.save(same_model_workers=True)
        self.assertTrue(await team.team_on())
        with self.assertRaises(ValueError):
            team.save(same_model_workers="true")
        token = workers.envelope.set("read_only")
        try:
            self.assertFalse(await team.team_on())
        finally:
            workers.envelope.reset(token)
        team.save(enabled=False)
        self.assertFalse(await team.team_on())

    async def test_fleet_queue_correlation_and_bounded_concurrency(self):
        active = peak = 0

        async def tiers():
            return {"standard": {"where": "online", "model": "synthetic", "provider": "openai"}}

        async def fake_worker(label, *args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(.02)
                return {"agent": label, "success": True, "summary": "fixture"}
            finally:
                active -= 1
        team.save(max_parallel=2)
        with obs.scope(run_id="batch-run"), patch.object(team, "tiers", tiers), patch.object(team, "run_subagent", fake_worker):
            events = [e async for e in team.delegate_many([{"task": "PRIVATE-BRIEF"}] * 3, None)]
        self.assertEqual(peak, 2)
        rows = self.events(run_id="batch-run", kinds=["worker"])
        done = [e for e in rows if e["kind"] == "worker.finished"]
        self.assertEqual(len(done), 3)
        self.assertEqual(len({e["ids"]["batch_id"] for e in done}), 1)
        batch_id = done[0]["ids"]["batch_id"]
        self.assertEqual(len(self.events(batch_id=batch_id, kinds=["fleet"])), 2)
        self.assertEqual({e["ids"]["worker_id"] for e in done}, {"sub1", "sub2", "sub3"})
        self.assertTrue(all(e["payload"]["execution_ms"] is not None for e in done))
        self.assertNotIn("PRIVATE-BRIEF", json.dumps(rows))

    async def test_fleet_cancellation_settles_workers(self):
        async def tiers():
            return {"standard": {"where": "online", "model": "synthetic", "provider": "openai"}}
        async def wait(*args):
            await asyncio.sleep(100)
        with patch.object(team, "tiers", tiers), patch.object(team, "run_subagent", wait):
            stream = team.delegate_many([{"task": "fixture"}] * 3, None)
            await anext(stream)
            await anext(stream)
            await stream.aclose()
        done = self.events(kinds=["worker.finished"])
        self.assertEqual(len(done), 3)
        self.assertTrue(all(e["payload"]["outcome"] == "cancelled" for e in done))

    async def test_followup_retains_late_original_constraint(self):
        original = "Original instruction\n" + "operational detail " * 180 + "\nPROJECT=CEDAR; NO EXTERNAL CHANGES"
        messages = [{"role": "user", "content": original}, {"role": "assistant", "content": "done"},
                    {"role": "user", "content": "Correction: keep all constraints"}]
        history = worker._conversation_history(messages, force_local=False)
        self.assertEqual(history[0]["content"], original)

    async def test_history_budget_is_explicit_and_preserves_ends(self):
        content = "START" + "x" * 100000 + "END"
        result = worker._conversation_history([{"role": "user", "content": content},
                                               {"role": "user", "content": "correction"}], force_local=True)
        self.assertLessEqual(len(result[0]["content"]), 12000)
        self.assertTrue(result[0]["content"].startswith("START"))
        self.assertTrue(result[0]["content"].endswith("END"))
        self.assertIn("excerpt omitted", result[0]["content"])
        self.assertEqual(self.events()[-1]["kind"], "context.history_bounded")

    async def test_tool_compaction_preserves_state_and_is_observable(self):
        convo = [{"role": "system", "content": "original"}, {"role": "user", "content": "instruction"}]
        convo += [{"role": "tool", "content": json.dumps({
            "success": False, "approval_id": "hold1", "job_id": "job1", "needs_approval": True,
            "body": "x" * 10000})} for _ in range(18)]
        worker._shrink(convo, force_local=False)
        self.assertEqual(convo[1]["content"], "instruction")
        compact = json.loads(convo[2]["content"])
        self.assertEqual(compact["approval_id"], "hold1")
        self.assertTrue(compact["needs_approval"])
        self.assertLess(self.events()[-1]["payload"]["after_chars"], self.events()[-1]["payload"]["before_chars"])

    async def test_cloud_retention_uses_configured_provider_without_private_method(self):
        with patch.object(llm.llm_service, "get_endpoint_info", return_value={"provider": "openai"}):
            self.assertEqual(worker._worker_context_limits()[0], 14)

    async def test_detail_exposes_payload_free_spans(self):
        from app.services import agent_page
        with obs.scope(run_id="detail-run"):
            obs.emit("run.started")
        detail = agent_page.detail("chat:synthetic")
        self.assertEqual(detail["observability"][-1]["ids"]["run_id"], "detail-run")

    async def test_local_worker_execution_evidence_reaches_existing_checks(self):
        result = {"agent": "sub1", "evidence": [
            {"tool": "computer_read_file", "ok": True, "summary": "item,units,price\na1,3,5",
             "args": {"filepath": "/scratch/alpha.csv"}, "source": "local_tool_execution"},
            {"tool": "computer_write_file", "ok": True, "summary": "Successfully written",
             "args": {"filepath": "/scratch/alpha.json"}, "source": "local_tool_execution"}]}
        logs = worker._delegated_tool_log(result)
        self.assertEqual(worker._remaining_from_log(
            "Read alpha.csv and write alpha.json.", logs), ([], []))
        shown = worker._steps_text(logs)
        self.assertIn("a1,3,5", shown)
        self.assertIn("not independent verification", shown)

    async def test_worker_report_alone_is_not_tool_evidence(self):
        result = {"agent": "outside", "success": True, "summary": "I read and wrote everything.",
                  "evidence": [{"tool": "computer_write_file", "ok": True, "summary": "done"}]}
        self.assertEqual(worker._delegated_tool_log(result), [])


if __name__ == "__main__":
    unittest.main()
