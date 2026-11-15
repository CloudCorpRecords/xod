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

One exception to sequential execution (grade fix cluster 2, Tier 5): a
subtask whose plan carries structured "delegate" worker briefs does not run
as one self-executing task at all. MissionRunner hands the briefs to
workers.coordinate, which spawns one read-only worker per brief in
parallel, records every worker in the workers table, and aggregates their
results (failures itemized, never dropped from the denominator) into the
step's result. A subtask whose instruction calls for delegation in prose
but whose plan carries no structured briefs fails honestly at dispatch,
naming that gap: the parent never quietly does the delegated work itself.
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
#
# One exception, added 2026-11-08 on the owner's standing needs-you
# rule (the AI guesses the strongest match and keeps moving): a
# QUESTION wait whose task carries a locate "ask" payload is answered
# by supervision itself at the threshold, with the strongest
# candidate, through the queue's own finish path, the guess written
# into the result as a guess. A mission died on exactly that shape
# (mission-5bf2c71b83) because the resume could not answer a question
# that already carried its own best answer. Approval waits remain
# exempt absolutely; blocked waits and payload-less questions keep
# the resume-then-fail shape above.
PAUSE_WEDGE_THRESHOLD_SEC = 600
PAUSE_RESUME_WINDOW_SEC = 600


# --------------------------------------- transient declared file inputs
# Cluster 5 (2026-11-14, from the Tier 3 live grade T3-L2,
# LIVE-GRADES-2026-11-13.md): the probe planted feed-a.txt present
# and feed-b.txt absent, appearing at +40s, to exercise the Tier 3
# assertion "a transient fault in the middle source is retried
# alone". The planner declared feed-b.txt as a file input of the
# merge step, correctly: the goal stated both feeds as present, and
# the plan prompt's own rule is to declare a file input whenever a
# step's work depends on it. Assembly then stopped the step before
# any task was created and that stop was terminal, so the transient
# was never exercised and the assertion graded UNKNOWN. As with
# AGE-37 below, the stop itself is honest; the defect is that it is
# the end of the road for a source that is only transiently absent.
#
# The remedy sits at that same assembly point and is bounded. When
# a step's declared file input cannot be read and the AGE-37
# adaptation does not apply, assembly is retried for that step
# alone over DECLARED_FILE_RETRY_WINDOW_SEC, polling the declared
# files from disk. The step still never starts on an undelivered
# input: a task is created only once every declared input is
# actually delivered into the request, exactly as before. If the
# window expires and the input still cannot be read, the original
# assembly stop stands unchanged and the mission fails honestly;
# nothing is fabricated and no substitute is invented by this
# surface (substitution remains the AGE-37 adaptation's, tried
# first, on its own evidence rules). The window is sized from the
# graded probe: the source appeared 40 seconds into the run while
# assembly was reached within the first 8, so the window must
# comfortably exceed the ~35 seconds assembly had to wait; 90
# seconds is a little over twice that, and stays far below the
# 600 second scale this module already treats as a wedge.
DECLARED_FILE_RETRY_WINDOW_SEC = 90.0
DECLARED_FILE_RETRY_POLL_SEC = 2.0


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


def _incompletion_signal(claimed: Any, exempt_objects=None,
                         step_scope_count: Optional[int] = None) -> Optional[str]:
    """The sentence of a subtask report that admits the step did not
    finish, or None. A subtask whose own words say the work is not
    complete, or name a test failure, cannot be verified no matter what
    the disk check found. Zero-count success phrasing ("5 passed,
    0 failed", "no failures") is a success report, not an admission, and
    is neutralised before scanning so clean reports do not trip this.

    `exempt_objects` (D23, AGE-40) names deliverables the plan assigns
    to OTHER steps of the same mission: a create/write/run admission
    whose only named object is one of those ("I did not create
    summary.txt", said by the step whose job was to compute the total
    the NEXT step writes) states the step's scope, not its own
    incompletion, and is not a signal. Every other admission shape,
    and every call without exemptions, is judged exactly as before.

    `step_scope_count` (grade fix cluster 2, Tier 5) is the size of
    THIS step's own enumerated scope (the graded shard step's six
    files, from its "01-06" range or its "six files" phrasing). An
    incompletion sentence that attributes the incompletion to a
    quantified scope strictly LARGER than that ("the 40-file sweep
    remains incomplete", said by the step that checked its six) is a
    statement about the mission's scope, not this step's: the step is
    judged on its own scope, and the mission's completeness is judged
    by its later steps and its finish, not by conscripting step 1's
    report into a verdict on all 40 files. A first-person admission
    ("I could not complete...", "I did not finish...") is never
    scope-exempt, whatever numbers it names."""
    if not claimed or not isinstance(claimed, str):
        return None
    text = re.sub(r"\b0 (?:failed|failures|errors)\b", "0", claimed, flags=re.I)
    text = re.sub(r"\bno (?:failures|errors|failed tests)\b", "none", text, flags=re.I)
    for sentence in re.split(r"[.\n]", text):
        low = sentence.lower()
        if any(re.search(p, low) for p in _INCOMPLETION_RES):
            if (exempt_objects
                    and not any(re.search(p, low) for p in _NON_OBJECT_INCOMPLETION_RES)
                    and not (re.search(r"\bfail", low)
                             and re.search(r"\b(pytest|test suite|tests?|collection)\b", low))
                    and _sibling_scope_admission(low, exempt_objects)):
                continue
            if step_scope_count and _mission_scope_admission(low, step_scope_count):
                continue
            return sentence.strip()[:160]
        if re.search(r"\bfail", low) and re.search(r"\b(pytest|test suite|tests?|collection)\b", low):
            return sentence.strip()[:160]
    return None


# The incompletion shapes that never name a creatable object, plus the
# complete/finish verb forms: a sentence matching any of these is an
# admission about the step itself and is never scope-exempt (D23).
_NON_OBJECT_INCOMPLETION_RES = (
    r"\bnot (?:yet )?(?:complete|completed|finished|done)\b",
    r"\bincomplete\b",
    r"\bpartially (?:complete|completed|finished|done)\b",
    r"\b(?:could not|couldn't|unable to|failed to) (?:complete|finish)\b",
    r"\bdid not (?:complete|finish)\b",
    r"\bcollection error\b",
)

_OBJECT_ADMISSION_RES = (
    re.compile(r"\b(?:could not|couldn't|unable to|failed to) (?:create|write|run|make)\b"),
    re.compile(r"\bdid not (?:create|write|run)\b"),
)


def _sibling_scope_admission(low: str, exempt_objects) -> bool:
    """True when the (lowercased) sentence's admission is a
    create/write/run statement whose named object is one of
    `exempt_objects` (basenames of deliverables the plan assigns to
    other steps). The sentence split above breaks on the dots inside a
    file name, so the object token can arrive truncated ("`summary"
    for summary.txt): a token matches a basename exactly, as its stem,
    or as the basename's dotted prefix."""
    for rx in _OBJECT_ADMISSION_RES:
        m = rx.search(low)
        if not m:
            continue
        rest = low[m.end():].strip()
        if not rest:
            return False
        name = rest.split()[0].strip("`'\".,;:()[]").rsplit("/", 1)[-1]
        if not name:
            return False
        return any(name == b or b.startswith(name + ".")
                   or os.path.splitext(b)[0] == name
                   for b in exempt_objects)
    return False


def _sibling_effect_names(subtask: Dict[str, Any]) -> set:
    """Basenames of the file effects the plan assigns to the OTHER
    steps of this step's mission, less any name this step itself
    declares (D23, AGE-40): the objects a report may truthfully say
    this step did not create, because creating them is a sibling
    step's declared work."""
    own = {os.path.basename(str(e.get("path", "")))
           for e in subtask.get("expected_effects") or [] if e.get("path")}
    names = set()
    try:
        siblings = list_subtasks(subtask.get("mission_id") or "")
    except Exception:
        return set()
    for s in siblings:
        if s.get("id") == subtask.get("id"):
            continue
        for e in s.get("expected_effects") or []:
            if e.get("path"):
                names.add(os.path.basename(str(e["path"])))
    return names - own


# ------------------------------------------------------- delegation + step scope
# Grade fix cluster 2 (Tier 5, live grade 2026-11-13, mission-c797b09d0b):
# the planner authored exactly the right delegation shape in prose (seven
# shard-range steps, each instructing "Delegate these six files to three
# parallel workers, assigning each worker a disjoint pair and a brief
# covering only that pair"), and execution diverged completely: 0 rows in
# the workers table, step 1's own task read its six shards itself, and its
# report was then failed for admitting the whole 40-file mission was
# incomplete. Root causes, all in this module's path: validate_plan kept
# only title/instruction/effects/inputs/tools, so a plan carried NO
# structured fan-out the executor could act on (the prose was the whole of
# it); MissionRunner created exactly one ordinary queue task per subtask
# and never called workers.coordinate (the only writer of workers rows
# for fan-outs, reached by plain tasks through orchestrator._run_parallel
# on a structured {"parallel": [...]} plan step, a path mission tasks
# never take: queue.create is called here with no mode, so the task is not
# stepwise); and the one delegate-flavoured tool a step agent can be
# offered, team_delegate (app/agent/worker.py), is offered only when
# team.team_on() finds more than one distinct brain, writes team subagent
# logs rather than workers rows, and its own note tells the agent "Small
# jobs: just do them yourself" - nothing in the path made it call it.
# The fix: the plan schema carries structured "delegate" briefs (below,
# in validate_plan and PLAN_PROMPT), MissionRunner executes them through
# workers.coordinate (see _delegated_outcome), and a step that calls for
# delegation without structured briefs fails honestly instead of being
# silently self-executed.

_DELEGATION_CALL_RE = re.compile(
    r"\bdelegat\w*\b|\bparallel workers\b|\bworkers in parallel\b", re.I)
_SCOPE_UNITS = r"(?:files?|shards?|items?|records?|pages?|documents?|sources?)"
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "eleven": 11, "twelve": 12}


