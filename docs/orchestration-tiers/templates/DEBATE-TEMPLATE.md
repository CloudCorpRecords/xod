# Debate Template

Open a debate when a proposition remains disputed after the participants inspect the same evidence, or when a rubric term supports materially different verdicts.

Create a directory named `DELIB-NNNN` under `deliberations/`. Place the opening note, response notes, and synthesis in that directory and link them in `deliberations/INDEX.md`.

```markdown
# DELIB-<NNNN>: <Question in plain language>

- **Opened:** <YYYY-MM-DD>
- **Opened by:** <contributor>
- **Status:** <open, evidence requested, synthesis proposed, converged, split, or quiet>
- **Claims affected:** <CLM identifiers>
- **Assessments affected:** <paths or NONE>
- **Rubric sections:** <model and tier files>

## Question

<State one question. Avoid bundling several propositions into a single debate.>

## Proposition A

<State the first position fairly, in terms its supporter would recognize.>

## Proposition B

<State the competing position fairly, in terms its supporter would recognize.>

<Add Proposition C only when a genuinely distinct third interpretation exists.>

## Agreed facts

- <Fact and evidence link>
- <Fact and evidence link>

## Disputed classifications

| Dispute | Position A | Position B | Why it matters |
| --- | --- | --- | --- |
| <evidence class, gate, environment, threshold, or definition> | <reading> | <reading> | <effect on verdict> |

## Evidence currently available

| Evidence | Class | Supports | Limits |
| --- | --- | --- | --- |
| <artifact> | <A to E> | <proposition or gate> | <limit> |

## Decisive evidence

<Describe the run, artifact, read back, or rubric clarification that could materially resolve the debate. Explain why it would be decisive.>

## Notes in this chain

- DELIB-<NNNN>-01: <title and role>
- DELIB-<NNNN>-02: <title and role>

## Current synthesis

<Link to the synthesis note. Summarize the current state without hiding dissent.>

## Dissent

<Name unresolved positions and link to the notes that hold them.>

## Closure conditions

<State what would allow this debate to converge, split with bounded claims, or close as unresolved.>
```

## Debate rules

1. Argue against the strongest version of the opposing position.
2. Add evidence before repeating a conclusion.
3. Identify whether disagreement concerns facts, evidence classification, rubric meaning, scope, or values.
4. Preserve dissent in the synthesis.
5. Change a verdict only under the rules in `docs/DELIBERATION-PROTOCOL.md`.
6. Do not use debate participation as a vote count for truth.
