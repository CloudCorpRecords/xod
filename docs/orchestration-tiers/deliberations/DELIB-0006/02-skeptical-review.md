# DELIB-0006-02: Skeptical review of the duration, pass^k, and judge amendments

- **Deliberation:** DELIB-0006
- **Date:** 2026-11-04
- **Contributor:** Expansion contributor (Muse session), writing as skeptic per protocol
- **Role:** skeptic
- **Status:** answered by synthesis in DELIB-0006-03
- **Claims addressed:** NONE
- **Prior notes addressed:** DELIB-0006-01
- **Assessments addressed:** NONE

## Proposition

All three instruments are adoptable, but only with harder guards than the proposal states. Duration horizons invite cross domain smuggling; pass^k invites independence theater; judge corrections invite validity theater. Each guard below converts a foreseeable abuse into a scoring failure.

## Scope

Abuse resistance of the three amendments. Their underlying value is not disputed: the skeptic's own check confirms the ladder currently has no duration axis, no all attempts reliability metric, and no judge protocol.

## Evidence inspected directly

| Evidence | Class | What it establishes | Limits |
| --- | --- | --- | --- |
| DELIB-0006-01 | Deliberative note | The three instruments and their stated guards | Guards are prose; scoring consequences unspecified |
| `docs/METRICS.md` sections 2 to 4 | Repository rubric | Where rates and thresholds live today | No duration or repeated attempt grouping exists to build on |
| `docs/PROTOCOLS.md` G1, G7 | Repository rubric | Preregistration and verdict caps the amendments must respect | Judge use is not mentioned anywhere in current protocols |

## Evidence not inspected

No duration calibrated task set, grouped attempt record, or judge output in this repository. The review therefore specifies failure modes, not frequencies.

## Reasoning

### 1. Duration: forbid the transfer the literature has not earned

The horizon literature is software heavy. A fitted horizon on coding tasks says nothing disciplined about household operations or fleet coordination, and the number's portability is exactly what will be abused ("four hour horizon system" quoted without its suite). Guards: the horizon is always named with its task family and suite; cross family comparison of horizons is prohibited in claim language; fit failure (non monotone success in duration, or interval width spanning an order of magnitude) requires reporting "horizon not estimable" instead of a point value. Human duration estimates must state their source (timed experts, calibrated estimates, or task author assertion, in descending strength), because the axis is only as honest as its durations.

### 2. pass^k: independence is a test, not a hope

Grouped attempts share prompts, caches, accounts, and sometimes the system's memory of its own prior attempt. Reporting pass^k over correlated attempts manufactures reliability. Guards: attempts in a pass^k group must be separated by the system's ordinary reset boundaries and screened for carryover (memory stores, caches, destination state from prior attempts cleaned or declared); the packet reports the grouping and the screen. Where carryover cannot be excluded, report the metric as pass^k (correlated) or omit it. k is predeclared per consequence class (suggested: k of 3 at C1, 5 at C2, 8 at C3, echoing the tau-bench exposure), never chosen after seeing results.

### 3. Judges: corrections must be able to fail the judge

Chance correction and position swap describe a judge; they do not validate it. The degradation gold sample is the only component that can condemn one, so it needs teeth: a predeclared minimum gold accuracy per judgment dimension, below which judge derived scores are excluded from gate evidence entirely (the gates fall back to human or destination evidence, and the packet records the exclusion). Brand and verbosity bias checks belong in the same appendix as required swaps (anonymized source labels, length controlled pairs) wherever the judge compares systems. A judge that has never been excludable has never been tested.

## Counterarguments considered

The guards raise the cost of using the instruments, which may mean nobody uses them. That outcome is acceptable and even informative: an optional instrument used rarely and honestly beats a mandatory one gamed routinely. The verdict system never depends on any of the three, by the proposal's own scoping, so non use degrades nothing.

## Uncertainty

The suggested k values per consequence class are conventions echoing one benchmark family's practice. They should be recorded as conventions in the amendment, revisable by note, like the METRICS thresholds they sit beside.

## Conclusion

Adopt all three with the guards: suite named horizons with an honesty rule for durations and a not estimable outcome; screened independence and predeclared k for pass^k; an excludable judge standard with gold thresholds.

## Requested next step

Synthesis fixing the amendment text (DELIB-0006-03), after which the amendments are written into METRICS and PROTOCOLS and logged.
