# DELIB-0003-01: Operationalizing the audit versus confidentiality knee (CLM-018)

- **Deliberation:** DELIB-0003
- **Date:** 2026-11-04
- **Contributor:** Expansion contributor (Muse session)
- **Role:** proposer
- **Status:** open, answered by DELIB-0003-02, synthesis in DELIB-0003-03
- **Claims addressed:** CLM-018 (primary), CLM-009 (related)
- **Prior notes addressed:** DELIB-0002-02 (the falsifiability objection), DELIB-0002-03 (the narrowing requirement)
- **Assessments addressed:** NONE

## Proposition

The "identifiable knee" in CLM-018 should be operationalized as follows, as the linked narrowing note DELIB-0002 requires before CLM-018 can move above proposed:

> Retain fleet audit content at five graduated levels, L0 to L4 (identifiers only; identifiers plus hashes; plus structured action records; plus redacted payloads; plus full payloads). Score reconstruction fidelity at each level with the PROTOCOLS G5 procedure on the same operations. The knee is the lowest level after which doubling retained content volume yields less than 2 percentage points of additional reconstruction fidelity, sustained across two successive doublings, in a named corpus class.

## Scope

This note defines a measurement. It does not claim the knee exists at any particular level, does not set retention policy for any system, and does not amend the registered wording of CLM-018. If the measurement is adopted, future evidence for or against CLM-018 must be reported in its terms.

## Evidence inspected directly

| Evidence | Class | What it establishes | Limits |
| --- | --- | --- | --- |
| `docs/PROTOCOLS.md` G5 | Repository rubric | The reconstruction scoring procedure the knee is defined over | Says nothing about where fidelity plateaus |
| DELIB-0002-02, objection 1 | Deliberative note | The elastic reading of "identifiable knee" that must be excluded | A methodological objection, not data |
| `tiers/tier-08.md` open questions | Repository rubric | The accountability versus surveillance tension the claim formalizes | Frames the problem; supplies no curve |

## Evidence not inspected

No fleet audit corpus, at any retention level, was inspected. The 2 point threshold and the two doubling rule are conventions proposed for falsifiability, not values derived from data.

## Reasoning

A knee claim becomes falsifiable when three things are fixed in advance: the retention ladder, the fidelity measure, and the plateau rule. The ladder must be graduated by content sensitivity, not merely by volume, because the claim's second axis is confidentiality risk: identifiers and hashes expose little, payloads expose much. Fidelity is already defined by G5 as the proportion of material steps an independent reviewer recovers. The plateau rule (under 2 points per doubling, twice sustained) excludes single step noise while remaining attainable in realistic corpora: five levels give at most four doublings, so the rule can trigger, and can also fail to trigger, which is what makes the claim able to lose.

The exposure axis needs equal discipline. At each level, the study records what sensitive content classes are retained (direct identifiers, message content, location, third party names) as a checklist, not a score. CLM-018 predicts fidelity flattens while the exposure checklist keeps growing. A study in which both flatten together, or both grow together, refutes the trade off shape even if a knee is found.

## Counterarguments considered

The threshold values are arbitrary in the way all such conventions are arbitrary. The defense is not that 2 points is natural, but that a predeclared arbitrary threshold beats a post hoc visual knee, and the convention can itself be revised by a later note with reasons. A second counterargument, that five levels are too coarse to locate a knee, is accepted as a limit: the procedure locates the knee to within one level, which is sufficient for retention decisions and for the claim's review condition.

## Uncertainty

It is unknown whether reconstruction fidelity is monotone in retention level. Reviewers may exploit ordering cues that richer trails accidentally remove. Non monotone results would not refute CLM-018 directly; they would indict the ladder design, and the protocol requires reporting them rather than smoothing them.

## Conclusion

Adopt the operationalization as the linked narrowing note for CLM-018. The claim remains proposed.

## Requested next step

A skeptical review of the ladder and threshold choices, particularly whether the exposure axis is measurable enough to carry the claim's second half (DELIB-0003-02).
