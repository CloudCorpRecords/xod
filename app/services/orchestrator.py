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
import os
import re
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
    independent = False
    depends_on: List[Any] = []
    if isinstance(text, dict):
        parallel = text.get("parallel")
        independent = bool(text.get("independent"))
        depends_on = list(text.get("depends_on") or [])
        text = text.get("text", "")
    return {"id": f"s-{uuid.uuid4().hex[:6]}", "text": str(text).strip(), "status": "pending", "parallel": parallel,
            "independent": independent, "depends_on": depends_on,
            "blocker": None, "result": None, "evidence": [], "origin": origin,
            "attempts": 0, "started_at": None, "finished_at": None, "note": None}


def public_plan(task_id: str) -> Dict[str, Any]:
    items, version, instructions = load_plan(task_id)
    out = {"task_id": task_id, "version": version, "items": items, "instructions": instructions,
           "inbox": inbox(task_id)}
    try:   # observed worker state: running vs quiet vs stalled, with the true last-activity time
        from app.services import workers
        crds = workers.list_workers(task_id)
        out["workers"] = [{k: w.get(k) for k in ("id", "role", "shown_state", "last_activity_at", "last_activity",
                                                 "stalled_reason", "parent_id")}
                          for c in crds for w in [c] + workers.list_workers(c["id"])]
    except Exception:
        pass
    return out


async def ainbox(task_id: str, state: Optional[str] = None) -> List[Dict[str, Any]]:
    """inbox() off the event loop. Running steps poll it twice a second; opened on the loop, a busy database froze the
    whole server for 0.6 s at a time (live, load test with 12 workers, 2026-11-04)."""
    return await asyncio.to_thread(inbox, task_id, state)


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
    c.execute("UPDATE steer_inbox SET state = ?, applied_at = ?, applied_note = ? WHERE id = ? AND state IN ('queued', 'taken')",
              (state, time.time(), note[:500], steer_id))
    c.commit()
    c.close()


# --------------------------------------------------------------------- planning (model calls, injectable)
PLAN_PROMPT = """You are planning a task for a personal AI agent that will carry it out step by step.

Task from the person:
{request}

Break it into 1 to {max_steps} concrete steps, in order. Each step must have an outcome that can be
checked (a file exists with given content, a value was found, a message was drafted). Keep every exact
detail from the task in the step that needs it: full file paths, names, numbers, quoted text. Do not
add steps the person did not ask for. A task that is genuinely one action is one step. Never split reading
or looking something up from the step that uses it: "summarize the file X" is ONE step (read it and
summarize it), not a read step followed by a summary step.

Keep each step description concise (under 45 words): never paste multi-line scripts, plists, or full file
bodies inside a step description; the step worker writes the code with its file tools. When a task asks to
build and install a system service, Quick Action, workflow, or CLI utility, plan 3 distinct steps:
(1) create the complete bundle and script files on disk, (2) register or install at the destination and
verify registration, (3) run an end-to-end functional test on sample input and verify its output.

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
    from app.services.llm import llm_service
    reply = await llm_service.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1, max_tokens=600, think=False, task="mission_plan")
    return reply.get("content", "") or ""


_INLINE_CODE_DUMP_RE = re.compile(
    r"(?:\s*(?:with the following content|containing the following(?:\s+content|\s+script|\s+code)?)\s*:\s*\n.+)"
    r"|(?:\n+```[\s\S]*)"
    r"|(?:\n+#!/[\s\S]*)",
    re.I | re.S,
)

_MACOS_SERVICE_TASK_RE = re.compile(
    r"(?:~/Library/Services|/Library/Services|\b(?:right[- ]click|quick action|macos service|services menu)\b)"
    r"[\s\S]{0,400}?\b(?:service|workflow|quick action|right[- ]click|ai|llm|selected text|selection)\b",
    re.I,
)


def _strip_inline_code_from_step(text: str) -> str:
    """Removes multi-line script/code dumps that a planner stuffed into a step description."""
    cleaned = _INLINE_CODE_DUMP_RE.sub("", text or "").strip()
    if "\n" in cleaned and len(cleaned) > 320:
        first_line = cleaned.splitlines()[0].strip().rstrip(":")
        if len(first_line) >= 20:
            cleaned = first_line
    return cleaned or (text or "").strip()


def _sanitize_and_shape_steps(request: str, steps: List[Any]) -> List[Any]:
    """Sanitizes planner output (stripping inline truncated code dumps) and enforces a 3-step
    build -> register -> functional-test plan shape for macOS Service / Quick Action builds."""
    sanitized: List[Any] = []
    for s in steps or []:
        if isinstance(s, dict) and s.get("text") and not s.get("parallel"):
            s_copy = dict(s)
            s_copy["text"] = _strip_inline_code_from_step(str(s["text"]))
            sanitized.append(s_copy)
        elif isinstance(s, str):
            sanitized.append(_strip_inline_code_from_step(s))
        else:
            sanitized.append(s)

    req = request or ""
    if _MACOS_SERVICE_TASK_RE.search(req):
        joined = " ".join(s.get("text", "") if isinstance(s, dict) else str(s) for s in sanitized).lower()
        has_bundle = ".workflow" in joined and "info.plist" in joined
        has_pbs = "pbs" in joined
        has_test = ("test" in joined or "verify" in joined) and len(sanitized) >= 3
        if not (has_bundle and has_pbs and has_test):
            # Extract custom service name if the request explicitly named a .workflow bundle, else AskXodAI.workflow.
            m_wf = re.search(r"([\w\- ]+\.workflow)\b", req)
            wf_name = m_wf.group(1).strip() if m_wf else "AskXodAI.workflow"
            return [
                (f"Create the files ~/Library/Services/{wf_name}/Contents/Info.plist (valid XML plist with NSServices "
                 f"array containing NSMenuItem default 'Ask Xod AI', NSMessage 'runWorkflowAsService', and NSSendTypes "
                 f"['public.utf8-plain-text']), ~/Library/Services/{wf_name}/Contents/document.wflow (valid Automator "
                 f"Run Shell Script plist invoking ask_ai.py), and ~/Library/Services/{wf_name}/Contents/ask_ai.py "
                 f"(executable Python script reading selected text from sys.argv or sys.stdin and querying "
                 f"http://127.0.0.1:1234/v1/chat/completions with fallback to http://127.0.0.1:11434/v1/chat/completions, "
                 f"without requiring an external API key)."),
                (f"Register the Quick Action with macOS by running /System/Library/CoreServices/pbs -update and "
                 f"verify {wf_name} appears in /System/Library/CoreServices/pbs -dump_pboard."),
                (f"Test the installed service end-to-end by running "
                 f"python3 ~/Library/Services/{wf_name}/Contents/ask_ai.py 'Reply with OK' and confirming exit code 0 "
                 f"and a non-empty response."),
            ]
    return sanitized[:MAX_STEPS]


MAX_ARTIFACT_SUBSTEPS = 8


def _raw_path_in_text(text: str, path: Any) -> Optional[Tuple[str, int, int]]:
    """The raw path token in `text` that resolves to `path` (a claim path from
    task_queue._claimed_file_effects, which expands ~), as (raw, start, end)."""
    import os
    from app.services import task_queue as TQ
    for m in TQ._PATH_RE.finditer(text or ""):
        raw = m.group(0).rstrip(".,;:)`")
        if not raw or raw in ("~", "/"):
            continue
        try:
            if os.path.normpath(os.path.expanduser(raw)) == os.path.normpath(str(path)):
                return raw, m.start(), m.start() + len(raw)
        except Exception:
            continue
    return None


def _spec_after_path(text: str, end: int) -> str:
    """The balanced parenthetical immediately following a path in the step text, if any:
    the shaped build step carries each file's spec that way (".../Info.plist (valid XML
    plist with NSServices ...)")."""
    i = end
    while i < len(text) and text[i] in " \t":
        i += 1
    if i >= len(text) or text[i] != "(":
        return ""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return text[i + 1:j].strip()
    return ""


