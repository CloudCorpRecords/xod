"""
browser_run LIVE long-form suite (AGE-99): real headless Chrome, Xod's own sandbox
profile on a throwaway data dir, and a local test site served from this process.
No accounts, no sign-ins, nothing leaves 127.0.0.1.

Three runs, each 10+ steps across tabs:
  Run 1 research: search on the home page, follow results, open the catalog in a
        second tab, extract, switch back and forth by URL and by index, assert.
  Run 2 recovery: a result that renders 900 ms late (settle recovery), a button
        covered by an overlay that only the alternate dispatch reaches, then a
        full form fill with read-back and a verified submit message.
  Run 3 tab juggling: open three tabs, switch by title, close the middle one,
        verify the counts and that the run stayed on its claimed tab.

Run on the Mac: python3 tests/smoke/run_browser_longform_live_test.py
Prints one line per step and a per-run summary; exit 1 if any run is incomplete.
"""
import asyncio, json, os, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_longform_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1", "XOD_SPEECH": "off",
                   "HOME": str(TMP), "XDG_CONFIG_HOME": str(TMP / "config"),
                   "XDG_DATA_HOME": str(TMP / "share"),
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_SECRETS_SERVICE": "Xod test keys"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

from app import paths
paths.DEV_ENV_PATH = TMP / "absent.env"

