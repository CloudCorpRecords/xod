"""Take Over from the phone: the Xod phone app shows Xod's browser, and taps, types and scrolls in it.

For what Xod can't do by itself, mostly signing in to a site it has no saved login for (Facebook, a bank, anything
with a code by text). The person signs in once from their phone; the session stays in Xod's browser profile, so Xod
can carry on afterwards. What they type this way goes straight into the page: it's never shown to the AI, never put
in the browser's action history, and the request that carried it isn't kept (services/phone_api.py hides it).

Everything travels as Agent Mail between the person's own phone and computer, end-to-end encrypted. A screen is a
small JPEG of what the browser shows, sized to fit one message.
"""
import asyncio
import base64
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("xod.browser_remote")

SCREEN_WIDTH = 720          # pixels across the picture sent to the phone (the page is scaled to fit)
MAX_IMAGE = 110_000         # characters of base64; one Agent Mail envelope carries up to 256 KB

# The site Xod last found wanting a sign-in it couldn't do, so the phone can offer "Sign in" right there.
_signin: Dict[str, Any] = {}


def _svc():
    from app.services.browser_sandbox import browser_sandbox_service
    return browser_sandbox_service


def note_signin(url: str) -> Optional[str]:
    """A sign-in page Xod has no saved login for: remember it (the phone shows a Sign in button) and say what to do."""
    from app.services import credential_vault
    site = credential_vault.site_key(url)
    if credential_vault.describe_login(url):
        return None
    _signin.update({"site": site, "url": url, "at": time.time()})
    return (f"{site} wants a sign-in and there's no saved login for it. Ask the person to sign in once themselves: from "
            "the Xod app on their phone (the Sign in button, or Browser), or on this computer with Take Over. Xod keeps "
            "the session, so then you can carry on. Never ask for their password in chat.")


_WALL_JS = """(() => { const p = [...document.querySelectorAll('input[type=password]')].find(e => e.offsetParent !== null);
  return p ? location.href : ''; })()"""


async def check_signin() -> Optional[str]:
    """If the page Xod's browser shows is a sign-in it has no saved login for: what to tell the model."""
    try:
        url = (await _svc().evaluate_js(_WALL_JS)).get("result") or ""
    except Exception:
        return None
    return note_signin(url) if url else None


def signin_since(started: float) -> Optional[Dict[str, Any]]:
    """The sign-in Xod hit after `started`, if any (for the chat answer to the phone)."""
    return {"site": _signin["site"], "url": _signin["url"]} if _signin.get("site") and _signin.get("at", 0) >= started else None


def signed_in() -> None:
    _signin.clear()


async def _session(fn):
    """Runs fn(ws, send) on the page Xod's browser is showing, under the browser's lock."""
    import websockets
    svc = _svc()
    async with svc._lock:
        await svc.ensure_browser_running()
        ws_url = await svc._get_active_tab_ws_url()
        async with websockets.connect(ws_url, max_size=50 * 1024 * 1024, ping_interval=None) as ws:
            async def send(method, params=None):
                return await svc._send_cdp(ws, method, params or {})
            return await fn(ws, send)


PHONE_WINDOW = (500, 900)       # CSS pixels (Chrome's narrowest window): sites lay out for a phone, readable on one
DESK_WINDOW = (1440, 900)       # browser_sandbox's own size (--window-size)
IDLE_RESTORE = 600              # seconds without the phone before the browser goes back to its own size
_phone = {"on": False, "last": 0.0, "task": None}


async def _window(size) -> bool:
    """Resizes the window of the page Xod's browser shows (the page lays itself out again for the new width)."""
    async def resize(ws, send):
        target = (await send("Target.getTargetInfo")).get("targetInfo", {}).get("targetId")
        win = await send("Browser.getWindowForTarget", {"targetId": target} if target else {})
        await send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"width": size[0], "height": size[1]}})
        for _ in range(20):          # the page lays itself out again a moment later
            vp = (await send("Page.getLayoutMetrics")).get("cssVisualViewport") or {}
            if abs(float(vp.get("clientWidth") or 0) - size[0]) < 80:
                break
            await asyncio.sleep(0.1)
    try:
        await _session(resize)
        return True
    except Exception as e:
        logger.info(f"Take Over: couldn't resize the browser window ({e})")
        return False


async def phone_layout(on: bool) -> None:
    """Narrow while the person is signing in from the phone; back to normal after, or when the phone goes quiet."""
    _phone["last"] = time.time()
    if on == _phone["on"]:
        return
    if await _window(PHONE_WINDOW if on else DESK_WINDOW):
        _phone["on"] = on
    if on and not _phone["task"]:
        async def watch():
            try:
                while _phone["on"]:
                    await asyncio.sleep(30)
                    if time.time() - _phone["last"] > IDLE_RESTORE:
                        await phone_layout(False)
            finally:
                _phone["task"] = None
        _phone["task"] = asyncio.get_running_loop().create_task(watch())


