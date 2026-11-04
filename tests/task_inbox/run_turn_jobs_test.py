"""
A chat turn waits for its own background jobs before it writes the answer (agent/turn_jobs.py).

Live (orchestrator acceptance, 2026-11-04): the agent checked a 30-75 s job about 2 s after starting it and
answered from that check. Now the answer is written from the job's observed end.

  J1 the turn's job ids come from its computer_start_job results, once each
  J2 a running job is waited for, with progress, and its observed end (exit 0, its output) is what the answer sees
  J3 a failing job's exit code is what the answer sees
  J4 a service (it listens on a port) is not waited for, and is reported as up, not finished
  J5 a job still running at the bound is reported as NOT finished
  J6 the answer's evidence shows the job's final state, not the early check

Run: .venv/bin/python tests/task_inbox/run_turn_jobs_test.py [--repeat N]
"""
import asyncio, os, socket, sys, tempfile, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_turnjobs_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

from app.agent import turn_jobs
from app.agent.evidence import EvidenceBoard
from app.services.system_control import system_control_service as sc

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


async def run(ids, **kw):
    statuses, jobs = [], {}
    async for ev in turn_jobs.settle(ids, **kw):
        if ev["type"] == "status":
            statuses.append(ev["content"])
        else:
            jobs[ev["job_id"]] = ev["result"]
    return statuses, jobs


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def main(repeat):
    for i in range(repeat):
        print(f"=== run {i + 1}/{repeat}")
        print("J1 job ids from the turn")
        trail = [{"tool": "computer_start_job", "result": {"job_id": "job-a"}}, {"tool": "computer_check_job", "result": {"job_id": "job-a"}},
                 {"tool": "computer_start_job", "result": {"job_id": "job-b"}}, {"tool": "computer_start_job", "result": {"success": False}},
                 {"tool": "computer_start_job", "result": {"job_id": "job-a"}}]
        check("started jobs, in order, once each", turn_jobs.started_ids(trail) == ["job-a", "job-b"], str(turn_jobs.started_ids(trail)))

        print("J2 a running job is waited for")
        r = await sc.start_background_job("count", "sleep 3; echo counted-to-three", cwd=str(TMP))
        early = sc.check_job(r["job_id"])
        t0 = time.time()
        st, jobs = await run([r["job_id"]], max_sec=30)
        j = jobs[r["job_id"]]
        check("the early check said running", early["status"] == "running")
        check("it waited, saying so", any("Waiting for count" in s for s in st), str(st))
        check("the answer sees it completed with exit 0", j["status"] == "completed" and j["exit_code"] == 0, str(j))
        check("and its output", any("counted-to-three" in line for line in j["new_output"]), str(j["new_output"]))
        check("it did not wait past the job", time.time() - t0 < 8, f"{time.time() - t0:.1f}s")

        print("J3 a failing job")
        r = await sc.start_background_job("fails", "sleep 1; echo nope >&2; exit 3", cwd=str(TMP))
        _, jobs = await run([r["job_id"]], max_sec=30)
        j = jobs[r["job_id"]]
        check("the answer sees it failed with exit 3", j["status"] == "failed" and j["exit_code"] == 3, str(j))

        print("J4 a service is not waited for")
        port = free_port()
        r = await sc.start_background_job("web", f"exec {sys.executable} -m http.server {port} --bind 127.0.0.1", cwd=str(TMP))
        for _ in range(40):
            if turn_jobs.is_service(sc.background_jobs[r["job_id"]]):
                break
            await asyncio.sleep(0.25)
        t0 = time.time()
        _, jobs = await run([r["job_id"]], max_sec=30)
        j = jobs[r["job_id"]]
        check("it returned at once", time.time() - t0 < 3, f"{time.time() - t0:.1f}s")
        check("reported as an up service, not finished", j["status"] == "running" and "service" in (j.get("note") or ""), str(j))
        await sc.stop_job(r["job_id"], force=True)

        print("J5 still running at the bound")
        r = await sc.start_background_job("slow", "sleep 30", cwd=str(TMP))
        t0 = time.time()
        _, jobs = await run([r["job_id"]], max_sec=1.5)
        j = jobs[r["job_id"]]
        check("it stopped waiting at the bound", 1.4 < time.time() - t0 < 4, f"{time.time() - t0:.1f}s")
        check("reported as NOT finished", j["status"] == "running" and "NOT finished" in (j.get("note") or ""), str(j))
        await sc.stop_job(r["job_id"], force=True)

        print("J6 the answer's evidence shows the final state")
        board = EvidenceBoard("run a build")
        r = await sc.start_background_job("build", "sleep 2; echo build-ok", cwd=str(TMP))
        board.update_from_tool("computer_start_job", {"name": "build"}, r)
        board.update_from_tool("computer_check_job", {"job_id": r["job_id"]}, sc.check_job(r["job_id"]))
        async for ev in turn_jobs.settle(turn_jobs.started_ids(board.tool_audit_trail), max_sec=30):
            if ev["type"] == "job":
                board.update_from_tool("computer_check_job", {"job_id": ev["job_id"]}, ev["result"])
        summary = board.to_compact_summary()
        check("the summary shows it completed with exit 0 and its output",
              "status completed, exit code 0" in summary and "build-ok" in summary, summary[-800:])
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--repeat") + 1]) if "--repeat" in sys.argv else 1
    asyncio.run(main(n))
