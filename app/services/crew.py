"""
The Crew: jobs Xod hands to coding agents and conducts natively, starting with Muse (services/muse_native.py,
Muse's own protocol). Each job is one Muse session in a folder:

  start(task, folder)   a job: Muse gets the task and works; every step is recorded as it happens (what it says,
                        each tool it runs with its status, files it edits)
  message(job, text)    follow-up or steering ("also add tests"; steer=True changes course mid-turn)
  interrupt / stop
  approvals             Muse asks before running things (its approval mode decides what): each request becomes a
                        Crew approval, shown on the Crew board, in the approvals history and on the phone; Xod's
                        safety check looks first and adds its warning. Your decision goes back to Muse; nothing
                        is ever approved automatically.
  questions             Muse's questions for you, the same way, answered by picking an option or writing.

Claude Code jobs (services/claude_native.py) work the same way on the board: one `claude -p` per job over its
stream-json protocol, every permission it would ask for sent to Xod (--permission-prompt-tool stdio), its questions
(AskUserQuestion) as Crew questions, follow-ups into the same running conversation, or --resume after a restart.
Connected apps Xod may share (GitHub) go to the job in memory, never into a file.

One Muse host (`muse serve`) is kept running and reused, so only the first job waits for Muse to start its tools
(it starts every MCP server in its settings first: minutes, with many). It stops after HOST_IDLE minutes unused.
"""
import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR
from app.services.claude_native import ClaudeError, ClaudeRun
from app.services.muse_native import MuseError, MuseHost

logger = logging.getLogger("xod.crew")

CREW_FILE = DATA_DIR / "crew.json"
MAX_STEPS = 400
HOST_IDLE = 30 * 60
MAX_JOBS_RUNNING = 4

_jobs: Dict[str, Dict[str, Any]] = {}
_by_session: Dict[str, str] = {}
_host: Dict[str, Any] = {"host": None, "lock": None, "used": 0.0, "starting": False}
_runs: Dict[str, ClaudeRun] = {}           # job id -> its running Claude Code
AGENTS = {"muse": "Muse", "claude": "Claude Code"}
_listeners: List[Any] = []                 # callables(job_id, event) for whoever is watching (the board, a waiting tool)


class CrewError(Exception):
    pass


# ------------------------------------------------------------------ keeping jobs
def _load() -> None:
    if _jobs:
        return
    try:
        for j in json.loads(CREW_FILE.read_text()):
            if j.get("status") in ("starting", "working", "waiting"):
                j["status"], j["note"] = "interrupted", "Xod restarted while it was working: send a message to carry on."
            j.setdefault("approvals", {})
            j.setdefault("questions", {})
            _jobs[j["id"]] = j
            if j.get("session_id"):
                _by_session[j["session_id"]] = j["id"]
    except (OSError, ValueError):
        pass


def _save() -> None:
    try:
        keep = sorted(_jobs.values(), key=lambda j: j["started"])[-60:]
        CREW_FILE.write_text(json.dumps(keep, default=str))
    except OSError:
        pass


def _view(j: Dict[str, Any], steps: int = 60) -> Dict[str, Any]:
    hide = ("input", "suggestions")             # Claude Code's raw tool input (a whole file for Write): not for the board
    pend_a = [{k: v for k, v in a.items() if k not in hide} for a in j["approvals"].values() if a["state"] == "waiting"]
    pend_q = [{k: v for k, v in q.items() if k not in hide} for q in j["questions"].values() if q["state"] == "waiting"]
    return {**{k: v for k, v in j.items() if k not in ("steps", "approvals", "questions", "_text")},
            "steps": j["steps"][-steps:], "step_count": len(j["steps"]), "approvals": pend_a, "questions": pend_q,
            "answer": j.get("answer") or j.get("_text") or ""}


def jobs(active_only: bool = False) -> List[Dict[str, Any]]:
    _load()
    out = [_view(j, steps=8) for j in sorted(_jobs.values(), key=lambda j: -j["started"])]
    return [j for j in out if j["status"] in ("starting", "working", "waiting")] if active_only else out


def job(jid: str) -> Dict[str, Any]:
    _load()
    j = _jobs.get(jid) or next((x for x in _jobs.values() if x.get("name") == jid), None)
    if not j:
        raise CrewError(f"There's no Crew job {jid}.")
    return j


def _emit(j: Dict[str, Any], event: Dict[str, Any]) -> None:
    for f in list(_listeners):
        try:
            f(j["id"], event)
        except Exception:
            pass


def _step(j: Dict[str, Any], kind: str, text: str, **extra: Any) -> None:
    s = {"at": time.time(), "kind": kind, "text": (text or "")[:600], **extra}
    j["steps"].append(s)
    del j["steps"][:-MAX_STEPS]
    j["updated"] = time.time()
    _emit(j, {"type": "step", "step": s})


# ------------------------------------------------------------------ the Muse host
async def _muse() -> MuseHost:
    from app.services import agent_clis
    if _host["lock"] is None:
        _host["lock"] = asyncio.Lock()
    async with _host["lock"]:
        h: Optional[MuseHost] = _host["host"]
        busy = any(j["status"] in ("working", "waiting") for j in _jobs.values())
        if h and h.running() and _host.get("stale") and not busy:
            await h.stop()                            # the tools for jobs changed: start Muse again with the new set
            h = None
        if h and h.running():
            _host["used"] = time.time()
            return h
        _host["stale"] = False
        path = agent_clis.which("muse")
        if not path:
            raise CrewError("Muse isn't installed. Xod can install it (with your OK): cli_agent_install muse.")
        env = dict(agent_clis._env(), XDG_CONFIG_HOME=str(_muse_config()))
        h = MuseHost(path, str(agent_clis.HOME), env=env)
        h.on_event, h.on_request = _on_event, _on_request
        await h.start(timeout=180)
        _host["host"], _host["used"] = h, time.time()
        asyncio.create_task(_idle_watch(h))
        return h


