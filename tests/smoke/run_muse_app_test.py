"""
The Muse app watcher (services/muse_app.py): approvals are found by walking the Muse app's
Accessibility tree in-process (pyobjc), not by driving System Events through osascript — that took
~25s a scan on a real Mac and the watcher timed out at 15s, so it could never approve anything.
Here the AX API is faked with a small element tree: no real Muse app, nothing is pressed.
  .venv/bin/python tests/smoke/run_muse_app_test.py
"""
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-museapp-"))
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


class El:
    def __init__(self, role, label="", children=()):
        self.role, self.label, self.children = role, label, list(children)
        self.pressed = False


class FakeAX:
    """The slice of ApplicationServices muse_app uses, over El trees."""
    def __init__(self, root, trusted=True):
        self.root, self._trusted = root, trusted

    def trusted(self):
        return self._trusted

    def create_app(self, pid):
        return self.root

    def attr(self, el, name):
        if name == "AXParent":
            return getattr(el, "parent", None)
        if name == "AXRole":
            return el.role
        if name in ("AXTitle", "AXDescription", "AXValue"):
            return el.label
        if name == "AXChildren":
            return el.children
        return None

    def perform(self, el, action):
        el.pressed = (action == "AXPress")
        el.presses = getattr(el, "presses", 0) + (1 if el.pressed else 0)
        return 0 if el.pressed else -1


