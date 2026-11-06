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

Observability (surface v2): five read tools expose this Xod's own records to the attached
agent: timeline (the append-only event log, services/event_log.py), task_read, mission_read
and crew_read (deep grading reads over the task queue, the missions store and the crew jobs)
and snapshot (the app's own view of itself). They read only what the app itself recorded; an
activity class the app does not persist is never synthesized, and the gaps are named in the
record that shipped the surface.
Repo Worker (surface v3): the open-source contribution stack ships in the box
(services/repo_worker.py, policy in services/repo_worker_policy.py). Eleven
repo_* tools run the fleet CLIs (Greenlight wave, check, friction, racecheck,
reprocheck, prosecheck, commitcheck) plus two native gates (the full-CI split
and the gate pass itself) behind this same manifest and invoke, each answer
shaped {claimed, verified, evidence}. The open path is approval-shaped and
dry-run in this phase: repo_open_plan prepares and verifies, and never opens.
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
    from app.services import event_log
    event_log.emit("harness.submitted", actor=caller, ids={"ref": rec["ref"]},
                   payload={"target": "self", "kind": rec["kind"], "state": rec["state"]})
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
        from app.services import event_log
        event_log.emit("harness.submitted", actor=rec["caller"], ids={"ref": rec["ref"]},
                       payload={"target": member["id"], "kind": rec["kind"], "state": rec["state"]})
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
        from app.services import event_log
        event_log.emit("harness.submitted", actor=rec["caller"], ids={"ref": rec["ref"]},
                       payload={"target": member["id"], "kind": rec["kind"], "state": rec["state"]})
        return 202, _view(rec)
    rec["remote"]["remote_ref"] = r.get("ref")
    rec["remote"]["reachable"] = True
    rec["state"] = r.get("status") or "relayed"
    rec["updated_at"] = time.time()
    with _lock:
        _save()
    from app.services import event_log
    event_log.emit("harness.submitted", actor=rec["caller"], ids={"ref": rec["ref"]},
                   payload={"target": member["id"], "kind": rec["kind"], "state": rec["state"]})
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
# Surface v2 adds the observability tier: timeline, task_read, mission_read, crew_read and
# snapshot, all reads over the app's own records (the event log, the task queue, the
# missions store, the crew jobs, the ledgers). Nothing on this tier writes or approves.

import asyncio

SURFACE_VERSION = 3

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
    {"name": "timeline", "version": 1, "kind": "read",
     "description": "This Xod's event log: task and mission lifecycle, tool calls, model calls, approval cards, config changes, network and Partner events, each stamped with its actor and correlation ids. Query by time window, kind prefix and correlation id; paginated by cursor in stable sequence order.",
     "args": {"since": "epoch seconds (optional)", "until": "epoch seconds (optional)",
              "kinds": "comma separated kind prefixes, e.g. task,model (optional)",
              "task_id": "only this task's events (optional)", "mission_id": "only this mission's events (optional)",
              "subtask_id": "only this subtask's events (optional)", "crew_job": "only this crew job's events (optional)",
              "actor": "only this actor's events (optional)",
              "limit": "page size, 1 to 1000 (default 200)", "cursor": "last seq from the previous page (optional)"}},
    {"name": "task_read", "version": 1, "kind": "read",
     "description": "One task's full grading record: the queue row, its mission link if any, every recorded step with its tool calls, the wedge state (stalled, quiet seconds), and the task's own timeline events.",
     "args": {"task_id": "the task id"}},
    {"name": "mission_read", "version": 1, "kind": "read",
     "description": "One mission's full grading record: the plan, every subtask with its declared effects, evidence, claimed vs verified status and its linked task's live state (waiting text, wedge clock), the final report, mission usage totals, and the mission's timeline events.",
     "args": {"mission_id": "the mission id"}},
    {"name": "crew_read", "version": 1, "kind": "read",
     "description": "One crew job's full record: every step, every approval and question in every state, the usage, the answer, and for board jobs the seat record (model requested, model that actually ran, and how that is known).",
     "args": {"job": "the crew job id from crew_start"}},
    {"name": "snapshot", "version": 1, "kind": "read",
     "description": "This Xod's own view of itself: version, configuration (key presence only), server and beacon state, queue depths by state, the oldest waiting item's age, Partner reachability as the app sees it, family members with last seen, today's usage totals, and the event log's size.",
     "args": {}},
    {"name": "social_read", "version": 1, "kind": "read",
     "description": "The social reply queue (X and LinkedIn): item counts by state and platform, and the oldest awaiting-approval item's age. Counts only; draft text never rides this surface.",
     "args": {}},
    {"name": "repo_find_work", "version": 1, "kind": "read",
     "description": "Repo Worker, stages 0 to 2: mine the live Agent Policy Index with Greenlight wave, then gate each candidate fresh (check verdict, racecheck, friction, the repo's own policy read, one open PR per repo). Returns passed, killed and deferred candidates, each with the gate evidence that decided it. An unknown gate defers; it never passes.",
     "args": {"repos": "candidate repos for the wave (default 6)", "limit": "candidates to gate (default 8)",
              "min_share": "minimum outsider merge share (optional)", "stars": "star band lo,hi (optional)",
              "clearings": "recorded firsthand-evidence clearings, one record per candidate and gate: [{repo, issue, gate (greenlight|racecheck), evidence, source, recorded_by, recorded_at}] (optional)"}},
    {"name": "repo_wave", "version": 1, "kind": "read",
     "description": "Greenlight wave: the build queue from the live Agent Policy Index. Winnable open repos, starter issues x-rayed, behavioral gates and repos where you already have an open PR excluded.",
     "args": {"repos": "candidate repos (default 6)", "min_share": "minimum outsider merge share (optional)",
              "stars": "star band lo,hi (optional)", "issues_per_repo": "starter issues per repo (optional)"}},
    {"name": "repo_check", "version": 1, "kind": "read",
     "description": "Greenlight's verdict on one issue: GO, RISKY, TAKEN, GATED or STALE, with reasons.",
     "args": {"issue_url": "the issue URL", "repo": "owner/repo (with issue, instead of issue_url)",
              "issue": "the issue number (with repo)"}},
    {"name": "repo_friction", "version": 1, "kind": "read",
     "description": "Greenlight friction for a repo: how hard an outside PR is to land, 0 to 100 (100 is easiest), with its policy and behavioral gates.",
     "args": {"repo": "owner/repo", "lite": "true for the policy-gates-only pass (optional)"}},
    {"name": "repo_racecheck", "version": 1, "kind": "read",
     "description": "The race check for one issue: open PRs fixing the same problem without ever linking it. CLEAR, SUSPECT or RACE, with the matched PRs and keywords.",
     "args": {"repo": "owner/repo", "issue": "the issue number"}},
    {"name": "repo_reprocheck", "version": 1, "kind": "write",
     "description": "The red-green proof for a fix in a local clone: the repro runs on the base ref and on the head ref; it must fail on the base and pass on the head (reprocheck).",
     "args": {"repo_dir": "the clone's folder", "base": "the base ref", "head": "the head ref",
              "cmd": "the repro command (or give runner)", "runner": "pytest | go | auto (optional)",
              "test_file": "a test file to focus on (optional)", "test_name": "a test name (optional)"}},
    {"name": "repo_prosecheck", "version": 1, "kind": "read",
     "description": "The prose gate: lint a draft PR body, commit message or reply with prosecheck. PASS or FAIL with findings; a FAIL blocks the open, and a prosecheck that cannot run reads unknown, which blocks too.",
     "args": {"kind": "pr-body | commit | comment", "text": "the draft text"}},
    {"name": "repo_commitcheck", "version": 1, "kind": "read",
     "description": "Commit history hygiene for a local clone: subject shape, sign-off, convention (commitcheck).",
     "args": {"repo_dir": "the clone's folder", "target": "what to check (optional)"}},
    {"name": "repo_ci_gate", "version": 1, "kind": "write",
     "description": "The full-CI gate (pr_full_ci_gate): run a repo's root checks on the head and on a base worktree, and split every failure into ours, base red, or unknown. It passes only when the split is green or base-red-explained.",
     "args": {"repo_dir": "the clone's folder", "base_ref": "the base ref (default origin/main)",
              "commands": "the check commands (optional; detected from the repo when omitted)"}},
    {"name": "repo_open_plan", "version": 1, "kind": "write",
     "description": "Repo Worker stages 3 to 5 as a dry run behind the approval model: re-run a candidate's gates fresh, run the prose, commit and CI gates over the prepared work when provided, and return the open plan with its evidence and what blocks it. Phase 1 never pushes and never opens.",
     "args": {"repo": "owner/repo", "issue": "the issue number", "issue_url": "the issue URL (optional)",
              "labels": "the issue's labels (optional)", "clone_dir": "the prepared clone (optional)",
              "pr_body": "the draft PR body (optional)", "commit_messages": "draft commit messages (optional)",
              "base_ref": "the base ref for the CI gate (optional)", "commands": "CI gate commands (optional)",
              "budget_remaining": "GitHub core rate-limit remainder, for the budget gate (optional)",
              "clearings": "recorded firsthand-evidence clearings for this candidate: [{gate (greenlight|racecheck), evidence, source, recorded_by, recorded_at}] (optional)"}},
    {"name": "repo_watch", "version": 1, "kind": "read",
     "description": "Repo Worker stage 6, the read side: the signed-in account's open PRs with their attention state (changes requested, failing checks, conflicts, stale marks), split into needs attention, waiting on maintainer, and quiet.",
     "args": {"limit": "open PRs to read (default 25, max 50)"}},
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


# ------------------------------------------------------------- observability (surface v2)

def _fnum(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _inum(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _tool_timeline(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import event_log
    kinds = args.get("kinds")
    if isinstance(kinds, str):
        kinds = [k.strip() for k in kinds.split(",") if k.strip()]
    return event_log.query(since=_fnum(args.get("since")), until=_fnum(args.get("until")),
                           kinds=kinds if isinstance(kinds, list) else None,
                           limit=_inum(args.get("limit"), 200) or 200,
                           cursor=_inum(args.get("cursor")),
                           task_id=str(args.get("task_id") or "") or None,
                           mission_id=str(args.get("mission_id") or "") or None,
                           subtask_id=str(args.get("subtask_id") or "") or None,
                           crew_job=str(args.get("crew_job") or "") or None,
                           actor=str(args.get("actor") or "") or None)


def _tool_task_read(task_id: str) -> Dict[str, Any]:
    from app.services import activity, agent_log, event_log, missions
    from app.services.task_queue import task_queue
    t = task_queue.get(task_id)
    if t is None:
        raise HarnessError(404, f"No task with id {task_id!r}.")
    link = None
    c = missions._conn()
    row = c.execute("SELECT mission_id, id FROM mission_subtasks WHERE task_id = ?",
                    (task_id,)).fetchone()
    c.close()
    if row:
        link = {"mission_id": row["mission_id"], "subtask_id": row["id"]}
    grouped = agent_log.steps_for(f"task:{task_id}")     # {agent: [steps]}, oldest first
    steps = []
    for ag, rows in grouped.items():
        for s in rows:
            steps.append({**s, "agent": ag})
    steps.sort(key=lambda s: s.get("ts") or 0)
    tool_calls = [{k: e.get(k) for k in ("tool", "args", "result_summary", "ok", "at", "error", "step_id")}
                  for e in activity.entries_for_origin(f"task:{task_id}")]
    last = task_queue._progress_at(t) or 0
    wedge = {"stalled": bool(t.get("stalled")), "stalled_reason": t.get("stalled_reason"),
             "last_progress_at": last or None,
             "quiet_sec": round(time.time() - last) if last else None}
    return {"task": t, "mission": link, "steps": steps, "tool_calls": tool_calls, "wedge": wedge,
            "events": event_log.query(task_id=task_id, limit=200)}


def _tool_mission_read(mission_id: str) -> Dict[str, Any]:
    from app.services import event_log, missions, usage_ledger
    from app.services.task_queue import task_queue
    m = missions.get_mission(mission_id)
    if m is None:
        raise HarnessError(404, f"No mission with id {mission_id!r}.")
    now = time.time()
    subs = []
    for s in m.get("subtasks") or []:
        row = dict(s)
        lt = task_queue.get(s["task_id"]) if s.get("task_id") else None
        if lt:
            last = task_queue._progress_at(lt) or 0
            row["linked_task"] = {"status": lt.get("status"), "waiting_kind": lt.get("waiting_kind"),
                                  "waiting_detail": lt.get("waiting_detail"),
                                  "stalled": bool(lt.get("stalled")),
                                  "stalled_reason": lt.get("stalled_reason"),
                                  "quiet_sec": round(now - last) if last else None,
                                  "now": lt.get("now")}
        else:
            row["linked_task"] = None
        subs.append(row)
    return {"mission": m, "report": missions.get_report(mission_id), "subtasks": subs,
            "usage": usage_ledger.mission_totals(mission_id),
            "events": event_log.query(mission_id=mission_id, limit=400)}


def _tool_crew_read(job_id: str) -> Dict[str, Any]:
    from app.services import crew, event_log
    try:
        j = crew.job(job_id)
    except crew.CrewError as e:
        raise HarnessError(404, str(e))
    out = {k: j.get(k) for k in ("id", "title", "agent", "kind", "status", "cwd",
                                 "started_at", "ended_at", "error", "seat", "usage", "answer")}
    out["steps"] = [{k: s.get(k) for k in ("kind", "tool", "summary")} for s in j.get("steps") or []]
    out["approvals"] = [{k: a.get(k) for k in ("id", "summary", "state", "auto", "decided_at")}
                        for a in j.get("approvals") or []]
    out["questions"] = [{k: q.get(k) for k in ("id", "question", "choices", "state", "answer", "answered_at")}
                        for q in j.get("questions") or []]
    out["events"] = event_log.query(crew_job=job_id, limit=200)
    return out


def _tool_snapshot() -> Dict[str, Any]:
    from app.services import crew as crew_mod
    from app.services import event_log, missions, partner_client, usage_ledger, xod_network
    from app.services.task_queue import task_queue
    cfg = _tool_config()
    server: Dict[str, Any] = {}
    try:
        server = json.loads((DATA_DIR / "server.json").read_text(encoding="utf-8"))
    except Exception:
        server = {}
    net = xod_network.status()
    waiting = [t for t in task_queue.list() if t.get("status") == "waiting"]
    oldest = None
    if waiting:
        now = time.time()
        w = min(waiting, key=lambda t: t.get("updated_at") or now)
        oldest = {"task_id": w["id"],
                  "age_sec": round(now - (w.get("updated_at") or now)),
                  "waiting_kind": w.get("waiting_kind")}
    mc = missions._conn()
    mcounts = {r["state"]: r["n"] for r in
               mc.execute("SELECT state, COUNT(*) AS n FROM missions GROUP BY state")}
    mc.close()
    view = _members_view()
    family = [{k: m.get(k) for k in ("id", "name", "platform", "endpoint", "last_seen", "status")}
              for m in view["members"]]
    ps = partner_client.summary() or {}
    usage = usage_ledger.summary("today")
    try:
        from app.services import social_reply
        social = social_reply.queue_summary()
    except Exception:
        social = {}
    return {"version": cfg["version"], "config": cfg,
            "server": {"url": server.get("url"), "pid": server.get("pid")},
            "network": {"signed_in": net.get("signed_in"), "ip": net.get("ip"),
                        "beacon_running": xod_network.beacon_running()},
            "queues": {"tasks": task_queue.counts(), "missions": mcounts,
                       "crew_active": len(crew_mod.jobs(active_only=True))},
            "oldest_waiting": oldest,
            "partner": {"answered": bool(ps), "trusted": ps.get("trusted"),
                        "version": ps.get("version"), "watchdog": ps.get("watchdog")},
            "family": family, "family_registry_state": view.get("state"),
            "usage_today": usage.get("totals") or {},
            "social": social,
            "event_log": event_log.stats()}


def _tool_social() -> Dict[str, Any]:
    from app.services import social_reply
    return social_reply.queue_summary()

def _tool_repo(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Repo Worker tools (services/repo_worker.py): the contribution stack
    behind this same surface, each answer shaped {claimed, verified,
    evidence} by the module itself."""
    from app.services import repo_worker
    return repo_worker.run_tool(name, args)



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
        if tool == "timeline":
            out = await asyncio.to_thread(_tool_timeline, args)
            return 200, _envelope(tool, "ok", result=out, verified={"count": out.get("count")})
        if tool == "task_read":
            tid = str(args.get("task_id") or "").strip()
            if not tid:
                raise HarnessError(400, "No task_id given.")
            out = await asyncio.to_thread(_tool_task_read, tid)
            return 200, _envelope(tool, "ok", result=out,
                                  claimed={"status": out["task"].get("status")},
                                  verified={"wedge": out["wedge"]})
        if tool == "mission_read":
            mid = str(args.get("mission_id") or "").strip()
            if not mid:
                raise HarnessError(400, "No mission_id given.")
            out = await asyncio.to_thread(_tool_mission_read, mid)
            rep = out.get("report") or {}
            return 200, _envelope(tool, "ok", result=out,
                                  claimed={"state": out["mission"].get("state")},
                                  verified={"totals": rep.get("totals")})
        if tool == "crew_read":
            jid = str(args.get("job") or "").strip()
            if not jid:
                raise HarnessError(400, "No job given.")
            out = await asyncio.to_thread(_tool_crew_read, jid)
            return 200, _envelope(tool, "ok", result=out,
                                  verified={"status": out.get("status"), "seat": out.get("seat")})
        if tool == "snapshot":
            out = await asyncio.to_thread(_tool_snapshot)
            return 200, _envelope(tool, "ok", result=out, verified=out)
        if tool == "social_read":
            out = await asyncio.to_thread(_tool_social)
            return 200, _envelope(tool, "ok", result=out, verified=out)
        if tool.startswith("repo_"):
            try:
                out = await asyncio.to_thread(_tool_repo, tool, args)
            except ValueError as e:
                raise HarnessError(400, str(e))
            verified = out.get("verified") if isinstance(out.get("verified"), dict) else {}
            if verified.get("state") == "unknown":
                return 200, _envelope(tool, "error", result=out,
                                      claimed=out.get("claimed"), verified=verified,
                                      evidence=out.get("evidence") or [],
                                      error={"code": "gate_unknown",
                                             "message": str(verified.get("reason")
                                                            or "the tool could not establish a result")[:300]})
            return 200, _envelope(tool, "ok", result=out,
                                  claimed=out.get("claimed"), verified=verified,
                                  evidence=out.get("evidence") or [])

    except HarnessError:
        raise
    except Exception as e:
        return 200, _envelope(tool, "error",
                              error={"code": "tool_failed", "message": str(e)[:300]})
    raise HarnessError(404, f"Unknown tool {tool!r}.")
