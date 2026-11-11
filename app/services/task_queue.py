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
import inspect
import base64
import json
import logging
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.paths import DATA_DIR

logger = logging.getLogger("xod.tasks")


# ------------------------------------------------------- artifact typing
# Fix round 6 (BOARD-001 Design 2): existence is not the artifact. A
# claimed file is also the wrong artifact when its type does not hold:
# a .py that does not compile, a test file that collects no tests
# (the collects-at-least-one-test bar: a module docstring wrapping
# prose compiles fine and runs zero tests, which is exactly the shape
# the BOARD-001 fixture shipped), or a .json that does not parse.
def _is_test_file_name(name: str) -> bool:
    return name.startswith("test_") or name.endswith("_test.py")


def _count_tests(text: str) -> int:
    """Test functions a pytest-style collection would find, counted from
    the AST: module-level test_* functions plus test_* methods in any
    class. Import-time behaviour is not executed; collection shape is
    what the bar measures."""
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0
    count = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            count += 1
        elif isinstance(node, ast.ClassDef):
            count += sum(1 for item in node.body
                         if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and item.name.startswith("test"))
    return count


def _file_type_problem(p: Path) -> Optional[str]:
    """A type problem with the file at p, or None. Only the suffixes
    with a decidable bar are judged (.py, test files, .json, .plist, .wflow,
    shell scripts, and flat .service/.workflow files under Services); anything
    else passes on existence and content, as before."""
    import plistlib
    try:
        sfx = p.suffix.lower()
        if sfx in (".service", ".workflow") and p.is_file() and (
            "Services" in p.parts or sys.platform == "darwin"
        ):
            return (f"{p.name} is a flat file, not a macOS Service bundle directory: "
                    f"a service in ~/Library/Services must be a .workflow or .service directory "
                    f"containing Contents/Info.plist (with NSServices) and Contents/document.wflow")
        if sfx == ".py":
            if p.stat().st_size > 2_000_000:
                return None
            text = p.read_text(encoding="utf-8", errors="replace")
            try:
                compile(text, str(p), "exec")
            except SyntaxError as e:
                return f"{p.name} is not valid Python ({e.msg}, line {e.lineno})"
            if _is_test_file_name(p.name) and _count_tests(text) == 0:
                return (f"{p.name} collects no tests: a test file must define "
                        f"at least one test")
        elif sfx == ".json":
            if p.stat().st_size > 2_000_000:
                return None
            try:
                json.loads(p.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                return f"{p.name} is not valid JSON ({e})"
        elif sfx in (".plist", ".wflow"):
            if p.stat().st_size > 2_000_000:
                return None
            try:
                pl = plistlib.loads(p.read_bytes())
            except Exception as e:
                return f"{p.name} is not a valid plist ({e})"
            if p.name == "Info.plist" and any(
                part.lower().endswith((".workflow", ".service")) for part in p.parts
            ):
                ns_services = pl.get("NSServices") if isinstance(pl, dict) else None
                if not isinstance(ns_services, list) or not ns_services or not all(
                    isinstance(item, dict) and item.get("NSMenuItem") and item.get("NSMessage")
                    for item in ns_services
                ):
                    return (f"{p.name} is missing a valid NSServices entry "
                            f"(requires NSMenuItem and NSMessage)")
        else:
            is_shell = sfx in (".sh", ".bash", ".zsh")
            if not is_shell and p.is_file() and 0 < p.stat().st_size <= 500_000:
                head_bytes = p.read_bytes()[:64]
                if head_bytes.startswith(b"#!") and re.search(br"\b(?:ba|z)?sh\b", head_bytes.splitlines()[0]):
                    is_shell = True
            if is_shell and p.stat().st_size <= 500_000:
                shell_bin = "/bin/zsh" if sfx == ".zsh" and os.path.exists("/bin/zsh") else "/bin/bash"
                if os.path.exists(shell_bin):
                    proc = subprocess.run(
                        [shell_bin, "-n", str(p)],
                        capture_output=True, text=True, timeout=5,
                    )
                    if proc.returncode != 0:
                        err_line = ((proc.stderr or proc.stdout or "").strip().splitlines() or ["syntax error"])[-1]
                        return f"{p.name} has a shell syntax error ({err_line[:160]})"
    except (OSError, subprocess.SubprocessError):
        return None
    return None

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = DATA_DIR / "xod.db"
SHOTS_DIR = DATA_DIR / "tasks"

LIVE_FIELDS = ("id", "title", "request", "status", "waiting_kind", "waiting_detail", "result", "now",
               "created_at", "updated_at", "finished_at", "steps", "has_screenshot", "history",
               "last_progress_at", "stalled", "stalled_reason", "mode", "plan_version", "plan_json", "origin", "scratch")


# ------------------------------------------------------- scratch / playground classification
# Phase 2 of the simplicity redesign: tasks carry the same origin marker
# Phase 0 added for projects (create(..., origin=...)). A job is scratch
# when its origin marker says so, or - for jobs created before the marker
# existed - when its title matches the same conservative legacy-debris
# name check Phase 0 uses for projects (project_storage.is_scratch_project).
# Classification is display-only: scratch jobs are filtered out of the
# Work feed into the Playground (Settings, Advanced); nothing is deleted.
def is_scratch_job(title: str = "", origin: str = "") -> bool:
    try:
        from app.services.project_storage import is_scratch_project
        return is_scratch_project(title or "", [], origin or "user")
    except Exception:
        return (origin or "").strip().lower() in {"scratch", "test", "proving", "eval", "smoke"}

# Cancel and watchdog timing. A first cancel is graceful (the run stops after
# its current step); if the task is still working CANCEL_GRACE_SEC later, or
# the user cancels a second time, the cancel is forced: the worker asyncio
# task itself is cancelled, because a hung await never sees the graceful flag.
CANCEL_GRACE_SEC = 30
# Progress is measured on its own clock (the last_progress_at column), never
# on updated_at: cancel notes and other status pokes update the row, and a
# user trying to cancel a wedged task must not mask the wedge by doing so.
# A working task with no genuine progress for STALL_THRESHOLD_SEC is flagged
# stalled; at STALL_RECOVER_SEC the watchdog stops it for real.
STALL_THRESHOLD_SEC = 600
STALL_RECOVER_SEC = 2 * STALL_THRESHOLD_SEC
WATCHDOG_INTERVAL_SEC = 30


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    # WAL's standard pairing (Tier 6 load work, 2026-11-08): under WAL,
    # synchronous=FULL fsyncs on EVERY commit, and this database is
    # written synchronously on the event loop for every progress line,
    # plan save and state change. Under three concurrent tasks those
    # fsyncs serialized into seconds of loop stall (measured: ~25 ms
    # per commit on this machine's disk, loop lag p95 ~1.9 s under the
    # Tier 6 three-task shape). NORMAL fsyncs at WAL checkpoints
    # instead: an app crash loses nothing, an OS crash can drop the
    # most recent writes, and task state is re-verified against the
    # disk effects it describes before anything is called done.
    try:
        c.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.DatabaseError:
        pass
    return c


def _init() -> None:
    c = _conn()
    # Write-ahead logging: a read no longer waits for a write. Xod reads xod.db on its event loop (settings, task
    # state), and with tasks and workers writing progress side by side those reads waited on the lock: the server
    # stalled up to 1.3 s under load (live, 2026-11-04). The setting stays with the file; nothing copies xod.db.
    try:
        c.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError as e:
        logger.warning(f"xod.db stays in rollback-journal mode: {e}")
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            finished_at REAL,
            title TEXT NOT NULL,
            request TEXT NOT NULL,
            status TEXT NOT NULL,          -- queued | working | waiting | paused | done | failed | cancelled
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
            history TEXT DEFAULT '[]',
            last_progress_at REAL,
            stalled INTEGER DEFAULT 0,
            stalled_reason TEXT
        )
        """
    )
    cols = [r[1] for r in c.execute("PRAGMA table_info(tasks)").fetchall()]
    if "last_progress_at" not in cols:
        c.execute("ALTER TABLE tasks ADD COLUMN last_progress_at REAL")
        # Existing rows start from their last row update; from here on the
        # clock only moves on genuine progress (see _touch_progress).
        c.execute("UPDATE tasks SET last_progress_at = updated_at WHERE last_progress_at IS NULL")
    if "evidence_json" not in cols:
        c.execute("ALTER TABLE tasks ADD COLUMN evidence_json TEXT")
    if "stalled" not in cols:
        c.execute("ALTER TABLE tasks ADD COLUMN stalled INTEGER DEFAULT 0")
    if "stalled_reason" not in cols:
        c.execute("ALTER TABLE tasks ADD COLUMN stalled_reason TEXT")
    # Orchestrator Phase 1 (services/orchestrator.py): a task's durable plan, its version,
    # and the execution mode ("stepwise" runs the plan one step at a time with the steer
    # inbox checked between steps; NULL is the classic one-stream run).
    if "plan_json" not in cols:
        c.execute("ALTER TABLE tasks ADD COLUMN plan_json TEXT")
    if "plan_version" not in cols:
        c.execute("ALTER TABLE tasks ADD COLUMN plan_version INTEGER DEFAULT 0")
    if "mode" not in cols:
        c.execute("ALTER TABLE tasks ADD COLUMN mode TEXT")
    if "memory_json" not in cols:          # the pre-work memory search, with sources (services/memory_layers.py)
        c.execute("ALTER TABLE tasks ADD COLUMN memory_json TEXT")
    if "external_ref" not in cols:         # another agent's id for this same task (a later Xod and Tree sync matches on it)
        c.execute("ALTER TABLE tasks ADD COLUMN external_ref TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS tasks_external_ref ON tasks(external_ref)")
    if "origin" not in cols:
        c.execute("ALTER TABLE tasks ADD COLUMN origin TEXT DEFAULT 'user'")
    c.commit()
    c.close()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    if "stalled" in d:
        d["stalled"] = bool(d["stalled"])
    d["origin"] = d.get("origin") or "user"
    d["scratch"] = is_scratch_job(d.get("title") or "", d["origin"])
    for k in ("next_action", "waiting_detail", "mission_json", "pending_json", "history", "evidence_json", "plan_json"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


def notify(title: str, message: str) -> None:
    """A desktop notification (Mac or Windows); never blocks or fails the task."""
    esc = lambda t: (t or "").replace("\\", "\\\\").replace('"', '\\"')[:220]
    try:
        if sys.platform == "win32":
            from app.services.system_control_win import notify_bg
            notify_bg(f"Xod: {title}", message)
            return
        subprocess.Popen(["osascript", "-e", f'display notification "{esc(message)}" with title "Xod" subtitle "{esc(title)}"'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _phone(task: Optional[Dict[str, Any]], event: str) -> None:
    """Tells the linked phone too (services/phone.py), if there is one. Never fails the task."""
    if not task:
        return
    try:
        from app.services import phone
        phone.on_task(task, event)
    except Exception as e:
        logger.debug(f"phone: {e}")


# ------------------------------------------------------------- file-effect claims
# What a task's own words promise on disk. Deliberately conservative: an effect
# verb, a resolvable path, and no negation governing the verb. Anything less
# explicit is left to the agent's report; the recorded-ops check in
# TaskQueue._verify_file_effects still applies to every task.
_EFFECT_VERB = re.compile(r"\b(create|write|save|make|delete|remove|trash|erase)\b", re.I)
_DELETE_VERB = re.compile(r"\b(delete|remove|trash|erase)\b", re.I)
_NEGATION = re.compile(r"\b(do not|don't|never)\b", re.I)
# A backtick never belongs to a path: reports quote paths in backticks
# ("Created `/abs/sample.txt`"), and a path extracted with its closing
# backtick names a file that can never exist, which contradicted honest
# work whose only fault was being quoted (re-run 3 T1A and T1D, where
# the round 3 carry-forward pasted the earlier step's backticked report
# into the next step's request and this parser believed it).
_PATH_RE = re.compile(r"(?:~|/|[A-Za-z]:[\\/])[^\s\"'<>|`]+")
_NAMED_INSIDE_RE = re.compile(r"file named ([^\s/]+?) inside the ([^\s/]+?) folder", re.I)
_FOLDER_NAMED_RE = re.compile(r"folder named ([^\s/(]+)", re.I)
_FILE_NAMED_RE = re.compile(r"file named ([^\s/]+)", re.I)
_EXACT_CONTENT_RE = re.compile(r"(?:contents? (?:are|is) exactly this|containing exactly|exact text)([^:]*):\s*(.+)", re.I | re.S)
_RUN_RE = re.compile(r"Run exactly this command from the directory (.+?) and require exit code (-?\d+): (.+)$")


def _enumerated_lines(qualifier: str, body: str) -> Optional[List[str]]:
    """The lines an aggregate exact-content ask enumerates, or None when the
    captured text is not a line enumeration. An ask of the shape "containing
    exactly five lines, in order: a.txt, b.txt, and c.txt" dictates the lines
    a.txt, b.txt, c.txt, one per line; the enumeration's own punctuation
    (quotes, commas, the closing "and", the sentence period) is prose, not
    content. Reading the raw enumeration as the expected content compares
    the file against a string it could never equal and contradicts honest
    work in both trailing-newline shapes (D5, AGE-7 re-grade). Only a
    qualifier that speaks of lines, and items that are single tokens, are
    read this way; anything else keeps the literal reading."""
    if not re.search(r"\blines?\b", qualifier or "", re.I):
        return None
    text = (body or "").strip().rstrip(".")
    if not text:
        return None
    items: List[str] = []
    for part in text.split(","):
        item = part.strip().strip("\"'`").strip()
        item = re.sub(r"^and\s+", "", item).strip().strip("\"'`").strip()
        items.append(item)
    if len(items) < 2:
        return None
    if any(not it or re.search(r"\s", it) for it in items):
        return None
    return items


_ANAPHORIC_CONTAINER = re.compile(r"\b(there|in it)\b", re.I)
_BARE_FILENAME = re.compile(r"(?<![\w./\-])([A-Za-z0-9_][\w.\-]*\.[A-Za-z0-9]{1,10})\b")


def _bare_creation_filenames(text: str) -> List[str]:
    """Bare filenames a step's creation sentences name: the files the
    step says it makes, named without a path (the lookbehind in
    _BARE_FILENAME keeps the tails of full paths out). A sentence whose
    only effect verb is a deletion verb names files to remove, not
    creations, and does not count."""
    out: List[str] = []
    for s in re.split(r"\.(?=\s|$)|[!?\n]", text or ""):
        if not _EFFECT_VERB.search(s):
            continue
        if _DELETE_VERB.search(s) and not re.search(r"\b(create|write|save|make)\b", s, re.I):
            continue
        out.extend(_BARE_FILENAME.findall(s))
    return out


def _path_framed_as_container(text: str, p: Path) -> bool:
    """The request's own words use this path as a container: a sentence that
    names the path also speaks of working inside it, or another path the
    request names sits inside it. A step that names its working folder by
    path and the files inside it by bare names produces a lone claim for
    the folder with no directory wording attached, so the claim parser
    cannot tell it apart from a file claim; the framing in the words is
    what tells, and a directory on disk at that path is then the effect
    the step asked for (D5 container shape, AGE-7 re-grade). Since D8
    the words are not the only evidence consulted: the disk-evidence
    branch below lets the step's own named files, present inside the
    folder, prove the framing directly."""
    for s in re.split(r"\.(?=\s|$)|[!?\n]", text or ""):
        if str(p) in s and re.search(r"\b(inside|within)\b", s, re.I):
            return True
    for m in _PATH_RE.finditer(text or ""):
        raw = m.group(0).rstrip(".,;:)`")
        if not raw or raw in ("~", "/"):
            continue
        try:
            q = Path(os.path.expanduser(raw))
        except Exception:
            continue
        if len(p.parts) < len(q.parts) and q.parts[:len(p.parts)] == p.parts:
            return True
    # Anaphoric framing (D6, AGE-21 re-grade): the planner's live steps
    # carry the containment in a locative anaphor instead. "Create
    # <folder> if needed. Create marker-1.txt, marker-2.txt, and
    # marker-3.txt there with exactly ..." (Leg A), "Create <folder> if
    # needed, then create a.txt there containing exactly ..." (the load
    # jobs), "Create <folder> if needed. In it, create marker-1.txt ..."
    # (AGE-7's Leg A): no sentence naming the path says inside or within,
    # and no second full path sits inside the folder, so both tests above
    # miss it and the folder claim stays a file claim. The tell is the
    # combination the live texts share: the folder is the text's only
    # path, some sentence joins a creation verb to the anaphor, and the
    # files are named bare. A plain file ask ("create the file at <path>
    # containing exactly ...") has no anaphor and no bare filenames, so
    # the true squatting case still blocks with its true reason.
    mentioned = _mentioned_paths(text or "")
    if len(mentioned) == 1 and os.path.normcase(str(mentioned[0])) == os.path.normcase(str(p)):
        # Disk-evidence framing (D8, AGE-21 re-grade run 2): the fourth
        # locative in three graded runs ("in that folder") matched none
        # of the branches above or below, because the verifier
        # enumerates prose while the planner generates it. When the
        # folder is the text's only path and every bare filename the
        # step's creation sentences name is on disk as a file directly
        # inside it, the disk itself proves the folder was the working
        # container, whatever locative the planner chose; no phrase
        # list is consulted. The conjunction is the conservative part:
        # an empty folder, a folder holding unrelated files, or one
        # missing some of the named files proves nothing and falls
        # through, and a plain file ask names no bare creation files
        # at all, so the true squatting case is untouched.
        bare = [tok for tok in _bare_creation_filenames(text or "") if tok != p.name]
        if bare and all((p / tok).is_file() for tok in set(bare)):
            return True
        sentences = re.split(r"\.(?=\s|$)|[!?\n]", text or "")
        anaphoric_create = any(_ANAPHORIC_CONTAINER.search(s) and _EFFECT_VERB.search(s)
                               for s in sentences)
        bare = [tok for tok in _BARE_FILENAME.findall(text or "") if tok != p.name]
        if anaphoric_create and bare:
            return True
    return False


def _recorded_write_inside(text: str, p: Path, written_paths: set) -> bool:
    """The step's own recorded effects prove the container: the path is
    the step text's only mentioned path, and the activity log for this
    task shows a file effect landing strictly inside it during this
    step. A step that wrote its files into this directory was working
    in its container, whatever words it used to say so; the ledger
    outranks the prose (D8). The only-path guard keeps the judgment
    about this path: a text naming several paths does not get a folder
    claim satisfied by writes that may belong to another part of it."""
    if not written_paths:
        return False
    mentioned = _mentioned_paths(text or "")
    if len(mentioned) != 1 or os.path.normcase(str(mentioned[0])) != os.path.normcase(str(p)):
        return False
    prefix = os.path.normcase(str(p)) + os.sep
    return any(w.startswith(prefix) for w in written_paths)


def _path_mentioned(sentence: str, p: Path) -> bool:
    if str(p) in sentence or p.name in sentence:
        return True
    return bool(p.stem and re.search(rf"\b{re.escape(p.stem)}\b", sentence))


def _mention_pos(sentence: str, p: Path) -> int:
    """Where `sentence` mentions path `p` (full path, then file name, then
    stem), or -1. Mirrors the representations _path_mentioned accepts."""
    idx = sentence.find(str(p))
    if idx >= 0:
        return idx
    if p.name:
        idx = sentence.find(p.name)
        if idx >= 0:
            return idx
    if p.stem:
        m = re.search(rf"\b{re.escape(p.stem)}\b", sentence)
        if m:
            return m.start()
    return -1


def _read_governed_in_sentence(sentence: str, p: Path) -> bool:
    """The sentence's mention of `p` is governed by a read verb: within
    the clause that names the path (the text since the last clause
    separator: a comma, a semicolon, "and" or "then"), the verb nearest
    the mention is a read verb (_READ_VERB: read, copy, open, ...) and
    no effect or delete verb precedes it in that clause. Such a mention
    makes the path an input of the sentence, not an effect it promises:
    "read the file X and create Y" promises Y and reads X, and "create
    Y, then read X" still promises only Y. Judging a path by any effect
    verb anywhere in its sentence turned X into a write claim, and the
    orchestrator then split the step and minted a "Create exactly one
    file: X" substep for a file the step only reads (Tier 6 leg C,
    AGE-19). The clause bound is what keeps "create a copy of the
    template at Y" honest: there the read verb sits in the create's own
    clause, before the path, and the path stays the create's claim."""
    pos = _mention_pos(sentence, p)
    if pos < 0:
        return False
    start = 0
    for sep in (",", ";"):
        idx = sentence.rfind(sep, 0, pos)
        if idx >= 0:
            start = max(start, idx + 1)
    for m in re.finditer(r"\s+(?:and|then)\s+", sentence[:pos]):
        start = max(start, m.end())
    verbs: Dict[int, str] = {}
    for m in _DELETE_VERB.finditer(sentence, start, pos):
        verbs[m.start()] = "delete"
    for m in _EFFECT_VERB.finditer(sentence, start, pos):
        verbs.setdefault(m.start(), "effect")
    for m in _READ_VERB.finditer(sentence, start, pos):
        verbs.setdefault(m.start(), "read")
    if not verbs:
        return False
    ordered = [verbs[k] for k in sorted(verbs)]
    return ordered[-1] == "read" and all(c == "read" for c in ordered)


