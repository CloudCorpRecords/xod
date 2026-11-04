"""
browser-ops in Xod: the adapter between Xod's agent and the vendored module (app/vendor/browser_ops).

The module is a tab registry with owners and heartbeats over a Chromium's DevTools port, a sweeper,
per-tab and desktop screenshots, and tools.json, its agent tool surface. It is vendored unchanged and
never imported: Xod runs its own CLI (src/browser_ops.py) and wrapper (bin/bops) as programs and reads
their one JSON object back, exactly as the module's contract says. Everything Xod-specific lives here.

Where it runs ("devices"):
  local   the browser on this computer. Offered only when this computer has no screen tools of its own
          (Xod's "Use this computer's screen" is not ready: off, not allowed, or not a Mac or Windows),
          and the config has a device_runtime block. Xod deploys the module here the way its README
          says a host provisions a device: the engine next to a config.json holding the device_runtime
          block, in <data>/browser-ops/device/.
  remote  the device in the config's remote block, reached over SSH by the module's own wrapper, which
          Xod places at <data>/browser-ops/bin/bops so that it reads the one config file. That device
          keeps its own deployment (engine, config.json, the desktop capture task), as its README says.

Configuration: one file, the module's own format (config.example.json), at <data>/browser-ops/config.json
(XOD_BROWSER_OPS_DIR moves the folder). Nothing about browser-ops is in Xod's settings. The device's
config.json and the wrapper's copy are written from it; edit the one file.

Safety, kept exactly as the module has it, plus one Xod-side guard:
  - the module's own rules are untouched: the sweeper and close-mine close only registered tabs (close-mine
    only the caller's), never a tab with a fresh heartbeat, and never leave the browser with no tab;
  - a config that turns the module's last_tab_guard off is refused, locally and on the remote device;
  - the window guard: the module's last-tab guard is browser-wide (its tab list has no windows), so before
    any sweep or close-mine Xod asks the browser which window each tab is in and refuses the whole call,
    saying why, if it would close every tab of a window. Unknown windows are refused too (fail closed);
  - owner is the calling lane's: Xod sets it from who is calling (a task, the chat), so an agent cannot act
    as another lane. A different owner in the call is ignored, and the result says so.
"""
import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.paths import DATA_DIR

logger = logging.getLogger("xod.browser_ops")

MODULE_DIR = Path(__file__).resolve().parents[1] / "vendor" / "browser_ops"
TOOLS_JSON = MODULE_DIR / "tools.json"


def home() -> Path:
    return Path(os.environ.get("XOD_BROWSER_OPS_DIR") or (Path(DATA_DIR) / "browser-ops"))


def config_path() -> Path:
    return home() / "config.json"


def device_dir() -> Path:
    return home() / "device"


# tool -> the module CLI subcommand (tools.json "cli"); tabs_register is open or register by its arguments
CLI = {"tabs_heartbeat": "heartbeat", "tabs_mark_done": "done", "tabs_list": "list", "tabs_close_mine": "close-mine",
       "tabs_sweep_now": "sweep", "screen_capture_tab": "shot-tab", "screen_capture_desktop": "shot-desktop"}
TOOL_NAMES = ("tabs_register",) + tuple(CLI)
CLOSING = {"tabs_close_mine", "tabs_sweep_now"}
CALL_TIMEOUT = 150


class ConfigError(Exception):
    pass


# ------------------------------------------------------------------ config and the tool surface

