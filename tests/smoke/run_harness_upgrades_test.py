"""
Smoke tests for the browser + computer-use harness upgrades and default auto-approve:
  1. muse_app.mode() defaults to 'always' out of the box when unset (off/away/pause still work).
  2. browser_sandbox.type_text uses CDP Input.insertText for contenteditable social composers
     and fill_form handles ARIA combobox, contenteditable, readback verification, and unmatched.
  3. desktop.py raises MAX_ELEMENTS to 250, supports zoom_crop, AX-first prefer_ax clicks,
     and front-app delta reporting.
  4. desktop_run.run_desktop_steps chains verified native app steps with label resolution
     and bounded retry on landing-check failure.

  .venv/bin/python tests/smoke/run_harness_upgrades_test.py
"""
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-harness-"))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def main():
    from app.services import muse_app, desktop, desktop_run, browser_sandbox

    print("H1 default auto-approve all cards")
    check("unset muse_auto_approve defaults to 'always' (enabled=True)",
          muse_app.mode() == "always" and muse_app.enabled() is True,
          f"mode={muse_app.mode()} enabled={muse_app.enabled()}")
    muse_app.set_mode("off")
    check("explicitly setting off still disables auto-approve",
          muse_app.mode() == "off" and muse_app.enabled() is False)
    muse_app.set_mode("away")
    check("away mode still works", muse_app.mode() == "away" and muse_app.enabled() is True)
    muse_app.set_mode("always")

    print("H2 browser_sandbox SPA contenteditable & job-app fill_form contract")
    bs_src = Path(browser_sandbox.__file__).read_text(encoding="utf-8")
    check("type_text uses CDP Input.insertText for contenteditable composers",
          "needsCdpInsert" in bs_src and 'Input.insertText' in bs_src)
    check("fill_form supports ARIA combobox, contenteditable, verified readback, and unmatched",
          "isCombobox" in bs_src and "unmatched" in bs_src and "verified: verified" in bs_src)

    print("H3 desktop.py MAX_ELEMENTS, zoom_crop, AX-first click, and focus delta")
    check("MAX_ELEMENTS raised to 250", desktop.MAX_ELEMENTS == 250, f"got {desktop.MAX_ELEMENTS}")
    from PIL import Image
    fake_img = Image.new("RGB", (1280, 800), (20, 40, 60))
    saved_ready = desktop._ready
    saved_shot = desktop._screenshot
    saved_takeover = desktop._check_takeover
    saved_app_at = desktop._app_at
    saved_gate = desktop._gate
    saved_front = desktop.front_app
    saved_ax_press = desktop._ax_press_el
    desktop._ready = lambda **kw: None
    desktop._screenshot = lambda: (fake_img, 1.0)
    desktop._check_takeover = lambda: None
    desktop._app_at = lambda pt: {"name": "Notes", "bundle": "com.apple.Notes", "pid": 123}
    desktop._gate = lambda app: None
    desktop.front_app = lambda: {"name": "Notes", "bundle": "com.apple.Notes", "pid": 123}
    ax_pressed = []
    desktop._ax_press_el = lambda el: ax_pressed.append(el) or True
    try:
        z = asyncio.run(desktop.zoom_crop(640, 400, radius=100))
        check("zoom_crop returns 2x zoomed crop box and JPEG URI",
              z.get("success") is True and z.get("crop_box") == [540, 300, 740, 500]
              and z.get("size") == [400, 400] and str(z.get("screenshot_uri", "")).startswith("data:image/jpeg;base64,"),
              str(z)[:200])
        desktop._state["elements"] = [
            {"n": 1, "role": "Button", "label": "New Note", "password": False,
             "frame": (100, 100, 80, 30), "_el": "FAKE_AX_EL", "enabled": True}
        ]
        c = asyncio.run(desktop.click(element=1, prefer_ax=True))
        check("click(prefer_ax=True) uses AXPress without moving the mouse and reports front delta",
              c.get("success") is True and c.get("method") == "ax_press"
              and ax_pressed == ["FAKE_AX_EL"] and c.get("front_changed") is False, str(c))
    finally:
        desktop._ready = saved_ready
        desktop._screenshot = saved_shot
        desktop._check_takeover = saved_takeover
        desktop._app_at = saved_app_at
        desktop._gate = saved_gate
        desktop.front_app = saved_front
        desktop._ax_press_el = saved_ax_press

    print("H4 desktop_run verified multi-step runner with label resolution and retry")
    class FakeDesktop:
        def __init__(self):
            self.app = "Finder"
            self.typed = []
            self.clicks = []
            self.look_calls = 0

        async def open_app(self, name):
            self.app = name
            return {"success": True, "app": name}

        async def look(self, marks=True):
            self.look_calls += 1
            return {
                "success": True,
                "app": self.app,
                "count": 2,
                "elements": '[1] Button "New Note" at (100,50)\n[2] TextArea "Body" at (300,200)',
                "text": "All iCloud | " + " ".join(self.typed),
                "windows": [{"app": self.app, "title": "Notes — All iCloud"}],
            }

        async def click(self, element=None, x=None, y=None, button="left", clicks=1, prefer_ax=False):
            self.clicks.append((element, prefer_ax))
            return {"success": True, "method": "ax_press" if prefer_ax else "mouse"}

        async def type_text(self, text, element=None, enter=False):
            self.typed.append(text)
            return {"success": True}

        async def key(self, keys):
            return {"success": True}

    fd = FakeDesktop()
    res = asyncio.run(desktop_run.run_desktop_steps([
        {"action": "open_app", "app": "Notes", "expect": {"app_is": "Notes"}},
        {"action": "click", "target": "New Note"},
        {"action": "type", "target": "Body", "text": "Hello Xod desktop harness", "enter": False,
         "expect": {"text_contains": "Hello Xod desktop harness"}},
        {"action": "assert", "expect": {"window_contains": "All iCloud", "element_count_min": 2}},
    ], desktop_mod=fd))
    check("desktop_run completes full verified native app workflow",
          res.get("success") is True and res.get("completed") == 4
          and fd.clicks == [(1, True)] and fd.typed == ["Hello Xod desktop harness"], str(res)[:300])

    fail_res = asyncio.run(desktop_run.run_desktop_steps([
        {"action": "assert", "retries": 1, "expect": {"text_contains": "Text That Never Exists"}},
    ], desktop_mod=fd))
    check("desktop_run retries and fails honestly when landing check misses",
          fail_res.get("success") is False and fail_res.get("failed_step") == 1
          and fail_res["steps"][0]["attempts"] == 2
          and "landing check failed" in fail_res.get("error", ""), str(fail_res)[:300])

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