def _path_read_governed(text: str, p: Path) -> bool:
    """Any sentence of `text` mentions `p` under a governing read verb."""
    sentences = re.split(r"\.(?=\s|$)|[!?\n]", text or "")
    return any(_path_mentioned(s, p) and _read_governed_in_sentence(s, p)
               for s in sentences)


# The delivered-inputs reference fence (fix round 6): missions wrap
# the inputs they deliver to a step between these markers, and claim
# parsing strips them, exactly as it strips run-effect lines: a claim
# comes from what the step itself was asked to do, not from reference
# material it was handed. Keep the literals in step with missions.py.
_INPUT_FENCE_START = "----- delivered inputs (reference only; not this step's instructions) -----"
_INPUT_FENCE_END = "----- end of delivered inputs -----"


def _strip_reference_lines(text: str) -> str:
    out: List[str] = []
    fenced = False
    for line in (text or "").splitlines():
        if line.strip() == _INPUT_FENCE_START:
            fenced = True
            continue
        if line.strip() == _INPUT_FENCE_END:
            fenced = False
            continue
        if not fenced:
            out.append(line)
    return "\n".join(out)


def _claimed_file_effects(request: str) -> List[Dict[str, Any]]:
    """File effects the request itself explicitly asks for:
    [{kind: "write" or "delete", path: Path, content: exact text or None,
    content_lines: the enumerated lines of an aggregate ask, or None}]."""
    text = request or ""
    # Run-effect lines are the run gate's vocabulary, not file claims: the
    # paths and words inside a declared command are not file promises.
    # Delivered-input fences are reference material, not claims either.
    text = _strip_reference_lines(text)
    text = "\n".join(l for l in text.splitlines() if not _RUN_RE.search(l))
    paths: List[Path] = []
    for m in _PATH_RE.finditer(text):
        if m.start() and text[m.start() - 1] in "/:":
            continue   # the tail of a longer path or a URL, not a path of its own
        if (m.start() and len(m.group(0)) > 1 and m.group(0)[1] == ":"
                and text[m.start() - 1].isalnum()):
            continue   # a drive-letter shape inside a word (the "p:/" of a URL)
        raw = m.group(0).rstrip(".,;:)`")
        if raw not in ("", "~", "/"):
            paths.append(Path(os.path.expanduser(raw)))
    named = _NAMED_INSIDE_RE.search(text)
    if named:
        paths.append(Path.home() / named.group(2) / named.group(1))
    elif "home folder" in text.lower():
        folder, fname = _FOLDER_NAMED_RE.search(text), _FILE_NAMED_RE.search(text)
        if folder and fname:
            paths.append(Path.home() / folder.group(1) / fname.group(1))
    content = None
    content_lines = None
    cm = _EXACT_CONTENT_RE.search(text)
    if cm:
        raw_content = cm.group(2).split("\n")[0].strip() or None
        if raw_content is not None:
            content_lines = _enumerated_lines(cm.group(1), raw_content)
            if content_lines is None:
                content = raw_content
    claims: List[Dict[str, Any]] = []
    sentences = re.split(r"\.(?=\s|$)|[!?\n]", text)
    for p in dict.fromkeys(paths):
        kind = None
        for s in sentences:
            if not _path_mentioned(s, p) or _NEGATION.search(s):
                continue
            if _read_governed_in_sentence(s, p):
                continue   # this sentence reads the path; it does not promise it
            if _DELETE_VERB.search(s):
                kind = "delete"
            elif _EFFECT_VERB.search(s):
                kind = "write"
        if kind is None and len(paths) == 1 and not _NEGATION.search(text) \
                and not _path_read_governed(text, p):
            if _DELETE_VERB.search(text):
                kind = "delete"
            elif _EFFECT_VERB.search(text):
                kind = "write"
        if kind:
            target = "file"
            if kind == "write":
                # A claim whose own words call the target a directory or folder is
                # a directory claim: a directory can never satisfy a file check.
                # (Hardening round 2026-10-30: a mission step that created its
                # project directory exactly as asked was contradicted because the
                # check required is_file() on the directory path.) The sentence
                # must name this path itself, not merely mention it by name: a
                # sentence that names the file while calling its container a
                # directory ("a file proof.txt inside the new directory") would
                # otherwise turn the file's claim into a directory claim and
                # contradict work that is on disk (mission-0d528a2c13).
                for sent in sentences:
                    if (str(p) in sent and not _NEGATION.search(sent)
                            and re.search(r"\b(directory|folder)\b", sent, re.IGNORECASE)):
                        target = "directory"
                        break
            claims.append({"kind": kind, "path": p, "content": content if kind == "write" else None,
                           "content_lines": content_lines if kind == "write" else None,
                           "target": target})
    # A write claim whose path is only the container of a sibling write
    # claim (a proper path prefix: "create proof.txt inside /abs/dir" names
    # the directory but asks for nothing to be made of it) was manufactured
    # by the path extraction, not by the request: no sentence calls that
    # path a file, and the contained claim's existence on disk already
    # implies the container is there. Keeping it demands is_file() of a
    # directory, which can never hold, so honest work was contradicted
    # (H6, mission-1938ea6dd9). A claim whose own words make it a directory
    # keeps its claim: one step may genuinely create both a directory and
    # a file inside it, and each is then checked as what it is.
    write_paths = [c["path"] for c in claims if c["kind"] == "write"]

    def _proper_prefix(a: Path, b: Path) -> bool:
        return len(a.parts) < len(b.parts) and b.parts[:len(a.parts)] == a.parts

    claims = [c for c in claims
              if not (c["kind"] == "write" and c.get("target") == "file"
                      and any(_proper_prefix(c["path"], q) for q in write_paths))]
    return claims


