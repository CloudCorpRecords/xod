"""
Memory broker: one place that decides what may sit in this Mac's memory.

Consumers: the chat model in LM Studio (the "teacher", ~7 GB), the media worker (image/video
models), specialist mini-model servers, and training runs. Before something big loads, it asks
the broker for room. The broker frees memory in order of how cheap it is to get back:
    1. trim the media worker's caches / unload its model
    2. stop specialist servers (Xod falls back to the teacher automatically)
    3. pause the teacher (unload it from LM Studio, remembering how to reload it)
and gives the teacher back as soon as anything needs to talk to it (llm_service calls
ensure_teacher() before each request), or after the media worker has been idle a while.
"""
import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xod.memory")

HEADROOM_GB = 1.5          # always leave this much free for macOS and the apps you have open
TEACHER_RESTORE_IDLE_SEC = 45  # give the teacher back this long after the last media job

LMS_CLI = Path.home() / ".lmstudio" / "bin" / "lms"


def available_gb() -> float:
    import psutil
    return round(psutil.virtual_memory().available / 1e9, 2)


class MemoryBroker:
    def __init__(self) -> None:
        self._lock: Optional[asyncio.Lock] = None
        self.teacher_paused: Optional[Dict[str, Any]] = None   # what to reload, if we paused it
        self.paused_specialists: List[str] = []
        self.events: List[Dict[str, Any]] = []                  # recent decisions, for the UI / debugging
        self._last_media_use = 0.0
        self._restore_task: Optional[asyncio.Task] = None

    def _lk(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _log(self, text: str) -> None:
        logger.info(text)
        self.events.append({"at": time.time(), "text": text})
        del self.events[:-40]

    # ------------------------------------------------------------------ teacher (LM Studio)
    async def _teacher_status(self) -> Optional[Dict[str, Any]]:
        try:
            from app.config import settings
            if settings.llm_provider != "lm_studio":
                return None
            from app.services.llm import llm_service
            return (await llm_service.get_lmstudio_status()).get("loaded_model")
        except Exception:
            return None

    async def _pause_teacher(self) -> bool:
        loaded = await self._teacher_status()
        if not loaded:
            return False
        from app.services.llm import llm_service
        await llm_service.unload_lmstudio_model(loaded.get("instance_id"))
        self.teacher_paused = {"key": loaded["key"], "context_length": loaded.get("context_length")}
        self._log(f"Paused the chat model ({loaded['key']}) to make room")
        await asyncio.sleep(1.5)
        return True

    async def ensure_teacher(self) -> None:
        """Called before every chat-model request: reloads it if the broker had paused it."""
        if not self.teacher_paused:
            return
        async with self._lk():
            if not self.teacher_paused:
                return
            teacher = self.teacher_paused
            from app.services.media_runtime import media_runtime
            need = 7.5
            if available_gb() < need + HEADROOM_GB:
                await media_runtime.release(full=True)  # the conversation comes first
                # macOS reclaims a stopped process's memory with a short delay; wait for it before loading.
                for _ in range(20):
                    if available_gb() >= need:
                        break
                    await asyncio.sleep(0.5)
            ok = await self._load_teacher(teacher)
            if ok:
                self.teacher_paused = None
                self._log("Chat model is back")

    async def _load_teacher(self, teacher: Dict[str, Any]) -> bool:
        try:
            if LMS_CLI.exists():
                args = [str(LMS_CLI), "load", teacher["key"], "-y"]
                if teacher.get("context_length"):
                    args += ["--context-length", str(teacher["context_length"])]
                p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                         stderr=asyncio.subprocess.DEVNULL)
                await p.wait()
                if p.returncode == 0:
                    return True
            from app.services.llm import llm_service
            return bool((await llm_service.load_lmstudio_model(teacher["key"])).get("success"))
        except Exception as e:
            logger.warning(f"Could not reload the chat model: {e}")
            return False

    # ------------------------------------------------------------------ making room
    async def make_room(self, need_gb: float, purpose: str, keep_media_model: Optional[str] = None) -> Dict[str, Any]:
        """
        Frees memory until need_gb (+ headroom) is available, or reports that it can't.
        keep_media_model: don't unload this media model (it's the one about to be used).
        """
        async with self._lk():
            steps: List[str] = []
            target = need_gb + HEADROOM_GB
            if available_gb() >= target:
                return {"ok": True, "available_gb": available_gb(), "freed": steps}

            from app.services.media_runtime import media_runtime
            # 1. Media worker: drop caches, then any model other than the one we need.
            if await media_runtime.trim(keep_model=keep_media_model):
                steps.append("unloaded the previous media model")
                if await self._wait_for(target, 4):
                    return self._done(steps, purpose)

            # 2. Specialist servers (their task falls back to the teacher by itself).
            from app.services import specialists
            for name in list(specialists._servers):
                await specialists.stop_server(name)
                self.paused_specialists.append(name)
                steps.append(f"stopped mini model {name}")
            if self.paused_specialists and await self._wait_for(target, 3):
                return self._done(steps, purpose)

            # 3. The teacher.
            if await self._pause_teacher():
                steps.append("paused the chat model")
                if await self._wait_for(target, 6):
                    return self._done(steps, purpose)

            if await self._wait_for(target, 4):  # memory from an earlier release may still be arriving
                return self._done(steps or ["waited for memory to free up"], purpose)
            have = available_gb()
            self._log(f"Not enough memory for {purpose}: need {target:.1f} GB, have {have} GB")
            return {"ok": False, "available_gb": have, "need_gb": round(target, 1), "freed": steps}

    @staticmethod
    async def _wait_for(target: float, seconds: float) -> bool:
        """macOS hands a stopped process's memory back over a few seconds; give it that long."""
        for _ in range(int(seconds / 0.5)):
            if available_gb() >= target:
                return True
            await asyncio.sleep(0.5)
        return available_gb() >= target

    def _done(self, steps: List[str], purpose: str) -> Dict[str, Any]:
        self._log(f"Made room for {purpose}: " + ", ".join(steps))
        return {"ok": True, "available_gb": available_gb(), "freed": steps}

    # ------------------------------------------------------------------ giving memory back
    def media_used(self) -> None:
        """Media worker just finished a job: schedule giving the teacher back once it goes idle."""
        self._last_media_use = time.time()
        if self.teacher_paused and (self._restore_task is None or self._restore_task.done()):
            self._restore_task = asyncio.create_task(self._restore_when_idle())

    async def _restore_when_idle(self) -> None:
        while time.time() - self._last_media_use < TEACHER_RESTORE_IDLE_SEC:
            await asyncio.sleep(5)
        from app.services.media_runtime import media_runtime
        await media_runtime.release(full=True)
        await self.ensure_teacher()
        from app.services import specialists
        for name in self.paused_specialists:
            await specialists.start_server(name)
        self.paused_specialists = []

    def status(self) -> Dict[str, Any]:
        return {"available_gb": available_gb(), "headroom_gb": HEADROOM_GB,
                "teacher_paused": self.teacher_paused, "paused_specialists": self.paused_specialists,
                "recent": self.events[-10:]}


memory_broker = MemoryBroker()
