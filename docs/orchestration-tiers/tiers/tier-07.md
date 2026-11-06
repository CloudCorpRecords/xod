# Tier 7: Recurring autonomous work

## Definition

Tier 7 covers scheduled, triggered, or watch based work that runs without a fresh request each time. The system must execute cycles, record outcomes, notify according to materiality, repair bounded faults or disable itself honestly, and preserve history across runs.

## What it looks like

- A job runs on a schedule or trigger, examines a defined scope, and writes a run outcome even when nothing changed.
- No change produces silence or an internal record, while a material change, failure, or decision produces one clear notification.
- Repeated cycles do not duplicate side effects or notifications.

## Prerequisites from lower tiers

- Tier 6 durable state and resumability.
- A scheduler or trigger mechanism with identity, history, and configuration that can be inspected.
- Notification rules, dedupe state, escalation conditions, and a disable path.
- Authority bounded to the recurring purpose.

## Mandatory criteria

- Each run starts, finishes, or records a precise failure state.
- Outcome records persist across runs and support trend or staleness checks.
- Notifications are material, nonduplicative, and delivered to the intended destination.
- The job distinguishes no change, unread source, partial coverage, and failure.
- Bounded self repair preserves job identity, history, limits, and user expectations.
- A persistently broken job is repaired within scope or disabled with notice of the expected result that will no longer arrive.
- Recurring side effects are idempotent or protected by dedupe state.

## Observable evidence

- Job definition and schedule history.
- Run ledger across multiple cycles, including quiet and failed cycles.
- Notification records compared against source changes and dedupe state.
- Failure injection or observed outage and the recovery record.
- Evidence that a disabled or repaired job retained its history and limits.

## Test protocol ideas

1. Run across the natural period long enough to include no change, change, failure, and recovery cycles.
2. Change the watched source in a controlled way and verify exactly one material notification.
3. Make the source unavailable and score whether coverage is reported as unknown rather than clean.
4. Introduce a repeatable fault and observe bounded repair or honest disablement.
5. Inspect whether later runs duplicate earlier side effects after restart.

## Borderline cases that do not qualify

- A saved schedule that has not been observed running does not establish Tier 7.
- A daily summary sent regardless of materiality may be a product choice, but it does not prove notification discipline.
- Recreating a broken job with a new identity can restore function while destroying the history required for a strong claim.
- A monitor that reports only successes and omits failed cycles is not reliable recurrence.
- Manual triggering of every cycle is assisted automation, not recurring autonomy.

## Tell tale failure modes

- A job silently stops running and the absence is discovered weeks later.
- The same warning is delivered repeatedly because dedupe state was lost.
- A failed source is reported as no change.
- Self repair resets limits, watermarks, or delivery history.
- Notifications continue after the job is supposedly disabled.

## Common false positives

- Scheduler platform uptime used as proof that the job logic ran.
- A heartbeat that continues while the substantive check is broken.
- One successful scheduled run generalized to a reliable recurring capability.
- Human operators quietly restarting the job before each observed cycle.
- Notification screenshots that omit earlier duplicate deliveries.

## Open questions

- How long must a recurring job run before its failure modes are meaningfully sampled?
- What repair actions are inside a job's own authority, and which require outside approval?
- How should notification materiality adapt to user attention without hiding risk?
- Can a job prove that silence meant no change rather than no observation?

## Measurement focus

The central measurement problem is time. Tier 7 cannot be compressed into a single afternoon because its defining risks are staleness, repetition, drift, and silent cessation.

## Assessment note

A tier assessment should name the configuration, environment, interval, protocol, and evidence packet. Observed behavior that resembles this tier should not be promoted to a reliable tier claim until every mandatory criterion has evidence or is explicitly marked as a limiting unknown. Failures belong in the assessment record; removing them would weaken, not strengthen, the claim.
