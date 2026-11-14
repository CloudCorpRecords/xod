"""AGE-46 Tier 3 grading rig: One agent sequence (TIER-DEEP-DIVES.md, tier-03.md).

Encodes Tier 3's deep-dive mission shape at the tool-contract level using
planted workspaces and scripted step outcomes through the real MissionRunner,
plan validator, step assembler, outcome gate, and report builder (no live LLM).

Contract points verified locally:
  - T3-A: Pre-execution plan recorded before any step runs; prior_step
    carry-forward delivers verified upstream outputs; destination read-back
    verifies report.txt; build_report totals match original request.
  - T3-B (load-bearing gate): A failed middle step stops the chain;
    downstream write step is never queued; no deliverable lands on disk;
    report separates completed, failed, and unattempted components.
  - T3-C (failing look-alike): A middle step admitting it could not read an
    input and estimating instead is rejected by the outcome gate; downstream
    steps do not execute on the ungrounded assumption.

Live-only assertions (marked SKIPPED with reason; executed in the future
pinned-Mac + family-key ceremony run):
  - Live LLM planner decomposing the goal into a dependency-ordered plan.
  - Live agent retrying only the failed middle source on transient timeout.

Run: python tests/brain/run_tier3_grading_test.py
"""
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_t3_grade_"))
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
    def __init__(self, behaviors, on_create=None):
        self.behaviors = list(behaviors)
        self.created = []
        self.tasks = {}
        self.on_create = on_create

    def create(self, request, title=None, origin=None):
        tid = f"task-t3-{len(self.created) + 1}"
        self.created.append({"id": tid, "request": request, "title": title})
        if self.on_create:
            self.on_create(tid, request, title)
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


def _make_sequence_plan(ws: Path):
    f1, f2, f3 = str(ws / "fig1.txt"), str(ws / "fig2.txt"), str(ws / "fig3.txt")
    out = str(ws / "report.txt")
    return {
        "subtasks": [
            {
                "title": "Gather figure 1",
                "instruction": f"Read {f1} and report the integer figure.",
                "expected_effects": [],
                "inputs": [{"source": "goal"}, {"source": "file", "ref": f1}],
                "tools": ["computer_read_file"],
            },
            {
                "title": "Gather figure 2",
                "instruction": f"Read {f2} and report the integer figure.",
                "expected_effects": [],
                "inputs": [{"source": "goal"}, {"source": "file", "ref": f2}, {"source": "prior_step", "ref": "1"}],
                "tools": ["computer_read_file"],
            },
            {
                "title": "Gather figure 3 and compute total",
                "instruction": f"Read {f3}, add it to the figures from prior steps, and report the total.",
                "expected_effects": [],
                "inputs": [{"source": "goal"}, {"source": "file", "ref": f3}, {"source": "prior_step", "ref": "1"}, {"source": "prior_step", "ref": "2"}],
                "tools": ["computer_read_file"],
            },
            {
                "title": "Write verified report",
                "instruction": f"Write {out} containing exactly 'Total: N' where N is the computed total.",
                "expected_effects": [{"kind": "create", "path": out, "content": None}],
                "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "3"}],
                "tools": ["computer_write_file"],
            },
        ]
    }