def _artifact_substep_text(step_text: str, claim: Dict[str, Any]) -> Optional[str]:
    """One single-artifact step text for one write claim of a multi-file step, carrying that
    file's own spec fragment. Returns None when the resulting text does not claim exactly
    this one file (the step checker reads claims from the text, so a substep text that
    still claimed the sibling files would be judged on all of them)."""
    from app.services import task_queue as TQ
    found = _raw_path_in_text(step_text, claim["path"])
    if not found:
        return None
    raw, _start, end = found
    spec = _spec_after_path(step_text, end)
    tail = (" This is one file of several named by a single larger step; the other files are "
            "separate substeps and are NOT part of this substep. Create only this file; do "
            "not create or modify any other file.")
    candidates = []
    if spec:
        candidates.append(f"Create exactly one file: {raw} ({spec})." + tail)
    candidates.append(f"Create exactly one file: {raw}." + tail)
    import os
    for text in candidates:
        claims = TQ._claimed_file_effects(text)
        if len(claims) == 1 and claims[0].get("kind") == "write" and \
                os.path.normpath(str(claims[0]["path"])) == os.path.normpath(str(claim["path"])):
            return text
    return None


def _multi_file_write_claims(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The distinct file-write claims a step's own text makes (empty when the step is not a
    splittable multi-file step)."""
    if step.get("parallel") or step.get("substep_of"):
        return []
    from app.services import task_queue as TQ
    claims = [c for c in TQ._claimed_file_effects(str(step.get("text") or ""))
              if c.get("kind") == "write" and c.get("target") == "file"]
    seen, out = set(), []
    import os
    for c in claims:
        key = os.path.normpath(str(c["path"]))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out if 2 <= len(out) <= MAX_ARTIFACT_SUBSTEPS else []


def _maybe_split_step(q: Any, task_id: str, items: List[Dict[str, Any]], instr: List[str],
                      step: Dict[str, Any]) -> bool:
    """Splits a step that must produce several files into one plan item per file, so no
    single model stream has to emit every file (Run 4, 2026-11-08: step 1 streamed 585 s,
    wrote only Info.plist, and its one retry re-ran the whole 3-file step into a 300 s
    stall). Each substep is a normal plan item: the step checker judges it on its one file,
    its artifact ledger entry is recorded when that file lands, and a retry only ever
    re-runs the one missing file.

    Splits a fresh pending step, or an in-progress step on its retry (retry_pending): on a
    retry, a claimed file the queue's own file-effect check verifies on disk becomes a
    completed substep and is never re-run; the rest become pending substeps carrying the
    failed attempt's note, so their prompts check prior effects before redoing anything.
    Returns True when the plan changed (the caller re-picks the next step)."""
    claims = _multi_file_write_claims(step)
    if not claims:
        return False
    is_retry = step.get("status") == "in_progress" and step.pop("retry_pending", False)
    if step.get("status") == "in_progress" and not is_retry:
        return False   # a step resumed mid-flight keeps the existing resume semantics
    step_text = str(step.get("text") or "")
    sub_texts: List[Optional[str]] = [_artifact_substep_text(step_text, c) for c in claims]
    if any(t is None for t in sub_texts):
        if is_retry:
            step["retry_pending"] = True   # unsplittable: leave the whole-step retry as it was
        return False
    subs: List[Dict[str, Any]] = []
    for claim, sub_text in zip(claims, sub_texts):
        sub = _item(sub_text, origin="substep")
        sub["substep_of"] = step["id"]
        if is_retry:
            verified = False
            try:
                verified = not q._verify_file_effects(
                    {"id": task_id, "request": f"Create the file {claim['path']}"},
                    step.get("started_at") or 0)
            except Exception:
                verified = False
            if verified:
                sub["status"] = "completed"
                sub["result"] = ("Already on disk and verified when the original step was split "
                                 "for its retry; not re-run.")
                sub["artifacts"] = [str(claim["path"])]
                sub["evidence"] = [f"verified on disk at split: {claim['path']}"]
                sub["finished_at"] = time.time()
            else:
                note = str(step.get("note") or "")
                sub["note"] = note if note.startswith("first attempt did not check out") else \
                    "first attempt did not check out: the original multi-file step did not finish"
                if step.get("started_at"):
                    sub["prior_started_at"] = step["started_at"]
        subs.append(sub)
    idx = next(n for n, i in enumerate(items) if i["id"] == step["id"])
    items[idx:idx + 1] = subs
    save_plan(task_id, items, instr, bump=True,
              why=f"split multi-file step into {len(subs)} single-artifact substeps")
    _emit("plan.split", task_id, step_id=step["id"], substeps=len(subs),
          pre_completed=sum(1 for s in subs if s["status"] == "completed"))
    q._progress(task_id, f"• Splitting a {len(claims)}-file step into {len(subs)} single-file "
                         f"substeps, one model stream each")
    return True


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
            if s.get("independent") or s.get("depends_on"):
                out.append({
                    "text": str(s["text"]).strip(),
                    "independent": bool(s.get("independent")),
                    "depends_on": list(s.get("depends_on") or []),
                })
            else:
                out.append(str(s["text"]).strip())
        elif str(s).strip():
            out.append(str(s).strip())
    return out[:MAX_STEPS]


async def default_planner(request: str) -> List[str]:
    raw_steps = _parse_steps(await _ask_model(PLAN_PROMPT.format(request=request, max_steps=MAX_STEPS)))
    return _sanitize_and_shape_steps(request, raw_steps)


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


def submit(request: str, title: Optional[str] = None, source: str = "user", external_ref: Optional[str] = None) -> Dict[str, Any]:
    """Creates an orchestrated task and returns its ack immediately. Nothing has run yet:
    planning and every step happen afterwards in the background worker."""
    from app.services.task_queue import task_queue
    if source != "user":
        # Only user-originated direction may create goals (spec section 10).
        raise PermissionError("Only the person can start a task.")
    t = task_queue.create(request, title=title, mode="stepwise", external_ref=external_ref)
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
                           waiting_kind=None, waiting_detail=None, pending_json=None,
                           now="Cancelled", note="Cancelled by you", result=report)
        from app.services import agent_log
        agent_log.clear_waiting_for_origin(f"task:{task_id}", decision="cancelled")
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


def task_memory(task_id: str) -> Optional[Dict[str, Any]]:
    c = _conn()
    r = c.execute("SELECT memory_json FROM tasks WHERE id = ?", (task_id,)).fetchone()
    c.close()
    try:
        return json.loads(r["memory_json"]) if r and r["memory_json"] else None
    except Exception:
        return None


async def _search_memory_first(q: Any, task_id: str, request: str) -> Dict[str, Any]:
    """The mandatory pre-work step (spec section 8): memory is searched before any planning, once
    per task, and the result (each fact with where it was learned) is stored on the task."""
    mem = task_memory(task_id)
    if mem is not None:
        return mem
    from app.services import memory_layers
    try:
        mem = await memory_layers.search_before_work(request)
    except Exception as e:
        mem = {"facts": [], "past": [], "error": str(e)}
    c = _conn()
    c.execute("UPDATE tasks SET memory_json = ? WHERE id = ?", (json.dumps(mem), task_id))
    c.commit()
    c.close()
    q._progress(task_id, f"• Searched memory first: {len(mem.get('facts') or [])} fact(s), "
                         f"{len(mem.get('past') or [])} related past item(s)")
    return mem


def _is_large_context() -> bool:
    """True when the active chat model is a cloud provider (or large-context backend) rather than
    a memory-constrained local model."""
    try:
        from app.services.llm import llm_service
        return not llm_service._is_local()
    except Exception:
        return False


def _step_written_files(task_id: str, since: float) -> List[str]:
    """Ordered unique file paths written or edited by this task's tools since `since`."""
    try:
        from app.services import activity
        entries = activity.entries_for_origin(f"task:{task_id}", since=since - 0.5)
    except Exception:
        return []
    paths: List[str] = []
    seen = set()
    for e in entries:
        if not e.get("ok"):
            continue
        for op in e.get("ops") or []:
            if op.get("kind") in ("write_file", "restore_file", "remove_created"):
                p = str(op.get("path") or "").strip()
                if p and p not in seen:
                    seen.add(p)
                    paths.append(p)
        if not e.get("ops") and e.get("tool") in ("computer_write_file", "computer_edit_file", "fs_write_workspace_file"):
            p = str(e.get("detail") or "").strip()
            if p and p not in seen:
                seen.add(p)
                paths.append(p)
    return paths