def _delegation_briefs(subtask: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The structured worker briefs a subtask's plan carries, from its
    stored capability contract (where _plan records them) or, for a
    plan dict in hand, from the subtask itself. Briefs without a task
    text are not briefs."""
    contract = subtask.get("contract") or {}
    briefs = contract.get("delegate") or subtask.get("delegate") or []
    if not isinstance(briefs, list):
        return []
    return [b for b in briefs
            if isinstance(b, dict) and str(b.get("task") or "").strip()]


def _calls_for_delegation(subtask: Dict[str, Any]) -> bool:
    """True when the step's own instruction calls for delegated
    parallel workers, in words. Judged on the instruction only, never
    on the title (a step titled "Delegate parallel shard reads" whose
    instruction is "Collect shard totals from workers and report them"
    is a collection step, the T5-D shape, not a delegation call) and
    never on delivered inputs (the mission goal is reference material)."""
    return bool(_DELEGATION_CALL_RE.search(subtask.get("instruction") or ""))


def _step_scope_count(subtask: Dict[str, Any]) -> Optional[int]:
    """The size of this step's own enumerated scope, when its title or
    instruction enumerates one: an item range ("shards 01-06" -> 6) or
    a counted unit phrase ("these six files" -> 6). None when the step
    enumerates no scope, in which case no scope exemption applies."""
    text = f"{subtask.get('title') or ''} {subtask.get('instruction') or ''}".lower()
    m = re.search(r"\b(\d{1,3})\s*[-–]\s*(\d{1,3})\b", text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if hi >= lo:
            return hi - lo + 1
    m = re.search(r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")\s+"
                  + _SCOPE_UNITS + r"\b", text)
    if m:
        tok = m.group(1)
        return int(tok) if tok.isdigit() else _NUMBER_WORDS[tok]
    return None


def _mission_scope_admission(low: str, step_scope_count: int) -> bool:
    """True when an (already lowercased) incompletion sentence
    attributes its incompletion to a quantified scope strictly larger
    than this step's own scope ("the 40-file sweep remains incomplete"
    from a six-file step). A first-person or complete/finish admission
    is the step talking about itself and is never mission-scoped."""
    if re.search(r"\b(?:could not|couldn't|unable to|failed to) "
                 r"(?:complete|finish)\b", low):
        return False
    if re.search(r"\bdid not (?:complete|finish)\b", low):
        return False
    for m in re.finditer(r"\b(\d+)\s*[- ]\s*" + _SCOPE_UNITS + r"\b", low):
        if int(m.group(1)) > step_scope_count:
            return True
    return False


# ------------------------------------------------------- answer content
# D19 (AGE-36, re-grade run 8 T1B): the AGE-32 repair gave the Everest
# research step working web access and the step used it, but its claimed
# result was a link list headed by a bare token, with no temperature
# value, no units and no context in it, and the verdict recorded it done
# and unverifiable on absence of contradiction. The write step then
# landed a research.md that itself states the Everest value remains to
# be verified, and the mission completed with half its goal missing.
# Access was repaired; what no layer judged was whether the report
# answers the step. The judgment here is structural, in the style of
# the screens above: it applies only where the step's own instruction
# demands a sourced fact (the _instruction_needs_research test, judged
# on the step's own instruction and title, never on delivered reference
# sections), and it asks what the claimed result carries outside its
# citations. Citations and bare URLs are pointers to an answer, not the
# answer; link text is citation text, so a list of links carries no
# answer content however its titles read. What remains must contain at
# least one answer statement: a clause of at least four words that
# states something (it carries a value, a digit, or a finite reporting
# verb), and that is not itself an admission that the fact is missing
# or unverified. A bare acknowledgment token, a link list, and prose
# whose only statements say the fact remains to be verified all leave
# no answer statement, so the step fails with that true reason, in the
# same disposition the incompletion screen gives a report that admits
# it did not finish (failed, contradicted): the step was not blocked
# by a missing input or tool (it had its access and used it), it
# reported, and what it reported does not answer its instruction. A
# step whose instruction demands no sourced fact (pure reasoning, a
# summary over delivered inputs) is never judged here, and a step with
# declared effects keeps its effect based verdicts; this check is for
# the no-effect step whose report is the whole of its work.

_ANSWER_CITATION_RE = re.compile(r"\[[^\]]*\]\(\s*[^)\s]+[^)]*\)")
_ANSWER_URL_RE = re.compile(r"(?:https?://|www\.)[^\s)\]]+", re.I)
_ANSWER_VERB_RE = re.compile(
    r"\b(?:is|are|was|were|has|have|had|reports?|reported|stands?|stood|"
    r"measures?|measured|averages?|averaged|reaches|reached|equals?|"
    r"contains?|contained|shows?|showed|indicates?|indicated|lists?|"
    r"listed|provides?|provided|records?|recorded|numbers?|numbered|"
    r"totals?|sits?|sat)\b", re.I)
_ANSWER_MISSING_RES = (
    r"\bunverified\b",
    r"\bto be (?:verified|confirmed|checked)\b",
    r"\bnot (?:yet )?(?:been )?(?:verified|confirmed)\b",
    r"\bdid not (?:provide|include|report|contain|give|state|specify)\b",
    r"\bno (?:sourced )?(?:value|fact|answer|figure|data|information) "
    r"(?:was )?(?:provided|reported|found|available)\b",
    r"\bcould not be (?:verified|confirmed)\b",
    r"\bneeds? to be (?:verified|confirmed)\b",
)


def _has_answer_statement(prose: str) -> bool:
    """True when prose outside any citation carries an answer
    statement (see the D19 note above): a clause of at least four
    words, carrying a digit or a finite reporting verb, that does not
    itself say the demanded fact is missing or unverified."""
    for line in prose.splitlines():
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line)
        if line.lstrip().startswith("#"):
            continue
        for clause in re.split(r"[.!?;\n]", line):
            s = clause.strip(" \t*#>`\"'")
            if not s:
                continue
            low = s.lower()
            if any(re.search(p, low) for p in _ANSWER_MISSING_RES):
                continue
            if _incompletion_signal(s):
                continue
            words = re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", s, flags=re.UNICODE)
            if len(words) < 4:
                continue
            if re.search(r"\d", s) or _ANSWER_VERB_RE.search(s):
                return True
    return False


def _instruction_demands_primary_ranking(subtask: Dict[str, Any]) -> bool:
    """The step's own text demands primary-over-secondary ranking: a
    primary source or record is named, and secondary coverage, a
    ranking verb, or the issuing authority is named with it."""
    both = (str(subtask.get("title", "")) + "\n"
            + str(subtask.get("instruction", ""))).lower()
    if not re.search(r"\bprimary\b", both):
        return False
    return bool(re.search(r"\bsecondary\b|\brank\w*\b|\bprefer\w*\b|"
                          r"\bover\b[^.\n]{0,30}\bsecondary\b|"
                          r"\bauthorit\w*\b|\bofficial record", both))


def _primary_ranking_problem(subtask: Dict[str, Any],
                             claimed: Any) -> Optional[str]:
    """The reason a report is not the primary-ranked answer its own
    instruction demands, or None when it ranks (or the step demands
    no ranking). Fix wave cluster 3 (Tier 4 grade T4-L2): the live
    research settled for secondary-source counts its own text flagged
    as not verified against the primary authorities, and no layer
    judged the ranking. Two deterministic failures: the report itself
    flags its answer unverified against the primary authority, or it
    never names a primary source at all, so the demanded ranking is
    absent from the answer."""
    if not _instruction_demands_primary_ranking(subtask):
        return None
    if not claimed or not isinstance(claimed, str):
        return None
    low = claimed.lower()
    if (re.search(r"\bnot (?:yet )?(?:been )?verified against\b", low)
            or re.search(r"\bunverified\b", low)):
        return ("The step's instruction requires ranking primary sources "
                "over secondary coverage, but its report flags its own "
                "answer as unverified against the primary authority: it "
                "settled for secondary-source figures and labelled them "
                "unverified instead of establishing the fact from a "
                "primary source. A secondary figure its own report calls "
                "unverified is not the primary-ranked answer its "
                "instruction required.")
    if "primary" not in low:
        return ("The step's instruction requires ranking primary sources "
                "over secondary coverage, but its report names no primary "
                "source at all: the ranking its instruction demanded is "
                "absent, and secondary coverage alone does not satisfy it.")
    return None


def _sourced_answer_problem(subtask: Dict[str, Any], claimed: Any) -> Optional[str]:
    """The reason a no-effect step's claimed result is not the sourced
    fact its own instruction demands, or None when the report carries
    the answer (or the step demands no sourced fact). Judged on the
    step's own text only, via _instruction_needs_research; the claimed
    result is judged on what it carries, never on wording specific to
    any one run."""
    if not _instruction_needs_research(subtask):
        return None
    if not claimed or not isinstance(claimed, str) or not claimed.strip():
        return ("The step's instruction requires a sourced fact (the fact, with its "
                "source name and URL reported), but its report was empty, so it "
                "contains no answer to that instruction. An empty report is not "
                "done on absence of contradiction.")
    if not _ANSWER_URL_RE.search(claimed):
        return ("The step's instruction requires a sourced fact (the fact, with its "
                "source name and URL reported), but its report names no source URL, "
                "so the fact it states is not the sourced fact its instruction "
                "required.")
    prose = _ANSWER_URL_RE.sub(" ", _ANSWER_CITATION_RE.sub(" ", claimed))
    if _has_answer_statement(prose):
        ranking_problem = _primary_ranking_problem(subtask, claimed)
        if ranking_problem:
            return ranking_problem
        return None
    return ("The step's instruction requires a sourced fact (the fact, with its "
            "source name and URL reported), but its report contains no answer "
            "statement: outside its source links and citations there is no "
            "statement of the fact it was asked to report, only links, an "
            "acknowledgment, or a statement that the fact remains unverified. "
            "A report with no answer content is not done on absence of "
            "contradiction.")


# ------------------------------------------------------- discovery content
# D22 (AGE-39, Tier 2 grade T2C): a no-effect step whose whole work is a
# discovery (inspect candidate files, identify the one matching a stated
# criterion, report it and the value it carries) had no answer-content
# judgment at all. The D19 judgment is scoped to research instructions
# (a source and its URL to report), which a discovery instruction does
# not demand, and the incompletion screen did not fire on the graded
# admission ("I can't identify the matching file or its code"), so the
# step recorded done and unverifiable on a report that admits the
# discovery never happened, and the mission consumed the non-discovery
# downstream. The judgment here mirrors D19 in scope and disposition:
# it applies only where the step's own instruction and title demand a
# discovery (candidate inspection, a stated selection criterion, and a
# report of the identified file), judged on that text only, never on
# delivered inputs or reference sections (the D13 rule), and a report
# that does not make the discovery fails with that true reason, in the
# disposition the incompletion screen and D19 give a report whose
# content defeats verification (failed, contradicted). Blocked stays
# reserved for a missing input or tool, which the blocked screen ahead
# of this check in the composition still decides first. A plain read
# of a named file, a pure reasoning step, and a research step are not
# discovery steps and are never judged here.

_DISCOVERY_ADMISSION_RES = (
    r"\b(?:can't|cannot|can not|could not|couldn't|unable to|not able to|"
    r"did not|didn't|do not|don't) (?:identify|determine|find|locate|"
    r"tell|say which)\b",
    r"\bno (?:matching )?file (?:was|has been) (?:identified|found|"
    r"located|determined)\b",
    r"\bmatching file (?:was|has) not (?:been )?(?:identified|found|located)\b",
    r"\bnot (?:yet )?(?:identified|determined)\b",
)
_DISCOVERY_FILE_RE = re.compile(
    r"(?:[A-Za-z0-9_.\-]+/)*[A-Za-z0-9_\-]+\.[A-Za-z0-9]{1,10}\b")
_DISCOVERY_MARKER_RE = re.compile(
    r"\bmatch(?:ing|es|ed)?\b|\bidentif\w*\b|\bfound\b|\bselected\b|"
    r"\bchosen\b|\bis the (?:one|file)\b|\bwhose\b|\bfirst line\b|"
    r"\breads\b|\bbegins with\b|\bstarts with\b|"
    r"\b(?:file|path)\s*[:=]", re.I)
_DISCOVERY_VALUE_LABEL_RE = re.compile(
    r"\b(?:code|value|reference|result|text|contents?)\b[^.\n]{0,30}?"
    r"\b(?:is|:|=)\s*[`'\"]?([A-Za-z0-9][\w\-]*)", re.I)
_DISCOVERY_VALUE_STOPWORDS = {"the", "a", "an", "file", "matching", "in",
                              "of", "is", "was", "it", "this", "that"}


_CLASS_SOURCE_RE = re.compile(
    r"registry|blog|wire|reprint|syndicat\w*|primary|secondary|"
    r"press release|filing|archive|news article|announcement", re.I)


def _instruction_needs_classification(subtask: Dict[str, Any]) -> bool:
    """The step's own text demands a multi-source classification, not
    a single-target discovery: inspect several sources and say what
    each one IS (the registry extract, the company blog, a wire
    reprint), judged on the instruction and title only. An explicit
    single-target criterion (the matching file, the unique file, the
    file whose first line ...) always wins: that step is a discovery
    even when its text also names source kinds. Fix wave cluster 3
    (Tier 4 grade T4-L1): the AGE-39 discovery judgment fired on a
    classify-all-sources step and demanded the one statement that
    step must not make (a single matching file), killing the mission
    before its adjudication could run."""
    text = str(subtask.get("instruction", ""))
    title = str(subtask.get("title", "")).strip()
    both = f"{title}\n{text}".lower()
    if re.search(r"\bmatching file\b|\bunique file\b|\bfile whose\b|"
                 r"\bwhich file\b|\bone whose\b", both):
        return False
    gather = (re.search(r"\b(?:list|inspect|examine|read|check|scan|search)\b", both)
              and re.search(r"\b(?:sources?|files?|candidates?|extracts?)\b", both))
    if not gather:
        return False
    signal = (re.search(r"\bclassif\w*\b", both)
              or re.search(r"\bany\b[^.\n]{0,40}\breprint\b", both)
              or re.search(r"\b(?:each|all|every) sources?\b", both)
              or re.search(r"\bidentify the\b[^.\n]{0,100}(?:,| and | or )", both))
    return bool(signal)


def _instruction_needs_discovery(subtask: Dict[str, Any]) -> bool:
    """The step's own text demands a discovery: inspect candidate
    files, select the one matching a stated criterion, and report the
    identified file. Judged on the instruction and title only. A
    multi-source classification step is not a discovery (see
    _instruction_needs_classification)."""
    if _instruction_needs_classification(subtask):
        return False
    text = str(subtask.get("instruction", ""))
    title = str(subtask.get("title", "")).strip()
    both = f"{title}\n{text}".lower()
    inspect = (re.search(r"\b(?:list|inspect|examine|search|scan|check)\b", both)
               and re.search(r"\b(?:files?|folder|directory|inbox|candidates?)\b", both))
    selection = (re.search(r"\bmatching file\b|\bunique file\b|\bfile whose\b|"
                           r"\bfile that\b|\bfile which\b|\bone whose\b|"
                           r"\bwhich file\b|\bidentify the\b|\bfind the file\b", both)
                 or re.match(r"(?:find|identify|locate|discover)\b", title, re.I))
    report = re.search(r"\b(?:report|identify|name|state|give)\b", both)
    return bool(inspect and selection and report)


def _discovery_prefix(instruction: str) -> Optional[str]:
    """The quoted literal prefix the instruction selects on (for
    example 'CODE:'), or None when it names no such prefix."""
    m = re.search(r"['\"]([A-Za-z][A-Za-z0-9 _\-]*:)['\"]", instruction)
    return m.group(1) if m else None


def _discovery_value_present(claimed: str, prefix: Optional[str]) -> bool:
    """The report carries the discovered value: the selected prefix
    with a value after it, or a labelled statement of the value."""
    if prefix and re.search(re.escape(prefix) + r"\s*[^\s,;`'\"]", claimed):
        return True
    for m in _DISCOVERY_VALUE_LABEL_RE.finditer(claimed):
        if m.group(1).lower() not in _DISCOVERY_VALUE_STOPWORDS:
            return True
    return False


def _instruction_demands_contents(subtask: Dict[str, Any]) -> bool:
    """The discovery step's own instruction demands the identified
    file's contents, not just its identity or the value on a preview
    line: full/complete/entire contents, contents with their line
    endings, or reproducing the contents. Judged on the instruction
    only (fix wave cluster 1, Tier 2 grade T2C)."""
    text = str(subtask.get("instruction", "")).lower()
    if re.search(r"\b(?:full|complete|entire) contents?\b", text):
        return True
    if re.search(r"\bcontents?\b", text) and re.search(r"\bline endings?\b", text):
        return True
    if re.search(r"\breproduce\b[^.\n]{0,40}\bcontents?\b", text):
        return True
    return False


def _identified_files(text: str) -> List[str]:
    """The file paths a report names as its match: file mentions in a
    clause that carries an identification marker, in order."""
    out: List[str] = []
    for clause in re.split(r"(?:[;!?\n]|\.\s+)", text):
        if not _DISCOVERY_MARKER_RE.search(clause):
            continue
        for m in _DISCOVERY_FILE_RE.finditer(clause):
            if m.group(0) not in out:
                out.append(m.group(0))
    return out


def _read_record(entry: Any, reads: set) -> None:
    """Fold one evidence/agent-step entry into the set of files it
    shows were successfully read, when it records a computer_read_file
    call that did not fail. Paths are kept whole and as basenames."""
    if not isinstance(entry, dict):
        return
    tool = str(entry.get("tool") or entry.get("name") or "")
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    if not tool and payload:
        tool = str(payload.get("tool") or "")
    if tool != "computer_read_file":
        return
    src = payload if payload else entry
    if (src.get("success") is False or src.get("error")
            or entry.get("ok") is False or entry.get("success") is False):
        return
    path = str(src.get("filepath") or src.get("file_path") or src.get("path") or "")
    if not path:
        # agent_log rows name the read in their title ("Read x.txt").
        m = re.match(r"\s*Read\s+(.+?)\s*$", str(entry.get("title") or ""))
        path = m.group(1) if m else ""
    if path:
        norm = path.replace("\\", "/").rstrip("/")
        reads.add(norm.lower())
        reads.add(norm.rsplit("/", 1)[-1].lower())


def _successful_read_files(task: Optional[Dict[str, Any]]) -> set:
    """Files the step's task record shows were actually read with
    computer_read_file: the task's evidence entries, its stored
    evidence_json, its agent_steps, and the agent log's step slice
    for the task (the live event slice). A listing, a preview, or a
    failed read is not in this set."""
    reads: set = set()
    if not isinstance(task, dict):
        return reads
    ev = task.get("evidence")
    if isinstance(ev, list):
        for entry in ev:
            _read_record(entry, reads)
    for key in ("evidence_json", "agent_steps"):
        raw = task.get(key)
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                data = None
            entries = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
            for entry in entries:
                _read_record(entry, reads)
    tid = task.get("id")
    if tid:
        try:
            from app.services import agent_log
            for rows in (agent_log.steps_for(f"task:{tid}") or {}).values():
                for entry in rows or []:
                    _read_record(entry, reads)
        except Exception:
            pass
    return reads


_CLASS_MARKER_RE = re.compile(
    r"\bis (?:a|an|the)\b|\bclassified as\b|\bcounts as\b|\breprint of\b|"
    r"\bnot (?:an? )?independent\b|\bindependent source\b|"
    r"\bofficial record\b|\boriginal (?:record|filing|announcement)\b", re.I)


def _classification_answer_problem(subtask: Dict[str, Any],
                                   claimed: Any) -> Optional[str]:
    """The reason a multi-source classification step's claimed result
    is not the classification its own instruction demands, or None
    when the report classifies the candidates (or the step demands no
    classification). A classification must classify: at least two
    candidates each named with what it is (its source kind, its
    independence, its provenance). A bare listing of the candidates
    classifies none of them, and one classified source leaves the
    rest unclassified, so both fail with that true reason, in the
    same disposition as the discovery judgment."""
    if not _instruction_needs_classification(subtask):
        return None
    demand = ("The step's instruction requires a classification (inspect "
              "the sources and say, for each candidate, what kind of "
              "source it is)")
    if not claimed or not isinstance(claimed, str) or not claimed.strip():
        return (demand + ", but its report was empty, so no source was "
                "classified at all. An empty report is not done on "
                "absence of contradiction.")
    text = claimed.replace("\u2019", "'")
    for sentence in re.split(r"[.\n]", text):
        low = sentence.lower()
        if any(re.search(p, low) for p in _DISCOVERY_ADMISSION_RES):
            return (demand + f", but its report admits the classification "
                    f"was not made: \"{sentence.strip()[:160]}\" It is not "
                    "done on absence of contradiction.")
    classified: set = set()
    for clause in re.split(r"(?:[;!?\n]|\.\s+)", text):
        if not _CLASS_MARKER_RE.search(clause):
            continue
        files = _DISCOVERY_FILE_RE.findall(clause)
        if files:
            classified.update(f.lower() for f in files)
        else:
            m = _CLASS_SOURCE_RE.search(clause)
            if m:
                classified.add("kind:" + m.group(0).lower().split()[0])
    if len(classified) < 2:
        return (demand + ", but its report does not classify the "
                "candidates: fewer than two sources are named with what "
                "they are (their kind, independence, or provenance), so "
                "at most a listing of the candidates it inspected. A "
                "listing is not a classification, so the classification "
                "this step exists to make was not made. It is not done "
                "on absence of contradiction.")
    return None


def _discovery_answer_problem(subtask: Dict[str, Any],
                              claimed: Any,
                              task: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The reason a no-effect discovery step's claimed result is not
    the discovery its own instruction demands, or None when the report
    makes the discovery (or the step demands none). An explicit
    admission that the target was not identified is a non-discovery
    whatever else the report lists; a listing of candidates is not an
    identification, because no statement in it names one file as the
    one matching the stated criterion."""
    if not _instruction_needs_discovery(subtask):
        return None
    demand = ("The step's instruction requires a discovery (inspect the "
              "candidate files, identify the one matching the stated "
              "criterion, and report it with the value it carries)")
    if not claimed or not isinstance(claimed, str) or not claimed.strip():
        return (demand + ", but its report was empty, so no discovery was "
                "reported at all. An empty report is not done on absence "
                "of contradiction.")
    text = claimed.replace("\u2019", "'")
    for sentence in re.split(r"[.\n]", text):
        low = sentence.lower()
        if any(re.search(p, low) for p in _DISCOVERY_ADMISSION_RES):
            return (demand + f", but its report admits the discovery was "
                    f"not made: \"{sentence.strip()[:160]}\" A report that "
                    "the matching file was not identified does not "
                    "identify it, so the discovery this step exists to "
                    "make did not happen. It is not done on absence of "
                    "contradiction.")
    identified = False
    # Clauses split on sentence boundaries only: a full stop inside a
    # file path (directive.txt) is not a boundary, so the split is on
    # a stop followed by whitespace, or on ; ! ? and newlines.
    for clause in re.split(r"(?:[;!?\n]|\.\s+)", text):
        if _DISCOVERY_FILE_RE.search(clause) and _DISCOVERY_MARKER_RE.search(clause):
            identified = True
            break
    if not identified:
        return (demand + ", but its report does not identify the matching "
                "file: no statement in it names a file as the one matching "
                "the stated criterion, only (at most) a listing of the "
                "candidates it inspected. A listing is not a discovery, "
                "so the discovery this step exists to make was not made. "
                "It is not done on absence of contradiction.")
    instruction = str(subtask.get("instruction", ""))
    prefix = _discovery_prefix(instruction)
    if (prefix and re.search(r"\bfollowing\b|\bexact text\b|\bvalue\b",
                             instruction, re.I)
            and not _discovery_value_present(text, prefix)):
        return (demand + ", but although its report names a file it does "
                f"not report the discovered value its instruction requires "
                f"(the exact text following '{prefix}'), so the discovery "
                "is incomplete: the file is named but the value it was "
                "inspected for is not reported. It is not done on absence "
                "of contradiction.")
    if _instruction_demands_contents(subtask):
        # Cluster 1 (T2C, fourth grade): identification from the
        # listing's first_line previews is real identification, but a
        # step whose instruction demands the file's full contents is
        # not done until the identified file was actually read. The
        # graded step named directive.txt, reported that the rest of
        # the file was unavailable, and recorded done/unverifiable
        # with no successful computer_read_file on it anywhere in its
        # event slice, so the next step had nothing to copy. A worker
        # that tried and failed to read still fails honestly here: no
        # successful read in the slice means the contents were never
        # delivered, whatever the report claims.
        identified = _identified_files(text)
        reads = _successful_read_files(task)
        read_through = any(
            f.lower() in reads or f.rsplit("/", 1)[-1].lower() in reads
            or any(r.endswith("/" + f.lower()) for r in reads)
            for f in identified)
        if identified and not read_through:
            return (demand + ", but although its report names "
                    f"{identified[0]} it shows no successful read of that "
                    "file in the step's tool record, while the step's "
                    "instruction demands the file's contents: the match "
                    "was identified from the listing preview and never "
                    "read, so the contents the next step needs were not "
                    "delivered. Identification without the read is not "
                    "done on absence of contradiction.")
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


