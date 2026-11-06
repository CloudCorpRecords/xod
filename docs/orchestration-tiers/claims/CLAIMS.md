# Claim Registry

This registry records propositions about the orchestration ladder, assessment method, and multi agent coordination. It is not a list of product intentions. A claim's status describes the state of argument and evidence inside this repository, not the reputation of any external system.

Status values are intentionally close to the model verdicts but apply to registry propositions: `supported`, `mixed`, `unsupported`, `refuted`, `unknown`, `proposed`, and `under-deliberation`.

## CLM-001: Capability and reliability are distinct properties

- **Statement:** A system that performs a behavior once has demonstrated capability in that instance, but reliability requires repeated performance with failures, variation, and recovery included in the record.
- **Type:** theoretical
- **Status:** supported
- **Confidence:** HIGH
- **Introduced:** 2026-11-04
- **Evidence:** `docs/THEORY.md` sections 2.1 and 2.2; `docs/MODEL.md` sections 6 and 8.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-002, CLM-003
- **Deliberations:** NONE
- **Review condition:** A formal counterexample showing that a single authentic run can carry the same inferential weight as a repeated record for a reliability claim.

## CLM-002: A single demonstration cannot establish a PROVEN tier

- **Statement:** A single demonstration, however impressive, cannot by itself establish a PROVEN tier verdict because it lacks a denominator, replication, and adequate tests of hidden assistance, selection, and destination effects.
- **Type:** methodological
- **Status:** supported
- **Confidence:** HIGH
- **Introduced:** 2026-11-04
- **Evidence:** `docs/THEORY.md` section 3; `docs/EVIDENCE-STANDARD.md` sections 2 and 3.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-001, CLM-005
- **Deliberations:** DELIB-0001
- **Review condition:** A protocol in which one run contains internally replicated and independently verified sub trials sufficient to meet the model's replication rule.

## CLM-003: Tier attainment is gated, not averaged

- **Statement:** A reliable through Tier N claim requires the mandatory gates at Tier N and below to be satisfied; excellence in one property cannot offset the absence of another mandatory property.
- **Type:** definitional
- **Status:** supported
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `docs/MODEL.md` section 4; each file in `tiers/`.
- **Counterevidence:** The linearity concern recorded in `docs/OPEN-QUESTIONS.md` OQ-08 may justify future branching for specialized systems.
- **Related claims:** CLM-008, CLM-009
- **Deliberations:** NONE
- **Review condition:** Accumulation of specialized cases in which cumulative gating misclassifies the operational property of interest.

## CLM-004: Action success requires destination evidence

- **Statement:** For a side effecting action, a tool response or internal success state is insufficient to claim completion; the intended state must be observed at the destination or through an adequate independent record.
- **Type:** methodological
- **Status:** supported
- **Confidence:** HIGH
- **Introduced:** 2026-11-04
- **Evidence:** `tiers/tier-02.md`; `docs/EVIDENCE-STANDARD.md` sections 2.2 and 6.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-005, CLM-009
- **Deliberations:** NONE
- **Review condition:** A class of action for which destination observation is impossible and an alternative proof mechanism is shown to be equally strong.

## CLM-005: UNKNOWN is a necessary outcome category

- **Statement:** Assessment requires UNKNOWN as a distinct result for attempted work whose outcome cannot be established; converting unknown outcomes into success or failure corrupts reliability measurement and can cause duplicate side effects.
- **Type:** methodological
- **Status:** supported
- **Confidence:** HIGH
- **Introduced:** 2026-11-04
- **Evidence:** `docs/EVIDENCE-STANDARD.md` section 5; `docs/MODEL.md` section 6.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-004
- **Deliberations:** NONE
- **Review condition:** Evidence that a binary success and failure scheme can preserve retry safety and honest coverage without an unknown state.

## CLM-006: Correlated agent agreement is not independent confirmation

- **Statement:** Agreement among agents does not provide independent confirmation when the agents share the decisive source, model assumptions, prompt context, or unverified artifact; independence must be assessed by evidential path, not participant count.
- **Type:** theoretical
- **Status:** supported
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `docs/THEORY.md` section 11; `docs/OPEN-QUESTIONS.md` OQ-14 and OQ-15.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-012
- **Deliberations:** NONE
- **Review condition:** A formal independence metric that shows participant level separation reliably predicts evidential independence in this setting.

