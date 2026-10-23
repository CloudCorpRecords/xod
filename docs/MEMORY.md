# Memory

One store for everything Xod remembers (`app/services/memory_store.py`): facts about you,
long-term memories/learnings, and episodes (things that happened, with a date) share one record
shape and one search.

## Backends — local first, replaceable

| Setting | Backend | Where it lives |
| --- | --- | --- |
| *(default)* | Chroma, embedded | `DATA_DIR/chroma` on this machine |
| `XOD_CHROMA_URL=http://host:8000` | Chroma server | that server — the "something bigger" option |
| `XOD_MEMORY_BACKEND=sqlite` | built-in SQLite fallback | `DATA_DIR/memory.db` |

If Chroma isn't installed or can't start, Xod falls back to the SQLite store automatically and
says why in `GET /api/memory/store` (`fallback_reason`). Other vector databases can be added
behind the same `MemoryStore` interface (`upsert/get/delete/search/list/stats`) without touching
callers.

Embeddings stay local: nomic-embed-text via LM Studio/Ollama when available, otherwise a
deterministic hashed vector. Nothing about memory requires a cloud model.

## Migration

`POST /api/memory/store/migrate` (or `memory_store.migrate()`) imports, by stable id:

- `about_you.json` facts → `kind="fact"` (`fact:<id>`)
- `xod.db` `memories` rows → `kind="memory"` (existing embedding blobs are reused)
- `recall.db` `episodes` rows → `kind="episode"` (existing embedding blobs are reused)

Sources are never deleted, and re-running imports nothing new. New facts, memories and episodes
are mirrored into the store as they are written, so the legacy files remain a readable backup
during the transition.

## API

- `GET /api/memory/store` — backend, counts by kind, Chroma URL if set
- `POST /api/memory/store/migrate` — run the import, returns per-source counts
- `POST /api/memory/store/search` — one search across facts + memories + episodes
  (`category` may carry a comma list of kinds to restrict to)

Tests: `tests/smoke/run_memory_store_test.py` (no model needed).