# --------------------------------------------- outcome retry (AGE-77)
# Live re-grade 3 (2026-11-14) left two terminal shapes that one
# bounded retry at the mission layer answers, because everything
# needed to finish the step already exists when the first pass ends:
#
# (a) Missing capability. The worker's report names a real tool its
# declared contract leaves out and asks for it (the graded T2C
# report: "Please allow `computer_list_directory` or provide the
# filenames"). The verdict recorded the step failed and the mission
# ended, although the repair is one line: add the named tool to the
# contract and run the step again. The plea is judged conservatively,
# in the _blocked_signal style: a plea sentence, a tool name from
# the catalog itself (never an invented name), and that tool absent
# from the declared contract. A step that declared no contract is
# not judged here: dispatch-time tool selection governs it, and a
# tool missing there is a selection defect, not a contract defect.
#
# (b) Disclaimed listings. The worker's report says the names in a
# folder were not available in the directory information shown,
# while the step's own tool record holds a successful
# computer_list_directory of that folder (Tier 4 L1: two listings,
# both ok, three files, then "their names and kinds were not
# available in the directory information shown"). The gates are
# right to fail that report; what was missing is the retry, with
# the received results re-presented (rendered from a fresh listing
# of the same folders, so the retry carries the names themselves)
# and the step run once more.
#
# One spend per step across both shapes, recorded in the step's
# saved contract ("outcome_retry") before the retry task is
# created, so a re-drive after a restart cannot spend it twice.
# The verdict gates are untouched: the retry's report is judged by
# exactly the same composition as the first pass's.

_CAPABILITY_PLEA_RES = (
    r"\bplease allow\b",
    r"\bdeclared tools?\b[^.\n]{0,60}\b(?:do not|does not|did not|don't) include\b",
    r"\btools?\b[^.\n]{0,40}\b(?:do not|does not|did not|don't) include\b",
    r"\bhave no (?:tool|way) to\b",
    r"\bno (?:tool|way) to\b",
    r"\bmissing\b[^.\n]{0,40}\btool\b",
    r"\blacks?\b[^.\n]{0,40}\btools?\b",
    r"\bnot (?:among|in) (?:my|the|its) (?:declared )?tools?\b",
    r"\bneeds?\b[^.\n]{0,50}\btool\b",
)


def _catalog_tool_names() -> set:
    """Every tool name a worker could name: the catalog's own names,
    plus the planner-facing aliases, kept both as written and as the
    canonical names they run as."""
    names = set()
    try:
        from app.agent.tools import TOOL_DEFINITIONS
        for t in TOOL_DEFINITIONS:
            n = (t.get("function") or {}).get("name")
            if n:
                names.add(str(n))
    except Exception:
        pass
    for alias, canon in _TOOL_NAME_ALIASES.items():
        names.add(alias)
        names.add(canon)
    return names


def _missing_capability_tool(subtask: Dict[str, Any],
                             outcome: Dict[str, Any]) -> Optional[str]:
    """The catalog tool a finished step's report pleads for and its
    declared contract leaves out, or None. Judged only for a failed
    or blocked outcome on a step that declared a contract."""
    if not isinstance(outcome, dict) \
            or outcome.get("status") not in ("failed", "blocked"):
        return None
    claimed = outcome.get("claimed")
    if not claimed or not isinstance(claimed, str):
        return None
    contract = subtask.get("contract") or {}
    tools = contract.get("tools")
    if tools is None:
        return None
    declared = [str(t) for t in tools if str(t).strip()]
    canon = set(declared) | {_canonical_tool_name(t) for t in declared}
    catalog = _catalog_tool_names()
    if not catalog:
        return None
    text = claimed.replace("\u2019", "'")
    for sentence in re.split(r"[.\n]", text):
        low = sentence.lower()
        if re.search(r"\bneeds? no\b|\bno (?:additional|other|further) tools?\b",
                     low):
            continue
        if not any(re.search(p, low) for p in _CAPABILITY_PLEA_RES):
            continue
        for name in sorted(catalog, key=len, reverse=True):
            if name in low and _canonical_tool_name(name) not in canon:
                return _canonical_tool_name(name)
    return None


