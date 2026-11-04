"""
The orchestrator layer, Phase 1 (docs: ORCHESTRATOR-SCOPE-FOR-CLAUDE-2026-11-04, sections 1, 2, 3, 6).

What it adds around the existing TaskQueue, without replacing it:

  - Acknowledge first: submit() returns an ack (task id plus a one-line restatement of the
    scope) the moment the task row exists, before any planning or tool call. The ack is its
    own event (task.ack) and its own history entry, distinct from progress and completion.

  - Heavy work off the chat turn: an orchestrated task ("stepwise" mode) runs in the task
    queue's background worker, never inside a chat reply. The chat gets the ack and returns.

  - A durable plan per task: tasks.plan_json is a todo state machine (pending, in_progress,
    blocked, completed, cancelled), versioned by tasks.plan_version. At most one item is
    in_progress. A step is completed only when its outcome is established (the queue's own
    file-effect check finds nothing wrong), not when it was attempted. Edits keep history:
    a redirect cancels the superseded steps (they stay visible) and appends new ones.

  - The steer inbox: steer_inbox rows (pause, resume, steer, redirect, cancel, note) are
    read by the execution loop between steps, and during a step when the steer is urgent
    (cancel, or now=true). A steer is "queued" until the loop applies it; only then is it
    "applied", with a note saying what it changed. Queued is never reported as applied.

State lives in xod.db, so a backend restart loses nothing: the plan shows the step that was
in flight (still in_progress, marked interrupted), and the resumed run is told which actions
that step already took, so completed work is never repeated.
"""
import asyncio
import json
import logging
import sqlite3
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("xod.orchestrator")

PLAN_STATES = ("pending", "in_progress", "blocked", "completed", "cancelled")
STEER_KINDS = ("pause", "resume", "steer", "redirect", "cancel", "note")
# Kinds that cut into a running step without waiting for its end.
URGENT_KINDS = ("cancel",)
STEER_POLL_SEC = 0.5
MAX_STEPS = 8
MAX_PARALLEL = 8


