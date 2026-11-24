"""
browser missions, verified step execution (AGE-100): the mission engine holds
the tab claim, a step counts only when its landing check passes, and a missed
verifiable step is retried once through a re-resolved element.

The browser is faked at the service surface (no Chrome, no model): a scripted
fake LLM returns a fixed plan sequence, and the fake page implements the trap
this work exists for, a type that reports success while the value never
sticks. The mission's own code (guards, dispatch, landing checks, recovery,
audit records) is the real code throughout.

Scenarios:
  A happy path with a sticky field: the type misses, the read-back catches it,
    the recovery retry lands it; the claim is taken at the start and released
    at the end; audit steps carry verified/landing/recovered.
  B a field that never takes the value: the step is recorded failed with the
    landing miss named, and the mission record stays honest.
  C the configurable step ceiling (XOD_MISSION_MAX_STEPS) stops a mission
    that would otherwise scroll forever.
  D the wall-clock budget (XOD_MISSION_WALL_SEC) stops a slow mission cleanly.
  E a browser restart mid-mission (claim cleared) is repaired by re-pinning
    at the top of the next step.

Run: python tests/smoke/run_browser_mission_verify_test.py
"""
import asyncio, json, os, re, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_mission_verify_"))
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

results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))


ELEMENTS = [
    {"index": 1, "tag": "input", "type": "search", "text": "", "label": "Search"},
    {"index": 2, "tag": "input", "type": "text", "text": "", "label": "Full name"},
    {"index": 3, "tag": "input", "type": "checkbox", "text": "Agree", "label": "Agree"},
    {"index": 4, "tag": "button", "type": "", "text": "More details", "label": ""},
]
LABELS = {1: "Search", 2: "Full name", 3: "Agree", 4: ""}
PAGE_TEXT = "Form page. Full name field, Agree checkbox, More details button."


class FakePage:
    def __init__(self, sticky_drops=0):
        self.values = {}
        self.checked = {}
        self.sticky_drops = sticky_drops   # types into field 2 that silently do not stick
        self.drop_all_types = False


def make_service(script, page, llm_delay=0.0):
    from app.services.browser_sandbox import BrowserSandboxService
    from app.services import llm as llm_mod
    import app.services.vision as vision_mod

    sb = BrowserSandboxService()
    log = {"claims": [], "releases": 0, "interacts": []}
    queue = list(script)

    async def fake_chat(messages=None, **kwargs):
        if llm_delay:
            await asyncio.sleep(llm_delay)
        if queue:
            return {"content": queue.pop(0), "_provider": "fake", "_example_id": ""}
        return {"content": '{"tips": []}', "_provider": "fake", "_example_id": ""}
    llm_mod.llm_service.chat_completion = fake_chat

    async def fake_main_sees():
        return True
    vision_mod.main_sees = fake_main_sees

    async def navigate(url):
        sb.current_url = url
        sb.current_title = "Form"
        return {"success": True}
    sb.navigate = navigate

    async def claim_tab(tab_id=None, owner="run"):
        log["claims"].append(owner)
        sb.claimed_tab_id = "tab1"
        sb.claim_owner = owner
        return {"success": True, "claimed_tab_id": "tab1", "owner": owner}
    sb.claim_tab = claim_tab

    def release_tab_claim():
        log["releases"] += 1
        sb.claimed_tab_id = None
        sb.claim_owner = None
        return {"success": True}
    sb.release_tab_claim = release_tab_claim

    async def manage_tabs(action="list", target_id=None, url="about:blank"):
        tab = {"id": "tab1", "type": "page", "title": sb.current_title, "url": sb.current_url}
        if action == "list":
            return {"success": True, "tabs": [tab], "active_tab_id": "tab1", "count": 1}
        return {"success": True, "action": action}
    sb.manage_tabs = manage_tabs

    async def observe(max_results=70):
        sb.indexed_elements = [dict(e) for e in ELEMENTS]
        return {"success": True, "elements": sb.indexed_elements}
    sb.observe = observe

    async def extract_clean_markdown(max_length=40000):
        return {"markdown": PAGE_TEXT}
    sb.extract_clean_markdown = extract_clean_markdown

    async def detect_captcha():
        return {}
    sb.detect_captcha = detect_captcha

    async def take_screenshot(**kwargs):
        return {}
    sb.take_screenshot = take_screenshot

    async def wait_ready(timeout=4.0):
        return None
    sb._wait_for_page_ready = wait_ready

    async def page_text_snapshot():
        return PAGE_TEXT
    sb._page_text_snapshot = page_text_snapshot

    async def page_tab_ids():
        return ["tab1"]
    sb._page_tab_ids = page_tab_ids

    async def tabs_block():
        return "TABS: 1 open (tab 1: Form)"
    sb._tabs_block = tabs_block

    async def invalid_form_fields():
        return []
    sb._invalid_form_fields = invalid_form_fields

    async def interact_by_index(index=None, action="click", text=None, press_enter=False,
                                clear_first=True, selector=None, text_target=None):
        log["interacts"].append((action, index, text))
        if action == "type":
            if page.drop_all_types:
                return {"success": True}              # reports success, value never sticks
            if index == 2 and page.sticky_drops > 0:
                page.sticky_drops -= 1
                return {"success": True}              # the sticky trap: silent miss
            page.values[index] = text
            return {"success": True}
        if action == "check":
            page.checked[index] = True
            return {"success": True}
        if action == "uncheck":
            page.checked[index] = False
            return {"success": True}
        return {"success": True}
    sb.interact_by_index = interact_by_index

    async def field_state(index):
        if index in (1, 2):
            return f'"{LABELS[index]}" now = "{page.values.get(index, "")}"'
        if index == 3:
            return f'"Agree" is now {"checked" if page.checked.get(3) else "not checked"}'
        return ""
    sb._field_state = field_state

    async def evaluate_js(expression):
        m = re.search(r'data-xod-idx="(\d+)"', expression or "")
        if m:
            return {"success": True, "result": LABELS.get(int(m.group(1)), "")}
        return {"success": True, "result": ""}
    sb.evaluate_js = evaluate_js

    async def scroll(direction="down", amount=700):
        return {"success": True}
    sb.scroll = scroll

    async def press_key(key):
        return {"success": True}
    sb.press_key = press_key

    async def unstuck():
        return None
    sb.unstuck = unstuck

    return sb, log


