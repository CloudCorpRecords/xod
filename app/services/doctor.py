"""
Xod's doctor: checks that the machinery works and fixes what it safely can.

Checks: chat model (LM Studio), memory, disk, browser, media engine, task inbox, API keys, and
recent tool failures. Each failing check can carry a fix. Fixes are things Xod can undo or that
only touch Xod's own processes: reload the chat model, restart Xod's browser, stop a hung media
worker or task, free memory. Nothing here touches the user's files, accounts, or other apps.

Runs at startup and every few minutes (applying only the hands-off fixes: hung tasks and hung
media jobs), on demand from the Health panel or the agent's xod_doctor tool, and inside repairs
(healing.Repairer calls auto_fix() for the kind of failure it saw).

It also remembers what fixed a failure (data/repair_lessons.json) so the next repair of the same
kind of problem starts from what worked.
"""
import asyncio
import json
import logging
import shutil
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional
from app.paths import DATA_DIR

logger = logging.getLogger("xod.doctor")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
LESSONS_PATH = DATA_DIR / "repair_lessons.json"
CHECK_EVERY_SEC = 300
TASK_STUCK_SEC = 1200


def _check(name: str, label: str, level: str, detail: str, fix: Optional[str] = None, fix_label: Optional[str] = None):
    return {"name": name, "label": label, "level": level, "ok": level == "ok", "detail": detail,
            "fix": fix, "fix_label": fix_label}


