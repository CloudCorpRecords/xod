# Long-form context: design (AGE-96)

Branch `tree/longform-context-2026-11-22`, base 55bf0860 (v1.1.18). Merge target: main.
Owner directive (2026-11-22): Xod should hold and use long conversations and long
documents without losing the thread. This is the smallest working version of that:
a chunked persistent store, budgeted recall, and compaction instead of truncation.

## Current state (what exists at base)

- `app/services/memory_layers.py::checkpointed()` is the only long-conversation
  mechanism. After 30 messages it summarizes everything older than the last 12
  turns into one stored checkpoint (`chat_checkpoints` in `xod.db`) and the model
  sees checkpoint + recent turns. Two gaps: (1) the summary is the only survivor —
  verbatim detail (an exact number, a quoted sentence, a file path from message 4)
  is gone and cannot be recovered; (2) if the summarizer call fails, the fallback
  is sending the *full* history, which can overflow the model outright.
- `app/services/recall.py` is episodic memory over *finished* work (chats, tasks)
  in `recall.db` (FTS5 + local embeddings). It answers "what did we do last
  week?", but it is not scoped to the live conversation's own long inputs, and a
  long document pasted or attached in this chat is not in it.
- Long inputs (a pasted transcript, a big file's text) go into the prompt whole.
  Nothing in the chat path measures the history against a token budget or fits it.

## Design

One new module, `app/services/longform_context.py`, stdlib only (the light core
ships without numpy; embeddings stay with `recall.py`). Storage follows the
`recall.db` pattern: its own SQLite file, `longform.db`, in `DATA_DIR`.

### 1. Chunk store

Tables: `sources(id, kind, title, session_id, created, chars)` and
`chunks(id, source_id, seq, text, tokens)`, plus an FTS5 virtual table over chunk
text (porter unicode61, same tokenizer as recall).

- `ingest_text(text, kind, title, session_id, source_id=None)` splits the text
  into chunks and stores them. Chunking is paragraph-aware: paragraphs pack into
  ~1200-char chunks, a single oversized paragraph is hard-split, and consecutive
  chunks overlap by ~150 chars so a fact straddling a boundary survives whole in
  at least one chunk. Re-ingesting the same `source_id` replaces its chunks, so a
  growing chat thread can be re-ingested safely.
- `ingest_messages(session_id, messages)` renders a chat history (role: content)
  and ingests it under `chat-<session_id>`.
- Token estimate is the codebase-standard rough cut: `ceil(len(text) / 4)`,
  stored per chunk at ingest so recall never re-measures.

### 2. Budgeted recall

`recall(query, budget_tokens, session_id=None)` assembles context that is
guaranteed to fit the budget:

1. Rank chunks by FTS5 BM25 against the query's terms (a LIKE scan is the
   fallback when FTS has nothing or the query has no terms, newest first).
2. Greedily take ranked chunks while their stored token counts fit the budget;
   a chunk that does not fit is skipped, not cut, and smaller later chunks may
   still fit.
3. Return the chosen chunks in document order (source, then seq) with a header
   naming the source, plus stats (chunks used, tokens used, budget).

The guarantee the caller gets: `tokens_used <= budget_tokens`, always, and the
result names where each piece came from.

### 3. Compaction hook (agent path)

`fit_budget(session_id, messages, budget_tokens=DEFAULT_HISTORY_BUDGET)` runs in
the chat path in `app/main.py`, immediately after `memory_layers.checkpointed()`:

- Under budget: returns the messages untouched (the common case; no behavior
  change for normal chats).
- Over budget: leading system messages are kept; the newest tail that fits ~55%
  of the budget is kept verbatim; the middle is replaced by one checkpoint
  message. Before anything is replaced, the *full* history is ingested into the
  chunk store, so compacted detail stays recallable verbatim via `recall()`.
- The checkpoint text comes from the existing summarizer seam
  (`memory_layers.summarizer`, injectable for tests). If summarizing fails, the
  fallback is a deterministic extractive digest (the head of each middle message,
  capped to ~25% of the budget) — never the full history, never a silent hard
  truncation.

## Deliberately out of v1

- No embedding/semantic ranking in this store (keyword + recency only);
  `recall.py` already owns semantic search over finished episodes, and merging
  the two ranking schemes is a follow-up once this is proven in the live path.
- No UI, no new endpoints, no settings surface. `status()` exists for tests and
  logs only.
- No change to `checkpointed()` itself; `fit_budget` is a second, budget-driven
  layer behind it, so existing checkpoint behavior is untouched.
- Attached-document auto-ingest at upload time: the store accepts documents via
  `ingest_text` today; wiring the upload route to call it is a follow-up.

## Tests

`tests/smoke/run_longform_context_test.py` (repo smoke style, throwaway data
folder, no model needed — the summarizer is injected):

- L1 chunking: paragraph packing, oversized-paragraph split, overlap, token counts.
- L2 ingest: sources/chunks persisted, re-ingest replaces, `status()` counts.
- L3 recall: finds the chunk carrying a rare term; assembled tokens never exceed
  the budget; document order preserved; session scoping.
- L4 fit_budget: under budget is identity; over budget keeps system + tail
  verbatim, replaces the middle with the injected summary, and the result fits.
- L5 compaction fallback: a failing summarizer yields the extractive digest,
  still within budget, never the full history.
- L6 nothing is lost: after compaction, `recall()` over the session returns a
  verbatim detail that only existed in a compacted middle message.
- L7 message rendering: non-string content (lists/dicts) ingests without error.

Existing gates re-run: the muse_* smoke suites and the partner suites under
Python 3.12, compared against base 55bf0860 if anything disagrees.
