"""
Browser sandbox regression tests for ensure_browser_running.

Case A (slow, not gone): the debug port times out for the first three probes
and answers on the fourth. ensure must return True WITHOUT killing the running
Chrome or launching a new one. Both Xod instances share one profile folder and
one debug port, so killing a busy sibling's Chrome destroys its in-flight work.

Case B (cold start slower than 5 seconds): nothing is listening, Chrome is
launched, and the debug port only starts listing pages 6.5 seconds later.
ensure must still return True. The old code gave up at 5 seconds and raised
"Background Chrome failed to start or open remote debugging port."

Everything Chrome-shaped is faked; no real browser is touched.
Run: .venv/bin/python tests/self_heal/run_browser_wait_test.py
"""
import asyncio, json, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import app.services.browser_sandbox as BS

svc = BS.browser_sandbox_service
TABS = [{"id": "t1", "type": "page", "webSocketDebuggerUrl": "ws://unused"}]


class Resp:
    def __init__(self, payload):
        self._p = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._p).encode()


class FakeProc:
    def poll(self):
        return None

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def timeout_error():
    return urllib.error.URLError(TimeoutError("timed out"))


def refused_error():
    return urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))


class Sandbox:
    """Context manager swapping every Chrome-shaped seam for a fake."""

    def __init__(self, urlopen):
        self.urlopen = urlopen
        self.calls = {"kill": 0, "popen": 0}

    def __enter__(self):
        self._real_urlopen = urllib.request.urlopen
        self._real_popen = BS.subprocess.Popen
        self._real_kill = BS._kill_own_browser
        self._real_data_dir = BS.DATA_DIR
        self._real_end = getattr(BS, "_end_with_xod", None)
        calls = self.calls

        def fake_popen(*a, **k):
            calls["popen"] += 1
            return FakeProc()

        def fake_kill(port_too=False):
            calls["kill"] += 1
            return 0

        urllib.request.urlopen = self.urlopen
        BS.subprocess.Popen = fake_popen
        BS._kill_own_browser = fake_kill
        BS.DATA_DIR = tempfile.mkdtemp(prefix="xod_sandbox_test_")
        if self._real_end is not None:
            BS._end_with_xod = lambda proc: None
        svc._get_chrome_path = lambda: "/bin/true"
        svc.proc = None
        svc.headless = True
        return self

    def __exit__(self, *a):
        urllib.request.urlopen = self._real_urlopen
        BS.subprocess.Popen = self._real_popen
        BS._kill_own_browser = self._real_kill
        BS.DATA_DIR = self._real_data_dir
        if self._real_end is not None:
            BS._end_with_xod = self._real_end
        del svc.__dict__["_get_chrome_path"]
        return False


async def case_a():
    state = {"n": 0}

    def fake_urlopen(url, timeout=None):
        state["n"] += 1
        if state["n"] <= 3:
            raise timeout_error()
        return Resp(TABS)

    with Sandbox(fake_urlopen) as sb:
        ok = await svc.ensure_browser_running()
    assert ok is True, "ensure should succeed once the busy Chrome answers"
    assert sb.calls["kill"] == 0, f"must not kill a busy-but-alive Chrome (kill called {sb.calls['kill']}x)"
    assert sb.calls["popen"] == 0, "must not launch a second Chrome while one is alive"
    print(f"PASS case A: slow Chrome left alone (probes={state['n']})", flush=True)


async def case_b():
    state = {"popen_at": None}
    real_popen_marker = state

    def fake_urlopen(url, timeout=None):
        if real_popen_marker["popen_at"] is None:
            raise refused_error()
        if time.time() - real_popen_marker["popen_at"] < 6.5:
            return Resp([])
        return Resp(TABS)

    with Sandbox(fake_urlopen) as sb:
        orig_fake = BS.subprocess.Popen

        def marking_popen(*a, **k):
            state["popen_at"] = time.time()
            return orig_fake(*a, **k)

        BS.subprocess.Popen = marking_popen
        t0 = time.time()
        ok = await svc.ensure_browser_running()
        elapsed = time.time() - t0
    assert ok is True, "ensure should wait out a slow cold start"
    assert sb.calls["popen"] == 1, "exactly one Chrome should be launched"
    assert elapsed >= 6.5, f"cannot have succeeded before pages existed (elapsed {elapsed:.1f}s)"
    print(f"PASS case B: slow cold start waited out ({elapsed:.1f}s)", flush=True)


async def main():
    await case_a()
    await case_b()
    print("PASS: browser wait tests", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
