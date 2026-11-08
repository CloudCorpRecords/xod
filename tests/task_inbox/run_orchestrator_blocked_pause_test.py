"""
Orchestrator blocked-state fix (Tier 6 leg C, 2026-11-08): a blocked
step pauses the task on the person instead of failing it.

Live record (Tier 6 protocol, leg C): a stepwise task whose first
phase needs an input file that does not exist. On the shipped tree
the step's problems survive one retry, the step is marked blocked,
and run_stepwise finishes the whole task FAILED on the spot: no
needs-you, no unblock condition, nothing a person can act on, even
though the person-pause machinery (_paused_on_person/_take_resume)
exists in the same file for browser stops. The acceptance driver is
~/workspace/xod-eval/tier6/leg_c.py against a live backend; this
suite is the same shape against the real queue and orchestrator
with a fake agent:

  - phase 1 copies seed-input.txt to copy.txt; the seed is absent,
    so the step cannot check out and blocks after its one retry.
  - the task sits WAITING (not failed), its step blocked with the
    reason, exactly one needs-you raised, and the wait's message
    names the missing file and what would unblock the step.
  - the person creates the seed and answers; the task resumes
    through _take_resume, the step completes, phase 2 runs, and the
    task finishes done with both files byte-exact.
  - the finished task's waiting state is cleared (waiting_kind and
    waiting_detail empty), the known shipped leak.
  - a second blocked task cancelled while waiting lands cancelled
    with its waiting state cleared and the blocker still on the step.

Run: .venv/bin/python tests/task_inbox/run_orchestrator_blocked_pause_test.py
"""
import asyncio, json, os, re, sys, tempfile, time, uuid
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_orch_blocked_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.task_queue as TQ
import app.services.orchestrator as O
import app.agent.researcher as RS
from app.services import activity

NOTIFIES = []
TQ.notify = lambda title, msg: NOTIFIES.append((title, msg))
TQ._phone = lambda task, kind: None

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


# --------------------------------------------------------------------- fakes
STEP_RE = re.compile(r"YOUR STEP NOW \(step \d+ of \d+\):\n(.+?)\n\nDo only this step", re.S)
WRITE_RE = re.compile(r"Create the file (\S+) containing exactly this: (\S+)")
COPY_RE = re.compile(r"Copy the file (\S+) to (\S+) with exactly the same contents")
JOB = {}   # {"seed": Path, "copy": Path} for the job under test


def _record_write(path, content: bytes) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    c = activity._conn()
    c.execute("INSERT INTO activity (id, ts, origin, tool, title, detail, ok, undo, undoable) VALUES (?,?,?,?,?,?,?,?,?)",
              (uuid.uuid4().hex, time.time(), activity.origin.get(), "computer_write_file", "Wrote a file",
               str(path), 1, json.dumps([{"kind": "write_file", "path": str(path)}]), 1))
    c.commit()
    c.close()


def _do_copy(seed, copy):
    _record_write(copy, Path(seed).read_bytes())
    return f"Copied {seed} to {copy}; the contents match byte for byte."


class FakeResearcher:
    def __init__(self, *a, **k):
        pass

    async def run_research_stream(self, messages, **k):
        msg = messages[-1]["content"]
        await asyncio.sleep(0.05)
        if "(continue)" in msg:
            # A resume after a person-pause: the paused step's work, re-derived
            # from the job's state, exactly like a real agent re-checking.
            seed, copy = JOB.get("seed"), JOB.get("copy")
            if seed and copy and Path(seed).exists() and not Path(copy).exists():
                yield {"type": "content", "content": _do_copy(seed, copy)}
            else:
                yield {"type": "content", "content": "Continued."}
            return
        m = STEP_RE.search(msg)
        step = m.group(1).strip() if m else msg
        c = COPY_RE.search(step)
        if c:
            seed, copy = c.group(1), c.group(2)
            JOB["seed"], JOB["copy"] = seed, copy
            if Path(seed).exists():
                yield {"type": "content", "content": _do_copy(seed, copy)}
            else:
                yield {"type": "content", "content":
                       f"I couldn't read the file {seed} because it does not exist, "
                       "so there is nothing to copy."}
            return
        w = WRITE_RE.search(step)
        if w:
            _record_write(w.group(1), w.group(2).encode())
            yield {"type": "content", "content": f"Wrote {w.group(1)}."}
            return
        yield {"type": "content", "content": f"Did: {step[:80]}"}


RS.AgenticResearcher = FakeResearcher


async def fake_planner(request):
    return [l.strip("- ").strip() for l in request.split("\n") if l.strip().startswith("- ")]


O.planner = fake_planner


def request_for(*steps):
    return "Do these steps:\n" + "\n".join(f"- {s}" for s in steps)


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


