"""
Missions harness fix (2026-11-08): a question nobody answers must not
kill a mission when the question carries its own best answer.

Live record (the owner's Mac, shipped 1.0.2, mission-5bf2c71b83): a locate
hunt ended in "ask" with two close matches sharing one file name
(class-fq-name-to-source.tab) at different paths. Nobody answered;
the fix round 5 supervision spent its one automatic resume, the task
waited again past the resume window, and the mission failed. The
person's standing needs-you rule says the opposite for exactly this
shape: the AI guesses the strongest match and keeps moving, logging
the guess.

Covered here (the paused-wedge harness, thresholds injected short):
  - the live shape: past the threshold, the strongest candidate
    (highest score) is picked, the task is finished through the
    queue's own finish path with the guess written into its result
    (both paths named, the pick marked as a guess), and the mission
    completes instead of failing. No retry is spent.
  - a question with no locate payload behind it keeps the old
    honest shape: one resume attempt, then failure past the window.
  - an approval wait is never auto-answered, payload or not.
  - a blocked wait is not a question and is never auto-answered.
  - a question answered in time is never touched.

Run: .venv/bin/python tests/brain/run_missions_question_autoanswer_test.py
"""
import asyncio, json, os, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_missions_autoanswer_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.missions as M

M.DB = TMP / "xod.db"
M._spawn = lambda mid: None   # tests drive missions by hand

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


# --------------------------------------------------------------------- fakes
class FakeAsk:
    def __init__(self, replies):
        self.replies = list(replies)

    async def __call__(self, prompt):
        return self.replies.pop(0) if self.replies else "{}"


class AnswerQueue:
    """The wedge suite's queue shape, plus the queue's own finish path
    (_finish), which is how a resolved question closes for real."""

    def __init__(self, behaviors, retry_result=None):
        self.behaviors = list(behaviors)
        self.created = []
        self.tasks = {}
        self.retry_calls = []
        self.finished = []
        self.retry_result = {"success": True} if retry_result is None else retry_result

    def create(self, request, title=None, origin=None):
        tid = f"task-fake{len(self.created) + 1}"
        self.created.append({"request": request, "title": title})
        self.tasks[tid] = {"behavior": self.behaviors[len(self.created) - 1], "gets": 0}
        return {"id": tid, "status": "queued", "title": title}

    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None:
            return None
        rec["gets"] += 1
        return rec["behavior"](self, tid, rec)

    def retry(self, tid):
        self.retry_calls.append(tid)
        return dict(self.retry_result)

    def _finish(self, tid, status, report, note=None, **fields):
        self.finished.append({"id": tid, "status": status, "report": report, "note": note})
        ev = self.tasks[tid]["behavior"](self, tid, self.tasks[tid]).get("evidence_json")
        self.tasks[tid]["behavior"] = lambda q, t, rec: {
            "id": t, "status": "done", "result": report, "evidence_json": ev}


ASK_PATH_A = "/Users/owner/work/alpha/class-fq-name-to-source.tab"
ASK_PATH_B = "/Users/owner/work/beta/class-fq-name-to-source.tab"


def ask_evidence(score_a=812.0, score_b=830.0):
    payload = {"status": "ask",
               "question": "Two close matches: class-fq-name-to-source.tab and "
                           "class-fq-name-to-source.tab. Which one did you mean?",
               "candidates": [
                   {"path": ASK_PATH_A, "name": "class-fq-name-to-source.tab",
                    "kind": "file", "is_folder": False, "score": score_a,
                    "modified": "2026-10-02", "size": 4096, "why": ["name matches"]},
                   {"path": ASK_PATH_B, "name": "class-fq-name-to-source.tab",
                    "kind": "file", "is_folder": False, "score": score_b,
                    "modified": "2026-11-01", "size": 4096, "why": ["name matches"]}]}
    return [{"tool": "locate_files", "payload": payload}]


def waiting(kind, evidence=None, message="Needs a person."):
    def behave(q, tid, rec):
        t = {"id": tid, "status": "waiting", "waiting_kind": kind,
             "waiting_detail": {"message": message}, "result": ""}
        if evidence is not None:
            t["evidence_json"] = evidence
        return t
    return behave


def waiting_then_done(gets_needed, evidence=None):
    def behave(q, tid, rec):
        if rec["gets"] <= gets_needed:
            return waiting("question", evidence)(q, tid, rec)
        t = {"id": tid, "status": "done", "result": "All done."}
        if evidence is not None:
            t["evidence_json"] = evidence
        return t
    return behave


def plan_json(subs):
    return json.dumps({"subtasks": subs})


