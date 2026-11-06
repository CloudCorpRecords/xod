"""
The family's shared rules: one rulebook the owner sets once and both of
his agents follow (this Xod, and Tree, the family's Muse agent).

The canonical file is family-rules.json in the rdrive family shared
folder, next to tree-inbox/ (spec: SHARED-RULES-STORE-SPEC-2026-11-06 in
the xod-eval notes). A rule the owner states to either agent lands in
that file with its source recorded, and the other agent picks it up on
its next read. Rules carry a scope (tree, xod, both) and a kind
(constraint, preference, respond, do_not_respond, fact); the respond and
do_not_respond kinds are the owner's response-discretion rules, the ones
that say what gets answered and what gets left alone.

This module is the Xod side:

  rules_path()       where the file is on this computer, or None
  load_rules()       the parsed file plus a state: ok, missing, invalid.
                     Never raises; a missing or broken rules file means
                     "fall back to built-in defaults", never a block.
  manifest_section() the xod and both scoped rules, for the capability
                     manifest (services/capability_manifest.py)
  summary_block()    those rules in plain words, for a session's system
                     context, next to the capability facts
  append_rule()      the write path for a rule the owner states in a Xod
                     chat: atomic write, supersede by id, re-read and
                     return the stored entry. Reports ok False with a
                     state when the shared space is not reachable,
                     instead of claiming a save that did not happen.

File resolution, first hit wins (the tree_inbox ladder):
  1. env XOD_FAMILY_RULES_PATH
  2. setting family_rules_path
  3. the rdrive server space on this machine
     (~/Documents/rdrive/data/files/<account>/family-rules.json)
  4. the family shared space the registry lives in
     (<space root>/family-rules.json, sibling of family/)
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("family_rules")

SCOPES = ("tree", "xod", "both")
KINDS = ("constraint", "preference", "respond", "do_not_respond", "fact")
XOD_SCOPES = ("xod", "both")
STALE_AFTER = 7 * 24 * 3600        # seconds; older than this, flag it in summaries


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _candidate_paths() -> List[Path]:
    """Ordered candidate locations for family-rules.json on this machine.
    Configured locations come first and are returned even when absent (a
    configured path is an explicit statement of where the file belongs);
    discovered locations are returned only when their space exists."""
    out: List[Path] = []
    env = os.environ.get("XOD_FAMILY_RULES_PATH", "").strip()
    if env:
        out.append(Path(env).expanduser())
        return out
    try:
        from app.services.storage import get_setting
        saved = (get_setting("family_rules_path", "") or "").strip()
    except Exception:
        saved = ""
    if saved:
        out.append(Path(saved).expanduser())
        return out
    files_root = Path.home() / "Documents" / "rdrive" / "data" / "files"
    if files_root.is_dir():
        for account in sorted(files_root.iterdir()):
            if account.is_dir():
                out.append(account / "family-rules.json")
    try:
        from app.services import family_registry
        space_root = family_registry.registry_path().parent.parent
        if space_root.is_dir():
            out.append(space_root / "family-rules.json")
    except Exception:
        pass
    return out


def rules_path() -> Optional[Path]:
    """The rules file on this computer, or None when no copy is present."""
    for cand in _candidate_paths():
        if cand.is_file():
            return cand
    return None


def _empty(state: str, path: Optional[Path]) -> Dict[str, Any]:
    return {"state": state, "path": str(path) if path else None,
            "updated_at": None, "stale": False, "rules": [], "history": []}


def load_rules(path: Optional[Path] = None) -> Dict[str, Any]:
    """Parse the rules file. Never raises. State is one of:
    ok (rules apply), missing (no file; use built-in defaults),
    invalid (a file exists but does not parse; treated as missing)."""
    p = path if path is not None else rules_path()
    if p is None:
        return _empty("missing", None)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        rules = doc.get("rules")
        if not isinstance(rules, list):
            raise ValueError("rules is not a list")
    except Exception as e:
        logger.warning(f"family rules at {p} unusable: {e}")
        return _empty("invalid", p)
    clean = [r for r in rules if isinstance(r, dict) and r.get("text")]
    updated = doc.get("updated_at")
    ts = _parse_ts(updated)
    stale = bool(ts and time.time() - ts > STALE_AFTER)
    return {"state": "ok", "path": str(p), "updated_at": updated,
            "stale": stale, "rules": clean,
            "history": doc.get("history") if isinstance(doc.get("history"), list) else []}


_cache: Dict[str, Any] = {"at": 0.0, "loaded": None}


def cached_rules(max_age: float = 60) -> Dict[str, Any]:
    if _cache["loaded"] is None or time.time() - _cache["at"] > max_age:
        _cache.update(loaded=load_rules(), at=time.time())
    return _cache["loaded"]


def for_xod(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The rules that bind this Xod: scoped xod or both. Tree-scoped rules
    are not Xod's instructions and are never shown to it."""
    return [r for r in rules if r.get("scope", "both") in XOD_SCOPES]


