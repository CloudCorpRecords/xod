"""
Muse, driven natively through its own protocol (MSP: `muse serve`, JSON-RPC over stdin/stdout, one JSON object per
line) instead of a terminal: Xod starts and resumes Muse sessions, sends turns, steers or interrupts them, and sees
every step as it happens (its messages, the tools it runs, the files it edits). Muse's questions come to Xod too:
an approval ("may I run npm install?") or a question for you arrives as a server request, Xod shows it to you
(services/crew.py turns it into an approval card) and sends your decision back.

One MuseHost is one `muse serve` process; it can hold several sessions. Ids Muse requires to be UUIDv7 are made
here (Python 3.12 has no uuid7).
"""
import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("xod.muse")

CLIENT = {"name": "xod", "title": "Xod", "version": "0.8.0"}


def uuid7() -> str:
    """A UUIDv7: 48 bits of milliseconds, version 7, the variant bits, and randomness."""
    ms = int(time.time() * 1000)
    r = int.from_bytes(os.urandom(10), "big")
    v = (ms << 80) | (0x7 << 76) | (((r >> 64) & 0xFFF) << 64) | (0b10 << 62) | (r & ((1 << 62) - 1))
    return str(uuid.UUID(int=v))


class MuseError(Exception):
    def __init__(self, message: str, code: int = 0, data: Any = None):
        super().__init__(message)
        self.code, self.data = code, data


Handler = Callable[[str, Dict[str, Any]], Awaitable[None]]


class MuseHost:
    """One `muse serve` process and Xod's connection to it."""

    def __init__(self, muse_path: str, cwd: str, env: Optional[Dict[str, str]] = None, provider: str = "", extra: Optional[List[str]] = None):
        self.muse_path, self.cwd, self.env, self.provider = muse_path, cwd, env, provider
        self.extra = list(extra or [])
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._next = 0
        self._waiting: Dict[int, asyncio.Future] = {}
        self._reader: Optional[asyncio.Task] = None
        self._stderr: List[str] = []
        self.on_event: Optional[Handler] = None        # every notification: (method, params)
        self.on_request: Optional[Handler] = None      # approval/request, userInput/request: (method, params)
        self.info: Dict[str, Any] = {}

    async def start(self, timeout: float = 120) -> Dict[str, Any]:
        args = ["serve"] + (["--provider", self.provider] if self.provider else []) + self.extra
        self.proc = await asyncio.create_subprocess_exec(self.muse_path, *args, cwd=self.cwd, env=self.env,
                                                         stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read())
        asyncio.create_task(self._read_stderr())
        self.info = await self.call("initialize", {"clientInfo": CLIENT}, timeout=timeout)
        await self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})    # the handshake's last step
        return self.info

    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()
        for f in self._waiting.values():
            if not f.done():
                f.set_exception(MuseError("Muse stopped."))
        self._waiting.clear()

    async def _send(self, obj: Dict[str, Any]) -> None:
        if not self.running():
            raise MuseError("Muse isn't running" + (f": {self._stderr[-1]}" if self._stderr else "."))
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self.proc.stdin.drain()

    async def call(self, method: str, params: Dict[str, Any], timeout: float = 120) -> Any:
        """A request; waits for its own answer (other messages keep flowing meanwhile)."""
        self._next += 1
        rid = self._next
        fut = asyncio.get_running_loop().create_future()
        self._waiting[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise MuseError(f"Muse didn't answer {method} within {int(timeout)} s.")
        finally:
            self._waiting.pop(rid, None)

    async def command(self, method: str, params: Dict[str, Any], timeout: float = 120) -> Any:
        """A command (they all carry a UUIDv7 commandId Muse uses to drop duplicates)."""
        return await self.call(method, {"commandId": uuid7(), **params}, timeout=timeout)

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                logger.warning(f"Muse sent something that isn't JSON: {line[:200]!r}")
                continue
            try:
                await self._dispatch(msg)
            except Exception as e:
                logger.warning(f"Handling a Muse message failed: {e}")
        for f in self._waiting.values():
            if not f.done():
                f.set_exception(MuseError("Muse stopped" + (f": {self._stderr[-1]}" if self._stderr else ".")))

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            self._stderr.append(line.decode("utf-8", "replace").strip()[:500])
            del self._stderr[:-50]

    async def _dispatch(self, msg: Dict[str, Any]) -> None:
        if "id" in msg and "method" not in msg:                     # an answer to one of ours
            fut = self._waiting.get(msg["id"])
            if fut and not fut.done():
                if "error" in msg:
                    e = msg["error"] or {}
                    fut.set_exception(MuseError(e.get("message") or "Muse refused that.", e.get("code") or 0, e.get("data")))
                else:
                    fut.set_result(msg.get("result"))
            return
        method, params = msg.get("method") or "", msg.get("params") or {}
        if "id" in msg:                                             # Muse asks us something: receipt now, answer later
            await self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
            if self.on_request:
                asyncio.create_task(self.on_request(method, params))
            return
        if self.on_event:
            await self.on_event(method, params)

    # ------------------------------------------------------------------ the protocol, as Xod uses it
    async def start_session(self, workspace: str, approval_mode: Optional[str] = None,
                            mcp_servers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        p: Dict[str, Any] = {"workspaceRoot": workspace}
        if approval_mode:
            p["approvalMode"] = approval_mode
        if mcp_servers:
            p["config"] = {"mcpServers": mcp_servers}
        res = await self.command("session/start", p)
        return res["session"]

    async def resume_session(self, session_id: str) -> Dict[str, Any]:
        res = await self.command("session/resume", {"sessionId": session_id, "excludeItems": True})
        return res.get("session") or res

    async def send(self, session_id: str, text: str, if_busy: str = "queue") -> Dict[str, Any]:
        return await self.command("turn/start", {"sessionId": session_id, "input": [{"type": "text", "text": text}], "ifBusy": if_busy})

    async def interrupt(self, session_id: str) -> Any:
        return await self.command("turn/interrupt", {"sessionId": session_id})

    async def decide(self, session_id: str, approval_id: str, choice_id: str, requirement_id: Any = None,
                     feedback: Optional[str] = None) -> Any:
        p: Dict[str, Any] = {"sessionId": session_id, "approvalId": approval_id, "choiceId": choice_id}
        if requirement_id is not None:
            p["requirementId"] = requirement_id
        if feedback:
            p["feedback"] = feedback
        return await self.command("approval/decide", p)

    async def answer(self, session_id: str, user_input_id: str, answers: List[Dict[str, Any]]) -> Any:
        return await self.command("userInput/answer", {"sessionId": session_id, "userInputId": user_input_id, "answers": answers})

    async def pending(self, session_id: str) -> Dict[str, Any]:
        return await self.call("approval/listPending", {"sessionId": session_id})

    async def set_approval_mode(self, session_id: str, mode: str) -> Any:
        return await self.command("session/setApprovalMode", {"sessionId": session_id, "mode": mode})
