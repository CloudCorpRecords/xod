"""
1-Click Superpowers Setup Shelf for Xod Control Center.

Makes high-impact optional tools (Tailscale, GitHub CLI, Ollama, Media & Dev Pack,
1Password CLI, Cloudflare Tunnel) dead-simple for everyday users to check, install,
and connect without touching a terminal.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List

logger = logging.getLogger("xod.superpowers")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _run(cmd: List[str], timeout: int = 12) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=NO_WINDOW,
    )


def _check_tailscale() -> Dict[str, Any]:
    from app.services import remote_devices
    info = remote_devices.list_devices()
    installed = bool(info.get("tailscale_installed"))
    running = bool(info.get("tailscale_running"))
    ts_self = info.get("self") or {}
    online = info.get("online_count", 0)
    total = info.get("total_count", 0)

    if running:
        detail = f"Connected as {ts_self.get('name') or 'this device'} ({ts_self.get('ip') or 'tailnet'}) · {online}/{total} peers online."
    elif installed:
        detail = "Installed, not signed in yet. Click Sign In to connect your devices."
    else:
        detail = "Connect all your Macs, PCs, servers & phones so Xod can jump into any device over private mesh + SSH."

    return {
        "id": "tailscale",
        "name": "Tailscale",
        "tagline": "Private multi-device mesh & remote SSH",
        "installed": installed,
        "connected": running,
        "status_label": f"Connected ({online} online)" if running else ("Installed · Sign in" if installed else "Not installed"),
        "detail": detail,
        "can_install": not installed,
        "can_login": installed and not running,
        "can_enable_ssh": running and sys.platform != "win32",
        "peers_online": online,
        "peers_total": total,
        "self_ip": ts_self.get("ip", ""),
    }


def _check_gh() -> Dict[str, Any]:
    path = shutil.which("gh")
    installed = bool(path)
    authed = False
    if path:
        try:
            r = _run([path, "auth", "status"], timeout=6)
            authed = r.returncode == 0
        except Exception:
            pass
    return {
        "id": "gh",
        "name": "GitHub CLI",
        "tagline": "Push code, inspect PRs, releases & Actions",
        "installed": installed,
        "connected": authed,
        "status_label": "Signed in" if authed else ("Installed" if installed else "Not installed"),
        "detail": "Ready for repos, PRs, and GitHub releases." if authed else (
            "Installed — sign in with `gh auth login`." if installed else "Lets Xod manage repos, PRs, and releases directly."
        ),
        "can_install": not installed,
    }


def _check_ollama() -> Dict[str, Any]:
    path = shutil.which("ollama")
    installed = bool(path) or os.path.isdir("/Applications/Ollama.app")
    running = False
    if installed:
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1.2) as resp:
                running = resp.status == 200
        except Exception:
            pass
    return {
        "id": "ollama",
        "name": "Ollama",
        "tagline": "Private offline AI models on your hardware",
        "installed": installed,
        "connected": running,
        "status_label": "Running" if running else ("Installed" if installed else "Not installed"),
        "detail": "Local Ollama daemon is online and ready." if running else (
            "Installed — open Ollama to run local models." if installed else "Run fast, private local AI models completely offline."
        ),
        "can_install": not installed,
    }


def _check_media_dev_pack() -> Dict[str, Any]:
    tools = ["ffmpeg", "yt-dlp", "rg", "jq"]
    present = [t for t in tools if shutil.which(t)]
    missing = [t for t in tools if t not in present]
    all_ok = len(missing) == 0
    return {
        "id": "media_dev_pack",
        "name": "Media & Dev Pack",
        "tagline": "ffmpeg, yt-dlp, ripgrep & jq",
        "installed": all_ok,
        "connected": all_ok,
        "status_label": f"{len(present)}/{len(tools)} installed",
        "detail": "All 4 power tools ready." if all_ok else f"Missing: {', '.join(missing)}. 1-click install adds them all.",
        "can_install": not all_ok,
        "missing": missing,
    }


def _check_op() -> Dict[str, Any]:
    installed = bool(shutil.which("op"))
    return {
        "id": "op",
        "name": "1Password CLI",
        "tagline": "Safe secret lookup without pasting keys in chat",
        "installed": installed,
        "connected": installed,
        "status_label": "Installed" if installed else "Not installed",
        "detail": "Ready for vault-backed secret access." if installed else "Lets Xod pull credentials securely via 1Password approval.",
        "can_install": not installed,
    }


def _check_cloudflared() -> Dict[str, Any]:
    installed = bool(shutil.which("cloudflared"))
    return {
        "id": "cloudflared",
        "name": "Cloudflare Tunnel",
        "tagline": "Share local previews securely in 1 click",
        "installed": installed,
        "connected": installed,
        "status_label": "Installed" if installed else "Not installed",
        "detail": "Ready to spin up instant HTTPS tunnels." if installed else "Expose a local dev server over HTTPS without port forwarding.",
        "can_install": not installed,
    }


def status() -> Dict[str, Any]:
    items = [
        _check_tailscale(),
        _check_gh(),
        _check_ollama(),
        _check_media_dev_pack(),
        _check_op(),
        _check_cloudflared(),
    ]
    # Also report cross-chat memory stats so users see it working
    mem_facts = 0
    try:
        from app.services import user_memory
        mem_facts = len(user_memory.facts())
    except Exception:
        pass
    return {
        "items": items,
        "installed_count": sum(1 for i in items if i.get("installed")),
        "total_count": len(items),
        "memory_facts_count": mem_facts,
    }


async def install_superpower(power_id: str) -> Dict[str, Any]:
    pid = (power_id or "").strip().lower()
    from app.services import terminal_pro

    if pid == "media_dev_pack":
        results = []
        for tool in ("ffmpeg", "yt-dlp", "rg", "jq"):
            if not shutil.which(tool):
                results.append(await terminal_pro.install_cli_tool(tool, "auto"))
        ok = all(r.get("ok") for r in results) if results else True
        return {"ok": ok, "power_id": pid, "results": results, "status": status()}

    if pid in ("tailscale", "gh", "ollama", "op", "cloudflared"):
        if pid in ("tailscale", "gh", "ffmpeg", "yt-dlp", "rg", "jq"):
            res = await terminal_pro.install_cli_tool(pid, "auto")
            return {"ok": bool(res.get("ok")), "power_id": pid, "detail": res, "status": status()}
        # Fallback package manager install for ollama / op / cloudflared
        brew = shutil.which("brew")
        winget = shutil.which("winget") if sys.platform == "win32" else None
        cmd = None
        if brew:
            pkg = {"ollama": ["brew", "install", "--cask", "ollama"],
                   "op": ["brew", "install", "--cask", "1password-cli"],
                   "cloudflared": ["brew", "install", "cloudflared"]}[pid]
            cmd = pkg
        elif winget:
            pkg = {"ollama": ["winget", "install", "-e", "--id", "Ollama.Ollama"],
                   "op": ["winget", "install", "-e", "--id", "AgileBits.1Password.CLI"],
                   "cloudflared": ["winget", "install", "-e", "--id", "Cloudflare.cloudflared"]}[pid]
            cmd = pkg
        if not cmd:
            return {"ok": False, "power_id": pid, "error": "No supported package manager (Homebrew or winget) found.", "status": status()}
        try:
            import asyncio
            r = await asyncio.to_thread(_run, cmd, 300)
            return {
                "ok": r.returncode == 0,
                "power_id": pid,
                "stdout": (r.stdout or "")[-2000:],
                "stderr": (r.stderr or "")[-2000:],
                "status": status(),
            }
        except Exception as e:
            return {"ok": False, "power_id": pid, "error": str(e), "status": status()}

    return {"ok": False, "error": f"Unknown superpower: {power_id}"}


def tailscale_action(action: str) -> Dict[str, Any]:
    act = (action or "").strip().lower()
    from app.services import remote_devices
    cli = remote_devices._tailscale_cli()
    if not cli:
        return {"ok": False, "error": "Tailscale is not installed yet. Click Install first."}

    if act == "login":
        if sys.platform == "darwin" and os.path.isdir("/Applications/Tailscale.app"):
            subprocess.Popen(["open", "-a", "Tailscale"])
            return {"ok": True, "message": "Opened Tailscale app — click Sign In from the menu bar to connect your devices."}
        try:
            r = _run([cli, "up"], timeout=15)
            out = (r.stdout or "") + "\n" + (r.stderr or "")
            return {"ok": r.returncode == 0, "message": out.strip()[:400] or "Tailscale sign-in started."}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act == "enable_ssh":
        if sys.platform == "win32":
            return {
                "ok": False,
                "message": "On Windows, enable OpenSSH Server in Settings → System → Optional Features → OpenSSH Server (or run `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0` in Admin PowerShell).",
            }
        try:
            r = _run([cli, "set", "--ssh"], timeout=15)
            out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
            if r.returncode == 0:
                return {"ok": True, "message": "Tailscale SSH is now enabled on this machine!", "status": status()}
            return {
                "ok": False,
                "error": out[:300] or "Could not enable Tailscale SSH automatically (try running `sudo tailscale set --ssh` once).",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": f"Unknown Tailscale action: {action}"}