MUSE_CONFIG = DATA_DIR / "muse-config"


def muse_tools() -> Dict[str, Any]:
    """Muse's own tools (the MCP servers in its settings) and which ones Crew jobs get. Each one Muse starts before a
    session can begin (31 took over 3 minutes), so jobs get none unless you pick some."""
    from app.services.storage import get_setting
    try:
        settings = json.loads((_user_muse_dir() / "settings.json").read_text())
    except (OSError, ValueError):
        settings = {}
    try:
        chosen = json.loads(get_setting("crew_muse_tools", "") or "[]")
    except ValueError:
        chosen = []
    names = sorted((settings.get("mcpServers") or {}).keys())
    return {"available": names, "chosen": [n for n in chosen if n in names]}


def set_muse_tools(names: List[str]) -> Dict[str, Any]:
    from app.services.storage import set_setting
    avail = set(muse_tools()["available"])
    set_setting("crew_muse_tools", json.dumps([n for n in names if n in avail]))
    _host["stale"] = True          # Muse restarts with the new set once no job is working (never under a running job)
    return muse_tools()


def _user_muse_dir():
    from pathlib import Path
    import os
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "muse"


def _muse_config():
    """Xod's own copy of your Muse settings for Crew jobs: everything as you set it, except that only the tools you picked
    for jobs are there. Your sign-in and trusted folders are linked, not copied, and your own settings are never changed."""
    import os
    src = _user_muse_dir()
    dst = MUSE_CONFIG / "muse"
    dst.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(MUSE_CONFIG, 0o700)
    except OSError:
        pass
    try:
        settings = json.loads((src / "settings.json").read_text())
    except (OSError, ValueError):
        settings = {}
    chosen = set(muse_tools()["chosen"])
    servers = {k: v for k, v in (settings.get("mcpServers") or {}).items() if k in chosen}
    settings.pop("mcpServers", None)
    if servers:
        settings["mcpServers"] = servers
    out = dst / "settings.json"
    out.write_text(json.dumps(settings, indent=1))
    os.chmod(out, 0o600)
    for name in ("auth.json", "trust.json"):
        link = dst / name
        if link.is_symlink() or link.exists():
            link.unlink()
        if (src / name).exists():
            link.symlink_to(src / name)
    return MUSE_CONFIG


async def _idle_watch(h: MuseHost) -> None:
    while h.running():
        await asyncio.sleep(60)
        busy = any(j["status"] in ("starting", "working", "waiting") for j in _jobs.values())
        if not busy and time.time() - _host["used"] > HOST_IDLE:
            logger.info("Stopping the Muse host: unused for a while")
            await h.stop()
            if _host["host"] is h:
                _host["host"] = None


async def shutdown() -> None:
    h = _host.get("host")
    if h:
        await h.stop()
        _host["host"] = None
    for jid in list(_runs):
        await _runs.pop(jid).stop()


# ------------------------------------------------------------------ jobs
def default_agent() -> str:
    """Claude Code when it's installed (and Muse isn't asked for), else Muse."""
    from app.services import agent_clis
    return "claude" if agent_clis.which("claude") or not agent_clis.which("muse") else "muse"


async def start(task: str, folder: str = "", title: str = "", approval_mode: Optional[str] = None, agent: str = "") -> Dict[str, Any]:
    """Hands a task to a coding agent (Claude Code or Muse) in a folder. Returns the job at once; it works in the background."""
    from app.services import agent_clis
    _load()
    agent = (agent or "").strip().lower().replace("claude code", "claude").replace("claude-code", "claude") or default_agent()
    if agent not in AGENTS:
        raise CrewError(f"The Crew works with {' and '.join(AGENTS.values())}, not {agent}.")
    if not agent_clis.which(agent):
        raise CrewError(f"{AGENTS[agent]} isn't installed. Xod can set it up: Tools → Agent setup, or say \"set up {AGENTS[agent]}\".")
    if not (task or "").strip():
        raise CrewError(f"What should {AGENTS[agent]} do?")
    cwd = agent_clis._folder(folder)
    if len([j for j in _jobs.values() if j["status"] in ("starting", "working", "waiting")]) >= MAX_JOBS_RUNNING:
        raise CrewError(f"{MAX_JOBS_RUNNING} Crew jobs are already working: wait for one, or stop one.")
    jid = uuid.uuid4().hex[:8]
    n = 1 + sum(1 for j in _jobs.values() if j["agent"] == agent)
    j = {"id": jid, "name": f"{agent}-job-{n}", "agent": agent, "title": (title or task.strip().splitlines()[0])[:100], "task": task,
         "folder": cwd, "status": "starting", "started": time.time(), "updated": time.time(), "session_id": None, "turn_id": None,
         "steps": [], "approvals": {}, "questions": {}, "_text": "", "answer": "", "usage": {}, "error": None}
    _jobs[jid] = j
    if agent == "claude":
        _step(j, "status", "Starting Claude Code")
        asyncio.create_task(_claude_start(j, j["task"]))
        _save()
        return _view(j)
    _step(j, "status", "Starting Muse" + (" (the first job waits while it starts its tools)" if not (_host["host"] and _host["host"].running()) else ""))
    asyncio.create_task(_run_start(j, approval_mode))
    _save()
    return _view(j)


