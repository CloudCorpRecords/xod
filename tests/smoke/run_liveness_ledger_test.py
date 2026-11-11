"""
The activity timer's ledger (services/liveness.py): activity from the
code's own choke points resets the timer, work in flight is named with
its last genuine progress, and the ledger lands on disk in the shape
the shell's review (electron/liveness.js) reads. No server, no network.
  .venv/bin/python tests/smoke/run_liveness_ledger_test.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-liveness-"))
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
(TMP / "data").mkdir()

from app.services import liveness  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


snap = liveness.snapshot()
check("timer: the ledger starts with this process and no work in flight",
      snap["pid"] == os.getpid() and snap["in_flight"] == {}, str(snap)[:200])

before = snap["last_activity_at"]
time.sleep(0.02)
liveness.touch("request", "/api/chat")
snap = liveness.snapshot()
check("timer: a request resets the timer and names itself",
      snap["last_activity_at"] > before and snap["last_activity_kind"] == "request"
      and snap["last_activity_detail"] == "/api/chat", str(snap)[:200])

liveness.work_started("task-1")
snap = liveness.snapshot()
check("timer: work starting is in flight with a progress stamp",
      "task-1" in snap["in_flight"] and snap["in_flight"]["task-1"]["last_progress_at"] > 0,
      str(snap)[:200])

p0 = snap["in_flight"]["task-1"]["last_progress_at"]
time.sleep(0.02)
liveness.work_progressed("task-1")
snap = liveness.snapshot()
check("timer: genuine progress moves the in-flight progress stamp",
      snap["in_flight"]["task-1"]["last_progress_at"] > p0, str(snap)[:200])
check("timer: last_progress_at reads the newest movement anywhere",
      liveness.last_progress_at(snap) == max(snap["last_activity_at"],
                                             snap["in_flight"]["task-1"]["last_progress_at"])
      and liveness.last_progress_at(snap) > p0,
      str(liveness.last_progress_at(snap)))

liveness.work_finished("task-1")
snap = liveness.snapshot()
check("timer: finished work leaves the in-flight set",
      snap["in_flight"] == {}, str(snap)[:200])

on_disk = liveness.read_ledger()
check("timer: the ledger is on disk, in the shape the review reads",
      on_disk is not None and on_disk["pid"] == os.getpid()
      and "last_activity_at" in on_disk and "in_flight" in on_disk,
      str(on_disk)[:200])
raw = json.loads((TMP / "data" / "activity.json").read_text())
check("timer: the on-disk ledger is complete JSON (atomic replace, never a torn write)",
      raw["updated_at"] >= raw["boot_at"], str(raw)[:200])

# An event through the real choke point (services/event_log.emit) is activity.
from app.services import event_log  # noqa: E402
before = liveness.snapshot()["last_activity_at"]
time.sleep(0.02)
event_log.emit("test.ping", actor="liveness_test")
snap = liveness.snapshot()
check("timer: an emitted event resets the timer",
      snap["last_activity_at"] > before and snap["last_activity_kind"] == "event",
      str(snap)[:200])

# A stepwise task claimed by the orchestrator pool enters "working" through
# a direct SQL claim (orchestrator._claim_next), not task_queue._update:
# the timer must still see the flight. (The AGE-7 wedge was a stepwise
# task; a claim path the timer cannot see would leave exactly that class
# unsupervised.)
from app.services import orchestrator  # noqa: E402
from app.services.task_queue import TaskQueue  # noqa: E402
q = TaskQueue()
made = q.create("a stepwise job for the timer test", mode="stepwise")
claimed = orchestrator._claim_next()
snap = liveness.snapshot()
check("timer: a stepwise claim puts the task in flight",
      claimed == made["id"] and made["id"] in snap["in_flight"],
      f"claimed={claimed} in_flight={list(snap['in_flight'])}")
q._update(made["id"], status="done")
snap = liveness.snapshot()
check("timer: the claimed task leaving working ends the flight",
      made["id"] not in snap["in_flight"], str(list(snap["in_flight"])))

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
if failed:
    print("FAILED: " + ", ".join(failed), flush=True)
raise SystemExit(1 if failed else 0)
