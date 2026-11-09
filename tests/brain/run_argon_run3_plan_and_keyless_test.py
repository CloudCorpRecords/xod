"""
Argon Run 3 regression & acceptance suite:
  1. Run 2 Plan-Shape & Truncated Inline Script Sanitization (orchestrator._sanitize_and_shape_steps)
  2. Run 2 Rule 1 Destination Bundle & Syntax Gate (task_queue._file_type_problem & _verify_file_effects)
  3. Run 2 Rule 6 Incompletion Admission & Authority-Punt Gate (orchestrator._self_report_problem)
  4. New Defect Candidate: Keyless Cloud Route Probe Never Closes as Done (researcher + orchestrator + task_queue)
  5. Worker Routing for Multi-Step Orchestrator Prompts & Local LLM Scripts (worker.route)

Run:
  .venv/bin/python tests/brain/run_argon_run3_plan_and_keyless_test.py
"""
import asyncio
import json
import os
import plistlib
import sys
import tempfile
import time
import uuid
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.setdefault("XOD_NO_UPDATE_CHECK", "1")

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_argon_run3_"))
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

from app.agent import researcher as RS  # noqa: E402
from app.agent import worker as W  # noqa: E402
from app.services import activity  # noqa: E402
from app.services import orchestrator as O  # noqa: E402
from app.services import task_queue as TQ  # noqa: E402

TQ.notify = lambda title, msg: None
TQ._phone = lambda task, kind: None


async def _no_tab(self, task, resuming):
    return True


TQ.TaskQueue._ensure_tab = _no_tab

passed = 0
failures = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed
    if ok:
        passed += 1
        print(f"  PASS {name}", flush=True)
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"  [{detail}]" if detail else ""), flush=True)


# ---------------------------------------------------------------------------
# 1. Run 2 Plan-Shape & Truncated Inline Script Sanitization
# ---------------------------------------------------------------------------
print("1. Run 2 plan-shape & inline-script sanitization")
RUN2_REQUEST = (
    "Build and install a macOS right-click Quick Action service in ~/Library/Services "
    "that sends selected text to a local AI model and displays the response, then register "
    "it with pbs and test it."
)
RUN2_TRUNCATED_STEP = (
    "Create a new file at ~/Library/Services/AIRightClick.service with the following content:\n\n"
    "#!/bin/bash\n"
    "# macOS Quick Action Service: Send Selected Text to AI\n"
    "SELECTED_TEXT=$(pbpaste)\n"
    "RESPONSE=$(curl -s https://api.openai.com/v1/chat/completions \\\n"
    "  -H \"Authorization: Bearer $OPENAI_API_KEY\" \\\n"
    "  -d \"{\n"
    "    \\\"model\\\": \\\"gpt-3.5"
)

shaped = O._sanitize_and_shape_steps(RUN2_REQUEST, [RUN2_TRUNCATED_STEP])
check("1a. 1-step truncated service plan expands to 3 steps", len(shaped) == 3, str(shaped))
check("1b. Step 1 names Info.plist, document.wflow, and ask_ai.py",
      all(k in shaped[0] for k in ("Info.plist", "document.wflow", "ask_ai.py", "127.0.0.1:1234")),
      shaped[0])
check("1c. Step 1 forbids requiring external API key and truncated inline bash is gone",
      "without requiring an external API key" in shaped[0] and "#!/bin/bash" not in shaped[0],
      shaped[0])
check("1d. Step 2 registers with /System/Library/CoreServices/pbs -update and dumps pboard",
      "/System/Library/CoreServices/pbs -update" in shaped[1] and "-dump_pboard" in shaped[1],
      shaped[1])
check("1e. Step 3 runs end-to-end functional test of ask_ai.py",
      "ask_ai.py" in shaped[2] and "exit code 0" in shaped[2],
      shaped[2])

step1_claims = TQ._claimed_file_effects(shaped[0])
step2_claims = TQ._claimed_file_effects(shaped[1])
step3_claims = TQ._claimed_file_effects(shaped[2])
check("1f. Step 1 file claims extract all 3 bundle files (Info.plist, document.wflow, ask_ai.py)",
      [c["path"].name for c in step1_claims] == ["Info.plist", "document.wflow", "ask_ai.py"],
      str(step1_claims))
