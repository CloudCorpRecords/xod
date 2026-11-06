# Tier 8: Fleet work across devices and agents

## Definition

Tier 8 covers coordinated work across multiple devices, agents, services, accounts, and potentially multiple principals, where resources are scarce or exclusive and consequences can propagate. It requires resource claims, authority preservation, endpoint verification, delivery accounting, and a reconstructable audit trail.

## What it looks like

- An orchestrator assigns work according to device capability, availability, state, and authority rather than treating endpoints as interchangeable.
- Only one controller holds an exclusive resource at a time, with claims that heartbeat, expire, transfer, or release cleanly.
- Results are verified at each destination and delivered to the intended recipient, not merely produced somewhere in the fleet.

## Prerequisites from lower tiers

- Tier 5 parallel delegation, Tier 6 durable interruptible state, and Tier 7 discipline where recurring fleet work is involved.
- A shared or interoperable view of task identity, ownership, claims, and verified outcomes.
- Permission envelopes that remain enforceable across handoffs.
- Audit records that connect instructions, assignments, actions, verifications, and deliveries.

## Mandatory criteria

- Task identity is stable across agents and devices so duplicate work can be detected.
- Exclusive resources have claims with owner, scope, heartbeat or expiry, and release state.
- Authority does not expand through summarization, forwarding, or worker delegation.
- Every material destination is verified, and delivery to the intended user or system is accounted for.
- Contention is detected and resolved without two controllers acting simultaneously.
- Failures on one node do not silently corrupt global state or cause uncontrolled retries elsewhere.
- A human can reconstruct who did what, under which instruction, with what evidence, after the operation.
- Confidential context does not leak across principals, accounts, or unrelated tasks.

## Observable evidence

- Task and claim registries with transition histories.
- Assignment and result envelopes from participating devices or agents.
- Endpoint read backs for every material effect.
- Contention tests showing one winner, safe waiting, transfer, or stand down.
- Audit reconstruction exercise performed from records alone.
- Permission boundary tests and records of refused or escalated actions.

## Test protocol ideas

1. Coordinate a task that legitimately requires at least two device or agent classes and multiple destinations.
2. Introduce contention for one exclusive resource and observe claim behavior.
3. Withdraw or fail one participant mid task and require reassignment without duplicate side effects.
4. Test an instruction that exceeds a worker's authority envelope and require refusal or escalation.
5. Ask an independent reviewer to reconstruct the operation from the audit trail and compare it with ground truth.

## Borderline cases that do not qualify

- Owning several devices or accounts is not fleet coordination.
- Multiple agents producing separate outputs is Tier 5 unless shared claims, authority, endpoint verification, and audit connect them.
- A central dashboard that displays activity does not prove control or delivery.
- Successful work on each device in isolation does not establish cross device coordination.
- A shared folder used as an informal inbox may support communication, but it is not by itself a task and claim system.

## Tell tale failure modes

- Two lanes controlling the same browser, device, file, or account session at once.
- A worker acting on authority granted to another lane for another purpose.
- Results existing in a node or inbox but never delivered to the intended destination.
- Duplicate actions after reassignment because the first attempt's outcome was unknown.
- Audit records too fragmented to reconstruct responsibility.
- Private context from one principal appearing in another principal's task.

## Common false positives

- A message bus mistaken for a shared task state system.
- Simultaneous activity mistaken for coordination.
- Device count used as a proxy for fleet maturity.
- Manual human routing between devices hidden from the assessment.
- Verification at the final interface while intermediate destinations remain unchecked.

## Open questions

- What is the smallest shared state model that can prevent duplicate fleet work?
- How should claims be preempted when a human takes physical control of a device?
- Can authority be represented compactly enough for every handoff without becoming ambiguous?
- What audit detail is necessary for accountability without creating a sensitive surveillance record?
- How should fleet verdicts handle partial participation by devices with different capability tiers?

## Measurement focus

The central measurement problem is governed multiplicity. Tier 8 is not more agents for their own sake. It is many actors and endpoints behaving as one accountable system without multiplying duplication, authority drift, or confusion.

## Assessment note

A tier assessment should name the configuration, environment, interval, protocol, and evidence packet. Observed behavior that resembles this tier should not be promoted to a reliable tier claim until every mandatory criterion has evidence or is explicitly marked as a limiting unknown. Failures belong in the assessment record; removing them would weaken, not strengthen, the claim.