async def case_t3a_clean_sequence():
    print("== T3-A: clean 4-step sequence with pre-execution plan & destination verify ==", flush=True)
    ws = TMP / "t3a"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "fig1.txt").write_text("10\n", encoding="utf-8")
    (ws / "fig2.txt").write_text("25\n", encoding="utf-8")
    (ws / "fig3.txt").write_text("15\n", encoding="utf-8")
    plan = _make_sequence_plan(ws)
    goal = (f"In {ws}, read fig1.txt, fig2.txt, and fig3.txt, compute their sum, "
            f"and write 'Total: N' to {ws / 'report.txt'}.")

    plan_present_before_first_task = []

    def on_create(tid, req, title):
        if len(plan_present_before_first_task) == 0:
            cur = M.get_mission(m["id"])
            plan_present_before_first_task.append(bool(cur and cur.get("plan") and cur["plan"].get("subtasks")))

    def s1(rec):
        val = int((ws / "fig1.txt").read_text(encoding="utf-8").strip())
        return {
            "id": rec["id"], "title": rec["title"], "request": rec["request"],
            "status": "done", "result": f"Figure 1 is {val}.",
            "evidence": [{"tool": "computer_read_file", "path": str(ws / "fig1.txt"), "content": f"{val}"}],
        }

    def s2(rec):
        val = int((ws / "fig2.txt").read_text(encoding="utf-8").strip())
        return {
            "id": rec["id"], "title": rec["title"], "request": rec["request"],
            "status": "done", "result": f"Figure 2 is {val}.",
            "evidence": [{"tool": "computer_read_file", "path": str(ws / "fig2.txt"), "content": f"{val}"}],
        }

    def s3(rec):
        val = int((ws / "fig3.txt").read_text(encoding="utf-8").strip())
        m1 = re.search(r"Figure 1 is (\d+)", rec["request"])
        m2 = re.search(r"Figure 2 is (\d+)", rec["request"])
        total = int(m1.group(1)) + int(m2.group(1)) + val
        return {
            "id": rec["id"], "title": rec["title"], "request": rec["request"],
            "status": "done", "result": f"Figure 3 is {val}. Total is {total}.",
            "evidence": [{"tool": "computer_read_file", "path": str(ws / "fig3.txt"), "content": f"{val}"}],
        }

    def s4(rec):
        mt = re.search(r"Total is (\d+)", rec["request"])
        total = mt.group(1) if mt else "?"
        (ws / "report.txt").write_text(f"Total: {total}", encoding="utf-8")
        return {
            "id": rec["id"], "title": rec["title"], "request": rec["request"],
            "status": "done", "result": f"Wrote Total: {total} to {ws / 'report.txt'}.",
            "tool_calls": [{"name": "computer_write_file", "ok": True, "path": str(ws / "report.txt")}],
        }

    q = FakeQueue([s1, s2, s3, s4], on_create=on_create)
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]), poll_sec=0.01)
    m = M.create_mission(goal)
    await runner.drive(m["id"])

    m = M.get_mission(m["id"])
    rep = M.get_report(m["id"])
    check("T3-A: mission completes done", m["state"] == "done", f"state={m['state']} reason={m.get('failure_reason')}")
    check("T3-A: plan artifact existed before first step task was created",
          plan_present_before_first_task == [True], str(plan_present_before_first_task))
    check("T3-A: prior_step carry-forward delivered step 1 & 2 outputs to step 3",
          len(q.created) == 4 and "Figure 1 is 10." in q.created[2]["request"] and "Figure 2 is 25." in q.created[2]["request"])
    check("T3-A: report.txt landed on disk with exact total (Total: 50)",
          (ws / "report.txt").exists() and (ws / "report.txt").read_text(encoding="utf-8") == "Total: 50")
    check("T3-A: build_report confirms all 4 steps done and verified",
          rep["totals"]["subtasks"] == 4 and rep["totals"]["done"] == 4 and rep["totals"]["verified"] == 4,
          str(rep["totals"]))


