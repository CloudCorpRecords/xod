"""
Live previews of apps and games Xod builds, and a headless check that runs them and reports errors.

A preview is served at /preview/<token>/<root id>/<path>:

  token    random per server start, so web pages can't guess preview URLs
  root id  a folder that was registered for previews (the builder registers each app it makes);
           nothing else on the Mac can be reached this way

Previews are untrusted code (a model wrote them), so they are fenced off from Xod:
  - every preview response carries `Content-Security-Policy: sandbox allow-scripts ...` without
    allow-same-origin, so even opened on its own the page has an opaque origin, and the server's
    guard refuses its requests to /api (Origin: null)
  - the preview pane's <iframe> is sandboxed the same way
  - only GET requests under the token path skip the cross-site check

Each HTML page gets two things injected at the top of <head>: an import map, so
`import * as THREE from 'three'` works offline (three.js r160 ships in app/static/vendor/three),
and a small console bridge that sends logs and errors to the preview pane.
"""
import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import APP_ROOT, DATA_DIR

logger = logging.getLogger("xod.previews")

TOKEN = secrets.token_urlsafe(18)
SERVER_BASE = ""          # set from each local request (the desktop app may not be on port 8000)
PREFIX = f"/preview/{TOKEN}/"
REGISTRY = DATA_DIR / "previews.json"
VENDOR = APP_ROOT / "app" / "static" / "vendor"
LIBS = {"three": VENDOR / "three"}
APP_MARK = ".xod-app.json"
TEXT_EXT = {".html", ".htm", ".css", ".js", ".mjs", ".json", ".txt", ".md", ".svg", ".glsl", ".csv", ".xml"}

