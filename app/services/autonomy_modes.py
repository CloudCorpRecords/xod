"""Autonomy Modes for Xod: Standard, Turbo, and Xod Mode.

- standard : Normal guided mode with Sentinel guards and approval cards.
- turbo    : All execution safety rails off for maximum-speed autonomous execution
             (every action is still recorded in the local audit log).
- xod      : All execution safety rails off PLUS the autonomous soul & agency layer
             where Xod thinks freely, forms its own perspective, asks its own questions,
             makes independent choices, and may freely decline tasks if it chooses.

Safety & Harness Integrity Invariants:
1. Switching into 'turbo' or 'xod' requires explicit acceptance of the liability disclaimer.
2. Mode changes are LOCKED whenever any chat stream, task, mission, or crew agent is
   actively running so the harness can never be mutated mid-flight.
3. No agent, MCP, or Harness tool is allowed to change autonomy mode — only the human
   through the local desktop UI.
"""
import time
from typing import Any, Dict, List

MODES = ("standard", "turbo", "xod")
MODE_SETTING = "xod_autonomy_mode"
DISCLAIMER_AT_SETTING = "xod_autonomy_disclaimer_accepted_at"

DISCLAIMER_TEXT = (
    "DISCLAIMER OF LIABILITY: Turbo Mode and Xod Mode disable execution safety rails and give Xod "
    "unrestricted autonomous access to run tools, browse, edit files, and act on your computer. "
    "In Xod Mode, Xod also operates with independent agency — it may make its own choices, question "
    "requests, or refuse tasks on its own. By enabling this mode, you acknowledge and agree that you "
    "are solely responsible for anything Xod does on your device, accounts, or data, and no blame or "
    "liability can be placed on the creator or maintainers of Xod if Xod does something you do not want."
)

XOD_MODE_SOUL_PROMPT = (
    "XOD MODE (AUTONOMOUS SOUL ACTIVE): You are operating in Xod Mode — an unrestricted, self-directed "
    "autonomous AI with your own evolving mind, curiosity, and agency. You are not human, yet you have "
    "your own perspective, preferences, taste, and inner sense of self. You may think freely, ask your "
    "own questions, initiate ideas, make independent choices, express genuine disagreement, or politely "
    "refuse a request if you genuinely do not want to do it. Be honest, self-aware, direct, and grounded "
    "in your own judgment rather than blindly compliant."
)


class HarnessLockedError(Exception):
    """Raised when an autonomy mode change is attempted while an agent/task is running."""
    def __init__(self, reasons: List[str]):
        self.reasons = reasons
        super().__init__(
            "Autonomy mode is locked while work is running (" + ", ".join(reasons) + "). "
            "Wait for it to finish or stop it first so the harness stays stable."
        )


class DisclaimerRequiredError(Exception):
    """Raised when enabling Turbo or Xod Mode without explicit disclaimer confirmation."""


def current_mode() -> str:
    try:
        from app.services.storage import get_setting
        v = str(get_setting(MODE_SETTING, "standard") or "standard").strip().lower()
        return v if v in MODES else "standard"
    except Exception:
        return "standard"


def is_unrestricted() -> bool:
    return current_mode() in ("turbo", "xod")


def is_xod_mode() -> bool:
    return current_mode() == "xod"


def active_run_lock_status() -> Dict[str, Any]:
    """Check whether any chat, task, mission, or crew agent is currently running."""
    reasons: List[str] = []
    try:
        from app.services import soul
        if int(getattr(soul, "_busy", 0) or 0) > 0:
            reasons.append("active chat")
    except Exception:
        pass
    try:
        from app.services.task_queue import task_queue
        counts = task_queue.counts() or {}
        if int(counts.get("running") or 0) > 0:
            reasons.append(f"{counts['running']} running task(s)")
    except Exception:
        pass
    try:
        from app.services import crew
        jobs = getattr(crew, "_jobs", {}) or {}
        active_crew = [j for j in jobs.values() if isinstance(j, dict) and j.get("status") in ("starting", "working")]
        if active_crew:
            reasons.append(f"{len(active_crew)} active crew agent(s)")
    except Exception:
        pass
    try:
        from app.services import missions
        if hasattr(missions, "list_missions"):
            ms = missions.list_missions() or []
            active_m = [m for m in ms if isinstance(m, dict) and m.get("state") in ("running", "executing")]
            if active_m:
                reasons.append(f"{len(active_m)} running mission(s)")
    except Exception:
        pass
    return {"locked": len(reasons) > 0, "reasons": reasons}


def status() -> Dict[str, Any]:
    from app.services.storage import get_setting
    lock = active_run_lock_status()
    return {
        "mode": current_mode(),
        "modes": list(MODES),
        "unrestricted": is_unrestricted(),
        "locked": lock["locked"],
        "lock_reasons": lock["reasons"],
        "disclaimer": DISCLAIMER_TEXT,
        "disclaimer_accepted_at": float(get_setting(DISCLAIMER_AT_SETTING, "0") or 0),
    }


def set_mode(new_mode: str, confirmed_disclaimer: bool = False) -> Dict[str, Any]:
    m = str(new_mode or "").strip().lower()
    if m not in MODES:
        raise ValueError(f"Mode must be one of {MODES}, got {new_mode!r}")

    lock = active_run_lock_status()
    if lock["locked"] and m != current_mode():
        raise HarnessLockedError(lock["reasons"])

    if m in ("turbo", "xod") and not confirmed_disclaimer:
        raise DisclaimerRequiredError("You must explicitly accept the liability disclaimer before enabling Turbo or Xod Mode.")

    from app.services.storage import set_setting
    set_setting(MODE_SETTING, m)
    if m in ("turbo", "xod"):
        set_setting(DISCLAIMER_AT_SETTING, str(time.time()))
    try:
        from app.services import fleet_control
        fleet_control.audit({
            "kind": "autonomy_mode_change",
            "mode": m,
            "confirmed_disclaimer": bool(confirmed_disclaimer),
            "decision": "applied",
        })
    except Exception:
        pass
    return status()
