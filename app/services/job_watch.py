"""
Waiting for things, the way a person would: start something long, then watch for it to be ready.

wait_for_stream() polls one condition and yields progress lines every ~15 s (shown in chat and on
the task card, which also keeps the doctor from thinking a long wait is a hung task), then the
result. Conditions:
  job_done      a background job (computer_start_job) finished             job_id
  job_output    a background job printed something (e.g. "ready on")       job_id, text (regex ok)
  file_exists   a file appeared (optionally at least min_bytes big)        path, min_bytes
  text_in_file  a file contains some text                                  path, text
  port_open     something is listening on a local port                     port
  url_ok        a URL answers with a non-error status                      url
  process_gone  a process exited                                           pid
A wait never runs longer than MAX_WAIT_SEC; a job that fails while being waited on ends the wait
early with its last output, instead of waiting out the clock.
"""
import asyncio
import os
import re
import socket
import time
from typing import Any, AsyncGenerator, Dict, Tuple

MAX_WAIT_SEC = 600
PROGRESS_EVERY_SEC = 15
CONDITIONS = ("job_done", "job_output", "file_exists", "text_in_file", "port_open", "url_ok", "process_gone")


def _describe(cond: str, a: Dict[str, Any]) -> str:
    return {
        "job_done": f"job {a.get('job_id')} to finish",
        "job_output": f"job {a.get('job_id')} to print “{a.get('text')}”",
        "file_exists": f"{os.path.basename(str(a.get('path', '')))} to appear",
        "text_in_file": f"“{a.get('text')}” in {os.path.basename(str(a.get('path', '')))}",
        "port_open": f"port {a.get('port')} to open",
        "url_ok": f"{a.get('url')} to respond",
        "process_gone": f"process {a.get('pid')} to exit",
    }.get(cond, cond)


async def _probe(cond: str, a: Dict[str, Any]) -> Tuple[bool, str, bool]:
    """(met, detail, give_up_early)"""
    from app.services.system_control import system_control_service as sc
    if cond in ("job_done", "job_output"):
        job = sc.background_jobs.get(a.get("job_id", "")) or next(
            (j for j in sc.background_jobs.values() if j["name"] == a.get("job_id")), None)
        if not job:
            return False, f"no job called {a.get('job_id')}", True
        if cond == "job_done":
            if job["status"] != "running":
                return True, f"{job['status']} (exit code {job.get('exit_code')})", False
            return False, "still running", False
        pat = re.compile(str(a.get("text", "")), re.I) if a.get("text") else None
        hit = next((l for l in reversed(job["logs"]) if pat and pat.search(l)), None)
        if hit:
            return True, hit[:200], False
        if job["status"] != "running":
            tail = " | ".join(job["logs"][-5:])[-400:]
            return False, f"the job ended ({job['status']}, exit {job.get('exit_code')}) without printing it. Last output: {tail}", True
        return False, "not printed yet", False
    if cond == "file_exists":
        p = os.path.expanduser(str(a.get("path", "")))
        ok = os.path.exists(p) and os.path.getsize(p) >= int(a.get("min_bytes") or 1)
        return ok, (f"{os.path.getsize(p)} bytes" if os.path.exists(p) else "not there yet"), False
    if cond == "text_in_file":
        p = os.path.expanduser(str(a.get("path", "")))
        try:
            with open(p, "r", errors="replace") as f:
                found = str(a.get("text", "")) in f.read()
            return found, "found" if found else "not in the file yet", False
        except FileNotFoundError:
            return False, "file not there yet", False
    if cond == "port_open":
        try:
            with socket.create_connection(("127.0.0.1", int(a.get("port"))), timeout=1):
                return True, "listening", False
        except OSError:
            return False, "nothing listening yet", False
    if cond == "url_ok":
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as c:
                r = await c.get(str(a.get("url")))
            return r.status_code < 400, f"HTTP {r.status_code}", False
        except Exception as e:
            return False, f"no answer yet ({type(e).__name__})", False
    if cond == "process_gone":
        try:
            os.kill(int(a.get("pid")), 0)
            return False, "still running", False
        except ProcessLookupError:
            return True, "exited", False
        except PermissionError:
            return False, "still running", False
    return False, f"unknown condition '{cond}' (use one of: {', '.join(CONDITIONS)})", True


def infer_condition(a: Dict[str, Any]) -> str:
    """The model often leaves out `condition`; the other arguments say what it meant."""
    if a.get("job_id"):
        return "job_output" if a.get("text") else "job_done"
    if a.get("port"):
        return "port_open"
    if a.get("url"):
        return "url_ok"
    if a.get("path"):
        return "text_in_file" if a.get("text") else "file_exists"
    if a.get("pid"):
        return "process_gone"
    return ""


async def wait_for_stream(args: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
    cond = str(args.get("condition", "")).strip()
    if cond not in CONDITIONS:
        cond = infer_condition(args) or cond
    timeout = max(1, min(int(args.get("timeout_sec") or 120), MAX_WAIT_SEC))
    interval = max(0.5, min(float(args.get("check_every_sec") or 1.0), 30))
    what = _describe(cond, args)
    started = last_progress = time.time()
    met, detail, stop = await _probe(cond, args)
    while not met and not stop and time.time() - started < timeout:
        await asyncio.sleep(interval)
        met, detail, stop = await _probe(cond, args)
        if not met and time.time() - last_progress >= PROGRESS_EVERY_SEC:
            last_progress = time.time()
            yield {"type": "progress", "text": f"⏳ Waiting for {what}: {detail} ({int(time.time() - started)} s)"}
    waited = round(time.time() - started, 1)
    if met:
        result = {"success": True, "met": True, "condition": cond, "waited_sec": waited, "detail": detail}
    elif stop:
        result = {"success": False, "met": False, "condition": cond, "waited_sec": waited, "error": detail}
    else:
        result = {"success": False, "met": False, "condition": cond, "waited_sec": waited,
                  "error": f"Timed out after {timeout} s waiting for {what} (last check: {detail})"}
    yield {"type": "result", "result": result}
