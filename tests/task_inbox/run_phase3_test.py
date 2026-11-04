"""
Orchestrator Phase 3: resource registry, stall watchdog, steering running workers, resume after restart.

  R1 two workers request the same phone at once: one is granted, one queues with a visible reason,
     and the grant passes over cleanly when the first finishes, and when a holder dies (spec 12)
  R2 the tool executor serializes a capacity-1 device across lanes and defers instead of fighting
  R3 a frozen worker is flagged stalled within the threshold with its TRUE last-activity time, the
     task's status reports exactly that, and past the stop threshold it is stopped (spec 11)
  R4 steering a running worker: a queued follow-up is not applied until the worker's next report
     confirms it; now=true interrupts the current turn and starts a fresh one (spec 6)
  R5 resume after restart: a fan-out killed mid-way resumes; finished jobs keep their results and are
     not re-run, unfinished ones run again; no completed step repeats (Phase 3 exit criterion)

Run: .venv/bin/python tests/task_inbox/run_phase3_test.py [--repeat N]
"""
import asyncio, json, os, sys, tempfile, time, uuid
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_phase3_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.task_queue as TQ
import app.services.orchestrator as O
import app.services.workers as W
import app.services.resources as R
from app.agent import tools
from app.services import activity

TQ.notify = lambda title, msg: None
TQ._phone = lambda task, kind: None


async def _no_tab(self, task, resuming):
    return True


TQ.TaskQueue._ensure_tab = _no_tab
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


CALLS = {}       # job -> number of agent turns started for it
SCRIPT = {}      # job -> seconds to take (or "freeze")
PROMPTS = {}     # worker id -> prompts it got


async def fake_agent(prompt, wid):
    PROMPTS.setdefault(wid, []).append(prompt)
    job = prompt.split("JOB:\n", 1)[1].split("\n", 1)[0].strip()
    CALLS[job] = CALLS.get(job, 0) + 1
    dur = SCRIPT.get(job, 0.5)
    yield {"type": "status", "content": f"• working on {job[:40]}"}
    if dur == "freeze":
        await asyncio.Event().wait()     # a hung await: no activity, ever
    end = time.time() + dur
    while time.time() < end:
        await asyncio.sleep(0.05)
    extra = prompt.split("NEW INSTRUCTION FROM YOUR PARENT", 1)[1].strip() if "NEW INSTRUCTION FROM YOUR PARENT" in prompt else ""
    yield {"type": "tool_result", "name": "web_search", "data": {"success": True}}
    yield {"type": "content", "content": f"result for {job}" + (f" [following: {extra}]" if extra else "")}


W.agent = fake_agent
import app.agent.researcher as RS


class FakeResearcher:
    """The task's own (non-worker) steps."""
    def __init__(self, *a, **k):
        pass

    async def run_research_stream(self, messages, **k):
        msg = messages[-1]["content"]
        step = msg.split("YOUR STEP NOW", 1)[1].split("\n", 1)[1].split("\n", 1)[0].strip() if "YOUR STEP NOW" in msg else msg
        CALLS[step] = CALLS.get(step, 0) + 1
        yield {"type": "status", "content": "• doing it"}
        yield {"type": "content", "content": f"Done: {step}"}


RS.AgenticResearcher = FakeResearcher


async def fake_synth(job, ok):
    return " | ".join(r["report"] for r in ok)


W.synthesize = fake_synth
PLANS = {}


async def fake_planner(request):
    return PLANS[request]


O.planner = fake_planner


