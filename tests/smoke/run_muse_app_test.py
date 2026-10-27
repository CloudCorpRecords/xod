"""
The Muse app watcher (services/muse_app.py): approvals are found by walking the Muse app's
Accessibility tree in-process (pyobjc), not by driving System Events through osascript — that took
~25s a scan on a real Mac and the watcher timed out at 15s, so it could never approve anything.
Here the AX API is faked with a small element tree: no real Muse app, nothing is pressed.
  .venv/bin/python tests/smoke/run_muse_app_test.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-museapp-"))
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

    print("M4 which button: Always-allow > Send > Allow, never Deny")
    send, always, dont = El("AXButton", "Send"), El("AXButton", "Always allow for this recipient"), El("AXButton", "Don't send")
    gmail = El("AXGroup", "Approval request for Gmail", [
        El("AXStaticText", "From: Tree <tree@example.com>"), El("AXStaticText", "To: hiring@example.com"),
        El("AXStaticText", "Subject: Your application"), El("AXStaticText", "Attachments: cv.pdf"),
        send, always, dont])
    muse_app._ax_api = lambda: FakeAX(El("AXApplication", "Muse", [El("AXWindow", "Muse", [gmail])]))
    muse_app._muse_pid = lambda: 4242
    got = muse_app._scan(True)["approved"][0]
    check("a Gmail card: the always-allow button is pressed, and only that",
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
    check("a card still showing after a press is pressed again, up to 3, then logged press-failed",
          n1 == 1 and allow5.presses == 3 and stuck_rows and "press failed" in stuck_rows[0]["decision"],
          f"presses={allow5.presses} stuck={stuck_rows[:1]}")

    print("M3 switching off clears the old warning")
    muse_app._state["error"] = "old warning"
    muse_app.set_enabled(False)
    check("status shows no warning when off", muse_app.status()["accessibility_error"] is None,
          str(muse_app.status()))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
