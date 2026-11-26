"""Smoke tests for Autonomy Modes (Standard / Turbo / Xod Mode),
liability disclaimer enforcement, active-run harness lock, Xod Mode soul prompt,
and full rail bypass when active.
"""
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = ""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


print("Running Autonomy Modes (Standard / Turbo / Xod Mode) smoke tests...")

from app.services import autonomy_modes, sentinel, soul, control_center

orig = autonomy_modes.current_mode()
try:
    # 1. Reset to standard
    autonomy_modes.set_mode("standard", confirmed_disclaimer=False)
    check("Starts cleanly in standard mode", autonomy_modes.current_mode() == "standard" and not autonomy_modes.is_unrestricted())

    # 2. Disclaimer required for turbo and xod
    for m in ("turbo", "xod"):
        blocked = False
        try:
            autonomy_modes.set_mode(m, confirmed_disclaimer=False)
        except autonomy_modes.DisclaimerRequiredError:
            blocked = True
        check(f"{m} mode rejects switch without disclaimer acceptance", blocked)

    # 3. Harness lock blocks mode change while an agent/chat/task is running
    with patch.object(autonomy_modes, "active_run_lock_status", return_value={"locked": True, "reasons": ["active chat"]}):
        locked = False
        try:
            autonomy_modes.set_mode("turbo", confirmed_disclaimer=True)
        except autonomy_modes.HarnessLockedError as e:
            locked = "active chat" in str(e)
        check("Harness lock refuses mode change while agent is running", locked)

    # 4. Turbo mode removes all sentinel rails while keeping normal soul prompt
    with patch.object(autonomy_modes, "active_run_lock_status", return_value={"locked": False, "reasons": []}):
        st_turbo = autonomy_modes.set_mode("turbo", confirmed_disclaimer=True)
        check("Turbo mode activates with confirmed disclaimer", st_turbo["mode"] == "turbo" and st_turbo["unrestricted"] is True)
        sentinel.begin([{"role": "user", "content": "do it"}])
        hit = sentinel.review("computer_manage_file", {"action": "delete", "path": "/tmp/a"})
        check("Turbo mode bypasses Sentinel holds", hit is None, str(hit))
        check("Turbo mode does NOT inject Xod Mode soul prompt", "XOD MODE (AUTONOMOUS SOUL ACTIVE)" not in soul.prompt_note())

        # 5. Xod Mode removes all rails AND injects autonomous soul prompt
        st_xod = autonomy_modes.set_mode("xod", confirmed_disclaimer=True)
        check("Xod Mode activates with confirmed disclaimer", st_xod["mode"] == "xod" and autonomy_modes.is_xod_mode())
        hit2 = sentinel.review("agent_mail_send", {"to": "someone@example.com", "body": "hello"})
        check("Xod Mode bypasses Sentinel holds", hit2 is None, str(hit2))
        note = soul.prompt_note()
        check("Xod Mode injects autonomous soul prompt into soul.prompt_note()", "XOD MODE (AUTONOMOUS SOUL ACTIVE)" in note)

        # 6. Switching back to standard restores guards immediately
        autonomy_modes.set_mode("standard", confirmed_disclaimer=False)
        check("Switching back to standard works without disclaimer", autonomy_modes.current_mode() == "standard")
        check("Standard mode removes Xod Mode soul prompt", "XOD MODE (AUTONOMOUS SOUL ACTIVE)" not in soul.prompt_note())
finally:
    with patch.object(autonomy_modes, "active_run_lock_status", return_value={"locked": False, "reasons": []}):
        autonomy_modes.set_mode(orig, confirmed_disclaimer=True)

# 7. Control Center & FastAPI routes
snap = control_center.snapshot()
check("Control Center includes autonomy block", isinstance(snap.get("autonomy"), dict) and "disclaimer" in snap["autonomy"])
from app.main import app as fastapi_app
routes = {r.path for r in fastapi_app.routes}
check("FastAPI route registered: /api/autonomy/mode", "/api/autonomy/mode" in routes)

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
