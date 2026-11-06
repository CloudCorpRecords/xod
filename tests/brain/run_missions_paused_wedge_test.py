"""
Missions fix round 5 (Lane 4): the paused-mission wedge.

Regression suite for the 2026-10-31 Tier 1 re-run 4 record
(ESCALATION-T1-RERUN4-2026-10-31.md, wobble T1B): mission-d960ff04ca, a
research mission, sat in state paused for the full 900 second
measurement cap and never produced a report; no output file existed.
The identical mission completed in about a minute in other graded
attempts: across five runs it wedged twice and completed twice at
about a minute, with nothing in between.

The shape in the code (app/services/missions.py): a mission pauses
when its current subtask's task reports status waiting, and the
background driver re-drives paused missions every couple of seconds,
so a wait nobody answers is re-entered forever. Nothing kept a clock
on the pause and nothing ever gave it a terminal shape: no resume, no
timeout, no failure verdict, just silence.

Covered here, with the supervision thresholds and clock injected
short (the runner's pause_threshold_sec, pause_resume_window_sec and
clock attributes; on the unfixed code those attributes are inert and
the remedy checks below fail in exactly the wedge shape):

  - a wedged mission: below the threshold it is simply paused and no
    resume is attempted; past the threshold exactly one resume is
    attempted, through the queue's own retry path; when the task
    waits again past the resume window the mission fails honestly,
    with the verdict naming the wedge, the wait and the one resume;
    no second resume is ever attempted.
  - a positive control: a task that waits until the resume and then
    proceeds completes the mission normally.
  - a resume attempt that itself fails fails the mission honestly at
    once, naming the failed attempt.
  - an approval wait is a person's gate: supervision never resumes
    it and never times it out, however long it sits.
  - a wait answered in time (the task proceeds before the threshold)
    is never touched by supervision at all.

Run: .venv/bin/python tests/brain/run_missions_paused_wedge_test.py
"""
import asyncio, json, os, sys, tempfile, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_missions_paused_"))
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
        self.prompts = []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "{}"


class WedgeQueue:
    """The fix4 FakeQueue shape, plus the queue's retry path, which is
    the resume mechanism the supervision uses. Each task's get()
    behavior is a callable over (queue, task_id, record), so a script
    can wait forever, wait until retried, or wait for an approval."""

    def __init__(self, behaviors, retry_result=None):
        self.behaviors = list(behaviors)
        self.created = []
        self.tasks = {}
        self.retry_calls = []
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


def waiting(kind, message="Needs a person."):
    def behave(q, tid, rec):
        return {"id": tid, "status": "waiting", "waiting_kind": kind,
                "waiting_detail": {"message": message}, "result": ""}
    return behave


def waiting_until_retried(kind):
    def behave(q, tid, rec):
        if tid in q.retry_calls:
            return {"id": tid, "status": "done", "result": "All done."}
        return {"id": tid, "status": "waiting", "waiting_kind": kind,
                "waiting_detail": {"message": "Needs a person."}, "result": ""}
    return behave


def waiting_then_done(gets_needed):
    def behave(q, tid, rec):
        if rec["gets"] <= gets_needed:
            return {"id": tid, "status": "waiting", "waiting_kind": "question",
                    "waiting_detail": {"message": "Needs a person."}, "result": ""}
        return {"id": tid, "status": "done", "result": "All done."}
    return behave


def plan_json(subs):
    return json.dumps({"subtasks": subs})


def sub(title, instruction, effects=None):
    return {"title": title, "instruction": instruction, "expected_effects": effects or []}


def make_runner(behaviors, clock, retry_result=None):
    """A runner with the supervision clock and thresholds injected
    short: a 5 second wedge threshold and a 5 second resume window,
    on a fake clock the case advances by hand between drives (the
    production driver re-drives paused missions on its own loop; here
    each drive call is one of those re-drives)."""
    q = WedgeQueue(behaviors, retry_result=retry_result)
    runner = M.MissionRunner(queue=q, ask=FakeAsk(
        [plan_json([sub("Gather the reference", "Look up the reference value and record it.")])]),
        poll_sec=0.01)
    runner.pause_threshold_sec = 5.0
    runner.pause_resume_window_sec = 5.0
    runner.clock = lambda: clock["t"]
    return runner, q


