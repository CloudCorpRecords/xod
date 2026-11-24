"""
browser mission, live local site (AGE-100): the real mission engine and the
real sandbox browser (headless Chrome) on a local test site, driven by a
scripted brain that reads element numbers out of the mission prompt. Proves
the AGE-100 wiring end to end on a live page: the mission holds the tab
claim, typed and checked steps pass only on DOM read-back, and the claim is
released when the mission ends.

The brain: call 1 types into the search box, call 2 ticks the checkbox,
call 3 clicks the results link, call 4 finishes once the results heading is
on the page. No model, no network beyond 127.0.0.1, no accounts.

NOTE: never redirect HOME for a process that launches Chrome (round 2
defect B): Chrome starts but Page.navigate never commits. Isolation comes
from XOD_DATA_DIR and the file keyring.

Run: python tests/smoke/run_browser_mission_live_test.py
"""
import asyncio, json, os, re, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_mission_live_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1", "XOD_SPEECH": "off",
                   "XDG_CONFIG_HOME": str(TMP / "config"),
                   "XDG_DATA_HOME": str(TMP / "share"),
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_SECRETS_SERVICE": "Xod test keys",
                   "XOD_MISSION_VISION": "never"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

from app import paths
paths.DEV_ENV_PATH = TMP / "absent.env"

HOME_PAGE = """<!doctype html><html><head><title>Mission Home</title></head><body>
<h1>Mission Home</h1>
<form>
<label>Search <input id="q" type="search" name="q"></label>
<label>Agree <input id="agree" type="checkbox" name="agree"></label>
</form>
<a id="go" href="/results">See results</a>
</body></html>"""

RESULTS_PAGE = """<!doctype html><html><head><title>Results</title></head><body>
<h1>Results Page</h1>
<p>Three results found for your search.</p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = RESULTS_PAGE if self.path.startswith("/results") else HOME_PAGE
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))


def find_index(text, pattern):
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    from app.services.browser_sandbox import BrowserSandboxService
    from app.services import llm as llm_mod
    import app.services.vision as vision_mod

    async def fake_main_sees():
        return True
    vision_mod.main_sees = fake_main_sees

    state = {"calls": 0}

    async def brain(messages=None, **kwargs):
        state["calls"] += 1
        text = "\n".join(str(m.get("content", "")) for m in (messages or []))
        n = state["calls"]
        if n == 1:
            idx = find_index(text, r"\[(\d+)\] input:search")
            plan = {"action": "type", "index": idx, "text": "hello world",
                    "reasoning": "type the search"}
        elif n == 2:
            idx = find_index(text, r"\[(\d+)\] input:checkbox")
            plan = {"action": "check", "index": idx, "reasoning": "tick agree"}
        elif n == 3:
            idx = find_index(text, r"\[(\d+)\] a \"See results\"")
            plan = {"action": "click", "index": idx, "reasoning": "open results"}
        elif "Results Page" in text:
            plan = {"action": "finish", "answer": "Results Page reached; three results found."}
        else:
            plan = {"action": "finish", "answer": "Brain ran out of script."}
        return {"content": json.dumps(plan), "_provider": "fake", "_example_id": ""}
    llm_mod.llm_service.chat_completion = brain

    sb = BrowserSandboxService()
    try:
        rec = await asyncio.wait_for(
            sb.run_browser_mission(
                "Search for hello world, tick Agree, then open See results and report the heading.",
                start_url=f"{base}/home"),
            timeout=240)
    finally:
        try:
            await sb.stop_browser()
        except Exception:
            pass
        server.shutdown()

    steps = rec.get("steps", [])
    by_action = {}
    for s in steps:
        by_action.setdefault(s["action"], []).append(s)
    check("L1 mission completed", rec["status"] == "completed", rec["status"])
    check("L2 claim recorded on the mission", bool(rec.get("claim", {}).get("success")), str(rec.get("claim")))
    t = (by_action.get("type") or [{}])[0]
    check("L3 typed step passed on DOM read-back",
          t.get("status") == "success" and t.get("verified") == "read-back", str(t))
    c = (by_action.get("check") or [{}])[0]
    check("L4 check step passed on DOM read-back",
          c.get("status") == "success" and c.get("verified") == "read-back", str(c))
    k = (by_action.get("click") or [{}])[0]
    check("L5 click step recorded", k.get("status") == "success", str(k))
    check("L6 mission ended on the results page",
          str(rec.get("final_answer", "")).find("Results Page") >= 0 or "/results" in str(steps[-1].get("url", "")),
          str(rec.get("final_answer"))[:120])
    check("L7 claim released at the end", sb.claimed_tab_id is None, f"claimed={sb.claimed_tab_id}")
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
