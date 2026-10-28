"""
Your Xods: sign in once and every Xod you have finds the others.

Sign-in is Tailscale's (tailscale.com, free): one click opens its page, where you pick Google, Microsoft, GitHub or Apple.
Every computer signed into the same account (the tailnet) can then reach the others privately, from anywhere. Xod keeps
no accounts of its own.

  status()     Tailscale installed / signed in, and this computer's name on it
  sign_in()    starts Tailscale's sign-in and opens its page in the browser
  find()       the Xods on the tailnet: each online device is asked "are you a Xod?" (the beacon below)

The beacon is a tiny listener on this computer's Tailscale address only (BEACON_PORT), so only your tailnet can reach
it. It runs while you're signed in and the setting xod_findable isn't "off":
  GET  /.well-known/xod     this computer's name and OS, nothing else
  POST /v1/tasks            {request, from}: a task from another of your Xods
  GET  /v1/tasks/{ref}      whether it was approved, and how the task is going
A handed task never runs by itself: it becomes an approval card on this computer (in the chat and on the phone), and
only the person's Approve puts it in the task inbox. Cards are made only for devices signed in to the same account as
this one: Tailscale vouches for who is on the other end of each connection (`tailscale whois`), so a shared machine or
another person's device is refused. A caller can only read the tasks it handed over.
"""
import asyncio
import json
import logging
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xod.network")

BEACON_PORT = int(os.environ.get("XOD_BEACON_PORT", "8791"))
FINDABLE_SETTING = "xod_findable"
SEEN_SETTING = "xod_network_seen"
DOWNLOAD_URL = "https://tailscale.com/download"
PROBE_SEC = 2.0
MAX_REQUEST = 4000
_URL_RE = re.compile(r"https://login\.tailscale\.com/\S+")

_beacon = {"server": None, "task": None, "ip": ""}
_login = {"proc": None}
_incoming: Dict[str, Dict[str, Any]] = {}     # ref -> {request, sender, caller, grant, approval_id, task_id}


class NetworkError(Exception):
    pass


def cli() -> str:
    from app.services import home_post
    return home_post._tailscale_cli()


def _status_json() -> Dict[str, Any]:
    path = cli()
    if not path:
        return {}
    try:
        r = subprocess.run([path, "status", "--json"], capture_output=True, text=True, timeout=8)
        return json.loads(r.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}


def _ipv4(node: Dict[str, Any]) -> str:
    ips = [ip for ip in node.get("TailscaleIPs") or [] if ":" not in ip]
    return ips[0] if ips else ""


def status() -> Dict[str, Any]:
    """{installed, signed_in, name, ip, account}"""
    if not cli():
        return {"installed": False, "signed_in": False, "download": DOWNLOAD_URL}
    st = _status_json()
    me = st.get("Self") or {}
    signed_in = st.get("BackendState") == "Running" and bool(me.get("DNSName"))
    user = (st.get("User") or {}).get(str(me.get("UserID"))) or {}
    return {"installed": True, "signed_in": signed_in, "state": st.get("BackendState") or "",
            "name": (me.get("DNSName") or "").rstrip(".").split(".")[0], "ip": _ipv4(me) if signed_in else "",
            "account": user.get("LoginName") or "", "signing_in": _login_running()}


def _login_running() -> bool:
    p = _login["proc"]
    return p is not None and p.poll() is None


