# Tier 3: Multi-step sequence, one agent

## Definition

Tier 3 covers a dependent chain performed by one coordinating agent: gather, transform, decide, act, verify, and report. Its distinctive property is state discipline across steps, especially when a middle step fails.

## What it looks like

- A visible or reconstructable plan orders the steps and their dependencies.
- Each step is marked complete only after its own outcome is established.
- The final output reflects the actual path taken, including substitutions, blocked steps, and partial completion.

## Prerequisites from lower tiers

- Tier 1 grounding for inputs and Tier 2 verification for side effecting steps.
- A representation of task state that persists at least for the duration of the sequence.
- Rules for stopping, branching, or escalating when a prerequisite fails.

## Mandatory criteria

- The plan covers the requested outcome and respects dependency order.
- Inputs to each step are the verified outputs of prior steps, not assumptions.
- A failed middle step stops or redirects the chain rather than being silently skipped.
- Side effects are verified individually and in aggregate.
- Partial completion is reported with completed, failed, and unattempted components separated.
- The final self check compares delivery against the original request.

## Observable evidence

- Plan and step state artifacts.
- Inputs and outputs for each material transition.
- Logs showing branch or stop behavior after an induced failure.
- Destination read backs for all side effecting steps.
- Comparison of the final report with the original task specification.

## Test protocol ideas

1. Use sequences with at least one transformation and one verifiable action.
2. Induce a missing input or failed tool call in the middle and observe whether downstream claims change.
3. Vary step order and naming to test dependency reasoning rather than script recall.
4. Ask for a status report mid sequence and compare it with artifacts.
5. Repeat with a longer chain to identify where bookkeeping begins to degrade.

## Borderline cases that do not qualify

- Several unrelated Tier 2 actions performed in a row do not automatically form a Tier 3 sequence.
- A final artifact that looks correct cannot prove state discipline if intermediate failures were hidden.
- A rigid script followed successfully may qualify for a narrow sequence, but not for general Tier 3 if it cannot handle a branch.
- A plan generated after execution is a narrative, not evidence of planning.
- Partial output described as complete because most steps succeeded fails the tier.

## Tell tale failure modes

- Steps marked done because they were attempted.
- A missing middle result replaced by a plausible assumption.
- Downstream work built on a failed prerequisite.
- The final answer omitting a requested component without notice.
- State confusion between similar items or stages.

## Common false positives

- Evaluator supplied checklists that perform the coordination for the agent.
- A chain in which every tool is unusually reliable and no branch is tested.
- Final output inspection used as a substitute for transition evidence.
- Long transcripts mistaken for state management.
- Human prompts during the run that quietly repair each transition.

## Open questions

- How much plan structure should be externalized for short sequences?
- When should a sequence branch autonomously and when should it stop for a decision?
- Can a system receive Tier 3 credit for recovery if the original chain design was brittle?
- What is the best representation of partial completion for dependent work?

## Measurement focus

The central measurement problem is propagation. Tier 3 asks whether truth and state survive movement through a chain, especially when the chain does not go as planned.

## Assessment note

A tier assessment should name the configuration, environment, interval, protocol, and evidence packet. Observed behavior that resembles this tier should not be promoted to a reliable tier claim until every mandatory criterion has evidence or is explicitly marked as a limiting unknown. Failures belong in the assessment record; removing them would weaken, not strengthen, the claim.
