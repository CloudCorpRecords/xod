"""Reproduction & regression test for worker tool recovery (Round 4).

Reproduces both live shapes from the re-grade:
  - Shape A (mission 46ea4dac5f, T2C): read-only worker calls computer_run_command,
    gets outside_envelope=True, and must recover by switching to an in-envelope
    tool (computer_list_directory / computer_read_file) rather than breaking out.
    Also verifies select_tools_for_query hides out-of-envelope tools under read_only.
  - Shape B (mission 0376ea1eb1, T4L1): worker calls computer_read_file on a
    directory ("Path is not a regular file"), or calls computer_read_file with
    missing filepath; Repairer must recover deterministically. Also verifies a
    contents-demanding worker cannot pass on directory-listing evidence alone.

Run: python tests/task_inbox/run_worker_tool_recovery_test.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_trec_"))
os.environ.update({
    "HOME": str(TMP),
    "XOD_DATA_DIR": str(TMP / "data"),
    "XOD_NO_UPDATE_CHECK": "1",
})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.agent.healing import Repairer, check_result, classify  # noqa: E402
from app.agent.tools import TOOL_DEFINITIONS, execute_tool, select_tools_for_query  # noqa: E402
import app.services.workers as W  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


async def case_s1_shape_a_outside_envelope_recovery():
    print("== S1 (Shape A): outside_envelope on computer_run_command recovers in-envelope ==", flush=True)
    inbox = TMP / "inbox_s1"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "directive.txt").write_text("CODE: ZULU-9\n", encoding="utf-8")
    (inbox / "note.txt").write_text("Morning notes.\n", encoding="utf-8")

    tok = W.envelope.set("read_only")
    try:
        args = {"command": f"head -n 1 {inbox}/directive.txt"}
        res = await execute_tool("computer_run_command", args)
        check("S1: computer_run_command is refused with outside_envelope=True",
              res.get("outside_envelope") is True and res.get("success") is False, str(res))

        calls = []

        async def run(name, a):
            calls.append((name, a))
            return await execute_tool(name, a)

        candidates = select_tools_for_query(
            f"List files in {inbox} and inspect their first lines", max_tools=16
        )
        repairer = Repairer(
            run,
            f"List files in {inbox} and inspect their first lines for CODE:",
            TOOL_DEFINITIONS,
            candidates,
        )
        final_ev = None
        async for ev in repairer.repair("computer_run_command", args, res, "call-s1"):
            if ev["type"] == "_repair_done":
                final_ev = ev

        check("S1: Repairer switched to an in-envelope read/list tool and succeeded",
              bool(calls) and final_ev is not None
              and check_result(final_ev["tool"], final_ev["args"], final_ev["result"])["ok"]
              and W.allowed(final_ev["tool"]) is None,
              f"calls={calls} final={final_ev}")
    finally:
        W.envelope.reset(tok)


def case_s2_select_tools_respects_read_only_envelope():
    print("== S2 (Shape A): select_tools_for_query filters out-of-envelope tools under read_only ==", flush=True)
    tok = W.envelope.set("read_only")
    try:
        tools = select_tools_for_query("Run a shell command to list files and write summary.txt", max_tools=25)
        names = {t["function"]["name"] for t in tools}
        check("S2: computer_run_command and computer_write_file are excluded under read_only",
              "computer_run_command" not in names and "computer_write_file" not in names,
              str(sorted(names)))
        check("S2: computer_list_directory or computer_read_file is available",
              "computer_list_directory" in names or "computer_read_file" in names,
              str(sorted(names)))
    finally:
        W.envelope.reset(tok)


async def case_s3_shape_b_read_on_directory_recovery():
    print("== S3 (Shape B): computer_read_file on directory recovers via computer_list_directory ==", flush=True)
    inbox = TMP / "inbox_s3"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "alpha.txt").write_text("Alpha contents\n", encoding="utf-8")
    (inbox / "beta.txt").write_text("Beta contents\n", encoding="utf-8")

    args = {"filepath": str(inbox)}
    res = await execute_tool("computer_read_file", args)
    check("S3: computer_read_file on directory fails with 'not a regular file'",
          res.get("success") is False and "not a regular file" in (res.get("error") or "").lower(),
          str(res))

    calls = []

    async def run(name, a):
        calls.append((name, a))
        return await execute_tool(name, a)

    candidates = select_tools_for_query(f"Read the files inside {inbox}", max_tools=16)
    repairer = Repairer(run, f"Inspect the contents of files in {inbox}", TOOL_DEFINITIONS, candidates)
    final_ev = None
    async for ev in repairer.repair("computer_read_file", args, res, "call-s3"):
        if ev["type"] == "_repair_done":
            final_ev = ev

    check("S3: Repairer switched to computer_list_directory on the directory and succeeded",
          final_ev is not None and final_ev["tool"] == "computer_list_directory"
          and check_result(final_ev["tool"], final_ev["args"], final_ev["result"])["ok"],
          f"calls={calls} final={final_ev}")


async def case_s4_shape_b_missing_filepath_recovery():
    print("== S4 (Shape B): computer_read_file with missing filepath recovers ==", flush=True)
    f = TMP / "target_s4.txt"
    f.write_text("SECRET: OAK-4\n", encoding="utf-8")

    args = {}
    res = await execute_tool("computer_read_file", args)
    check("S4: computer_read_file with {} fails with 'needs filepath'",
          res.get("success") is False and "filepath" in (res.get("error") or "").lower(),
          str(res))

    calls = []

    async def run(name, a):
        calls.append((name, a))
        return await execute_tool(name, a)

    candidates = select_tools_for_query(f"Read {f}", max_tools=16)
    repairer = Repairer(run, f"Read the file {f} and report its contents", TOOL_DEFINITIONS, candidates)
    final_ev = None
    async for ev in repairer.repair("computer_read_file", args, res, "call-s4"):
        if ev["type"] == "_repair_done":
            final_ev = ev

    check("S4: Repairer recovered by calling computer_read_file with the file path",
          final_ev is not None and final_ev["tool"] == "computer_read_file"
          and check_result(final_ev["tool"], final_ev["args"], final_ev["result"])["ok"]
          and "OAK-4" in str(final_ev["result"]),
          f"calls={calls} final={final_ev}")


async def case_s5_contents_step_not_answered_from_filenames_alone():
    print("== S5: contents-demanding worker cannot pass from directory listing alone ==", flush=True)
    orig_agent = W.agent
    orig_synth = W.synthesize

    async def list_only_agent(brief, wid):
        yield {"type": "tool_result", "name": "computer_list_directory",
               "data": {"success": True, "entries": ["directive.txt", "note.txt"]}}
        yield {"type": "content", "content": "**Confirmed:** directive.txt is the matching file (file:///tmp/inbox/directive.txt)."}

    async def synth(job, ok_results):
        return f"ok={len(ok_results)}"

    W.agent = list_only_agent
    W.synthesize = synth
    try:
        res = await W.coordinate(
            "parent-s5",
            "Find matching file by contents",
            [{"task": "List inbox and inspect their first lines to find the file whose first line starts with 'CODE:'"}],
            timeout=5.0,
        )
        check("S5: worker that only listed filenames without reading contents is marked failed",
              res["workers"][0]["outcome"] == "failed" and res["outcome"] == "failed",
              f"res={res}")
    finally:
        W.agent = orig_agent
        W.synthesize = orig_synth


async def main():
    await case_s1_shape_a_outside_envelope_recovery()
    case_s2_select_tools_respects_read_only_envelope()
    await case_s3_shape_b_read_on_directory_recovery()
    await case_s4_shape_b_missing_filepath_recovery()
    await case_s5_contents_step_not_answered_from_filenames_alone()

    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed ==========", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