def manifest_section(loaded: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The rules section of the capability manifest: state plus the rules
    this Xod follows, presence and text only (rules are the owner's own
    words to his agents, never secrets)."""
    doc = loaded if loaded is not None else cached_rules()
    mine = for_xod(doc["rules"])
    return {"state": doc["state"], "path": doc["path"],
            "updated_at": doc["updated_at"], "stale": doc["stale"],
            "total": len(doc["rules"]),
            "rules": [{"id": r.get("id"), "kind": r.get("kind") or "preference",
                       "text": r.get("text"), "scope": r.get("scope") or "both",
                       "version": r.get("version") or 1}
                      for r in mine]}


def summary_block(section: Optional[Dict[str, Any]] = None) -> str:
    """The rules in plain words for a session's system context. Empty when
    no rules file is set up here, so the manifest reads exactly as before.
    Response-discretion rules get their own subheading: they decide what
    this Xod answers and what it leaves alone."""
    sec = section if section is not None else manifest_section()
    if sec["state"] != "ok" or not sec["rules"]:
        return ""
    standing = [r for r in sec["rules"] if r["kind"] not in ("respond", "do_not_respond")]
    discretion = [r for r in sec["rules"] if r["kind"] in ("respond", "do_not_respond")]
    lines = ["FAMILY RULES FOR THIS HOUSEHOLD (set by the owner, shared with his other agent; follow them):"]
    lines += [f"- {r['text']}" for r in standing]
    if discretion:
        lines.append("RESPONSE DISCRETION (what to answer, what to leave alone):")
        for r in discretion:
            tag = "ANSWER" if r["kind"] == "respond" else "LEAVE ALONE"
            lines.append(f"- [{tag}] {r['text']}")
    if sec["stale"]:
        lines.append("(These rules may be stale; the shared file has not updated in over a week.)")
    return "\n".join(lines)


def _slug(text: str) -> str:
    words = "".join(c if c.isalnum() else " " for c in text.lower()).split()
    return "-".join(words[:6]) or "rule"


def append_rule(text: str, scope: str = "both", kind: str = "preference",
                stated_by: str = "owner", via: str = "xod-chat",
                rule_id: Optional[str] = None) -> Dict[str, Any]:
    """Record a rule the owner stated, in the canonical file. Atomic
    (write tmp, rename), then re-read: the returned entry is the stored
    one. A rule whose id matches a live rule supersedes it: the old
    version moves to history untouched, the new one carries version + 1.
    Returns {"ok": False, "state": ...} when no writable shared location
    exists on this computer, so the caller can say so honestly."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "state": "empty"}
    if scope not in SCOPES:
        scope = "both"
    if kind not in KINDS:
        kind = "preference"
    target = rules_path()
    if target is None:
        for cand in _candidate_paths():
            if cand.parent.is_dir():
                target = cand
                break
    if target is None:
        return {"ok": False, "state": "unreachable"}
    doc: Dict[str, Any] = {"schema": "family-rules/v1", "rules": [], "history": []}
    if target.is_file():
        try:
            parsed = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(parsed, dict) and isinstance(parsed.get("rules"), list):
                doc = parsed
        except Exception as e:
            logger.warning(f"family rules at {target} unparseable on append: {e}")
            return {"ok": False, "state": "invalid"}
    rid = rule_id or _slug(text)
    now = _now_iso()
    entry = {"id": rid, "text": text, "scope": scope, "kind": kind,
             "source": {"stated_by": stated_by, "via": via,
                        "at": now[:10]},
             "version": 1, "supersedes": None, "updated_at": now}
    rules = doc["rules"]
    for i, old in enumerate(rules):
        if isinstance(old, dict) and old.get("id") == rid:
            entry["version"] = int(old.get("version") or 1) + 1
            entry["supersedes"] = {"id": rid, "version": int(old.get("version") or 1)}
            doc.setdefault("history", []).append(old)
            rules[i] = entry
            break
    else:
        rules.append(entry)
    doc["updated_at"] = now
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
    except Exception as e:
        logger.warning(f"family rules write failed at {target}: {e}")
        return {"ok": False, "state": "write_failed"}
    _cache.update(loaded=None, at=0.0)
    stored = load_rules(target)
    for r in stored["rules"]:
        if r.get("id") == rid and r.get("version") == entry["version"]:
            return {"ok": True, "state": "stored", "rule": r}
    return {"ok": False, "state": "verify_failed"}