# --------------------------------------------------------------------- storage
def _conn() -> sqlite3.Connection:
    from app.services import task_queue as TQ
    c = TQ._conn()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS steer_inbox (
            id TEXT PRIMARY KEY,
            target_type TEXT NOT NULL,     -- task | worker
            target_id TEXT NOT NULL,
            kind TEXT NOT NULL,            -- pause | resume | steer | redirect | cancel | note
            body TEXT DEFAULT '',
            source TEXT NOT NULL,          -- user | orchestrator | worker | external
            urgent INTEGER DEFAULT 0,      -- 1: interrupt the running step instead of waiting for its end
            state TEXT NOT NULL,           -- queued | applied | rejected
            created_at REAL NOT NULL,
            applied_at REAL,
            applied_note TEXT
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS steer_inbox_target ON steer_inbox(target_id, state)")
    return c


def _emit(event: str, task_id: str, **payload: Any) -> None:
    try:
        from app.services import event_log
        event_log.emit(event, actor="orchestrator", ids={"task_id": task_id}, payload=payload)
    except Exception:
        pass


def load_plan(task_id: str) -> Tuple[List[Dict[str, Any]], int, List[str]]:
    """(items, version, standing instructions) for a task; empty when it has no plan yet."""
    c = _conn()
    r = c.execute("SELECT plan_json, plan_version FROM tasks WHERE id = ?", (task_id,)).fetchone()
    c.close()
    if not r or not r["plan_json"]:
        return [], int(r["plan_version"] or 0) if r else 0, []
    try:
        doc = json.loads(r["plan_json"])
    except Exception:
        return [], int(r["plan_version"] or 0), []
    return list(doc.get("items") or []), int(r["plan_version"] or 0), list(doc.get("instructions") or [])


def save_plan(task_id: str, items: List[Dict[str, Any]], instructions: List[str],
              expect_version: Optional[int] = None, bump: bool = False, why: str = "") -> int:
    """Writes the plan. With bump, the version rises (an edit: redirect, steer, first plan);
    expect_version makes the write conditional, so an edit lands on the version it was
    computed from or not at all. Returns the version now stored."""
    assert sum(1 for i in items if i.get("status") == "in_progress") <= 1, "one in_progress item at most"
    for i in items:
        assert i.get("status") in PLAN_STATES, f"bad plan state {i.get('status')}"
    doc = json.dumps({"items": items, "instructions": instructions})
    c = _conn()
    r = c.execute("SELECT plan_version FROM tasks WHERE id = ?", (task_id,)).fetchone()
    cur = int((r["plan_version"] if r else 0) or 0)
    if expect_version is not None and cur != expect_version:
        c.close()
        raise RuntimeError(f"plan version moved from {expect_version} to {cur}")
    new = cur + 1 if bump else cur
    c.execute("UPDATE tasks SET plan_json = ?, plan_version = ?, updated_at = ? WHERE id = ?",
              (doc, new, time.time(), task_id))
    c.commit()
    c.close()
    if bump:
        _emit("plan.updated", task_id, version=new, why=why[:200],
              items=[{"text": i["text"][:80], "status": i["status"]} for i in items])
    return new


def _item(text: Any, origin: str = "plan") -> Dict[str, Any]:
    parallel = None
    if isinstance(text, dict):
        parallel, text = text.get("parallel"), text.get("text", "")
    return {"id": f"s-{uuid.uuid4().hex[:6]}", "text": str(text).strip(), "status": "pending", "parallel": parallel,
            "blocker": None, "result": None, "evidence": [], "origin": origin,
            "attempts": 0, "started_at": None, "finished_at": None, "note": None}


def public_plan(task_id: str) -> Dict[str, Any]:
    items, version, instructions = load_plan(task_id)
    return {"task_id": task_id, "version": version, "items": items, "instructions": instructions,
            "inbox": inbox(task_id)}


def inbox(task_id: str, state: Optional[str] = None) -> List[Dict[str, Any]]:
    c = _conn()
    q = "SELECT * FROM steer_inbox WHERE target_id = ?"
    args: List[Any] = [task_id]
    if state:
        q += " AND state = ?"
        args.append(state)
    rows = c.execute(q + " ORDER BY created_at ASC", args).fetchall()
    c.close()
    return [{**dict(r), "urgent": bool(r["urgent"])} for r in rows]


def _mark_steer(steer_id: str, state: str, note: str) -> None:
    c = _conn()
    c.execute("UPDATE steer_inbox SET state = ?, applied_at = ?, applied_note = ? WHERE id = ? AND state = 'queued'",
              (state, time.time(), note[:500], steer_id))
    c.commit()
    c.close()


# --------------------------------------------------------------------- planning (model calls, injectable)
PLAN_PROMPT = """You are planning a task for a personal AI agent that will carry it out step by step.

Task from the person:
{request}

Break it into 2 to {max_steps} concrete steps, in order. Each step must have an outcome that can be
checked (a file exists with given content, a value was found, a message was drafted). Keep every exact
detail from the task in the step that needs it: full file paths, names, numbers, quoted text. Do not
add steps the person did not ask for. A task that is genuinely one action is one step.

When the task asks for several INDEPENDENT look-ups or research jobs (reading only: no writing, sending or
changing anything), put them in ONE step that runs them in parallel:
  {{"text": "short name of the combined job", "parallel": ["job one, fully self-contained", "job two, ..."]}}
Each parallel job must carry every detail it needs; it will not see the task or the other jobs.

Answer with JSON only: {{"steps": ["step one", {{"text": "...", "parallel": ["...", "..."]}}, "step three", ...]}}"""

REPLAN_PROMPT = """You are re-planning a task for a personal AI agent after the person changed direction mid-task.

Original task:
{request}

Steps already completed (their effects exist and must NOT be redone):
{done}

Steps that were still to do before the change:
{remaining}

The person's change, which wins over the original wherever they conflict:
{instruction}

Write the steps that remain, in order, so the task finishes against the person's changed target.
Keep every exact detail (full paths, names, quoted text). Never include a completed step again.
1 to {max_steps} steps.

Answer with JSON only: {{"steps": ["...", "..."]}}"""


async def _ask_model(prompt: str) -> str:
    from app.services.llm import llm_service, JSON_MIN_TOKENS
    reply = await llm_service.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1, max_tokens=max(JSON_MIN_TOKENS, 1200), think=False, task="mission_plan")
    return reply.get("content", "") or ""


def _parse_steps(raw: str) -> List[Any]:
    """Steps as text, or {"text", "parallel": [jobs]} for a fan-out of independent read-only jobs."""
    from app.services.llm import parse_llm_json
    data = parse_llm_json(raw)
    steps = data.get("steps") if isinstance(data, dict) else data if isinstance(data, list) else None
    out: List[Any] = []
    for s in steps or []:
        if isinstance(s, dict) and isinstance(s.get("parallel"), list):
            jobs = [str(j).strip() for j in s["parallel"] if str(j).strip()][:MAX_PARALLEL]
            if len(jobs) >= 2:
                out.append({"text": str(s.get("text") or "; ".join(jobs))[:300], "parallel": jobs})
            elif jobs:
                out.append(jobs[0])
        elif isinstance(s, dict) and s.get("text"):
            out.append(str(s["text"]).strip())
        elif str(s).strip():
            out.append(str(s).strip())
    return out[:MAX_STEPS]


async def default_planner(request: str) -> List[str]:
    return _parse_steps(await _ask_model(PLAN_PROMPT.format(request=request, max_steps=MAX_STEPS)))


