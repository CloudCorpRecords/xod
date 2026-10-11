"""
Routines: teach Xod a job once, then have it repeat it, by hand or on a schedule.

A routine is the original request plus "what worked last time": the successful steps from the
activity log of the chat or task it was made from (pages opened, files written, commands run).
Each run becomes an ordinary task (task_queue), so it gets progress notes, approval gates and a
notification. The steps are a guide for the model, not a blind replay, because websites and
folders change. After a successful run the steps are refreshed from that run, so a routine
keeps up with small changes on its own.

Schedules: manual, daily at HH:MM, weekly on given days at HH:MM, or every N hours.
A run missed while the Mac was asleep or Xod was closed happens once when Xod is back.
"""
import asyncio
import datetime as dt
import json
import logging
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.routines")
DB = DATA_DIR / "xod.db"
TICK_SEC = 30
MAX_STEPS = 25
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS routines (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, instructions TEXT NOT NULL, steps TEXT, schedule TEXT,
        enabled INTEGER DEFAULT 1, created_at REAL, updated_at REAL, next_run_at REAL, last_run_at REAL,
        last_task_id TEXT, last_status TEXT, runs INTEGER DEFAULT 0, source TEXT)""")
    return c


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["steps"] = json.loads(d.get("steps") or "[]")
    d["schedule"] = json.loads(d.get("schedule") or '{"kind": "manual"}')
    d["enabled"] = bool(d["enabled"])
    d["schedule_text"] = describe_schedule(d["schedule"])
    return d


# ---------------------------------------------------------------- schedules

def _hm(s: str) -> tuple:
    h, m = (s or "09:00").split(":")[:2]
    return max(0, min(23, int(h))), max(0, min(59, int(m)))


def next_run(schedule: Dict[str, Any], after: Optional[float] = None) -> Optional[float]:
    """Next time (epoch seconds, local time) the schedule fires after `after`; None for manual."""
    kind = (schedule or {}).get("kind", "manual")
    now = dt.datetime.fromtimestamp(after or time.time())
    if kind == "every":
        hours = max(1, int(schedule.get("hours") or 24))
        return (now + dt.timedelta(hours=hours)).timestamp()
    if kind not in ("daily", "weekly"):
        return None
    h, m = _hm(schedule.get("time", "09:00"))
    days = {DAYS.index(d) for d in schedule.get("days", DAYS) if d in DAYS} if kind == "weekly" else set(range(7))
    days = days or set(range(7))
    for add in range(0, 8):
        cand = (now + dt.timedelta(days=add)).replace(hour=h, minute=m, second=0, microsecond=0)
        if cand > now and cand.weekday() in days:
            return cand.timestamp()
    return None


def describe_schedule(s: Dict[str, Any]) -> str:
    kind = (s or {}).get("kind", "manual")
    if kind == "every":
        n = int(s.get("hours") or 24)
        return f"Every {n} hour{'s' if n != 1 else ''}"
    if kind in ("daily", "weekly"):
        h, m = _hm(s.get("time", "09:00"))
        t = dt.time(h, m).strftime("%-I:%M %p")
        if kind == "daily":
            return f"Every day at {t}"
        days = [d for d in DAYS if d in s.get("days", [])]
        if days == DAYS[:5]:
            return f"Weekdays at {t}"
        return f"{', '.join(d.title() for d in days) or 'Never'} at {t}"
    return "When you run it"


def _clean_schedule(s: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    s = s or {"kind": "manual"}
    kind = s.get("kind", "manual")
    if kind == "every":
        return {"kind": "every", "hours": max(1, min(168, int(s.get("hours") or 24)))}
    if kind == "daily":
        return {"kind": "daily", "time": "%02d:%02d" % _hm(s.get("time"))}
    if kind == "weekly":
        return {"kind": "weekly", "time": "%02d:%02d" % _hm(s.get("time")), "days": [d for d in DAYS if d in (s.get("days") or [])] or ["mon"]}
    return {"kind": "manual"}


# ---------------------------------------------------------------- the steps that worked

def steps_from(origin: str) -> List[str]:
    """Successful actions from the activity log for a chat ("chat:<id>") or task ("task:<id>")."""
    from app.services import activity
    with activity._conn() as c:
        rows = c.execute("SELECT title, detail FROM activity WHERE origin = ? AND ok = 1 ORDER BY ts", (origin,)).fetchall()
    steps = []
    for r in rows:
        line = r["title"] + (f" ({r['detail'][:140]})" if r["detail"] and r["detail"] not in r["title"] else "")
        if not steps or steps[-1] != line:
            steps.append(line)
    return steps[:MAX_STEPS]


def compose_request(r: Dict[str, Any]) -> str:
    text = r["instructions"].strip()
    if r["steps"]:
        text += (f"\n\n(This is my saved routine \"{r['name']}\". Steps that worked last time, as a guide; "
                 "pages and files may have changed since, so check as you go:\n"
                 + "\n".join(f"{i}. {s}" for i, s in enumerate(r["steps"], 1)) + ")")
    return text


# ---------------------------------------------------------------- CRUD

def list_all() -> List[Dict[str, Any]]:
    with _conn() as c:
        return [_row(r) for r in c.execute("SELECT * FROM routines ORDER BY created_at DESC")]


def get(rid: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        r = c.execute("SELECT * FROM routines WHERE id = ?", (rid,)).fetchone()
    return _row(r) if r else None


def create(name: str, instructions: str, schedule: Optional[Dict[str, Any]] = None,
           source: Optional[str] = None) -> Dict[str, Any]:
    """source: "chat:<session id>" or "task:<task id>" to learn the steps from (optional)."""
    instructions = (instructions or "").strip()
    if not instructions:
        raise ValueError("A routine needs instructions.")
    sched = _clean_schedule(schedule)
    rid = f"rtn-{uuid.uuid4().hex[:8]}"
    now = time.time()
    steps = steps_from(source) if source else []
    with _conn() as c:
        c.execute("INSERT INTO routines (id, name, instructions, steps, schedule, enabled, created_at, updated_at, next_run_at, runs, source) "
                  "VALUES (?,?,?,?,?,1,?,?,?,0,?)",
                  (rid, (name or instructions[:60]).strip(), instructions, json.dumps(steps), json.dumps(sched), now, now,
                   next_run(sched, now), source))
    return get(rid)


def update(rid: str, **fields: Any) -> Optional[Dict[str, Any]]:
    r = get(rid)
    if not r:
        return None
    sets, vals = [], []
    for k in ("name", "instructions"):
        if fields.get(k) is not None:
            sets.append(f"{k} = ?"); vals.append(str(fields[k]).strip())
    if fields.get("steps") is not None:
        sets.append("steps = ?"); vals.append(json.dumps([str(s) for s in fields["steps"]][:MAX_STEPS]))
    if fields.get("enabled") is not None:
        sets.append("enabled = ?"); vals.append(int(bool(fields["enabled"])))
    if fields.get("schedule") is not None:
        sched = _clean_schedule(fields["schedule"])
        sets += ["schedule = ?", "next_run_at = ?"]; vals += [json.dumps(sched), next_run(sched)]
    if not sets:
        return r
    sets.append("updated_at = ?"); vals.append(time.time())
    with _conn() as c:
        c.execute(f"UPDATE routines SET {', '.join(sets)} WHERE id = ?", (*vals, rid))
    return get(rid)


def delete(rid: str) -> bool:
    with _conn() as c:
        return c.execute("DELETE FROM routines WHERE id = ?", (rid,)).rowcount > 0


def run_now(rid: str, reason: str = "you asked") -> Dict[str, Any]:
    from app.services.task_queue import task_queue
    r = get(rid)
    if not r:
        return {"success": False, "error": "No such routine."}
    task = task_queue.create(compose_request(r), title=f"↻ {r['name']}")
    now = time.time()
    with _conn() as c:
        c.execute("UPDATE routines SET last_run_at = ?, last_task_id = ?, last_status = 'running', runs = runs + 1, next_run_at = ? WHERE id = ?",
                  (now, task["id"], next_run(r["schedule"], now), rid))
    logger.info(f"Routine {r['name']} started ({reason}) as {task['id']}")
    return {"success": True, "task": task}


# ---------------------------------------------------------------- scheduler

def _follow_up() -> None:
    """When a routine's task finishes: record how it went; on success, learn its steps."""
    from app.services.task_queue import task_queue
    for r in list_all():
        if r.get("last_status") != "running" or not r.get("last_task_id"):
            continue
        t = task_queue.get(r["last_task_id"])
        status = (t or {}).get("status")
        if status in ("done", "failed", "cancelled") or not t:
            fields: Dict[str, Any] = {}
            if status == "done":
                fresh = steps_from(f"task:{r['last_task_id']}")
                if fresh:
                    fields["steps"] = fresh
            if fields:
                update(r["id"], **fields)
            with _conn() as c:
                c.execute("UPDATE routines SET last_status = ? WHERE id = ?", (status or "gone", r["id"]))


def tick() -> List[str]:
    started = []
    _follow_up()
    now = time.time()
    for r in list_all():
        if r["enabled"] and r.get("next_run_at") and r["next_run_at"] <= now and r.get("last_status") != "running":
            late = now - r["next_run_at"] > 3 * TICK_SEC
            run_now(r["id"], reason="catching up on a missed run" if late else "schedule")
            started.append(r["id"])
    return started


_loop_task: Optional[asyncio.Task] = None


def start() -> None:
    global _loop_task
    if _loop_task and not _loop_task.done():
        return

    async def loop():
        while True:
            try:
                await asyncio.to_thread(tick)
            except Exception as e:
                logger.warning(f"routine scheduler: {e}")
            await asyncio.sleep(TICK_SEC)
    _loop_task = asyncio.get_event_loop().create_task(loop())
