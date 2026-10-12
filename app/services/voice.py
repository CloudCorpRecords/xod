"""
Voice Engine & Fast-Path Intent Router for Hands-Free Computer Control.

Features:
- Sub-400ms regex fast-path router for everyday speech commands:
    * "open Notes / Safari / Slack / Finder / Terminal"
    * "turn volume to 50% / mute / unmute / louder / softer"
    * "clean / organize downloads"
    * "show files in Desktop / tree of documents"
    * "what's my battery / cpu / hardware overview"
    * "what's on port 8000 / inspect port"
    * "read clipboard / copy X to clipboard"
- LLM Router fallback via local LM Studio / active provider for complex intents.
- Direct execution of system_control service without multi-turn agent latency.
- Natural spoken response generator with macOS native `say` TTS support.
- Automatic trace recording into `trace_recorder` for on-device distillation.
"""
import asyncio
import os
import re
import time
import subprocess
from typing import Dict, Any, Optional

from app.services.system_control import system_control_service
from app.services.trace_recorder import record_trace
from app.services.llm import llm_service


class VoiceEngine:
    def __init__(self):
        self.fast_patterns = [
            # Open app / item
            (re.compile(r"^(?:open|launch|start)\s+(?:the\s+)?([a-zA-Z0-9\.\-_ ]+)$", re.IGNORECASE), "open_app"),
            # Clean / organize folder
            (re.compile(r"^(?:clean|organize|tidy)\s+(?:up\s+)?(?:the\s+)?(downloads|desktop|documents)$", re.IGNORECASE), "organize"),
            # Directory tree / list
            (re.compile(r"^(?:show|list|tree|what is in|whats in)\s+(?:the\s+)?(downloads|desktop|documents|folder|directory)(?:\s+(?:folder|dir))?$", re.IGNORECASE), "tree"),
            # Volume control
            (re.compile(r"^(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})(?:%)?$", re.IGNORECASE), "set_volume"),
            (re.compile(r"^(?:mute|silence)(?:\s+volume)?$", re.IGNORECASE), "mute_volume"),
            (re.compile(r"^(?:unmute)(?:\s+volume)?$", re.IGNORECASE), "unmute_volume"),
            (re.compile(r"^(?:volume\s+up|louder|turn\s+it\s+up)$", re.IGNORECASE), "volume_up"),
            (re.compile(r"^(?:volume\s+down|softer|quieter|turn\s+it\s+down)$", re.IGNORECASE), "volume_down"),
            # Hardware / battery
            (re.compile(r"^(?:hardware|battery|specs|system\s+status|laptop\s+status|(?:how|what)\s+is\s+(?:my\s+)?battery(?:'s\s+status)?|how\s+much\s+battery|battery\s+level|system\s+specs)$", re.IGNORECASE), "hardware"),
            # Disk space
            (re.compile(r"^(?:disk\s+space|storage|how\s+much\s+(?:free\s+)?space|disk\s+usage)$", re.IGNORECASE), "disk_space"),
            # Top processes / CPU load
            (re.compile(r"^(?:what\s+is\s+using\s+cpu|top\s+processes|cpu\s+usage|running\s+processes)$", re.IGNORECASE), "top_processes"),
            # Git status
            (re.compile(r"^(?:git\s+status|check\s+git|repo\s+status)$", re.IGNORECASE), "git_status"),
            # Clipboard
            (re.compile(r"^(?:read\s+clipboard|what\s+is\s+on\s+(?:my\s+)?clipboard|whats\s+on\s+(?:my\s+)?clipboard)$", re.IGNORECASE), "read_clipboard"),
            # Port check
            (re.compile(r"^(?:what(?:'s|\s+is)\s+on\s+port|check\s+port|inspect\s+port)\s+(\d+)$", re.IGNORECASE), "inspect_port"),
            # Reveal in Finder
            (re.compile(r"^(?:reveal|show\s+in\s+finder)\s+(downloads|desktop|documents|project)$", re.IGNORECASE), "reveal_finder"),
        ]

    def _resolve_standard_path(self, name: str) -> str:
        name_clean = name.lower().strip()
        home = str(os.path.expanduser("~"))
        if "download" in name_clean:
            return os.path.join(home, "Downloads")
        if "desktop" in name_clean:
            return os.path.join(home, "Desktop")
        if "document" in name_clean:
            return os.path.join(home, "Documents")
        return home

    async def execute_fastpath(self, text: str) -> Optional[Dict[str, Any]]:
        clean_text = text.strip()
        for pattern, action in self.fast_patterns:
            m = pattern.match(clean_text)
            if not m:
                continue

            if action == "open_app":
                target = m.group(1).strip()
                # If target looks like a common app
                res = await system_control_service.open_item(target, app_name=target)
                if not res.get("success"):
                    # fallback to generic open without -a
                    res = await system_control_service.open_item(target)
                spoken = f"Opening {target}." if res.get("success") else f"Could not open {target}."
                return {
                    "matched": True,
                    "action": "open_app",
                    "spoken": spoken,
                    "details": res,
                    "tool": "computer_open_item",
                    "args": {"target": target, "app_name": target}
                }

            elif action == "organize":
                folder_alias = m.group(1).strip()
                target_path = self._resolve_standard_path(folder_alias)
                res = system_control_service.batch_organize_files(target_path)
                moved = res.get("total_files_moved", 0)
                spoken = f"Organized {moved} files in your {folder_alias} folder."
                return {
                    "matched": True,
                    "action": "organize",
                    "spoken": spoken,
                    "details": res,
                    "tool": "computer_batch_organize",
                    "args": {"source_dir": target_path}
                }

            elif action == "tree":
                folder_alias = m.group(1).strip()
                target_path = self._resolve_standard_path(folder_alias)
                res = system_control_service.get_directory_tree(target_path, max_depth=2, max_entries=25)
                stats = res.get("stats", {})
                dirs = stats.get("total_dirs", 0)
                files = stats.get("total_files", 0)
                spoken = f"{folder_alias.capitalize()} contains {files} files across {dirs} folders."
                return {
                    "matched": True,
                    "action": "tree",
                    "spoken": spoken,
                    "details": res,
                    "tool": "computer_directory_tree",
                    "args": {"path": target_path, "max_depth": 2}
                }

            elif action == "set_volume":
                level = int(m.group(1))
                level = max(0, min(100, level))
                script = f"set volume output volume {level}"
                res = await system_control_service.run_applescript(script)
                spoken = f"Volume set to {level} percent."
                return {
                    "matched": True,
                    "action": "set_volume",
                    "spoken": spoken,
                    "details": res,
                    "tool": "computer_run_applescript",
                    "args": {"script": script}
                }

            elif action == "mute_volume":
                script = "set volume with output muted"
                res = await system_control_service.run_applescript(script)
                return {
                    "matched": True,
                    "action": "mute_volume",
                    "spoken": "Audio muted.",
                    "details": res,
                    "tool": "computer_run_applescript",
                    "args": {"script": script}
                }

            elif action == "unmute_volume":
                script = "set volume without output muted"
                res = await system_control_service.run_applescript(script)
                return {
                    "matched": True,
                    "action": "unmute_volume",
                    "spoken": "Audio unmuted.",
                    "details": res,
                    "tool": "computer_run_applescript",
                    "args": {"script": script}
                }

            elif action == "volume_up":
                script = "set volume output volume ((output volume of (get volume settings)) + 10)"
                res = await system_control_service.run_applescript(script)
                return {
                    "matched": True,
                    "action": "volume_up",
                    "spoken": "Turned volume up.",
                    "details": res,
                    "tool": "computer_run_applescript",
                    "args": {"script": script}
                }

            elif action == "volume_down":
                script = "set volume output volume ((output volume of (get volume settings)) - 10)"
                res = await system_control_service.run_applescript(script)
                return {
                    "matched": True,
                    "action": "volume_down",
                    "spoken": "Turned volume down.",
                    "details": res,
                    "tool": "computer_run_applescript",
                    "args": {"script": script}
                }

            elif action == "hardware":
                res = system_control_service.get_hardware_overview()
                bat = res.get("battery", {})
                pct = bat.get("percent", "Unknown")
                chip = res.get("chip_model", "Apple Silicon")
                spoken = f"Mac running {chip}. Battery is at {pct}."
                return {
                    "matched": True,
                    "action": "hardware",
                    "spoken": spoken,
                    "details": res,
                    "tool": "computer_hardware_overview",
                    "args": {}
                }

            elif action == "read_clipboard":
                res = await system_control_service.read_clipboard()
                text_content = res.get("text", "").strip()
                preview = (text_content[:60] + "...") if len(text_content) > 60 else text_content
                spoken = f"Clipboard contains: {preview}" if preview else "Your clipboard is empty."
                return {
                    "matched": True,
                    "action": "read_clipboard",
                    "spoken": spoken,
                    "details": res,
                    "tool": "computer_read_clipboard",
                    "args": {}
                }

            elif action == "inspect_port":
                port = int(m.group(1))
                res = await system_control_service.inspect_port(port)
                procs = res.get("processes", [])
                if procs:
                    pnames = ", ".join(p.get("command", "") for p in procs)
                    spoken = f"Port {port} is occupied by {pnames}."
                else:
                    spoken = f"Port {port} is free."
                return {
                    "matched": True,
                    "action": "inspect_port",
                    "spoken": spoken,
                    "details": res,
                    "tool": "computer_inspect_port",
                    "args": {"port": port}
                }

            elif action == "disk_space":
                res = system_control_service.get_system_telemetry()
                disk = res.get("disk", {})
                free_gb = disk.get("free_gb", "unknown")
                spoken = f"You have {free_gb} gigabytes of free disk space remaining."
                return {
                    "matched": True,
                    "action": "disk_space",
                    "spoken": spoken,
                    "details": res,
                    "tool": "computer_inspect_system",
                    "args": {}
                }

            elif action == "top_processes":
                res = system_control_service.get_system_telemetry()
                procs = res.get("top_processes", [])
                if procs:
                    top_names = ", ".join(p.get("command", "") for p in procs[:3])
                    spoken = f"Top CPU consumers are {top_names}."
                else:
                    spoken = "System load is normal with no heavy CPU consumers."
                return {
                    "matched": True,
                    "action": "top_processes",
                    "spoken": spoken,
                    "details": res,
                    "tool": "computer_inspect_system",
                    "args": {}
                }

            elif action == "git_status":
                res = await system_control_service.run_command("git status -s")
                stdout = res.get("stdout", "").strip()
                if stdout:
                    lines = len(stdout.splitlines())
                    spoken = f"Git working tree has {lines} modified files."
                else:
                    spoken = "Git working tree is completely clean."
                return {
                    "matched": True,
                    "action": "git_status",
                    "spoken": spoken,
                    "details": res,
                    "tool": "computer_run_command",
                    "args": {"command": "git status -s"}
                }

            elif action == "reveal_finder":
                folder_alias = m.group(1).strip()
                target_path = self._resolve_standard_path(folder_alias)
                res = await system_control_service.reveal_in_finder(target_path)
                spoken = f"Revealing {folder_alias} in Finder."
                return {
                    "matched": True,
                    "action": "reveal_finder",
                    "spoken": spoken,
                    "details": res,
                    "tool": "computer_reveal_finder",
                    "args": {"path": target_path}
                }

        return None

    async def speak_native(self, text: str, voice: Optional[str] = None) -> bool:
        """Speaks text through the Mac's speakers with the user's chosen voice (services/speech.py).
        The text never goes through a shell."""
        from app.services import speech
        try:
            return await speech.speak_out_loud(text, voice)
        except Exception:
            return False

    async def process_voice_command(self, query: str, speak_back: bool = True) -> Dict[str, Any]:
        """
        Main entrypoint:
        1. Fast-path pattern match (<400ms)
        2. Fallback to router / researcher
        3. Records trace to SQLite
        4. Optional macOS native TTS
        """
        t0 = time.time()
        fast_res = await self.execute_fastpath(query)

        if fast_res:
            latency = int((time.time() - t0) * 1000)
            trace_id = record_trace(
                source="voice_fastpath",
                query=query,
                task_type="system",
                route={"tool": fast_res.get("tool"), "args": fast_res.get("args")},
                tool_calls=[{"name": fast_res.get("tool"), "args": fast_res.get("args"), "result": fast_res.get("details")}],
                outcome=fast_res.get("spoken", ""),
                success=True,
                latency_ms=latency,
                model="fastpath_regex"
            )
            if speak_back and fast_res.get("spoken"):
                await self.speak_native(fast_res["spoken"])

            return {
                "success": True,
                "fastpath": True,
                "query": query,
                "spoken": fast_res.get("spoken"),
                "details": fast_res.get("details"),
                "tool": fast_res.get("tool"),
                "latency_ms": latency,
                "trace_id": trace_id
            }

        # If not matched by regex, return note indicating need for agent routing
        return {
            "success": False,
            "fastpath": False,
            "query": query,
            "message": "Command requires deep reasoning or complex multi-step execution."
        }


voice_engine = VoiceEngine()
