# DELIB-0002-02: Skeptical review of registering CLM-015 to CLM-020

- **Deliberation:** DELIB-0002
- **Date:** 2026-11-04
- **Contributor:** Expansion contributor (Muse session), writing as skeptic per protocol
- **Role:** skeptic
- **Status:** answered by synthesis in DELIB-0002-03
- **Claims addressed:** CLM-015, CLM-016, CLM-017, CLM-018, CLM-019, CLM-020
- **Prior notes addressed:** DELIB-0002-01
- **Assessments addressed:** NONE

## Proposition

Registration is acceptable only for statements that are narrow enough to be wrong today. Reviewed against that bar, CLM-018 as worded is too elastic to register without a narrowing note, and CLM-016 carries an operational contamination risk the proposal understates. The remaining four are registrable as proposed.

## Scope

This review examines falsifiability and registry hygiene. It does not evaluate the truth of any statement and proposes no product direction.

## Evidence inspected directly

| Evidence | Class | What it establishes | Limits |
| --- | --- | --- | --- |
| DELIB-0002-01 | Deliberative note | The registration rationale | Concedes the dilution objection |
| `docs/PROPOSED-CLAIMS.md` | Contributor draft | The six statements and review conditions | Same author as the proposal; not independent |
| `docs/OPEN-QUESTIONS.md` OQ-11, OQ-14, OQ-22 | Repository rubric | The open questions each claim advances | Shows the questions are live, not that the answers hold |

## Evidence not inspected

No empirical study bearing on any statement was inspected. In particular, no canary trial (CLM-016) and no reconstruction accuracy curve (CLM-018) exists in this repository.

## Reasoning

### 1. CLM-018's knee is unfalsifiable as worded

"An identifiable knee that varies by domain consequence" can absorb any observed curve: wherever accuracy plateaus, the knee is declared to be. To be wrong, the claim needs a predeclared measurement: for example, reconstruction accuracy gains of less than 2 percentage points per doubling of retained content, sustained across two doublings, in at least one representative fleet corpus. Without such an operationalization, the review condition ("a study reporting both axes") could be satisfied by a study that merely draws two curves and gestures at their crossing. The claim should register with its statement intact (history must not be rewritten) but with a linked narrowing note required before any status above proposed is considered.

### 2. CLM-016's canary can contaminate its own test

A fabrication disclosed in a controlled channel may propagate through ordinary human repetition rather than through the record path under test. The draft's refutation condition (control canaries also reappearing) partially covers this, but the protocol implication belongs in the claim's file now: canaries must be contentless with respect to real persons and decisions, sealed with timestamps before disclosure, and never reused. PROTOCOLS should carry the procedure so the first field use does not improvise it.

### 3. Dilution is a citation problem, not a storage problem

The proposal's mitigation is correct but incomplete. Registries are quoted in fragments. The synthesis should add the practical rule: any external quotation of a claim from this registry must carry its status word, and assessments citing CLM-015 to CLM-020 must note the proposed status inline. That is a documentation convention, cheap to state now.

### 4. The remaining four pass the falsifiability bar

CLM-015 names three separately scoreable dimensions (timing, payload, routing). CLM-017 names its refutation (regressions without classifiable antecedent change). CLM-019 names its refutation (a failure class only original state prevents). CLM-020 names its test (dual scored fleet packets against incidents). Each can lose. Each may register.

## Counterarguments considered

The proposer could answer that narrowing CLM-018 now edits a statement before history exists. The skeptic agrees the statement must stand verbatim; the remedy is a linked narrowing note governing future status changes, which is how this repository already handles qualification (see CLM-003's counterevidence field). No silent edit is required.

## Uncertainty

Whether inter evaluator scoring of escalation quality (CLM-015) can reach the kappa levels METRICS section 6 treats as determinate is unknown. That uncertainty limits confidence, not registrability.

## Conclusion

Register CLM-015, CLM-017, CLM-019, CLM-020 as proposed without qualification. Register CLM-016 with the canary handling procedure added to PROTOCOLS. Register CLM-018 with a linked narrowing requirement before any status change.

## Requested next step

A synthesis adopting or rejecting these three qualifications, with the registry updated in the same change (DELIB-0002-03).
