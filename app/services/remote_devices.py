"""
Remote Device Engine (Tailscale + SSH + Saved Device Profiles).

Gives Xod effortless, Muse-style access to the user's other machines on their
Tailscale tailnet or ~/.ssh/config without memorizing IP addresses or wrestling
with raw SSH flags.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.remote_devices")

PROFILES_PATH = Path(DATA_DIR) / "remote_devices.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _run(cmd: List[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=NO_WINDOW,
    )


def _tailscale_cli() -> str:
    try:
        from app.services import home_post
        cli = home_post._tailscale_cli()
        if cli:
            return cli
    except Exception:
        pass
    found = shutil.which("tailscale")
    if found:
        return found
    for cand in (
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        "/opt/homebrew/bin/tailscale",
        "/usr/local/bin/tailscale",
    ):
        if os.path.isfile(cand):
            return cand
    return ""


def _load_profiles() -> Dict[str, Dict[str, Any]]:
    try:
        d = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_profiles(data: Dict[str, Dict[str, Any]]) -> None:
    PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, PROFILES_PATH)
    try:
        os.chmod(PROFILES_PATH, 0o600)
    except OSError:
        pass


def save_device_profile(
    name: str,
    user: str = "",
    note: str = "",
    host: str = "",
    os_name: str = "",
) -> Dict[str, Any]:
    key = (name or "").strip().lower()
    if not key:
        return {"ok": False, "error": "Device name is required."}
    profiles = _load_profiles()
    cur = dict(profiles.get(key) or {})
    if user.strip():
        cur["user"] = user.strip()
    if note.strip():
        cur["note"] = note.strip()
    if host.strip():
        cur["host"] = host.strip()
    if os_name.strip():
        cur["os"] = os_name.strip()
    cur["name"] = name.strip()
    profiles[key] = cur
    _save_profiles(profiles)

    # Also store a concise fact in cross-chat user_memory so every chat knows this device.
    try:
        from app.services import user_memory
        parts = [f"Device '{name.strip()}'"]
        if cur.get("user"):
            parts.append(f"SSH user '{cur['user']}'")
        if cur.get("host"):
            parts.append(f"host {cur['host']}")
        if cur.get("os"):
            parts.append(f"({cur['os']})")
        if cur.get("note"):
            parts.append(f"— {cur['note']}")
        user_memory.add(" ".join(parts), topic="projects", source="device profile")
    except Exception:
        pass

    return {"ok": True, "device": cur}


def _parse_ssh_config() -> List[Dict[str, Any]]:
    cfg = Path.home() / ".ssh" / "config"
    if not cfg.is_file():
        return []
    out: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    try:
        for raw in cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^(\S+)\s+(.+)$", line)
            if not m:
                continue
            k, v = m.group(1).lower(), m.group(2).strip()
            if k == "host":
                names = [n for n in v.split() if "*" not in n and "?" not in n]
                if names:
                    cur = {"name": names[0], "host": names[0], "user": "", "source": "ssh_config", "online": True}
                    out.append(cur)
                else:
                    cur = None
            elif cur is not None:
                if k == "hostname":
                    cur["host"] = v
                elif k == "user":
                    cur["user"] = v
    except Exception:
        pass
    return out


def list_devices() -> Dict[str, Any]:
    """
    Discovers all reachable devices across Tailscale, ~/.ssh/config, and saved profiles.
    """
    cli = _tailscale_cli()
    ts_installed = bool(cli)
    ts_running = False
    ts_self: Dict[str, Any] = {}
    ts_error = ""
    devices: List[Dict[str, Any]] = []
    by_key: Dict[str, Dict[str, Any]] = {}

    if cli:
        try:
            r = _run([cli, "status", "--json"], timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                data = json.loads(r.stdout)
                backend_state = str(data.get("BackendState") or "")
                ts_running = backend_state.lower() == "running"
                s = data.get("Self") or {}
                if s:
                    ips = s.get("TailscaleIPs") or []
                    ts_self = {
                        "name": s.get("HostName") or (s.get("DNSName") or "").split(".")[0],
                        "dns": (s.get("DNSName") or "").rstrip("."),
                        "ip": ips[0] if ips else "",
                        "os": s.get("OS") or "",
                        "online": bool(s.get("Online", True)),
                        "ssh_enabled": bool(s.get("Capabilities") or s.get("CapMap")),
                    }
                for _k, p in (data.get("Peer") or {}).items():
                    ips = p.get("TailscaleIPs") or []
                    ip = ips[0] if ips else ""
                    dns = (p.get("DNSName") or "").rstrip(".")
                    short = (dns.split(".")[0] if dns else "") or (p.get("HostName") or ip)
                    if not short:
                        continue
                    entry = {
                        "name": short,
                        "hostname": p.get("HostName") or short,
                        "dns": dns,
                        "ip": ip,
                        "host": ip or dns or short,
                        "os": (p.get("OS") or "").lower(),
                        "online": bool(p.get("Online", False)),
                        "tailscale_ssh": bool(p.get("sshHostKeys")),
                        "source": "tailscale",
                        "user": "",
                        "note": "",
                    }
                    by_key[short.lower()] = entry
                    devices.append(entry)
            else:
                ts_error = (r.stderr or r.stdout or "").strip()[:220]
        except Exception as e:
            ts_error = str(e)[:220]

    # Merge ~/.ssh/config hosts
    for s_entry in _parse_ssh_config():
        k = s_entry["name"].lower()
        if k in by_key:
            if s_entry.get("user") and not by_key[k].get("user"):
                by_key[k]["user"] = s_entry["user"]
            by_key[k]["ssh_alias"] = s_entry["name"]
        else:
            entry = {
                "name": s_entry["name"],
                "hostname": s_entry["name"],
                "dns": "",
                "ip": "",
                "host": s_entry["host"],
                "os": "",
                "online": True,
                "tailscale_ssh": False,
                "source": "ssh_config",
                "user": s_entry.get("user", ""),
                "note": "",
            }
            by_key[k] = entry
            devices.append(entry)

    # Merge saved device profiles (nicknames, SSH users, notes)
    profiles = _load_profiles()
    for k, prof in profiles.items():
        if k in by_key:
            if prof.get("user"):
                by_key[k]["user"] = prof["user"]
            if prof.get("note"):
                by_key[k]["note"] = prof["note"]
            if prof.get("os") and not by_key[k].get("os"):
                by_key[k]["os"] = prof["os"]
        else:
            entry = {
                "name": prof.get("name") or k,
                "hostname": prof.get("name") or k,
                "dns": "",
                "ip": "",
                "host": prof.get("host") or prof.get("name") or k,
                "os": prof.get("os") or "",
                "online": True,
                "tailscale_ssh": False,
                "source": "saved_profile",
                "user": prof.get("user") or "",
                "note": prof.get("note") or "",
            }
            by_key[k] = entry
            devices.append(entry)

    # Online devices first, then alphabetical
    devices.sort(key=lambda d: (0 if d.get("online") else 1, d.get("name", "").lower()))
    return {
        "ok": True,
        "tailscale_installed": ts_installed,
        "tailscale_running": ts_running,
        "tailscale_error": ts_error,
        "self": ts_self,
        "devices": devices,
        "online_count": sum(1 for d in devices if d.get("online")),
        "total_count": len(devices),
    }


def resolve_device(query: str) -> Optional[Dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return None
    listing = list_devices()
    devs = listing.get("devices") or []
    for d in devs:
        if q in (
            (d.get("name") or "").lower(),
            (d.get("hostname") or "").lower(),
            (d.get("ip") or "").lower(),
            (d.get("dns") or "").lower(),
        ):
            return d
    partial = [
        d for d in devs
        if q in (d.get("name") or "").lower()
        or q in (d.get("hostname") or "").lower()
        or q in (d.get("note") or "").lower()
    ]
    if len(partial) == 1:
        return partial[0]
    return None


def run_command(
    device: str,
    command: str,
    user: str = "",
    timeout: int = 45,
) -> Dict[str, Any]:
    """
    Executes a shell command on a remote Tailscale or SSH device.
    """
    if not command or not command.strip():
        return {"ok": False, "error": "Command is required."}
    ssh_bin = shutil.which("ssh")
    if not ssh_bin:
        return {"ok": False, "error": "OpenSSH client (`ssh`) is not installed on this machine."}

    resolved = resolve_device(device)
    target_host = (resolved.get("ssh_alias") or resolved.get("host") or device).strip() if resolved else device.strip()
    target_user = (user or (resolved.get("user") if resolved else "") or "").strip()
    target_name = resolved.get("name") if resolved else device.strip()

    if resolved and not resolved.get("online") and resolved.get("source") == "tailscale":
        return {
            "ok": False,
            "device": target_name,
            "host": target_host,
            "error": f"{target_name} appears offline on Tailscale right now.",
        }

    dest = f"{target_user}@{target_host}" if target_user else target_host
    ssh_cmd = [
        ssh_bin,
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        dest,
        command,
    ]
    try:
        r = _run(ssh_cmd, timeout=max(5, min(int(timeout or 45), 300)))
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "device": target_name,
            "host": target_host,
            "command": command,
            "error": f"Remote command timed out after {timeout}s.",
        }
    except OSError as e:
        return {"ok": False, "device": target_name, "host": target_host, "error": str(e)}

    stdout = (r.stdout or "")[-12000:]
    stderr = (r.stderr or "")[-4000:]
    hint = ""
    if r.returncode != 0:
        low = stderr.lower()
        if "permission denied" in low or "host key verification" in low:
            hint = (
                f"SSH authentication failed for {dest}. "
                "Enable Tailscale SSH on that machine (Control Center → Superpowers → Enable Remote SSH, or `tailscale set --ssh`), "
                "or pass `user='your_username'` and save it with `remote_device(action='save')`."
            )
        elif "connection refused" in low or "timed out" in low or "could not resolve" in low:
            hint = (
                f"Could not connect to {dest} on port 22. "
                "Make sure Tailscale is running on both machines and SSH / Remote Login is enabled on the target device."
            )
    return {
        "ok": r.returncode == 0,
        "device": target_name,
        "host": target_host,
        "user": target_user,
        "command": command,
        "returncode": r.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "hint": hint,
    }


def inspect_device(device: str, user: str = "") -> Dict[str, Any]:
    """Quick health & environment snapshot of a remote device."""
    resolved = resolve_device(device)
    is_win = bool(resolved and "win" in (resolved.get("os") or "").lower())
    cmd = (
        'powershell -NoProfile -Command "hostname; whoami; (Get-CimInstance Win32_OperatingSystem | Select-Object Caption,FreePhysicalMemory,TotalVisibleMemorySize | ConvertTo-Json -Compress)"'
        if is_win
        else "hostname && whoami && uname -srm && uptime && df -h / | tail -n 1"
    )
    return run_command(device, cmd, user=user, timeout=25)


def read_remote_file(device: str, path: str, max_lines: int = 200, user: str = "") -> Dict[str, Any]:
    """Reads up to max_lines from a file on a remote device."""
    if not path or not path.strip():
        return {"ok": False, "error": "Remote file path is required."}
    n = max(1, min(int(max_lines or 200), 1000))
    resolved = resolve_device(device)
    is_win = bool(resolved and "win" in (resolved.get("os") or "").lower())
    if is_win:
        safe_p = path.replace("'", "''")
        cmd = f"powershell -NoProfile -Command \"Get-Content -Path '{safe_p}' -TotalCount {n}\""
    else:
        cmd = f"head -n {n} -- {shlex.quote(path)}"
    return run_command(device, cmd, user=user, timeout=25)
