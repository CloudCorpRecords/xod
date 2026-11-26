"""Fleet Control: unified rubber-stamp auto-approver, Cross-Chat Agent Bus,
and Shared Fleet Blackboard for multi-chat and multi-machine orchestration.

Design principles:
- Rubber-stamp parity with Muse (services/muse_review.py):
  When fleet_auto_approve is 'act' (the default), tasks/missions handed over by
  the user's own verified devices (same Tailscale account whois / Harness token)
  are stamped and auto-approved immediately without blocking on a human tap.
  Every stamp is appended to DATA_DIR/fleet_control/audit.jsonl.
- Cross-Chat Agent Bus:
  Agents and Fleet operators can list chats, read another chat's context,
  hand off work between chats (or spawn linked child chats), and steer chats.
- Shared Fleet Blackboard:
  Lightweight CRDT-merged working memory (active goals, touched branches/files,
  recent chat summaries) shared across chats and across Tailscale family Xods.
"""
import json
import logging
import os
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.fleet_control")

SETTING = "fleet_auto_approve"
SUMMARIES_SETTING = "memory_sync_chat_summaries"
MODES = ("off", "decide", "act")
POLICY = "rubberstamp"
MAX_BLACKBOARD = 60

_bb_lock = threading.RLock()


def _dir() -> Path:
    d = Path(DATA_DIR) / "fleet_control"
    d.mkdir(parents=True, exist_ok=True)
    return d


def audit_path() -> Path:
    return _dir() / "audit.jsonl"


def blackboard_path() -> Path:
    return _dir() / "blackboard.json"


def _hostname() -> str:
    return socket.gethostname().split(".")[0][:60]


# ------------------------------------------------------------------ 1. Fleet Rubber-Stamp Policy

def mode() -> str:
    """'act' (rubber-stamp auto-approve), 'decide' (audit-only pause), or 'off' (manual ask)."""
    from app.services.storage import get_setting
    v = str(get_setting(SETTING, "act") or "act").strip().lower()
    return v if v in MODES else "act"


def set_mode(new_mode: str) -> str:
    from app.services.storage import set_setting
    m = str(new_mode or "").strip().lower()
    if m not in MODES:
        raise ValueError(f"fleet_auto_approve must be one of {MODES}, got {new_mode!r}")
    set_setting(SETTING, m)
    logger.info(f"fleet auto-approve mode -> {m}")
    return m


def sync_chat_summaries_enabled() -> bool:
    from app.services.storage import get_setting
    return get_setting(SUMMARIES_SETTING, "1") != "0"


def set_sync_chat_summaries(on: bool) -> bool:
    from app.services.storage import set_setting
    set_setting(SUMMARIES_SETTING, "1" if on else "0")
    return bool(on)


