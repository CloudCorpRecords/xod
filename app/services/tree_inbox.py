"""Xod -> Tree bridge: leave a message for Tree (Rene's Muse agent).

Rene's directive (2026-11-03): Xod must be able to message Tree directly —
he tells Xod something, or Xod finishes/needs something, and it reaches
Tree without Rene relaying it by hand.

Transport: one JSON file per message in a ``tree-inbox/`` folder inside the
family shared space (the rdrive synced space; production host is the
Razer). Tree's watcher polls that folder, delivers each new message into
the conversation, and marks it read. Writes are atomic (write ``.tmp``,
rename into place) so a partially written file is never read as a message.

Message format:
    {"id": <uuid>, "ts": <ISO-8601 UTC>, "from_device": <registry name>,
     "from_app": "xod", "kind": "note"|"task"|"result", "text": ...,
     "read": false}

Inbox folder resolution, first hit wins:
  1. env XOD_TREE_INBOX_DIR
  2. setting tree_inbox_dir
  3. the rdrive server space on this machine
     (~/Documents/rdrive/data/files/<account>/tree-inbox)
  4. the family shared space the registry lives in
     (<space root>/tree-inbox, sibling of family/)
If none of those spaces exists on this machine, the message queues in
DATA_DIR/tree-inbox-outbox/ and send_message reports state "queued"
honestly instead of claiming it was delivered. When the inbox becomes
reachable again, queued files drain into it on the next send.
"""
import json
import os
import platform
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.paths import DATA_DIR

KINDS = ("note", "task", "result")
MAX_TEXT = 20000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def outbox_dir() -> Path:
    return DATA_DIR / "tree-inbox-outbox"


def from_device() -> str:
    """This device's display name from the family registry; its registry
    id when the record has no name; the machine hostname when this device
    has no family identity yet. Never raises."""
    try:
        from app.services import family_registry
        did = family_registry.my_device_id()
    except Exception:
        did = ""
    if did:
        try:
            view = family_registry.load_registry()
            for d in view.get("devices", []):
                if d.get("id") == did and d.get("name"):
                    return str(d["name"])
        except Exception:
            pass
        return did
    return platform.node() or "unknown device"


def _configured_dir() -> Path | None:
    """The explicitly configured inbox (env, then setting). Returned even
    when missing: a configured-but-absent folder means 'not reachable',
    which send_message reports as queued rather than silently rerouting."""
    env = os.environ.get("XOD_TREE_INBOX_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    try:
        from app.services.storage import get_setting
        saved = (get_setting("tree_inbox_dir", "") or "").strip()
    except Exception:
        saved = ""
    if saved:
        return Path(saved).expanduser()
    return None


def _discovered_dir() -> Path | None:
    """Default inbox locations in the shared spaces this machine can see.
    Returned only when the surrounding space actually exists."""
    # The rdrive server space, on the machine that hosts the family drive.
    files_root = Path.home() / "Documents" / "rdrive" / "data" / "files"
    if files_root.is_dir():
        for account in sorted(files_root.iterdir()):
            if account.is_dir():
                return account / "tree-inbox"
    # The shared space the family registry lives in (sibling of family/).
    try:
        from app.services import family_registry
        space_root = family_registry.registry_path().parent.parent
        if space_root.is_dir():
            return space_root / "tree-inbox"
    except Exception:
        pass
    return None


def inbox_dir() -> Path | None:
    """Where messages go, or None when no shared inbox is reachable."""
    configured = _configured_dir()
    if configured is not None:
        return configured if configured.is_dir() else None
    return _discovered_dir()


def _write_atomic(folder: Path, message: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / f"{message['id']}.json"
    tmp = folder / f".{message['id']}.tmp"
    tmp.write_text(json.dumps(message, indent=1), encoding="utf-8")
    os.replace(tmp, final)
    return final


def _drain_outbox(inbox: Path) -> int:
    """Move queued messages into a now-reachable inbox. Best effort: a
    file that cannot move stays queued for the next send."""
    out = outbox_dir()
    if not out.is_dir():
        return 0
    moved = 0
    for f in sorted(out.glob("*.json")):
        try:
            shutil.move(str(f), str(inbox / f.name))
            moved += 1
        except OSError:
            pass
    return moved


def send_message(kind: str, text: str) -> dict:
    """Write one message for Tree. Returns where it landed:
    state "inbox" (in the shared tree-inbox, watcher will deliver it) or
    state "queued" (local outbox; drains when the inbox is reachable)."""
    kind = (kind or "note").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    text = (text or "").strip()
    if not text:
        raise ValueError("text is empty")
    if len(text) > MAX_TEXT:
        raise ValueError(f"text is too long (max {MAX_TEXT} characters)")
    message = {
        "id": str(uuid.uuid4()),
        "ts": _now_iso(),
        "from_device": from_device(),
        "from_app": "xod",
        "kind": kind,
        "text": text,
        "read": False,
    }
    inbox = inbox_dir()
    if inbox is None:
        path = _write_atomic(outbox_dir(), message)
        return {"state": "queued", "id": message["id"], "path": str(path),
                "from_device": message["from_device"], "drained": 0,
                "message": "No shared tree-inbox is reachable from this "
                           "machine, so the message is queued locally and "
                           "will drain when the family drive is."}
    drained = _drain_outbox(inbox)
    path = _write_atomic(inbox, message)
    return {"state": "inbox", "id": message["id"], "path": str(path),
            "from_device": message["from_device"], "drained": drained,
            "message": "Message is in the family tree-inbox; Tree's "
                       "watcher will deliver it."}