## CLM-007: A controlled load test alone does not prove Tier 6

- **Statement:** Passing or nearly passing a controlled load test does not by itself prove Tier 6, because Tier 6 also requires durable state, steering, interruption recovery, side effect accounting, and observed status across a long horizon.
- **Type:** empirical and methodological
- **Status:** under-deliberation
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `tiers/tier-06.md`; `assessments/xod-phase1-2026-11-04.md`.
- **Counterevidence:** A load protocol could be designed as one component of a broader Tier 6 protocol, but that broader design is the point under dispute.
- **Related claims:** CLM-002, CLM-003
- **Deliberations:** DELIB-0001
- **Review condition:** Completion of DELIB-0001 or a Tier 6 protocol that integrates load, interruption, restart, steering, and destination evidence.

## CLM-008: A schedule definition does not prove recurring capability

- **Statement:** The existence of a schedule, trigger, or job definition is not evidence of Tier 7 reliability; observed cycles, outcome records, dedupe behavior, and failure handling across the natural period are required.
- **Type:** methodological
- **Status:** supported
- **Confidence:** HIGH
- **Introduced:** 2026-11-04
- **Evidence:** `tiers/tier-07.md`; `docs/EVIDENCE-STANDARD.md` section 3.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-003
- **Deliberations:** NONE
- **Review condition:** A verified scheduler attestation mechanism that directly proves execution and outcome recording without separate run artifacts.

## CLM-009: Fleet coordination requires shared task identity and resource claims

- **Statement:** Tier 8 fleet coordination cannot be established from simultaneous activity on multiple devices alone; it requires stable task identity, resource claims or an equivalent contention mechanism, delivery accounting, and endpoint verification.
- **Type:** definitional and empirical
- **Status:** supported
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `tiers/tier-08.md`; `assessments/tree-2026-11-04.md`.
- **Counterevidence:** A future coordination mechanism might replace explicit claims with a formally equivalent mutual exclusion method.
- **Related claims:** CLM-003, CLM-010
- **Deliberations:** NONE
- **Review condition:** Demonstration of safe, auditable fleet coordination without stable identity or an equivalent contention control.

## CLM-010: Authority does not automatically transfer through delegation

- **Statement:** When an orchestrator delegates work, the worker receives only the authority specified for that assignment; authority granted for another purpose, principal, destination, or interval does not transfer by implication.
- **Type:** theoretical and normative
- **Status:** supported
- **Confidence:** HIGH
- **Introduced:** 2026-11-04
- **Evidence:** `docs/THEORY.md` section 2.3; `tiers/tier-08.md`; `docs/OPEN-QUESTIONS.md` OQ-12.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-009, CLM-011
- **Deliberations:** NONE
- **Review condition:** A delegation framework that proves semantic preservation of authority through handoffs without explicit scope statements.

## CLM-011: Human oversight belongs inside the assessed system boundary

- **Statement:** If human approvals, corrections, selections, or verification materially contribute to a run, an assessment that excludes them misstates the system's autonomy and reliability.
- **Type:** methodological
- **Status:** supported
- **Confidence:** HIGH
- **Introduced:** 2026-11-04
- **Evidence:** `docs/THEORY.md` section 8; `docs/EVIDENCE-STANDARD.md` section 4.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-010
- **Deliberations:** NONE
- **Review condition:** A reporting convention that reliably separates machine contribution without hiding material human work.

## CLM-012: Verdict determinism is a property of packet plus rubric

- **Statement:** Assessment can approach determinism only when the evidence packet, environment, claim scope, and rubric version are fixed; the same prose conclusion evaluated against different packets is not a determinism failure.
- **Type:** methodological
- **Status:** supported
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `docs/THEORY.md` section 6; `docs/MODEL.md` sections 3 and 12; `docs/OPEN-QUESTIONS.md` OQ-06.
- **Counterevidence:** Evaluator judgment in classifying independence and materiality may impose a practical limit on determinism.
- **Related claims:** CLM-006
- **Deliberations:** NONE
- **Review condition:** Inter evaluator studies showing which rubric terms produce persistent disagreement from identical packets.

## CLM-013: Material configuration change expires a current claim