check("1g. Steps 2 and 3 do not manufacture false file write claims",
      step2_claims == [] and step3_claims == [],
      f"s2={step2_claims} s3={step3_claims}")

plain_truncated = O._strip_inline_code_from_step(
    "Create the helper script /tmp/demo.sh with the following content:\n#!/bin/bash\necho \"unclosed"
)
check("1h. Inline code dump stripped from generic step text",
      "#!/bin/bash" not in plain_truncated and "/tmp/demo.sh" in plain_truncated,
      plain_truncated)


# ---------------------------------------------------------------------------
# 2. Run 2 Rule 1 Destination Bundle & Syntax Gate
# ---------------------------------------------------------------------------
print("2. Run 2 Rule 1 destination bundle & syntax gate")
svc_dir = TMP / "Services"
svc_dir.mkdir(parents=True, exist_ok=True)

flat_svc = svc_dir / "AIRightClick.service"
flat_svc.write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
prob_flat = TQ._file_type_problem(flat_svc)
check("2a. Flat .service file in Services is rejected",
      prob_flat is not None and "flat file" in prob_flat and "bundle directory" in prob_flat,
      str(prob_flat))

trunc_sh = TMP / "truncated.sh"
trunc_sh.write_text(
    "#!/bin/bash\nRESPONSE=$(curl -s https://api.openai.com/v1/chat/completions -d \"{\n  \\\"model\\\": \\\"gpt-3.5",
    encoding="utf-8",
)
prob_sh = TQ._file_type_problem(trunc_sh)
check("2b. Truncated bash script is rejected by bash -n syntax check",
      prob_sh is not None and "shell syntax error" in prob_sh,
      str(prob_sh))

wf_contents = svc_dir / "AskXodAI.workflow" / "Contents"
wf_contents.mkdir(parents=True, exist_ok=True)
bad_info = wf_contents / "Info.plist"
bad_info.write_bytes(plistlib.dumps({"CFBundleName": "AskXodAI"}))
prob_bad_info = TQ._file_type_problem(bad_info)
check("2c. Info.plist inside .workflow missing NSServices is rejected",
      prob_bad_info is not None and "NSServices" in prob_bad_info,
      str(prob_bad_info))

good_info_data = {
    "NSServices": [
        {
            "NSBackgroundColorName": "background",
            "NSIconName": "NSActionTemplate",
            "NSMenuItem": {"default": "Ask Xod AI"},
            "NSMessage": "runWorkflowAsService",
            "NSSendTypes": ["public.utf8-plain-text"],
        }
    ]
}
bad_info.write_bytes(plistlib.dumps(good_info_data))
check("2d. Valid Info.plist with NSServices passes _file_type_problem",
      TQ._file_type_problem(bad_info) is None,
      str(TQ._file_type_problem(bad_info)))

wflow_file = wf_contents / "document.wflow"
wflow_file.write_bytes(plistlib.dumps({"AMDocumentVersion": "2", "actions": []}))
check("2e. Valid document.wflow plist passes _file_type_problem",
      TQ._file_type_problem(wflow_file) is None,
      str(TQ._file_type_problem(wflow_file)))


# ---------------------------------------------------------------------------
# 3. Run 2 Rule 6 Incompletion Admission & Authority-Punt Gate
# ---------------------------------------------------------------------------
print("3. Run 2 Rule 6 self-report incompletion & authority-punt gate")
run2_report = (
    "Created `~/Library/Services/AIRightClick.service`.\n\n"
    "The script is written, but it is incomplete — the `curl` command is missing its closing brace "
    "and response handling. I can fix that in the next step if you'd like me to continue building this out."
)
prob_rule6 = O._self_report_problem(
    "Create a new file at ~/Library/Services/AIRightClick.service",
    run2_report,
)
check("3a. Report admitting script is incomplete / offering to continue is flagged even when step has file claim",
      prob_rule6 is not None and "incomplete" in prob_rule6.lower(),
      str(prob_rule6))

