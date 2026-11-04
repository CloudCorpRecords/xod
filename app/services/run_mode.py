"""
How Xod runs: one switch, shared by the desktop app and the terminal.

  headless     no window: the desktop shell starts the server, tray, talk shortcut and updater,
               and renders nothing (electron/mode.js). The terminal (`xod`) is the headless way to
               use Xod: chat, tasks, steering and approvals, against the same server.
  lightweight  a lean server: the optional background loops are skipped and the sandbox browser
               closes after 3 idle minutes. Tasks, the orchestrator, routines, approvals, Agent
               Mail and the phone link keep working.

Both live in desktop.json in Xod's data folder, the file the desktop shell already keeps its
preferences in, so the tray, Settings and `xod mode` all flip the same switch. Environment
variables (XOD_HEADLESS, XOD_LIGHTWEIGHT) override it for one launch. A change to lightweight
applies when the server next starts (it says so); headless applies at the next app launch.
"""
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

PREFS = Path(DATA_DIR) / "desktop.json"
_lock = threading.Lock()
TRUE = ("1", "true", "yes", "on")

# Skipped in lightweight mode: background loops that are optional to getting work done.
OPTIONAL_LOOPS = {
    "soul_life": "the companion's own idle activity",
    "recall_startup_index": "re-indexing past chats at startup (search still indexes on use)",
    "autoconnect": "re-scanning this computer for AI servers every few minutes",
    "xod_network": "the link to your other Xods",
    "memory_sync": "sharing memory with your other Xods",
    "muse_app": "watching the Muse app",
    "handoff_dispatch": "watching repositories for hand-off files",
    "specialists": "restarting specialist model servers at startup",
    "partner_watchdog": "supervising the Partner process",
}
LIGHT_BROWSER_IDLE_MINUTES = 3


def _read() -> Dict[str, Any]:
    try:
        return json.loads(PREFS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _env(name: str) -> Optional[bool]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip().lower() in TRUE


def get() -> Dict[str, Any]:
    p = _read()
    env_h, env_l = _env("XOD_HEADLESS"), _env("XOD_LIGHTWEIGHT")
    return {
        "headless": env_h if env_h is not None else bool(p.get("headless")),
        "lightweight": env_l if env_l is not None else bool(p.get("lightweight")),
        "saved": {"headless": bool(p.get("headless")), "lightweight": bool(p.get("lightweight"))},
        "overridden_by_env": [n for n, v in (("XOD_HEADLESS", env_h), ("XOD_LIGHTWEIGHT", env_l)) if v is not None],
        "lightweight_skips": OPTIONAL_LOOPS,
    }


def set(headless: Optional[bool] = None, lightweight: Optional[bool] = None) -> Dict[str, Any]:
    """Saves the switch. Keeps every other preference in desktop.json as it was."""
    with _lock:
        p = _read()
        if headless is not None:
            p["headless"] = bool(headless)
        if lightweight is not None:
            p["lightweight"] = bool(lightweight)
        PREFS.parent.mkdir(parents=True, exist_ok=True)
        tmp = PREFS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(p, indent=1), encoding="utf-8")
        tmp.replace(PREFS)
    return get()


# Decided once per server process: a running server does not change its shape under its own feet.
_started_light: Optional[bool] = None


def lightweight_active() -> bool:
    global _started_light
    if _started_light is None:
        _started_light = get()["lightweight"]
    return _started_light


def skip(loop: str) -> bool:
    """True when this optional loop is skipped by the running server's lightweight mode."""
    return loop in OPTIONAL_LOOPS and lightweight_active()


def server_status() -> Dict[str, Any]:
    g = get()
    running_light = lightweight_active()
    return {**g, "server_lightweight": running_light,
            "skipped_now": sorted(OPTIONAL_LOOPS) if running_light else [],
            "pending_restart": g["lightweight"] != running_light}