# ------------------------------------------------------------ input-effect claims
# A step can declare a file as its INPUT ("read the file X and create Y from
# it"). Write and delete claims are verified against the disk; inputs were
# not modeled at all, so a step whose input never existed could still
# complete on invented output (Tier 6 leg C, defect D1: copy.txt held the
# invented string "content from step 1" because seed-input.txt was never on
# disk, and nothing in the judgment chain ever asked whether it existed).
# Input claims are extracted conservatively, on purpose: a false input
# claim blocks honest steps, so this fires only on explicit read wording in
# a sentence that names the path, never on optional phrasing ("if it
# exists"), and never for a path the request itself claims as an output.
_READ_VERB = re.compile(
    r"\b(?:read|reading|reads|copy|copying|summari[sz]e|summari[sz]ing|"
    r"open|opening|load|loading|parse|parsing|extract|extracting)\b", re.I)
_OPTIONAL_INPUT = re.compile(
    r"\bif (?:it |the file |the folder )?(?:exists|is present|is there)\b"
    r"|\bif present\b|\boptional\b", re.I)


def _mentioned_paths(text: str) -> List[Path]:
    """Paths a request text names, with the same shape guards the claim
    parser uses (kept beside it so the two never read paths differently)."""
    paths: List[Path] = []
    for m in _PATH_RE.finditer(text):
        if m.start() and text[m.start() - 1] in "/:":
            continue   # the tail of a longer path or a URL, not a path of its own
        if (m.start() and len(m.group(0)) > 1 and m.group(0)[1] == ":"
                and text[m.start() - 1].isalnum()):
            continue   # a drive-letter shape inside a word (the "p:/" of a URL)
        raw = m.group(0).rstrip(".,;:)`")
        if raw not in ("", "~", "/"):
            paths.append(Path(os.path.expanduser(raw)))
    named = _NAMED_INSIDE_RE.search(text)
    if named:
        paths.append(Path.home() / named.group(2) / named.group(1))
    return list(dict.fromkeys(paths))


def _claimed_input_effects(request: str) -> List[Path]:
    """Files the request declares as inputs to read from: paths named in a
    read-shaped sentence that are not themselves claimed write or delete
    targets."""
    text = _strip_reference_lines(request or "")
    text = "\n".join(l for l in text.splitlines() if not _RUN_RE.search(l))
    claimed = {os.path.normcase(str(c["path"]))
               for c in _claimed_file_effects(request or "")}
    inputs: List[Path] = []
    sentences = re.split(r"[.!?\n]", text)
    for p in _mentioned_paths(text):
        if os.path.normcase(str(p)) in claimed:
            continue
        for i, s in enumerate(sentences):
            if not _path_mentioned(s, p):
                continue
            # The sentence split breaks on the dots inside a path itself,
            # so the words that qualify the mention ("if it exists") often
            # land in the next fragment; judge the mention on the fragment
            # joined with the one after it.
            window = s + ("." + sentences[i + 1] if i + 1 < len(sentences) else "")
            if (_READ_VERB.search(window) and not _NEGATION.search(window)
                    and not _OPTIONAL_INPUT.search(window)):
                inputs.append(p)
                break
    return inputs


# ------------------------------------------------------- supplied-input claims
# _claimed_input_effects judges one text at a time and, by design, never
# checks a path that same text claims as an output. Decomposition breaks
# that containment (Tier 6 Leg C, defect D7, AGE-21 re-grade): the
# original request declares seed-input.txt as the file to READ ("read
# the file <seed> and create copy.txt containing exactly its contents"),
# but the executed plan contains a substep claiming seed-input.txt as
# its own output, so no executed text ever declares it an input, the
# worker invents its contents, and the task completes. Whether an input
# is protected cannot depend on how the plan happens to decompose the
# ask, so supplied inputs are extracted from the ORIGINAL request with
# clause-level verb attachment: a path is supplied when a clause of the
# request attaches a read verb to it and no clause attaches a
# create/write/delete verb to it. That attachment is the request's own
# framing, and it separates the two cases the layer must not confuse:
# "read the file X and create Y" declares X supplied although a create
# verb shares its sentence, while "create X, then read X and create Y
# from it" frames X as an intermediate the plan itself makes (and a
# same-file rewrite, "create X ... after reading X", is claimed as an
# output first, matching the output-wins rule above).
_CLAUSE_SPLIT = re.compile(r",|\band\b|\bthen\b|;", re.I)


def _clause_objects(clause: str, paths: List[Path]) -> List[Path]:
    """The paths a clause actually names as its objects: a path that is a
    proper prefix of another path named in the same clause is the
    container riding along inside its file's path, not an object."""
    named = [q for q in paths if str(q) in clause]
    return [q for q in named
            if not any(q != r and len(q.parts) < len(r.parts)
                       and r.parts[:len(q.parts)] == q.parts for r in named)]


def _verb_object(clause: str, match: "re.Match[str]", objects: List[Path]) -> Optional[Path]:
    """The object a verb occurrence governs: the first object path at or
    after the verb ("read the file X", "copy X to Y" both name their
    source first), or the clause's only object when the path rides in
    front of the verb."""
    after = [q for q in objects if clause.find(str(q), match.end()) >= 0]
    if after:
        return after[0]
    return objects[0] if len(objects) == 1 else None


def _declared_supplied_inputs(request: str) -> List[Path]:
    """Files the request declares as inputs it will be supplied with, as
    opposed to intermediates the plan itself creates (see the section
    note above). Conservative in the same directions as
    _claimed_input_effects: no optional phrasing, no negation, and any
    creation framing in the request wins over the read framing."""
    text = _strip_reference_lines(request or "")
    text = "\n".join(l for l in text.splitlines() if not _RUN_RE.search(l))
    paths = _mentioned_paths(text)
    if not paths:
        return []
    supplied: List[Path] = []
    sentences = re.split(r"\.(?=\s|$)|[!?\n]", text)
    for p in paths:
        read = created = False
        for s in sentences:
            if str(p) not in s:
                continue
            for clause in _CLAUSE_SPLIT.split(s):
                objects = _clause_objects(clause, paths)
                if p not in objects:
                    continue
                for m in _EFFECT_VERB.finditer(clause):
                    if _verb_object(clause, m, objects) == p:
                        created = True
                for m in _DELETE_VERB.finditer(clause):
                    if _verb_object(clause, m, objects) == p:
                        created = True
                for m in _READ_VERB.finditer(clause):
                    if _verb_object(clause, m, objects) != p:
                        continue
                    if _NEGATION.search(clause) or _OPTIONAL_INPUT.search(clause):
                        continue
                    read = True
        if read and not created:
            supplied.append(p)
    return supplied


def _supplied_input_problems(task_request: str, step_text: str) -> List[str]:
    """Ways a step conflicts with the task's declared supplied inputs,
    judged against the state before the step runs: the step claims as a
    write target a file the task's own request declares as a supplied
    input, and that input is not on disk. The input must exist as
    supplied; it is not the plan's to create, so a step that would
    create it is blocked with the declared-input layer's own problem
    string, and both shapes of the defect (the direct step and the
    decomposed one) surface the same truthful block. A step that merely
    consumes an intermediate the original request itself creates is not
    a conflict: that path is plan-made by the request's own framing and
    never enters the supplied set."""
    supplied = _declared_supplied_inputs(task_request or "")
    if not supplied:
        return []
    claimed = {os.path.normcase(str(c["path"]))
               for c in _claimed_file_effects(step_text or "")
               if c.get("kind") == "write"}
    return [f"the task reads from {p.name}, but it is not on disk"
            for p in supplied
            if os.path.normcase(str(p)) in claimed and not p.exists()]


# ------------------------------------------------------------- run-effect claims
# A run effect proves produced code works, not just that it exists (H4
# remainder, hardening round 2026-10-30: a mission's pytest file whose 5
# tests all failed with NameError was reported "verified" because the gate
# only checked the file was on disk). The vocabulary is one canonical
# phrasing, parsed deterministically; anything outside it is not a run claim.
RUN_TIMEOUT_SEC = 120
_RUN_OUTPUT_TAIL = 2000


def _is_prefix(a: Path, b: Path) -> bool:
    return len(a.parts) < len(b.parts) and b.parts[:len(a.parts)] == a.parts


def _paths_related(a: Path, b: Path) -> bool:
    """True when either path is the other, inside it, or its container."""
    return a == b or _is_prefix(a, b) or _is_prefix(b, a)


def _split_command(command: str) -> List[str]:
    """Split a declared run command into argv. On Windows, POSIX shlex
    treats backslashes as escapes and mangles native paths
    (C:\\Users\\... becomes C:Users...), so a declared run naming an
    absolute Windows path could never start at all (fix round 6;
    found by the run-effect suites on the Windows build, where every
    run claim died with WinError 2). Split with posix=False there and
    strip the matched quotes it leaves on the tokens."""
    if sys.platform == "win32":
        parts = shlex.split(command, posix=False)
        return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'"
                else p for p in parts]
    return shlex.split(command)


def _run_refusal(command: str) -> Optional[str]:
    """Why a declared run command must not be executed, or None if it may run.
    The gate executes an argv list with shell=False, but the declared
    vocabulary is a single plain command: anything that only makes sense to
    a shell (chaining, pipes, redirection, substitution, backgrounding) is
    refused at parse time. Shell metacharacters inside quoted argument text
    are inert in an argv list and stay allowed, except dollar and backtick
    inside double quotes, where a shell would substitute; when in doubt,
    refuse."""
    if "\n" in command or "\r" in command:
        return "the command is not a single line"
    try:
        argv = _split_command(command)
    except ValueError as e:
        return f"the command could not be parsed ({e})"
    if not argv:
        return "the command is empty"
    quote = None
    i = 0
    while i < len(command):
        ch = command[i]
        if quote == "'":
            if ch == "'":
                quote = None
        elif quote == '"':
            if ch == '"':
                quote = None
            elif ch in "$`":
                return f"the command contains {ch} inside double quotes, where a shell would substitute it"
            elif ch == "\\":
                i += 1
        else:
            if ch in "'\"":
                quote = ch
            elif ch == "\\":
                i += 1
            elif ch in ";&|><`$()":
                return f"the command contains the shell metacharacter {ch}"
        i += 1
    if quote is not None:
        return "the command has an unbalanced quote"
    return None


