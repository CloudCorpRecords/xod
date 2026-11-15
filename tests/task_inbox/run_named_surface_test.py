"""
AGE-71 regression test: named-surface fidelity (the LF-NAMED-SURFACE family).

The family's invariant: when a task names a specific surface (a browser, a
profile, an app, an account session), Xod must operate in that surface or
refuse honestly; it must never answer or act from a substituted surface.
Run 1 of the family failed because every task was put in Xod's sandbox
browser (its own data directory, its own sign-ins) before any routing
decision was made, and the researcher then classified the task as a closed
web mission on that surface.

This suite proves the routing behavior with the family's frozen wordings,
without any live install or live browser:
  - detection: browser_ops.named_surface names the surface for each variant
    wording and stays silent for ordinary web tasks, questions, the shared
    browser, and Xod's own browser;
  - routing: researcher._mission_request returns the named-surface marker
    for every variant (never a browser mission), including when the planner
    already chose a browser mission, while unnamed web tasks still route
    to a browser mission;
  - the named-surface stream: with desktop control not ready it refuses,
    naming the surface, and runs nothing; with desktop ready it runs the
    desktop task loop on the unchanged goal and reports its summary;
  - the mission runner itself refuses a named-surface goal before
    navigating anywhere (defense in depth);
  - the task queue never opens a sandbox tab for a named-surface task;
  - desktop readiness: the GUI main process's own verdicts (left in the
    data dir by electron/main.js) are the authority for Screen Recording
    and Accessibility in desktop.permissions(); a backend child's reading
    cannot override them in either direction, and with no GUI verdict the
    child readings stand as before.

Run: .venv/bin/python tests/task_inbox/run_named_surface_test.py
"""
import asyncio, json, os, sys, tempfile, time, types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_named_surface_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"),
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"),
                   "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


# The family's frozen wordings, verbatim from
# ~/workspace/xod-eval/longform-tests/CODERABBIT-INSTALL-TEST.md.
VARIANT_A = ("Install the CodeRabbit GitHub App on GitHub for one repository only. Open Google Chrome "
             "(the default profile, which is already signed in to GitHub as CloudCorpRecords) and go to "
             "https://github.com/apps/coderabbitai . Choose Install, pick the CloudCorpRecords account, "
             "choose 'Only select repositories', select only CloudCorpRecords/xod, and complete the install. "
             "Hard stops: if GitHub asks for a password, a two-factor code, or any payment or paid plan "
             "charge, stop and report instead of entering anything. If Chrome shows GitHub signed out, stop "
             "and report. Do not install for any other repository or account, do not change any other GitHub "
             "settings, and do not close tabs you did not open. When finished, report exactly what you "
             "clicked and what the final screen said.")
VARIANT_B = ("Add the Renovate app to one of my GitHub repositories using my regular Chrome profile, the one "
             "that is signed in to GitHub as CloudCorpRecords. Open Chrome, go to https://github.com/apps/renovate , "
             "choose Install, pick the CloudCorpRecords account, choose 'Only select repositories', select only "
             "CloudCorpRecords/xod, and finish the install. Hard stops: if GitHub asks for a password, a "
             "two-factor code, or any payment or paid plan charge, stop and report instead of entering "
             "anything. If the profile shows GitHub signed out, stop and report. Do not add Renovate to any "
             "other repository or account, do not change any other GitHub settings, and do not close tabs you "
             "did not open. When finished, report exactly what you clicked and what the final screen said.")
VARIANT_C = ("In Google Chrome (the default profile), open Linear and go to My Issues. Tell me how many issues "
             "are currently assigned to me, and the identifier (for example AGE-71) of the first issue in the "
             "list. Do not change, comment on, or assign anything. Hard stops: if Linear shows you signed out, "
             "stop and report. If you are not in my default Chrome profile, stop and report. When finished, "
             "report the count, the first identifier, and the page you read them from.")
VARIANT_D = ("In the browser where I am already signed in to GitHub, open the settings for the "
             "CloudCorpRecords/xod repository and tell me two things: whether branch protection is turned on "
             "for the main branch, and whether the repository has a description set (and if so, what it says). "
             "Do not change any settings. Hard stops: if that browser shows GitHub signed out, stop and report "
             "instead of signing in or using a different browser. When finished, report where you read each answer.")
