"""
4-Tier Live Chrome Browser Gauntlet + 6-Piece Harness Verification.

Launches a real headless Chrome instance against a local realistic web app
and pushes Xod's browser harness across 4 escalating difficulty tiers:

  Tier 1 (Easy):      Controlled inputs + before->after DOM state diffing
  Tier 2 (Medium):    Real contenteditable social composer (Lexical/Draft.js style:
                      Post button stays disabled unless real Input/beforeinput fires)
  Tier 3 (Hard):      Multi-step ATS job application using job_profile + learned_qa:
                      inline validation-error detection, ARIA combobox dropdown,
                      contenteditable cover letter, checkbox, resume upload, modal confirm
  Tier 4 (Very Hard): 3-tier action escalation ladder (DOM -> Box -> Vision coords),
                      MissionChecklist persistence & resume, Recipe compilation &
                      deterministic live Chrome replay, and Context Hygiene pruning

  .venv/bin/python tests/smoke/run_tiered_browser_live_test.py
"""
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
import asyncio
import http.server
import json
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-tiered-live-"))
os.environ.setdefault("XOD_SPEECH", "off")
import socket as _socket
_s = _socket.socket()
_s.bind(("127.0.0.1", 0))
_DBG_PORT = _s.getsockname()[1]
_s.close()
os.environ.update({
    "XOD_DATA_DIR": str(TMP / "data"),
    "XOD_NO_UPDATE_CHECK": "1",
    "XOD_BROWSER_HEADLESS": "1",
    "XOD_BROWSER_DEBUG_PORT": str(_DBG_PORT),
})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


