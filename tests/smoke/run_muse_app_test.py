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
         "pressed": False}]}, str(seen))
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
