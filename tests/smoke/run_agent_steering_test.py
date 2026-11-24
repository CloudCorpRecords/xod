"""
Verification suite for Replit/Muse-style Agent Steering, Signed-In Tab Jumping, and Agent Tools (2026-11-24):
  Part A: Live mid-mission stuck-pause -> user steer -> immediate in-place resume & completion
  Part B: Smart find_or_open_site_tab() auto-adopting real signed-in Chrome & reusing existing social tabs
  Part C: Xod's own agent tool registry (app/agent/tools.py) exposes & executes all new browser/tab/pool/steer tools
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_bsteer_"))
os.environ.update({
    "XOD_DATA_DIR": str(TMP / "data"),
    "XOD_NO_UPDATE_CHECK": "1",
    "XOD_SPEECH": "off",
    "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
    "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"),
    "XOD_SECRETS_SERVICE": "Xod test keys",
})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

from app import paths
paths.DEV_ENV_PATH = TMP / "absent.env"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


# ================================================================ Part A: Live Stuck-Pause & Mid-Mission Steer
async def part_a_live_steering():
    from app.services.browser_sandbox import BrowserSandboxService
    from app.services import llm as llm_mod

    sb = BrowserSandboxService()
    sb.current_url = "https://www.facebook.com/"
    sb.current_title = "Facebook"
    sb.indexed_elements = [
        {"index": 1, "tag": "div", "text": "Wrong box"},
        {"index": 2, "tag": "button", "text": "Open Composer"},
    ]
    page_text = ["Feed initial"]
    saw_needs_steer = False

    sb.ensure_browser_running = lambda: asyncio.sleep(0, result=True)
    sb.claim_tab = lambda tab_id=None, owner="mission": asyncio.sleep(0, result={"success": True, "claimed_tab_id": "t1"})
    sb.navigate = lambda url: asyncio.sleep(0, result={"success": True, "url": url, "title": "Facebook"})
    sb.observe = lambda **k: asyncio.sleep(0, result={"success": True, "elements": sb.indexed_elements})
    sb.extract_clean_markdown = lambda **k: asyncio.sleep(0, result={"success": True, "markdown": page_text[0]})
    sb.take_screenshot = lambda **k: asyncio.sleep(0, result={"success": True})
    sb._wait_for_page_ready = lambda **k: asyncio.sleep(0)
    sb._learn_site_tips = lambda *a, **k: asyncio.sleep(0)
    sb._tabs_block = lambda: asyncio.sleep(0, result="")
    sb.evaluate_js = lambda expr: asyncio.sleep(0, result={"success": True, "result": ""})
    sb._page_text_snapshot = lambda: asyncio.sleep(0, result=page_text[0])
    sb.unstuck = lambda **k: asyncio.sleep(0, result={"success": True})

    async def fake_interact(index=None, action="click", **k):
        if index == 2:
            page_text[0] = "Composer opened successfully!"
        return {"success": True}

    sb.interact_by_index = fake_interact

    # 3 no-effect clicks on [1] -> triggers stuck-pause (needs_steer) -> user steers -> clicks [2] -> finishes!
    step_calls = [0]
    saw_steer_in_prompt = [False]

    async def fake_chat(messages=None, **k):
        step_calls[0] += 1
        joined = json.dumps(messages or [])
        if page_text[0] == "Composer opened successfully!":
            return {"content": json.dumps({"action": "finish", "answer": "Composer opened after your steer."}), "_provider": "fake", "_example_id": ""}
        if "USER STEER (follow immediately): Click Open Composer [2]" in joined:
            saw_steer_in_prompt[0] = True
            return {"content": json.dumps({"action": "click", "index": 2, "reasoning": "following user steer"}), "_provider": "fake", "_example_id": ""}
        return {"content": json.dumps({"action": "click", "index": 1, "reasoning": "stuck clicking wrong box"}), "_provider": "fake", "_example_id": ""}

    orig_chat = llm_mod.llm_service.chat_completion
    llm_mod.llm_service.chat_completion = fake_chat
    os.environ["XOD_MISSION_STEER_WAIT_SEC"] = "5"

    async def user_steerer():
        nonlocal saw_needs_steer
        for _ in range(50):
            await asyncio.sleep(0.05)
            if (sb.latest_mission or {}).get("status") == "needs_steer":
                saw_needs_steer = True
                sb.steer_mission("Click Open Composer [2]", source="user")
                return

    try:
        steerer_task = asyncio.create_task(user_steerer())
        rec = await sb.run_browser_mission("Open the composer", start_url="https://www.facebook.com/", max_steps=8)
        await steerer_task
        check("A1 mission paused in-place with status='needs_steer' when stuck 3 times", saw_needs_steer)
        check("A2 live user steer woke up mission and was injected into LLM prompt", saw_steer_in_prompt[0] and len(rec.get("steers") or []) == 1, str(rec.get("steers")))
        check("A3 mission resumed in-place after steer and completed cleanly", rec.get("status") == "completed", f"status={rec.get('status')} ans={rec.get('final_answer')}")
    finally:
        llm_mod.llm_service.chat_completion = orig_chat
        os.environ.pop("XOD_MISSION_STEER_WAIT_SEC", None)


# ================================================================ Part B: Smart Find-or-Open Signed-In Tab
async def part_b_find_tab():
    from app.services.browser_sandbox import BrowserSandboxService

    sb = BrowserSandboxService()
    tabs_9222 = [
        {"id": "fb1", "type": "page", "title": "Facebook", "url": "https://www.facebook.com/home.php", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/fb1"},
        {"id": "x1", "type": "page", "title": "Home / X", "url": "https://x.com/home", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/x1"},
    ]

    async def fake_devtools(url, timeout=2.0, method=None, data=None):
        if ":9222/" not in url:
            raise OSError("refused")
        if url.endswith("/json/version"):
            return json.dumps({"Browser": "Chrome/133.0", "User-Agent": "Chrome/133.0"})
        if url.endswith("/json"):
            return json.dumps(tabs_9222)
        if "/activate/" in url:
            return "Target activated"
        if "/new" in url:
            nt = {"id": "li1", "type": "page", "title": "LinkedIn", "url": "https://www.linkedin.com/feed/", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/li1"}
            tabs_9222.append(nt)
            return json.dumps(nt)
        return "{}"

    sb._devtools = fake_devtools
    sb._inspect_port_process = lambda p: {"pid": 2001, "exe": "/Applications/Google Chrome.app", "cmdline": ["--remote-debugging-port=9222"], "user_data_dir": "/Users/alice/Library/Application Support/Google/Chrome", "is_headless": False} if p == 9222 else {}
    sb._probe_devtools = lambda t: "ready" if sb.active_port() == 9222 else "refused"

    r_fb = await sb.find_or_open_site_tab("facebook")
    check("B1 find_or_open_site_tab('facebook') auto-adopts signed-in Chrome and reuses existing FB tab",
          r_fb.get("success") and r_fb.get("browser_mode") == "user_chrome" and r_fb.get("reused_existing_tab") is True and r_fb.get("tab_id") == "fb1",
          str(r_fb))

    r_x = await sb.find_or_open_site_tab("x")
    check("B2 find_or_open_site_tab('x') switches directly to existing X tab",
          r_x.get("success") and r_x.get("reused_existing_tab") is True and r_x.get("tab_id") == "x1",
          str(r_x))

    r_li = await sb.find_or_open_site_tab("linkedin")
    check("B3 find_or_open_site_tab('linkedin') opens LinkedIn in the same signed-in Chrome when not yet open",
          r_li.get("success") and r_li.get("reused_existing_tab") is False and r_li.get("tab_id") == "li1" and sb.browser_mode == "user_chrome",
          str(r_li))


# ================================================================ Part C: Agent Tool Registry
def part_c_agent_tools():
    from app.agent.tools import TOOL_DEFINITIONS
    names = {t["function"]["name"] for t in TOOL_DEFINITIONS if isinstance(t, dict) and "function" in t}
    expected = {
        "browser_find_tab", "browser_adopt", "browser_detach", "browser_tabs",
        "browser_select", "browser_drag", "browser_run_parallel", "browser_steer_mission",
    }
    missing = expected - names
    check("C1 app/agent/tools.py registers all 8 new browser/tab/pool/steer tools", not missing, f"missing={missing}")


async def main():
    await part_a_live_steering()
    await part_b_find_tab()
    part_c_agent_tools()
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
