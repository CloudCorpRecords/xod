"""
Orchestrator Phase 2: workers, the coordinator pattern, permission envelopes, verified outcomes.

The agent is faked (each worker's "agent" is scripted per job); the queue, plan, workers store,
tool executor guard and outcome ledger are the real code.

  W1 two unrelated research jobs run as two concurrent workers, each with a structured result,
     no context leaking between them, and the parent's one answer draws on both (spec 4)
  W2 a job needing 6 parallel checks yields exactly ONE parent-level result that names which
     checks failed and why (spec 5)
  W3 envelopes: a read-only worker cannot write or send; a worker cannot start workers (spec 4, 10)
  W4 UNKNOWN discipline: the network dies mid-send; the outcome is UNKNOWN, no blind retry while the
     destination cannot be read; on recovery it confirms the original (landed) or retries exactly
     once (did not land); never twice (spec 9)
  W5 a real file write through the tool executor comes back CONFIRMED by read-back
  W6 cancelling a task mid fan-out stops its workers and records them interrupted
  W7 a worker left running by a dead process is shown interrupted at startup, never "running"

Run: .venv/bin/python tests/task_inbox/run_workers_test.py [--repeat N]
"""
import asyncio, json, os, sys, tempfile, time, uuid
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_workers_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.task_queue as TQ
import app.services.orchestrator as O
import app.services.workers as W
import app.services.outcomes as OC
from app.agent import tools

TQ.notify = lambda title, msg: None
TQ._phone = lambda task, kind: None


async def _no_tab(self, task, resuming):
    return True


TQ.TaskQueue._ensure_tab = _no_tab
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


# --------------------------------------------------------------------- fake worker agent
RUNS = {}          # worker id -> {"brief", "start", "end"}
JOB_SCRIPT = {}    # job text -> ("answer" | "fail" | "slow", payload)


async def fake_agent(brief, wid):
    rec = RUNS.setdefault(wid, {"brief": brief, "start": time.time(), "end": None})
    job = brief.split("JOB:\n", 1)[1].split("\n", 1)[0].strip()
    kind, payload = JOB_SCRIPT.get(job, ("answer", f"answer for {job}"))
    yield {"type": "status", "content": f"• looking up {job[:40]}"}
    await asyncio.sleep(0.8 if kind != "slow" else 30)
    if kind == "fail":
        yield {"type": "tool_result", "name": "web_search", "data": {"success": False, "error": payload}}
        rec["end"] = time.time()
        return
    if kind == "admit":
        yield {"type": "tool_result", "name": "xod_self_check", "data": {"done": True}}
        yield {"type": "content", "content": payload}
        rec["end"] = time.time()
        return
    if kind == "fail_quietly":
        # Every tool failed, yet the worker still writes a confident-looking report.
        yield {"type": "tool_result", "name": "computer_read_file", "data": {"success": False, "error": payload}}
        yield {"type": "content", "content": "Here is what the source says."}
        rec["end"] = time.time()
        return
    yield {"type": "tool_result", "name": "web_search", "data": {"success": True, "results": [payload]}}
    yield {"type": "content", "content": payload}
    rec["end"] = time.time()


W.agent = fake_agent
SYNTH_INPUTS = []


async def fake_synth(job, ok_results):
    SYNTH_INPUTS.append((job, [r["report"] for r in ok_results]))
    return " | ".join(r["report"] for r in ok_results)


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


# --------------------------------------------------------------------- tests
async def w1_two_jobs(q):
    print("W1 two unrelated research jobs, two concurrent workers")
    a = f"Find the population of Lisbon {uuid.uuid4().hex[:4]}"
    b = f"Find the boiling point of ethanol {uuid.uuid4().hex[:4]}"
    JOB_SCRIPT[a] = ("answer", "Lisbon: about 545,000 (city proper), source: INE")
    JOB_SCRIPT[b] = ("answer", "Ethanol boils at 78.37 C, source: CRC Handbook")
    req = f"Two things: {a}. And {b}."
    PLANS[req] = [{"text": "Look both up", "parallel": [a, b]}]
    tid = O.submit(req)["task_id"]
    t = await wait_status(q, tid, ("done", "failed"))
    items, _, _ = O.load_plan(tid)
    ws = [w for w in W.list_workers() if w["role"] == "worker" and any(x in w["brief"] for x in (a, b))]
    check("task done", t["status"] == "done", t["status"])
    check("two workers ran", len(ws) == 2)
    ra, rb = (RUNS[w["id"]] for w in ws)
    check("they ran concurrently", ra["start"] < rb["end"] and rb["start"] < ra["end"])
    check("each returned a structured result (outcome, report, evidence)",
          all(w["result"]["outcome"] == "done" and w["result"]["report"] and w["result"]["evidence"] for w in ws))
    leak = [w for w in ws if (a in w["brief"]) == (b in w["brief"])]
    check("no context leaked: each brief holds only its own job", not leak)
    check("the parent's one answer draws on both", "545,000" in (t["result"] or "") and "78.37" in (t["result"] or ""))
    crd = [w for w in W.list_workers() if w["role"] == "coordinator" and w["parent_id"] == tid]
    check("one coordinator owned the fan-out", len(crd) == 1 and all(w["parent_id"] == crd[0]["id"] for w in ws))


