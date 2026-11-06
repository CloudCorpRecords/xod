# Assessments

This directory contains dated assessments of particular system configurations in particular environments. An assessment is a bounded historical judgment, not a permanent ranking and not a product plan.

## Reading an assessment

Read the assessment basis before the verdicts. It tells you what snapshot, interval, configuration, and evidence class support the document. If the underlying raw evidence packet is not in this repository, the assessment should say so and should avoid stronger language than the snapshot can carry.

Verdicts follow `docs/MODEL.md`:

- PROVEN
- SUPPORTED
- MIXED
- UNSUPPORTED
- REFUTED
- UNKNOWN

Confidence is reported separately. A high level summary such as "reliable through Tier N" is valid only in the gated sense defined by the model. Upper tier observations that do not alter the reliable tier belong in their own section.

## Adding an assessment

1. Copy `templates/ASSESSMENT-TEMPLATE.md`.
2. Name the file with a system slug and date.
3. State the exact snapshot or evidence packet.
4. Assess tiers in ascending order.
5. Mark untested gates UNKNOWN.
6. Include failures, interventions, contradictions, and unknowns.
7. Link any deliberation that interprets disputed evidence.
8. Do not update an old assessment silently after a material change. Add a new dated assessment or a clearly linked revision note.

## Seed assessments

- `tree-2026-11-04.md`: a snapshot based assessment of an agentic operations system whose lower and middle tiers are described as an everyday operating pattern, with explicit limits at Tiers 6 through 8.
- `xod-phase1-2026-11-04.md`: a dated assessment of one orchestrator build during a Phase 1 test count, based only on the numerical and qualitative snapshot recorded in the file.

Neither seed assessment should be quoted without its date and evidence limits.
