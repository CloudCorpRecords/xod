"""
Orchestrated tasks run side by side, under the resource registry (orchestrator scope, Phase 3:
"parallel execution of side-effecting work under the resource registry").

Before: one task at a time, because the browser's mission and pending-approval state are global.
Now: stepwise tasks run in a pool; the global browser state is only touched by the task holding
the browser claim, so tasks that need the browser serialize on it and everything else overlaps.

  K1 two side-effecting tasks run at the same time and both finish, each effect once
  K2 two tasks that both drive the browser never use it at the same time; the second waits with
     a visible reason and both finish
  K3 classic (non-orchestrated) tasks still run through the classic queue alongside the pool
  K4 a forced cancel stops a pool task that ignores the graceful one
  K5 a task that never held the browser is not paused by another task's browser mission
  K6 a whole browser mission (the agent's mission path, not a single tool) claims the browser too: two
     tasks' missions never run at once (live 2026-11-04: missions bypassed the claim)
  K7 a mission that stops to ask the person leaves its task waiting with the question, not "done"
     (live 2026-11-04: "I need something from you" became a completed step's result)

Run: .venv/bin/python tests/task_inbox/run_concurrency_test.py [--repeat N]
"""
import asyncio, json, os, sys, tempfile, time, uuid
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_conc_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.task_queue as TQ
import app.services.orchestrator as O
import app.services.resources as R
import app.agent.researcher as RS
import app.services.browser_sandbox as BS
from app.agent import tools
from app.services import activity

TQ.notify = lambda title, msg: None
TQ._phone = lambda task, kind: None


async def _no_tab(self, task, resuming):
    return True


TQ.TaskQueue._ensure_tab = _no_tab
results = []
SPANS = {}           # step text -> (start, end)
BROWSER = {"active": 0, "max": 0}


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


RealResearcher = RS.AgenticResearcher
MISSIONS = {"active": 0, "max": 0}


async def fake_mission(goal, start_url=None, max_steps=25, allow_submit=False):
    """The sandbox's mission: holds the browser a moment; a goal with 'ask' stops for the person."""
    MISSIONS["active"] += 1
    MISSIONS["max"] = max(MISSIONS["max"], MISSIONS["active"])
    stops = "ask" in goal.lower()
    rec = {"mission_id": uuid.uuid4().hex[:8], "goal": goal, "started_at": time.time(), "steps": [],
           "status": "needs_input" if stops else "done"}
    BS.browser_sandbox_service.latest_mission = rec
    await asyncio.sleep(0.6)
    MISSIONS["active"] -= 1
    answer = 'The form on **Example** asks: **"Your phone number"**. I do not know your answer, so I did not fill it in.'
    return {"status": rec["status"], "final_answer": answer if stops else "Read the page.", "steps": []}


BS.browser_sandbox_service.run_browser_mission = fake_mission


class FakeResearcher:
    def __init__(self, *a, **k):
        pass

    async def run_research_stream(self, messages, **k):
        msg = messages[-1]["content"]
        step = msg.split("YOUR STEP NOW", 1)[1].split("\n", 1)[1].split("\n", 1)[0] if "YOUR STEP NOW" in msg else msg
        t0 = time.time()
        yield {"type": "status", "content": f"• {step[:40]}"}
        if step.startswith("Browse "):
            for _ in range(2):
                await tools.execute_tool("browser_navigate", {"url": "https://example.test"}, reviewed=True)
        elif step.startswith("Mission2 "):
            real = RealResearcher.__new__(RealResearcher)
            async for ev in real._stream_browser_mission(step, None, False, 25, False):
                if ev.get("type") == "content":
                    SPANS[step] = (t0, time.time())
                    yield ev
            return
        elif step.startswith("Hang "):
            while True:
                await asyncio.sleep(3600)
        elif step.startswith("Mission "):
            # This task's browser mission stops at a submit, as another task finishes a plain step.
            await tools.execute_tool("browser_navigate", {"url": "https://example.test/form"}, reviewed=True)
            BS.browser_sandbox_service.latest_mission = {"started_at": time.time(), "status": "needs_confirmation", "steps": []}
            BS.browser_sandbox_service.pending_submit = {"label": "Send"}
        else:
            await asyncio.sleep(1.0)
        if step.startswith("Write "):
            p = Path(step.split("Write ", 1)[1])
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a") as f:
                f.write("x\n")
        SPANS[step] = (t0, time.time())
        yield {"type": "content", "content": f"Done: {step}"}


RS.AgenticResearcher = FakeResearcher
real_exec = tools._execute_tool


async def fake_exec(name, args, ws=None):
    if name == "browser_navigate":
        BROWSER["active"] += 1
        BROWSER["max"] = max(BROWSER["max"], BROWSER["active"])
        await asyncio.sleep(0.4)
        BROWSER["active"] -= 1
        return {"success": True}
    return await real_exec(name, args, ws)


tools._execute_tool = fake_exec


async def fake_planner(request):
    return [request]


O.planner = fake_planner