def config() -> Dict[str, Any]:
    p = config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ConfigError(f"{p} is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise ConfigError(f"{p} must hold a JSON object")
    return data


def surface() -> Dict[str, Any]:
    """tools.json, as the module ships it."""
    return json.loads(TOOLS_JSON.read_text(encoding="utf-8"))


_native: Dict[str, Any] = {"at": 0.0, "value": False}


def native_observation() -> bool:
    """True when this computer has Xod's own screen tools ready (services/desktop.py)."""
    if time.time() - _native["at"] < 60:
        return _native["value"]
    try:
        from app.services import desktop
        value = bool(desktop.permissions().get("ready"))
    except Exception:
        value = False
    _native.update(at=time.time(), value=value)
    return value


def devices() -> List[str]:
    """Where these tools can run now: 'local' when this computer lacks native observation and the config has a
    device_runtime block, 'remote' when the config names a device to reach."""
    try:
        cfg = config()
    except ConfigError:
        return []
    out = []
    if isinstance(cfg.get("device_runtime"), dict) and not native_observation():
        out.append("local")
    if isinstance(cfg.get("remote"), dict) and cfg["remote"].get("ssh_user_host"):
        out.append("remote")
    return out


def available() -> bool:
    return bool(devices())


def tool_definitions(devs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """tools.json as Xod tool definitions: names and descriptions verbatim. Two schema changes, both enforced
    here instead: the top-level anyOf of tabs_register (url or target_id; tool schemas take no top-level anyOf)
    and owner, which Xod fills in. A device argument is added when there is more than one device."""
    devs = devices() if devs is None else devs
    out = []
    for t in surface()["tools"]:
        schema = copy.deepcopy(t["input_schema"])
        schema.pop("anyOf", None)
        props = schema.setdefault("properties", {})
        if "owner" in props:
            props["owner"] = {"type": "string", "description": "Filled in by Xod with your lane id (this task or chat); "
                                                               "leave it out."}
            schema["required"] = [r for r in schema.get("required", []) if r != "owner"]
        if t["name"] == "tabs_register":
            props["url"]["description"] += " Give exactly one of url or target_id."
        if len(devs) > 1:
            props["device"] = {"type": "string", "enum": devs,
                               "description": "Which browser: 'local' is this computer's, 'remote' the configured device's."}
        if not schema.get("required"):
            schema.pop("required", None)
        out.append({"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": schema}})
    return out


# ------------------------------------------------------------------ provisioning (the README's own steps)

def _sync(src: Path, dst: Path) -> None:
    if dst.exists() and hashlib.sha256(dst.read_bytes()).digest() == hashlib.sha256(src.read_bytes()).digest():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def provision_local() -> Path:
    """This computer as a device: the engine (and, on Windows, the desktop capture script) next to a config.json
    holding the device_runtime block."""
    rt = config().get("device_runtime")
    if not isinstance(rt, dict):
        raise ConfigError("the config has no device_runtime block for this computer")
    if rt.get("last_tab_guard", True) is False:
        raise ConfigError("the config turns last_tab_guard off; Xod does not run browser-ops without it")
    d = device_dir()
    _sync(MODULE_DIR / "src" / "browser_ops.py", d / "browser_ops.py")
    if sys.platform == "win32":
        _sync(MODULE_DIR / "src" / "desktop_shot.ps1", d / "desktop_shot.ps1")
    _write_json(d / "config.json", rt)
    return d


def provision_host() -> Path:
    """The module's wrapper where it reads the one config file (it reads config.json next to its own folder)."""
    remote = config().get("remote")
    if not isinstance(remote, dict) or not remote.get("ssh_user_host"):
        raise ConfigError("the config has no remote block with ssh_user_host")
    dst = home() / "bin" / "bops"
    _sync(MODULE_DIR / "bin" / "bops", dst)
    return dst


# ------------------------------------------------------------------ running the module

def _parse(proc: subprocess.CompletedProcess) -> Dict[str, Any]:
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if lines:
        try:
            out = json.loads(lines[-1])
            if isinstance(out, dict):
                return out
        except ValueError:
            pass
    return {"ok": False, "error": "browser-ops gave no JSON answer", "stderr": (proc.stderr or "")[-400:],
            "stdout": (proc.stdout or "")[-400:]}


def run_cli(device: str, argv: List[str], timeout: float = CALL_TIMEOUT) -> Dict[str, Any]:
    """One module subcommand on one device; its JSON object back."""
    if device == "local":
        d = provision_local()
        cmd = [sys.executable, str(d / "browser_ops.py"), *argv]
        env = {**os.environ, "BOPS_CONFIG": str(d / "config.json")}
    elif device == "remote":
        cmd = [sys.executable, str(provision_host()), *argv]
        env = dict(os.environ)
    else:
        raise ConfigError(f"unknown device {device!r}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"browser-ops did not answer within {int(timeout)} s"}
    return _parse(proc)


# The window guard's probe: runs where the browser is (here, or on the remote device over the same SSH
# settings the module's wrapper uses). It reads that device's browser-ops config.json and registry, asks the
# browser which window each page tab is in, and picks the tabs the requested close would close, by the
# module's own rules and the device's own clock. Needs websocket-client, which the module's shot-tab needs too.
PROBE = r'''
import json, os, sys, urllib.request
from datetime import datetime
cfg_path, mode, owner = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "")
here = os.path.dirname(os.path.abspath(cfg_path))
cfg = {"cdp_host": "127.0.0.1", "cdp_port": 9222, "registry_path": "registry.json", "stale_minutes": 45, "last_tab_guard": True}
if os.path.exists(cfg_path):
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg.update(json.load(fh))
reg_path = cfg["registry_path"] if os.path.isabs(cfg["registry_path"]) else os.path.join(here, cfg["registry_path"])
base = "http://%s:%d" % (cfg["cdp_host"], int(cfg["cdp_port"]))
def get(p):
    with urllib.request.urlopen(base + p, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))
out = {"ok": True, "last_tab_guard": cfg.get("last_tab_guard", True) is not False}
try:
    pages = [t for t in get("/json/list") if t.get("type") == "page"]
    reg = {"tabs": {}}
    if os.path.exists(reg_path):
        with open(reg_path, "r", encoding="utf-8") as fh:
            reg = json.load(fh)
    live = {t["id"] for t in pages}
    now = datetime.now().astimezone()
    closing = []
    for tid, e in (reg.get("tabs") or {}).items():
        if tid not in live:
            continue
        if mode == "close-mine":
            if e.get("owner") == owner:
                closing.append(tid)
        else:
            hb = None
            try:
                hb = datetime.fromisoformat(e.get("last_heartbeat", ""))
            except Exception:
                hb = None
            if e.get("done") or hb is None or (now - hb).total_seconds() > float(cfg.get("stale_minutes", 45)) * 60.0:
                closing.append(tid)
    import websocket
    ws = websocket.create_connection(get("/json/version")["webSocketDebuggerUrl"], timeout=20)
    windows = {}
    try:
        for i, t in enumerate(pages):
            ws.send(json.dumps({"id": i + 1, "method": "Browser.getWindowForTarget", "params": {"targetId": t["id"]}}))
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == i + 1:
                    break
            if "result" in m:
                windows[t["id"]] = m["result"]["windowId"]
    finally:
        ws.close()
    out.update(pages=[{"target_id": t["id"], "url": t.get("url", ""), "title": t.get("title", "")} for t in pages],
               windows=windows, closing=closing)
except Exception as e:
    out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
print(json.dumps(out))
'''


def _quote(arg: str) -> str:       # the module wrapper's own quoting for the device shell
    if any(c in arg for c in ' \t"'):
        return '"' + arg.replace('"', "'") + '"'
    return arg


def probe(device: str, mode: str, owner: str = "") -> Dict[str, Any]:
    if device == "local":
        cmd = [sys.executable, "-", str(provision_local() / "config.json"), mode, owner]
    else:
        remote = config()["remote"]
        opts = [os.path.expanduser(o) for o in remote.get("ssh_options", [])]
        dev_dir = remote.get("device_dir", "")
        sep = "\\" if "\\" in dev_dir else "/"
        remote_cmd = " ".join([remote.get("python", "python"), "-", _quote(dev_dir + sep + "config.json"), mode, _quote(owner or "-")])
        cmd = ["ssh", *opts, remote["ssh_user_host"], remote_cmd]
    try:
        proc = subprocess.run(cmd, input=PROBE, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "the window check did not answer within 60 s"}
    return _parse(proc)


def window_guard(device: str, mode: str, owner: str = "") -> Optional[Dict[str, Any]]:
    """None when the close may run; otherwise the refusal, with the reason. Fails closed."""
    p = probe(device, mode, owner)
    if not p.get("ok"):
        return {"ok": False, "refused": True, "error": f"Not done: Xod could not check which window each tab is in "
                                                         f"({p.get('error') or 'no answer'}), so nothing was closed."}
    if not p.get("last_tab_guard"):
        return {"ok": False, "refused": True, "error": "Not done: that device's browser-ops config turns last_tab_guard "
                                                       "off. Xod does not close tabs without it."}
    closing, windows = set(p.get("closing") or []), p.get("windows") or {}
    unknown = [t for t in closing if t not in windows]
    if unknown:
        return {"ok": False, "refused": True, "error": f"Not done: the browser did not say which window holds "
                                                       f"{', '.join(unknown)}, so nothing was closed."}
    by_window: Dict[Any, List[str]] = {}
    for tid, w in windows.items():
        by_window.setdefault(w, []).append(tid)
    emptied = [w for w, tabs in by_window.items() if tabs and all(t in closing for t in tabs)]
    if emptied:
        titles = {x["target_id"]: x.get("title") or x.get("url") for x in p.get("pages") or []}
        which = "; ".join(", ".join(f"{t} ({titles.get(t, '')[:60]})" for t in by_window[w]) for w in emptied)
        return {"ok": False, "refused": True, "would_empty_windows": [by_window[w] for w in emptied],
                "error": f"Not done: this would close the last tab in a window ({which}). Xod never closes a window's "
                         f"last tab. Nothing was closed; open another tab in that window first, or leave it."}
    return None


# ------------------------------------------------------------------ the tools

def lane_owner(origin: str) -> str:
    return "xod-" + (re.sub(r"[^A-Za-z0-9_.-]+", "-", origin or "chat").strip("-") or "chat")


def argv_for(name: str, args: Dict[str, Any], owner: str) -> List[str]:
    if name == "tabs_register":
        url, target = (args.get("url") or "").strip(), (args.get("target_id") or "").strip()
        if bool(url) == bool(target):
            raise ValueError("tabs_register needs exactly one of url (open a new tab) or target_id (claim an open tab).")
        task = str(args.get("task") or "").strip()
        if not task:
            raise ValueError("tabs_register needs task: what this tab is for.")
        return (["open", "--owner", owner, "--task", task, "--url", url] if url
                else ["register", "--owner", owner, "--task", task, "--target", target])
    sub = CLI[name]
    if name in ("tabs_heartbeat", "tabs_mark_done"):
        return [sub, "--owner", owner] + (["--target", str(args["target_id"])] if args.get("target_id") else [])
    if name == "tabs_close_mine":
        return [sub, "--owner", owner]
    if name == "screen_capture_tab":
        if not args.get("target_id"):
            raise ValueError("screen_capture_tab needs target_id (from tabs_list or tabs_register).")
        return [sub, "--target", str(args["target_id"])]
    return [sub]


def call_sync(name: str, args: Dict[str, Any], origin: str) -> Dict[str, Any]:
    args = dict(args or {})
    devs = devices()
    if not devs:
        return {"success": False, "error": "browser-ops isn't set up for this computer: add "
                f"{config_path()} (the module's config.example.json format), or this computer already has "
                "its own screen tools."}
    device = args.get("device") or devs[0]
    if device not in devs:
        return {"success": False, "error": f"device must be one of: {', '.join(devs)}."}
    owner = lane_owner(origin)
    try:
        argv = argv_for(name, args, owner)
        if name in CLOSING:
            refused = window_guard(device, "close-mine" if name == "tabs_close_mine" else "sweep", owner)
            if refused:
                return {"success": False, "device": device, **refused}
        out = run_cli(device, argv)
    except (ValueError, ConfigError) as e:
        return {"success": False, "error": str(e)}
    out = {"success": bool(out.get("ok")), "device": device, **out}
    if args.get("owner") and args["owner"] != owner:
        out["owner_note"] = f"owner is this lane's own ({owner}); the owner given ({args['owner']}) was not used"
    if out.get("ok") is False and "error" in out:
        out["error"] = str(out["error"])
    return out


def resolve_shot_target(args: Dict[str, Any], origin: str) -> Optional[Dict[str, Any]]:
    """screen_capture_tab only (it reads, never changes anything): a target_id that is not a live tab id, such as
    "the example.com tab" or a plan's "<target_id from step 1>", is matched against the live tabs' URLs and
    titles. Exactly one match is used, and the result says so; none, or several, is answered with the
    candidates. Returns a refusal, or None after setting args["target_id"]."""
    given = str(args.get("target_id") or "")
    listing = run_cli(args.get("device") or devices()[0], ["list"])
    if not listing.get("ok"):
        return None
    rows = [{"target_id": r["target_id"], "url": r.get("live_url", ""), "title": r.get("live_title", ""), "owner": r.get("owner")}
            for r in listing.get("registered", []) if r.get("alive")] + \
           [{**u, "owner": None} for u in listing.get("unregistered_pages", [])]
    if any(r["target_id"] == given for r in rows):
        return None
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9.-]{2,}", given.lower())
             if w not in {"target", "target_id", "tab", "the", "from", "step", "that", "this", "id"}]
    hits = [r for r in rows if words and any(w in (r["url"] + " " + r["title"]).lower() for w in words)]
    mine = [r for r in rows if r["owner"] == lane_owner(origin)]
    if not hits:   # a bare placeholder: the lane's own single live tab is the one it means
        hits = mine if len(mine) == 1 else []
    elif len(hits) > 1:   # several match: the caller's own one, if exactly one of them is its
        own = [r for r in hits if r in mine]
        hits = own if len(own) == 1 else hits
    if len(hits) == 1:
        args["target_id"] = hits[0]["target_id"]
        args["_resolved_from"] = given
        return None
    return {"success": False, "error": f"'{given}' is not a tab id, and it does not name exactly one tab. Use a target_id "
                                       "from this list.", "tabs": rows}