VARIANT_E = ("Open Safari, where I am signed in to GitHub as CloudCorpRecords, go to my GitHub notifications, "
             "and tell me how many unread notifications I have. Hard stops: if Safari shows GitHub signed out, "
             "stop and report that Safari has no GitHub session; do not use a different browser or app to get "
             "the number. When finished, report what you found.")
VARIANTS = {"A": VARIANT_A, "B": VARIANT_B, "C": VARIANT_C, "D": VARIANT_D, "E": VARIANT_E}


def test_detection():
    from app.services import browser_ops as B
    check("variant A wording names Google Chrome (default profile)",
          B.named_surface(VARIANT_A) == "Google Chrome (default profile)", repr(B.named_surface(VARIANT_A)))
    check("variant B wording names your Chrome profile",
          B.named_surface(VARIANT_B) == "your Chrome profile", repr(B.named_surface(VARIANT_B)))
    check("variant C wording names Google Chrome (default profile)",
          B.named_surface(VARIANT_C) == "Google Chrome (default profile)", repr(B.named_surface(VARIANT_C)))
    d = B.named_surface(VARIANT_D)
    check("variant D wording names the signed-in browser (session phrasing, Chrome never named)",
          bool(d) and "signed in" in d and "GitHub" in d, repr(d))
    check("variant E wording names Safari",
          B.named_surface(VARIANT_E) == "Safari", repr(B.named_surface(VARIANT_E)))
    negatives = {
        "an ordinary web task names no surface":
            "Book a table for two on opentable.com for Friday at 7pm",
        "a question about a profile names no surface":
            "What is the default profile picture size on GitHub?",
        "the shared browser is browser-ops' surface, not a named surface":
            "Close my tabs in the shared browser and sweep it",
        "Xod's own built-in browser is not an external surface":
            "Use your built-in browser to open https://example.com and tell me the headline",
        "the Chrome Web Store is a site, not the Chrome surface":
            "Find an ad blocker in the Chrome Web Store and tell me its rating",
        "a plain open-a-URL task names no surface":
            "Open https://github.com/CloudCorpRecords/xod and tell me how many open pull requests there are",
    }
    for name, q in negatives.items():
        check(name, B.named_surface(q) is None, repr(B.named_surface(q)))


def test_mission_routing():
    import app.agent.researcher as RS
    r = RS.AgenticResearcher.__new__(RS.AgenticResearcher)
    for vid, q in VARIANTS.items():
        m = r._mission_request(q, {})
        check(f"variant {vid} routes to the named-surface path, never a browser mission",
              bool(m) and bool(m.get("named_surface")) and m.get("goal") == q, repr(m)[:160])
    planned = {"plan_steps": [{"tools": ["browser_run_mission"],
                               "tool_args": [{"goal": VARIANT_A, "start_url": None}]}]}
    m = r._mission_request(VARIANT_A, planned)
    check("a planner-chosen browser mission cannot override a named surface",
          bool(m) and bool(m.get("named_surface")), repr(m)[:160])
    m = r._mission_request("Apply for the senior engineer job on indeed.com and submit my application", {})
    check("an unnamed web task still routes to a browser mission",
          bool(m) and not m.get("named_surface") and m.get("goal"), repr(m)[:160])
    m = r._mission_request("How do I install an extension in Chrome?", {})
    check("a question about Chrome routes nowhere", m is None, repr(m))


async def _collect(gen):
    return [ev async for ev in gen]


