"""
Verified outcomes for side-effecting tools (orchestrator scope, section 9; Phase 2).

Every side-effecting tool call gets an outcome:
    CONFIRMED  read back at the destination
    FAILED     verified not done (the destination does not hold it)
    UNKNOWN    the call broke off (timeout, dropped connection, crash) and the destination
               could not be checked: it may or may not have happened
    UNVERIFIED the tool reported success but there is no way to read it back here

The engine never blindly retries an UNKNOWN action: the same call (same origin, tool and
arguments) is first checked at the destination. If the earlier attempt landed, the call is
answered with that confirmation and nothing is repeated; if it verifiably did not, the retry
runs (once); if the destination still cannot be read, the retry is refused with the reason.

A non-idempotent action (a send) that was CONFIRMED is never repeated with the same arguments
in the same task or worker either: that would be the duplicate the rule exists to prevent.
"""
import hashlib
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger("xod.outcomes")

CONFIRMED, FAILED, UNKNOWN, UNVERIFIED = "CONFIRMED", "FAILED", "UNKNOWN", "UNVERIFIED"

SIDE_EFFECT_TOOLS = {
    "computer_write_file", "computer_edit_file", "fs_write_workspace_file", "computer_manage_file",
    "computer_batch_organize", "fs_move_file", "fs_organize_files", "fs_create_folder",
    "computer_run_command", "computer_run_applescript", "computer_kill_process", "computer_start_job",
    "agent_mail_send", "browser_interact", "browser_run_mission", "phone", "tailscale_files",
}
# Sending twice is a second message; writing the same bytes twice is the same file.
NON_IDEMPOTENT = {"agent_mail_send", "phone", "tailscale_files", "browser_run_mission", "browser_interact",
                  "computer_run_applescript", "computer_start_job"}

# tool -> fn(args) -> (outcome, evidence): read the destination now. Tests register their own.
Verifier = Callable[[Dict[str, Any]], Tuple[str, str]]
VERIFIERS: Dict[str, Verifier] = {}


def _file_verifier(args: Dict[str, Any]) -> Tuple[str, str]:
    import os
    raw = args.get("filepath") or args.get("file_path") or args.get("path") or ""
    p = Path(os.path.expanduser(str(raw)))
    content = args.get("content")
    if not raw:
        return UNKNOWN, "no path to read back"
    try:
        if not p.is_file():
            return FAILED, f"{p} is not on disk"
        if isinstance(content, str):
            wanted = {content}
            try:   # the write tool turns literal \n into newlines for some file types
                from app.agent.tools import _real_newlines
                wanted.add(_real_newlines(str(raw), content))
            except Exception:
                pass
            got = p.read_bytes().decode("utf-8", "replace")
            if got in wanted:
                return CONFIRMED, f"read back {p}: {len(got)} chars, exact content"
            if any(got.rstrip("\n") == w.rstrip("\n") for w in wanted):
                return CONFIRMED, f"read back {p}: same content apart from a trailing newline"
            return FAILED, f"{p} exists but does not hold the content that was written"
        return CONFIRMED, f"{p} is on disk"
    except OSError as e:
        return UNKNOWN, f"could not read {p} back: {e}"


VERIFIERS["computer_write_file"] = _file_verifier
VERIFIERS["fs_write_workspace_file"] = _file_verifier


