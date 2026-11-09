"""Argon acceptance suite (2026-11-08): D1 completion-fabrication fixes,
2b mission-killer fixes, and 2c 8GB-catalog / unmanaged-resident budget rules.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("XOD_SPEECH", "off")
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_argon_d1_"))
os.environ.update({
    "HOME": str(TMP),
    "XOD_DATA_DIR": str(TMP / "data"),
    "XOD_NO_UPDATE_CHECK": "1",
})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from app.agent import worker  # noqa: E402
from app.agent.researcher import AgenticResearcher  # noqa: E402
from app.services import agent_clis, agent_log, keychain, local_engine, missions as M, model_budget as MB, ollama_context, orchestrator as O, task_queue as TQ  # noqa: E402
from app.services.filesystem import filesystem_service  # noqa: E402
from app.services.system_control import system_control_service  # noqa: E402

M.DB = TMP / "data" / "xod.db"
TQ.DB_PATH = M.DB
TQ.SHOTS_DIR = TMP / "shots"
agent_log.DB = M.DB
M._spawn = lambda mid: None

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok)))
    status = "PASS" if ok else "FAIL"
    print(f"  {status}  {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


async def test_d1_worker_done_check_timeout() -> None:
    print("1. D1 Run 1 fix: worker.done_check never certifies completion on LLM timeout/error")
    orig_chat = worker.llm_service.chat_completion

    async def _timeout_chat(*args, **kwargs):
        raise httpx.ReadTimeout("timed out after 100s under swap pressure")

    worker.llm_service.chat_completion = _timeout_chat
    try:
        log = [{"name": "xod_readiness", "args": {}, "fp": "ready", "failed": False, "out": "macOS; brew; python3", "exit": 0, "new": True}]
        verdict = await worker.done_check("Install ripgrep and verify rg --version", log, "Done.", kind="computer")
        check("done_check returns done=False when verification model call times out",
              verdict.get("done") is False and verdict.get("checker_error") is True,
              str(verdict))
    finally:
        worker.llm_service.chat_completion = orig_chat


async def test_d1_approval_hold_pauses_task() -> None:
    print("2. D1 Run 0 fix: unexecuted approval cards pause tasks in waiting (never done)")
    q = TQ.TaskQueue()
    orig_rs = TQ.TaskQueue._stream

    async def _fake_approval_stream(self, task_id, message, steps, system_extra=""):
        agent_log.approval_opened(f"card-{task_id}", "sentinel", "Terminal", "Run privileged install", "sudo make install")
        self._last_stream_meta[task_id] = {
            "errors": [],
            "held_approval": {"needs_approval": True, "tool": "computer_run_command", "message": "Needs your OK to run sudo make install"},
            "duration_ms": 12,
        }
        return "**Waiting for your OK** on the card above.", steps + 1, []

    TQ.TaskQueue._stream = _fake_approval_stream
    try:
        t = q.create("Build and install the CLI tool", origin="user")
        await q._run(t["id"])
        after = q.get(t["id"])
        check("classic task with held approval pauses in status='waiting', waiting_kind='approval'",
              after["status"] == "waiting" and after["waiting_kind"] == "approval",
              f"status={after['status']} waiting_kind={after.get('waiting_kind')}")
        # Now cancel the task and verify question/approval lifecycle cleanup
        await q.cancel(t["id"])
        cancelled = q.get(t["id"])
        open_cards = agent_log.waiting_approvals_for(f"task:{t['id']}")
        check("cancelling waiting task clears waiting_kind, waiting_detail, pending_json, and agent_approvals",
              cancelled["status"] == "cancelled"
              and cancelled.get("waiting_kind") is None
              and cancelled.get("waiting_detail") is None
              and not open_cards,
              f"cancelled={cancelled} open_cards={open_cards}")
    finally:
        TQ.TaskQueue._stream = orig_rs


async def test_d1_stream_error_never_fabricates_done() -> None:
    print("3. D1 Run 1 fix: stream error with zero output fails verification instead of closing as Done")
    q = TQ.TaskQueue()
    orig_rs = TQ.TaskQueue._stream

    async def _fake_errored_stream(self, task_id, message, steps, system_extra=""):
        self._last_stream_meta[task_id] = {
            "errors": ["ollama stopped responding (no output for 100s)."],
            "held_approval": None,
            "duration_ms": 100000,
        }
        return "", steps, []

    TQ.TaskQueue._stream = _fake_errored_stream
    try:
        t = q.create("Generate project report", origin="user")
        await q._run(t["id"])
        after = q.get(t["id"])
        check("task with model stream error and empty output finishes as 'failed', never 'done'",
              after["status"] == "failed" and "model stream error" in (after.get("result") or ""),
              f"status={after['status']} result={after.get('result')}")
    finally:
        TQ.TaskQueue._stream = orig_rs


async def test_d1_timeout_vs_down_distinction() -> None:
    print("4. D1 Run 1 fix: ollama_context.status distinguishes ReadTimeout (busy) from ConnectError (down)")
    orig_client = ollama_context.httpx.AsyncClient

    class _TimeoutClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url, **kw):
            raise httpx.ReadTimeout("timed out reading /api/tags")

    ollama_context.httpx.AsyncClient = _TimeoutClient
    try:
        st = await ollama_context.status("http://127.0.0.1:11434/v1", "qwen2.5-coder:7b")
        check("ReadTimeout reports running=True and 'busy or slow', not 'Ollama isn't running'",
              st.get("running") is True and "busy" in (st.get("problem") or "") and "isn't running" not in (st.get("problem") or ""),
              str(st))
    finally:
        ollama_context.httpx.AsyncClient = orig_client


async def test_2b_blocked_step_continuation_and_question_cleanup() -> None:
    print("5. Section 2b fixes: blocked-state DAG continuation + wedged question cleanup")
    q = TQ.TaskQueue()
    # Create a real task in `waiting` state with a question, then trigger mission `_fail_paused_wedge`
    waiting_task = q.create("Ask a clarifying question", origin="user")
    q._update(
        waiting_task["id"],
        status="waiting",
        waiting_kind="question",
        waiting_detail={"message": "Which port should I use?"},
        now="Needs your answer",
    )
    agent_log.approval_opened(f"q-{waiting_task['id']}", "question", "Xod", "Which port?", "Port number")

    class _HybridQueue:
        def __init__(self, real_q):
            self.real_q = real_q
            self.created = []
            self.behaviors = [
                # Step 1: returns blocked self-report
                {"status": "done", "result": "I could not do this step: I have no tools to access the external vault."},
                # Step 3 (independent): succeeds
                {"status": "done", "result": "Created independent summary."},
            ]

        def create(self, request, title=None, origin=None):
            tid = f"task-dag-{len(self.created) + 1}"
            idx = len(self.created)
            self.created.append({"id": tid, "request": request, "title": title, "behavior": self.behaviors[idx]})
            return {"id": tid, "status": "queued", "title": title}

        def get(self, tid):
            for c in self.created:
                if c["id"] == tid:
                    return {"id": tid, "title": c["title"], "request": c["request"], **c["behavior"]}
            return self.real_q.get(tid)

        def clear_waiting(self, tid, reason="Task ended", status="failed"):
            return self.real_q.clear_waiting(tid, reason=reason, status=status)

    hq = _HybridQueue(q)
    plan = json.dumps({
        "subtasks": [
            {
                "title": "Fetch vault secret",
                "instruction": "Read the secret from /tmp/vault_secret.txt",
                "expected_effects": [{"kind": "create", "path": "/tmp/vault_secret.txt", "content": None}],
                "inputs": [{"source": "goal"}],
            },
            {
                "title": "Use vault secret",
                "instruction": "Use the secret from step 1",
                "expected_effects": [],
                "inputs": [{"source": "prior_step", "ref": "1"}],
            },
            {
                "title": "Independent health summary",
                "instruction": "Summarize local disk space",
                "expected_effects": [],
                "inputs": [{"source": "goal"}],
            },
        ]
    })

    async def _ask(_prompt):
        return plan

    runner = M.MissionRunner(queue=hq, ask=_ask, poll_sec=0.01)
    m = M.create_mission("DAG blocked continuation test", continue_on_failure=False)
    await runner.drive(m["id"])
    m_after = M.get_mission(m["id"])
    subs = m_after["subtasks"]
    check("Step 1 is blocked, dependent Step 2 is marked blocked, and independent Step 3 still ran and completed",
          subs[0]["status"] == "blocked"
          and subs[1]["status"] == "blocked"
          and subs[2]["status"] == "done"
          and len(hq.created) == 2,
          str([(s["title"], s["status"]) for s in subs]))

    # Now test _fail_paused_wedge clearing the underlying waiting task's question
    runner._fail_paused_wedge(
        m["id"],
        {"id": subs[0]["id"], "idx": 0, "title": "Wedged step", "task_id": waiting_task["id"]},
        q.get(waiting_task["id"]),
        waited=605.0,
        resume_failed=False,
    )
    cleared_task = q.get(waiting_task["id"])
    check("_fail_paused_wedge clears waiting_kind and waiting_detail on the underlying task when the mission step ends",
          cleared_task["status"] == "failed"
          and cleared_task.get("waiting_kind") is None
          and cleared_task.get("waiting_detail") is None,
          str(cleared_task))


def test_2b_pytest_shim_and_python_path() -> None:
    print("6. Section 2b fixes: search_path Python priority + pytest_shim execution")
    agent_clis._path_cache.update(at=0.0, path="")
    sp = agent_clis.search_path()
    first_dir = sp.split(os.pathsep)[0]
    check("search_path() places active Python bin directory ahead of /usr/bin",
          first_dir == str(Path(sys.executable).parent) or first_dir == str(Path(sys.executable).resolve().parent),
          f"first_dir={first_dir}")

    proj_dir = TMP / "pytest_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    test_file = proj_dir / "test_sample.py"
    test_file.write_text(
        "import pytest\n\n"
        "@pytest.fixture\n"
        "def base_val():\n"
        "    return 10\n\n"
        "@pytest.mark.parametrize('x,expected', [(1, 11), (2, 12)])\n"
        "def test_add(base_val, x, expected, tmp_path):\n"
        "    p = tmp_path / 'out.txt'\n"
        "    p.write_text(str(base_val + x))\n"
        "    assert int(p.read_text()) == expected\n"
        "    assert 0.1 + 0.2 == pytest.approx(0.3)\n\n"
        "def test_raises():\n"
        "    with pytest.raises(ValueError, match='bad value'):\n"
        "        raise ValueError('bad value here')\n",
        encoding="utf-8",
    )
    problem1 = TQ._check_run_claim({"command": "pytest -q", "cwd": proj_dir, "expect_exit": 0, "refused": None})
    problem2 = TQ._check_run_claim({"command": "python3 -m pytest -q", "cwd": proj_dir, "expect_exit": 0, "refused": None})
    check("_check_run_claim('pytest -q') succeeds via pytest_shim", problem1 is None, str(problem1))
    check("_check_run_claim('python3 -m pytest -q') succeeds via active Python + pytest_shim", problem2 is None, str(problem2))


def test_2b_write_verification_and_keychain_cooldown() -> None:
    print("7. Section 2b fixes: fsync + SHA-256 write verification & keychain negative-cache cooldown")
    target = TMP / "verified_write.txt"
    content = "verified content line 1\nline 2\n"
    expected_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    res = system_control_service.write_file(str(target), content)
    check("system_control.write_file verifies bytes on disk and returns sha256",
          res.get("success") is True and res.get("verified_on_disk") is True and res.get("sha256") == expected_sha,
          str(res))
    edit_res = system_control_service.edit_file(str(target), "line 2", "line 2 updated")
    check("system_control.edit_file verifies edited bytes on disk",
          edit_res.get("success") is True and edit_res.get("verified_on_disk") is True and "line 2 updated" in target.read_text(),
          str(edit_res))
    fs_target = TMP / "fs_verified.txt"
    fs_res = filesystem_service.write_file(str(fs_target), "hello fs")
    check("filesystem.write_file verifies bytes on disk and returns sha256",
          fs_res.get("success") is True and fs_res.get("verified_on_disk") is True,
          str(fs_res))

    # Keychain negative-cache cooldown test
    import keyring
    orig_get = keyring.get_password
    calls = {"n": 0}

    def _locked_keychain(service, name):
        calls["n"] += 1
        raise RuntimeError("Can't fetch password from system: (-25308, 'errSecInteractionNotAllowed')")

    keyring.get_password = _locked_keychain
    keychain.reset_cache()
    try:
        v1 = keychain.get_password("Xod API keys", "openai_api_key")
        v2 = keychain.get_password("Xod API keys", "anthropic_api_key")
        v3 = keychain.get_password("Xod API keys", "gemini_api_key")
        check("keychain negative-cache cooldown suppresses repeated securityd IPC calls after -25308",
              v1 is None and v2 is None and v3 is None and calls["n"] == 1,
              f"calls={calls['n']}")
        keychain.reset_cache()
        v4 = keychain.get_password("Xod API keys", "openai_api_key")
        check("keychain.reset_cache() clears the negative-cache cooldown immediately",
              v4 is None and calls["n"] == 2,
              f"calls={calls['n']}")
    finally:
        keyring.get_password = orig_get
        keychain.reset_cache()


async def test_2c_8gb_catalog_and_unmanaged_resident() -> None:
    print("8. Section 2c fixes: 8 GB 40%-cap-vs-catalog-pick resolution & unmanaged over-budget resident policy")
    rec_8gb_healthy = local_engine.recommend(8.0, available_gb=6.0)
    check("8 GB Mac with 6.0 GB free recommends Qwen3.5-4B with bounded 4k KV cache (3.05 GB <= 3.20 GB cap)",
          rec_8gb_healthy["id"] == "qwen3.5-4b"
          and rec_8gb_healthy["need_gb"] == 3.05
          and rec_8gb_healthy.get("max_kv_size") == 4096,
          str(rec_8gb_healthy))

    rec_8gb_tight = local_engine.recommend(8.0, available_gb=4.5)
    check("8 GB Mac with 4.5 GB free (where 3.05 GB would breach the 2.0 GB 25% floor) steers to Qwen3.5-2B (1.8 GB)",
          rec_8gb_tight["id"] == "qwen3.5-2b" and rec_8gb_tight.get("steered_from") == "qwen3.5-4b",
          str(rec_8gb_tight))

    rec_4gb = local_engine.recommend(4.0)
    check("4 GB machine flags cloud_only_recommended=True",
          rec_4gb.get("cloud_only_recommended") is True,
          str(rec_4gb))

    # Verify ModelBudget.check_load allows Qwen3.5-4B on an 8 GB machine by applying the bounded 3.05 GB footprint
    async def _empty_obs():
        return {"answering": {"provider": "", "model": ""}, "engines": {e: {"running": False, "models": []} for e in MB.ENGINES}}

    b8 = MB.ModelBudget(
        machine_fn=lambda: {"total_gb": 8.0, "available_gb": 6.0},
        observer=_empty_obs,
        cloud_fn=lambda: False,
        ledger_path=TMP / "b8.jsonl",
        queue_path=TMP / "b8-q.json",
    )
    d8 = await b8.check_load("builtin", "mlx-community/Qwen3.5-4B-MLX-4bit", 4.0, "test_8gb")
    check("ModelBudget.check_load allows Qwen3.5-4B on 8 GB RAM with bounded_kv_tokens=4096 (projected 3.05 GB <= 3.2 GB)",
          d8["allowed"] is True and d8["projected_gb"] == 3.05 and d8.get("bounded_kv_tokens") == 4096,
          str(d8))

    # Verify unmanaged over-budget resident (`bonsai` 7.94 GB on 16 GB Mac)
    async def _bonsai_obs():
        return {
            "answering": {"provider": "lm_studio", "model": "bonsai"},
            "engines": {
                "builtin": {"running": False, "models": []},
                "lm_studio": {"running": True, "models": [{"model": "bonsai", "gb": 7.94, "in_use": True}]},
                "ollama": {"running": False, "models": []},
            },
        }

    b16 = MB.ModelBudget(
        machine_fn=lambda: {"total_gb": 16.0, "available_gb": 7.5},
        observer=_bonsai_obs,
        cloud_fn=lambda: True,
        ledger_path=TMP / "b16.jsonl",
        queue_path=TMP / "b16-q.json",
    )
    await b16.reconcile()
    st16 = b16.budget_status()
    check("Unmanaged 7.94 GB 'bonsai' is flagged unmanaged_over_budget=True with tier2 lockout/reuse/cloud policy",
          st16["unmanaged_over_budget"] is True
          and st16["unmanaged_resident_gb"] == 7.94
          and st16["unmanaged_policy"] == "tier2_card_lockout_new_loads_reuse_or_cloud",
          str(st16))
    # Reusing already-resident bonsai takes 0 extra RAM and is allowed
    reuse_d = await b16.check_load("lm_studio", "bonsai", 7.94, "chat")
    # Loading a second model is refused and routed to cloud
    new_d = await b16.check_load("builtin", "mlx-community/Qwen3.5-4B-MLX-4bit", 4.0, "chat")
    check("While unmanaged 'bonsai' is over budget, reusing 'bonsai' is allowed (already_resident) while any new load is refused -> cloud",
          reuse_d["allowed"] is True and reuse_d.get("already_resident") is True
          and new_d["allowed"] is False and new_d["route"] == "cloud",
          f"reuse={reuse_d} new={new_d}")


async def main() -> int:
    await test_d1_worker_done_check_timeout()
    await test_d1_approval_hold_pauses_task()
    await test_d1_stream_error_never_fabricates_done()
    await test_d1_timeout_vs_down_distinction()
    await test_2b_blocked_step_continuation_and_question_cleanup()
    test_2b_pytest_shim_and_python_path()
    test_2b_write_verification_and_keychain_cooldown()
    await test_2c_8gb_catalog_and_unmanaged_resident()

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