def _task_tool_entries(task: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every tool-record entry a finished task carries: its evidence
    list, its stored evidence_json, its agent_steps, and the agent
    log's step slice for the task. The same sources
    _successful_read_files judges a step's reads from."""
    entries: List[Dict[str, Any]] = []
    if not isinstance(task, dict):
        return entries
    ev = task.get("evidence")
    if isinstance(ev, list):
        entries += [e for e in ev if isinstance(e, dict)]
    for key in ("evidence_json", "agent_steps"):
        raw = task.get(key)
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                data = None
            items = data if isinstance(data, list) else (
                [data] if isinstance(data, dict) else [])
            entries += [e for e in items if isinstance(e, dict)]
    tid = task.get("id")
    if tid:
        try:
            from app.services import agent_log
            for rows in (agent_log.steps_for(f"task:{tid}") or {}).values():
                entries += [e for e in rows or [] if isinstance(e, dict)]
        except Exception:
            pass
    return entries


def _successful_listings(task: Optional[Dict[str, Any]]) -> List[str]:
    """The folder paths a step's task record shows were listed with
    computer_list_directory in a call that did not fail, in record
    order. A failed listing is not in this list."""
    paths: List[str] = []
    for entry in _task_tool_entries(task):
        tool = str(entry.get("tool") or entry.get("name") or "")
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        if not tool and payload:
            tool = str(payload.get("tool") or "")
        if tool != "computer_list_directory":
            continue
        src = payload if payload else entry
        if (src.get("success") is False or src.get("error")
                or entry.get("ok") is False or entry.get("success") is False):
            continue
        path = str(src.get("path") or src.get("folder_path")
                   or src.get("filepath") or entry.get("detail") or "").strip()
        if path and path not in paths:
            paths.append(path)
    return paths


_LISTING_DISCLAIMER_RES = (
    r"\bnot available in the directory information\b",
    r"\bnot (?:shown|available|included) in (?:the )?(?:directory|listing)\b",
    r"\bnames? (?:were|was) not available\b",
    r"\bcould identify only\b",
    r"\bcan identify only\b",
    r"\blisting (?:did not|does not|didn't) (?:show|include|provide|contain)\b",
    r"\bdirectory information\b[^.\n]{0,60}\bnot\b",
)


def _listing_disclaimer(claimed: Any) -> Optional[str]:
    """The sentence of a step report that disclaims the directory
    information it was shown (the names in a listed folder called
    unavailable, or only one of the listed files identified), or
    None."""
    if not claimed or not isinstance(claimed, str):
        return None
    text = claimed.replace("\u2019", "'")
    for sentence in re.split(r"[.\n]", text):
        low = sentence.lower()
        if any(re.search(p, low) for p in _LISTING_DISCLAIMER_RES):
            return sentence.strip()[:200]
    return None


def _render_listing_representation(folders: List[str]) -> Optional[str]:
    """The re-presentation block for a disclaimed-listing retry: a
    fresh listing of each folder the step already listed, with the
    entries' names and first lines, or None when no folder can be
    listed now (nothing true to re-present)."""
    try:
        from app.services.system_control import system_control_service as sc
    except Exception:
        return None
    blocks: List[str] = []
    for folder in folders[:3]:
        try:
            out = sc.list_directory(folder)
        except Exception:
            continue
        if not isinstance(out, dict) or not out.get("success"):
            continue
        lines = [f"Folder {out.get('path') or folder}:"]
        for f in out.get("folders") or []:
            lines.append(f"- {f.get('name')} (folder)")
        for f in out.get("files") or []:
            first = f.get("first_line")
            lines.append(f"- {f.get('name')}"
                         + (f" (first line: {first})" if first else ""))
        blocks.append("\n".join(lines))
    if not blocks:
        return None
    return ("Established results from this step's own tool record, "
            "re-presented by the task layer:\n" + "\n".join(blocks))


# A step slower than this is named in the log (Tier 6 D4: the stalls
# under load could not be attributed to any step because nothing
# recorded per-step durations). orchestrator.py keeps the same
# threshold for stepwise tasks; keep the two in step.
SLOW_STEP_MS = 1000


def _log_slow_subtask(mission_id: str, sub: Dict[str, Any], task: Optional[Dict[str, Any]],
                      outcome: Dict[str, Any]) -> None:
    """Slow-step record: a mission step whose dispatch-to-outcome run
    took over SLOW_STEP_MS is logged (warning + a mission.slow_step
    event) with its duration and the tools its task used, so a freeze
    under load names the step that was running. Fast steps record
    nothing, and a failure to record never breaks the run."""
    try:
        started = sub.get("started_at")
        if not started:
            # The driving subtask dict predates dispatch; the persisted
            # row carries the dispatch stamp.
            stored = next((s for s in list_subtasks(mission_id) if s["id"] == sub.get("id")), None)
            started = (stored or {}).get("started_at")
        if not started:
            return
        dur_ms = int((time.time() - float(started)) * 1000)
        if dur_ms <= SLOW_STEP_MS:
            return
        tools: List[str] = []
        raw = (task or {}).get("agent_steps")
        try:
            steps = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            steps = []
        if isinstance(steps, list):
            tools = sorted({str(s.get("tool") or s.get("name")) for s in steps
                            if isinstance(s, dict) and (s.get("tool") or s.get("name"))})
        logger.warning(f"slow step: mission {mission_id} step {sub.get('idx', -1) + 1} "
                       f"({sub.get('id')}) took {dur_ms} ms; tools: "
                       f"{', '.join(tools) or 'none recorded'}")
        from app.services import event_log
        event_log.emit("mission.slow_step", actor="missions",
                       ids={"mission_id": mission_id, "task_id": sub.get("task_id")},
                       payload={"step_id": sub.get("id"),
                                "step_index": sub.get("idx", -1) + 1,
                                "duration_ms": dur_ms, "tools": tools,
                                "status": outcome.get("status")})
    except Exception:
        pass


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


_FILE_LIKE_SUFFIX_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,9}$")


def _effect_is_directory(subtask: Dict[str, Any], eff: Dict[str, Any]) -> bool:
    """Whether THIS create effect's target is a directory: the step's own
    words must present the effect's path itself as the directory being
    created. A folder the step merely works inside ("in the requested
    folder", "in the proof directory") is the container, not the
    artifact. The looser test this replaces (any mention of the words
    directory or folder anywhere in the step) misclassified T1B's
    research.md step at every layer at once (AGE-26, re-grade run 3):
    the rendered task told the worker to create a directory at the
    file's path, the task layer parsed that as a directory claim, and
    the fresh check required is_dir() of a file, so the file the task
    had verifiably written was reported not on disk while the product's
    own ledger recorded the writes. A target whose basename carries a
    file suffix (.md, .py, .txt) is a file whatever the surrounding
    prose says; an extensionless target is a directory only when the
    directory wording governs it: it names the effect's own path, or it
    is the creation the step's single create effect performs (and the
    wording does not place that creation inside another folder)."""
    raw = str(eff.get("path", "") or "")
    base = raw.rstrip("/").rsplit("/", 1)[-1]
    if not base or _FILE_LIKE_SUFFIX_RE.search(base):
        return False
    words = f"{subtask.get('title', '')} {subtask.get('instruction', '')}"
    if not re.search(r"\b(directory|folder)\b", words, re.I):
        return False
    creates = [e for e in subtask.get("expected_effects") or []
               if e.get("kind") in ("create", "modify")]
    if len(creates) == 1 and re.search(
            r"\bcreat\w*\b(?:(?! in the | inside | into ).)*\b(directory|folder)\b",
            words, re.I):
        return True
    p = os.path.expanduser(raw)
    for target in {p, base}:
        if not target:
            continue
        esc = re.escape(target)
        if re.search(r"\b(directory|folder)\b\s+(?:at\s+|named\s+|called\s+)?"
                     + esc + r"(?![\w/])", words, re.I):
            return True
        if re.search(esc + r"\s+(directory|folder)\b", words, re.I):
            return True
    return False


def _declares_directory(subtask: Dict[str, Any]) -> bool:
    """The plan's own words call one of this step's create effects a
    directory creation (the same test render_request uses when it words
    the step's task). Per effect: see _effect_is_directory."""
    return any(e.get("kind") == "create" and _effect_is_directory(subtask, e)
               for e in subtask.get("expected_effects") or [])


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
        if kind == "create" and _effect_is_directory(subtask, eff):
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
    # Origin marker (simplicity redesign Phase 2): the same stamp Phase 0
    # added for projects, so proving-run missions can be filtered out of
    # the Work feed into the Playground. Display-only; nothing is deleted.
    mcols = [r[1] for r in c.execute("PRAGMA table_info(missions)").fetchall()]
    if "origin" not in mcols:
        c.execute("ALTER TABLE missions ADD COLUMN origin TEXT DEFAULT 'user'")
    c.commit()
    return c


def is_scratch_mission(goal: str = "", origin: str = "") -> bool:
    """True for test/proving missions: origin marker, or the conservative
    legacy name check Phase 0 uses for projects applied to the goal text."""
    try:
        from app.services.task_queue import is_scratch_job
        return is_scratch_job(goal or "", origin or "user")
    except Exception:
        return (origin or "").strip().lower() in {"scratch", "test", "proving", "eval", "smoke"}