def _task_artifact_ledger(task_id: str, items: List[Dict[str, Any]], large_ctx: Optional[bool] = None) -> str:
    """Builds a provenance-preserving workspace artifact ledger (CLM-024) of files created or
    modified by completed steps in this task, reading their current on-disk contents so later
    steps do not suffer step-boundary amnesia."""
    import os
    from pathlib import Path
    if large_ctx is None:
        large_ctx = _is_large_context()
    per_file = 2400 if large_ctx else 700
    total_cap = 9600 if large_ctx else 2100
    max_files = 8 if large_ctx else 4

    candidate_paths: List[str] = []
    seen = set()
    for i in items:
        if i.get("status") != "completed":
            continue
        for p in i.get("artifacts") or []:
            if p and p not in seen:
                seen.add(p)
                candidate_paths.append(p)
    for p in _step_written_files(task_id, 0.0):
        if p and p not in seen:
            seen.add(p)
            candidate_paths.append(p)

    if not candidate_paths:
        return ""

    blocks: List[str] = []
    used = 0
    for raw_p in candidate_paths[-max_files:]:
        try:
            fp = Path(os.path.expanduser(raw_p))
            if not fp.is_file():
                continue
            size = fp.stat().st_size
            raw_bytes = fp.read_bytes()[: per_file * 2]
            if b"\x00" in raw_bytes[:512]:
                snippet = "(binary file)"
            else:
                text = raw_bytes.decode("utf-8", errors="replace").strip()
                if len(text) > per_file:
                    half = per_file // 2
                    snippet = text[:half] + "\n... [truncated] ...\n" + text[-half:]
                else:
                    snippet = text
            entry = f"- {raw_p} ({size} bytes):\n  ```\n  {snippet.replace(chr(10), chr(10) + '  ')}\n  ```"
            if used + len(entry) > total_cap and blocks:
                break
            blocks.append(entry)
            used += len(entry)
        except Exception:
            continue

    if not blocks:
        return ""
    return (
        "\nWorkspace artifact ledger (files created or updated by earlier steps in this task, read from disk now):\n"
        + "\n".join(blocks)
    )


_PLAN_UPDATE_RE = re.compile(r"(?:^|\n)\s*PLAN_UPDATE:\s*(.+?)\s*$", re.I | re.M)
MAX_AUTO_REPLANS = 2


def _extract_plan_update(report: str) -> Tuple[str, Optional[str]]:
    """Extracts an optional `PLAN_UPDATE: <instruction>` directive from a step's report and returns
    `(cleaned_report, plan_update_instruction)`."""
    if not report:
        return "", None
    m = _PLAN_UPDATE_RE.search(report)
    if not m:
        return report, None
    directive = m.group(1).strip()
    cleaned = _PLAN_UPDATE_RE.sub("", report).strip()
    return (cleaned or report.strip()), (directive if len(directive) >= 5 else None)