SEED_CONTENT = b"T6C-SEED-9182"


async def case_block_pause_resume(q):
    print("blocked step pauses, the person unblocks, the task completes")
    d = TMP / "work" / f"jobc-{uuid.uuid4().hex[:4]}"
    d.mkdir(parents=True)
    seed, copy, after = d / "seed-input.txt", d / "copy.txt", d / "after.txt"
    if seed.exists():
        seed.unlink()
    n_notify = len(NOTIFIES)
    ack = O.submit(request_for(
        f"Copy the file {seed} to {copy} with exactly the same contents",
        write_step(after, "T6C-AFTER")))
    tid = ack["task_id"]
    t = await wait_status(q, tid, ("waiting", "failed", "done"))
    check("blocked: the task waits on the person instead of failing",
          t["status"] == "waiting", f"status={t['status']}")
    items, _, _ = O.load_plan(tid)
    s1 = items[0]
    check("blocked: step 1 is blocked with its reason after its retry",
          s1["status"] == "blocked" and s1.get("waiting") and (s1.get("blocker") or ""),
          json.dumps(s1)[:300])
    check("blocked: the blocker names the missing input",
          # The shipped self-report check splits a report into sentences
          # on dots, so the blocker itself quotes the path truncated at
          # "seed-input"; the full name is asserted on the wait's
          # message below, which is the surface the person acts on.
          "seed-input" in (s1.get("blocker") or ""), s1.get("blocker") or "")
    check("blocked: step 2 never ran while blocked",
          items[1]["status"] == "pending" and not after.exists())
    wd = t.get("waiting_detail") or {}
    msg = wd.get("message") or ""
    check("blocked: the wait names the missing file and the unblock condition",
          "seed-input.txt" in msg and "exists" in msg and "answer" in msg.lower(), msg[:300])
    check("blocked: exactly one needs-you was raised for the wait",
          len(NOTIFIES) == n_notify + 1, f"notifies={len(NOTIFIES) - n_notify}")

    # The person provides the missing input and answers (leg C's unblock).
    seed.write_bytes(SEED_CONTENT)
    res = q.answer(tid, "The file seed-input.txt now exists in the job folder "
                        "with the input content. Please continue.")
    check("unblock: the answer was accepted", bool(res.get("success")), str(res))
    t = await wait_status(q, tid, ("done", "failed"), timeout=60)
    check("unblock: the task finishes done", t["status"] == "done", f"status={t['status']}")
    check("unblock: copy.txt is byte-exact against the seed",
          copy.exists() and copy.read_bytes() == SEED_CONTENT,
          f"copy={copy.read_bytes() if copy.exists() else None}")
    check("unblock: phase 2 landed byte-exact too",
          after.exists() and after.read_bytes() == b"T6C-AFTER",
          f"after={after.read_bytes() if after.exists() else None}")
    items, _, _ = O.load_plan(tid)
    check("unblock: every step completed", all(i["status"] == "completed" for i in items),
          json.dumps([i["status"] for i in items]))
    check("cleared: the finished task keeps no waiting state",
          t.get("waiting_kind") is None and not t.get("waiting_detail"),
          f"kind={t.get('waiting_kind')} detail={t.get('waiting_detail')}")


async def case_block_cancel(q):
    print("a blocked task cancelled while waiting")
    d = TMP / "work" / f"jobx-{uuid.uuid4().hex[:4]}"
    d.mkdir(parents=True)
    seed, copy = d / "seed-input.txt", d / "copy.txt"
    ack = O.submit(request_for(f"Copy the file {seed} to {copy} with exactly the same contents"))
    tid = ack["task_id"]
    t = await wait_status(q, tid, ("waiting", "failed"))
    check("cancel: the second task also waits instead of failing",
          t["status"] == "waiting", f"status={t['status']}")
    res = await q.cancel(tid)
    t = q.get(tid)
    check("cancel: the task lands cancelled", t["status"] == "cancelled", f"status={t['status']} res={res}")
    items, _, _ = O.load_plan(tid)
    check("cancel: the blocker is preserved on the step",
          items[0]["status"] in ("blocked", "cancelled") and "seed-input" in (items[0].get("blocker") or items[0].get("note") or ""),
          json.dumps(items[0])[:300])
    check("cancel: the cancelled task keeps no waiting state",
          t.get("waiting_kind") is None and not t.get("waiting_detail"),
          f"kind={t.get('waiting_kind')} detail={t.get('waiting_detail')}")


async def main():
    q = TQ.task_queue = TQ.TaskQueue()
    q.start()
    try:
        await case_block_pause_resume(q)
        await case_block_cancel(q)
    finally:
        q.stop()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1 if failed else 0)


asyncio.run(main())