async def default_replanner(request: str, done: List[Dict[str, Any]], remaining: List[Dict[str, Any]],
                            instruction: str) -> List[str]:
    fmt = lambda xs: "\n".join(f"- {x['text']}" + (f" (result: {str(x.get('result') or '')[:200]})" if x.get("result") else "")
                               for x in xs) or "- (none)"
    return _parse_steps(await _ask_model(REPLAN_PROMPT.format(
        request=request, done=fmt(done), remaining=fmt(remaining), instruction=instruction, max_steps=MAX_STEPS)))


# Tests swap these for scripted fakes.
planner: Callable[[str], Awaitable[List[str]]] = default_planner
replanner: Callable[..., Awaitable[List[str]]] = default_replanner


# --------------------------------------------------------------------- user-facing actions
def restate(request: str) -> str:
    """The ack's one-line restatement of scope: the request itself, trimmed, never paraphrased
    by a model (an ack must be instant and must not invent scope)."""
    one = " ".join((request or "").split())
    return one if len(one) <= 160 else one[:157].rsplit(" ", 1)[0] + "…"


def submit(request: str, title: Optional[str] = None, source: str = "user") -> Dict[str, Any]:
    """Creates an orchestrated task and returns its ack immediately. Nothing has run yet:
    planning and every step happen afterwards in the background worker."""
    from app.services.task_queue import task_queue
    if source != "user":
        # Only user-originated direction may create goals (spec section 10).
        raise PermissionError("Only the person can start a task.")
    t = task_queue.create(request, title=title, mode="stepwise")
    ack = {"type": "ack", "task_id": t["id"], "scope": restate(request), "status": t["status"],
           "acked_at": time.time(),
           "text": f"Got it. Task {t['id']}: {restate(request)}. I'm on it in the background; "
                   f"you can keep talking to me, and pause, redirect or cancel it any time."}
    _emit("task.ack", t["id"], scope=ack["scope"])
    return ack


def steer(task_id: str, kind: str, body: str = "", source: str = "user", now: bool = False) -> Dict[str, Any]:
    """Puts an instruction in a task's inbox. Returns the queued steer and when it will apply.
    It is not applied until the execution loop says so (or, for a task with no live run,
    until this call applies it directly and says so)."""
    from app.services.task_queue import task_queue
    kind = (kind or "").lower().strip()
    if kind not in STEER_KINDS:
        return {"success": False, "error": f"Unknown steer kind '{kind}'. Use one of: {', '.join(STEER_KINDS)}."}
    if kind in ("steer", "redirect") and not (body or "").strip():
        return {"success": False, "error": f"A {kind} needs the new instruction."}
    if source != "user" and kind in ("steer", "redirect"):
        # Direction only from the person: content and workers may add notes, never goals.
        return {"success": False, "error": "Only the person can change a task's direction."}
    t = task_queue.get(task_id)
    if not t:
        return {"success": False, "error": "No such task."}
    if t["status"] in ("done", "failed", "cancelled"):
        return {"success": False, "error": f"The task is already {t['status']}; nothing to steer."}
    sid = f"steer-{uuid.uuid4().hex[:8]}"
    urgent = 1 if (kind in URGENT_KINDS or now) else 0
    c = _conn()
    c.execute("INSERT INTO steer_inbox (id, target_type, target_id, kind, body, source, urgent, state, created_at) "
              "VALUES (?,?,?,?,?,?,?,?,?)", (sid, "task", task_id, kind, (body or "").strip(), source, urgent,
                                             "queued", time.time()))
    c.commit()
    c.close()
    _emit("steer.received", task_id, steer_id=sid, kind=kind, body=(body or "")[:200], source=source, urgent=bool(urgent))
    task_queue._update(task_id, note=f"Steer received ({kind}){': ' + body[:120] if body else ''}; queued")
    out = {"success": True, "steer_id": sid, "kind": kind, "state": "queued"}
    # No run is live for this task (queued, waiting, paused): there is no loop to apply it,
    # so the parts that need no model are applied here, honestly, right now.
    if t["status"] != "working":
        applied = _apply_idle(task_id, t, sid, kind, body)
        out.update(applied)
    else:
        out["applies"] = ("now: the current step is being interrupted" if urgent
                          else "when the current step finishes")
    return out