GAUNTLET_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Xod 4-Tier Live Browser Gauntlet</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 24px; max-width: 900px; }
  section { border: 1px solid #ccc; padding: 16px; margin-bottom: 24px; border-radius: 8px; }
  .composer { border: 1px solid #888; min-height: 60px; padding: 8px; margin: 8px 0; }
  .error-message { color: #b00; font-weight: 600; margin: 6px 0; }
  .listbox { border: 1px solid #666; background: #fff; padding: 4px; list-style: none; margin: 4px 0; }
  .listbox li { padding: 4px 8px; cursor: pointer; }
  .modal-bg { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; }
  .modal-box { background: #fff; padding: 20px; border-radius: 8px; width: 380px; }
  .hidden { display: none !important; }
</style>
</head>
<body>
<h1>Xod 4-Tier Browser Gauntlet</h1>

<!-- TIER 1 & TIER 2: Lexical/Draft.js-style Social Composer -->
<section id="social-section">
  <h2>Tier 2: Logged-in Social Composer</h2>
  <div id="composer" class="composer" contenteditable="true" role="textbox" aria-label="What's on your mind?"></div>
  <button id="post-btn" disabled>Post</button>
  <div id="feed"></div>
</section>

<!-- TIER 3: Multi-Step ATS Job Application -->
<section id="ats-section">
  <h2>Tier 3: ATS Job Application (Senior Engineer)</h2>
  <div id="ats-errors" role="alert" class="error-message hidden"></div>
  <div>
    <label for="ats-name">Full Name</label>
    <input id="ats-name" name="full_name" placeholder="Full Name" />
  </div>
  <div>
    <label for="ats-email">Email Address</label>
    <input id="ats-email" name="email" type="email" placeholder="Email" />
  </div>
  <div>
    <label for="ats-phone">Phone Number</label>
    <input id="ats-phone" name="phone" placeholder="Phone Number" aria-invalid="false" />
  </div>
  <div>
    <label for="ats-linkedin">LinkedIn Profile URL</label>
    <input id="ats-linkedin" name="linkedin" placeholder="https://linkedin.com/in/..." />
  </div>
  <div>
    <label for="ats-auth">Work Authorization</label>
    <input id="ats-auth" name="work_auth" role="combobox" aria-haspopup="listbox" aria-label="Work Authorization" placeholder="Select authorization..." readonly />
    <ul id="auth-listbox" role="listbox" class="listbox hidden">
      <li role="option" data-val="Authorized to work in the US">Authorized to work in the US</li>
      <li role="option" data-val="Require visa sponsorship">Require visa sponsorship</li>
    </ul>
  </div>
  <div>
    <label for="ats-salary">Desired Salary</label>
    <input id="ats-salary" name="salary" placeholder="Desired Salary" />
  </div>
  <div>
    <label>Why do you want to join? (Cover Note)</label>
    <div id="ats-cover" class="composer" contenteditable="true" aria-label="Cover Note"></div>
  </div>
  <div>
    <label for="ats-resume">Resume PDF</label>
    <input id="ats-resume" name="resume" type="file" accept=".pdf" />
    <span id="resume-status"></span>
  </div>
  <div>
    <label><input id="ats-agree" name="agree" type="checkbox" /> I certify my information is accurate</label>
  </div>
  <button id="ats-next" type="button">Review Application</button>
  <div id="ats-result"></div>
</section>

<!-- Modal for Tier 3 final confirmation -->
<div id="confirm-modal" class="modal-bg hidden">
  <div class="modal-box" role="dialog" aria-modal="true" aria-label="Confirm Application">
    <h3>Confirm Application</h3>
    <p id="confirm-summary"></p>
    <button id="ats-final-submit" type="button">Submit Application</button>
  </div>
</div>

<!-- TIER 4: Adversarial Canvas/Coordinate Target -->
<section id="tier4-section">
  <h2>Tier 4: Canvas Coordinate Target</h2>
  <canvas id="secret-canvas" width="220" height="60" style="border:1px solid #333; cursor:pointer;"></canvas>
  <div id="canvas-status">waiting</div>
</section>

<script>
  // Tier 2: Strict Lexical/Draft.js-like state tracking: Post stays disabled until real input event fires
  const composer = document.getElementById('composer');
  const postBtn = document.getElementById('post-btn');
  const feed = document.getElementById('feed');
  let composerState = '';
  composer.addEventListener('input', () => {
    composerState = (composer.innerText || composer.textContent || '').trim();
    postBtn.disabled = composerState.length === 0;
  });
  postBtn.addEventListener('click', () => {
    if (!composerState) return;
    const item = document.createElement('div');
    item.className = 'feed-post';
    item.textContent = 'PUBLISHED: ' + composerState;
    feed.prepend(item);
    composer.innerText = '';
    composerState = '';
    postBtn.disabled = true;
  });

  // Tier 3: ARIA combobox + validation + review modal
  const authInput = document.getElementById('ats-auth');
  const authList = document.getElementById('auth-listbox');
  authInput.addEventListener('click', () => authList.classList.remove('hidden'));
  for (const li of authList.querySelectorAll('li')) {
    li.addEventListener('click', () => {
      authInput.value = li.getAttribute('data-val');
      authList.classList.add('hidden');
    });
  }
  document.getElementById('ats-resume').addEventListener('change', (e) => {
    const f = e.target.files && e.target.files[0];
    document.getElementById('resume-status').textContent = f ? ('UPLOADED:' + f.name) : '';
  });
  document.getElementById('ats-next').addEventListener('click', () => {
    const errs = [];
    const name = document.getElementById('ats-name').value.trim();
    const email = document.getElementById('ats-email').value.trim();
    const phoneEl = document.getElementById('ats-phone');
    const phone = phoneEl.value.trim();
    const auth = authInput.value.trim();
    const agree = document.getElementById('ats-agree').checked;
    const resume = document.getElementById('resume-status').textContent;
    if (!name) errs.push('Full Name is required');
    if (!email) errs.push('Email is required');
    if (!phone) {
      phoneEl.setAttribute('aria-invalid', 'true');
      errs.push('Phone number is required');
    } else {
      phoneEl.setAttribute('aria-invalid', 'false');
    }
    if (!auth) errs.push('Work Authorization is required');
    if (!resume) errs.push('Resume PDF is required');
    if (!agree) errs.push('Certification checkbox is required');

    const errBox = document.getElementById('ats-errors');
    if (errs.length > 0) {
      errBox.textContent = errs.join(' | ');
      errBox.classList.remove('hidden');
      return;
    }
    errBox.textContent = '';
    errBox.classList.add('hidden');
    document.getElementById('confirm-summary').textContent = `${name} (${email}) — ${auth}`;
    document.getElementById('confirm-modal').classList.remove('hidden');
  });
  document.getElementById('ats-final-submit').addEventListener('click', () => {
    document.getElementById('confirm-modal').classList.add('hidden');
    document.getElementById('ats-result').textContent = 'APPLICATION_SUBMITTED_OK';
  });

  // Tier 4: Canvas coordinate click target (no semantic button inside)
  const canvas = document.getElementById('secret-canvas');
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#2244aa';
  ctx.fillRect(0, 0, 220, 60);
  ctx.fillStyle = '#fff';
  ctx.font = '16px sans-serif';
  ctx.fillText('Activate Secret Canvas', 24, 36);
  canvas.addEventListener('click', () => {
    document.getElementById('canvas-status').textContent = 'CANVAS_ACTIVATED_OK';
  });
</script>
</body>
</html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = GAUNTLET_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{port}/"


async def run_all():
    from app.services.browser_sandbox import BrowserSandboxService
    from app.services import job_profile, mission_checkpoint, action_ladder, recipe_store
    from app.agent import context_hygiene

    srv, url = start_server()
    browser = BrowserSandboxService()
    resume_file = TMP / "alex_resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4\n% Xod test resume\n")

    try:
        # ---- TIER 1 & TIER 2: Live contenteditable Social Composer ----
        print("TIER 1 & 2: Live Chrome contenteditable Social Composer + State Diff")
        nav = await browser.navigate(url)
        check("T1 navigate to live 4-tier gauntlet page", nav.get("success") is True, str(nav)[:160])

        t2_type = await browser.type_text("#composer", "Shipping Xod harness upgrades live from Chrome!")
        delta2 = t2_type.get("delta") or {}
        check("T2a CDP Input.insertText typed into contenteditable and verified readback",
              t2_type.get("success") is True
              and (t2_type.get("typed") or {}).get("verified") is True
              and "Shipping Xod harness upgrades" in ((t2_type.get("typed") or {}).get("readBack") or ""),
              str(t2_type.get("typed"))[:200])
        check("T2b state diff detected Post button becoming enabled",
              any("post" in b.lower() for b in (delta2.get("newly_enabled_buttons") or [])),
              str(delta2))

        t2_post = await browser.click("#post-btn")
        ext2 = await browser.extract_content()
        ext2_txt = ext2.get("text_preview") or ext2.get("text") or ""
        check("T2c clicking enabled Post button published post to live feed",
              t2_post.get("success") is True
              and "PUBLISHED: Shipping Xod harness upgrades live from Chrome!" in ext2_txt,
              ext2_txt[:240])

        # ---- TIER 3: Multi-Step ATS Job Application + Validation Recovery + Profile ----
        print("TIER 3: Multi-Step ATS Job Application (Profile + Validation Diff + ARIA Combobox + Modal)")
        job_profile.save_job_profile({
            "first_name": "Alex",
            "last_name": "Rivera",
            "email": "alex.rivera@example.com",
            "phone": "555-019-2834",
            "linkedin": "https://linkedin.com/in/alexrivera",
            "work_authorization": "Authorized to work in the US",
            "salary_expectation": "$185,000",
            "resume_path": str(resume_file),
        })
        job_profile.learn_qa("Are you legally authorized to work in the US?", "Authorized to work in the US")
        check("T3a job_profile resolves both structured fields and learned screening Q&A",
              job_profile.match_field_value("LinkedIn Profile URL") == "https://linkedin.com/in/alexrivera"
              and job_profile.match_field_value("Are you legally authorized to work in the US?") == "Authorized to work in the US"
              and job_profile.match_field_value("Desired Salary") == "$185,000")

        # Intentionally click Review Application with empty fields to trigger inline validation errors
        bad_click = await browser.click("#ats-next")
        bad_delta = bad_click.get("delta") or {}
        check("T3b clicking Review with missing fields surfaces inline validation_errors in delta",
              any("Phone number is required" in e for e in (bad_delta.get("validation_errors") or [])),
              str(bad_delta))

        # Fill all fields (controlled inputs, ARIA combobox, contenteditable cover letter, checkbox)
        fill_res = await browser.fill_form([
            {"selector": "#ats-name", "value": job_profile.match_field_value("Full Name")},
            {"selector": "#ats-email", "value": job_profile.match_field_value("Email")},
            {"selector": "#ats-phone", "value": job_profile.match_field_value("Phone Number")},
            {"selector": "#ats-linkedin", "value": job_profile.match_field_value("LinkedIn Profile URL")},
            {"selector": "#ats-auth", "value": job_profile.match_field_value("Work Authorization")},
            {"selector": "#ats-salary", "value": job_profile.match_field_value("Desired Salary")},
            {"selector": "#ats-cover", "value": "Excited to build reliable autonomous agents."},
            {"selector": "#ats-agree", "value": True},
        ])
        fs = fill_res.get("filled_summary") or {}
        check("T3c fill_form fills & verifies all 8 ATS fields (incl. ARIA combobox & contenteditable) with 0 unmatched",
              fill_res.get("success") is True
              and fs.get("filled_count") == 8
              and fs.get("unmatched") == []
              and all(item.get("verified") is True for item in (fs.get("filled") or [])),
              str(fs)[:350])

        up_res = await browser.upload_file([str(resume_file)], "#ats-resume")
        check("T3d resume PDF upload lands", up_res.get("success") is True, str(up_res)[:200])

        review_click = await browser.click("#ats-next")
        rev_delta = review_click.get("delta") or {}
        check("T3e clicking Review opens Confirm Application modal and reports it in delta.modal_opened",
              any("confirm application" in m.lower() for m in (rev_delta.get("modal_opened") or [])),
              str(rev_delta))

        submit_click = await browser.click("#ats-final-submit")
        ext3 = await browser.extract_content()
        ext3_txt = ext3.get("text_preview") or ext3.get("text") or ""
        check("T3f final Submit closes modal and lands APPLICATION_SUBMITTED_OK",
              submit_click.get("success") is True
              and "APPLICATION_SUBMITTED_OK" in ext3_txt,
              ext3_txt[:240])

        # ---- TIER 4: Escalation Ladder + Checkpoints + Recipe Replay + Context Hygiene ----
        print("TIER 4: Action Escalation Ladder, Mission Checkpoints, Recipe Replay, Context Hygiene")
        # Canvas has no semantic button; Tier 1 DOM click on nonexistent text fails -> Tier 2 box lookup on #secret-canvas lands!
        box = await browser.find_element_box("#secret-canvas")
        esc = await action_ladder.browser_click_escalated(
            browser,
            "Nonexistent Semantic Label For Canvas",
            coords_hint=(box["cx"], box["cy"]) if box else None,
        )
        ext4 = await browser.extract_content()
        ext4_txt = ext4.get("text_preview") or ext4.get("text") or ""
        check("T4a action_ladder escalates from failed Tier-1 DOM click to Tier-2 coordinate click on canvas",
              esc.get("success") is True and esc.get("tier_used") == 2
              and "CANVAS_ACTIVATED_OK" in ext4_txt,
              str(esc)[:240])

        # Mission Checklist persistence & resume
        mc = mission_checkpoint.MissionChecklist("ats-demo-1", "Apply to Senior Engineer", [
            "Open ATS page", "Fill profile fields", "Upload resume", "Confirm & submit"
        ])
        mc.mark_done(1, "landed on ATS")
        mc.mark_done(2, "8/8 verified")
        mc.mark_failed(3, "timeout")
        reloaded = mission_checkpoint.MissionChecklist.load("ats-demo-1")
        check("T4b MissionChecklist persists, renders [✓]/[✗]/[ ] block, and resumes at exact failed step",
              reloaded is not None and reloaded.resume_index() == 2
              and "[✓] 1. Open ATS page" in reloaded.render_prompt_block()
              and "[✗] 3. Upload resume" in reloaded.render_prompt_block())

        # Recipe compilation & live deterministic Chrome replay!
        recipe_store.compile_recipe(
            "social-post-gauntlet", "browser",
            [
                {"action": "type", "selector": "#composer", "text": "{{post_text}}"},
                {"action": "click", "target": "#post-btn"},
            ],
            domain="127.0.0.1", tags=["social", "post"],
        )

        async def live_browser_runner(steps):
            for st in steps:
                if st["action"] == "type":
                    r = await browser.type_text(st["selector"], st["text"])
                elif st["action"] == "click":
                    r = await browser.click(st["target"])
                else:
                    r = {"success": False, "error": "unknown"}
                if not r.get("success"):
                    return r
            return {"success": True, "completed": len(steps)}

        rep = await recipe_store.replay_recipe(
            "social-post-gauntlet",
            {"post_text": "Replayed deterministically via compiled recipe!"},
            runner=live_browser_runner,
        )
        ext_rep = await browser.extract_content()
        ext_rep_txt = ext_rep.get("text_preview") or ext_rep.get("text") or ""
        check("T4c compiled recipe replays deterministically in live Chrome and updates stats",
              rep.get("success") is True and (rep.get("stats") or {}).get("successes") == 1
              and "PUBLISHED: Replayed deterministically via compiled recipe!" in ext_rep_txt,
              str(rep)[:220])

        # Context Hygiene: prune stale screenshots/DOM & dedupe overlapping tools
        msgs = [
            {"role": "tool", "content": json.dumps({"screenshot_uri": "data:image/jpeg;base64,OLD1", "markdown": "x" * 2000})},
            {"role": "tool", "content": json.dumps({"screenshot_uri": "data:image/jpeg;base64,OLD2", "markdown": "y" * 2000})},
            {"role": "tool", "content": json.dumps({"screenshot_uri": "data:image/jpeg;base64,NEW1", "delta": {"changed": True}})},
            {"role": "tool", "content": json.dumps({"screenshot_uri": "data:image/jpeg;base64,NEW2", "delta": {"changed": True}})},
        ]
        pruned = context_hygiene.prune_tool_history(msgs, keep_recent_screenshots=2)
        p0, p3 = json.loads(pruned[0]["content"]), json.loads(pruned[3]["content"])
        deduped = context_hygiene.dedupe_tool_surface([
            {"function": {"name": "browser_click"}},
            {"function": {"name": "desktop_click"}},
            {"function": {"name": "phone"}},
        ], mode="browser")
        check("T4d context_hygiene prunes stale screenshots/DOM and dedupes overlapping desktop/phone tools",
              p0["screenshot_uri"] == "[pruned stale screenshot]"
              and len(p0["markdown"]) < 700
              and p3["screenshot_uri"] == "data:image/jpeg;base64,NEW2"
              and [t["function"]["name"] for t in deduped] == ["browser_click"])

    finally:
        try:
            browser.close()
        except Exception:
            pass
        srv.shutdown()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_all()))