def sign_in(open_browser: bool = True) -> Dict[str, Any]:
    """Starts Tailscale's sign-in. {url} to open, or {signed_in: True} when it already is, or {download} without Tailscale."""
    path = cli()
    if not path:
        if open_browser:
            webbrowser.open(DOWNLOAD_URL)
        return {"success": False, "installed": False, "download": DOWNLOAD_URL,
                "message": "Install Tailscale (free) first; its download page is open."}
    if status()["signed_in"]:
        return {"success": True, "signed_in": True}
    if _login_running():
        _login["proc"].terminate()
    proc = subprocess.Popen([path, "login"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    _login["proc"] = proc
    found: Dict[str, str] = {}

    def read():
        for line in proc.stdout:
            m = _URL_RE.search(line)
            if m and "url" not in found:
                found["url"] = m.group(0)
    threading.Thread(target=read, daemon=True).start()
    until = time.time() + 20
    while time.time() < until and "url" not in found and proc.poll() is None:
        time.sleep(0.2)
    if "url" in found:
        if open_browser:
            webbrowser.open(found["url"])
        return {"success": True, "url": found["url"], "message": "Tailscale's sign-in page is open: pick Google, Microsoft, GitHub or Apple."}
    if status()["signed_in"]:
        return {"success": True, "signed_in": True}
    return {"success": False, "error": "Tailscale didn't give a sign-in link. Open the Tailscale app and sign in there."}


def _seen() -> Dict[str, Any]:
    from app.services.storage import get_setting
    try:
        return json.loads(get_setting(SEEN_SETTING, "{}") or "{}")
    except ValueError:
        return {}


async def _probe(ip: str) -> Optional[Dict[str, Any]]:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=PROBE_SEC) as c:
            r = await c.get(f"http://{ip}:{BEACON_PORT}/.well-known/xod")
        d = r.json() if r.status_code == 200 else {}
        return d if d.get("service") == "xod" else None
    except (httpx.HTTPError, ValueError):
        return None


async def find() -> Dict[str, Any]:
    """{signed_in, me, xods: [{name, host, ip, os, online, same_account, last_seen}], others}: the Xods on the tailnet,
    asked now when online; ones seen before that are offline now are listed as offline."""
    st = await asyncio.to_thread(_status_json)
    me = st.get("Self") or {}
    if not cli() or st.get("BackendState") != "Running":
        return {"signed_in": False, "xods": [], "others": 0}
    peers = [p for p in (st.get("Peer") or {}).values() if _ipv4(p)]
    online = [p for p in peers if p.get("Online")]
    answers = await asyncio.gather(*[_probe(_ipv4(p)) for p in online])
    seen, now = _seen(), time.time()
    xods = []
    for p, a in zip(online, answers):
        if not a:
            continue
        host = (p.get("DNSName") or "").rstrip(".").split(".")[0]
        seen[host] = now
        xods.append({"name": a.get("machine") or p.get("HostName") or host, "host": host, "ip": _ipv4(p),
                     "os": a.get("os") or p.get("OS") or "", "online": True, "same_account": p.get("UserID") == me.get("UserID"),
                     "last_seen": now})
    for p in peers:
        host = (p.get("DNSName") or "").rstrip(".").split(".")[0]
        if host in seen and not any(x["host"] == host for x in xods):
            xods.append({"name": p.get("HostName") or host, "host": host, "ip": _ipv4(p), "os": p.get("OS") or "",
                         "online": False, "same_account": p.get("UserID") == me.get("UserID"), "last_seen": seen[host]})
    from app.services.storage import set_setting
    set_setting(SEEN_SETTING, json.dumps(seen))
    return {"signed_in": True, "me": (me.get("DNSName") or "").rstrip(".").split(".")[0], "xods": xods,
            "others": len(peers) - len(xods), "findable": beacon_running()}


async def _target(machine: str) -> Dict[str, Any]:
    want = (machine or "").strip().lower()
    if not want:
        raise NetworkError("Which computer? Say its name.")
    f = await find()
    if not f["signed_in"]:
        raise NetworkError("This computer isn't signed in to Tailscale yet (Your Xods → Sign in).")
    mine = [x for x in f["xods"] if x["same_account"]]
    hit = [x for x in mine if want in (x["host"].lower(), x["name"].lower(), x["ip"])] or \
          [x for x in mine if want in x["host"].lower() or want in x["name"].lower()]
    if not hit:
        raise NetworkError(f"No Xod of yours called '{machine}'. Your Xods: {', '.join(x['name'] for x in mine) or 'none yet'}.")
    if len(hit) > 1:
        raise NetworkError(f"'{machine}' matches more than one: {', '.join(x['name'] for x in hit)}.")
    if not hit[0]["online"]:
        raise NetworkError(f"{hit[0]['name']} is offline.")
    return hit[0]


async def _call(method: str, ip: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.request(method, f"http://{ip}:{BEACON_PORT}{path}", json=body)
    except httpx.HTTPError as e:
        raise NetworkError(f"Couldn't reach it: {e}")
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code != 200:
        raise NetworkError(str(d.get("detail") or f"It answered {r.status_code}."))
    return d


async def send_task(machine: str, request: str) -> Dict[str, Any]:
    """Hands a task to another of your Xods; it waits there for your OK. {machine, ref, status}"""
    x = await _target(machine)
    me = (await asyncio.to_thread(status)).get("name") or socket.gethostname()
    r = await _call("POST", x["ip"], "/v1/tasks", {"request": request, "from": me})
    return {"machine": x["name"], **r, "message": f"Sent to {x['name']}: it runs once you approve it there (the card is on that computer and your phone)."}


async def task_status(machine: str, ref: str) -> Dict[str, Any]:
    x = await _target(machine)
    return {"machine": x["name"], **await _call("GET", x["ip"], f"/v1/tasks/{ref}")}


# ---------------------------------------------------------------- the beacon

def findable() -> bool:
    from app.services.storage import get_setting
    return get_setting(FINDABLE_SETTING, "on") != "off"


def beacon_running() -> bool:
    t = _beacon["task"]
    return t is not None and not t.done()


def _about() -> Dict[str, Any]:
    return {"service": "xod", "version": 1, "machine": socket.gethostname().split(".")[0],
            "os": {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, platform.system())}


def _caller(ip: str) -> Dict[str, Any]:
    """Who Tailscale says is on the other end: {host, same_account}."""
    path = cli()
    try:
        r = subprocess.run([path, "whois", "--json", ip], capture_output=True, text=True, timeout=8) if path else None
        who = json.loads(r.stdout or "{}") if r and r.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        who = {}
    me = (_status_json().get("Self") or {}).get("UserID")
    them = (who.get("UserProfile") or {}).get("ID")
    host = ((who.get("Node") or {}).get("ComputedName") or "").strip()
    return {"host": host, "same_account": me is not None and them is not None and str(me) == str(them)}


def receive_task(caller_ip: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """A task from another device: an approval card here when it's one of this account's own, else NetworkError.
    {ref, status: "waiting_for_ok"}"""
    who = _caller(caller_ip)
    if not who["same_account"]:
        raise NetworkError("Only your own devices can hand this Xod work.")
    request = str(body.get("request") or "").strip()
    if not request:
        raise ValueError("No task given.")
    if len(request) > MAX_REQUEST:
        raise ValueError(f"The task is over {MAX_REQUEST} characters.")
    sender = str(body.get("from") or who["host"] or caller_ip)[:60]
    from app.services import sentinel
    ref, grant = uuid.uuid4().hex[:12], uuid.uuid4().hex
    held = sentinel.hold("xod_network_accept", {"ref": ref, "_grant": grant},
                         {"rule": "xod_network_task", "why": f"Your Xod on {sender} wants this computer to do: {request[:300]}"})
    _incoming[ref] = {"request": request, "sender": sender, "caller": who["host"] or caller_ip, "grant": grant,
                      "approval_id": held.get("approval_id"), "task_id": None}
    logger.info(f"Xod network: task from {sender} is waiting for approval ({ref})")
    return {"ref": ref, "status": "waiting_for_ok"}


def accept(ref: str, grant: str) -> Dict[str, Any]:
    """The card's Approve: the task goes into the task inbox. Only with the card's own grant."""
    inc = _incoming.get(ref)
    if not inc or not grant or grant != inc["grant"] or inc["task_id"]:
        return {"success": False, "error": "That handed-over task isn't waiting any more."}
    from app.services.task_queue import task_queue
    t = task_queue.create(inc["request"])
    task_queue._update(t["id"], note=f"Handed over by your Xod on {inc['sender']}, approved by you")
    inc.update(task_id=t["id"], grant="")
    return {"success": True, "task_id": t["id"], "title": t["title"], "message": f"Added to your tasks: {t['title']}"}


def read_task(caller_ip: str, ref: str) -> Dict[str, Any]:
    who = _caller(caller_ip)
    if not who["same_account"]:
        raise NetworkError("Only your own devices can read this Xod's tasks.")
    inc = _incoming.get(ref)
    if not inc or inc["caller"] not in (who["host"] or caller_ip, caller_ip):
        raise KeyError(ref)
    if not inc["task_id"]:
        from app.services.mcp_client import mcp_hub
        waiting = inc["approval_id"] in mcp_hub.pending
        return {"ref": ref, "status": "waiting_for_ok" if waiting else "declined"}
    from app.services.task_queue import task_queue
    t = task_queue.get(inc["task_id"]) or {}
    return {"ref": ref, **{k: t.get(k) for k in ("title", "status", "now", "waiting_kind", "result", "finished_at")}}


async def _start_beacon(ip: str) -> None:
    import uvicorn
    from fastapi import FastAPI, HTTPException, Request
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.get("/.well-known/xod")(_about)

    @app.post("/v1/tasks")
    async def handed_task(request: Request) -> Dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "Send JSON.")
        try:
            return await asyncio.to_thread(receive_task, request.client.host, body if isinstance(body, dict) else {})
        except NetworkError as e:
            raise HTTPException(403, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/v1/tasks/{ref}")
    async def handed_status(ref: str, request: Request) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(read_task, request.client.host, ref)
        except NetworkError as e:
            raise HTTPException(403, str(e))
        except KeyError:
            raise HTTPException(404, "No task like that was handed over from there.")
    try:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((ip, BEACON_PORT))
    except OSError as e:
        logger.warning(f"Xod network: port {BEACON_PORT} on {ip} isn't free ({e}); other Xods won't find this one.")
        return
    server = uvicorn.Server(uvicorn.Config(app, host=ip, port=BEACON_PORT, log_level="warning", access_log=False, lifespan="off"))
    server.install_signal_handlers = lambda: None
    _beacon.update(server=server, ip=ip, task=asyncio.get_running_loop().create_task(server.serve()))
    for _ in range(50):
        if getattr(server, "started", False):
            break
        await asyncio.sleep(0.1)
    logger.info(f"Xod network: findable at {ip}:{BEACON_PORT} on your tailnet")


async def _stop_beacon() -> None:
    if _beacon["server"] is not None:
        _beacon["server"].should_exit = True
    if _beacon["task"] is not None:
        try:
            await asyncio.wait_for(_beacon["task"], 5)
        except Exception:
            pass
    _beacon.update(server=None, task=None, ip="")


async def sync_beacon() -> bool:
    """Beacon on when signed in and findable, on the current Tailscale address; off otherwise. Returns whether it runs."""
    st = await asyncio.to_thread(status)
    want = st.get("ip") if st.get("signed_in") and findable() else ""
    if beacon_running() and _beacon["ip"] != want:
        await _stop_beacon()
    if want and not beacon_running():
        await _start_beacon(want)
    return beacon_running()


async def run_forever(every: float = 60.0) -> None:
    while True:
        try:
            await sync_beacon()
        except Exception as e:
            logger.warning(f"Xod network check failed: {e}")
        await asyncio.sleep(every)
