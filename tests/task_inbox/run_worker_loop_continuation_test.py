"""Deterministic reproduction & regression test for Round 7:
worker / task loop continuation on self-declared incomplete checkpoints
(Shapes A, B, C from live re-grade 2).

Run: python tests/task_inbox/run_worker_loop_continuation_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services import workers as W  # noqa: E402
from app.agent import worker as worker_agent  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" [{detail}]" if detail and not ok else ""), flush=True)


def _insert_worker(wid: str, brief: str, env: str = "read_only"):
    c = W._conn()
    c.execute(
        "INSERT INTO workers (id, parent_id, parent_type, role, brief, envelope, session_id, state, created_at, last_activity_at, needs) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (wid, f"task-{uuid.uuid4().hex[:8]}", "task", "worker", brief, env, f"worker-session-{wid}", "running", 0.0, 0.0, "[]"),
    )
    c.commit()
    c.close()


async def case_c1_workers_run_continuation():
    print("== C1: workers._run continues a worker on a self-declared incomplete checkpoint ==", flush=True)
    tmp = Path(tempfile.mkdtemp(prefix="xod_cont_c1_"))
    (tmp / "directive.txt").write_text("CODE: ZULU-9\n", encoding="utf-8")
    (tmp / "notes.txt").write_text("Auxiliary notes.\n", encoding="utf-8")
    chosen = tmp / "chosen.txt"
    code_f = tmp / "code.txt"

    brief = W.render_brief(
        task=(
            f"Inspect {tmp / 'directive.txt'} and {tmp / 'notes.txt'}, then write "
            f"the chosen filename to {chosen} and the code to {code_f}."
        ),
        outcome=f"Both {chosen} and {code_f} are written and verified.",
        constraints=["Use file tools only."],
        facts=[],
        env="ro",
    )

    prompts_seen = []
    orig_agent = W.agent

    async def fake_agent(prompt: str, wid: str):
        prompts_seen.append(prompt)
        turn = len(prompts_seen)
        if turn == 1:
            yield {
                "type": "tool_result",
                "name": "computer_read_file",
                "data": {"success": True, "content": "CODE: ZULU-9\n"},
            }
            yield {
                "type": "content",
                "content": (
                    f"Read {tmp / 'directive.txt'}: code is ZULU-9.\n\n"
                    "**Not finished yet:** Provide a complete, untruncated directory listing. "
                    "Say **continue** and I'll keep going."
                ),
            }
            return
        # Turn 2: continuation prompt should enumerate remaining items/deliverables.
        chosen.write_text("directive.txt\n", encoding="utf-8")
        code_f.write_text("ZULU-9\n", encoding="utf-8")
        yield {
            "type": "tool_result",
            "name": "computer_read_file",
            "data": {"success": True, "content": "Auxiliary notes.\n"},
        }
        yield {
            "type": "tool_result",
            "name": "computer_write_file",
            "data": {"success": True, "filepath": str(chosen)},
        }
        yield {
            "type": "tool_result",
            "name": "computer_write_file",
            "data": {"success": True, "filepath": str(code_f)},
        }
        yield {
            "type": "content",
            "content": f"Read directive.txt and notes.txt; wrote {chosen} and {code_f} with ZULU-9.",
        }

    W.agent = fake_agent
    try:
        wid = f"wrk-{uuid.uuid4().hex[:8]}"
        _insert_worker(wid, brief)
        await W._run(wid, brief, "read_only")
        row = W.get(wid) or {}
        res = row.get("result") or {}
        check("C1: worker continued to turn 2 instead of stopping at turn 1",
              len(prompts_seen) == 2, f"turns={len(prompts_seen)}")
        check("C1: continuation prompt named remaining items/deliverables",
              len(prompts_seen) >= 2
              and "notes.txt" in prompts_seen[1]
              and "chosen.txt" in prompts_seen[1]
              and "code.txt" in prompts_seen[1],
              prompts_seen[1][:300] if len(prompts_seen) >= 2 else "no turn 2")
        check("C1: deliverables were written",
              chosen.exists() and code_f.exists(), f"chosen={chosen.exists()} code={code_f.exists()}")
        check("C1: worker ended done after completing remaining items",
              row.get("state") == "done" and res.get("outcome") == "done",
              f"state={row.get('state')} why={res.get('why_failed')}")
    finally:
        W.agent = orig_agent


async def case_c2_worker_stream_continuation():
    print("== C2: worker.stream does not stop on Not finished yet while brief items remain ==", flush=True)
    tmp = Path(tempfile.mkdtemp(prefix="xod_cont_c2_"))
    s16 = tmp / "shard-16.txt"
    s17 = tmp / "shard-17.txt"
    summary = tmp / "sweep_summary.txt"
    s16.write_text("clean\n", encoding="utf-8")
    s17.write_text("FLAG: ORCHID-7\n", encoding="utf-8")

    goal = (
        f"Inspect {s16} and {s17} for any flag, then write the final summary to {summary}."
    )

    orig_ready = worker_agent.readiness
    orig_delib = worker_agent.llm_service.deliberate_stream
    orig_done = worker_agent.done_check
    turn_idx = 0
    nudges = []

    async def fake_ready():
        return {
            "ok": True,
            "os": "win32",
            "shell": "powershell",
            "home": str(tmp),
            "package managers": ["winget"],
            "languages": ["python"],
            "online": True,
            "free_gb": 50,
            "admin": False,
            "model_tools": True,
            "blockers": [],
            "notes": [],
        }

    async def fake_delib(messages=None, tools=None, tool_choice=None, temperature=None, max_tokens=None):
        nonlocal turn_idx
        turn_idx += 1
        last_user = ""
        for m in reversed(messages or []):
            if m.get("role") == "user":
                last_user = m.get("content") or ""
                break
        if turn_idx == 1:
            yield {
                "type": "deliberation_complete",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "computer_read_file", "arguments": json.dumps({"filepath": str(s17)})},
                }],
                "content": "",
            }
        elif turn_idx == 2:
            # Model tries to stop with an incomplete checkpoint even though shard-16.txt and sweep_summary.txt remain.
            yield {
                "type": "deliberation_complete",
                "tool_calls": [],
                "content": "**Not finished yet:** shard-16.txt not checked yet. Say **continue** and I'll keep going.",
            }
        elif turn_idx == 3:
            nudges.append(last_user)
            yield {
                "type": "deliberation_complete",
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "computer_read_file", "arguments": json.dumps({"filepath": str(s16)})},
                    },
                    {
                        "id": "call_3",
                        "type": "function",
                        "function": {"name": "computer_write_file", "arguments": json.dumps({"filepath": str(summary), "content": "ORCHID-7 in shard-17.txt\n"})},
                    },
                ],
                "content": "",
            }
        else:
            yield {
                "type": "deliberation_complete",
                "tool_calls": [],
                "content": f"Checked {s16} and {s17}; found ORCHID-7 and wrote {summary}.",
            }

    async def fake_done(g, log, final, kind="computer"):
        # Even if done_check naively says done: True on turn 2, the loop must not stop on Not finished yet / remaining items.
        return {"done": True, "proof": "checked", "needs_person": False}

    async def fake_exec(name, args):
        if name == "computer_read_file":
            p = Path(args["filepath"])
            return {"success": True, "content": p.read_text(encoding="utf-8")}
        if name == "computer_write_file":
            p = Path(args["filepath"])
            p.write_text(args["content"], encoding="utf-8")
            return {"success": True, "filepath": str(p)}
        return {"success": False, "error": "unsupported"}

    worker_agent.readiness = fake_ready
    worker_agent.llm_service.deliberate_stream = fake_delib
    worker_agent.done_check = fake_done
    try:
        final_content = ""
        async for ev in worker_agent.stream(goal, [], {"goal": goal, "kind": "computer"}, fake_exec):
            if ev.get("type") == "content":
                final_content = ev.get("content") or ""
        check("C2: worker.stream did not exit on turn 2 incomplete checkpoint",
              turn_idx >= 4, f"turns={turn_idx}")
        check("C2: nudge enumerated remaining items (shard-16.txt / sweep_summary.txt)",
              bool(nudges) and "shard-16.txt" in nudges[0] and "sweep_summary.txt" in nudges[0],
              nudges[0][:250] if nudges else "no nudge")
        check("C2: deliverable sweep_summary.txt was written",
              summary.exists() and "ORCHID-7" in summary.read_text(encoding="utf-8"))
        check("C2: final report has no Not finished yet admission",
              "Not finished yet" not in final_content, final_content[:200])
    finally:
        worker_agent.readiness = orig_ready
        worker_agent.llm_service.deliberate_stream = orig_delib
        worker_agent.done_check = orig_done


async def case_c3_hard_failure_not_retried_and_exhausted_fails():
    print("== C3: real failure still fails without runaway; exhausted continuations still fail ==", flush=True)
    orig_agent = W.agent
    calls_c3a = 0

    async def fake_hard_fail(prompt: str, wid: str):
        nonlocal calls_c3a
        calls_c3a += 1
        yield {
            "type": "tool_result",
            "name": "computer_read_file",
            "data": {"success": False, "error": "File not found"},
        }
        yield {"type": "content", "content": "The file could not be found, so there is no status line."}

    W.agent = fake_hard_fail
    try:
        wid = f"wrk-{uuid.uuid4().hex[:8]}"
        brief = W.render_brief("Read missing.txt", "Status line returned", [], [], "read_only")
        _insert_worker(wid, brief)
        await W._run(wid, brief, "read_only")
        row = W.get(wid) or {}
        check("C3a: hard tool failure still fails promptly in 1 turn",
              row.get("state") == "failed" and calls_c3a == 1,
              f"state={row.get('state')} calls={calls_c3a}")
    finally:
        W.agent = orig_agent

    calls_c3b = 0

    async def fake_always_incomplete(prompt: str, wid: str):
        nonlocal calls_c3b
        calls_c3b += 1
        yield {"type": "tool_result", "name": "computer_list_directory", "data": {"success": True, "entries": ["a.txt"]}}
        yield {"type": "content", "content": "**Not finished yet:** a.txt remains. Say **continue** and I'll keep going."}

    W.agent = fake_always_incomplete
    try:
        wid = f"wrk-{uuid.uuid4().hex[:8]}"
        brief = W.render_brief("Read a.txt and write out.txt", "out.txt written", [], [], "read_only")
        _insert_worker(wid, brief)
        await W._run(wid, brief, "read_only")
        row = W.get(wid) or {}
        check("C3b: exhausted continuations still fail on incompletion gate",
              row.get("state") == "failed" and calls_c3b >= 2,
              f"state={row.get('state')} calls={calls_c3b}")
    finally:
        W.agent = orig_agent


async def main():
    await case_c1_workers_run_continuation()
    await case_c2_worker_stream_continuation()
    await case_c3_hard_failure_not_retried_and_exhausted_fails()
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n========== {passed}/{total} checks passed ==========")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
