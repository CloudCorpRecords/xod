"""
The harness port: a stable control surface on the beacon (services/xod_network.py) that lets an
operator agent (Tree, the owner's Muse agent on his VM) hand this Xod work and read it back, and relay
work through this Xod to another family member's Xod.

  POST /v1/harness/tasks        {request, target, kind}   hand work here, or relay it to a member
  GET  /v1/harness/tasks/{ref}  how that work is going, claimed kept apart from verified
  GET  /v1/harness/members      the family registry's members (services/family_registry.py)

Two locks, both required, on every call:
  1. Tailscale vouches for the caller: the beacon's own whois check (xod_network._caller) must say
     the source is one of this account's devices.
  2. The caller presents the harness token (the "harness_token" setting) in the X-Harness-Token
     header. The token lives only in this Xod's own settings: never in the family registry, never
     in a repo, never logged. No token configured means the port answers 503 (closed), never open.

Approval semantics are the beacon's, unchanged: a submission for this machine goes through
xod_network.receive_task, so it becomes an approval card here and only the person's Approve puts
it in the task inbox (kind "task") or creates the mission (kind "mission"). The harness adds no
way around that card. A relay hands the request to the member's own beacon (/v1/tasks), where
that machine's card decides; the record here keeps the remote ref and polls it for status.

Honesty rules:
  - An unreachable member is recorded as relay_unreachable (or queued_for_relay when the registry
    has no endpoint for it), with the error kept on the record. Never as a success.
  - A record's claimed_result is what the worker said it did; verified_status and evidence are
    what Xod's own checks established. The two are never merged into one claim (the missions
    report rule, services/missions.py build_report).
  - A member's stock beacon has no mission kind: a relayed mission is forwarded as a task there,
    and the record says so (remote.kind_note) instead of pretending otherwise.

Records live in DATA_DIR/harness_tasks.json, capped at the newest MAX_RECORDS. They survive a
restart of the app; the beacon's own handoff records do not, and a status read that finds its
handoff gone says exactly that on the record.

The family registry is unsigned interim data in F0 (see family_registry.py): it only PICKS the
relay endpoint here. It authorizes nothing. The member's beacon independently whois-checks this
machine before it cards the relayed task, and this port's two locks gate every call.
"""
import hmac
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.paths import DATA_DIR

logger = logging.getLogger("xod.harness")

TOKEN_SETTING = "harness_token"
CANARY_SETTING = "harness_canary_token"
EVENTS_SETTING = "harness_security_events"
MAX_REQUEST = 4000          # mirrors the beacon's cap in xod_network
MAX_RECORDS = 100
RELAY_TIMEOUT = 15          # mirrors xod_network._call

HOW_TO_READ = ("claimed_result is what the worker said it did. verified_status and evidence are "
               "what Xod's own checks established. A plain task's result is the agent's word "
               "until a check covers it, so it reads not_checked rather than borrowed confidence.")

_lock = threading.Lock()
_records: Optional[Dict[str, Dict[str, Any]]] = None


class HarnessError(Exception):
    """A refusal with the HTTP status the beacon route should answer."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class RelayError(Exception):
    """The member's beacon could not be reached or refused the relay."""


# ------------------------------------------------------------------ records

def _store_path() -> Path:
    return DATA_DIR / "harness_tasks.json"


def _load() -> Dict[str, Dict[str, Any]]:
    global _records
    if _records is None:
        try:
            data = json.loads(_store_path().read_text(encoding="utf-8"))
            _records = data if isinstance(data, dict) else {}
        except Exception:
            _records = {}
    return _records


def _save() -> None:
    recs = _load()
    if len(recs) > MAX_RECORDS:
        keep = sorted(recs.values(), key=lambda r: r.get("created_at", 0))[-MAX_RECORDS:]
        recs.clear()
        recs.update({r["ref"]: r for r in keep})
    try:
        tmp = _store_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(recs), encoding="utf-8")
        os.replace(tmp, _store_path())
    except OSError as e:
        logger.warning(f"Harness: could not save records: {e}")


def _view(rec: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: rec.get(k) for k in ("ref", "kind", "target", "request", "caller", "created_at",
                                   "updated_at", "state", "beacon_ref", "remote", "title",
                                   "claimed_result", "verified_status", "evidence", "report",
                                   "error")}
    out["how_to_read"] = HOW_TO_READ
    return out


