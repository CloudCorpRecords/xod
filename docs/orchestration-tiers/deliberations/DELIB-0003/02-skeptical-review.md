# DELIB-0003-02: Skeptical review of the CLM-018 operationalization

- **Deliberation:** DELIB-0003
- **Date:** 2026-11-04
- **Contributor:** Expansion contributor (Muse session), writing as skeptic per protocol
- **Role:** skeptic
- **Status:** answered by synthesis in DELIB-0003-03
- **Claims addressed:** CLM-018
- **Prior notes addressed:** DELIB-0003-01
- **Assessments addressed:** NONE

## Proposition

The operationalization is adoptable with three amendments: the exposure axis must be scored, not only checklisted; the corpus class must be named before scoring, not after; and the plateau rule must handle non monotone fidelity explicitly. Without the first amendment, the study can find the knee while saying nothing quantitative about confidentiality, which is half the claim.

## Scope

This review examines measurability only. It takes no position on where the knee lies or whether it exists.

## Evidence inspected directly

| Evidence | Class | What it establishes | Limits |
| --- | --- | --- | --- |
| DELIB-0003-01 | Deliberative note | Ladder, fidelity measure, plateau rule | Exposure axis left as a checklist |
| `docs/PROTOCOLS.md` G5 step 5 | Repository rubric | The exposure note already required beside fidelity | Qualitative in current form |
| `docs/METRICS.md` section 6 | Repository rubric | The repository's own standard for when judgment counts as measured | Sets the bar this proposal must meet |

## Evidence not inspected

No retention corpus at any level. The objection is structural: as drafted, one axis is a number and the other is a list, and a trade off claim needs both axes in commensurable form before its shape can be confirmed or refuted.

## Reasoning

### 1. Score the exposure axis

Replace the checklist with a retained sensitivity inventory: counts, per operation, of retained items in four classes (direct identifiers of principals, content payloads, third party identifiers, precise location or timing traces), each weighted by a predeclared severity weight. The weights are arbitrary in the same defensible way as the 2 point threshold: predeclared, published with the study, revisable by note. With both axes numeric, the claim's prediction becomes a shape (fidelity slope near zero while exposure slope stays positive) that data can violate.

### 2. Name the corpus class in advance

"Corpus class" currently floats. If the class is chosen after seeing the curves, any knee can be rescued by reclassifying the corpus. The study registration must name the class (for example, multi device household operations, or cross account work coordination) and its operation count before scoring, and results generalize only within the named class until replicated outside it. This matches the claim's own wording ("varies by domain consequence") and converts that phrase from an escape hatch into a replication requirement.

### 3. Non monotone fidelity needs a rule, not a shrug

The proposal notes non monotonicity "would indict the ladder." Indictment is not a scoring rule. Adopt this: if fidelity at any higher level falls below a lower level by more than the plateau threshold, the study reports the ladder as defective for that corpus, the knee is recorded as NOT LOCATED, and CLM-018 gains no support from the study. That preserves falsifiability in the direction the proposer left soft.

## Counterarguments considered

Scoring exposure with weights may manufacture false precision. The risk is real and smaller than the alternative: an unscored axis invites every reader to project their own risk intuition onto the same curve, which is precisely the eloquence driven verdict movement the model prohibits. Predeclared weights at least make the projection visible and disputable.

## Uncertainty

Whether independent reviewers can apply the four sensitivity classes consistently is an inter evaluator question (METRICS section 6 applies to the exposure inventory exactly as it applies to gates). A pilot double scoring of the inventory should accompany the first study.

## Conclusion

Adopt the operationalization with the three amendments. CLM-018 remains proposed; its review condition is now executable.

## Requested next step

Synthesis recording the adopted text as the linked narrowing note for CLM-018 (DELIB-0003-03).
