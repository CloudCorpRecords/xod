# DELIB-0006-01: Add duration scaling, pass^k, and judge validity to the measurement layer

- **Deliberation:** DELIB-0006
- **Date:** 2026-11-04
- **Contributor:** Expansion contributor (Muse session)
- **Role:** proposer
- **Status:** open, answered by DELIB-0006-02, synthesis in DELIB-0006-03
- **Claims addressed:** NONE (instruments, not claims; see scope)
- **Prior notes addressed:** NONE
- **Assessments addressed:** NONE

## Proposition

Three SOTA measurement instruments should enter the commons as protocol amendments, without new tiers, gates, or claims:

1. **Duration scaling (METR style).** METRICS gains the 50 percent task completion time horizon: fit success probability against the log of human expert task duration within a tier's task set; report the duration at which the fitted curve crosses 0.5, with its interval. This gives "reliable through Tier N" a duration axis the categorical ladder lacks.
2. **pass^k reliability.** METRICS section 2 gains pass^k beside confirmation and clean rates: the probability that all k independent attempts at the same task succeed. Where per attempt success is p and attempts are independent, pass^k is estimated directly from grouped attempts, never computed as p to the k without an independence check.
3. **Judge validity appendix (MVVP).** PROTOCOLS gains an appendix for packets that use model judges: the minimum viable validity protocol of chance corrected agreement (not raw agreement), position swap on pairwise judgments, repeated runs per item, and a degradation gold audit sample (items whose correct judgment is known by construction) reported beside every judge derived score.

## Scope

These are measurement amendments to `docs/METRICS.md` and `docs/PROTOCOLS.md`, logged in `docs/AMENDMENT-LOG.md`. They change no tier definition, gate, verdict rule, or claim. External benchmarks (GAIA, SWE-bench, tau-bench and successors) are referenced as task sources, not adopted as verdicts; a packet may draw tasks from a public suite while scoring them under this repository's gates.

## Evidence inspected directly

| Evidence | Class | What it establishes | Limits |
| --- | --- | --- | --- |
| METR, Measuring AI Ability to Complete Long Software Tasks (2025) and Time Horizon 1.1 (2026) | External literature | The horizon construction, its logistic form, and field doubling behavior | Software task suites; transfer to other domains is an empirical question |
| tau-bench (Yao et al., 2024) and successor reports | External literature | pass^k as the reliability metric that exposes per attempt success inflation | Enterprise service domains; independence across attempts must be verified, not assumed |
| LLM judge reliability audits (2026), including degradation gold designs | External literature | Raw agreement overstates judge validity; position, verbosity, and brand biases are measurable and partially correctable | Judge and domain dependent; no universal correction factor exists |

## Evidence not inspected

No packet in this repository yet contains duration scaled tasks, grouped repeated attempts, or a model judge. All three instruments therefore enter as untested conventions, stated so they can be challenged.

## Reasoning

The ladder answers "what kind of work" with unmatched discipline and "how long, how repeatably, and judged by whom" with comparatively thin instruments. Duration is the missing axis: two systems can both be SUPPORTED at Tier 6 while one sustains twenty minute tasks and the other four hour tasks, and the current verdict language cannot say so. pass^k is the missing strictness: consequence classes C2 and C3 care about every attempt, not the average attempt, and tau-bench's collapse from pass@1 to pass^8 is the canonical demonstration that averages flatter. Judge validity is the missing hygiene: packets will use model judges (the assessments already anticipate structured scoring), and the first judge derived number quoted without bias controls will be believed at face value, which the 2026 audits show is exactly what raw agreement invites.

Each addition is small and lands in an existing home: one METRICS section, one paragraph and formula in METRICS section 2, one PROTOCOLS appendix. No tier text changes, so no prior claim needs reinterpretation.

## Counterarguments considered

Duration fitting needs human duration estimates per task, which are expensive and noisy. The proposal answers by scoping: the horizon is optional, reported only when a packet includes duration calibrated tasks, and never required for a verdict. pass^k punishes exploration friendly systems; the answer is that it is reported beside, not instead of, the clean rate, and trust language at C2 and C3 is where it binds. Judge controls add cost per item; the degradation gold sample is a sample, not a census, precisely to bound that cost.

## Uncertainty

Whether the logistic form fits non software task families is unknown. The amendment should require reporting fit quality and forbid horizon language where the fit fails, rather than assuming the form transfers.

## Conclusion

Adopt the three instruments as amendments, with the scoping guards above.

## Requested next step

A skeptical review testing whether the guards actually prevent the known abuses (DELIB-0006-02).