async def _run_start(j: Dict[str, Any], approval_mode: Optional[str]) -> None:
    try:
        h = await _muse()
        shared = {name: {"url": c["url"], "headers": {"Authorization": f"Bearer {c['token']}"}} for name, c in _shared().items()}
        s = await h.start_session(j["folder"], approval_mode=approval_mode, mcp_servers=shared or None)
        j["session_id"] = s["sessionId"]
        _by_session[s["sessionId"]] = j["id"]
        _step(j, "status", "Muse is working on it")
        await _send(j, j["task"])
    except (MuseError, CrewError) as e:
        _fail(j, str(e))
    except Exception as e:
        logger.exception("Crew job didn't start")
        _fail(j, f"It didn't start: {e}")


def _fail(j: Dict[str, Any], why: str) -> None:
    j.update(status="failed", error=why, ended=time.time())
    _step(j, "error", why)
    _save()
    _emit(j, {"type": "done", "status": "failed"})


async def _send(j: Dict[str, Any], text: str, if_busy: str = "queue") -> None:
    h = await _muse()
    j["status"] = "working"
    j["_text"] = ""
    r = await h.send(j["session_id"], text, if_busy=if_busy)
    j["turn_id"] = r.get("turnId")
    _save()


async def message(jid: str, text: str, steer: bool = False) -> Dict[str, Any]:
    """A follow-up to a job (or, with steer, a change of course while it's working)."""
    j = job(jid)
    if not (text or "").strip():
        raise CrewError("What should it hear?")
    if j["agent"] == "claude":
        return await _claude_message(j, text, steer)
    if not j.get("session_id"):
        raise CrewError("That job never got going: start a new one.")
    h = await _muse()
    if j["status"] in ("interrupted", "done", "failed", "stopped") and not h.running():
        pass
    if j["session_id"] not in _by_session or j["status"] == "interrupted":
        try:
            await h.resume_session(j["session_id"])
            _by_session[j["session_id"]] = j["id"]
        except MuseError as e:
            raise CrewError(f"Muse couldn't pick that session up again: {e}")
    _step(j, "you", text)
    await _send(j, text, "steer" if steer and j["status"] == "working" else "queue")
    return _view(j)


async def interrupt(jid: str) -> Dict[str, Any]:
    j = job(jid)
    if j["agent"] == "claude":
        r = _runs.get(j["id"])
        if r and r.running() and j["status"] in ("working", "waiting"):
            j["_interrupting"] = True
            try:
                await r.interrupt()
            except ClaudeError as e:
                raise CrewError(str(e))
        _step(j, "status", "Interrupted")
        return _view(j)
    h = _host.get("host")
    if h and h.running() and j.get("session_id") and j["status"] in ("working", "waiting"):
        try:
            await h.interrupt(j["session_id"])
        except MuseError as e:
            raise CrewError(str(e))
    _step(j, "status", "Interrupted")
    return _view(j)


async def stop(jid: str) -> Dict[str, Any]:
    j = job(jid)
    if j["status"] in ("starting", "working", "waiting"):
        try:
            await interrupt(jid)
        except CrewError:
            pass
    for a in j["approvals"].values():
        if a["state"] == "waiting":
            a["state"] = "dropped"
    r = _runs.pop(j["id"], None)
    if r:
        await r.stop()
    j.update(status="stopped", ended=time.time())
    _save()
    _emit(j, {"type": "done", "status": "stopped"})
    return _view(j)


