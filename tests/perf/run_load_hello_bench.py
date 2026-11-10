"""
Responsiveness under load, VM harness (R5, 2026-11-09).

The Tier 6 load leg (Mac, evidence legLoad.json) measured
GET /api/cli/hello latency while 3 stepwise file tasks ran: idle p95
41 ms, under load p95 4,367 ms / worst 14,484 ms against a 1 s limit.
This harness reproduces the same SHAPE on whatever machine runs it:
the real FastAPI server (uvicorn, own thread), the real orchestrator
and task_queue, real file writes recorded in the real activity log —
with a scripted researcher standing in for the model's token stream
(it streams ~100 small content chunks over ~1.2 s per step, then does
the step's file write). Numbers produced here are THIS machine's
numbers; they are not the Tier 6 grade, which only the Mac bench
under the Tier 6 protocol can give.

Run: .venv/bin/python tests/perf/run_load_hello_bench.py [--label X]
Prints a JSON summary; also writes /tmp/resp-bench-<label>.json.
"""
import argparse, asyncio, json, os, re, statistics, sys, tempfile, threading, time, urllib.request, uuid
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_loadbench_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO_UPDATE_CHECK": "1", "no_proxy": "localhost,127.0.0.1",
                   "NO_PROXY": "localhost,127.0.0.1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

PORT = 18931
BASE = f"http://127.0.0.1:{PORT}"

import app.services.task_queue as TQ
import app.services.orchestrator as O
import app.agent.researcher as RS
from app.services import activity, event_log

TQ.notify = lambda title, msg: None
TQ._phone = lambda task, kind: None

STEP_RE = re.compile(r"YOUR STEP NOW \(step \d+ of \d+\):\n(.+?)\n\nDo only this step", re.S)
WRITE_RE = re.compile(r"Create the file (\S+) containing exactly this: (\S+)")


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


class LoadResearcher:
    """Scripted stand-in for the model stream: streams content chunks
    for ~1.2 s (as a real step's generation does), then performs the
    step's file write through the activity log, like the real tool."""

    def __init__(self, *a, **k):
        pass

    async def run_research_stream(self, messages, **k):
        msg = messages[-1]["content"]
        m = STEP_RE.search(msg)
        step = m.group(1).strip() if m else msg
        w = WRITE_RE.search(step)
        for _ in range(100):
            yield {"type": "content", "content": "x" * 40}
            await asyncio.sleep(0.012)
        if w:
            _record_write(w.group(1), w.group(2))
            yield {"type": "content", "content": f"Wrote {w.group(1)}."}
            return
        yield {"type": "content", "content": f"Did: {step[:80]}"}


RS.AgenticResearcher = LoadResearcher


async def fake_planner(request):
    return [l.strip("- ").strip() for l in request.split("\n") if l.strip().startswith("- ")]


O.planner = fake_planner


def hello_once():
    t0 = time.time()
    try:
        with urllib.request.urlopen(BASE + "/api/cli/hello", timeout=30) as resp:
            resp.read()
            return (time.time() - t0) * 1000.0, resp.status
    except Exception as e:
        return (time.time() - t0) * 1000.0, repr(e)


def dist(samples):
    if not samples:
        return {}
    s = sorted(samples)
    return {"n": len(s), "min_ms": round(s[0], 1), "median_ms": round(statistics.median(s), 1),
            "p95_ms": round(s[min(len(s) - 1, int(0.95 * len(s)))], 1), "max_ms": round(s[-1], 1),
            "over_1000ms": sum(1 for x in s if x > 1000.0)}


def job_request(n, d):
    return ("Do these steps:\n"
            f"- Create the file {d}/a.txt containing exactly this: T6L{n}-A\n"
            f"- Create the file {d}/b.txt containing exactly this: T6L{n}-B\n"
            f"- Create the file {d}/c.txt containing exactly this: T6L{n}-C\n"
            f"- Create the file {d}/index.txt containing exactly this: IDX{n}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="vm")
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()

    import uvicorn
    from app.main import app
    cfg = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(cfg)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        ms, st = hello_once()
        if st == 200:
            break
        time.sleep(0.2)
    else:
        print(json.dumps({"error": "server never answered hello"}))
        return 1

    ev = {"label": a.label, "root": str(ROOT)}
    ev["hello_idle"] = dist([hello_once()[0] for _ in range(10)])
    print("idle", ev["hello_idle"], flush=True)

    ids = {}

    def submit(n):
        d = TMP / "work" / f"job{n}"
        body = json.dumps({"request": job_request(n, d)}).encode()
        r = urllib.request.Request(BASE + "/api/orchestrator/tasks", data=body,
                                   headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(r, timeout=60) as resp:
            ack = json.loads(resp.read()).get("ack") or {}
            ids[n] = ack.get("task_id")

    threads = [threading.Thread(target=submit, args=(n,)) for n in (1, 2, 3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ev["task_ids"] = ids
    print("tasks", ids, flush=True)

    samples = []
    t0 = time.time()
    done = False
    while time.time() - t0 < a.timeout:
        ms, st = hello_once()
        samples.append(ms)
        statuses = []
        for tid in ids.values():
            if tid:
                try:
                    statuses.append(TQ.get(tid)["status"])
                except Exception:
                    statuses.append(None)
        if statuses and all(s in ("done", "failed", "cancelled") for s in statuses):
            done = True
            break
        time.sleep(0.25)
    ev["all_tasks_terminal"] = done
    ev["final_statuses"] = statuses
    ev["hello_under_load"] = dist(samples)
    print("under load", ev["hello_under_load"], "done:", done, flush=True)

    slow = []
    for tid in ids.values():
        if tid:
            for e in event_log.query(kinds=["task.slow_step"], task_id=tid)["events"]:
                slow.append({"task_id": tid, **(e.get("payload") or {})})
    ev["slow_steps"] = slow
    print("slow steps", json.dumps(slow), flush=True)

    out = Path(f"/tmp/resp-bench-{a.label}.json")
    out.write_text(json.dumps(ev, indent=1))
    print("WROTE", out)
    server.should_exit = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
