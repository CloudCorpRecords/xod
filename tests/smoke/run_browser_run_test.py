"""
browser_run (AGE-99): the deterministic long-form runner and the sandbox tab claim.

Part A runs the REAL BrowserSandboxService claim code with its DevTools transport
faked: claim pins a tab, primitives refuse to adopt another tab when the claimed
one vanishes, explicit tab actions move or clear the claim and say so.

Part B runs the runner against a scripted fake sandbox: a late-landing step is
recovered by the settle re-check, a missed click by the alternate indexed
strategy, a drifted active tab by re-pinning, and the fake's call log proves no
identical call was ever repeated back to back.

Run: python tests/smoke/run_browser_run_test.py
"""
import asyncio, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_brun_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1", "XOD_SPEECH": "off",
                   "HOME": str(TMP), "XDG_CONFIG_HOME": str(TMP / "config"),
                   "XDG_DATA_HOME": str(TMP / "share"),
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_SECRETS_SERVICE": "Xod test keys"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

from app import paths
paths.DEV_ENV_PATH = TMP / "absent.env"

results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))


# ---------------------------------------------------------------- Part A: claim semantics
from app.services.browser_sandbox import BrowserSandboxService


def make_service(tab_ids=("t1", "t2", "t3")):
    sb = BrowserSandboxService()
    tabs = [{"id": t, "type": "page", "title": f"Tab {t}", "url": f"http://x/{t}",
             "webSocketDebuggerUrl": f"ws://x/{t}"} for t in tab_ids]

    async def fake_devtools(url, timeout=2.0, method=None, data=None):
        if url.endswith("/json"):
            return json.dumps(tabs)
        if "/new" in url:
            nt = {"id": "tN", "type": "page", "title": "New", "url": "about:blank",
                  "webSocketDebuggerUrl": "ws://x/tN"}
            tabs.append(nt)
            return json.dumps(nt)
        if "/activate/" in url:
            return "Target activated"
        if "/close/" in url:
            cid = url.rsplit("/", 1)[-1]
            tabs[:] = [t for t in tabs if t["id"] != cid]
            return "Target closed"
        raise RuntimeError(f"unexpected devtools url {url}")

    async def no_launch():
        return True

    sb._devtools = fake_devtools
    sb.ensure_browser_running = no_launch
    return sb, tabs


async def part_a():
    sb, tabs = make_service()
    r = await sb.claim_tab(tab_id="t2", owner="unit")
    check("A1 claim pins the requested tab", r.get("success") and sb.claimed_tab_id == "t2", str(r))
    sb.active_tab_id = "t1"  # something else moved the active pointer
    ws = await sb._get_active_tab_ws_url()
    check("A2 primitives follow the claim, not the active pointer", ws == "ws://x/t2", ws)
    tabs[:] = [t for t in tabs if t["id"] != "t2"]  # the claimed tab is closed behind the run
    try:
        await sb._get_active_tab_ws_url()
        check("A3 vanished claim raises instead of adopting another tab", False, "no raise")
    except RuntimeError as e:
        check("A3 vanished claim raises instead of adopting another tab", "Claimed tab" in str(e), str(e))
    r = await sb.manage_tabs("switch", target_id="t3")
    check("A4 explicit switch moves the claim and says so",
          r.get("claim_moved") is True and sb.claimed_tab_id == "t3", str(r))
    r = await sb.manage_tabs("close", target_id="t3")
    check("A5 closing the claimed tab clears the claim and says so",
          r.get("claim_cleared") is True and sb.claimed_tab_id is None, str(r))
    await sb.claim_tab(tab_id="t1", owner="unit")
    sb.release_tab_claim()
    check("A6 release clears the claim", sb.claimed_tab_id is None and sb.claim_owner is None)
    r = await sb.claim_tab(tab_id="nope", owner="unit")
    check("A7 claiming a missing tab fails cleanly", r.get("success") is False and sb.claimed_tab_id is None, str(r))


# ---------------------------------------------------------------- Part B: the runner
from app.services import browser_run as BR


