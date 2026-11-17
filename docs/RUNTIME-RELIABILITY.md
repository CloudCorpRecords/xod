# Long-running work: repair evidence and limits

## Scope

This pass repairs existing execution paths. It does not add another conductor,
replace the provider stack, or recreate the external Tree/Lane Highway tooling.

- Team batches and read-only coordinator turns share a process/event-loop worker
  gate, using Team's existing parallel limit (default 3, range 1–6).
- Model HTTP requests have independent admission gates: one request at a time
  for loopback/local inference and bounded parallelism for remote providers.
  A model gate is held only during the request, not during tool execution.
  Provider selection and credentials remain unchanged.
- Team batches reject more than 32 pieces explicitly. Queued workers emit
  status; close, cancel and error paths cancel and await owned tasks/getters.
- Team device claims use a unique holder while audit/effect ownership stays
  with the parent. Existing resource mappings, trust and outcome guards remain
  authoritative; arbitrary shell commands and unknown external MCP tools are
  not magically classified as device operations.
- Worker results carry compact evidence and explicitly identify themselves as
  worker reports, not independent verification. Approval holds propagate back
  to the parent rather than allowing it to continue with the next tools.
- Context shrinking retains the original user/system messages and structured
  effect state, pending-approval IDs, paths and job handles in tool results.
- Crew reserves startup capacity before asynchronous workspace setup. Startup
  tasks are tracked, cancellation cleans up partially started native hosts,
  and process-kill paths await actual process exit.
  Stopping during isolated workspace creation waits for the uncancellable
  creation thread, records its result, and removes the new unused worktree and
  branch before confirming stop. Cleanup failure retains the reference and an
  explicit interrupted state. Legacy or previously launched workspaces are
  never treated as unused startup workspaces.
- Crew checkpoints use atomic replacement; write failure is explicit and
  preserves the old file. Unfinished jobs are retained independently of the
  60-job completed-history limit, including approval/session records.
- Force-stop does not declare a task stopped if its Python worker is still
  alive after the existing cleanup timeout. One retained stop operation owns
  cancellation, eventual settlement, tab cleanup, and consumer replacement;
  request timeout or caller cancellation does not abandon it. Competing stop
  and watchdog requests do not cancel cleanup twice. Stall re-drive uses the
  same mechanism and cannot requeue work until the previous run exits.
  Deliberately long-lived background
  jobs keep their existing separate lifecycle; this is not a new kill-all policy.
- Lightweight mode keeps the existing Muse watcher and Partner supervisor.
  Disabled/paused/away rules, dry-run behavior, OS permission requirements, and
  one-time approval intent records are unchanged. Health monitoring already
  belonged to the lightweight core.
- Refresh requests coalesce, stale responses are discarded after user changes,
  and timer ticks do not invalidate a merely slow response. Crew's panel cadence
  is 3 seconds instead of 1.5; the separate approval chip remains at 4 seconds.
  Drafts and scroll offsets are preserved during redraws.

## Reproducible scheduler measurements

Linux Replit workspace, Python 3.12, scripted workers only. Each worker sleeps
200 ms; no real inference, network requests, device actions, or billed tokens.
Baseline loads the prior `team.py` from Git without replacing working files.
Measurements are short observations, not statistically significant latency claims.

| Scenario / metric | Before | After |
|---|---:|---:|
| One batch: peak workers | 3 | 3 |
| One batch: completion | 201.80 ms | 216.34 ms |
| One batch: first worker | 0.30 ms | 14.26 ms |
| One batch: maximum queue wait (3 samples) | 0.33 ms | 14.31 ms |
| One batch: loop lag p50 / p95 | 0.43 / 2.50 ms | 0.37 / 2.16 ms |
| Two batches: peak workers | **6** | **3** |
| Two batches: completion | 202.20 ms | 418.60 ms |
| Two batches: first worker | 0.32 ms | 16.21 ms |
| Two batches: maximum queue wait (6 samples) | 0.37 ms | 217.13 ms |
| Two batches: loop lag p50 / p95 | 0.45 / 1.01 ms | 0.38 / 1.20 ms |
| Workers still running after stream close | **3** | **0** |
| Simulated calls, two batches plus cancel probe | 9 | 9 |
| Real model calls / tokens | 0 / not measured | 0 / not measured |