# --------------------------------------------------------------------- ledger
def _conn() -> sqlite3.Connection:
    from app.services import task_queue as TQ
    c = TQ._conn()
    c.execute("""CREATE TABLE IF NOT EXISTS tool_outcomes (
        id TEXT PRIMARY KEY, origin TEXT, tool TEXT, args_hash TEXT, outcome TEXT, evidence TEXT,
        created_at REAL, resolved_at REAL, resolved_outcome TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS tool_outcomes_key ON tool_outcomes(origin, tool, args_hash)")
    return c


def args_hash(args: Dict[str, Any]) -> str:
    clean = {k: v for k, v in (args or {}).items() if not str(k).startswith("_")}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _record(origin: str, tool: str, h: str, outcome: str, evidence: str) -> None:
    c = _conn()
    c.execute("INSERT INTO tool_outcomes (id, origin, tool, args_hash, outcome, evidence, created_at) VALUES (?,?,?,?,?,?,?)",
              (uuid.uuid4().hex[:12], origin, tool, h, outcome, evidence[:500], time.time()))
    c.commit()
    c.close()
    try:
        from app.services import event_log
        kind = "outcome.unknown" if outcome == UNKNOWN else "outcome.verified" if outcome in (CONFIRMED, FAILED) else "outcome.unverified"
        event_log.emit(kind, actor="outcomes", ids=event_log.ids_from_origin(origin),
                       payload={"tool": tool, "outcome": outcome, "evidence": evidence[:200]})
    except Exception:
        pass


def _latest(origin: str, tool: str, h: str) -> Optional[Dict[str, Any]]:
    c = _conn()
    r = c.execute("SELECT * FROM tool_outcomes WHERE origin = ? AND tool = ? AND args_hash = ? "
                  "ORDER BY created_at DESC LIMIT 1", (origin, tool, h)).fetchone()
    c.close()
    return dict(r) if r else None


def _resolve(row_id: str, outcome: str) -> None:
    c = _conn()
    c.execute("UPDATE tool_outcomes SET resolved_at = ?, resolved_outcome = ? WHERE id = ?", (time.time(), outcome, row_id))
    c.commit()
    c.close()


def history(origin: str) -> list:
    c = _conn()
    rows = c.execute("SELECT * FROM tool_outcomes WHERE origin = ? ORDER BY created_at", (origin,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------- the guard around a call
def guard(origin: str, tool: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Before a side-effecting call: None lets it run; a dict answers it without running.
    This is where blind retries of UNKNOWN actions, and repeats of confirmed sends, stop."""
    if tool not in SIDE_EFFECT_TOOLS:
        return None
    h = args_hash(args)
    last = _latest(origin, tool, h)
    if not last:
        return None
    state = last.get("resolved_outcome") or last["outcome"]
    if state == UNKNOWN:
        verify = VERIFIERS.get(tool)
        if verify is None:
            return {"success": False, "outcome": UNKNOWN,
                    "error": (f"An earlier {tool} with these exact arguments broke off and its outcome is UNKNOWN. "
                              "Nothing was retried: check the destination first (it may already have happened), "
                              "then tell the person what you found.")}
        outcome, evidence = verify(args)
        _resolve(last["id"], outcome)
        _record(origin, tool, h, outcome, f"checked before retrying: {evidence}")
        if outcome == CONFIRMED:
            return {"success": True, "outcome": CONFIRMED, "already_done": True,
                    "message": f"The earlier attempt did land ({evidence}). Not repeated."}
        if outcome == FAILED:
            return None          # verifiably not done: the retry may run, once
        return {"success": False, "outcome": UNKNOWN,
                "error": f"The earlier attempt's outcome is still UNKNOWN ({evidence}). Not retried blindly."}
    if state == CONFIRMED and tool in NON_IDEMPOTENT:
        return {"success": True, "outcome": CONFIRMED, "already_done": True,
                "message": "This exact action was already done and confirmed in this task. Not repeated."}
    if state == UNVERIFIED and tool in NON_IDEMPOTENT:
        # It ran and reported success, but nothing could read it back: its effect is not known
        # to be absent, so a second run would be a blind duplicate (live L4: a background job
        # started twice). Check it instead.
        return {"success": False, "outcome": UNVERIFIED, "already_done": True,
                "error": (f"This exact {tool} already ran earlier in this task (it reported success; its effect "
                          "was not read back). Not repeated: check its status or its result instead.")}
    if state == FAILED and tool in NON_IDEMPOTENT:
        tries = sum(1 for r in history(origin) if r["tool"] == tool and r["args_hash"] == h and r["outcome"] == FAILED)
        if tries >= 2:
            return {"success": False, "outcome": FAILED,
                    "error": "This exact action already failed twice in this task. Not retried again."}
    return None


def settle(origin: str, tool: str, args: Dict[str, Any], result: Any = None,
           error: Optional[BaseException] = None) -> Dict[str, Any]:
    """After a side-effecting call (or its exception): records and returns its outcome, adding
    the outcome to the result the agent sees."""
    if tool not in SIDE_EFFECT_TOOLS:
        if error is not None:
            raise error
        return result
    h = args_hash(args)
    verify = VERIFIERS.get(tool)
    if error is not None:
        if verify:
            outcome, evidence = verify(args)
            if outcome == FAILED:
                evidence = f"the call broke off ({type(error).__name__}) and the destination does not hold it: {evidence}"
            elif outcome == CONFIRMED:
                evidence = f"the call broke off ({type(error).__name__}) but it landed: {evidence}"
        else:
            outcome, evidence = UNKNOWN, f"the call broke off ({type(error).__name__}: {error}); the destination was not read"
        _record(origin, tool, h, outcome, evidence)
        return {"success": outcome == CONFIRMED, "outcome": outcome, "evidence": evidence,
                **({} if outcome == CONFIRMED else {"error": evidence})}
    res = result if isinstance(result, dict) else {"result": result}
    ok = not res.get("error") and res.get("success", True) is not False
    if res.get("already_done"):
        return res
    if verify:
        outcome, evidence = verify(args)
        if not ok and outcome == CONFIRMED:
            evidence = "the tool reported an error, but " + evidence
    else:
        outcome, evidence = (UNVERIFIED, "the tool reported success; no read-back exists for it") if ok \
            else (FAILED, f"the tool reported it did not happen: {str(res.get('error'))[:200]}")
    _record(origin, tool, h, outcome, evidence)
    return {**res, "outcome": outcome, "outcome_evidence": evidence}