def _apply_idle(task_id: str, t: Dict[str, Any], sid: str, kind: str, body: str) -> Dict[str, Any]:
    """A steer for a task with no live run."""
    from app.services.task_queue import task_queue
    status = t["status"]
    if kind == "pause":
        if status == "paused":
            _mark_steer(sid, "applied", "already paused")
            return {"state": "applied", "note": "It was already paused."}
        task_queue._update(task_id, status="paused", now="Paused by you; nothing is running",
                           note="Paused by you")
        _mark_steer(sid, "applied", "paused before its next step")
        _emit("steer.applied", task_id, steer_id=sid, kind=kind, note="paused")
        return {"state": "applied", "note": "Paused. Nothing is running for it."}
    if kind == "resume":
        if status != "paused":
            _mark_steer(sid, "applied", f"was not paused (it is {status})")
            return {"state": "applied", "note": f"It wasn't paused; it is {status}."}
        task_queue._update(task_id, status="queued", now="Resuming", note="Resumed by you",
                           next_action={"type": "continue", "text": "resume"})
        _mark_steer(sid, "applied", "resumed")
        _emit("steer.applied", task_id, steer_id=sid, kind=kind, note="resumed")
        task_queue._kick()
        return {"state": "applied", "note": "Resumed; it continues from its next unfinished step."}
    if kind == "cancel":
        report = _cancel_plan(task_id, "you cancelled it")
        task_queue._update(task_id, status="cancelled", finished_at=time.time(), next_action=None,
                           waiting_kind=None, now="Cancelled", note="Cancelled by you", result=report)
        _mark_steer(sid, "applied", report)
        _emit("steer.applied", task_id, steer_id=sid, kind=kind, note=report[:200])
        return {"state": "applied", "note": report}
    if kind in ("steer", "redirect") and status == "paused":
        # A change of direction for a paused task: it stays queued in the inbox and the task
        # resumes so its loop applies it (re-planning needs the model, which runs in the worker).
        task_queue._update(task_id, status="queued", now="Resuming to apply your change",
                           note="Resumed to apply your change",
                           next_action={"type": "continue", "text": "resume"})
        task_queue._kick()
        return {"state": "queued", "applies": "as soon as it resumes (resuming now)"}
    if kind == "note":
        _mark_steer(sid, "applied", "recorded")
        return {"state": "applied", "note": "Noted on the task."}
    return {"state": "queued", "applies": "when its run starts"}


def _cancel_plan(task_id: str, why: str) -> str:
    """Cancels every unfinished plan item and says exactly what was stopped."""
    items, version, instr = load_plan(task_id)
    done = [i for i in items if i["status"] == "completed"]
    stopped = []
    for i in items:
        if i["status"] in ("pending", "in_progress", "blocked"):
            stopped.append((i["text"], i["status"]))
            i["status"] = "cancelled"
            i["note"] = f"cancelled: {why}"
            i["finished_at"] = time.time()
    if items:
        save_plan(task_id, items, instr, bump=True, why=f"cancel ({why})")
    lines = [f"Cancelled ({why})."]
    if stopped:
        lines.append("Stopped: " + "; ".join(f"{txt} ({'was running' if st == 'in_progress' else st})" for txt, st in stopped) + ".")
    else:
        lines.append("No plan steps were left to stop.")
    if done:
        lines.append("Already done and kept: " + "; ".join(i["text"] for i in done) + ".")
    return " ".join(lines)


# --------------------------------------------------------------------- the execution loop
def _ops_summary(task_id: str, since: float) -> List[str]:
    """Side-effecting actions this task's tools recorded since a time: what a resumed or
    later step must not repeat."""
    try:
        from app.services import activity
        entries = activity.entries_for_origin(f"task:{task_id}", since=since - 0.5)
    except Exception:
        return []
    out = []
    for e in entries:
        if not e.get("ok"):
            continue
        label = (e.get("title") or e.get("tool") or "").strip()
        detail = (e.get("detail") or "").strip()
        out.append((label + (f": {detail}" if detail else ""))[:240])
    return out


def _step_prompt(task: Dict[str, Any], items: List[Dict[str, Any]], instructions: List[str],
                 step: Dict[str, Any], prior_ops: List[str]) -> str:
    live = [i for i in items if i["status"] != "cancelled"]
    k = next((n for n, i in enumerate(live, 1) if i["id"] == step["id"]), 0)
    done = [i for i in items if i["status"] == "completed"]
    parts = ["You are carrying out ONE step of a larger task for the person.",
             f"\nThe overall task:\n{task['request']}"]
    if instructions:
        parts.append("\nChanges the person made since the task started (newest last; they win over the "
                     "original wording wherever they conflict):\n" + "\n".join(f"- {x}" for x in instructions))
    if done:
        parts.append("\nSteps already completed. Their effects already exist; do NOT redo any of them:\n"
                     + "\n".join(f"- {i['text']}" + (f" -> {str(i.get('result') or '')[:300]}" if i.get("result") else "")
                                 for i in done))
    if prior_ops:
        parts.append("\nThis step was started before and interrupted (by a restart or by the person). These actions "
                     "from it were already taken:\n" + "\n".join(f"- {o}" for o in prior_ops)
                     + "\nFirst check whether their effects are in place. Do not redo an action whose effect is "
                     "already there. Redo only what is missing: a background job or command started before a "
                     "restart may have been stopped by it, so its effect may be missing.")
    if str(step.get("note") or "").startswith("first attempt did not check out"):
        parts.append("\nYou already attempted this step once and checking found: "
                     + step["note"].split(": ", 1)[1] + ". Do it now for real, using your tools.")
    parts.append(f"\nYOUR STEP NOW (step {k} of {len(live)}):\n{step['text']}")
    parts.append("\nDo only this step, using your tools. Then reply with a short report of what you did "
                 "and what you found. If you cannot do it, say exactly what blocks it.")
    return "\n".join(parts)


