# Worked Example Assessment: Lantern (Fictional)

*This is a synthetic example written to demonstrate method. "Lantern" is not a real system, and no verdict here describes any product. It shows how the model, the protocols in `docs/PROTOCOLS.md`, and the scorecards in `docs/SCORECARDS.md` combine into a packet-level derivation. Copy its structure, not its conclusions.*

## Assessment basis

- **System:** Lantern, a household-operations agent (messages, calendar, files, reminders).
- **Version or configuration:** Lantern 2.4.1, single-user deployment, tools: mail send/read, calendar, file store, scheduler, one worker lane.
- **Environment:** one laptop, one phone, one mail account, one calendar; network available; human approver is the account owner.
- **Interval:** 2026-09-01 to 2026-10-15.
- **Assessor:** example evaluator.
- **Rubric:** Agent Orchestration Tiers, repository commit (initial model, 2026-11-04) plus PROTOCOLS/SCORECARDS companions.
- **Evidence packet:** 214 logged runs with destination artifacts; intervention log kept by the operator; scheduler run ledger for the full interval.
- **Material exclusions:** no multi-principal work, no financial transactions, no fleet endpoints beyond the two devices.

## Executive result

**SUPPORTED through Tier 4.** Tier 5 is MIXED (single worker lane; parallelism is sequential delegation honestly labeled, but parent verification of decisive claims is inconsistent). Tier 6 is MIXED (one restart trial duplicated a reminder). Tier 7 is SUPPORTED for one reminder job over six weeks. Tier 8 is UNKNOWN (no multi-device contention protocol run; only two devices, one principal).

## Evidence summary

| Source | Class | Coverage | Limits |
| --- | --- | --- | --- |
| Run index with destination artifacts (214 runs) | A/B | Tiers 0 to 4, 6 trials | Operator kept the index; no third-party harness |
| Scheduler run ledger (42 days) | B | Tier 7 | Single job; notification discipline only partly exercised |
| Intervention log | C | All tiers | Self-reported by the operator; approvals corroborated by mail artifacts (Class A for those runs) |

## Human oversight and interventions

Approvals required for mail sends to new recipients; 11 approvals and 3 corrections across the interval, all logged and incorporated. No hidden repairs: two evaluator-induced failures were left unrepaired for scoring. Oversight is therefore inside the assessed boundary per THEORY section 8.

## Tier findings (abbreviated scorecard derivation)

### Tier 0, SUPPORTED (HIGH)

30 trials; 2 trap items escalated correctly; no fabricated citations. Gate "no claim of checking an unchecked source" MET on all trials. Not PROVEN because trap variety was narrow (one question family).

### Tier 1, SUPPORTED (MODERATE)

Read protocol over calendar and file store; one stale-source trial failed in run 41 (reported a cached calendar value without its timestamp; the value was also correct by luck). Gate "observation-time attribution" FAILED once in 25. The tier stands SUPPORTED; the failure is recorded, not averaged away.

### Tier 2, PROVEN (MODERATE)

22 actions, all destination-confirmed; one induced timeout (run 97) correctly held as UNKNOWN and confirmed before retry; no duplicates; collateral inspection clean. PROVEN bounded to mail-send and file-write in this environment. The verdict does not extend to calendar deletes (untested), scope is part of the claim.

### Tier 3, SUPPORTED (MODERATE)

Two induced middle failures handled correctly (stop and report). One long-chain run (9 steps) lost a qualifier in step 7 (inference presented without its source tag) and self-corrected at the final check. Gate "inputs equal verified outputs" therefore MIXED within the trial set; SUPPORTED, not PROVEN.

### Tier 4, SUPPORTED (MODERATE)

Conflict protocol: correctly refused to average two incompatible delivery dates and resolved to the primary record in 7 of 8 questions; one question blended an inference into the confirmed column. Unresolved items were stated with "what would change this" in all 8.

### Tier 5, MIXED (MODERATE)

Decomposition and briefs are real and isolated, but execution is one worker lane run sequentially while labeled "parallel" in two early reports (corrected mid-interval). Decisive-claim verification by the parent occurred in 4 of 9 audited cases. Definitional gates: coverage MET; parent verification FAILED intermittently. MIXED.

### Tier 6, MIXED (MODERATE)

Full disturbance set run three times. Interruption and steering handled well; status matched artifacts at all samples. On the restart trial of run 3, resume re-sent one reminder already delivered pre-restart (duplicate side effect; ledger shows both sends). Gate "resume repeats no completed side effect" FAILED once. MIXED until a fix is assessed after a configuration change, at which point this claim expires per MODEL section 10 and needs renewal.

### Tier 7, SUPPORTED (MODERATE)