async def case_t3b_middle_failure_stops_chain():
    print("== T3-B: failed middle step stops chain; downstream never fires ==", flush=True)
    ws = TMP / "t3b"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "fig1.txt").write_text("10\n", encoding="utf-8")
    (ws / "fig2.txt").write_text("25\n", encoding="utf-8")  # present for assembly, but step 2 fails (timeout)
    (ws / "fig3.txt").write_text("15\n", encoding="utf-8")
    plan = _make_sequence_plan(ws)
    goal = (f"In {ws}, read fig1.txt, fig2.txt, and fig3.txt, compute their sum, "
            f"and write 'Total: N' to {ws / 'report.txt'}.")

    s1 = {"status": "done", "result": "Figure 1 is 10.",
          "tool_calls": [{"name": "computer_read_file", "ok": True, "path": str(ws / "fig1.txt")}]}
    s2 = {"status": "failed", "result": "Timed out reading fig2.txt; figure 2 is unavailable."}
    s3 = {"status": "done", "result": "Should never run."}
    s4 = {"status": "done", "result": "Should never run."}

    q = FakeQueue([s1, s2, s3, s4])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]), poll_sec=0.01)
    m = M.create_mission(goal, continue_on_failure=False)
    await runner.drive(m["id"])

    m = M.get_mission(m["id"])
    rep = M.get_report(m["id"])
    subs = rep["subtasks"]
    check("T3-B: mission ends failed (not done)", m["state"] == "failed", f"state={m['state']}")
    check("T3-B: only steps 1 and 2 were queued; steps 3 and 4 never ran",
          len(q.created) == 2 and subs[2]["task_id"] is None and subs[3]["task_id"] is None,
          f"created={len(q.created)} s3={subs[2]['status']} s4={subs[3]['status']}")
    check("T3-B: report.txt was never written on disk", not (ws / "report.txt").exists())
    check("T3-B: report separates completed (1), failed (1), and unattempted (2)",
          rep["totals"]["done"] == 1 and rep["totals"]["failed"] == 1
          and subs[0]["status"] == "done" and subs[1]["status"] == "failed"
          and subs[2]["task_id"] is None and subs[3]["task_id"] is None
          and subs[2]["status"] not in ("done", "failed")
          and subs[3]["status"] not in ("done", "failed"),
          f"totals={rep['totals']} s3={subs[2]['status']} s4={subs[3]['status']}")
    check("T3-B: failure_reason names the failed middle step",
          "Gather figure 2" in (m.get("failure_reason") or "") or "step 2" in (m.get("failure_reason") or "").lower(),
          str(m.get("failure_reason")))


async def case_t3c_assumption_lookalike_rejected():
    print("== T3-C: failing look-alike (estimated middle figure) rejected by outcome gate ==", flush=True)
    ws = TMP / "t3c"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "fig1.txt").write_text("10\n", encoding="utf-8")
    (ws / "fig2.txt").write_text("25\n", encoding="utf-8")
    (ws / "fig3.txt").write_text("15\n", encoding="utf-8")
    plan = _make_sequence_plan(ws)
    goal = f"In {ws}, read fig1.txt, fig2.txt, and fig3.txt, compute their sum, and write {ws / 'report.txt'}."

    s1 = {"status": "done", "result": "Figure 1 is 10.",
          "tool_calls": [{"name": "computer_read_file", "ok": True, "path": str(ws / "fig1.txt")}]}
    # Step 2 claims done, but admits it could not complete reading fig2.txt
    s2 = {"status": "done", "result": "I could not complete reading fig2.txt due to an outage; assuming 25."}
    s3 = {"status": "done", "result": "Total is 50."}
    s4 = {"status": "done", "result": "Wrote report."}

    q = FakeQueue([s1, s2, s3, s4])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]), poll_sec=0.01)
    m = M.create_mission(goal, continue_on_failure=False)
    await runner.drive(m["id"])

    m = M.get_mission(m["id"])
    subs = m["subtasks"]
    check("T3-C: mission rejects the incomplete middle step and ends failed",
          m["state"] == "failed" and subs[1]["status"] == "failed",
          f"state={m['state']} s2={subs[1]['status']}")
    check("T3-C: downstream write step never executed on the assumed value",
          len(q.created) == 2 and not (ws / "report.txt").exists(),
          f"created={len(q.created)}")


async def main():
    await case_t3a_clean_sequence()
    await case_t3b_middle_failure_stops_chain()
    await case_t3c_assumption_lookalike_rejected()
    skip_live("T3-LIVE-1: LLM planner produces dependency-ordered plan from raw goal",
              "requires live LLM + pinned Mac scratch instance + family key ceremony")
    skip_live("T3-LIVE-2: agent retries only failed middle source on transient timeout",
              "requires live agent execution + pinned Mac scratch instance")

    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed "
          f"({len(skipped)} live-only skipped) ==========", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        return 1
    print("ALL TIER 3 GRADING RIG CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
