"""
"A new version is out": at most once a day Xod asks GitHub for the latest release, and the home
screen offers it. That request is the only thing it sends: no IDs, no usage data (GitHub sees
an IP address, like any download). Turn it off from the notice, or with XOD_NO_UPDATE_CHECK=1.

Phase 5 (simplicity redesign): the update POLICY lives here. The default is
"automatic": Xod checks and installs updates on its own (the desktop updater
downloads in the background and installs when Xod quits; see electron/updater.js)
and reports the outcome in Work - it never prompts. The user can turn automatic
updates off in Settings ("off"); the older on/off check switch (update_check)
is kept as the same switch's other name: set_enabled(False) turns the policy
off, set_enabled(True) returns it to automatic.
"""
import json
import os
import re
import time
from typing import Any, Dict, Optional, Tuple

import httpx

from app.version import VERSION

REPO = "CloudCorpRecords/xod"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_EVERY = 24 * 3600

POLICY_AUTOMATIC = "automatic"
POLICY_OFF = "off"


def _ver(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def _setting(key: str, default: str = "") -> str:
    from app.services.storage import get_setting
    return get_setting(key, default)


def policy() -> str:
    """The update policy: "automatic" unless the user turned it off.
    Default is automatic (Phase 5): an unset policy IS automatic."""
    mode = _setting("update_mode", "")
    if mode in (POLICY_AUTOMATIC, POLICY_OFF):
        return mode
    # The pre-Phase-5 switch: "Tell me about new versions" off meant no
    # checks at all; honour a stored off as the policy being off.
    if _setting("update_check", "on") == "off":
        return POLICY_OFF
    return POLICY_AUTOMATIC


def set_policy(mode: str) -> str:
    from app.services.storage import set_setting
    mode = POLICY_OFF if str(mode).lower() == POLICY_OFF else POLICY_AUTOMATIC
    set_setting("update_mode", mode)
    set_setting("update_check", "off" if mode == POLICY_OFF else "on")
    return mode


def enabled() -> bool:
    return not os.environ.get("XOD_NO_UPDATE_CHECK") and policy() != POLICY_OFF


def auto_enabled() -> bool:
    """True when Xod may check AND install on its own, without prompting."""
    return enabled() and policy() == POLICY_AUTOMATIC


def set_enabled(on: bool) -> None:
    set_policy(POLICY_AUTOMATIC if on else POLICY_OFF)


def last_updated() -> Dict[str, Any]:
    """When Xod last updated itself, and to what ({} if it never has)."""
    try:
        return json.loads(_setting("update_last", "") or "{}")
    except ValueError:
        return {}


def mark_updated(version: str) -> None:
    from app.services.storage import set_setting
    set_setting("update_last", json.dumps({"version": version, "at": time.time()}))


async def status(force: bool = False) -> Dict[str, Any]:
    from app.services.storage import set_setting
    out: Dict[str, Any] = {"current": VERSION, "enabled": enabled(),
                           "policy": policy(), "automatic": auto_enabled(),
                           "last_updated": last_updated(), "available": False}
    if not out["enabled"]:
        return out
    try:
        cached = json.loads(_setting("update_latest", "") or "{}")
    except ValueError:
        cached = {}
    if force or time.time() - cached.get("checked_at", 0) > CHECK_EVERY:
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(LATEST, headers={"Accept": "application/vnd.github+json", "User-Agent": "Xod"})
            if r.status_code == 200:
                d = r.json()
                cached = {"version": d.get("tag_name", "").lstrip("v"), "url": d.get("html_url"),
                          "name": d.get("name"), "checked_at": time.time()}
                set_setting("update_latest", json.dumps(cached))
        except httpx.HTTPError:
            pass
    latest: Optional[str] = cached.get("version")
    if latest:
        out.update(latest=latest, url=cached.get("url"), name=cached.get("name"),
                   available=_ver(latest) > _ver(VERSION))
    return out
