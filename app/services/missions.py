"""
Missions: the parent orchestrator (Xod Brain v1).

A mission is a job too big for one task. The parent (this module) asks the
configured model to break the goal into a short plan of subtasks, each with the
file effects it is expected to have, in the same words the task queue's
file-effect check understands. The plan is strict: it is validated against a
schema, and an invalid plan is re-asked once with the problems named, then the
mission fails honestly instead of improvising.

Subtasks then run one at a time, in order, as ordinary tasks in the existing
TaskQueue. The queue's own file-effect gate decides each subtask: this module
never overrides that verdict, it records it. For every subtask the mission
keeps two separate things: what the agent claimed (its report), and what was
verified (the gate's outcome, with the evidence). A finished mission writes a
report built on that distinction, the workproof habit: "the agent said" is
never dressed up as "checked on disk".

State lives in the same xod.db as tasks, so a mission survives an app
restart. On boot, missions that were planning, running or paused are picked
up again from the first unfinished subtask; a subtask whose task was
interrupted is simply watched while the queue re-runs it.

Deliberate limits of v1: subtasks run sequentially (there is one task queue
and one browser), on this machine only, with no bus. Usage and cost are not
tracked here; the report says so until a usage ledger exists to read from.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from pathlib import Path

from app.paths import DATA_DIR

logger = logging.getLogger("xod.missions")
DB = DATA_DIR / "xod.db"

MISSION_STATES = ("planning", "running", "paused", "done", "failed")
_EFFECT_KINDS = ("create", "modify", "delete")
_TERMINAL_TASK = ("done", "failed", "cancelled")

# ------------------------------------------------------- pause supervision
# Fix round 5 (2026-10-31, from the Tier 1 re-run 4 record, T1B): a
# research mission sat in state paused for the full 900 second
# measurement cap and never produced a report, because its subtask's
# task was waiting and nobody answered. The driver loop re-drives a
# paused mission every couple of seconds, but all that did was enter
# the same wait again: nothing kept a clock on the pause, so a wait
# nobody answers had no terminal shape at all.
#
# The remedy is bounded. A subtask task that stays waiting past
# PAUSE_WEDGE_THRESHOLD_SEC gets ONE automatic resume through the
# queue's own retry path (the same "trying again" a person can ask
# for; never a raw state edit, never an invented answer). If the task
# waits again and that second wait also runs past
# PAUSE_RESUME_WINDOW_SEC, the step fails honestly: the verdict names
# the wedge, how long it sat, and that one resume was attempted.
#
# The numbers are the codebase's own. The task queue flags a working
# task stalled at 600 seconds without progress (STALL_THRESHOLD_SEC)
# and stops it at twice that (STALL_RECOVER_SEC); a waiting task has
# already notified the user (Mac notification and phone) when it
# started waiting, so 600 unattended seconds is a wedge, not a wait
# in progress, and healthy missions finish whole steps in 60 to 90
# seconds. Worst case to an honest failure is about 1200 seconds,
# the same total budget the queue gives a wedged working task.
#
# One wait kind is exempt: a task waiting for approval is a person's
# gate. Supervision never resumes it (an automatic retry must not
# stand in for a person's OK on an irreversible step) and never times
# it out. Question and blocked waits are supervised, but the resume
# goes through the normal retry path, so a person can still answer
# at any moment before the threshold and nothing here invents an
# answer for them. What the code cannot do is tell an unattended
# question from one a person is about to answer; the 600 second
# threshold is the whole distinction, and it is recorded as a limit.
PAUSE_WEDGE_THRESHOLD_SEC = 600
PAUSE_RESUME_WINDOW_SEC = 600


# ------------------------------------------------------- verdict integrity
# Fix round 1 (2026-10-31, from the Tier 1 baseline): a mission closed
# "done" with every subtask "verified" while the subtasks' own reports said
# "not complete", one named a pytest failure, the produced test file was a
# planner instruction sentence rather than code, and the mission's evidence
# text asserted a declared run check had exited 0 without anything having
# established that. The helpers below are the mission layer's own checks,
# applied before any subtask may be called verified: the subtask's own
# report must not admit incompletion, its deliverable content must not be
# planner scaffolding, and every declared run effect is re-executed here
# through the queue's run check and must exit with the required code. The
# queue's gate still runs first; these checks close the gaps where the
# queue has nothing to contradict (a step with no file claims, a file whose
# existence was checked but whose contents were not, a run the plan
# declared but the task never proved).

_IMPERATIVE_START = (r"(?:construct|create|write|insert|add|include|place|put|"
                     r"generate|produce|implement|draft|compose|build|ensure)\b")


def _find_instruction_leak(text: Any) -> Optional[str]:
    """A line of would-be deliverable content that is actually a planner
    instruction addressed to the writer, or None. Conservative by design:
    a line needs two signals at once, an imperative addressed to the
    writer and a reference to the planning process itself (after
    inspection, the preceding step or source check, inserting a verified
    source before saving). An imperative alone is ordinary content (user
    guides, notes and code are full of them) and never matches, so
    dictated proof strings, notes and code do not false-positive.

    One more two-signal shape, measured in production three runs in a
    row (re-run 2, T1A): the whole line is a single angle-bracketed
    descriptive sentence about the artifact to be made ("<pytest tests
    invoking the actual CLI via subprocess with sys.executable and
    tmp_path input files; assert exact stdout formatting and
    counts, ...>"). It has no imperative opener (the bracket and the
    noun phrase come first) and no planning-process reference, so the
    rule above returned None for it at plan validation and at the
    on-disk verdict check alike, and the sentence was written to disk
    as the test file. The bracket is the first signal and descriptive
    artifact vocabulary inside it (pytest, subprocess, tmp_path,
    assert, stdout, invoking) is the second; a bracketed line carrying
    markup (a closing tag inside it) or ordinary prose is not matched."""
    if not text or not isinstance(text, str):
        return None
    for line in text.splitlines():
        s = line.strip()
        if len(s) > 2 and s.startswith("<") and s.endswith(">"):
            inner = s[1:-1].strip()
            if ("<" not in inner and ">" not in inner
                    and len(inner.split()) >= 5
                    and re.search(r"\b(pytest|subprocess|sys\.executable|tmp_path|"
                                  r"assert|invok\w*|verif\w*|stdout|tests?)\b",
                                  inner.lower())):
                return s[:160]
        low = line.strip().lstrip("(\"'*-> ").lower()
        if re.search(r"\bfrom the preceding source check\b", low):
            return line.strip()[:160]
        if not re.match(_IMPERATIVE_START, low):
            continue
        if re.search(r"\bafter inspection\b", low):
            return line.strip()[:160]
        if re.search(r"\b(preceding|previous) (step|check|task|source)\b", low):
            return line.strip()[:160]
        if (re.search(r"\bbefore saving\b", low)
                and re.search(r"\b(source|url|link|citation|exact)\b", low)):
            return line.strip()[:160]
    return None


_INCOMPLETION_RES = (
    r"\bnot (?:yet )?(?:complete|completed|finished|done)\b",
    r"\bincomplete\b",
    r"\bpartially (?:complete|completed|finished|done)\b",
    r"\b(?:could not|couldn't|unable to|failed to) (?:complete|finish|create|write|run|make)\b",
    r"\bdid not (?:complete|finish|create|write|run)\b",
    r"\bcollection error\b",
)


def _incompletion_signal(claimed: Any) -> Optional[str]:
    """The sentence of a subtask report that admits the step did not
    finish, or None. A subtask whose own words say the work is not
    complete, or name a test failure, cannot be verified no matter what
    the disk check found. Zero-count success phrasing ("5 passed,
    0 failed", "no failures") is a success report, not an admission, and
    is neutralised before scanning so clean reports do not trip this."""
    if not claimed or not isinstance(claimed, str):
        return None
    text = re.sub(r"\b0 (?:failed|failures|errors)\b", "0", claimed, flags=re.I)
    text = re.sub(r"\bno (?:failures|errors|failed tests)\b", "none", text, flags=re.I)
    for sentence in re.split(r"[.\n]", text):
        low = sentence.lower()
        if any(re.search(p, low) for p in _INCOMPLETION_RES):
            return sentence.strip()[:160]
        if re.search(r"\bfail", low) and re.search(r"\b(pytest|test suite|tests?|collection)\b", low):
            return sentence.strip()[:160]
    return None


# ------------------------------------------------------- blocked signal
# Fix round 6 (BOARD-001, Lane D): a worker that reports it could not
# run for a missing input or a missing tool is not "done", and it is
# not an ordinary failure either: it is blocked, a typed state the
# verdict layer honours, so the mission names the block instead of
# dressing the step up as verified (the BOARD-001 mission's subtask 3
# reported it had no tools to create files and still closed verified)
# or routing around it through some other flow. The patterns are
# conservative on purpose, in the _incompletion_signal style: a
# self-report of missing capability or missing input, in the worker's
# own words. Ordinary incompletion ("could not finish") stays with
# _incompletion_signal; this fires only when the report names WHY in
# capability/input terms.
_BLOCKED_RES = (
    r"\b(?:have|has|had) no (?:tools?|way) to\b",
    r"\bno (?:file )?(?:write|create|writing) tool\b",
    r"\bnot (?:included|provided|given|delivered|attached) (?:in|to) (?:me|this step|what i received)\b",
    r"\b(?:evidence pack|evidence|brief|input|inputs|file|context) (?:was|were|is) not (?:included|provided|given|attached|delivered)\b",
    r"\bmissing (?:input|inputs|evidence|context|file) (?:needed|required|to)\b",
    r"\bcannot (?:assess|evaluate|complete|proceed) without\b",
    r"\bwithout the (?:tools?|input|evidence|file|context) (?:needed|required|to)\b",
)


def _blocked_signal(claimed: Any) -> Optional[str]:
    """The sentence of a subtask report that says the step was blocked
    by a missing input or a missing tool, or None."""
    if not claimed or not isinstance(claimed, str):
        return None
    for sentence in re.split(r"[.\n]", claimed):
        low = sentence.lower()
        if any(re.search(p, low) for p in _BLOCKED_RES):
            return sentence.strip()[:200]
    return None


def _has_decisive_evidence(task: Dict[str, Any]) -> bool:
    """Positive evidence a no-effect step can be verified on (fix round
    6, Lane D): the task carries a decisive-evidence payload (the tool
    result blocks the hunt layer renders and the queue stores on the
    task), something the verdict can inspect, as opposed to the mere
    absence of a contradiction on disk."""
    raw = task.get("evidence_json")
    if raw:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            data = None
        if isinstance(data, (list, dict)) and data:
            return True
    ev = task.get("evidence")
    return isinstance(ev, list) and bool(ev)


def _run_effect_problem(eff: Dict[str, Any]) -> Optional[str]:
    """Re-executes one declared run effect through the queue's own run
    check; returns its problem text, or None when the exit code is the
    required one. A declared run is the proof a subtask's code works, so
    the mission layer establishes that proof itself instead of asserting
    it: a run that failed, is refused, or never ran (its target does not
    exist) returns a problem here and blocks verification."""
    from app.services import task_queue as _tq
    command = str(eff.get("command", ""))
    cwd = Path(os.path.expanduser(str(eff.get("cwd", ""))))
    claim = {"command": command, "cwd": cwd,
             "expect_exit": eff.get("expect_exit", 0),
             "refused": _tq._run_refusal(command)}
    return _tq._check_run_claim(claim)


def _declares_directory(subtask: Dict[str, Any]) -> bool:
    """The plan's own words call this create step a directory step (the
    same test render_request uses when it words the step's task)."""
    words = f"{subtask.get('title', '')} {subtask.get('instruction', '')}".lower()
    return bool(re.search(r"\b(directory|folder)\b", words))


def _fresh_effect_problems(subtask: Dict[str, Any], types_only: bool = False) -> List[str]:
    """Ways this subtask's declared effects are NOT true on disk right
    now, checked fresh at verdict time with the same rules the task
    layer's gate uses (task_queue._verify_file_effects over declared
    claims): a create or modify target must be on disk (as a directory
    when the step is a directory step), dictated single-line content
    must match byte for byte, a delete target must be absent, and a
    declared run must exit with its required code. This is the only
    source a verdict may compose contradiction reasons from: a reason
    recorded when a task finished can be stale by verdict time (a
    parallel step landed the file after that check ran), and a file
    that exists at verdict time must never be named as missing."""
    from app.services.task_queue import _file_type_problem
    problems: List[str] = []
    for eff in subtask.get("expected_effects") or []:
        kind = eff.get("kind")
        if kind == "run":
            if not types_only:
                problem = _run_effect_problem(eff)
                if problem:
                    problems.append(problem)
            continue
        if kind not in ("create", "modify", "delete"):
            continue
        p = Path(os.path.expanduser(str(eff.get("path", ""))))
        if kind == "delete":
            if p.exists() and not types_only:
                problems.append(f"the task asked for {p.name} to be deleted, "
                                "but it is still on disk")
            continue
        if kind == "create" and _declares_directory(subtask):
            if not p.is_dir() and not types_only:
                problems.append(f"the task asked for {p.name} to be created, "
                                "but it is not on disk")
            continue
        if not p.is_file():
            if not types_only:
                problems.append(f"the task asked for {p.name} to be created, "
                                "but it is not on disk")
            continue
        content = eff.get("content")
        if not types_only and isinstance(content, str) and content == content.strip() and "\n" not in content:
            try:
                actual = p.read_bytes() if p.stat().st_size <= 1_000_000 else None
            except OSError:
                actual = None
            if actual is not None and actual != content.encode("utf-8"):
                problems.append(f"{p.name} does not contain exactly what the task asked for")
        # Type validation (fix round 6, BOARD-001 Design 2): existence is
        # not enough. A .py that does not compile, a test file that
        # collects no tests, or a .json that does not parse is not the
        # artifact the step declared, whatever its name.
        type_problem = _file_type_problem(p)
        if type_problem:
            problems.append(type_problem)
    return problems


def _recheck_failed_subtasks(mission_id: str) -> None:
    """Verdict-time re-check of every subtask recorded failed and
    contradicted (fix round 4, re-run 3 T1A): re-check its declared
    effects fresh, the way _outcome_from_task now does when the task
    finishes. A subtask whose effects are all true now (a parallel or
    doubled step landed them after its own check ran) is upgraded to
    done and verified, with the supersession stated in its evidence; a
    subtask that still fails keeps its verdict, but any recorded
    missing-file reason for a file that exists now is dropped, so the
    mission's stated reasons match disk truth at verdict time. A
    subtask whose own report admits incompletion, or whose deliverable
    carries planner instruction text, is never upgraded: those are not
    timing artifacts."""
    for s in list_subtasks(mission_id):
        if s["status"] != "failed" or s.get("verified_status") != "contradicted":
            continue
        if not (s.get("expected_effects") or []):
            continue
        if _incompletion_signal(s.get("claimed")):
            continue
        if _content_leak_for_subtask(s):
            continue
        fresh = _fresh_effect_problems(s)
        if not fresh:
            _save_subtask(s["id"], status="done", verified_status="verified",
                          evidence=["A fresh check at verdict time found every declared "
                                    "effect of this step true on disk. The problem recorded "
                                    "when its task finished no longer held (the work landed "
                                    "after that check ran), so it is not carried into the "
                                    "verdict."])
            continue
        kept: List[str] = []
        for e in s.get("evidence") or []:
            stale = False
            for eff in s.get("expected_effects") or []:
                if eff.get("kind") in ("create", "modify"):
                    p = Path(os.path.expanduser(str(eff.get("path", ""))))
                    if (f"{p.name} to be created, but it is not on disk" in e
                            and (p.is_file() or p.is_dir())):
                        stale = True
            if not stale:
                kept.append(e)
        if kept != (s.get("evidence") or []):
            _save_subtask(s["id"], evidence=kept
                          or [f"Checked on disk: {p}" for p in fresh])


def _repair_finished_verdict(mission_id: str) -> None:
    """Fix round 5: reconcile a finished mission whose recorded verdict
    a stale peer process wrote from superseded evidence. Two backend
    processes share this database; a process running an older build in
    memory composes verdicts the old way (re-run 4 T1A: its outcome
    copied the task's recorded problems verbatim, backticked phantom
    file included, and it finished the mission) while this build's
    driver, polling the same subtask, composes the fresh outcome and
    saves it to the subtask row moments later. The row then disagrees
    with the mission's failure reason and report, and nothing ever
    reconciles them, because a finished mission is never driven again.
    This runs when a driver of this build reaches a finished mission
    (its own finish call finding the mission already finished, or an
    outcome save for a subtask of one): re-run the finish-time
    re-check, and if the recorded verdict cites evidence no subtask
    row carries any more, rebuild the report and the failure reason
    from the current rows. A mission whose failed steps all verify
    fresh is closed done. Nothing is rewritten when the recorded
    verdict still matches the rows."""
    mission = get_mission(mission_id)
    if not mission or mission["state"] not in ("done", "failed"):
        return
    if not mission.get("finished_at"):
        return
    before = {s["id"]: (s["status"], s.get("verified_status"),
                         tuple(s.get("evidence") or []))
              for s in mission["subtasks"]}
    _recheck_failed_subtasks(mission_id)
    current = get_mission(mission_id)
    after = {s["id"]: (s["status"], s.get("verified_status"),
                        tuple(s.get("evidence") or []))
             for s in current["subtasks"]}
    rows_changed = before != after
    reason = current.get("failure_reason") or ""
    failed = [s for s in current["subtasks"] if s["status"] == "failed"]
    # Blocked steps (fix round 6) stand exactly as failed ones do here:
    # a mission with a blocked step is not done, and its citation is
    # reconciled against the blocked row, never repaired away.
    blocked = [s for s in current["subtasks"] if s["status"] == "blocked"]
    new_reason, new_state = reason, current["state"]
    if current["state"] == "failed" and not failed and not blocked:
        new_state, new_reason = "done", None
    elif current["state"] == "failed" and reason.startswith("Step "):
        word = " blocked: " if " blocked: " in reason else " failed: "
        cited = reason.split(word, 1)[-1] if word in reason else ""
        step_no = None
        matched = re.match(r"Step (\d+) ", reason)
        if matched:
            step_no = int(matched.group(1))
        pool = blocked if word == " blocked: " else failed
        if not pool:
            pool = failed + blocked
        target = next((s for s in pool if s["idx"] + 1 == step_no), None)
        if target is None and pool:
            target = pool[0]
        if target is not None and cited and cited not in (target.get("evidence") or []):
            ev = target.get("evidence") or ["see the step report"]
            new_reason = f"Step {target['idx'] + 1} ({target['title']}){word}{ev[0]}"
    if not rows_changed and new_reason == reason and new_state == current["state"]:
        return
    logger.warning(f"Mission {mission_id}: repairing a finished verdict that cited "
                   "superseded evidence (a stale peer process finished it first); "
                   "the verdict now follows the fresh row evidence.")
    if new_state != current["state"] or new_reason != reason:
        _save_mission(mission_id, state=new_state,
                      failure_reason=new_reason if new_state == "failed" else None)
    _save_mission(mission_id, report=build_report(get_mission(mission_id)))


def _content_leak_for_subtask(subtask: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Planner scaffolding inside a subtask's deliverable as a
    (path, line) pair, or None. Checks the content the plan declared and,
    when a created or modified file is on disk and reads as UTF-8 text,
    the file's actual content: the baseline's instruction-text test file
    was never in the plan, the worker wrote the instruction sentence
    itself. Files that are absent (the queue's gate owns existence) or
    not text are not judged here."""
    for eff in subtask.get("expected_effects") or []:
        if eff.get("kind") == "run":
            continue
        leak = _find_instruction_leak(eff.get("content"))
        if leak:
            return str(eff.get("path", "")), leak
        if eff.get("kind") in ("create", "modify"):
            p = Path(os.path.expanduser(str(eff.get("path", ""))))
            try:
                if p.is_file() and p.stat().st_size <= 1_000_000:
                    leak = _find_instruction_leak(p.read_text(encoding="utf-8"))
                    if leak:
                        return str(p), leak
            except (OSError, UnicodeDecodeError):
                continue
    return None


# --------------------------------------------------------------------- storage
def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            goal TEXT NOT NULL,
            state TEXT NOT NULL,               -- planning | running | paused | done | failed
            continue_on_failure INTEGER DEFAULT 0,
            plan_json TEXT,                    -- the validated decomposition
            failure_reason TEXT,
            report_json TEXT,                  -- written when the mission ends
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS mission_subtasks (
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            title TEXT NOT NULL,
            instruction TEXT NOT NULL,
            expected_effects TEXT DEFAULT '[]',  -- json: [{kind, path, content}]
            status TEXT NOT NULL,                -- pending | queued | working | waiting | done | failed | skipped
            task_id TEXT,                        -- the TaskQueue task doing this subtask
            claimed TEXT,                        -- what the agent reported (its words, not a verdict)
            verified_status TEXT,                -- verified | contradicted | failed | unverifiable
            evidence TEXT DEFAULT '[]',          -- json: what the disk check found
            started_at REAL,
            finished_at REAL
        )
        """
    )
    # Fix round 5: one subtask row per (mission, step), enforced by the
    # shared store itself. Two backend processes share this database
    # (the installed app and a dev instance), and a process running an
    # older build in memory does not implement the round 4 atomic plan
    # claim: in re-run 4 the stale process planned right past the claim
    # and inserted a second paraphrased plan, doubling two missions to
    # six rows. Code-level claims can only bind processes that run the
    # code; a trigger binds every writer of the store, including a
    # stale one, whose duplicate insert now aborts inside its own
    # planning call and leaves the winning plan's rows alone. The
    # trigger fires only on new inserts, so doubled rows a stale peer
    # already wrote into a legacy database do not block its creation.
    c.execute(
        """
        CREATE TRIGGER IF NOT EXISTS mission_subtasks_one_row_per_step
        BEFORE INSERT ON mission_subtasks
        WHEN EXISTS (SELECT 1 FROM mission_subtasks
                     WHERE mission_id = NEW.mission_id AND idx = NEW.idx)
        BEGIN
            SELECT RAISE(ABORT, 'a subtask row for this mission step already exists');
        END
        """
    )
    # Pause supervision stamps (fix round 5): when the current wait
    # began on the supervision clock, and how many automatic resumes
    # this subtask has already spent. Added the way the task queue
    # adds its own later columns, so existing databases pick them up.
    cols = [r[1] for r in c.execute("PRAGMA table_info(mission_subtasks)").fetchall()]
    if "paused_at" not in cols:
        c.execute("ALTER TABLE mission_subtasks ADD COLUMN paused_at REAL")
    if "pause_resume_attempts" not in cols:
        c.execute("ALTER TABLE mission_subtasks ADD COLUMN pause_resume_attempts INTEGER DEFAULT 0")
    # Capability contract (fix round 6, BOARD-001 Design 1): the step's
    # declared inputs and tools as validated at planning, and the
    # input-delivery read-back recorded at dispatch (one entry per
    # delivered input: source, ref, sha256 of the delivered text).
    if "contract" not in cols:
        c.execute("ALTER TABLE mission_subtasks ADD COLUMN contract TEXT DEFAULT '{}'")
    if "input_delivery" not in cols:
        c.execute("ALTER TABLE mission_subtasks ADD COLUMN input_delivery TEXT DEFAULT '[]'")
    c.commit()
    return c


def _mission_row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["continue_on_failure"] = bool(d.get("continue_on_failure"))
    for k in ("plan_json", "report_json"):
        if d.get(k):
            try:
                d[k.replace("_json", "")] = json.loads(d[k])
            except Exception:
                d[k.replace("_json", "")] = None
        else:
            d[k.replace("_json", "")] = None
        d.pop(k, None)
    return d


def _subtask_row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    for k in ("contract",):
        try:
            d[k] = json.loads(d.get(k) or "{}")
        except Exception:
            d[k] = {}
    try:
        d["input_delivery"] = json.loads(d.get("input_delivery") or "[]")
    except Exception:
        d["input_delivery"] = []
    for k in ("expected_effects", "evidence"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except Exception:
            d[k] = []
    return d


def _save_mission(mission_id: str, **fields: Any) -> None:
    # Observability (services/event_log.py): a plan landing or a state change is an
    # event; the prior state is read first when this save carries either.
    old_state: Optional[str] = None
    watch = ("state" in fields) or ("plan" in fields)
    if watch:
        c0 = _conn()
        r0 = c0.execute("SELECT state FROM missions WHERE id = ?", (mission_id,)).fetchone()
        c0.close()
        old_state = r0["state"] if r0 else None
    plan = fields.get("plan")
    for k in ("plan", "report"):
        if k in fields:
            fields[k + "_json"] = json.dumps(fields.pop(k)) if fields[k] is not None else None
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    c = _conn()
    c.execute(f"UPDATE missions SET {cols} WHERE id = ?", (*fields.values(), mission_id))
    c.commit()
    c.close()
    if watch:
        from app.services import event_log
        if isinstance(plan, dict):
            event_log.emit("mission.planned", actor="missions", ids={"mission_id": mission_id},
                           payload={"subtasks": len(plan.get("subtasks") or [])})
        if "state" in fields and fields["state"] != old_state:
            payload: Dict[str, Any] = {"from": old_state, "to": fields["state"]}
            if fields.get("failure_reason"):
                payload["failure_reason"] = fields["failure_reason"]
            event_log.emit("mission.status", actor="missions",
                           ids={"mission_id": mission_id}, payload=payload)


def _save_subtask(subtask_id: str, **fields: Any) -> None:
    # Observability: status changes, a pause clock starting, and a spent resume are
    # events; the prior row is read first when this save carries any of them.
    old: Optional[Dict[str, Any]] = None
    watch = ("status" in fields) or ("paused_at" in fields) or ("pause_resume_attempts" in fields)
    if watch:
        c0 = _conn()
        r0 = c0.execute("SELECT mission_id, idx, status, task_id, paused_at, pause_resume_attempts "
                        "FROM mission_subtasks WHERE id = ?", (subtask_id,)).fetchone()
        c0.close()
        old = dict(r0) if r0 else None
    for k in ("expected_effects", "evidence", "contract", "input_delivery"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    cols = ", ".join(f"{k} = ?" for k in fields)
    c = _conn()
    c.execute(f"UPDATE mission_subtasks SET {cols} WHERE id = ?", (*fields.values(), subtask_id))
    c.commit()
    c.close()
    if old is not None:
        from app.services import event_log
        ids = {"mission_id": old["mission_id"], "subtask_id": subtask_id}
        if old.get("task_id"):
            ids["task_id"] = old["task_id"]
        if "status" in fields and fields["status"] != old["status"]:
            payload: Dict[str, Any] = {"from": old["status"], "to": fields["status"], "idx": old["idx"]}
            if fields.get("verified_status"):
                payload["verified_status"] = fields["verified_status"]
            event_log.emit("subtask.status", actor="missions", ids=ids, payload=payload)
        if fields.get("paused_at") is not None and old.get("paused_at") is None:
            event_log.emit("subtask.wait", actor="missions", ids=ids,
                           payload={"idx": old["idx"], "paused_at": fields["paused_at"]})
        if (fields.get("pause_resume_attempts") or 0) > (old.get("pause_resume_attempts") or 0):
            event_log.emit("subtask.resumed", actor="missions", ids=ids,
                           payload={"idx": old["idx"], "attempts": fields["pause_resume_attempts"]})


def get_mission(mission_id: str) -> Optional[Dict[str, Any]]:
    c = _conn()
    r = c.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
    c.close()
    if not r:
        return None
    m = _mission_row(r)
    m["subtasks"] = list_subtasks(mission_id)
    done = sum(1 for s in m["subtasks"] if s["status"] == "done")
    verified = sum(1 for s in m["subtasks"] if s.get("verified_status") == "verified")
    m["progress"] = {"subtasks": len(m["subtasks"]), "done": done, "verified": verified}
    return m


def list_missions(limit: int = 50) -> List[Dict[str, Any]]:
    c = _conn()
    rows = c.execute("SELECT * FROM missions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [get_mission(r["id"]) for r in rows]


def list_subtasks(mission_id: str) -> List[Dict[str, Any]]:
    c = _conn()
    rows = c.execute("SELECT * FROM mission_subtasks WHERE mission_id = ? ORDER BY idx ASC",
                     (mission_id,)).fetchall()
    c.close()
    return [_subtask_row(r) for r in rows]


def _missions_needing_drive() -> List[str]:
    c = _conn()
    rows = c.execute("SELECT id FROM missions WHERE state IN ('planning', 'running', 'paused') "
                     "ORDER BY created_at ASC").fetchall()
    c.close()
    return [r["id"] for r in rows]


# --------------------------------------------------------------------- planning
PLAN_PROMPT = """You are the planning part of Xod, a local assistant. Someone gave Xod a job that takes several steps:
{goal}

Break it into a short list of subtasks the agent can do one at a time, in order. Each subtask must be checkable:
- "title": a few words.
- "instruction": exactly what to do, in one or two sentences.
- "expected_effects": the files this subtask will create, modify or delete, so the result can be checked on disk afterwards. Each effect is {{"kind": "create", "path": "/absolute/path", "content": null}} where kind is "create", "modify" or "delete", path is absolute or starts with ~/, and content is the exact file contents ONLY when the job dictates them word for word, otherwise null. An effect can instead prove that produced code works: {{"kind": "run", "command": "a single command line", "cwd": "/absolute/path", "expect_exit": 0}} where command is one plain command with no shell chaining, pipes or redirection, cwd is the directory the command runs from, and expect_exit (which defaults to 0) is the exit code that counts as success. If a subtask changes no files (looking something up, answering, deciding), expected_effects is [].
- "inputs": what this subtask must be given to work. Each input is {{"source": "goal"}} (the mission goal itself, which carries any evidence pack or brief the user supplied), {{"source": "prior_step", "ref": "1"}} (the reported result of an earlier step, by its number), or {{"source": "file", "ref": "/absolute/path"}} (a file whose contents the step needs). The goal is always delivered; declare prior_step or file inputs whenever the step's work depends on them. A step whose declared input cannot be delivered never starts.
- "tools": the tools this subtask needs, by name (for example "computer_write_file" for a step that writes a file, "computer_read_file" for one that only reads). A step whose declared file effects need a tool its "tools" list leaves out never starts.
- When a subtask creates a test file or an executable artifact, give it a "run" effect that runs what it made: run the test file with the project's test runner, or invoke the artifact harmlessly, with cwd set to the directory the subtask creates the file in. Creating a file is not proof it works; the run is the proof.

Between 2 and 8 subtasks. Later subtasks may rely on earlier ones; nothing runs at the same time.
Reply with JSON only: {{"subtasks": [{{"title": "...", "instruction": "...", "expected_effects": [], "inputs": [{{"source": "goal"}}], "tools": []}}]}}"""


class PlanRejected(Exception):
    """The model could not produce a valid plan in two attempts."""

    def __init__(self, problems: List[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def validate_plan(raw: Any) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Strict schema check for a decomposition. Returns (plan, []) or (None, problems)."""
    problems: List[str] = []
    if not isinstance(raw, dict):
        return None, ["the reply was not a JSON object with a subtasks list"]
    subs = raw.get("subtasks")
    if not isinstance(subs, list) or not subs:
        return None, ["subtasks must be a non-empty list"]
    if len(subs) > 12:
        problems.append(f"too many subtasks ({len(subs)}); the limit is 12")
    out: List[Dict[str, Any]] = []
    for i, s in enumerate(subs[:12]):
        where = f"subtask {i + 1}"
        if not isinstance(s, dict):
            problems.append(f"{where}: must be an object")
            continue
        title = s.get("title")
        if not isinstance(title, str) or not title.strip():
            problems.append(f"{where}: title must be a non-empty string")
            title = ""
        elif len(title) > 120:
            problems.append(f"{where}: title is over 120 characters")
        instruction = s.get("instruction")
        if not isinstance(instruction, str) or not instruction.strip():
            problems.append(f"{where}: instruction must be a non-empty string")
            instruction = ""
        elif len(instruction) > 4000:
            problems.append(f"{where}: instruction is over 4000 characters")
        effects = s.get("expected_effects")
        if not isinstance(effects, list):
            problems.append(f"{where}: expected_effects must be a list (use [] when no files change)")
            effects = []
        elif len(effects) > 10:
            problems.append(f"{where}: too many expected_effects ({len(effects)}); the limit is 10")
        clean_effects: List[Dict[str, Any]] = []
        for j, e in enumerate(effects[:10]):
            ewhere = f"{where} effect {j + 1}"
            if not isinstance(e, dict):
                problems.append(f"{ewhere}: must be an object")
                continue
            kind = e.get("kind")
            if kind is None and e.get("type") == "run":
                kind = "run"
            if kind == "run":
                command = e.get("command")
                if not isinstance(command, str) or not command.strip():
                    problems.append(f"{ewhere}: command must be a non-empty string")
                    continue
                command = command.strip()
                if "\n" in command or "\r" in command:
                    problems.append(f"{ewhere}: command must be a single line")
                    continue
                cwd = e.get("cwd")
                if not isinstance(cwd, str) or not cwd.strip():
                    problems.append(f"{ewhere}: cwd must be a non-empty string")
                    continue
                cwd = cwd.strip()
                if not _looks_absolute(cwd):
                    problems.append(f"{ewhere}: cwd must be absolute or start with ~/")
                    continue
                if ".." in _path_parts(cwd):
                    problems.append(f"{ewhere}: cwd must not contain ..")
                    continue
                expect_exit = e.get("expect_exit", 0)
                if isinstance(expect_exit, bool) or not isinstance(expect_exit, int):
                    problems.append(f"{ewhere}: expect_exit must be an integer")
                    continue
                clean_effects.append({"kind": "run", "command": command, "cwd": cwd,
                                      "expect_exit": expect_exit})
                continue
            if kind not in _EFFECT_KINDS:
                problems.append(f"{ewhere}: kind must be one of create, modify, delete")
                continue
            path = e.get("path")
            if not isinstance(path, str) or not path.strip():
                problems.append(f"{ewhere}: path must be a non-empty string")
                continue
            path = path.strip()
            if not _looks_absolute(path):
                problems.append(f"{ewhere}: path must be absolute or start with ~/")
                continue
            if ".." in _path_parts(path):
                problems.append(f"{ewhere}: path must not contain ..")
                continue
            content = e.get("content")
            if kind == "delete" and content is not None:
                problems.append(f"{ewhere}: a delete effect cannot carry content")
                continue
            if content is not None and not isinstance(content, str):
                problems.append(f"{ewhere}: content must be a string or null")
                continue
            if isinstance(content, str) and len(content) > 20000:
                problems.append(f"{ewhere}: content is over 20000 characters")
                continue
            if isinstance(content, str) and content:
                leak = _find_instruction_leak(content)
                if leak:
                    problems.append(f"{ewhere}: content is a planner instruction, not the "
                                    f"requested artifact: \"{leak}\"")
                    continue
            clean_effects.append({"kind": kind, "path": path, "content": content})
        # Capability contract (fix round 6, BOARD-001 Design 1): the
        # step declares the inputs it must be given and the tools it
        # needs. Both are validated here so a plan that cannot be
        # honoured is re-asked at planning, and what survives is what
        # assembly enforces at dispatch.
        clean_inputs: List[Dict[str, Any]] = []
        raw_inputs = s.get("inputs")
        if raw_inputs is not None:
            if not isinstance(raw_inputs, list) or len(raw_inputs) > 6:
                problems.append(f"{where}: inputs must be a list of at most 6 declared inputs")
                raw_inputs = []
            for j, inp in enumerate(raw_inputs):
                iwhere = f"{where} input {j + 1}"
                if not isinstance(inp, dict):
                    problems.append(f"{iwhere}: must be an object")
                    continue
                source = inp.get("source")
                if source not in ("goal", "prior_step", "file"):
                    problems.append(f"{iwhere}: source must be one of goal, prior_step, file")
                    continue
                if source == "goal":
                    clean_inputs.append({"source": "goal"})
                    continue
                ref = inp.get("ref")
                if not isinstance(ref, str) or not ref.strip():
                    problems.append(f"{iwhere}: a {source} input needs a ref")
                    continue
                ref = ref.strip()
                if source == "prior_step":
                    if not ref.isdigit() or not (1 <= int(ref) <= i):
                        problems.append(f"{iwhere}: ref must be the number of an earlier step")
                        continue
                    clean_inputs.append({"source": "prior_step", "ref": ref})
                else:
                    if not _looks_absolute(ref) or ".." in _path_parts(ref):
                        problems.append(f"{iwhere}: ref must be an absolute path or start with ~/")
                        continue
                    clean_inputs.append({"source": "file", "ref": ref})
        clean_tools: Optional[List[str]] = None
        raw_tools = s.get("tools")
        if raw_tools is not None:
            if (not isinstance(raw_tools, list) or len(raw_tools) > 24
                    or any(not isinstance(t, str) or not t.strip() for t in raw_tools)):
                problems.append(f"{where}: tools must be a list of tool names")
            else:
                clean_tools = [t.strip() for t in raw_tools]
        out.append({"title": str(title).strip(), "instruction": str(instruction).strip(),
                    "expected_effects": clean_effects,
                    "inputs": clean_inputs, "tools": clean_tools})
    if problems:
        return None, problems
    # A merged double plan (the baseline's interleaved 1,1,2,2,3,3) repeats
    # steps exactly: same title, instruction and effects. Collapse exact
    # repeats to their first occurrence; steps that differ in any field
    # are real steps and stay.
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for s in out:
        key = json.dumps(s, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    return {"subtasks": deduped}, []


def _looks_absolute(path: str) -> bool:
    """Absolute for plan purposes: POSIX root, ~/ home form, or a Windows
    drive-letter path (C:\\ or C:/). Before fix round 6 the checks below
    accepted only the first two, so a plan naming a native Windows path
    was rejected at planning on the Windows build."""
    return (path.startswith("/") or path.startswith("~/")
            or bool(re.match(r"^[A-Za-z]:[\\/]", path)))


def _path_parts(path: str) -> List[str]:
    return [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]


async def _default_ask(prompt: str) -> str:
    """One plain question to the configured model; returns its text."""
    from app.services.llm import llm_service, JSON_MIN_TOKENS
    reply = await llm_service.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2, max_tokens=max(JSON_MIN_TOKENS, 1400), think=False, task="mission_plan")
    return reply.get("content", "") or ""


async def decompose(goal: str, ask: Callable[[str], Any]) -> Dict[str, Any]:
    """Asks the model for a plan; an invalid plan is re-asked once with the
    problems named, then rejected. Never falls back to an invented plan."""
    from app.services.llm import parse_llm_json
    prompt = PLAN_PROMPT.format(goal=goal)
    problems: List[str] = []
    for _attempt in (1, 2):
        raw = parse_llm_json(await ask(prompt))
        plan, problems = validate_plan(raw)
        if plan is not None:
            return plan
        prompt = (PLAN_PROMPT.format(goal=goal)
                  + "\n\nYour previous answer was rejected for these reasons:\n- "
                  + "\n- ".join(problems)
                  + "\nAnswer again with JSON only, fixing every one of them.")
    raise PlanRejected(problems)


# --------------------------------------------------------------------- subtask rendering
def render_request(subtask: Dict[str, Any]) -> str:
    """The task text for one subtask. Declared file effects are spelled out in
    the words the queue's file-effect check parses (a path plus a create, write
    or delete verb in the same sentence), so the plan's own claims are what get
    checked on disk. Exact contents are dictated in the check's exact-text form
    only when the content is a single line; longer contents ride on the
    instruction and only the file's existence is enforced, which is the honest
    limit of the check's vocabulary."""
    lines = [subtask["instruction"].rstrip()]
    for eff in subtask.get("expected_effects") or []:
        if eff["kind"] == "run":
            cwd = os.path.expanduser(eff["cwd"])
            lines.append(f"Run exactly this command from the directory {cwd} "
                         f"and require exit code {eff.get('expect_exit', 0)}: {eff['command']}")
            continue
        p = os.path.expanduser(eff["path"])
        content = eff.get("content")
        if eff["kind"] == "delete":
            lines.append(f"Delete the file at {p}.")
        elif content is not None and content == content.strip() and "\n" not in content:
            # The exact-text form carries one line byte for byte, nothing
            # more: content with leading or trailing whitespace (a trailing
            # newline above all) would be silently reworded by the strip,
            # and the queue would then enforce words the plan never said
            # (mission-2f2579b68a). Such content rides on the instruction
            # below instead, and only the file's existence is enforced.
            verb = "Create" if eff["kind"] == "create" else "Write"
            lines.append(f"{verb} the file at {p} with exact text: {content.strip()}")
        elif eff["kind"] == "create":
            words = f"{subtask.get('title', '')} {subtask['instruction']}".lower()
            if re.search(r"\b(directory|folder)\b", words):
                lines.append(f"Create the directory at {p}.")
            else:
                lines.append(f"Create the file at {p}." + (f" Its contents should be:\n{content}" if content else ""))
        else:
            lines.append(f"Write the updated file at {p}." + (f" Its contents should be:\n{content}" if content else ""))
    return "\n".join(lines)


# ------------------------------------------------------- subtask assembly
# Fix round 3 (2026-10-31, re-run 2 T1B): the research steps finished,
# then the write step was invoked with no content at all
# ("computer_write_file needs content. Nothing was done.") and the file
# never landed. Two assembly defects in this module fed that: each
# subtask's task was assembled from that subtask alone, so content an
# earlier step produced never reached the step meant to write it; and
# nothing checked the assembled payload of a write step before a task
# (and its tool calls) was created from it. The helpers below are that
# assembly: the request a write subtask runs with, and the guard that
# stops an empty one before any tool can be invoked with it.

def _empty_declared_content_problem(subtask: Dict[str, Any]) -> Optional[str]:
    """A write subtask whose plan declares content for a file but whose
    assembled content is empty, or None. Declared content is a string
    the planner supplied for a create or modify effect; an empty or
    whitespace-only string is a plan/execution error, not a file: left
    through, it renders as a create with no contents and reaches the
    file tool as an empty write. Content declared as null is not this:
    null means the executing step composes the file from its
    instruction and the carried results, which is the normal shape."""
    for eff in subtask.get("expected_effects") or []:
        if eff.get("kind") not in ("create", "modify"):
            continue
        content = eff.get("content")
        if isinstance(content, str) and not content.strip():
            return (f"Assembly stopped this step before any tool ran: subtask "
                    f"\"{subtask.get('title', '')}\" declares content for "
                    f"{eff.get('path', '')} but the assembled content is empty. "
                    f"A write with empty content is a plan/execution error, "
                    f"not a file, so no write was attempted.")
    return None


# ------------------------------------------------------- write-tool assembly
# Fix round 4 (2026-10-31, re-run 3 T1D): the write subtask reported it
# had no tools to create files, and the file never landed, for the
# fourth run in a row. The trace: MissionRunner creates a subtask's
# task with queue.create(request, title) and nothing else; no tool set
# is specified, checked, or guaranteed anywhere on the path from the
# mission to the researcher or the worker lane, whose tool sets are
# chosen per request by keyword affinity (app/agent/tools.py
# select_tools_for_query caps its answer at 16 and, before this round,
# its always-on foundational set carried computer_edit_file but not
# computer_write_file, so a write phrased without the lucky keywords,
# "Put the result in /abs/out/result.json", was offered no write tool
# at all). A plain task and a mission write step take the same path,
# so the guarantee has to be made where the mission assembles the
# step: before a task is created, the tools that execution can offer
# for the assembled request are checked for a tool that can perform
# each declared file effect, and a step that cannot be equipped fails
# here, at assembly, naming the problem, instead of being discovered
# unequipped by the subtask itself.

_FILE_WRITE_TOOLS = ("computer_write_file", "fs_write_workspace_file")
_DIR_WRITE_TOOLS = ("computer_manage_file", "fs_create_folder", "computer_write_file")
_DELETE_TOOLS = ("computer_manage_file",)


def _available_tool_names(request: str) -> List[str]:
    """The tool names the execution layers can offer for this request:
    the same selector the researcher's follow-up and repair loops draw
    their candidate tools from, at the same cap."""
    try:
        from app.agent.tools import select_tools_for_query
        return [t["function"]["name"]
                for t in select_tools_for_query(query=request or "", max_tools=16)]
    except Exception:
        pass
    # The selector itself could not run (it reaches into the researcher
    # for its routing regexes, so a replaced or broken researcher breaks
    # it without saying anything about tools). Fall back to the tool
    # catalog itself, pack-filtered: a tool that exists and is switched
    # on can be equipped (the deliberator's tool list is drawn from the
    # same catalog), and only a catalog with no usable file tool at all
    # leaves a write step unequippable.
    try:
        from app.agent.tools import TOOL_DEFINITIONS
        try:
            from app.services import packs
            return [t["function"]["name"] for t in TOOL_DEFINITIONS
                    if packs.tool_allowed(t["function"]["name"])]
        except Exception:
            return [t["function"]["name"] for t in TOOL_DEFINITIONS]
    except Exception:
        return []


def _write_tool_problem(subtask: Dict[str, Any], request: str) -> Optional[str]:
    """A write step whose declared file effects no available file tool
    can perform, or None. One problem per subtask is enough: assembly
    stops at the first unequippable effect and names it."""
    effects = [e for e in subtask.get("expected_effects") or []
               if e.get("kind") in ("create", "modify", "delete")]
    if not effects:
        return None
    names = set(_available_tool_names(request))
    for eff in effects:
        if eff.get("kind") == "delete":
            needed, what = _DELETE_TOOLS, "delete"
        elif eff.get("kind") == "create" and _declares_directory(subtask):
            needed, what = _DIR_WRITE_TOOLS, "create the directory"
        else:
            needed, what = _FILE_WRITE_TOOLS, "write the file"
        if not any(n in names for n in needed):
            return (f"Assembly stopped this step before any task was created: subtask "
                    f"\"{subtask.get('title', '')}\" must {what} {eff.get('path', '')}, "
                    f"but none of the file tools that could do it "
                    f"({', '.join(needed)}) is available for this step's assembled "
                    f"request. A write step that cannot be equipped with file tools "
                    f"fails here, at assembly, not inside the subtask.")
    return None


def _contract_tool_problem(subtask: Dict[str, Any]) -> Optional[str]:
    """Capability contract (fix round 6, BOARD-001 Design 1): when the
    plan declared a tools list for this step, every declared file
    effect must be performable by a tool ON that list. A step whose
    contract excludes the tool its effect needs fails at assembly,
    naming the gap, exactly like a step no available flow can equip
    (_write_tool_problem, the fix round 4 check this generalises)."""
    contract = subtask.get("contract") or {}
    tools = contract.get("tools")
    if tools is None:
        return None
    names = set(tools)
    for eff in subtask.get("expected_effects") or []:
        if eff.get("kind") == "delete":
            needed, what = _DELETE_TOOLS, "delete"
        elif eff.get("kind") == "create" and _declares_directory(subtask):
            needed, what = _DIR_WRITE_TOOLS, "create the directory"
        elif eff.get("kind") in ("create", "modify"):
            needed, what = _FILE_WRITE_TOOLS, "write the file"
        else:
            continue
        if not any(n in names for n in needed):
            return (f"Assembly stopped this step before any task was created: subtask "
                    f"\"{subtask.get('title', '')}\" must {what} {eff.get('path', '')}, "
                    f"but its declared capability contract (tools: "
                    f"{', '.join(sorted(names)) or 'none'}) includes none of the tools "
                    f"that could do it ({', '.join(needed)}). A step is dispatched only "
                    f"to a flow its contract equips; the contract and the effects must "
                    f"be fixed in the plan, not worked around at run time.")
    return None


def assemble_step(mission_id: str, subtask: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    """Assembles one subtask's task text under its capability contract
    (fix round 6, BOARD-001 Design 1). Returns (request, delivery,
    problem): delivery is the input-delivery read-back, one record per
    declared input actually placed into the request (source, ref,
    sha256 of the delivered text); problem is a typed assembly failure
    when a declared input cannot be delivered, and no task may be
    created from it.

    Every subtask implicitly declares the mission goal as an input:
    the goal is where a brief or evidence pack the user supplied
    lives, and before this round it reached NO subtask at all (the
    BOARD-001 mission's workers assessed an evidence pack they had
    never been given, and said so). Declared prior_step and file
    inputs are delivered the same way, to every step that declares
    them, not only to write steps. Write steps additionally keep the
    fix round 4 behaviour: the reported results of earlier finished
    steps ride along as the content the write exists to land."""
    mission = get_mission(mission_id)
    goal = (mission or {}).get("goal") or ""
    subs = list_subtasks(mission_id)
    contract = subtask.get("contract") or {}
    declared: List[Dict[str, Any]] = list(contract.get("inputs") or [])
    if not any(d.get("source") == "goal" for d in declared):
        declared = [{"source": "goal"}] + declared
    sections: List[Tuple[str, str]] = []
    delivery: List[Dict[str, Any]] = []
    declared_prior_refs = set()
    for d in declared:
        source = d.get("source")
        if source == "goal":
            label, text = "Mission goal (declared input)", goal
        elif source == "prior_step":
            ref = str(d.get("ref") or "")
            declared_prior_refs.add(ref)
            prior = next((s for s in subs if s["idx"] == int(ref) - 1), None) if ref.isdigit() else None
            if prior is None or prior["status"] != "done" or not (prior.get("claimed") or "").strip():
                return (render_request(subtask), [],
                        f"Assembly stopped this step before any task was created: subtask "
                        f"\"{subtask.get('title', '')}\" declares step {ref}'s result as an "
                        f"input, but that step has no finished result to deliver. A step "
                        f"whose declared inputs cannot be delivered does not start.")
            label = f"Result of step {ref} ({prior['title']}) (declared input)"
            text = prior["claimed"].strip()
        elif source == "file":
            ref = str(d.get("ref") or "")
            try:
                text = Path(os.path.expanduser(ref)).read_text(
                    encoding="utf-8", errors="replace")[:8000]
            except OSError:
                return (render_request(subtask), [],
                        f"Assembly stopped this step before any task was created: subtask "
                        f"\"{subtask.get('title', '')}\" declares the file {ref} as an "
                        f"input, but it could not be read. A step whose declared inputs "
                        f"cannot be delivered does not start.")
            label = f"Contents of {ref} (declared input)"
        else:
            continue
        delivery.append({"source": source, "ref": d.get("ref"),
                         "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                         "chars": len(text)})
        sections.append((label, text))
    parts = [render_request(subtask)]
    tools = contract.get("tools")
    if tools is not None:
        parts += ["", "Declared tools for this step (its capability contract): "
                  + (", ".join(tools) if tools else "none")]
    for label, text in sections:
        parts += ["", f"{label}:", text]
    # Input-delivery read-back: every delivered input's text must be
    # present in the assembled request, verified here at dispatch,
    # not assumed from the assembly code above.
    request = "\n".join(parts)
    for (label, text), rec in zip(sections, delivery):
        if text and text not in request:
            return (request, [],
                    f"Assembly stopped this step before any task was created: the "
                    f"declared input \"{label}\" was assembled but is missing from "
                    f"the request at read-back. The step does not start on an "
                    f"undelivered input.")
    if any(e.get("kind") in ("create", "modify")
           for e in subtask.get("expected_effects") or []):
        prior = [s for s in subs
                 if s["idx"] < subtask["idx"] and s["status"] == "done"
                 and (s.get("claimed") or "").strip()
                 and str(s["idx"] + 1) not in declared_prior_refs]
        if prior:
            lines = [request, "",
                     "Results from the earlier steps of this mission, for use in this step:"]
            for s in prior:
                lines.append(f"- Step {s['idx'] + 1} ({s['title']}): "
                             f"{s['claimed'].strip()[:2000]}")
            request = "\n".join(lines)
    return request, delivery, None


def assemble_request(mission_id: str, subtask: Dict[str, Any]) -> str:
    """The task text for one subtask, assembled at dispatch time. Kept
    as the text-only view of assemble_step (fix round 6) for callers
    that render a request without dispatching it."""
    request, _delivery, _problem = assemble_step(mission_id, subtask)
    return request


def _outcome_from_task(task: Optional[Dict[str, Any]], subtask: Dict[str, Any]) -> Dict[str, Any]:
    """What the queue decided about a finished subtask task, kept separate from
    what the agent claimed. The verdict comes from the task's final state,
    which the queue only reaches after its file-effect check has run."""
    if task is None:
        return {"status": "failed", "claimed": "", "verified_status": "unverifiable",
                "evidence": ["The task record for this step is gone, so its result cannot be checked."]}
    result = task.get("result") or ""
    if task["status"] == "done":
        effects = subtask.get("expected_effects") or []
        files = [e for e in effects if e.get("kind") != "run"]
        runs = [e for e in effects if e.get("kind") == "run"]
        # Verdict integrity: a finished task is not automatically a verified
        # subtask. The subtask's own report, its deliverable content and its
        # declared run proof are all checked before "verified" is said.
        blocked = _blocked_signal(result)
        if blocked:
            return {"status": "blocked", "claimed": result, "verified_status": "blocked",
                    "evidence": [f"The step reported itself blocked: \"{blocked}\" "
                                 "A step that could not run for a missing input or a "
                                 "missing tool is recorded blocked, not done, and no "
                                 "other flow is substituted for it."]}
        signal = _incompletion_signal(result)
        if signal:
            return {"status": "failed", "claimed": result, "verified_status": "contradicted",
                    "evidence": [f"The step's own report admits it did not finish: \"{signal}\" "
                                 "A report of incompletion cannot be verified."]}
        leak_hit = _content_leak_for_subtask(subtask)
        if leak_hit:
            leak_path, leak = leak_hit
            return {"status": "failed", "claimed": result, "verified_status": "contradicted",
                    "evidence": [f"Checked on disk: {leak_path} contains planner instruction "
                                 f"text, not the requested artifact: \"{leak}\""]}
        # Lane B at the verdict layer: the queue's finish-time check
        # established existence, dictated content and runs; what it
        # never produced is type evidence, so the fresh check here is
        # scoped to types only (types_only) rather than re-trying the
        # queue's verdict against a disk that parallel steps may
        # still be landing on.
        fresh = _fresh_effect_problems(subtask, types_only=True)
        if fresh:
            return {"status": "failed", "claimed": result, "verified_status": "contradicted",
                    "evidence": [f"Checked on disk: {p}" for p in fresh]}
        evidence: List[str] = []
        if files:
            dictated = [e for e in files
                        if e.get("content") is not None and "\n" not in str(e["content"]).strip()]
            if len(dictated) == len(files):
                checked = "every declared effect was true on disk, including the dictated text"
            elif dictated:
                checked = ("the declared effects were true on disk; text matched exactly where "
                           "the plan dictated it, and the other contents were not checked")
            else:
                checked = ("the declared paths existed on disk when the task finished; "
                           "their contents were not checked against the instruction")
            evidence.append(f"The queue's file-effect check passed when the task finished: {checked}.")
        for e in runs:
            problem = _run_effect_problem(e)
            if problem:
                return {"status": "failed", "claimed": result, "verified_status": "contradicted",
                        "evidence": [f"Run check: {problem}"]}
            cwd = os.path.expanduser(str(e.get("cwd", "")))
            evidence.append(f"The queue also ran the declared check: '{e.get('command', '')}' "
                            f"from {cwd} exited with code {e.get('expect_exit', 0)}, "
                            "the code the plan required.")
            evidence.append(f"The mission re-ran the declared check itself: '{e.get('command', '')}' "
                            f"from {cwd} exited with code {e.get('expect_exit', 0)}, "
                            "the code the plan required.")
        if not evidence:
            # Positive-evidence standard (fix round 6, Lane D): a step
            # with no file effects verifies only when its task carries
            # decisive evidence the verdict can inspect. Nothing on
            # disk contradicting the report is not verification; the
            # step closes done on the agent's word, marked
            # unverifiable, and the report says so.
            if _has_decisive_evidence(task):
                return {"status": "done", "claimed": result, "verified_status": "verified",
                        "evidence": ["The step's task carried decisive evidence for its "
                                     "result (a tool-result payload the verdict layer "
                                     "can inspect), and nothing on disk contradicts it."]}
            return {"status": "done", "claimed": result, "verified_status": "unverifiable",
                    "evidence": ["This step claimed no file effects and its task carried "
                                 "no decisive evidence, so nothing positively verified "
                                 "the report; it is recorded done on the agent's word "
                                 "alone. Absence of contradiction is not verification."]}
        return {"status": "done", "claimed": result, "verified_status": "verified", "evidence": evidence}
    if task["status"] == "cancelled":
        return {"status": "failed", "claimed": result, "verified_status": "failed",
                "evidence": ["The task was cancelled before it finished."]}
    marker = "Not finished:"
    if marker in result:
        claimed, _, tail = result.partition(marker)
        problems = [p.strip() for p in tail.strip().split(";") if p.strip()]
        if subtask.get("expected_effects"):
            # Fix round 4: the problems above were recorded when the task's
            # own check ran. By verdict time they can be stale (re-run 3
            # T1A: step 2 was contradicted for a sample.txt that a doubled
            # plan's parallel step landed afterwards), so the verdict is
            # composed from a fresh check of the declared effects, never
            # from the recorded text. The step's own admissions and any
            # planner instruction text on disk are not timing artifacts
            # and still block verification, checked fresh here as well.
            blocked = _blocked_signal(claimed)
            if blocked:
                return {"status": "blocked", "claimed": claimed.strip(),
                        "verified_status": "blocked",
                        "evidence": [f"The step reported itself blocked: \"{blocked}\" "
                                     "A step that could not run for a missing input or a "
                                     "missing tool is recorded blocked, not done."]}
            signal = _incompletion_signal(claimed.strip())
            if signal:
                return {"status": "failed", "claimed": claimed.strip(),
                        "verified_status": "contradicted",
                        "evidence": [f"The step's own report admits it did not finish: \"{signal}\" "
                                     "A report of incompletion cannot be verified."]}
            leak_hit = _content_leak_for_subtask(subtask)
            if leak_hit:
                leak_path, leak = leak_hit
                return {"status": "failed", "claimed": claimed.strip(),
                        "verified_status": "contradicted",
                        "evidence": [f"Checked on disk: {leak_path} contains planner instruction "
                                     f"text, not the requested artifact: \"{leak}\""]}
            fresh = _fresh_effect_problems(subtask)
            if fresh:
                return {"status": "failed", "claimed": claimed.strip(),
                        "verified_status": "contradicted",
                        "evidence": [f"Checked on disk: {p}" for p in fresh]}
            return {"status": "done", "claimed": claimed.strip(),
                    "verified_status": "verified",
                    "evidence": ["A fresh check at verdict time found every declared effect "
                                 "true on disk. The problem the task recorded when it "
                                 "finished no longer held (the work landed after that "
                                 "check ran), so it is not carried into the verdict."]}
        blocked = _blocked_signal(claimed)
        if blocked:
            return {"status": "blocked", "claimed": claimed.strip(),
                    "verified_status": "blocked",
                    "evidence": [f"The step reported itself blocked: \"{blocked}\" "
                                 "A step that could not run for a missing input or a "
                                 "missing tool is recorded blocked, not done."]}
        return {"status": "failed", "claimed": claimed.strip(),
                "verified_status": "contradicted",
                "evidence": [f"Checked on disk: {p}" for p in problems]
                            or ["Checked on disk: the claimed effects were not true."]}
    return {"status": "failed", "claimed": result, "verified_status": "failed",
            "evidence": ["The task failed before its result could be verified: "
                         + (result.strip()[-300:] or "no report was left")]}


# --------------------------------------------------------------------- usage (Lane 1 seam)
def _usage_section(mission_id: str) -> Dict[str, Any]:
    """Reads token/cost totals for a mission from a usage ledger, if one is
    present at runtime. Lane 1 builds that ledger separately; until it lands,
    and whenever it cannot answer, the honest answer is that usage is not
    tracked in this build. This module never invents numbers."""
    for module_name, func_name in (("app.services.usage_ledger", "mission_totals"),
                                   ("app.services.agent_usage", "mission_totals")):
        try:
            import importlib
            fn = getattr(importlib.import_module(module_name), func_name, None)
            if callable(fn):
                totals = fn(mission_id)
                if isinstance(totals, dict) and totals:
                    return {"status": "tracked", **totals}
        except Exception:
            continue
    return {"status": "not tracked in this build"}


# --------------------------------------------------------------------- the runner
class MissionRunner:
    """Drives missions through the TaskQueue, one subtask at a time.

    Everything it needs is in the database, so a fresh runner over the same
    database (an app restart) picks a mission up exactly where it stopped.
    The queue and the model are injectable so tests can fake both."""

    def __init__(self, queue: Any = None, ask: Optional[Callable] = None, poll_sec: float = 1.0,
                 pause_threshold_sec: float = PAUSE_WEDGE_THRESHOLD_SEC,
                 pause_resume_window_sec: float = PAUSE_RESUME_WINDOW_SEC,
                 clock: Optional[Callable[[], float]] = None):
        self._queue = queue
        self._ask = ask or _default_ask
        self.poll_sec = poll_sec
        # Pause supervision knobs (fix round 5): how long a subtask's
        # task may sit waiting before the one automatic resume, how
        # long a post-resume wait may sit before the honest failure,
        # and the clock both are measured on. Injectable so tests can
        # run the wedge on a short fake clock.
        self.pause_threshold_sec = pause_threshold_sec
        self.pause_resume_window_sec = pause_resume_window_sec
        self.clock = clock

    def _now(self) -> float:
        return self.clock() if self.clock is not None else time.time()

    @property
    def queue(self) -> Any:
        if self._queue is None:
            from app.services.task_queue import task_queue
            self._queue = task_queue
        return self._queue

    # ------------------------------------------------------------ planning
    async def _plan(self, mission: Dict[str, Any]) -> bool:
        """Fills in the plan and subtask rows. False means the mission failed."""
        if mission.get("plan"):
            return True
        fresh = get_mission(mission["id"])
        if fresh and fresh.get("plan"):
            return True   # another driver planned it while this one was starting
        try:
            # Planning calls are this mission's own spend: stamp the activity
            # origin so the usage ledger records them under the mission's tag
            # (subtask calls are stamped by the queue with their task id).
            from app.services import activity
            _origin = activity.origin.set(f"mission:{mission['id']}")
            try:
                plan = await decompose(mission["goal"], self._ask)
            finally:
                activity.origin.reset(_origin)
        except PlanRejected as e:
            _save_mission(mission["id"], state="failed", finished_at=time.time(),
                          failure_reason="The plan could not be made valid: " + "; ".join(e.problems))
            return False
        except Exception as e:
            logger.exception(f"Mission {mission['id']}: planning failed")
            _save_mission(mission["id"], state="failed", finished_at=time.time(),
                          failure_reason=f"The planning step itself failed: {e}")
            return False
        now = time.time()
        c = _conn()
        # Fix round 4: the plan is claimed with one conditional update, so
        # of two drivers planning at once (two driver loops can share this
        # database: the installed app and a dev instance both scan for
        # missions to drive, each with its own in-memory _driving set) only
        # the first inserts subtask rows. Before this, both decomposed,
        # both inserted, and the mission ran a doubled plan of interleaved
        # paraphrased copies (re-run 3: six subtask rows, idx 0,0,1,1,2,2,
        # for a three step plan stored once in plan_json).
        cur = c.execute("UPDATE missions SET plan_json = ?, state = 'running', started_at = ?, updated_at = ? "
                        "WHERE id = ? AND plan_json IS NULL",
                        (json.dumps(plan), now, now, mission["id"]))
        if cur.rowcount == 0:
            c.commit()
            c.close()
            return True   # another driver won the claim; its plan and rows stand
        for i, s in enumerate(plan["subtasks"]):
            c.execute("INSERT INTO mission_subtasks (id, mission_id, idx, title, instruction, "
                      "expected_effects, contract, status) VALUES (?,?,?,?,?,?,?,?)",
                      (f"msub-{uuid.uuid4().hex[:10]}", mission["id"], i, s["title"], s["instruction"],
                       json.dumps(s["expected_effects"]),
                       json.dumps({"inputs": s.get("inputs") or [], "tools": s.get("tools")}),
                       "pending"))
        c.commit()
        c.close()
        return True

    # ------------------------------------------------------------ execution
    async def drive(self, mission_id: str) -> None:
        """Runs one mission as far as it can go: plan it, then work through
        its subtasks in order until it finishes, fails, or a subtask waits
        on the user (the mission pauses and a later drive call resumes it)."""
        mission = get_mission(mission_id)
        if not mission or mission["state"] in ("done", "failed"):
            return
        if mission["state"] == "planning" or not mission.get("plan"):
            if not await self._plan(mission):
                return
            mission = get_mission(mission_id)
        if mission["state"] == "paused":
            _save_mission(mission_id, state="running")
        while True:
            subs = list_subtasks(mission_id)
            current = next((s for s in subs if s["status"] not in ("done", "failed", "skipped", "blocked")), None)
            if current is None:
                self._finish(mission_id)
                return
            if not await self._run_subtask(mission_id, current):
                return   # paused on a waiting task; a later drive resumes

    async def _run_subtask(self, mission_id: str, sub: Dict[str, Any]) -> bool:
        """Works one subtask to a recorded outcome. False means the mission
        paused because the subtask's task is waiting on the user."""
        # Fix round 4: work from the stored row, not the caller's snapshot.
        # Two drivers can hold snapshots of the same subtask at once; the
        # stored row is the one that knows whether a task already exists
        # (or the step already has an outcome), and dispatching from a
        # stale snapshot is how a doubled plan dispatched steps twice.
        stored = next((s for s in list_subtasks(mission_id) if s["id"] == sub["id"]), None)
        if stored is not None:
            if stored["status"] in ("done", "failed", "skipped", "blocked"):
                return True   # another driver already recorded this step's outcome
            sub = stored
        if not sub.get("task_id"):
            request, delivery, input_problem = assemble_step(mission_id, sub)
            problem = (_empty_declared_content_problem(sub)
                       or _contract_tool_problem(sub)
                       or _write_tool_problem(sub, request)
                       or input_problem)
            if problem:
                # Assembly-time failure: the subtask is recorded failed
                # with the problem as its evidence and no task is ever
                # created, so the bad write cannot reach a tool. The
                # outcome flows through the same recording and
                # stop-on-failure path as a task outcome below.
                outcome = {"status": "failed", "claimed": "",
                           "verified_status": "contradicted",
                           "evidence": [problem]}
                _save_subtask(sub["id"], status=outcome["status"],
                              claimed=outcome["claimed"],
                              verified_status=outcome["verified_status"],
                              evidence=outcome["evidence"],
                              finished_at=time.time())
                mission = get_mission(mission_id)
                if not mission["continue_on_failure"]:
                    for rest in list_subtasks(mission_id):
                        if rest["status"] in ("pending", "queued"):
                            _save_subtask(rest["id"], status="skipped")
                    self._finish(mission_id,
                                 reason=f"Step {sub['idx'] + 1} ({sub['title']}) failed: "
                                        + outcome["evidence"][0])
                return True
            task = self.queue.create(request,
                                     title=f"{sub['title']} (mission step {sub['idx'] + 1})")
            _save_subtask(sub["id"], task_id=task["id"], status="queued",
                          started_at=time.time(), input_delivery=delivery)
            sub = {**sub, "task_id": task["id"]}
        while True:
            task = self.queue.get(sub["task_id"])
            status = (task or {}).get("status")
            if status == "waiting":
                verdict = self._supervise_pause(mission_id, sub, task)
                if verdict == "resume":
                    continue   # the task is queued again; keep watching it in this drive
                if verdict == "fail":
                    return True   # the wedge verdict is recorded; the mission is finished or moves on
                _save_subtask(sub["id"], status="waiting")
                _save_mission(mission_id, state="paused")
                return False
            if task is None or status in _TERMINAL_TASK:
                break
            if status in ("queued", "working") and sub["status"] != status:
                _save_subtask(sub["id"], status=status)
            await asyncio.sleep(self.poll_sec)
        outcome = _outcome_from_task(task, sub)
        _save_subtask(sub["id"], status=outcome["status"], claimed=outcome["claimed"],
                      verified_status=outcome["verified_status"], evidence=outcome["evidence"],
                      finished_at=time.time())
        # Fix round 5: if a stale peer process already finished this
        # mission from its own older composition of this same outcome,
        # the verdict of record is reconciled with the fresh outcome
        # just saved (a no-op when the mission is still running).
        _repair_finished_verdict(mission_id)
        if outcome["status"] in ("failed", "blocked"):
            mission = get_mission(mission_id)
            if not mission["continue_on_failure"]:
                for rest in list_subtasks(mission_id):
                    if rest["status"] in ("pending", "queued"):
                        _save_subtask(rest["id"], status="skipped")
                self._finish(mission_id,
                             reason=f"Step {sub['idx'] + 1} ({sub['title']}) {outcome['status']}: "
                                    + (outcome["evidence"][0] if outcome["evidence"] else "see the step report"))
                return True
        return True

    # ------------------------------------------------------- pause supervision
    def _supervise_pause(self, mission_id: str, sub: Dict[str, Any], task: Dict[str, Any]) -> str:
        """Watches one waiting subtask task (fix round 5; see the
        PAUSE_WEDGE_THRESHOLD_SEC note at the top of this module).
        Returns "wait" (leave the mission paused), "resume" (the one
        automatic resume was just issued through the queue's retry
        path; the caller keeps watching the task), or "fail" (the
        wedge verdict has been recorded by _fail_paused_wedge).

        The wait clock lives on the subtask row (paused_at), not in
        memory and not on the mission's updated_at: the driver loop
        rewrites the mission row on every re-drive, so its stamp can
        never say how long the pause has lasted, and a wedge that
        survives an app restart must keep its clock and its spent
        resume across the restart."""
        fresh = next((s for s in list_subtasks(mission_id) if s["id"] == sub["id"]), sub)
        if task.get("waiting_kind") == "approval":
            # A person's approval gate is never timed out and never
            # auto-answered. Clear the wedge clock while approval is
            # what is being waited on, so time spent at the gate never
            # counts against a later, different wait.
            if fresh.get("paused_at") is not None:
                _save_subtask(sub["id"], paused_at=None)
            return "wait"
        now = self._now()
        paused_at = fresh.get("paused_at")
        if paused_at is None:
            _save_subtask(sub["id"], paused_at=now)
            return "wait"
        waited = now - paused_at
        attempts = fresh.get("pause_resume_attempts") or 0
        window = self.pause_threshold_sec if attempts == 0 else self.pause_resume_window_sec
        if waited < window:
            return "wait"
        if attempts == 0:
            try:
                res = self.queue.retry(sub["task_id"])
                ok = isinstance(res, dict) and bool(res.get("success"))
            except Exception:
                logger.exception(f"Mission {mission_id}: the automatic resume of "
                                 f"task {sub['task_id']} raised")
                ok = False
            if ok:
                _save_subtask(sub["id"], paused_at=None, pause_resume_attempts=1)
                logger.warning(f"Mission {mission_id}: step {fresh['idx'] + 1} "
                               f"({fresh['title']}) sat waiting {int(waited)}s "
                               f"({task.get('waiting_kind') or 'waiting'}); one automatic "
                               "resume attempted through the queue retry path")
                return "resume"
            self._fail_paused_wedge(mission_id, fresh, task, waited, resume_failed=True)
            return "fail"
        self._fail_paused_wedge(mission_id, fresh, task, waited, resume_failed=False)
        return "fail"

    def _fail_paused_wedge(self, mission_id: str, sub: Dict[str, Any], task: Dict[str, Any],
                           waited: float, resume_failed: bool) -> None:
        """Records the honest terminal shape for a paused wedge: the
        subtask fails with the wedge as its evidence, and the mission
        follows the same stop-or-continue path as a subtask whose task
        failed, with the wedge named in the mission's failure reason."""
        kind = task.get("waiting_kind") or "waiting"
        detail = task.get("waiting_detail") or {}
        note = detail.get("message") if isinstance(detail, dict) else None
        where = f" ({note})" if note else ""
        if resume_failed:
            why = (f"wedged in paused: its task sat waiting {int(waited)} seconds for "
                   f"{kind}{where} and nobody answered; the one automatic resume "
                   "attempt failed, so the step fails here instead of waiting forever")
        else:
            why = (f"wedged in paused: its task sat waiting {int(waited)} seconds for "
                   f"{kind}{where}, one automatic resume was attempted, and the task "
                   "waited again past the resume window with nobody answering; the "
                   "step fails here instead of waiting forever")
        logger.warning(f"Mission {mission_id}: step {sub['idx'] + 1} ({sub['title']}) {why}")
        _save_subtask(sub["id"], status="failed", verified_status="failed",
                      claimed=(task.get("result") or ""), evidence=[why],
                      finished_at=time.time(), paused_at=None)
        mission = get_mission(mission_id)
        if mission and not mission["continue_on_failure"]:
            for rest in list_subtasks(mission_id):
                if rest["status"] in ("pending", "queued"):
                    _save_subtask(rest["id"], status="skipped")
            self._finish(mission_id,
                         reason=f"Step {sub['idx'] + 1} ({sub['title']}) failed: {why}")

    # ------------------------------------------------------------ finishing
    def _finish(self, mission_id: str, reason: Optional[str] = None) -> None:
        mission = get_mission(mission_id)
        if mission["state"] in ("done", "failed") and mission.get("finished_at"):
            # Already finished (a stop-on-failure finish, then the loop's
            # own). Fix round 5: the finish may have been written by a
            # stale peer process from evidence this build's fresh checks
            # have since superseded; repair the recorded verdict if so.
            _repair_finished_verdict(mission_id)
            return
        _recheck_failed_subtasks(mission_id)
        mission = get_mission(mission_id)
        subs = mission["subtasks"]
        failed = [s for s in subs if s["status"] == "failed"]
        blocked = [s for s in subs if s["status"] == "blocked"]
        if reason is None and (failed or blocked):
            bad = failed + blocked
            names = "; ".join(f"Step {s['idx'] + 1} ({s['title']})" for s in bad)
            reason = (f"{len(bad)} of {len(subs)} steps did not complete "
                      f"({len(failed)} failed, {len(blocked)} blocked): {names}; the rest were run "
                      "because this mission was set to keep going past failures.")
        state = "failed" if (reason or failed or blocked) else "done"
        finished = time.time()
        _save_mission(mission_id, state=state, finished_at=finished,
                      failure_reason=reason if state == "failed" else None)
        report = build_report(get_mission(mission_id))
        _save_mission(mission_id, report=report)
        from app.services import event_log
        event_log.emit("mission.verdict", actor="missions", ids={"mission_id": mission_id},
                       payload={"state": state, "failure_reason": reason if state == "failed" else None,
                                "totals": report.get("totals") or {},
                                "duration_sec": report.get("duration_sec")})


def build_report(mission: Dict[str, Any]) -> Dict[str, Any]:
    """The mission report. claimed_result is the agent's own words;
    verified_status and evidence are what the queue's file-effect check
    established on disk. The two are never merged into one claim."""
    subs = mission["subtasks"]
    rows = []
    for s in subs:
        rows.append({
            "step": s["idx"] + 1,
            "title": s["title"],
            "instruction": s["instruction"],
            "expected_effects": s["expected_effects"],
            "task_id": s.get("task_id"),
            "contract": s.get("contract") or {},
            "input_delivery": s.get("input_delivery") or [],
            "status": s["status"],
            "claimed_result": s.get("claimed") or "",
            "verified_status": s.get("verified_status"),
            "evidence": s.get("evidence") or [],
        })
    started, finished = mission.get("started_at"), mission.get("finished_at")
    return {
        "mission_id": mission["id"],
        "goal": mission["goal"],
        "state": mission["state"],
        "continue_on_failure": mission["continue_on_failure"],
        "plan": mission.get("plan"),
        "failure_reason": mission.get("failure_reason"),
        "subtasks": rows,
        "totals": {
            "subtasks": len(rows),
            "done": sum(1 for s in rows if s["status"] == "done"),
            "verified": sum(1 for s in rows if s["verified_status"] == "verified"),
            "failed": sum(1 for s in rows if s["status"] == "failed"),
            "blocked": sum(1 for s in rows if s["status"] == "blocked"),
            "unverifiable": sum(1 for s in rows if s["verified_status"] == "unverifiable"),
        },
        "started_at": started,
        "finished_at": finished,
        "duration_sec": round(finished - started, 1) if started and finished else None,
        "usage": _usage_section(mission["id"]),
        "how_to_read": ("claimed_result is what the agent reported. verified_status and evidence "
                        "are what the task queue's file-effect check found on disk. A step with no "
                        "file effects is verified only when its task carried decisive evidence; "
                        "otherwise it is marked unverifiable, because absence of contradiction is "
                        "not verification. A step that reported a missing input or tool is "
                        "blocked, a state of its own, never done."),
    }


# --------------------------------------------------------------------- public API
runner = MissionRunner()


def create_mission(goal: str, continue_on_failure: bool = False) -> Dict[str, Any]:
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("Tell Xod the goal first.")
    mid = f"mission-{uuid.uuid4().hex[:10]}"
    now = time.time()
    c = _conn()
    c.execute("INSERT INTO missions (id, goal, state, continue_on_failure, created_at, updated_at) "
              "VALUES (?,?,?,?,?,?)", (mid, goal, "planning", 1 if continue_on_failure else 0, now, now))
    c.commit()
    c.close()
    from app.services import event_log
    event_log.emit("mission.created", actor="missions", ids={"mission_id": mid},
                   payload={"goal": goal[:300], "continue_on_failure": bool(continue_on_failure)})
    _spawn(mid)
    return get_mission(mid)


def get_report(mission_id: str) -> Optional[Dict[str, Any]]:
    m = get_mission(mission_id)
    if not m:
        return None
    if m.get("report"):
        return m["report"]
    if m["state"] in ("done", "failed"):
        return build_report(m)
    return None


# --------------------------------------------------------------------- background driver
_driving: set = set()
_driver_task: Optional[asyncio.Task] = None


def _spawn(mission_id: str) -> None:
    """Starts driving a mission now, if an event loop is running."""
    if mission_id in _driving:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _driving.add(mission_id)

    async def _go() -> None:
        try:
            await runner.drive(mission_id)
        except Exception:
            logger.exception(f"Mission {mission_id}: driver error")
        finally:
            _driving.discard(mission_id)

    loop.create_task(_go())


async def _driver() -> None:
    """The background loop: any mission left in planning, running or paused
    state (including by a previous run of the app) gets driven again. This is
    what makes a mission survive an app restart: nothing about its progress
    lives only in memory."""
    while True:
        try:
            for mid in _missions_needing_drive():
                _spawn(mid)
        except Exception:
            logger.exception("Mission driver scan failed")
        await asyncio.sleep(2)


def start() -> None:
    """Called at app startup. Picks up missions a previous run left behind."""
    _conn().close()
    global _driver_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _driver_task is None or _driver_task.done():
        _driver_task = loop.create_task(_driver())