# ------------------------------------------------------------------ seams
# Module-level so suites can stand in fakes, the way the network suite
# replaces xod_network.cli. The defaults wire the real beacon and registry.

def _caller(ip: str) -> Dict[str, Any]:
    from app.services import xod_network
    return xod_network._caller(ip)


def _receive(ip: str, body: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import xod_network
    return xod_network.receive_task(ip, body)


def _read(ip: str, ref: str) -> Dict[str, Any]:
    from app.services import xod_network
    return xod_network.read_task(ip, ref)


def _members_view() -> Dict[str, Any]:
    from app.services import family_registry
    return family_registry.load_registry()


def _own_ip() -> str:
    from app.services import xod_network
    return xod_network.status().get("ip") or ""


def _own_name() -> str:
    from app.services import xod_network
    return xod_network._about().get("machine") or ""


def _setting(key: str) -> str:
    from app.services.storage import get_setting
    return get_setting(key, "") or ""


def _relay(method: str, endpoint: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One call to a member's beacon. RelayError (never a fake answer) when it cannot be reached."""
    import httpx
    from app.services.xod_network import BEACON_PORT
    try:
        with httpx.Client(timeout=RELAY_TIMEOUT) as c:
            r = c.request(method, f"http://{endpoint}:{BEACON_PORT}{path}", json=body)
    except httpx.HTTPError as e:
        raise RelayError(f"could not reach the member's beacon: {e}")
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code != 200:
        raise RelayError(str(d.get("detail") or f"the member's beacon answered {r.status_code}"))
    return d


# ------------------------------------------------------------------ security

def security_events() -> list:
    """The harness security log (canary presentations), newest last."""
    try:
        events = json.loads(_setting(EVENTS_SETTING) or "[]")
        return events if isinstance(events, list) else []
    except ValueError:
        return []


def _log_security_event(kind: str, caller_ip: str, who: Dict[str, Any]) -> None:
    from app.services.storage import set_setting
    events = security_events()
    events.append({"at": time.time(), "kind": kind, "caller": who.get("host") or "", "ip": caller_ip})
    try:
        set_setting(EVENTS_SETTING, json.dumps(events[-50:]))
    except Exception:
        pass
    logger.warning(f"Harness security event: {kind} from {caller_ip}")


def _authorize(caller_ip: str, headers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Both locks, every call. Returns the whois caller, or raises HarnessError."""
    who = _caller(caller_ip)
    if not who.get("same_account"):
        raise HarnessError(403, "Only your own devices can use the harness port.")
    token = _setting(TOKEN_SETTING).strip()
    if not token:
        raise HarnessError(503, "The harness port is not configured on this Xod yet.")
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    presented = (hdrs.get("x-harness-token") or "").strip()
    canary = _setting(CANARY_SETTING).strip()
    if canary and presented and hmac.compare_digest(presented, canary):
        _log_security_event("canary_token_presented", caller_ip, who)
        raise HarnessError(401, "That token is not accepted here.")
    if not presented or not hmac.compare_digest(presented, token):
        raise HarnessError(401, "A valid harness token is required.")
    return who


# ------------------------------------------------------------------ members

def members(caller_ip: str, headers: Optional[Dict[str, Any]]) -> Tuple[int, Dict[str, Any]]:
    """Where work can go: the family registry's active members."""
    _authorize(caller_ip, headers)
    view = _members_view()
    out = [{k: m.get(k) for k in ("id", "name", "platform", "endpoint", "capabilities",
                                  "last_seen", "status")} for m in view["members"]]
    return 200, {"members": out, "registry_state": view["state"],
                 "interim_unsigned": view["interim_unsigned"], "problems": view["problems"]}


def _find_member(target: str) -> Optional[Dict[str, Any]]:
    """A registry member by id (then by name), active only; None when there is no such member."""
    view = _members_view()
    want = target.strip().lower()
    for m in view["members"]:
        if m["id"].lower() == want:
            return m
    for m in view["members"]:
        if m["name"].lower() == want:
            return m
    return None


def _is_self(member: Dict[str, Any]) -> bool:
    own = _own_ip()
    return bool(own) and member.get("endpoint") == own


# ------------------------------------------------------------------ submit

def submit(caller_ip: str, headers: Optional[Dict[str, Any]],
           body: Optional[Dict[str, Any]]) -> Tuple[int, Dict[str, Any]]:
    """{request, target, kind} in; the harness record out. See the module docstring."""
    who = _authorize(caller_ip, headers)
    body = body if isinstance(body, dict) else {}
    request = str(body.get("request") or "").strip()
    if not request:
        raise HarnessError(400, "No task given.")
    if len(request) > MAX_REQUEST:
        raise HarnessError(400, f"The task is over {MAX_REQUEST} characters.")
    kind = str(body.get("kind") or "task").strip().lower()
    if kind not in ("task", "mission"):
        raise HarnessError(400, f"Unknown kind {kind!r}: task or mission.")
    target = str(body.get("target") or "self").strip() or "self"
    member = None
    if target != "self":
        member = _find_member(target)
        if member is None:
            raise HarnessError(404, f"No family member called {target!r} in the registry.")
        if _is_self(member):
            member = None
    caller = who.get("host") or caller_ip
    now = time.time()
    rec: Dict[str, Any] = {"ref": "h-" + uuid.uuid4().hex[:12], "kind": kind,
                           "target": "self" if member is None else member["id"],
                           "request": request, "caller": caller, "created_at": now,
                           "updated_at": now, "state": "submitting", "beacon_ref": None,
                           "remote": None, "title": None, "claimed_result": None,
                           "verified_status": "not_checked", "evidence": [], "report": None,
                           "error": None}
    if member is None:
        return _submit_self(caller_ip, caller, rec)
    return _submit_relay(rec, member)


def _submit_self(caller_ip: str, caller: str, rec: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Through the beacon's own handoff, so the approval card decides. Nothing bypasses it."""
    from app.services import xod_network
    try:
        r = _receive(caller_ip, {"request": rec["request"], "from": caller, "kind": rec["kind"]})
    except xod_network.NetworkError as e:
        raise HarnessError(403, str(e))
    except ValueError as e:
        raise HarnessError(400, str(e))
    rec["beacon_ref"] = r.get("ref")
    rec["state"] = r.get("status") or "waiting_for_ok"
    rec["updated_at"] = time.time()
    with _lock:
        _load()[rec["ref"]] = rec
        _save()
    return 200, _view(rec)


def _submit_relay(rec: Dict[str, Any], member: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """To the member's own beacon, where its card decides. Unreachable is a state, not a failure
    of this call: the record carries it honestly and the caller sees 202."""
    rec["state"] = "relaying"
    rec["remote"] = {"member": member["id"], "name": member.get("name") or member["id"],
                    "endpoint": member.get("endpoint"), "remote_ref": None, "reachable": None}
    if rec["kind"] == "mission":
        rec["remote"]["kind_note"] = ("the member's beacon takes tasks; the mission kind applies "
                                      "once the member runs the harness build")
    with _lock:
        _load()[rec["ref"]] = rec
        _save()
    if not member.get("endpoint"):
        rec["state"] = "queued_for_relay"
        rec["remote"]["reachable"] = False
        rec["error"] = "That member has no tailnet endpoint in the registry, so there is nowhere to relay to yet."
        rec["updated_at"] = time.time()
        with _lock:
            _save()
        return 202, _view(rec)
    try:
        r = _relay("POST", member["endpoint"], "/v1/tasks",
                   {"request": rec["request"], "from": _own_name() or "harness"})
    except RelayError as e:
        rec["state"] = "relay_unreachable"
        rec["remote"]["reachable"] = False
        rec["error"] = str(e)
        rec["updated_at"] = time.time()
        with _lock:
            _save()
        return 202, _view(rec)
    rec["remote"]["remote_ref"] = r.get("ref")
    rec["remote"]["reachable"] = True
    rec["state"] = r.get("status") or "relayed"
    rec["updated_at"] = time.time()
    with _lock:
        _save()
    return 200, _view(rec)


# ------------------------------------------------------------------ status

def task_status(caller_ip: str, headers: Optional[Dict[str, Any]],
                ref: str) -> Tuple[int, Dict[str, Any]]:
    """One record, refreshed from its source: this Xod's handoff for self work, the member's
    beacon for relayed work. Only the caller that submitted it can read it."""
    who = _authorize(caller_ip, headers)
    with _lock:
        rec = _load().get(ref)
    if not rec or rec.get("caller") not in (who.get("host") or caller_ip, caller_ip):
        raise HarnessError(404, "No harness task like that.")
    rec = dict(rec)
    if rec.get("beacon_ref"):
        _refresh_self(caller_ip, rec)
    elif rec.get("remote"):
        _refresh_relay(rec)
    rec["updated_at"] = time.time()
    with _lock:
        _load()[rec["ref"]] = rec
        _save()
    return 200, _view(rec)


def _refresh_self(caller_ip: str, rec: Dict[str, Any]) -> None:
    from app.services import xod_network
    try:
        t = _read(caller_ip, rec["beacon_ref"])
    except KeyError:
        rec["error"] = ("This Xod no longer holds the handoff record (it may have restarted); "
                        "the submission cannot be read back from the beacon.")
        return
    except xod_network.NetworkError as e:
        raise HarnessError(403, str(e))
    rec["error"] = None
    rec["state"] = t.get("status") or rec["state"]
    if t.get("title"):
        rec["title"] = t["title"]
    if t.get("report") is not None:
        rec["report"] = t["report"]
        _merge_report(rec, t["report"])
    elif t.get("result") is not None:
        rec["claimed_result"] = t["result"]


def _merge_report(rec: Dict[str, Any], report: Dict[str, Any]) -> None:
    """A finished mission's report, in its own style: claims stay per step in the report; the
    top level carries the aggregate verified_status and the flattened evidence."""
    totals = report.get("totals") or {}
    if totals.get("failed"):
        rec["verified_status"] = "failed"
    elif totals.get("done") and totals.get("verified") == totals.get("done"):
        rec["verified_status"] = "verified"
    elif totals.get("done"):
        rec["verified_status"] = "partially_verified"
    else:
        rec["verified_status"] = "unverifiable"
    evidence: list = []
    for row in report.get("subtasks") or []:
        evidence.extend(row.get("evidence") or [])
    rec["evidence"] = evidence


def _refresh_relay(rec: Dict[str, Any]) -> None:
    remote = rec.get("remote") or {}
    if not remote.get("remote_ref") or not remote.get("endpoint"):
        return
    try:
        t = _relay("GET", remote["endpoint"], f"/v1/tasks/{remote['remote_ref']}")
    except RelayError as e:
        remote["reachable"] = False
        rec["error"] = f"The member is unreachable while checking: {e}"
        return
    remote["reachable"] = True
    rec["error"] = None
    rec["state"] = t.get("status") or rec["state"]
    if t.get("title"):
        rec["title"] = t["title"]
    if t.get("result") is not None:
        rec["claimed_result"] = t["result"]


# ------------------------------------------------------------------ tool surface
# The harness generalized (agent-first design, section 1): beyond handing over tasks, an
# attached Muse can call a curated set of this Xod's own capabilities as tools and get
# structured results back: what was claimed, what was verified, and the evidence, kept
# apart the same way task records keep them apart. Reads answer with what they read, so
# the read itself is the verification. Writes keep the house approval semantics exactly:
# task_submit goes through the beacon handoff and its card; a crew job cards its own
# approvals as it works; browser navigation is the endorsed local-browser operation, and
# anything consequential inside a page (a submit, a send, a purchase) is NOT offered on
# this surface at all: it goes through task_submit, where the card decides, like every
# other consequential action. Errors are machine-readable states, never dressed-up prose.

import asyncio

SURFACE_VERSION = 1

TOOL_SURFACE = [
    {"name": "config_read", "version": 1, "kind": "read",
     "description": "This Xod's version and model configuration: provider, model, base URL, whether Auto picks the model, and which provider keys are present (presence only: never a key, never a masked key).",
     "args": {}},
    {"name": "family_read", "version": 1, "kind": "read",
     "description": "The family registry's active members, their endpoints and advertised capabilities, and the registry's interim state.",
     "args": {}},
    {"name": "usage_read", "version": 1, "kind": "read",
     "description": "The usage ledger summary: calls, tokens and estimated cost by model and by key profile. Written by the executor, not the agent.",
     "args": {"range": "today | 7d | all (default today)"}},
    {"name": "locate", "version": 1, "kind": "read",
     "description": "Find files or folders from a plain description. Candidates are existence-verified and carry their evidence; an honest not_found lists what was tried.",
     "args": {"request": "what to find, in plain words"}},
    {"name": "task_submit", "version": 1, "kind": "write",
     "description": "Hand this Xod (or a family member) a task or a mission. It becomes an approval card there; nothing runs until the person approves. Returns the harness record reference.",
     "args": {"request": "the work, in plain words", "target": "self or a member id (default self)",
              "kind": "task | mission (default task)"}},
    {"name": "task_status", "version": 1, "kind": "read",
     "description": "Read back a task_submit record: state, claimed result, verified status and evidence, kept apart.",
     "args": {"ref": "the h-... reference from task_submit"}},
    {"name": "crew_start", "version": 1, "kind": "write",
     "description": "Start a Crew job (Claude Code or Muse) with a brief, in a folder. The job asks for its own approvals as it works. kind board seats the Claude participant as a board member: the board model pin applies and the job records the seat.",
     "args": {"task": "the brief", "folder": "folder to work in (optional)",
              "agent": "claude | muse (optional)", "kind": "board for a board seat (optional)"}},
    {"name": "crew_status", "version": 1, "kind": "read",
     "description": "Read back a Crew job: status, answer, usage, and for board jobs the seat record (model requested, model that actually ran, and how that is known).",
     "args": {"job": "the crew job id from crew_start"}},
    {"name": "browser_open", "version": 1, "kind": "write",
     "description": "Open a page in this Xod's own local browser and read back where it landed (the verified URL and title). In-page actions that change anything are not offered here; use task_submit so the card decides.",
     "args": {"url": "the page to open"}},
    {"name": "browser_state", "version": 1, "kind": "read",
     "description": "Where the local browser is now: running or not, the current URL and title, and a short read of the page text.",
     "args": {}},
]

_TOOL_NAMES = {t["name"] for t in TOOL_SURFACE}


def manifest(caller_ip: str, headers: Optional[Dict[str, Any]]) -> Tuple[int, Dict[str, Any]]:
    """What this Xod offers an attached agent: the tool surface, versioned, plus the
    endpoints beside it. Same two locks as every harness call."""
    _authorize(caller_ip, headers)
    return 200, {"surface": "xod-harness-tools", "version": SURFACE_VERSION,
                 "tools": TOOL_SURFACE,
                 "endpoints": {"manifest": "GET /v1/harness/manifest",
                               "invoke": "POST /v1/harness/invoke",
                               "submit": "POST /v1/harness/tasks",
                               "status": "GET /v1/harness/tasks/{ref}",
                               "members": "GET /v1/harness/members"},
                 "how_to_read": HOW_TO_READ}


# ------------------------------------------------------------- tool seams
# Module-level so suites can stand in fakes, like the relay seam above.

def _tool_config() -> Dict[str, Any]:
    from app.config import settings
    from app.services import auto_model
    from app.version import VERSION
    keys = {"openai": settings.openai_api_key, "anthropic": settings.anthropic_api_key,
            "gemini": settings.gemini_api_key, "groq": settings.groq_api_key,
            "openrouter": settings.openrouter_api_key, "fireworks": settings.fireworks_api_key,
            "tavily": settings.tavily_api_key, "huggingface": settings.huggingface_token,
            "trustmrr": settings.trustmrr_api_key, "you": settings.you_api_key,
            "youtube": settings.youtube_api_key}
    return {"version": VERSION, "llm_provider": settings.llm_provider,
            "llm_base_url": settings.llm_base_url, "llm_model": settings.llm_model,
            "llm_auto_model": bool(auto_model.auto_enabled()),
            "keys_present": {k: bool(v) for k, v in keys.items()}}


def _tool_usage(rng: str) -> Dict[str, Any]:
    from app.services import usage_ledger
    return usage_ledger.summary(rng)


def _locate_impl(request: str) -> Dict[str, Any]:
    from app.services import locate as locate_mod
    return locate_mod.locate(request)


def _browser_service():
    from app.services.browser_sandbox import browser_sandbox_service
    return browser_sandbox_service


def _envelope(tool: str, state: str, result: Any = None, claimed: Any = None,
              verified: Any = None, evidence: Optional[list] = None,
              error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"tool": tool, "surface_version": SURFACE_VERSION, "state": state,
            "claimed": claimed, "verified": verified, "evidence": evidence or [],
            "result": result, "error": error}


async def invoke(caller_ip: str, headers: Optional[Dict[str, Any]],
                 body: Optional[Dict[str, Any]]) -> Tuple[int, Dict[str, Any]]:
    """{tool, args} in; a structured envelope out. Both locks first, on every call.
    Request-shape problems raise HarnessError (unknown tool 404, bad args 400, the locks
    as usual); a tool that itself fails answers state "error" with a machine-readable
    code, because the call was fine and the tool is what is broken."""
    _authorize(caller_ip, headers)
    body = body if isinstance(body, dict) else {}
    tool = str(body.get("tool") or "").strip()
    args = body.get("args") if isinstance(body.get("args"), dict) else {}
    if tool not in _TOOL_NAMES:
        raise HarnessError(404, f"Unknown tool {tool!r}. GET /v1/harness/manifest lists the surface.")
    try:
        if tool == "config_read":
            out = await asyncio.to_thread(_tool_config)
            return 200, _envelope(tool, "ok", result=out, verified=out)
        if tool == "family_read":
            _, view = members(caller_ip, headers)
            return 200, _envelope(tool, "ok", result=view, verified=view)
        if tool == "usage_read":
            rng = str(args.get("range") or "today").strip()
            from app.services import usage_ledger
            if rng not in usage_ledger.RANGES:
                raise HarnessError(400, f"range must be one of: {', '.join(sorted(usage_ledger.RANGES))}.")
            out = await asyncio.to_thread(_tool_usage, rng)
            return 200, _envelope(tool, "ok", result=out, verified=out)
        if tool == "locate":
            request = str(args.get("request") or "").strip()
            if not request:
                raise HarnessError(400, "No request given: say what to find.")
            out = await asyncio.to_thread(_locate_impl, request)
            slim = {k: out.get(k) for k in ("status", "candidates", "tried", "diagnostics")}
            return 200, _envelope(tool, "ok", result=slim, verified=slim.get("candidates"))
        if tool == "task_submit":
            code, rec = await asyncio.to_thread(submit, caller_ip, headers, args)
            return code, _envelope(tool, rec.get("state") or "submitted", result=rec,
                                   verified={"verified_status": rec.get("verified_status")},
                                   evidence=rec.get("evidence") or [])
        if tool == "task_status":
            ref = str(args.get("ref") or "").strip()
            if not ref:
                raise HarnessError(400, "No ref given.")
            _, rec = await asyncio.to_thread(task_status, caller_ip, headers, ref)
            return 200, _envelope(tool, rec.get("state") or "unknown", result=rec,
                                  claimed=rec.get("claimed_result"),
                                  verified={"verified_status": rec.get("verified_status")},
                                  evidence=rec.get("evidence") or [])
        if tool == "crew_start":
            from app.services import crew
            task = str(args.get("task") or "").strip()
            if not task:
                raise HarnessError(400, "No task given: the brief is the task.")
            try:
                view = await crew.start(task, folder=str(args.get("folder") or ""),
                                        agent=str(args.get("agent") or ""),
                                        kind=str(args.get("kind") or ""))
            except crew.CrewError as e:
                raise HarnessError(400, str(e))
            return 200, _envelope(tool, "ok", result=view)
        if tool == "crew_status":
            from app.services import crew
            jid = str(args.get("job") or "").strip()
            if not jid:
                raise HarnessError(400, "No job given.")
            try:
                view = crew._view(crew.job(jid))
            except crew.CrewError as e:
                raise HarnessError(404, str(e))
            return 200, _envelope(tool, "ok", result=view,
                                  verified={"status": view.get("status")})
        if tool == "browser_open":
            url = str(args.get("url") or "").strip()
            if not url:
                raise HarnessError(400, "No url given.")
            svc = _browser_service()
            res = await svc.navigate(url)
            landed = {"url": svc.current_url, "title": svc.current_title}
            if isinstance(res, dict) and res.get("success") is False:
                return 200, _envelope(tool, "error", claimed=url, verified=landed,
                                      error={"code": "navigation_failed",
                                             "message": str(res.get("message") or res.get("error") or "the browser could not open it")[:300]})
            return 200, _envelope(tool, "ok", claimed=url, verified=landed, result=landed)
        if tool == "browser_state":
            svc = _browser_service()
            out = {"running": bool(getattr(svc, "proc", None)),
                   "url": svc.current_url, "title": svc.current_title, "text": ""}
            if out["running"]:
                try:
                    page = await svc.extract_content(max_length=1500)
                    out["text"] = str(page.get("text") or "")[:1500] if isinstance(page, dict) else str(page)[:1500]
                except Exception:
                    out["text"] = ""
            return 200, _envelope(tool, "ok", result=out, verified=out)
    except HarnessError:
        raise
    except Exception as e:
        return 200, _envelope(tool, "error",
                              error={"code": "tool_failed", "message": str(e)[:300]})
    raise HarnessError(404, f"Unknown tool {tool!r}.")