async def w2_six_checks(q):
    print("W2 six parallel checks, one parent-level result, failures itemized")
    tag = uuid.uuid4().hex[:4]
    jobs = [f"Check source {n} {tag}" for n in range(1, 7)]
    for j in jobs:
        JOB_SCRIPT[j] = ("answer", f"{j}: fine")
    JOB_SCRIPT[jobs[1]] = ("fail", "HTTP 503 from the source")
    JOB_SCRIPT[jobs[4]] = ("fail", "page blocked by a login wall")
    req = f"Run the six source checks {tag}"
    PLANS[req] = [{"text": "Six source checks", "parallel": jobs}]
    tid = O.submit(req)["task_id"]
    t = await wait_status(q, tid, ("done", "failed"))
    items, _, _ = O.load_plan(tid)
    check("exactly one plan item carries the whole fan-out", len(items) == 1 and len(items[0].get("workers") or []) == 6)
    fails = items[0].get("failures") or []
    check("the parent can name which 2 failed", sorted(f["task"] for f in fails) == sorted([jobs[1], jobs[4]]), str(fails))
    check("and why", any("503" in f["why"] for f in fails) and any("login wall" in f["why"] for f in fails))
    check("the final report itemizes the unread sources", (t["result"] or "").count("unread/failed") == 2)
    check("the synthesis used only the 4 good results", len(SYNTH_INPUTS[-1][1]) == 4)


async def w3_envelopes(q):
    print("W3 permission envelopes")
    target = TMP / "should-not-exist.txt"
    tok = W.envelope.set("read_only")
    try:
        r = await tools.execute_tool("computer_write_file", {"filepath": str(target), "content": "x"})
        r2 = await tools.execute_tool("agent_mail_send", {"to": "someone@example.com", "text": "hi"})
    finally:
        W.envelope.reset(tok)
    check("read-only worker cannot write a file", r.get("outside_envelope") and not target.exists(), str(r)[:200])
    check("read-only worker cannot send", r2.get("outside_envelope"))
    wid = W.spawn("test-parent", "Look up anything")
    await W.wait([wid], timeout=10)
    tok = W.current_worker.set(wid)
    try:
        try:
            W.spawn("x", "nested")
            check("a worker cannot spawn a worker", False)
        except PermissionError:
            check("a worker cannot spawn a worker", True)
    finally:
        W.current_worker.reset(tok)
    try:
        W.spawn("test-parent", "write something", env="full")
        check("delegation never widens permission (no full-envelope worker)", False)
    except PermissionError:
        check("delegation never widens permission (no full-envelope worker)", True)


async def w4_unknown(q):
    print("W4 UNKNOWN: the network dies mid-send")
    real = tools._execute_tool
    for landed in (True, False):
        dest = []
        net = {"up": False}
        sends = {"n": 0}

        async def fake_exec(name, args, ws=None):
            if name != "agent_mail_send":
                return await real(name, args, ws)
            sends["n"] += 1
            if not net["up"]:
                if landed:
                    dest.append(args["text"])         # the server took it, then the connection dropped
                raise ConnectionError("network unreachable")
            dest.append(args["text"])
            return {"success": True, "sent": True}

        def verifier(args):
            if not net["up"]:
                return OC.UNKNOWN, "the outbox could not be reached"
            n = dest.count(args["text"])
            return (OC.CONFIRMED, f"found in the sent folder ({n})") if n else (OC.FAILED, "not in the sent folder")

        tools._execute_tool = fake_exec
        OC.VERIFIERS["agent_mail_send"] = verifier
        origin = f"task:w4-{uuid.uuid4().hex[:6]}"
        from app.services import activity
        tok = activity.origin.set(origin)
        args = {"to": "friend@example.com", "text": f"hello {landed} {uuid.uuid4().hex[:4]}"}
        try:
            r1 = await tools.execute_tool("agent_mail_send", dict(args), reviewed=True)
            r2 = await tools.execute_tool("agent_mail_send", dict(args), reviewed=True)
            sends_after_r2 = sends["n"]
            net["up"] = True
            r3 = await tools.execute_tool("agent_mail_send", dict(args), reviewed=True)
            r4 = await tools.execute_tool("agent_mail_send", dict(args), reviewed=True)
        finally:
            activity.origin.reset(tok)
            tools._execute_tool = real
            OC.VERIFIERS.pop("agent_mail_send", None)
        label = "landed" if landed else "did not land"
        check(f"[{label}] first attempt reports UNKNOWN", r1.get("outcome") == OC.UNKNOWN, str(r1)[:200])
        check(f"[{label}] no blind retry while the destination is unreadable", r2.get("outcome") == OC.UNKNOWN and sends_after_r2 == 1, f"r2={r2} sends={sends_after_r2}")
        if landed:
            check("[landed] on recovery the original is confirmed and not re-sent",
                  r3.get("outcome") == OC.CONFIRMED and r3.get("already_done") and sends["n"] == 1, str(r3)[:200])
        else:
            check("[did not land] on recovery it retries exactly once and confirms",
                  r3.get("outcome") == OC.CONFIRMED and sends["n"] == 2, str(r3)[:200])
        check(f"[{label}] a further identical send is refused: never twice", r4.get("already_done") and dest.count(args["text"]) == 1,
              f"dest={dest} r4={str(r4)[:120]}")


