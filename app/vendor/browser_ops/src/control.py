"""Device-side CDP control. Fixed scripts, explicit targets, no user-supplied JS."""
import json
import os
import re
import subprocess
import sys
import time

try:
    from .control_inputs import validate
except ImportError:
    from control_inputs import validate


# Indices refer to this bounded, currently visible interactive map. No DOM writes.
ELEMENTS = r"""
const all = [...document.querySelectorAll('button,a[href],input,textarea,select,[role="button"],[contenteditable="true"],[tabindex]')];
const visible = e => { const r=e.getBoundingClientRect(),s=getComputedStyle(e);
 return r.width>0 && r.height>0 && r.bottom>0 && r.right>0 &&
 r.top<innerHeight && r.left<innerWidth && s.visibility!=='hidden' && s.display!=='none'; };
const items=all.filter(visible).slice(0,500);
const label=e=>(e.getAttribute('aria-label')||e.labels?.[0]?.innerText||e.innerText||e.getAttribute('placeholder')||'').trim().slice(0,160);
const selector=e=>{if(e.id)return '#'+CSS.escape(e.id);const parts=[];
 while(e&&e.nodeType===1&&e!==document.documentElement){const tag=e.tagName.toLowerCase();
 const peers=e.parentElement?[...e.parentElement.children].filter(x=>x.tagName===e.tagName):[e];
 parts.unshift(tag+':nth-of-type('+(peers.indexOf(e)+1)+')');e=e.parentElement;}
 return 'html>'+parts.join('>');};
const describe=e=>{const r=e.getBoundingClientRect();return {index:items.indexOf(e)+1,
 kind:e.tagName.toLowerCase(),selector:selector(e),text:label(e),
 rect:{x:r.x,y:r.y,width:r.width,height:r.height}};};
"""


class Session:
    def __init__(self, target, cdp_get, list_targets):
        if not any(t.get("id") == target and t.get("type") == "page" for t in list_targets()):
            raise ValueError("The explicit page target does not exist.")
        self.target, self.cdp_get, self.counter = target, cdp_get, 0
        self.deadline = time.monotonic() + 45

    def __enter__(self):
        url = self.cdp_get("/json/version")["webSocketDebuggerUrl"]
        try:
            import websocket
        except ImportError:
            try:
                from websockets.sync.client import connect
            except ImportError as exc:
                raise RuntimeError("Install websocket-client or websockets on the device.") from exc
            self.ws, self.ws_client = connect(url, open_timeout=10), False
        else:
            self.ws, self.ws_client = websocket.create_connection(url, timeout=10), True
        self.session = None
        try:
            self.session = self.call("Target.attachToTarget", {
                "targetId": self.target, "flatten": True})["sessionId"]
            return self
        except BaseException:
            self.ws.close()
            raise

    def call(self, method, params=None):
        self.counter += 1
        request = {"id": self.counter, "method": method, "params": params or {}}
        if self.session:
            request["sessionId"] = self.session
        self.ws.send(json.dumps(request))
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Device operation deadline exceeded.")
            if self.ws_client:
                self.ws.settimeout(remaining)
                message = json.loads(self.ws.recv())
            else:
                message = json.loads(self.ws.recv(timeout=remaining))
            if message.get("id") != self.counter:
                continue
            if "error" in message:
                raise RuntimeError("CDP rejected " + method + ": " + str(message["error"].get("message", ""))[:200])
            return message.get("result", {})

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": False})
        if result.get("exceptionDetails"):
            raise RuntimeError("Page inspection failed; the locator may be invalid or stale.")
        if "value" not in result.get("result", {}):
            raise RuntimeError("Page inspection returned no serializable value.")
        return result["result"]["value"]

    def mouse(self, kind, x, y, **kwargs):
        return self.call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, **kwargs})

    def __exit__(self, *exc):
        try:
            if time.monotonic() < self.deadline:
                session_id, self.session = self.session, None
                self.call("Target.detachFromTarget", {"sessionId": session_id})
        except Exception:
            pass
        finally:
            self.ws.close()


