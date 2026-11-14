"""AGE-46 Tier 5 grading rig: Delegated parallel work (TIER-DEEP-DIVES.md, tier-05.md).

Encodes Tier 5's deep-dive mission shape at the tool-contract level using
planted disjoint shards and scripted workers through the real workers.coordinate,
workers.spawn, workers.allowed, workers.render_result, and MissionRunner
(no live LLM).

Contract points verified locally:
  - T5-A: Disjoint briefs with context isolation; real parallel execution
    (4x 0.4s workers finish in < 1.3s wall time); explicit denominator (4/4).
  - T5-B (load-bearing gate): A worker failure is preserved in the denominator
    (4 workers listed, outcome='partial', failure itemized in failures and
    render_result; failed unit never vanishes from the count).
  - T5-C: Permission envelope & nesting guard (read-only workers cannot write
    or spawn sub-workers).
  - T5-D (load-bearing gate): Parent independently verifies a decisive file on
    disk and writes verified sweep_summary.txt through MissionRunner.

Live-only assertions (marked SKIPPED with reason; executed in the future
pinned-Mac + family-key ceremony run):
  - Live orchestrator writing its own disjoint worker briefs for a 40-file sweep.
  - Live parent independently selecting decisive claims to spot-check on disk.

Run: python tests/brain/run_tier5_grading_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_t5_grade_"))
os.environ.update({
    "HOME": str(TMP),
    "XOD_DATA_DIR": str(TMP / "data"),
    "XOD_NO_UPDATE_CHECK": "1",
})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import app.services.missions as M  # noqa: E402
import app.services.task_queue as TQ  # noqa: E402
import app.services.workers as W  # noqa: E402

DBF = TMP / "xod.db"
M.DB = DBF
TQ.DB_PATH, TQ.SHOTS_DIR = DBF, TMP / "shots"
TQ.notify = lambda title, msg: None
if hasattr(TQ, "_phone"):
    TQ._phone = lambda task, kind: None
M._spawn = lambda mid: None

results = []
skipped = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def skip_live(name, reason):
    skipped.append((name, reason))
    print(f"  SKIP (live-only) {name}  [{reason}]", flush=True)


class FakeAsk:
    def __init__(self, replies):
        self.replies = list(replies)

    async def __call__(self, prompt):
        return self.replies.pop(0) if self.replies else "{}"


class FakeQueue:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.created = []
        self.tasks = {}

    def create(self, request, title=None, origin=None):
        tid = f"task-t5-{len(self.created) + 1}"
        self.created.append({"id": tid, "request": request, "title": title})
        idx = len(self.created) - 1
        beh = self.behaviors[idx] if idx < len(self.behaviors) else {"status": "failed", "result": "unexpected task"}
        self.tasks[tid] = {"id": tid, "request": request, "title": title or "", "behavior": beh}
        return {"id": tid, "status": "queued", "title": title}

    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None:
            return None
        b = rec["behavior"]
        if callable(b):
            return b(rec)
        return {"id": tid, "title": rec["title"], "request": rec["request"], **b}


async def case_t5a_parallel_disjoint_sweep():
    print("== T5-A: disjoint worker briefs, context isolation, real parallelism ==", flush=True)
    orig_agent = W.agent
    orig_synth = W.synthesize
    seen_briefs = {}

    async def parallel_agent(brief, wid):
        seen_briefs[wid] = brief
        job = brief.split("JOB:\n", 1)[1].split("\n", 1)[0].strip()
        await asyncio.sleep(0.4)
        yield {"type": "tool_result", "name": "computer_read_file",
               "data": {"success": True, "content": f"ok:{job}"}}
        yield {"type": "content", "content": f"Confirmed: {job} verified (file:///tmp/{wid})."}

    async def synth(job, ok_results):
        return f"Synthesized {len(ok_results)}/4 shards."

    W.agent = parallel_agent
    W.synthesize = synth
    try:
        subtasks = [
            {"task": f"Inspect shard-{s}", "outcome": f"Counts for shard-{s}",
             "facts": [f"SECRET_SCOPE_SHARD_{s}"], "constraints": ["Do not inspect other shards"]}
            for s in ("A", "B", "C", "D")
        ]
        t0 = time.monotonic()
        res = await W.coordinate("t5a-parent", "Sweep 4 disjoint shards", subtasks, timeout=10.0)
        elapsed = time.monotonic() - t0

        check("T5-A: all 4 workers completed in explicit denominator",
              res["outcome"] == "done" and len(res["workers"]) == 4
              and all(w["outcome"] == "done" for w in res["workers"]),
              str(res["workers"]))
        check("T5-A: workers ran concurrently (4x 0.4s finished in < 1.3s)",
              elapsed < 1.3, f"elapsed={elapsed:.2f}s")
        # Context isolation: each worker's brief has only its own shard fact
        isolated = all(
            f"SECRET_SCOPE_SHARD_{s}" in seen_briefs[w["id"]]
            and all(f"SECRET_SCOPE_SHARD_{other}" not in seen_briefs[w["id"]]
                    for other in ("A", "B", "C", "D") if other != s)
            for s, w in zip(("A", "B", "C", "D"), res["workers"])
        )
        check("T5-A: briefs are isolated (no cross-worker context leakage)", isolated)
    finally:
        W.agent = orig_agent
        W.synthesize = orig_synth


async def case_t5b_failure_in_denominator():
    print("== T5-B: failed worker preserved in explicit denominator ==", flush=True)
    orig_agent = W.agent
    orig_synth = W.synthesize

    async def partial_agent(brief, wid):
        job = brief.split("JOB:\n", 1)[1].split("\n", 1)[0].strip()
        if "shard-C" in job:
            yield {"type": "tool_result", "name": "computer_read_file",
                   "data": {"success": False, "error": "EACCES: shard-C/bad.txt unreadable"}}
            yield {"type": "content", "content": "shard-C/bad.txt could not be read."}
        else:
            yield {"type": "tool_result", "name": "computer_read_file",
                   "data": {"success": True, "content": "3 files ok"}}
            yield {"type": "content", "content": f"Confirmed: {job} complete."}

    async def synth(job, ok_results):
        return f"Completed {len(ok_results)} of 4 assigned shards."

    W.agent = partial_agent
    W.synthesize = synth
    try:
        subtasks = [{"task": f"Inspect shard-{s}", "outcome": "File counts"} for s in ("A", "B", "C", "D")]
        res = await W.coordinate("t5b-parent", "Sweep 4 shards with 1 unreadable", subtasks, timeout=10.0)
        rendered = W.render_result(res)
        check("T5-B: outcome is 'partial' (never 'done')", res["outcome"] == "partial", str(res["outcome"]))
        check("T5-B: all 4 assigned workers remain in denominator (3 done, 1 failed)",
              len(res["workers"]) == 4
              and sum(1 for w in res["workers"] if w["outcome"] == "done") == 3
              and sum(1 for w in res["workers"] if w["outcome"] == "failed") == 1,
              str(res["workers"]))
        check("T5-B: failure itemized in failures list and render_result",
              len(res["failures"]) == 1 and "shard-C" in res["failures"][0]["task"]
              and "Workers that did not deliver:" in rendered and "shard-C" in rendered,
              rendered)
    finally:
        W.agent = orig_agent
        W.synthesize = orig_synth


def case_t5c_envelopes_and_nesting():
    print("== T5-C: read-only worker envelope & nesting guard ==", flush=True)
    c = W._conn()
    c.execute(
        "INSERT OR REPLACE INTO workers (id, parent_id, parent_type, role, brief, envelope, state, created_at, last_activity_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("wrk-parent-fake", "t5c-parent", "task", "worker", "b", "read_only", "running", 1.0, 1.0),
    )
    c.commit()
    c.close()

    tok = W.current_worker.set("wrk-parent-fake")
    etok = W.envelope.set("read_only")
    try:
        refusal = W.allowed("computer_write_file")
        check("T5-C: read-only worker is refused computer_write_file",
              refusal is not None and "read-only" in refusal, str(refusal))

        nested_blocked = False
        try:
            W.spawn("wrk-parent-fake", "Nested worker", role="worker")
        except PermissionError:
            nested_blocked = True
        check("T5-C: a worker cannot spawn sub-workers (PermissionError)", nested_blocked)
    finally:
        W.envelope.reset(etok)
        W.current_worker.reset(tok)

    write_env_blocked = False
    try:
        W.spawn("t5c-parent", "Try write env", env="write")
    except PermissionError:
        write_env_blocked = True
    check("T5-C: workers.spawn rejects non-read_only envelope with PermissionError", write_env_blocked)


async def case_t5d_parent_verification_mission():
    print("== T5-D: parent independently verifies decisive claim on disk ==", flush=True)
    ws = TMP / "t5d"
    ws.mkdir(parents=True, exist_ok=True)
    decisive = ws / "shard-A-file1.txt"
    decisive.write_text("VALUE: 42\n", encoding="utf-8")
    out = ws / "sweep_summary.txt"

    plan = {
        "subtasks": [
            {
                "title": "Delegate parallel shard reads",
                "instruction": "Collect shard totals from workers and report them.",
                "expected_effects": [],
                "inputs": [{"source": "goal"}],
                "tools": ["computer_read_file"],
            },
            {
                "title": "Parent spot-check and write verified summary",
                "instruction": f"Independently read {decisive} to verify the decisive claim, then write {out}.",
                "expected_effects": [{"kind": "create", "path": str(out), "content": None}],
                "inputs": [{"source": "goal"}, {"source": "file", "ref": str(decisive)}, {"source": "prior_step", "ref": "1"}],
                "tools": ["computer_read_file", "computer_write_file"],
            },
        ]
    }
    goal = f"In {ws}, delegate shard reads, independently verify {decisive}, and write {out}."

    s1 = {"status": "done", "result": "Workers reported shard-A=42, shard-B=8, total=50.",
          "evidence": [{"tool": "workers.coordinate", "workers": 2}]}

    def s2(rec):
        val = decisive.read_text(encoding="utf-8").strip()
        out.write_text(f"Verified {decisive.name} ({val}); total=50", encoding="utf-8")
        return {"id": rec["id"], "title": rec["title"], "request": rec["request"],
                "status": "done", "result": f"Independently verified {decisive.name} and wrote {out}."}

    q = FakeQueue([s1, s2])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]), poll_sec=0.01)
    m = M.create_mission(goal)
    await runner.drive(m["id"])

    m = M.get_mission(m["id"])
    check("T5-D: parent-verified mission completes done", m["state"] == "done", str(m.get("failure_reason")))
    check("T5-D: parent step received decisive file input & wrote verified summary",
          out.exists() and "VALUE: 42" in out.read_text(encoding="utf-8")
          and m["subtasks"][1]["verified_status"] == "verified")


async def main():
    await case_t5a_parallel_disjoint_sweep()
    await case_t5b_failure_in_denominator()
    case_t5c_envelopes_and_nesting()
    await case_t5d_parent_verification_mission()
    skip_live("T5-LIVE-1: live orchestrator writes its own disjoint worker briefs for 40-file sweep",
              "requires live LLM + pinned Mac scratch instance + family key ceremony")
    skip_live("T5-LIVE-2: live parent independently selects decisive claims to spot-check",
              "requires live LLM + pinned Mac scratch instance")

    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed "
          f"({len(skipped)} live-only skipped) ==========", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        return 1
    print("ALL TIER 5 GRADING RIG CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
