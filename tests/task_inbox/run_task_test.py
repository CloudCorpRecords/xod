"""
Task inbox end-to-end test against the local MockJobs site (isolated test databases).

T1 and T2 each send a different message; both must pause at "Send" in their own tab. Approving
T2 first and then T1 must send exactly those two messages, each once, with the right text.
T3 (a price comparison) runs in the same queue and must finish with both prices.
"""
import asyncio, json, os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys as _sys; _sys.path.insert(0, str(ROOT)); from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
TMP = Path(tempfile.mkdtemp(prefix="xod_task_test_"))
os.environ["XOD_DATASET_DB"] = str(_DATA / "test_runs" / "flywheel_test.db")
sys.path.insert(0, str(ROOT))
import app.services.task_queue as TQ
TQ.DB_PATH, TQ.SHOTS_DIR = TMP / "tasks.db", TMP / "shots"
q = TQ.TaskQueue()
TQ.notify = lambda title, msg: print(f"   [notification] {title}: {msg}", flush=True)
SITE = "http://localhost:8799"


def state():
    return json.loads(urllib.request.urlopen(SITE + "/admin/state").read())


async def wait_for(task_id, statuses, timeout=900):
    t0, last = time.time(), None
    while time.time() - t0 < timeout:
        t = q.get(task_id)
        if t["now"] != last:
            print(f"   {task_id[-4:]} [{t['status']}] {t['now']}", flush=True)
            last = t["now"]
        if t["status"] in statuses:
            return t
        await asyncio.sleep(2)
    raise TimeoutError(task_id)


async def main():
    subprocess.run("lsof -ti:8799 | xargs kill -9", shell=True, stderr=subprocess.DEVNULL)
    mock = subprocess.Popen([sys.executable, str(ROOT / "tests/browser_missions/mock_jobs.py"), "8799"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    q.start()
    t1 = q.create(f"Send a message to the Acme recruiter on {SITE}/messages saying: First message from task one.")
    t2 = q.create(f"Send a message to the Acme recruiter on {SITE}/messages saying: Second message from task two.")
    t3 = q.create(f"Compare the price of the Trail Runner 2 shoes on {SITE}/shopa and {SITE}/shopb and tell me which shop is cheaper")
    for t in (t1, t2):
        w = await wait_for(t["id"], ("waiting", "done", "failed"))
        print(f"== {t['id'][-4:]} -> {w['status']} / {w.get('waiting_kind')} / {json.dumps(w.get('waiting_detail'))[:120]}", flush=True)
    sent_before = len(state()["messages"])
    print("messages sent before any approval:", sent_before, flush=True)
    print("approve T2 ->", q.approve(t2["id"]), flush=True)
    d2 = await wait_for(t2["id"], ("done", "failed", "waiting"))
    print("approve T1 ->", q.approve(t1["id"]), flush=True)
    d1 = await wait_for(t1["id"], ("done", "failed", "waiting"))
    d3 = await wait_for(t3["id"], ("done", "failed", "waiting"))
    msgs = [m["text"] for m in state()["messages"]]
    print("\n========== RESULTS ==========")
    print("T2:", d2["status"], "| T1:", d1["status"], "| T3:", d3["status"])
    print("messages:", msgs)
    ok_iso = msgs == ["Second message from task two.", "First message from task one."]
    print("isolation (exact messages, right order, once each):", ok_iso)
    print("T3 answer:", (d3.get("result") or "")[:200].replace("\n", " "))
    print("T3 has both prices:", "89.99" in (d3.get("result") or "") and "79.5" in (d3.get("result") or ""))
    print("screenshots saved:", sorted(p.name for p in (TMP / "shots").glob("*.jpg")))
    mock.kill()


asyncio.run(main())
