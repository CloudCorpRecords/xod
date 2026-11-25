"""
action_ladder: 3-tier action escalation ladder for browser and desktop targets.

Instead of retrying the same failing selector or stalling on a tricky UI element:
  Browser click ladder:
    Tier 1 -> Semantic DOM / CSS selector click (browser.click)
    Tier 2 -> Bounding-box real CDP coordinate click (browser.click_coordinates)
    Tier 3 -> Vision/explicit coordinate click fallback (vision_locator -> click_coordinates)

  Desktop click ladder:
    Tier 1 -> Silent AXPress (desktop.click(..., prefer_ax=True))
    Tier 2 -> Physical OS mouse click at AX bounding-box center
    Tier 3 -> zoom_crop + vision/explicit coordinate click
"""
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple


async def browser_click_escalated(
    browser,
    target: str,
    *,
    coords_hint: Optional[Tuple[float, float]] = None,
    vision_locator: Optional[Callable[[str, str], Awaitable[Optional[Tuple[float, float]]]]] = None,
) -> Dict[str, Any]:
    """Execute a browser click with automatic 3-tier escalation."""
    attempts: List[Dict[str, Any]] = []

    # Tier 1: Semantic DOM / CSS selector click
    r1 = await browser.click(target)
    attempts.append({"tier": 1, "method": "dom_click", "ok": bool(r1.get("success")), "error": r1.get("error")})
    if r1.get("success"):
        return {**r1, "tier_used": 1, "attempts": attempts}

    # Tier 2: Coordinate click from explicit hint or bounding-box lookup
    xy = coords_hint
    if xy is None and hasattr(browser, "find_element_box"):
        try:
            box = await browser.find_element_box(target)
            if box and box.get("cx") is not None and box.get("cy") is not None:
                xy = (float(box["cx"]), float(box["cy"]))
        except Exception:
            xy = None
    if xy is not None and hasattr(browser, "click_coordinates"):
        r2 = await browser.click_coordinates(xy[0], xy[1])
        attempts.append({"tier": 2, "method": "box_coordinates", "xy": list(xy), "ok": bool(r2.get("success")), "error": r2.get("error")})
        if r2.get("success"):
            return {**r2, "tier_used": 2, "attempts": attempts}

    # Tier 3: Vision-guided coordinate click
    if vision_locator is not None and hasattr(browser, "click_coordinates"):
        shot = getattr(browser, "latest_screenshot_uri", "") or ""
        try:
            vxy = await vision_locator(target, shot)
        except Exception as e:
            vxy = None
            attempts.append({"tier": 3, "method": "vision_locate", "ok": False, "error": str(e)})
        if vxy is not None:
            r3 = await browser.click_coordinates(float(vxy[0]), float(vxy[1]))
            attempts.append({"tier": 3, "method": "vision_coordinates", "xy": [float(vxy[0]), float(vxy[1])],
                             "ok": bool(r3.get("success")), "error": r3.get("error")})
            if r3.get("success"):
                return {**r3, "tier_used": 3, "attempts": attempts}

    return {"success": False, "tier_used": None, "error": f"all click tiers failed for '{target}'", "attempts": attempts}


async def desktop_click_escalated(
    desktop_mod,
    *,
    element: Any = None,
    coords_hint: Optional[Tuple[float, float]] = None,
    vision_locator: Optional[Callable[[str], Awaitable[Optional[Tuple[float, float]]]]] = None,
) -> Dict[str, Any]:
    """Execute a desktop click with automatic 3-tier escalation (AXPress -> Mouse -> Zoom+Vision)."""
    attempts: List[Dict[str, Any]] = []

    # Tier 1: Silent AXPress (falls back inside click if AX unavailable, so check method)
    if element not in (None, ""):
        r1 = await desktop_mod.click(element=element, prefer_ax=True)
        attempts.append({"tier": 1 if r1.get("method") == "ax_press" else 2,
                         "method": r1.get("method") or "mouse", "ok": bool(r1.get("success")), "error": r1.get("error")})
        if r1.get("success"):
            return {**r1, "tier_used": 1 if r1.get("method") == "ax_press" else 2, "attempts": attempts}

    # Tier 2: Direct mouse click at coords_hint
    if coords_hint is not None:
        r2 = await desktop_mod.click(x=coords_hint[0], y=coords_hint[1], prefer_ax=False)
        attempts.append({"tier": 2, "method": "mouse_coords", "xy": list(coords_hint),
                         "ok": bool(r2.get("success")), "error": r2.get("error")})
        if r2.get("success"):
            return {**r2, "tier_used": 2, "attempts": attempts}

    # Tier 3: Zoom-crop + vision locator
    if vision_locator is not None and coords_hint is not None and hasattr(desktop_mod, "zoom_crop"):
        z = await desktop_mod.zoom_crop(coords_hint[0], coords_hint[1])
        if z.get("success"):
            vxy = await vision_locator(z.get("screenshot_uri") or "")
            if vxy is not None:
                # Map zoomed 2x crop coords back to look() screenshot coords
                left, top, _right, _bottom = z["crop_box"]
                sx, sy = left + float(vxy[0]) / 2.0, top + float(vxy[1]) / 2.0
                r3 = await desktop_mod.click(x=sx, y=sy, prefer_ax=False)
                attempts.append({"tier": 3, "method": "zoom_vision_mouse", "xy": [sx, sy],
                                 "ok": bool(r3.get("success")), "error": r3.get("error")})
                if r3.get("success"):
                    return {**r3, "tier_used": 3, "attempts": attempts}

    return {"success": False, "tier_used": None, "error": "all desktop click tiers failed", "attempts": attempts}