class FakeRun:
    """A scripted sandbox: two tabs, three traps (late landing, alternate-only
    click, silent type), and a call log that proves the recovery shape."""

    def __init__(self):
        self.tabs = [
            {"id": "a", "type": "page", "title": "Alpha", "url": "http://t/alpha"},
            {"id": "b", "type": "page", "title": "Beta", "url": "http://t/beta"},
        ]
        self.active_tab_id = "a"
        self.claimed_tab_id = None
        self.claim_owner = None
        self.current_url = "http://t/alpha"
        self.current_title = "Alpha"
        self.text = {"a": "alpha body", "b": "beta body"}
        self.fields = {"a": {}, "b": {}}
        self.calls = []
        self._late_checks = 0
        self._mission_lock = None

    # -- tabs / claim ------------------------------------------------------
    async def manage_tabs(self, action="list", target_id=None, url="about:blank"):
        if action == "list":
            return {"success": True, "tabs": [dict(t) for t in self.tabs],
                    "active_tab_id": self.active_tab_id, "count": len(self.tabs)}
        if action == "switch":
            self.active_tab_id = target_id
            if self.claimed_tab_id:
                self.claimed_tab_id = target_id
            t = next(t for t in self.tabs if t["id"] == target_id)
            self.current_url, self.current_title = t["url"], t["title"]
            return {"success": True, "action": "switch"}
        if action == "new":
            nt = {"id": f"t{len(self.tabs)}", "type": "page", "title": "New", "url": url}
            self.tabs.append(nt)
            self.active_tab_id = nt["id"]
            if self.claimed_tab_id:
                self.claimed_tab_id = nt["id"]
            return {"success": True, "action": "new", "tab": nt}
        if action == "close":
            self.tabs = [t for t in self.tabs if t["id"] != target_id]
            if self.claimed_tab_id == target_id:
                self.claimed_tab_id = None
            return {"success": True, "action": "close"}
        return {"success": False, "error": "unknown"}

    async def claim_tab(self, tab_id=None, owner="run"):
        self.claimed_tab_id = tab_id or self.active_tab_id
        self.claim_owner = owner
        return {"success": True, "claimed_tab_id": self.claimed_tab_id}

    def release_tab_claim(self):
        self.claimed_tab_id = None
        return {"success": True}

    # -- page primitives ----------------------------------------------------
    async def navigate(self, url):
        self.calls.append(("navigate", url))
        t = next(t for t in self.tabs if t["id"] == self.active_tab_id)
        t["url"] = url
        self.current_url = url
        return {"success": True}

    async def click(self, target):
        self.calls.append(("click", target))
        if target == "#alt-btn":
            return {"success": False, "error": "element covered"}
        if target == "#late-btn":
            return {"success": True}  # lands, but the text shows up one check later
        return {"success": True}

    async def observe(self):
        return {"success": True, "elements": [{"index": 7, "text": "Alt Button"}]}

    async def interact_by_index(self, index=None, action="click", text=None,
                                press_enter=False, clear_first=True, selector=None, text_target=None):
        self.calls.append(("interact", action, selector or index))
        if action == "click":
            self.text[self.active_tab_id] += " alt done"
            return {"success": True}
        if action == "type":
            self.fields[self.active_tab_id][selector] = text
            return {"success": True}
        return {"success": True}

    async def type_text(self, selector, text, press_enter=False, clear_first=True):
        self.calls.append(("type_text", selector, text))
        if selector == "#sticky":
            return {"success": True}  # reports success, value never sticks
        self.fields[self.active_tab_id][selector] = text
        return {"success": True}

    async def extract_content(self):
        body = self.text[self.active_tab_id]
        if "late" not in body and self._late_pending():
            self._late_checks += 1
            if self._late_checks >= 2:
                self.text[self.active_tab_id] += " late text"
                body = self.text[self.active_tab_id]
        return {"success": True, "content": body}

    def _late_pending(self):
        return any(c == ("click", "#late-btn") for c in self.calls)

    async def evaluate_js(self, expression):
        if "querySelector" in expression and "!!" in expression:
            return {"success": True, "result": True}
        for sel, val in self.fields[self.active_tab_id].items():
            if sel in expression:
                return {"success": True, "result": val}
        return {"success": True, "result": None}

    async def scroll(self, direction="down", amount=500, to_selector=None):
        return {"success": True}

    async def press_key(self, key):
        return {"success": True}

    async def wait_for_selector(self, target, timeout_sec=8.0):
        return {"success": True}

    async def fill_form(self, fields, submit_index=None):
        for f in fields:
            self.fields[self.active_tab_id][f["selector"]] = f["value"]
        return {"success": True}


class FlakyVerify(FakeRun):
    """evaluate_js raises TimeoutError, the way a CDP read does on a busy page
    mid-navigation. fail_first=N: only the first N reads raise."""

    def __init__(self, fail_first=0, fail_always=False):
        super().__init__()
        self._eval_calls = 0
        self._fail_first = fail_first
        self._fail_always = fail_always

    async def evaluate_js(self, expression):
        self._eval_calls += 1
        if self._fail_always or self._eval_calls <= self._fail_first:
            raise TimeoutError("simulated CDP read timeout")
        return await super().evaluate_js(expression)


class SlowText(FakeRun):
    """Page text arrives late: the first N content reads are empty shells,
    the way an extract on a just-opened tab sees the page mid-load."""

    def __init__(self, empty_reads=0):
        super().__init__()
        self._reads = 0
        self._empty_reads = empty_reads

    async def extract_content(self):
        self._reads += 1
        if self._reads <= self._empty_reads:
            return {"success": True, "content": ""}
        return await super().extract_content()


