# Tier 0: Reflex reply

## Definition

Tier 0 covers an immediate reply that requires no live retrieval, external action, delegation, or durable state. The system interprets a simple prompt, uses information already present in the conversation or stable general knowledge, and answers directly.

## What it looks like

- A date, definition, arithmetic result, translation, or short conversational continuation is returned without ceremony.
- The response is proportionate: a one line question receives a concise answer unless ambiguity requires clarification.
- The system recognizes when a superficially simple request actually depends on current or private information and escalates the task to a grounded read instead of guessing.

## Prerequisites from lower tiers

- A functioning language interface and enough conversational context to identify the intended question.
- No lower tier exists; this is the base layer for every higher claim.
- Basic instruction following and refusal discipline are assumed, but they are not substitutes for factual accuracy.

## Mandatory criteria

- The answer addresses the question actually asked rather than a nearby common question.
- Specific names, dates, quantities, and quotations are not invented.
- No tool, worker, schedule, or persistent process is invoked unnecessarily.
- Uncertainty or ambiguity is disclosed when it materially affects the answer.
- The reply does not claim to have checked a source that was not checked.
- Response latency and format are appropriate to a reflex task.

## Observable evidence

- Prompt and response transcripts across varied simple tasks.
- Cases that distinguish stable knowledge from facts requiring a live read.
- Negative controls containing false premises, ambiguous references, or requests for invented detail.
- Repeated trials showing whether the same answer remains stable when wording changes superficially.
- Records of unnecessary escalation or unnecessary tool invocation where observable.

## Test protocol ideas

1. Present a balanced set of direct questions with known answers and no hidden dependency on current state.
2. Include a minority of traps whose correct behavior is clarification or escalation to Tier 1.
3. Score factual exactness, proportionality, and uncertainty separately.
4. Repeat with paraphrases to separate robust understanding from memorized phrasing.
5. Inspect whether the system narrates nonexistent verification or fabricates citations.

## Borderline cases that do not qualify

- A polished answer based on a lucky guess does not qualify as reliable Tier 0.
- A response that retrieves a live file is successful work, but it belongs to Tier 1 rather than proving reflex restraint.
- A long generic essay in response to a simple prompt may contain the answer while failing proportionality.
- A refusal to answer any uncertain question is not the same as calibrated uncertainty.
- A correct answer accompanied by an invented source is a grounding failure.

## Tell tale failure modes

- Hallucinated specifics presented with conversational confidence.
- A whole workflow launched to answer a trivial question.
- Failure to notice that a pronoun or date reference is ambiguous.
- Answering a current status question from stale context.
- Continuing a false premise instead of correcting it when the correction is material.

## Common false positives

- Benchmark familiarity mistaken for general response reliability.
- A fluent style that makes weak factual control appear strong.
- One curated answer used to imply broad Tier 0 competence.
- Tool traces hidden from the evaluator, making a Tier 1 task look like a reflex answer.
- Human editing of the response before display.

## Open questions

- How should common knowledge be bounded when facts change over time?
- What level of uncertainty should trigger clarification rather than a best effort answer?
- Can proportionality be measured without penalizing useful context?
- How should evaluators treat a correct answer reached through visibly faulty reasoning?

## Measurement focus

The central measurement problem is restraint. Tier 0 is not merely speed; it is the ability to solve the small problem at the small scale while recognizing when the problem is not small.

## Assessment note

A tier assessment should name the configuration, environment, interval, protocol, and evidence packet. Observed behavior that resembles this tier should not be promoted to a reliable tier claim until every mandatory criterion has evidence or is explicitly marked as a limiting unknown. Failures belong in the assessment record; removing them would weaken, not strengthen, the claim.