def locate(session, args):
    query = {k: args.get(k) for k in ("selector", "text", "index")}
    return session.evaluate("(()=>{" + ELEMENTS + "const q=" + json.dumps(query) + r""";
const matches=q.selector?[...document.querySelectorAll(q.selector)].filter(visible):
 q.text?items.filter(e=>label(e)===q.text):items.filter((e,i)=>i+1===q.index);
if(matches.length!==1)throw Error('Locator must identify exactly one visible element');
const e=matches[0],r=e.getBoundingClientRect();
return {...describe(e),x:Math.max(0,r.left)+(Math.min(innerWidth,r.right)-Math.max(0,r.left))/2,
y:Math.max(0,r.top)+(Math.min(innerHeight,r.bottom)-Math.max(0,r.top))/2,
editable:e.matches('input:not([type="password"]),textarea'),value:e.type==='password'?null:e.value,
selection_start:e.selectionStart,selection_end:e.selectionEnd};
})()""")


def press(session, key, modifiers=""):
    key = {"Return": "Enter", "Esc": "Escape", "Space": " "}.get(key, key)
    bits = {"alt": 1, "ctrl": 2, "control": 2, "meta": 4, "cmd": 4, "command": 4, "shift": 8}
    mask = 0
    for value in modifiers.split(","):
        mask |= bits.get(value.strip().lower(), 0)
    codes = {"Enter": 13, "Escape": 27, "Tab": 9, "Backspace": 8, "Delete": 46,
             "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40,
             "Home": 36, "End": 35, "PageUp": 33, "PageDown": 34, " ": 32}
    code = codes.get(key, ord(key.upper()) if len(key.upper()) == 1 else
                     (111 + int(key[1:]) if re.fullmatch(r"F\d+", key) else 0))
    params = {"key": key, "modifiers": mask, "windowsVirtualKeyCode": code}
    if not mask and (len(key) == 1 or key == "Enter"):
        params["text"] = "\r" if key == "Enter" else key
    session.call("Input.dispatchKeyEvent", {"type": "keyDown", **params})
    params.pop("text", None)
    session.call("Input.dispatchKeyEvent", {"type": "keyUp", **params})


