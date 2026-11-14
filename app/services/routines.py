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
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR
from app.timefmt import nopad

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
    # Orchestrator Phase 4 (services/delivery.py): what kind of job this is (a "watch" stays
    # silent when it finds nothing), which outcomes reach the person, and the open run record.
    cols = [r[1] for r in c.execute("PRAGMA table_info(routines)").fetchall()]
    if "kind" not in cols:
        c.execute("ALTER TABLE routines ADD COLUMN kind TEXT DEFAULT 'task'")
    if "notify_on" not in cols:
        c.execute("ALTER TABLE routines ADD COLUMN notify_on TEXT")
    if "last_run_id" not in cols:
        c.execute("ALTER TABLE routines ADD COLUMN last_run_id TEXT")
    return c


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["steps"] = json.loads(d.get("steps") or "[]")
    d["schedule"] = json.loads(d.get("schedule") or '{"kind": "manual"}')
    d["enabled"] = bool(d["enabled"])
    d["kind"] = d.get("kind") or "task"
    try:
        d["notify_on"] = json.loads(d["notify_on"]) if d.get("notify_on") else None
    except Exception:
        d["notify_on"] = None
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
    if kind == "once":
        try:
            at = dt.datetime.fromisoformat(str(schedule.get("at")))
        except ValueError:
            return None
        return at.timestamp() if at > now else None
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


def _after_run(schedule: Dict[str, Any], now: float) -> Optional[float]:
    """The next time after a run: none for a one-off (run early by hand, it was still its one run)."""
    return None if (schedule or {}).get("kind") == "once" else next_run(schedule, now)


def describe_schedule(s: Dict[str, Any]) -> str:
    kind = (s or {}).get("kind", "manual")
    if kind == "once":
        try:
            at = dt.datetime.fromisoformat(str(s.get("at")))
            return "Once, " + at.strftime(nopad("%b %-d, %Y at %-I:%M %p"))
        except ValueError:
            return "Once"
    if kind == "every":
        n = int(s.get("hours") or 24)
        return f"Every {n} hour{'s' if n != 1 else ''}"
    if kind in ("daily", "weekly"):
        h, m = _hm(s.get("time", "09:00"))
        t = dt.time(h, m).strftime(nopad("%-I:%M %p"))
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
    if kind == "once":
        try:
            return {"kind": "once", "at": dt.datetime.fromisoformat(str(s.get("at"))).strftime("%Y-%m-%dT%H:%M")}
        except ValueError:
            return {"kind": "manual"}
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


PRIOR_CONTENTS_MAX = 2000   # chars of the previous check's report carried into a watch task


def compose_request(r: Dict[str, Any]) -> str:
    text = r["instructions"].strip()
    if (r.get("kind") or "task") == "watch":
        from app.services import delivery
        text += delivery.WATCH_PROTOCOL
        # Grade fix, Tier 7 cluster 4 (live grade 2026-11-13, attempt 2): run-over-run state
        # reached the framework (result hashes) but never the watch task, so an instruction
        # asking the model to compare against "what you saw on the previous check" failed
        # every cycle, honestly, and cost the routine its enabled state. The task now gets
        # the previous completed check's reported contents, bounded, labelled with the hash
        # the framework already tracks for that result.
        try:
            prior = delivery.last_completed_result(r["id"])
        except Exception:
            prior = None
        if prior and (prior.get("excerpt") or "").strip():
            contents = prior["excerpt"].strip()
            if len(contents) > PRIOR_CONTENTS_MAX:
                contents = contents[:PRIOR_CONTENTS_MAX] + "\n[prior contents truncated]"
            text += (f"\n\n(What you found on the previous check of this watch "
                     f"(result hash {prior.get('result_hash') or 'unknown'}) follows. If your instructions "
                     f"ask you to compare against the previous check, compare against this:\n{contents}\n)")
    if r["steps"]:
        text += (f"\n\n(This is my saved routine \"{r['name']}\". Steps that worked last time, as a guide; "
                 "pages and files may have changed since, so check as you go:\n"
                 + "\n".join(f"{i}. {s}" for i, s in enumerate(r["steps"], 1)) + ")")
    return text


# ---------------------------------------------------------------- templates

# Ready-made routines anyone can start from. A template is a name, a description, the
# instructions, and a schedule; the steps are a first guide for the model and are
# refreshed from the first successful run, like any routine's steps.
TEMPLATES = [
    {
        "id": "morning-brief",
        "name": "Morning brief",
        "description": "Every weekday at 8: the five biggest tech stories with sources.",
        "instructions": ("Find the five biggest tech stories from the last 24 hours. For each one, "
                         "write a short paragraph on what happened and why it matters, with the source link. "
                         "Skip rumors and prefer primary sources."),
        "schedule": {"kind": "weekly", "time": "08:00", "days": ["mon", "tue", "wed", "thu", "fri"]},
        "steps": [
            "Searched for the day's top tech stories",
            "Opened each story and noted what happened, with its source link",
            "Wrote the five-story brief",
        ],
    },
]


