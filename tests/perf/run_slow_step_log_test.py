"""
Slow-step logging (Tier 6 D4, 2026-11-08): a step that takes over a
second is named in the log; a fast one is not.

Tier 6's load leg froze the server (hello p95 4.4s, worst 14.5s)
and nothing in Xod's records could say which step was running: plan
items carried timestamps but no duration record existed. Both step
engines now emit one when a step crosses 1000 ms: a warning in the
log and an event (task.slow_step / mission.slow_step) carrying the
step id, the step index, the duration, the tools the step used, and
the task / mission id.

Covered here:
  - a stepwise task with one slow step (1.3 s) and one fast step:
    exactly one task.slow_step event, for the slow step, with its
    duration, its plan step id, and computer_write_file among its
    tools; a "slow step" warning was logged for it.
  - a mission whose first subtask's task takes 1.2 s and whose
    second is instant: exactly one mission.slow_step event, for
    step 1, carrying the mission id.
  - fast work alone records nothing (the fast steps above).

Run: .venv/bin/python tests/perf/run_slow_step_log_test.py
"""
import asyncio, json, logging, os, re, sys, tempfile, time, uuid
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_slowstep_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.task_queue as TQ
import app.services.orchestrator as O
import app.services.missions as M
import app.agent.researcher as RS
from app.services import activity, event_log

TQ.notify = lambda title, msg: None
TQ._phone = lambda task, kind: None
M.DB = TMP / "xod.db"
M._spawn = lambda mid: None

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


WARNINGS = []


class _Capture(logging.Handler):
    def emit(self, record):
        if "slow step" in record.getMessage():
            WARNINGS.append(record.getMessage())


logging.getLogger("xod.orchestrator").addHandler(_Capture())
logging.getLogger("xod.missions").addHandler(_Capture())

# ---------------------------------------------------------------- orchestrator
STEP_RE = re.compile(r"YOUR STEP NOW \(step \d+ of \d+\):\n(.+?)\n\nDo only this step", re.S)
WRITE_RE = re.compile(r"Create the file (\S+) containing exactly this: (\S+)")
SLOW_SECONDS = 1.3


def _record_write(path, content):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    c = activity._conn()
    c.execute("INSERT INTO activity (id, ts, origin, tool, title, detail, ok, undo, undoable) VALUES (?,?,?,?,?,?,?,?,?)",
              (uuid.uuid4().hex, time.time(), activity.origin.get(), "computer_write_file", "Wrote a file",
               str(path), 1, json.dumps([{"kind": "write_file", "path": str(path)}]), 1))
    c.commit()
    c.close()


class FakeResearcher:
    def __init__(self, *a, **k):
        pass

    async def run_research_stream(self, messages, **k):
        msg = messages[-1]["content"]
        m = STEP_RE.search(msg)
        step = m.group(1).strip() if m else msg
        w = WRITE_RE.search(step)
        if w:
            if "slow" in Path(w.group(1)).name:
                await asyncio.sleep(SLOW_SECONDS)
            _record_write(w.group(1), w.group(2))
            yield {"type": "content", "content": f"Wrote {w.group(1)}."}
            return
        yield {"type": "content", "content": f"Did: {step[:80]}"}


RS.AgenticResearcher = FakeResearcher


async def fake_planner(request):
    return [l.strip("- ").strip() for l in request.split("\n") if l.strip().startswith("- ")]


O.planner = fake_planner


def write_step(path, content):
    return f"Create the file {path} containing exactly this: {content}"


async def wait_status(q, tid, statuses, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = q.get(tid)
        if t["status"] in statuses:
            return t
        await asyncio.sleep(0.05)
    raise TimeoutError(f"{tid} stuck at {q.get(tid)['status']}")


async def case_orchestrator(q):
    print("orchestrator: a slow step is named, a fast one is not")
    d = TMP / "work" / f"slow-{uuid.uuid4().hex[:4]}"
    d.mkdir(parents=True)
    ack = O.submit("Do these steps:\n"
                   f"- {write_step(d / 'slow-one.txt', 'S')}\n"
                   f"- {write_step(d / 'fast-two.txt', 'F')}")
    tid = ack["task_id"]
    t = await wait_status(q, tid, ("done", "failed"))
    check("orch: the task completes", t["status"] == "done", t["status"])
    items, _, _ = O.load_plan(tid)
    evs = event_log.query(kinds=["task.slow_step"], task_id=tid)["events"]
    check("orch: exactly one slow-step event was recorded", len(evs) == 1, f"events={len(evs)}")
    if evs:
        p = evs[0]["payload"]
        check("orch: the event is the slow step, with its duration and tools",
              p.get("step_id") == items[0]["id"] and (p.get("duration_ms") or 0) >= 1000
              and "computer_write_file" in (p.get("tools") or []),
              json.dumps(p)[:300])
    check("orch: a 'slow step' warning was logged for the task",
          any(tid in w and "slow" in w for w in WARNINGS), str(WARNINGS))


# -------------------------------------------------------------------- missions
class FakeAsk:
    def __init__(self, replies):
        self.replies = list(replies)

    async def __call__(self, prompt):
        return self.replies.pop(0) if self.replies else "{}"


class SlowQueue:
    """The wedge suites' fake queue shape; the first task's first poll
    takes 1.2 s (a slow step), everything else is instant."""

    def __init__(self):
        self.created = []
        self.tasks = {}
        self.slept = False

    def create(self, request, title=None, origin=None):
        tid = f"task-fake{len(self.created) + 1}"
        self.created.append({"request": request, "title": title})
        self.tasks[tid] = {"slow": len(self.created) == 1}
        return {"id": tid, "status": "queued", "title": title}

    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None:
            return None
        if rec["slow"] and not self.slept:
            self.slept = True
            time.sleep(1.2)
        return {"id": tid, "status": "done", "result": "All done.",
                "agent_steps": json.dumps([{"name": "computer_write_file"}])}


async def case_missions():
    print("missions: a slow step is named, a fast one is not")
    subs = [{"title": "Slow gather", "instruction": "Gather the slow reference.",
             "expected_effects": []},
            {"title": "Fast gather", "instruction": "Gather the fast reference.",
             "expected_effects": []}]
    q = SlowQueue()
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps({"subtasks": subs})]), poll_sec=0.01)
    m = M.create_mission("gather two references")
    mid = m["id"]
    await runner.drive(mid)
    m = M.get_mission(mid)
    check("missions: the mission completes", m["state"] == "done", m["state"])
    evs = event_log.query(kinds=["mission.slow_step"], mission_id=mid)["events"]
    check("missions: exactly one slow-step event was recorded", len(evs) == 1, f"events={len(evs)}")
    if evs:
        p = evs[0]["payload"]
        check("missions: the event is step 1, with its duration and tools",
              p.get("step_index") == 1 and (p.get("duration_ms") or 0) >= 1000
              and "computer_write_file" in (p.get("tools") or []),
              json.dumps(p)[:300])
    check("missions: a 'slow step' warning was logged for the mission",
          any(mid in w for w in WARNINGS), str(WARNINGS))


async def main():
    q = TQ.task_queue = TQ.TaskQueue()
    q.start()
    try:
        await case_orchestrator(q)
    finally:
        q.stop()
    await case_missions()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1 if failed else 0)


asyncio.run(main())
