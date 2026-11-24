"""
Live & unit verification suite for the 2026-11-23 40-test benchmark failures (E9, E13, E14, E15, E16, E17):
  - E14/W17: detect_captcha() labels Google reCAPTCHA v2 (with data-sitekey) as 'recaptcha', NEVER 'cloudflare_turnstile'
  - E15/W18: get_downloads() sees workspace root, workspace/downloads, /tmp/xod_ai_downloads, and ~/Downloads
  - E17/W23: extract_clean_markdown() strips nav/header/footer chrome and returns > 15k chars (up to 40k default)
  - E9/W19:  TodoMVC / list-entry Enter is NOT blocked by the submit-confirmation gate
  - E13/W21: Generic file upload uses the file path handed in the goal instead of demanding profile resume_path
  - E16/W22: Repeating the same no-effect step twice triggers the loop-detection NOTE

Runs completely isolated (own XOD_DATA_DIR, hashed CDP port, never touches port 9228 or live 1.1.21 app).
"""
import asyncio
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_bfix_"))
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


LONG_ARTICLE = ("The distributed browser architecture verifies every interaction at the destination DOM. " * 260)

PAGES = {
    "/recaptcha": """<!doctype html><html><head><title>reCAPTCHA Demo</title></head><body>
      <h1>Google reCAPTCHA v2 Demo</h1>
      <div class="g-recaptcha" data-sitekey="6Le-wvkSAAAAAPBMRTvw0Q4Muexq9bi0DJwx_mJ-" style="width:304px;height:78px;"></div>
    </body></html>""",
    "/turnstile": """<!doctype html><html><head><title>Turnstile Demo</title></head><body>
      <h1>Cloudflare Turnstile Demo</h1>
      <div class="cf-turnstile" data-sitekey="0x4AAAAAAA" style="width:300px;height:65px;"></div>
    </body></html>""",
    "/article": f"""<!doctype html><html><head><title>Long Article</title></head><body>
      <header><nav>NAV CHROME SPAM MENU LINKS SIGN IN SUBSCRIBE</nav></header>
      <aside>SIDEBAR AD SPAM</aside>
      <main>
        <h1>Real Article Heading</h1>
        <p>{LONG_ARTICLE}</p>
        <h2>Final Conclusion Section</h2>
        <p>End of long article proof.</p>
      </main>
      <footer>FOOTER LEGAL CHROME SPAM</footer>
    </body></html>""",
    "/keys": """<!doctype html><html><head><title>Key Presses</title></head><body>
      <h1>Key Presses</h1>
      <input id="key-in" type="text" />
      <div id="key-log"></div>
      <script>
        const keys = [];
        document.addEventListener('keydown', e => {
          keys.push(e.key);
          document.getElementById('key-log').textContent = keys.join(',');
        });
      </script>
    </body></html>""",
}


class FixHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = PAGES.get(self.path, PAGES["/article"]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


async def part_live():
    from app.services.browser_sandbox import BrowserSandboxService

    httpd = socketserver.TCPServer(("127.0.0.1", 0), FixHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    ws_dir = TMP / "workspace"
    ws_dir.mkdir(exist_ok=True)
    sb = BrowserSandboxService()
    sb.set_workspace_path(str(ws_dir))
    try:
        # E14: reCAPTCHA with data-sitekey must NOT be mislabeled Turnstile
        await sb.navigate(f"{base}/recaptcha")
        det_rc = await sb.detect_captcha()
        check("E14a reCAPTCHA v2 with data-sitekey is labeled 'recaptcha' (not cloudflare_turnstile)",
              det_rc.get("has_captcha") is True and det_rc.get("type") == "recaptcha",
              str(det_rc))

        # E14: real Cloudflare Turnstile still detected as cloudflare_turnstile
        await sb.navigate(f"{base}/turnstile")
        det_ts = await sb.detect_captcha()
        check("E14b real .cf-turnstile is still labeled 'cloudflare_turnstile'",
              det_ts.get("has_captcha") is True and det_ts.get("type") == "cloudflare_turnstile",
              str(det_ts))

        # E17: Markdown strips nav/header/footer/aside chrome and returns > 18k chars
        await sb.navigate(f"{base}/article")
        md_res = await sb.extract_clean_markdown()
        md = md_res.get("markdown") or ""
        check("E17a extract_clean_markdown leads with main article and strips nav/footer/aside chrome",
              md.startswith("# Real Article Heading") and "NAV CHROME SPAM" not in md and "FOOTER LEGAL CHROME" not in md and "SIDEBAR AD SPAM" not in md,
              f"head={md[:120]!r}")
        check("E17b extract_clean_markdown exceeds old 15k ceiling (> 18k chars returned)",
              len(md) > 18000 and "Final Conclusion Section" in md,
              f"len={len(md)}")

        # E15: get_downloads sees files in active_workspace_path
        dl_file = ws_dir / "downloaded_report_probe.txt"
        dl_file.write_text("download proof", encoding="utf-8")
        dls = await sb.get_downloads()
        names = [f["name"] for f in dls.get("downloads") or []]
        check("E15 get_downloads lists real downloaded file in workspace root",
              dls.get("success") is True and "downloaded_report_probe.txt" in names,
              f"names={names[:10]}")

        # Benchmark #22: press_key dispatches real A, B, 7 (not Enter!)
        await sb.navigate(f"{base}/keys")
        await sb.press_key("A")
        await sb.press_key("B")
        await sb.press_key("7")
        klog = (await sb.evaluate_js("document.getElementById('key-log').textContent")).get("result")
        check("E16_keys press_key dispatches real printable keys A, B, 7",
              klog == "A,B,7",
              f"klog={klog!r}")

        # Benchmark #39: CDP Back/Forward navigation history
        await sb.navigate(f"{base}/recaptcha")
        await sb.navigate(f"{base}/turnstile")
        r_bk = await sb._navigate_history(-1)
        r_fw = await sb._navigate_history(1)
        check("E12 CDP back/forward navigates history cleanly (/turnstile -> /recaptcha -> /turnstile)",
              r_bk.get("success") and "/recaptcha" in (r_bk.get("url") or "") and r_fw.get("success") and "/turnstile" in (r_fw.get("url") or ""),
              f"bk={r_bk.get('url')} fw={r_fw.get('url')}")
    finally:
        sb.close()
        httpd.shutdown()


async def part_mission_guards():
    from app.services.browser_sandbox import (
        BrowserSandboxService,
        _looks_like_list_entry_box,
        _extract_explicit_file_path,
    )
    from app.services import llm as llm_mod

    # E9 helper check
    check("E9a _looks_like_list_entry_box recognizes TodoMVC input",
          _looks_like_list_entry_box({"placeholder": "What needs to be done?", "class": "new-todo"}) is True)
    check("E9b _looks_like_list_entry_box rejects normal checkout email field",
          _looks_like_list_entry_box({"placeholder": "Email address", "name": "email"}) is False)

    # E13 helper check
    sample = TMP / "upload_handout.txt"
    sample.write_text("hello upload", encoding="utf-8")
    found_path = _extract_explicit_file_path({}, f"Please upload {sample} to the page.")
    check("E13a _extract_explicit_file_path extracts handed file path from goal",
          found_path == str(sample), f"found={found_path!r} want={str(sample)!r}")

    # E9 + E13 + E16 scripted mission checks
    sb = BrowserSandboxService()
    sb.current_url = "http://t.local/todo"
    sb.current_title = "TodoMVC"
    sb.indexed_elements = [
        {"index": 1, "tag": "input", "type": "text", "placeholder": "What needs to be done?", "class": "new-todo", "text": ""},
        {"index": 2, "tag": "input", "type": "file", "text": "Choose File"},
        {"index": 3, "tag": "input", "type": "password", "name": "password", "text": "Password"},
    ]
    uploaded_files = []
    typed_values = []

    sb.ensure_browser_running = lambda: asyncio.sleep(0, result=True)
    sb.claim_tab = lambda tab_id=None, owner="mission": asyncio.sleep(0, result={"success": True, "claimed_tab_id": "t1"})
    sb.navigate = lambda url: asyncio.sleep(0, result={"success": True, "url": url, "title": "TodoMVC"})
    sb.observe = lambda **k: asyncio.sleep(0, result={"success": True, "elements": sb.indexed_elements})
    sb.extract_clean_markdown = lambda **k: asyncio.sleep(0, result={"success": True, "markdown": "TodoMVC app"})
    sb.take_screenshot = lambda **k: asyncio.sleep(0, result={"success": True})
    sb._wait_for_page_ready = lambda **k: asyncio.sleep(0)
    sb._learn_site_tips = lambda *a, **k: asyncio.sleep(0)
    sb._tabs_block = lambda: asyncio.sleep(0, result="")
    sb.evaluate_js = lambda expr: asyncio.sleep(0, result={"success": True, "result": ""})
    sb._field_state = lambda idx: asyncio.sleep(0, result=f'"What needs to be done?" now = "{typed_values[-1] if typed_values else ""}"')
    sb.press_key = lambda key: asyncio.sleep(0, result={"success": True, "key": key})
    sb.unstuck = lambda **k: asyncio.sleep(0, result={"success": True})

    async def fake_interact(index=None, action="click", text="", press_enter=False, **k):
        if action == "type":
            typed_values.append(text)
        return {"success": True}

    async def fake_upload(paths_list, selector_or_index=None):
        uploaded_files.extend(paths_list)
        return {"success": True, "files": paths_list}

    sb.interact_by_index = fake_interact
    sb.upload_file = fake_upload

    orig_chat = llm_mod.llm_service.chat_completion
    # Test E9: typing + pressing Enter on TodoMVC must NOT pause for submit confirmation!
    plans_e9 = iter([
        json.dumps({"action": "type", "index": 1, "text": "Buy milk", "reasoning": "add todo"}),
        json.dumps({"action": "press_key", "text": "Enter", "reasoning": "commit todo"}),
        json.dumps({"action": "finish", "answer": "Added Buy milk."}),
    ])
    llm_mod.llm_service.chat_completion = lambda *a, **k: asyncio.sleep(0, result={"content": next(plans_e9), "_provider": "fake", "_example_id": ""})
    try:
        rec_e9 = await sb.run_browser_mission("Add 'Buy milk' to TodoMVC", start_url="http://t.local/todo", max_steps=5)
        check("E9c TodoMVC Enter is NOT blocked by submit confirmation gate",
              rec_e9.get("status") == "completed",
              f"status={rec_e9.get('status')} ans={rec_e9.get('final_answer')}")

        # Test E13: generic file upload with handed path uses that path instead of demanding resume_path!
        plans_e13 = iter([
            json.dumps({"action": "type", "index": 2, "text": str(sample), "reasoning": "upload file"}),
            json.dumps({"action": "finish", "answer": "Uploaded."}),
        ])
        llm_mod.llm_service.chat_completion = lambda *a, **k: asyncio.sleep(0, result={"content": next(plans_e13), "_provider": "fake", "_example_id": ""})
        rec_e13 = await sb.run_browser_mission(f"Upload {sample} to the file form", start_url="http://t.local/todo", max_steps=4)
        check("E13b mission uploads handed file path without hijacking into upload_resume",
              rec_e13.get("status") == "completed" and str(sample) in uploaded_files,
              f"status={rec_e13.get('status')} uploaded={uploaded_files}")

        # Test E16: repeating the same no-effect click twice injects LOOP warning
        plans_e16 = iter([
            json.dumps({"action": "click", "index": 1, "reasoning": "click input"}),
            json.dumps({"action": "click", "index": 1, "reasoning": "click input again"}),
            json.dumps({"action": "finish", "answer": "Done"}),
        ])
        llm_mod.llm_service.chat_completion = lambda *a, **k: asyncio.sleep(0, result={"content": next(plans_e16), "_provider": "fake", "_example_id": ""})
        rec_e16 = await sb.run_browser_mission("Click input", start_url="http://t.local/todo", max_steps=4)
        has_loop_note = any("NOTE: the last two steps repeated" in h for h in (rec_e16.get("log") or []))
        check("E16 repeating same no-effect step twice injects loop-detection NOTE",
              has_loop_note,
              str(rec_e16.get("log")))

        # Test E7/W20a: password explicitly handed in GOAL is accepted & audit-logged (masked)
        typed_values.clear()
        plans_e7_ok = iter([
            json.dumps({"action": "type", "index": 3, "text": "SuperSecretPassword!", "reasoning": "type demo pw"}),
            json.dumps({"action": "finish", "answer": "Logged in."}),
        ])
        llm_mod.llm_service.chat_completion = lambda *a, **k: asyncio.sleep(0, result={"content": next(plans_e7_ok), "_provider": "fake", "_example_id": ""})
        rec_e7_ok = await sb.run_browser_mission(
            "Log in with username tomsmith and password SuperSecretPassword!",
            start_url="http://t.local/login",
            max_steps=4,
        )
        has_audit = any("audit: typed goal-supplied password" in h for h in (rec_e7_ok.get("log") or []))
        check("E7a goal-supplied demo password is typed and audit-logged (masked)",
              rec_e7_ok.get("status") == "completed" and "SuperSecretPassword!" in typed_values and has_audit,
              f"status={rec_e7_ok.get('status')} typed={typed_values} audit={has_audit}")

        # Test E7/W20b: password NOT in GOAL is still rejected
        typed_values.clear()
        plans_e7_bad = iter([
            json.dumps({"action": "type", "index": 3, "text": "NotInGoal123", "reasoning": "invented pw"}),
            json.dumps({"action": "finish", "answer": "Done"}),
        ])
        llm_mod.llm_service.chat_completion = lambda *a, **k: asyncio.sleep(0, result={"content": next(plans_e7_bad), "_provider": "fake", "_example_id": ""})
        rec_e7_bad = await sb.run_browser_mission(
            "Log into the site.",
            start_url="http://t.local/login",
            max_steps=4,
        )
        check("E7b ungrounded password NOT in goal is rejected and blocked by verdict gate",
              rec_e7_bad.get("status") == "failed" and "NotInGoal123" not in typed_values,
              f"status={rec_e7_bad.get('status')} typed={typed_values}")
    finally:
        llm_mod.llm_service.chat_completion = orig_chat


async def main():
    await part_live()
    await part_mission_guards()
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
