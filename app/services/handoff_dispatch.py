"""
The handoff, carried by Xod: when another AI (Claude) leaves work for Muse Code in a repo's docs/AI_HANDOFF.md, Xod
starts a Crew job for Muse in that repo itself, so nobody has to copy the note across.

  repos      the repos Xod watches (setting handoff_dispatch_repos); none until you add one
  whose turn after Muse Code's last section, a section by someone else that names "Muse Code" is work for Muse
  once       each such section starts at most one job (its heading and place are remembered in the data folder);
             a job that failed is tried again later, at most RETRIES times; a running job is never doubled
  limits     before starting, Xod reads Muse's usage (services/agent_usage.py) and waits for the reset when it's
             used up; a job that still fails with 429 / "quota exhausted" waits the same way. Neither counts as a
             failed try
  rules      the job reads the handoff, does the items for Muse Code, runs the tests it touches, leaves the work
             uncommitted (you approve every commit, push and release) and appends its own section when done

Checked every TICK_SEC while Xod runs; a job's approvals go through Crew like any other job's.
"""
import asyncio
import datetime as dt
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.handoff_dispatch")

SETTING = "handoff_dispatch_repos"
STATE_FILE = DATA_DIR / "handoff_dispatch.json"
TICK_SEC = 60
RETRIES = 2
RETRY_AFTER_SEC = 600
QUOTA_WAIT_SEC = 1800
MUSE = "Muse Code"
_QUOTA_RE = re.compile(r"\b429\b|quota|rate[_ ]limit|usage (?:limit|window)", re.I)
_RESET_RE = re.compile(r"resets? at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)", re.I)

TASK = (
    "You are Muse Code, the one who writes the code in this repo. Another AI left work for you in docs/AI_HANDOFF.md.\n"
    "1. Read the sections after your own last section (headings containing \"Muse Code\" are yours).\n"
    "2. Do every open item addressed to Muse Code there, most urgent first. Run the tests for what you change.\n"
    "3. Leave your work uncommitted: do not commit, push, merge, tag or release (the person who owns the repo approves each of those).\n"
    "4. When done or blocked, append one section to docs/AI_HANDOFF.md in its format (\"## <date> - Muse Code -> next AI\" "
    "with Changed, Verified as command -> observed result, Decisions, Open, Questions for the other AI). "
    "Use the ai-handoff tools if you have them.\n"
    "The newest work for you starts at: {heading}"
)

_HEAD_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2}) - (.+?)(?: -> .+| answers.*| asks .+)?$")
_state: Dict[str, Any] = {"task": None}


def repos() -> List[str]:
    from app.services.storage import get_setting
    try:
        return [r for r in json.loads(get_setting(SETTING, "") or "[]") if isinstance(r, str) and r]
    except ValueError:
        return []


def set_repos(paths: List[str]) -> Dict[str, Any]:
    from app.services.storage import set_setting
    keep = []
    for p in paths or []:
        root = Path(p).expanduser().resolve()
        if (root / ".git").exists() and str(root) not in keep:
            keep.append(str(root))
    set_setting(SETTING, json.dumps(keep))
    if keep:
        start()
    return status()


def _load_state() -> Dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(s: Dict[str, Any]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(s, indent=1))
    except OSError as e:
        logger.debug(f"handoff dispatch state not saved: {e}")


def status() -> Dict[str, Any]:
    return {"repos": repos(), "dispatched": _load_state()}


def sections(text: str) -> List[Dict[str, Any]]:
    """The dated sections of a handoff file, oldest first: heading, author, body, and the line it starts on."""
    out: List[Dict[str, Any]] = []
    for i, line in enumerate(text.splitlines()):
        if line.startswith("## "):
            m = _HEAD_RE.match(line.strip())
            out.append({"heading": line.strip(), "author": (m.group(2).strip() if m else ""), "line": i, "body": [],
                        "dated": bool(m)})
        elif out:
            out[-1]["body"].append(line)
    for s in out:
        s["body"] = "\n".join(s["body"])
    return [s for s in out if s["dated"]]


def work_for_muse(text: str) -> List[Dict[str, Any]]:
    """Sections after Muse Code's last one, written by someone else, that name Muse Code: its turn."""
    secs = sections(text)
    last_muse = max((i for i, s in enumerate(secs) if MUSE.lower() in s["author"].lower()), default=-1)
    return [s for s in secs[last_muse + 1:]
            if MUSE.lower() not in s["author"].lower() and MUSE.lower() in (s["heading"] + "\n" + s["body"]).lower()]


