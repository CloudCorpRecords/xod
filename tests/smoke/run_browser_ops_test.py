"""
browser-ops in Xod (app/vendor/browser_ops, services/browser_ops.py), against a real headless Chrome on a
throwaway profile and port. The remote path runs through a stand-in ssh/scp that executes on this machine,
so the module's own wrapper (bin/bops) and the window probe are exercised end to end.

  V1 the vendored code and tools.json are byte-identical to the module's PR head
  V2 the tool surface is tools.json's: the 8 names and descriptions verbatim
  V3 where it applies: no config, nothing; this computer without screen tools, local; with them, not local;
     a remote block, remote; last_tab_guard off is refused
  V4 provisioning: the engine deployed unchanged next to a config.json that is the device_runtime block
  V5 register (open and claim), owner bound to the calling lane, another lane's tab cannot be claimed
  V6 list shows registered tabs with owners and live titles, and unregistered tabs too
  V7 a tab screenshot is a real PNG of that tab
  V8 the sweeper closes a done tab and never an unregistered one
  V9 the window guard: a sweep that would close a window's last tab is refused whole, nothing closed;
     once the window has another tab, the same sweep runs
  V10 close-mine closes only the caller's tabs, and the window guard holds for it too
  V11 stale: a lane that stopped heartbeating loses its tab; one that heartbeats keeps it
  V12 the agent sees the tools only where they apply, and calls them through execute_tool
  V13 remote: the same calls over (stand-in) SSH through the module's own wrapper, window guard included

Run: .venv/bin/python tests/smoke/run_browser_ops_test.py [--repeat N]
"""
import asyncio, hashlib, json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_bops_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1", "XOD_SPEECH": "off",
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_SECRETS_SERVICE": "Xod test keys"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

from app.services import browser_ops as B

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# The module's PR head (CloudCorpRecords/browser-ops 6d6cadf): git blob ids of the files Xod runs or reads.
PR_BLOBS = {"src/browser_ops.py": None, "src/desktop_shot.ps1": None, "bin/bops": None, "tools.json": None, "LANES.md": None}
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def blob(path):
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Chrome:
    def __init__(self):
        self.port, self.profile = free_port(), Path(tempfile.mkdtemp(prefix="bops_chrome_"))
        self.proc = subprocess.Popen([CHROME, "--headless=new", f"--user-data-dir={self.profile}", f"--remote-debugging-port={self.port}",
                                      "--no-first-run", "--no-default-browser-check", "data:text/html,<title>home</title>home"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                self.get("/json/version")
                return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError("chrome did not start")

    def get(self, path):
        return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5).read())

    def pages(self):
        return [t for t in self.get("/json/list") if t["type"] == "page"]

    def ids(self):
        return {t["id"] for t in self.pages()}

    def cdp(self, method, params):
        import websocket
        ws = websocket.create_connection(self.get("/json/version")["webSocketDebuggerUrl"], timeout=10)
        try:
            ws.send(json.dumps({"id": 1, "method": method, "params": params}))
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == 1:
                    return m
        finally:
            ws.close()

    def new_window(self, url):
        return self.cdp("Target.createTarget", {"url": url, "newWindow": True})["result"]["targetId"]

    def new_tab_in(self, target_in_window, url):
        # a tab in the same window: created from that window's context by opening without newWindow while it is focused
        self.cdp("Target.activateTarget", {"targetId": target_in_window})
        return self.cdp("Target.createTarget", {"url": url})["result"]["targetId"]

    def settle(self, gone, timeout=5.0):
        """The page list once the tabs just closed have left it (Chrome drops them a moment after the close)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            ids = self.ids()
            if not (set(gone) & ids):
                return ids
            time.sleep(0.1)
        return self.ids()

    def window_of(self, tid):
        return self.cdp("Browser.getWindowForTarget", {"targetId": tid})["result"]["windowId"]

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


def write_config(chrome, stale_minutes=45, remote=None, guard=True):
    cfg = {"device_runtime": {"cdp_host": "127.0.0.1", "cdp_port": chrome.port, "stale_minutes": stale_minutes,
                              "last_tab_guard": guard}}
    if remote:
        cfg["remote"] = remote
    B.config_path().parent.mkdir(parents=True, exist_ok=True)
    B.config_path().write_text(json.dumps(cfg))


def fake_ssh_dir():
    """ssh and scp stand-ins that run on this machine: the device's backslash paths become slashes."""
    d = TMP / "fakebin"
    d.mkdir(exist_ok=True)
    (d / "ssh").write_text(f"""#!{sys.executable}
import subprocess, sys
a, i, rest = sys.argv[1:], 0, []
while i < len(a):
    if a[i] in ("-i", "-o", "-p", "-F", "-l"):
        i += 2
        continue
    rest = a[i + 1:]
    break
cmd = " ".join(rest).replace("\\\\", "/")
sys.exit(subprocess.run(["/bin/sh", "-c", cmd], stdin=sys.stdin).returncode)
""")
    (d / "scp").write_text(f"""#!{sys.executable}
import shutil, sys
src, dst = sys.argv[-2], sys.argv[-1]
shutil.copyfile(src.split(":", 1)[1], dst)
""")
    for f in ("ssh", "scp"):
        os.chmod(d / f, 0o755)
    return d