def _step_prompt(task: Dict[str, Any], items: List[Dict[str, Any]], instructions: List[str],
                 step: Dict[str, Any], prior_ops: List[str]) -> str:
    live = [i for i in items if i["status"] != "cancelled"]
    k = next((n for n, i in enumerate(live, 1) if i["id"] == step["id"]), 0)
    done = [i for i in items if i["status"] == "completed"]
    large_ctx = _is_large_context()
    parts = ["You are carrying out ONE step of a larger task for the person.",
             f"\nThe overall task:\n{task['request']}"]
    try:
        from app.services import memory_layers
        mem_text = memory_layers.render(task_memory(task["id"]))
    except Exception:
        mem_text = ""
    if mem_text:
        parts.append("\n" + mem_text)
    if instructions:
        parts.append("\nChanges the person made since the task started (newest last; they win over the "
                     "original wording wherever they conflict):\n" + "\n".join(f"- {x}" for x in instructions))
    if done:
        # Results travel between steps in full (up to 6000 chars for the last 3 on cloud / 4000 for the last 2
        # on local, and 1500 chars for older steps on cloud / 600 on local): each step is a fresh agent run,
        # and a 300-character snippet once left a later step without the text its earlier step had found (live S10).
        recent_n = 3 if large_ctx else 2
        recent_cap = 6000 if large_ctx else 4000
        older_cap = 1500 if large_ctx else 600
        recent = {i["id"] for i in done[-recent_n:]}
        parts.append("\nSteps already completed. Their effects already exist; do NOT redo any of them:\n"
                     + "\n".join(f"- {i['text']}" + (f"\n  RESULT: {str(i.get('result') or '')[:recent_cap if i['id'] in recent else older_cap]}"
                                                       if i.get("result") else "")
                                 for i in done))
        ledger = _task_artifact_ledger(task["id"], items, large_ctx=large_ctx)
        if ledger:
            parts.append(ledger)
    retrying = str(step.get("note") or "").startswith("first attempt did not check out")
    if prior_ops and retrying:
        # Unknown is not failed (spec section 9): actions already taken get checked, never redone blindly.
        parts.append("\nYour earlier attempt at this step already took these actions:\n"
                     + "\n".join(f"- {o}" for o in prior_ops)
                     + "\nTheir outcome is UNKNOWN, not failed. Do NOT repeat them. First check whether they took "
                     "effect: a background job may still be running (wait for it or check its status), a file or "
                     "destination can be read. Repeat an action only if you have verified its effect is absent and "
                     "it is no longer running, and then only once.")
    elif prior_ops:
        parts.append("\nThis step was started before and interrupted (by a restart or by the person). These actions "
                     "from it were already taken:\n" + "\n".join(f"- {o}" for o in prior_ops)
                     + "\nFirst check whether their effects are in place. Do not redo an action whose effect is "
                     "already there. Redo only what is missing: a background job or command started before a "
                     "restart may have been stopped by it, so its effect may be missing.")
    if retrying:
        found = step["note"].split(": ", 1)[1]
        parts.append("\nYou already attempted this step once and checking found: " + found + "."
                     + (" Establish the real outcome as above." if prior_ops else " Do it now for real, using your tools."))
    parts.append(f"\nYOUR STEP NOW (step {k} of {len(live)}):\n{step['text']}")
    tail = ("\nDo only this step, using your tools. Then reply with a short report of what you did "
            "and what you found. If you cannot do it, say exactly what blocks it.")
    if k < len(live):
        tail += (" If what you discovered on disk means the remaining steps of the plan need to be adjusted, "
                 "add a final line `PLAN_UPDATE: <how the remaining steps should be updated>`.")
    parts.append(tail)
    return "\n".join(parts)


async def _apply_inbox(q: Any, task_id: str, task: Dict[str, Any]) -> Optional[str]:
    """Applies every queued steer, oldest first. Returns 'paused' or 'cancelled' when the run
    must stop here, else None."""
    for s in await ainbox(task_id, state="queued"):
        kind, body, sid = s["kind"], s["body"] or "", s["id"]
        items, version, instr = load_plan(task_id)
        if kind == "cancel":
            report = _cancel_plan(task_id, "you cancelled it")
            _mark_steer(sid, "applied", report)
            _emit("steer.applied", task_id, steer_id=sid, kind=kind, note=report[:200])
            q._update(task_id, status="cancelled", finished_at=time.time(), next_action=None, waiting_kind=None,
                      waiting_detail=None, pending_json=None,
                      now="Cancelled", note="Cancelled by you", result=report)
            from app.services import agent_log
            agent_log.clear_waiting_for_origin(f"task:{task_id}", decision="cancelled")
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
    for f in (task_memory(task_id) or {}).get("facts") or []:
        facts.append(f"From memory: {f['text']} (learned from: {f['source']}, {f['learned']})")
    jobs = [{"task": j, "facts": facts} for j in step["parallel"]]
    q._progress(task_id, f"⚡ Fanning out {len(jobs)} parallel read-only jobs")
    # A fan-out interrupted earlier (a restart) keeps the jobs its workers already finished.
    prev = [w for w in workers.list_workers(task_id) if w["role"] == "coordinator" and w["brief"] == step["text"]
            and w["state"] in ("interrupted", "running")]
    run = asyncio.ensure_future(workers.coordinate(task_id, step["text"], jobs,
                                                   reuse_from=prev[-1]["id"] if prev else None))
    while not run.done():
        try:
            await asyncio.wait({run}, timeout=STEER_POLL_SEC)
        except asyncio.CancelledError:
            run.cancel()        # the task's own run is going away: so does its fan-out
            raise
        if run.done():
            break
        q._touch_progress(task_id)
        if any(s["urgent"] for s in await ainbox(task_id, state="queued")):
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


JOB_WAIT_SEC = 900


def _step_jobs(task_id: str, since: float) -> List[Dict[str, Any]]:
    """Background jobs this task started during the step, as the job registry observes them."""
    try:
        from app.services.system_control import system_control_service as sc
    except Exception:
        return []
    return [j for j in sc.background_jobs.values()
            if j.get("origin") == f"task:{task_id}" and j.get("start_time", 0) >= since - 1]


async def _await_step_jobs(q: Any, task_id: str, since: float) -> Tuple[List[Dict[str, Any]], bool]:
    """A step's own job still running when the agent's turn ended is waited for here, bounded,
    with progress, and interruptible by an urgent steer. Returns (jobs, interrupted). The step is
    judged on what the jobs observably did, not on the agent's guess from a check seconds in."""
    jobs = _step_jobs(task_id, since)
    running = [j for j in jobs if j.get("status") == "running"]
    if not running:
        return jobs, False
    q._progress(task_id, f"⏳ Waiting for {len(running)} job(s) this step started: "
                         + ", ".join(j.get("name") or j["id"] for j in running)[:150])
    deadline = time.time() + JOB_WAIT_SEC
    while any(j.get("status") == "running" for j in running) and time.time() < deadline:
        await asyncio.sleep(1.0)
        q._touch_progress(task_id)
        if any(s["urgent"] for s in await ainbox(task_id, state="queued")):
            return jobs, True
    return _step_jobs(task_id, since), False


def _job_problems(jobs: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """(problems, evidence) from the observed end state of a step's jobs."""
    problems, evidence = [], []
    runs: Dict[Tuple[Any, Any], int] = {}
    for j in jobs:
        key = (j.get("command"), j.get("cwd"))
        runs[key] = runs.get(key, 0) + 1
    for (cmd, _cwd), n in runs.items():
        if cmd and n > 1:
            # Observed, not guessed (live L4: one append ran twice under two labels). It cannot be undone
            # from here, so it is reported, never absorbed into a "done".
            problems.append(f"duplicated side effect: the command '{str(cmd)[:120]}' ran {n} times in this step")
    for j in jobs:
        label = j.get("name") or j["id"]
        st, code = j.get("status"), j.get("exit_code")
        if st == "running":
            problems.append(f"its job '{label}' was still running after {JOB_WAIT_SEC} s")
        elif st == "completed" and code == 0:
            evidence.append(f"observed: job '{label}' finished with exit code 0")
        else:
            problems.append(f"its job '{label}' ended {st} with exit code {code}")
    return problems, evidence


# Test hooks only (XOD_TEST_HOOKS=1): a task whose step actions are scripted tool calls, so an acceptance
# test can exercise the engine (executor, outcomes, step judging) without depending on the model's choices.
SCRIPTED: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}