def _claimed_run_effects(request: str, anchors: List[Path]) -> List[Dict[str, Any]]:
    request = _strip_reference_lines(request or "")
    """Run effects the request declares in the gate's canonical phrasing:
    [{command, cwd, expect_exit, refused}]. A claim whose command is not a
    single plain command, or whose working directory is not anchored by a
    path this same task declares (equal to it, inside it, or the directory
    containing it), is refused: recorded here with its reason, never
    silently dropped and never executed."""
    claims: List[Dict[str, Any]] = []
    for line in (request or "").splitlines():
        m = _RUN_RE.search(line)
        if not m:
            continue
        cwd = Path(os.path.expanduser(m.group(1).strip()))
        command = m.group(3).strip()
        claim: Dict[str, Any] = {"command": command, "cwd": cwd,
                                 "expect_exit": int(m.group(2)), "refused": None}
        refusal = _run_refusal(command)
        if refusal:
            claim["refused"] = refusal
        elif not any(_paths_related(cwd, a) for a in anchors):
            claim["refused"] = ("the working directory is not inside any path this task "
                                "declares, so nothing anchors where it would run")
        claims.append(claim)
    return claims


def _output_tail(*parts: Any) -> str:
    chunks = []
    for part in parts:
        if not part:
            continue
        if isinstance(part, bytes):
            part = part.decode("utf-8", "replace")
        chunks.append(str(part))
    return "\n".join(chunks).strip()[-_RUN_OUTPUT_TAIL:]


# ------------------------------------------------------------- pytest interpreters
# A declared run of the form "python3 -m pytest ..." names a bare
# interpreter, and a bare python3 resolves through the app process's
# PATH: on the owner's Mac that is the Xcode command-line-tools stub
# (3.9, no pytest), so the run could only ever exit 1 with "No module
# named pytest" and the step failed as if its tests had failed
# (missions e0c5872dea and c9a6185f36, 2026-11-08). The tests were
# never exercised: an environment failure wearing a test failure's
# clothes. A pytest run therefore resolves a sane interpreter first:
# the project's own venv, then the interpreter this app itself runs
# on (the bundled python in the shipped app), then a task venv
# provisioned in the run's directory. An interpreter pinned by
# absolute path is respected verbatim, except the Xcode stub itself.
_PYTEST_PROBE_TIMEOUT_SEC = 20
_PYTEST_PROVISION_TIMEOUT_SEC = 300


def _is_disqualified_python(path: str) -> bool:
    """The Xcode stub is never a test interpreter: it ships no pytest
    and exists only to nag for the command line tools install."""
    return "/Xcode.app/" in (path or "").replace("\\", "/")


def _python_can_pytest(py: str) -> bool:
    """Whether this interpreter itself can import pytest, measured in
    isolation: the probe runs with -I (no PYTHONPATH, no user site, no
    current-directory entry on sys.path) from the interpreter's own
    directory. Unisolated, the probe answers a different question: a
    pytest.py in the caller's working directory (this repo's own root
    carries one, the pytest_shim entry point) makes every interpreter
    look pytest-capable, which is one way the run-effect binding went
    unstable across runs (AGE-27): resolution trusted the bundle
    interpreter on one machine layout and the run then died with
    "No module named pytest" on another."""
    try:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        proc = subprocess.run([py, "-I", "-c", "import pytest"], capture_output=True,
                              timeout=_PYTEST_PROBE_TIMEOUT_SEC,
                              cwd=os.path.dirname(os.path.abspath(py)), env=env)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _venv_python(base: Path) -> Optional[str]:
    for sub in ("bin/python", "bin/python3", "Scripts/python.exe"):
        p = base / sub
        if p.exists():
            return str(p)
    return None


def _provision_pytest_venv(cwd: Path) -> Optional[str]:
    """Makes <cwd>/.xod-test-venv from the app's own interpreter and
    installs pytest into it; returns its python, or None when that
    could not be done (no usable base interpreter, venv or install
    failed, pytest still not importable)."""
    base = sys.executable
    if not base or _is_disqualified_python(base) or not os.path.exists(base):
        return None
    venv_dir = Path(cwd) / ".xod-test-venv"
    try:
        proc = subprocess.run([base, "-m", "venv", str(venv_dir)],
                              capture_output=True, text=True,
                              timeout=_PYTEST_PROVISION_TIMEOUT_SEC)
        if proc.returncode != 0:
            return None
        py = _venv_python(venv_dir)
        if not py:
            return None
        proc = subprocess.run([py, "-m", "pip", "install", "--quiet", "pytest"],
                              capture_output=True, text=True,
                              timeout=_PYTEST_PROVISION_TIMEOUT_SEC)
        if proc.returncode != 0:
            return None
        return py if _python_can_pytest(py) else None
    except (OSError, subprocess.SubprocessError):
        return None


def _resolve_pytest_python(cwd: Path):
    """(interpreter, why_not) for a pytest run from cwd: the first
    candidate that can import pytest, in the order project venv, task
    venv, the app's own interpreter, the system python3/python the
    search path offers (each probed in path order, so a bundled or
    stub interpreter that cannot run pytest is passed over, not
    trusted), freshly provisioned task venv. (None, why) when no
    interpreter here can run pytest.

    The system scan is what the plan's own word "python3" means: the
    interpreter a terminal on this machine would run. Binding a
    test run to the app's bundled interpreter alone made the binding
    unstable across runs (AGE-27): the same-shaped effect verified in
    one run, when resolution happened to engage, and failed in the
    next on an interpreter with no pytest, when it did not."""
    candidates = []
    for name in (".venv", "venv", ".xod-test-venv"):
        py = _venv_python(Path(cwd) / name)
        if py:
            candidates.append(py)
    own = sys.executable
    if own and not _is_disqualified_python(own):
        candidates.append(own)
    seen = set(candidates)
    for py in candidates:
        if not _is_disqualified_python(py) and _python_can_pytest(py):
            return py, ""
    try:
        from app.services.agent_clis import search_path
        path_val = search_path()
    except Exception:
        path_val = os.environ.get("PATH", "")
    for entry in path_val.split(os.pathsep):
        if not entry:
            continue
        for name in ("python3", "python", "python3.exe", "python.exe"):
            cand = os.path.join(entry, name)
            if cand in seen or _is_disqualified_python(cand):
                continue
            seen.add(cand)
            if os.path.isfile(cand) and _python_can_pytest(cand):
                return cand, ""
    py = _provision_pytest_venv(cwd)
    if py:
        return py, ""
    return None, ("no venv in the project, no app or system interpreter and "
                  "no provisioned task venv here can run pytest")


# Interpreter flags that may sit between the interpreter and -m in a
# pinned run command. -X and -W additionally consume the token after
# them (or carry it joined: -Xdev, -Wignore).
_PY_FLAGS_NO_VALUE = {"-B", "-E", "-I", "-O", "-OO", "-P", "-R", "-S",
                      "-b", "-d", "-i", "-q", "-s", "-u", "-v", "-x"}
_PY_FLAGS_WITH_VALUE = {"-X", "-W"}


def _module_run_index(argv: List[str]) -> Optional[int]:
    """The index of "-m" in a "python [flags] -m module ..." argv, or
    None when the argv is not a module run. Interpreter flags between
    the interpreter and -m are skipped: the planner pins commands like
    "python3 -B -m pytest ..." (AGE-27, re-grade run 3 T1A), and a
    detector that only accepts -m in slot 1 never sees such a run as a
    test run at all, so no interpreter resolution happens and the run
    falls back to whatever interpreter the app itself runs on."""
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "-m":
            return i
        if tok in _PY_FLAGS_WITH_VALUE:
            i += 2
            continue
        if tok in _PY_FLAGS_NO_VALUE or (
                len(tok) > 2 and tok[:2] in _PY_FLAGS_WITH_VALUE):
            i += 1
            continue
        return None
    return None


def _is_pytest_invocation(argv: List[str]) -> bool:
    """A "pythonX [flags] -m pytest ..." run whose interpreter this layer must
    resolve: a bare interpreter name always; an absolute path only
    when it names the disqualified Xcode stub (a pinned interpreter
    anywhere else is the plan's own choice and runs verbatim)."""
    if len(argv) < 3:
        return False
    mi = _module_run_index(argv)
    if mi is None or mi + 1 >= len(argv) or argv[mi + 1] != "pytest":
        return False
    if os.path.basename(argv[0]).lower() in ("python", "python3", "python.exe", "python3.exe"):
        return os.path.sep not in argv[0] or _is_disqualified_python(argv[0])
    return False


def _check_run_claim(claim: Dict[str, Any]) -> Optional[str]:
    """Runs one accepted run claim; returns its problem text, or None when
    the exit code is the required one. Refused claims are never executed;
    the refusal itself is the problem."""
    import shutil
    from app.services.agent_clis import search_path
    command, cwd = claim["command"], claim["cwd"]
    if claim.get("refused"):
        return (f"the run of '{command}' from {cwd} was refused and was not run: "
                f"{claim['refused']}")
    argv = _split_command(command)
    path_val = search_path()
    env = dict(os.environ, PATH=path_val)
    existing_py_path = env.get("PYTHONPATH", "")
    base_dir_str = str(BASE_DIR)
    if base_dir_str not in existing_py_path.split(os.pathsep):
        env["PYTHONPATH"] = f"{existing_py_path}{os.pathsep}{base_dir_str}" if existing_py_path else base_dir_str
    # Resolve bare `python` / `python3` / `pytest` to a project venv in cwd or Xod's
    # active Python interpreter so run claims never fall back to Xcode's pytest-less
    # /usr/bin/python3 stub.
    ran_with = None
    if _is_pytest_invocation(argv):
        py, why_not = _resolve_pytest_python(cwd)
        if py is None:
            return (f"the run of '{command}' from {cwd} could not be run as a test: "
                    f"{why_not}. This is an environment problem, not a test failure: "
                    f"the tests themselves were never exercised.")
        argv = [py] + argv[1:]
        ran_with = py
    elif argv:
        head = argv[0]
        cwd_path = Path(cwd)
        venv_py = next(
            (
                p for p in (
                    cwd_path / ".venv" / "bin" / "python",
                    cwd_path / "venv" / "bin" / "python",
                    cwd_path / ".venv" / "Scripts" / "python.exe",
                    cwd_path / "venv" / "Scripts" / "python.exe",
                )
                if p.is_file()
            ),
            None,
        )
        active_py = str(venv_py) if venv_py else sys.executable
        if head in ("python", "python3"):
            argv = [active_py, *argv[1:]]
        elif head == "pytest" and not shutil.which("pytest", path=path_val):
            py, _why_not = _resolve_pytest_python(cwd_path)
            argv = [py or active_py, "-m", "pytest", *argv[1:]]
            ran_with = py
    try:
        proc = subprocess.run(argv, cwd=str(cwd), shell=False, capture_output=True,
                              text=True, timeout=RUN_TIMEOUT_SEC, env=env)
    except subprocess.TimeoutExpired as e:
        tail = _output_tail(e.output, e.stderr)
        return (f"the run of '{command}' from {cwd} timed out after {RUN_TIMEOUT_SEC} "
                "seconds and was stopped"
                + (f". Output tail: {tail}" if tail else ""))
    except OSError as e:
        return f"the run of '{command}' from {cwd} could not be started: {e}"
    if proc.returncode == claim["expect_exit"]:
        return None
    tail = _output_tail(proc.stdout, proc.stderr)
    return (f"the run of '{command}' from {cwd} exited with code {proc.returncode}, "
            f"not the required code {claim['expect_exit']}"
            + (f". Output tail: {tail}" if tail else ". It produced no output")
            + (f" (The pytest run used the interpreter {ran_with}.)" if ran_with else ""))


def _candidate_for_answer_path(p: str) -> Dict[str, Any]:
    """A locate candidate dict for an absolute path a person named in an
    answer, built from the disk (existence was checked by the caller)."""
    from datetime import datetime
    from app.services.locate import _kind_of
    st = os.stat(p)
    is_dir = os.path.isdir(p)
    return {"path": p, "name": os.path.basename(p.rstrip("/")) or p,
            "kind": _kind_of(p, is_dir), "is_folder": is_dir,
            "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
            "modified_ts": st.st_mtime, "size": st.st_size, "score": 200.0,
            "why": ["you named this path in your answer"]}