prob_clean = O._self_report_problem(
    "Create the file /tmp/clean.txt",
    "Created `/tmp/clean.txt` and verified its contents on disk.",
)
check("3b. Clean completion report with file claim passes", prob_clean is None, str(prob_clean))


# ---------------------------------------------------------------------------
# 4. New Defect Candidate: Keyless Cloud Route Probe Never Closes as Done
# ---------------------------------------------------------------------------
print("4. Keyless cloud route pre-flight probe fails honestly")


async def test_keyless_probe():
    from app.services import llm as llm_mod

    orig_stream = llm_mod.llm_service.stream_chat_completion
    orig_chat = llm_mod.llm_service.chat_completion
    orig_planner = O.planner

    async def keyless_stream(*args, **kwargs):
        yield {
            "type": "error",
            "content": "Missing API key for FIREWORKS. Please configure it in the Model Switcher settings.",
        }

    async def keyless_chat(*args, **kwargs):
        return {"content": "", "error": "Missing API key for FIREWORKS."}

    llm_mod.llm_service.stream_chat_completion = keyless_stream
    llm_mod.llm_service.chat_completion = keyless_chat
    O.planner = O.default_planner

    q = TQ.task_queue = TQ.TaskQueue()
    q.start()
    try:
        ack = O.submit("Reply with exactly this text and nothing else: BRAIN-OK")
        tid = ack["task_id"]
        t0 = time.time()
        while time.time() - t0 < 15:
            t = q.get(tid)
            if t["status"] in ("done", "failed", "waiting", "cancelled"):
                break
            await asyncio.sleep(0.05)
        t = q.get(tid)
        items, _, _ = O.load_plan(tid)
        check("4a. Keyless Fireworks probe task finishes as failed, never done",
              t["status"] == "failed",
              f"status={t['status']} result={t.get('result')!r}")
        check("4b. Step 1 is marked blocked with missing API key reason",
              len(items) == 1 and items[0]["status"] == "blocked" and "Missing API key for FIREWORKS" in (items[0].get("blocker") or ""),
              json.dumps(items))
        check("4c. Fatal missing API key blocks on attempt 1 without wasting a retry",
              items[0].get("attempts") == 1,
              f"attempts={items[0].get('attempts')}")
    finally:
        q.stop()
        llm_mod.llm_service.stream_chat_completion = orig_stream
        llm_mod.llm_service.chat_completion = orig_chat
        O.planner = orig_planner


asyncio.run(test_keyless_probe())


# ---------------------------------------------------------------------------
# 5. Worker Routing for Multi-Step Orchestrator Prompts & Local LLM Scripts
# ---------------------------------------------------------------------------
print("5. Worker routing for orchestrator steps & local LLM service scripts")
long_context = "Prior step output line. " * 100  # > 2400 chars
for idx, step_txt in enumerate(shaped, 1):
    full_prompt = (
        f"You are carrying out ONE step of a larger task for the person.\n"
        f"The overall task:\n{RUN2_REQUEST}\n\n"
        f"Steps already completed:\n- prior\n  RESULT: {long_context}\n\n"
        f"YOUR STEP NOW (step {idx} of 3):\n{step_txt}\n\n"
        f"Do only this step, using your tools."
    )
    routed = W.route(full_prompt, [{"role": "user", "content": full_prompt}])
    check(f"5{chr(96 + idx)}. Shaped step {idx} routes to computer worker despite >2400 char prompt",
          routed is not None and routed.get("kind") == "computer",
          str(routed))

for non_worker_q in ("install the Notion connector", "download a new model"):
    check(f"5d. '{non_worker_q}' still stays out of worker lane",
          W.route(non_worker_q, [{"role": "user", "content": non_worker_q}]) is None)


# ---------------------------------------------------------------------------
# 6. Long-Form Context Continuity: Workspace Artifact Ledger, Context-Scaled
#    Shrinking, Step-Scoped done_check, Router Isolation, and Adaptive Replan
# ---------------------------------------------------------------------------
print("6. Long-form context continuity (artifact ledger, shrinking, step done_check, adaptive replan)")