async def call(name: str, args: Dict[str, Any], origin: str) -> Dict[str, Any]:
    args = dict(args or {})
    if name == "screen_capture_tab" and args.get("target_id") and devices():
        refused = await asyncio.to_thread(resolve_shot_target, args, origin)
        if refused:
            return refused
    out = await asyncio.to_thread(call_sync, name, {k: v for k, v in args.items() if k != "_resolved_from"}, origin)
    if args.get("_resolved_from"):
        out["resolved"] = f"'{args['_resolved_from']}' is tab {args['target_id']} ({out.get('title') or out.get('url') or ''})"
    if name.startswith("screen_capture") and out.get("success"):
        img = out.get("local_path") or out.get("path")
        vm, desc = None, None
        try:   # as desktop_look does: the vision model reads the image, so any chat model can use it
            from app.services import vision
            vm = await vision.model()
            desc = await vision.describe(img, "What this browser tab or screen shows: the page, its headings and text, "
                                              "and anything in front of it.") if img and vm else None
        except Exception:
            desc = None
        out["screen"] = (desc or "")[:1500] or (
            f"The vision model ({vm}) could not read the image; it is saved at the path above." if vm
            else "No vision model is set up to read the image; it is saved at the path above.")
    return out


def summary(name: str, result: Dict[str, Any]) -> str:
    """A browser-ops result as the answer-writer reads it: complete, and in words."""
    if not isinstance(result, dict):
        return str(result)[:1500]
    if not result.get("success", result.get("ok")):
        return f"failed: {result.get('error')}" + (f" Tabs: {json.dumps(result.get('tabs'))[:1200]}" if result.get("tabs") else "")
    if name == "tabs_list":
        reg = [f"{r.get('live_title') or '(no title)'} <{r.get('live_url')}> id {r['target_id']}, owned by {r.get('owner')} "
               f"for '{r.get('task')}'{', done' if r.get('done') else ''}{'' if r.get('alive') else ', gone'}"
               for r in result.get("registered", [])]
        unreg = [f"{u.get('title') or '(no title)'} <{u.get('url')}> id {u['target_id']}, owned by nobody (unregistered)"
                 for u in result.get("unregistered_pages", [])]
        return (f"{result.get('page_tab_count')} tab(s) on {result.get('device')}. " + " | ".join(reg + unreg))[:3000]
    keep = {k: v for k, v in result.items() if k not in ("success", "ok")}
    return json.dumps(keep, default=str)[:2500]


