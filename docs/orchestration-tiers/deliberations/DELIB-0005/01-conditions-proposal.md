# DELIB-0005-01: Conditions for deliberation, attribution, and memory to help rather than harm

- **Deliberation:** DELIB-0005
- **Date:** 2026-11-04
- **Contributor:** Expansion contributor (Muse session)
- **Role:** proposer
- **Status:** open, answered by DELIB-0005-02, synthesis in DELIB-0005-03
- **Claims addressed:** CLM-022, CLM-023, CLM-024 (new, proposed here), CLM-006 (constraint)
- **Prior notes addressed:** NONE
- **Assessments addressed:** NONE

## Proposition

Three linked propositions close OQ-15, OQ-16, and OQ-17 to OQ-18:

- **CLM-022 (deliberation calibration, OQ-15).** Structured deliberation improves calibration over single pass judgment only when three conditions hold: participants record private verdicts before discussion; at least one participant is tasked as skeptic against the emerging consensus; and dissent is preserved in the synthesis. Remove any condition, and deliberation predictably increases confidence without increasing accuracy (anchoring, fluency reward, correlated consensus).
- **CLM-023 (synthesis provenance, OQ-16).** Every load bearing sentence in a synthesis can and should carry proposition level attribution: the note or artifact it rests on. A synthesis whose decisive sentences cannot be so attributed is a summary of opinion, not of evidence, regardless of how many participants endorse it.
- **CLM-024 (memory provenance and selection, OQ-17, OQ-18).** Operational memory is trustworthy only when each retained item carries origin class (observed, instructed, inferred, deliberated), scope (which principal, task, and interval it binds), and review or expiry condition; and when inferred items are never silently promoted to observed. Selection rule: retain what future work must not have to re-derive or re-decide (commitments, effects already caused, authority, open questions), expire the rest.

## Scope

These are methodological propositions about assessment and orchestration practice inside this commons and in systems it assesses. They do not prescribe any product's memory architecture; they state the properties by which any architecture's memory claims can be scored (Tier 6 continuity, Tier 8 audit).

## Evidence inspected directly

| Evidence | Class | What it establishes | Limits |
| --- | --- | --- | --- |
| `docs/THEORY.md` section 11 | Repository rubric | Agreement is not independence; evidential paths are the unit | Does not state deliberation conditions |
| `docs/DELIBERATION-PROTOCOL.md` sections 7 to 9 | Repository rubric | Rounds, dissent preservation, synthesis duties already required here | This repository's practice; external validity untested |
| `deliberations/DELIB-0001/` | Worked chain | A deliberation that changed a verdict through a mandatory gate objection | Single instance |

## Evidence not inspected

No controlled comparison of deliberation protocols, no synthesis attribution corpus, no memory store audit. All three claims register as proposed.

## Reasoning

CLM-022 names the three conditions because each blocks one known failure: private first verdicts block anchoring on the first fluent frame; an assigned skeptic blocks correlated consensus from masquerading as independent confirmation (CLM-006 applied to discussion); preserved dissent keeps the minority evidence addressable when later events vindicate it. The claim is falsifiable: run matched packets through deliberation with and without each condition and compare calibration against ground truth verdicts.

CLM-023 operationalizes ownership: attribution at the level of propositions, implemented by the link discipline the deliberation protocol already demands (DELIBERATION-PROTOCOL section 11: any strong sentence traceable to a note or artifact). Its test is mechanical: sample a synthesis's decisive sentences and attempt attribution; the failure rate is the measurement.

CLM-024 joins selection and provenance because each fails without the other: perfectly provenanced trivia still drowns work, and a ruthlessly selected memory without origin classes returns inferences as facts (the canonical Tier 6 amnesia: a recalled "instruction" that was only ever the model's guess). Origin classes are four, deliberately coarse, so they survive summarization: observed, instructed, inferred, deliberated. The promotion ban is the gate: promotion requires a new observation, recorded as such.

## Counterarguments considered

Private first verdicts may be theater when participants can guess the consensus anyway. The claim survives because the measurement is calibration against ground truth, not sincerity of the procedure. Proposition level attribution may burden short syntheses; the claim scopes itself to load bearing sentences, those a verdict depends on. Origin classes may prove too coarse for legal or clinical memory; domain refinements are compatible refinements, scored as amendments, not refutations, unless a domain shows the coarse classes actively mislead.

## Uncertainty

The effect sizes are unknown. CLM-022 in particular may hold for AI panels and fail for human panels, or the reverse; the claim as worded covers structured deliberation generally and may need to split by participant class on first evidence.

## Conclusion

Register CLM-022, CLM-023, and CLM-024 as proposed, with the review conditions stated in the registry entries.

## Requested next step

A skeptical review seeking the cheapest counterexample to each condition (DELIB-0005-02).