PAGES = {
    "/home": """<!doctype html><title>Test Home</title>
      <h1>Longform Test Home</h1>
      <input id="q" type="text" placeholder="Search the test site">
      <button id="go" onclick="location.href='/results?query='+encodeURIComponent(document.getElementById('q').value)">Search</button>
      <a id="catalog-link" href="/catalog">Open the catalog</a>
      <a id="form-link" href="/form">Open the form</a>""",
    "/results": """<!doctype html><title>Test Results</title>
      <h1>Results page</h1><p id="echo">You searched for: <span id="term"></span></p>
      <script>document.getElementById('term').textContent =
        new URLSearchParams(location.search).get('query') || '';</script>
      <a href="/catalog">Continue to the catalog</a>""",
    "/catalog": """<!doctype html><title>Test Catalog</title>
      <h1>Catalog</h1>
      <ul><li>Item Alpha</li><li>Item Beta</li><li>Item Gamma</li></ul>
      <button id="reveal" onclick="setTimeout(() => {
        const d = document.createElement('p'); d.id = 'secret';
        d.textContent = 'Secret Item 42'; document.body.appendChild(d); }, 900)">Reveal the secret item</button>
      <div style="position:relative; width:220px; height:60px;">
        <button id="covered" style="width:220px; height:60px;"
          onclick="document.getElementById('cov').textContent = 'Covered button reached'">Covered Button</button>
        <div style="position:absolute; inset:0; background:rgba(200,0,0,0.35);"></div>
      </div>
      <p id="cov">not reached yet</p>""",
    "/form": """<!doctype html><title>Test Form</title>
      <h1>Signup form</h1>
      <label>Name <input id="name" type="text"></label>
      <label>Email <input id="email" type="text"></label>
      <label>Color <select id="color"><option>red</option><option>green</option><option>blue</option></select></label>
      <label>Agree <input id="agree" type="checkbox"></label>
      <button id="save" onclick="document.getElementById('saved').textContent =
        'Saved: ' + document.getElementById('name').value">Save</button>
      <p id="saved">not saved yet</p>""",
    "/tab2": "<!doctype html><title>Marker Two</title><h1>Marker Two Page</h1>",
    "/tab3": "<!doctype html><title>Marker Three</title><h1>Marker Three Page</h1>",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        body = PAGES.get(path)
        if body is None:
            self.send_response(404); self.end_headers(); self.wfile.write(b"nope"); return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def report(tag, rec):
    print(f"\n== {tag}: {rec['status']}  landed {rec.get('steps_landed')}/{rec.get('steps_total')}"
          f" in {rec.get('secs')}s")
    for s in rec["steps"]:
        tries = ",".join(a.get("strategy", "?") for a in s.get("attempts", []))
        print(f"  {s.get('step_num'):>2}. {s['action']:<10} {s['status']:<9} [{tries}] {s.get('detail', '')[:90]}")
    return rec["status"] == "completed"


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    print(f"test site on {base}, data dir {TMP}")

    from app.services.browser_sandbox import browser_sandbox_service as sb
    from app.services.browser_run import run_browser_steps
    sb.headless = True
    ok = True
    try:
        run1 = await run_browser_steps(sb, [
            {"action": "navigate", "url": f"{base}/home"},
            {"action": "type", "selector": "#q", "text": "secret item"},
            {"action": "click", "target": "#go", "expect": {"url_contains": "/results"}},
            {"action": "assert", "expect": {"text_contains": "secret item"}},
            {"action": "open_tab", "url": f"{base}/catalog"},
            {"action": "assert", "expect": {"text_contains": "Item Gamma"}},
            {"action": "extract", "name": "catalog"},
            {"action": "switch_tab", "tab": {"url_contains": "/results"}},
            {"action": "assert", "expect": {"text_contains": "Results page"}},
            {"action": "switch_tab", "tab": {"url_contains": "/catalog"}},
            {"action": "open_tab", "url": f"{base}/form"},
            {"action": "assert", "expect": {"title_contains": "Test Form"}},
            {"action": "switch_tab", "tab": {"url_contains": "/results"}},
            {"action": "assert", "expect": {"title_contains": "Test Results"}},
        ], owner="longform-live-1")
        ok &= report("run 1 research across tabs", run1)
        ok &= "Item Gamma" in run1["extracts"].get("catalog", "")

        run2 = await run_browser_steps(sb, [
            {"action": "navigate", "url": f"{base}/catalog"},
            {"action": "click", "target": "#reveal", "expect": {"text_contains": "Secret Item 42"}},
            {"action": "click", "target": "#covered", "expect": {"text_contains": "Covered button reached"}},
            {"action": "navigate", "url": f"{base}/form"},
            {"action": "type", "selector": "#name", "text": "Ada Lovelace"},
            {"action": "type", "selector": "#email", "text": "ada@example.org"},
            {"action": "fill", "fields": [{"selector": "#name", "value": "Ada Lovelace"},
                                          {"selector": "#email", "value": "ada@example.org"}]},
            {"action": "click", "target": "#save", "expect": {"text_contains": "Saved: Ada Lovelace"}},
            {"action": "assert", "expect": {"value_equals": {"selector": "#name", "value": "Ada Lovelace"}}},
            {"action": "scroll", "direction": "down", "amount": 300},
            {"action": "press_key", "key": "Tab"},
            {"action": "wait", "target": "#saved"},
            {"action": "extract", "name": "form_final"},
        ], owner="longform-live-2")
        ok &= report("run 2 recovery and form", run2)
        s2 = {s["step_num"]: s for s in run2["steps"]}
        print("  reveal step:", s2.get(2, {}).get("status"), "| covered step:", s2.get(3, {}).get("status"))
        ok &= s2.get(2, {}).get("status") in ("landed", "recovered")
        ok &= s2.get(3, {}).get("status") in ("landed", "recovered")

        run3 = await run_browser_steps(sb, [
            {"action": "navigate", "url": f"{base}/home"},
            {"action": "open_tab", "url": f"{base}/tab2"},
            {"action": "open_tab", "url": f"{base}/tab3"},
            {"action": "assert", "expect": {"title_contains": "Marker Three"}},
            {"action": "switch_tab", "tab": {"title_contains": "Marker Two"}},
            {"action": "assert", "expect": {"text_contains": "Marker Two Page"}},
            {"action": "close_tab", "tab": {"title_contains": "Marker Two"}},
            {"action": "switch_tab", "tab": {"title_contains": "Marker Three"}},
            {"action": "assert", "expect": {"title_contains": "Marker Three"}},
            {"action": "switch_tab", "tab": {"index": 0}},
            {"action": "switch_tab", "tab": {"title_contains": "Test Home"}},
            {"action": "assert", "expect": {"title_contains": "Test Home"}},
            {"action": "close_tab", "tab": {"title_contains": "Marker Three"}},
            {"action": "assert", "expect": {"title_contains": "Test Home"}},
        ], owner="longform-live-3")
        ok &= report("run 3 tab juggling", run3)
        ok &= not any("Marker" in (t.get("title") or "") for t in run3["final_tabs"])
    finally:
        try:
            sb.close()
        except Exception:
            pass
        server.shutdown()
    print("\nLIVE SUITE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