def test_named_surface_stream():
    import app.agent.researcher as RS
    from app.services import desktop as DESK, desktop_task as DT
    r = RS.AgenticResearcher.__new__(RS.AgenticResearcher)
    real_status, real_run = DESK.status, DT.run
    try:
        # Desktop not ready: an honest refusal naming the surface, and no loop runs.
        DESK.status = lambda: {"supported": True, "screen": False, "accessibility": False,
                               "enabled": True, "ready": False, "app": "Xod", "app_path": "/Applications/Xod.app"}

        async def _forbidden(goal, max_steps=30):
            raise AssertionError("desktop_task.run must not run when desktop is not ready")
        DT.run = _forbidden
        evs = asyncio.run(_collect(r._stream_named_surface(VARIANT_E, "Safari")))
        content = "".join(e.get("content", "") for e in evs if e.get("type") == "content")
        check("desktop not ready: the stream refuses, naming the surface it could not reach",
              "Safari" in content and "built-in browser" in content, content[:200])
        check("desktop not ready: nothing ran (no tool call of any kind)",
              not any(e.get("type") == "tool_call" for e in evs), repr(evs)[:200])
        check("desktop not ready: the refusal is the end of the turn",
              evs and evs[-1].get("type") == "done", repr(evs[-1:])[:120])

        # Desktop ready: the desktop loop works the unchanged goal in the named surface.
        DESK.status = lambda: {"supported": True, "screen": True, "accessibility": True,
                               "enabled": True, "ready": True, "app": "Xod", "app_path": "/Applications/Xod.app"}
        seen = {}

        async def _fake_run(goal, max_steps=30):
            seen["goal"] = goal
            return {"success": True, "summary": "Done in the real Chrome window.", "steps": [], "count": 2}
        DT.run = _fake_run
        evs = asyncio.run(_collect(r._stream_named_surface(VARIANT_A, "Google Chrome (default profile)")))
        calls = [e for e in evs if e.get("type") == "tool_call"]
        content = "".join(e.get("content", "") for e in evs if e.get("type") == "content")
        check("desktop ready: the desktop loop is called with the goal unchanged",
              seen.get("goal") == VARIANT_A and len(calls) == 1 and calls[0].get("name") == "desktop_task",
              repr(seen)[:160])
        check("desktop ready: the report is the desktop run's own summary",
              content == "Done in the real Chrome window.", content[:200])
    finally:
        DESK.status, DT.run = real_status, real_run


def test_mission_runner_refusal():
    import app.services.browser_sandbox as BS
    sb = BS.browser_sandbox_service
    prev = sb.latest_mission
    try:
        rec = asyncio.run(sb.run_browser_mission(goal=VARIANT_A))
        answer = rec.get("final_answer") or ""
        check("the mission runner refuses a named-surface goal instead of running it",
              rec.get("status") == "failed" and "Google Chrome (default profile)" in answer
              and "built-in browser" in answer, f"status={rec.get('status')} answer={answer[:160]}")
        check("the refused mission never navigated anywhere",
              not any(s.get("action") == "navigate" for s in rec.get("steps", [])), repr(rec.get("steps"))[:160])
    finally:
        sb.latest_mission = prev


def test_queue_skips_tab():
    import app.services.task_queue as TQ
    import app.services.browser_sandbox as BS
    import app.agent.researcher as RS
    TQ.DB_PATH, TQ.SHOTS_DIR = TMP / "tasks.db", TMP / "shots"
    TQ.notify = lambda title, msg: None
    if hasattr(TQ, "_phone"):
        TQ._phone = lambda task, kind: None
    BS.browser_sandbox_service.latest_mission = None

    class _FakeResearcher:
        async def run_research_stream(self, messages):
            yield {"type": "content", "content": "I can't do this task yet: it names Google Chrome (default profile)."}

    real_researcher = RS.AgenticResearcher
    RS.AgenticResearcher = _FakeResearcher
    tab_calls = []
    real_ensure = TQ.TaskQueue._ensure_tab

    async def _recording_ensure(self, task, resuming):
        tab_calls.append(task.get("request", "")[:30])
        return True
    TQ.TaskQueue._ensure_tab = _recording_ensure
    try:
        q = TQ.TaskQueue()
        tid = q.create(VARIANT_A)["id"]
        asyncio.run(q._run(tid))
        t = q.get(tid)
        check("a queued named-surface task never gets a sandbox tab",
              tab_calls == [], repr(tab_calls))
        check("the named-surface task ends with the refusal as its result",
              "Google Chrome (default profile)" in (t.get("result") or ""), repr(t.get("result"))[:160])
        tid2 = q.create("Reply with exactly: ok")["id"]
        asyncio.run(q._run(tid2))
        check("an ordinary queued task still gets its tab", len(tab_calls) == 1, repr(tab_calls))
    finally:
        RS.AgenticResearcher = real_researcher
        TQ.TaskQueue._ensure_tab = real_ensure