async def wait_status(q, tid, statuses, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = q.get(tid)
        if t["status"] in statuses:
            return t
        await asyncio.sleep(0.05)
    raise TimeoutError(f"{tid} stuck at {q.get(tid)['status']}")


async def k1_parallel(q):
    print("K1 two side-effecting tasks run at the same time")
    d = TMP / f"k1-{uuid.uuid4().hex[:4]}"
    a, b = f"Write {d}/a.txt", f"Write {d}/b.txt"
    ta, tb = O.submit(a)["task_id"], O.submit(b)["task_id"]
    ra, rb = await wait_status(q, ta, ("done", "failed")), await wait_status(q, tb, ("done", "failed"))
    check("both finished", ra["status"] == rb["status"] == "done")
    (a0, a1), (b0, b1) = SPANS[a], SPANS[b]
    check("they overlapped in time", a0 < b1 and b0 < a1, f"a {a0:.2f}-{a1:.2f} b {b0:.2f}-{b1:.2f}")
    check("each effect happened once", (d / "a.txt").read_text() == "x\n" and (d / "b.txt").read_text() == "x\n")


async def k2_browser(q):
    print("K2 two browser tasks share the browser one at a time")
    BROWSER["max"] = 0
    tag = uuid.uuid4().hex[:4]
    ta, tb = O.submit(f"Browse site one {tag}")["task_id"], O.submit(f"Browse site two {tag}")["task_id"]
    saw_queue = None
    t0 = time.time()
    while time.time() - t0 < 20 and not saw_queue:
        st = R.status("browser")[0]
        if st["queue"]:
            saw_queue = st
        await asyncio.sleep(0.05)
    ra, rb = await wait_status(q, ta, ("done", "failed")), await wait_status(q, tb, ("done", "failed"))
    check("both finished", ra["status"] == rb["status"] == "done")
    check("never two tasks in the browser at once", BROWSER["max"] == 1, str(BROWSER))
    check("the waiting task showed why", saw_queue and "held by task:" in (saw_queue["queue"][0]["reason"] or ""),
          json.dumps(saw_queue)[:200] if saw_queue else "never queued")


async def k3_classic(q):
    print("K3 classic tasks still run alongside")
    t = q.create(f"Classic job {uuid.uuid4().hex[:4]}")
    s = O.submit(f"Write {TMP}/k3-{uuid.uuid4().hex[:4]}.txt")["task_id"]
    rc, rs = await wait_status(q, t["id"], ("done", "failed")), await wait_status(q, s, ("done", "failed"))
    check("classic task done", rc["status"] == "done", rc["status"])
    check("pool task done", rs["status"] == "done")


async def k4_force(q):
    print("K4 a forced cancel stops a hung pool task")
    tid = O.submit(f"Hang forever {uuid.uuid4().hex[:4]}")["task_id"]
    await wait_status(q, tid, ("working",))
    await asyncio.sleep(0.5)
    await q.cancel(tid)
    await asyncio.sleep(0.8)
    await q.cancel(tid)            # second cancel forces
    t = await wait_status(q, tid, ("cancelled",), timeout=10)
    check("cancelled for real", t["status"] == "cancelled" and tid not in O._pool, str(list(O._pool)))


async def k5_no_cross_pause(q):
    print("K5 another task's browser mission does not pause this one")
    tag = uuid.uuid4().hex[:4]
    tm = O.submit(f"Mission form {tag}")["task_id"]
    tp = O.submit(f"Write {TMP}/k5-{tag}.txt")["task_id"]
    rm, rp = await wait_status(q, tm, ("waiting", "done", "failed")), await wait_status(q, tp, ("waiting", "done", "failed"))
    check("the browser task waits for its own approval", rm["status"] == "waiting" and rm["waiting_kind"] == "approval")
    check("the plain task is not paused by it", rp["status"] == "done", rp["status"])
    await q.cancel(tm)


async def k6_mission_claims(q):
    print("K6 whole browser missions claim the browser")
    MISSIONS["max"] = 0
    tag = uuid.uuid4().hex[:4]
    ta, tb = O.submit(f"Mission2 read page one {tag}")["task_id"], O.submit(f"Mission2 read page two {tag}")["task_id"]
    saw_queue = None
    t0 = time.time()
    while time.time() - t0 < 20 and not saw_queue:
        st = R.status("browser")[0]
        if st["queue"]:
            saw_queue = st
        await asyncio.sleep(0.05)
    ra, rb = await wait_status(q, ta, ("done", "failed")), await wait_status(q, tb, ("done", "failed"))
    check("both finished", ra["status"] == rb["status"] == "done", f"{ra['status']} {rb['status']}")
    check("never two missions in the browser at once", MISSIONS["max"] == 1, str(MISSIONS))
    check("the waiting one was queued with a reason", saw_queue and "held by task:" in (saw_queue["queue"][0]["reason"] or ""))


async def k7_mission_question(q):
    print("K7 a mission that asks the person leaves the task waiting")
    tid = O.submit(f"Mission2 ask for the phone number {uuid.uuid4().hex[:4]}")["task_id"]
    t = await wait_status(q, tid, ("waiting", "done", "failed"))
    check("the task waits for the person, not done", t["status"] == "waiting" and t["waiting_kind"] == "question",
          f"{t['status']} {t.get('waiting_kind')}")
    await q.cancel(tid)


async def main(repeat):
    for i in range(repeat):
        print(f"=== run {i + 1}/{repeat}")
        q = TQ.task_queue = TQ.TaskQueue()
        q.start()
        await k1_parallel(q)
        await k2_browser(q)
        await k3_classic(q)
        await k4_force(q)
        await k5_no_cross_pause(q)
        await k6_mission_claims(q)
        await k7_mission_question(q)
        q.stop()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--repeat") + 1]) if "--repeat" in sys.argv else 1
    asyncio.run(main(n))