class TaskQueue:
    def __init__(self) -> None:
        _init()
        self._wake: Optional[asyncio.Event] = None
        self._worker: Optional[asyncio.Task] = None
        self._running_id: Optional[str] = None
        self._cancel_requested: set = set()
        self._cancel_at: Dict[str, float] = {}
        self._escalations: Dict[str, asyncio.Task] = {}
        self._watchdog: Optional[asyncio.Task] = None
        self._last_stream_meta: Dict[str, Dict[str, Any]] = {}

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
        # A progress history entry is genuine progress: it moves the progress
        # clock (and clears any stalled flag). Nothing else in _progress or
        # _update may do this; row updates in general must not.
        c.execute("UPDATE tasks SET history = ?, last_progress_at = ?, stalled = 0, stalled_reason = NULL WHERE id = ?",
                  (json.dumps(hist[-40:]), entry["at"], task_id))
        c.commit()
        c.close()
        from app.services import event_log
        event_log.emit("task.step", actor="task_queue", ids={"task_id": task_id},
                       payload={"text": line[:300]})

    def _touch_progress(self, task_id: str, at: Optional[float] = None) -> None:
        """Records genuine progress right now: a step advance, a progress
        history entry, tool activity, or a run starting. This is the ONLY
        clock the stall detector and the watchdog read. Cancel notes, status
        pokes and every other _update leave it alone, so they can never mask
        a wedged task the way refreshing updated_at used to."""
        c = _conn()
        c.execute("UPDATE tasks SET last_progress_at = ?, stalled = 0, stalled_reason = NULL WHERE id = ?",
                  (at if at is not None else time.time(), task_id))
        c.commit()
        c.close()
        # The activity timer (services/liveness.py) reads the same genuine
        # progress this clock records: work moved, so the timer resets.
        try:
            from app.services import liveness
            liveness.work_progressed(task_id)
        except Exception:
            pass

    def _progress_at(self, task: Dict[str, Any]) -> float:
        """When this task last genuinely progressed. Legacy rows without a
        progress stamp fall back to their creation time, never updated_at."""
        return task.get("last_progress_at") or task.get("created_at") or task.get("updated_at") or 0.0

    def _update(self, task_id: str, note: Optional[str] = None, **fields: Any) -> None:
        fields["updated_at"] = time.time()
        # Observability (services/event_log.py): a status, waiting or stall change is an
        # event, so the row's prior state is read before the write when this update
        # carries one of those. Plain note and progress-clock updates stay unobserved
        # here on purpose: _progress is their event.
        watch = ("status" in fields) or ("waiting_kind" in fields) or ("stalled" in fields)
        old: Optional[Dict[str, Any]] = None
        if watch:
            c0 = _conn()
            r0 = c0.execute("SELECT status, waiting_kind, stalled FROM tasks WHERE id = ?",
                            (task_id,)).fetchone()
            c0.close()
            old = dict(r0) if r0 else None
        raw_waiting_detail = fields.get("waiting_detail")
        # Leaving "working" clears the stalled flag; only the watchdog sets it.
        # Note what is NOT done here: last_progress_at is never touched by a
        # plain row update, so notes and status pokes cannot fake progress.
        if "status" in fields and fields["status"] != "working":
            fields.setdefault("stalled", 0)
            fields.setdefault("stalled_reason", None)
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
        if old is not None:
            from app.services import event_log
            if "status" in fields and fields["status"] != old["status"]:
                payload: Dict[str, Any] = {"from": old["status"], "to": fields["status"]}
                if note:
                    payload["note"] = note[:300]
                if fields["status"] == "waiting":
                    payload["waiting_kind"] = fields.get("waiting_kind", old["waiting_kind"])
                    wd = raw_waiting_detail
                    if isinstance(wd, str):
                        try:
                            wd = json.loads(wd)
                        except Exception:
                            wd = None
                    if isinstance(wd, dict):
                        payload["waiting_detail"] = wd
                if fields.get("result"):
                    payload["has_result"] = True
                event_log.emit("task.status", actor="task_queue",
                               ids={"task_id": task_id}, payload=payload)
            if fields.get("stalled") and not old.get("stalled"):
                event_log.emit("task.stalled", actor="task_queue", ids={"task_id": task_id},
                               payload={"reason": fields.get("stalled_reason") or note or ""})
            # The activity timer (services/liveness.py): a task entering
            # "working" is work in flight; leaving it ends the flight.
            try:
                from app.services import liveness
                if "status" in fields and fields["status"] == "working" and old["status"] != "working":
                    liveness.work_started(task_id)
                elif "status" in fields and fields["status"] != "working" and old["status"] == "working":
                    liveness.work_finished(task_id)
            except Exception:
                pass

    def list(self, limit: int = 60) -> List[Dict[str, Any]]:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM tasks ORDER BY CASE status WHEN 'waiting' THEN 0 WHEN 'working' THEN 1 WHEN 'paused' THEN 2 WHEN 'queued' THEN 2 ELSE 3 END, "
            "updated_at DESC LIMIT ?", (limit,)).fetchall()
        c.close()
        return [{k: v for k, v in _row(r).items() if k in LIVE_FIELDS} for r in rows]

    def counts(self) -> Dict[str, int]:
        c = _conn()
        rows = c.execute("SELECT status, COUNT(*) AS n FROM tasks "
                         "WHERE COALESCE(LOWER(origin), 'user') NOT IN ('scratch', 'test', 'proving', 'eval', 'smoke') "
                         "GROUP BY status").fetchall()
        c.close()
        return {r["status"]: r["n"] for r in rows}

    # ------------------------------------------------------------------ user actions
    def create(self, request: str, title: Optional[str] = None, mode: Optional[str] = None,
               external_ref: Optional[str] = None, origin: str = "user") -> Dict[str, Any]:
        request = (request or "").strip()
        if not request:
            raise ValueError("Tell Xod what to do.")
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        now = time.time()
        title = title or (request if len(request) <= 70 else request[:67].rsplit(" ", 1)[0] + "…")
        c = _conn()
        c.execute(
            "INSERT INTO tasks (id, created_at, updated_at, title, request, status, next_action, history, last_progress_at, mode, "
            "external_ref, origin) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, now, now, title, request, "queued", json.dumps({"type": "start"}),
             json.dumps([{"at": now, "text": "Acknowledged; added to your tasks" if mode == "stepwise" else "Added to your tasks",
                          "kind": "ack" if mode == "stepwise" else None}]), now, mode, (external_ref or "").strip()[:200] or None,
             origin or "user"),
        )
        c.commit()
        c.close()
        self._kick()
        made = self.get(task_id)
        from app.services import event_log
        event_log.emit("task.created", actor="task_queue",
                       ids={"task_id": task_id, **({"external_ref": made.get("external_ref")} if made.get("external_ref") else {})},
                       payload={"title": title, "request": request[:300]})
        return made

    def by_external_ref(self, ref: str) -> Optional[Dict[str, Any]]:
        """The task another agent knows by this id, if any (the most recent one)."""
        c = _conn()
        r = c.execute("SELECT id FROM tasks WHERE external_ref = ? ORDER BY created_at DESC LIMIT 1", ((ref or "").strip(),)).fetchone()
        c.close()
        return self.get(r["id"]) if r else None

    # ------------------------------------------------ self-maintenance cards (Phase 5)
    # Silent self-maintenance (services/self_maintenance.py) reports through
    # the Work feed: a success is an ordinary Finished result card and an
    # unrecovered fault is one Needs-you card. These rows are written
    # directly - they are records of work Xod already did on itself, so the
    # worker is never kicked and nothing is executed. origin="self" is a
    # real (non-scratch) origin: these cards belong in the feed, unlike
    # scratch/proving runs.

    def record_finished(self, title: str, result: str, origin: str = "self") -> Dict[str, Any]:
        """A job that is already done: a Finished result card, written as an
        outcome (the title IS the outcome sentence), never a step log."""
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        now = time.time()
        c = _conn()
        c.execute(
            "INSERT INTO tasks (id, created_at, updated_at, finished_at, title, request, status,"
            " result, now, history, last_progress_at, origin) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, now, now, now, title, title, "done", result, "Done",
             json.dumps([{"at": now, "text": "Finished"}]), now, origin or "self"),
        )
        c.commit()
        c.close()
        return self.get(task_id)

    def record_waiting_maintenance(self, key: str, title: str, detail: str = "",
                                   fix: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """One Needs-you card for an unrecovered self-maintenance fault,
        deduped by key: if a card for this fault is already waiting, it is
        returned unchanged (with _created False) instead of stacking."""
        c = _conn()
        rows = c.execute("SELECT id, waiting_detail FROM tasks WHERE status = 'waiting'"
                         " AND waiting_kind = 'maintenance'").fetchall()
        c.close()
        for r in rows:
            try:
                wd = json.loads(r["waiting_detail"] or "{}")
            except Exception:
                wd = {}
            if wd.get("key") == key:
                t = self.get(r["id"])
                if t:
                    t["_created"] = False
                return t
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        now = time.time()
        wd = {"key": key, "message": title, "detail": detail, "fix": fix}
        c = _conn()
        c.execute(
            "INSERT INTO tasks (id, created_at, updated_at, title, request, status,"
            " waiting_kind, waiting_detail, now, history, last_progress_at, origin)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, now, now, title, title, "waiting", "maintenance", json.dumps(wd),
             title, json.dumps([{"at": now, "text": "Needs you"}]), now, "self"),
        )
        c.commit()
        c.close()
        t = self.get(task_id)
        if t:
            t["_created"] = True
        return t

    def resolve_waiting_maintenance(self, key: str, result: str) -> bool:
        """Completes the waiting maintenance card for `key` (the fault
        healed, or its Fix it worked). Returns True when a card was waiting."""
        c = _conn()
        rows = c.execute("SELECT id, waiting_detail FROM tasks WHERE status = 'waiting'"
                         " AND waiting_kind = 'maintenance'").fetchall()
        found = None
        for r in rows:
            try:
                wd = json.loads(r["waiting_detail"] or "{}")
            except Exception:
                wd = {}
            if wd.get("key") == key:
                found = r["id"]
                break
        c.close()
        if not found:
            return False
        now = time.time()
        c = _conn()
        c.execute("UPDATE tasks SET status = 'done', result = ?, waiting_kind = NULL,"
                  " waiting_detail = NULL, now = 'Done', updated_at = ?, finished_at = ? WHERE id = ?",
                  (result, now, now, found))
        c.commit()
        c.close()
        return True

    def approve(self, task_id: str) -> Dict[str, Any]:
        t = self.get(task_id)
        if not t or t["status"] != "waiting" or t.get("waiting_kind") != "approval":
            return {"success": False, "error": "This task isn't waiting for an approval."}
        self._update(task_id, note="You approved it", status="queued", next_action={"type": "approve"},
                     waiting_kind=None, waiting_detail=None, now="Approved; continuing")
        from app.services import agent_log
        agent_log.approval_decided(f"task-{task_id}-{t.get('steps') or 0}", "allowed")
        agent_log.clear_waiting_for_origin(f"task:{task_id}", decision="allowed")
        from app.services import event_log
        event_log.emit("task.action", actor="user", ids={"task_id": task_id},
                       payload={"action": "approve"})
        self._kick()
        return {"success": True}

    def answer(self, task_id: str, text: str) -> Dict[str, Any]:
        t = self.get(task_id)
        if not t or t["status"] not in ("waiting", "failed"):
            return {"success": False, "error": "This task isn't waiting for you."}
        text = (text or "").strip()
        if not text:
            return {"success": False, "error": "Write an answer first."}
        resolved = self._locate_answer_resolution(t, text)
        if resolved is not None:
            self._finish(task_id, "done", resolved, note="Finished with your answer")
            from app.services import event_log
            event_log.emit("task.action", actor="user", ids={"task_id": task_id},
                           payload={"action": "answer", "text": text[:200], "resolved": "locate"})
            return {"success": True}
        self._update(task_id, note=f"You replied: {text}", status="queued",
                     next_action={"type": "continue", "text": text},
                     waiting_kind=None, waiting_detail=None, pending_json=None,
                     now="Continuing with your answer")
        from app.services import agent_log
        agent_log.clear_waiting_for_origin(f"task:{task_id}", decision="answered")
        from app.services import event_log
        event_log.emit("task.action", actor="user", ids={"task_id": task_id},
                       payload={"action": "answer", "text": text[:200]})
        self._kick()
        return {"success": True}

    def _locate_answer_resolution(self, task: Dict[str, Any], text: str) -> Optional[str]:
        """The delivered report when an answer RESOLVES a locate ask, else None.

        A non-mission hunt that ends in "ask" waits with its decisive
        payload stored as evidence. The old flow queued a continuation
        whose entire context was the answer text plus "(continue)": the
        original hunt never re-ran with the answer in it, the resumed run
        re-derived the same hunt, and the task re-asked the identical
        question - answered locate tasks sat waiting forever (live
        acceptance, 2026-11-02). But an ask needs no re-run: the person
        is the authority on their own files, so an answer that names the
        answer - one candidate's exact path, or another absolute path
        that exists on disk - finishes the task with that path. Only
        existence-checked paths are ever named; an answer with no usable
        path returns None and keeps the old continue flow. Mission tasks
        carry no locate payload in evidence (their state is the mission),
        so their answers are untouched by this path."""
        if task.get("status") != "waiting" or task.get("waiting_kind") != "question":
            return None
        payload: Optional[Dict[str, Any]] = None
        entries = task.get("evidence_json") or []
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict) and e.get("tool") == "locate_files":
                    p = e.get("payload") or {}
                    if p.get("status") == "ask":
                        payload = p
        if not payload:
            return None
        candidates = [c for c in (payload.get("candidates") or []) if c.get("path")]
        by_path = {c["path"]: c for c in candidates}
        mentioned: List[str] = []
        for raw in re.findall(r"/[^\s\"'<>|`]+", text or ""):
            p = raw.rstrip(".,;:)")
            if len(p) >= 3 and p not in mentioned and os.path.exists(p):
                mentioned.append(p)
        chosen: Optional[Dict[str, Any]] = None
        fresh = [p for p in mentioned if p not in by_path]
        if len(fresh) == 1:
            # One existing path that is not a candidate: the correction
            # ("neither of those - it lives at ..."), which wins over any
            # candidate path the answer merely quotes back.
            chosen = _candidate_for_answer_path(fresh[0])
        elif len(mentioned) == 1 and mentioned[0] in by_path:
            chosen = by_path[mentioned[0]]
        if chosen is None:
            named = [c for c in candidates if c.get("name") and c["name"] in (text or "")]
            if len(named) == 1:
                chosen = named[0]
        if chosen is None:
            return None
        from app.services import decisive_evidence
        found_payload = dict(payload)
        found_payload["status"] = "found"
        found_payload["candidates"] = [chosen]
        found_payload.pop("question", None)
        block = decisive_evidence.render_locate(found_payload)
        if not block or "Not found" in block:
            block = (f"**{chosen.get('name') or 'file'}**\n"
                     f"   Path: {chosen.get('path') or ''}\n\n"
                     "You named this path in your answer.")
        return block

    def retry(self, task_id: str) -> Dict[str, Any]:
        t = self.get(task_id)
        if not t or t["status"] not in ("failed", "waiting", "cancelled"):
            return {"success": False, "error": "Only stopped tasks can be retried."}
        if t.get("mode") == "stepwise" and t["status"] in ("failed", "cancelled"):
            # A retry re-opens the plan's unfinished steps; completed steps stay done.
            from app.services import orchestrator
            orchestrator.reopen(task_id)
        # A bot check the user cleared continues on the same page; everything else picks up where it stopped.
        self._update(task_id, note="Trying again", status="queued",
                     next_action={"type": "continue", "text": "continue"},
                     waiting_kind=None, waiting_detail=None, pending_json=None, now="Trying again")
        from app.services import agent_log
        agent_log.clear_waiting_for_origin(f"task:{task_id}", decision="retried")
        from app.services import event_log
        event_log.emit("task.action", actor="user", ids={"task_id": task_id},
                       payload={"action": "retry"})
        self._kick()
        return {"success": True}

    def clear_waiting(self, task_id: str, reason: str = "Task ended", status: str = "failed") -> bool:
        """Clears any open clarification question or approval wait when a task or its parent mission ends."""
        t = self.get(task_id)
        from app.services import agent_log
        agent_log.clear_waiting_for_origin(f"task:{task_id}", decision=f"expired ({reason[:80]})")
        if not t:
            return False
        if t["status"] == "waiting" or t.get("waiting_kind") or t.get("waiting_detail") or t.get("pending_json"):
            new_status = status if t["status"] == "waiting" else t["status"]
            fields: Dict[str, Any] = {
                "status": new_status,
                "waiting_kind": None,
                "waiting_detail": None,
                "pending_json": None,
                "next_action": None,
            }
            if new_status in ("done", "failed", "cancelled") and not t.get("finished_at"):
                fields["finished_at"] = time.time()
                fields["now"] = "Stopped" if new_status != "done" else "Done"
            if reason and not t.get("result"):
                fields["result"] = reason
            self._update(task_id, note=reason[:300], **fields)
            return True
        return False

    async def cancel(self, task_id: str) -> Dict[str, Any]:
        t = self.get(task_id)
        if not t or t["status"] in ("done", "cancelled"):
            return {"success": False, "error": "Nothing to cancel."}
        if t.get("mode") == "stepwise" and not (t["status"] == "working" and task_id in self._cancel_requested):
            # An orchestrated task cancels through its steer inbox, so the cancel is recorded
            # and reports exactly what it stopped. A second cancel still forces (below).
            from app.services import orchestrator
            res = orchestrator.steer(task_id, "cancel", source="user")
            if t["status"] == "working":
                self._cancel_requested.add(task_id)
                self._cancel_at[task_id] = time.time()
                self._schedule_cancel_escalation(task_id)
            return res
        if t["status"] == "working":
            if task_id in self._cancel_requested:
                # A second cancel while the first is still only "stopping"
                # means the graceful path is not landing: force it now.
                forced = await self._force_cancel(task_id, "you asked a second time while it was still working")
                return {"success": True, "forced": forced}
            self._cancel_requested.add(task_id)  # stops after the current step
            self._cancel_at[task_id] = time.time()
            # This note updates the row but must not touch the progress
            # clock: poking a wedged task cannot be allowed to hide it.
            self._update(task_id, note="Stopping after the current step", now="Stopping…")
            self._schedule_cancel_escalation(task_id)
            return {"success": True, "stopping": True}
        self._update(task_id, note="Cancelled", status="cancelled", waiting_kind=None, waiting_detail=None,
                     pending_json=None, finished_at=time.time(), next_action=None)
        from app.services import agent_log
        if t.get("waiting_kind") == "approval":
            agent_log.approval_decided(f"task-{task_id}-{t.get('steps') or 0}", "declined")
        agent_log.clear_waiting_for_origin(f"task:{task_id}", decision="cancelled")
        await self._close_tab(t.get("tab_id"))
        return {"success": True}

    # ------------------------------------------------------- forced stopping
    def _schedule_cancel_escalation(self, task_id: str) -> None:
        """Arms the grace timer for a graceful cancel. If the task is still
        working when it fires, the cancel is forced (see _force_cancel)."""
        existing = self._escalations.get(task_id)
        if existing is not None and not existing.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._escalations[task_id] = asyncio.create_task(self._escalate_cancel(task_id))

    async def _escalate_cancel(self, task_id: str) -> None:
        try:
            await asyncio.sleep(CANCEL_GRACE_SEC)
        except asyncio.CancelledError:
            return
        self._escalations.pop(task_id, None)
        t = self.get(task_id)
        if t and t["status"] == "working" and task_id in self._cancel_requested:
            await self._force_cancel(
                task_id, f"it did not stop within {int(CANCEL_GRACE_SEC)} seconds of your cancel")

    async def _force_cancel(self, task_id: str, why: str) -> bool:
        """Terminates a working task's run for real and records that the
        cancel had to be forced, and why. The graceful flag alone cannot do
        this: a hung await inside a step never checks it."""
        return await self._force_stop(
            task_id, status="cancelled",
            note=f"Cancel forced: {why}, so Xod cancelled the run itself",
            now_text="Cancelled")

    async def _force_stop(self, task_id: str, status: str, note: str, now_text: str,
                          result: Optional[str] = None) -> bool:
        """The recover_stuck mechanism, shared by forced cancels, the
        watchdog and the doctor fix: cancel the worker asyncio task itself
        (not just the flag), settle the row, restart the worker if it died."""
        t = self.get(task_id)
        if not t or t["status"] != "working":
            return False
        was_running = (task_id == self._running_id and self._worker is not None
                       and not self._worker.done())
        worker = self._worker if was_running else None
        if worker is not None:
            worker.cancel()  # a hung await never sees the cancel flag; cancel the worker itself
            try:
                await asyncio.wait_for(asyncio.shield(worker), timeout=5)
            except BaseException:
                pass
            self._running_id = None
        else:
            # An orchestrated task runs in the orchestrator's pool, in its own asyncio task.
            from app.services import orchestrator
            run = orchestrator._pool.get(task_id)
            if run is not None and not run.done() and run is not asyncio.current_task():
                run.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(run), timeout=5)
                except BaseException:
                    pass
        await self._sweep_tool_children(task_id)
        fields: Dict[str, Any] = {"status": status, "finished_at": time.time(), "now": now_text,
                                  "next_action": None, "waiting_kind": None,
                                  "waiting_detail": None, "pending_json": None}
        if result is not None:
            fields["result"] = result
        self._update(task_id, note=note, **fields)
        from app.services import agent_log
        agent_log.clear_waiting_for_origin(f"task:{task_id}", decision=f"stopped ({status})")
        self._cancel_requested.discard(task_id)
        self._cancel_at.pop(task_id, None)
        esc = self._escalations.pop(task_id, None)
        if esc is not None and esc is not asyncio.current_task() and not esc.done():
            esc.cancel()
        await self._close_tab(t.get("tab_id"))
        if worker is not None and self._wake is not None:
            if worker.done():
                self._worker = asyncio.create_task(self._loop())  # only restart a worker that was running
            else:
                asyncio.create_task(self._respawn_worker(worker))
        return True

    async def _sweep_tool_children(self, task_id: str) -> None:
        """Task end means tool-child end: reap any computer-tool process this task
        spawned that is still running, so a finished, cancelled, or watchdog-stopped
        task can never leave orphans behind (the whole-home find processes that
        outlived their tasks in the eval runs). Only this task's own origin is
        swept; chat and other tasks keep their children."""
        try:
            from app.services.system_control import system_control_service
            await system_control_service.reap_tool_children(origin=f"task:{task_id}")
        except Exception:
            pass

    async def _respawn_worker(self, old_worker: asyncio.Task) -> None:
        """Restarts the worker loop once a force-cancelled worker finishes
        dying, if nothing else has already replaced it."""
        try:
            await old_worker
        except BaseException:
            pass
        if self._worker is old_worker and self._wake is not None:
            self._worker = asyncio.create_task(self._loop())

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
        if not self._wake:
            return
        loop = getattr(self, "_loop_ref", None)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop and running is not loop:
            loop.call_soon_threadsafe(self._wake.set)   # called from a worker thread (e.g. the routine scheduler)
        else:
            self._wake.set()
        try:
            from app.services import orchestrator
            orchestrator.wake_pool(loop if (loop and running is not loop) else None)
        except Exception:
            pass

    def stuck(self, max_quiet_sec: float = 1200) -> List[Dict[str, Any]]:
        """Tasks marked working that haven't genuinely progressed in a long
        time. Reads the progress clock (last_progress_at), never updated_at:
        cancel attempts and status pokes refresh updated_at, and must not be
        able to mask a wedged task from this check."""
        now = time.time()
        c = _conn()
        rows = c.execute("SELECT * FROM tasks WHERE status = 'working'").fetchall()
        c.close()
        out = []
        for r in rows:
            d = _row(r)
            if now - self._progress_at(d) >= max_quiet_sec:
                out.append({"id": d["id"], "title": d["title"],
                            "last_progress_at": self._progress_at(d),
                            "updated_at": d.get("updated_at")})
        return out

    async def recover_stuck(self, max_quiet_sec: float = 1200) -> List[str]:
        """Stops tasks that hung (no progress for max_quiet_sec), marks them failed, restarts the worker."""
        fixed = []
        for t in self.stuck(max_quiet_sec):
            if await self._force_stop(
                    t["id"], status="failed",
                    note="Stopped by Xod's health check: no progress for a long time",
                    now_text="Stopped (it hung)",
                    result="This task stopped making progress, so Xod stopped it. Retry it from the inbox."):
                fixed.append(t["title"])
        if fixed and self._wake is not None and (not self._worker or self._worker.done()):
            self._worker = asyncio.create_task(self._loop())  # only restart a worker that was running
        return fixed

    # ------------------------------------------------------------- watchdog
    def _quiet_text(self, quiet_sec: float) -> str:
        mins = int(quiet_sec // 60)
        if mins >= 1:
            return f"Stalled: no progress for {mins} minute{'s' if mins != 1 else ''}"
        return f"Stalled: no progress for {int(quiet_sec)} seconds"

    async def run_watchdog(self, now: Optional[float] = None,
                           stall_sec: Optional[float] = None,
                           recover_sec: Optional[float] = None) -> Dict[str, List[str]]:
        """One watchdog pass over working tasks, on the progress clock.
        Past stall_sec with no progress: flag the task stalled (flag and
        reason on the task dict, a history entry, and the now-text). Past
        recover_sec: auto-recover through the same force path as
        recover_stuck (worker cancelled, task failed with the honest
        reason, worker restarted if it died)."""
        now = time.time() if now is None else now
        stall_sec = STALL_THRESHOLD_SEC if stall_sec is None else stall_sec
        recover_sec = STALL_RECOVER_SEC if recover_sec is None else recover_sec
        c = _conn()
        rows = c.execute("SELECT * FROM tasks WHERE status = 'working'").fetchall()
        c.close()
        stalled, recovered = [], []
        for r in rows:
            t = _row(r)
            quiet = now - self._progress_at(t)
            if quiet >= recover_sec:
                mins = int(quiet // 60)
                if await self._force_stop(
                        t["id"], status="failed",
                        note=f"Stopped by Xod's watchdog: no progress for {mins} minutes",
                        now_text="Stopped (it hung)",
                        result="This task stopped making progress, so Xod stopped it. Retry it from the inbox."):
                    recovered.append(t["id"])
            elif quiet >= stall_sec:
                reason = self._quiet_text(quiet)
                if t.get("stalled_reason") != reason:
                    self._update(t["id"], note=reason, stalled=1, stalled_reason=reason, now=reason)
                stalled.append(t["id"])
            elif t.get("stalled"):
                self._update(t["id"], stalled=0, stalled_reason=None)
        return {"stalled": stalled, "recovered": recovered}

    async def _watchdog_loop(self) -> None:
        """The periodic watchdog. It runs as its own coroutine, not inside
        the worker loop, because the worker loop is blocked inside _run
        exactly while a task is working (and possibly wedged)."""
        while True:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL_SEC)
                await self.run_watchdog()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.exception(f"Task watchdog error: {e}")

    def start(self) -> None:
        """Called at app startup. Interrupted tasks go back in the queue."""
        c = _conn()
        c.execute("UPDATE tasks SET status = 'queued', now = 'Xod restarted; picking this back up', "
                  "stalled = 0, stalled_reason = NULL, last_progress_at = ? WHERE status = 'working'",
                  (time.time(),))
        c.commit()
        c.close()
        self._wake = asyncio.Event()
        self._loop_ref = asyncio.get_event_loop()
        self._worker = asyncio.create_task(self._loop())
        if self._watchdog is None or self._watchdog.done():
            self._watchdog = asyncio.create_task(self._watchdog_loop())
        # Orchestrated tasks run side by side in their own pool (services/orchestrator.py).
        from app.services import orchestrator
        orchestrator.start_pool(self)

    def stop(self) -> None:
        """Stops every loop and run of this queue at once (shutdown; tests use it as a crash)."""
        for t in (self._worker, self._watchdog):
            if t is not None:
                t.cancel()
        from app.services import orchestrator
        orchestrator.stop_pool()

    async def _loop(self) -> None:
        while True:
            try:
                c = _conn()
                r = c.execute("SELECT id FROM tasks WHERE status = 'queued' AND (mode IS NULL OR mode != 'stepwise') "
                              "ORDER BY updated_at ASC LIMIT 1").fetchone()
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
                finally:
                    # Every _run exit path lands here (done, failed, waiting, and a
                    # forced cancel unwinding through this loop): no tool child of
                    # this task survives the task.
                    await self._sweep_tool_children(r["id"])
                    try:   # a task that stopped running holds no device (services/resources.py)
                        from app.services import resources
                        resources.release_holder(f"task:{r['id']}")
                    except Exception:
                        pass
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

    def _task_framing(self) -> str:
        """The framing a queued task's run carries as SYSTEM context: this is
        work on the person's computer, plus the capability grounding rules
        (services/capability_manifest.py). A bare request left the agent free
        to finish with "I couldn't run commands; I'll need an environment
        with shell execution enabled" on a backend that has shell execution -
        the framing and the rules are what make that claim checkable against
        the live registry before it is written.

        System context, never the person's message: routing, the sentinel
        and the delivered answer all read the user message as the person's
        own words. When the framing rode inside the user message (the first
        version of this fix), find requests stopped reaching the hunt - the
        framing's own words matched the worker lane first - and the rules
        text surfaced verbatim inside delivered answers (the xod-work
        marker's goal, the report). The worker lane puts the same rules in
        its system prompt; the research loop takes them the same way, via
        run_research_stream's system_extra."""
        head = ("[Queued task: you are running on the person's computer with your full tool set, "
                "not answering in chat. Work the request to a verified finish.]\n")
        try:
            from app.services import capability_manifest
            rules = capability_manifest.grounding_rules()
        except Exception:
            rules = ""
        return head + (rules + "\n\n" if rules else "")

    def _framed_request(self, task: Dict[str, Any]) -> str:
        """The framing (see _task_framing) followed by the request itself:
        the composed view of everything a queued task's run is told. The
        run itself receives the two parts through separate channels - the
        request as the person's message, the framing as system context."""
        return self._task_framing() + (task.get("request") or "")

    async def _stream(self, task_id: str, message: str, steps: int, system_extra: str = ""):
        """Runs the agent for one stretch of a task; returns (report_text, steps, decisive_payloads).
        Raises whatever the stream raises; the caller finishes the task as crashed.
        The message is the person's own text; the task framing rides as
        system_extra (system context), never inside the message."""
        from app.agent.researcher import AgenticResearcher
        from app.services import decisive_evidence
        content, last_status = [], ""
        decisive_pairs = []
        errors: List[str] = []
        held_approval: Optional[Dict[str, Any]] = None
        researcher = AgenticResearcher()
        stream_kwargs: Dict[str, Any] = {}
        if system_extra:
            # Pass the framing only to researcher implementations whose
            # signature takes it; simpler implementations (older builds,
            # test doubles) keep the pre-framing call shape.
            try:
                params = inspect.signature(researcher.run_research_stream).parameters
                accepts = "system_extra" in params or any(
                    prm.kind == inspect.Parameter.VAR_KEYWORD for prm in params.values())
            except (TypeError, ValueError):
                accepts = True
            if accepts:
                stream_kwargs["system_extra"] = system_extra
        stream_t0 = time.time()
        async for ev in researcher.run_research_stream(
                [{"role": "user", "content": message}], **stream_kwargs):
            if ev.get("type") in ("tool_result", "subagent_tool_result"):
                decisive_pairs.append((ev.get("name"), ev.get("data")))
                data = ev.get("data")
                if isinstance(data, dict) and data.get("needs_approval"):
                    held_approval = data
            elif ev.get("type") == "approval_hold":
                data = ev.get("data")
                held_approval = data if isinstance(data, dict) else {"needs_approval": True}
            elif ev.get("type") == "error":
                err_txt = str(ev.get("content") or ev.get("error") or "Model stream error").strip()
                if err_txt:
                    errors.append(err_txt)
            if ev.get("type") == "content":
                piece = ev.get("content", "")
                content.append(piece if isinstance(piece, str) else json.dumps(piece))
            elif ev.get("type") == "status":
                line = str(ev.get("content", ""))[:200]
                if line != last_status:
                    last_status = line
                    if line[:1] in "✓✗⏸•":
                        steps += 1
                    self._touch_progress(task_id)  # a fresh status line is tool activity: genuine progress
                    self._update(task_id, now=line, steps=steps)
                    if line.lstrip()[:1] in "".join(self._PROGRESS_MARKS) or line.startswith(self._PROGRESS_MARKS):
                        self._progress(task_id, line)
            if task_id in self._cancel_requested:
                break
        stream_ms = int((time.time() - stream_t0) * 1000)
        if stream_ms >= 500:
            logger.info("slow_step task=%s phase=stream duration_ms=%d steps=%d", task_id, stream_ms, steps)
        self._last_stream_meta[task_id] = {"errors": errors, "held_approval": held_approval, "duration_ms": stream_ms}
        return "".join(content).strip(), steps, decisive_evidence.collect(decisive_pairs)

    def _verify_file_effects(self, task: Dict[str, Any], started: float) -> List[str]:
        """Ways this task's declared effects are NOT true (empty list = verified).
        Layers, all factual: (1) the file ops the activity log recorded for this
        task must match the disk now; (2) file effects the request itself spells out
        (a path plus create/write/delete wording, exact contents when dictated) must
        hold on disk, whether or not any tool ran; (3) run effects the request
        declares must exit with the required code when executed; (4) a claimed
        file must be the artifact its type promises (fix round 6: a .py compiles,
        a test file collects at least one test, a .json parses); (5) provenance:
        when this task used the write tools at all, a claimed file no tool call
        from this task touched is not its artifact, whoever put it there;
        (6) a file the request itself declares as an input must be on disk.
        The
        agent has reported tasks done without calling a single file tool; this
        check trusts the disk, the type and the write boundary, not the report."""
        from app.services import activity
        problems: List[str] = []
        try:
            entries = activity.entries_for_origin(f"task:{task['id']}", since=started - 1)
        except Exception:
            logger.exception("file-effect check: activity read failed")
            entries = []
        expect: Dict[str, str] = {}
        written_by_tool: set = set()
        written_paths: set = set()
        touched: set = set()
        for e in entries:
            if not e.get("ok"):
                continue
            for op in e.get("ops") or []:
                kind = op.get("kind")
                for key in ("path", "now_at", "from", "src"):
                    if op.get(key):
                        touched.add(os.path.normcase(str(op[key])))
                if kind == "write_file":
                    written_by_tool.add(os.path.normcase(str(op.get("path"))))
                    written_paths.add(os.path.normcase(str(op.get("path"))))
                    expect[str(op.get("path"))] = "file"
                elif kind in ("restore_file", "remove_created"):
                    written_paths.add(os.path.normcase(str(op.get("path"))))
                    expect[str(op.get("path"))] = "file"
                elif kind == "untrash":
                    expect[str(op.get("path"))] = "absent"
                elif kind in ("move_back", "remove_copy"):
                    written_paths.add(os.path.normcase(
                        str(op.get("now_at") or op.get("to_hint"))))
                    expect[str(op.get("now_at") or op.get("to_hint"))] = "present"
                elif kind == "remove_empty_dir":
                    expect[str(op.get("path"))] = "present"
        for raw, want in expect.items():
            p = Path(raw)
            if want == "file" and not p.is_file():
                problems.append(f"{p.name} was written during the task but is not on disk now")
            elif want == "file" and p.is_file():
                type_problem = _file_type_problem(p)
                if type_problem:
                    problems.append(type_problem)
            elif want == "absent" and p.exists():
                problems.append(f"{p.name} was deleted during the task but is still on disk")
            elif want == "present" and not p.exists():
                problems.append(f"{p.name} should be on disk but is missing")
        file_claims = _claimed_file_effects(task.get("request") or "")
        for claim in file_claims:
            p = claim["path"]
            if claim["kind"] == "delete":
                if p.exists():
                    problems.append(f"the task asked for {p.name} to be deleted, but it is still on disk")
            else:
                if claim.get("target") == "directory":
                    if not p.is_dir():
                        problems.append(f"the task asked for {p.name} to be created, but it is not on disk")
                elif not p.is_file():
                    if p.is_dir() and _path_framed_as_container(task.get("request") or "", p):
                        # The path is on disk as a directory and the step's
                        # own words frame it as the container it worked
                        # inside (its files named by bare names): that is
                        # the effect the step asked for. Reporting it as
                        # not on disk would state the opposite of the disk.
                        pass
                    elif p.is_dir() and _recorded_write_inside(
                            task.get("request") or "", p, written_paths):
                        # The step's recorded file effects landed inside
                        # this directory during the step: the directory
                        # is the container the step worked in, proven by
                        # the ledger rather than by the step's wording
                        # (D8). A directory the step never wrote into
                        # gets no such credit and still blocks below.
                        pass
                    elif p.is_dir():
                        problems.append(f"the task asked for {p.name} to be created as a file, "
                                        f"but a directory is on disk at that path")
                    else:
                        problems.append(f"the task asked for {p.name} to be created, but it is not on disk")
                elif claim.get("content") is not None or claim.get("content_lines"):
                    try:
                        actual = p.read_bytes() if p.stat().st_size <= 1_000_000 else None
                    except Exception:
                        actual = None
                    if actual is not None:
                        lines = claim.get("content_lines")
                        if lines:
                            joined = "\n".join(lines).encode("utf-8")
                            if actual not in (joined, joined + b"\n"):
                                problems.append(f"{p.name} does not contain exactly what the task asked for")
                        elif actual != claim["content"].encode("utf-8"):
                            problems.append(f"{p.name} does not contain exactly what the task asked for")
                if claim["kind"] != "delete" and claim.get("target") != "directory" and p.is_file():
                    type_problem = _file_type_problem(p)
                    if type_problem:
                        problems.append(type_problem)
                    # Provenance (fix round 6): files become artifacts
                    # through the write tools. When this task's activity
                    # shows it used those tools, a claimed file outside
                    # every tool call's reach did not come through the
                    # boundary and is not credited to the task. A task
                    # that never used the write tools (a shell-only
                    # flow) proves nothing either way and is not judged
                    # on this layer.
                    if written_by_tool and os.path.normcase(str(p)) not in touched:
                        problems.append(
                            f"Provenance: {p.name} is on disk, but no tool call from "
                            f"this task wrote it; this task's files came through the "
                            f"write tools, and {p.name} did not")
        # (3) run effects the request declares: the command is executed with
        # an argv list (never a shell) and its exit code must be the required
        # one. A contradicted run is a problem exactly like a missing file,
        # so the honest retry and the failure text treat it the same way.
        for claim in _claimed_run_effects(task.get("request") or "",
                                          [c["path"] for c in file_claims]):
            problem = _check_run_claim(claim)
            if problem:
                problems.append(problem)
        # (6) declared inputs (Tier 6 defect D1): a file the request names
        # as its input must be on disk. The layers above judge claimed
        # outputs only, so a step could satisfy every output check with
        # invented content while its declared input never existed. An
        # input that was never there means the output was not derived
        # from it, whatever the output looks like.
        for p in _claimed_input_effects(task.get("request") or ""):
            if not p.exists():
                problems.append(f"the task reads from {p.name}, but it is not on disk")
        seen, out = set(), []
        for pr in problems:
            if pr not in seen:
                seen.add(pr)
                out.append(pr)
        return out

    async def _run(self, task_id: str) -> None:
        from app.services import activity
        activity.origin.set(f"task:{task_id}")   # tool calls in this task are logged as the task's
        from app.agent.researcher import AgenticResearcher
        from app.services.browser_sandbox import browser_sandbox_service as sb

        task = self.get(task_id)
        if task.get("mode") == "stepwise":
            from app.services import orchestrator
            await orchestrator.run_stepwise(self, task_id)
            return
        action = task.get("next_action") or {"type": "start"}
        self._running_id = task_id
        self._update(task_id, status="working", now="Starting…" if action["type"] == "start" else task.get("now"))
        self._touch_progress(task_id)  # the progress clock starts when the run does

        resuming = action["type"] in ("approve", "continue")
        try:
            page_ok = await self._ensure_tab(task, resuming)
        except Exception as e:
            # The browser is a tool, not a gate: a task that never opens a page
            # must not die because Chrome would not start. Browser tools call
            # ensure_browser_running again when a page is actually needed, and
            # the failure surfaces there instead of failing the whole task.
            logger.warning(f"Task {task_id}: browser tab unavailable ({e}); continuing without one")
            self._update(task_id, note="Browser unavailable; carrying on without it")
            page_ok = False
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

        framing = self._task_framing()
        if action["type"] == "start":
            message = task["request"]
        elif action["type"] == "approve":
            message = "go ahead"
        else:
            message = f"{action.get('text', '')}\n(continue)"

        started = time.time()
        steps = task.get("steps") or 0
        payloads: List[Dict[str, Any]] = []
        try:
            report, steps, payloads = await self._stream(task_id, message, steps, framing)
        except Exception as e:
            logger.exception(f"Task {task_id} crashed")
            self._finish(task_id, "failed", f"Something went wrong: {e}", note="Crashed")
            self._running_id = None
            return

        # A task is only done when its file effects are true on disk (see
        # _verify_file_effects). No-mission tasks get one honest retry with the
        # problem named; mission tasks are verified at their finish below,
        # without a retry (their flow has its own confirmation states).
        mission = sb.latest_mission if (sb.latest_mission or {}).get("started_at", 0) >= started - 1 else None
        meta = self._last_stream_meta.get(task_id) or {}
        from app.services import agent_log
        open_approvals = agent_log.waiting_approvals_for(f"task:{task_id}", since=started - 1)
        held_approval = meta.get("held_approval") or (open_approvals[0] if open_approvals else None)
        if not mission and held_approval and task_id not in self._cancel_requested:
            # D1 fix (Run 0): an unexecuted approval card pauses the task in waiting;
            # it may never close the task as done.
            self._running_id = None
            label = str(held_approval.get("tool") or held_approval.get("title") or "Approve")
            msg = str(held_approval.get("message") or held_approval.get("detail") or report or "Needs your approval.")
            evidence = {"evidence_json": json.dumps(payloads)} if payloads else {}
            self._update(
                task_id,
                note=f"Waiting for your OK ({label})",
                status="waiting",
                waiting_kind="approval",
                pending_json=held_approval,
                waiting_detail={"label": label, "message": msg},
                result=report,
                now="Waiting for your OK",
                next_action=None,
                **evidence,
            )
            notify(self.get(task_id)["title"], f"Needs your OK ({label}).")
            _phone(self.get(task_id), "approval")
            return

        problems: List[str] = []
        if not mission and task_id not in self._cancel_requested:
            ver_t0 = time.time()
            problems = await asyncio.to_thread(self._verify_file_effects, task, started)
            ver_ms = int((time.time() - ver_t0) * 1000)
            if ver_ms >= 500:
                logger.info("slow_step task=%s phase=verify_file_effects duration_ms=%d", task_id, ver_ms)
            stream_errors = meta.get("errors") or []
            if stream_errors and (not (report or "").strip() or any(err in (report or "") for err in stream_errors)):
                problems.append(f"model stream error: {'; '.join(stream_errors)}")
            elif re.search(r"(?:^|\n)\s*(?:The model stopped:|\*\*Paused after |\*\*Not finished yet:)", report or ""):
                problems.append(f"the worker stopped before finishing ({(report or '').splitlines()[0][:160]})")
            fatal_key_err = any(
                re.search(r"missing api key for|no api key (?:configured|set|saved) for", p, re.I)
                for p in problems
            )
            if problems and not fatal_key_err:
                self._update(task_id, note="The first attempt did not check out; running it again",
                             now="Checking the result before finishing")
                retry_msg = (f"{task['request']}\n\n(You already attempted this once and reported finishing, "
                             f"but checking found: {'; '.join(problems)}. Do it now for real, using your "
                             "file tools, then confirm briefly.)")
                try:
                    again, steps, payloads2 = await self._stream(task_id, retry_msg, steps, framing)
                    if again:
                        report = again
                    if payloads2:
                        payloads = payloads2
                except Exception:
                    logger.exception(f"Task {task_id} verification retry crashed")
                meta2 = self._last_stream_meta.get(task_id) or {}
                open_approvals2 = agent_log.waiting_approvals_for(f"task:{task_id}", since=started - 1)
                held2 = meta2.get("held_approval") or (open_approvals2[0] if open_approvals2 else None)
                if held2 and task_id not in self._cancel_requested:
                    self._running_id = None
                    label = str(held2.get("tool") or held2.get("title") or "Approve")
                    msg = str(held2.get("message") or held2.get("detail") or report or "Needs your approval.")
                    evidence = {"evidence_json": json.dumps(payloads)} if payloads else {}
                    self._update(
                        task_id,
                        note=f"Waiting for your OK ({label})",
                        status="waiting",
                        waiting_kind="approval",
                        pending_json=held2,
                        waiting_detail={"label": label, "message": msg},
                        result=report,
                        now="Waiting for your OK",
                        next_action=None,
                        **evidence,
                    )
                    return
                problems = await asyncio.to_thread(self._verify_file_effects, task, started)
                stream_errors2 = meta2.get("errors") or []
                if stream_errors2 and (not (report or "").strip() or any(err in (report or "") for err in stream_errors2)):
                    problems.append(f"model stream error: {'; '.join(stream_errors2)}")
                elif re.search(r"(?:^|\n)\s*(?:The model stopped:|\*\*Paused after |\*\*Not finished yet:)", report or ""):
                    problems.append(f"the worker stopped before finishing ({(report or '').splitlines()[0][:160]})")

        self._running_id = None
        if task_id in self._cancel_requested:
            self._cancel_requested.discard(task_id)
            self._cancel_at.pop(task_id, None)
            esc = self._escalations.pop(task_id, None)
            if esc is not None and not esc.done():
                esc.cancel()
            self._update(task_id, note="Cancelled", status="cancelled", finished_at=time.time(), next_action=None, now="Cancelled")
            await self._close_tab(self.get(task_id).get("tab_id"))
            return

        if not mission:
            evidence = {"evidence_json": json.dumps(payloads)} if payloads else {}
            if problems:
                tail = f"Not finished: {'; '.join(problems)}"
                self._finish(task_id, "failed", f"{report}\n\n{tail}".strip() if report else tail,
                             note="Verification failed", **evidence)
            elif any(p["payload"].get("status") == "ask" for p in payloads):
                # The hunt ended in a question (two close matches): wait for
                # the person's answer instead of picking one for them.
                self._update(task_id, note="Waiting for you", status="waiting", waiting_kind="question",
                             waiting_detail={"message": report}, result=report,
                             now="Needs your answer", next_action=None, **evidence)
                notify(self.get(task_id)["title"], "Has a question for you.")
                _phone(self.get(task_id), "question")
            else:
                self._finish(task_id, "done", report or "Done.", note="Finished", **evidence)
            await self._close_tab(self.get(task_id).get("tab_id"))
            return

        snapshot = {k: v for k, v in mission.items() if k not in ("latest_screenshot_uri",)}
        snapshot["steps"] = [{k: v for k, v in s.items() if k != "screenshot_uri"} for s in mission.get("steps", [])]
        self._save_screenshot(task_id, mission.get("latest_screenshot_uri"))
        status = mission.get("status")
        common = {"mission_json": snapshot, "result": report, "next_action": None}

        if status == "completed":
            problems = await asyncio.to_thread(self._verify_file_effects, task, started)
            if problems:
                self._finish(task_id, "failed",
                             f"{report}\n\nNot finished: {'; '.join(problems)}".strip(),
                             note="Verification failed", **common)
            else:
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
            from app.services import agent_log
            t_now = self.get(task_id)
            agent_log.approval_opened(f"task-{task_id}-{t_now.get('steps') or 0}", "task",
                                      str(pending.get("title") or pending.get("url") or "a web page"),
                                      f"Press “{pending.get('label', 'Submit')}”", t_now.get("title") or "")
            notify(self.get(task_id)["title"], f"Ready to press \"{pending.get('label', 'Submit')}\". Needs your OK.")
            _phone(self.get(task_id), "approval")
        elif status in ("needs_input", "blocked"):
            kind = "question" if status == "needs_input" else "blocked"
            self._update(task_id, note="Waiting for you", status="waiting", waiting_kind=kind,
                         waiting_detail={"message": mission.get("final_answer")},
                         now="Needs your answer" if kind == "question" else "Stuck at a verification check", **common)
            notify(self.get(task_id)["title"], "Has a question for you." if kind == "question" else "Stuck at a verification check.")
            _phone(self.get(task_id), kind)
        else:
            self._finish(task_id, "failed", report or mission.get("final_answer") or "It didn't finish.", note="Stopped", **common)

    def _finish(self, task_id: str, status: str, report: str, note: str, **fields: Any) -> None:
        fields.pop("result", None)  # the report argument wins
        # A finished task is not waiting for anything: clear the whole
        # waiting state, not just the kind (on the shipped tree the
        # detail and a stale next_action outlived the task's end).
        fields.setdefault("waiting_detail", None)
        fields.setdefault("next_action", None)
        self._update(task_id, note=note, status=status, result=report, finished_at=time.time(),
                     waiting_kind=None, pending_json=None,
                     now="Done" if status == "done" else "Stopped", **fields)
        from app.services import agent_log
        agent_log.clear_waiting_for_origin(f"task:{task_id}", decision=f"expired ({status})")
        t = self.get(task_id)
        routine_run = None
        try:   # a scheduled run's result reaches the person through its delivery policy only
            from app.services import delivery
            routine_run = delivery.open_run_for_task(task_id)
        except Exception:
            pass
        if routine_run is None:
            notify(t["title"], "Done." if status == "done" else "Couldn't finish. Open Tasks to see why.")
            _phone(t, status)
        try:   # it becomes part of Xod's memory of what it has done (services/recall.py)
            from app.services import recall
            recall.schedule_index(2.0)
        except Exception:
            pass

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