def quota_reset(error: str) -> Optional[float]:
    """When a job's error says the agent is out of usage: the time it resets (or a while from now). Else None."""
    if not error or not _QUOTA_RE.search(error):
        return None
    m = _RESET_RE.search(error)
    if m:
        try:
            return dt.datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).timestamp() + 60
        except ValueError:
            pass
    return time.time() + QUOTA_WAIT_SEC


def _key(sec: Dict[str, Any]) -> str:
    return hashlib.sha256(f"{sec['line']}\n{sec['heading']}\n{sec['body']}".encode()).hexdigest()[:16]


async def check(repo: str) -> Optional[Dict[str, Any]]:
    """Start Muse on the newest work left for it in this repo, unless that's already been done. Returns the new job."""
    from app.services import crew
    path = Path(repo) / "docs" / "AI_HANDOFF.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    pending = work_for_muse(text)
    if not pending:
        return None
    newest = pending[-1]
    key = _key(newest)
    state = _load_state()
    mine = state.get(repo) or {}
    if time.time() < mine.get("wait_until", 0):
        return None              # Muse is out of usage until then: any job would fail the same way
    out_of_usage = False
    if mine.get("job"):
        try:
            prev = crew.job(mine["job"])
        except crew.CrewError:
            prev = None
        if prev and prev["status"] in ("starting", "working", "waiting"):
            return None          # Muse is still on it (maybe on older work; it reads the whole file)
        reset = quota_reset(str((prev or {}).get("error") or "")) if prev and prev["status"] != "done" else None
        if reset and not mine.get("waited_for") == mine["job"]:
            state[repo] = {**mine, "wait_until": reset, "waited_for": mine["job"]}
            _save_state(state)
            logger.info(f"handoff for {repo}: Muse is out of usage; trying again at {time.strftime('%H:%M', time.localtime(reset))}")
            return None
        out_of_usage = mine.get("waited_for") == mine["job"]
        if mine.get("key") == key:
            if not prev or prev["status"] == "done":
                return None      # this work was handed over and finished
            if not out_of_usage and (mine.get("tries", 0) > RETRIES or time.time() - mine.get("at", 0) < RETRY_AFTER_SEC):
                return None
    from app.services import agent_usage
    usage = await asyncio.to_thread(agent_usage.muse)
    if usage.get("out"):             # check first, instead of starting a job that fails on its first step
        until = usage.get("until") or time.time() + QUOTA_WAIT_SEC
        state[repo] = {**mine, "wait_until": until, "waited_for": mine.get("job")}
        _save_state(state)
        logger.info(f"handoff for {repo}: Muse is out of usage; starting at {time.strftime('%H:%M', time.localtime(until))}")
        return None
    same = mine.get("key") == key
    tries = (mine.get("tries", 0) + (0 if out_of_usage else 1)) if same else 1
    try:
        j = await crew.start(TASK.format(heading=newest["heading"]), folder=repo, agent="muse", isolate=False, kind="handoff",
                             title=f"Handoff: work for Muse Code ({len(pending)} section{'s' if len(pending) != 1 else ''})")
    except crew.CrewError as e:
        logger.warning(f"handoff for {repo}: couldn't start Muse: {e}")
        state[repo] = {**mine, "key": key, "error": str(e), "at": time.time(), "tries": tries}
        _save_state(state)
        return None
    state[repo] = {"key": key, "job": j["id"], "heading": newest["heading"], "at": time.time(), "tries": tries}
    _save_state(state)
    logger.info(f"handoff for {repo}: started Muse job {j['id']} for {newest['heading']}")
    return j


async def _loop() -> None:
    while True:
        for repo in repos():
            try:
                await check(repo)
            except Exception as e:
                logger.warning(f"handoff dispatch {repo}: {e}")
        await asyncio.sleep(TICK_SEC)


def start() -> None:
    t = _state["task"]
    if t is None or t.done():
        try:
            _state["task"] = asyncio.get_running_loop().create_task(_loop())
        except RuntimeError:
            pass       # no running loop (imported from a script or a test)
