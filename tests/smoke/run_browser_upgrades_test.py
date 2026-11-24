"""
Comprehensive verification suite for Xod browser upgrades (2026-11-23):
  Part A: W10 + E7  — GET /api/tasks/{id}, full CSS/attribute selectors, clear_first
  Part B: W3  + E2  — Destination verdict gate (no zero-effect 'completed')
  Part C: W2  + E8  — Chrome identity etiquette, safe user Chrome adoption, control any tab
  Part D: W1  + C*  — Live isolated Chrome gauntlet (attribute selectors, checkboxes,
                      dropdowns, JS alert/confirm/prompt, hover, HTML5 drag-and-drop,
                      same-origin iframe, open Shadow DOM, file upload, TodoMVC SPA,
                      multi-tab control by index/URL/title, screenshot/PDF/markdown)

Runs completely isolated (own XOD_DATA_DIR and hashed CDP port, never touches port 9228
or the running 1.1.21 app). Keeps real HOME so Chrome Page.navigate commits cleanly (AGE-99).

Run: /Users/reneturcios/xod-light-venv/bin/python3.12 tests/smoke/run_browser_upgrades_test.py
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
TMP = Path(tempfile.mkdtemp(prefix="xod_bupg_"))
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


# ================================================================ Part A: Task GET API
def part_a_task_get():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services.task_queue import task_queue

    # Create a task directly in the isolated SQLite DB without launching background worker
    t = task_queue.create("Check single task GET endpoint", origin="test")
    tid = t["id"]
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    r_ok = client.get(f"/api/tasks/{tid}")
    body = r_ok.json() if r_ok.status_code == 200 else {}
    check(
        "A1 GET /api/tasks/{task_id} returns 200 with the task (no 405)",
        r_ok.status_code == 200 and body.get("success") is True and (body.get("task") or {}).get("id") == tid,
        f"status={r_ok.status_code} body={body}",
    )
    r_404 = client.get("/api/tasks/nonexistent-task-id-999")
    check(
        "A2 GET /api/tasks/{missing} returns 404 cleanly",
        r_404.status_code == 404,
        f"status={r_404.status_code}",
    )


# ================================================================ Part B: Verdict Gate
async def part_b_verdict_gate():
    from app.services.browser_sandbox import BrowserSandboxService, evaluate_mission_verdict

    # Unit check on evaluate_mission_verdict
    v_all_fail = evaluate_mission_verdict(
        {"steps": [{"action": "click", "status": "failed"}, {"action": "type", "status": "failed"}]},
        current_url="https://example.com",
        current_title="Example",
    )
    check(
        "B1 verdict gate rejects when all actions failed",
        v_all_fail["passed"] is False and v_all_fail["reason"] == "all_actions_failed",
        str(v_all_fail),
    )

    v_no_mut = evaluate_mission_verdict(
        {
            "steps": [
                {"action": "navigate", "status": "success", "verified": "read-back"},
                {"action": "type", "status": "failed", "verified": "read-back"},
                {"action": "click", "status": "failed", "verified": "state-read"},
            ]
        },
        current_url="https://example.com/form",
        current_title="Form",
    )
    check(
        "B2 verdict gate rejects when zero mutating steps landed",
        v_no_mut["passed"] is False and v_no_mut["reason"] == "no_verified_mutation",
        str(v_no_mut),
    )

    v_blank = evaluate_mission_verdict(
        {"steps": [], "notes": []},
        current_url="about:blank",
        current_title="Empty Tab",
    )
    check(
        "B3 verdict gate rejects about:blank with zero work",
        v_blank["passed"] is False and v_blank["reason"] == "blank_destination_no_work",
        str(v_blank),
    )

    v_ok = evaluate_mission_verdict(
        {
            "steps": [
                {"action": "navigate", "status": "success", "verified": "read-back"},
                {"action": "type", "status": "success", "verified": "read-back"},
            ]
        },
        current_url="https://example.com/form",
        current_title="Form",
    )
    check(
        "B4 verdict gate passes verified mutation run",
        v_ok["passed"] is True and v_ok["verified_steps"] == 2,
        str(v_ok),
    )

    # End-to-end mission check: fake model claims finish after failed type -> must downgrade to failed!
    from app.services import llm as llm_mod
    sb = BrowserSandboxService()
    sb.current_url = "http://t.local/form"
    sb.current_title = "Form"
    sb.indexed_elements = [{"index": 1, "tag": "input", "type": "text", "text": "Name"}]

    async def no_op(*a, **k):
        return {"success": True, "url": "http://t.local/form", "title": "Form"}

    async def fake_observe(*a, **k):
        return {"success": True, "elements": sb.indexed_elements}

    async def fake_interact(*a, **k):
        return {"success": True}  # silent miss: field stays empty

    async def fake_field_state(idx):
        return '"Name" now = ""'

    async def fake_eval(expr):
        return {"success": True, "result": "Name"}

    async def fake_md(*a, **k):
        return {"success": True, "markdown": "Form page"}

    sb.ensure_browser_running = lambda: asyncio.sleep(0, result=True)
    sb.claim_tab = lambda tab_id=None, owner="mission": asyncio.sleep(0, result={"success": True, "claimed_tab_id": "t1"})
    sb.navigate = no_op
    sb.observe = fake_observe
    sb.interact_by_index = fake_interact
    sb._field_state = fake_field_state
    sb.evaluate_js = fake_eval
    sb.extract_clean_markdown = fake_md
    sb.take_screenshot = lambda **k: asyncio.sleep(0, result={"success": True})
    sb._wait_for_page_ready = lambda **k: asyncio.sleep(0)
    sb._learn_site_tips = lambda *a, **k: asyncio.sleep(0)
    sb._tabs_block = lambda: asyncio.sleep(0, result="")

    plans = iter([
        json.dumps({"action": "type", "index": 1, "text": "Ada", "reasoning": "fill name"}),
        json.dumps({"action": "finish", "answer": "All done!", "reasoning": "claimed done"}),
    ])

    orig_chat = llm_mod.llm_service.chat_completion
    async def fake_chat(*a, **k):
        content = next(plans, json.dumps({"action": "finish", "answer": "done"}))
        return {"content": content, "_provider": "fake", "_example_id": ""}

    llm_mod.llm_service.chat_completion = fake_chat
    try:
        rec = await sb.run_browser_mission("Fill Name with Ada", start_url="http://t.local/form", max_steps=4)
        check(
            "B5 mission with failed type cannot close as completed (verdict gate blocks)",
            rec.get("status") == "failed" and (rec.get("verdict") or {}).get("passed") is False,
            f"status={rec.get('status')} verdict={rec.get('verdict')}",
        )
    finally:
        llm_mod.llm_service.chat_completion = orig_chat


# ================================================================ Part C: Chrome Identity & Adoption
async def part_c_chrome_adoption():
    from app.services.browser_sandbox import BrowserSandboxService

    sb = BrowserSandboxService()

    # Mock devtools + process inspection for 3 distinct Chrome ports:
    # 9228 = xod_sandbox, 9225 = other_agent_chrome (playwright), 9222 = real user_chrome
    fake_tabs = {
        9228: [{"id": "s1", "type": "page", "title": "Sandbox", "url": "about:blank", "webSocketDebuggerUrl": "ws://127.0.0.1:9228/s1"}],
        9225: [{"id": "o1", "type": "page", "title": "Playwright", "url": "https://example.com", "webSocketDebuggerUrl": "ws://127.0.0.1:9225/o1"}],
        9222: [
            {"id": "u1", "type": "page", "title": "Gmail - Inbox", "url": "https://mail.google.com/mail/u/0/", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/u1"},
            {"id": "u2", "type": "page", "title": "GitHub - xod", "url": "https://github.com/CloudCorpRecords/xod", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/u2"},
        ],
    }

    async def fake_devtools(url, timeout=2.0, method=None, data=None):
        port = int(url.split("127.0.0.1:")[1].split("/")[0])
        if port not in fake_tabs:
            raise OSError("refused")
        if url.endswith("/json/version"):
            ua = "HeadlessChrome/133.0" if port == 9228 else "Chrome/133.0"
            return json.dumps({"Browser": "Chrome/133.0", "User-Agent": ua})
        if url.endswith("/json"):
            return json.dumps(fake_tabs[port])
        if "/activate/" in url:
            return "Target activated"
        return "{}"

    def fake_inspect(port):
        if port == 9228:
            return {"pid": 1001, "exe": "/Applications/Google Chrome.app", "cmdline": ["--xod-sandbox-instance=1", "--headless=new"], "user_data_dir": "/tmp/xod_ai_browser_sandbox", "is_headless": True}
        if port == 9225:
            return {"pid": 1002, "exe": "/Applications/Chromium.app", "cmdline": ["--user-data-dir=/tmp/playwright_profile"], "user_data_dir": "/tmp/playwright_profile", "is_headless": False}
        if port == 9222:
            return {"pid": 1003, "exe": "/Applications/Google Chrome.app", "cmdline": ["--remote-debugging-port=9222"], "user_data_dir": "/Users/reneturcios/Library/Application Support/Google/Chrome", "is_headless": False}
        return {"pid": None, "exe": "", "cmdline": [], "user_data_dir": "", "is_headless": False}

    sb._devtools = fake_devtools
    sb._inspect_port_process = fake_inspect
    sb._probe_devtools = lambda timeout: "ready" if sb.active_port() in fake_tabs else "refused"

    id_sb = await sb.identify_chrome(9228)
    id_other = await sb.identify_chrome(9225)
    id_user = await sb.identify_chrome(9222)
    check("C1 identify_chrome classifies xod_sandbox", id_sb["kind"] == "xod_sandbox", str(id_sb))
    check("C2 identify_chrome classifies other_agent_chrome", id_other["kind"] == "other_agent_chrome", str(id_other))
    check("C3 identify_chrome classifies user_chrome", id_user["kind"] == "user_chrome" and id_user["signed_in_profile"] is True, str(id_user))

    r_refuse_sb = await sb.adopt_user_chrome(port=9228)
    check("C4 adopt_user_chrome refuses Xod sandbox", r_refuse_sb.get("success") is False and "sandbox" in r_refuse_sb.get("error", "").lower(), str(r_refuse_sb))

    r_refuse_other = await sb.adopt_user_chrome(port=9225)
    check("C5 adopt_user_chrome refuses other agent Chrome", r_refuse_other.get("success") is False and "hijack" in r_refuse_other.get("error", "").lower(), str(r_refuse_other))

    r_adopt = await sb.adopt_user_chrome(port=9222)
    check("C6 adopt_user_chrome adopts real user Chrome", r_adopt.get("success") is True and sb.browser_mode == "user_chrome" and sb.active_port() == 9222, str(r_adopt))

    r_restart = await sb.restart_browser()
    check("C7 restart_browser refuses to kill adopted user Chrome", r_restart.get("success") is False and sb.browser_mode == "user_chrome", str(r_restart))

    # Control any tab by URL and title!
    r_sw = await sb.manage_tabs("switch", target_url_contains="github.com")
    check("C8 manage_tabs switches by target_url_contains", r_sw.get("success") is True and r_sw.get("active_tab_id") == "u2", str(r_sw))

    r_sw2 = await sb.manage_tabs("switch", target_title_contains="gmail")
    check("C9 manage_tabs switches by target_title_contains", r_sw2.get("success") is True and r_sw2.get("active_tab_id") == "u1", str(r_sw2))

    r_det = sb.detach_user_chrome()
    check("C10 detach_user_chrome cleanly returns to sandbox", r_det.get("success") is True and sb.browser_mode == "sandbox", str(r_det))


# ================================================================ Part D: Live Chrome Gauntlet
GAUNTLET_HTML = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Xod Browser Gauntlet</title>
<style>
  .hover-box { height: 48px; }
  .hover-box .secret { display: none; }
  .hover-box:hover .secret { display: block; }
  #drag-a, #drag-b { width: 80px; height: 40px; border: 1px solid #333; display: inline-block; margin: 4px; }
</style>
</head>
<body>
  <h1>Gauntlet Home</h1>

  <!-- Hidden decoy input before visible attribute-selector input (W10) -->
  <input type="text" name="email" data-testid="contact-email" style="display:none" value="decoy" />
  <label for="vis-email">Contact Email</label>
  <input id="vis-email" type="text" name="email" data-testid="contact-email" placeholder="you@example.com" />

  <!-- Checkbox & Dropdown (C13, C14) -->
  <input id="cb1" type="checkbox" />
  <select id="dd1">
    <option value="">Choose</option>
    <option value="opt1">Option One</option>
    <option value="opt2">Option Two</option>
  </select>

  <!-- JS Alert / Confirm / Prompt (C15) -->
  <button id="btn-alert" onclick="alert('Hello Alert!'); document.getElementById('dlg-out').textContent='alert-ok';">Trigger Alert</button>
  <button id="btn-prompt" onclick="const v = prompt('Your name?'); document.getElementById('dlg-out').textContent='prompt:' + v;">Trigger Prompt</button>
  <div id="dlg-out"></div>

  <!-- Hover (C20) -->
  <div id="hover-card" class="hover-box">Hover Me<div class="secret">Hover Revealed!</div></div>

  <!-- HTML5 Drag and Drop (C21) -->
  <div id="drag-a" draggable="true">Alpha</div>
  <div id="drag-b" draggable="true">Beta</div>
  <script>
    let dragged = null;
    document.getElementById('drag-a').addEventListener('dragstart', e => { dragged = 'drag-a'; });
    document.getElementById('drag-b').addEventListener('dragover', e => e.preventDefault());
    document.getElementById('drag-b').addEventListener('drop', e => {
      e.preventDefault();
      if (dragged === 'drag-a') {
        const a = document.getElementById('drag-a'), b = document.getElementById('drag-b');
        const tmp = a.textContent; a.textContent = b.textContent; b.textContent = tmp;
      }
    });
  </script>

  <!-- Same-origin iframe (C22) -->
  <iframe id="inner-frame" src="/iframe" width="300" height="100"></iframe>

  <!-- Open Shadow DOM (C25) -->
  <div id="shadow-host"></div>
  <script>
    const host = document.getElementById('shadow-host');
    const sr = host.attachShadow({ mode: 'open' });
    sr.innerHTML = '<p>Shadow Secret Text</p><input data-shadow="input" type="text" /><button id="shadow-btn">Shadow Click</button><span id="shadow-out"></span>';
    sr.getElementById('shadow-btn').addEventListener('click', () => {
      sr.getElementById('shadow-out').textContent = 'shadow-clicked:' + sr.querySelector('[data-shadow="input"]').value;
    });
  </script>

  <!-- File upload (C16) -->
  <input id="file-up" type="file" onchange="document.getElementById('up-out').textContent = this.files[0] ? this.files[0].name : ''" />
  <div id="up-out"></div>

  <!-- TodoMVC-style SPA (C36) -->
  <div id="todo-app">
    <input class="new-todo" placeholder="What needs to be done?" />
    <ul id="todo-list"></ul>
    <span id="todo-count">0 left</span>
  </div>
  <script>
    const todos = [];
    function renderTodos() {
      const ul = document.getElementById('todo-list');
      ul.innerHTML = '';
      todos.forEach((t, i) => {
        const li = document.createElement('li');
        li.innerHTML = `<input type="checkbox" class="toggle-todo" data-idx="${i}" ${t.done ? 'checked' : ''} /><span>${t.text}</span>`;
        li.querySelector('input').addEventListener('change', e => { todos[i].done = e.target.checked; renderTodos(); });
        ul.appendChild(li);
      });
      const left = todos.filter(t => !t.done).length;
      document.getElementById('todo-count').textContent = left + ' left';
    }
    document.querySelector('.new-todo').addEventListener('keydown', e => {
      if (e.key === 'Enter' && e.target.value.trim()) {
        todos.push({ text: e.target.value.trim(), done: false });
        e.target.value = '';
        renderTodos();
      }
    });
  </script>
</body>
</html>
"""

