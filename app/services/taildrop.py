"""
Moving files between this computer and the person's own devices with Tailscale's file sharing (Taildrop).

Only devices on the person's own tailnet can be targets, and Tailscale itself decides that list:
  tailscale file cp --targets            "<ip>\t<name>[\t<note>]" per device, note "offline; …" when it's away
  tailscale file cp <file> <ip>:         send one file
  tailscale file get --conflict=rename   take what was sent to this computer, without overwriting anything

A send leaves the computer, so the tool asks first (an approval card); this module only does the work.
"""
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List

MAX_BYTES = 4 * 1024 ** 3
INBOX = Path.home() / "Downloads" / "Xod"


class TaildropError(Exception):
    pass


def cli() -> str:
    from app.services import home_post
    return home_post._tailscale_cli()


def _run(args: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
    path = cli()
    if not path:
        raise TaildropError("Tailscale isn't installed on this computer.")
    try:
        return subprocess.run([path, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise TaildropError(f"Tailscale took longer than {timeout}s.")
    except OSError as e:
        raise TaildropError(str(e))


def devices() -> List[Dict[str, Any]]:
    """The person's devices that can take a file: [{name, ip, online, note}]."""
    r = _run(["file", "cp", "--targets"], timeout=20)
    if r.returncode != 0:
        raise TaildropError((r.stderr or r.stdout).strip()[:300] or "Tailscale couldn't list devices (is it signed in?).")
    out = []
    for line in r.stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        note = parts[2].strip() if len(parts) > 2 else ""
        out.append({"name": parts[1].strip(), "ip": parts[0].strip(), "online": not note.lower().startswith("offline"),
                    "note": note})
    return out


def _device(name: str) -> Dict[str, Any]:
    want = (name or "").strip().lower()
    if not want:
        raise TaildropError("Which device? Say its name.")
    found = devices()
    for d in found:
        if want in (d["name"].lower(), d["ip"]):
            return d
    close = [d for d in found if want in d["name"].lower()]
    if len(close) == 1:
        return close[0]
    names = ", ".join(d["name"] for d in found) or "none"
    if len(close) > 1:
        raise TaildropError(f"'{name}' matches more than one device: {', '.join(d['name'] for d in close)}.")
    raise TaildropError(f"No device called '{name}' on your Tailscale. Devices: {names}.")


def check_send(path: str, device: str) -> Dict[str, Any]:
    """What a send would do, before anything leaves: {file, size, device, ip}. Raises TaildropError when it can't."""
    p = Path(os.path.expanduser(path or "")).resolve()
    if not path or not p.is_file():
        raise TaildropError(f"There's no file at {path or '(no path)'}.")
    size = p.stat().st_size
    if size > MAX_BYTES:
        raise TaildropError(f"{p.name} is {size / 1024 ** 3:.1f} GB; the limit here is 4 GB.")
    d = _device(device)
    if not d["online"]:
        raise TaildropError(f"{d['name']} is offline ({d['note']}).")
    return {"file": str(p), "name": p.name, "size": size, "device": d["name"], "ip": d["ip"]}


def send(path: str, device: str) -> Dict[str, Any]:
    plan = check_send(path, device)
    r = _run(["file", "cp", plan["file"], f"{plan['ip']}:"], timeout=600)
    if r.returncode != 0:
        raise TaildropError((r.stderr or r.stdout).strip()[:300] or "Tailscale couldn't send it.")
    return {**plan, "sent": True}


def receive(folder: str = "") -> Dict[str, Any]:
    """Takes files waiting for this computer into folder (default ~/Downloads/Xod). {folder, files}."""
    dest = Path(os.path.expanduser(folder)) if folder else INBOX
    dest.mkdir(parents=True, exist_ok=True)
    before = {x.name for x in dest.iterdir()}
    r = _run(["file", "get", "--conflict=rename", str(dest)], timeout=600)
    if r.returncode != 0:
        raise TaildropError((r.stderr or r.stdout).strip()[:300] or "Tailscale couldn't take the files.")
    new = sorted(x.name for x in dest.iterdir() if x.name not in before)
    return {"folder": str(dest), "files": new}
