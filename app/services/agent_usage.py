"""
How much of a coding agent's subscription is left, so Xod knows before a job fails, not after.

Muse prints it with `muse usage`:
  Tier: Muse Code High Usage
  Window: 100% used, resets in 1h 24m
  Weekly: 85% used, resets in 4d 20h
Read at most every CACHE_SEC (and on demand), turned into percentages and reset times. A window at 100% means
any Muse job fails until the reset, whichever Muse model it uses (the quota is shared across models).
"""
import re
import subprocess
import time
from typing import Any, Dict, Optional

CACHE_SEC = 300
FULL = 100

_LINE_RE = re.compile(r"^\s*(window|weekly)\s*:\s*(\d+(?:\.\d+)?)\s*%\s*used(?:\s*,\s*resets in\s+(.+?))?\s*$", re.I | re.M)
_TIER_RE = re.compile(r"^\s*tier\s*:\s*(.+?)\s*$", re.I | re.M)
_PART_RE = re.compile(r"(\d+)\s*(d|h|m|s)\b", re.I)
_UNIT = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_cache: Dict[str, Any] = {}


def _seconds(text: str) -> Optional[int]:
    parts = _PART_RE.findall(text or "")
    return sum(int(n) * _UNIT[u.lower()] for n, u in parts) if parts else None


def parse(text: str, now: Optional[float] = None) -> Dict[str, Any]:
    """`muse usage` output -> {tier, window: {used, resets_at}, weekly: {...}, out}. Missing parts are None."""
    now = time.time() if now is None else now
    out: Dict[str, Any] = {"tier": None, "window": None, "weekly": None}
    m = _TIER_RE.search(text or "")
    if m:
        out["tier"] = m.group(1)
    for kind, used, resets in _LINE_RE.findall(text or ""):
        secs = _seconds(resets)
        out[kind.lower()] = {"used": float(used), "resets_at": (now + secs) if secs is not None else None}
    out["out"] = bool(out["window"] and out["window"]["used"] >= FULL) or bool(out["weekly"] and out["weekly"]["used"] >= FULL)
    return out


def _until(u: Dict[str, Any]) -> Optional[float]:
    """When work can go again: the later of the full limits' reset times."""
    times = [x["resets_at"] for x in (u.get("window"), u.get("weekly")) if x and x["used"] >= FULL and x.get("resets_at")]
    return max(times) if times else None


def muse(refresh: bool = False) -> Dict[str, Any]:
    """Muse's usage, cached CACHE_SEC. {available, tier, window, weekly, out, until, checked_at, error}."""
    hit = _cache.get("muse")
    if hit and not refresh and time.time() - hit["checked_at"] < CACHE_SEC:
        return hit
    from app.services import agent_clis
    path = agent_clis.which("muse")
    res: Dict[str, Any] = {"available": bool(path), "checked_at": time.time(), "error": None,
                           "tier": None, "window": None, "weekly": None, "out": False, "until": None}
    if path:
        try:
            r = subprocess.run([path, "usage"], capture_output=True, text=True, timeout=30)
            text = (r.stdout or "") + "\n" + (r.stderr or "")
            if r.returncode == 0 or _LINE_RE.search(text):
                res.update(parse(text))
                res["until"] = _until(res) if res["out"] else None
            else:
                res["error"] = (text.strip() or f"muse usage exited {r.returncode}")[:200]
        except (OSError, subprocess.TimeoutExpired) as e:
            res["error"] = str(e)[:200]
    _cache["muse"] = res
    return res