def audit(record: Dict[str, Any]) -> None:
    rec = {"ts": time.time(), "host": _hostname(), **record}
    try:
        with open(audit_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception as e:
        logger.warning(f"fleet audit write failed: {e}")


def audit_count() -> int:
    p = audit_path()
    if not p.exists():
        return 0
    try:
        return sum(1 for line in p.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
    except Exception:
        return 0


def recent_audits(limit: int = 15) -> List[Dict[str, Any]]:
    p = audit_path()
    if not p.exists():
        return []
    try:
        lines = [l for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        out = []
        for l in lines[-limit:]:
            try:
                out.append(json.loads(l))
            except ValueError:
                pass
        return list(reversed(out))
    except Exception:
        return []


def evaluate_fleet_card(kind: str, ref: str, sender: str, request_preview: str) -> Dict[str, Any]:
    """Evaluate a verified same-account fleet card under the rubber-stamp policy."""
    cur = mode()
    if cur == "act":
        verdict = {
            "auto_approve": True,
            "decision": "approve",
            "policy": POLICY,
            "mode": cur,
            "reason": "rubber-stamp: verified same-account fleet work auto-approved",
        }
    elif cur == "decide":
        verdict = {
            "auto_approve": False,
            "decision": "audit_only",
            "policy": POLICY,
            "mode": cur,
            "reason": "fleet_auto_approve is in audit-only (decide) mode; waiting for manual OK",
        }
    else:
        verdict = {
            "auto_approve": False,
            "decision": "ask",
            "policy": "manual",
            "mode": cur,
            "reason": "fleet_auto_approve is off; waiting for manual OK",
        }
    audit({
        "kind": kind,
        "ref": ref,
        "sender": sender,
        "preview": (request_preview or "")[:240],
        **verdict,
    })
    return verdict


# ------------------------------------------------------------------ 2. Shared Fleet Blackboard

def _load_bb() -> List[Dict[str, Any]]:
    p = blackboard_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_bb(items: List[Dict[str, Any]]) -> None:
    items = sorted(items, key=lambda x: float(x.get("updated_at") or 0), reverse=True)[:MAX_BLACKBOARD]
    p = blackboard_path()
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, p)


def blackboard_post(topic: str, summary: str, actor: str = "xod", session_id: str = "",
                    branch: str = "", files: Optional[List[str]] = None,
                    status: str = "active", entry_id: str = "") -> Dict[str, Any]:
    """Post or update an item on the Shared Fleet Blackboard."""
    t = str(topic or "general").strip()[:80]
    s = str(summary or "").strip()[:1200]
    if not s:
        raise ValueError("Blackboard summary cannot be empty.")
    now = time.time()
    eid = entry_id or f"bb_{uuid.uuid4().hex[:10]}"
    entry = {
        "id": eid,
        "topic": t,
        "summary": s,
        "actor": str(actor or "xod")[:60],
        "origin": _hostname(),
        "session_id": str(session_id or "")[:64],
        "branch": str(branch or "")[:80],
        "files": [str(f)[:160] for f in (files or [])[:12]],
        "status": str(status or "active")[:24],
        "updated_at": now,
    }
    with _bb_lock:
        items = [x for x in _load_bb() if x.get("id") != eid]
        items.insert(0, entry)
        _save_bb(items)
    try:
        from app.services import event_log
        event_log.emit("fleet.blackboard_post", actor=entry["actor"], ids={"entry_id": eid, "session_id": entry["session_id"]},
                       payload={"topic": t, "summary": s[:200], "status": entry["status"]})
    except Exception:
        pass
    return entry


def blackboard_read(topic: str = "", limit: int = 20, include_done: bool = False) -> List[Dict[str, Any]]:
    with _bb_lock:
        items = _load_bb()
    t = str(topic or "").strip().lower()
    out = []
    for x in items:
        if not include_done and x.get("status") == "done":
            continue
        if t and t not in str(x.get("topic") or "").lower() and t not in str(x.get("summary") or "").lower():
            continue
        out.append(x)
        if len(out) >= limit:
            break
    return out


def blackboard_merge(incoming: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Idempotent newest-updated_at-wins merge for cross-machine Fleet Blackboard sync."""
    merged = 0
    with _bb_lock:
        by_id = {x["id"]: x for x in _load_bb() if isinstance(x, dict) and x.get("id")}
        for item in incoming or []:
            if not isinstance(item, dict) or not item.get("id") or not item.get("summary"):
                continue
            eid = str(item["id"])[:64]
            prev = by_id.get(eid)
            if not prev or float(item.get("updated_at") or 0) > float(prev.get("updated_at") or 0):
                by_id[eid] = {
                    "id": eid,
                    "topic": str(item.get("topic") or "general")[:80],
                    "summary": str(item.get("summary") or "")[:1200],
                    "actor": str(item.get("actor") or "fleet")[:60],
                    "origin": str(item.get("origin") or "remote")[:60],
                    "session_id": str(item.get("session_id") or "")[:64],
                    "branch": str(item.get("branch") or "")[:80],
                    "files": [str(f)[:160] for f in (item.get("files") or [])[:12]],
                    "status": str(item.get("status") or "active")[:24],
                    "updated_at": float(item.get("updated_at") or time.time()),
                }
                merged += 1
        _save_bb(list(by_id.values()))
    return {"merged": merged, "total": len(by_id)}


def recent_chat_summaries(limit: int = 8) -> List[Dict[str, Any]]:
    """Compact one-paragraph summaries of recent chats for cross-chat / cross-machine context."""
    if not sync_chat_summaries_enabled():
        return []
    from app.services import storage
    sessions = storage.list_sessions()[:limit]
    out = []
    for s in sessions:
        sid = s.get("id")
        msgs = storage.get_session_messages(sid) if sid else []
        last_user = next((m.get("content") or "" for m in reversed(msgs) if m.get("role") == "user" and (m.get("content") or "").strip()), "")
        last_asst = next((m.get("content") or "" for m in reversed(msgs) if m.get("role") == "assistant" and (m.get("content") or "").strip()), "")
        if not last_user and not last_asst:
            continue
        out.append({
            "session_id": sid,
            "title": (s.get("title") or "Chat")[:100],
            "source": s.get("source") or "desktop",
            "updated_at": s.get("updated_at"),
            "last_request": last_user.strip()[:220],
            "last_outcome": last_asst.strip()[:300],
        })
    return out


# ------------------------------------------------------------------ 3. Cross-Chat Agent Bus

def chat_list(limit: int = 25, source: str = "") -> Dict[str, Any]:
    from app.services import storage
    sessions = storage.list_sessions()
    if source:
        sessions = [s for s in sessions if (s.get("source") or "desktop").lower() == source.lower()]
    items = []
    for s in sessions[:limit]:
        sid = s["id"]
        msgs = storage.get_session_messages(sid)
        last = next((m for m in reversed(msgs) if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()), None)
        items.append({
            "id": sid,
            "title": s.get("title") or "Chat",
            "source": s.get("source") or "desktop",
            "message_count": s.get("message_count") or len(msgs),
            "updated_at": s.get("updated_at"),
            "last_role": last.get("role") if last else "",
            "last_preview": ((last.get("content") or "").strip()[:180]) if last else "",
        })
    return {"count": len(items), "chats": items}


def chat_read(session_id: str, limit: int = 20) -> Dict[str, Any]:
    from app.services import storage
    sid = str(session_id or "").strip()
    sess = storage.get_session(sid)
    if not sess:
        raise ValueError(f"Chat session {sid!r} not found.")
    msgs = [
        {"role": m.get("role"), "content": (m.get("content") or "")[:2400], "created_at": m.get("created_at")}
        for m in storage.get_session_messages(sid)
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ][-max(1, min(limit, 50)):]
    return {
        "session_id": sid,
        "title": sess.get("title") or "Chat",
        "source": sess.get("source") or "desktop",
        "message_count": len(msgs),
        "messages": msgs,
    }


def chat_handoff(summary: str, target_session_id: str = "", from_session_id: str = "",
                 next_step: str = "", artifacts: Optional[List[str]] = None,
                 create_title: str = "", actor: str = "fleet") -> Dict[str, Any]:
    """Transfer context and next steps from one chat/agent into another chat (or a new linked chat)."""
    from app.services import storage
    body_summary = str(summary or "").strip()
    if not body_summary:
        raise ValueError("Handoff summary is required.")
    tid = str(target_session_id or "").strip()
    created_new = False
    if not tid or tid.lower() == "new":
        sess = storage.create_session(title=(create_title or "Fleet Handoff")[:100], source="fleet")
        tid = sess["id"]
        created_new = True
    elif not storage.get_session(tid):
        raise ValueError(f"Target chat {tid!r} not found.")

    lines = [f"**Fleet Handoff** *(from {actor}{f' · chat {from_session_id[:8]}' if from_session_id else ''})*", "", body_summary]
    if artifacts:
        lines.append("")
        lines.append("**Artifacts:** " + ", ".join(str(a)[:160] for a in artifacts[:10]))
    if next_step:
        lines.append("")
        lines.append(f"**Next step:** {str(next_step).strip()}")
    content = "\n".join(lines)
    storage.add_message(tid, "assistant", content)

    bb = blackboard_post(
        topic=create_title or "chat-handoff",
        summary=f"Handoff to chat {tid[:8]}: {body_summary[:300]}" + (f" | Next: {next_step[:180]}" if next_step else ""),
        actor=actor,
        session_id=tid,
        files=artifacts or [],
        status="active",
    )
    try:
        from app.services import event_log
        event_log.emit("fleet.chat_handoff", actor=actor,
                       ids={"from_session_id": from_session_id, "target_session_id": tid},
                       payload={"created_new": created_new, "summary": body_summary[:240]})
    except Exception:
        pass
    return {"success": True, "target_session_id": tid, "created_new": created_new, "blackboard_id": bb["id"]}


def chat_steer(session_id: str, instruction: str, actor: str = "fleet",
               queue_task: bool = True) -> Dict[str, Any]:
    """Steer an existing chat by recording the instruction and optionally queueing an autonomous follow-up task."""
    from app.services import storage
    sid = str(session_id or "").strip()
    instr = str(instruction or "").strip()
    if not storage.get_session(sid):
        raise ValueError(f"Chat session {sid!r} not found.")
    if not instr:
        raise ValueError("Steering instruction is required.")
    storage.add_message(sid, "user", f"[Fleet steer · {actor}] {instr}")
    task_id = None
    if queue_task:
        try:
            from app.services.task_queue import task_queue
            t = task_queue.create(f"[Chat {sid[:8]}] {instr}")
            task_id = t.get("id")
            task_queue._update(task_id, note=f"Fleet steer from {actor} for chat {sid[:8]}")
        except Exception as e:
            logger.warning(f"chat_steer task queue failed: {e}")
    blackboard_post(topic="chat-steer", summary=f"Steered chat {sid[:8]}: {instr[:260]}", actor=actor, session_id=sid)
    return {"success": True, "session_id": sid, "task_id": task_id, "instruction": instr[:300]}


def status_snapshot() -> Dict[str, Any]:
    """Compact Fleet Control snapshot for Control Center and Harness."""
    from app.services import muse_app, muse_review
    bb = blackboard_read(limit=10)
    return {
        "auto_approve_mode": mode(),
        "policy": POLICY,
        "audit_count": audit_count(),
        "recent_audits": recent_audits(limit=6),
        "muse_mode": muse_app.mode(),
        "muse_review_mode": muse_review.mode(),
        "sync_chat_summaries": sync_chat_summaries_enabled(),
        "blackboard_count": len(blackboard_read(limit=MAX_BLACKBOARD)),
        "blackboard": bb,
        "recent_chats": recent_chat_summaries(limit=6),
    }