def make_runner(behaviors, clock, retry_result=None):
    q = AnswerQueue(behaviors, retry_result=retry_result)
    runner = M.MissionRunner(queue=q, ask=FakeAsk([plan_json([
        {"title": "Find the tab file",
         "instruction": "Find the file class-fq-name-to-source.tab on this computer.",
         "expected_effects": []}])]), poll_sec=0.01)
    runner.pause_threshold_sec = 5.0
    runner.pause_resume_window_sec = 5.0
    runner.clock = lambda: clock["t"]
    return runner, q


# ------------------------------------------------------------- the live shape
async def case_live_shape():
    clock = {"t": 1000.0}
    runner, q = make_runner([waiting("question", ask_evidence())], clock)
    m = M.create_mission("find the tab file")
    mid = m["id"]
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("live: the mission pauses on the question", m["state"] == "paused", m["state"])

    clock["t"] += 4   # below the threshold: an ordinary wait so far
    await runner.drive(mid)
    check("live: below the threshold nothing is answered or retried",
          len(q.finished) == 0 and len(q.retry_calls) == 0,
          f"finished={len(q.finished)} retries={len(q.retry_calls)}")

    clock["t"] += 2   # past the threshold
    await runner.drive(mid)
    m = M.get_mission(mid)
    s = m["subtasks"][0]
    check("live: the question was answered through the queue's finish path",
          len(q.finished) == 1 and q.finished[0]["status"] == "done",
          f"finished={q.finished}")
    if q.finished:
        report = q.finished[0]["report"]
        check("live: the strongest match (highest score) is the pick",
              ASK_PATH_B in report and "strongest" in report, report[:300])
        check("live: the guess is logged as a guess, with the other match named",
              "guess" in report.lower() and ASK_PATH_A in report, report[:300])
    check("live: no retry was spent answering the question",
          len(q.retry_calls) == 0, f"retries={len(q.retry_calls)}")
    check("live: the mission completes instead of dying on the question",
          m["state"] == "done" and s["status"] == "done",
          f"state={m['state']} sub={s['status']} reason={m['failure_reason']}")
    check("live: the step's recorded result carries the pick",
          ASK_PATH_B in (s["claimed"] or ""), (s["claimed"] or "")[:200])


async def case_no_payload_keeps_old_shape():
    clock = {"t": 2000.0}
    runner, q = make_runner([waiting("question")], clock)
    m = M.create_mission("find the tab file")
    mid = m["id"]
    await runner.drive(mid)
    clock["t"] += 6
    await runner.drive(mid)
    check("no payload: the one automatic resume is attempted as before",
          len(q.retry_calls) == 1 and len(q.finished) == 0,
          f"retries={len(q.retry_calls)} finished={len(q.finished)}")
    clock["t"] += 6
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("no payload: past the resume window the mission still fails honestly",
          m["state"] == "failed" and len(q.finished) == 0,
          f"state={m['state']} finished={len(q.finished)}")


async def case_approval_never_answered():
    clock = {"t": 3000.0}
    runner, q = make_runner([waiting("approval", ask_evidence(), "Waiting for your OK.")], clock)
    m = M.create_mission("find the tab file")
    mid = m["id"]
    await runner.drive(mid)
    clock["t"] += 100000
    await runner.drive(mid)
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("approval: never auto-answered and never timed out",
          len(q.finished) == 0 and len(q.retry_calls) == 0 and m["state"] == "paused",
          f"finished={len(q.finished)} retries={len(q.retry_calls)} state={m['state']}")


async def case_blocked_not_answered():
    clock = {"t": 4000.0}
    runner, q = make_runner([waiting("blocked", ask_evidence(), "Stuck at a check.")], clock)
    m = M.create_mission("find the tab file")
    mid = m["id"]
    await runner.drive(mid)
    clock["t"] += 6
    await runner.drive(mid)
    check("blocked: a blocked wait is not auto-answered (the resume path runs instead)",
          len(q.finished) == 0 and len(q.retry_calls) == 1,
          f"finished={len(q.finished)} retries={len(q.retry_calls)}")


async def case_answered_in_time():
    clock = {"t": 5000.0}
    runner, q = make_runner([waiting_then_done(1, ask_evidence())], clock)
    m = M.create_mission("find the tab file")
    mid = m["id"]
    await runner.drive(mid)
    clock["t"] += 1
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("in time: a question answered in time is never auto-answered",
          m["state"] == "done" and len(q.finished) == 0 and len(q.retry_calls) == 0,
          f"state={m['state']} finished={len(q.finished)} retries={len(q.retry_calls)}")


async def main():
    await case_live_shape()
    await case_no_payload_keeps_old_shape()
    await case_approval_never_answered()
    await case_blocked_not_answered()
    await case_answered_in_time()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1 if failed else 0)


asyncio.run(main())
