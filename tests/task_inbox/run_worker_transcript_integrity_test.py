"""Reproduction & regression test for worker session transcript integrity
under parallel tool calls (Round 5, Tier 5 live re-grade defect).

Reproduces the live OpenAI 400 failure where a worker turn issues >4 parallel
tool calls (or stops early on approval/error) and leaves tool_call_ids
unanswered in the transcript sent to the next model call.

Checks:
  T1: worker.stream with 6 parallel tool calls (including an out-of-envelope
      refusal and a tool exception) records exactly one role='tool' response
      for every tool_call_id before the second model call.
  T2: worker.stream with 3 parallel tool calls where call 0 returns
      needs_approval=True still records role='tool' responses for calls 1 & 2.
  T3: llm.reconcile_tool_transcript repairs missing/duplicate/orphaned
      tool_call_ids before sending a transcript to the model API.

Run: python tests/task_inbox/run_worker_transcript_integrity_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_t5_transcript_"))
os.environ.update({
    "HOME": str(TMP),
    "XOD_DATA_DIR": str(TMP / "data"),
    "XOD_NO_UPDATE_CHECK": "1",
})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.agent import worker as W  # noqa: E402
from app.services import llm as LLM  # noqa: E402
from app.services import workers as WS  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def _tool_id_counts(messages):
    """Return (missing_ids, duplicate_ids, orphan_ids) across all assistant tool_calls."""
    missing, dupes, orphans = [], [], []
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            issued = [tc.get("id") for tc in m["tool_calls"] if tc.get("id")]
            seen = {}
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                tid = messages[j].get("tool_call_id")
                seen[tid] = seen.get(tid, 0) + 1
                j += 1
            for tid in issued:
                cnt = seen.get(tid, 0)
                if cnt == 0:
                    missing.append(tid)
                elif cnt > 1:
                    dupes.append(tid)
            for tid in seen:
                if tid not in issued:
                    orphans.append(tid)
            i = j
        else:
            if m.get("role") == "tool":
                orphans.append(m.get("tool_call_id"))
            i += 1
    return missing, dupes, orphans


async def case_t1_worker_parallel_six_calls():
    print("== T1: worker.stream with 6 parallel tool calls (4+ files, refusal, exception) ==", flush=True)
    orig_stream = LLM.llm_service.deliberate_stream
    orig_ready = W.readiness
    orig_check = W.done_check
    captured_turns = []

    async def fake_ready():
        return {"os": "windows", "shell": "powershell", "home": str(TMP), "package managers": [],
                "languages": [], "online": True, "free_gb": 50, "admin": False,
                "browser": True, "tools": [], "blockers": [], "notes": []}

    async def fake_check(goal, log, content, kind):
        return {"done": True, "proof": "all checked"}

    async def fake_stream(messages, **kwargs):
        captured_turns.append([dict(m) for m in messages])
        if len(captured_turns) == 1:
            calls = [
                {"id": f"call_p_{k}", "type": "function",
                 "function": {"name": "computer_read_file", "arguments": json.dumps({"filepath": f"/tmp/f{k}.txt"})}}
                for k in range(5)
            ]
            calls.append({
                "id": "call_p_5", "type": "function",
                "function": {"name": "computer_run_command", "arguments": json.dumps({"command": "ls"})},
            })
            yield {"type": "deliberation_complete", "content": "", "tool_calls": calls}
        else:
            yield {"type": "deliberation_complete", "content": "Finished reading all files.", "tool_calls": []}

    async def fake_exec(name, args):
        if args.get("filepath") == "/tmp/f2.txt":
            raise RuntimeError("boom on f2")
        if name == "computer_run_command":
            return {"success": False, "error": "outside_envelope", "outside_envelope": True}
        return {"success": True, "content": f"contents of {args.get('filepath')}"}

    W.readiness = fake_ready
    W.done_check = fake_check
    LLM.llm_service.deliberate_stream = fake_stream
    tok = WS.envelope.set("read_only")
    try:
        async for _ in W.stream("Read 6 files", [], {"goal": "Read 6 files", "kind": "computer"}, fake_exec):
            pass
    finally:
        WS.envelope.reset(tok)
        W.readiness = orig_ready
        W.done_check = orig_check
        LLM.llm_service.deliberate_stream = orig_stream

    check("T1: second model turn was reached", len(captured_turns) >= 2, f"turns={len(captured_turns)}")
    if len(captured_turns) >= 2:
        missing, dupes, orphans = _tool_id_counts(captured_turns[1])
        check("T1: zero missing tool_call_ids in transcript sent to 2nd model turn",
              not missing, f"missing={missing}")
        check("T1: zero duplicate or orphaned tool_call_ids",
              not dupes and not orphans, f"dupes={dupes} orphans={orphans}")


async def case_t2_worker_needs_approval_mid_parallel():
    print("== T2: worker.stream with needs_approval mid-parallel leaves no orphaned tool_call_ids ==", flush=True)
    orig_stream = LLM.llm_service.deliberate_stream
    orig_ready = W.readiness
    captured_convo = None

    async def fake_ready():
        return {"os": "windows", "shell": "powershell", "home": str(TMP), "package managers": [],
                "languages": [], "online": True, "free_gb": 50, "admin": False,
                "browser": True, "tools": [], "blockers": [], "notes": []}

    async def fake_stream(messages, **kwargs):
        nonlocal captured_convo
        captured_convo = messages
        yield {"type": "deliberation_complete", "content": "", "tool_calls": [
            {"id": "call_a0", "type": "function", "function": {"name": "computer_read_file", "arguments": '{"filepath": "/tmp/a0"}'}},
            {"id": "call_a1", "type": "function", "function": {"name": "computer_read_file", "arguments": '{"filepath": "/tmp/a1"}'}},
            {"id": "call_a2", "type": "function", "function": {"name": "computer_read_file", "arguments": '{"filepath": "/tmp/a2"}'}},
        ]}

    async def fake_exec(name, args):
        return {"needs_approval": True, "tool": name, "message": "Approve read"}

    W.readiness = fake_ready
    LLM.llm_service.deliberate_stream = fake_stream
    try:
        async for _ in W.stream("Read 3 files", [], {"goal": "Read 3 files", "kind": "computer"}, fake_exec):
            pass
    finally:
        W.readiness = orig_ready
        LLM.llm_service.deliberate_stream = orig_stream

    missing, dupes, orphans = _tool_id_counts(captured_convo or [])
    check("T2: all 3 parallel tool_call_ids answered even when call 0 pauses for approval",
          captured_convo is not None and not missing and not dupes and not orphans,
          f"missing={missing} dupes={dupes} orphans={orphans}")


def case_t3_reconcile_tool_transcript_unit():
    print("== T3: llm.reconcile_tool_transcript repairs missing, duplicate, and orphaned ids ==", flush=True)
    if not hasattr(LLM, "reconcile_tool_transcript"):
        check("T3: llm.reconcile_tool_transcript exists", False, "missing function")
        return
    broken = [
        {"role": "user", "content": "Check files"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "computer_read_file", "arguments": "{}"}},
            {"id": "t2", "type": "function", "function": {"name": "computer_read_file", "arguments": "{}"}},
            {"id": "t3", "type": "function", "function": {"name": "web_search", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "t1", "name": "computer_read_file", "content": '{"ok": true}'},
        {"role": "tool", "tool_call_id": "t1", "name": "computer_read_file", "content": '{"ok": true}'},
        {"role": "tool", "tool_call_id": "t_stray", "name": "web_search", "content": '{"ok": false}'},
        {"role": "user", "content": "Continue"},
    ]
    fixed = LLM.reconcile_tool_transcript(broken)
    missing, dupes, orphans = _tool_id_counts(fixed)
    check("T3: repaired transcript has zero missing, duplicate, or orphaned tool_call_ids",
          not missing and not dupes and not orphans,
          f"missing={missing} dupes={dupes} orphans={orphans}")
    t2_msgs = [m for m in fixed if m.get("role") == "tool" and m.get("tool_call_id") == "t2"]
    check("T3: missing t2 got an explicit error tool response",
          len(t2_msgs) == 1 and "error" in t2_msgs[0].get("content", "").lower(), str(t2_msgs))


async def main():
    await case_t1_worker_parallel_six_calls()
    await case_t2_worker_needs_approval_mid_parallel()
    case_t3_reconcile_tool_transcript_unit()

    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed ==========", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