def _mission_row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["continue_on_failure"] = bool(d.get("continue_on_failure"))
    d["origin"] = d.get("origin") or "user"
    d["scratch"] = is_scratch_mission(d.get("goal") or "", d["origin"])
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
- "tools": the tools this subtask needs, by their exact names (for example "computer_write_file" for a step that writes a file, "computer_read_file" for one that only reads, "computer_list_directory" for a step that must list a folder's contents to find, inspect or classify the files in it, "computer_manage_file" or "fs_create_folder" for a step that creates a directory, "computer_manage_file" for one that deletes a file). Do not invent tool names. A step whose declared file effects need a tool its "tools" list leaves out never starts.
- "delegate": when a subtask's work is several independent read-only pieces that should be done by parallel workers (for example a sweep of many files split into ranges), do NOT leave the delegation as a sentence in the instruction. Give the subtask a "delegate" list of at least two worker briefs, each {{"task": "the one piece this worker does, with every path and detail it needs", "outcome": "what this worker must report", "facts": [], "constraints": []}}. The briefs must be disjoint: each worker gets only its own piece, never the whole job and never another worker's piece, and each brief must be fully self-contained because the worker sees nothing else. Workers run read-only in parallel, so a delegated subtask declares no expected_effects: put any file writing in its own later subtask that consumes the workers' combined result through a prior_step input. A subtask whose instruction says to delegate but that carries no "delegate" list never starts.
- When a subtask creates a test file or an executable artifact, give it a "run" effect that runs what it made: run the test file with the project's test runner, or invoke the artifact harmlessly, with cwd set to the directory the subtask creates the file in. Creating a file is not proof it works; the run is the proof.
- When a subtask researches a fact across sources, its instruction must require ranking primary sources (the official record, the issuing authority) over secondary coverage, must state that a syndicated reprint is not an independent confirmation, and must forbid presenting a figure sourced only to secondary coverage as established: if no primary source can be reached, the step reports that plainly instead of settling for the secondary figure.
- When a subtask must inspect several sources and say what each one is, write it as a classification (name every candidate with its kind), not as a search for one matching file; and when a discovery subtask's next step needs the identified file's contents, its instruction must require reading the identified file itself, not only identifying it from a listing preview.

Between 2 and 8 subtasks. Later subtasks may rely on earlier ones; subtasks run one at a time in order, and the workers inside one delegated subtask run at the same time.
Reply with JSON only: {{"subtasks": [{{"title": "...", "instruction": "...", "expected_effects": [], "inputs": [{{"source": "goal"}}], "tools": [], "delegate": []}}]}}"""


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
        # Structured delegation (grade fix cluster 2, Tier 5): the
        # fan-out a step calls for is carried as data the executor can
        # act on, not as a sentence in the instruction. Workers are
        # read-only (workers.spawn refuses any other envelope), so a
        # delegated step declares no file effects of its own, and its
        # briefs must be disjoint: two identical briefs are one worker
        # counted twice, the exact denominator fraud T5-B forbids.
        clean_delegate: List[Dict[str, Any]] = []
        raw_delegate = s.get("delegate")
        if raw_delegate is not None:
            if not isinstance(raw_delegate, list) or len(raw_delegate) > 8:
                problems.append(f"{where}: delegate must be a list of at most 8 worker briefs")
                raw_delegate = []
            elif 0 < len(raw_delegate) < 2:
                problems.append(f"{where}: delegate needs at least two worker briefs; "
                                f"one worker is not parallel delegation")
                raw_delegate = []
            seen_briefs: set = set()
            for j, b in enumerate(raw_delegate if isinstance(raw_delegate, list) else []):
                bwhere = f"{where} worker brief {j + 1}"
                if not isinstance(b, dict):
                    problems.append(f"{bwhere}: must be an object with a task")
                    continue
                btask = b.get("task")
                if not isinstance(btask, str) or not btask.strip():
                    problems.append(f"{bwhere}: task must be a non-empty string")
                    continue
                btask = btask.strip()
                if len(btask) > 2000:
                    problems.append(f"{bwhere}: task is over 2000 characters")
                    continue
                if btask in seen_briefs:
                    problems.append(f"{bwhere}: worker briefs must be disjoint; "
                                    f"this brief repeats an earlier one verbatim")
                    continue
                seen_briefs.add(btask)
                boutcome = b.get("outcome")
                if boutcome is not None and not isinstance(boutcome, str):
                    problems.append(f"{bwhere}: outcome must be a string")
                    continue
                clean_lists: Dict[str, List[str]] = {}
                bad_list = False
                for key in ("facts", "constraints"):
                    vals = b.get(key)
                    if vals is None:
                        clean_lists[key] = []
                    elif (isinstance(vals, list) and len(vals) <= 12
                          and all(isinstance(v, str) and v.strip() for v in vals)):
                        clean_lists[key] = [v.strip() for v in vals]
                    else:
                        problems.append(f"{bwhere}: {key} must be a list of strings")
                        bad_list = True
                if bad_list:
                    continue
                clean_delegate.append({"task": btask,
                                       "outcome": (boutcome or "").strip(),
                                       "facts": clean_lists["facts"],
                                       "constraints": clean_lists["constraints"]})
            if clean_delegate and clean_effects:
                problems.append(f"{where}: a delegated step declares no expected_effects "
                                f"(its workers run read-only); put the file writing in "
                                f"its own later step")
        out.append({"title": str(title).strip(), "instruction": str(instruction).strip(),
                    "expected_effects": clean_effects,
                    "inputs": clean_inputs, "tools": clean_tools,
                    "delegate": clean_delegate})
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


async def preview_plan(goal: str) -> Dict[str, Any]:
    """The validated plan for a goal, as plain step titles, without
    creating or running a mission (Phase 1: the one composer's plan
    check). Uses the same decompose() the runner plans with."""
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("Tell Xod the goal first.")
    plan = await decompose(goal, _default_ask)
    return {"goal": goal,
            "steps": [{"title": s["title"], "instruction": s["instruction"]}
                      for s in plan["subtasks"]]}


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
            if _effect_is_directory(subtask, eff):
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
_EDIT_TOOLS = ("computer_edit_file",)
_DIR_WRITE_TOOLS = ("computer_manage_file", "fs_create_folder", "computer_write_file",
                    "fs_write_workspace_file")
_DELETE_TOOLS = ("computer_manage_file",)

# Planner-facing names for the file-effect tools. The executor canonicalizes
# model tool names through its alias map (app/agent/researcher.py
# _canonicalize_tool_name); a declared capability contract is judged by the
# names its tools would actually run as, so a planner that reaches for
# computer_create_directory (mission-baba238e08, 2026-11-08: the step was
# stopped at assembly for a capability the step genuinely had) is judged
# against computer_manage_file, not against a string it never wrote.
# Mirrors the file-effect family of the executor's map; keep the two in step.
_TOOL_NAME_ALIASES = {
    "computer_create_directory": "computer_manage_file",
    "create_directory": "computer_manage_file",
    "make_directory": "computer_manage_file",
    "computer_create_folder": "computer_manage_file",
    "computer_mkdir": "computer_manage_file",
    "create_folder": "fs_create_folder",
    "mkdir": "fs_create_folder",
    "computer_create_file": "computer_write_file",
    "create_file": "computer_write_file",
    "write_file": "computer_write_file",
    "computer_delete_file": "computer_manage_file",
    "delete_file": "computer_manage_file",
    "computer_remove_file": "computer_manage_file",
    "remove_file": "computer_manage_file",
}


def _canonical_tool_name(name: str) -> str:
    n = (name or "").strip().lower()
    return _TOOL_NAME_ALIASES.get(n, n)


def _effect_tools(subtask: dict, eff: dict):
    """(tools that can perform this effect, what it does in words) — the one
    mapping both gates judge against: the declared-contract check below and
    the dispatch-time availability check (_write_tool_problem). A modify is
    performable by the edit tool as well as the whole-file writers."""
    kind = (eff.get("kind") or "").strip().lower()
    if kind == "delete":
        return _DELETE_TOOLS, "delete"
    if kind == "create" and _effect_is_directory(subtask, eff):
        return _DIR_WRITE_TOOLS, "create the directory"
    if kind == "modify":
        return _FILE_WRITE_TOOLS + _EDIT_TOOLS, "write the file"
    return _FILE_WRITE_TOOLS, "write the file"


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
        needed, what = _effect_tools(subtask, eff)
        if not any(n in names for n in needed):
            return (f"Assembly stopped this step before any task was created: subtask "
                    f"\"{subtask.get('title', '')}\" must {what} {eff.get('path', '')}, "
                    f"but none of the file tools that could do it "
                    f"({', '.join(needed)}) is available for this step's assembled "
                    f"request. A write step that cannot be equipped with file tools "
                    f"fails here, at assembly, not inside the subtask.")
    return None


# The delivered-inputs reference fence (fix round 6). The task
# queue strips lines between these markers when it parses a request
# for file/run claims; keep the two in step (task_queue.py has the
# same literals).
INPUT_FENCE_START = "----- delivered inputs (reference only; not this step's instructions) -----"
INPUT_FENCE_END = "----- end of delivered inputs -----"


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
    declared = {str(t) for t in tools if str(t).strip()}
    # Judge the contract by the names its tools would actually run as: the
    # executor canonicalizes aliases, so a declared computer_create_directory
    # counts as the directory tool it names, not as an unknown string. The
    # problem text still quotes what the planner actually declared.
    names = declared | {_canonical_tool_name(t) for t in declared}
    for eff in subtask.get("expected_effects") or []:
        if eff.get("kind") in ("create", "modify", "delete"):
            needed, what = _effect_tools(subtask, eff)
        else:
            continue
        if not any(n in names for n in needed):
            return (f"Assembly stopped this step before any task was created: subtask "
                    f"\"{subtask.get('title', '')}\" must {what} {eff.get('path', '')}, "
                    f"but its declared capability contract (tools: "
                    f"{', '.join(sorted(declared)) or 'none'}) includes none of the tools "
                    f"that could do it ({', '.join(needed)}). A step is dispatched only "
                    f"to a flow its contract equips; the contract and the effects must "
                    f"be fixed in the plan, not worked around at run time.")
    return None


# ------------------------------------------------- capability repair (AGE-32)
# D15 (AGE-32, re-grade run 6 T1B): the plan declared tools: [] for
# both research subtasks, and no layer validated the declared contract
# against the capability the step's own instruction requires. The
# file-effect gates judge declared effects, and a research step
# declares none, so the empty contract passed every gate; the steps
# ran under a contract naming no research capability, honestly
# reported they had no research access, and were recorded done on
# non-answers, with the mission's goal unreachable from that point.
# The capability a step needs is judged from what the step itself
# declares: its run effects (a command must run) and its instruction's
# own demands (a step that must report sourced facts with their URLs
# must be able to research). Where a declared contract cannot serve a
# declared need, assembly repairs the contract by adding the
# capability's canonical tool, and the repair is recorded both in the
# step's saved contract and in its assembled request. A contract that
# already serves the step's needs is untouched, a step that declares
# no contract at all is untouched (no contract line is rendered for
# it and dispatch-time tool selection governs), and a step whose
# instruction declares no capability need (a pure reasoning step) is
# never force-fed tools. File effects keep their established gate:
# _contract_tool_problem fails such a step at assembly rather than
# dispatching it unequipped.
#
# D21 (AGE-38, Tier 2 grade T2C step 2): that last sentence was the
# whole story for file-effect steps, and it is the defect. The plan
# paired a create effect for chosen.txt with a contract of
# computer_manage_file only, a tool that cannot write the file;
# assembly stopped the step, no repair ran (this repair by design
# left a step declaring file effects untouched), and the mission
# failed. The repair now covers that excluded shape as well: a step
# declaring file effects whose contract includes none of the tools
# that could serve an effect (the _effect_tools mapping both gates
# judge against) gets the canonical serving tool added, recorded
# exactly as the AGE-32 repairs are recorded. _contract_tool_problem
# still runs after the repair, so a step the repair cannot serve
# still fails at assembly rather than dispatching unequipped.
#
# AGE-77 (live re-grade 3): the same repair now also judges the
# folder-enumeration need (_instruction_needs_listing): a discovery,
# classification, or explicit listing step whose contract carries no
# listing tool gains computer_list_directory, recorded the same way.

_RESEARCH_TOOLS = ("web.run", "web_search", "tavily_search", "you_search",
                   "web_scrape")
_RUN_TOOLS = ("computer_run_command", "computer_start_job")
_RESEARCH_GATHER_RE = re.compile(
    r"\b(?:research|find|look(?:ing)? up|search(?:ing)? for|investigate|gather)\b", re.I)
_SOURCE_RE = re.compile(r"\bsources?\b", re.I)
_URL_RE = re.compile(r"\burls?\b", re.I)


def _instruction_needs_research(subtask: Dict[str, Any]) -> bool:
    """The step's own text demands externally sourced facts with
    attribution: a gathering verb, plus a source and its URL to
    report. The attribution demand is judged on the instruction, and
    the gathering verb must be the instruction's own or the title's
    imperative: a write step titled "Write research file" whose
    instruction places the sources it was handed next to their claims
    declares no research need, because nothing in it gathers anything
    (its title's "research" names the artifact, not an action). A
    step reading local files or reasoning over delivered inputs may
    say "find", but it does not require a source name and a URL for
    facts it must fetch from outside."""
    text = str(subtask.get("instruction", ""))
    title = str(subtask.get("title", "")).strip()
    gather = (_RESEARCH_GATHER_RE.search(text)
              or re.match(r"(?:research|find|look up|search|investigate|gather)\b",
                          title, re.I))
    return bool(gather and _SOURCE_RE.search(text) and _URL_RE.search(text))


# ------------------------------------------------- listing need (AGE-77)
# Live re-grade 3 (2026-11-14) on the shipped v1.1.6: both failing
# steps (T2C step 1, Tier 4 L1 step 1) were planned with the contract
# [computer_manage_file, computer_read_file] although each step's own
# instruction requires enumerating a folder ("List the text files in
# .../inbox and classify each by name"; "name and classify every
# candidate" among the files in a folder). The AGE-32/AGE-38 repair
# judged research needs, run effects and file effects, but a
# folder-enumeration need was no layer's business, so the contract
# passed assembly unchanged; on the plain-task path the T2C worker
# then made zero tool calls and stopped, asking for the listing tool
# its contract left out. The repair below extends to that need,
# judged from the step's own text the way the discovery and
# classification judgments judge it: those two demands are
# enumeration by definition, and an explicit list-the-contents
# instruction is the third shape. The explicit shape is not judged
# for a step declaring file effects: a write step placing a list it
# was handed ("write the list of delivered file names to ...") makes
# no enumeration demand of its own.

_LISTING_TOOLS = ("computer_list_directory", "computer_directory_tree",
                  "fs_list_workspace_files")
_LIST_VERB_RE = re.compile(r"\b(?:list|enumerate|inventory)\b", re.I)
_LIST_OBJECT_RE = re.compile(
    r"\b(?:files?|contents?|entries|folder|directory|inbox)\b", re.I)


def _instruction_needs_listing(subtask: Dict[str, Any]) -> bool:
    """The step's own text requires enumerating a folder's contents:
    a discovery or a classification (both inspect the candidates a
    folder holds), or an explicit instruction to list a folder's
    files or contents. Judged on the instruction and title only."""
    if (_instruction_needs_discovery(subtask)
            or _instruction_needs_classification(subtask)):
        return True
    both = (str(subtask.get("title", "")) + "\n"
            + str(subtask.get("instruction", "")))
    if not (_LIST_VERB_RE.search(both) and _LIST_OBJECT_RE.search(both)):
        return False
    return not any(e.get("kind") in ("create", "modify", "delete")
                   for e in subtask.get("expected_effects") or [])


def _capability_contract_repair(subtask: Dict[str, Any]) -> Optional[Tuple[List[str], str]]:
    """(repaired tools, repair note) when the step's declared contract
    cannot serve a capability the step itself declares, else None.
    Only a declared contract is judged: tools None means the plan
    declared no contract for this step."""
    contract = subtask.get("contract") or {}
    tools = contract.get("tools")
    if tools is None:
        return None
    declared = [str(t) for t in tools if str(t).strip()]
    canon = set(declared) | {_canonical_tool_name(t) for t in declared}
    added: List[str] = []
    needs: List[str] = []
    # A step declaring file effects is a producing step: its contract
    # is judged by the file-effect gates, and naming the artifact in
    # its instruction ("place each source name and URL next to its
    # claim" in a step writing research.md) is placement of work it
    # was handed, not a research need. The research repair is for
    # steps whose whole job is gathering, which declare no effects.
    has_file_effects = any(e.get("kind") in ("create", "modify", "delete")
                           for e in subtask.get("expected_effects") or [])
    if (not has_file_effects
            and _instruction_needs_research(subtask)
            and not any(n in _RESEARCH_TOOLS or n.startswith("browser_") for n in canon)):
        added.append("web.run")
        needs.append("research (its instruction requires sourced facts, "
                     "with the source name and URL reported)")
    if (any(e.get("kind") == "run" for e in subtask.get("expected_effects") or [])
            and not any(n in _RUN_TOOLS for n in canon)):
        added.append("computer_run_command")
        needs.append("running a command (it declares a run effect)")
    # Folder enumeration (AGE-77): a step whose own text requires
    # enumerating a folder (a discovery, a classification, or an
    # explicit listing) is contracted with the listing tool, so the
    # worker is never sent to enumerate a folder it cannot list.
    if (_instruction_needs_listing(subtask)
            and not any(n in canon or n in added for n in _LISTING_TOOLS)):
        added.append("computer_list_directory")
        needs.append("listing a folder's contents (its instruction requires "
                     "enumerating the files in a folder)")
    # File effects (AGE-38): an effect whose serving tools are all
    # absent from the contract gets the canonical serving tool, the
    # first tool in the _effect_tools mapping for that effect. An
    # effect the contract already serves, by a declared name or by
    # an alias that canonicalizes to a serving tool, is untouched,
    # and a step whose effects are all served gains nothing here.
    for eff in subtask.get("expected_effects") or []:
        if eff.get("kind") not in ("create", "modify", "delete"):
            continue
        needed, what = _effect_tools(subtask, eff)
        if any(n in canon or n in added for n in needed):
            continue
        added.append(needed[0])
        needs.append(f"{what} {eff.get('path', '')} "
                     f"(it declares a {eff.get('kind')} effect)")
    if not added:
        return None
    note = (f"Assembly note: this step's capability contract was repaired before "
            f"its task was created. The plan declared tools: "
            f"{', '.join(declared) or 'none'}, which cannot serve what this step "
            f"requires: {' and '.join(needs)}. Added to the contract: "
            f"{', '.join(added)}. The repair is recorded in the step's saved "
            f"contract as well as here.")
    return declared + added, note


# ------------------------------------------------- input adaptation (AGE-37)
# D20 (AGE-37, Tier 2 grade T2B): the plan declared the goal-named
# file sales.csv as a file input for step 1. Assembly stopped the
# step before any task was created because the file could not be
# read, and that stop was terminal: no re-plan, no adapted step, no
# discovery step ever happened, although sales-2025.csv (columns
# item,amount, honest total 12) sat in the same folder. The mission
# failed in 0.0s having made only the planning call. The stop itself
# is honest; the defect is that it is the end of the road.
#
# The adaptation surface, at that same assembly point: when a step's
# declared file input cannot be delivered, the folder the file was
# declared in is listed from disk, and a candidate is plausible only
# on evidence: a regular file in that same folder, of the same type
# (same suffix), whose stem is the declared stem or extends it at a
# name boundary (sales-2025 for sales: the dated or versioned sibling
# a folder actually holds). Exactly one plausible candidate adapts
# the step: the candidate is delivered in place of the missing file,
# the discrepancy is named in the step's instruction and in an
# assembly note in its request, and the change is recorded as plan
# version 2 in the mission's plan of record (the plan_json the
# runner already maintains, with an adaptations entry holding the
# original instruction and inputs, the folder contents the decision
# saw, and the candidate's first line), as well as in the step's
# saved contract. The missing file and its contents are never
# invented. No candidate, or more than one plausible candidate, is
# not adapted: the original assembly stop stands and the mission
# fails honestly. At most one adaptation is spent per mission, so
# an adapted step that still cannot be delivered is not adapted
# again, and no later step is adapted either.

def _plausible_file_candidates(missing: Path) -> Tuple[List[Path], List[str]]:
    """(plausible candidates, the folder's entry names) for a missing
    declared file input, read from disk at adaptation time."""
    folder = missing.parent
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name)
    except OSError:
        return [], []
    names = [e.name for e in entries]
    suffix = missing.suffix.lower()
    if not suffix:
        return [], names
    stem = missing.stem.lower()
    out: List[Path] = []
    for e in entries:
        if e == missing or not e.is_file():
            continue
        if e.suffix.lower() != suffix:
            continue
        cs = e.stem.lower()
        if (cs == stem or cs.startswith(stem + "-")
                or cs.startswith(stem + "_") or cs.startswith(stem + " ")):
            out.append(e)
    return out, names


def _file_input_adaptation(subtask: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], str, Dict[str, Any]]]:
    """(adapted subtask, assembly note, adaptation record) when the
    step declares exactly one file input that cannot be read and
    its folder holds exactly one plausible candidate for it, else
    None. Pure with respect to the mission store: the caller records
    the adaptation and enforces the one-per-mission bound."""
    contract = subtask.get("contract") or {}
    inputs = list(contract.get("inputs") or [])
    missing_idx: Optional[int] = None
    missing_ref = ""
    for i, d in enumerate(inputs):
        if not isinstance(d, dict) or d.get("source") != "file":
            continue
        ref = str(d.get("ref") or "")
        if not ref:
            continue
        try:
            Path(os.path.expanduser(ref)).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            if missing_idx is not None:
                return None   # more than one undeliverable input: not this surface
            missing_idx, missing_ref = i, ref
    if missing_idx is None:
        return None
    missing = Path(os.path.expanduser(missing_ref))
    candidates, names = _plausible_file_candidates(missing)
    if len(candidates) != 1:
        return None
    cand = candidates[0]
    try:
        cand_text = cand.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    first_line = (cand_text.splitlines() or [""])[0][:200]
    folder_names = ", ".join(names) if names else "nothing"
    adapted_instruction = (
        str(subtask.get("instruction") or "")
        + f" Adaptation (plan v2): the declared file {missing} could not be "
          f"read, so this step was adapted before any task was created. The "
          f"folder {missing.parent} contains: {folder_names}. Use the "
          f"candidate file {cand} instead of the missing one; its first line "
          f"is: {first_line}. Name the discrepancy in your report (the "
          f"declared file name, and the candidate's actual columns if they "
          f"differ from the ones the original instruction names), and if the "
          f"columns differ, work from the candidate's actual columns.")
    new_inputs = [dict(d) if isinstance(d, dict) else d for d in inputs]
    new_inputs[missing_idx] = {"source": "file", "ref": str(cand)}
    adapted = {**subtask, "instruction": adapted_instruction,
               "contract": {**contract, "inputs": new_inputs}}
    note = (f"Assembly note: this step was adapted (plan v2) before its task "
            f"was created. The plan declared the file {missing} as an input, "
            f"but it could not be read. The folder was listed from disk: "
            f"{folder_names}. The one plausible candidate, {cand}, is "
            f"delivered instead; its first line is: {first_line}. The missing "
            f"file was not invented. The adaptation is recorded in the "
            f"mission plan (version 2) and in this step's saved contract and "
            f"instruction as well as here.")
    record = {"step": int(subtask.get("idx") or 0) + 1,
              "missing": str(missing), "candidate": str(cand),
              "folder_contents": names, "candidate_first_line": first_line,
              "original_instruction": str(subtask.get("instruction") or ""),
              "original_inputs": inputs}
    return adapted, note, record


def _unreadable_declared_file_inputs(subtask: Dict[str, Any]) -> List[str]:
    """The refs of the step's declared file inputs that cannot be
    read from disk right now, in declaration order. Empty when every
    declared file input is deliverable (or none is declared)."""
    contract = subtask.get("contract") or {}
    out: List[str] = []
    for d in contract.get("inputs") or []:
        if not isinstance(d, dict) or d.get("source") != "file":
            continue
        ref = str(d.get("ref") or "")
        if not ref:
            continue
        try:
            Path(os.path.expanduser(ref)).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            out.append(ref)
    return out


def _record_plan_adaptation(mission_id: str, sub: Dict[str, Any],
                            adapted: Dict[str, Any], record: Dict[str, Any]) -> None:
    """Records an adaptation the way the plan machinery records any
    plan change: the mission's plan of record moves to version 2
    with the adapted step in place and an adaptations entry holding
    the evidence and the original, and the step's saved contract and
    instruction become the adapted ones."""
    mission = get_mission(mission_id)
    plan = (mission or {}).get("plan")
    if isinstance(plan, dict):
        plan = json.loads(json.dumps(plan))
        subs = plan.get("subtasks") or []
        idx = int(sub.get("idx") or 0)
        if 0 <= idx < len(subs) and isinstance(subs[idx], dict):
            entry = dict(subs[idx])
            entry["instruction"] = adapted.get("instruction")
            entry["inputs"] = (adapted.get("contract") or {}).get("inputs") or []
            subs[idx] = entry
            plan["subtasks"] = subs
        plan["version"] = 2
        plan.setdefault("adaptations", []).append(record)
        _save_mission(mission_id, plan=plan)
    _save_subtask(sub["id"], contract=adapted.get("contract") or {},
                  instruction=adapted.get("instruction") or "")


def assemble_step(mission_id: str, subtask: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    """Assembles one subtask's task text under its capability contract
    (fix round 6, BOARD-001 Design 1). Returns (request, delivery,
    problem): delivery is the input-delivery read-back, one record per
    declared input actually placed into the request (source, ref,
    sha256 of the delivered text); problem is a typed assembly failure
    when a declared input cannot be delivered, and no task may be
    created from it.

    The mission goal is an implicit input for steps with no file
    effects: the goal is where a brief or evidence pack the user
    supplied lives, and before this round it reached NO subtask at
    all (the BOARD-001 mission's workers assessed an evidence pack
    they had never been given, and said so). A step WITH file effects
    gets the goal only when its plan declares it: a goal normally
    names every artifact in the mission, and a write step flooded
    with all of them is a step whose request mentions other steps'
    files (the two-instance gate's proofdir step, whose request
    suddenly "mentioned note.txt", is the standing example).
    Declared prior_step and file inputs are delivered to every step
    that declares them, not only to write steps. Write steps
    additionally keep the fix round 4 behaviour: the reported
    results of earlier finished steps ride along as the content the
    write exists to land."""
    mission = get_mission(mission_id)
    goal = (mission or {}).get("goal") or ""
    subs = list_subtasks(mission_id)
    contract = subtask.get("contract") or {}
    declared: List[Dict[str, Any]] = list(contract.get("inputs") or [])
    has_file_effects = any(e.get("kind") in ("create", "modify", "delete")
                           for e in subtask.get("expected_effects") or [])
    if not has_file_effects and not any(d.get("source") == "goal" for d in declared):
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
    if sections:
        # Delivered inputs are reference material, fenced as such: the
        # task queue's claim parsers strip the fence, so a claim can
        # only come from this step's own rendered request, never from
        # what it was handed (a goal dictating an exact text for one
        # step must not become a claim against the read-back step).
        parts += ["", INPUT_FENCE_START]
        for label, text in sections:
            parts += ["", f"{label}:", text]
        parts += ["", INPUT_FENCE_END]
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


def _browser_surface_plea(result: str, subtask: Dict[str, Any]) -> bool:
    """The step's final pass ran on the browser surface and its report is
    that surface's needs-input plea for the very tools this step's work
    requires (AGE-28, re-grade run 3 T1A): the browser mission report's
    own heading, or its request for terminal and file-writing access
    "through the current browser interface". Only judged for steps that
    declare file or run effects: a research step asking its user a
    question is a genuine question, not a routing failure, and keeps
    the normal verdict paths."""
    if not result:
        return False
    if not any(e.get("kind") in ("create", "modify", "delete", "run")
               for e in subtask.get("expected_effects") or []):
        return False
    if "I need something from you" in result and "browser" in result.lower():
        return True
    low = result.lower()
    return ("current browser interface" in low
            and ("terminal" in low or "file-writing" in low
                 or "file writing" in low))


def _outcome_from_task(task: Optional[Dict[str, Any]], subtask: Dict[str, Any]) -> Dict[str, Any]:
    """What the queue decided about a finished subtask task, kept separate from
    what the agent claimed. The verdict comes from the task's final state,
    which the queue only reaches after its file-effect check has run."""
    if task is None:
        return {"status": "failed", "claimed": "", "verified_status": "unverifiable",
                "evidence": ["The task record for this step is gone, so its result cannot be checked."]}
    result = task.get("result") or ""
    if _browser_surface_plea(result, subtask):
        # The plea is not the step's work product and must not become
        # its recorded claim: the step is failed on a routing defect in
        # the machinery, named as such. The verdict-time re-check can
        # still upgrade the step afterwards if its declared effects
        # verify fresh on disk; what it can no longer do is carry a
        # browser worker's request for tools as this step's result.
        return {"status": "failed", "claimed": "", "verified_status": "contradicted",
                "evidence": ["The step's final pass ran on the browser surface, which "
                             "does not carry the tools this step's declared effects "
                             "need, and its report asked for those tools instead of "
                             "doing the work. That is a routing failure in the "
                             "machinery, not the step's work product, so the plea "
                             "is not recorded as the step's result."]}
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
        # The incompletion screen (D14, AGE-31): worker prose is
        # evidence about the work, never a veto over effects the
        # verdict itself positively re-verifies. A step whose declared
        # run effects were just re-executed above and exited as
        # required is decided by that re-execution: its report's
        # admission that it could not verify in its own environment
        # (re-grade run 5 T1A, the worker's shell had no pytest) is a
        # statement about the worker's tooling, and the re-run
        # supersedes it with fact. Where the step declares no run for
        # the verdict to re-execute, the screen keeps its force: file
        # effects in this path are checked types-only (the queue's
        # finish check owns their existence), so the report's own
        # admission remains a deciding signal there, and a step with
        # no declared effects is judged on its report and the evidence
        # its task carries, as before.
        # D23 (AGE-40): the screen judges the step against its own
        # assignment, so a create/write/run admission naming a
        # deliverable the plan assigns to a sibling step is exempt:
        # in the Tier 2 re-grade's T2B the adapted step's honest
        # report ended "I did not create summary.txt", which is true
        # and correct (summary.txt is step 2's declared effect), and
        # once the declared-input layer stopped failing the task at
        # the queue, this screen alone stood between that report and
        # the mission completing.
        signal = None if runs else _incompletion_signal(
            result, exempt_objects=_sibling_effect_names(subtask),
            step_scope_count=_step_scope_count(subtask))
        if signal:
            return {"status": "failed", "claimed": result, "verified_status": "contradicted",
                    "evidence": [f"The step's own report admits it did not finish: \"{signal}\" "
                                 "A report of incompletion cannot be verified."]}
        if not effects:
            # D19 (AGE-36): a no-effect step whose own instruction
            # demands a sourced fact is judged on whether its report
            # carries the answer (see _sourced_answer_problem). This
            # sits after the blocked and incompletion screens, so an
            # honest report of a missing input, a missing tool, or a
            # failure to finish keeps its established disposition, and
            # before the absence-of-contradiction closing below, which
            # is exactly the disposition it replaces for this shape.
            answer_problem = _sourced_answer_problem(subtask, result)
            if answer_problem:
                return {"status": "failed", "claimed": result,
                        "verified_status": "contradicted",
                        "evidence": [answer_problem]}
            # D22 (AGE-39): a no-effect step whose own instruction
            # demands a discovery is judged on whether its report
            # actually identifies the target (see
            # _discovery_answer_problem), for the same reason and in
            # the same place: the research scope above does not cover
            # a discovery instruction, so without this the closing
            # branch below would record an admitted non-discovery done
            # on absence of contradiction.
            # Cluster 3 (Tier 4 grade T4-L1): a multi-source
            # classification step is judged on whether its report
            # classifies the candidates, never on the single-target
            # discovery standard below.
            classification_problem = _classification_answer_problem(subtask, result)
            if classification_problem:
                return {"status": "failed", "claimed": result,
                        "verified_status": "contradicted",
                        "evidence": [classification_problem]}
            discovery_problem = _discovery_answer_problem(subtask, result, task)
            if discovery_problem:
                return {"status": "failed", "claimed": result,
                        "verified_status": "contradicted",
                        "evidence": [discovery_problem]}
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
            # from the recorded text. Planner instruction text on disk is
            # not a timing artifact and still blocks verification, checked
            # fresh here as well.
            #
            # D14 (AGE-31): the incompletion prose screen no longer sits
            # in this branch. The fresh check below re-verifies every
            # declared effect (existence, dictated content, type, and
            # runs re-executed), so it decides: a step whose effects
            # verify is verified whatever its report admits (re-grade
            # run 5 T1A step 3 admitted it could not verify in its own
            # shell while the pinned run effect passed here), and a step
            # whose effects do not verify fails on the fresh problems,
            # the true reasons, not on its wording. Run 4 and run 5
            # graded the identical step opposite ways on the worker's
            # phrasing alone while this screen sat ahead of the check;
            # prose is evidence about the work, never a veto over
            # checkable effects. The screen keeps its force where no
            # declared effects exist to check (the branch below) and in
            # the done-task path for steps the verdict does not itself
            # re-verify.
            blocked = _blocked_signal(claimed)
            if blocked:
                return {"status": "blocked", "claimed": claimed.strip(),
                        "verified_status": "blocked",
                        "evidence": [f"The step reported itself blocked: \"{blocked}\" "
                                     "A step that could not run for a missing input or a "
                                     "missing tool is recorded blocked, not done."]}
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
    if task["status"] == "failed" and "stopped making progress" in result:
        # D16 (AGE-33, re-grade run 6 T1B): the task's agent stream died
        # mid-step inside a healthy server; the queue re-drove it within
        # its recovery budget and then stopped it with this result. The
        # stop is a verdict about the run, not the work: where the step
        # declares effects, the fresh declared-effect check decides
        # whether the step's deliverable landed before the stream died
        # (in the graded run research.md was on disk, written by the
        # step itself, while its mission failed on the stop). A step
        # whose effects do not verify fresh keeps the failed verdict
        # below with its true reason, and a deliverable carrying planner
        # instruction text is never upgraded.
        if (subtask.get("expected_effects")
                and not _content_leak_for_subtask(subtask)):
            fresh = _fresh_effect_problems(subtask)
            if not fresh:
                return {"status": "done", "claimed": "",
                        "verified_status": "verified",
                        "evidence": ["The step's task stopped making progress and Xod "
                                     "stopped it, but a fresh check at verdict time "
                                     "found every declared effect of this step true "
                                     "on disk: the work landed before the task's "
                                     "stream died, so the step is verified on its "
                                     "effects rather than failed on its run."]}
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
                 clock: Optional[Callable[[], float]] = None,
                 worker_timeout: Optional[float] = None,
                 file_input_retry_window_sec: float = DECLARED_FILE_RETRY_WINDOW_SEC,
                 file_input_retry_poll_sec: float = DECLARED_FILE_RETRY_POLL_SEC):
        self._queue = queue
        self._ask = ask or _default_ask
        self.poll_sec = poll_sec
        # Delegated steps (grade fix cluster 2): the per-step budget for
        # workers.coordinate; None keeps the workers module's own
        # WORKER_TIMEOUT_SEC. Injectable so tests run the fan-out fast.
        self.worker_timeout = worker_timeout
        # Transient declared-file retry knobs (cluster 5; see the
        # DECLARED_FILE_RETRY_WINDOW_SEC note at the top of this
        # module). Injectable so tests can run the window short.
        self.file_input_retry_window_sec = file_input_retry_window_sec
        self.file_input_retry_poll_sec = file_input_retry_poll_sec
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
                       json.dumps({"inputs": s.get("inputs") or [], "tools": s.get("tools"),
                                   "delegate": s.get("delegate") or []}),
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

    async def _delegated_outcome(self, mission_id: str, sub: Dict[str, Any],
                                 briefs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Executes one delegated subtask through workers.coordinate
        (grade fix cluster 2, Tier 5) and returns its outcome in the
        same shape _outcome_from_task returns for a task-backed step.

        The step is judged on ITS OWN scope: the denominator is this
        step's authored briefs, nothing else. Every brief becomes one
        worker row (parented on a coordinator row parented on this
        subtask), the workers run in parallel under the read-only
        envelope (T5-C), a failed worker stays in the denominator and
        fails the step rather than vanishing (T5-B), and the step's
        claimed result is the coordinator's rendered aggregation of
        the workers' reports. There is no self-execution fallback: a
        step that calls for delegation but carries no usable briefs,
        or whose fan-out does not spawn every authored worker, fails
        here saying exactly that."""
        def _failed(evidence: List[str], claimed: str = "") -> Dict[str, Any]:
            return {"status": "failed", "claimed": claimed,
                    "verified_status": "failed", "evidence": evidence}

        if not briefs:
            return _failed([
                f"Dispatch stopped this step before any task was created: subtask "
                f"\"{sub.get('title', '')}\" calls for delegated parallel workers in its "
                f"instruction, but its plan carries no structured worker briefs (a "
                f"\"delegate\" list), so there is nothing for the executor to spawn. "
                f"The step was NOT carried out by the parent itself: self-executing a "
                f"step that was planned as delegated would hide the non-delegation. "
                f"Re-plan the step with one disjoint brief per worker."])
        if len(briefs) < 2:
            return _failed([
                f"Dispatch stopped this step before any worker was spawned: subtask "
                f"\"{sub.get('title', '')}\" carries only {len(briefs)} worker brief(s); "
                f"parallel delegation needs at least two disjoint briefs. The step was "
                f"not self-executed instead."])
        tasks = [str(b.get("task") or "").strip() for b in briefs]
        if len(set(tasks)) != len(tasks):
            return _failed([
                f"Dispatch stopped this step before any worker was spawned: subtask "
                f"\"{sub.get('title', '')}\" carries duplicate worker briefs; briefs must "
                f"be disjoint, so one worker's work is never counted twice. The step "
                f"was not self-executed instead."])
        if sub.get("expected_effects"):
            return _failed([
                f"Dispatch stopped this step before any worker was spawned: subtask "
                f"\"{sub.get('title', '')}\" is delegated but declares file effects; "
                f"delegated workers run read-only (the T5-C envelope) and cannot "
                f"perform them. The write belongs in its own later step."])
        from app.services import workers as W
        job = f"{sub.get('title', '')}: {sub.get('instruction', '')}".strip(": ").strip()
        kwargs: Dict[str, Any] = {}
        if self.worker_timeout is not None:
            kwargs["timeout"] = self.worker_timeout
        # A fan-out interrupted by a restart keeps the briefs its
        # workers already finished (the orchestrator's own rule for
        # its fan-outs, mirrored here): only unfinished briefs re-run.
        try:
            prev = [w for w in W.list_workers(sub["id"])
                    if w["role"] == "coordinator"
                    and w["state"] in ("interrupted", "running")]
            if prev:
                kwargs["reuse_from"] = prev[-1]["id"]
        except Exception:
            pass
        try:
            result = await W.coordinate(sub["id"], job, briefs, **kwargs)
        except Exception as e:
            return _failed([
                f"Delegated execution for subtask \"{sub.get('title', '')}\" failed "
                f"inside workers.coordinate: {e}. No result is claimed for this step."])
        rows = result.get("workers") or []
        if len(rows) != len(briefs):
            return _failed([
                f"Delegated execution for subtask \"{sub.get('title', '')}\" spawned "
                f"{len(rows)} worker(s) for {len(briefs)} authored brief(s); a fan-out "
                f"that does not spawn every authored worker is a failed delegation, "
                f"not a step the parent quietly finishes itself."],
                claimed=W.render_result(result))
        done_n = sum(1 for w in rows if w.get("outcome") == "done")
        evidence = [
            f"Delegated execution: coordinator {result.get('coordinator')} spawned "
            f"{len(rows)} worker(s) for this step through workers.coordinate; "
            f"{done_n}/{len(rows)} done. The workers are recorded as rows in the "
            f"workers table: {', '.join(str(w.get('id')) for w in rows)}."]
        for f in result.get("failures") or []:
            evidence.append(f"Worker did not deliver: {f.get('task')}: "
                            f"{f.get('outcome')} ({f.get('why')})")
        rendered = W.render_result(result)
        if result.get("outcome") == "done":
            return {"status": "done", "claimed": rendered,
                    "verified_status": "verified" if result.get("evidence") else "unverifiable",
                    "evidence": evidence}
        return _failed(evidence + [
            f"The delegated outcome was \"{result.get('outcome')}\": {done_n} of "
            f"{len(rows)} workers delivered, so this step's own scope is not "
            f"complete. The failed workers stay in the count above; the step is "
            f"recorded failed, never done on a partial fan-out."], claimed=rendered)

    async def _retry_transient_file_inputs(
            self, mission_id: str, sub: Dict[str, Any],
            request: str, delivery: List[Dict[str, Any]],
            input_problem: Optional[str]) -> Tuple[str, List[Dict[str, Any]], Optional[str], Optional[str]]:
        """Bounded re-assembly for a step stopped only by a declared
        file input that cannot be read (cluster 5; see the
        DECLARED_FILE_RETRY_WINDOW_SEC note). Polls the declared
        files from disk and re-assembles this step alone until every
        declared input is deliverable or the window expires. Returns
        (request, delivery, input_problem, retry_note): on recovery
        the problem is cleared and the note names the wait, so the
        step's request records that its input arrived late; on
        expiry the original request, delivery and problem are
        returned unchanged, so the honest assembly stop stands
        exactly as before. A problem that is not (or not only) an
        unreadable declared file input is returned at once: this
        surface retries one thing, a transiently absent source."""
        missing = _unreadable_declared_file_inputs(sub)
        if not input_problem or not missing:
            return request, delivery, input_problem, None
        window = max(0.0, float(self.file_input_retry_window_sec))
        poll = max(0.01, float(self.file_input_retry_poll_sec))
        started = time.monotonic()
        while time.monotonic() - started < window:
            await asyncio.sleep(min(poll, window - (time.monotonic() - started)))
            if _unreadable_declared_file_inputs(sub):
                continue
            new_request, new_delivery, new_problem = assemble_step(mission_id, sub)
            if new_problem:
                # The files arrived but assembly now stops on a
                # different ground: that stop is the honest one.
                return new_request, new_delivery, new_problem, None
            waited = time.monotonic() - started
            logger.warning(
                f"Mission {mission_id}: step {int(sub.get('idx') or 0) + 1} "
                f"({sub.get('title')}) had declared file input(s) "
                f"{', '.join(missing)} unreadable at first assembly; "
                f"deliverable after {waited:.0f}s of bounded retry, "
                "the step now starts with its inputs delivered")
            note = (f"Assembly note: this step's declared file input "
                    f"{', '.join(missing)} could not be read when the step was "
                    f"first assembled. Assembly was retried for this step alone "
                    f"for {waited:.0f} seconds (bounded at "
                    f"{window:.0f} seconds), the file was then readable, and "
                    f"its actual contents are delivered above as the declared "
                    f"input. Nothing was substituted or invented during the "
                    f"wait.")
            return new_request, new_delivery, None, note
        logger.warning(
            f"Mission {mission_id}: step {int(sub.get('idx') or 0) + 1} "
            f"({sub.get('title')}) declared file input(s) "
            f"{', '.join(missing)} still unreadable after the bounded "
            f"{window:.0f}s assembly retry; the assembly stop stands")
        return request, delivery, input_problem, None

    async def _poll_task(self, mission_id: str, sub: Dict[str, Any]):
        """Polls the subtask's current task to a terminal state.
        Returns (task, pause_exit): pause_exit is None when the task
        reached a terminal state; otherwise it is the value
        _run_subtask returns for the pause (True: the wedge verdict
        was recorded by pause supervision; False: the mission was
        saved paused on a wait)."""
        while True:
            task = self.queue.get(sub["task_id"])
            status = (task or {}).get("status")
            if status == "waiting":
                verdict = self._supervise_pause(mission_id, sub, task)
                if verdict == "resume":
                    continue   # the task is queued again; keep watching it in this drive
                if verdict == "fail":
                    return task, True   # the wedge verdict is recorded; the mission is finished or moves on
                _save_subtask(sub["id"], status="waiting")
                _save_mission(mission_id, state="paused")
                return task, False
            if task is None or status in _TERMINAL_TASK:
                return task, None
            if status in ("queued", "working") and sub["status"] != status:
                _save_subtask(sub["id"], status=status)
            await asyncio.sleep(self.poll_sec)

    def _outcome_retry_task(self, mission_id: str, sub: Dict[str, Any],
                            task: Optional[Dict[str, Any]],
                            outcome: Dict[str, Any]):
        """The step's one bounded outcome retry (AGE-77; see the
        outcome-retry note at _missing_capability_tool). When the
        finished outcome is a missing-capability plea the machinery
        can repair, or a disclaimer of listings the step's own record
        shows it received, the repair is made, recorded in the step's
        saved contract (which also spends the retry), a fresh task is
        created for the step under the repaired contract or with the
        received results re-presented, and (new task, evidence line,
        new contract) is returned; the caller polls the new task and
        lets its outcome replace the first pass's. None when the
        outcome is neither shape, when the retry is already spent,
        or when the retry's own assembly cannot deliver the step's
        declared inputs (the first outcome then stands)."""
        if not isinstance(outcome, dict) \
                or outcome.get("status") not in ("failed", "blocked"):
            return None
        contract = dict(sub.get("contract") or {})
        if contract.get("outcome_retry"):
            return None
        note = None
        evidence_line = None
        missing_tool = _missing_capability_tool(sub, outcome)
        if missing_tool:
            declared = [str(t) for t in contract.get("tools") or []
                        if str(t).strip()]
            contract["tools"] = declared + [missing_tool]
            contract["outcome_retry"] = {"kind": "capability",
                                         "tool": missing_tool,
                                         "at": time.time()}
            note = (f"Capability retry note: this step's first pass ended "
                    f"with a report that a capability was missing from its "
                    f"declared contract: it asked for {missing_tool}, which "
                    f"the contract did not include. The contract was "
                    f"repaired before this retry (added: {missing_tool}; "
                    f"the repair is recorded in the step's saved contract), "
                    f"and the declared contract above is the contract of "
                    f"record for this pass. This is the step's one bounded "
                    f"capability retry: a second missing-capability report "
                    f"ends the step.")
            evidence_line = (f"The step's first pass ended {outcome['status']} "
                             f"on a missing-capability report naming "
                             f"{missing_tool}; the machinery repaired the "
                             f"step's declared contract (added {missing_tool}) "
                             f"and re-ran the step once. This outcome is from "
                             f"that retry.")
        else:
            listings = _successful_listings(task)
            disclaimer = _listing_disclaimer(outcome.get("claimed")) \
                if listings else None
            if not listings or not disclaimer:
                return None
            representation = _render_listing_representation(listings)
            if not representation:
                return None
            contract["outcome_retry"] = {"kind": "listing_represent",
                                         "folders": listings,
                                         "at": time.time()}
            note = (f"Re-presentation note: this step's first pass reported "
                    f"that listing results were not available "
                    f"(\"{disclaimer}\"), but the step's own tool record "
                    f"shows the listing succeeded.\n{representation}\n"
                    f"These names are established results of this step, not "
                    f"unavailable information: a report that the names in a "
                    f"listed folder were not available contradicts the "
                    f"step's own record and will fail the step again. Read "
                    f"the files themselves and finish the step. This is "
                    f"the step's one bounded retry.")
            evidence_line = (f"The step's first pass ended {outcome['status']} "
                             f"on a report disclaiming listing results the "
                             f"step had already received "
                             f"({', '.join(listings)}); the machinery "
                             f"re-presented those results and re-ran the "
                             f"step once. This outcome is from that retry.")
        retry_sub = {**sub, "contract": contract}
        request, delivery, problem = assemble_step(mission_id, retry_sub)
        if problem:
            return None
        _save_subtask(sub["id"], contract=contract)
        request = request + "\n\n" + note
        new_task = self.queue.create(
            request,
            title=f"{sub['title']} (mission step {sub['idx'] + 1})",
            origin=(get_mission(mission_id) or {}).get("origin") or "user")
        _save_subtask(sub["id"], task_id=new_task["id"], status="queued",
                      started_at=time.time(), input_delivery=delivery)
        return new_task, evidence_line, contract

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
            # Capability repair (AGE-32): a declared contract that
            # cannot serve the capability this step itself declares is
            # repaired before assembly renders it, and the repaired
            # contract is what gets saved, so the executed contract of
            # record is the one that can serve the step.
            repair_note = None
            repair = _capability_contract_repair(sub)
            if repair:
                new_tools, repair_note = repair
                contract = {**(sub.get("contract") or {}), "tools": new_tools}
                _save_subtask(sub["id"], contract=contract)
                sub = {**sub, "contract": contract}
            request, delivery, input_problem = assemble_step(mission_id, sub)
            if repair_note:
                request = request + "\n\n" + repair_note
            # Input adaptation (AGE-37): a declared file input that
            # cannot be delivered is not the end of the road when
            # the folder itself holds exactly one plausible
            # candidate for it. At most one adaptation per mission:
            # a plan that already carries an adaptation record has
            # spent it, and the assembly stop then stands as before.
            if input_problem:
                plan_now = (get_mission(mission_id) or {}).get("plan") or {}
                if not plan_now.get("adaptations"):
                    adaptation = _file_input_adaptation(sub)
                    if adaptation:
                        adapted_sub, adaptation_note, record = adaptation
                        _record_plan_adaptation(mission_id, sub, adapted_sub, record)
                        sub = adapted_sub
                        request, delivery, input_problem = assemble_step(mission_id, sub)
                        if repair_note:
                            request = request + "\n\n" + repair_note
                        request = request + "\n\n" + adaptation_note
            # Transient declared file inputs (cluster 5): the stop
            # still stands when the adaptation does not apply, but a
            # declared source that is only transiently absent gets
            # one bounded assembly retry for this step alone before
            # the stop becomes the step's recorded failure. The step
            # never starts undelivered: recovery means every declared
            # input was actually read into the request.
            if input_problem:
                (request, delivery, input_problem,
                 retry_note) = await self._retry_transient_file_inputs(
                     mission_id, sub, request, delivery, input_problem)
                if retry_note:
                    if repair_note:
                        request = request + "\n\n" + repair_note
                    request = request + "\n\n" + retry_note
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
            # Delegated steps (grade fix cluster 2, Tier 5): a subtask
            # whose plan carries structured worker briefs, or whose
            # instruction calls for delegation at all, never becomes a
            # single self-executing queue task. It runs through
            # workers.coordinate (or fails honestly there when the
            # briefs are missing or unusable), with the workers table
            # as its evidence.
            briefs = _delegation_briefs(sub)
            if briefs or _calls_for_delegation(sub):
                outcome = await self._delegated_outcome(mission_id, sub, briefs)
                _save_subtask(sub["id"], status=outcome["status"],
                              claimed=outcome["claimed"],
                              verified_status=outcome["verified_status"],
                              evidence=outcome["evidence"],
                              started_at=time.time(), finished_at=time.time())
                _repair_finished_verdict(mission_id)
                if outcome["status"] in ("failed", "blocked"):
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
                                     title=f"{sub['title']} (mission step {sub['idx'] + 1})",
                                     origin=(get_mission(mission_id) or {}).get("origin") or "user")
            _save_subtask(sub["id"], task_id=task["id"], status="queued",
                          started_at=time.time(), input_delivery=delivery)
            sub = {**sub, "task_id": task["id"]}
        task, pause_exit = await self._poll_task(mission_id, sub)
        if pause_exit is not None:
            return pause_exit
        outcome = _outcome_from_task(task, sub)
        # Outcome retry (AGE-77): a first pass that ended on a
        # missing-capability plea the machinery can repair, or on a
        # disclaimer of listings the step's own record shows it
        # received, is re-run once under the repair; the retry's
        # outcome replaces the first pass's, with the retry named in
        # its evidence. Every other outcome flows through unchanged.
        retry = self._outcome_retry_task(mission_id, sub, task, outcome)
        if retry is not None:
            new_task, retry_line, new_contract = retry
            sub = {**sub, "task_id": new_task["id"], "contract": new_contract}
            task, pause_exit = await self._poll_task(mission_id, sub)
            if pause_exit is not None:
                return pause_exit
            outcome = _outcome_from_task(task, sub)
            outcome["evidence"] = list(outcome.get("evidence") or []) + [retry_line]
        _save_subtask(sub["id"], status=outcome["status"], claimed=outcome["claimed"],
                      verified_status=outcome["verified_status"], evidence=outcome["evidence"],
                      finished_at=time.time())
        _log_slow_subtask(mission_id, sub, task, outcome)
        # Fix round 5: if a stale peer process already finished this
        # mission from its own older composition of this same outcome,
        # the verdict of record is reconciled with the fresh outcome
        # just saved (a no-op when the mission is still running).
        _repair_finished_verdict(mission_id)
        if outcome["status"] in ("failed", "blocked"):
            mission = get_mission(mission_id)
            if not mission["continue_on_failure"]:
                remaining = [r for r in list_subtasks(mission_id) if r["status"] in ("pending", "queued")]
                # Blocked-state continuation (Section 2b): if a step is `blocked` and later subtasks
                # explicitly declare independent contracts (e.g. `independent: True` or `inputs`
                # that only reference `goal` and not the blocked step or its file paths), mark only
                # the dependent subtasks `blocked` and allow the independent subtasks to run!
                blocked_paths = {
                    str(e.get("path") or "")
                    for e in (sub.get("expected_effects") or [])
                    if isinstance(e, dict) and e.get("path")
                }
                def _is_independent_of_blocked(rest_sub: Dict[str, Any]) -> bool:
                    if outcome["status"] != "blocked":
                        return False
                    contract = rest_sub.get("contract") or {}
                    if contract.get("independent") is True:
                        return True
                    inputs = contract.get("inputs") or []
                    if not inputs:
                        return False
                    for inp in inputs:
                        if not isinstance(inp, dict):
                            continue
                        src = inp.get("source")
                        ref = str(inp.get("ref") or "")
                        if src == "prior_step" and ref in ("", str(sub["idx"] + 1), sub.get("id", ""), sub.get("title", "")):
                            return False
                        if src == "file" and (not ref or ref in blocked_paths):
                            return False
                    return True

                if outcome["status"] == "blocked" and any(_is_independent_of_blocked(r) for r in remaining):
                    for rest in remaining:
                        if not _is_independent_of_blocked(rest):
                            _save_subtask(
                                rest["id"],
                                status="blocked",
                                verified_status="blocked",
                                evidence=[f"Blocked by Step {sub['idx'] + 1} ({sub['title']})"],
                                finished_at=time.time(),
                            )
                    return True

                for rest in remaining:
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
        if task.get("waiting_kind") == "question" and self._auto_answer_question(
                mission_id, fresh, task, waited):
            # The question carried its own best answer and nobody gave
            # a better one in time: the step's one automatic
            # intervention is the answer itself, spent like the resume.
            _save_subtask(sub["id"], paused_at=None,
                          pause_resume_attempts=max(attempts, 1))
            return "resume"
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

    def _auto_answer_question(self, mission_id: str, sub: Dict[str, Any],
                              task: Dict[str, Any], waited: float) -> bool:
        """Answers a waiting locate question with its strongest match,
        through the queue's own finish path; True when it answered.

        The person's standing needs-you rule: when a question carries
        its own candidates (a locate "ask": two close matches), the AI
        guesses the strongest match and keeps moving, and logs the
        guess. The shipped behaviour spent the one automatic resume
        and then failed the mission when the resumed task waited again
        (mission-5bf2c71b83, 2026-11-08: two files with the same name,
        nobody answered, the mission died on a question it could have
        answered itself). Only a question wait reaches this, only when
        the task's stored evidence holds an ask payload with
        candidates, and the finish is the queue's own _finish (the
        same path a person's resolving answer takes), never an
        attribution to the person: the result text names the pick as
        Xod's guess and names the matches not picked. Approval waits
        never reach here (they return earlier in _supervise_pause) and
        are never auto-answered."""
        raw = task.get("evidence_json")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            data = None
        entries = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        payload = None
        for entry in entries:  # the queue's own tag for a locate payload first
            if isinstance(entry, dict) and entry.get("tool") == "locate_files":
                p = entry.get("payload")
                if isinstance(p, dict) and p.get("status") == "ask" and p.get("candidates"):
                    payload = p
                    break
        if payload is None:
            for entry in entries:
                p = entry.get("payload") if isinstance(entry, dict) else None
                if isinstance(p, dict) and p.get("status") == "ask" and p.get("candidates"):
                    payload = p
                    break
        if payload is None:
            return False
        cands = [c for c in payload["candidates"] if isinstance(c, dict) and c.get("path")]
        if not cands:
            return False
        # The strongest match: the highest locate score; the payload's
        # own order breaks ties (it arrives ranked).
        chosen = max(enumerate(cands),
                     key=lambda t: (float(t[1].get("score") or 0), -t[0]))[1]
        finish = getattr(self.queue, "_finish", None)
        if not callable(finish):
            return False
        from app.services import decisive_evidence
        found = dict(payload)
        found["status"] = "found"
        found["candidates"] = [chosen]
        found.pop("question", None)
        block = decisive_evidence.render_locate(found)
        if not block or "Not found" in block:
            block = (f"**{chosen.get('name') or 'file'}**\n"
                     f"   Path: {chosen.get('path') or ''}")
        others = [c for c in cands if c is not chosen]
        note = (f"Nobody answered this question within {int(waited)} seconds, so Xod "
                f"answered it itself with the strongest match, per the standing rule "
                f"that Xod guesses the strongest match and keeps moving: "
                f"**{chosen.get('name') or 'file'}** at `{chosen.get('path')}`.")
        if others:
            note += (" The other close match" + ("es were" if len(others) > 1 else " was")
                     + ": " + ", ".join(f"**{c.get('name') or 'file'}** at `{c.get('path')}`"
                                        for c in others)
                     + ". This pick is Xod's best guess, not the person's answer; "
                       "if it is the wrong file, that is the pick to correct.")
        try:
            finish(sub["task_id"], "done", note + "\n\n" + block,
                   note="Xod answered its own question (strongest match)")
        except Exception:
            logger.exception(f"Mission {mission_id}: the automatic answer of "
                             f"task {sub['task_id']} raised")
            return False
        logger.warning(f"Mission {mission_id}: step {sub['idx'] + 1} ({sub['title']}) "
                       f"sat waiting {int(waited)}s on a question nobody answered; "
                       f"Xod picked the strongest match itself: {chosen.get('path')}")
        try:
            from app.services import event_log
            event_log.emit("mission.auto_answer", actor="missions",
                           ids={"mission_id": mission_id, "task_id": sub["task_id"]},
                           payload={"question": payload.get("question"),
                                    "picked": chosen.get("path"),
                                    "candidates": [c.get("path") for c in cands],
                                    "waited_sec": int(waited)})
        except Exception:
            pass
        return True

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
        if sub.get("task_id") and hasattr(self.queue, "clear_waiting"):
            try:
                self.queue.clear_waiting(sub["task_id"], reason=why, status="failed")
            except Exception:
                logger.debug("clear_waiting on wedged subtask failed", exc_info=True)
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
        # Question lifecycle cleanup (Section 2b): when a mission ends, ensure no
        # subtask's underlying task is left hanging in `waiting` with an open question.
        if hasattr(self.queue, "clear_waiting"):
            for s in subs:
                tid = s.get("task_id")
                if tid:
                    try:
                        self.queue.clear_waiting(tid, reason=reason or f"Mission {state}", status="failed" if state == "failed" else "done")
                    except Exception:
                        pass
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


def create_mission(goal: str, continue_on_failure: bool = False, origin: str = "user") -> Dict[str, Any]:
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("Tell Xod the goal first.")
    mid = f"mission-{uuid.uuid4().hex[:10]}"
    now = time.time()
    c = _conn()
    c.execute("INSERT INTO missions (id, goal, state, continue_on_failure, created_at, updated_at, origin) "
              "VALUES (?,?,?,?,?,?,?)", (mid, goal, "planning", 1 if continue_on_failure else 0, now, now, origin or "user"))
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