def open_app(alias, cfg):
    allowed = cfg.get("allowed_apps") or {}
    spec = allowed.get(alias)
    if not isinstance(spec, dict):
        return {"ok": False, "error": "App alias is not configured in allowed_apps. Nothing was launched."}
    if sys.platform == "darwin":
        bundle = spec.get("bundle_id", "")
        if not isinstance(bundle, str) or not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", bundle):
            return {"ok": False, "error": "This app needs a valid configured bundle_id."}
        result = subprocess.run(["/usr/bin/open", "-b", bundle], capture_output=True, timeout=15)
        if result.returncode:
            return {"ok": False, "error": "macOS rejected the app launch."}
    elif sys.platform == "win32":
        executable = spec.get("executable", "")
        if not isinstance(executable, str) or not os.path.isabs(executable) or not executable.lower().endswith(".exe") or not os.path.isfile(executable):
            return {"ok": False, "error": "This app needs an existing absolute .exe path in allowed_apps."}
        subprocess.Popen([executable], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        return {"ok": False, "error": "Native app launching is supported only on macOS and Windows."}
    return {"ok": True, "application": alias, "launch_requested": True, "verified_visible": False}


def run_action(action, args, cfg, cdp_get, list_targets):
    error = validate(action, args)
    if error:
        return {"ok": False, "error": error}
    try:
        if action == "open_app":
            return open_app(args["application"], cfg)
        with Session(args["target"], cdp_get, list_targets) as session:
            if action == "look":
                result = session.evaluate("(()=>{" + ELEMENTS + r"""
return {title:document.title,url:location.href,elements:items.map(describe),
text:(document.body?.innerText||'').slice(0,16000),
viewport:{width:innerWidth,height:innerHeight}};})()""")
                return {"ok": True, "target_id": args["target"], **result}
            if action in ("click", "hover", "type_text"):
                element = locate(session, args)
                if action == "type_text" and not element["editable"]:
                    return {"ok": False, "error": "Target is not a supported non-password text input. Nothing was typed."}
                session.mouse("mouseMoved", element["x"], element["y"])
                if action != "hover":
                    try:
                        session.mouse("mousePressed", element["x"], element["y"], button="left", clickCount=1)
                    finally:
                        session.mouse("mouseReleased", element["x"], element["y"], button="left", clickCount=1)
                if action == "type_text":
                    if args.get("clear"):
                        press(session, "a", "meta" if sys.platform == "darwin" else "ctrl")
                    before = locate(session, args)
                    start, end = before.get("selection_start"), before.get("selection_end")
                    expected = args["value"] if args.get("clear") else (
                        before["value"][:start] + args["value"] + before["value"][end:]
                        if type(start) is int and type(end) is int and isinstance(before.get("value"), str)
                        else None)
                    session.call("Input.insertText", {"text": args["value"]})
                    actual = locate(session, args).get("value")
                    verified = expected is not None and actual == expected
                    return {"ok": verified, "value_now": actual, "read_back_verified": verified,
                            "outcome_unknown": not verified,
                            **({} if verified else {"error": "Typing was dispatched but read-back did not verify. Inspect before retrying."})}
            elif action == "press":
                press(session, args["key"], args.get("modifiers", ""))
            elif action in ("scroll", "drag"):
                viewport = session.evaluate("({width:innerWidth,height:innerHeight})")
                if action == "scroll":
                    session.mouse("mouseWheel", viewport["width"] / 2, viewport["height"] / 2,
                                  deltaX=args["delta_x"], deltaY=args["delta_y"])
                else:
                    sx, sy, ex, ey = (args[k] for k in ("start_x", "start_y", "end_x", "end_y"))
                    if max(sx, ex) >= viewport["width"] or max(sy, ey) >= viewport["height"]:
                        return {"ok": False, "error": "Drag coordinates are outside the current viewport."}
                    session.mouse("mouseMoved", sx, sy)
                    try:
                        session.mouse("mousePressed", sx, sy, button="left", buttons=1, clickCount=1)
                        for step in range(1, args.get("steps", 10) + 1):
                            fraction = step / args.get("steps", 10)
                            session.mouse("mouseMoved", sx+(ex-sx)*fraction, sy+(ey-sy)*fraction,
                                          button="left", buttons=1)
                    finally:
                        session.mouse("mouseReleased", ex, ey, button="left", buttons=0, clickCount=1)
            else:
                return {"ok": False, "error": "Unsupported control operation."}
            return {"ok": True, "target_id": args["target"], "action": action, "dispatched": True}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:250]}",
                "outcome_unknown": action != "look"}


def add_commands(sub, cfg, cdp_get, list_targets):
    for verb in ("look", "click", "type", "press", "scroll", "hover", "drag", "open-app"):
        parser = sub.add_parser(verb, help="Explicit-target computer control: " + verb)
        action = {"type": "type_text", "open-app": "open_app"}.get(verb, verb)
        if verb == "open-app":
            parser.add_argument("--app", dest="application", required=True)
        else:
            parser.add_argument("--target", required=True)
        if verb in ("click", "hover", "type"):
            group = parser.add_mutually_exclusive_group(required=True)
            group.add_argument("--selector", default="")
            group.add_argument("--text", default="")
            if verb != "type":
                group.add_argument("--index", type=int)
        if verb == "type":
            parser.add_argument("--value", required=True)
            parser.add_argument("--clear", action="store_true")
        elif verb == "press":
            parser.add_argument("--key", required=True)
            parser.add_argument("--modifiers", default="")
        elif verb == "scroll":
            parser.add_argument("--delta-x", type=int, default=0)
            parser.add_argument("--delta-y", type=int, default=0)
        elif verb == "drag":
            for flag in ("start-x", "start-y", "end-x", "end-y"):
                parser.add_argument("--" + flag, type=int, required=True)
            parser.add_argument("--steps", type=int, default=10)
        parser.set_defaults(fn=lambda args, action=action: run_action(
            action, vars(args), cfg, cdp_get, list_targets))
