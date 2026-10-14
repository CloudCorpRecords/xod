import asyncio
import os
import platform
import shutil
import subprocess
import time
import json
import uuid
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, AsyncGenerator, Callable
from app.paths import DATA_DIR

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_FILE = DATA_DIR / "computer_state.json"

class SystemControlService:
    """
    Dedicated background computer control and telemetry service for Xod AI.
    Executes commands, retrieves telemetry, and interacts with macOS non-intrusively
    (e.g., background app launches, notifications, Finder reveals) without stealing
    focus or interrupting the user's workflow.
    Maintains persistent host identity, current working directory, and command history.
    """

    def __init__(self):
        self.command_history: List[Dict[str, Any]] = []
        self.background_jobs: Dict[str, Dict[str, Any]] = {}
        self.computer_id: str = f"mac-{uuid.uuid4().hex[:8]}"
        self.current_working_directory: str = os.getcwd()
        self._load_state()

    def _load_state(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self.computer_id = data.get("computer_id", self.computer_id)
                saved_cwd = data.get("current_working_directory")
                if saved_cwd and os.path.isdir(saved_cwd):
                    self.current_working_directory = saved_cwd
                self.command_history = data.get("command_history", [])[-50:]
        except Exception:
            pass

    def _save_state(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "computer_id": self.computer_id,
                "current_working_directory": self.current_working_directory,
                "command_history": self.command_history[-50:],
                "updated_at": time.time()
            }
            STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_computer_profile(self) -> Dict[str, Any]:
        """Returns persistent host machine profile, cwd, and specs."""
        return {
            "computer_id": self.computer_id,
            "host_os": f"{platform.system()} {platform.release()}",
            "arch": platform.machine(),
            "user_home": str(Path.home()),
            "current_working_directory": self.current_working_directory,
            "recent_commands_count": len(self.command_history),
            "background_jobs_count": len(self.background_jobs)
        }

    def get_listening_ports(self) -> List[Dict[str, Any]]:
        """
        Discovers all active listening TCP ports in real time via lsof.
        """
        try:
            out = subprocess.check_output(
                ["lsof", "-iTCP", "-sTCP:LISTEN", "-P", "-n"],
                text=True,
                timeout=4
            ).splitlines()
            ports_map: Dict[int, Dict[str, Any]] = {}
            for line in out[1:]:
                m = re.search(r':(\d+)\s+\(LISTEN\)', line)
                if m:
                    port = int(m.group(1))
                    parts = line.split()
                    raw_cmd = parts[0].replace('\\x20', ' ')
                    pid = int(parts[1]) if parts[1].isdigit() else parts[1]
                    user = parts[2] if len(parts) > 2 else ""
                    # Friendly naming for well known process commands
                    cmd_label = raw_cmd
                    if "LM" in raw_cmd or port == 1234:
                        cmd_label = "LM Studio"
                    elif "ollama" in raw_cmd.lower() or port == 11434:
                        cmd_label = "Ollama"
                    elif "Chrome" in raw_cmd or port == 9228:
                        cmd_label = "Chrome CDP Sandbox"
                    elif "uvicorn" in raw_cmd.lower() or (raw_cmd == "Python" and port == 8000):
                        cmd_label = "Xod Server (uvicorn)"

                    if port not in ports_map:
                        ports_map[port] = {
                            "port": port,
                            "command": cmd_label,
                            "raw_command": raw_cmd,
                            "pid": pid,
                            "user": user
                        }

            def port_sort_key(p):
                common_ports = [8000, 1234, 9228, 11434, 3000, 5173, 8080]
                return (0 if p["port"] in common_ports else 1, p["port"])

            return sorted(ports_map.values(), key=port_sort_key)
        except Exception:
            return []

    def get_system_telemetry(self) -> Dict[str, Any]:
        """
        Gathers real-time host telemetry including CPU load, memory pressure,
        disk storage, top resource-intensive processes (with RSS MB), and active listening ports.
        """
        try:
            load_1, load_5, load_15 = os.getloadavg()
        except Exception:
            load_1, load_5, load_15 = 0.0, 0.0, 0.0

        # Memory stats via vm_stat on macOS
        mem_info = {"total_gb": 0.0, "free_gb": 0.0, "used_gb": 0.0, "pressure_pct": 0.0, "pressure_level": "normal"}
        try:
            if platform.system() == "Darwin":
                out_mem = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
                total_bytes = int(out_mem)
                mem_info["total_gb"] = round(total_bytes / (1024 ** 3), 2)

                # Free = what macOS can hand out right now (free + reclaimable cache), via psutil.
                # (The old vm_stat parse assumed 4 KB pages; Apple silicon uses 16 KB, so it reported
                # ~99% "critical" on a healthy Mac.) Pressure comes from macOS itself.
                import psutil
                vm = psutil.virtual_memory()
                mem_info["free_gb"] = round(vm.available / (1024 ** 3), 2)
                mem_info["used_gb"] = round(max(0.0, mem_info["total_gb"] - mem_info["free_gb"]), 2)
                mem_info["pressure_pct"] = round(mem_info["used_gb"] / mem_info["total_gb"] * 100, 1) if mem_info["total_gb"] else 0.0
                mem_info["swap_used_gb"] = round(psutil.swap_memory().used / (1024 ** 3), 2)
                level = subprocess.check_output(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], text=True).strip()
                mem_info["pressure_level"] = {"1": "normal", "2": "elevated", "4": "critical"}.get(level, "normal")
        except Exception:
            pass

        # Disk stats
        disk_info = {"total_gb": 0.0, "free_gb": 0.0, "used_gb": 0.0, "percent_used": 0.0}
        try:
            statvfs = os.statvfs(os.path.expanduser("~"))
            total = statvfs.f_frsize * statvfs.f_blocks
            free = statvfs.f_frsize * statvfs.f_bavail
            used = total - free
            disk_info["total_gb"] = round(total / (1024 ** 3), 2)
            disk_info["free_gb"] = round(free / (1024 ** 3), 2)
            disk_info["used_gb"] = round(used / (1024 ** 3), 2)
            if total > 0:
                disk_info["percent_used"] = round((used / total) * 100, 1)
        except Exception:
            pass

        # Top 8 CPU & Memory processes with RSS (MB)
        top_procs: List[Dict[str, Any]] = []
        try:
            ps_out = subprocess.check_output(
                ["ps", "-A", "-o", "pid,%cpu,%mem,rss,comm", "-r"],
                text=True
            ).strip().splitlines()
            for line in ps_out[1:9]:
                parts = line.strip().split(None, 4)
                if len(parts) >= 5:
                    rss_val = parts[3]
                    rss_kb = float(rss_val) if rss_val.replace(".", "").isdigit() else 0.0
                    rss_mb = round(rss_kb / 1024.0, 1)
                    raw_cmd = parts[4]
                    top_procs.append({
                        "pid": int(parts[0]) if parts[0].isdigit() else parts[0],
                        "cpu_pct": parts[1],
                        "mem_pct": parts[2],
                        "rss_mb": rss_mb,
                        "command": os.path.basename(raw_cmd),
                        "full_path": raw_cmd
                    })
        except Exception:
            pass

        # Dynamic listening ports
        listening_ports = self.get_listening_ports()

        return {
            "os": f"{platform.system()} {platform.release()}",
            "arch": platform.machine(),
            "cpu_load": {
                "1m": round(load_1, 2),
                "5m": round(load_5, 2),
                "15m": round(load_15, 2)
            },
            "memory": mem_info,
            "disk": disk_info,
            "top_processes": top_procs,
            "listening_ports": listening_ports,
            "active_ports_count": len(listening_ports),
            "background_jobs_count": len(self.background_jobs),
            "current_working_directory": self.current_working_directory,
            "uptime_seconds": time.time()
        }

    async def run_command(self, command: str, cwd: Optional[str] = None, timeout_sec: int = 45) -> Dict[str, Any]:
        """
        Executes a shell command in the background asynchronously with timeout protection.
        Does not steal user screen, focus, or interrupt active tasks.
        """
        target_cwd = cwd or self.current_working_directory or os.getcwd()
        if not os.path.isdir(target_cwd):
            target_cwd = os.getcwd()
        self.current_working_directory = target_cwd

        # Support directory persistence when user/agent calls cd
        effective_command = command
        is_cd = command.strip().startswith("cd ")
        if is_cd and "&& pwd" not in command:
            effective_command = f"{command} && pwd"

        start_t = time.time()
        record: Dict[str, Any] = {
            "command": command,
            "cwd": target_cwd,
            "start_time": start_t,
            "start_time_str": time.strftime("%H:%M:%S", time.localtime(start_t)),
            "status": "running"
        }

        try:
            proc = await asyncio.create_subprocess_shell(
                effective_command,
                cwd=target_cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
                duration = round(time.time() - start_t, 2)

                out_str = stdout.decode("utf-8", errors="replace") if stdout else ""
                err_str = stderr.decode("utf-8", errors="replace") if stderr else ""

                if proc.returncode == 0 and is_cd:
                    lines = [l.strip() for l in out_str.strip().splitlines() if l.strip()]
                    if lines and os.path.isdir(lines[-1]):
                        self.current_working_directory = lines[-1]
                        out_str = f"Changed directory to: {self.current_working_directory}"

                # Truncate output if massive to prevent UI / context exhaustion
                max_chars = 15000
                if len(out_str) > max_chars:
                    out_str = out_str[:max_chars] + f"\n... [Truncated: {len(out_str) - max_chars} additional chars]"
                if len(err_str) > max_chars:
                    err_str = err_str[:max_chars] + f"\n... [Truncated: {len(err_str) - max_chars} additional chars]"

                record.update({
                    "status": "completed" if proc.returncode == 0 else "failed",
                    "exit_code": proc.returncode,
                    "stdout": out_str,
                    "stderr": err_str,
                    "duration_sec": duration,
                    "current_working_directory": self.current_working_directory
                })
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                duration = round(time.time() - start_t, 2)
                record.update({
                    "status": "timed_out",
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"Command timed out after {timeout_sec} seconds.",
                    "duration_sec": duration,
                    "current_working_directory": self.current_working_directory
                })
        except Exception as e:
            record.update({
                "status": "error",
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "duration_sec": round(time.time() - start_t, 2),
                "current_working_directory": self.current_working_directory
            })

        self.command_history.append(record)
        if len(self.command_history) > 50:
            self.command_history.pop(0)

        self._save_state()
        return record

    async def run_command_stream(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_sec: int = 60
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Asynchronously streams stdout and stderr chunks in real time as the process executes.
        Enables an interactive terminal experience with live log streaming and timeout guarantees.
        """
        target_cwd = cwd or self.current_working_directory or os.getcwd()
        if not os.path.isdir(target_cwd):
            target_cwd = os.getcwd()
        self.current_working_directory = target_cwd

        effective_command = command
        is_cd = command.strip().startswith("cd ")
        if is_cd and "&& pwd" not in command:
            effective_command = f"{command} && pwd"

        process_id = f"proc-{uuid.uuid4().hex[:8]}"
        start_t = time.time()

        yield {
            "type": "start",
            "process_id": process_id,
            "command": command,
            "cwd": target_cwd,
            "start_time": start_t,
            "start_time_str": time.strftime("%H:%M:%S", time.localtime(start_t))
        }

        try:
            proc = await asyncio.create_subprocess_shell(
                effective_command,
                cwd=target_cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout_chunks: List[str] = []
            stderr_chunks: List[str] = []

            async def _read_stdout():
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace")
                    stdout_chunks.append(decoded)
                    return_queue.put_nowait({"type": "stdout", "text": decoded})

            async def _read_stderr():
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace")
                    stderr_chunks.append(decoded)
                    return_queue.put_nowait({"type": "stderr", "text": decoded})

            return_queue: asyncio.Queue = asyncio.Queue()
            t_out = asyncio.create_task(_read_stdout())
            t_err = asyncio.create_task(_read_stderr())

            timed_out = False
            async def _waiter():
                await asyncio.gather(t_out, t_err)
                await proc.wait()

            waiter_task = asyncio.create_task(_waiter())

            while not waiter_task.done() or not return_queue.empty():
                try:
                    ev = await asyncio.wait_for(return_queue.get(), timeout=0.1)
                    yield ev
                except asyncio.TimeoutError:
                    if time.time() - start_t > timeout_sec:
                        timed_out = True
                        try:
                            proc.kill()
                            await proc.wait()
                        except Exception:
                            pass
                        break

            if timed_out:
                duration = round(time.time() - start_t, 2)
                yield {
                    "type": "error",
                    "error": f"Command timed out after {timeout_sec} seconds.",
                    "exit_code": -1,
                    "duration_sec": duration
                }
                return

            await waiter_task
            duration = round(time.time() - start_t, 2)
            exit_code = proc.returncode

            full_stdout = "".join(stdout_chunks)
            if exit_code == 0 and is_cd:
                lines = [l.strip() for l in full_stdout.strip().splitlines() if l.strip()]
                if lines and os.path.isdir(lines[-1]):
                    self.current_working_directory = lines[-1]
                    yield {
                        "type": "stdout",
                        "text": f"\nChanged directory to: {self.current_working_directory}\n"
                    }

            record = {
                "command": command,
                "cwd": target_cwd,
                "start_time": start_t,
                "start_time_str": time.strftime("%H:%M:%S", time.localtime(start_t)),
                "status": "completed" if exit_code == 0 else "failed",
                "exit_code": exit_code,
                "stdout": full_stdout[:15000],
                "stderr": "".join(stderr_chunks)[:15000],
                "duration_sec": duration,
                "current_working_directory": self.current_working_directory
            }
            self.command_history.append(record)
            if len(self.command_history) > 50:
                self.command_history.pop(0)
            self._save_state()

            yield {
                "type": "done",
                "process_id": process_id,
                "exit_code": exit_code,
                "status": "completed" if exit_code == 0 else "failed",
                "duration_sec": duration,
                "cwd": self.current_working_directory
            }

        except Exception as e:
            yield {
                "type": "error",
                "error": str(e),
                "exit_code": -1,
                "duration_sec": round(time.time() - start_t, 2)
            }

    async def notify_user(self, title: str, message: str, sound: bool = False) -> Dict[str, Any]:
        """
        Sends a native macOS notification banner without stealing window focus.
        Optionally plays a gentle subtle chime.
        """
        # Text goes in as arguments, never pasted into the script: a message can't break out and run AppleScript.
        line = 'display notification (item 2 of argv) with title (item 1 of argv)' + (' sound name "Tink"' if sound else "")
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", "on run argv", "-e", line, "-e", "end run", str(title)[:200], str(message)[:500],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return {"success": True, "title": title, "message": message}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def reveal_in_finder(self, path: str) -> Dict[str, Any]:
        """
        Reveals a file in Finder (highlighted with -R) or opens a folder in Finder.
        Handles system paths, web paths (/media/...), and relative paths.
        """
        if not path or not str(path).strip():
            path = str(DATA_DIR / "media_studio")

        raw_path = str(path).strip().strip("`'\" ")

        # Handle web /media/ paths
        if raw_path.startswith("/media/") or raw_path.startswith("media/"):
            clean = raw_path.replace("/media/", "").replace("media/", "")
            target_path = DATA_DIR / "media_studio" / clean
        elif raw_path.startswith("~"):
            target_path = Path(os.path.expanduser(raw_path))
        elif Path(raw_path).is_absolute():
            target_path = Path(raw_path)
        else:
            if (DATA_DIR / "media_studio" / raw_path).exists():
                target_path = DATA_DIR / "media_studio" / raw_path
            else:
                target_path = (BASE_DIR / raw_path).resolve()

        if not target_path.exists():
            candidate = DATA_DIR / "media_studio" / target_path.name
            if candidate.exists():
                target_path = candidate
            elif target_path.parent.exists():
                target_path = target_path.parent
            else:
                return {"success": False, "error": f"Path not found on computer: {path}"}

        target_str = str(target_path.resolve())
        try:
            if target_path.is_dir():
                cmd = ["open", target_str]
            else:
                cmd = ["open", "-R", target_str]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return {
                    "success": True,
                    "target": target_str,
                    "action": "open_folder" if target_path.is_dir() else "reveal_file",
                    "message": f"Opened {target_path.name} in macOS Finder."
                }
            else:
                return {"success": False, "error": stderr.decode("utf-8", errors="replace").strip()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def open_folder(self, path: Optional[str] = None) -> Dict[str, Any]:
        """
        Explicitly opens a folder window in macOS Finder.
        """
        if not path or not str(path).strip():
            path = str(DATA_DIR / "media_studio")
        raw_path = str(path).strip().strip("`'\" ")
        if raw_path.startswith("/media/") or raw_path.startswith("media/"):
            target_path = DATA_DIR / "media_studio"
        elif raw_path.startswith("~"):
            target_path = Path(os.path.expanduser(raw_path))
        elif Path(raw_path).is_absolute():
            target_path = Path(raw_path)
        else:
            target_path = (BASE_DIR / raw_path).resolve()

        if target_path.is_file():
            target_path = target_path.parent

        if not target_path.exists():
            target_path = DATA_DIR / "media_studio"

        target_str = str(target_path.resolve())
        try:
            proc = await asyncio.create_subprocess_exec("open", target_str)
            await proc.communicate()
            return {"success": True, "opened_folder": target_str, "message": f"Opened folder {target_path.name} in macOS Finder."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def open_in_background(self, target: str) -> Dict[str, Any]:
        """
        Opens a file or URL in the background without stealing user window focus (-g flag).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "open", "-g", target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return {"success": True, "target": target}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def find_files(self, query: str, root_dir: Optional[str] = None, max_results: int = 30) -> Dict[str, Any]:
        """
        Uses macOS Spotlight index (mdfind) for instant global search across the Mac,
        falling back to fast find if mdfind is not available.
        """
        target_root = os.path.expanduser(root_dir) if root_dir else os.path.expanduser("~")
        results = []
        try:
            cmd = ["mdfind"]
            if root_dir and os.path.isdir(target_root):
                cmd.extend(["-onlyin", target_root])
            cmd.append(query)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=6)
            lines = [l.strip() for l in stdout.decode("utf-8", errors="replace").splitlines() if l.strip()]
            for p in lines[:max_results]:
                if os.path.exists(p):
                    try:
                        st = os.stat(p)
                        results.append({
                            "path": p,
                            "name": os.path.basename(p),
                            "is_dir": os.path.isdir(p),
                            "size_bytes": st.st_size if not os.path.isdir(p) else 0,
                            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
                        })
                    except Exception:
                        results.append({"path": p, "name": os.path.basename(p), "is_dir": os.path.isdir(p)})
            return {"success": True, "count": len(results), "query": query, "root": target_root, "results": results}
        except Exception as e:
            return {"success": False, "error": str(e), "results": []}

    async def manage_file(self, action: str, src: str, dst: Optional[str] = None) -> Dict[str, Any]:
        """
        Safe macOS file operations:
        - 'trash': Moves file to macOS Finder Trash (recoverable) via AppleScript.
        - 'copy': Copies file/directory safely.
        - 'move': Moves or renames file safely.
        - 'mkdir': Creates directory path recursively.
        """
        src_path = os.path.expanduser(src)
        if action == "mkdir":
            try:
                os.makedirs(src_path, exist_ok=True)
                return {"success": True, "action": "mkdir", "path": src_path}
            except Exception as e:
                return {"success": False, "error": str(e)}

        if not os.path.exists(src_path):
            return {"success": False, "error": f"Source path does not exist: {src_path}"}

        if action == "trash":
            # /usr/bin/trash (macOS 14+) takes the path as an argument: no Finder permission prompt, and no way
            # for a file name to inject AppleScript. Older macOS: Finder, with the path passed as an argument too.
            if os.path.exists("/usr/bin/trash"):
                argv = ["/usr/bin/trash", src_path]
            else:
                argv = ["osascript", "-e", "on run argv", "-e", 'tell application "Finder" to delete (POSIX file (item 1 of argv) as alias)',
                        "-e", "end run", src_path]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode == 0:
                    return {"success": True, "action": "trash", "path": src_path, "note": "Safely moved to macOS Trash (recoverable)"}
                else:
                    return {"success": False, "error": stderr.decode("utf-8", errors="replace").strip()}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif action == "copy":
            if not dst:
                return {"success": False, "error": "Destination path required for copy"}
            dst_path = os.path.expanduser(dst)
            try:
                if os.path.isdir(src_path):
                    shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    shutil.copy2(src_path, dst_path)
                return {"success": True, "action": "copy", "src": src_path, "dst": dst_path}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif action == "move":
            if not dst:
                return {"success": False, "error": "Destination path required for move"}
            dst_path = os.path.expanduser(dst)
            try:
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                shutil.move(src_path, dst_path)
                return {"success": True, "action": "move", "src": src_path, "dst": dst_path}
            except Exception as e:
                return {"success": False, "error": str(e)}

        return {"success": False, "error": f"Unknown action: {action}"}

    async def read_clipboard(self) -> Dict[str, Any]:
        """Reads current plain text from macOS system clipboard via pbpaste."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "pbpaste",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            text = stdout.decode("utf-8", errors="replace")
            return {"success": True, "text": text, "chars": len(text)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def write_clipboard(self, text: str) -> Dict[str, Any]:
        """Copies text directly into macOS system clipboard via pbcopy."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "pbcopy",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate(input=text.encode("utf-8"))
            return {"success": True, "chars": len(text)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def inspect_port(self, port: int) -> Dict[str, Any]:
        """Inspects which process and PID is listening on or connected to a given local port."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "lsof", "-i", f":{port}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            lines = stdout.decode("utf-8", errors="replace").strip().splitlines()
            processes = []
            if len(lines) > 1:
                for line in lines[1:]:
                    parts = line.split(None, 8)
                    if len(parts) >= 2:
                        processes.append({
                            "command": parts[0],
                            "pid": int(parts[1]),
                            "user": parts[2] if len(parts) > 2 else "",
                            "name": parts[-1] if len(parts) > 8 else ""
                        })
            return {"success": True, "port": port, "in_use": len(processes) > 0, "processes": processes}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def kill_process(self, pid: int, force: bool = False) -> Dict[str, Any]:
        """Terminates a process safely by PID (SIGTERM by default, SIGKILL if force=True)."""
        import signal
        try:
            sig = getattr(signal, "SIGKILL", signal.SIGTERM) if force else signal.SIGTERM   # Windows has no SIGKILL
            os.kill(pid, sig)
            return {"success": True, "pid": pid, "signal": "SIGKILL" if force else "SIGTERM"}
        except ProcessLookupError:
            return {"success": True, "pid": pid, "status": "already_stopped"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def start_background_job(self, name: str, command: str, cwd: Optional[str] = None) -> Dict[str, Any]:
        """
        Starts a persistent background job (daemon, dev server, watcher) that keeps running
        independently while buffering output.
        """
        import uuid
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        target_cwd = cwd or os.getcwd()
        if not os.path.isdir(target_cwd):
            target_cwd = os.getcwd()

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=target_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        job_info: Dict[str, Any] = {
            "id": job_id,
            "name": name,
            "command": command,
            "cwd": target_cwd,
            "pid": proc.pid,
            "status": "running",
            "start_time": time.time(),
            "start_time_str": time.strftime("%H:%M:%S"),
            "proc": proc,
            "logs": []
        }
        self.background_jobs[job_id] = job_info

        job_info["last_output_at"] = time.time()
        job_info["read_cursor"] = 0          # how many log lines the agent has already seen
        job_info["dropped"] = 0              # lines trimmed from the front of the buffer

        async def _pump(stream, tag):
            while True:
                line = await stream.readline()
                if not line:
                    break
                job_info["logs"].append(f"[{tag}] {line.decode('utf-8', errors='replace').rstrip()}")
                job_info["last_output_at"] = time.time()
                if len(job_info["logs"]) > 1000:
                    job_info["logs"].pop(0)
                    job_info["dropped"] += 1

        async def _log_reader():
            # Read stdout and stderr together: draining one first can deadlock a chatty process
            # once the other pipe's buffer fills.
            await asyncio.gather(_pump(proc.stdout, "out"), _pump(proc.stderr, "err"))
            await proc.wait()
            if job_info["status"] == "running":
                job_info["status"] = "completed" if proc.returncode == 0 else "failed"
            job_info["exit_code"] = proc.returncode
            job_info["end_time"] = time.time()

        asyncio.create_task(_log_reader())

        return {
            "success": True,
            "job_id": job_id,
            "pid": proc.pid,
            "name": name,
            "command": command,
            "status": "running"
        }

    def list_jobs(self) -> List[Dict[str, Any]]:
        """Lists all registered background daemon jobs and their current execution state."""
        jobs = []
        for jid, info in self.background_jobs.items():
            jobs.append({
                "id": info["id"],
                "name": info["name"],
                "command": info["command"],
                "cwd": info["cwd"],
                "pid": info["pid"],
                "status": info["status"],
                "start_time": info["start_time_str"],
                "exit_code": info.get("exit_code"),
                "log_lines": len(info["logs"])
            })
        return jobs

    async def stop_job(self, job_id: str, force: bool = False) -> Dict[str, Any]:
        """Stops a running background daemon job."""
        info = self.background_jobs.get(job_id)
        if not info:
            return {"success": False, "error": f"Job not found: {job_id}"}
        proc = info.get("proc")
        if proc and proc.returncode is None:
            if force:
                proc.kill()
            else:
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            info["status"] = "stopped"
            info["exit_code"] = proc.returncode
            return {"success": True, "job_id": job_id, "status": "stopped"}
        return {"success": True, "job_id": job_id, "status": info.get("status", "already_stopped")}

    def check_job(self, job_id: str, tail: int = 30) -> Dict[str, Any]:
        """Status of a background job plus only the output the agent hasn't seen yet (like a progress check)."""
        info = self.background_jobs.get(job_id) or next(
            (j for j in self.background_jobs.values() if j["name"] == job_id), None)
        if not info:
            known = [f"{j['id']} ({j['name']})" for j in self.background_jobs.values()]
            return {"success": False, "error": f"Job not found: {job_id}. Known jobs: {', '.join(known) or 'none'}"}
        total = info["dropped"] + len(info["logs"]) if "dropped" in info else len(info["logs"])
        seen = info.get("read_cursor", 0)
        start = max(0, seen - info.get("dropped", 0))
        new_lines = info["logs"][start:]
        info["read_cursor"] = total
        now = time.time()
        quiet = round(now - info.get("last_output_at", info["start_time"]))
        running = info["status"] == "running"
        return {
            "success": True, "job_id": info["id"], "name": info["name"], "status": info["status"],
            "exit_code": info.get("exit_code"),
            "running_for_sec": round((info.get("end_time") or now) - info["start_time"]),
            "new_output": new_lines[-tail:], "new_output_lines": len(new_lines),
            "quiet_for_sec": quiet if running else None,
            "note": ("No new output for a while; it may be stuck or just busy." if running and quiet > 120 else None),
        }

    def get_job_logs(self, job_id: str, tail: int = 100) -> Dict[str, Any]:
        """Retrieves recent stdout/stderr lines for a background daemon job."""
        info = self.background_jobs.get(job_id)
        if not info:
            return {"success": False, "error": f"Job not found: {job_id}"}
        return {
            "success": True,
            "job_id": job_id,
            "status": info["status"],
            "logs": info["logs"][-tail:]
        }

    def _is_protected_path(self, p: Path) -> bool:
        """Guards critical macOS system paths from accidental modification."""
        try:
            resolved = str(p.resolve())
            home_str = str(Path.home())
            if resolved.startswith(home_str) or resolved.startswith("/tmp") or resolved.startswith("/private/tmp"):
                return False
            if resolved == "/":
                return True
            protected = ["/System", "/Library", "/usr", "/bin", "/sbin", "/opt", "/etc", "/var", "/private"]
            for prot in protected:
                if resolved == prot or (resolved.startswith(prot + "/") and not resolved.startswith("/private/tmp")):
                    return True
            return False
        except Exception:
            return False

    def read_file(
        self,
        filepath: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        max_bytes: int = 60000
    ) -> Dict[str, Any]:
        """
        Reads text content from ANY file across the host filesystem.
        Supports 1-indexed line range slicing (start_line to end_line) with line numbers.
        """
        try:
            p = Path(os.path.expanduser(filepath)).resolve()
            if not p.exists():
                return {"success": False, "error": f"File not found: {filepath}"}
            if not p.is_file():
                return {"success": False, "error": f"Path is not a regular file: {filepath}"}

            size = p.stat().st_size
            raw_text = p.read_text(encoding="utf-8", errors="replace")
            all_lines = raw_text.splitlines()
            total_lines = len(all_lines)

            # Handle line range slicing if specified
            if start_line is not None or end_line is not None:
                s_idx = max(1, start_line or 1)
                e_idx = min(total_lines, end_line or total_lines)
                if s_idx > e_idx:
                    return {
                        "success": False,
                        "error": f"start_line ({s_idx}) cannot be greater than end_line ({e_idx}). Total lines: {total_lines}."
                    }
                sliced_lines = all_lines[s_idx - 1:e_idx]
                numbered_lines = [f"{s_idx + i}: {line}" for i, line in enumerate(sliced_lines)]
                content = "\n".join(numbered_lines)
                return {
                    "success": True,
                    "filepath": str(p),
                    "name": p.name,
                    "total_lines": total_lines,
                    "sliced_range": f"{s_idx}-{e_idx}",
                    "lines_returned": len(sliced_lines),
                    "size_bytes": size,
                    "content": content
                }

            # Full file read (with max_bytes protection)
            is_truncated = size > max_bytes
            content = raw_text[:max_bytes] if is_truncated else raw_text
            numbered_content = "\n".join([f"{i+1}: {line}" for i, line in enumerate(content.splitlines()[:400])])

            return {
                "success": True,
                "filepath": str(p),
                "name": p.name,
                "size_bytes": size,
                "lines_count": total_lines,
                "is_truncated": is_truncated,
                "content": content,
                "numbered_preview": numbered_content
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def write_file(self, filepath: str, content: str, create_dirs: bool = True) -> Dict[str, Any]:
        """
        Creates or overwrites a file on the host computer.
        Automatically creates missing parent directories.
        """
        try:
            p = Path(os.path.expanduser(filepath)).resolve()
            if self._is_protected_path(p):
                return {"success": False, "error": f"Cannot write to protected system path: {filepath}"}

            if create_dirs:
                p.parent.mkdir(parents=True, exist_ok=True)

            p.write_text(content, encoding="utf-8")
            st = p.stat()
            return {
                "success": True,
                "filepath": str(p),
                "name": p.name,
                "size_bytes": st.st_size,
                "lines_count": len(content.splitlines()),
                "message": f"Successfully written {len(content.splitlines())} lines to {p.name}"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def edit_file(
        self,
        filepath: str,
        target_content: str,
        replacement_content: str,
        allow_multiple: bool = False
    ) -> Dict[str, Any]:
        """
        Surgically replaces a specific block of text in an existing file anywhere on the computer.
        Avoids overwriting entire files, ensuring safe and precise updates.
        """
        try:
            p = Path(os.path.expanduser(filepath)).resolve()
            if not p.exists():
                return {"success": False, "error": f"File not found: {filepath}"}
            if not p.is_file():
                return {"success": False, "error": f"Path is not a regular file: {filepath}"}
            if self._is_protected_path(p):
                return {"success": False, "error": f"Cannot edit protected system file: {filepath}"}

            original_text = p.read_text(encoding="utf-8")
            count = original_text.count(target_content)

            if count == 0:
                return {
                    "success": False,
                    "error": f"Target content not found in {p.name}. Make sure exact indentation and text match.",
                    "filepath": str(p)
                }

            if count > 1 and not allow_multiple:
                return {
                    "success": False,
                    "error": f"Target content found {count} times in {p.name}. Provide a more specific contiguous chunk or pass allow_multiple=True.",
                    "occurrences": count,
                    "filepath": str(p)
                }

            # Find starting line number of first occurrence for reporting
            char_idx = original_text.find(target_content)
            line_num = original_text[:char_idx].count("\n") + 1

            if allow_multiple:
                new_text = original_text.replace(target_content, replacement_content)
                replaced_count = count
            else:
                new_text = original_text.replace(target_content, replacement_content, 1)
                replaced_count = 1

            p.write_text(new_text, encoding="utf-8")

            old_lines_count = len(original_text.splitlines())
            new_lines_count = len(new_text.splitlines())
            delta = new_lines_count - old_lines_count

            return {
                "success": True,
                "filepath": str(p),
                "name": p.name,
                "line_number": line_num,
                "occurrences_replaced": replaced_count,
                "lines_delta": delta,
                "total_lines": new_lines_count,
                "message": f"Successfully updated {p.name} around line {line_num} ({replaced_count} occurrence replaced)."
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def patch_file(self, filepath: str, search_text: str, replace_text: str) -> Dict[str, Any]:
        """Backward-compatible alias for edit_file."""
        return self.edit_file(filepath=filepath, target_content=search_text, replacement_content=replace_text)

    def get_directory_tree(
        self,
        path: Optional[str] = None,
        max_depth: int = 2,
        max_entries: int = 120,
        show_hidden: bool = False
    ) -> Dict[str, Any]:
        """
        Generates a clean ASCII directory tree and metadata audit for ANY folder on the Mac.
        Useful for exploring of user workspaces, downloads, or configurations.
        """
        try:
            target_str = path or self.current_working_directory or str(Path.home())
            root = Path(os.path.expanduser(target_str)).resolve()
            if not root.exists():
                return {"success": False, "error": f"Directory not found: {target_str}"}
            if not root.is_dir():
                return {"success": False, "error": f"Path is not a directory: {target_str}"}

            tree_lines = [f"📁 {root}"]
            stats = {"total_dirs": 0, "total_files": 0, "total_size_bytes": 0, "key_files": []}
            entries_count = 0

            def _fmt_size(b: int) -> str:
                if b < 1024:
                    return f"{b} B"
                elif b < 1024 * 1024:
                    return f"{b / 1024:.1f} KB"
                elif b < 1024 * 1024 * 1024:
                    return f"{b / (1024 * 1024):.1f} MB"
                return f"{b / (1024 * 1024 * 1024):.2f} GB"

            def _walk(current_dir: Path, prefix: str, current_depth: int):
                nonlocal entries_count
                if current_depth > max_depth or entries_count >= max_entries:
                    return

                try:
                    items = sorted(list(current_dir.iterdir()), key=lambda x: (not x.is_dir(), x.name.lower()))
                except (PermissionError, OSError):
                    tree_lines.append(f"{prefix}└── [Permission Denied]")
                    return

                # Filter hidden files unless requested
                if not show_hidden:
                    items = [it for it in items if not it.name.startswith(".")]

                # Skip excessive node_modules, .git, or __pycache__ sub-traversals
                filtered_items = []
                for it in items:
                    if it.name in (".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".DS_Store"):
                        if it.is_dir():
                            tree_lines.append(f"{prefix}├── 📁 {it.name}/ [skipped]")
                        continue
                    filtered_items.append(it)

                count = len(filtered_items)
                for idx, item in enumerate(filtered_items):
                    if entries_count >= max_entries:
                        tree_lines.append(f"{prefix}└── ... [truncated at {max_entries} items]")
                        break

                    entries_count += 1
                    is_last = (idx == count - 1)
                    connector = "└── " if is_last else "├── "
                    sub_prefix = prefix + ("    " if is_last else "│   ")

                    if item.is_dir():
                        stats["total_dirs"] += 1
                        try:
                            sub_count = sum(1 for _ in item.iterdir())
                            tree_lines.append(f"{prefix}{connector}📁 {item.name}/ ({sub_count} items)")
                        except Exception:
                            tree_lines.append(f"{prefix}{connector}📁 {item.name}/")
                        _walk(item, sub_prefix, current_depth + 1)
                    else:
                        stats["total_files"] += 1
                        try:
                            st = item.stat()
                            sz = st.st_size
                            stats["total_size_bytes"] += sz
                            tree_lines.append(f"{prefix}{connector}📄 {item.name} ({_fmt_size(sz)})")
                            if item.name.lower() in ("readme.md", "package.json", "pyproject.toml", "requirements.txt", "dockerfile", ".env", "makefile"):
                                stats["key_files"].append(str(item))
                        except Exception:
                            tree_lines.append(f"{prefix}{connector}📄 {item.name}")

            _walk(root, "", 1)
            tree_text = "\n".join(tree_lines)

            return {
                "success": True,
                "base_path": str(root),
                "name": root.name,
                "tree": tree_text,
                "stats": {
                    "total_dirs": stats["total_dirs"],
                    "total_files": stats["total_files"],
                    "total_size_bytes": stats["total_size_bytes"],
                    "total_size_formatted": _fmt_size(stats["total_size_bytes"]),
                    "key_files": stats["key_files"]
                }
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_directory(
        self,
        path: Optional[str] = None,
        show_hidden: bool = False,
        max_items: int = 100
    ) -> Dict[str, Any]:
        """
        Lists directory contents for ANY path on the Mac with rich item telemetry.
        """
        try:
            target_str = path or self.current_working_directory or str(Path.home())
            root = Path(os.path.expanduser(target_str)).resolve()
            if not root.exists():
                return {"success": False, "error": f"Directory not found: {target_str}"}
            if not root.is_dir():
                return {"success": False, "error": f"Path is not a directory: {target_str}"}

            folders = []
            files = []
            with os.scandir(root) as it:
                for entry in it:
                    if not show_hidden and entry.name.startswith("."):
                        continue
                    try:
                        st = entry.stat()
                        if entry.is_dir():
                            folders.append({
                                "name": entry.name,
                                "path": entry.path,
                                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
                            })
                        else:
                            files.append({
                                "name": entry.name,
                                "path": entry.path,
                                "size_bytes": st.st_size,
                                "extension": Path(entry.name).suffix.lower(),
                                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
                            })
                    except Exception:
                        pass

            folders.sort(key=lambda x: x["name"].lower())
            files.sort(key=lambda x: x["name"].lower())

            return {
                "success": True,
                "path": str(root),
                "name": root.name,
                "parent": str(root.parent),
                "folder_count": len(folders),
                "file_count": len(files),
                "folders": folders[:max_items],
                "files": files[:max_items]
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def batch_organize_files(
        self,
        source_dir: str,
        categories: Optional[Dict[str, List[str]]] = None
    ) -> Dict[str, Any]:
        """
        Intelligently cleans and categorizes loose files in a directory (like ~/Downloads or ~/Desktop)
        into dedicated subfolders (Documents, Images, Audio, Video, Code, Archives) based on extensions.
        """
        try:
            root = Path(os.path.expanduser(source_dir)).resolve()
            if not root.exists() or not root.is_dir():
                return {"success": False, "error": f"Directory not found: {source_dir}"}
            if self._is_protected_path(root):
                return {"success": False, "error": f"Cannot organize protected system directory: {source_dir}"}

            rules = categories or {
                "Documents": [".pdf", ".docx", ".doc", ".txt", ".xlsx", ".pptx", ".csv", ".pages", ".numbers", ".md"],
                "Images": [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".tiff", ".heic"],
                "Audio": [".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"],
                "Video": [".mp4", ".mov", ".mkv", ".webm", ".avi"],
                "Code": [".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json", ".sh", ".swift", ".rs", ".go"],
                "Archives": [".zip", ".tar", ".gz", ".dmg", ".pkg", ".7z", ".rar"]
            }

            ext_to_cat = {}
            for cat, exts in rules.items():
                for ext in exts:
                    ext_to_cat[ext.lower()] = cat

            moved_by_category: Dict[str, List[str]] = {cat: [] for cat in rules}
            moved_by_category["Other"] = []

            for entry in list(root.iterdir()):
                if entry.is_file() and not entry.name.startswith("."):
                    ext = entry.suffix.lower()
                    target_cat = ext_to_cat.get(ext)
                    if target_cat:
                        dest_folder = root / target_cat
                        dest_folder.mkdir(parents=True, exist_ok=True)
                        dest_file = dest_folder / entry.name
                        # Avoid collisions
                        if dest_file.exists():
                            dest_file = dest_folder / f"{entry.stem}_{int(time.time())}{entry.suffix}"
                        shutil.move(str(entry), str(dest_file))
                        moved_by_category[target_cat].append(entry.name)

            total_moved = sum(len(v) for v in moved_by_category.values())
            summary = {k: len(v) for k, v in moved_by_category.items() if len(v) > 0}

            return {
                "success": True,
                "source_dir": str(root),
                "total_files_moved": total_moved,
                "breakdown": summary,
                "details": {k: v for k, v in moved_by_category.items() if len(v) > 0},
                "message": f"Organized {total_moved} files into categorized folders in {root.name}."
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def open_item(self, target: str, app_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Opens a file, directory, or URL using macOS 'open'.
        Optionally specifies a target application (-a 'Visual Studio Code', -a 'Finder', etc.).
        """
        try:
            expanded = os.path.expanduser(target)
            cmd = ["open"]
            if app_name:
                cmd.extend(["-a", app_name])
            cmd.append(expanded)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return {
                    "success": True,
                    "target": target,
                    "app_name": app_name or "Default Application",
                    "message": f"Opened {target} with {app_name or 'default application'}."
                }
            else:
                return {"success": False, "error": stderr.decode("utf-8", errors="replace").strip()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def run_applescript(self, script: str) -> Dict[str, Any]:
        """
        Executes native macOS AppleScript automation safely via osascript.
        Allows controlling Finder, Safari, Notes, Music, Terminal, System Settings, etc.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            out_str = stdout.decode("utf-8", errors="replace").strip()
            err_str = stderr.decode("utf-8", errors="replace").strip()

            return {
                "success": proc.returncode == 0,
                "exit_code": proc.returncode,
                "output": out_str,
                "error": err_str if proc.returncode != 0 else None,
                "script": script
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_hardware_overview(self) -> Dict[str, Any]:
        """
        Retrieves detailed macOS hardware specs, Apple Silicon / Intel architecture,
        battery state, and operating system build.
        """
        try:
            # macOS version
            os_ver = subprocess.check_output(["sw_vers", "-productVersion"], text=True).strip()
            build_ver = subprocess.check_output(["sw_vers", "-buildVersion"], text=True).strip()

            # CPU & Machine model
            try:
                chip_brand = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
            except Exception:
                chip_brand = platform.processor() or "Apple Silicon"

            try:
                model_name = subprocess.check_output(["sysctl", "-n", "hw.model"], text=True).strip()
            except Exception:
                model_name = platform.machine()

            try:
                ncpu = int(subprocess.check_output(["sysctl", "-n", "hw.ncpu"], text=True).strip())
            except Exception:
                ncpu = os.cpu_count() or 1

            # Battery status (pmset -g batt)
            battery_info = {"has_battery": False, "status": "Desktop / AC Power"}
            try:
                pm_out = subprocess.check_output(["pmset", "-g", "batt"], text=True).strip()
                if "InternalBattery" in pm_out:
                    m_pct = re.search(r'(\d+)%', pm_out)
                    m_state = "charging" if "charging" in pm_out else ("discharging" if "discharging" in pm_out else "charged")
                    battery_info = {
                        "has_battery": True,
                        "percentage": int(m_pct.group(1)) if m_pct else 100,
                        "state": m_state,
                        "raw": pm_out.splitlines()[-1].strip()
                    }
            except Exception:
                pass

            # RAM & Disk
            telemetry = self.get_system_telemetry()

            return {
                "success": True,
                "macos_version": f"macOS {os_ver} (Build {build_ver})",
                "chip": chip_brand,
                "model": model_name,
                "cpu_cores": ncpu,
                "arch": platform.machine(),
                "battery": battery_info,
                "memory_gb": telemetry.get("memory", {}).get("total_gb", 0),
                "memory_used_gb": telemetry.get("memory", {}).get("used_gb", 0),
                "disk_free_gb": telemetry.get("disk", {}).get("free_gb", 0),
                "user_home": str(Path.home()),
                "hostname": platform.node()
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def ai_review_system(self, user_intent: Optional[str] = None) -> Dict[str, Any]:
        """
        Synthesizes real-time telemetry, active listening ports, memory pressure,
        and host processes into an actionable diagnosis and executable remediation actions.
        Uses local or cloud LLM with reliable algorithmic fallback.
        """
        telemetry = self.get_system_telemetry()
        ports = telemetry.get("listening_ports", [])
        top_procs = telemetry.get("top_processes", [])
        mem = telemetry.get("memory", {})
        cpu = telemetry.get("cpu_load", {})
        jobs = self.list_jobs()

        diagnoses = []
        remediations = []
        status = "healthy"

        # Check runaway CPU
        for proc in top_procs:
            try:
                cpu_val = float(proc.get("cpu_pct", 0))
                if cpu_val >= 75.0:
                    status = "critical" if status != "critical" else status
                    diagnoses.append({
                        "severity": "critical",
                        "title": f"Runaway Process: {proc['command']} (PID {proc['pid']})",
                        "description": f"Process PID {proc['pid']} is consuming {cpu_val}% CPU and {proc.get('rss_mb', 0)} MB RAM.",
                        "component": "CPU"
                    })
                    remediations.append({
                        "id": f"kill-{proc['pid']}",
                        "action_type": "kill_process",
                        "label": f"⚡ Terminate Runaway Process PID {proc['pid']} ({proc['command']})",
                        "description": f"Stops runaway CPU spike and frees {proc.get('rss_mb', 0)} MB memory.",
                        "params": {"pid": proc["pid"], "force": False},
                        "danger_level": "caution"
                    })
            except Exception:
                pass

        # Check memory pressure
        pressure_pct = mem.get("pressure_pct", 0)
        if pressure_pct >= 88.0:
            status = "critical"
            diagnoses.append({
                "severity": "critical",
                "title": f"Critical Memory Pressure ({pressure_pct}% Used)",
                "description": f"{mem.get('used_gb', 0)} GB out of {mem.get('total_gb', 0)} GB RAM consumed. May cause swapping or latency.",
                "component": "RAM"
            })
            remediations.append({
                "id": "reclaim-cache",
                "action_type": "run_command",
                "label": "🧹 Reclaim System Memory & Temp Caches",
                "description": "Purges temporary files in /tmp and clears idle filesystem buffers.",
                "params": {"command": "rm -rf /tmp/xod_sandbox* /tmp/xod_cache* 2>/dev/null && echo 'Cleaned temporary caches'"},
                "danger_level": "safe"
            })
        elif pressure_pct >= 75.0:
            if status != "critical":
                status = "warning"
            diagnoses.append({
                "severity": "warning",
                "title": f"Elevated Memory Pressure ({pressure_pct}% Used)",
                "description": f"RAM utilization is at {mem.get('used_gb', 0)} / {mem.get('total_gb', 0)} GB.",
                "component": "RAM"
            })

        # Check open ports
        ports_summary_list = [f":{p.get('port')} ({p.get('command')})" for p in ports[:6]]
        ports_desc = ", ".join(ports_summary_list) if ports_summary_list else "None detected"
        diagnoses.append({
            "severity": "info",
            "title": f"{len(ports)} Active Listening Ports Discovered",
            "description": f"Discovered open network endpoints: {ports_desc}.",
            "component": "Network"
        })

        summary = f"System status is {status.upper()}. CPU 1m load is {cpu.get('1m', 0.00)}, memory pressure is {pressure_pct}% ({mem.get('used_gb', 0)}/{mem.get('total_gb', 0)} GB), with {len(ports)} listening ports."

        # If user provided a specific intent or query, attempt LLM synthesis
        llm_feedback = None
        try:
            from app.services.llm import llm_service
            import httpx
            sys_snapshot_json = json.dumps({
                "load": cpu,
                "memory": mem,
                "top_processes": top_procs[:6],
                "ports": ports[:8],
                "jobs_count": len(jobs),
                "cwd": self.current_working_directory
            }, indent=2)

            prompt = f"""You are Xod AI System Supervisor and Host Diagnostics Engine.
Analyze this live host machine telemetry and formulate an executive diagnostic review:
Telemetry Snapshot:
{sys_snapshot_json}

User Query / Intent:
{user_intent or 'Diagnose machine health, high memory/CPU usage, active ports, and propose concrete remediations.'}

Provide a concise 2-3 sentence executive diagnosis and assessment. Be crisp, factual, and direct."""

            endpoint = llm_service.resolve_endpoint_for_tier("local")
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(
                    f"{endpoint['base_url']}/chat/completions",
                    headers={"Authorization": f"Bearer {endpoint['api_key']}", "Content-Type": "application/json"},
                    json={
                        "model": endpoint["model"],
                        "messages": [
                            {"role": "system", "content": "You are Xod AI System Supervisor. Provide expert, concise diagnosis of computer telemetry."},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.2,
                        "max_tokens": 300,
                        "stream": False
                    }
                )
                if res.status_code == 200:
                    data = res.json()
                    llm_feedback = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if llm_feedback:
                        summary = llm_feedback
        except Exception:
            pass

        return {
            "success": True,
            "status": status,
            "summary": summary,
            "user_intent": user_intent,
            "diagnoses": diagnoses,
            "remediations": remediations,
            "telemetry": telemetry,
            "timestamp": time.time()
        }

    async def execute_system_action(self, action_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes an AI-recommended or user-approved remediation action safely.
        """
        result = {"success": False, "action_type": action_type}
        try:
            if action_type == "kill_process":
                pid = int(params.get("pid", 0))
                force = bool(params.get("force", False))
                result = await self.kill_process(pid=pid, force=force)
                result["message"] = f"Process PID {pid} successfully terminated."
            elif action_type == "run_command":
                cmd = params.get("command", "")
                cwd = params.get("cwd") or self.current_working_directory
                result = await self.run_command(command=cmd, cwd=cwd)
                result["message"] = f"Executed shell command: {cmd}"
            elif action_type == "write_file":
                filepath = params.get("filepath", "")
                content = params.get("content", "")
                result = self.write_file(filepath=filepath, content=content)
                result["message"] = f"Updated file: {filepath}"
            elif action_type == "patch_file":
                filepath = params.get("filepath", "")
                search_text = params.get("search_text", "")
                replace_text = params.get("replace_text", "")
                result = self.patch_file(filepath=filepath, search_text=search_text, replace_text=replace_text)
                result["message"] = f"Patched file: {filepath}"
            elif action_type == "stop_job":
                job_id = params.get("job_id", "")
                result = await self.stop_job(job_id=job_id)
                result["message"] = f"Stopped background job: {job_id}"
            else:
                result = {"success": False, "error": f"Unknown action type: {action_type}"}

            if "success" not in result or result["success"] is None:
                result["success"] = (result.get("exit_code") == 0 if "exit_code" in result else True)
            result["updated_telemetry"] = self.get_system_telemetry()
            return result
        except Exception as e:
            return {"success": False, "error": str(e), "action_type": action_type}


system_control_service = SystemControlService()