async def _apply_inbox(q: Any, task_id: str, task: Dict[str, Any]) -> Optional[str]:
    """Applies every queued steer, oldest first. Returns 'paused' or 'cancelled' when the run
    must stop here, else None."""
    for s in inbox(task_id, state="queued"):
        kind, body, sid = s["kind"], s["body"] or "", s["id"]
        items, version, instr = load_plan(task_id)
        if kind == "cancel":
            report = _cancel_plan(task_id, "you cancelled it")
            _mark_steer(sid, "applied", report)
            _emit("steer.applied", task_id, steer_id=sid, kind=kind, note=report[:200])
            q._update(task_id, status="cancelled", finished_at=time.time(), next_action=None, waiting_kind=None,
                      now="Cancelled", note="Cancelled by you", result=report)
            return "cancelled"
        if kind == "pause":
            nxt = next((i for i in items if i["status"] in ("in_progress", "pending")), None)
            note = "paused before: " + (nxt["text"] if nxt else "(nothing left)")
            _mark_steer(sid, "applied", note)
            _emit("steer.applied", task_id, steer_id=sid, kind=kind, note=note)
            q._update(task_id, status="paused", now="Paused by you; nothing is running", note=f"Paused ({note})")
            return "paused"
        if kind == "resume":
            _mark_steer(sid, "applied", "it was already running")
            continue
        if kind == "note":
            _mark_steer(sid, "applied", "recorded")
            continue
        if kind == "steer":
            instr = instr + [body]
            save_plan(task_id, items, instr, expect_version=version, bump=True, why=f"steer: {body}")
            note = f"added to the instructions every remaining step follows (plan v{version + 1})"
            _mark_steer(sid, "applied", note)
            _emit("steer.applied", task_id, steer_id=sid, kind=kind, note=note)
            q._progress(task_id, f"↪ Your instruction applied: {body[:150]}")
            continue
        if kind == "redirect":
            done = [i for i in items if i["status"] == "completed"]
            remaining = [i for i in items if i["status"] in ("pending", "in_progress", "blocked")]
            try:
                new_steps = await replanner(task["request"], done, remaining, body)
            except Exception as e:
                logger.warning(f"replan failed for {task_id}: {e}")
                new_steps = []
            if not new_steps:
                new_steps = [f"Finish the task with this change from the person applied: {body}"]
            for i in remaining:
                i["status"] = "cancelled"
                i["note"] = f"superseded by your redirect (plan v{version + 1})"
                i["finished_at"] = time.time()
            items = items + [_item(t, origin="redirect") for t in new_steps]
            instr = instr + [body]
            save_plan(task_id, items, instr, expect_version=version, bump=True, why=f"redirect: {body}")
            note = (f"kept {len(done)} completed step(s); replaced {len(remaining)} remaining step(s) "
                    f"with {len(new_steps)} new one(s) (plan v{version + 1})")
            _mark_steer(sid, "applied", note)
            _emit("steer.applied", task_id, steer_id=sid, kind=kind, note=note)
            q._progress(task_id, f"↪ Redirected: {note}")
            continue
    return None


async def _run_parallel(q: Any, task_id: str, step: Dict[str, Any], instructions: List[str],
                        steps: int) -> Tuple[str, int, bool, Optional[str]]:
    """A fan-out step: one coordinator, one read-only worker per job, ONE result back with every
    failure itemized. Urgent steers interrupt it like any step (its workers are stopped).
    Returns (report, steps, interrupted, problem)."""
    from app.services import workers
    facts = [f"The person's standing instruction: {x}" for x in instructions]
    jobs = [{"task": j, "facts": facts} for j in step["parallel"]]
    q._progress(task_id, f"⚡ Fanning out {len(jobs)} parallel read-only jobs")
    run = asyncio.ensure_future(workers.coordinate(task_id, step["text"], jobs))
    while not run.done():
        await asyncio.wait({run}, timeout=STEER_POLL_SEC)
        if run.done():
            break
        q._touch_progress(task_id)
        if any(s["urgent"] for s in inbox(task_id, state="queued")):
            run.cancel()        # the coordinator stops its own workers on the way out
            try:
                await run
            except BaseException:
                pass
            return "", steps, True, None
    result = run.result()
    step["coordinator"] = result["coordinator"]
    step["workers"] = result["workers"]
    step["failures"] = result["failures"]
    problem = None
    if result["outcome"] == "failed":
        problem = "every parallel job failed: " + "; ".join(f"{f['task'][:60]}: {f['why'][:120]}" for f in result["failures"])
    return workers.render_result(result), steps + len(jobs), False, problem