async def w4b_job_not_restarted(q):
    print("W4b a background job is judged by its observed fate before any restart")
    from app.services.system_control import system_control_service as sc
    real = tools._execute_tool
    starts = {"n": 0}

    async def fake_exec(name, args, ws=None):
        if name == "computer_start_job":
            starts["n"] += 1
            jid = f"job-{uuid.uuid4().hex[:8]}"
            sc.background_jobs[jid] = {"id": jid, "name": args["name"], "status": "running", "exit_code": None}
            return {"success": True, "job_id": jid}
        return await real(name, args, ws)

    tools._execute_tool = fake_exec
    from app.services import activity
    tok = activity.origin.set(f"task:w4b-{uuid.uuid4().hex[:6]}")
    args = {"name": "Append step 7", "command": "sleep 40 && echo step7 >> /tmp/x"}
    try:
        r1 = await tools.execute_tool("computer_start_job", dict(args), reviewed=True)
        n1 = starts["n"]
        r2 = await tools.execute_tool("computer_start_job", dict(args), reviewed=True)
        sc.background_jobs[r1["job_id"]].update(status="completed", exit_code=0)
        r3 = await tools.execute_tool("computer_start_job", dict(args), reviewed=True)
        sc.background_jobs.pop(r1["job_id"])           # the process restarted: the job is gone
        r4 = await tools.execute_tool("computer_start_job", dict(args), reviewed=True)
        r5 = await tools.execute_tool("computer_start_job", dict(args), reviewed=True)
        sc.background_jobs[r4["job_id"]].update(status="completed", exit_code=0)
        renamed = {**args, "name": "Append step8"}     # live L4: the same job again under another label
        r6 = await tools.execute_tool("computer_start_job", renamed, reviewed=True)
    finally:
        activity.origin.reset(tok)
        tools._execute_tool = real
    check("first start runs and is UNVERIFIED", r1.get("outcome") == OC.UNVERIFIED and n1 == 1, str(r1)[:200])
    check("while it runs, an identical start is refused (wait instead)", r2.get("already_done") and "already running" in r2.get("error", ""))
    check("once it finished OK, an identical start is refused as done", r3.get("outcome") == OC.CONFIRMED and r3.get("already_done"))
    check("a job lost to a restart may run once more (its effect is not known)", starts["n"] == 2 and r4.get("job_id"), str(r4)[:200])
    check("and that new run is guarded the same way", r5.get("already_done") and starts["n"] == 2)
    check("the same command under another name is the same job (not run again)", r6.get("already_done") and starts["n"] == 2,
          str(r6)[:200])


async def w8_resolve_unknowns(q):
    print("W8 an UNKNOWN is checked at its destination before the work is judged")
    origin = f"task:w8-{uuid.uuid4().hex[:6]}"
    dest, up = [], {"v": False}

    def verifier(args):
        if not up["v"]:
            return OC.UNKNOWN, "unreachable"
        return (OC.CONFIRMED, "in inbox") if args["text"] in dest else (OC.FAILED, "not in inbox")
    OC.VERIFIERS["test_send"] = verifier
    args = {"port": 1, "text": f"m-{uuid.uuid4().hex[:4]}"}
    dest.append(args["text"])
    OC.settle(origin, "test_send", args, error=ConnectionResetError("dropped"))

    async def come_back():
        await asyncio.sleep(0.5)
        up["v"] = True
    asyncio.ensure_future(come_back())
    got = await OC.resolve_unknowns(origin, OC.ARGS_SEEN, wait_sec=5, every=0.1)
    check("UNKNOWN waited for the destination and resolved CONFIRMED", [g["final"] for g in got] == [OC.CONFIRMED], str(got)[:200])
    check("and nothing is left unresolved", OC.unresolved_unknowns(origin) == [])
    OC.VERIFIERS.pop("test_send", None)


