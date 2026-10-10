"""
Task inbox: jobs you hand to Xod that keep going without you watching.

A task runs in the background, in its own browser tab, through the same agent as chat.
It ends up in one of three places:
    working  - Xod is on it
    waiting  - it needs you: approve an irreversible step, answer a question, or clear a bot check
    done / failed
Waiting tasks keep their tab and their pending approval, so answering later picks up exactly
where it paused. You get a Mac notification when a task needs you or finishes.

One task runs at a time (there's one browser); the rest queue up. Tasks survive restarts.
"""
import asyncio
import base64
import json
import logging
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.paths import DATA_DIR

logger = logging.getLogger("xod.tasks")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = DATA_DIR / "xod.db"
SHOTS_DIR = DATA_DIR / "tasks"

LIVE_FIELDS = ("id", "title", "request", "status", "waiting_kind", "waiting_detail", "result", "now",
               "created_at", "updated_at", "finished_at", "steps", "has_screenshot", "history")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _init() -> None:
    c = _conn()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            finished_at REAL,
            title TEXT NOT NULL,
            request TEXT NOT NULL,
            status TEXT NOT NULL,          -- queued | working | waiting | done | failed | cancelled
            next_action TEXT,              -- json: what the worker should do next
            waiting_kind TEXT,             -- approval | question | blocked
            waiting_detail TEXT,           -- json shown to the user
            result TEXT,                   -- final report (markdown)
            now TEXT,                      -- latest progress line
            steps INTEGER DEFAULT 0,
            tab_id TEXT,
            mission_json TEXT,             -- last mission record (to resume from)
            pending_json TEXT,             -- pending approval saved for this task
            has_screenshot INTEGER DEFAULT 0,
            history TEXT DEFAULT '[]'
        )
        """
    )
    c.commit()
    c.close()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    for k in ("next_action", "waiting_detail", "mission_json", "pending_json", "history"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


def notify(title: str, message: str) -> None:
    """Mac notification; never blocks or fails the task."""
    esc = lambda t: (t or "").replace("\\", "\\\\").replace('"', '\\"')[:220]
    try:
        subprocess.Popen(["osascript", "-e", f'display notification "{esc(message)}" with title "Xod" subtitle "{esc(title)}"'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


class TaskQueue:
    def __init__(self) -> None:
        _init()
        self._wake: Optional[asyncio.Event] = None
        self._worker: Optional[asyncio.Task] = None
        self._running_id: Optional[str] = None
        self._cancel_requested: set = set()

    # ------------------------------------------------------------------ storage
    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        c = _conn()
        r = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        c.close()
        return _row(r) if r else None

    # Status lines worth keeping as progress notes (tool steps, repairs, waits, approvals).
    _PROGRESS_MARKS = ("✓", "✗", "⏸", "•", "⏳", "🩺", "🔧", "↪", "⚡", "🔄")

    def _progress(self, task_id: str, line: str) -> None:
        """Adds a progress note; consecutive "⏳ waiting" lines replace each other instead of piling up."""
        c = _conn()
        r = c.execute("SELECT history FROM tasks WHERE id = ?", (task_id,)).fetchone()
        hist = json.loads(r["history"] or "[]") if r else []
        entry = {"at": time.time(), "text": line[:300], "kind": "progress"}
        if hist and line.startswith("⏳") and str(hist[-1].get("text", "")).startswith("⏳"):
            hist[-1] = entry
        else:
            hist.append(entry)
        c.execute("UPDATE tasks SET history = ? WHERE id = ?", (json.dumps(hist[-40:]), task_id))
        c.commit()
        c.close()

    def _update(self, task_id: str, note: Optional[str] = None, **fields: Any) -> None:
        fields["updated_at"] = time.time()
        for k in ("next_action", "waiting_detail", "mission_json", "pending_json"):
            if k in fields and fields[k] is not None and not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k])
        c = _conn()
        if note:
            r = c.execute("SELECT history FROM tasks WHERE id = ?", (task_id,)).fetchone()
            hist = json.loads(r["history"] or "[]") if r else []
            hist.append({"at": time.time(), "text": note[:300]})
            fields["history"] = json.dumps(hist[-40:])
        cols = ", ".join(f"{k} = ?" for k in fields)
        c.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))
        c.commit()
        c.close()

    def list(self, limit: int = 60) -> List[Dict[str, Any]]:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM tasks ORDER BY CASE status WHEN 'waiting' THEN 0 WHEN 'working' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END, "
            "updated_at DESC LIMIT ?", (limit,)).fetchall()
        c.close()
        return [{k: v for k, v in _row(r).items() if k in LIVE_FIELDS} for r in rows]

    def counts(self) -> Dict[str, int]:
        c = _conn()
        rows = c.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status").fetchall()
        c.close()
        return {r["status"]: r["n"] for r in rows}

    # ------------------------------------------------------------------ user actions
    def create(self, request: str, title: Optional[str] = None) -> Dict[str, Any]:
        request = (request or "").strip()
        if not request:
            raise ValueError("Tell Xod what to do.")
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        now = time.time()
        title = title or (request if len(request) <= 70 else request[:67].rsplit(" ", 1)[0] + "…")
        c = _conn()
        c.execute(
            "INSERT INTO tasks (id, created_at, updated_at, title, request, status, next_action, history) VALUES (?,?,?,?,?,?,?,?)",
            (task_id, now, now, title, request, "queued", json.dumps({"type": "start"}),
             json.dumps([{"at": now, "text": "Added to your tasks"}])),
        )
        c.commit()
        c.close()
        self._kick()
        return self.get(task_id)

    def approve(self, task_id: str) -> Dict[str, Any]:
        t = self.get(task_id)
        if not t or t["status"] != "waiting" or t.get("waiting_kind") != "approval":
            return {"success": False, "error": "This task isn't waiting for an approval."}
        self._update(task_id, note="You approved it", status="queued", next_action={"type": "approve"},
                     waiting_kind=None, now="Approved; continuing")
        self._kick()
        return {"success": True}

    def answer(self, task_id: str, text: str) -> Dict[str, Any]:
        t = self.get(task_id)
        if not t or t["status"] not in ("waiting", "failed"):
            return {"success": False, "error": "This task isn't waiting for you."}
        text = (text or "").strip()
        if not text:
            return {"success": False, "error": "Write an answer first."}
        self._update(task_id, note=f"You replied: {text}", status="queued",
                     next_action={"type": "continue", "text": text}, waiting_kind=None, now="Continuing with your answer")
        self._kick()
        return {"success": True}

    def retry(self, task_id: str) -> Dict[str, Any]:
        t = self.get(task_id)
        if not t or t["status"] not in ("failed", "waiting", "cancelled"):
            return {"success": False, "error": "Only stopped tasks can be retried."}
        # A bot check the user cleared continues on the same page; everything else picks up where it stopped.
        self._update(task_id, note="Trying again", status="queued",
                     next_action={"type": "continue", "text": "continue"}, waiting_kind=None, now="Trying again")
        self._kick()
        return {"success": True}

    async def cancel(self, task_id: str) -> Dict[str, Any]:
        t = self.get(task_id)
        if not t or t["status"] in ("done", "cancelled"):
            return {"success": False, "error": "Nothing to cancel."}
        if t["status"] == "working":
            self._cancel_requested.add(task_id)  # stops after the current step
            self._update(task_id, note="Stopping after the current step", now="Stopping…")
            return {"success": True, "stopping": True}
        self._update(task_id, note="Cancelled", status="cancelled", waiting_kind=None, finished_at=time.time(), next_action=None)
        await self._close_tab(t.get("tab_id"))
        return {"success": True}

    def delete(self, task_id: str) -> bool:
        t = self.get(task_id)
        if not t or t["status"] == "working":
            return False
        c = _conn()
        c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        c.commit()
        c.close()
        (SHOTS_DIR / f"{task_id}.jpg").unlink(missing_ok=True)
        return True

    def screenshot_path(self, task_id: str) -> Optional[Path]:
        p = SHOTS_DIR / f"{task_id}.jpg"
        return p if p.exists() else None

    # ------------------------------------------------------------------ worker
    def _kick(self) -> None:
        if self._wake:
            self._wake.set()

    def stuck(self, max_quiet_sec: float = 1200) -> List[Dict[str, Any]]:
        """Tasks marked working that haven't reported progress in a long time."""
        c = _conn()
        rows = c.execute("SELECT id, title, updated_at FROM tasks WHERE status = 'working' AND updated_at < ?",
                         (time.time() - max_quiet_sec,)).fetchall()
        c.close()
        return [dict(r) for r in rows]

    async def recover_stuck(self, max_quiet_sec: float = 1200) -> List[str]:
        """Stops tasks that hung (no progress for max_quiet_sec), marks them failed, restarts the worker."""
        fixed = []
        for t in self.stuck(max_quiet_sec):
            if t["id"] == self._running_id and self._worker and not self._worker.done():
                self._worker.cancel()  # a hung await never sees the cancel flag; cancel the worker itself
                try:
                    await asyncio.wait_for(asyncio.shield(self._worker), timeout=5)
                except BaseException:
                    pass
                self._running_id = None
            self._update(t["id"], note="Stopped by Xod's health check: no progress for a long time", status="failed",
                         finished_at=time.time(), now="Stopped (it hung)", next_action=None,
                         result="This task stopped making progress, so Xod stopped it. Retry it from the inbox.")
            fixed.append(t["title"])
        if fixed and self._wake is not None and (not self._worker or self._worker.done()):
            self._worker = asyncio.create_task(self._loop())  # only restart a worker that was running
        return fixed

    def start(self) -> None:
        """Called at app startup. Interrupted tasks go back in the queue."""
        c = _conn()
        c.execute("UPDATE tasks SET status = 'queued', now = 'Xod restarted; picking this back up' WHERE status = 'working'")
        c.commit()
        c.close()
        self._wake = asyncio.Event()
        self._worker = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                c = _conn()
                r = c.execute("SELECT id FROM tasks WHERE status = 'queued' ORDER BY updated_at ASC LIMIT 1").fetchone()
                c.close()
                if not r:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=30)
                    except asyncio.TimeoutError:
                        pass
                    continue
                try:
                    await self._run(r["id"])
                except Exception as e:
                    # Never leave a task stuck in "working": say what happened and move on.
                    logger.exception(f"Task {r['id']} failed: {e}")
                    self._running_id = None
                    self._update(r["id"], note=f"Error: {e}", status="failed", finished_at=time.time(),
                                 result=f"Something went wrong inside Xod: {e}", now="Stopped", next_action=None)
            except Exception as e:
                logger.exception(f"Task worker error: {e}")
                await asyncio.sleep(3)

    async def _ensure_tab(self, task: Dict[str, Any], resuming: bool) -> bool:
        """Puts the task's own tab in front. Returns False if a paused page was lost (e.g. after a restart)."""
        from app.services.browser_sandbox import browser_sandbox_service as sb
        await sb.ensure_browser_running()
        tab_id = task.get("tab_id")
        if tab_id:
            tabs = (await sb.manage_tabs("list")).get("tabs", [])
            if any(t.get("id") == tab_id for t in tabs):
                await sb.manage_tabs("switch", target_id=tab_id)
                return True
        res = await sb.manage_tabs("new", url="about:blank")
        self._update(task["id"], tab_id=(res.get("tab") or {}).get("id"))
        return not resuming

    async def _close_tab(self, tab_id: Optional[str]) -> None:
        if not tab_id:
            return
        try:
            from app.services.browser_sandbox import browser_sandbox_service as sb
            tabs = [t for t in (await sb.manage_tabs("list")).get("tabs", []) if t.get("type") == "page"]
            if len(tabs) > 1 and any(t.get("id") == tab_id for t in tabs):
                await sb.manage_tabs("close", target_id=tab_id)
        except Exception:
            pass

    async def _run(self, task_id: str) -> None:
        from app.agent.researcher import AgenticResearcher
        from app.services.browser_sandbox import browser_sandbox_service as sb

        task = self.get(task_id)
        action = task.get("next_action") or {"type": "start"}
        self._running_id = task_id
        self._update(task_id, status="working", now="Starting…" if action["type"] == "start" else task.get("now"))

        resuming = action["type"] in ("approve", "continue")
        page_ok = await self._ensure_tab(task, resuming)
        if resuming and not page_ok:
            # The paused page is gone (browser restarted). Start over; any submit still asks again.
            self._update(task_id, note="The paused page was lost, so I started this task over")
            action, resuming = {"type": "start"}, False

        # Restore this task's own mission state so "go ahead" / answers resume the right thing.
        if resuming and task.get("mission_json"):
            sb.latest_mission = task["mission_json"]
        sb.pending_submit = None
        if action["type"] == "approve" and task.get("pending_json"):
            sb.pending_submit = {**task["pending_json"], "created_at": time.time()}

        if action["type"] == "start":
            message = task["request"]
        elif action["type"] == "approve":
            message = "go ahead"
        else:
            message = f"{action.get('text', '')}\n(continue)"

        started = time.time()
        content, last_status, steps = [], "", task.get("steps") or 0
        try:
            async for ev in AgenticResearcher().run_research_stream([{"role": "user", "content": message}]):
                if ev.get("type") == "content":
                    content.append(ev.get("content", ""))
                elif ev.get("type") == "status":
                    line = str(ev.get("content", ""))[:200]
                    if line != last_status:
                        last_status = line
                        if line[:1] in "✓✗⏸•":
                            steps += 1
                        self._update(task_id, now=line, steps=steps)
                        if line.lstrip()[:1] in "".join(self._PROGRESS_MARKS) or line.startswith(self._PROGRESS_MARKS):
                            self._progress(task_id, line)
                if task_id in self._cancel_requested:
                    break
        except Exception as e:
            logger.exception(f"Task {task_id} crashed")
            self._finish(task_id, "failed", f"Something went wrong: {e}", note="Crashed")
            self._running_id = None
            return

        self._running_id = None
        if task_id in self._cancel_requested:
            self._cancel_requested.discard(task_id)
            self._update(task_id, note="Cancelled", status="cancelled", finished_at=time.time(), next_action=None, now="Cancelled")
            await self._close_tab(self.get(task_id).get("tab_id"))
            return

        report = "".join(content).strip()
        mission = sb.latest_mission if (sb.latest_mission or {}).get("started_at", 0) >= started - 1 else None
        if not mission:
            self._finish(task_id, "done", report or "Done.", note="Finished")
            await self._close_tab(self.get(task_id).get("tab_id"))
            return

        snapshot = {k: v for k, v in mission.items() if k not in ("latest_screenshot_uri",)}
        snapshot["steps"] = [{k: v for k, v in s.items() if k != "screenshot_uri"} for s in mission.get("steps", [])]
        self._save_screenshot(task_id, mission.get("latest_screenshot_uri"))
        status = mission.get("status")
        common = {"mission_json": snapshot, "result": report, "next_action": None}

        if status == "completed":
            self._finish(task_id, "done", report, note="Finished", **common)
            await self._close_tab(self.get(task_id).get("tab_id"))
        elif status == "needs_confirmation":
            pending = sb.pending_submit or {}
            sb.pending_submit = None  # each task keeps its own approval; never leave one lying around globally
            self._update(task_id, note=f"Waiting for your OK to press \"{pending.get('label', '…')}\"",
                         status="waiting", waiting_kind="approval", pending_json=pending,
                         waiting_detail={"label": pending.get("label"), "page": pending.get("title") or pending.get("url"),
                                         "message": mission.get("final_answer")},
                         now="Waiting for your OK", **common)
            notify(self.get(task_id)["title"], f"Ready to press \"{pending.get('label', 'Submit')}\". Needs your OK.")
        elif status in ("needs_input", "blocked"):
            kind = "question" if status == "needs_input" else "blocked"
            self._update(task_id, note="Waiting for you", status="waiting", waiting_kind=kind,
                         waiting_detail={"message": mission.get("final_answer")},
                         now="Needs your answer" if kind == "question" else "Stuck at a verification check", **common)
            notify(self.get(task_id)["title"], "Has a question for you." if kind == "question" else "Stuck at a verification check.")
        else:
            self._finish(task_id, "failed", report or mission.get("final_answer") or "It didn't finish.", note="Stopped", **common)

    def _finish(self, task_id: str, status: str, report: str, note: str, **fields: Any) -> None:
        fields.pop("result", None)  # the report argument wins
        self._update(task_id, note=note, status=status, result=report, finished_at=time.time(),
                     waiting_kind=None, now="Done" if status == "done" else "Stopped", **fields)
        t = self.get(task_id)
        notify(t["title"], "Done." if status == "done" else "Couldn't finish. Open Tasks to see why.")

    def _save_screenshot(self, task_id: str, uri: Optional[str]) -> None:
        if not uri or "," not in uri:
            return
        try:
            SHOTS_DIR.mkdir(parents=True, exist_ok=True)
            (SHOTS_DIR / f"{task_id}.jpg").write_bytes(base64.b64decode(uri.split(",", 1)[1]))
            self._update(task_id, has_screenshot=1)
        except Exception:
            pass


task_queue = TaskQueue()
