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
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.paths import DATA_DIR

logger = logging.getLogger("xod.missions")
DB = DATA_DIR / "xod.db"

MISSION_STATES = ("planning", "running", "paused", "done", "failed")
_EFFECT_KINDS = ("create", "modify", "delete")
_TERMINAL_TASK = ("done", "failed", "cancelled")


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
    for k in ("expected_effects", "evidence"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except Exception:
            d[k] = []
    return d


def _save_mission(mission_id: str, **fields: Any) -> None:
    for k in ("plan", "report"):
        if k in fields:
            fields[k + "_json"] = json.dumps(fields.pop(k)) if fields[k] is not None else None
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    c = _conn()
    c.execute(f"UPDATE missions SET {cols} WHERE id = ?", (*fields.values(), mission_id))
    c.commit()
    c.close()


def _save_subtask(subtask_id: str, **fields: Any) -> None:
    for k in ("expected_effects", "evidence"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    cols = ", ".join(f"{k} = ?" for k in fields)
    c = _conn()
    c.execute(f"UPDATE mission_subtasks SET {cols} WHERE id = ?", (*fields.values(), subtask_id))
    c.commit()
    c.close()


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
- When a subtask creates a test file or an executable artifact, give it a "run" effect that runs what it made: run the test file with the project's test runner, or invoke the artifact harmlessly, with cwd set to the directory the subtask creates the file in. Creating a file is not proof it works; the run is the proof.

Between 2 and 8 subtasks. Later subtasks may rely on earlier ones; nothing runs at the same time.
Reply with JSON only: {{"subtasks": [{{"title": "...", "instruction": "...", "expected_effects": []}}]}}"""


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
                if not (cwd.startswith("/") or cwd.startswith("~/")):
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
            if not (path.startswith("/") or path.startswith("~/")):
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
            clean_effects.append({"kind": kind, "path": path, "content": content})
        out.append({"title": str(title).strip(), "instruction": str(instruction).strip(),
                    "expected_effects": clean_effects})
    if problems:
        return None, problems
    return {"subtasks": out}, []


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
        elif content is not None and "\n" not in content.strip():
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
            cwd = os.path.expanduser(str(e.get("cwd", "")))
            evidence.append(f"The queue also ran the declared check: '{e.get('command', '')}' "
                            f"from {cwd} exited with code {e.get('expect_exit', 0)}, "
                            "the code the plan required.")
        if not evidence:
            evidence = ["This step claimed no file effects, and the queue's check found "
                        "nothing on disk to contradict the report."]
        return {"status": "done", "claimed": result, "verified_status": "verified", "evidence": evidence}
    if task["status"] == "cancelled":
        return {"status": "failed", "claimed": result, "verified_status": "failed",
                "evidence": ["The task was cancelled before it finished."]}
    marker = "Not finished:"
    if marker in result:
        claimed, _, tail = result.partition(marker)
        problems = [p.strip() for p in tail.strip().split(";") if p.strip()]
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

    def __init__(self, queue: Any = None, ask: Optional[Callable] = None, poll_sec: float = 1.0):
        self._queue = queue
        self._ask = ask or _default_ask
        self.poll_sec = poll_sec

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
        c.execute("UPDATE missions SET plan_json = ?, state = 'running', started_at = ?, updated_at = ? "
                  "WHERE id = ?", (json.dumps(plan), now, now, mission["id"]))
        for i, s in enumerate(plan["subtasks"]):
            c.execute("INSERT INTO mission_subtasks (id, mission_id, idx, title, instruction, "
                      "expected_effects, status) VALUES (?,?,?,?,?,?,?)",
                      (f"msub-{uuid.uuid4().hex[:10]}", mission["id"], i, s["title"], s["instruction"],
                       json.dumps(s["expected_effects"]), "pending"))
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
            current = next((s for s in subs if s["status"] not in ("done", "failed", "skipped")), None)
            if current is None:
                self._finish(mission_id)
                return
            if not await self._run_subtask(mission_id, current):
                return   # paused on a waiting task; a later drive resumes

    async def _run_subtask(self, mission_id: str, sub: Dict[str, Any]) -> bool:
        """Works one subtask to a recorded outcome. False means the mission
        paused because the subtask's task is waiting on the user."""
        if not sub.get("task_id"):
            task = self.queue.create(render_request(sub),
                                     title=f"{sub['title']} (mission step {sub['idx'] + 1})")
            _save_subtask(sub["id"], task_id=task["id"], status="queued",
                          started_at=time.time())
            sub = {**sub, "task_id": task["id"]}
        while True:
            task = self.queue.get(sub["task_id"])
            status = (task or {}).get("status")
            if status == "waiting":
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
        if outcome["status"] == "failed":
            mission = get_mission(mission_id)
            if not mission["continue_on_failure"]:
                for rest in list_subtasks(mission_id):
                    if rest["status"] in ("pending", "queued"):
                        _save_subtask(rest["id"], status="skipped")
                self._finish(mission_id,
                             reason=f"Step {sub['idx'] + 1} ({sub['title']}) failed: "
                                    + (outcome["evidence"][0] if outcome["evidence"] else "see the step report"))
                return True
        return True

    # ------------------------------------------------------------ finishing
    def _finish(self, mission_id: str, reason: Optional[str] = None) -> None:
        mission = get_mission(mission_id)
        if mission["state"] in ("done", "failed") and mission.get("finished_at"):
            return   # already finished (a stop-on-failure finish, then the loop's own)
        subs = mission["subtasks"]
        failed = [s for s in subs if s["status"] == "failed"]
        if reason is None and failed:
            reason = (f"{len(failed)} of {len(subs)} steps failed; the rest were run "
                      "because this mission was set to keep going past failures.")
        state = "failed" if (reason or failed) else "done"
        finished = time.time()
        _save_mission(mission_id, state=state, finished_at=finished,
                      failure_reason=reason if state == "failed" else None)
        report = build_report(get_mission(mission_id))
        _save_mission(mission_id, report=report)


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
        },
        "started_at": started,
        "finished_at": finished,
        "duration_sec": round(finished - started, 1) if started and finished else None,
        "usage": _usage_section(mission["id"]),
        "how_to_read": ("claimed_result is what the agent reported. verified_status and evidence "
                        "are what the task queue's file-effect check found on disk. A step with no "
                        "file effects can only be verified as 'nothing contradicted it'."),
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
