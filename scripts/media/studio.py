"""
A tiny recording studio for Xod's screenshots and videos: drives headless Chrome over the DevTools
protocol, moves a visible cursor smoothly, types like a person, and records the screen to MP4.

Always point it at a Xod running on a demo data folder (never your own), since the results are public:
  XOD_DATA_DIR=/tmp/xod-demo .venv/bin/python -m uvicorn app.main:app --port 8125
"""
import asyncio
import base64
import json
import math
import random
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

import websockets


def _chrome() -> str:
    """Google Chrome on this computer (macOS, Windows or Linux)."""
    import os
    import platform
    if platform.system() == "Darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if platform.system() == "Windows":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", ""), os.environ.get("LOCALAPPDATA", "")):
            p = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if base and p.exists():
                return str(p)
    return shutil.which("google-chrome") or shutil.which("chromium") or "google-chrome"


CHROME = _chrome()

# A cursor drawn into the page (headless Chrome has none), with a soft ring on clicks.
CURSOR_JS = r"""
(function mount() {
  if (window.__studioCursor) return;
  if (!document.body) { document.addEventListener('DOMContentLoaded', mount, { once: true }); return; }
  const c = document.createElement('div');
  c.innerHTML = `<svg width="26" height="26" viewBox="0 0 26 26"><path d="M5 3 L5 21 L10 16.5 L13.5 24 L16.5 22.6 L13.1 15.3 L19.5 15.3 Z"
    fill="#fffaf0" stroke="#0f1222" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
  Object.assign(c.style, {position: 'fixed', left: '0', top: '0', zIndex: 2147483647, pointerEvents: 'none',
    transform: 'translate(-100px,-100px)', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.45))', transition: 'opacity .2s'});
  document.documentElement.appendChild(c);
  window.__studioCursor = c;
  document.addEventListener('mousemove', e => { c.style.transform = `translate(${e.clientX - 5}px, ${e.clientY - 3}px)`; }, true);
  document.addEventListener('mousedown', e => {
    const r = document.createElement('div');
    Object.assign(r.style, {position: 'fixed', left: e.clientX - 18 + 'px', top: e.clientY - 18 + 'px', width: '36px', height: '36px',
      borderRadius: '50%', border: '2px solid rgba(242,178,74,.9)', zIndex: 2147483646, pointerEvents: 'none',
      transition: 'transform .45s ease-out, opacity .45s ease-out', transform: 'scale(.4)', opacity: '1'});
    document.documentElement.appendChild(r);
    requestAnimationFrame(() => { r.style.transform = 'scale(1.3)'; r.style.opacity = '0'; });
    setTimeout(() => r.remove(), 500);
  }, true);
})();
"""