async def w9_failed_sources(q):
    print("W9 a worker whose every source failed counts as failed")
    tag = uuid.uuid4().hex[:4]
    jobs = [f"Source ok {tag}", f"Source broken {tag}"]
    JOB_SCRIPT[jobs[0]] = ("answer", "fine")
    JOB_SCRIPT[jobs[1]] = ("fail_quietly", "file not found")
    r = await W.coordinate("w9", "two sources", [{"task": j} for j in jobs])
    j3 = f"Source admits {tag}"
    JOB_SCRIPT[j3] = ("admit", "**Could not read:** The status line, because the file was not found.")
    r2 = await W.coordinate("w9b", "one source", [{"task": j3}])
    check("a worker whose report admits it could not do the job is a failed check",
          [f["task"] for f in r2["failures"]] == [j3] and "Could not read" in r2["failures"][0]["why"], json.dumps(r2["failures"])[:200])
    check("the broken source is itemized as failed, with why",
          [f["task"] for f in r["failures"]] == [jobs[1]] and "file not found" in r["failures"][0]["why"], json.dumps(r["failures"])[:200])


async def w5_file_confirmed(q):
    print("W5 a real file write is CONFIRMED by read-back")
    p = TMP / "w5" / "note.txt"
    from app.services import activity
    tok = activity.origin.set(f"task:w5-{uuid.uuid4().hex[:4]}")
    try:
        r = await tools.execute_tool("computer_write_file", {"filepath": str(p), "content": "exact text"}, reviewed=True)
    finally:
        activity.origin.reset(tok)
    check("outcome CONFIRMED with read-back evidence", r.get("outcome") == OC.CONFIRMED and "read back" in (r.get("outcome_evidence") or ""),
          str(r)[:300])


async def w6_cancel_fanout(q):
    print("W6 cancel during a fan-out stops the workers")
    tag = uuid.uuid4().hex[:4]
    jobs = [f"Slow lookup {n} {tag}" for n in range(3)]
    for j in jobs:
        JOB_SCRIPT[j] = ("slow", "")
    req = f"Slow fan-out {tag}"
    PLANS[req] = [{"text": "three slow lookups", "parallel": jobs}]
    tid = O.submit(req)["task_id"]
    t0 = time.time()
    while time.time() - t0 < 10 and len([w for w in W.list_workers() if any(j in w["brief"] for j in jobs)]) < 3:
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.3)
    await q.cancel(tid)
    t = await wait_status(q, tid, ("cancelled",), timeout=8)
    ws = [w for w in W.list_workers() if any(j in w["brief"] for j in jobs)]
    check("task cancelled promptly", t["status"] == "cancelled")
    check("all three workers recorded interrupted", [w["state"] for w in ws] == ["interrupted"] * 3, str([w["state"] for w in ws]))


async def w7_orphans(q):
    print("W7 orphaned workers are not shown running")
    c = W._conn()
    c.execute("INSERT INTO workers (id, parent_id, parent_type, role, brief, envelope, state, created_at, last_activity_at) "
              "VALUES ('wrk-orphan', 'p', 'task', 'worker', 'b', 'read_only', 'running', ?, ?)", (time.time() - 500, time.time() - 500))
    c.commit()
    c.close()
    check("a long-silent running worker is shown quiet, not stuck or done", W.get("wrk-orphan")["shown_state"] == "quiet")
    W.mark_orphans_interrupted()
    check("after a restart it is interrupted", W.get("wrk-orphan")["state"] == "interrupted")


async def main(repeat):
    for r in range(repeat):
        print(f"=== run {r + 1}/{repeat}")
        q = TQ.task_queue = TQ.TaskQueue()
        q.start()
        await w1_two_jobs(q)
        await w2_six_checks(q)
        await w3_envelopes(q)
        await w4_unknown(q)
        await w4b_job_not_restarted(q)
        await w5_file_confirmed(q)
        await w8_resolve_unknowns(q)
        await w9_failed_sources(q)
        await w6_cancel_fanout(q)
        if r == 0:
            await w7_orphans(q)
        q.stop()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--repeat") + 1]) if "--repeat" in sys.argv else 1
    asyncio.run(main(n))