def lanes_tabs(chrome):
    return B.call_sync("tabs_list", {}, "task:any")


def main(repeat):
    for n in range(repeat):
        print(f"=== run {n + 1}/{repeat}")
        shutil.rmtree(B.home(), ignore_errors=True)

        print("V1 vendored unchanged")
        src = TMP / "pr"
        if not src.exists():
            subprocess.run(["git", "init", "-q", str(src)], check=True)
            r = subprocess.run(["git", "-C", str(src), "fetch", "-q", "--depth", "1", "https://github.com/CloudCorpRecords/browser-ops.git",
                                "6d6cadffc1e6c3383cf65097627d608dcc6f2e82"], capture_output=True, text=True)
            subprocess.run(["git", "-C", str(src), "checkout", "-q", "FETCH_HEAD"], capture_output=True)
        if (src / "src" / "browser_ops.py").exists():
            same = [f for f in PR_BLOBS if blob(src / f) == blob(B.MODULE_DIR / f)]
            check("code, tools.json and LANES.md match the PR head byte for byte", len(same) == len(PR_BLOBS),
                  str(set(PR_BLOBS) - set(same)))
        else:
            check("the PR head could be fetched to compare (network)", False, "fetch failed")

        print("V2 the tool surface is tools.json's")
        surf = B.surface()
        defs = {t["function"]["name"]: t["function"] for t in B.tool_definitions(["local"])}
        check("the 8 tools, names verbatim", list(defs) == [t["name"] for t in surf["tools"]] and len(defs) == 8, str(list(defs)))
        check("descriptions verbatim", all(defs[t["name"]]["description"] == t["description"] for t in surf["tools"]))
        check("no top-level anyOf reaches the model; owner is not asked for",
              all("anyOf" not in f["parameters"] and "owner" not in f["parameters"].get("required", []) for f in defs.values()))
        two = {t["function"]["name"]: t["function"] for t in B.tool_definitions(["local", "remote"])}
        check("with two devices, a device argument", two["tabs_list"]["parameters"]["properties"]["device"]["enum"] == ["local", "remote"])

        print("V14 shared-browser requests are routed to these tools")
        cases = [("Open https://example.com in a new tab of the shared browser, registered as yours", True),
                 ("Take a screenshot of that example.com tab and tell me what the page says.", True),
                 ("which tabs are open and who owns them?", True), ("run the sweeper", True), ("close my tabs", True),
                 ("open example.com and sign me up for the newsletter", False), ("search the web for the best tabs app", False),
                 ("go to github.com/owner/repo and star it", False)]
        bad = [q for q, want in cases if B.addressed(q) != want]
        check("shared-browser requests recognized, web errands not", not bad, str(bad))
        from app.agent.researcher import AgenticResearcher
        r = AgenticResearcher.__new__(AgenticResearcher)
        B.config_path().parent.mkdir(parents=True, exist_ok=True)
        B.config_path().write_text(json.dumps({"device_runtime": {"cdp_port": 9}}))
        B._native.update(at=time.time(), value=False)
        check("no sandbox mission for a shared-browser request", r._mission_request(cases[0][0], {}) is None)
        planned = {"plan_steps": [{"tools": ["browser_run_mission"], "tool_args": [{"goal": "x"}]}]}
        check("not even when the planner picked one", r._mission_request(cases[0][0], planned) is None)
        check("a web errand still runs as a mission", r._mission_request("open example.com and sign me up for the newsletter", planned) is not None)
        check("an earlier step's target_id fills a later step's placeholder", "target_id" in AgenticResearcher._CARRY_KEYS)
        B.config_path().unlink()

        chrome = Chrome()
        try:
            print("V3 where it applies")
            B._native.update(at=time.time(), value=False)
            check("no config: nowhere", B.devices() == [])
            write_config(chrome)
            check("no screen tools here + device_runtime: local", B.devices() == ["local"])
            B._native.update(at=time.time(), value=True)
            check("this computer has its own screen tools: not local", B.devices() == [])
            B._native.update(at=time.time(), value=False)
            write_config(chrome, guard=False)
            r = B.call_sync("tabs_list", {}, "task:x")
            check("last_tab_guard off is refused", not r["success"] and "last_tab_guard" in r["error"], str(r))
            write_config(chrome)

            print("V4 provisioning")
            d = B.provision_local()
            check("the engine is deployed unchanged", blob(d / "browser_ops.py") == blob(B.MODULE_DIR / "src" / "browser_ops.py"))
            check("its config.json is the device_runtime block", json.loads((d / "config.json").read_text()) == json.loads(B.config_path().read_text())["device_runtime"])

            print("V5 register, owner bound to the lane")
            a = B.call_sync("tabs_register", {"url": "data:text/html,<title>lane A page</title>A", "task": "read A", "owner": "someone-else"}, "task:aaa")
            check("open mode registers to the calling lane", a["success"] and a["owner"] == "xod-task-aaa", str(a))
            check("a different owner in the call is not used, and it says so", "owner_note" in a)
            home_tab = next(t["id"] for t in chrome.pages() if t["title"] == "home")
            b = B.call_sync("tabs_register", {"target_id": home_tab, "task": "use home"}, "task:bbb")
            check("claim mode registers an open tab", b["success"] and b["owner"] == "xod-task-bbb", str(b))
            c = B.call_sync("tabs_register", {"target_id": home_tab, "task": "steal"}, "task:aaa")
            check("another lane's tab cannot be claimed", not c["success"] and "already registered" in c.get("error", ""), str(c))
            e = B.call_sync("tabs_register", {"url": "data:,x", "target_id": home_tab, "task": "both"}, "task:aaa")
            check("url and target_id together are refused (the anyOf)", not e["success"], str(e))
            # an unregistered tab, in A's window
            stray = chrome.new_tab_in(a["target_id"], "data:text/html,<title>stray</title>s")

            print("V6 list")
            l = B.call_sync("tabs_list", {}, "task:any")
            reg = {r["target_id"]: r for r in l["registered"]}
            check("registered tabs with owners and live titles", reg[a["target_id"]]["owner"] == "xod-task-aaa"
                  and reg[a["target_id"]]["live_title"] == "lane A page", json.dumps(l)[:300])
            check("unregistered tabs listed too", any(u["target_id"] == stray for u in l["unregistered_pages"]))

            print("V7 tab screenshot")
            s = B.call_sync("screen_capture_tab", {"target_id": a["target_id"]}, "task:aaa")
            png = Path(s.get("path") or "/nonexistent")
            check("a real PNG of that tab", s["success"] and png.exists() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
                  and s["bytes"] > 500 and s["title"] == "lane A page", str(s)[:300])

            print("V8 sweep: done tabs, never unregistered ones")
            B.call_sync("tabs_mark_done", {"target_id": a["target_id"]}, "task:aaa")
            same_window = chrome.window_of(a["target_id"]) == chrome.window_of(stray)
            sw = B.call_sync("tabs_sweep_now", {}, "task:any")
            ids = chrome.settle([a["target_id"]])
            check("the done tab is closed", sw["success"] and a["target_id"] not in ids and [x["target_id"] for x in sw["closed"]] == [a["target_id"]], str(sw)[:300])
            check("the unregistered tab is untouched", stray in ids and same_window)
            check("the other lane's fresh tab is kept", home_tab in ids)

            print("V9 the window guard")
            lone = chrome.new_window("data:text/html,<title>lone</title>l")
            B.call_sync("tabs_register", {"target_id": lone, "task": "alone in its window"}, "task:ccc")
            B.call_sync("tabs_mark_done", {}, "task:ccc")
            before = chrome.ids()
            sw = B.call_sync("tabs_sweep_now", {}, "task:any")
            check("a sweep that would empty a window is refused, saying why", not sw["success"] and sw.get("refused")
                  and "last tab in a window" in sw["error"], str(sw)[:300])
            check("and nothing was closed", chrome.ids() == before)
            mate = chrome.new_tab_in(lone, "data:text/html,<title>mate</title>m")
            if chrome.window_of(mate) == chrome.window_of(lone):
                sw = B.call_sync("tabs_sweep_now", {}, "task:any")
                ids = chrome.settle([lone])
                check("with another tab in that window, the same sweep runs", sw["success"] and lone not in ids
                      and mate in ids, str(sw)[:300])
            else:
                check("a second tab could be put in that window (test setup)", False, "opened in another window")

            print("V10 close-mine")
            m1 = B.call_sync("tabs_register", {"url": "data:text/html,<title>d1</title>1", "task": "d1"}, "task:ddd")
            other = B.call_sync("tabs_register", {"url": "data:text/html,<title>e1</title>1", "task": "e1"}, "task:eee")
            cm = B.call_sync("tabs_close_mine", {}, "task:ddd")
            ids = chrome.settle([m1["target_id"]])
            check("only the caller's tabs close", cm["success"] and m1["target_id"] not in ids and other["target_id"] in ids
                  and home_tab in ids and stray in ids, str(cm)[:300])
            lone2 = chrome.new_window("data:text/html,<title>lone2</title>l")
            B.call_sync("tabs_register", {"target_id": lone2, "task": "alone"}, "task:fff")
            cm = B.call_sync("tabs_close_mine", {}, "task:fff")
            check("close-mine that would empty a window is refused, nothing closed", not cm["success"] and lone2 in chrome.ids(), str(cm)[:300])

            print("V11 stale tabs")
            # 6 s window; the module stamps heartbeats to the second, and the sweep's own window check takes a
            # moment, so the margins are seconds wide: g's heartbeat is ~3 s old at the sweep, h's ~8 s
            write_config(chrome, stale_minutes=0.1)
            g = B.call_sync("tabs_register", {"url": "data:text/html,<title>g</title>g", "task": "g"}, "task:ggg")
            h = B.call_sync("tabs_register", {"url": "data:text/html,<title>h</title>h", "task": "h"}, "task:hhh")
            time.sleep(5.0)
            B.call_sync("tabs_heartbeat", {}, "task:ggg")
            time.sleep(2.5)
            sw = B.call_sync("tabs_sweep_now", {}, "task:any")
            ids = chrome.settle([x["target_id"] for x in sw.get("closed", [])])
            closed = {x["target_id"]: x["reason"] for x in sw.get("closed", [])}
            check("the lane that heartbeats keeps its tab", g["target_id"] in ids, str(sw)[:400])
            check("the silent lane's tab is closed as stale", closed.get(h["target_id"]) == "stale" and h["target_id"] not in ids, str(sw)[:400])
            check("unregistered tabs still untouched", stray in ids)
            write_config(chrome)

            print("V12 offered where it applies, called through execute_tool")
            from app.agent import tools
            from app.services import activity
            names = {t["function"]["name"] for t in tools.select_tools_for_query("list the tabs in the browser")}
            check("offered for a browser request when this computer has no screen tools", set(B.TOOL_NAMES) <= names)
            B._native.update(at=time.time(), value=True)
            names = {t["function"]["name"] for t in tools.select_tools_for_query("list the tabs in the browser")}
            check("not offered when this computer has its own screen tools (and no remote)", not (set(B.TOOL_NAMES) & names))
            B._native.update(at=time.time(), value=False)
            tok = activity.origin.set("task:zzz")
            try:
                r = asyncio.run(tools.execute_tool("tabs_register", {"url": "data:text/html,<title>z</title>z", "task": "via executor"}, reviewed=True))
            finally:
                activity.origin.reset(tok)
            check("execute_tool runs it, owned by the calling task", r.get("success") and r.get("owner") == "xod-task-zzz", str(r)[:300])

            print("V15 readable results, screenshots by name")
            l = B.call_sync("tabs_list", {}, "task:any")
            text = B.summary("tabs_list", {**l, "success": True})
            check("the list reads in words, unregistered tabs included", "owned by nobody" in text and "stray" in text
                  and "xod-task-ggg" in text, text[:400])
            q1 = B.call_sync("tabs_register", {"url": "data:text/html,<title>Quarterly Numbers</title>q", "task": "q"}, "task:qqq")
            s1 = asyncio.run(B.call("screen_capture_tab", {"target_id": "the quarterly numbers tab"}, "task:any"))
            check("named by its title, and it says which tab", s1.get("success") and s1.get("target_id") == q1["target_id"]
                  and "resolved" in s1, str(s1)[:300])
            from app.agent import tools as _tools
            from app.services import activity as _activity
            _tok = _activity.origin.set("task:qqq")
            try:   # through the executor, as the agent calls it (its placeholder guard runs first)
                s2 = asyncio.run(_tools.execute_tool("screen_capture_tab", {"target_id": "<target_id from step 1>"}, reviewed=True))
            finally:
                _activity.origin.reset(_tok)
            check("a plan's placeholder: the lane's one tab", s2.get("success") and s2.get("target_id") == q1["target_id"], str(s2)[:300])
            s3 = asyncio.run(B.call("screen_capture_tab", {"target_id": "<target_id from step 1>"}, "task:nobody"))
            check("no single match: the tabs come back, nothing is guessed", not s3.get("success") and s3.get("tabs"), str(s3)[:300])
            check("the screenshot result says what it shows, or that nothing can read it", s1.get("screen"))
            q2 = B.call_sync("tabs_register", {"url": "data:text/html,<title>Quarterly Numbers</title>q2", "task": "q2"}, "task:qq2")
            s4 = asyncio.run(B.call("screen_capture_tab", {"target_id": "my quarterly numbers tab"}, "task:qq2"))
            check("two tabs match: the caller's own one", s4.get("success") and s4.get("target_id") == q2["target_id"], str(s4)[:300])
            s5 = asyncio.run(B.call("screen_capture_tab", {"target_id": "the quarterly numbers tab"}, "task:any"))
            check("two match and neither is the caller's: the tabs come back", not s5.get("success") and s5.get("tabs"), str(s5)[:300])
            m = B.call_sync("tabs_mark_done", {"target_id": "the quarterly numbers tab"}, "task:qqq")
            check("a change never resolves names (mark done by a title marks nothing)", m.get("marked_done") == 0, str(m)[:300])

            print("V16 the scheduled sweep")
            t1 = B.call_sync("tabs_register", {"url": "data:text/html,<title>sched</title>s", "task": "s"}, "task:sss")
            B.call_sync("tabs_mark_done", {}, "task:sss")
            res = B.sweep_all()
            check("one pass on this computer closes done tabs", len(res) == 1 and res[0].get("success")
                  and t1["target_id"] in {c["target_id"] for c in res[0].get("closed", [])}, str(res)[:300])
            lone4 = chrome.new_window("data:text/html,<title>lone4</title>l")
            B.call_sync("tabs_register", {"target_id": lone4, "task": "alone"}, "task:sss")
            B.call_sync("tabs_mark_done", {}, "task:sss")
            res = B.sweep_all()
            check("and is held by the window guard like any sweep", res and not res[0].get("success") and lone4 in chrome.ids(), str(res)[:300])
            chrome.new_tab_in(lone4, "data:text/html,<title>m4</title>m")
            B.sweep_all()

            print("V17 screenshots read in words")
            from app.services import vision as _vision
            from app.services.llm import llm_service as _llm
            real = (_vision.model, _vision.describe, _llm.get_endpoint_info, _llm.chat_completion)
            png = str(s1.get("path"))
            async def _vm(): return "moondream:latest"
            async def _fail(*a, **k): return None
            async def _main(messages, **k):
                assert messages[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
                return {"content": "A page titled Quarterly Numbers."}
            try:
                _vision.model, _vision.describe = _vm, _fail
                _llm.get_endpoint_info = lambda: {"provider": "openai", "model": "x"}
                _llm.chat_completion = _main
                t = asyncio.run(B.read_screenshot(png, "what it shows"))
                check("local vision fails: the cloud main model reads it", t == "A page titled Quarterly Numbers.", t)
                _llm.get_endpoint_info = lambda: {"provider": "ollama", "model": "x"}
                t = asyncio.run(B.read_screenshot(png, "what it shows"))
                check("neither can: it says so, naming the vision model", "moondream" in t and "could not read" in t, t)
            finally:
                _vision.model, _vision.describe, _llm.get_endpoint_info, _llm.chat_completion = real

            print("V13 remote, over (stand-in) SSH through the module's wrapper")
            dev = TMP / f"device-{n}"
            dev.mkdir()
            shutil.copyfile(B.MODULE_DIR / "src" / "browser_ops.py", dev / "browser_ops.py")
            (dev / "config.json").write_text(json.dumps({"cdp_host": "127.0.0.1", "cdp_port": chrome.port, "stale_minutes": 45}))
            os.environ["PATH"] = f"{fake_ssh_dir()}:{os.environ['PATH']}"
            write_config(chrome, remote={"device": "test-device", "device_dir": str(dev).replace("/", "\\"), "python": sys.executable,
                                         "ssh_options": ["-o", "ConnectTimeout=5"], "ssh_user_host": "tester@device"})
            B._native.update(at=time.time(), value=True)        # this computer sees for itself: only remote is offered
            check("only remote is offered", B.devices() == ["remote"])
            r = B.call_sync("tabs_register", {"url": "data:text/html,<title>remote r</title>r", "task": "remote"}, "task:rrr")
            check("register on the device", r.get("success") and r.get("device") == "remote" and r.get("owner") == "xod-task-rrr", str(r)[:300])
            s = B.call_sync("screen_capture_tab", {"target_id": r.get("target_id", "")}, "task:rrr")
            lp = Path(s.get("local_path") or "/nonexistent")
            check("its screenshot comes back to this computer (the wrapper's local_path)", s.get("success") and lp.exists()
                  and lp.read_bytes()[:4] == b"\x89PNG" and lp.is_relative_to(B.home()), str(s)[:300])
            lone3 = chrome.new_window("data:text/html,<title>lone3</title>l")
            B.call_sync("tabs_register", {"target_id": lone3, "task": "alone"}, "task:rrr")
            cm = B.call_sync("tabs_close_mine", {}, "task:rrr")
            check("the window guard holds on the device", not cm["success"] and "last tab in a window" in cm.get("error", "")
                  and lone3 in chrome.ids(), str(cm)[:300])
            B.call_sync("tabs_mark_done", {"target_id": lone3}, "task:rrr")
            chrome.new_tab_in(lone3, "data:text/html,<title>m3</title>m")
            cm = B.call_sync("tabs_close_mine", {}, "task:rrr")
            ids = chrome.settle([lone3, r["target_id"]])
            check("and once it is safe, close-mine runs there", cm.get("success") and lone3 not in ids
                  and r["target_id"] not in ids, str(cm)[:300])
        finally:
            chrome.stop()
            B._native.update(at=0.0, value=False)
            os.environ["PATH"] = os.environ["PATH"].split(":", 1)[1] if "fakebin" in os.environ["PATH"].split(":", 1)[0] else os.environ["PATH"]

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main(int(sys.argv[sys.argv.index("--repeat") + 1]) if "--repeat" in sys.argv else 1)