def list_templates() -> List[Dict[str, Any]]:
    out = []
    for t in TEMPLATES:
        sched = _clean_schedule(t.get("schedule"))
        out.append({**t, "schedule": sched, "schedule_text": describe_schedule(sched)})
    return out


def create_from_template(template_id: str) -> Dict[str, Any]:
    t = next((x for x in TEMPLATES if x["id"] == template_id), None)
    if not t:
        raise ValueError("No such template.")
    r = create(t["name"], t["instructions"], t.get("schedule"))
    if t.get("steps"):
        r = update(r["id"], steps=t["steps"]) or r
    return r


# ---------------------------------------------------------------- CRUD

def list_all() -> List[Dict[str, Any]]:
    with _conn() as c:
        return [_row(r) for r in c.execute("SELECT * FROM routines ORDER BY created_at DESC")]


def get(rid: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        r = c.execute("SELECT * FROM routines WHERE id = ?", (rid,)).fetchone()
    return _row(r) if r else None


def create(name: str, instructions: str, schedule: Optional[Dict[str, Any]] = None,
           source: Optional[str] = None, kind: str = "task", notify_on: Optional[List[str]] = None) -> Dict[str, Any]:
    """source: "chat:<session id>" or "task:<task id>" to learn the steps from (optional)."""
    instructions = (instructions or "").strip()
    if not instructions:
        raise ValueError("A routine needs instructions.")
    sched = _clean_schedule(schedule)
    rid = f"rtn-{uuid.uuid4().hex[:8]}"
    now = time.time()
    steps = steps_from(source) if source else []
    with _conn() as c:
        c.execute("INSERT INTO routines (id, name, instructions, steps, schedule, enabled, created_at, updated_at, next_run_at, runs, source, kind, notify_on) "
                  "VALUES (?,?,?,?,?,1,?,?,?,0,?,?,?)",
                  (rid, (name or instructions[:60]).strip(), instructions, json.dumps(steps), json.dumps(sched), now, now,
                   next_run(sched, now), source, kind if kind in ("task", "watch") else "task",
                   json.dumps(notify_on) if notify_on else None))
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


# Runs of one routine serialize (grade fix, Tier 7 cluster 4): run_now's check-and-start and
# _follow_up's settle-and-start-next are one critical section, so two requests in the same
# second can never both start, and a queued run can never be started twice.
_RUN_LOCK = threading.RLock()


def _start_run(r: Dict[str, Any], queued_run_id: Optional[str] = None) -> Dict[str, Any]:
    """Starts a run of routine r now: an ordinary task plus its first-class run record.
    With queued_run_id, that already-recorded queued run is activated (its request was
    counted when it queued); otherwise a fresh run record is created."""
    from app.services.task_queue import task_queue
    from app.services import delivery
    task = task_queue.create(compose_request(r), title=f"↻ {r['name']}")
    if queued_run_id:
        delivery.run_activate(queued_run_id, task["id"])
        run_id = queued_run_id
    else:
        run_id = delivery.run_started(r["id"], task["id"])   # a first-class run record (services/delivery.py)
    now = time.time()
    with _conn() as c:
        c.execute("UPDATE routines SET last_run_at = ?, last_task_id = ?, last_status = 'running', next_run_at = ?, last_run_id = ? WHERE id = ?",
                  (now, task["id"], _after_run(r["schedule"], now), run_id, r["id"]))
    return {"success": True, "task": task, "run_id": run_id}


def run_now(rid: str, reason: str = "you asked") -> Dict[str, Any]:
    with _RUN_LOCK:
        return _run_now_locked(rid, reason)


def _run_now_locked(rid: str, reason: str = "you asked") -> Dict[str, Any]:
    r = get(rid)
    if not r:
        return {"success": False, "error": "No such routine."}
    if r.get("source") == "reminder":
        # A plain reminder: a notification here and on the phone, no agent run.
        from app.services.task_queue import notify
        notify("Reminder", r["instructions"][:200])
        try:
            from app.services import phone
            phone._fire(phone.send(f"⏰ {r['instructions'][:500]}"))
        except Exception:
            pass
        now = time.time()
        with _conn() as c:
            c.execute("UPDATE routines SET last_run_at = ?, last_status = 'done', runs = runs + 1, next_run_at = ? WHERE id = ?",
                      (now, _after_run(r["schedule"], now), rid))
        return {"success": True, "reminded": True}
    from app.services import delivery
    with _conn() as c:
        c.execute("UPDATE routines SET runs = runs + 1 WHERE id = ?", (rid,))
    if delivery.open_runs(rid):
        # A run of this routine is already in flight. Starting another on top of it is what
        # orphaned run records in the 2026-11-13 live grade (five same-second manual runs,
        # four records with no outcome ever): the request is recorded as queued instead, and
        # _follow_up starts it when the in-flight run settles.
        run_id = delivery.run_queued(rid)
        logger.info(f"Routine {r['name']} run queued behind an in-flight run ({reason}) as {run_id}")
        return {"success": True, "queued": True, "run_id": run_id, "task": None}
    out = _start_run(r)
    logger.info(f"Routine {r['name']} started ({reason}) as {out['task']['id']}")
    return out


# ---------------------------------------------------------------- scheduler

def _follow_up() -> None:
    """When a routine's task finishes: record how it went; on success, learn its steps.

    Settles EVERY open run record of the routine, not only the one routines.last_run_id
    points at: a record left open by an older build (or by runs started on top of each
    other) is finished from its own task's real end state, never left orphaned. Then the
    oldest queued run (run_now, while one was in flight) is started, one at a time; if the
    routine has been turned off in the meantime, its queued runs are superseded instead."""
    from app.services.task_queue import task_queue
    from app.services import delivery
    with _RUN_LOCK:
        for r in list_all():
            open_runs = delivery.open_runs(r["id"])
            if not open_runs and not delivery.queued_runs(r["id"]):
                continue
            still_open = False
            settled_status: Optional[str] = None
            fresh_steps: List[str] = []
            for run in open_runs:
                t = task_queue.get(run["task_id"])
                status = (t or {}).get("status")
                if status == "waiting":
                    # It needs the person: that decision reaches them once per waiting episode, while the
                    # run itself stays open until its task really ends.
                    delivery.note_decision(r, t)
                    still_open = True
                    continue
                if status in ("done", "failed", "cancelled") or not t:
                    delivery.run_finished(run["id"], r, t)
                    settled_status = status or "gone"
                    if status == "done":
                        fresh = steps_from(f"task:{run['task_id']}")
                        if fresh:
                            fresh_steps = fresh
                    if delivery.consecutive_failures(r["id"]) >= delivery.MAX_CONSECUTIVE_FAILURES and r["enabled"]:
                        # A broken job is stopped, and the person told which result will no longer arrive.
                        update(r["id"], enabled=False)
                        r = {**r, "enabled": False}
                        delivery.run_finished(delivery.run_started(r["id"], None), {**r, "notify_on": ["failed"]},
                                              {"status": "failed", "result": (
                                                  f"It failed {delivery.MAX_CONSECUTIVE_FAILURES} runs in a row, so Xod turned it off. "
                                                  f"You will not get \"{r['name']}\" results until it is fixed and turned back on.")})
                else:
                    still_open = True
            if fresh_steps:
                update(r["id"], steps=fresh_steps)
            if settled_status is not None and not still_open:
                with _conn() as c:
                    c.execute("UPDATE routines SET last_status = ? WHERE id = ?", (settled_status, r["id"]))
            queued = delivery.queued_runs(r["id"])
            if queued and not still_open:
                current = get(r["id"]) or r
                if current.get("enabled"):
                    # The queued run's request is composed now, at its real start, so it sees
                    # the state (and the previous check's contents) as of this run, not as of
                    # when it was requested.
                    _start_run(current, queued_run_id=queued[0]["id"])
                    logger.info(f"Routine {current['name']}: queued run {queued[0]['id']} started")
                    queued = queued[1:]
                if queued and not current.get("enabled"):
                    for qrun in queued:
                        delivery.run_superseded(qrun["id"], "the routine was turned off before this queued run could start")


def tick() -> List[str]:
    started = []
    _follow_up()
    try:
        from app.services import delivery
        delivery.deliver_pending()        # results that reach the person, per each routine's policy, exactly once
    except Exception as e:
        logger.warning(f"delivery: {e}")
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
    try:   # a delivery a previous process left mid-send is settled at the destination first
        from app.services import delivery
        delivery.recover()
    except Exception as e:
        logger.warning(f"delivery recovery: {e}")

    async def loop():
        while True:
            try:
                await asyncio.to_thread(tick)
            except Exception as e:
                logger.warning(f"routine scheduler: {e}")
            try:      # goals' check-ins and the morning brief (services/goals.py) run on the same clock
                from app.services import goals
                await asyncio.to_thread(goals.tick)
            except Exception as e:
                logger.warning(f"goals: {e}")
            await asyncio.sleep(TICK_SEC)
    _loop_task = asyncio.get_event_loop().create_task(loop())
