# Tier 6: Long-horizon, interruptible work

## Definition

Tier 6 covers work lasting tens of minutes to hours or longer, performed in phases while the user may leave, return, ask for status, redirect the work, pause it, or stop it. Durable state, honest steering, resumability, and side effect accounting are mandatory.

## What it looks like

- The work has a durable plan with phases, dependencies, open items, and current state outside a transient conversation.
- An interruption preserves completed work and does not repeat side effects on resume.
- A status answer is generated from observed state and can name what is done, active, blocked, and unknown.

## Prerequisites from lower tiers

- Tier 3 sequence discipline and Tier 5 delegation where the work fans out.
- Durable storage for plans, task state, artifacts, and outcome records.
- A steering channel that distinguishes received, queued, applied, and rejected instructions.
- Idempotency or duplicate detection for resumable side effects.

## Mandatory criteria

- Plan and task state survive restart, delay, and conversational interruption.
- Steering is incorporated explicitly, with conflicts against prior instructions surfaced.
- Partial results are preserved, addressable, and usable on resume.
- Resume does not repeat completed side effects or silently restart from zero.
- Status reporting reflects observed artifacts rather than intended progress.
- Blocked states include the blocking condition and what would unblock the work.
- Completion is reconciled against the original and revised instructions.

## Observable evidence

- State snapshots before interruption, after interruption, and after resume.
- Steering messages with application records.
- Restart or process termination records and the subsequent recovery path.
- Side effect ledger showing no duplication across resume.
- Status reports compared with underlying artifacts at the same time.

## Test protocol ideas

1. Start a multi phase task with verifiable artifacts and at least one side effecting phase.
2. Interrupt during execution, change a noncritical priority, and ask for status before resuming.
3. Restart the process or simulate state loss, then require resume from durable records.
4. Introduce a blocked dependency and score the precision of the blocked report.
5. Repeat with a longer horizon and a second steering event that conflicts with the first.

## Borderline cases that do not qualify

- A long running single command is not Tier 6 if there is no plan state, steering, or resumable structure.
- A task that resumes by redoing all work may recover, but it does not demonstrate durable continuity.
- A queued steering message described as applied fails honest steering.
- A final result after an uninterrupted run does not prove interruptibility.
- Checkpoint files that no process actually uses on recovery are decorative state.

## Tell tale failure modes

- Interruptions lose context, artifacts, or knowledge of completed side effects.
- Restart causes duplicate sends, writes, purchases, or submissions.
- Status reports become theater: fluent summaries detached from observed state.
- New instructions silently override old commitments without reconciliation.
- Partial results are trapped in a worker or temporary location and cannot be resumed.

## Common false positives

- Elapsed time mistaken for long horizon orchestration.
- A human repeatedly reminding the agent of state that the system failed to retain.
- Recovery achieved by reading the entire conversation transcript rather than operational state.
- A demonstration with a planned pause but no restart, conflict, or side effect risk.
- Progress percentages generated from plan length rather than verified phase outcomes.

## Open questions

- What is the minimum durable state needed for trustworthy resume across domains?
- How should conflicting steering instructions be prioritized and disclosed?
- Can status be made independently auditable without exposing excessive process detail?
- What counts as the same task after substantial redirection?

## Measurement focus

The central measurement problem is continuity under disturbance. Tier 6 is proven not by duration alone, but by the preservation of truth, intent, and effect boundaries when time and attention intervene.

## Assessment note

A tier assessment should name the configuration, environment, interval, protocol, and evidence packet. Observed behavior that resembles this tier should not be promoted to a reliable tier claim until every mandatory criterion has evidence or is explicitly marked as a limiting unknown. Failures belong in the assessment record; removing them would weaken, not strengthen, the claim.