The doubled two-batch completion time is intentional: six independent sleeping
fixtures no longer bypass a three-worker budget. This proves bounded work, not
a faster model. No reduction in live model calls, tokens or cost is claimed.

Backend observations: before restart, RSS 135.77 MiB and 0.6% process CPU over
5 seconds. The first post-restart sample was 137.54 MiB and 2.6% during preview
activity, so it is **not a comparable idle sample**. The repair does not establish
a smaller always-on memory footprint. A controlled weak-hardware soak and
startup/import profile are still required before setting an idle resource budget.

For the subsequent proposed 8 GB budget, reproducible process-tree collector,
controlled Linux idle measurements and native limitations, see
`docs/LIGHTWEIGHT-FOOTPRINT.md`. Those observations do not turn these earlier
unmatched snapshots into a valid before/after comparison.

Commands:

```sh
python tests/runtime/benchmark_team.py --check
python tests/runtime/benchmark_team.py --batches 1 --check
# Supply the pre-change revision when reproducing a comparison:
python tests/runtime/benchmark_team.py --baseline-ref <revision>
python tests/runtime/test_runtime.py
python tests/runtime/test_task_stop.py
node tests/runtime/test_refresh_gate.js
```

## Verification

Passed in isolated state:

- Team: 37 checks.
- Task concurrency: 15 checks.
- Read-only workers: 43 checks.
- Orchestrator (including interruption/resume): 82 checks.
- Blocked/pause handling: 16 checks.
- Long-form execution: 32 checks.
- Muse watcher: 61 checks.
- Approval reliability: 16 tests after the handoff repair; Partner delegation also passed.
- Native Crew protocol fixtures: 96 checks.
- New lifecycle tests: 13; refresh-gate regression checks passed. The startup
  race test uses real Git in a throwaway repository and verifies absence of the
  worktree and branch plus the retained stopped checkpoint after reload.
- Python compilation, changed JavaScript syntax, and diff whitespace checks.

New lifecycle and refresh checks are wired into CI. Native Crew fixtures now
isolate XDG_CONFIG_HOME as well as HOME, so inherited developer configuration
cannot silently change their inputs.

The handoff repair adds `tests/runtime/test_task_stop.py`: real SQLite/queue
regressions for cleanup exceeding the five-second response timeout, competing
watchdog/stop requests, a disconnected caller, shutdown without consumer
respawn, cancellation during stall re-drive, and orchestrated cleanup.
The delayed-cancellation defect identified after the earlier pass is repaired
by retaining the stop operation until the run really exits.
All five new stop tests passed on Linux. The existing watchdog suite passed
24 checks, task concurrency 15, lifecycle 13, Crew 96, Muse watcher 62, and
signing requirement fixtures 18. Partner delegation, Partner AX fixtures, and
the own-Python MCP child import check also passed. The real CoreFoundation
check was skipped on Linux and is UNKNOWN here, not a native pass.

The restarted app serves its local preview. A remote browser test was attempted
once but could not enter: the deliberate localhost-only middleware returned 403.
The testing browser also lacked request interception for isolated Crew fixtures.
Consequently the browser selection/draft/scroll journey is **not verified**.
The localhost-only guard was not weakened. Existing Linux warnings include an
unavailable OS keyring, no configured chat model, and a macOS-only health probe;
none was replaced with insecure credential storage or a fake success.

## Remaining limits

- Real macOS/Windows permission continuity, Muse UI behavior under load, and
  subprocess cleanup on those operating systems need native validation.
- Replit-managed OpenAI discovery returned no available integration here.
  A subsequent user-authorized key did verify live `gpt-6.1-terra` through
  actual Xod dispatch; see `docs/MODEL-CONFIGURATION.md`. Managed provenance
  was not established, and BYOK/local support was not replaced.
- Gates are process-local, appropriate to the single desktop backend. They do
  not coordinate multiple backend processes or remote installations.
- This is not yet a separate featherweight always-on relay. Heavy startup-import
  deferral, local model loading/unloading contention, and controlled 8 GB device
  measurements remain work, not claimed wins.
- Existing durable orchestration checkpoints passed regression tests; arbitrary
  delegated shell/external effects still require their existing confirmation
  checks. Cancellation of remote work is not proof that a side effect did not occur.
