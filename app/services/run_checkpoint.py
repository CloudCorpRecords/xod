"""Private local checkpoint files, atomic writes and cross-process run leases."""
import json
import os
from pathlib import Path
import re
import tempfile
import time
from contextlib import contextmanager
from app.paths import DATA_DIR

ROOT = Path(DATA_DIR) / "run_checkpoints"
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_RUN_BYTES = 64 * 1024 * 1024


def folder(run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid checkpoint identifier")
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ROOT, 0o700)
    root = ROOT / run_id
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return root


def write(run_id, name, data):
    if Path(name).name != name:
        raise ValueError("Invalid checkpoint filename")
    root = folder(run_id)
    raw = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    if len(raw) > MAX_FILE_BYTES:
        raise OSError("Checkpoint exceeds its 12 MiB limit")
    target = root / name
    used = sum(p.stat().st_size for p in root.iterdir() if p.is_file() and p != target)
    if used + len(raw) > MAX_RUN_BYTES:
        raise OSError("Checkpoint archive quota reached (64 MiB per run)")
    fd, temporary = tempfile.mkstemp(dir=root, prefix=".pending-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return str(target)


def load(run_id):
    target = folder(run_id) / "checkpoint.json"
    if target.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Checkpoint is too large")
    data = json.loads(target.read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("id") != run_id:
        raise ValueError("Unsupported checkpoint format")
    return data


@contextmanager
def lease(run_id):
    """OS releases the lock on crash; the checkpoint's pending intents remain."""
    handle = (folder(run_id) / "lease.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            handle.write(b"1")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise ValueError("This checkpoint already has an active owner")
    try:
        yield
    finally:
        handle.close()


def recent():
    if not ROOT.exists():
        return []
    rows = []
    for path in ROOT.glob("*/checkpoint.json"):
        try:
            data = load(path.parent.name)
            rows.append({k: data.get(k) for k in
                         ("id", "provider", "model", "status", "updated_at", "steps", "requests", "compactions", "reason")})
        except (OSError, ValueError):
            continue
    return sorted(rows, key=lambda r: r.get("updated_at") or 0, reverse=True)[:10]
