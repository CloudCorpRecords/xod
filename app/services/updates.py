"""
"A new version is out": at most once a day Xod asks GitHub for the latest release, and the home
screen offers it. That request is the only thing it sends: no IDs, no usage data (GitHub sees
an IP address, like any download). Turn it off from the notice, or with XOD_NO_UPDATE_CHECK=1.
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


def _ver(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def _setting(key: str, default: str = "") -> str:
    from app.services.storage import get_setting
    return get_setting(key, default)


def enabled() -> bool:
    return not os.environ.get("XOD_NO_UPDATE_CHECK") and _setting("update_check", "on") != "off"


def set_enabled(on: bool) -> None:
    from app.services.storage import set_setting
    set_setting("update_check", "on" if on else "off")


async def status(force: bool = False) -> Dict[str, Any]:
    from app.services.storage import set_setting
    out: Dict[str, Any] = {"current": VERSION, "enabled": enabled(), "available": False}
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