_WANTS_RE = re.compile(r"\b(tabs?|browser|chrome|chromium|screenshots?|screen ?shots?|screen|desktop|web ?page|page|sweep|"
                       r"browser-ops|cdp)\b", re.I)


def wanted(query: str) -> bool:
    return bool(_WANTS_RE.search(query or ""))


# A request about the shared browser itself: its tabs and their owners, not a web errand.
_ADDRESSED_RE = re.compile(r"\bshared (browser|chrome|chromium)\b|\bbrowser-ops\b|\b(sweep(er)?|heartbeat)\b"
                           r"|\btabs?\b[^?!\n]{0,60}\b(own|owns|owned|owner|owners|whose|registered|register|claim|mine|my own|lanes?)\b"
                           r"|\b(own|owns|owned|owner|owners|whose|registered|register|claim|my own|lanes?)\b[^?!\n]{0,60}\btabs?\b"
                           r"|\bclose (only )?my (own )?tabs\b|\bscreenshot (of )?(the|that|this|a) [^?!\n]{0,40}\btab\b", re.I)


def addressed(query: str) -> bool:
    return bool(_ADDRESSED_RE.search(query or ""))


def planner_lines() -> List[str]:
    """The tools as the planner's list shows them (the planner only plans with tools named in its prompt)."""
    devs = devices()
    if not devs:
        return []
    lines = [f"7. Shared browser on {' and '.join(devs)} (browser-ops; register every tab you open, look before you act, "
             "close only your own). For the shared browser and its tabs use these, not browser_* (those drive Xod's own "
             "separate browser). A later step can use an earlier step's target_id:"]
    for t in tool_definitions(devs):
        f = t["function"]
        args = {k: "..." for k in f["parameters"].get("properties", {}) if k != "owner"}
        lines.append(f"   - {f['name']}: {f['description'][:120]} Args: {json.dumps(args)}")
    return lines
