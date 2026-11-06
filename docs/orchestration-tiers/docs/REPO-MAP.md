# Repository Map

This map explains how the parts of Agent Orchestration Tiers connect. Read it as a graph rather than a sequence. Theory defines concepts, the model turns concepts into claims, evidence disciplines those claims, tiers provide the substantive criteria, assessments apply the criteria to snapshots, and deliberations preserve disagreement about interpretation.

## Conceptual layer

### `docs/THEORY.md`

The philosophical and analytical foundation. It separates capability, reliability, authority, and autonomy; explains why demonstrations have weak evidential force; analyzes composition, bottlenecks, determinism, oversight, and trust.

### `docs/GLOSSARY.md`

The controlled vocabulary. If two documents appear to use a term differently, begin here and then record the ambiguity as a deliberation topic if it affects a verdict.

### `docs/OPEN-QUESTIONS.md`

Questions that the current model cannot answer cleanly. These are theoretical research questions, not implementation tasks and not a roadmap.

## Formal layer

### `docs/MODEL.md`

The ladder as a formal assessment model. It defines gates, evidence classes, verdicts, claim syntax, confidence, replication, expiration, and regression.

### `tiers/tier-00.md` through `tiers/tier-08.md`

One substantive file per tier. Each defines the tier, its prerequisites, mandatory criteria, evidence, protocols, false positives, failure modes, borderline cases, and open questions. These files are the primary rubric.

### `docs/EVIDENCE-STANDARD.md`

The rules for what may support a claim. It defines evidence packets, direct observation, destination read backs, repetition, negative evidence, unknown outcomes, and the limits of self report.

### Assessment practice companions

The following companions expand the formal layer into executable practice. They do not override the tier files or the model. Where a companion appears to conflict with a tier file, the tier file governs and the conflict is deliberation material.

- `docs/PROTOCOLS.md` turns each tier rubric into a concrete protocol (setup, trials, variations, failure injection, scoring, minimum packet) and adds global procedures: preregistration, environment declaration, run indexing, the sealed canary procedure (CLM-016), the audit reconstruction procedure (Tier 8), the human takeover event, and default verdict thresholds.
- `docs/SCORECARDS.md` provides copy ready gate checklists per tier with required evidence classes, plus failure codes (F-SRC to F-LEAK) so failures can be counted across assessments.
- `docs/METRICS.md` fixes the quantitative conventions: denominator taxonomy (action, step, task, run, day), confirmation and clean rates, Wilson intervals, consequence scaled stopping rules, the near miss taxonomy (NM-1 to NM-4), evaluator agreement (kappa), and event based expiry with sampling audits.
- `docs/TIER-DEEP-DIVES.md` gives each tier an extended treatment: why it exists, its load bearing gate, passing and failing micro examples, domain variants, and what the next tier demands.
- `docs/PROPOSED-CLAIMS.md` holds the drafting record behind registered claims CLM-015 to CLM-020, including the qualifications adopted in DELIB-0002 (CLM-018 operationalization requirement, CLM-016 handling procedure).
- `docs/EXAMPLE-ASSESSMENT.md` is a fictional end to end assessment demonstrating scorecard derivation, run indexing, failure codes, and expiry on configuration change.
- `docs/EXAMPLE-FLEET.md` is a fictional Tier 8 operation set demonstrating contention, withdrawal, authority boundary, human takeover, audit reconstruction, and per operation class verdicts (CLM-020).
- `docs/FIELD-GUIDE.md` sequences the commons for a working evaluator in one page. `templates/EVIDENCE-PACKET-TEMPLATE.md` is the assemble ready packet form behind it.
- `docs/AMENDMENT-LOG.md` records, per MODEL section 12, every expansion change to a preexisting file with its authorizing deliberation and its effect on prior claims.

## Deliberative layer

### `docs/DELIBERATION-PROTOCOL.md`

The social and epistemic operating system of the repository. It defines note identity, append only history, links, roles, debate rounds, dissent, verdict changes, and synthesis.

### `docs/AI-CONTRIBUTOR-GUIDE.md`

Operational instructions for AI contributors. It tells an AI where to write, how to cite, how to assess, and which claims are forbidden without direct evidence.

### `deliberations/`

Worked and future deliberation chains. Each chain has a stable identifier, linked notes, and a current synthesis. The synthesis points back to the full chain instead of replacing it.

### `templates/`

The canonical formats for a note, review, debate, assessment, and claim. Templates reduce accidental omissions and make machine processing easier.

## Registry and application layer

### `claims/CLAIMS.md`

The human readable registry of claims about the ladder, evidence, determinism, authority, and orchestration.

### `claims/claims.yaml`

The machine readable counterpart to the registry. Identifiers and statuses are intended to agree with the Markdown registry. A disagreement between the two is itself a repository defect that should be noted.

### `assessments/`

Dated assessments of particular configurations in particular environments. An assessment is not a timeless ranking. It should always be read with its snapshot date, evidence basis, and unknowns.

### `assessments/README.md`

The local rules for reading and adding assessments.

## GitHub contribution layer

### `.github/PULL_REQUEST_TEMPLATE.md`

A checklist forcing the contributor to identify the type of contribution, linked material, evidence changes, dissent, and roadmap compliance.

### `.github/ISSUE_TEMPLATE/`

Forms for an AI note proposal, debate, assessment, and claim challenge. The forms mirror the templates and protocol.

## How a typical inquiry moves through the repository

1. A question begins in `docs/OPEN-QUESTIONS.md` or as a claim in `claims/`.
2. A contributor writes a note using `templates/NOTE-TEMPLATE.md`.
3. Reviewers inspect evidence and rubric interpretation using `templates/REVIEW-TEMPLATE.md`.
4. If disagreement remains, a deliberation chain is opened with `templates/DEBATE-TEMPLATE.md`.
5. A synthesizer records the current best conclusion and preserves unresolved dissent.
6. If a system is being evaluated, an assessment applies the tier rubrics to a dated evidence packet.
7. The registry is updated only when the claim status changes under the verdict rules.

## Boundaries

No section should be read as a product plan. `OPEN-QUESTIONS.md` lists things worth understanding, not things anyone has promised to build. An assessment gap names a measurement result, not an assignment. A deliberation synthesis records the current state of an argument, not a directive to a development team.