# ------------------------------------------------------------- the wedge
async def case_wedge_fails_honestly():
    clock = {"t": 1000.0}
    runner, q = make_runner([waiting("blocked", "Stuck at a verification check.")], clock)
    m = M.create_mission("compile a short reference note")
    mid = m["id"]

    await runner.drive(mid)
    m = M.get_mission(mid)
    check("wedge: a waiting subtask pauses the mission",
          m["state"] == "paused" and m["subtasks"][0]["status"] == "waiting", m["state"])

    clock["t"] += 4   # below the 5s threshold: an ordinary wait so far
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("wedge: below the threshold the mission is simply paused, no resume attempted",
          m["state"] == "paused" and len(q.retry_calls) == 0,
          f"state={m['state']} retries={len(q.retry_calls)}")

    clock["t"] += 2   # 6s into the wait: past the threshold
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("wedge: past the threshold exactly one resume is attempted through the queue retry path",
          len(q.retry_calls) == 1, f"retries={len(q.retry_calls)}")
    check("wedge: the resumed task waits again, so the mission is paused, not failed early",
          m["state"] == "paused", m["state"])

    clock["t"] += 4   # 4s into the second wait: inside the resume window
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("wedge: inside the resume window the mission is still only paused",
          m["state"] == "paused" and len(q.retry_calls) == 1,
          f"state={m['state']} retries={len(q.retry_calls)}")

    clock["t"] += 2   # 6s into the second wait: the window has closed
    await runner.drive(mid)
    m = M.get_mission(mid)
    s = m["subtasks"][0]
    check("wedge: past the resume window the mission fails instead of sitting paused forever",
          m["state"] == "failed", m["state"])
    check("wedge: the subtask is recorded failed, with the wedge in its evidence",
          s["status"] == "failed" and any("paused" in e for e in s["evidence"]),
          f"status={s['status']} evidence={s['evidence']}")
    reason = m["failure_reason"] or ""
    check("wedge: the failure verdict names the wedge, the wait and the one resume",
          "paused" in reason and "blocked" in reason and "resume" in reason,
          f"reason={reason}")

    clock["t"] += 100000   # far future: nothing more may happen
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("wedge: no second resume is ever attempted and the failure stands",
          len(q.retry_calls) == 1 and m["state"] == "failed",
          f"retries={len(q.retry_calls)} state={m['state']}")


async def case_resume_recovers():
    """Positive control: the task waits because its page was stuck; the
    one automatic resume unsticks it and the mission completes."""
    clock = {"t": 2000.0}
    runner, q = make_runner([waiting_until_retried("blocked")], clock)
    m = M.create_mission("compile a short reference note")
    mid = m["id"]
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("recover: the mission pauses on the waiting task", m["state"] == "paused", m["state"])
    clock["t"] += 6
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("recover: exactly one resume was attempted", len(q.retry_calls) == 1,
          f"retries={len(q.retry_calls)}")
    check("recover: a mission that proceeds after the resume completes normally",
          m["state"] == "done" and m["failure_reason"] is None,
          f"state={m['state']} reason={m['failure_reason']}")


async def case_resume_attempt_fails():
    clock = {"t": 3000.0}
    runner, q = make_runner([waiting("question")], clock,
                            retry_result={"success": False, "error": "Only stopped tasks can be retried."})
    m = M.create_mission("compile a short reference note")
    mid = m["id"]
    await runner.drive(mid)
    clock["t"] += 6
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("resume failure: the one resume was attempted", len(q.retry_calls) == 1,
          f"retries={len(q.retry_calls)}")
    check("resume failure: a failed resume attempt fails the mission honestly at once",
          m["state"] == "failed" and "resume attempt failed" in (m["failure_reason"] or ""),
          f"state={m['state']} reason={m['failure_reason']}")


async def case_approval_never_touched():
    """An approval wait is a person's gate, not a wedge: supervision
    must neither resume it nor time it out, however long it sits."""
    clock = {"t": 4000.0}
    runner, q = make_runner([waiting("approval", "Waiting for your OK.")], clock)
    m = M.create_mission("compile a short reference note")
    mid = m["id"]
    await runner.drive(mid)
    clock["t"] += 100000
    await runner.drive(mid)
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("approval: a mission waiting on approval is never auto-resumed",
          len(q.retry_calls) == 0, f"retries={len(q.retry_calls)}")
    check("approval: a mission waiting on approval is never timed out",
          m["state"] == "paused" and m["failure_reason"] is None,
          f"state={m['state']} reason={m['failure_reason']}")


async def case_answered_in_time():
    """The ordinary pause: the wait clears before the threshold (here
    the task simply proceeds on the next look), so supervision never
    fires and the mission completes untouched."""
    clock = {"t": 5000.0}
    runner, q = make_runner([waiting_then_done(1)], clock)
    m = M.create_mission("compile a short reference note")
    mid = m["id"]
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("answered: the mission pauses while the task waits", m["state"] == "paused", m["state"])
    clock["t"] += 1
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("answered: the mission resumes and finishes with no supervision resume spent",
          m["state"] == "done" and len(q.retry_calls) == 0,
          f"state={m['state']} retries={len(q.retry_calls)}")


async def main():
    await case_wedge_fails_honestly()
    await case_resume_recovers()
    await case_resume_attempt_fails()
    await case_approval_never_touched()
    await case_answered_in_time()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1 if failed else 0)


asyncio.run(main())