# ------------------------------------------------------------------ what Muse says
def _job_for(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    jid = _by_session.get(params.get("sessionId") or "")
    return _jobs.get(jid) if jid else None


async def _on_event(method: str, p: Dict[str, Any]) -> None:
    j = _job_for(p)
    if not j:
        return
    it = p.get("item") or {}
    kind = it.get("kind")
    if method == "item/delta" and p.get("field", "text") == "text":
        j["_text"] = (j.get("_text") or "") + (p.get("delta") or "")
        _emit(j, {"type": "delta", "text": p.get("delta") or ""})
        return
    if method in ("item/started", "item/completed", "item/updated") and kind:
        if kind == "agentMessage" and method == "item/completed":
            j["_text"] = it.get("text") or j.get("_text") or ""
            j["answer"] = j["_text"]
            _step(j, "says", j["_text"][:600])
        elif kind == "toolCall":
            dl = it.get("displayLabel") if isinstance(it.get("displayLabel"), dict) else {}
            st = it.get("status")
            label = dl.get("active") if st == "inProgress" else dl.get("done") if st == "completed" else dl.get("neutral")
            what = it.get("statusLine") or label or _tool_brief(it)
            path = _tool_path(it)
            if method == "item/started":
                _step(j, "tool", what, tool=it.get("tool"), item=it.get("itemId"), status="running")
            elif method == "item/completed":
                summ = it.get("patchSummary") if isinstance(it.get("patchSummary"), dict) else {}
                edit = f" (+{summ.get('added', 0)} −{summ.get('removed', 0)} lines)" if summ else ""
                fields = dict(tool=it.get("tool"), item=it.get("itemId"), status=st,
                              result=(it.get("visibleOutput") or it.get("failureReason") or "")[:400], file=path if summ else None)
                running = next((x for x in reversed(j["steps"]) if x.get("item") == it.get("itemId") and x["kind"] == "tool"), None)
                if running:                        # the same step, now finished: update its row, not a second one
                    running.update(text=(what + edit)[:600], at=time.time(), **fields)
                    j["updated"] = time.time()
                    _emit(j, {"type": "step", "step": running})
                else:
                    _step(j, "tool", what + edit, **fields)
                if summ and path:
                    j["files"] = sorted(set(j.get("files") or []) | {path})
        elif kind == "reasoning" and method == "item/completed":
            pass
        elif kind in ("subagent", "webSearch", "workflow", "userShell", "compaction") and method == "item/completed":
            _step(j, kind, it.get("fallbackText") or it.get("objective") or it.get("commandText") or it.get("text") or kind)
        # hookRun, reminderChild, damagedRange … are Muse's own bookkeeping: not shown
        return
    if method == "turn/completed":
        terminal = p.get("terminal")
        j["usage"] = p.get("usage") or j.get("usage") or {}
        if terminal in ("completed", "succeeded", "success", "done"):
            j.update(status="done", ended=time.time())
            _step(j, "status", "Done")
        elif terminal in ("interrupted", "cancelled", "canceled"):
            j.update(status="stopped" if j["status"] == "stopped" else "interrupted", ended=time.time())
        else:
            err = (p.get("error") or {}).get("message") if isinstance(p.get("error"), dict) else None
            j.update(status="failed", error=err or p.get("reason") or str(terminal), ended=time.time())
            _step(j, "error", j["error"])
        _save()
        _emit(j, {"type": "done", "status": j["status"]})
    elif method == "session/statusChanged" and p.get("status") == "running" and j["status"] == "starting":
        j["status"] = "working"


def _tool_path(it: Dict[str, Any]) -> Optional[str]:
    try:
        args = json.loads(it.get("args") or "{}")
    except ValueError:
        return None
    if isinstance(args, dict):
        for k in ("path", "file_path", "filePath", "file", "target_file"):
            if isinstance(args.get(k), str):
                return args[k]
    return None


def _tool_brief(it: Dict[str, Any]) -> str:
    tool = it.get("tool") or "a tool"
    try:
        args = json.loads(it.get("args") or "{}")
    except ValueError:
        args = {}
    for k in ("command", "cmd", "path", "file_path", "url", "query", "pattern"):
        if isinstance(args, dict) and args.get(k):
            return f"{tool}: {str(args[k])[:200]}"
    return tool


# ------------------------------------------------------------------ Muse asks: approvals and questions
def _subject_text(subj: Dict[str, Any], tool: str) -> str:
    k = subj.get("kind")
    if k == "shell":
        return f"run: {subj.get('command') or ''}"
    if k == "fileAccess":
        return f"{subj.get('access') or 'use'} {subj.get('path') or 'a file'}"
    if k == "network":
        return f"connect to {subj.get('host') or '?'}" + (f":{subj['port']}" if subj.get("port") else "")
    if k == "tool":
        return f"use {subj.get('toolName') or tool}"
    return f"{k or 'do something'} {subj.get('target') or subj.get('command') or ''}".strip()


def _safety(command: str, what: str) -> str:
    """Xod's own look at what Muse asks to run. These arrive outside any chat turn, so the checks that compare an action
    with the person's request don't apply; these ones stand on their own."""
    text = command or what
    try:
        from app.agent.worker import refused
        if refused("computer_run_command", {"command": text}):
            return "Xod would never run this itself: it could wipe a disk or the system, or switch off its protection."
        from app.services import sentinel
        if sentinel._REMOTE_RUN_RE.search(text):
            return "It downloads a program from the internet and runs it straight away."
        if any(rx.search(text) for rx in sentinel._SECRET_RES):
            return "It has what looks like a password or key in it."
        if re.search(r"\b(?:sudo|chmod\s+-R\s+777|chown\s+-R)\b", text):
            return "It changes system-wide permissions or runs as administrator."
        if re.search(r"\bgit\s+push\b.*--force|\bgit\s+reset\s+--hard\b|\brm\s+-[a-z]*r", text):
            return "It deletes or rewrites things that may not come back."
    except Exception:
        pass
    return ""


async def _on_request(method: str, p: Dict[str, Any]) -> None:
    j = _job_for(p)
    if not j:
        return
    if method == "approval/request":
        aid = p.get("approvalId")
        if not aid or aid in j["approvals"]:
            return
        subj = p.get("subject") or {}
        what = _subject_text(subj, p.get("toolName") or "")
        warning = _safety(subj.get("command") or "", what)
        choices = [{"id": c.get("choiceId"), "label": c.get("label"), "decision": c.get("decision"), "scope": c.get("scope")}
                   for c in p.get("availableChoices") or []]
        a = {"id": aid, "job": j["id"], "what": what, "tool": p.get("toolName"), "subject": subj, "choices": choices,
             "requirement": p.get("currentRequirementId"), "warning": warning, "state": "waiting", "at": time.time(),
             "protected": bool(p.get("protectedWrite"))}
        j["approvals"][aid] = a
        j["status"] = "waiting"
        _step(j, "asks", f"Muse wants to {what}" + (f" (safety check: {warning})" if warning else ""), approval=aid)
        try:
            from app.services import agent_log
            agent_log.approval_opened(f"crew-{aid}", "crew", "Muse", f"{j['title'][:60]}: {what}"[:200], warning)
        except Exception:
            pass
        _notify_phone(j, f"Muse ({j['title'][:60]}) wants to {what}." + (f"\nSafety check: {warning}" if warning else "")
                      + "\nOpen Xod → Crew to approve or deny.")
        _save()
        _emit(j, {"type": "approval", "approval": a})
    elif method == "userInput/request":
        uid = p.get("userInputId")
        if not uid or uid in j["questions"]:
            return
        qs = [{"id": q.get("id"), "header": q.get("header"), "question": q.get("question"),
               "options": [o.get("label") for o in q.get("options") or [] if isinstance(o, dict)], "selection": q.get("selection")}
              for q in p.get("questions") or []]
        q = {"id": uid, "job": j["id"], "questions": qs, "state": "waiting", "at": time.time()}
        j["questions"][uid] = q
        j["status"] = "waiting"
        _step(j, "asks", "Muse asks: " + " / ".join(x["question"] or x["header"] or "" for x in qs), question=uid)
        _notify_phone(j, f"Muse ({j['title'][:60]}) asks: " + " / ".join(x["question"] or "" for x in qs) + "\nAnswer in Xod → Crew.")
        _save()
        _emit(j, {"type": "question", "question": q})


def _notify_phone(j: Dict[str, Any], text: str) -> None:
    try:
        from app.services import phone
        phone._fire(phone.send(text))
    except Exception:
        pass


async def decide(jid: str, approval_id: str, decision: str = "once") -> Dict[str, Any]:
    """Your decision on one of Muse's approvals: once, session (for the rest of this job) or deny."""
    j = job(jid)
    a = j["approvals"].get(approval_id)
    if not a or a["state"] != "waiting":
        raise CrewError("That request was already answered, or isn't there.")
    want = {"once": ("approved", "once"), "session": ("approvedForSession", "session"), "deny": ("denied", None)}.get(decision)
    if not want:
        raise CrewError("Decide once, session or deny.")
    if j["agent"] == "claude":
        return await _claude_decide(j, a, decision)
    choice = next((c for c in a["choices"] if c["decision"] == want[0] and (want[1] is None or c["scope"] == want[1])), None) \
        or next((c for c in a["choices"] if c["decision"] == want[0]), None) \
        or (next((c for c in a["choices"] if str(c["decision"]).startswith("denied")), None) if decision == "deny" else None)
    if not choice:
        raise CrewError(f"Muse didn't offer that ({', '.join(c['label'] or c['decision'] for c in a['choices'])}).")
    h = await _muse()
    try:
        await h.decide(j["session_id"], approval_id, choice["id"], a.get("requirement"))
    except MuseError as e:
        raise CrewError(f"Muse didn't take the decision: {e}")
    a["state"] = "approved" if decision != "deny" else "denied"
    try:
        from app.services import agent_log
        agent_log.approval_decided(f"crew-{approval_id}", "allowed" if decision != "deny" else "declined")
    except Exception:
        pass
    _step(j, "you", ("Approved" if decision != "deny" else "Denied") + f": {a['what']}" + (" (for this job)" if decision == "session" else ""))
    if not any(x["state"] == "waiting" for x in j["approvals"].values()) and not any(x["state"] == "waiting" for x in j["questions"].values()):
        j["status"] = "working"
    _save()
    return _view(j)


async def answer(jid: str, question_id: str, answers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Answers to one of Muse's questions: [{"question": id, "choice": label} or {"question": id, "text": "…"}]."""
    j = job(jid)
    q = j["questions"].get(question_id)
    if not q or q["state"] != "waiting":
        raise CrewError("That question was already answered, or isn't there.")
    if j["agent"] == "claude":
        return await _claude_answer(j, q, answers)
    wire = []
    for qq in q["questions"]:
        a = next((x for x in answers if x.get("question") in (qq["id"], None)), None) or {}
        if a.get("choice"):
            wire.append({"questionId": qq["id"], "selectedLabel": a["choice"]})
        elif a.get("choices"):
            wire.append({"questionId": qq["id"], "selectedLabels": a["choices"]})
        else:
            wire.append({"questionId": qq["id"], "freeText": str(a.get("text") or "")[:500]})
    h = await _muse()
    try:
        await h.answer(j["session_id"], question_id, wire)
    except MuseError as e:
        raise CrewError(f"Muse didn't take the answer: {e}")
    q["state"] = "answered"
    _step(j, "you", "Answered: " + "; ".join(str(w.get("selectedLabel") or w.get("selectedLabels") or w.get("freeText")) for w in wire))
    if not any(x["state"] == "waiting" for x in j["approvals"].values()):
        j["status"] = "working"
    _save()
    return _view(j)


async def wait(jid: str, seconds: float = 120) -> Dict[str, Any]:
    """Waits until the job is done, needs you, or the time is up; returns where it stands."""
    j = job(jid)
    end = time.time() + seconds
    while time.time() < end and j["status"] in ("starting", "working"):
        await asyncio.sleep(0.5)
    return _view(j)


# ------------------------------------------------------------------ Claude Code jobs
def _shared() -> Dict[str, Dict[str, Any]]:
    try:
        from app.services import connectors
        return connectors.for_agents()
    except Exception:
        logger.exception("Shared connectors")
        return {}


CLAUDE_MCP = DATA_DIR / "crew-claude-mcp.json"


def _claude_env_and_config() -> tuple:
    """Claude Code's environment and its --mcp-config for shared connectors. The file names each token by an environment
    variable (${XOD_SHARED_GITHUB}); the token itself is only in the job's environment."""
    import os
    from app.services import agent_clis
    env = dict(agent_clis._env())
    servers = {}
    for name, c in _shared().items():
        var = "XOD_SHARED_" + re.sub(r"[^A-Z0-9]", "_", name.upper())
        env[var] = c["token"]
        servers[name] = {"type": "http", "url": c["url"], "headers": {"Authorization": f"Bearer ${{{var}}}"}}
    if not servers:
        return env, ""
    CLAUDE_MCP.write_text(json.dumps({"mcpServers": servers}, indent=1))
    os.chmod(CLAUDE_MCP, 0o600)
    return env, str(CLAUDE_MCP)


async def _claude_run(j: Dict[str, Any], resume: str = "") -> ClaudeRun:
    from app.services import agent_clis
    r = _runs.get(j["id"])
    if r and r.running():
        return r
    path = agent_clis.which("claude")
    if not path:
        raise CrewError("Claude Code isn't installed. Xod can set it up: Tools → Agent setup.")
    env, mcp = _claude_env_and_config()
    r = ClaudeRun(path, j["folder"], env=env, resume=resume, mcp_config=mcp)
    jid = j["id"]

    async def on_event(m: Dict[str, Any]) -> None:
        await _claude_event(_jobs.get(jid), m)

    async def on_request(m: Dict[str, Any]) -> None:
        await _claude_request(_jobs.get(jid), m)

    async def on_exit(code: Optional[int], err: str) -> None:
        jj = _jobs.get(jid)
        if _runs.get(jid) is r:
            _runs.pop(jid, None)
        if jj and jj["status"] in ("starting", "working", "waiting"):
            _fail(jj, "Claude Code stopped unexpectedly" + (f": {err}" if err else f" (exit code {code})."))

    r.on_event, r.on_request, r.on_exit = on_event, on_request, on_exit
    try:
        await r.start(timeout=90)
    except ClaudeError as e:
        raise CrewError(str(e))
    _runs[jid] = r
    asyncio.create_task(_claude_idle(jid, r))
    return r


async def _claude_idle(jid: str, r: ClaudeRun) -> None:
    """A finished job's Claude Code stays up for follow-ups, then stops after HOST_IDLE (a follow-up resumes it)."""
    while r.running():
        await asyncio.sleep(60)
        j = _jobs.get(jid)
        if not j or (j["status"] not in ("starting", "working", "waiting") and time.time() - j.get("updated", 0) > HOST_IDLE):
            await r.stop()
            if _runs.get(jid) is r:
                _runs.pop(jid, None)


async def _claude_start(j: Dict[str, Any], text: str) -> None:
    try:
        r = await _claude_run(j)
        j["status"] = "working"
        _step(j, "status", "Claude Code is working on it")
        await r.send(text)
        _save()
    except (ClaudeError, CrewError) as e:
        _fail(j, str(e))
    except Exception as e:
        logger.exception("Crew job didn't start")
        _fail(j, f"It didn't start: {e}")


async def _claude_message(j: Dict[str, Any], text: str, steer: bool) -> Dict[str, Any]:
    r = _runs.get(j["id"])
    if not (r and r.running()):
        if not j.get("session_id"):
            raise CrewError("That job never got going: start a new one.")
        try:
            r = await _claude_run(j, resume=j["session_id"])     # after a restart or a long pause: the same conversation
        except CrewError as e:
            raise CrewError(f"Claude Code couldn't pick that conversation up again: {e}")
    elif steer and j["status"] in ("working", "waiting"):
        j["_interrupting"] = True
        try:
            await r.interrupt()
        except ClaudeError:
            pass
    _step(j, "you", text)
    j.update(status="working", _text="", error=None)
    j.pop("ended", None)
    try:
        await r.send(text)
    except ClaudeError as e:
        raise CrewError(str(e))
    _save()
    return _view(j)


QUIET_TOOLS = {"ToolSearch"}                 # Claude Code's own bookkeeping: not a step a person needs to see


def _short(path: Any, folder: str) -> str:
    """A path inside the job's folder, relative to it (hello.py, not /Users/…/project/hello.py)."""
    p = str(path or "")
    f = (folder or "").rstrip("/") + "/"
    return p[len(f):] if f != "/" and p.startswith(f) else p.replace(str(__import__("pathlib").Path.home()), "~", 1)


def _claude_brief(name: str, inp: Dict[str, Any], folder: str = "", done: bool = True) -> str:
    """One line for a tool step, as a person would say it: "Writing hello.py" while it runs, "Wrote hello.py" after."""
    inp = inp if isinstance(inp, dict) else {}
    f = _short(inp.get("file_path"), folder) or "a file"
    verbs = {"Bash": ("Running", "Ran", str(inp.get("command") or "")[:300].replace((folder or "\0").rstrip("/") + "/", "")),
             "Edit": ("Editing", "Edited", f), "MultiEdit": ("Editing", "Edited", f), "Write": ("Writing", "Wrote", f),
             "Read": ("Reading", "Read", f), "Grep": ("Searching for", "Searched for", inp.get("pattern") or ""),
             "Glob": ("Looking for", "Looked for", inp.get("pattern") or ""), "WebFetch": ("Fetching", "Fetched", inp.get("url") or ""),
             "WebSearch": ("Searching the web for", "Searched the web for", inp.get("query") or ""),
             "Task": ("Handing a part to a helper:", "Handed a part to a helper:", inp.get("description") or "")}
    if name in verbs:
        now, then, what = verbs[name]
        return f"{then if done else now} {what}".strip()
    if name == "TodoWrite":
        return "Updated its to-do list" if done else "Updating its to-do list"
    if name.startswith("mcp__"):
        parts = name.split("__")
        return f"{'Used' if done else 'Using'} {parts[-1].replace('_', ' ')} ({parts[1] if len(parts) > 2 else 'a tool'})"
    return name


def _claude_lines(inp: Dict[str, Any], name: str) -> str:
    if name == "Write":
        return f" (+{len(str(inp.get('content') or '').splitlines())} lines)"
    if name == "Edit":
        old, new = str(inp.get("old_string") or ""), str(inp.get("new_string") or "")
        return f" (+{len(new.splitlines())} −{len(old.splitlines())} lines)"
    if name == "MultiEdit":
        edits = inp.get("edits") or []
        return f" (+{sum(len(str(e.get('new_string') or '').splitlines()) for e in edits)} −{sum(len(str(e.get('old_string') or '').splitlines()) for e in edits)} lines)"
    return ""


async def _claude_event(j: Optional[Dict[str, Any]], m: Dict[str, Any]) -> None:
    if not j:
        return
    t = m.get("type")
    if t == "system" and m.get("subtype") == "init":
        if m.get("session_id") and j.get("session_id") != m["session_id"]:
            j["session_id"] = m["session_id"]
            _save()
        return
    if t == "assistant":
        for c in (m.get("message") or {}).get("content") or []:
            if c.get("type") == "text" and (c.get("text") or "").strip():
                j["_text"] = c["text"]
                _step(j, "says", c["text"][:600])
            elif c.get("type") == "tool_use":
                name, inp = c.get("name") or "", c.get("input") or {}
                if name == "AskUserQuestion" or name in QUIET_TOOLS:
                    continue                                   # the question shows as itself; bookkeeping not at all
                _step(j, "tool", _claude_brief(name, inp, j["folder"], done=False), tool=name, item=c.get("id"), status="running",
                      file=_short(inp.get("file_path"), j["folder"]) if name in ("Edit", "Write", "MultiEdit") else None,
                      _done=_claude_brief(name, inp, j["folder"]) + _claude_lines(inp, name))
        return
    if t == "user":
        content = (m.get("message") or {}).get("content")
        for c in content if isinstance(content, list) else []:
            if c.get("type") != "tool_result":
                continue
            step = next((x for x in reversed(j["steps"]) if x.get("item") == c.get("tool_use_id") and x["kind"] == "tool"), None)
            if not step:
                continue
            res = c.get("content")
            if isinstance(res, list):
                res = " ".join(str(x.get("text") or "") for x in res if isinstance(x, dict))
            failed = bool(c.get("is_error"))
            quiet = not failed and step.get("tool") in ("Edit", "MultiEdit", "Write")   # "The file … has been updated": nothing to read
            step.update(status="failed" if failed else "completed", result="" if quiet else str(res or "")[:400], at=time.time())
            done_text = step.pop("_done", "")
            if not failed:
                step["text"] = (done_text or step["text"])[:600]
                if step.get("file"):
                    j["files"] = sorted(set(j.get("files") or []) | {step["file"]})
            j["updated"] = time.time()
            _emit(j, {"type": "step", "step": step})
        return
    if t == "result":
        u = m.get("usage") or {}
        j["usage"] = {"inputTokens": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0),
                      "outputTokens": u.get("output_tokens") or 0, "costUsd": m.get("total_cost_usd"),
                      "seconds": round((m.get("duration_ms") or 0) / 1000, 1)}
        if m.get("result"):
            j["answer"] = j["_text"] = str(m["result"])
        for a in j["approvals"].values():
            if a["state"] == "waiting":
                a["state"] = "dropped"
        if j.pop("_interrupting", False) or j["status"] == "stopped":
            j.update(status="stopped" if j["status"] == "stopped" else "interrupted", ended=time.time())
        elif m.get("subtype") == "success" and not m.get("is_error"):
            j.update(status="done", ended=time.time())
            _step(j, "status", "Done")
        else:
            why = m.get("result") or "; ".join(m.get("errors") or []) or str(m.get("subtype") or "it stopped")
            j.update(status="failed", error=str(why)[:400], ended=time.time())
            _step(j, "error", j["error"])
        _save()
        _emit(j, {"type": "done", "status": j["status"]})


def _claude_what(name: str, inp: Dict[str, Any], folder: str = "") -> str:
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash":
        cmd = str(inp.get("command") or "")
        return f"run: {cmd.replace(folder.rstrip('/') + '/', '') if folder else cmd}"
    if name == "Write":
        return f"write {_short(inp.get('file_path'), folder) or 'a file'}"
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"edit {_short(inp.get('file_path') or inp.get('notebook_path'), folder) or 'a file'}"
    if name == "WebFetch":
        return f"fetch {inp.get('url') or 'a page'}"
    if name == "WebSearch":
        return f"search the web for {inp.get('query') or ''}"
    if name.startswith("mcp__"):
        parts = name.split("__")
        return f"use {parts[-1].replace('_', ' ')} in {parts[1] if len(parts) > 2 else 'a connected app'}"
    return f"use {name}"


async def _claude_request(j: Optional[Dict[str, Any]], m: Dict[str, Any]) -> None:
    req = m.get("request") or {}
    rid = m.get("request_id") or ""
    if req.get("subtype") != "can_use_tool":
        return
    if not j:
        return
    name, inp = req.get("tool_name") or "", req.get("input") or {}
    if name == "AskUserQuestion":
        qs = [{"id": str(i), "header": q.get("header"), "question": q.get("question"),
               "options": [o.get("label") for o in q.get("options") or [] if isinstance(o, dict)],
               "selection": "multi" if q.get("multiSelect") else "single"} for i, q in enumerate(inp.get("questions") or [])]
        q = {"id": rid, "job": j["id"], "questions": qs, "state": "waiting", "at": time.time(), "input": inp}
        j["questions"][rid] = q
        j["status"] = "waiting"
        _step(j, "asks", "Claude Code asks: " + " / ".join(x["question"] or x["header"] or "" for x in qs), question=rid)
        _notify_phone(j, f"Claude Code ({j['title'][:60]}) asks: " + " / ".join(x["question"] or "" for x in qs) + "\nAnswer in Xod → Crew.")
        _save()
        _emit(j, {"type": "question", "question": q})
        return
    what = _claude_what(name, inp, j["folder"])
    warning = _safety(str(inp.get("command") or ""), what)
    a = {"id": rid, "job": j["id"], "what": what, "tool": name, "subject": {"kind": "shell" if name == "Bash" else "tool", "command": inp.get("command")},
         "choices": [{"id": "once", "label": "Yes", "decision": "approved", "scope": "once"},
                     {"id": "session", "label": "Yes, for this job", "decision": "approvedForSession", "scope": "session"},
                     {"id": "deny", "label": "No", "decision": "denied", "scope": "once"}],
         "input": inp, "suggestions": req.get("permission_suggestions") or [], "warning": warning, "state": "waiting",
         "at": time.time(), "detail": req.get("description") or ""}
    j["approvals"][rid] = a
    j["status"] = "waiting"
    _step(j, "asks", f"Claude Code wants to {what}" + (f" (safety check: {warning})" if warning else ""), approval=rid)
    try:
        from app.services import agent_log
        agent_log.approval_opened(f"crew-{rid}", "crew", "Claude Code", f"{j['title'][:60]}: {what}"[:200], warning)
    except Exception:
        pass
    _notify_phone(j, f"Claude Code ({j['title'][:60]}) wants to {what}." + (f"\nSafety check: {warning}" if warning else "")
                  + "\nOpen Xod → Crew to approve or deny.")
    _save()
    _emit(j, {"type": "approval", "approval": a})


async def _claude_decide(j: Dict[str, Any], a: Dict[str, Any], decision: str) -> Dict[str, Any]:
    r = _runs.get(j["id"])
    if not (r and r.running()):
        a["state"] = "dropped"
        raise CrewError("Claude Code isn't running any more, so it can't take that. Send a message to carry on.")
    try:
        if decision == "deny":
            await r.deny(a["id"], "The person said no. Don't try this again another way; ask them or carry on without it.")
        else:
            # "for this job": the rules Claude Code suggested, kept for this conversation only (never written to your settings)
            perms = [{**s, "destination": "session"} for s in a.get("suggestions") or [] if s.get("type") == "addRules"] \
                if decision == "session" else None
            await r.allow(a["id"], a.get("input") or {}, perms)
    except ClaudeError as e:
        raise CrewError(f"Claude Code didn't take the decision: {e}")
    a["state"] = "approved" if decision != "deny" else "denied"
    try:
        from app.services import agent_log
        agent_log.approval_decided(f"crew-{a['id']}", "allowed" if decision != "deny" else "declined")
    except Exception:
        pass
    _step(j, "you", ("Approved" if decision != "deny" else "Denied") + f": {a['what']}" + (" (for this job)" if decision == "session" else ""))
    if not any(x["state"] == "waiting" for x in j["approvals"].values()) and not any(x["state"] == "waiting" for x in j["questions"].values()):
        j["status"] = "working"
    _save()
    return _view(j)


async def _claude_answer(j: Dict[str, Any], q: Dict[str, Any], answers: List[Dict[str, Any]]) -> Dict[str, Any]:
    r = _runs.get(j["id"])
    if not (r and r.running()):
        q["state"] = "dropped"
        raise CrewError("Claude Code isn't running any more, so it can't take that. Send a message to carry on.")
    inp = dict(q.get("input") or {})
    picked: Dict[str, str] = {}
    for qq in q["questions"]:
        a = next((x for x in answers if x.get("question") in (qq["id"], None)), None) or {}
        val = a.get("choice") or (", ".join(a["choices"]) if a.get("choices") else str(a.get("text") or "")[:500])
        picked[qq["question"] or qq["header"] or qq["id"]] = val
    inp["answers"] = picked
    try:
        await r.allow(q["id"], inp)
    except ClaudeError as e:
        raise CrewError(f"Claude Code didn't take the answer: {e}")
    q["state"] = "answered"
    _step(j, "you", "Answered: " + "; ".join(picked.values()))
    if not any(x["state"] == "waiting" for x in j["approvals"].values()):
        j["status"] = "working"
    _save()
    return _view(j)