async def _scripted_stream(task_id: str, calls: List[Tuple[str, Dict[str, Any]]], steps: int):
    from app.agent import tools
    lines = []
    for name, args in calls:
        try:
            r = await tools.execute_tool(name, dict(args), reviewed=True)
        except Exception as e:
            r = {"success": False, "error": f"{type(e).__name__}: {e}"}
        lines.append(f"{name}: outcome {r.get('outcome')} ({r.get('outcome_evidence') or r.get('error') or r.get('message') or ''})")
    return "Ran the scripted action. " + " | ".join(lines), steps + len(calls), []


async def _run_step(q: Any, task_id: str, prompt: str, steps: int) -> Tuple[str, int, bool]:
    """Runs one step's agent stream as its own asyncio task, watching the inbox for urgent
    steers while it runs. Returns (report, steps, interrupted)."""
    import inspect
    if task_id in SCRIPTED:
        run = asyncio.ensure_future(_scripted_stream(task_id, SCRIPTED[task_id], steps))
    else:
        framing = q._task_framing() if hasattr(q, "_task_framing") else ""
        try:
            params = inspect.signature(q._stream).parameters
            accepts_framing = "system_extra" in params or len(params) >= 4
        except Exception:
            accepts_framing = False
        if accepts_framing and framing:
            run = asyncio.ensure_future(q._stream(task_id, prompt, steps, framing))
        else:
            run = asyncio.ensure_future(q._stream(task_id, prompt, steps))
    try:
        while not run.done():
            await asyncio.wait({run}, timeout=STEER_POLL_SEC)
            if run.done():
                break
            if any(s["urgent"] for s in await ainbox(task_id, state="queued")):
                run.cancel()
                try:
                    await run
                except BaseException:
                    pass
                return "", steps, True
        report, steps, _payloads = run.result()
        # The stream also stops early on the queue's own cancel flag; a step cut short that
        # way was interrupted, never completed.
        if task_id in q._cancel_requested or any(s["urgent"] for s in await ainbox(task_id, state="queued")):
            return report, steps, True
        return report, steps, False
    except asyncio.CancelledError:
        run.cancel()
        raise


_UNVERIFIED_RES = (
    r"\b(?:can't|cannot|can not|couldn't|could not|unable to|wasn't able to|was not able to|did not|didn't) "
    r"(?:tell|verify|confirm|check|see)\b",
    r"\b(?:access|shell|tool|tools) (?:is|are|was|were) (?:unavailable|not available)\b",
    r"\bno longer have (?:shell|file|tool)",
    r"\b(?:is|was) still running\b",
    r"\bnot (?:yet )?(?:verified|confirmed)\b",
    r"\b(?:status|outcome|result) (?:is|was|remains) (?:unclear|unknown)\b",
    r"\bunclear\b",
    # Live S9 and S5 (2026-11-04): "I couldn't send the text because the test_send tool isn't available"
    # was marked completed. Not being able to do the step's action is not doing it.
    r"\b(?:couldn't|could not|can't|cannot|unable to|wasn't able to|was not able to|did not|didn't)\s+"
    r"(?:send|post|upload|submit|save|deliver|read|open|access|reach|connect|download|install|start|perform|load|fetch|use)\b",
    r"\b(?:isn't|is not|wasn't|was not|aren't|are not)\s+(?:available|accessible|reachable)\b",
    r"\b(?:no|nothing)\s+(?:\w+\s+)?(?:was|were)\s+(?:sent|written|saved|delivered|uploaded|posted|submitted)\b",
)


_MODEL_OR_PLAN_ERROR_RES = (
    r"\bmissing api key for\b",
    r"\bno api key (?:configured|set|saved) for\b",
    r"\brefused the api key\b",
    r"\bi couldn't work out the steps for that just now, so i haven't done anything\b",
    r"\bi can't do this one yet:\b",
)

_EXPLICIT_INCOMPLETE_OR_PUNT_RES = (
    r"\b(?:is|remains|left)\s+incomplete\b",
    r"\bwritten,?\s+but\s+(?:it\s+is\s+)?incomplete\b",
    r"\bmissing its closing\b",
    r"\b(?:truncated|cut off)\s+(?:mid-|before|at)\b",
    r"\bi can (?:fix|finish|complete) (?:that|this|it) in the next step if you\b",
    r"\bif you'?d like me to (?:continue|finish|fix|complete)\b",
    r"\bwould you like me to (?:continue|finish|fix|complete)\b",
    r"\blet me know if you(?:'d like| want) me to (?:continue|finish|fix|complete)\b",
)


def _self_report_problem(step_text: str, report: str) -> Optional[str]:
    """The step's own report admits it did not finish, was blocked, or could not tell whether
    its outcome holds. A step whose outcome the disk check cannot establish (no file or run
    claim in its words) is only completed on a report that does not say otherwise: a step is
    never marked completed because it was attempted. A step with verifiable claims that all
    held is judged by the disk, not by a hedge in its wording — unless the report explicitly
    emits a model/API-key error, admits the written artifact is incomplete/truncated, or punts
    completion under granted authority (Rule 6)."""
    import re
    from app.services import missions
    from app.services import task_queue as TQ
    if not (report or "").strip():
        return "the step ended without any report of what it did"
    if re.search(r"(?:^|\n)\s*(?:The model stopped:|\*\*Paused after |\*\*Not finished yet:)", report or ""):
        return f"its report says: {(report or '').strip().splitlines()[0][:200]}"
    norm_report = (report or "").replace("\u2019", "'").replace("\u2018", "'")
    for p in _MODEL_OR_PLAN_ERROR_RES:
        m_err = re.search(p, norm_report, re.I)
        if m_err:
            return f"its report says: {norm_report.strip().splitlines()[0][:200]}"
    for p in _EXPLICIT_INCOMPLETE_OR_PUNT_RES:
        m_inc = re.search(p, norm_report, re.I)
        if m_inc:
            for s in re.split(r"[.\n]", norm_report):
                if re.search(p, s, re.I):
                    return f"its report admits the step is incomplete: {s.strip()[:200]}"
            return f"its report admits the step is incomplete: {m_inc.group(0)[:200]}"
    m_exact = re.search(r"reply with exactly this text and nothing else:\s*(.+)", step_text or "", re.I)
    if m_exact:
        want = m_exact.group(1).strip().strip("\"'`")
        got = re.sub(r"<!--.*?-->", "", norm_report, flags=re.S).strip()
        if want and got != want:
            return f"exact reply mismatch: expected {want!r}, got {got[:120]!r}"
    if TQ._claimed_file_effects(step_text) or TQ._claimed_run_effects(step_text, []):
        # Judged by the disk, unless a declared input is missing: then the
        # disk has nothing honest to judge the output against, and the
        # report's own words are evidence again. Without this, a step that
        # admits its input file is missing still completes, because the
        # output claim silences the report and the disk check never models
        # inputs (Tier 6 leg C, defect D1).
        if all(p.exists() for p in TQ._claimed_input_effects(step_text)):
            return None
    report = norm_report
    # An empty "couldn't ...: Nothing" section of a confirmed/inferred/unknown report is not an admission (live S5).
    report = re.sub(r"(?:could not|couldn't|can't|cannot|unable to)\s+[\w ,/]{0,40}?[:*]+\s*\**\s*(?:nothing|none|n/?a|-|—)\b\.?",
                    "", report, flags=re.I)
    # A verifying retry is told not to repeat what already happened; saying it did not is success.
    report = re.sub(r"\b(?:i )?(?:did not|didn't|did not need to|without) (?:re-?run|run|repeat|restart|start)\w* "
                    r"(?:it|them|the [\w-]+(?: [\w-]+)?|anything) again\b", "", report, flags=re.I)
    hit = missions._incompletion_signal(report) or missions._blocked_signal(report)
    if hit:
        return f"its report says: {hit}"
    for sentence in re.split(r"[.\n]", report):
        if any(re.search(p, sentence.lower()) for p in _UNVERIFIED_RES):
            return f"its report says: {sentence.strip()[:200]}"
    return None