class Doctor:
    def __init__(self) -> None:
        self.failures: Deque[Dict[str, Any]] = deque(maxlen=60)
        self.fixes: Deque[Dict[str, Any]] = deque(maxlen=40)   # what the doctor did, for the UI
        self.last_report: Optional[Dict[str, Any]] = None
        self._loop_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ memory of failures / fixes
    def record_failure(self, tool: str, kind: str, detail: str) -> None:
        self.failures.append({"at": time.time(), "tool": tool, "kind": kind, "detail": (detail or "")[:300]})

    def _did(self, text: str) -> str:
        self.fixes.append({"at": time.time(), "text": text})
        logger.info(f"Doctor: {text}")
        return text

    def _lessons(self) -> List[Dict[str, Any]]:
        try:
            return json.loads(LESSONS_PATH.read_text())
        except Exception:
            return []

    def record_fix(self, tool: str, kind: str, detail: str, fix_tool: str, fix_args: Dict[str, Any],
                   auto_fix: Optional[str] = None) -> None:
        """A repair worked: remember it so similar failures start from this fix."""
        lessons = self._lessons()
        key = (tool, kind, fix_tool)
        for l in lessons:
            if (l["tool"], l["kind"], l["fix_tool"]) == key:
                l.update(count=l.get("count", 1) + 1, last=time.time(), example_error=detail[:200],
                         example_args=fix_args, auto_fix=auto_fix or l.get("auto_fix"))
                break
        else:
            lessons.append({"tool": tool, "kind": kind, "fix_tool": fix_tool, "example_error": detail[:200],
                            "example_args": fix_args, "auto_fix": auto_fix, "count": 1, "last": time.time()})
        lessons = sorted(lessons, key=lambda l: -l.get("last", 0))[:200]
        try:
            LESSONS_PATH.write_text(json.dumps(lessons, indent=1, default=str))
        except Exception as e:
            logger.warning(f"Couldn't save repair lesson: {e}")

    def lessons_for(self, tool: str, kind: str) -> List[Dict[str, Any]]:
        same = [l for l in self._lessons() if l["tool"] == tool and l["kind"] == kind]
        return [{"fix": f"{l['fix_tool']} with args like {json.dumps(l['example_args'], default=str)[:200]}",
                 "worked_times": l.get("count", 1)} for l in sorted(same, key=lambda l: -l.get("count", 1))[:3]]

    def planner_notes(self, limit: int = 5) -> List[str]:
        """Facts about this setup learned from repairs, worth telling the planner up front
        (e.g. a search service has no key here). Only environment facts: one-off typos aren't."""
        notes = []
        for l in sorted(self._lessons(), key=lambda l: -l.get("count", 1)):
            if l["kind"] in ("missing_key", "blocked") and l["fix_tool"] != l["tool"]:
                notes.append(f"{l['tool']} doesn't work here ({l['kind'].replace('_', ' ')}); {l['fix_tool']} worked instead.")
            elif l["kind"] == "bad_args" and l["fix_tool"] == l["tool"] and l.get("count", 1) >= 2:
                keys = ", ".join(sorted((l.get("example_args") or {}).keys()))
                notes.append(f"{l['tool']} needs arguments like: {keys}.")
            if len(notes) >= limit:
                break
        return notes

    # ------------------------------------------------------------------ individual checks
    async def _check_chat_model(self) -> Dict[str, Any]:
        from app.config import settings
        if settings.llm_provider == "xod":
            from app.services.local_engine import local_engine, is_downloaded, catalog_entry
            from app.services.llm import llm_service
            repo = llm_service.get_endpoint_info()["model"]
            label = (catalog_entry(repo) or {}).get("label", repo)
            if not is_downloaded(repo):
                return _check("chat_model", "Chat model", "fail", f"Built-in model {label} isn't downloaded yet.")
            if local_engine.running():
                return _check("chat_model", "Chat model", "ok", f"{label} running (built-in)")
            if local_engine.last_error:
                return _check("chat_model", "Chat model", "fail", f"{label} failed to start: {local_engine.last_error[-160:]}",
                              "load_chat_model", "Start it")
            return _check("chat_model", "Chat model", "ok", f"{label} ready (starts with your next message)")
        if settings.llm_provider != "lm_studio":
            key_field = f"{settings.llm_provider}_api_key"
            has = bool(getattr(settings, key_field, "")) if hasattr(settings, key_field) else True
            return _check("chat_model", "Chat model", "ok" if has else "fail",
                          f"{settings.llm_provider}: {settings.llm_model}" + ("" if has else " (no API key set)"))
        from app.services.llm import llm_service
        from app.services.memory_broker import memory_broker
        status = await llm_service.get_lmstudio_status()
        if not status or status.get("error") or status.get("online") is False or "models" not in status:
            return _check("chat_model", "Chat model", "fail", "LM Studio isn't responding.", "start_lmstudio", "Start LM Studio")
        loaded = status.get("loaded_model")
        if loaded:
            return _check("chat_model", "Chat model", "ok", f"{loaded.get('key')} loaded")
        if memory_broker.teacher_paused:
            return _check("chat_model", "Chat model", "ok", "Paused to make room for media; comes back on its own")
        return _check("chat_model", "Chat model", "fail", f"No model loaded in LM Studio (Xod uses {settings.llm_model}).",
                      "load_chat_model", "Load it")

    def _check_memory(self) -> Dict[str, Any]:
        import subprocess
        import psutil
        from app.services.media_runtime import media_runtime
        avail = psutil.virtual_memory().available / 1e9
        swap = psutil.swap_memory().used / 1e9
        try:  # macOS's own verdict: 1 normal, 2 warning, 4 critical
            level = subprocess.check_output(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], text=True).strip()
        except Exception:
            level = "1"
        detail = f"{avail:.1f} GB free, {swap:.1f} GB swap in use"
        fix = ("free_memory", "Free memory") if media_runtime.alive else (None, None)
        tip = " Closing apps you aren't using, or giving the chat model a shorter context in LM Studio, frees the most."
        if level == "4" or avail < 0.5:
            return _check("memory", "Memory", "fail", detail + ". macOS reports critical memory pressure; things will slow down." + tip, *fix)
        if level == "2":
            return _check("memory", "Memory", "warn", detail + ". macOS reports elevated memory pressure." + tip, *fix)
        return _check("memory", "Memory", "ok", detail)

    def _check_disk(self) -> Dict[str, Any]:
        free = shutil.disk_usage(str(BASE_DIR)).free / 1e9
        if free < 5:
            return _check("disk", "Disk space", "fail", f"Only {free:.0f} GB free; downloads and videos will fail.")
        if free < 20:
            return _check("disk", "Disk space", "warn", f"{free:.0f} GB free")
        return _check("disk", "Disk space", "ok", f"{free:.0f} GB free")

    async def _browser_reachable(self) -> bool:
        import httpx
        from app.services.browser_sandbox import DEBUG_PORT
        try:
            async with httpx.AsyncClient(timeout=1.5) as c:
                r = await c.get(f"http://127.0.0.1:{DEBUG_PORT}/json/version")
                return r.status_code == 200
        except Exception:
            return False

    async def _check_browser(self) -> Dict[str, Any]:
        from app.services.browser_sandbox import browser_sandbox_service as sb
        if await self._browser_reachable():
            return _check("browser", "Browser", "ok", "Xod's browser is running")
        if getattr(sb, "proc", None) is not None:
            return _check("browser", "Browser", "fail", "Xod's browser stopped responding.", "restart_browser", "Restart it")
        return _check("browser", "Browser", "ok", "Not running (starts when a task needs it)")

    def _check_media(self) -> Dict[str, Any]:
        from app.services.media_runtime import REQUEST_TIMEOUT_SEC, media_runtime
        if media_runtime.alive and media_runtime._lock().locked() and \
                time.time() - (media_runtime.job_started or time.time()) > REQUEST_TIMEOUT_SEC + 60:
            return _check("media", "Image & video engine", "fail", "A media job has been running far too long.",
                          "restart_media", "Stop it")
        if media_runtime.alive:
            name = (media_runtime.loaded_model or "starting").split("/")[-1]
            return _check("media", "Image & video engine", "ok", f"Running ({name})")
        return _check("media", "Image & video engine", "ok", "Idle (no memory in use)")

    def _check_tasks(self) -> Dict[str, Any]:
        from app.services.task_queue import task_queue
        stuck = task_queue.stuck(TASK_STUCK_SEC)
        if stuck:
            return _check("tasks", "Task inbox", "fail",
                          f"{len(stuck)} task(s) stopped making progress: " + ", ".join(t['title'][:40] for t in stuck),
                          "recover_tasks", "Stop them")
        c = task_queue.counts()
        return _check("tasks", "Task inbox", "ok", ", ".join(f"{v} {k}" for k, v in c.items() if v) or "Empty")

    def _check_keys(self) -> Dict[str, Any]:
        from app.services import secret_store
        saved = [s["name"].replace("_api_key", "").replace("_token", "") for s in secret_store.status() if s["saved"]]
        search = [n for n in ("tavily", "you") if n in saved]
        if not search:
            return _check("keys", "API keys", "warn", "No web search key (Tavily or You.com); web search falls back to free sources.")
        return _check("keys", "API keys", "ok", "In the Keychain: " + ", ".join(saved))

    def _check_failures(self) -> Dict[str, Any]:
        recent = [f for f in self.failures if time.time() - f["at"] < 3600]
        if not recent:
            return _check("failures", "Recent tool errors", "ok", "None in the last hour")
        kinds: Dict[str, int] = {}
        for f in recent:
            kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
        top = sorted(kinds.items(), key=lambda kv: -kv[1])
        detail = ", ".join(f"{n}× {k.replace('_', ' ')}" for k, n in top[:4]) + " in the last hour"
        return _check("failures", "Recent tool errors", "warn" if top[0][1] >= 3 else "ok", detail)

    async def check(self, apply_safe_fixes: bool = False) -> Dict[str, Any]:
        checks: List[Dict[str, Any]] = []
        for fn in (self._check_chat_model, self._check_memory, self._check_disk, self._check_browser,
                   self._check_media, self._check_tasks, self._check_keys, self._check_failures):
            name = fn.__name__.replace("_check_", "")
            try:
                r = fn()
                checks.append(await r if asyncio.iscoroutine(r) else r)
            except Exception as e:
                checks.append(_check(name, name.replace("_", " ").title(), "warn", f"Couldn't check: {e}"))
        if apply_safe_fixes:
            # Hands-off fixes only: things that are clearly hung inside Xod.
            for c in checks:
                if not c["ok"] and c["fix"] in ("recover_tasks", "restart_media"):
                    res = await self.fix(c["fix"])
                    c["detail"] += f" → {res['detail']}"
        overall = "fail" if any(c["level"] == "fail" for c in checks) else "warn" if any(c["level"] == "warn" for c in checks) else "ok"
        self.last_report = {"at": time.time(), "overall": overall, "checks": checks,
                            "recent_fixes": list(self.fixes)[-10:], "recent_failures": list(self.failures)[-10:]}
        return self.last_report

    # ------------------------------------------------------------------ fixes
    async def fix(self, fix_id: str) -> Dict[str, Any]:
        try:
            if fix_id == "load_chat_model":
                from app.config import settings
                if settings.llm_provider == "xod":
                    from app.services.local_engine import local_engine
                    from app.services.llm import llm_service
                    await local_engine.ensure_running(llm_service.get_endpoint_info()["model"])
                    return {"ok": True, "detail": self._did("Started the built-in model")}
                return {"ok": await self._load_chat_model(), "detail": self.fixes[-1]["text"] if self.fixes else ""}
            if fix_id == "start_lmstudio":
                from app.services.memory_broker import LMS_CLI
                if LMS_CLI.exists():
                    p = await asyncio.create_subprocess_exec(str(LMS_CLI), "server", "start",
                                                             stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await asyncio.wait_for(p.wait(), timeout=60)
                    ok = await self._load_chat_model()
                    return {"ok": ok, "detail": self._did("Started LM Studio's server" + (" and loaded the chat model" if ok else ""))}
                return {"ok": False, "detail": "LM Studio's command-line tool isn't installed; open LM Studio yourself."}
            if fix_id == "restart_browser":
                from app.services.browser_sandbox import browser_sandbox_service as sb
                res = await sb.restart_browser(clear_profile=False, force=True)  # never wipes logins
                ok = await self._browser_reachable()
                return {"ok": ok, "detail": self._did("Restarted Xod's browser" if ok else f"Browser restart failed: {res.get('error')}")}
            if fix_id == "free_memory":
                from app.services.media_runtime import media_runtime
                released = await media_runtime.release(full=True)
                return {"ok": True, "detail": self._did("Stopped the media engine to free memory" if released
                                                        else "Nothing of Xod's to free; close other apps to get memory back")}
            if fix_id == "restart_media":
                from app.services.media_runtime import media_runtime
                await media_runtime._kill()
                return {"ok": True, "detail": self._did("Stopped a hung media job (the engine restarts on the next request)")}
            if fix_id == "recover_tasks":
                from app.services.task_queue import task_queue
                fixed = await task_queue.recover_stuck(TASK_STUCK_SEC)
                return {"ok": True, "detail": self._did(f"Stopped {len(fixed)} hung task(s); retry them from the inbox") if fixed else "No hung tasks"}
            return {"ok": False, "detail": f"Unknown fix {fix_id}"}
        except Exception as e:
            logger.exception(f"Fix {fix_id} failed")
            return {"ok": False, "detail": f"The fix failed: {e}"}

    async def _load_chat_model(self) -> bool:
        from app.config import settings
        from app.services.memory_broker import memory_broker
        if memory_broker.teacher_paused:
            await memory_broker.ensure_teacher()
            ok = not memory_broker.teacher_paused
        else:
            ok = await memory_broker._load_teacher({"key": settings.llm_model, "context_length": 32768})
        self._did(f"Loaded the chat model ({settings.llm_model})" if ok else "Couldn't load the chat model")
        return ok

    async def auto_fix(self, kind: str) -> Optional[str]:
        """Called by repairs: fix the machinery behind a kind of failure. Returns what it did, or None."""
        try:
            if kind == "browser_down":
                from app.services.browser_sandbox import browser_sandbox_service as sb
                if await self._browser_reachable():
                    res = await sb.unstuck(reload_if_blank=True)
                    if res.get("success", True):
                        return self._did("Unstuck the browser page")
                r = await self.fix("restart_browser")
                return r["detail"] if r["ok"] else None
            if kind == "model_unavailable":
                c = await self._check_chat_model()
                if c["ok"]:
                    return None
                r = await self.fix(c["fix"] or "load_chat_model")
                return r["detail"] if r["ok"] else None
            if kind == "memory":
                r = await self.fix("free_memory")
                return r["detail"] if "Stopped" in r["detail"] else None
            if kind == "rate_limit":
                await asyncio.sleep(5)
                return self._did("Waited 5 s for the rate limit")
            if kind in ("network", "timeout"):
                await asyncio.sleep(2)
                return None
        except Exception as e:
            logger.warning(f"auto_fix({kind}) failed: {e}")
        return None

    # ------------------------------------------------------------------ background
    def start(self) -> None:
        if not self._loop_task or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        await asyncio.sleep(20)  # let the app finish starting
        while True:
            try:
                report = await self.check(apply_safe_fixes=True)
                bad = [c["label"] for c in report["checks"] if c["level"] == "fail"]
                if bad:
                    logger.warning("Health check: problems with " + ", ".join(bad))
            except Exception as e:
                logger.warning(f"Health check failed: {e}")
            await asyncio.sleep(CHECK_EVERY_SEC)


doctor = Doctor()
