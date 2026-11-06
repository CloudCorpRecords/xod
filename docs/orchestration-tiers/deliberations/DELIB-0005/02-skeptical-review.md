# DELIB-0005-02: Skeptical review of the deliberation, attribution, and memory conditions

- **Deliberation:** DELIB-0005
- **Date:** 2026-11-04
- **Contributor:** Expansion contributor (Muse session), writing as skeptic per protocol
- **Role:** skeptic
- **Status:** answered by synthesis in DELIB-0005-03
- **Claims addressed:** CLM-022, CLM-023, CLM-024 (proposed in DELIB-0005-01), CLM-006
- **Prior notes addressed:** DELIB-0005-01
- **Assessments addressed:** NONE

## Proposition

CLM-023 is registrable as stated. CLM-022 needs its outcome variable pinned down before it can lose. CLM-024 needs an explicit decay rule, or its expiry condition is decorative. None of the three should register above proposed on current evidence, a point the proposal concedes.

## Scope

Falsifiability and completeness only. No position is taken on likely effect sizes.

## Evidence inspected directly

| Evidence | Class | What it establishes | Limits |
| --- | --- | --- | --- |
| DELIB-0005-01 | Deliberative note | The three conditions, attribution rule, origin classes | Proposal stage reasoning throughout |
| `docs/METRICS.md` section 6 | Repository rubric | How this repository measures judgment quality (agreement, kappa, ledgers) | Measures agreement, not calibration against truth |
| `docs/THEORY.md` section 10 | Repository rubric | Memory categories the selection rule must cover | Categories named; no retention rule given there |

## Evidence not inspected

No deliberation experiment, attribution sample, or memory audit in any system. Every objection below is therefore about testability, which is the correct standard at registration.

## Reasoning

### 1. CLM-022: name the calibration metric or the claim cannot lose

"Improves calibration" needs its instrument. Adopt: calibration is scored as verdict accuracy against ground truth on matched packets, plus mean confidence error (stated confidence versus realized accuracy) where confidences are elicited. The three conditions are then ablatable factors in a factorial design (present or absent), and the claim predicts a main effect for each and no condition free improvement. Without the metric sentence, any disappointing result can be re-described as measuring the wrong kind of calibration. With it, the claim can be refuted cleanly, which is the compliment this registry pays its claims.

### 2. CLM-024: expiry needs a default, not only a field

Requiring an expiry or review condition per item fails in the predictable way: everything is filed as "review when relevant," which never arrives. Add the default: items with no explicit condition expire at the end of their stated scope interval, and survival past expiry requires an affirmative re-derivation or re-instruction recorded with its own origin. The default converts memory from an accumulating archive into a maintained position, which is what Tier 6 continuity actually needs (commitments and effects persist because they are carried, not because nothing is ever deleted).

### 3. CLM-023: accepted, with a sampling rule

Attribution of "load bearing sentences" invites dispute over which sentences bear load. Settle it mechanically in the review condition: a sentence is load bearing if removing it changes a verdict, a confidence, or a requested next step. The audit samples all such sentences. That definition is checkable by deletion, which keeps the claim out of rhetoric.

## Counterarguments considered

The proposer might resist the factorial framing as laboratory thinking imposed on a deliberative practice. The registry's own standard answers this: a claim narrow enough to be wrong needs its losing observation described, and for CLM-022 the factorial ablation is that description. Nothing requires anyone to fund the experiment before registration; the claim waits at proposed until someone does.

## Uncertainty

Ground truth verdicts for deliberation studies are themselves evaluator products, so CLM-022 experiments inherit the determinism problem (OQ-06). Double scored ground truth (METRICS section 6) is the available mitigation and should be named in the review condition.

## Conclusion

Register all three as proposed, with CLM-022 carrying the calibration metric sentence, CLM-024 carrying the default expiry rule, and CLM-023 carrying the deletion test for load bearing sentences.

## Requested next step

Synthesis adopting the registered wordings (DELIB-0005-03).
