"""Instant Checkpoints & 1-Click Undo for Xod autonomous file edits.

Automatically records lightweight pre-edit file snapshots (deduplicated by SHA-256)
whenever Xod writes or edits a local file, computes a compact unified diff after the
change lands, and allows 1-click rollback (restoring exact pre-edit bytes or removing
a newly created file) from Control Center or `/api/checkpoints/{id}/undo`.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

MAX_CHECKPOINTS = 100
MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024  # 5 MB cap per snapshot blob


def _root_dir(base_dir: Optional[Path] = None) -> Path:
    return Path(base_dir) if base_dir is not None else (DATA_DIR / "checkpoints")


def _index_path(base_dir: Optional[Path] = None) -> Path:
    return _root_dir(base_dir) / "index.json"


def _blobs_dir(base_dir: Optional[Path] = None) -> Path:
    return _root_dir(base_dir) / "blobs"


def _load_index(base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = _index_path(base_dir)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_index(items: List[Dict[str, Any]], base_dir: Optional[Path] = None) -> None:
    root = _root_dir(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    p = _index_path(base_dir)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items[:MAX_CHECKPOINTS], indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(p))


def _store_blob(raw: bytes, base_dir: Optional[Path] = None) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    bdir = _blobs_dir(base_dir)
    bdir.mkdir(parents=True, exist_ok=True)
    target = bdir / digest
    if not target.exists():
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(raw)
        os.replace(str(tmp), str(target))
    return digest


def capture_before(
    filepath: str,
    tool: str = "write_file",
    proof_b64: str = "",
    base_dir: Optional[Path] = None,
) -> str:
    """Capture a lightweight pre-edit snapshot of `filepath`. Returns checkpoint_id ('' if skipped)."""
    try:
        p = Path(os.path.expanduser(str(filepath))).resolve()
        existed = p.exists() and p.is_file()
        before_hash = ""
        before_size = 0
        if existed:
            st = p.stat()
            if st.st_size > MAX_SNAPSHOT_BYTES:
                return ""
            raw = p.read_bytes()
            before_size = len(raw)
            before_hash = _store_blob(raw, base_dir=base_dir)

        cid = f"cp-{uuid.uuid4().hex[:10]}"
        entry = {
            "id": cid,
            "created_at": time.time(),
            "path": str(p),
            "name": p.name,
            "tool": tool,
            "existed": existed,
            "before_hash": before_hash,
            "before_size": before_size,
            "after_hash": "",
            "after_size": 0,
            "diff_preview": "",
            "undone": False,
            "undone_at": 0.0,
            "proof_b64": (proof_b64 or "")[:28000],
        }
        items = _load_index(base_dir)
        items.insert(0, entry)
        _save_index(items, base_dir)
        return cid
    except Exception:
        return ""


def _build_diff_preview(before_bytes: bytes, after_bytes: bytes, name: str, existed: bool) -> str:
    try:
        before_lines = before_bytes.decode("utf-8", errors="replace").splitlines() if existed else []
        after_lines = after_bytes.decode("utf-8", errors="replace").splitlines()
        diff = list(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"before/{name}" if existed else "/dev/null",
                tofile=f"after/{name}",
                n=2,
                lineterm="",
            )
        )
        if not diff:
            return "(no textual diff)"
        clipped = diff[:28]
        out = "\n".join(clipped)
        if len(diff) > 28:
            out += f"\n… (+{len(diff) - 28} more diff lines)"
        return out[:1800]
    except Exception:
        return "(binary file)"


def finalize_after(checkpoint_id: str, filepath: str, base_dir: Optional[Path] = None) -> None:
    """Attach post-edit hash and unified diff preview to a previously captured checkpoint."""
    if not checkpoint_id:
        return
    try:
        p = Path(os.path.expanduser(str(filepath))).resolve()
        if not p.exists() or not p.is_file():
            return
        after_bytes = p.read_bytes()
        after_hash = hashlib.sha256(after_bytes).hexdigest()

        items = _load_index(base_dir)
        for it in items:
            if it.get("id") == checkpoint_id:
                before_bytes = b""
                if it.get("existed") and it.get("before_hash"):
                    bpath = _blobs_dir(base_dir) / str(it["before_hash"])
                    if bpath.exists():
                        before_bytes = bpath.read_bytes()
                it["after_hash"] = after_hash
                it["after_size"] = len(after_bytes)
                it["diff_preview"] = _build_diff_preview(before_bytes, after_bytes, it.get("name") or p.name, bool(it.get("existed")))
                break
        _save_index(items, base_dir)
    except Exception:
        pass


def list_checkpoints(limit: int = 25, base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return newest checkpoints for Activity Replay & Control Center."""
    items = _load_index(base_dir)
    return items[: max(1, min(limit, MAX_CHECKPOINTS))]


def undo_checkpoint(checkpoint_id: str, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Roll back a single checkpoint: restores exact pre-edit bytes or deletes a newly created file."""
    items = _load_index(base_dir)
    target_item = next((it for it in items if it.get("id") == checkpoint_id), None)
    if not target_item:
        return {"ok": False, "error": f"Checkpoint '{checkpoint_id}' not found."}
    if target_item.get("undone"):
        return {"ok": False, "error": "This checkpoint was already undone."}

    p = Path(str(target_item.get("path") or ""))
    try:
        if target_item.get("existed"):
            bhash = str(target_item.get("before_hash") or "")
            bpath = _blobs_dir(base_dir) / bhash
            if not bhash or not bpath.exists():
                return {"ok": False, "error": "Original snapshot blob is missing."}
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".xod_undo.tmp")
            tmp.write_bytes(bpath.read_bytes())
            os.replace(str(tmp), str(p))
            action = "restored"
        else:
            if p.exists() and p.is_file():
                p.unlink()
            action = "removed_created_file"

        target_item["undone"] = True
        target_item["undone_at"] = time.time()
        _save_index(items, base_dir)
        return {
            "ok": True,
            "id": checkpoint_id,
            "path": str(p),
            "action": action,
            "message": f"Undid edit to {p.name} ({action}).",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