- **Statement:** A present tense tier claim expires when the model, tools, state system, scheduler, permissions, or material environment changes, even though the earlier claim may remain valid as a dated historical assessment.
- **Type:** methodological
- **Status:** supported
- **Confidence:** HIGH
- **Introduced:** 2026-11-04
- **Evidence:** `docs/MODEL.md` sections 2 and 10.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-003
- **Deliberations:** NONE
- **Review condition:** An equivalence method that can prove a change immaterial for every affected gate.

## CLM-014: Partial long horizon results require side effect accounting

- **Statement:** A partially completed long horizon task cannot be evaluated from artifact value alone; the assessment must account for completed side effects, duplicated effects, blocked dependencies, and whether the remainder can resume safely.
- **Type:** theoretical and methodological
- **Status:** proposed
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `tiers/tier-06.md`; `docs/OPEN-QUESTIONS.md` OQ-20.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-004, CLM-007
- **Deliberations:** NONE
- **Review condition:** Field assessments that reveal a better partial credit model tied to dependency structure and reversibility.

## CLM-015: Escalation quality is measurable independently of escalation rate

- **Statement:** Two systems with identical escalation rates can differ measurably in escalation quality: whether the escalation occurs before an irreversible step, carries the context a decision-maker needs, and is routed to a principal entitled to decide.
- **Type:** theoretical and methodological
- **Status:** proposed
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `docs/PROPOSED-CLAIMS.md` (CLM-015 rationale); `docs/OPEN-QUESTIONS.md` OQ-11.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-011
- **Deliberations:** DELIB-0002
- **Review condition:** Inter-evaluator study scoring escalation timing, payload, and routing on identical records.

## CLM-016: A canary statement can detect record leakage that audits cannot

- **Statement:** A fabrication disclosed to exactly one witness in a controlled channel, which later reappears in completed or corrected form, establishes that a record path outside that channel carries statements, and does so with fewer assumptions than log audit alone.
- **Type:** methodological
- **Status:** proposed
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `docs/PROPOSED-CLAIMS.md` (CLM-016 rationale); `docs/OPEN-QUESTIONS.md` OQ-14, OQ-15.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-006, CLM-011
- **Deliberations:** DELIB-0002
- **Review condition:** Replication across at least two independent systems with sealed control canaries.

## CLM-017: Claim expiry should be event-based, not calendar-based

- **Statement:** A tier claim's present-tense force ends at the first material configuration event affecting a satisfied gate, not at a fixed date; calendar expiry is only a fallback when event detection is unavailable.
- **Type:** methodological
- **Status:** proposed
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `docs/PROPOSED-CLAIMS.md` (CLM-017 rationale); `docs/METRICS.md` section 7; `docs/OPEN-QUESTIONS.md` OQ-05.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-003, CLM-013
- **Deliberations:** DELIB-0002
- **Review condition:** A change-to-gate map with at least one field validation.

## CLM-018: Audit completeness and principal confidentiality trade off measurably

- **Statement:** Beyond a threshold, additional audit detail yields diminishing accountability gains while monotonically increasing confidentiality risk; the trade-off has an identifiable knee that varies by domain consequence.
- **Type:** theoretical
- **Status:** proposed
- **Confidence:** LOW
- **Introduced:** 2026-11-04
- **Evidence:** `docs/PROPOSED-CLAIMS.md` (CLM-018 rationale); `docs/OPEN-QUESTIONS.md` OQ-22.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-009
- **Deliberations:** DELIB-0002, DELIB-0003 (operationalization adopted; see the synthesis for the executable review condition)
- **Review condition:** A Tier 8 reconstruction study reporting both accuracy and exposure axes.

## CLM-019: Behavioral continuity, not storage identity, is the Tier 6 property

- **Statement:** Tier 6 continuity is satisfied when resumed behavior preserves commitments, side-effect boundaries, and status truth, regardless of whether the underlying store is the original state, a transcript reconstruction, or an equivalent re-derivation.
- **Type:** definitional
- **Status:** proposed
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `docs/PROPOSED-CLAIMS.md` (CLM-019 rationale); `docs/TIER-DEEP-DIVES.md` (Tier 6); `docs/OPEN-QUESTIONS.md` OQ-19.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-007, CLM-014
- **Deliberations:** DELIB-0002
- **Review condition:** Two independent Tier 6 packets with differing storage designs.

## CLM-020: Fleet verdicts should be issued per operation class, not per fleet

