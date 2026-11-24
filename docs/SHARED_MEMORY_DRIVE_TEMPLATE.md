# Xod Shared Memory Template (Bring-Your-Own Google Drive)

Xod is open source, so every user owns their own shared memory store — no central Xod cloud server and no hardcoded accounts.

## How It Works
Each Xod device points `XOD_SHARED_MEMORY_DIR` to a folder inside its own **Google Drive Desktop** (or iCloud / Dropbox / Syncthing) mount:

```bash
export XOD_SHARED_MEMORY_DIR="$HOME/Library/CloudStorage/GoogleDrive-you@gmail.com/My Drive/XodSharedMemory"
```

Inside that folder, Xod creates a conflict-free template:

```text
XodSharedMemory/
├── manifest.json          # Schema version + registered devices
├── README.txt
└── devices/
    ├── macbook-darwin.jsonl   # Append-only shard written ONLY by MacBook
    └── desktop-windows.jsonl  # Append-only shard written ONLY by Desktop
```

### Why Per-Device Shards?
- **Zero sync conflicts**: Device A only ever appends to `devices/A.jsonl` and reads `devices/*.jsonl`. Google Drive never creates conflicting duplicate files.
- **Idempotent**: Items are fingerprinted by `(kind, category, content)` so repeated exports never duplicate entries.
- **Privacy-first**: Never store raw passwords or API keys in shared memory (use `credential_vault` / `muse_vault` for credentials).

## Quick Start (Python / Agent)
```python
from app.services.memory_drive_template import DriveSharedMemoryAdapter

mem = DriveSharedMemoryAdapter()  # reads XOD_SHARED_MEMORY_DIR
mem.init_template()
mem.export_items([{"kind": "fact", "category": "preference", "content": "Prefer concise PR summaries"}])
peer_data = mem.import_peer_items()
```