async def test_longform_context_continuity():
    from app.services import activity, hunt_routing
    import app.agent.researcher as RS

    # 6a. Workspace artifact ledger travels across steps even when Step 1's prose result omits code
    proj_dir = TMP / "longform_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    schema_file = proj_dir / "schema.py"
    schema_code = "SERVICE_PORT = 8944\nclass EndpointConfig:\n    host = '127.0.0.1'\n"
    schema_file.write_text(schema_code, encoding="utf-8")

    items = [
        {
            **O._item(f"Create {schema_file}"),
            "status": "completed",
            "result": "Created schema.py.",
            "artifacts": [str(schema_file)],
        },
        {
            **O._item(f"Create {proj_dir / 'runner.py'} importing EndpointConfig and SERVICE_PORT"),
            "status": "pending",
        },
    ]
    step2_prompt = O._step_prompt(
        {"id": "task-lf-1", "request": "Build schema.py and runner.py"},
        items,
        [],
        items[1],
        [],
    )
    check(
        "6a. Workspace artifact ledger injects Step 1 file path and live on-disk code into Step 2 prompt",
        "Workspace artifact ledger" in step2_prompt
        and str(schema_file) in step2_prompt
        and "SERVICE_PORT = 8944" in step2_prompt
        and "class EndpointConfig:" in step2_prompt,
        step2_prompt[:600],
    )

    # 6b. Step-scoped worker.done_check judges only YOUR STEP NOW, not the overall 3-step task
    captured_check_prompts = []

    async def capture_done_check_llm(messages, **kw):
        captured_check_prompts.append(messages[-1]["content"])
        return {"content": json.dumps({"done": True, "proof": "wrote files", "missing": "", "needs_person": False})}

    orig_worker_chat = W.llm_service.chat_completion
    W.llm_service.chat_completion = capture_done_check_llm
    try:
        verdict = await W.done_check(
            step2_prompt,
            [{"name": "computer_write_file", "args": {"filepath": str(proj_dir / "runner.py")}, "failed": False, "out": "wrote runner.py", "exit": 0}],
            "Created runner.py.",
            "computer",
        )
        seen_prompt = captured_check_prompts[-1] if captured_check_prompts else ""
        check(
            "6b. worker.done_check scopes goal to YOUR STEP NOW and uses step-scoped rubric",
            verdict.get("done") is True
            and "ONE step of a multi-step plan" in seen_prompt
            and "Build schema.py and runner.py" not in seen_prompt,
            seen_prompt[:500],
        )
    finally:
        W.llm_service.chat_completion = orig_worker_chat

    # 6c. Context-aware worker._shrink scales between local (6 full / 550 chars) and cloud (14 full / 2800 chars)
    local_convo = [{"role": "tool", "content": "X" * 2000} for _ in range(10)]
    W._shrink(local_convo, force_local=True)
    cloud_convo = [{"role": "tool", "content": "Y" * 2000} for _ in range(10)]
    W._shrink(cloud_convo, force_local=False)
    check(
        "6c. worker._shrink shrinks 10th-oldest 2000-char tool output on local but preserves it in full on cloud",
        len(local_convo[0]["content"]) < 600 and len(cloud_convo[0]["content"]) == 2000,
        f"local_len={len(local_convo[0]['content'])} cloud_len={len(cloud_convo[0]['content'])}",
    )

    # 6d. Router isolation: hunt_routing.is_find_intent does not misfire on dates/filenames in step history
    step_with_year_history = (
        "You are carrying out ONE step of a larger task for the person.\n\n"
        "The overall task:\nBuild my 2026 Quick Action summary service.\n\n"
        "Steps already completed:\n- Wrote document.wflow for 2026 summary\n\n"
        "YOUR STEP NOW (step 2 of 3):\n"
        "Register the Quick Action with macOS by running /System/Library/CoreServices/pbs -update\n\n"
        "Do only this step, using your tools."
    )
    check(
        "6d. hunt_routing.is_find_intent ignores years/document nouns in step history when YOUR STEP NOW is an action",
        hunt_routing.is_find_intent(step_with_year_history) is False,
    )

    # 6e-6g. End-to-end adaptive post-step replanning (`PLAN_UPDATE:`) and artifact tracking in run_stepwise
    adapt_dir = TMP / "adapt_proj"
    adapt_dir.mkdir(parents=True, exist_ok=True)
    seen_step2_prompts = []

    class AdaptiveFakeResearcher:
        def __init__(self, *a, **k):
            pass

        async def run_research_stream(self, messages, **k):
            msg = messages[-1]["content"]
            step_txt = W._step_goal(msg)
            yield {"type": "status", "content": f"• running: {step_txt[:50]}"}
            if "Inspect workspace" in step_txt:
                p = adapt_dir / "discovered_config.py"
                p.write_text("PORT = 9099\n", encoding="utf-8")
                c = activity._conn()
                c.execute(
                    "INSERT INTO activity (id, ts, origin, tool, title, detail, ok, undo, undoable) VALUES (?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, time.time(), activity.origin.get(), "computer_write_file", "Wrote a file",
                     str(p), 1, json.dumps([{"kind": "write_file", "path": str(p)}]), 1),
                )
                c.commit()
                c.close()
                yield {
                    "type": "content",
                    "content": (
                        f"Created {p} with PORT = 9099.\n"
                        f"PLAN_UPDATE: Write {adapt_dir / 'app_v2.py'} importing PORT from discovered_config.py instead of old_app.py"
                    ),
                }
                return
            seen_step2_prompts.append(msg)
            p2 = adapt_dir / "app_v2.py"
            p2.write_text("from discovered_config import PORT\nprint(PORT)\n", encoding="utf-8")
            c = activity._conn()
            c.execute(
                "INSERT INTO activity (id, ts, origin, tool, title, detail, ok, undo, undoable) VALUES (?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, time.time(), activity.origin.get(), "computer_write_file", "Wrote a file",
                 str(p2), 1, json.dumps([{"kind": "write_file", "path": str(p2)}]), 1),
            )
            c.commit()
            c.close()
            yield {"type": "content", "content": f"Created {p2} using PORT = 9099."}

    orig_rs = RS.AgenticResearcher
    orig_pl = O.planner
    orig_repl = O.replanner
    RS.AgenticResearcher = AdaptiveFakeResearcher

    async def adapt_planner(_req):
        return [
            f"Inspect workspace and create {adapt_dir / 'discovered_config.py'}",
            f"Create {adapt_dir / 'old_app.py'}",
        ]

    async def adapt_replanner(_req, _done, _rem, instruction):
        if "app_v2.py" in instruction:
            return [f"Create the file {adapt_dir / 'app_v2.py'} importing PORT"]
        return []

    O.planner = adapt_planner
    O.replanner = adapt_replanner
    q = TQ.task_queue = TQ.TaskQueue()
    q.start()
    try:
        ack = O.submit("Inspect workspace and build the app")
        tid = ack["task_id"]
        t0 = time.time()
        while time.time() - t0 < 15:
            t = q.get(tid)
            if t["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.05)
        t = q.get(tid)
        plan_items, plan_ver, _ = O.load_plan(tid)
        check(
            "6e. Adaptive replan task finishes done and supersedes stale Step 2 with adapted Step 2",
            t["status"] == "done"
            and [i["status"] for i in plan_items] == ["completed", "cancelled", "completed"]
            and plan_items[2].get("origin") == "adaptive",
            json.dumps(plan_items),
        )
        check(
            "6f. Step 1 strips PLAN_UPDATE directive from user-facing result and records artifacts",
            "PLAN_UPDATE" not in (plan_items[0].get("result") or "")
            and str(adapt_dir / "discovered_config.py") in (plan_items[0].get("artifacts") or []),
            json.dumps(plan_items[0]),
        )
        check(
            "6g. Adapted Step 2 prompt receives Step 1's discovered_config.py content via the Workspace artifact ledger",
            bool(seen_step2_prompts) and "PORT = 9099" in seen_step2_prompts[-1],
            seen_step2_prompts[-1][:500] if seen_step2_prompts else "no step 2 prompt",
        )
    finally:
        q.stop()
        RS.AgenticResearcher = orig_rs
        O.planner = orig_pl
        O.replanner = orig_repl


asyncio.run(test_longform_context_continuity())


print(f"\n{passed}/{passed + len(failures)} checks passed")
if failures:
    print("FAILED:", *failures, sep="\n  ")
    sys.exit(1)
