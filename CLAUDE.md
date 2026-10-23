# Handoff: unified memory store (branch `memory-unified-store`, parked for Rene's review)

**Status: DO NOT merge to `main` without Rene's explicit OK.** The branch is pushed
(`a7e18bd` + this note) for review only. `main` is untouched at `6263d34 Release 0.9.2`.

## What this branch does

One memory store for facts + long-term memories + recall episodes, replacing three
disconnected stores. Start with `docs/MEMORY.md`, then `app/services/memory_store.py`.

- Default backend: Chroma embedded (`DATA_DIR/chroma`). Bigger backend: `XOD_CHROMA_URL`.
  Fallback: built-in SQLite (`memory.db`) if Chroma can't start; doctor reports it
  (“Memory store” check in `app/services/doctor.py`).
- Migration (`memory_store.migrate()`, `POST /api/memory/store/migrate`) imports
  `about_you.json`, `xod.db` memories, `recall.db` episodes by stable id. Sources are
  never deleted; re-running imports nothing new. Rene's live data is already migrated
  (64 records: 2 facts, 6 memories, 56 episodes).
- Passwords/keys/cards are refused by the store on purpose — they belong only in the
  Keychain vault (`app/services/credential_vault.py`). Do not weaken that guard.
- Also fixed: conversational “yeah, i also want you to remember…” now saves a fact
  (`REMEMBER_RE` in `app/services/user_memory.py`), and `crew_status` reports
  `agent_ask` threads so an empty Crew list isn't reported as “nothing was done”.

## Before merging (in order)

1. Start LM Studio (chat + `nomic-embed-text`), then run the two suites that were
   blocked because LM Studio was down — they are unverified, not failing:
   `.venv/bin/python tests/smoke/run_memory_test.py`
   `.venv/bin/python tests/smoke/run_recall_test.py`
2. Re-run the offline suites: `run_memory_store_test.py`, `run_keychain_test.py`,
   `run_crew_test.py` (all passed 2026-10-23).
3. Only with Rene's OK: merge to `main`, then rebuild/release — `/Applications/Xod.app`
   runs a bundled copy, so merging alone does not update the installed app.

Commits on this repo carry `Signed-off-by` (`git commit -s`).