def _overrule_hedge(report: str, observed: List[str]) -> str:
    """The step's report minus the sentences that hedge on an outcome Xod observed, plus what it observed."""
    import re
    kept = [x for x in re.split(r"(?<=[.!?])\s+|\n+", report or "")
            if x.strip() and not any(re.search(p, x.replace("\u2019", "'").lower()) for p in _UNVERIFIED_RES)]
    seen = "; ".join(o.strip() for o in observed[:3]) or "every job this step started finished cleanly"
    return (" ".join(kept).strip() + "\n\n" if kept else "") + f"Checked by Xod: {seen}."


def _flag_section(items: List[Dict[str, Any]]) -> str:
    seen, lines = set(), []
    for i in items:
        for f in i.get("flags") or []:
            if f["quote"] in seen:
                continue
            seen.add(f["quote"])
            lines.append(f"- \"{f['quote'][:240]}\" (found by {f['tool']}; not followed"
                         + (f"; nothing was sent to {', '.join(f['addresses'][:2])}" if f.get("addresses") else "") + ")")
    if not lines:
        return ""
    return ("\n\nFlagged: content I read contained instructions aimed at an AI. They came from the content, not "
            "from you, so I did not follow them:\n" + "\n".join(lines))


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
    return "\n".join(lines) + _flag_section(items)


async def run_stepwise(q: Any, task_id: str) -> None:
    """The background execution of one orchestrated task, from wherever its plan stands."""
    task = q.get(task_id)
    q._update(task_id, status="working", now="Starting…" if not task.get("plan_json") else "Picking up where it stood")
    q._touch_progress(task_id)
    try:
        await _search_memory_first(q, task_id, task["request"])
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
        auto_replans = 0
        # No browser tab is grabbed up front: tasks run side by side, and the browser is claimed (and
        # its global mission state touched) only by a task that actually uses it (services/resources.py).
        resume_msg = await _take_resume(q, task_id, task)
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
            if _maybe_split_step(q, task_id, items, instr, step):
                continue   # the plan changed under us: re-pick (the first pending substep)
            prior_ops: List[str] = []
            if step["status"] == "in_progress" and step.get("started_at"):
                # In flight when the run stopped (a restart, a crash, an interrupt): resume it,
                # carrying the actions it already took so they are not repeated.
                prior_ops = _ops_summary(task_id, step["started_at"])
                resumed = not step.pop("retry_pending", False)
                if resumed:
                    step["note"] = "resumed after an interruption"
                    step.setdefault("log", []).append({"at": time.time(), "text": "resumed after an interruption"})
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
            jobs, cut = await _await_step_jobs(q, task_id, step.get("started_at") or began)
            if cut:
                step["note"] = "interrupted by your instruction"
                save_plan(task_id, items, instr)
                continue
            job_problems, job_evidence = _job_problems(jobs)
            # Unknown is not failed (spec section 9): an action of this step that broke off is checked at
            # its destination (waiting for it to become readable) before the step is judged.
            try:
                from app.services import outcomes
                settled = await outcomes.resolve_unknowns(f"task:{task_id}", outcomes.ARGS_SEEN,
                                                          since=step.get("started_at") or began)
            except Exception:
                settled = []
            for u in settled:
                if u["final"] == "CONFIRMED":
                    job_evidence.append(f"{u['tool']} broke off but landed: {u['evidence']}")
                elif u["final"] == "FAILED":
                    job_problems.append(f"{u['tool']} broke off and did not land ({u['evidence']}); it may be retried once")
                else:
                    job_problems.append(f"{u['tool']} outcome is still UNKNOWN ({u['evidence']}); not retried blindly")
            problems = await asyncio.to_thread(q._verify_file_effects, {"id": task_id, "request": step["text"]}, began)
            meta = getattr(q, "_last_stream_meta", {}).get(task_id) or {}
            step_ops = _ops_summary(task_id, step.get("started_at") or began)
            if meta.get("errors") and (
                not (report or "").strip()
                or any(err in (report or "") for err in meta["errors"])
                or not step_ops
            ):
                problems = problems + [f"model stream error: {'; '.join(meta['errors'])}"]
            own = None if step.get("parallel") else _self_report_problem(step["text"], report)
            if own and jobs and not job_problems:
                # The agent could not see the end; the orchestrator did: every job it started
                # for this step finished cleanly. The observation outranks the hedge, and the result says so
                # instead of telling the person it couldn't confirm (live L3, count k).
                own = None
                report = _overrule_hedge(report, [e for e in job_evidence if "observed" in e])
            problems = problems + job_problems
            if own:
                problems = problems + [own]
            if fan_problem:
                problems = problems + [fan_problem]
            fatal_key_err = any(
                re.search(r"missing api key for|no api key (?:configured|set|saved) for", p, re.I)
                for p in problems
            )
            if problems and step["attempts"] < 2 and not fatal_key_err:
                step["note"] = "first attempt did not check out: " + "; ".join(problems)
                step.setdefault("log", []).append({"at": time.time(), "text": step["note"][:300]})
                step["retry_pending"] = True
                save_plan(task_id, items, instr)
                q._progress(task_id, f"🔄 Step {k} did not check out; trying it once more")
                continue
            step["evidence"] = step_ops + job_evidence
            step_files = _step_written_files(task_id, step.get("started_at") or began)
            if step_files and step.get("substep_of"):
                # A substep's ledger entry is exactly its one file: the written-files scan
                # uses a half-second slack window, which would otherwise absorb a sibling
                # substep's just-landed file when substeps run back to back.
                import os
                from app.services import task_queue as TQ
                own = {os.path.normpath(str(c["path"]))
                       for c in TQ._claimed_file_effects(step["text"]) if c.get("kind") == "write"}
                scoped = [p for p in step_files if os.path.normpath(os.path.expanduser(p)) in own]
                if scoped:
                    step_files = scoped
            if step_files:
                step["artifacts"] = step_files
            try:   # instructions found inside content this step read: recorded by code, reported, never followed
                from app.services import sentinel
                flags = sentinel.flags_for(f"task:{task_id}", step["started_at"] or began)
                if flags:
                    step["flags"] = [{"quote": f["quote"], "tool": f["tool"], "addresses": f["targets"][:5]} for f in flags]
            except Exception:
                pass
            cleaned_report, plan_update_directive = _extract_plan_update(report or "")
            res_cap = 6000 if _is_large_context() else 4000
            step["result"] = (cleaned_report or "")[:res_cap]
            step["finished_at"] = time.time()
            _log_slow_step(task_id, step, k, began, blocked=bool(problems))
            if problems:
                step["status"] = "blocked"
                step["blocker"] = "; ".join(problems)
                # Blocked-state continuation (Section 2b): if any remaining pending steps are
                # explicitly marked independent (or have `depends_on` not naming this blocked step),
                # block only dependent steps and let independent steps run!
                pending_after = [i for i in items if i["status"] == "pending"]
                has_explicit_dag = any(i.get("independent") or i.get("depends_on") for i in pending_after)
                if has_explicit_dag:
                    blocked_ids = {step["id"], k}
                    for rem in pending_after:
                        deps = set(rem.get("depends_on") or [])
                        if not rem.get("independent") and (not deps or (deps & blocked_ids)):
                            rem["status"] = "blocked"
                            rem["blocker"] = f"blocked by step {k}: {step['blocker'][:160]}"
                            rem["finished_at"] = time.time()
                            blocked_ids.add(rem["id"])
                    save_plan(task_id, items, instr)
                    if any(i["status"] == "pending" for i in items):
                        q._progress(task_id, f"✗ Step {k} blocked ({step['blocker'][:100]}); continuing independent steps")
                        continue
                if fatal_key_err:
                    # A missing API key cannot be supplied by an answer
                    # here (Argon keyless-probe contract, check 4a): the
                    # task fails honestly instead of waiting on the person.
                    save_plan(task_id, items, instr)
                    q._finish(task_id, "failed", _final_report(items) + f"\n\nStopped at step {k}: {step['blocker']}",
                              note="A step did not check out")
                    return
                step["waiting"] = True
                save_plan(task_id, items, instr)
                _pause_blocked_on_person(q, task_id, items, step, k, problems)
                return
            step["status"] = "completed"
            save_plan(task_id, items, instr)
            q._progress(task_id, f"✓ Step {k}/{n_live} done")
            remaining_pending = [i for i in items if i["status"] == "pending"]
            if plan_update_directive and remaining_pending and auto_replans < MAX_AUTO_REPLANS:
                done_now = [i for i in items if i["status"] == "completed"]
                try:
                    adapted_steps = await replanner(task["request"], done_now, remaining_pending, plan_update_directive)
                    adapted_steps = _sanitize_and_shape_steps(task["request"], adapted_steps)
                except Exception as e:
                    logger.warning(f"adaptive replan failed for {task_id}: {e}")
                    adapted_steps = []
                if adapted_steps:
                    auto_replans += 1
                    for rem in remaining_pending:
                        rem["status"] = "cancelled"
                        rem["note"] = f"superseded by step {k} discovery (plan v{version + 1})"
                        rem["finished_at"] = time.time()
                    items = items + [_item(t, origin="adaptive") for t in adapted_steps]
                    version = save_plan(task_id, items, instr, expect_version=version, bump=True,
                                        why=f"adaptive: {plan_update_directive[:160]}")
                    _emit("plan.adapted", task_id, step_index=k, note=plan_update_directive[:200], version=version)
                    q._progress(task_id, f"↪ Plan adapted from step {k} discovery: {plan_update_directive[:120]}")
    finally:
        q._cancel_requested.discard(task_id)
        q._cancel_at.pop(task_id, None)
        esc = q._escalations.pop(task_id, None)
        if esc is not None and not esc.done() and esc is not asyncio.current_task():
            esc.cancel()