async def screen() -> Dict[str, Any]:
    """What the browser shows now: a JPEG (base64) with its size, and the page's address and title."""
    if _phone["on"]:
        _phone["last"] = time.time()

    async def grab(ws, send):
        await send("Page.enable")
        m = await send("Page.getLayoutMetrics")
        vp = m.get("cssVisualViewport") or m.get("cssLayoutViewport") or {}
        w, h = float(vp.get("clientWidth") or 1440), float(vp.get("clientHeight") or 900)
        info = await send("Runtime.evaluate", {"expression": "JSON.stringify([location.href, document.title])", "returnByValue": True})
        try:
            import json
            url, title = json.loads(info.get("result", {}).get("value") or "[]")
        except Exception:
            url, title = "", ""
        scale = min(2.0, SCREEN_WIDTH / w)      # a narrow (phone) layout is drawn at up to twice its size: crisp
        data = ""
        for quality in (60, 45, 32, 22):
            shot = await send("Page.captureScreenshot", {"format": "jpeg", "quality": quality,
                                                          "clip": {"x": 0, "y": 0, "width": w, "height": h, "scale": scale}})
            data = shot.get("data") or ""
            if len(data) <= MAX_IMAGE:
                break
        if len(data) > MAX_IMAGE:
            scale *= 0.6
            shot = await send("Page.captureScreenshot", {"format": "jpeg", "quality": 30,
                                                          "clip": {"x": 0, "y": 0, "width": w, "height": h, "scale": scale}})
            data = shot.get("data") or ""
        return {"image": data, "width": round(w * scale), "height": round(h * scale), "page_width": w, "page_height": h,
                "url": url, "title": title, "signed_in_hint": _signin.get("site")}
    return await _session(grab)


async def tap(x: float, y: float) -> Dict[str, Any]:
    """A tap at (x, y) as fractions of the picture (0..1 across, 0..1 down), then the new screen."""
    x, y = min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0)

    async def click(ws, send):
        m = await send("Page.getLayoutMetrics")
        vp = m.get("cssVisualViewport") or m.get("cssLayoutViewport") or {}
        px, py = x * float(vp.get("clientWidth") or 1440), y * float(vp.get("clientHeight") or 900)
        await send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": px, "y": py})
        await send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": px, "y": py, "button": "left", "clickCount": 1})
        await send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": px, "y": py, "button": "left", "clickCount": 1})
    await _session(click)
    await asyncio.sleep(0.6)
    return await screen()


async def type_text(text: str, enter: bool = False) -> Dict[str, Any]:
    """Types into whatever has focus. Never logged or remembered: it's often a password."""
    text = str(text or "")[:2000]

    async def typing(ws, send):
        if text:
            await send("Input.insertText", {"text": text})
        if enter:
            for kind in ("rawKeyDown", "keyUp"):
                await send("Input.dispatchKeyEvent", {"type": kind, "windowsVirtualKeyCode": 13, "key": "Enter", "code": "Enter",
                                                      **({"text": "\r"} if kind == "rawKeyDown" else {})})
    await _session(typing)
    await asyncio.sleep(0.8 if enter else 0.3)
    return await screen()


_KEYS = {"enter": (13, "Enter"), "backspace": (8, "Backspace"), "tab": (9, "Tab"), "escape": (27, "Escape")}


async def key(name: str) -> Dict[str, Any]:
    code, k = _KEYS.get(str(name or "").lower(), (None, None))
    if not code:
        raise ValueError("Keys: enter, backspace, tab or escape.")

    async def press(ws, send):
        await send("Input.dispatchKeyEvent", {"type": "rawKeyDown", "windowsVirtualKeyCode": code, "key": k, "code": k})
        await send("Input.dispatchKeyEvent", {"type": "keyUp", "windowsVirtualKeyCode": code, "key": k, "code": k})
    await _session(press)
    await asyncio.sleep(0.5)
    return await screen()


async def scroll(direction: str = "down") -> Dict[str, Any]:
    dy = -600 if str(direction).lower() == "up" else 600

    async def wheel(ws, send):
        await send("Runtime.evaluate", {"expression": f"window.scrollBy(0, {dy})"})
    await _session(wheel)
    await asyncio.sleep(0.4)
    return await screen()


async def go(url: str) -> Dict[str, Any]:
    url = str(url or "").strip()
    if not url:
        raise ValueError("Which address?")
    import re
    import urllib.parse
    if re.match(r"^(javascript|data|file|blob|chrome|about|view-source|vbscript):", url, re.I):
        raise ValueError("Only web addresses (http or https).")
    if "://" not in url:
        url = "https://" + url
    parts = urllib.parse.urlparse(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or not re.fullmatch(r"[a-z0-9.\-\[\]:]+", parts.hostname, re.I):
        raise ValueError("Only web addresses (http or https).")
    await _svc().navigate(url)
    return await screen()


async def back() -> Dict[str, Any]:
    async def hist(ws, send):
        await send("Runtime.evaluate", {"expression": "history.back()"})
    await _session(hist)
    await asyncio.sleep(1.0)
    return await screen()


async def done() -> Dict[str, Any]:
    """The person finished (signed in): Xod can carry on with the session the browser now has."""
    site = _signin.get("site")
    signed_in()
    await phone_layout(False)
    return {"message": f"Thanks! I'll use your {site} session from now on." if site else "Thanks! Xod's browser is yours again."}


def to_image_bytes(screen_out: Dict[str, Any]) -> bytes:
    """For tests: the JPEG itself."""
    return base64.b64decode(screen_out.get("image") or "")