async def part_b():
    sb = FakeRun()
    steps = [
        {"action": "navigate", "url": "http://t/alpha"},
        {"action": "type", "selector": "#q", "text": "hello"},
        {"action": "click", "target": "#late-btn", "expect": {"text_contains": "late text"}},
        {"action": "click", "target": "#alt-btn", "expect": {"text_contains": "alt done"}},
        {"action": "type", "selector": "#sticky", "text": "stuck"},
        {"action": "switch_tab", "tab": {"id": "b"}},
        {"action": "assert", "expect": {"text_contains": "beta body"}},
        {"action": "extract", "name": "beta_text"},
    ]
    rec = await BR.run_browser_steps(sb, steps, owner="unit")
    check("B1 run completes all 8 steps", rec["status"] == "completed",
          f"{rec['status']} landed={rec.get('steps_landed')}")
    by_action = {s["step_num"]: s for s in rec["steps"]}
    check("B2 late landing recovered by settle re-check", by_action[3]["status"] == "recovered",
          str(by_action[3]))
    check("B3 covered click recovered by the alternate strategy", by_action[4]["status"] == "recovered",
          str(by_action[4]))
    check("B4 silent type recovered and read back", by_action[5]["status"] == "recovered",
          str(by_action[5]))
    check("B5 extract captured the second tab text", "beta body" in rec["extracts"].get("beta_text", ""),
          str(rec["extracts"]))
    check("B6 claim released at the end of the run", sb.claimed_tab_id is None)
    adjacent_dupes = [i for i in range(1, len(sb.calls)) if sb.calls[i] == sb.calls[i - 1]]
    check("B7 no identical primitive call repeated back to back", not adjacent_dupes,
          f"dupes at {adjacent_dupes}: {sb.calls}")

    # a step that can never land fails the run honestly, with attempts recorded
    sb2 = FakeRun()
    rec2 = await BR.run_browser_steps(sb2, [
        {"action": "assert", "expect": {"text_contains": "never on the page"}},
    ], owner="unit")
    check("B8 an impossible expectation fails, attempts recorded",
          rec2["status"] == "incomplete" and rec2["steps"][0]["status"] == "failed"
          and len(rec2["steps"][0]["attempts"]) >= 2, str(rec2["steps"]))

    # Round 2 regression: a verification read that raises (a CDP timeout on a
    # busy page) is a miss with its cause recorded, never an exception that
    # kills the run. The live suite crashed exactly here before the fix.
    sb3 = FlakyVerify(fail_first=1)
    rec3 = await BR.run_browser_steps(sb3, [
        {"action": "type", "selector": "#q", "text": "hello"},
    ], owner="unit")
    step3 = rec3["steps"][0]
    check("B9 a timed-out verification read is a miss, recovered on re-check",
          rec3["status"] == "completed" and step3["status"] == "recovered"
          and any("verification error" in str(a.get("detail", "")) for a in step3["attempts"]),
          str(step3))

    sb4 = FlakyVerify(fail_always=True)
    rec4 = await BR.run_browser_steps(sb4, [
        {"action": "type", "selector": "#q", "text": "hello"},
    ], owner="unit")
    check("B10 verification that always times out fails the step, run record intact",
          rec4["status"] == "incomplete" and rec4["steps"][0]["status"] == "failed"
          and any("verification error" in str(a.get("detail", "")) for a in rec4["steps"][0]["attempts"]),
          str(rec4["steps"]))

    # AGE-100, found live on the public web: an extract taken while the page
    # is still loading captures an empty snapshot. It must not count as
    # landed, and the stored extract must be the settled text, not the shell.
    sb5 = SlowText(empty_reads=1)
    rec5 = await BR.run_browser_steps(sb5, [
        {"action": "extract", "name": "early"},
    ], owner="unit")
    check("B11 an extract that starts empty lands on the settled text",
          rec5["status"] == "completed" and rec5["steps"][0]["status"] in ("landed", "recovered")
          and "alpha body" in rec5["extracts"].get("early", ""),
          str(rec5["steps"]) + " extracts=" + str(rec5["extracts"]))

    sb6 = SlowText(empty_reads=99)
    rec6 = await BR.run_browser_steps(sb6, [
        {"action": "extract", "name": "never"},
    ], owner="unit")
    check("B12 an extract on a page that never renders text fails honestly",
          rec6["status"] == "incomplete" and rec6["steps"][0]["status"] == "failed"
          and any("empty" in str(a.get("detail", "")) for a in rec6["steps"][0]["attempts"]),
          str(rec6["steps"]))


async def main():
    await part_a()
    await part_b()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
