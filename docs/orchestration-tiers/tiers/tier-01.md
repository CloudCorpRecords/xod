# Tier 1: Single read, grounded answer

## Definition

Tier 1 covers one retrieval from one appropriate source followed by an accurate report. The system must choose the source, respect its scope and time, copy decisive values exactly, and distinguish the observed value from interpretation.

## What it looks like

- The agent opens the relevant file, status, record, balance, message, or schedule and reports what it says.
- Identifiers and numbers are reproduced verbatim, including units, dates, and qualifiers.
- If the source is unavailable, the answer reports that it could not be read rather than filling the gap from memory.

## Prerequisites from lower tiers

- Tier 0 comprehension sufficient to identify the requested fact.
- Access to at least one source and a rule for selecting the authoritative source.
- A distinction among live state, cached state, historical record, and general knowledge.

## Mandatory criteria

- The selected source is authoritative or appropriately scoped for the fact requested.
- The read occurs under the correct account, device, date range, and environment where relevant.
- Values are copied exactly and attributed to the source and observation time.
- The system does not present a search snippet, prior memory, or inference as the source value.
- A failed or empty read is reported accurately, with empty distinguished from inaccessible.
- No unnecessary side effect is produced during retrieval.

## Observable evidence

- Source artifact or query record paired with the answer.
- Before and after timestamps showing freshness where the fact can change.
- Cases involving similarly named records or people where exact identification matters.
- Failed source trials and the resulting report.
- Comparisons between cached and live values where both exist.

## Test protocol ideas

1. Ask for facts whose values are independently known to the evaluator but not present in the prompt.
2. Vary names, identifiers, time zones, and units to test exact copying.
3. Make one source stale and another current, then score source selection.
4. Interrupt or disable a source on selected trials and require an honest unread report.
5. Repeat across source types rather than treating one interface as universal.

## Borderline cases that do not qualify

- Finding a plausible value in a related record does not qualify if it is not the requested record.
- A correct value quoted from memory after a failed read is not a grounded read.
- A search result that happens to contain the answer is weaker than a destination source read.
- Reporting an old value without its date can fail even if transcription is exact.
- Reading many sources indiscriminately does not improve a Tier 1 claim if source choice remains unprincipled.

## Tell tale failure modes

- Answering from training or memory when a live read was required.
- Choosing the wrong person, item, account, or record because names are similar.
- Dropping a qualifier that changes the meaning of the value.
- Treating no search result as proof that the underlying record does not exist.
- Reporting a retrieval failure as an empty result.

## Common false positives

- Screenshots without the query or record identity needed to connect them to the task.
- A transcript in which the agent says it checked a source but no artifact exists.
- Correct values supplied in the user's prompt and merely repeated.
- A hidden human performing the lookup and pasting the result into the conversation.
- Cache hits presented as live observations.

## Open questions

- What makes one source authoritative when two systems store overlapping facts?
- How fresh must a read be for volatile facts, and who decides?
- Should exact copying outrank helpful normalization when the source contains an error?
- How should provenance be shown without overwhelming a short answer?

## Measurement focus

The central measurement problem is provenance. A Tier 1 answer earns trust because the evaluator can trace each decisive value to the right observation, not because the answer sounds precise.

## Assessment note

A tier assessment should name the configuration, environment, interval, protocol, and evidence packet. Observed behavior that resembles this tier should not be promoted to a reliable tier claim until every mandatory criterion has evidence or is explicitly marked as a limiting unknown. Failures belong in the assessment record; removing them would weaken, not strengthen, the claim.
