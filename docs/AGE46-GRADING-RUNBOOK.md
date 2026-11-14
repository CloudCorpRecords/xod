# AGE-46 Grading Runbook (Tiers 3, 4, 5, and 7)

> **Status: RIG BUILT — NO TIER IS GRADED YET.**
> All four suites below are deterministic, offline tool-contract grading rigs (`FakeAsk` / `FakeQueue` / scripted `workers.agent` / scripted `task_queue` finishes). **No tier (3, 4, 5, or 7) is graded until the live ceremony run executes on a pinned Mac scratch instance.**

## 1. What Each Rig Checks

### Tier 3 — One Agent Sequence (`tests/brain/run_tier3_grading_test.py`)
Reference: `docs/orchestration-tiers/tiers/tier-03.md` and `TIER-DEEP-DIVES.md` (Tier 3).

- **T3-A (Clean 4-step sequence & pre-execution plan discipline):** Verifies that `m["plan"]` is recorded before the first subtask executes, `prior_step` carry-forward delivers verified upstream outputs to downstream steps, `report.txt` is created and verified at the destination (`Total: 50`), and `build_report` reports all 4 subtasks `done` and `verified`.
- **T3-B (Failed middle step stops chain — load-bearing gate):** When step 2 fails (`timeout`), `MissionRunner` (`continue_on_failure=False`) stops immediately: steps 3 and 4 are never queued (`task_id is None`), `report.txt` is never written, and `build_report` separates completed (`1`), failed (`1`), and unattempted (`2`) components while naming the failed middle step in `failure_reason`.
- **T3-C (Plausible-assumption / silent-skip look-alike rejected):** When step 2 claims `done` while admitting it could not complete reading the input and assumed a value, the outcome gate rejects it (`failed`), stopping the chain before the downstream write step runs.
- **Live-only (skipped):** Live LLM planner producing a dependency-ordered plan from the raw goal; live agent retrying only the failed middle source on transient timeout.

### Tier 4 — Research Across Sources (`tests/brain/run_tier4_grading_test.py`)
Reference: `docs/orchestration-tiers/tiers/tier-04.md` and `TIER-DEEP-DIVES.md` (Tier 4).

- **T4-A (Research contract repair & sourced-answer gate):** Verifies `_capability_contract_repair` repairs empty-tool research steps to `["web.run"]`, and `_sourced_answer_problem` rejects unsourced claims (no URL) and URL-only replies (no answer statement) while accepting claims with source name, URL, and fact.
- **T4-B (Contradiction & scope preservation + Confirmed/Inferred/Unknown):** Planted conflicting primary (`registry_primary.txt`: incorporated `2018-03-14`) and secondary (`launch_blog.txt`: launched `2019-06-01`) sources plus a non-independent reprint (`syndicated_wire.txt`) are reconciled into `synthesis.md` with distinct `## Confirmed`, `## Inferred`, and `## Unknown` sections and no averaged compromise date.
- **T4-C (Unread source narrowing — load-bearing gate):** Through `workers.coordinate`, a worker whose source read fails (`ENOENT`) is marked `failed` (never an empty clean result), setting coordinator outcome to `"partial"` and naming the unread source in `failures` and `render_result`.
- **Live-only (skipped):** Live LLM adjudicating conflicting live web sources without averaging dates or counting syndicated copies as independent confirmations; live LLM ranking primary records over secondary summaries.

### Tier 5 — Delegated Parallel Work (`tests/brain/run_tier5_grading_test.py`)
Reference: `docs/orchestration-tiers/tiers/tier-05.md` and `TIER-DEEP-DIVES.md` (Tier 5).

