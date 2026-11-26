"""Personal Training Data Vault for Xod.

Collects and packages Xod's locally stored training data from xod.db
(verified teacher examples, DPO preference pairs, chat turns, and multi-step
agent/tool trajectories) into standard ML-ready JSONL bundles with automatic
PII/secret scrubbing, dataset metadata card, and SHA-256 integrity checksums.

Supports saving to a local home folder (default ~/Xod-Training-Vault) or any
connected external drive under /Volumes/*.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.paths import DATA_DIR

DEFAULT_VAULT_DIR = Path.home() / "Xod-Training-Vault"

_SYSTEM_VOLUMES = {
    "Macintosh HD",
    "Macintosh HD - Data",
    "Recovery",
    "Preboot",
    "VM",
    "Data",
    "Update",
    "xART",
    "iSCPreboot",
    "Hardware",
}

_TRAJECTORY_TASK_PREFIXES = (
    "browser", "desktop", "computer", "agent", "mcp", "research", "plan", "mission", "crew",
)

# Ordered secret & PII scrub patterns (most specific first)
_SCRUB_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Private key blocks
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    # JWT tokens
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[REDACTED_JWT]"),
    # Provider / platform API keys & tokens
    (re.compile(r"\b(?:sk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|fw_[A-Za-z0-9_-]{16,})\b"), "[REDACTED_API_KEY]"),
    # Authorization: Bearer ...
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1[REDACTED_TOKEN]"),
    # Inline secret assignments (password=..., api_key: ..., token=...)
    (re.compile(r"(?i)(\b(?:password|passwd|api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token)\b\s*[:=]\s*['\"]?)([^\s'\",;}{]{6,})"), r"\1[REDACTED_SECRET]"),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    # Phone numbers (US / international formats with separators, avoiding plain numeric IDs)
    (re.compile(r"(?<!\w)(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}(?!\w)"), "[REDACTED_PHONE]"),
    # Personal home paths (macOS, Linux, Windows)
    (re.compile(r"(/Users/)[^/\s'\"`\\]+"), r"\1[USER]"),
    (re.compile(r"(/home/)[^/\s'\"`\\]+"), r"\1[USER]"),
    (re.compile(r"(C:\\Users\\)[^\\\s'\"`/]+", re.I), r"\1[USER]"),
]


def scrub_text(text: str) -> Tuple[str, int]:
    """Redact API keys, tokens, private keys, emails, phones, and home paths.
    Returns (cleaned_text, redaction_count)."""
    if not text:
        return "", 0
    s = str(text)
    total = 0
    for pat, repl in _SCRUB_PATTERNS:
        s, n = pat.subn(repl, s)
        total += n
    return s, total


def scrub_obj(obj: Any) -> Tuple[Any, int]:
    """Recursively scrub strings inside dicts/lists. Returns (cleaned_obj, redaction_count)."""
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, list):
        out = []
        total = 0
        for item in obj:
            c, n = scrub_obj(item)
            out.append(c)
            total += n
        return out, total
    if isinstance(obj, dict):
        out = {}
        total = 0
        for k, v in obj.items():
            c, n = scrub_obj(v)
            out[k] = c
            total += n
        return out, total
    return obj, 0


def _resolve_db_path(db_path: Optional[Path] = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    env_db = os.environ.get("XOD_DATASET_DB")
    if env_db:
        return Path(env_db)
    return DATA_DIR / "xod.db"


def _connect(db_path: Optional[Path] = None) -> Optional[sqlite3.Connection]:
    p = _resolve_db_path(db_path)
    if not p.exists():
        return None
    c = sqlite3.connect(str(p), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _existing_tables(conn: sqlite3.Connection) -> set:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def _safe_get_setting(key: str, default: str = "") -> str:
    try:
        from app.services.storage import get_setting
        return str(get_setting(key, default) or default)
    except Exception:
        return default


def _safe_set_setting(key: str, value: str) -> None:
    try:
        from app.services.storage import set_setting
        set_setting(key, value)
    except Exception:
        pass


def list_destinations() -> List[Dict[str, Any]]:
    """List recommended export destinations: home vault, saved custom dir, and connected external drives."""
    dests: List[Dict[str, Any]] = []
    seen: set = set()

    def _add(label: str, path: Path, kind: str, mount_root: Optional[Path] = None) -> None:
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        free_gb: Optional[float] = None
        try:
            probe = mount_root or (path if path.exists() else path.parent)
            if probe.exists():
                usage = shutil.disk_usage(str(probe))
                free_gb = round(usage.free / (1024 ** 3), 1)
        except Exception:
            pass
        dests.append({
            "label": label,
            "path": key,
            "kind": kind,
            "free_gb": free_gb,
        })

    saved = _safe_get_setting("training_vault_dir", "").strip()
    if saved:
        _add("Saved Vault Folder", Path(saved).expanduser(), "custom")

    _add("Home Folder (~/Xod-Training-Vault)", DEFAULT_VAULT_DIR, "local")

    # Detect mounted external drives on macOS
    if sys.platform == "darwin":
        vols = Path("/Volumes")
        if vols.exists() and vols.is_dir():
            try:
                for entry in sorted(vols.iterdir(), key=lambda p: p.name.lower()):
                    name = entry.name
                    if name.startswith(".") or name in _SYSTEM_VOLUMES or name.startswith("com.apple."):
                        continue
                    try:
                        if entry.is_symlink() and os.path.realpath(str(entry)) == "/":
                            continue
                        if not entry.is_dir() or not os.access(str(entry), os.W_OK):
                            continue
                    except Exception:
                        continue
                    _add(f"External Drive: {name}", entry / "Xod-Training-Vault", "external", mount_root=entry)
            except Exception:
                pass

    return dests


def counts(db_path: Optional[Path] = None) -> Dict[str, int]:
    """Fast read-only counts of exportable training records in xod.db."""
    out = {
        "sft_examples": 0,
        "chat_sessions": 0,
        "sft_chat": 0,
        "dpo_preferences": 0,
        "agent_trajectories": 0,
        "total_records": 0,
    }
    conn = _connect(db_path)
    if conn is None:
        return out
    try:
        tables = _existing_tables(conn)
        if "llm_examples" in tables:
            row = conn.execute("SELECT COUNT(*) AS n FROM llm_examples WHERE label = 1").fetchone()
            out["sft_examples"] = int(row["n"] or 0) if row else 0
            traj_rows = conn.execute(
                "SELECT task, COUNT(*) AS n FROM llm_examples WHERE label >= 0 GROUP BY task"
            ).fetchall()
            for r in traj_rows:
                t = str(r["task"] or "").lower()
                if any(t.startswith(p) for p in _TRAJECTORY_TASK_PREFIXES):
                    out["agent_trajectories"] += int(r["n"] or 0)

        if "preference_pairs" in tables:
            row = conn.execute("SELECT COUNT(*) AS n FROM preference_pairs").fetchone()
            out["dpo_preferences"] = int(row["n"] or 0) if row else 0

        if "sessions" in tables and "messages" in tables:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM ("
                "  SELECT session_id FROM messages "
                "  WHERE role IN ('user', 'assistant') AND TRIM(COALESCE(content, '')) != '' "
                "  GROUP BY session_id HAVING COUNT(*) >= 2"
                ")"
            ).fetchone()
            out["chat_sessions"] = int(row["n"] or 0) if row else 0
            row_t = conn.execute(
                "SELECT COUNT(DISTINCT session_id) AS n FROM messages "
                "WHERE tool_events IS NOT NULL AND TRIM(tool_events) NOT IN ('', '[]', 'null')"
            ).fetchone()
            out["agent_trajectories"] += int(row_t["n"] or 0) if row_t else 0

        out["sft_chat"] = out["sft_examples"] + out["chat_sessions"]
        out["total_records"] = out["sft_chat"] + out["dpo_preferences"] + out["agent_trajectories"]
    except Exception:
        pass
    finally:
        conn.close()
    return out


def status(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Return Training Data Vault status for Control Center and API."""
    last_raw = _safe_get_setting("training_vault_last_export", "")
    last_export: Dict[str, Any] = {}
    if last_raw:
        try:
            last_export = json.loads(last_raw)
        except Exception:
            last_export = {}
    dests = list_destinations()
    default_dir = _safe_get_setting("training_vault_dir", "").strip() or str(DEFAULT_VAULT_DIR)
    return {
        "counts": counts(db_path),
        "destinations": dests,
        "default_dir": default_dir,
        "scrub_pii_default": True,
        "last_export": last_export,
    }