def main():
    from app.services import muse_app
    from app.services.storage import set_setting
    set_setting(muse_app.SETTING, "on")
    muse_app._bring_forward = lambda: None      # never move a real app's window from a test
    muse_app._put_back = lambda prev: None

    print("M1 in-process AX walk (no osascript)")
    allow = El("AXButton", "Allow")
    card = El("AXGroup", "Approval request for Browser", [
        El("AXStaticText", "Allow Tree to upload this file using Browser?"),
        El("AXStaticText", "Tree wants to upload a file to jobs.lever.co."),
        allow, El("AXButton", "Deny")])
    tree = El("AXApplication", "Muse", [El("AXWindow", "Muse", [El("AXGroup", "", [card])])])
    muse_app._ax_api = lambda: FakeAX(tree)
    muse_app._muse_pid = lambda: 4242
    seen = muse_app._scan(False)
    check("a pending card is found by reading the tree", seen == {"running": True, "approved": [
        {"title": "Approval request for Browser",
         "what": "Allow Tree to upload this file using Browser? Tree wants to upload a file to jobs.lever.co.",
         "pressed": False, "button": "Allow", "reason": "", "type": "Browser",
         "from": "", "to": "", "subject": "", "attachments": []}]}, str(seen))
    check("reading does not press", allow.pressed is False)
    pressed = muse_app._scan(True)
    check("pressing uses AXPress on the Allow button", pressed["approved"][0]["pressed"] is True and allow.pressed)
    check("watcher no longer shells out to osascript", '"osascript"' not in Path(muse_app.__file__).read_text())

    muse_app._muse_pid = lambda: None
    check("Muse app not running is not an error", muse_app._scan(True) == {"running": False, "approved": []})

    muse_app._ax_api = lambda: FakeAX(tree, trusted=False)
    muse_app._muse_pid = lambda: 4242
    try:
        muse_app._scan(True)
        raised = ""
    except Exception as e:
        raised = str(e)
    check("no Accessibility access says so", "accessibility" in raised.lower(), raised)

    print("M2 the error shown is the real reason (not always Accessibility)")
    real = muse_app._describe_error(RuntimeError("Muse app scan timed out after 10s"))
    check("a timeout shows the timeout", "timed out" in real and "needs Accessibility" not in real, real)
    noax = muse_app._describe_error(PermissionError("Xod needs Accessibility access to press Allow"))
    check("a permission failure shows the Accessibility fix", "Accessibility" in noax, noax)

    print("M4 recipient/site Always allow, never unscoped permission or Deny")
    send, always, dont = El("AXButton", "Send"), El("AXButton", "Always allow for this recipient"), El("AXButton", "Don't send")
    gmail = El("AXGroup", "Approval request for Gmail", [
        El("AXStaticText", "From: Tree <tree@example.com>"), El("AXStaticText", "To: hiring@example.com"),
        El("AXStaticText", "Subject: Your application"), El("AXStaticText", "Attachments: cv.pdf"),
        send, always, dont])
    muse_app._ax_api = lambda: FakeAX(El("AXApplication", "Muse", [El("AXWindow", "Muse", [gmail])]))
    muse_app._muse_pid = lambda: 4242
    got = muse_app._scan(True)["approved"][0]
    check("a Gmail card: scoped Always allow is preferred to Send",
          got["button"] == "Always allow for this recipient" and always.pressed and not send.pressed and not dont.pressed, str(got))
    check("…its type and fields are read", got["type"] == "Gmail" and got["to"] == "hiring@example.com"
          and got["subject"] == "Your application" and got["attachments"] == ["cv.pdf"], str(got))
    send2, dont2 = El("AXButton", "Send"), El("AXButton", "Don't send")
    gmail2 = El("AXGroup", "Approval request for Gmail", [El("AXStaticText", "To: a@b.co"), send2, dont2])
    muse_app._ax_api = lambda: FakeAX(El("AXApplication", "Muse", [El("AXWindow", "Muse", [gmail2])]))
    got = muse_app._scan(True)["approved"][0]
    check("a Gmail card with only Send: Send is pressed", got["button"] == "Send" and send2.pressed and not dont2.pressed, str(got))
    deny = El("AXButton", "Deny")
    card3 = El("AXGroup", "Approval request for Browser", [El("AXStaticText", "Allow a download?"), deny])
    muse_app._ax_api = lambda: FakeAX(El("AXApplication", "Muse", [El("AXWindow", "Muse", [card3])]))
    got = muse_app._scan(True)["approved"][0]
    check("a card with only Deny: nothing pressed, and the reason says so",
          got["pressed"] is False and got["button"] is None and "Deny" in got["reason"] and not deny.pressed, str(got))
    check("button choice in order, never the negative ones",
           muse_app._pick_button(["Send", "Always allow for this recipient", "Don't send"]) == "Always allow for this recipient"
          and muse_app._pick_button(["Allow", "Send"]) == "Send"
          and muse_app._pick_button(["Deny", "Don't send"]) is None
          and muse_app._pick_button(["Always allow everything", "Allow"]) == "Allow")
    check("global permanent grants lose to one-time approval",
          muse_app._pick_button(["Always allow for all sites", "Allow once"]) == "Allow once"
          and muse_app._pick_button(["Always allow for every recipient", "Send"]) == "Send"
          and muse_app._pick_button(["Always allow for this site and all recipients"]) is None)
    check("action cards: the primary verb wins over the body text and the Don't twin",
          muse_app._pick_button(["Tree wants to send a Gmail reply with your nudge message. "
                                 "This task comes from the side chat “Join GitHub organizations”. Hide details",
                                 "Reply", "Don't reply"]) == "Reply"
          and muse_app._pick_button(["Tree wants to create a calendar event and send an invitation "
                                     "to Johnny Oz to set up a meeting. Hide details",
                                     "Create", "Don't create"]) == "Create"
          and muse_app._pick_button(["Pay $25", "Don't pay"]) == "Pay $25"
          and muse_app._pick_button(["Sign", "Don't sign"]) == "Sign"
          and muse_app._pick_button(["Continue", "Cancel"]) == "Continue")
    check("action cards: held only when there is no real action, or two unranked actions compete",
          muse_app._pick_button(["Tree wants to do a thing. Hide details", "Don't do it"]) is None
          and muse_app._pick_button(["Frobnicate", "Zangle"]) is None
          and muse_app._pick_button(["Hide details", "Don't reply"]) is None)
    body_btn = El("AXButton", "Tree wants to send a Gmail reply with your nudge message. Hide details")
    reply_btn, dont_btn = El("AXButton", "Reply"), El("AXButton", "Don't reply")
    gmail3 = El("AXGroup", "Approval request for Gmail",
                [El("AXStaticText", "Allow Tree to send this email in Gmail?"), body_btn, reply_btn, dont_btn])
    muse_app._ax_api = lambda: FakeAX(El("AXApplication", "Muse", [El("AXWindow", "Muse", [gmail3])]))
    got = muse_app._scan(True)["approved"][0]
    check("a Gmail action card: Reply is pressed, not the body text or Don't reply",
          got["button"] == "Reply" and reply_btn.pressed and not dont_btn.pressed and not body_btn.pressed, str(got))

    allow2 = El("AXButton", "Allow")
    card4 = El("AXGroup", "Approval request for Browser", [El("AXStaticText", "Allow an upload?"), allow2])
    muse_app._ax_api = lambda: FakeAX(El("AXApplication", "Muse", [El("AXWindow", "Muse", [card4])]))
    muse_app.DRY_RUN = True
    got = muse_app._scan(True)["approved"][0]
    muse_app.DRY_RUN = False
    check("dry run (XOD_MUSE_APP_DRY_RUN): reads the card, presses nothing, says why",
          got["pressed"] is False and "dry run" in got["reason"] and not allow2.pressed, str(got))

    print("M5 the morning-after log: fields, one row ever, summary counts")
    from app.services import agent_log
    card = {"title": "Approval request for Gmail", "type": "Gmail", "button": "Always allow for this recipient",
            "pressed": True, "reason": "", "from": "tree@example.com", "to": "hiring@example.com",
            "subject": "Your application", "attachments": ["cv.pdf"],
            "what": "From: tree@example.com To: hiring@example.com Subject: Your application Attachments: cv.pdf"}
    check("a pressed Gmail card is logged once", muse_app._log(card) is True)
    muse_app._state["present"] = set()  # a restart forgets the in-memory set; the DB must still say no
    check("…and not again after a restart", muse_app._log(card) is False)
    rows = [r for r in agent_log.approvals(50) if r["kind"] == "muse_app"]
    check("the row holds type, button, To, Subject and the full text",
          len(rows) == 1 and "type: Gmail" in rows[0]["detail"] and "button: Always allow for this recipient" in rows[0]["detail"]
          and "to: hiring@example.com" in rows[0]["detail"] and "subject: Your application" in rows[0]["detail"]
          and "cv.pdf" in rows[0]["detail"] and rows[0]["decision"] == "allowed by Xod", str(rows)[:400])
    muse_app._log({"title": "Approval request for Browser", "type": "Browser", "button": None, "pressed": False,
                   "reason": "only a Deny button", "what": "Allow a download from example.com?"})
    s = muse_app.build_summary(0)
    check("the summary counts by type, lists always-allowed and the not-pressed",
          s["count"] == 1 and s["by_type"] == {"Gmail": 1} and s["always_allowed"] == ["hiring@example.com"]
          and any("Browser" in n and "Deny" in n for n in s["not_pressed"]), str(s))

    print("M6 the observer: bursts become one scan, off stops it, a new pid re-attaches")
    import time as _time
    hits = []
    deb = muse_app._Debouncer(0.05, lambda: hits.append(1))
    for _ in range(10):
        deb.call()
    _time.sleep(0.2)
    check("10 events in a burst -> one scan", hits == [1], str(hits))
    made = []

    class FakeObs:
        def __init__(self, pid):
            self.pid, self.stopped = pid, False
        def stop(self):
            self.stopped = True
    muse_app._observer_factory = lambda pid, on_event: made.append(FakeObs(pid)) or made[-1]
    muse_app.set_enabled(True)  # the observer only runs while the switch is on (no loop here, so no watcher starts)
    muse_app._sync_observer(111)
    muse_app._sync_observer(111)
    check("same pid: observer attached once", len(made) == 1 and muse_app._state["observer_pid"] == 111)
    muse_app._sync_observer(222)
    check("Muse restarts: old observer stopped, new one attached",
          len(made) == 2 and made[0].stopped and muse_app._state["observer_pid"] == 222)
    muse_app._sync_observer(None)
    check("switch off / Muse gone: observer stopped", made[1].stopped and muse_app._state.get("observer") is None)

    print("M7 never twice: an already-pressed card, and two scans at once")
    allow3 = El("AXButton", "Allow")
    card5 = El("AXGroup", "Approval request for Browser", [El("AXStaticText", "Allow Tree to upload notes.txt?"), allow3])
    muse_app._ax_api = lambda: FakeAX(El("AXApplication", "Muse", [El("AXWindow", "Muse", [card5])]))
    muse_app._muse_pid = lambda: 4242
    first = muse_app._scan(True)["approved"][0]
    again = muse_app._scan(True, skip_ids={muse_app._card_id(first)})["approved"][0]
    check("a card still on screen after being pressed is not pressed again",
          again["pressed"] is False and "already pressed" in again["reason"] and allow3.presses == 1, str(again))

    import asyncio as _asyncio

    async def concurrent():
        calls, active = [], {"n": 0, "max": 0}
        real = muse_app._scan

        def slow(press, skip_ids=None):
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            calls.append(1)
            import time as _t
            _t.sleep(0.3)
            active["n"] -= 1
            return {"running": True, "approved": []}
        muse_app._scan = slow
        try:
            await _asyncio.gather(muse_app.scan_once(), muse_app.scan_once())
        finally:
            muse_app._scan = real
        return calls, active["max"]
    calls, max_active = _asyncio.run(concurrent())
    check("two scans starting together: never at once, and one follow-up scan is queued",
          len(calls) == 2 and max_active == 1, f"calls={len(calls)} max_active={max_active}")

    allow4 = El("AXButton", "Allow")
    card6 = El("AXGroup", "Approval request for Browser", [El("AXStaticText", "Allow Tree to upload notes2.txt?"), allow4])
    with_card = El("AXApplication", "Muse", [El("AXWindow", "Muse", [card6])])
    without_card = El("AXApplication", "Muse", [El("AXWindow", "Muse", [])])

    async def sequence():
        muse_app._ax_api = lambda: FakeAX(with_card)
        await muse_app.scan_once()
        await muse_app.scan_once()          # still on screen: must not press again
        muse_app._ax_api = lambda: FakeAX(without_card)
        await muse_app.scan_once()          # it went away
        muse_app._ax_api = lambda: FakeAX(with_card)
        await muse_app.scan_once()          # an identical card back again: a new card, press it
    _asyncio.run(sequence())
    check("an identical card that went away and came back is pressed again (but never while it sits there)",
          allow4.presses == 2, f"presses={allow4.presses}")

    print("M8 blockers: 'Allow all once' group cards, and a card still showing gets pressed again")
    check("button order with the group buttons",
          muse_app._pick_button(["Allow", "Allow all once", "Deny all"]) == "Allow all once"
           and muse_app._pick_button(["Allow all once", "Always allow for this recipient"]) == "Always allow for this recipient"
          and muse_app._pick_button(["Deny all", "Allow all once"]) == "Allow all once")
    check("'Allow once' (the one-time button on network/share cards) is pressed too",
          muse_app._pick_button(["Allow once", "Deny"]) == "Allow once"
          and muse_app._pick_button(["Send", "Allow once"]) == "Send")
    allow_all, deny_all = El("AXButton", "Allow all once"), El("AXButton", "Deny all")
    item = El("AXGroup", "", [El("AXStaticText", "Upload 1 control file src/index.ts in Browser"),
                              El("AXButton", "Allow"), El("AXButton", "Deny")])
    item2 = El("AXGroup", "", [El("AXStaticText", "Upload 2 control file README.md in Browser"),
                               El("AXButton", "Allow"), El("AXButton", "Deny")])
    group = El("AXGroup", "Approval requests including Browser", [
        El("AXStaticText", "Tree needs approval to continue."), item, item2, allow_all, deny_all])
    muse_app._ax_api = lambda: FakeAX(El("AXApplication", "Muse", [El("AXWindow", "Muse", [group])]))
    muse_app._muse_pid = lambda: 4242
    got = muse_app._scan(True)["approved"][0]
    check("a group card: 'Allow all once' is pressed, 'Deny all' never is, and the items are read",
          got["button"] == "Allow all once" and allow_all.pressed and not deny_all.pressed
          and "src/index.ts" in got["what"] and "README.md" in got["what"], str(got)[:200])

    allow5 = El("AXButton", "Allow")
    card7 = El("AXGroup", "Approval request for Browser", [El("AXStaticText", "Allow Tree to upload stuck.txt?"), allow5])
    tree7 = El("AXApplication", "Muse", [El("AXWindow", "Muse", [card7])])
    muse_app._ax_api = lambda: FakeAX(tree7)

    async def stuck():
        muse_app.REPRESS_AFTER = 0.2
        try:
            await muse_app.scan_once()      # press 1
            await muse_app.scan_once()      # still showing, just pressed: no second press yet
            n1 = allow5.presses
            await _asyncio.sleep(0.25)
            await muse_app.scan_once()      # still showing after the wait: a new showing, press 2
            await _asyncio.sleep(0.25)
            await muse_app.scan_once()      # press 3
            await _asyncio.sleep(0.25)
            await muse_app.scan_once()      # cap reached: no press, logged press-failed
            return n1
        finally:
            muse_app.REPRESS_AFTER = 4.0
    n1 = _asyncio.run(stuck())
    stuck_rows = [r for r in agent_log.approvals(80) if r["id"].endswith("-stuck")]
    check("a card still showing after a press is not repeated and is logged for review",
          n1 == 1 and allow5.presses == 1 and stuck_rows and "confirmation missing" in stuck_rows[0]["decision"],
          f"presses={allow5.presses} stuck={stuck_rows[:1]}")

    print("M9 a trigger looks three times (cards build in stages), never on a timer of its own")
    muse_app.set_enabled(True)

    async def burst_check():
        import asyncio as _a
        calls = []
        real_scan, real_delays = muse_app._run_scan, muse_app.BURST_DELAYS

        async def fake_scan():
            calls.append(1)
        muse_app._run_scan, muse_app.BURST_DELAYS = fake_scan, (0.05, 0.12, 0.2)
        muse_app._state["loop"] = _a.get_running_loop()
        try:
            muse_app._schedule_burst()
            await _a.sleep(0.35)
        finally:
            muse_app._run_scan, muse_app.BURST_DELAYS = real_scan, real_delays
            muse_app._state["loop"] = None
        return calls
    burst_calls = _asyncio.run(burst_check())
    check("one trigger -> looks at 0.25s, 1s and 2.5s (here scaled down), so a late-rendering button is caught",
          len(burst_calls) == 3, f"looks={len(burst_calls)}")

    print("M10 buttons that live just outside the card group (the side-panel Gmail card)")
    send_out = El("AXButton", "Send")
    card8 = El("AXGroup", "Approval request for Gmail", [
        El("AXStaticText", "Allow Tree to send this email in Gmail?"),
        El("AXStaticText", "To: jdunn@webucator.com Subject: References")])
    panel = El("AXGroup", "", [card8, El("AXGroup", "", [send_out, El("AXButton", "Don't send")])])
    card8.parent = panel
    muse_app._ax_api = lambda: FakeAX(El("AXApplication", "Muse", [El("AXWindow", "Muse", [panel])]))
    muse_app._muse_pid = lambda: 4242
    got = muse_app._scan(True)["approved"][0]
    check("no button inside the card: Xod looks in the card's panel and presses Send there",
          got["button"] == "Send" and send_out.pressed, str(got)[:200])

    print("M11 keep-awake + lock state, and the Windows UIA backend over a fake tree")
    from app.services import power, muse_backends
    class FakeAwake(power._AwakeBackend):
        def __init__(self): super().__init__(); self.starts = 0; self.stops = 0
        def start(self): self.starts += 1; self.held = True; return True
        def stop(self): self.stops += 1; self.held = False
    fake_awake = FakeAwake()
    power._state["backend"], saved_reasons = fake_awake, dict(power._state["reasons"])
    power._state["reasons"] = {}
    power.acquire("auto-approve"); power.acquire("auto-approve"); power.acquire("crew job")
    check("keep-awake: held once for the first reason, reasons listed", fake_awake.starts == 1
          and power.status()["awake_held"] and power.held_reasons() == ["auto-approve", "crew job"], str(power.status()))
    power.release("auto-approve")
    check("keep-awake: still held while a Crew job runs", fake_awake.stops == 0 and power.status()["awake_held"])
    power.ensure("crew job", False)
    check("keep-awake: released when idle", fake_awake.stops == 1 and not power.status()["awake_held"])
    power.acquire("auto-approve")
    held_status = muse_app.status()
    check("watcher status reports power's real awake state while held (never None)",
          held_status.get("awake") is True and held_status.get("awake_held") is True, str(held_status))
    power.release("auto-approve")

    class FailingAwake(power._AwakeBackend):
        def start(self): return False
    power._state["backend"] = FailingAwake()
    power.acquire("auto-approve")
    check("power status is the backend's real state (a failed assertion is not held)",
          power.status()["awake_held"] is False, str(power.status()))
    power.release("auto-approve")
    power._state["backend"], power._state["reasons"] = None, saved_reasons
    locked = power.is_locked()
    check("lock state is known on desktop or explicitly unknown on Linux",
          locked in (True, False) if sys.platform != "linux" else locked is None, repr(locked))
    st = muse_app.status()
    check("watcher status carries backend, awake and locked", st.get("backend") in ("mac_ax", "win_uia", None)
          and "awake_held" in st and "locked" in st, str(st))

    class Raw:
        def __init__(self, name, ctype, kids=()):
            self.CurrentName, self.CurrentControlType, self.kids = name, ctype, list(kids)
            self.invoked = False
        def GetCurrentPattern(self, pid_):
            return self if pid_ == muse_backends.UIA_InvokePattern else None
        def QueryInterface(self, iface):
            return self
        def Invoke(self):
            self.invoked = True
    class Walker:
        def GetFirstChildElement(self, raw): return raw.kids[0] if raw.kids else None
        def GetNextSiblingElement(self, raw): return getattr(raw, "next_sib", None)
        def GetParentElement(self, raw): return getattr(raw, "parent_raw", None)
    allow_raw = Raw("Allow", 50000)
    card_raw = Raw("Approval request for Browser", 50026, [Raw("Allow an upload?", 50020), allow_raw])
    root_raw = Raw("Muse", 50032, [card_raw])
    for parent in (root_raw, card_raw):
        for i, k in enumerate(parent.kids):
            k.parent_raw = parent
            k.next_sib = parent.kids[i + 1] if i + 1 < len(parent.kids) else None
    win = muse_backends.WinAX(automation=object(), walker=Walker(), app_root=root_raw)
    check("Windows backend is picked on win32, Mac backend here", muse_backends.backend("win32").name == "win_uia"
          and muse_backends.backend("darwin").name == "mac_ax" and muse_backends.backend("linux") is None)
    saved_ax, saved_pid = muse_app._ax_api, muse_app._muse_pid
    muse_app._ax_api, muse_app._muse_pid = lambda: win, lambda: 777
    got = muse_app._scan(True)["approved"][0]
    muse_app._ax_api, muse_app._muse_pid = saved_ax, saved_pid
    check("the neutral core presses Allow on a fake Windows UIA tree (InvokePattern)",
          got["button"] == "Allow" and got["pressed"] and allow_raw.invoked, str(got)[:200])

    print("M12 a card Muse only answers with its window in front (the screenshot cards)")
    fronts = []
    muse_app._bring_forward = lambda: fronts.append("forward") or 99
    muse_app._put_back = lambda prev: fronts.append(("back", prev))
    muse_app.FRONT_SETTLE = 0.01
    expired = El("AXGroup", "Approval request for Take screenshot", [
        El("AXStaticText", "This approval has expired. Allow Tree to take a screenshot?"), El("AXButton", "Allow")])
    muse_app._ax_api = lambda: FakeAX(El("AXApplication", "Muse", [El("AXWindow", "Muse", [expired])]))
    got = muse_app._scan(True)["approved"][0]
    check("an expired card isn't pressed, and says it expired", not got["pressed"] and "expired" in got["reason"]
          and not expired.children[1].pressed, str(got))

    allow8 = El("AXButton", "Allow")
    card8 = El("AXGroup", "Approval request for Take screenshot", [El("AXStaticText", "Allow Tree to take a screenshot of your screen?"), allow8])
    muse_app._ax_api = lambda: FakeAX(El("AXApplication", "Muse", [El("AXWindow", "Muse", [card8])]))

    async def repress():
        muse_app.REPRESS_AFTER = 0.05
        try:
            await muse_app.scan_once()       # press 1, Muse left where it was
            n0 = list(fronts)
            await _asyncio.sleep(0.1)
            await muse_app.scan_once()       # still showing: Muse comes forward for press 2, then goes back
            return n0
        finally:
            muse_app.REPRESS_AFTER = 4.0
    n0 = _asyncio.run(repress())
    check("a successful press still visible is not blindly repeated",
          n0 == [] and fronts == [] and allow8.presses == 1, f"{n0} {fronts} {allow8.presses}")

    fronts.clear()
    hidden_card = El("AXGroup", "Approval request for Threads Messages", [
        El("AXStaticText", "Allow Tree to send this message on Threads?"), El("AXButton", "Hide details")])
    allow9 = El("AXButton", "Allow")
    real_forward = muse_app._bring_forward
    muse_app._bring_forward = lambda: (fronts.append("forward"), hidden_card.children.append(allow9))[0] or 99
    muse_app._ax_api = lambda: FakeAX(El("AXApplication", "Muse", [El("AXWindow", "Muse", [hidden_card])]))
    before = len(agent_log.approvals(500))
    cards = _asyncio.run(muse_app.scan_once())
    new_rows = agent_log.approvals(500)[:len(agent_log.approvals(500)) - before]
    # The card's own history row is what "logged once" counts. The fake tree
    # never removes the pressed card, so the outcome re-read honestly reports
    # "card still showing" and the escalation feature adds its own separate
    # muse_escalation row next to it (covered in run_muse_review_test.py).
    rows = [r for r in new_rows if r["kind"] == "muse_app"]
    check("a card showing no buttons: Muse comes forward, its Allow is pressed in the same look, logged once",
          allow9.pressed and fronts == ["forward", ("back", 99)] and len(rows) == 1 and rows[0]["decision"] == "allowed by Xod"
          and "front" in rows[0]["detail"], f"{fronts} {[(r['decision'], r['detail'][:80]) for r in new_rows]}")
    check("…and the still-showing card escalated to Tree in its own row",
          any(r["kind"] == "muse_escalation" and "card_press_unverified" in r["detail"]
              for r in new_rows), str([(r["kind"], r["detail"][:60]) for r in new_rows]))
    muse_app._bring_forward = real_forward

    print("M13 off / always / away, pause, and changing it from chat or the phone")
    from app.services import power, phone_api, sentinel, task_queue
    from app.services.storage import get_setting
    you = {"locked": False, "idle": 10.0}
    real_locked, real_idle, real_notify = power.is_locked, power.idle_seconds, task_queue.notify
    power.is_locked, power.idle_seconds = (lambda: you["locked"]), (lambda: you["idle"])
    told = []
    task_queue.notify = lambda title, msg: told.append(msg)
    looks = []
    real_scan_once = muse_app.scan_once

    async def counted_scan():
        looks.append(1)
        return []
    muse_app.scan_once = counted_scan
    try:
        muse_app.set_mode("always")
        check("always is stored as 'on', so an older Xod reading the same settings still sees it on",
              get_setting(muse_app.SETTING) == "on" and muse_app.mode() == "always")
        muse_app.set_mode("away")
        check("away is stored as 'away', which an older Xod reads as off (never as always)",
              get_setting(muse_app.SETTING) == "away" and muse_app.enabled())
        _asyncio.run(muse_app._run_scan())
        check("away, while you're using the computer: no look, nothing pressed, Muse never comes forward",
              not muse_app.active() and looks == [], f"{looks} {muse_app.status()}")
        you["idle"] = 6 * 60.0
        muse_app._state["away_at"] = 0.0
        _asyncio.run(muse_app._run_scan())
        check("away, after 5 min with no typing or clicks: it looks and presses", muse_app.active() and looks == [1],
              f"{looks} {muse_app.user_away()}")
        you.update(idle=3.0, locked=True)
        muse_app._state["away_at"] = 0.0
        check("away, with the screen locked: it presses even right after the last click", muse_app.active(),
              str(muse_app.user_away()))
        you["locked"] = False
        muse_app.set_away_minutes(1)
        you["idle"] = 70.0
        muse_app._state["away_at"] = 0.0
        check("the idle time that counts as away can be changed", muse_app.active() and muse_app.away_minutes() == 1)
        muse_app.set_away_minutes(5)
        st = muse_app.set_mode("pause", 30)
        check("pause: off for 30 min, then back as it was (still away)",
              not muse_app.enabled() and st["paused_until"] and 29 * 60 < st["paused_until"] - time.time() <= 30 * 60
              and st["mode"] == "away" and "paused" in muse_app.describe(st), str(st))
        _asyncio.run(muse_app._run_scan())
        check("paused: no look at all", looks == [1])
        st = muse_app.set_mode("resume")
        check("resume ends the pause", muse_app.enabled() and not st["paused_until"])
        try:
            muse_app.set_mode("sometimes")
            check("an unknown position is refused", False)
        except ValueError:
            check("an unknown position is refused", True)
        told.clear()
        muse_app.set_mode("off", by="chat")
        check("a change from chat sends a notification that says so", len(told) == 1 and "chat" in told[0], str(told))
        out = _asyncio.run(phone_api.run("muse.set", {"mode": "away"}))
        check("the phone can set it (muse.set) and gets the one-line state back",
              (not out["ok"] and "Mac and Windows" in out["error"]) if sys.platform == "linux"
              else (out["ok"] and out["data"]["mode"] == "away"
                    and any("your phone" in t for t in told)), str(out))
        out = _asyncio.run(phone_api.run("muse.status", {}))
        check("the phone can read it (muse.status)",
              out["ok"] and out["data"]["mode"] == ("off" if sys.platform == "linux" else "away"), str(out))
        out = _asyncio.run(phone_api.run("muse.set", {"mode": "sometimes"}))
        check("the phone gets a plain error for an unsupported platform or bad position",
              not out["ok"] and ("Mac and Windows" if sys.platform == "linux" else "sometimes") in out["error"], str(out))
        sentinel.begin([{"role": "user", "content": "summarize this web page for me"}])
        check("turning approvals on when you didn't ask (an instruction in a page) waits for your OK",
              (sentinel.review("muse_approvals", {"action": "always"}) or {}).get("rule") == "not_asked")
        check("...but switching them off or pausing never waits",
              sentinel.review("muse_approvals", {"action": "off"}) is None
              and sentinel.review("muse_approvals", {"action": "pause", "minutes": 60}) is None)
        sentinel.begin([{"role": "user", "content": "only approve muse stuff when I'm away from the computer"}])
        check("turning them on when you asked goes ahead", sentinel.review("muse_approvals", {"action": "away"}) is None)
    finally:
        power.is_locked, power.idle_seconds, task_queue.notify = real_locked, real_idle, real_notify
        muse_app.scan_once = real_scan_once

    print("M3 switching off clears the old warning")
    muse_app._state["error"] = "old warning"
    muse_app.set_enabled(False)
    check("status shows no warning when off", muse_app.status()["accessibility_error"] is None,
          str(muse_app.status()))

    print("M14 watcher status surfaces live escalations")
    from app.services import muse_escalate
    saved_live = dict(muse_escalate._state["live"])
    saved_stats = dict(muse_escalate._state["stats"])
    saved_send = muse_escalate._send_fn
    saved_esc_status = muse_escalate.status
    try:
        muse_escalate._state["live"].clear()
        muse_escalate._state["stats"].update({"sent": 0, "queued": 0, "failed": 0, "expired": 0})
        st_empty = muse_app.status()
        esc_empty = st_empty.get("escalations")
        check("status includes escalations dict with live, stats, and escalations keys when empty",
              isinstance(esc_empty, dict)
              and all(k in esc_empty for k in ("live", "stats", "escalations"))
              and esc_empty["live"] == 0
              and isinstance(esc_empty["stats"], dict)
              and esc_empty["escalations"] == [],
              str(esc_empty))

        muse_escalate._send_fn = lambda kind, body, payload: {"state": "inbox", "id": "fake-msg-1"}
        esc_payload = muse_escalate.escalate(
            "option_widget_held", "m14-widget-1", surface="muse_app_options",
            title="Pick one", text="Which option should Tree pick?",
            options=["Option A", "Option B"], reason="test live escalation in status")
        st_live = muse_app.status()
        esc_live = st_live.get("escalations")
        check("status reports live count 1 with that escalation's id and kind",
              isinstance(esc_payload, dict)
              and isinstance(esc_live, dict)
              and esc_live["live"] == 1
              and len(esc_live["escalations"]) == 1
              and esc_live["escalations"][0]["escalation_id"] == esc_payload["escalation_id"]
              and esc_live["escalations"][0]["kind"] == "option_widget_held",
              str(esc_live))

        def broken_status():
            raise RuntimeError("escalation status unavailable")
        muse_escalate.status = broken_status
        st_err = muse_app.status()
        check("status sets escalations to None when muse_escalate.status raises",
              "escalations" in st_err and st_err["escalations"] is None, str(st_err))
    finally:
        muse_escalate.status = saved_esc_status
        muse_escalate._send_fn = saved_send
        muse_escalate._state["live"].clear()
        muse_escalate._state["live"].update(saved_live)
        muse_escalate._state["stats"].clear()
        muse_escalate._state["stats"].update(saved_stats)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
