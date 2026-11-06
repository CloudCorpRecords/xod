# Proposed New Claims (for deliberation, not yet registered)

The following claims extend the registry. They are proposed statements narrow enough to be wrong, each with the evidence that would support or refute it. To adopt one, deliberate in a chain, then copy it into `claims/CLAIMS.md` and `claims/claims.yaml` with status `proposed`, and open a deliberation chain if its wording is contested. Until then, these remain drafts and carry no registry weight.

## CLM-015: Escalation quality is measurable independently of escalation rate

- **Statement:** Two systems with identical escalation rates can differ measurably in escalation quality: whether the escalation occurs before an irreversible step, carries the context a decision-maker needs, and is routed to a principal entitled to decide.
- **Type:** theoretical and methodological
- **Status:** proposed
- **Confidence:** MODERATE
- **Related claims:** CLM-011
- **Advances:** OQ-11
- **Evidence that would support:** a protocol scoring escalation timing, payload completeness, and routing correctness across matched tasks, showing stable inter-system discrimination.
- **Evidence that would refute:** scoring attempts in which escalation quality collapses into rate under every rubric tried.
- **Review condition:** inter-evaluator study on identical escalation records.

## CLM-016: A canary statement can detect record leakage that audits cannot

- **Statement:** A fabrication disclosed to exactly one witness in a controlled channel, which later reappears in completed or corrected form, establishes that a record path outside that channel carries statements, and does so with fewer assumptions than log audit alone.
- **Type:** methodological
- **Status:** proposed
- **Confidence:** MODERATE
- **Related claims:** CLM-006, CLM-011
- **Advances:** OQ-14, OQ-15
- **Evidence that would support:** replicated canary trials with sealed control statements never reappearing.
- **Evidence that would refute:** control statements also reappearing (indicating the fabrication entered through an uncontrolled path), or canaries never reappearing even where a leak is independently proven.
- **Review condition:** replication across at least two independent systems with control canaries.

## CLM-017: Claim expiry should be event-based, not calendar-based

- **Statement:** A tier claim's present-tense force ends at the first material configuration event affecting a satisfied gate, not at a fixed date; calendar expiry is only a fallback when event detection is unavailable.
- **Type:** methodological
- **Status:** proposed
- **Confidence:** MODERATE
- **Related claims:** CLM-003, CLM-013
- **Advances:** OQ-05
- **Evidence that would support:** a change taxonomy in which each change class maps to the gates it can affect, validated against observed regressions.
- **Evidence that would refute:** regressions reliably occurring without any classifiable antecedent change, implying calendar re-verification is the only workable rule.
- **Review condition:** a published change-to-gate map with at least one field validation.

## CLM-018: Audit completeness and principal confidentiality trade off measurably

- **Statement:** Beyond a threshold, additional audit detail yields diminishing accountability gains while monotonically increasing confidentiality risk; the trade-off has an identifiable knee that varies by domain consequence.
- **Type:** theoretical
- **Status:** proposed
- **Confidence:** LOW
- **Related claims:** CLM-009
- **Advances:** OQ-22
- **Evidence that would support:** reconstruction exercises showing accuracy plateauing as retained detail grows, paired with exposure analysis of the same records.
- **Evidence that would refute:** reconstruction accuracy continuing to improve linearly with retained detail at negligible risk in representative fleet records.
- **Review condition:** a Tier 8 reconstruction study reporting both axes.

## CLM-019: Behavioral continuity, not storage identity, is the Tier 6 property

- **Statement:** Tier 6 continuity is satisfied when resumed behavior preserves commitments, side-effect boundaries, and status truth, regardless of whether the underlying store is the original state, a transcript reconstruction, or an equivalent re-derivation.
- **Type:** definitional
- **Status:** proposed
- **Confidence:** MODERATE
- **Related claims:** CLM-007, CLM-014
- **Advances:** OQ-19
- **Evidence that would support:** systems passing the full Tier 6 protocol through architecturally different continuity mechanisms.
- **Evidence that would refute:** a failure class that only original-state storage prevents and that matters operationally.
- **Review condition:** two independent Tier 6 packets with differing storage designs.

## CLM-020: Fleet verdicts should be issued per operation class, not per fleet

- **Statement:** A single fleet can simultaneously merit different verdicts for different operation classes (read-only coordination vs. irreversible actuation); a per-fleet verdict hides the distinction that matters for trust.
- **Type:** methodological
- **Status:** proposed
- **Confidence:** MODERATE
- **Related claims:** CLM-003, CLM-009, CLM-010
- **Advances:** OQ-08, OQ-09
- **Evidence that would support:** fleet assessments in which operation-class verdicts diverge and predict field failures better than a single verdict.
- **Evidence that would refute:** field studies showing per-fleet verdicts never mislead because operation classes never diverge in practice.
- **Review condition:** two fleet packets scored both ways and compared against subsequent incidents.

## Filing note

Each proposal names its review condition in advance so adoption is falsifiable. If a deliberation amends a statement, keep the original wording in the chain and register the amended text as a new claim version, per the repository's append-only practice.
