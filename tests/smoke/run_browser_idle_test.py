"""
The sandbox browser's idle close (services/browser_sandbox.py: check_idle / browser_idle_minutes):
a Chrome started for one task used to stay up for the life of the server, holding its memory
for nothing. Now it closes after a quiet spell and starts again on the next browser task.

  - the limit: the browser_idle_minutes setting, else XOD_BROWSER_IDLE_MINUTES, else 10; 0 = never
  - nothing running, or never used: nothing to close
  - used a moment ago: left alone
  - quiet past the limit: closed, and says so
  - a mission paused on the person's OK (pending_submit) is never closed from under them
  - an operation in flight (the lock held) is never closed from under it

No real Chrome: a stand-in process with the same shape (poll/terminate/wait/kill).
Throwaway data folder; XOD_SPEECH off.
  .venv/bin/python tests/smoke/run_browser_idle_test.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-browser-idle-")

from app.services import browser_sandbox as bs   # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


class FakeProc:
    """A running process, as far as the service can tell."""

    def __init__(self):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def fresh(minutes_env=None):
    """A service with a browser 'running', and the env limit set (or unset)."""
    if minutes_env is None:
        os.environ.pop("XOD_BROWSER_IDLE_MINUTES", None)
    else:
        os.environ["XOD_BROWSER_IDLE_MINUTES"] = minutes_env
    svc = bs.BrowserSandboxService()
    svc.proc = FakeProc()
    svc.last_used = time.time()
    return svc


async def main():
    # The limit itself.
    os.environ.pop("XOD_BROWSER_IDLE_MINUTES", None)
    check("default limit is 10 minutes", bs.browser_idle_minutes() == 10, str(bs.browser_idle_minutes()))
    os.environ["XOD_BROWSER_IDLE_MINUTES"] = "5"
    check("env sets the limit", bs.browser_idle_minutes() == 5)
    os.environ["XOD_BROWSER_IDLE_MINUTES"] = "0"
    check("env 0 turns the close off", bs.browser_idle_minutes() == 0)
    os.environ["XOD_BROWSER_IDLE_MINUTES"] = "banana"
    check("a bad env value falls back to 10", bs.browser_idle_minutes() == 10)

    # 0 = never: even a long-quiet browser stays.
    svc = fresh("0")
    svc.last_used = time.time() - 3600
    check("limit 0 never closes", await svc.check_idle() is None and svc.proc is not None)

    # Nothing running / never used.
    svc = fresh("10")
    svc.proc = None
    svc.last_used = time.time() - 3600
    check("no browser running: nothing to do", await svc.check_idle() is None)
    svc = fresh("10")
    svc.last_used = None
    check("browser up but never used: left alone", await svc.check_idle() is None and svc.proc is not None)

    # Used a moment ago.
    svc = fresh("10")
    check("used just now: left alone", await svc.check_idle() is None and svc.proc is not None)

    # Quiet past the limit: closed.
    svc = fresh("10")
    proc = svc.proc
    svc.last_used = time.time() - 11 * 60
    msg = await svc.check_idle()
    check("quiet past the limit: closed, and says so", msg is not None and svc.proc is None)
    check("the close terminated the process", proc.terminated)

    # A mission paused on the person's OK is using the browser, however long it waits.
    svc = fresh("10")
    svc.last_used = time.time() - 3600
    svc.pending_submit = {"mission": "m1"}
    check("pending submit: never closed", await svc.check_idle() is None and svc.proc is not None)
    svc.pending_submit = None

    # An operation in flight holds the lock.
    svc = fresh("10")
    svc.last_used = time.time() - 3600
    await svc._lock.acquire()
    try:
        check("operation in flight: never closed", await svc.check_idle() is None and svc.proc is not None)
    finally:
        svc._lock.release()
    msg = await svc.check_idle()
    check("once the operation ends, the quiet browser closes", msg is not None and svc.proc is None)


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'}: {len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