def _atomic_write_text(path: Path, text: str) -> str:
    """Atomically write UTF-8 text and return its hex SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = text.encode("utf-8")
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))
    return hashlib.sha256(data).hexdigest()


def export_vault(
    dest_dir: str = "",
    scrub_pii: bool = True,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Export all training datasets into dest_dir as ML-ready JSONL + dataset card + SHA256SUMS.txt."""
    target_str = (dest_dir or "").strip() or _safe_get_setting("training_vault_dir", "").strip() or str(DEFAULT_VAULT_DIR)
    target = Path(target_str).expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": f"Could not create export directory '{target}': {e}"}

    sft_rows: List[Dict[str, Any]] = []
    dpo_rows: List[Dict[str, Any]] = []
    traj_rows: List[Dict[str, Any]] = []
    task_breakdown: Dict[str, int] = {}
    redactions = 0

    def _clean(obj: Any) -> Any:
        nonlocal redactions
        if not scrub_pii:
            return obj
        c, n = scrub_obj(obj)
        redactions += n
        return c

    conn = _connect(db_path)
    if conn is not None:
        try:
            tables = _existing_tables(conn)

            # 1. Verified +1 teacher examples -> SFT chat JSONL
            if "llm_examples" in tables:
                rows = conn.execute(
                    "SELECT id, created_at, task, group_id, model, provider, messages_json, response, label, label_reason "
                    "FROM llm_examples WHERE label = 1 ORDER BY created_at ASC"
                ).fetchall()
                for r in rows:
                    try:
                        msgs = json.loads(r["messages_json"] or "[]")
                    except Exception:
                        continue
                    if not isinstance(msgs, list) or not (r["response"] or "").strip():
                        continue
                    convo = [
                        {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")}
                        for m in msgs
                        if isinstance(m, dict)
                    ]
                    convo.append({"role": "assistant", "content": str(r["response"])})
                    task_name = str(r["task"] or "general")
                    task_breakdown[task_name] = task_breakdown.get(task_name, 0) + 1
                    sft_rows.append(_clean({
                        "messages": convo,
                        "metadata": {
                            "source": "llm_examples",
                            "id": r["id"],
                            "task": task_name,
                            "model": r["model"] or "",
                            "provider": r["provider"] or "",
                            "created_at": r["created_at"],
                        },
                    }))

                # Agent / browser / desktop / MCP trajectories grouped by group_id (or id)
                t_rows = conn.execute(
                    "SELECT id, created_at, task, group_id, model, messages_json, response, label, label_reason "
                    "FROM llm_examples WHERE label >= 0 ORDER BY created_at ASC"
                ).fetchall()
                grouped: Dict[str, Dict[str, Any]] = {}
                for r in t_rows:
                    t = str(r["task"] or "").lower()
                    if not any(t.startswith(p) for p in _TRAJECTORY_TASK_PREFIXES):
                        continue
                    gid = str(r["group_id"] or r["id"])
                    bucket = grouped.setdefault(gid, {
                        "trajectory_id": gid,
                        "source": "llm_examples",
                        "task": str(r["task"] or ""),
                        "outcome": "verified" if r["label"] == 1 else "recorded",
                        "steps": [],
                    })
                    try:
                        msgs = json.loads(r["messages_json"] or "[]")
                    except Exception:
                        msgs = []
                    bucket["steps"].append({
                        "id": r["id"],
                        "created_at": r["created_at"],
                        "model": r["model"] or "",
                        "messages": msgs,
                        "response": r["response"] or "",
                        "label": int(r["label"] or 0),
                    })
                for traj in grouped.values():
                    traj_rows.append(_clean(traj))

            # 2. DPO preference pairs -> dpo_preferences.jsonl
            if "preference_pairs" in tables:
                rows = conn.execute(
                    "SELECT id, created_at, task, prompt_json, chosen, rejected, source, note "
                    "FROM preference_pairs ORDER BY created_at ASC"
                ).fetchall()
                for r in rows:
                    try:
                        prompt = json.loads(r["prompt_json"] or "[]")
                    except Exception:
                        prompt = [{"role": "user", "content": str(r["prompt_json"] or "")}]
                    dpo_rows.append(_clean({
                        "prompt": prompt,
                        "chosen": str(r["chosen"] or ""),
                        "rejected": str(r["rejected"] or ""),
                        "metadata": {
                            "id": r["id"],
                            "task": r["task"] or "",
                            "source": r["source"] or "",
                            "note": r["note"] or "",
                            "created_at": r["created_at"],
                        },
                    }))

            # 3. Finished multi-turn chat sessions -> SFT chat + tool-use trajectories
            if "sessions" in tables and "messages" in tables:
                sessions = conn.execute(
                    "SELECT id, title, persona, created_at FROM sessions ORDER BY created_at ASC"
                ).fetchall()
                for s in sessions:
                    m_rows = conn.execute(
                        "SELECT role, content, tool_events, created_at FROM messages "
                        "WHERE session_id = ? ORDER BY created_at ASC",
                        (s["id"],),
                    ).fetchall()
                    chat_msgs = []
                    tool_steps = []
                    for m in m_rows:
                        role = str(m["role"] or "")
                        content = str(m["content"] or "").strip()
                        if role in ("user", "assistant") and content:
                            chat_msgs.append({"role": role, "content": content})
                        te_raw = (m["tool_events"] or "").strip()
                        if te_raw and te_raw not in ("[]", "null"):
                            try:
                                te = json.loads(te_raw)
                            except Exception:
                                te = te_raw
                            tool_steps.append({
                                "role": role,
                                "content": content[:1000],
                                "tool_events": te,
                                "created_at": m["created_at"],
                            })
                    if len(chat_msgs) >= 2 and any(x["role"] == "user" for x in chat_msgs) and any(x["role"] == "assistant" for x in chat_msgs):
                        task_breakdown["chat"] = task_breakdown.get("chat", 0) + 1
                        sft_rows.append(_clean({
                            "messages": chat_msgs,
                            "metadata": {
                                "source": "chat_session",
                                "session_id": s["id"],
                                "persona": s["persona"] or "general",
                                "created_at": s["created_at"],
                            },
                        }))
                    if tool_steps:
                        traj_rows.append(_clean({
                            "trajectory_id": f"chat-{s['id']}",
                            "source": "chat_tool_events",
                            "task": s["persona"] or "chat",
                            "outcome": "completed",
                            "steps": tool_steps,
                        }))
        finally:
            conn.close()

    def _to_jsonl(items: List[Dict[str, Any]]) -> str:
        if not items:
            return ""
        return "\n".join(json.dumps(x, ensure_ascii=False) for x in items) + "\n"

    sft_text = _to_jsonl(sft_rows)
    dpo_text = _to_jsonl(dpo_rows)
    traj_text = _to_jsonl(traj_rows)
    est_tokens = (len(sft_text) + len(dpo_text) + len(traj_text)) // 4

    checksums: Dict[str, str] = {}
    files_written: Dict[str, str] = {}
    for fname, body in (
        ("sft_chat.jsonl", sft_text),
        ("dpo_preferences.jsonl", dpo_text),
        ("agent_trajectories.jsonl", traj_text),
    ):
        fpath = target / fname
        checksums[fname] = _atomic_write_text(fpath, body)
        files_written[fname] = str(fpath)

    exported_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    card = {
        "schema_version": "1.0",
        "generator": "Xod Training Data Vault",
        "exported_at": exported_at,
        "scrub_pii": bool(scrub_pii),
        "redactions_count": redactions,
        "counts": {
            "sft_chat": len(sft_rows),
            "dpo_preferences": len(dpo_rows),
            "agent_trajectories": len(traj_rows),
            "total_records": len(sft_rows) + len(dpo_rows) + len(traj_rows),
        },
        "estimated_tokens": est_tokens,
        "task_breakdown": task_breakdown,
        "compatible_frameworks": ["mlx-lm", "axolotl", "openai-ft", "fireworks-ft"],
        "files": list(checksums.keys()),
    }
    card_path = target / "dataset_card.json"
    checksums["dataset_card.json"] = _atomic_write_text(card_path, json.dumps(card, indent=2, ensure_ascii=False) + "\n")
    files_written["dataset_card.json"] = str(card_path)

    sums_lines = [f"{digest}  {fname}" for fname, digest in checksums.items()]
    sums_path = target / "SHA256SUMS.txt"
    _atomic_write_text(sums_path, "\n".join(sums_lines) + "\n")
    files_written["SHA256SUMS.txt"] = str(sums_path)

    summary = {
        "ok": True,
        "dest_dir": str(target),
        "exported_at": exported_at,
        "scrub_pii": bool(scrub_pii),
        "redactions_count": redactions,
        "counts": card["counts"],
        "estimated_tokens": est_tokens,
        "files": files_written,
        "sha256": checksums,
    }

    if db_path is None:
        _safe_set_setting("training_vault_dir", str(target))
        _safe_set_setting("training_vault_last_export", json.dumps({
            "dest_dir": str(target),
            "exported_at": exported_at,
            "counts": card["counts"],
            "estimated_tokens": est_tokens,
            "redactions_count": redactions,
            "scrub_pii": bool(scrub_pii),
        }))

    return summary