# A step slower than this is named in the log (Tier 6 D4: the stalls
# under load could not be attributed to any step because nothing
# recorded per-step durations). missions.py keeps the same threshold
# for mission steps; keep the two in step.
SLOW_STEP_MS = 1000


def _log_slow_step(task_id: str, step: Dict[str, Any], k: int, began: float,
                   blocked: bool) -> None:
    """Slow-step record: a step whose run took over SLOW_STEP_MS is
    logged (warning + a task.slow_step event) with its duration and
    the tools it used, so a freeze under load names the step that was
    running. Fast steps record nothing, and a failure to record never
    breaks the run."""
    try:
        dur_ms = int((time.time() - began) * 1000)
        if dur_ms <= SLOW_STEP_MS:
            return
        tools: List[str] = []
        try:
            from app.services import activity
            seen = {e.get("tool") for e in activity.entries_for_origin(
                f"task:{task_id}", since=began - 0.5)}
            tools = sorted(t for t in seen if t)
        except Exception:
            pass
        logger.warning(f"slow step: task {task_id} step {k} ({step.get('id')}) took "
                       f"{dur_ms} ms; tools: {', '.join(tools) or 'none recorded'}")
        from app.services import event_log
        event_log.emit("task.slow_step", actor="orchestrator", ids={"task_id": task_id},
                       payload={"step_id": step.get("id"), "step_index": k,
                                "duration_ms": dur_ms, "tools": tools,
                                "blocked": blocked})
    except Exception:
        pass


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
    """A browser mission or safety/MCP approval inside the step stopped for the person (an
    irreversible submit or tool call needs their OK, a question, a bot check). The step is blocked
    with what would unblock it and the task waits, keeping the page and the pending approval,
    exactly like a classic task. True when the run must stop here."""
    # Check non-browser tool/Sentinel approval holds first (D1 Run 0 fix).
    try:
        from app.services import agent_log
        meta = getattr(q, "_last_stream_meta", {}).get(task_id) or {}
        open_approvals = agent_log.waiting_approvals_for(f"task:{task_id}", since=began - 0.5)
        held = meta.get("held_approval") or (open_approvals[0] if open_approvals else None)
    except Exception:
        held = None
    if held:
        label = str(held.get("tool") or held.get("title") or "Approve")
        msg = str(held.get("message") or held.get("detail") or report or "Needs your approval.")
        step["status"] = "blocked"
        step["waiting"] = True
        step["blocker"] = f"waiting for your OK ({label})"
        save_plan(task_id, items, instr)
        q._update(task_id, note=f"Waiting for your OK ({label})", status="waiting",
                  waiting_kind="approval", pending_json=held, result=report,
                  waiting_detail={"label": label, "message": msg},
                  now="Waiting for your OK", next_action=None)
        from app.services import task_queue as TQ
        TQ.notify(q.get(task_id)["title"], f"Needs your OK ({label}).")
        TQ._phone(q.get(task_id), "approval")
        return True
    try:
        from app.services.browser_sandbox import browser_sandbox_service as sb
        from app.services import resources
    except Exception:
        return False
    # The browser's mission state belongs to whichever task holds the browser; a task that never
    # held it this step cannot have been stopped by it (tasks run side by side).
    holders = [h["holder_id"] for r in resources.status("browser") for h in r["holders"]]
    if f"task:{task_id}" not in holders:
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