IFRAME_HTML = """<!doctype html>
<html><body>
  <p>Inside Iframe Content</p>
  <input name="frame_field" type="text" />
  <button id="frame-btn" onclick="document.getElementById('frame-out').textContent='frame-ok:' + document.querySelector('[name=frame_field]').value">Frame Go</button>
  <span id="frame-out"></span>
</body></html>
"""

TAB2_HTML = "<!doctype html><html><head><title>Second Research Tab</title></head><body><h1>Tab Two Fact: 42</h1></body></html>"


class GauntletHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/iframe":
            body = IFRAME_HTML.encode()
        elif self.path == "/tab2":
            body = TAB2_HTML.encode()
        else:
            body = GAUNTLET_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


async def part_d_live_gauntlet():
    from app.services.browser_sandbox import BrowserSandboxService

    httpd = socketserver.TCPServer(("127.0.0.1", 0), GauntletHandler)
    port = httpd.server_address[1]
    srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    srv_thread.start()
    base = f"http://127.0.0.1:{port}"

    sb = BrowserSandboxService()
    sb.set_workspace_path(str(TMP / "workspace"))
    (TMP / "workspace").mkdir(exist_ok=True)
    try:
        nav = await sb.navigate(f"{base}/")
        check("D1 live navigate to gauntlet page", nav.get("success") is True and "Gauntlet" in (nav.get("title") or ""), str(nav.get("error")))

        # W10: Attribute selector skipping hidden decoy
        r_type = await sb.type_text('input[name="email"][data-testid="contact-email"]', "rene@xod.ai")
        val_vis = (await sb.evaluate_js("document.getElementById('vis-email').value")).get("result")
        check("D2 attribute selector types into visible input (skips hidden decoy)", r_type.get("success") is True and val_vis == "rene@xod.ai", f"val={val_vis!r} r={r_type.get('error')}")

        # clear_first=False append
        await sb.type_text('input[name="email"][data-testid="contact-email"]', ".co", clear_first=False)
        val_app = (await sb.evaluate_js("document.getElementById('vis-email').value")).get("result")
        check("D3 type_text clear_first=False appends cleanly", val_app == "rene@xod.ai.co", f"val={val_app!r}")

        # C13: Checkbox
        r_cb = await sb.set_checkbox("#cb1", True)
        check("D4 checkbox checked and read back", r_cb.get("success") is True and r_cb.get("checked") is True, str(r_cb))

        # C14: Dropdown select
        r_dd = await sb.select_option("#dd1", "Option Two")
        check("D5 dropdown selected by label and read back", r_dd.get("success") is True and r_dd.get("selected_value") == "opt2", str(r_dd))

        # C15: JS alert & prompt (no hang!)
        r_al = await sb.click("#btn-alert")
        dlg_out = (await sb.evaluate_js("document.getElementById('dlg-out').textContent")).get("result")
        check("D6 JS alert auto-handled without hanging CDP", r_al.get("success") is True and dlg_out == "alert-ok" and (sb.last_dialog or {}).get("message") == "Hello Alert!", f"out={dlg_out} dlg={sb.last_dialog}")

        sb.set_next_dialog(accept=True, prompt_text="XodAgent")
        await sb.click("#btn-prompt")
        dlg_out2 = (await sb.evaluate_js("document.getElementById('dlg-out').textContent")).get("result")
        check("D7 JS prompt answered with armed text", dlg_out2 == "prompt:XodAgent", f"out={dlg_out2}")

        # C20: Hover
        r_hov = await sb.hover("#hover-card")
        check("D8 hover primitive succeeds", r_hov.get("success") is True, str(r_hov.get("error")))

        # C21: HTML5 Drag and Drop
        r_drag = await sb.drag_and_drop("#drag-a", "#drag-b")
        a_txt = (await sb.evaluate_js("document.getElementById('drag-a').textContent")).get("result")
        b_txt = (await sb.evaluate_js("document.getElementById('drag-b').textContent")).get("result")
        check("D9 HTML5 drag-and-drop swaps Alpha and Beta", r_drag.get("success") is True and a_txt == "Beta" and b_txt == "Alpha", f"a={a_txt} b={b_txt}")

        # C22: Same-origin iframe extraction + type + click
        content = await sb.extract_content()
        text_preview = content.get("text_preview") or ""
        check("D10 extract_content reads same-origin iframe text", "Inside Iframe Content" in text_preview, text_preview[:200])
        await sb.type_text('input[name="frame_field"]', "in-frame")
        await sb.click("#frame-btn")
        f_out = (await sb.evaluate_js("document.getElementById('inner-frame').contentDocument.getElementById('frame-out').textContent")).get("result")
        check("D11 type + click inside same-origin iframe lands", f_out == "frame-ok:in-frame", f"f_out={f_out!r}")

        # C25: Open Shadow DOM extraction + type + click
        check("D12 extract_content reads open Shadow DOM text", "Shadow Secret Text" in text_preview, text_preview[:200])
        await sb.type_text('input[data-shadow="input"]', "in-shadow")
        await sb.click("#shadow-btn")
        s_out = (await sb.evaluate_js("document.getElementById('shadow-host').shadowRoot.getElementById('shadow-out').textContent")).get("result")
        check("D13 type + click inside open Shadow DOM lands", s_out == "shadow-clicked:in-shadow", f"s_out={s_out!r}")

        # C16: File upload
        sample_file = TMP / "resume_probe.txt"
        sample_file.write_text("Xod upload proof", encoding="utf-8")
        r_up = await sb.upload_file([str(sample_file)], selector_or_index="#file-up")
        up_out = (await sb.evaluate_js("document.getElementById('up-out').textContent")).get("result")
        check("D14 file upload lands and triggers onchange", r_up.get("success") is True and up_out == "resume_probe.txt", f"r={r_up} out={up_out!r}")

        # C36: TodoMVC SPA (add 3, check 1 off -> 2 left)
        for item in ("Buy milk", "Ship 1.1.22", "Walk dog"):
            await sb.type_text(".new-todo", item, press_enter=True)
        await sb.set_checkbox('input.toggle-todo[data-idx="1"]', True)
        todo_cnt = (await sb.evaluate_js("document.getElementById('todo-count').textContent")).get("result")
        check("D15 TodoMVC add 3 + check 1 off -> '2 left'", todo_cnt == "2 left", f"todo_cnt={todo_cnt!r}")

        # Multi-tab control by URL / title
        await sb.manage_tabs("new", url=f"{base}/tab2")
        await asyncio.sleep(0.8)
        r_back = await sb.manage_tabs("switch", target_title_contains="Gauntlet")
        r_fwd = await sb.manage_tabs("switch", target_url_contains="/tab2")
        t2_txt = (await sb.extract_content()).get("text_preview") or ""
        check("D16 multi-tab switch by title & URL reads back Tab 2 fact", r_back.get("success") and r_fwd.get("success") and "42" in t2_txt, f"t2={t2_txt[:100]!r}")

        # C38: Screenshot, PDF, Markdown primitives
        r_ss = await sb.take_screenshot()
        r_md = await sb.extract_clean_markdown()
        r_pdf = await sb.export_pdf(filename="gauntlet_probe.pdf")
        check(
            "D17 screenshot, markdown, and PDF primitives all succeed",
            r_ss.get("success") and r_md.get("success") and "Tab Two Fact" in (r_md.get("markdown") or "") and r_pdf.get("success") and Path(r_pdf.get("path", "")).exists(),
            f"ss={r_ss.get('success')} md={r_md.get('success')} pdf={r_pdf}",
        )
    finally:
        sb.close()
        httpd.shutdown()


async def main():
    part_a_task_get()
    await part_b_verdict_gate()
    await part_c_chrome_adoption()
    await part_d_live_gauntlet()
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
