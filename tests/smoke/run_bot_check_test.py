"""
Bot checks in browser missions (services/browser_sandbox.py), with the real mission loop over a stand-in
browser and a scripted planner. Xod never solves or gets past a bot check; what changes is who waits.

Live 2026-11-04 (orchestrator load run): a research task re-checking a fact it already had hit a reCAPTCHA
and stopped for the person.

  C1 a read-only mission skips a site showing a check: it does not wait for the person, says which site it
     skipped, and finishes from another site
  C2 nothing on the check page is pressed or filled (the "I'm not a robot" box stays unclicked)
  C3 stuck on the check page, it finishes "incomplete" and names the site it could not read; never "blocked"
  C4 a mission that has typed something (or may submit) still hands the check to the person (Take Over)

Run: .venv/bin/python tests/smoke/run_bot_check_test.py [--repeat N]
"""
import asyncio, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_botcheck_"))
os.environ.update({"XOD_DATA_DIR": str(TMP), "XOD_NO_UPDATE_CHECK": "1", "XOD_SPEECH": "off",
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_SECRETS_SERVICE": "Xod test keys"})
sys.path.insert(0, str(ROOT))

import app.services.browser_sandbox as BS
from app.services import llm as LLM

BS.MISSION_USE_VISION = False
results = []
PAGES = {
    "https://facts.example/": ("Facts", "Robot check. Please confirm you are human.", True),
    "https://other.example/": ("Other facts", "The Eiffel Tower is 330 metres tall including antennas.", False),
    "https://form.example/": ("Signup", "Sign up form", False),
}


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


class Page:
    """The stand-in browser: the pages above, a log of what was pressed, and whether it waited for the person."""

    def __init__(self, sb, captcha_after_typing=False):
        self.sb, self.pressed, self.waited, self.captcha_after_typing, self.typed = sb, [], 0, captcha_after_typing, False

    def install(self):
        sb, me = self.sb, self

        async def navigate(url, *a, **k):
            sb.current_url = url
            sb.current_title = PAGES.get(url, ("?", "", False))[0]
            return {"success": True}

        async def detect_captcha(*a, **k):
            on = PAGES.get(sb.current_url, ("", "", False))[2] or (me.captcha_after_typing and me.typed)
            return {"has_captcha": on, "type": "recaptcha"} if on else {"has_captcha": False}

        async def observe(*a, **k):
            if PAGES.get(sb.current_url, ("", "", False))[2] or (me.captcha_after_typing and me.typed):
                return {"elements": [{"index": 1, "tag": "input", "type": "checkbox", "text": "I'm not a robot", "label": "I'm not a robot"}]}
            if sb.current_url == "https://form.example/":
                return {"elements": [{"index": 2, "tag": "input", "type": "text", "text": "Name", "label": "Name"}]}
            return {"elements": []}

        async def markdown(*a, **k):
            return {"markdown": PAGES.get(sb.current_url, ("", "", False))[1]}

        async def interact(index=None, action="click", text=None, *a, **k):
            me.pressed.append((sb.current_url, index, action))
            if action == "type":
                me.typed = True
            return {"success": True}

        async def waited(*a, **k):
            me.waited += 1
            return False

        async def nothing(*a, **k):
            return None

        async def empty(*a, **k):
            return []

        async def text(*a, **k):
            return PAGES.get(sb.current_url, ("", "", False))[1]

        async def blank(*a, **k):
            return ""

        async def ok(*a, **k):
            return {"success": True}

        async def question(*a, **k):
            return "Name"

        for name, fn in {"navigate": navigate, "detect_captcha": detect_captcha, "observe": observe,
                         "extract_clean_markdown": markdown, "interact_by_index": interact, "_wait_for_human_check": waited,
                         "_wait_for_page_ready": nothing, "_tabs_block": blank, "_page_tab_ids": empty,
                         "_page_text_snapshot": text, "_learn_site_tips": nothing, "take_screenshot": ok, "unstuck": ok,
                         "evaluate_js": ok, "_field_question": question, "_field_state": nothing, "_invalid_form_fields": empty}.items():
            setattr(sb, name, fn)
        sb.current_url, sb.current_title, sb.latest_screenshot_uri = "about:blank", "", None


def planner(script):
    calls = {"n": 0}

    async def chat(messages=None, **k):
        i = min(calls["n"], len(script) - 1)
        calls["n"] += 1
        return {"content": json.dumps(script[i]), "_provider": "test"}
    return chat


def run(sb, goal, start, script, allow_submit=False, max_steps=8):
    LLM.llm_service.chat_completion = planner(script)
    return asyncio.run(sb._run_browser_mission_unlocked(goal, start, max_steps, allow_submit))


def main(repeat):
    real_chat = LLM.llm_service.chat_completion
    try:
        for n in range(repeat):
            print(f"=== run {n + 1}/{repeat}")
            sb = BS.BrowserSandboxService() if hasattr(BS, "BrowserSandboxService") else type(BS.browser_sandbox_service)()

            print("C1/C2 a read-only mission skips the checked site")
            p = Page(sb)
            p.install()
            r = run(sb, "Find the Eiffel Tower's height", "https://facts.example/", [
                {"action": "click", "index": 1, "reasoning": "tick the box"},
                {"action": "navigate", "url": "https://other.example/", "reasoning": "another source"},
                {"action": "finish", "answer": "330 m, from other.example (facts.example could not be read: bot check)."}])
            steps = [s["action"] for s in r["steps"]]
            check("it did not wait for the person", p.waited == 0, str(steps))
            check("it said which site it skipped", "skipped_bot_check" in steps, str(steps))
            check("it finished from another site", r["status"] == "completed" and "330" in (r["final_answer"] or ""), f"{r['status']} {r['final_answer']}")
            check("nothing on the check page was pressed", not [x for x in p.pressed if x[0] == "https://facts.example/"], str(p.pressed))

            print("C3 stuck on the check page")
            p = Page(sb)
            p.install()
            r = run(sb, "Find the Eiffel Tower's height", "https://facts.example/", [
                {"action": "scroll", "direction": "down", "reasoning": "look for the answer"}])
            check("it ends incomplete, naming the site, not blocked", r["status"] == "incomplete" and "facts.example" in (r["final_answer"] or ""),
                  f"{r['status']} {r['final_answer']}")
            check("still never waited, never pressed", p.waited == 0 and not p.pressed, f"{p.waited} {p.pressed}")

            print("C4 a mission that has typed still hands the check over")
            p = Page(sb, captcha_after_typing=True)
            p.install()
            r = run(sb, "Sign me up with the name Sam", "https://form.example/", [
                {"action": "type", "index": 2, "text": "Sam", "reasoning": "fill the name"},
                {"action": "finish", "answer": "done"}])
            check("it waited for the person (Take Over) and stopped blocked", p.waited == 1 and r["status"] == "blocked",
                  f"waited {p.waited} status {r['status']} steps {[s['action'] for s in r['steps']]}")
            p = Page(sb)
            p.install()
            r = run(sb, "Read the facts page", "https://facts.example/", [{"action": "finish", "answer": "x"}], allow_submit=True)
            check("a mission allowed to submit hands it over too", p.waited == 1 and r["status"] == "blocked", f"{p.waited} {r['status']}")
    finally:
        LLM.llm_service.chat_completion = real_chat
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main(int(sys.argv[sys.argv.index("--repeat") + 1]) if "--repeat" in sys.argv else 1)