def plan(action, **kw):
    return json.dumps({"action": action, "reasoning": f"scripted {action}", **kw})


async def scenario_a():
    page = FakePage(sticky_drops=1)
    sb, log = make_service([
        plan("type", index=2, text="Ada Lovelace"),
        plan("check", index=3),
        plan("click", index=4),
        plan("finish", answer="Form filled."),
    ], page)
    rec = await sb.run_browser_mission(
        "Fill the form: set Full name to Ada Lovelace and agree to the terms.",
        start_url="http://t.local/form")
    steps = {s["action"]: s for s in rec["steps"] if s["action"] in ("type", "check", "click")}
    check("A1 mission completed", rec["status"] == "completed", rec["status"])
    check("A2 claim taken at start and released at end",
          len(log["claims"]) == 1 and log["releases"] >= 1 and sb.claimed_tab_id is None,
          f"claims={log['claims']} releases={log['releases']} claimed={sb.claimed_tab_id}")
    check("A3 mission record carries the claim", bool(rec.get("claim", {}).get("success")), str(rec.get("claim")))
    t = steps.get("type", {})
    check("A4 sticky type recovered by the retry and marked read-back",
          t.get("status") == "success" and t.get("recovered") is True and t.get("verified") == "read-back",
          str(t))
    c = steps.get("check", {})
    check("A5 check step verified by read-back",
          c.get("status") == "success" and c.get("verified") == "read-back", str(c))
    k = steps.get("click", {})
    check("A6 click step verified by state re-read",
          k.get("status") == "success" and k.get("verified") == "state-read", str(k))
    check("A7 the field really holds the value", page.values.get(2) == "Ada Lovelace", str(page.values))


async def scenario_b():
    page = FakePage()
    page.drop_all_types = True
    sb, log = make_service([
        plan("type", index=2, text="Ada Lovelace"),
        plan("finish", answer="Tried."),
    ], page)
    rec = await sb.run_browser_mission(
        "Fill the form: set Full name to Ada Lovelace.",
        start_url="http://t.local/form")
    t = next((s for s in rec["steps"] if s["action"] == "type"), {})
    check("B1 a type that never lands is recorded failed",
          t.get("status") == "failed" and t.get("verified") == "read-back", str(t))
    check("B2 the landing miss is named in the record",
          "did not land" in str(t.get("reasoning", "")) or "retry also missed" in str(t.get("landing", "")),
          str(t))
    check("B3 claim still released", sb.claimed_tab_id is None and log["releases"] >= 1,
          f"releases={log['releases']}")


async def scenario_c():
    page = FakePage()
    sb, log = make_service([plan("scroll", direction="down")] * 30, page)
    os.environ["XOD_MISSION_MAX_STEPS"] = "5"
    try:
        rec = await sb.run_browser_mission("Scroll the catalog.", start_url="http://t.local/form",
                                           max_steps=25)
    finally:
        os.environ.pop("XOD_MISSION_MAX_STEPS", None)
    check("C1 the configured ceiling stops the mission at 5 steps",
          rec["status"] == "incomplete" and len(rec["steps"]) == 5,
          f"status={rec['status']} steps={len(rec['steps'])}")


async def scenario_d():
    page = FakePage()
    sb, log = make_service([plan("scroll", direction="down")] * 30, page, llm_delay=0.35)
    os.environ["XOD_MISSION_WALL_SEC"] = "0.2"
    try:
        rec = await sb.run_browser_mission("Scroll the catalog.", start_url="http://t.local/form")
    finally:
        os.environ.pop("XOD_MISSION_WALL_SEC", None)
    check("D1 the wall-clock budget stops the mission cleanly",
          rec["status"] == "incomplete" and "time budget" in str(rec.get("final_answer", "")),
          f"status={rec['status']} answer={str(rec.get('final_answer'))[:120]}")
    check("D2 claim released after a budget stop", sb.claimed_tab_id is None,
          f"claimed={sb.claimed_tab_id}")


async def scenario_e():
    page = FakePage()
    sb, log = make_service([
        plan("type", index=1, text="hello"),
        plan("finish", answer="Searched."),
    ], page)
    orig_interact = sb.interact_by_index
    cleared = {"done": False}

    async def interact_then_restart(**kwargs):
        res = await orig_interact(**kwargs)
        if not cleared["done"]:
            cleared["done"] = True
            sb.claimed_tab_id = None        # what restart_browser does to the claim
        return res
    sb.interact_by_index = interact_then_restart
    rec = await sb.run_browser_mission("Search the site for hello.", start_url="http://t.local/form")
    check("E1 mission completed after the restart", rec["status"] == "completed", rec["status"])
    check("E2 the claim was re-pinned at the next step",
          len(log["claims"]) == 2, f"claims={log['claims']}")
    check("E3 the re-pin is in the mission history",
          any("re-pinned" in h for h in rec.get("log", [])), str(rec.get("log", []))[-200:])


async def main():
    await scenario_a()
    await scenario_b()
    await scenario_c()
    await scenario_d()
    await scenario_e()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