async def wait_status(q, tid, statuses, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = q.get(tid)
        if t["status"] in statuses:
            return t
        await asyncio.sleep(0.05)
    raise TimeoutError(f"{tid} stuck at {q.get(tid)['status']}")


async def r1_phone(q):
    print("R1 two workers want the same phone")
    tag = uuid.uuid4().hex[:4]
    phone = f"phone:galaxy-{tag}"
    a, b = f"Phone job A {tag}", f"Phone job B {tag}"
    SCRIPT[a] = SCRIPT[b] = 1.0
    wa = W.spawn("p", a, needs=[phone])
    wb = W.spawn("p", b, needs=[phone])
    await asyncio.sleep(0.4)
    st = R.status(phone)[0]
    check("exactly one holder, one queued", len(st["holders"]) == 1 and len(st["queue"]) == 1, json.dumps(st)[:300])
    waiting = W.get(wb if st["holders"][0]["holder_id"] == f"worker:{wa}" else wa)
    check("the queued one shows why", "busy: held by worker:" in (waiting.get("last_activity") or ""), waiting.get("last_activity"))
    first = st["holders"][0]["holder_id"]
    got = await W.wait([wa, wb], timeout=10)
    check("both finished, one after the other",
          all(g["state"] == "done" for g in got.values()) and CALLS[a] == 1 and CALLS[b] == 1)
    ws = sorted(got.values(), key=lambda w: w["finished_at"])
    check("the grant passed over when the first finished", f"worker:{ws[0]['id']}" == first)
    # A holder that dies: it never beats again; its claim expires and the queue moves on.
    dead = R.acquire(phone, "worker:dead-one", ttl=1.0)
    c = W.spawn("p", f"Phone job C {tag}", needs=[phone])
    await asyncio.sleep(0.3)
    check("a live claim blocks the next worker", W.get(c)["state"] == "running" and "busy" in (W.get(c)["last_activity"] or ""))
    got = await W.wait([c], timeout=10)
    claims = R._conn().execute("SELECT state FROM resource_claims WHERE id = ?", (dead["claim_id"],)).fetchone()
    check("the dead holder's claim expired and the worker got the phone", got[c]["state"] == "done" and claims["state"] == "expired",
          f"{got[c]['state']} {claims['state']}")


async def r2_executor(q):
    print("R2 the executor serializes the browser across lanes")
    real = tools._execute_tool
    active = {"n": 0, "max": 0}

    async def fake_exec(name, args, ws=None):
        if name == "browser_navigate":
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            await asyncio.sleep(0.3)
            active["n"] -= 1
            return {"success": True}
        return await real(name, args, ws)

    tools._execute_tool = fake_exec

    async def lane(origin, n):
        tok = activity.origin.set(origin)
        try:
            for _ in range(n):
                await tools.execute_tool("browser_navigate", {"url": "https://example.test"}, reviewed=True)
        finally:
            activity.origin.reset(tok)

    ta, tb = f"task:r2a-{uuid.uuid4().hex[:4]}", f"task:r2b-{uuid.uuid4().hex[:4]}"
    try:
        la = asyncio.ensure_future(lane(ta, 2))
        await asyncio.sleep(0.05)
        lb = asyncio.ensure_future(lane(tb, 1))
        await asyncio.sleep(0.2)
        st = R.status("browser")[0]
        check("lane B queued behind lane A with a reason",
              st["holders"][0]["holder_id"] == ta and st["queue"] and "held by " + ta in (st["queue"][0]["reason"] or ""),
              json.dumps(st)[:300])
        await la
        R.release_holder(ta)            # lane A's task finished
        await lb
        check("never two lanes in the browser at once", active["max"] == 1)
        # Chat defers instead of fighting when the browser stays busy.
        R.acquire("browser", "task:hog", ttl=60)
        R.release_holder(tb)
        tok = activity.origin.set("chat:s1")
        orig = R.wait_for

        async def short_wait(name, holder, max_wait, ttl=120):
            return await orig(name, holder, 0.3, ttl)
        R.wait_for = short_wait
        try:
            r = await tools.execute_tool("browser_navigate", {"url": "https://example.test"}, reviewed=True)
        finally:
            R.wait_for = orig
            activity.origin.reset(tok)
        check("chat is deferred with the holder named, nothing done", r.get("deferred") and "task:hog" in r.get("error", ""), str(r)[:200])
        R.release_holder("task:hog")
    finally:
        tools._execute_tool = real


async def r3_stall(q):
    print("R3 a frozen worker is reported stalled with its true last activity")
    job = f"Frozen job {uuid.uuid4().hex[:4]}"
    SCRIPT[job] = "freeze"
    tid = f"task-r3{uuid.uuid4().hex[:4]}"
    cid = f"crd-r3{uuid.uuid4().hex[:4]}"
    c = W._conn()
    c.execute("INSERT INTO workers (id, parent_id, parent_type, role, brief, envelope, state, created_at, last_activity_at) "
              "VALUES (?,?,?,?,?,?,?,?,?)", (cid, tid, "task", "coordinator", "x", "read_only", "running", time.time(), time.time()))
    c.commit()
    c.close()
    wid = W.spawn(cid, job, parent_type="coordinator")
    await asyncio.sleep(0.3)
    last = W.get(wid)["last_activity_at"]
    r = await W.run_watchdog(now=last + 5, stall_sec=4, stop_sec=60)
    w = W.get(wid)
    at = time.strftime("%H:%M:%S", time.localtime(last))
    check("flagged stalled within the threshold", wid in r["stalled"] and w["state"] == "running")
    check("with the true last-activity time and what it was doing",
          at in (w["stalled_reason"] or "") and "working on" in w["stalled_reason"], w["stalled_reason"])
    pub = O.public_plan(tid)
    mine = next(x for x in pub["workers"] if x["id"] == wid)
    check("the parent's status reports exactly that, not a guess", mine["stalled_reason"] == w["stalled_reason"]
          and abs(mine["last_activity_at"] - last) < 1e-6)
    r = await W.run_watchdog(now=last + 61, stall_sec=4, stop_sec=60)
    await asyncio.sleep(0.2)
    w = W.get(wid)
    check("past the stop threshold it is stopped and failed with the observed reason",
          wid in r["stopped"] and w["state"] == "failed" and "no activity since " + at in json.dumps(w["result"]), json.dumps(w)[:300])


async def r4_steer_worker(q):
    print("R4 steering a running worker")
    job = f"Research topic {uuid.uuid4().hex[:4]}"
    SCRIPT[job] = 1.0
    wid = W.spawn("p", job)
    await asyncio.sleep(0.3)
    s = W.steer(wid, "focus on 2025 data only")
    await asyncio.sleep(0.2)
    from app.services import orchestrator as O2
    st = next(x for x in O2.inbox(wid))
    check("follow-up is queued while the turn runs", s["state"] == "queued" and st["state"] == "queued" and "after its current turn" in s["applies"])
    await asyncio.sleep(1.0)    # first turn ended; the follow-up turn is running
    st = next(x for x in O2.inbox(wid))
    check("taken into the next turn but not yet called applied", st["state"] == "taken")
    got = await W.wait([wid], timeout=10)
    st = next(x for x in O2.inbox(wid))
    check("applied only once the worker's next report arrived", st["state"] == "applied" and "next report" in st["applied_note"])
    check("the result follows the new instruction", "focus on 2025 data only" in got[wid]["result"]["report"] and CALLS[job] == 2)
    job2 = f"Long research {uuid.uuid4().hex[:4]}"
    SCRIPT[job2] = 5.0
    wid2 = W.spawn("p", job2)
    await asyncio.sleep(0.3)
    t0 = time.time()
    W.steer(wid2, "stop and summarise what you have", now=True)
    got = await W.wait([wid2], timeout=15)
    check("now=true interrupts the turn and a fresh turn takes the instruction",
          got[wid2]["state"] == "done" and CALLS[job2] == 2 and "summarise" in got[wid2]["result"]["report"], json.dumps(got[wid2])[:300])
    check("the interrupt landed fast (not after the 5 s turn)", time.time() - t0 < 7.0, f"{time.time() - t0:.1f}s")


async def r5_resume(q):
    print("R5 a fan-out survives a restart")
    tag = uuid.uuid4().hex[:4]
    jobs = [f"Quick check {n} {tag}" for n in (1, 2)] + [f"Slow check {tag}"]
    SCRIPT[jobs[0]] = SCRIPT[jobs[1]] = 0.3
    SCRIPT[jobs[2]] = 4.0
    req = f"Three checks {tag}"
    PLANS[req] = [f"Prepare {tag}", {"text": f"three checks {tag}", "parallel": jobs}]
    tid = O.submit(req)["task_id"]
    t0 = time.time()
    while time.time() - t0 < 15:
        done = [w for w in W.list_workers() if w["state"] == "done" and any(j in w["brief"] for j in jobs[:2])]
        if len(done) == 2:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.2)
    # The backend dies: the worker loop goes away mid fan-out.
    q._worker.cancel()
    q._watchdog.cancel()
    await asyncio.sleep(0.5)
    W.mark_orphans_interrupted()
    items, _, _ = O.load_plan(tid)
    check("after the crash the plan shows the fan-out step in flight", [i["status"] for i in items] == ["completed", "in_progress"])
    SCRIPT[jobs[2]] = 0.3
    q2 = TQ.task_queue = TQ.TaskQueue()
    q2.start()
    t = await wait_status(q2, tid, ("done", "failed"))
    items, _, _ = O.load_plan(tid)
    check("the task resumed and finished", t["status"] == "done", t["status"])
    check("finished jobs were not re-run", CALLS[jobs[0]] == 1 and CALLS[jobs[1]] == 1, str({j: CALLS.get(j) for j in jobs}))
    check("the unfinished job ran again", CALLS[jobs[2]] == 2)
    check("the result has all three", all(f"result for {j}" in (t["result"] or "") for j in jobs))
    check("the first step was not repeated", CALLS.get(f"Prepare {tag}", 0) == 1, str(CALLS.get(f"Prepare {tag}")))
    return q2


async def main(repeat):
    for r in range(repeat):
        print(f"=== run {r + 1}/{repeat}")
        q = TQ.task_queue = TQ.TaskQueue()
        q.start()
        await r1_phone(q)
        await r2_executor(q)
        await r3_stall(q)
        await r4_steer_worker(q)
        q = await r5_resume(q)
        q._worker.cancel()
        q._watchdog.cancel()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--repeat") + 1]) if "--repeat" in sys.argv else 1
    asyncio.run(main(n))
