"""
The Windows side of Xod's computer tools (services/system_control.py hands off to these on Windows).

  commands     run in Windows PowerShell (what models write best for Windows), not cmd.exe
  open/reveal  os.startfile and Explorer (/select, highlights a file)
  find files   a walk of the folder (no Spotlight on Windows), stopped after a few seconds
  trash        the Recycle Bin (recoverable), through .NET's FileSystem with the path passed as an argument
  clipboard    Get-Clipboard / Set-Clipboard, the text passed on stdin (never pasted into a command)
  ports        psutil
  notify       a tray balloon
  AppleScript  Mac only: says so, and points at PowerShell
"""
import asyncio
import fnmatch
import os
import platform
import subprocess
import time
from typing import Any, Dict, List, Optional

import psutil

POWERSHELL = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def shell_argv(command: str) -> List[str]:
    """A shell command as PowerShell runs it. UTF-8 out, so output isn't mangled."""
    return POWERSHELL + ["[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + command]


def cd_suffix() -> str:
    return "; (Get-Location).Path"


async def exec_shell(command: str, cwd: Optional[str], **kw):
    return await asyncio.create_subprocess_exec(*shell_argv(command), cwd=cwd, creationflags=NO_WINDOW, **kw)


async def _ps(script: str, *args: str, stdin: Optional[bytes] = None, timeout: float = 15) -> Dict[str, Any]:
    """Runs a fixed PowerShell script; values go in as $args, never into the script text."""
    proc = await asyncio.create_subprocess_exec(*POWERSHELL, f"& {{ {script} }} @args", *args,
                                                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=NO_WINDOW)
    out, err = await asyncio.wait_for(proc.communicate(input=stdin), timeout=timeout)
    return {"code": proc.returncode, "out": out.decode("utf-8", "replace"), "err": err.decode("utf-8", "replace").strip()}


def listening_ports() -> List[Dict[str, Any]]:
    seen: Dict[int, Dict[str, Any]] = {}
    try:
        conns = psutil.net_connections(kind="tcp")
    except Exception:
        return []
    for c in conns:
        if c.status != psutil.CONN_LISTEN or not c.laddr:
            continue
        port = c.laddr.port
        name = ""
        try:
            name = psutil.Process(c.pid).name() if c.pid else ""
        except Exception:
            pass
        label = ("LM Studio" if port == 1234 else "Ollama" if port == 11434 else
                 "Xod Server (uvicorn)" if name.lower().startswith("python") and port == 8000 else name)
        seen[port] = {"port": port, "pid": c.pid, "command": label or name, "raw_command": name, "user": ""}
    return sorted(seen.values(), key=lambda p: p["port"])


def inspect_port(port: int) -> Dict[str, Any]:
    procs = []
    try:
        for c in psutil.net_connections(kind="inet"):
            if (c.laddr and c.laddr.port == port) or (c.raddr and c.raddr.port == port):
                try:
                    name = psutil.Process(c.pid).name() if c.pid else ""
                except Exception:
                    name = ""
                procs.append({"command": name, "pid": c.pid, "user": "", "name": f"{c.laddr} {c.status}"})
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "port": port, "in_use": bool(procs), "processes": procs}


async def notify(title: str, message: str) -> Dict[str, Any]:
    script = ("Add-Type -AssemblyName System.Windows.Forms; $n = New-Object System.Windows.Forms.NotifyIcon; "
              "$n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
              "$n.ShowBalloonTip(6000, $args[0], $args[1], 'Info'); Start-Sleep -Seconds 6; $n.Dispose()")
    try:
        asyncio.ensure_future(_ps(script, str(title)[:200], str(message)[:500], timeout=20))
        return {"success": True, "title": title, "message": message}
    except Exception as e:
        return {"success": False, "error": str(e)}


def open_target(target: str) -> Dict[str, Any]:
    try:
        os.startfile(target)     # noqa: S606 (a file, folder or URL the agent was asked to open)
        return {"success": True, "target": target}
    except Exception as e:
        return {"success": False, "error": str(e)}