async def _run_step(q: Any, task_id: str, prompt: str, steps: int) -> Tuple[str, int, bool]:
    """Runs one step's agent stream as its own asyncio task, watching the inbox for urgent
    steers while it runs. Returns (report, steps, interrupted)."""
    run = asyncio.ensure_future(q._stream(task_id, prompt, steps))
    try:
        while not run.done():
            await asyncio.wait({run}, timeout=STEER_POLL_SEC)
            if run.done():
                break
            if any(s["urgent"] for s in inbox(task_id, state="queued")):
                run.cancel()
                try:
                    await run
                except BaseException:
                    pass
                return "", steps, True
        report, steps, _payloads = run.result()
        # The stream also stops early on the queue's own cancel flag; a step cut short that
        # way was interrupted, never completed.
        if task_id in q._cancel_requested or any(s["urgent"] for s in inbox(task_id, state="queued")):
            return report, steps, True
        return report, steps, False
    except asyncio.CancelledError:
        run.cancel()
        raise


_UNVERIFIED_RES = (
    r"\b(?:can't|cannot|can not|couldn't|could not|unable to|wasn't able to|was not able to) "
    r"(?:tell|verify|confirm|check|see) (?:whether|if|that)\b",
    r"\b(?:access|shell|tool|tools) (?:is|are|was|were) (?:unavailable|not available)\b",
)


def _self_report_problem(step_text: str, report: str) -> Optional[str]:
    """The step's own report admits it did not finish, was blocked, or could not tell whether
    its outcome holds. A step whose outcome the disk check cannot establish (no file or run
    claim in its words) is only completed on a report that does not say otherwise: a step is
    never marked completed because it was attempted. A step with verifiable claims that all
    held is judged by the disk, not by a hedge in its wording."""
    import re
    from app.services import missions
    from app.services import task_queue as TQ
    if TQ._claimed_file_effects(step_text) or TQ._claimed_run_effects(step_text, []):
        return None
    if not (report or "").strip():
        return "the step ended without any report of what it did"
    report = report.replace("\u2019", "'").replace("\u2018", "'")   # models write curly apostrophes
    hit = missions._incompletion_signal(report) or missions._blocked_signal(report)
    if hit:
        return f"its report says: {hit}"
    for sentence in re.split(r"[.\n]", report):
        if any(re.search(p, sentence.lower()) for p in _UNVERIFIED_RES):
            return f"its report says: {sentence.strip()[:200]}"
    return None


def _final_report(items: List[Dict[str, Any]]) -> str:
    marks = {"completed": "✓", "cancelled": "–", "blocked": "✗", "pending": "·", "in_progress": "…"}
    last = next((i for i in reversed(items) if i["status"] == "completed" and i.get("result")), None)
    lines = []
    if last:
        lines.append(str(last["result"]).strip())
        lines.append("")
    lines.append("Plan:")
    n = 0
    for i in items:
        if i["status"] != "cancelled":
            n += 1
            lines.append(f"{marks[i['status']]} {n}. {i['text']}")
            for f in i.get("failures") or []:
                lines.append(f"   unread/failed: {f['task'][:100]} ({f['outcome']}: {f['why'][:160]})")
        else:
            lines.append(f"{marks['cancelled']} (dropped) {i['text']}" + (f" ({i['note']})" if i.get("note") else ""))
    return "\n".join(lines)


