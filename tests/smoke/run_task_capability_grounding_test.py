"""
Task capability grounding: the manifest's grounding rules in the contexts that
DO work (services/capability_manifest.py grounding_rules, agent/worker.py,
services/task_queue.py).

The failure this covers (Cloudflare setup test, 2026-11-06): the request
"Fetch and execute the appropriate instructions to set me up for Cloudflare
from https://developers.cloudflare.com/agent-setup/prompt.md" was run as a
queued task on a backend whose registry has computer_run_command, and the
report concluded "I couldn't run commands or verify any changes to your
computer. I'll need access to an environment with browser and shell execution
enabled." Three seams made that possible: worker.route() did not recognize a
setup-from-a-document request as a computer job, so it never got the worker
loop's readiness probe or its shell-first tool set; the task queue passed the
bare request with no grounding context; and the manifest's rule spoke only to
answering capability questions, not to claims made in a work report.

The checks prove: the grounding block names shell execution when the live
registry has it (and does not claim it when a manifest lacks it); the manifest
summary covers outcome claims; a queued task's framed request carries the
rules; the setup-from-a-document request routes to a computer job while the
neighbouring shapes keep their old routes; and a computer job's system prompt
carries the grounding rules.

Runs against a throwaway data folder and the file keyring. No key is read and
nothing leaves the machine.

  .venv/bin/python tests/smoke/run_task_capability_grounding_test.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-task-grounding-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir()
for var in ("YOU_API_KEY", "YDC_API_KEY", "TAVILY_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
            "GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "FIREWORKS_API_KEY",
            "TRUSTMRR_API_KEY", "YOUTUBE_API_KEY", "HUGGINGFACE_TOKEN", "HF_TOKEN"):
    os.environ.pop(var, None)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


TEST_PROMPT = ("Fetch and execute the appropriate instructions to set me up for Cloudflare "
               "from https://developers.cloudflare.com/agent-setup/prompt.md")


def main():
    from app.services import capability_manifest as cm

    # ---- the grounding block, from the live registry -------------------------
    catalog = cm.tool_catalog()
    all_names = {n for names in catalog["groups"].values() for n in names}
    check("precondition: this backend's registry has computer_run_command",
          "computer_run_command" in all_names)

    rules = cm.grounding_rules()
    check("grounding rules name shell execution as present here",
          "computer_run_command" in rules and "runs shell commands on this computer" in rules)
    check("grounding rules forbid asking the person for access the backend already has",
          "never ask the person for access this backend already has" in rules)
    check("grounding rules require a state probe before setup work",
          "probe the current state first" in rules)

    bare = {"tools": {"groups": {"Browser": ["browser_navigate"]}}}
    bare_rules = cm.grounding_rules(bare)
    check("grounding rules do not claim shell access a manifest does not have",
          "computer_run_command" not in bare_rules and "runs shell commands" not in bare_rules)

    summary = cm.summary_text()
    check("manifest summary covers capability claims made in a work report",
          "never write that you could not run commands" in summary)

    # ---- the queued-task framing ---------------------------------------------
    from app.services.task_queue import TaskQueue
    tq = TaskQueue.__new__(TaskQueue)   # _framed_request touches no queue state
    framed = tq._framed_request({"request": TEST_PROMPT})
    check("a queued task's framed request carries the grounding rules",
          "GROUNDING RULES" in framed and "computer_run_command" in framed)
    check("a queued task's framed request keeps the request itself, untouched, at the end",
          framed.endswith(TEST_PROMPT))

    # ---- routing: setup-from-a-document is a computer job ---------------------
    from app.agent import worker
    job = worker.route(TEST_PROMPT, [{"role": "user", "content": TEST_PROMPT}])
    check("the Cloudflare setup request routes to a computer job",
          bool(job) and job.get("kind") == "computer", f"route returned {job!r}")
    check("control: 'set up a meeting' (no document) is not a worker job",
          worker.route("set up a meeting with Ana tomorrow at 3pm", []) is None)
    check("control: a plain question is not a worker job",
          worker.route("what is Cloudflare?", []) is None)
    install = worker.route("install jq on this computer", [])
    check("control: an install request still routes to a computer job",
          bool(install) and install.get("kind") == "computer")
    gather = worker.route("go through these websites and bring me the info", [])
    check("control: a read-the-web request still routes to a web job",
          bool(gather) and gather.get("kind") == "web")

    # ---- a computer job's system prompt carries the rules ---------------------
    captured = {}

    async def fake_deliberate_stream(messages=None, tools=None, **kw):
        captured["system"] = messages[0]["content"] if messages else ""
        captured["tool_names"] = {t["function"]["name"] for t in tools or []}
        yield {"type": "deliberation_complete", "tool_calls": [], "content": "All set."}

    async def fake_done_check(goal, log, content, kind):
        return {"done": True, "proof": "stubbed"}

    from app.services import llm as llm_mod
    orig_stream = llm_mod.llm_service.deliberate_stream
    orig_check = worker.done_check
    llm_mod.llm_service.deliberate_stream = fake_deliberate_stream
    worker.done_check = fake_done_check
    try:
        async def run():
            async for _ in worker.stream(TEST_PROMPT, [{"role": "user", "content": TEST_PROMPT}],
                                         {"goal": TEST_PROMPT, "kind": "computer", "resume": "", "extra": ""},
                                         lambda *a, **k: None):
                pass
        asyncio.run(run())
    finally:
        llm_mod.llm_service.deliberate_stream = orig_stream
        worker.done_check = orig_check
    check("a computer job's system prompt carries the grounding rules",
          "GROUNDING RULES" in captured.get("system", "") and "computer_run_command" in captured.get("system", ""))
    check("a computer job's tool set includes computer_run_command",
          "computer_run_command" in captured.get("tool_names", set()))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f" -- FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