def reveal(target: str, is_dir: bool) -> Dict[str, Any]:
    try:
        if is_dir:
            os.startfile(target)
        else:
            subprocess.Popen(["explorer", f"/select,{target}"], creationflags=NO_WINDOW)
        return {"success": True, "target": target, "action": "open_folder" if is_dir else "reveal_file",
                "message": f"Opened {os.path.basename(target) or target} in File Explorer."}
    except Exception as e:
        return {"success": False, "error": str(e)}


def find_files(query: str, root: str, max_results: int = 30, budget_s: float = 6.0) -> List[Dict[str, Any]]:
    """Names that contain the query (or match it as a pattern like *.pdf), newest folders first, for a few seconds."""
    q = query.strip().lower()
    pattern = q if any(ch in q for ch in "*?[") else None
    skip = {"node_modules", ".git", "appdata", "$recycle.bin", "windows", "program files", "program files (x86)", "__pycache__", ".venv"}
    out: List[Dict[str, Any]] = []
    t0 = time.time()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in skip and not d.startswith(".")]
        for name in dirnames + filenames:
            low = name.lower()
            if (fnmatch.fnmatch(low, pattern) if pattern else q in low):
                p = os.path.join(dirpath, name)
                try:
                    st = os.stat(p)
                    out.append({"path": p, "name": name, "is_dir": os.path.isdir(p), "size_bytes": 0 if os.path.isdir(p) else st.st_size,
                                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))})
                except OSError:
                    continue
                if len(out) >= max_results:
                    return out
        if time.time() - t0 > budget_s:
            break
    return out


async def to_recycle_bin(path: str) -> Dict[str, Any]:
    script = ("Add-Type -AssemblyName Microsoft.VisualBasic; $p = $args[0]; "
              "if (Test-Path -LiteralPath $p -PathType Container) { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($p, 'OnlyErrorDialogs', 'SendToRecycleBin') } "
              "else { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p, 'OnlyErrorDialogs', 'SendToRecycleBin') }")
    r = await _ps(script, path)
    if r["code"] == 0 and not os.path.exists(path):
        return {"success": True, "action": "trash", "path": path, "note": "Moved to the Recycle Bin (recoverable)"}
    return {"success": False, "error": r["err"] or "couldn't move it to the Recycle Bin"}


async def read_clipboard() -> Dict[str, Any]:
    r = await _ps("[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-Clipboard -Raw")
    text = r["out"].rstrip("\r\n") if r["code"] == 0 else ""
    return {"success": r["code"] == 0, "text": text, "chars": len(text)} if r["code"] == 0 else {"success": False, "error": r["err"]}


async def write_clipboard(text: str) -> Dict[str, Any]:
    r = await _ps("[Console]::InputEncoding = [Text.Encoding]::UTF8; $t = [Console]::In.ReadToEnd(); Set-Clipboard -Value $t",
                  stdin=text.encode("utf-8"))
    return {"success": True, "chars": len(text)} if r["code"] == 0 else {"success": False, "error": r["err"]}


def hardware() -> Dict[str, Any]:
    vm = psutil.virtual_memory()
    batt = None
    try:
        b = psutil.sensors_battery()
        if b:
            batt = {"percent": round(b.percent), "plugged_in": bool(b.power_plugged)}
    except Exception:
        pass
    return {"success": True, "os": f"{platform.system()} {platform.release()} ({platform.version()})", "chip": platform.processor() or platform.machine(),
            "model": platform.node(), "cpu_cores": psutil.cpu_count(logical=True), "physical_cores": psutil.cpu_count(logical=False),
            "ram_gb": round(vm.total / 1024 ** 3, 1), "ram_used_percent": vm.percent, "battery": batt}


APPLESCRIPT_ON_WINDOWS = {"success": False, "error": "AppleScript only exists on a Mac. On this Windows PC, do it with computer_run_command "
                                                      "(it runs Windows PowerShell)."}