async def run_stepwise(q: Any, task_id: str) -> None:
    """The background execution of one orchestrated task, from wherever its plan stands."""
    task = q.get(task_id)
    q._running_id = task_id
    q._update(task_id, status="working", now="Starting…" if not task.get("plan_json") else "Picking up where it stood")
    q._touch_progress(task_id)
    try:
        items, version, instr = load_plan(task_id)
        if not items:
            q._progress(task_id, "• Planning the steps")
            try:
                texts = await planner(task["request"])
            except Exception as e:
                logger.warning(f"planning failed for {task_id}: {e}")
                texts = []
            honest = None
            if not texts:
                texts = [task["request"]]
                honest = "planning returned nothing usable, so the task runs as one step"
            items = [_item(t) for t in texts]
            if honest:
                items[0]["note"] = honest
            version = save_plan(task_id, items, instr, bump=True, why="first plan")
            q._progress(task_id, f"• Plan ready: {len(items)} step(s)")
        steps = task.get("steps") or 0
        try:
            await q._ensure_tab(task, False)   # a page of its own for any browser work, as classic tasks have
        except Exception as e:
            logger.info(f"Task {task_id}: no browser tab ({e}); continuing without one")
        resume_msg = _take_resume(q, task_id, task)
        while True:
            stop = await _apply_inbox(q, task_id, task)
            if stop:
                return
            items, version, instr = load_plan(task_id)
            step = next((i for i in items if i["status"] == "in_progress"), None) or \
                next((i for i in items if i["status"] == "pending"), None)
            if step is None:
                live = [i for i in items if i["status"] != "cancelled"]
                ok = all(i["status"] == "completed" for i in live) and bool(live)
                q._finish(task_id, "done" if ok else "failed", _final_report(items),
                          note="Finished" if ok else "Stopped with open steps")
                return
            prior_ops: List[str] = []
            if step["status"] == "in_progress" and step.get("started_at"):
                # In flight when the run stopped (a restart, a crash, an interrupt): resume it,
                # carrying the actions it already took so they are not repeated.
                prior_ops = _ops_summary(task_id, step["started_at"])
                resumed = not step.pop("retry_pending", False)
                if resumed:
                    step["note"] = "resumed after an interruption"
            else:
                resumed = False
                if step.get("prior_started_at"):
                    prior_ops = _ops_summary(task_id, step["prior_started_at"])
                step["started_at"] = time.time()
            step["status"] = "in_progress"
            if not resumed or not step.get("attempts"):
                # An interrupted run is not a failed attempt: resuming it does not spend the retry.
                step["attempts"] = int(step.get("attempts") or 0) + 1
            save_plan(task_id, items, instr)
            n_live = sum(1 for i in items if i["status"] != "cancelled")
            k = next((n for n, i in enumerate([i for i in items if i["status"] != "cancelled"], 1) if i["id"] == step["id"]), 0)
            q._progress(task_id, f"• Step {k}/{n_live}: {step['text'][:150]}")
            began = time.time()
            mark = _mission_mark()
            prompt = _step_prompt(task, items, instr, step, prior_ops)
            if resume_msg is not None:
                prompt, resume_msg = resume_msg, None   # the person's OK or answer continues the paused page
            fan_problem = None
            if step.get("parallel"):
                report, steps, interrupted, fan_problem = await _run_parallel(q, task_id, step, instr, steps)
                fan = {k: step.get(k) for k in ("coordinator", "workers", "failures")}
            else:
                report, steps, interrupted = await _run_step(q, task_id, prompt, steps)
                fan = {}
            items, version, instr = load_plan(task_id)
            step = next(i for i in items if i["id"] == step["id"])
            step.update({k: v for k, v in fan.items() if v is not None})
            if interrupted:
                step["note"] = "interrupted by your instruction"
                save_plan(task_id, items, instr)
                q._progress(task_id, f"⏸ Step {k} interrupted by your instruction")
                continue
            if _paused_on_person(q, task_id, items, instr, step, report, began, mark):
                return
            problems = q._verify_file_effects({"id": task_id, "request": step["text"]}, began)
            own = None if step.get("parallel") else _self_report_problem(step["text"], report)
            if own:
                problems = problems + [own]
            if fan_problem:
                problems = problems + [fan_problem]
            if problems and step["attempts"] < 2:
                step["note"] = "first attempt did not check out: " + "; ".join(problems)
                step["retry_pending"] = True
                save_plan(task_id, items, instr)
                q._progress(task_id, f"🔄 Step {k} did not check out; trying it once more")
                continue
            step["evidence"] = _ops_summary(task_id, step["started_at"] or began)
            step["result"] = (report or "")[:4000]
            step["finished_at"] = time.time()
            if problems:
                step["status"] = "blocked"
                step["blocker"] = "; ".join(problems)
                save_plan(task_id, items, instr)
                q._finish(task_id, "failed", _final_report(items) + f"\n\nStopped at step {k}: {step['blocker']}",
                          note="A step did not check out")
                return
            step["status"] = "completed"
            save_plan(task_id, items, instr)
            q._progress(task_id, f"✓ Step {k}/{n_live} done")
    finally:
        if q._running_id == task_id:
            q._running_id = None
        q._cancel_requested.discard(task_id)
        q._cancel_at.pop(task_id, None)
        esc = q._escalations.pop(task_id, None)
        if esc is not None and not esc.done() and esc is not asyncio.current_task():
            esc.cancel()


def _mission_mark() -> Tuple[int, Any, int]:
    """Identity of the browser's mission state when a step starts, so a stop found afterwards
    is known to be new (a restored, already-answered mission must not re-pause the step)."""
    try:
        from app.services.browser_sandbox import browser_sandbox_service as sb
        return id(sb.latest_mission), (sb.latest_mission or {}).get("status"), id(sb.pending_submit)
    except Exception:
        return 0, None, 0