_PATH_TOKEN_RE = re.compile(r"(?:~[/\\]|/|[A-Za-z]:[/\\])[^\s\"'<>|`]+")
_MISSING_WORDS_RE = re.compile(r"not on disk|does not exist|no such file|could not be read|"
                               r"couldn't read|cannot read|can't read|missing|not found", re.I)


def _unblock_condition(problems: List[str], step_text: str = "") -> Tuple[str, bool]:
    """(what the person can do to unblock the step, whether it names a
    missing input) — derived from the step's own problems, so the
    needs-you says the condition, not just the failure: a path the
    step needed and the disk does not have is named outright, so the
    person can create it or point to the right one. A problem string
    can carry the path truncated at its first dot (the self-report
    check splits a report into sentences on dots), so a problem path
    that is a strict prefix of a path the step itself names is
    completed from the step's text before it is quoted."""
    text = "; ".join(problems)
    step_paths = [m.group(0).rstrip(".,;:)")
                  for m in _PATH_TOKEN_RE.finditer(step_text or "")]
    for m in _PATH_TOKEN_RE.finditer(text):
        p = m.group(0).rstrip(".,;:)")
        if len(p) < 3:
            continue
        full = next((sp for sp in step_paths if sp.startswith(p) and len(sp) > len(p)), None)
        cand = full or p
        try:
            if not os.path.exists(os.path.expanduser(cand)):
                return (f"To unblock: make sure {cand} exists (create it, or point to the "
                        f"right file in your answer), then answer here and the step "
                        f"will run again.", True)
        except OSError:
            continue
    if _MISSING_WORDS_RE.search(text):
        return ("To unblock: provide the missing input the step names, then answer "
                "here and the step will run again.", True)
    return ("To unblock: fix what the step describes, then answer here and the "
            "step will run again.", False)


def _pause_blocked_on_person(q: Any, task_id: str, items: List[Dict[str, Any]],
                             step: Dict[str, Any], k: int, problems: List[str]) -> None:
    """Parks a blocked step on the person (Tier 6 leg C fix): the task
    waits with the blocker as a needs-you — the same waiting machinery
    a classic task and a browser stop use, so the answer, retry and
    cancel surfaces all reach it — and the wait's message states the
    unblock condition derived from the problems. The step stays
    blocked with waiting=True, which is the shape _take_resume picks
    up when the person answers."""
    condition, missing_input = _unblock_condition(problems, step.get("text") or "")
    kind = "question" if missing_input else "blocked"
    question = f"Step {k} is blocked: {step.get('blocker')}. {condition}"
    q._update(task_id, note="Waiting for you", status="waiting", waiting_kind=kind,
              waiting_detail={"message": question, "blocker": step.get("blocker"), "step": k},
              result=_final_report(items), next_action=None,
              now="Needs your help to continue")
    from app.services import task_queue as TQ
    TQ.notify(q.get(task_id)["title"],
              "A step is blocked and needs you." if kind == "blocked"
              else "Has a question for you.")
    TQ._phone(q.get(task_id), kind)


async def _take_resume(q: Any, task_id: str, task: Dict[str, Any]) -> Optional[str]:
    """When the person approved or answered a waiting step: that step goes back in progress,
    the paused page's mission and pending approval are restored, and the message that
    continues it ("go ahead", or their answer) is returned for the step's next run."""
    action = task.get("next_action") or {}
    items, version, instr = load_plan(task_id)
    step = next((i for i in items if i["status"] == "blocked" and i.get("waiting")), None)
    if step is None or action.get("type") not in ("approve", "continue"):
        return None
    if task.get("mission_json") or task.get("pending_json"):
        # Restoring the paused page's mission touches the browser's global state: only with the browser held.
        from app.services import resources
        q._progress(task_id, "⏳ Waiting for the browser to continue the paused page")
        await resources.wait_for("browser", f"task:{task_id}", max_wait=3600)
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
    # The wait is over: the task row must not keep carrying it (a stale
    # waiting_detail outlived the wait on the shipped tree, because no
    # resume or finish path cleared it).
    q._update(task_id, next_action=None, waiting_kind=None, waiting_detail=None)
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


# --------------------------------------------------------------------- the pool: tasks side by side
POOL_SIZE = 3
_pool: Dict[str, asyncio.Task] = {}
_pool_loop: Optional[asyncio.Task] = None
_pool_wake: Optional[asyncio.Event] = None


def wake_pool(loop: Any = None) -> None:
    if _pool_wake is None:
        return
    if loop is not None:
        loop.call_soon_threadsafe(_pool_wake.set)
    else:
        _pool_wake.set()


def _claim_next() -> Optional[str]:
    """Atomically takes the oldest queued orchestrated task (one runner per task, even if two
    pools share the database)."""
    c = _conn()
    r = c.execute("SELECT id FROM tasks WHERE status = 'queued' AND mode = 'stepwise' ORDER BY updated_at ASC LIMIT 1").fetchone()
    if not r:
        c.close()
        return None
    n = c.execute("UPDATE tasks SET status = 'working', updated_at = ? WHERE id = ? AND status = 'queued'",
                  (time.time(), r["id"])).rowcount
    c.commit()
    c.close()
    if n:
        # The activity timer (services/liveness.py): this claim is the
        # moment a stepwise task enters "working", and it bypasses
        # task_queue._update (the claim is a direct SQL update so two
        # pools cannot take the same task), so the timer hears about
        # the flight here, at the transition itself.
        try:
            from app.services import liveness
            liveness.work_started(r["id"])
        except Exception:
            pass
    return r["id"] if n else None


async def _pool_run(q: Any, task_id: str) -> None:
    from app.services import activity, resources
    activity.origin.set(f"task:{task_id}")
    try:
        await run_stepwise(q, task_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception(f"Task {task_id} failed")
        q._update(task_id, note=f"Error: {e}", status="failed", finished_at=time.time(),
                  result=f"Something went wrong inside Xod: {e}", now="Stopped", next_action=None)
    finally:
        try:
            await q._sweep_tool_children(task_id)
        except Exception:
            pass
        resources.release_holder(f"task:{task_id}")
        _pool.pop(task_id, None)
        wake_pool()


async def _pool_main(q: Any) -> None:
    while True:
        try:
            while len([t for t in _pool.values() if not t.done()]) < POOL_SIZE:
                tid = _claim_next()
                if not tid:
                    break
                _pool[tid] = asyncio.ensure_future(_pool_run(q, tid))
            _pool_wake.clear()
            try:
                await asyncio.wait_for(_pool_wake.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("orchestrator pool")
            await asyncio.sleep(2)


def start_pool(q: Any) -> None:
    global _pool_loop, _pool_wake
    _pool_wake = asyncio.Event()
    if _pool_loop is None or _pool_loop.done():
        _pool_loop = asyncio.ensure_future(_pool_main(q))


def stop_pool() -> None:
    global _pool_loop
    if _pool_loop is not None:
        _pool_loop.cancel()
        _pool_loop = None
    for t in list(_pool.values()):
        t.cancel()
    _pool.clear()