class Studio:
    def __init__(self, width=1600, height=900, scale=2, port=9471, cursor=True, chrome_args=()):
        self.w, self.h, self.scale, self.port, self.cursor = width, height, scale, port, cursor
        self.chrome_args = list(chrome_args)
        self.n = 0
        self.mouse = (width / 2, height / 2)
        self.frames: List[Tuple[float, bytes]] = []
        self.recording = False
        self._pending = {}
        self._events = asyncio.Queue()

    # ---------------------------------------------------------------- plumbing
    async def __aenter__(self):
        self.profile = tempfile.mkdtemp(prefix="xod-studio-")
        self.chrome = subprocess.Popen(
            [CHROME, "--headless=new", f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile}",
             "--use-mock-keychain", "--password-store=basic", "--no-first-run", "--hide-scrollbars",
             "--force-dark-mode", "--autoplay-policy=no-user-gesture-required",
             # Without this, headless screencast frames come at 1x even with a 2x device scale.
             f"--force-device-scale-factor={self.scale}", f"--window-size={self.w},{self.h}",
             *self.chrome_args, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json"))
                break
            except Exception:
                time.sleep(0.2)
        target = next(t for t in tabs if t["type"] == "page")
        # No keep-alive pings: a busy headless Chrome can miss one, and the connection is dropped mid-test.
        self.ws = await websockets.connect(target["webSocketDebuggerUrl"], max_size=200_000_000, ping_interval=None)
        self.reader = asyncio.create_task(self._read())
        await self.cmd("Page.enable")
        await self.cmd("Emulation.setDeviceMetricsOverride", width=self.w, height=self.h,
                       deviceScaleFactor=self.scale, mobile=False)
        await self.cmd("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
        if self.cursor:
            await self.cmd("Page.addScriptToEvaluateOnNewDocument", source=CURSOR_JS)
        return self

    async def __aexit__(self, *exc):
        self.reader.cancel()
        await self.ws.close()
        if CHROME.lower().endswith(".exe"):      # Windows: Chrome's helper processes outlive a plain terminate
            subprocess.run(["taskkill", "/PID", str(self.chrome.pid), "/T", "/F"], capture_output=True)
        else:
            self.chrome.terminate()
        time.sleep(0.5)
        shutil.rmtree(self.profile, ignore_errors=True)

    async def _read(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self._pending:
                self._pending.pop(msg["id"]).set_result(msg)
            elif msg.get("method") == "Page.screencastFrame":
                p = msg["params"]
                if self.recording:
                    self.frames.append((time.monotonic(), base64.b64decode(p["data"])))
                asyncio.create_task(self.cmd("Page.screencastFrameAck", sessionId=p["sessionId"]))

    async def cmd(self, method, **params):
        self.n += 1
        fut = asyncio.get_event_loop().create_future()
        self._pending[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        msg = await fut
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result", {})

    async def js(self, expr):
        r = await self.cmd("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"JS error: {r['exceptionDetails'].get('text')} {expr[:120]}")
        return (r.get("result") or {}).get("value")

    async def goto(self, url, wait=2.5):
        await self.cmd("Page.navigate", url=url)
        await asyncio.sleep(wait)
        if self.cursor:
            await self.js(CURSOR_JS)
        await self.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=self.mouse[0], y=self.mouse[1])

    # ---------------------------------------------------------------- a person at the keyboard
    async def move(self, x, y, duration=0.7):
        """Moves the cursor along a gentle curve with ease-in-out, like a hand would."""
        x0, y0 = self.mouse
        steps = max(8, int(duration * 60))
        bend = random.uniform(-0.12, 0.12)
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)
            px = x0 + (x - x0) * e + (y - y0) * bend * math.sin(math.pi * t)
            py = y0 + (y - y0) * e - (x - x0) * bend * math.sin(math.pi * t)
            await self.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=px, y=py)
            await asyncio.sleep(duration / steps)
        self.mouse = (x, y)

    async def center_of(self, selector):
        r = await self.js(f"""(() => {{ const e = document.querySelector({json.dumps(selector)}); if (!e) return null;
            const b = e.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; }})()""")
        if not r:
            raise RuntimeError(f"not found: {selector}")
        return r

    async def click(self, selector=None, at=None, move=0.6):
        x, y = at or await self.center_of(selector)
        await self.move(x, y, move)
        await asyncio.sleep(0.12)
        for t in ("mousePressed", "mouseReleased"):
            await self.cmd("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1)
            await asyncio.sleep(0.07)

    async def type(self, text, cps=16):
        for ch in text:
            await self.cmd("Input.insertText", text=ch)
            await asyncio.sleep(random.uniform(0.6, 1.4) / cps)

    async def key(self, key="Enter", code="Enter", vk=13):
        for t in ("keyDown", "keyUp"):
            await self.cmd("Input.dispatchKeyEvent", type=t, key=key, code=code, windowsVirtualKeyCode=vk)

    # ---------------------------------------------------------------- capture
    async def shot(self, path, clip=None, scale=None):
        params = {"format": "png"}
        if clip:
            params["clip"] = {**clip, "scale": scale or 1}
        data = (await self.cmd("Page.captureScreenshot", **params))["data"]
        Path(path).write_bytes(base64.b64decode(data))

    async def start_recording(self):
        self.frames = []
        self.recording = True
        await self.cmd("Page.startScreencast", format="jpeg", quality=92,
                       maxWidth=self.w * self.scale, maxHeight=self.h * self.scale, everyNthFrame=1)

    async def stop_recording(self):
        await asyncio.sleep(0.2)
        self.recording = False
        await self.cmd("Page.stopScreencast")
        frames, self.frames = self.frames, []
        if frames:      # a page that stopped changing sends no more frames: its last one stays up until now
            frames.append((time.monotonic(), frames[-1][1]))
        return frames


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def frames_to_mp4(frames, out: Path, fps=30, size: Optional[Tuple[int, int]] = (1920, 1080), speed=1.0,
                  crop: Optional[Tuple[float, float, float, float]] = None, scale: float = 1.0):
    """Screencast frames arrive only when the page changes; this holds each one for as long as it was on screen.
    crop=(x, y, w, h) in CSS pixels (with the page's device scale `scale`) zooms into part of the page."""
    if not frames:
        raise RuntimeError("no frames recorded")
    tmp = Path(tempfile.mkdtemp(prefix="xod-frames-"))
    try:
        lines = []
        for i, (t, data) in enumerate(frames):
            f = tmp / f"{i:06d}.jpg"
            f.write_bytes(data)
            nxt = frames[i + 1][0] if i + 1 < len(frames) else t + 0.5
            lines += [f"file '{f.as_posix()}'", f"duration {max(0.001, (nxt - t) / speed):.4f}"]   # ffmpeg reads \ as an escape
        lines.append(f"file '{(tmp / f'{len(frames) - 1:06d}.jpg').as_posix()}'")
        (tmp / "list.txt").write_text("\n".join(lines))
        vf = f"fps={fps}"
        if crop:
            x, y, w, h = (round(v * scale / 2) * 2 for v in crop)
            vf = f"crop={w}:{h}:{x}:{y},{vf}"
        if size:
            vf = f"{vf.split(',fps=')[0]},scale={size[0]}:{size[1]}:flags=lanczos,fps={fps}" if crop else f"scale={size[0]}:{size[1]}:flags=lanczos,{vf}"
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                        "-vf", vf + ",format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                        "-movflags", "+faststart", str(out)], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out
