# Evaluator Field Guide: Running an Assessment in One Pass

A one page path through the commons for a working evaluator. It sequences existing documents; it adds no new rules. If anything here conflicts with a tier file, the tier file governs.

## Before any run

1. Write the claim in MODEL syntax (system, configuration, environment, interval, tier, rubric). If the subject is a fleet, split the claim by operation class (CLM-020).
2. Preregister (PROTOCOLS G1): protocols, trial counts (METRICS section 3), consequence class and threshold (METRICS section 4), rubric versions. File it with the packet (templates/EVIDENCE-PACKET-TEMPLATE.md).
3. Declare the environment (PROTOCOLS G2) and open the run index. From this moment, every started attempt is indexed, including the ones that will fail.

## During the protocol

4. Run tiers in ascending order. Do not let an impressive upper tier behavior substitute for an untested lower gate; mark the gate UNTESTED and keep moving.
5. Inject the failures the tier protocol requires: the disabled source (Tier 1), the unknown outcome (Tier 2), the failed middle (Tier 3), the conflicting sources (Tier 4), the corrupted worker unit (Tier 5), the four disturbances (Tier 6), the four cycle classes (Tier 7), contention, withdrawal, authority test, and human takeover (Tier 8, G6).
6. Score outcomes in the evidence standard vocabulary only (CONFIRMED, FAILED, UNKNOWN, PARTIAL, NOT RUN). Attach failure codes (SCORECARDS) and near miss codes (METRICS section 5) as events happen.
7. Verify at destinations, not at success responses. For every effect, ask the six read back questions (EVIDENCE-STANDARD section 6). Never retry an unknown outcome before resolving it.
8. Log every human intervention with its effect. Rescued runs stay in denominators and leave the clean rate.

## Deriving verdicts

9. Complete the SCORECARDS block per tier or class from artifacts, never from memory. Read each verdict off its worst definitional gate (PROTOCOLS G7). State verdict and confidence separately; HIGH confidence needs independent destination evidence and, for gate determinism, double scoring with kappa (METRICS section 6).
10. Compute confirmation and clean rates on the declared denominators with intervals. Check the consequence class threshold against the clean rate's lower bound before writing any trust language.
11. List what evidence would change each verdict, what remains UNKNOWN, and which watchlist events will expire the claim (METRICS section 7). For recurring and fleet claims, schedule the sampling audit now.

## Filing

12. File the assessment in `assessments/` with the packet, or link the packet location. Update the claim registry only through the verdict change rules: new evidence, reclassification, rubric error, narrowing, regression, or expiration. Register genuinely new propositions in both `claims/CLAIMS.md` and `claims/claims.yaml` with the same status, and open a deliberation chain when wording or interpretation is contested.
13. Preserve dissent. If a reviewer disagrees, their note joins the chain; the synthesis summarizes it in terms its author would recognize.

## The five fastest ways to invalidate a packet

- A denominator that quietly excludes failed, aborted, or retried runs.
- A success claimed from an action response without a destination observation.
- An unknown outcome retried blind, or rewritten as success after a later attempt worked.
- Agreement among workers or sources presented as independent when they share the decisive source (CLM-006).
- A verdict changed without one of the five permitted reasons. When in doubt, narrow the claim and say what is unknown. That is a successful evaluation.
