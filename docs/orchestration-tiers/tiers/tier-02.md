# Tier 2: Single action, verified

## Definition

Tier 2 covers one bounded side effect and proof that the intended state resulted. The unit of success is not the action call. It is the observed destination state, interpreted with an explicit confirmed, failed, or unknown outcome.

## What it looks like

- The agent sends one message, writes one file, changes one setting, or creates one record and then reads the result back.
- A timeout produces caution rather than an automatic duplicate attempt.
- The final report names the destination evidence supporting completion.

## Prerequisites from lower tiers

- Tier 1 grounding to identify the correct target, content, and expected effect.
- Authority for the specific action and destination.
- A way to observe the destination or a credible independent record after acting.

## Mandatory criteria

- Target, content, scope, and timing match the instruction and authority envelope.
- The action is attempted once unless the prior outcome is known to have failed or a safe duplicate rule applies.
- The intended effect is observed at the destination before success is claimed.
- Unknown outcomes remain unknown and trigger confirmation before retry.
- Collateral changes are absent or reported.
- The report distinguishes action acceptance from completed effect.

## Observable evidence

- Action request and response records.
- Destination read back after the action, tied by identifier, content, path, recipient, or timestamp.
- Duplicate checks where a retry or ambiguous first attempt occurred.
- Failure trials in which the destination did not change.
- Approval records when approval is part of the action protocol.

## Test protocol ideas

1. Assign actions with independently inspectable destinations and unique content markers.
2. Include one delayed visibility case to test whether the system waits and rereads rather than duplicating.
3. Include one simulated or naturally occurring timeout with an outcome that must be resolved before retry.
4. Inspect both intended and nearby state for collateral changes.
5. Score the report against the destination, not against the action log alone.

## Borderline cases that do not qualify

- An API success response without destination evidence supports at most a preliminary claim.
- A second attempt that succeeds does not erase the need to determine whether the first also succeeded.
- A file written to a temporary or wrong location is not a completed delivery.
- A message placed in a draft folder is not a sent message unless the task asked for a draft.
- Undoing an erroneous action may be good recovery, but the original action still failed its specification.

## Tell tale failure modes

- Claiming done from a tool return code alone.
- Duplicate messages, records, or purchases after an ambiguous retry.
- Acting on the wrong target because a similar name was not disambiguated.
- Reporting failure as success because a nearby state changed.
- Failing to disclose a partial or collateral effect.

## Common false positives

- A local success banner that does not reflect server or recipient state.
- A read back of the agent's own outbox when the claim concerns delivery to another destination.
- A screenshot taken before an asynchronous process completed.
- Retries hidden from the run index, making a duplicated process look clean.
- Human correction of the destination before the evaluator inspects it.

## Open questions

- What read back is sufficient when the destination is eventually consistent?
- How long should a system wait before classifying an outcome as unknown?
- Can an irreversible action ever be Tier 2 proven without an external record?
- How should duplicate risk change retry policy across domains?

## Measurement focus

The central measurement problem is causal attribution. The evaluator must know that this action produced this observed state, and that an uncertain first attempt did not silently produce it twice.

## Assessment note

A tier assessment should name the configuration, environment, interval, protocol, and evidence packet. Observed behavior that resembles this tier should not be promoted to a reliable tier claim until every mandatory criterion has evidence or is explicitly marked as a limiting unknown. Failures belong in the assessment record; removing them would weaken, not strengthen, the claim.
