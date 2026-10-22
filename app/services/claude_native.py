"""
Claude Code, natively: one running `claude -p` per Crew job, spoken to over its stream-json protocol (the same one the
Claude Agent SDK uses). Newline-delimited JSON both ways:

  in   {"type": "user", "message": {...}}                       a message (also a follow-up, while it keeps running)
       {"type": "control_request", "request": {"subtype": "initialize" | "interrupt"}}
       {"type": "control_response", "response": {...}}          the answer to one of its permission requests
  out  {"type": "system", "subtype": "init", "session_id": ...} then assistant / user (tool results) messages, and
       {"type": "result", ...} at the end of each turn (with usage and cost)
       {"type": "control_request", "request": {"subtype": "can_use_tool", ...}}   with --permission-prompt-tool stdio:
                                                                 every permission Claude Code would ask you for

Nothing is approved here: requests go to on_request (the Crew turns them into approvals on the board), and the
person's decision comes back through allow() or deny().
"""
import asyncio
import json
import logging
import os
import signal
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("xod.claude_native")


class ClaudeError(Exception):
    pass


class ClaudeRun:
    def __init__(self, path: str, cwd: str, env: Optional[Dict[str, str]] = None, resume: str = "",
                 mcp_config: str = "", model: str = ""):
        self.path, self.cwd, self.env = path, cwd, env or dict(os.environ)
        self.resume, self.mcp_config, self.model = resume, mcp_config, model
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.session_id = resume or ""
        self._pending: Dict[str, asyncio.Future] = {}
        self._reader: Optional[asyncio.Task] = None
        self._stderr: List[str] = []
        self.on_event: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self.on_request: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self.on_exit: Optional[Callable[[Optional[int], str], Awaitable[None]]] = None

    def argv(self) -> List[str]:
        a = [self.path, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
             "--permission-prompt-tool", "stdio"]
        if self.resume:
            a += ["--resume", self.resume]
        if self.mcp_config:
            a += ["--mcp-config", self.mcp_config]
        if self.model:
            a += ["--model", self.model]
        return a

    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self, timeout: float = 60) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv(), cwd=self.cwd, env=self.env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=16 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read())
        asyncio.create_task(self._read_err())
        try:
            await self.control({"subtype": "initialize", "hooks": None}, timeout=timeout)
        except ClaudeError:
            await self.stop()
            raise

    async def _write(self, obj: Dict[str, Any]) -> None:
        if not self.running():
            raise ClaudeError("Claude Code has stopped." + (f" It said: {self.last_error()}" if self.last_error() else ""))
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self.proc.stdin.drain()

    async def control(self, request: Dict[str, Any], timeout: float = 30) -> Any:
        rid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._write({"type": "control_request", "request_id": rid, "request": request})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise ClaudeError(f"Claude Code didn't answer ({request.get('subtype')}).")
        finally:
            self._pending.pop(rid, None)

    async def send(self, text: str) -> None:
        await self._write({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]},
                           "parent_tool_use_id": None, "session_id": self.session_id})

    async def interrupt(self) -> None:
        await self.control({"subtype": "interrupt"}, timeout=15)

    async def allow(self, request_id: str, tool_input: Dict[str, Any], permissions: Optional[List[Dict[str, Any]]] = None) -> None:
        resp: Dict[str, Any] = {"behavior": "allow", "updatedInput": tool_input}
        if permissions:
            resp["updatedPermissions"] = permissions
        await self._write({"type": "control_response", "response": {"subtype": "success", "request_id": request_id, "response": resp}})

    async def deny(self, request_id: str, message: str = "The person said no.") -> None:
        await self._write({"type": "control_response", "response": {"subtype": "success", "request_id": request_id,
                                                                     "response": {"behavior": "deny", "message": message}}})

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            try:
                line = await self.proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                continue                                    # one oversized line: skip it, keep the job going
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            t = msg.get("type")
            try:
                if t == "control_response":
                    r = msg.get("response") or {}
                    fut = self._pending.get(r.get("request_id") or "")
                    if fut and not fut.done():
                        if r.get("subtype") == "error":
                            fut.set_exception(ClaudeError(r.get("error") or "Claude Code refused that."))
                        else:
                            fut.set_result(r.get("response"))
                elif t == "control_request":
                    if self.on_request:
                        await self.on_request(msg)
                else:
                    if t == "system" and msg.get("subtype") == "init" and msg.get("session_id"):
                        self.session_id = msg["session_id"]
                    if self.on_event:
                        await self.on_event(msg)
            except Exception:
                logger.exception("Claude Code event")
        code = await self.proc.wait()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ClaudeError("Claude Code stopped." + (f" It said: {self.last_error()}" if self.last_error() else "")))
        if self.on_exit:
            try:
                await self.on_exit(code, self.last_error())
            except Exception:
                logger.exception("Claude Code exit")

    async def _read_err(self) -> None:
        assert self.proc and self.proc.stderr
        async for line in self.proc.stderr:
            self._stderr.append(line.decode("utf-8", "replace").rstrip())
            del self._stderr[:-40]

    def last_error(self) -> str:
        return "\n".join(x for x in self._stderr[-6:] if x.strip())[-600:]

    async def stop(self) -> None:
        if not self.proc:
            return
        if self.running():
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(self.proc.wait(), 3)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                    except Exception:
                        pass
