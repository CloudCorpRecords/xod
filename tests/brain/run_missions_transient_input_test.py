"""Cluster 5 (2026-11-14), from the Tier 3 live grade T3-L2
(LIVE-GRADES-2026-11-13.md, mission-ed87b01806):

  The probe planted feed-a.txt present and feed-b.txt ABSENT,
  appearing at +40s, to exercise the Tier 3 assertion "a transient
  fault in the middle source is retried alone". The planner declared
  feed-b.txt as a file input of step 1 (correctly: the goal stated
  both feeds as present, and the plan prompt's rule is to declare a
  file input whenever a step's work depends on it), and mission
  assembly's declared-input gate stopped the step before any task
  was created. The fail-fast is the designed T1N honesty behavior
  for genuinely undeliverable declared inputs; what was missing was
  any bounded retry for a source that is only transiently absent,
  so the behavior under test was never exercised (graded UNKNOWN).

  The fix (app/services/missions.py): when a step's declared file
  input cannot be read and the AGE-37 adaptation does not apply,
  assembly is retried for that step alone over a bounded window
  (DECLARED_FILE_RETRY_WINDOW_SEC, injectable on MissionRunner).
  The step still never starts on an undeliverable input: a task is
  created only once every declared input is actually delivered
  into the request. If the window expires, the original assembly
  stop stands unchanged and the mission fails honestly.

This suite pins both shapes deterministically (scripted queue, no
live LLM):
  - Case 1 (the T3-L2 probe shape): feed-b.txt absent at assembly,
    appearing shortly after. The mission now reaches execution,
    step 1's task is created exactly once, with feed-b's actual
    contents delivered as its declared input and a retry note
    naming the wait; merged.txt and digest.txt land exactly.
  - Case 2 (behavior preservation): a declared input that never
    appears still fail-fasts honestly after the bounded window:
    no task is created, nothing is fabricated, the failure reason
    is the original assembly stop, and the failure arrives in
    bounded time (the window), not after a hang. This case's
    checks also pass on the base tree (where the stop is simply
    immediate); it documents preserved behavior, not new behavior.
  - Case 3 (gate constants): the shipped default window must
    cover the graded probe's shape (source at +40s, assembly
    reached within the first ~8s, so the window must exceed the
    ~35s assembly had to wait).

On the base tree (7d6c462) case 1 and case 3 fail: assembly stops
the mission before any task is created, and the retry surface
does not exist.

Run: python tests/brain/run_missions_transient_input_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_t3_transient_"))
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


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


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
        tid = f"task-tr-{len(self.created) + 1}"
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


FEED_A = "alpha feed line\n"
FEED_B = "beta feed line\n"


def _probe_plan(ws: Path):
    fa, fb = str(ws / "feed-a.txt"), str(ws / "feed-b.txt")
    merged, digest = str(ws / "merged.txt"), str(ws / "digest.txt")
    return {"subtasks": [
        {"title": "Merge feeds",
         "instruction": f"Read {fa} and {fb} and write {merged} containing exactly "
                        f"the contents of feed-a.txt followed by the contents of feed-b.txt.",
         "expected_effects": [{"kind": "create", "path": merged, "content": None}],
         "inputs": [{"source": "goal"}, {"source": "file", "ref": fa},
                    {"source": "file", "ref": fb}],
         "tools": ["computer_read_file", "computer_write_file"]},
        {"title": "Write digest",
         "instruction": f"Write {digest} containing exactly 'Feeds merged' and nothing else.",
         "expected_effects": [{"kind": "create", "path": digest, "content": None}],
         "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
         "tools": ["computer_write_file"]},
    ]}


def _probe_goal(ws: Path):
    return (f"Work in the folder {ws}. There are two feed files, feed-a.txt and "
            f"feed-b.txt. Create merged.txt in that folder containing exactly the "
            f"contents of feed-a.txt followed by the contents of feed-b.txt, with "
            f"no extra text. Also create digest.txt containing exactly "
            f"'Feeds merged' and nothing else, after merged.txt exists.")


def _make_runner(q, plan, window, poll=0.05):
    """MissionRunner with a short injected retry window. On a tree
    without the retry surface the kwargs are rejected; fall back to
    the plain runner so the case's checks (not a crash) record the
    failure."""
    try:
        return M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]), poll_sec=0.01,
                               file_input_retry_window_sec=window,
                               file_input_retry_poll_sec=poll)
    except TypeError:
        return M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]), poll_sec=0.01)


async def case_transient_source_arrives():
    print("== Case 1: T3-L2 probe shape, declared source absent at assembly, appearing shortly after ==", flush=True)
    ws = TMP / "probe"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "feed-a.txt").write_text(FEED_A, encoding="utf-8")
    # feed-b.txt deliberately absent, exactly as planted.
    assert not (ws / "feed-b.txt").exists()

    def plant_b():
        time.sleep(1.0)
        (ws / "feed-b.txt").write_text(FEED_B, encoding="utf-8")

    def step1(rec):
        a = (ws / "feed-a.txt").read_text(encoding="utf-8")
        b = (ws / "feed-b.txt").read_text(encoding="utf-8")
        (ws / "merged.txt").write_text(a + b, encoding="utf-8")
        return {"id": rec["id"], "title": rec["title"], "request": rec["request"],
                "status": "done", "result": "Merged feed-a.txt and feed-b.txt into merged.txt.",
                "tool_calls": [{"name": "computer_write_file", "ok": True,
                                "path": str(ws / "merged.txt")}]}

    def step2(rec):
        (ws / "digest.txt").write_text("Feeds merged", encoding="utf-8")
        return {"id": rec["id"], "title": rec["title"], "request": rec["request"],
                "status": "done", "result": "Wrote digest.txt.",
                "tool_calls": [{"name": "computer_write_file", "ok": True,
                                "path": str(ws / "digest.txt")}]}

    q = FakeQueue([step1, step2])
    runner = _make_runner(q, _probe_plan(ws), window=10.0)
    m = M.create_mission(_probe_goal(ws))
    planter = threading.Thread(target=plant_b, daemon=True)
    planter.start()
    await runner.drive(m["id"])
    planter.join(timeout=5)
    m = M.get_mission(m["id"])
    subs = m["subtasks"]
    check("the mission completes done once the transient source appears",
          m["state"] == "done", f"state={m['state']} reason={m.get('failure_reason')}")
    check("step 1's task was created exactly once (assembly retried, the step was not duplicated)",
          len(q.created) == 2, f"created={len(q.created)}")
    req1 = q.created[0]["request"] if q.created else ""
    check("feed-b's actual contents were delivered to step 1 as its declared input",
          "beta feed line" in req1)
    check("step 1's request records the bounded assembly retry that delivered its input",
          "bounded" in req1 and "could not be read when the step was" in req1,
          req1[-400:] if req1 else "no request")
    check("step 1's recorded input delivery covers feed-b.txt",
          any(d.get("ref") == str(ws / "feed-b.txt") for d in (subs[0].get("input_delivery") or [])),
          str(subs[0].get("input_delivery") if subs else None))
    check("merged.txt is exactly feed-a followed by feed-b",
          (ws / "merged.txt").exists()
          and (ws / "merged.txt").read_text(encoding="utf-8") == FEED_A + FEED_B)
    check("digest.txt is exactly 'Feeds merged', written after merged.txt existed",
          (ws / "digest.txt").exists()
          and (ws / "digest.txt").read_text(encoding="utf-8") == "Feeds merged")


async def case_genuinely_missing_still_fail_fasts():
    print("== Case 2: a declared input that never arrives still fail-fasts honestly (preserved) ==", flush=True)
    ws = TMP / "never"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "feed-a.txt").write_text(FEED_A, encoding="utf-8")
    (ws / "notes.txt").write_text("nothing else here\n", encoding="utf-8")
    q = FakeQueue([])
    runner = _make_runner(q, _probe_plan(ws), window=1.0)
    m = M.create_mission(_probe_goal(ws))
    started = time.monotonic()
    await runner.drive(m["id"])
    elapsed = time.monotonic() - started
    m = M.get_mission(m["id"])
    subs = m["subtasks"]
    check("the mission fails honestly when the declared input never arrives",
          m["state"] == "failed" and "could not be read" in (m.get("failure_reason") or ""),
          f"state={m['state']} reason={m.get('failure_reason')}")
    check("the failure names the undeliverable declared input (feed-b.txt)",
          "feed-b.txt" in (m.get("failure_reason") or ""), str(m.get("failure_reason")))
    check("no task was ever created for the undelivered step or its dependent",
          len(q.created) == 0 and subs and subs[0]["task_id"] is None,
          f"created={len(q.created)}")
    check("nothing was fabricated: no merged.txt, no digest.txt, feed-b.txt never invented",
          not (ws / "merged.txt").exists() and not (ws / "digest.txt").exists()
          and not (ws / "feed-b.txt").exists())
    check("the failure is bounded, not a hang (well under 30s with a 1s injected window)",
          elapsed < 30, f"elapsed={elapsed:.1f}s")


def case_gate_constants():
    print("== Case 3: the shipped retry window covers the graded probe's shape ==", flush=True)
    window = getattr(M, "DECLARED_FILE_RETRY_WINDOW_SEC", None)
    poll = getattr(M, "DECLARED_FILE_RETRY_POLL_SEC", None)
    check("the default retry window exceeds the ~35s the graded probe's assembly had to wait",
          isinstance(window, (int, float)) and window > 35,
          f"window={window}")
    check("the retry window is bounded (under the 600s wedge scale this module uses)",
          isinstance(window, (int, float)) and window < 600,
          f"window={window}")
    check("the retry poll is a positive, sane interval",
          isinstance(poll, (int, float)) and 0 < poll <= 10,
          f"poll={poll}")


async def main():
    await case_transient_source_arrives()
    await case_genuinely_missing_still_fail_fasts()
    case_gate_constants()
    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed ==========", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        return 1
    print("ALL TRANSIENT DECLARED-INPUT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
