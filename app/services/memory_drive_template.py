"""
Open-source Shared Memory Layer template for Google Drive (or any synced cloud folder).

Because Xod is open source, every user owns their own Google Drive / cloud storage —
nothing is hardcoded to a central server or anyone else's account.

Design (conflict-free per-device shards):
  <shared_folder>/
    manifest.json               # schema version, created_at, device registry
    README.txt                  # human-readable note for the Drive folder
    devices/
      <device_id>.jsonl         # append-only log owned exclusively by that device

Why per-device JSONL shards?
  Google Drive Desktop / iCloud / Dropbox create duplicate conflict files when two
  machines edit the same SQLite DB or JSON file simultaneously. Giving each Xod
  device its own append-only shard means Device A only ever writes devices/A.jsonl
  and reads devices/*.jsonl — zero write conflicts across any number of devices.

Configuration:
  Set XOD_SHARED_MEMORY_DIR (or pass shared_dir explicitly) to your synced folder, e.g.:
    ~/Library/CloudStorage/GoogleDrive-you@gmail.com/My Drive/XodSharedMemory
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1


def _safe_id(raw: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_.-]", "_", (raw or "device").strip())[:64]
    return clean or "device"


def default_device_id() -> str:
    env_id = os.environ.get("XOD_DEVICE_ID")
    if env_id:
        return _safe_id(env_id)
    host = socket.gethostname().split(".")[0]
    return _safe_id(f"{host}-{platform.system().lower()}")


def item_fingerprint(kind: str, content: str, category: str = "") -> str:
    payload = f"{kind.strip().lower()}|{category.strip().lower()}|{content.strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


class DriveSharedMemoryAdapter:
    """Bring-your-own Google Drive / synced-folder shared memory adapter."""

    def __init__(self, shared_dir: Optional[str | Path] = None, device_id: Optional[str] = None):
        raw_dir = shared_dir or os.environ.get("XOD_SHARED_MEMORY_DIR") or ""
        self.shared_dir: Optional[Path] = Path(raw_dir).expanduser() if raw_dir else None
        self.device_id: str = _safe_id(device_id or default_device_id())

    def _require_dir(self) -> Path:
        if self.shared_dir is None:
            raise ValueError(
                "No shared memory folder configured. Set XOD_SHARED_MEMORY_DIR to your "
                "Google Drive synced folder (e.g. ~/Library/CloudStorage/GoogleDrive-.../My Drive/XodSharedMemory)."
            )
        return self.shared_dir

    def init_template(self) -> Dict[str, Any]:
        """Creates the open-source shared memory folder template in the user's Google Drive."""
        root = self._require_dir()
        devices_dir = root / "devices"
        devices_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = root / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "kind": "xod_shared_memory_template",
                "created_at": time.time(),
                "devices": {},
            }
        manifest.setdefault("devices", {})[self.device_id] = {
            "last_seen": time.time(),
            "platform": platform.system(),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        readme_path = root / "README.txt"
        if not readme_path.exists():
            readme_path.write_text(
                "Xod Shared Memory Folder (Bring-Your-Own Google Drive Template)\n"
                "Each Xod device writes only to devices/<device_id>.jsonl and reads all peers.\n"
                "Do not store raw passwords or API keys here.\n",
                encoding="utf-8",
            )
        return {
            "success": True,
            "shared_dir": str(root),
            "device_id": self.device_id,
            "manifest": str(manifest_path),
        }

    def shard_path(self, device_id: Optional[str] = None) -> Path:
        root = self._require_dir()
        return root / "devices" / f"{_safe_id(device_id or self.device_id)}.jsonl"

    def _read_shard(self, path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
            except ValueError:
                continue
        return rows

    def export_items(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Appends new memory items (facts, episodes, notes) to THIS device's shard only.
        Deduplicates against items already in this device's shard."""
        self.init_template()
        shard = self.shard_path(self.device_id)
        existing = self._read_shard(shard)
        seen_ids = {r.get("id") for r in existing if r.get("id")}

        written = 0
        with shard.open("a", encoding="utf-8") as fh:
            for item in items:
                content = str(item.get("content") or "").strip()
                if not content:
                    continue
                kind = str(item.get("kind") or "fact")
                category = str(item.get("category") or "general")
                fid = str(item.get("id") or item_fingerprint(kind, content, category))
                if fid in seen_ids:
                    continue
                row = {
                    "id": fid,
                    "kind": kind,
                    "category": category,
                    "content": content,
                    "source_device": self.device_id,
                    "ts": float(item.get("ts") or time.time()),
                    "deleted": bool(item.get("deleted", False)),
                }
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                seen_ids.add(fid)
                written += 1

        return {"success": True, "device_id": self.device_id, "written": written, "shard": str(shard)}

    def import_peer_items(self, include_self: bool = False) -> Dict[str, Any]:
        """Reads all peer device shards from the shared folder, merges latest state per item id."""
        root = self._require_dir()
        devices_dir = root / "devices"
        if not devices_dir.exists():
            return {"success": True, "items": [], "peers": []}

        merged: Dict[str, Dict[str, Any]] = {}
        peers: List[str] = []
        for shard in sorted(devices_dir.glob("*.jsonl")):
            peer_id = shard.stem
            if not include_self and peer_id == self.device_id:
                continue
            peers.append(peer_id)
            for row in self._read_shard(shard):
                fid = row.get("id")
                if not fid:
                    continue
                prev = merged.get(fid)
                if prev is None or float(row.get("ts", 0)) >= float(prev.get("ts", 0)):
                    merged[fid] = row

        active = [r for r in merged.values() if not r.get("deleted")]
        active.sort(key=lambda r: float(r.get("ts", 0)))
        return {
            "success": True,
            "device_id": self.device_id,
            "peers": peers,
            "count": len(active),
            "items": active,
        }

    def status(self) -> Dict[str, Any]:
        if self.shared_dir is None:
            return {"configured": False, "device_id": self.device_id}
        devices_dir = self.shared_dir / "devices"
        shards = sorted(p.stem for p in devices_dir.glob("*.jsonl")) if devices_dir.exists() else []
        return {
            "configured": True,
            "exists": self.shared_dir.exists(),
            "shared_dir": str(self.shared_dir),
            "device_id": self.device_id,
            "shards": shards,
        }