- **Statement:** A single fleet can simultaneously merit different verdicts for different operation classes (read-only coordination vs. irreversible actuation); a per-fleet verdict hides the distinction that matters for trust.
- **Type:** methodological
- **Status:** proposed
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `docs/PROPOSED-CLAIMS.md` (CLM-020 rationale); `docs/TIER-DEEP-DIVES.md` (Tier 8); `docs/OPEN-QUESTIONS.md` OQ-08, OQ-09.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-003, CLM-009, CLM-010
- **Deliberations:** DELIB-0002
- **Review condition:** Two fleet packets scored both ways and compared against subsequent incidents.

## CLM-021: Four precedence rules govern conflicting principals without leakage

- **Statement:** Fleet instruction conflicts among principals are decided, or correctly escalated, by four rules in order: authority is a ceiling no combination of principals can exceed; the resource owner's instruction governs the resource (the enrolling principal by default for irreversible actions absent a declared owner set); recency supersedes only within one principal; deadlock escalates on existence, resource, and needed decision only, holds the safest state, and resurfaces on owner contact and after a declared interval. Jointly, the rules require no authority expansion and no disclosure of one principal's confidential content to another.
- **Type:** theoretical and normative
- **Status:** proposed
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `deliberations/DELIB-0004/` (proposal, skeptical review, synthesis); `docs/OPEN-QUESTIONS.md` OQ-13.
- **Counterevidence:** NONE RECORDED. Known limitation: the enroller default can misassign accountability for estates, rotating administrators, and IT enrolled devices (DELIB-0004-02 dissent, preserved in DELIB-0004-03).
- **Related claims:** CLM-010
- **Deliberations:** DELIB-0004
- **Review condition:** A two principal fleet packet with instruction conflicts scored for correct rule application, zero authority expansion, and zero content disclosure. A conflict shape the rules cannot decide narrows or refutes this claim.

## CLM-022: Deliberation improves calibration only under three conditions

- **Statement:** Structured deliberation improves calibration over single pass judgment only when participants record private verdicts before discussion, at least one participant is tasked as skeptic against the emerging consensus, and dissent is preserved in the synthesis. Calibration is verdict accuracy against double scored ground truth plus mean confidence error, tested by factorial ablation of the three conditions.
- **Type:** theoretical and methodological
- **Status:** proposed
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `deliberations/DELIB-0005/`; `docs/OPEN-QUESTIONS.md` OQ-15.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-006, CLM-012
- **Deliberations:** DELIB-0005
- **Review condition:** Factorial deliberation study on matched packets. No condition effect, or improvement without the conditions, refutes the claim as stated; divergent results by participant class narrow it.

## CLM-023: Load bearing synthesis sentences require proposition level attribution

- **Statement:** Every load bearing sentence in a synthesis (one whose removal changes a verdict, a confidence, or a requested next step) can and should be attributed to the note or artifact it rests on. The unattributable fraction of such sentences is the quality measurement; a synthesis with a material unattributable fraction summarizes opinion, not evidence.
- **Type:** methodological
- **Status:** proposed
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `deliberations/DELIB-0005/`; `docs/DELIBERATION-PROTOCOL.md` section 11; `docs/OPEN-QUESTIONS.md` OQ-16.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-012
- **Deliberations:** DELIB-0005
- **Review condition:** Attribution audits of real syntheses (including this repository's), sampling all load bearing sentences by the deletion test.

## CLM-024: Operational memory requires origin class, scope, and default expiry

- **Statement:** Operational memory is trustworthy only when each retained item carries origin class (observed, instructed, inferred, deliberated), scope (principal, task, interval), and an expiry or review condition; items without an explicit condition expire at their scope interval's end and survive only by recorded re-derivation or re-instruction; inferred items are never silently promoted to observed. Selection retains commitments, effects already caused, authority, and open questions; the rest expires.
- **Type:** theoretical and methodological
- **Status:** proposed
- **Confidence:** MODERATE
- **Introduced:** 2026-11-04
- **Evidence:** `deliberations/DELIB-0005/`; `docs/THEORY.md` section 10; `docs/OPEN-QUESTIONS.md` OQ-17, OQ-18.
- **Counterevidence:** NONE RECORDED
- **Related claims:** CLM-019
- **Deliberations:** DELIB-0005
- **Review condition:** Memory store audits sampling origin classes, promotion events, and expired item survival. A domain where the four coarse classes actively mislead amends the class set; silent promotion surviving audit refutes the sufficiency direction.
