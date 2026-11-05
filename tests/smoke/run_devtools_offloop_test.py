"""
Asking Chrome's debug port never freezes the server (services/browser_sandbox.py).

Live 2026-11-04 (final count, pass h2, W2): with three research tasks driving the browser, the server froze for
3.27 s; the debug run showed the stall inside run_browser_mission. Each browser step asks Chrome's debug port
for its tabs, and those requests were made inline on the event loop, so a busy Chrome froze everything.

A stand-in debug port answers after 1.2 s; a ticker on the loop measures the longest gap while the browser
code asks it.

  D1 the startup check (ensure_browser_running) leaves the loop free
  D2 finding the active tab leaves the loop free
  D3 listing, opening, switching and closing tabs leave the loop free
  D4 the answers still arrive (the tab list, the new tab)

Run: .venv/bin/python tests/smoke/run_devtools_offloop_test.py
"""
import asyncio, json, os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.update({"XOD_DATA_DIR": tempfile.mkdtemp(prefix="xod_devtools_"), "XOD_NO_UPDATE_CHECK": "1"})
sys.path.insert(0, str(ROOT))
import app.services.browser_sandbox as BS

DELAY = 1.2
TABS = [{"id": "T1", "type": "page", "title": "One", "url": "https://one.example/", "webSocketDebuggerUrl": "ws://x/T1"}]
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


class Slow(BaseHTTPRequestHandler):
    def _answer(self):
        time.sleep(DELAY)
        body = json.dumps({"id": "T2", "type": "page", "title": "New", "url": "about:blank",
                           "webSocketDebuggerUrl": "ws://x/T2"} if "/new" in self.path else TABS
                          if self.path.rstrip("/").endswith("/json") else "ok").encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_PUT = _answer

    def log_message(self, *a):
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass               # a probe that gave up early (its short timeout); not a failure


srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BS.DEBUG_PORT = srv.server_address[1]


async def longest_gap(coro):
    gaps, stop = [], asyncio.Event()

    async def tick():
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.05)
            now = time.monotonic()
            gaps.append(now - last)
            last = now
    t = asyncio.ensure_future(tick())
    await asyncio.sleep(0.12)          # the ticker is running before the browser code starts
    try:
        out = await coro
    finally:
        stop.set()
        await t
    return out, max(gaps or [0])


async def main():
    sb = type(BS.browser_sandbox_service)()
    ok, gap = await longest_gap(sb.ensure_browser_running())
    check("D1 the startup check leaves the loop free", ok is True and gap < 0.4, f"ok={ok} gap={gap:.2f}s")
    ws, gap = await longest_gap(sb._get_active_tab_ws_url())
    check("D2 finding the active tab leaves the loop free", ws == "ws://x/T1" and gap < 0.4, f"ws={ws} gap={gap:.2f}s")
    worst, got = 0.0, {}
    for action, kw in (("list", {}), ("new", {"url": "about:blank"}), ("switch", {"target_id": "T1"}), ("close", {"target_id": "T2"})):
        r, gap = await longest_gap(sb.manage_tabs(action, **kw))
        got[action], worst = r, max(worst, gap)
    check("D3 tab list/new/switch/close leave the loop free", worst < 0.4, f"gap={worst:.2f}s")
    check("D4 the answers still arrive", got["list"].get("count") == 1 and got["new"].get("active_tab_id") == "T2"
          and all(got[a].get("success") for a in got), json.dumps(got, default=str)[:300])


asyncio.run(main())
srv.shutdown()
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:", *failed, sep="\n  ")
    sys.exit(1)
