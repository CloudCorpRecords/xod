"""
A turn does not end on a guess about its own background jobs.

Observed live (orchestrator acceptance, 2026-11-04): the agent started a 30-75 s job, checked it about 2 s
later, and wrote its answer from that check ("still running", or worse, "done"). The orchestrator now waits
on a step's jobs itself, but a plain chat turn did not. Here, before the answer is written, every job this
turn started that is still running is waited for, bounded, with progress, so the answer is written from the
job's observed end (status, exit code, output).

Services are not waited for: a job that has opened a listening port, or says it is listening or ready, is
meant to keep running, and the answer says it is up. Anything still running at the bound is reported as
still running, never as finished.
"""
import asyncio
import os
import re
import time
from typing import Any, AsyncGenerator, Dict, Iterable, List

TURN_JOB_WAIT_SEC = float(os.environ.get("XOD_TURN_JOB_WAIT_SEC", "120"))
SERVICE_RE = re.compile(r"\b(listening on|listening at|running on|serving (?:on|at)|server (?:started|running|listening)|"
                        r"ready (?:on|at|in)|started server|local:\s+https?://|https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0):\d+)",
                        re.I)


def started_ids(trail: Iterable[Dict[str, Any]]) -> List[str]:
    """The job ids this turn's computer_start_job calls returned, in order, once each."""
    out: List[str] = []
    for t in trail or []:
        r = t.get("result") if isinstance(t, dict) else None
        if t.get("tool") == "computer_start_job" and isinstance(r, dict) and r.get("job_id") and r["job_id"] not in out:
            out.append(r["job_id"])
    return out


def _listening(pid: int) -> bool:
    try:
        import psutil
        procs = [psutil.Process(pid)]
        procs += procs[0].children(recursive=True)
        for p in procs:
            try:
                if any(c.status == psutil.CONN_LISTEN for c in p.net_connections(kind="inet")):
                    return True
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
    except Exception:
        return False
    return False


def is_service(job: Dict[str, Any]) -> bool:
    """A job that is meant to keep running: it listens on a port, or its output says it is up."""
    if any(SERVICE_RE.search(str(line)) for line in (job.get("logs") or [])[-200:]):
        return True
    return bool(job.get("pid")) and _listening(int(job["pid"]))


async def settle(job_ids: List[str], max_sec: float = None, poll: float = 0.5) -> AsyncGenerator[Dict[str, Any], None]:
    """Waits for this turn's running jobs. Yields status events while it waits, then one
    {"type": "job", "job_id", "result"} per job: its observed state (system_control.check_job)."""
    from app.services.system_control import system_control_service as sc
    max_sec = TURN_JOB_WAIT_SEC if max_sec is None else max_sec
    jobs = {j: sc.background_jobs.get(j) for j in job_ids}
    jobs = {k: v for k, v in jobs.items() if v}
    waiting = [k for k, v in jobs.items() if v.get("status") == "running" and not is_service(v)]
    if waiting:
        names = ", ".join(jobs[k].get("name") or k for k in waiting)
        yield {"type": "status", "content": f"⏳ Waiting for {names} to finish before I answer…"}
        deadline, last_note = time.time() + max_sec, time.time()
        while time.time() < deadline:
            waiting = [k for k in waiting if jobs[k].get("status") == "running" and not is_service(jobs[k])]
            if not waiting:
                break
            if time.time() - last_note >= 15:
                last_note = time.time()
                yield {"type": "status", "content": f"⏳ Still waiting for {', '.join(jobs[k].get('name') or k for k in waiting)} "
                                                    f"({round(time.time() - jobs[waiting[0]]['start_time'])} s so far)…"}
            await asyncio.sleep(poll)
    for k, j in jobs.items():
        r = dict(sc.check_job(k, tail=40))
        if j.get("status") == "running":
            if is_service(j):
                r["note"] = "It is a service that keeps running (it is listening or says it is ready); it is up, not finished."
            else:
                r["note"] = (f"Still running after waiting {round(max_sec)} s: it has NOT finished, so do not say it did or "
                             f"report its result. Say it is still running and how to check on it.")
        yield {"type": "job", "job_id": k, "result": r}
