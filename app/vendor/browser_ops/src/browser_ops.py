#!/usr/bin/env python3
"""
browser-ops: modular browser operations toolkit for agent hosts.

Runs ON the device that hosts the browser (the "device side"). It talks to a
Chromium browser over the local Chrome DevTools Protocol (CDP) HTTP surface
and maintains a tab ownership registry so multiple agents (lanes) can share
one browser without stepping on each other:

  - Every tab an agent opens or claims is registered to an owner (lane id).
  - Owners heartbeat while working and mark tabs done when finished.
  - A sweeper closes only registered tabs that are done or stale, and never
    touches unregistered tabs or the last remaining tab.
  - Agents can capture a screenshot of a specific tab, or of the whole
    desktop, so they can SEE state instead of guessing from URL lists.

Every subcommand prints exactly one JSON object to stdout. Exit code 0 on
success, 1 on error (the JSON object then carries an "error" field).

No dependency on any particular agent host. A host (Tree today, Xod later)
exposes these operations to its agents as tools; see tools.json.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG = {
    "cdp_host": "127.0.0.1",
    "cdp_port": 9222,
    "registry_path": "registry.json",
    "screenshots_dir": "screenshots",
    "sweep_log_path": "sweep.log",
    "stale_minutes": 45,
    "desktop_shot_task": "BrowserOpsDesktopShot",
    "last_tab_guard": True,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    path = os.environ.get("BOPS_CONFIG") or os.path.join(HERE, "config.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    for key in ("registry_path", "screenshots_dir", "sweep_log_path"):
        if not os.path.isabs(cfg[key]):
            cfg[key] = os.path.join(HERE, cfg[key])
    return cfg


CFG = load_config()


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_iso(text):
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


# --------------------------------------------------------------------------
# CDP HTTP surface
# --------------------------------------------------------------------------

def cdp_url(path):
    return "http://%s:%d%s" % (CFG["cdp_host"], CFG["cdp_port"], path)


def cdp_get(path):
    with urllib.request.urlopen(cdp_url(path), timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_targets():
    return cdp_get("/json/list")


def page_targets():
    return [t for t in list_targets() if t.get("type") == "page"]


def new_tab(url):
    """Open a tab. Newer Chrome requires PUT for /json/new; fall back to GET."""
    encoded = urllib.parse.quote(url, safe=":/?&=%")
    for method in ("PUT", "GET"):
        req = urllib.request.Request(cdp_url("/json/new?" + encoded), method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if method == "PUT" and exc.code in (400, 405):
                continue
            raise
    raise RuntimeError("could not open tab")


def close_tab(target_id):
    with urllib.request.urlopen(cdp_url("/json/close/" + target_id), timeout=15) as resp:
        return resp.read().decode("utf-8", "replace").strip()


# --------------------------------------------------------------------------
# Registry (JSON file, lockfile-guarded read/modify/write)
# --------------------------------------------------------------------------

class RegistryLock:
    def __init__(self, path):
        self.lock_path = path + ".lock"
        self.fd = None

    def __enter__(self):
        deadline = time.time() + 15
        while True:
            try:
                self.fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                if time.time() > deadline:
                    # Break a lock abandoned by a crashed caller.
                    try:
                        os.unlink(self.lock_path)
                    except OSError:
                        pass
                    deadline = time.time() + 15
                time.sleep(0.1)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        try:
            os.unlink(self.lock_path)
        except OSError:
            pass


def registry_load():
    path = CFG["registry_path"]
    if not os.path.exists(path):
        return {"version": 1, "tabs": {}}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("version", 1)
    data.setdefault("tabs", {})
    return data


def registry_save(data):
    path = CFG["registry_path"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def sweep_log(line):
    path = CFG["sweep_log_path"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_open(args):
    target = new_tab(args.url)
    tid = target["id"]
    stamp = now_iso()
    with RegistryLock(CFG["registry_path"]):
        reg = registry_load()
        reg["tabs"][tid] = {
            "owner": args.owner,
            "task": args.task,
            "opened_at": stamp,
            "last_heartbeat": stamp,
            "done": False,
            "done_at": None,
            "registered_url": args.url,
        }
        registry_save(reg)
    return {"ok": True, "target_id": tid, "url": target.get("url", args.url),
            "title": target.get("title", ""), "owner": args.owner}


def cmd_register(args):
    live = {t["id"]: t for t in page_targets()}
    if args.target not in live:
        return {"ok": False, "error": "target not found among live page tabs: " + args.target}
    stamp = now_iso()
    with RegistryLock(CFG["registry_path"]):
        reg = registry_load()
        existing = reg["tabs"].get(args.target)
        if existing and existing["owner"] != args.owner:
            return {"ok": False, "error": "target already registered to owner " + existing["owner"]}
        reg["tabs"][args.target] = {
            "owner": args.owner,
            "task": args.task,
            "opened_at": (existing or {}).get("opened_at", stamp),
            "last_heartbeat": stamp,
            "done": False,
            "done_at": None,
            "registered_url": live[args.target].get("url", ""),
        }
        registry_save(reg)
    return {"ok": True, "target_id": args.target, "owner": args.owner,
            "url": live[args.target].get("url", ""), "title": live[args.target].get("title", "")}


def cmd_heartbeat(args):
    stamp = now_iso()
    count = 0
    with RegistryLock(CFG["registry_path"]):
        reg = registry_load()
        for tid, entry in reg["tabs"].items():
            if entry["owner"] != args.owner:
                continue
            if args.target and tid != args.target:
                continue
            entry["last_heartbeat"] = stamp
            count += 1
        registry_save(reg)
    return {"ok": True, "owner": args.owner, "heartbeats_updated": count, "at": stamp}


def cmd_done(args):
    stamp = now_iso()
    count = 0
    with RegistryLock(CFG["registry_path"]):
        reg = registry_load()
        for tid, entry in reg["tabs"].items():
            if entry["owner"] != args.owner:
                continue
            if args.target and tid != args.target:
                continue
            entry["done"] = True
            entry["done_at"] = stamp
            count += 1
        registry_save(reg)
    return {"ok": True, "owner": args.owner, "marked_done": count, "at": stamp}


def cmd_list(args):
    live = {t["id"]: t for t in page_targets()}
    reg = registry_load()
    registered = []
    for tid, entry in reg["tabs"].items():
        row = dict(entry)
        row["target_id"] = tid
        target = live.get(tid)
        row["alive"] = target is not None
        row["live_url"] = target.get("url", "") if target else ""
        row["live_title"] = target.get("title", "") if target else ""
        registered.append(row)
    unregistered = [
        {"target_id": t["id"], "url": t.get("url", ""), "title": t.get("title", "")}
        for t in live.values() if t["id"] not in reg["tabs"]
    ]
    return {"ok": True, "page_tab_count": len(live),
            "registered": registered, "unregistered_pages": unregistered}


def close_with_guard(target_id):
    """Close a tab, honoring the rule: never leave the browser with zero tabs."""
    pages = page_targets()
    if CFG.get("last_tab_guard", True) and len(pages) <= 1:
        if pages and pages[0]["id"] == target_id:
            new_tab("about:blank")
    close_tab(target_id)


def cmd_close_mine(args):
    closed = []
    with RegistryLock(CFG["registry_path"]):
        reg = registry_load()
        mine = [tid for tid, e in reg["tabs"].items() if e["owner"] == args.owner]
        live_ids = {t["id"] for t in page_targets()}
        for tid in mine:
            if tid in live_ids:
                close_with_guard(tid)
                closed.append(tid)
            del reg["tabs"][tid]
        registry_save(reg)
    return {"ok": True, "owner": args.owner, "closed": closed}


def cmd_sweep(args):
    stale_seconds = float(CFG.get("stale_minutes", 45)) * 60.0
    now = datetime.now().astimezone()
    live = {t["id"]: t for t in page_targets()}
    closed, pruned, kept = [], [], []
    with RegistryLock(CFG["registry_path"]):
        reg = registry_load()
        for tid in list(reg["tabs"].keys()):
            entry = reg["tabs"][tid]
            if tid not in live:
                sweep_log("%s PRUNE tab=%s owner=%s reason=vanished" % (now_iso(), tid, entry["owner"]))
                pruned.append(tid)
                del reg["tabs"][tid]
                continue
            reason = None
            if entry.get("done"):
                reason = "done"
            else:
                hb = parse_iso(entry.get("last_heartbeat", ""))
                if hb is None or (now - hb).total_seconds() > stale_seconds:
                    reason = "stale"
            if reason:
                close_with_guard(tid)
                sweep_log("%s CLOSE tab=%s owner=%s reason=%s url=%s" % (
                    now_iso(), tid, entry["owner"], reason, live[tid].get("url", "")))
                closed.append({"target_id": tid, "owner": entry["owner"], "reason": reason})
                del reg["tabs"][tid]
            else:
                kept.append(tid)
        registry_save(reg)
    return {"ok": True, "closed": closed, "pruned": pruned, "kept": kept,
            "remaining_page_tabs": len(page_targets())}


def capture_tab_png(target_id):
    """Screenshot a tab without focusing it. The reliable route on current
    Chrome is the browser-level socket: Target.attachToTarget in flatten
    mode, then Page.captureScreenshot inside that session. (Calling
    captureScreenshot on a background tab's own socket hangs on Chrome 156,
    verified on the Razer 2026-11-04.)"""
    import websocket  # websocket-client, present on the Razer
    version = cdp_get("/json/version")
    ws = websocket.create_connection(version["webSocketDebuggerUrl"], timeout=30)
    try:
        ws.send(json.dumps({"id": 1, "method": "Target.attachToTarget",
                            "params": {"targetId": target_id, "flatten": True}}))
        session_id = None
        while session_id is None:
            msg = json.loads(ws.recv())
            if msg.get("method") == "Target.attachedToTarget":
                session_id = msg["params"]["sessionId"]
            elif msg.get("id") == 1 and "error" in msg:
                raise RuntimeError("attach failed: " + json.dumps(msg["error"])[:200])
        ws.send(json.dumps({"id": 2, "sessionId": session_id,
                            "method": "Page.captureScreenshot",
                            "params": {"format": "png"}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 2:
                break
        if "result" not in msg or "data" not in msg.get("result", {}):
            raise RuntimeError("captureScreenshot failed: " + json.dumps(msg)[:300])
        return base64.b64decode(msg["result"]["data"])
    finally:
        ws.close()


def cmd_shot_tab(args):
    live = {t["id"]: t for t in list_targets()}
    target = live.get(args.target)
    if not target:
        return {"ok": False, "error": "target not found: " + args.target}
    try:
        import websocket  # noqa: F401
    except ImportError:
        return {"ok": False, "error": "python package websocket-client is required on the device"}
    raw = capture_tab_png(args.target)
    out_dir = CFG["screenshots_dir"]
    os.makedirs(out_dir, exist_ok=True)
    name = "tab-%s-%s.png" % (args.target[:8], datetime.now().strftime("%Y%m%d-%H%M%S"))
    path = os.path.join(out_dir, name)
    with open(path, "wb") as fh:
        fh.write(raw)
    return {"ok": True, "target_id": args.target, "path": path, "bytes": len(raw),
            "title": target.get("title", ""), "url": target.get("url", "")}


def cmd_shot_desktop(args):
    """Desktop capture must run in the interactive session, so this triggers
    the scheduled task installed for that purpose and waits for its PNG."""
    task = CFG.get("desktop_shot_task", "BrowserOpsDesktopShot")
    out_dir = CFG["screenshots_dir"]
    os.makedirs(out_dir, exist_ok=True)
    started = time.time()
    proc = subprocess.run(["schtasks", "/run", "/tn", task],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return {"ok": False,
                "error": "could not start desktop capture task %s: %s" % (task, (proc.stdout + proc.stderr)[:300])}
    deadline = started + 30
    last_seen = {}
    while time.time() < deadline:
        time.sleep(0.5)
        for name in os.listdir(out_dir):
            if name.startswith("desktop-") and name.endswith(".png") and name != "desktop-latest.png":
                full = os.path.join(out_dir, name)
                if os.path.getmtime(full) < started - 2:
                    continue
                size = os.path.getsize(full)
                # Accept only once the file is non-empty and its size has
                # been stable across two polls (the writer may still be
                # flushing when the file first appears).
                if size > 0 and last_seen.get(full) == size:
                    return {"ok": True, "path": full, "bytes": size}
                last_seen[full] = size
    return {"ok": False, "error": "desktop capture task ran but produced no fresh PNG within 30s"}


def cmd_status(args):
    try:
        version = cdp_get("/json/version")
        browser = version.get("Browser", "unknown")
    except Exception as exc:
        return {"ok": False, "error": "CDP unreachable: %s" % exc}
    reg = registry_load()
    return {"ok": True, "browser": browser, "cdp_port": CFG["cdp_port"],
            "page_tab_count": len(page_targets()),
            "registered_tabs": len(reg["tabs"]),
            "registry_path": CFG["registry_path"]}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="browser_ops", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("open", help="open a new tab and register it")
    p.add_argument("--owner", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--url", required=True)
    p.set_defaults(fn=cmd_open)

    p = sub.add_parser("register", help="register (claim) an existing tab")
    p.add_argument("--owner", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--target", required=True)
    p.set_defaults(fn=cmd_register)

    p = sub.add_parser("heartbeat", help="refresh heartbeat for my tabs")
    p.add_argument("--owner", required=True)
    p.add_argument("--target")
    p.set_defaults(fn=cmd_heartbeat)

    p = sub.add_parser("done", help="mark my tabs done (sweeper may close them)")
    p.add_argument("--owner", required=True)
    p.add_argument("--target")
    p.set_defaults(fn=cmd_done)

    p = sub.add_parser("list", help="registry merged with live browser state")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("close-mine", help="close only my registered tabs")
    p.add_argument("--owner", required=True)
    p.set_defaults(fn=cmd_close_mine)

    p = sub.add_parser("sweep", help="close done or stale registered tabs")
    p.set_defaults(fn=cmd_sweep)

    p = sub.add_parser("shot-tab", help="screenshot one tab by target id")
    p.add_argument("--target", required=True)
    p.set_defaults(fn=cmd_shot_tab)

    p = sub.add_parser("shot-desktop", help="screenshot the whole desktop")
    p.set_defaults(fn=cmd_shot_desktop)

    p = sub.add_parser("status", help="CDP and registry summary")
    p.set_defaults(fn=cmd_status)

    args = parser.parse_args(argv)
    try:
        result = args.fn(args)
    except Exception as exc:  # one JSON object on stdout, always
        result = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