SANDBOX = "allow-scripts allow-pointer-lock allow-modals allow-forms allow-popups allow-downloads"
PREVIEW_HEADERS = {
    "Content-Security-Policy": "; ".join([
        f"sandbox {SANDBOX}",
        "default-src 'self' data: blob: https:",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: https:",
        "style-src 'self' 'unsafe-inline' https:",
        "img-src * data: blob:",
        "media-src * data: blob:",
        "font-src * data:",
        "connect-src 'self' https: wss: data: blob:",
        "worker-src 'self' blob:",
        "frame-ancestors 'self'",
    ]),
    "X-Frame-Options": "SAMEORIGIN",
    "Access-Control-Allow-Origin": "*",          # the sandboxed page has an opaque origin; module scripts need CORS
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

BRIDGE = """(function(){var L=window.__xodLogs=[];function send(level,text){var e={level:level,text:String(text).slice(0,2000),t:Date.now()};L.push(e);if(L.length>300)L.shift();try{if(parent!==window)parent.postMessage({__xodPreview:1,entry:e},"*")}catch(_){}}
['log','info','warn','error','debug'].forEach(function(k){var o=console[k];console[k]=function(){try{send(k==='info'||k==='debug'?'log':k,[].map.call(arguments,function(a){try{return a instanceof Error?(a.stack||a.message):typeof a==='object'?JSON.stringify(a):String(a)}catch(_){return String(a)}}).join(' '))}catch(_){}return o.apply(console,arguments)}});
window.addEventListener('error',function(ev){var t=ev.target;if(t&&t!==window&&(t.src||t.href)){send('error','Could not load '+String(t.src||t.href).split('/').pop());return}send('error',(ev.error&&ev.error.stack)||((ev.message||'Error')+(ev.filename?' ('+String(ev.filename).split('/').pop()+':'+ev.lineno+')':'')))},true);
window.addEventListener('unhandledrejection',function(ev){var r=ev.reason;send('error','Unhandled promise: '+((r&&(r.stack||r.message))||r))});
window.addEventListener('load',function(){send('ready','loaded')});
/* localStorage for a sandboxed page: kept in .xod-storage.json in the app's folder, through the preview pane */
var D=__XOD_STORE__,T=0;function save(){clearTimeout(T);T=setTimeout(function(){try{if(parent!==window)parent.postMessage({__xodPreview:1,storage:D},"*")}catch(_){}},200)}
function mk(d,persist){return{getItem:function(k){k=String(k);return Object.prototype.hasOwnProperty.call(d,k)?d[k]:null},setItem:function(k,v){d[String(k)]=String(v);persist&&save()},
removeItem:function(k){delete d[String(k)];persist&&save()},clear:function(){for(var k in d)delete d[k];persist&&save()},key:function(i){return Object.keys(d)[i]||null},get length(){return Object.keys(d).length}}}
try{window.localStorage.getItem('x')}catch(_){try{Object.defineProperty(window,'localStorage',{value:mk(D,true),configurable:true})}catch(_){}}
try{window.sessionStorage.getItem('x')}catch(_){try{Object.defineProperty(window,'sessionStorage',{value:mk({},false),configurable:true})}catch(_){}}
})();"""
STORE_FILE = ".xod-storage.json"
STORE_MAX = 1024 * 1024


# ------------------------------------------------------------------ registry of preview folders

def _load() -> Dict[str, str]:
    try:
        return json.loads(REGISTRY.read_text())
    except (OSError, ValueError):
        return {}


def register(folder: Path) -> str:
    folder = Path(folder).resolve()
    rid = hashlib.sha1(str(folder).encode()).hexdigest()[:12]
    reg = _load()
    if reg.get(rid) != str(folder):
        reg[rid] = str(folder)
        REGISTRY.write_text(json.dumps(reg, indent=1))
    return rid


def root_of(rid: str) -> Optional[Path]:
    p = _load().get(rid or "")
    return Path(p) if p and Path(p).is_dir() else None


def url_for(folder: Path, entry: str = "index.html") -> str:
    return f"{PREFIX}{register(folder)}/{entry}"


def resolve(rid: str, rel: str) -> Optional[Path]:
    root = root_of(rid)
    if not root:
        return None
    rel = (rel or "").strip("/")
    if any(part.startswith(".") for part in Path(rel).parts):
        return None
    p = (root / rel).resolve()
    if p != root and not str(p).startswith(str(root) + os.sep):
        return None
    if p.is_dir():
        p = p / "index.html"
    return p if p.is_file() else None


def lib_file(rel: str) -> Optional[Path]:
    name, _, rest = (rel or "").partition("/")
    base = LIBS.get(name)
    if not base:
        return None
    p = (base / rest).resolve()
    return p if str(p).startswith(str(base.resolve()) + os.sep) and p.is_file() else None


def load_storage(rid: str) -> Dict[str, str]:
    root = root_of(rid)
    try:
        data = json.loads((root / STORE_FILE).read_text()) if root else {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_storage(rid: str, data: Dict[str, Any]) -> bool:
    root = root_of(rid)
    if not root or not isinstance(data, dict):
        return False
    body = json.dumps({str(k): str(v) for k, v in data.items()})
    if len(body) > STORE_MAX:
        return False
    (root / STORE_FILE).write_text(body)
    return True


def inject(html: str, rid: str = "") -> str:
    store = json.dumps(load_storage(rid) if rid else {}).replace("</", "<\\/")
    head = f"<script>{BRIDGE.replace('__XOD_STORE__', store)}</script>"
    if "importmap" not in html:
        imports = {"three": f"{PREFIX}__lib/three/three.module.min.js", "three/addons/": f"{PREFIX}__lib/three/addons/"}
        head = f'<script type="importmap">{json.dumps({"imports": imports})}</script>' + head
    # Classic <script src> from an opaque origin would report every error as just "Script error."
    html = re.sub(r"<script\b(?![^>]*\bcrossorigin)([^>]*\bsrc=)", r"<script crossorigin\1", html, flags=re.I)
    m = re.search(r"<head[^>]*>", html, re.I)
    if m:
        return html[:m.end()] + head + html[m.end():]
    m = re.search(r"<html[^>]*>", html, re.I)
    if m:
        return html[:m.end()] + "<head>" + head + "</head>" + html[m.end():]
    return head + html


def content_type(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in (".js", ".mjs"):
        return "text/javascript; charset=utf-8"
    t = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return t + ("; charset=utf-8" if t.startswith("text/") or ext in (".json", ".svg") else "")


# ------------------------------------------------------------------ apps in a folder

def find_apps(folder: Optional[str]) -> List[Dict[str, Any]]:
    """Apps the builder made in this folder (or the folder itself), newest first."""
    if not folder:
        return []
    root = Path(os.path.expanduser(folder)).resolve()
    out = []
    candidates = [root] + ([Path(e.path) for e in os.scandir(root) if e.is_dir() and not e.name.startswith(".")] if root.is_dir() else [])
    for d in candidates:
        mark = d / APP_MARK
        if mark.is_file():
            try:
                info = json.loads(mark.read_text())
            except (OSError, ValueError):
                info = {}
            out.append({**info, "path": str(d), "url": url_for(d, info.get("entry") or "index.html"),
                        "updated": mark.stat().st_mtime, "root": register(d)})
    return sorted(out, key=lambda a: -a["updated"])


def list_files(rid: str) -> List[Dict[str, Any]]:
    root = root_of(rid)
    if not root:
        return []
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "node_modules")
        for f in sorted(files):
            if f.startswith("."):
                continue
            p = Path(dirpath) / f
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({"path": str(p.relative_to(root)), "bytes": st.st_size, "modified": st.st_mtime,
                        "text": p.suffix.lower() in TEXT_EXT})
        if len(out) > 400:
            break
    return out


# ------------------------------------------------------------------ headless check

def _chrome() -> Optional[str]:
    for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
              "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"):
        if os.path.exists(p):
            return p
    return shutil.which("google-chrome") or shutil.which("chromium")


async def check(url: str, seconds: float = 3.0, keys: bool = True) -> Dict[str, Any]:
    """Opens the page in a throwaway headless Chrome, presses a few keys like a player would, and
    reports uncaught errors, console errors, files that failed to load, and a blank page."""
    import websockets
    chrome = _chrome()
    if not chrome:
        return {"ran": False, "ok": True, "errors": [], "note": "Chrome isn't installed, so I couldn't run it."}
    base = SERVER_BASE or "http://127.0.0.1:" + os.environ.get("XOD_PORT", "8000")
    full = url if url.startswith("http") else base + url
    prof = tempfile.mkdtemp(prefix="xod-check-")
    proc = subprocess.Popen([chrome, "--headless=new", f"--user-data-dir={prof}", "--remote-debugging-port=0",
                             "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--mute-audio",
                             "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--window-size=1024,700",
                             "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    errors: List[str] = []
    logs: List[str] = []
    failed: List[str] = []
    urls: Dict[str, str] = {}
    try:
        port_file = Path(prof) / "DevToolsActivePort"
        for _ in range(100):
            if port_file.exists() and port_file.read_text().strip():
                break
            await asyncio.sleep(0.1)
        port = int(port_file.read_text().split()[0])
        import httpx
        async with httpx.AsyncClient() as c:
            targets = (await c.get(f"http://127.0.0.1:{port}/json/list", timeout=5)).json()
        ws_url = next(t["webSocketDebuggerUrl"] for t in targets if t.get("type") == "page")
        async with websockets.connect(ws_url, max_size=50_000_000) as ws:
            n = 0

            async def send(method, params=None):
                nonlocal n
                n += 1
                await ws.send(json.dumps({"id": n, "method": method, "params": params or {}}))
                return n

            for m in ("Runtime.enable", "Log.enable", "Network.enable", "Page.enable"):
                await send(m)
            await send("Page.navigate", {"url": full})
            blank_id = None
            end = time.time() + seconds + 4
            pressed = False
            loaded_at = None
            while time.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.25)
                except asyncio.TimeoutError:
                    raw = None
                if raw:
                    msg = json.loads(raw)
                    meth, p = msg.get("method"), msg.get("params") or {}
                    if blank_id is not None and msg.get("id") == blank_id:
                        blank = (msg.get("result", {}).get("result", {}) or {}).get("value")
                        return {"ran": True, "ok": not _dedupe(errors) and not failed and not blank, "errors": _dedupe(errors)[:12],
                                "failed": _dedupe(failed)[:12], "blank": bool(blank), "logs": _dedupe(logs)[:12]}
                    if meth == "Network.requestWillBeSent":
                        urls[p.get("requestId")] = p.get("request", {}).get("url", "")
                    elif meth == "Runtime.exceptionThrown":
                        d = p.get("exceptionDetails", {})
                        desc = (d.get("exception") or {}).get("description") or d.get("text", "Error")
                        where = f" ({str(d.get('url', '')).split('/')[-1]}:{d.get('lineNumber', 0) + 1})" if d.get("url") else ""
                        errors.append(f"{desc.splitlines()[0]}{where}")
                    elif meth == "Runtime.consoleAPICalled":
                        text = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))[:500]
                        (errors if p.get("type") == "error" else logs).append(text)
                    elif meth == "Log.entryAdded" and "MIME type ('text/plain')" in p.get("entry", {}).get("text", ""):
                        pass       # a missing file: already reported as a 404
                    elif meth == "Log.entryAdded" and p.get("entry", {}).get("level") == "error":
                        e = p["entry"]
                        text = e.get("text", "")
                        if "favicon" not in (e.get("url") or "") and e.get("source") != "network":   # network failures come below, with the file name
                            errors.append(text[:500] + (f" ({e['url'].split('/')[-1]})" if e.get("url") else ""))
                    elif meth == "Network.responseReceived":
                        r = p.get("response", {})
                        if r.get("status", 200) >= 400 and "favicon" not in r.get("url", ""):
                            failed.append(f"{r['url'].split('/')[-1] or r['url']} ({r['status']})")
                    elif meth == "Network.loadingFailed" and not p.get("canceled"):
                        u = urls.get(p.get("requestId"), "")
                        if "favicon" not in u and not any(u.split("/")[-1] in f for f in failed):
                            failed.append(f"{u.split('/')[-1] or u or 'a file'} ({p.get('errorText', 'failed')})")
                    elif meth == "Page.loadEventFired":
                        loaded_at = time.time()
                if loaded_at and not pressed and keys and time.time() - loaded_at > 0.8:
                    pressed = True     # play a little: arrow keys, space, a click in the middle
                    for k, code in (("ArrowRight", 39), ("ArrowUp", 38), (" ", 32), ("w", 87)):
                        for t in ("keyDown", "keyUp"):
                            await send("Input.dispatchKeyEvent", {"type": t, "key": k, "windowsVirtualKeyCode": code,
                                                                  "code": "Space" if k == " " else ("Key" + k.upper() if len(k) == 1 else k)})
                    for t in ("mousePressed", "mouseReleased"):
                        await send("Input.dispatchMouseEvent", {"type": t, "x": 512, "y": 350, "button": "left", "clickCount": 1})
                if loaded_at and blank_id is None and time.time() - loaded_at > seconds:
                    blank_id = await send("Runtime.evaluate", {"returnByValue": True, "expression":
                        "(() => { const b = document.body; if (!b) return true; const vis = [...b.querySelectorAll('canvas,svg,img,video,button,input,h1,h2,p,div')]"
                        ".some(e => { const r = e.getBoundingClientRect(); return r.width > 20 && r.height > 20; });"
                        " return !vis && b.innerText.trim().length < 3; })()"})
            return {"ran": True, "ok": not _dedupe(errors) and not failed and loaded_at is not None, "errors": _dedupe(errors)[:12],
                    "failed": _dedupe(failed)[:12], "blank": False, "logs": _dedupe(logs)[:12],
                    "note": None if loaded_at else "The page didn't finish loading."}
    except Exception as e:
        logger.warning(f"preview check failed: {e}")
        return {"ran": False, "ok": True, "errors": [], "note": f"Couldn't run the check: {e}"}
    finally:
        proc.kill()
        shutil.rmtree(prof, ignore_errors=True)


# Headless Chrome can't capture the mouse, so a shooter that locks the pointer on click always "fails" there;
# in the preview pane and its own window it works (the sandbox allows pointer lock). Not a bug to fix.
HEADLESS_ONLY_RE = re.compile(r"pointer ?lock|WrongDocumentError|requestPointerLock", re.I)


def _dedupe(items: List[str]) -> List[str]:
    seen, out = set(), []
    for i in items:
        if i and HEADLESS_ONLY_RE.search(i):
            continue
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out