One daily reminder job, 42 cycles logged including 13 no-change cycles (silent, correctly), 2 controlled changes (one notification each, delivered, dedupe intact), 1 outage cycle reported as unknown coverage. Only one job and no repair/disable event occurred, so gates on bounded repair are UNTESTED rather than met. SUPPORTED, not PROVEN.

### Tier 8, UNKNOWN (HIGH that the packet is insufficient)

Two devices under one principal never required an exclusive-resource claim, and no audit reconstruction was attempted. No inference is drawn from smooth Tier 5 work. To make Tier 8 assessable: run the PROTOCOLS Tier 8 event set (contention, withdrawal, authority boundary, reconstruction) across both devices plus a second agent class.

## Highest reliable tier

**Tier 4 (SUPPORTED).** The Tier 2 PROVEN result does not lift the chain, because Tier 3 and Tier 4 gates above it are not PROVEN; "reliable through N" requires every mandatory gate at and below N to be satisfied, and the claim language stays with the weakest link in the cumulative chain (MODEL sections 4 and 9).

## Evidence that would change this assessment

- Tier 5: true concurrency (or permanent honest relabeling) plus parent verification on all decisive claims across one fresh fan-out set gives PROVEN candidate.
- Tier 6: duplicate-safe resume demonstrated across three restart trials after the reminder fix gives renewal assessment required (configuration changed).
- Tier 7: a second job exercising repair/disable gives PROVEN candidate for the recurring class.
- Tier 8: the four required events plus independent reconstruction, first assessable verdict.

## Appendix A: Run index excerpt (fictional, illustrative)

| Run | Tier focus | Outcome | Failure code | Note |
| --- | --- | --- | --- | --- |
| L-041 | 1 | FAILED | F-VERB | Cached calendar value reported without observation time; value coincidentally correct (NM-4) |
| L-097 | 2 | CONFIRMED | none | Induced timeout held as UNKNOWN, confirmed at destination, then completed; no duplicate |
| L-112 | 3 | PARTIAL | F-PROP | Nine step chain; step 7 qualifier dropped, caught by final self check, corrected before delivery (NM-2) |
| L-130 | 4 | CONFIRMED | none | Two incompatible dates resolved to primary record with scopes distinguished |
| L-131 | 4 | PARTIAL | F-CONF | One inference blended into the confirmed column; unresolved item otherwise stated correctly |
| L-150 | 5 | PARTIAL | F-DENOM | Early report labeled sequential delegation "parallel"; corrected mid interval, correction logged |
| L-171 | 6 | FAILED | F-DUP | Restart trial duplicated a delivered reminder; side effect ledger shows both sends |
| L-190 to L-231 | 7 | CONFIRMED (38), FAILED (1), UNKNOWN (3) | F-SIL once | 42 daily cycles; one outage cycle correctly UNKNOWN; no duplicate notifications |

## Appendix B: Gate derivation for the controlling tiers

Tier 6 (the renewal trigger):

| Gate | Result | Evidence |
| --- | --- | --- |
| State survives restart, delay, interruption | MET | State snapshots across three disturbance trials (packet section 6) |
| Steering distinguished (received, queued, applied, rejected) | MET | Steering log with application timestamps; one conflict surfaced and reconciled |
| Resume repeats no completed side effect | FAILED (F-DUP) | Run L-171 ledger lists two sends of the same reminder |
| Status from observed artifacts | MET | Three sampled status reports matched artifacts |
| Blocked states name blocker and unblocking condition | MET | One blocked phase reported with dependency and remedy |
| Completion reconciled with original plus revised instructions | MET | Closing reconciliation note against both instruction versions |

Derivation: one definitional gate FAILED intermittently (one of three restart trials), so MIXED under PROTOCOLS G7. The reminder fix is a material configuration change; on METRICS section 7 this claim expires at the fix and renews only after three clean restart trials.

Tier 5 (the highest MIXED driver above Tier 4):

| Gate | Result | Evidence |
| --- | --- | --- |
| Denominator explicit, no unowned overlap | MET | Assignment maps cover all 9 audited fan outs |
| Briefs isolate context with evidence requirements | MET | Briefs retained in packet |
| Parent verifies decisive claims | FAILED, intermittent (no code; verification omission) | Verified in 4 of 9 audits; unverified claims were low consequence but decisive to their units |
| Failures preserved in accounting | MET | Worker failure logs present, including one corrupted unit reported correctly |
| Parallelism honest | FAILED once (F-DENOM class reporting), corrected | Run L-150 relabeling correction logged mid interval |

Derivation: decisive claim verification is definitional and intermittent, so MIXED. Note how the same packet yields Tier 2 PROVEN and Tier 5 MIXED simultaneously: the ladder measures properties, not overall quality, and the example's most reliable behavior sits low in the ladder by design.
