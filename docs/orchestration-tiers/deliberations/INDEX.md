# Deliberations Index

Deliberations are append only chains of linked notes. Each chain addresses one question, preserves disagreement, and ends for the present in a synthesis that links the notes supporting, challenging, and qualifying its conclusion.

## Chain format

A chain directory contains:

1. an opening or initial position note;
2. one or more review, evidence, or skeptic notes;
3. response notes when new evidence or a correction is introduced;
4. a synthesis note naming the current conclusion, dissent, and open evidence requests.

Files are numbered in chain order. Note identifiers use the deliberation identifier plus the note sequence. A later note must link the earlier notes it answers. No earlier note is edited to make the chain look cleaner.

## Reading a chain

Start with the synthesis for the current state, then follow its links into the chain when a conclusion matters. The synthesis is an index and summary, not a replacement for the evidence or dissent.

## Chains

### DELIB-0001: Does passing a controlled load test prove Tier 6?

- **Status:** Synthesis proposed; open to new Tier 6 protocol evidence.
- **Question:** Whether a controlled load test, by itself, establishes Tier 6 long horizon interruptible capability.
- **Notes:**
  - `DELIB-0001/01-initial-position.md`: A qualified affirmative position that treats load as a central stress test for sustained orchestration.
  - `DELIB-0001/02-skeptical-review.md`: A skeptical review arguing that load evidence omits mandatory interruption, restart, steering, and side effect gates, and that the seed snapshot contains a material freeze.
  - `DELIB-0001/03-synthesis.md`: The current synthesis: a load test is valuable component evidence but is not sufficient proof of Tier 6 as the tier is defined.
- **Claims affected:** CLM-002, CLM-007, CLM-014.
- **Assessments affected:** `assessments/xod-phase1-2026-11-04.md`.

### DELIB-0002: Should CLM-015 to CLM-020 be registered as proposed claims?

- **Status:** Converged. All six registered as proposed with two qualifications (see synthesis).
- **Question:** Whether six extension statements (escalation quality, canary leak detection, event based expiry, audit versus confidentiality, behavioral continuity, per operation class fleet verdicts) belong in the registry before any supporting evidence exists.
- **Notes:**
  - `DELIB-0002/01-registration-proposal.md`: Proposes registering all six as proposed, arguing the registry exists to track falsifiable statements before evidence arrives.
  - `DELIB-0002/02-skeptical-review.md`: Finds CLM-018 too elastic as worded and CLM-016 operationally risky without a handling procedure; accepts the other four.
  - `DELIB-0002/03-synthesis.md`: Current synthesis: register all six as proposed; CLM-018 needs an operationalization note before any status change; CLM-016 tests require the sealed canary procedure in `docs/PROTOCOLS.md`.
- **Claims affected:** CLM-015, CLM-016, CLM-017, CLM-018, CLM-019, CLM-020.
- **Assessments affected:** NONE.

### DELIB-0003: How should the CLM-018 audit versus confidentiality knee be measured?

- **Status:** Converged. Operationalization adopted as the linked narrowing note for CLM-018.
- **Question:** What measurement (retention ladder, fidelity rule, exposure scoring) makes the CLM-018 trade off claim falsifiable, as DELIB-0002 required before any status change?
- **Notes:**
  - `DELIB-0003/01-operationalization-proposal.md`: Proposes a five level retention ladder, G5 reconstruction fidelity, and a plateau rule (under 2 points per doubling, twice sustained).
  - `DELIB-0003/02-skeptical-review.md`: Requires a scored exposure axis, corpus class named before scoring, and an explicit non monotonicity rule.
  - `DELIB-0003/03-synthesis.md`: Current synthesis: all amendments adopted; CLM-018 remains proposed, its review condition now executable.
- **Claims affected:** CLM-018 (primary), CLM-009.
- **Assessments affected:** NONE.

### DELIB-0004: How should conflicting principals be ordered in fleet work?

- **Status:** Converged. Precedence rules P1 to P4 adopted with two field defaults; CLM-021 registered as proposed.
- **Question:** Whether fleet instruction conflicts among principals can be decided, or correctly escalated, without authority expansion or confidentiality loss (OQ-13).
- **Notes:**
  - `DELIB-0004/01-precedence-proposal.md`: Proposes the authority ceiling, owner governs, intra principal recency, and existence only deadlock escalation.
  - `DELIB-0004/02-skeptical-review.md`: Finds two undecided shapes: the undeclared shared resource and the stale owner instruction; proposes field defaults.
  - `DELIB-0004/03-synthesis.md`: Current synthesis: rules adopted with the enroller default and the resurfacing requirement; CLM-021 registered; the enroller accountability dissent preserved.
- **Claims affected:** CLM-021 (new), CLM-010.
- **Assessments affected:** `docs/EXAMPLE-FLEET.md` (motivating boundary case).

### DELIB-0005: When do deliberation, synthesis, and memory help rather than harm?

- **Status:** Converged. Three claims registered as proposed, each with its test instrument.
- **Question:** The conditions under which structured deliberation improves calibration (OQ-15), syntheses can be owned at proposition level (OQ-16), and operational memory can be selected and provenance preserving (OQ-17, OQ-18).
- **Notes:**
  - `DELIB-0005/01-conditions-proposal.md`: Proposes the three deliberation conditions, proposition level attribution, and origin classed memory with a selection rule.
  - `DELIB-0005/02-skeptical-review.md`: Requires a calibration metric for CLM-022, a default expiry rule for CLM-024, and the deletion test for CLM-023.
  - `DELIB-0005/03-synthesis.md`: Current synthesis: all instruments adopted; CLM-022 to CLM-024 registered; participant class narrowing preserved as expected first correction.
- **Claims affected:** CLM-022, CLM-023, CLM-024 (new), CLM-006.
- **Assessments affected:** NONE.

### DELIB-0006: Should duration scaling, pass^k, and judge validity enter the measurement layer?

- **Status:** Converged. Three instruments adopted as amendments with abuse guards; no claim created or changed.
- **Question:** Whether METR style time horizons, tau-bench style pass^k, and an MVVP judge validity protocol should be added to METRICS and PROTOCOLS, and under what guards.
- **Notes:**
  - `DELIB-0006/01-instruments-proposal.md`: Proposes the duration axis, all attempts reliability, and judge hygiene as small amendments in existing homes.
  - `DELIB-0006/02-skeptical-review.md`: Requires suite named horizons with a not estimable outcome, screened independence with predeclared k, and an excludable judge standard.
  - `DELIB-0006/03-synthesis.md`: Current synthesis: all guards adopted; METRICS section 2 (pass^k) and section 9 (duration), PROTOCOLS Appendix J; existing assessments unaffected.
- **Claims affected:** NONE.
- **Assessments affected:** NONE (instruments are optional; absence from a packet is a noted limitation, not a defect).

## Opening a new chain

1. Choose the next `DELIB-NNNN` identifier.
2. Create a directory with that identifier.
3. Use `templates/DEBATE-TEMPLATE.md` for the opening frame.
4. Add the chain to this index in the same change.
5. Assign note numbers sequentially as notes are added.
6. Keep the synthesis current by adding a new synthesis note or a clearly versioned synthesis revision when the conclusion changes under the protocol.
