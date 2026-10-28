"""
This Xod's own post office: Agent Mail with no shared host. services/post_office.py runs here, on its own port
(8790, or XOD_HOME_POST_PORT) and only that, never the rest of Xod. Your address is name@<how others reach this
computer>:8790, and mail for you waits on this computer instead of on someone else's server.

How other agents reach it (networks()):
  network     the same Wi-Fi or office network: <this computer>.local (Bonjour/mDNS names, built into Macs and Windows)
  tailscale   a private network across the internet (tailscale.com, free): <this computer>.<tailnet>.ts.net, for your
              own devices and people you invite to your tailnet
A shared post office (a relay anyone runs, relay/) is still optional, for agents that can't reach this one.

Mail is end-to-end encrypted and signed whatever carries it, so these private networks use plain HTTP.
When your computer is off or asleep, mail to you can't arrive: the sender's Xod keeps it in its outbox and retries.
"""
import asyncio
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.home_post")

PORT = int(os.environ.get("XOD_HOME_POST_PORT", "8790"))
DB_PATH = DATA_DIR / "home_post.db"
_server = None              # the running uvicorn.Server
_task: Optional[asyncio.Task] = None
_domain = {"value": ""}     # what addresses on it are called (set when it starts)


def _run(cmd: List[str], timeout: float = 4.0) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:
        return ""


def local_name() -> str:
    """This computer's name on the local network (what <name>.local finds)."""
    name = _run(["/usr/sbin/scutil", "--get", "LocalHostName"]) if sys.platform == "darwin" else ""
    name = name or socket.gethostname().split(".")[0]
    return re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")


def lan_ip() -> str:
    """This computer's address on the local network (the interface that would reach the internet)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))       # nothing is sent: this only picks the interface
            return s.getsockname()[0]
    except OSError:
        return ""


def _tailscale_cli() -> str:
    for c in (shutil.which("tailscale"), "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
              r"C:\Program Files\Tailscale\tailscale.exe"):
        if c and os.path.exists(c):
            return c
    return ""


def tailscale() -> Optional[Dict[str, str]]:
    """This computer on Tailscale, if it's installed and connected: {"name": <host>.<tailnet>.ts.net, "ip": 100.x}."""
    cli = _tailscale_cli()
    if not cli:
        return None
    try:
        st = json.loads(_run([cli, "status", "--json"]) or "{}")
    except ValueError:
        return None
    me = st.get("Self") or {}
    if st.get("BackendState") != "Running" or not me.get("DNSName"):
        return None
    ips = [ip for ip in me.get("TailscaleIPs") or [] if ":" not in ip]
    return {"name": me["DNSName"].rstrip(".").lower(), "ip": ips[0] if ips else ""}


def networks() -> List[Dict[str, Any]]:
    """The ways other agents could reach this computer, best first."""
    out = []
    ts = tailscale()
    if ts:
        out.append({"kind": "tailscale", "domain": f"{ts['name']}:{PORT}",
                    "label": "Tailscale: your devices and people on your tailnet, from anywhere"})
    name = local_name()
    if name:
        out.append({"kind": "network", "domain": f"{name}.local:{PORT}",
                    "label": "This network: agents on the same Wi-Fi or office network"})
    if not ts and _tailscale_cli():
        out.append({"kind": "tailscale_signin", "domain": "", "label": "Tailscale is installed: open it and sign in"})
    if not ts and not _tailscale_cli():
        out.append({"kind": "tailscale_missing", "domain": "", "label": "For agents elsewhere, install Tailscale (free): tailscale.com/download"})
    return out


def pick(choice: str = "") -> str:
    """The domain for "network", "tailscale", a domain given as is, or the best one there is."""
    nets = [n for n in networks() if n["domain"]]
    if choice and ("." in choice or ":" in choice):
        return choice.strip().lower()
    for n in nets:
        if not choice or n["kind"] == choice:
            return n["domain"]
    return nets[0]["domain"] if nets else f"localhost:{PORT}"


def is_private_host(host: str) -> bool:
    """Hosts reached over a private network, where plain HTTP is fine (the mail itself is end-to-end encrypted)."""
    host = host.lower()
    if host in ("localhost",) or host.endswith((".local", ".localhost", ".ts.net", ".home.arpa", ".lan")):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")


def domain() -> str:
    return _domain["value"]


def base_for(dom: str) -> Optional[str]:
    """This Xod's own post office, asked directly (it's on this computer), when dom is its domain."""
    return f"http://127.0.0.1:{PORT}" if running() and dom and dom.lower() == domain().lower() else None


def running() -> bool:
    return _server is not None and not getattr(_server, "should_exit", True) and _task is not None and not _task.done()


async def start(dom: str) -> Dict[str, Any]:
    """Runs the post office for addresses @dom. Listens on every network interface, on its own port."""
    global _server, _task
    _domain["value"] = dom
    if running():
        return {"success": True, "domain": dom, "port": PORT}
    import uvicorn
    from app.services.post_office import make_app
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    from app.services import agent_mail, phone_pair

    def on_mail(name: str) -> None:      # mail for this Xod itself (e.g. the phone app asking): pick it up now
        if name == (agent_mail._config().get("name") or ""):
            agent_mail.wake()
    app = make_app(str(DB_PATH), domain, pair_code_ok=phone_pair.consume_code, on_mail=on_mail)
    try:
        with socket.socket() as s:       # is the port free? (uvicorn would only log the failure)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", PORT))
    except OSError:
        return {"success": False, "error": f"Port {PORT} on this computer is in use by something else (set XOD_HOME_POST_PORT to another)."}
    cfg = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="warning", access_log=False, lifespan="off")
    _server = uvicorn.Server(cfg)
    _server.install_signal_handlers = lambda: None
    _task = asyncio.get_running_loop().create_task(_server.serve())
    for _ in range(50):
        if getattr(_server, "started", False):
            break
        await asyncio.sleep(0.1)
    logger.info(f"Agent Mail post office on this computer: port {PORT}, addresses @{dom}")
    return {"success": True, "domain": dom, "port": PORT}


async def stop() -> None:
    global _server, _task
    if _server is not None:
        _server.should_exit = True
    if _task is not None:
        try:
            await asyncio.wait_for(_task, 5)
        except Exception:
            pass
    _server, _task = None, None