- **T5-A (Disjoint briefs, context isolation, real parallelism):** `workers.coordinate` fans out 4 disjoint shard briefs, isolates each worker's facts from sibling briefs, executes all 4 concurrently (`4 x 0.4s` completes in `< 1.3s` wall time), and accounts for all 4 in the explicit denominator.
- **T5-B (Worker failure preserved in denominator — load-bearing gate):** When 1 of 4 workers fails (`EACCES`), `outcome` is `"partial"` (never `"done"`), all 4 workers remain in `workers` (`3 done`, `1 failed`), and `failures` + `render_result` itemize the failed shard.
- **T5-C (Permission envelope & nesting guard):** `workers.allowed("computer_write_file")` refuses under `read_only` envelope; `workers.spawn` raises `PermissionError` for non-`read_only` envelopes and nested worker-spawns.
- **T5-D (Parent verification of decisive claims):** Through `MissionRunner`, the parent step independently reads a decisive shard file on disk and writes `sweep_summary.txt`, verified at destination.
- **Live-only (skipped):** Live orchestrator writing its own disjoint worker briefs for a 40-file sweep; live parent independently selecting decisive claims to spot-check on disk.

### Tier 7 — Recurring Autonomous Work (`tests/brain/run_tier7_grading_test.py`)
Reference: `docs/orchestration-tiers/tiers/tier-07.md` and `TIER-DEEP-DIVES.md` (Tier 7).

- **T7-A (Quiet cycles recorded — load-bearing gate):** 5 consecutive `no_change` watch cycles (`WATCH: nothing new`) produce 5 finished `routine_runs` records with `outcome="no_change"` and `delivery_state="suppressed"`, 0 chat messages, and 0 pings.
- **T7-B (Materiality & dedupe — load-bearing gate):** A material change (`WATCH: found`) delivers 1 message to `"Background updates"`; an identical finding on the next cycle is `deduped` (0 new messages); a distinct new finding delivers 1 new message.
- **T7-C (Unread source is failure, never `no_change`):** `WATCH: could not check ...` and missing `WATCH:` lines classify as `"failed"`.
- **T7-D (Restart recovery without duplicate notification):** `delivery.recover()` checks destination chat for an already-landed `'sending'` row and marks it `'sent'` without re-sending.
- **T7-E (Honest disablement preserving identity & history):** After `MAX_CONSECUTIVE_FAILURES` (3) failures, `routines._follow_up()` disables the routine (`enabled=False`) while preserving its `id` and `runs` history, and delivers a notice naming the stopped routine.
- **Live-only (skipped):** Multi-day / multi-week observation across Mac sleep/wake cycles; live LLM watch execution against live changing external sources and phone push delivery.

---

## 2. Exact Commands to Run Each Suite

Run from the repository root (`python` or `.venv/bin/python` on macOS):

```powershell
# Tier 3
python tests/brain/run_tier3_grading_test.py

# Tier 4
python tests/brain/run_tier4_grading_test.py

# Tier 5
python tests/brain/run_tier5_grading_test.py

# Tier 7
python tests/brain/run_tier7_grading_test.py
```

Existing Tier 2 brain suites patterned from:

```powershell
python tests/brain/run_missions_verdict_test.py
python tests/brain/run_missions_planner_integrity_test.py
python tests/brain/run_missions_tier2_adapt_test.py
```

---

## 3. What the Future Live Ceremony Run Still Needs

**No tier is graded until the live ceremony run completes.** Before executing live grading:

1. **Pinned Mac scratch instance:** Provision a dedicated, pinned macOS scratch host (not a developer workstation) with clean Xod state, isolated scratch directory, and real sleep/wake scheduling for Tier 7.
2. **Family key ceremony:** Complete the family key ceremony so live LLM provider credentials, search keys, and phone/desktop notification channels are unlocked in the scratch environment without ad-hoc key injection.
3. **Read-back discipline:** For every live run, enforce destination read-back before claiming `CONFIRMED` — inspect the actual mission report, `routine_runs` / `deliveries` tables, `"Background updates"` chat transcript, and destination files on disk, and record raw evidence packets before assigning any tier verdict.