def _paused_on_person(q: Any, task_id: str, items: List[Dict[str, Any]], instr: List[str],
                      step: Dict[str, Any], report: str, began: float, mark: Tuple[int, Any, int]) -> bool:
    """A browser mission inside the step stopped for the person (an irreversible submit needs
    their OK, a question, a bot check). The step is blocked with what would unblock it and the
    task waits, keeping the page and the pending approval, exactly like a classic task. True
    when the run must stop here."""
    try:
        from app.services.browser_sandbox import browser_sandbox_service as sb
    except Exception:
        return False
    mission = sb.latest_mission
    status = (mission or {}).get("status")
    if status not in ("needs_confirmation", "needs_input", "blocked"):
        return False
    same = id(mission) == mark[0] and status == mark[1]
    new_gate = status == "needs_confirmation" and sb.pending_submit is not None and id(sb.pending_submit) != mark[2]
    if same and not new_gate:
        return False      # the state this step started from, not a new stop
    snapshot = {k: v for k, v in mission.items() if k != "latest_screenshot_uri"}
    snapshot["steps"] = [{k: v for k, v in s.items() if k != "screenshot_uri"} for s in mission.get("steps", [])]
    q._save_screenshot(task_id, mission.get("latest_screenshot_uri"))
    step["status"] = "blocked"
    step["waiting"] = True
    if status == "needs_confirmation":
        pending = sb.pending_submit or {}
        sb.pending_submit = None
        step["blocker"] = f"waiting for your OK to press \"{pending.get('label', 'Submit')}\""
        save_plan(task_id, items, instr)
        q._update(task_id, note=f"Waiting for your OK to press \"{pending.get('label', '…')}\"", status="waiting",
                  waiting_kind="approval", pending_json=pending, mission_json=snapshot, result=report,
                  waiting_detail={"label": pending.get("label"), "page": pending.get("title") or pending.get("url"),
                                  "message": mission.get("final_answer")},
                  now="Waiting for your OK", next_action=None)
        from app.services import task_queue as TQ
        TQ.notify(q.get(task_id)["title"], f"Ready to press \"{pending.get('label', 'Submit')}\". Needs your OK.")
        TQ._phone(q.get(task_id), "approval")
    else:
        kind = "question" if status == "needs_input" else "blocked"
        step["blocker"] = "waiting for your answer" if kind == "question" else "stuck at a verification check you need to clear"
        save_plan(task_id, items, instr)
        q._update(task_id, note="Waiting for you", status="waiting", waiting_kind=kind, mission_json=snapshot,
                  waiting_detail={"message": mission.get("final_answer")}, result=report, next_action=None,
                  now="Needs your answer" if kind == "question" else "Stuck at a verification check")
        from app.services import task_queue as TQ
        TQ.notify(q.get(task_id)["title"], "Has a question for you." if kind == "question" else "Stuck at a verification check.")
        TQ._phone(q.get(task_id), kind)
    return True


def _take_resume(q: Any, task_id: str, task: Dict[str, Any]) -> Optional[str]:
    """When the person approved or answered a waiting step: that step goes back in progress,
    the paused page's mission and pending approval are restored, and the message that
    continues it ("go ahead", or their answer) is returned for the step's next run."""
    action = task.get("next_action") or {}
    items, version, instr = load_plan(task_id)
    step = next((i for i in items if i["status"] == "blocked" and i.get("waiting")), None)
    if step is None or action.get("type") not in ("approve", "continue"):
        return None
    try:
        from app.services.browser_sandbox import browser_sandbox_service as sb
        if task.get("mission_json"):
            sb.latest_mission = task["mission_json"]
        sb.pending_submit = None
        if action["type"] == "approve" and task.get("pending_json"):
            sb.pending_submit = {**task["pending_json"], "created_at": time.time()}
    except Exception:
        pass
    step["status"] = "in_progress"
    step["waiting"] = False
    step["blocker"] = None
    step["attempts"] = max(0, int(step.get("attempts") or 1) - 1)   # the person's turn is not a failed attempt
    save_plan(task_id, items, instr)
    q._update(task_id, next_action=None)
    if action["type"] == "approve":
        return "go ahead"
    return f"{action.get('text', '')}\n(continue)"


def reopen(task_id: str) -> None:
    """For a retry of a failed or cancelled orchestrated task: blocked and cancelled steps that
    were not superseded by a redirect become pending again; completed steps stay completed."""
    items, version, instr = load_plan(task_id)
    if not items:
        return
    for i in items:
        superseded = "superseded" in str(i.get("note") or "")
        if i["status"] == "blocked" or (i["status"] == "cancelled" and not superseded):
            i["status"] = "pending"
            i["blocker"] = None
            i["attempts"] = 0
            if i.get("started_at"):
                i["prior_started_at"] = i["started_at"]   # its actions so far are carried into the re-run
            i["started_at"] = None
            i["note"] = "re-opened by your retry"
    save_plan(task_id, items, instr, bump=True, why="retry")