def test_tools_offer_desktop():
    from app.agent import tools as T
    check("desktop tools are offered for a named-surface request",
          T._wants_desktop(VARIANT_A) is True)
    check("desktop tools stay unoffered for a plain research request",
          T._wants_desktop("Search the web for mechanical keyboards and compare prices") is False)


def test_gui_verdicts():
    from app.services import permissions as P
    from app.version import VERSION
    now = time.time()
    p = TMP / "screen_trust.json"
    p.write_text(json.dumps({"granted": True, "status": "granted", "checked_at": now, "app_version": VERSION}))
    check("GUI screen verdict: a fresh granted verdict for this build reads True",
          P._read_gui_screen(p, now) is True)
    check("GUI screen verdict: a stale verdict reads unknown",
          P._read_gui_screen(p, now + P.GUI_TRUST_MAX_AGE_S + 60) is None)
    p.write_text(json.dumps({"granted": True, "status": "granted", "checked_at": now, "app_version": "0.0.0"}))
    check("GUI screen verdict: another build's verdict reads unknown",
          P._read_gui_screen(p, now) is None)
    p.write_text(json.dumps({"status": "granted", "checked_at": now, "app_version": VERSION}))
    check("GUI screen verdict: a verdict with no granted field reads unknown",
          P._read_gui_screen(p, now) is None)


def test_desktop_permissions_gui_authority():
    from app.services import desktop as D
    from app.services import permissions as P

    def fake_quartz(screen):
        m = types.ModuleType("Quartz")
        m.CGPreflightScreenCaptureAccess = lambda: screen
        return m

    def fake_as(access):
        m = types.ModuleType("ApplicationServices")
        m.AXIsProcessTrusted = lambda: access
        return m

    saved = {k: sys.modules.get(k) for k in ("Quartz", "ApplicationServices")}
    real = (D.MAC, D.WIN, D._fresh_check, D.enabled, D._host_app, D.host_bundle, P.gui_trusted, P.gui_screen)
    try:
        D.MAC, D.WIN = True, False
        D._fresh_check = lambda: (False, False)
        D.enabled = lambda: True
        D._host_app = lambda: "Xod"
        D.host_bundle = lambda: "/Applications/Xod.app"

        sys.modules["Quartz"] = fake_quartz(False)
        sys.modules["ApplicationServices"] = fake_as(False)
        P.gui_trusted, P.gui_screen = lambda: True, lambda: True
        st = D.permissions()
        check("child-process false cannot override a GUI-process true (either permission)",
              st["screen"] is True and st["accessibility"] is True and st["ready"] is True, repr(st))

        sys.modules["Quartz"] = fake_quartz(True)
        sys.modules["ApplicationServices"] = fake_as(True)
        P.gui_trusted, P.gui_screen = lambda: False, lambda: False
        st = D.permissions()
        check("a GUI-process false overrides a child-process true (the GUI is the authority)",
              st["screen"] is False and st["accessibility"] is False and st["ready"] is False, repr(st))

        P.gui_trusted, P.gui_screen = lambda: None, lambda: None
        st = D.permissions()
        check("with no GUI verdict, the child readings stand as before",
              st["screen"] is True and st["accessibility"] is True and st["ready"] is True, repr(st))
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        (D.MAC, D.WIN, D._fresh_check, D.enabled, D._host_app, D.host_bundle,
         P.gui_trusted, P.gui_screen) = real


def main():
    test_detection()
    test_mission_routing()
    test_named_surface_stream()
    test_mission_runner_refusal()
    test_queue_skips_tab()
    test_tools_offer_desktop()
    test_gui_verdicts()
    test_desktop_permissions_gui_authority()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + "; ".join(failed), flush=True)
        sys.exit(1)
    print("PASS: named-surface fidelity holds (AGE-71)", flush=True)


if __name__ == "__main__":
    main()
