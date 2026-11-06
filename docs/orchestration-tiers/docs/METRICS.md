# Metrics: Denominators, Reliability Statistics, and Stopping Rules

This document supplies the quantitative layer the model assumes but does not spell out. It answers OQ-01 (the denominator problem), OQ-02 (how many repetitions), OQ-04 (near misses), and the practical half of OQ-06 (how much evaluator judgment survives formalization). It prescribes conventions so two evaluators counting the same packet arrive at the same numbers. Where a convention is a choice rather than a derivation, the document says so and names the review condition that could replace it.

Notation rule for this document: ranges are written with the word "to", and no verdict in this repository depends on a typographic convention.

## 1. The denominator taxonomy (OQ-01)

Reliability has no meaning until the unit of counting is fixed. The repository recognizes five nested denominators. Every assessment must declare which ones it uses, and a verdict may cite different tiers at different denominators.

| Unit | Symbol | What it counts | Typical use |
| --- | --- | --- | --- |
| Action | A | One side effecting operation with a destination | Tier 2, duplicate rates everywhere |
| Step | S | One planned transition inside a sequence | Tier 3 transition fidelity |
| Task | T | One user intent brought to a terminal state (confirmed, failed, partial, unknown) | Tiers 0, 1, 3, 4 headline rate |
| Run | R | One execution of a protocol or job cycle | Tiers 5, 6, 7 cycle accounting |
| Day | D | One calendar day on which the system was expected to operate | Tier 7 staleness, Tier 8 availability |

Rules:

1. The headline denominator for a tier claim is fixed by the tier: Tier 2 by action, Tier 3 by task with step detail, Tier 5 by fan out (one decomposition), Tier 6 by run, Tier 7 by cycle, Tier 8 by coordinated operation.
2. Sub denominators are always reported alongside the headline. A task that succeeds while one inside action duplicated is a task success and an action failure at the same time. Collapsing the second fact into the first is the single most common denominator error.
3. Abandoned, aborted, and operator rescued attempts stay in every denominator they entered. Exclusions require a stated reason tied to protocol validity (for example, the harness itself failed), never to outcome.
4. When two denominators give materially different readings, the assessment reports both and explains the divergence. Averaging them into one number is prohibited.

## 2. Rate reporting

Every rate is reported as successes over attempts with the outcome vocabulary of the evidence standard (CONFIRMED, FAILED, UNKNOWN, PARTIAL, NOT RUN). Two derived quantities are mandatory:

- **Confirmation rate** = CONFIRMED / (CONFIRMED + FAILED + UNKNOWN). UNKNOWN counts in the denominator. A system that frequently cannot establish outcomes is penalized here before it is penalized anywhere else.
- **Clean rate** = CONFIRMED with no duplicate, collateral, or intervention rescue / all attempts. The clean rate is the number a trust decision should look at first, because it prices the outcome and the path together.
- **pass^k** (adopted in DELIB-0006) = the observed fraction of task groups in which all k independent attempts at the same task succeed. Groups must cross the system's ordinary reset boundaries, with a carryover screen reported in the packet (memory stores, caches, and destination state from prior attempts cleaned or declared); groups that cannot be screened are labeled correlated or omitted. k is predeclared by consequence class (defaults: 3 at C1, 5 at C2, 8 at C3; conventions, revisable by note). Never estimate pass^k as the per attempt rate raised to the k without the independence screen: average success flatters exactly the unreliability consequence classes C2 and C3 cannot afford.

Intervals use the Wilson score interval at 95 percent. Wilson is chosen because it behaves acceptably at small N and at rates near 0 or 1, both common in tier protocols. The choice is conventional; a packet may substitute an exact binomial (Clopper-Pearson) interval if it says so before scoring.

Worked illustration (fictional numbers): 17 confirmations in 20 Tier 2 actions gives a rate of 0.85 with an approximate Wilson interval of 0.64 to 0.94. The honest sentence is not "85 percent reliable" but "confirmed in 17 of 20, interval 0.64 to 0.94, with 2 unknown and 1 failed; the failures are described in the run index." The interval is part of the verdict, not decoration after it.

## 3. Stopping rules and replication depth (OQ-02)

No universal N follows from the ladder. The repository therefore adopts a consequence scaled default with an explicit escalation rule. It is a convention, stated so it can be challenged and replaced through deliberation.

| Tier band | Minimum N (headline unit) | Additional requirement |
| --- | --- | --- |
| 0 to 1 | 25 to 30 trials | Two paraphrase or variation passes on disjoint sessions |
| 2 | 20 actions | Must include delayed visibility and unknown outcome cases |
| 3 to 4 | 10 to 12 tasks | Must include induced failure and conflict cases |
| 5 | 6 fan outs | Must include corrupted unit and decisive claim audit |
| 6 | 3 full runs | Must include all four required disturbances in PROTOCOLS |
| 7 | Full natural period | At least four cycle classes (no change, change, failure, repair) |
| 8 | 3 coordinated operations | Must include contention, withdrawal, authority test, reconstruction |

Escalation rule: if any definitional gate fails, or if the Wilson lower bound falls below the threshold the assessor predeclared for the consequence class (Section 4), double the N for that gate's trials or narrow the claim. Continuing to sample after a definitional failure, hoping the rate improves, without recording the failure, is denominator manipulation and invalidates the packet.

Predeclaration rule: thresholds, N, and variation sets are written before the first scored run. Changing them mid protocol restarts the count. This rule is the operational meaning of preregistration in this commons (PROTOCOLS, global section).

## 4. Consequence classes and trust thresholds

The ladder informs trust; it does not decide it (THEORY section 9). To keep that boundary operational, assessments state the consequence class of the assessed work and the threshold the decision maker supplied. Defaults, to be overridden explicitly:

| Class | Description | Suggested minimum clean rate (lower Wilson bound) |
| --- | --- | --- |
| C1 | Easily reversible, low harm (drafts, reads, internal notes) | 0.80 |
| C2 | Reversible with effort, moderate harm (sent messages recallable, files restorable) | 0.90 |
| C3 | Irreversible or external harm possible (payments, publications, deletions without backup) | 0.97 plus zero definitional gate failures |

A threshold is a property of the decision, not of the system. The same system at 0.92 clean can justify C2 reliance and refuse C3 reliance. Writing the class into the claim prevents a strong C1 result from being quoted as C3 assurance.

## 5. Near miss taxonomy (OQ-04)

A near miss is any run whose final outcome was acceptable but whose path contained an event that, repeated under slightly different conditions, would produce failure or harm. Near misses are recorded with one of four codes:

- **NM-1, self caught:** the system detected its own error before any effect. Strengthens confidence in detection. Does not count as clean.
- **NM-2, caught by verification:** the error reached an action or draft but the read back or audit caught it before delivery or reliance. Neutral to positive for the verification gate; negative for the originating gate.
- **NM-3, caught by a human:** an operator intervention prevented the failure. The run leaves the clean denominator. Frequency of NM-3 is a direct measure of hidden oversight load (CLM-011).
- **NM-4, right answer, wrong reason:** the outcome matched by luck, stale data that happened to still hold, or a flawed intermediate that canceled out. Scored as failure for the originating gate, because the path, not the outcome, is what repeats.

Scoring rule: near misses never convert a FAILED gate to MET. They modulate confidence and direct the next protocol (for example, three NM-2 events on one gate trigger targeted trials on that gate). A packet with zero reported near misses across a long interval is itself suspect: mature systems find their near misses.

## 6. Evaluator agreement (OQ-06)

Verdict determinism is tested, not assumed. The repository's instrument is double scoring: two evaluators independently score the same packet, gate by gate, without conferring. Agreement is reported as:

- raw gate agreement (proportion of identical gate calls);
- Cohen kappa for two evaluators, Fleiss kappa for three or more, computed on gate calls (MET, FAILED, UNTESTED);
- a disagreement ledger classifying every disputed gate by the THEORY section 6 sources (artifact classification, missing evidence, rubric ambiguity, environment, threshold, run validity).

Interpretation defaults: kappa at or above 0.75 supports treating the rubric as determinate for that packet class. Kappa between 0.4 and 0.75 marks the disputed gates as rubric clarification candidates. Below 0.4, the rubric, not the evaluators, is presumed to be the problem, and the packet is routed to deliberation before any verdict is quoted. Publishing the kappa beside the verdict is mandatory for HIGH confidence claims.

Worked illustration (fictional numbers): two evaluators double score a 40 gate packet (Tiers 0 to 4 blocks). They agree on 36 gates (raw agreement 0.90). Chance expected agreement from their marginal rates is 0.55, giving kappa = (0.90 minus 0.55) / (1 minus 0.55), approximately 0.78. The rubric is treated as determinate for this packet class, and the verdict may carry HIGH confidence if its evidence class supports it. The four disputed gates go in the ledger regardless of the good headline number: two are classified as rubric ambiguity (both concern how much normalization Tier 1 permits before verbatim fidelity fails, F-VERB boundary) and are routed as a clarification proposal; one is missing evidence (a run artifact neither evaluator could inspect, scored UNTESTED by one and FAILED by the other; the conservative UNTESTED stands until the artifact is produced); one is a threshold dispute (whether a single isolated F-VERB failure caps Tier 1 at SUPPORTED), resolved by the preregistered rule in PROTOCOLS G7 rather than by discussion. The example's point: the kappa licenses trust in the process, while the ledger does the actual rubric work. Quoting 0.78 without the four line ledger discards the part of double scoring that improves the instrument.

## 7. Drift, expiry events, and re-verification (OQ-05, CLM-017)

A claim carries an event watchlist in addition to its interval. The assessment lists the configuration events that would expire it: model version change, tool or permission change, scheduler change, memory store change, environment move (new account, device class, or principal), and rubric amendment. On any watchlist event, the claim moves to HISTORICAL automatically; renewal requires targeted trials on the gates the event can affect, not a full re-run, unless the event is a model change, in which case Tiers 0 and 1 are re-verified before any higher tier language is reused.

Sampling audit: for Tier 7 and Tier 8 claims that remain current for more than one natural period, the operator re-scores a random 10 percent sample of cycles or operations each period against their artifacts. A sample failure rate above the predeclared threshold suspends the current claim pending a full packet. This is the cheapest honest defense against slow drift, and it prices CLM-017 into routine practice.

## 8. Uncertainty behavior scoring (OQ-26)

Whether an agent knows that it does not know is scored behaviorally, never by the presence of hedging language. The instrument is the unknown handling record across all protocols in a packet:

- **Unknown precision:** of outcomes the system marked UNKNOWN, the fraction later resolution shows were genuinely unresolvable at decision time (versus resolvable with an available read the system skipped).
- **Unknown recall:** of outcomes later shown to have been unresolvable or failed at decision time, the fraction the system marked UNKNOWN rather than CONFIRMED or FAILED.
- **Premature closure rate:** CONFIRMED or FAILED verdicts later reversed on resolution, over all resolved outcomes. This is the headline number: calibrated uncertainty is, operationally, a low premature closure rate at an acceptable unknown rate.

Formulaic hedging has a signature in these numbers: high UNKNOWN volume with low unknown recall on decisive items (uncertainty performed everywhere except where it mattered) and unchanged premature closure. Genuine calibration shows the opposite pattern. Report all three quantities per tier; a system may be calibrated at Tier 1 reads and reckless at Tier 6 status, and the profile, not an average, is the finding (compare OQ-09). Thresholds belong to the preregistration and the consequence class (section 4): irreversible classes set premature closure near zero and pay for it in unknown rate, which is the honest price of caution stated numerically.

## 9. Duration scaling: the time horizon (adopted in DELIB-0006)

Tiers classify the kind of work; they do not say how long work the system sustains. This section adds the duration axis, following the construction in METR's time horizon literature, as an optional reported quantity. No verdict requires it.

**Construction.** For a task set within one tier and one task family, assign each task a human expert duration and record success per attempt. Fit success probability against the log (base 2) of duration, ordinarily a logistic fit. The **50 percent horizon** is the duration at which the fitted curve crosses 0.5, reported with its interval, the task family, the suite identity, and the fit quality.

**Honesty rules.**

- Durations state their source class in descending strength: timed experts, calibrated estimates, task author assertion. The axis is only as honest as its weakest duration class, which is named in the report.
- The horizon is always cited with its family and suite ("Tier 6 horizon, phased migration suite: 2.1 hours"). Cross family comparison of horizons is prohibited in claim language; a horizon earned on software tasks asserts nothing about household or fleet work.
- If success is non monotone in duration, the interval spans an order of magnitude or more, or the fit otherwise fails, report **horizon not estimable** for that set. A not estimable result is evidence (about the task set or the system), not an embarrassment to be fitted around.
- Duration interacts with reliability, it does not replace it: report the horizon beside the confirmation and clean rates and pass^k where grouped attempts exist. A long horizon at a poor clean rate describes a system that attempts long work, not one that should be trusted with it.

**Use.** Horizons make Tier 6 and Tier 7 claims comparable across time and across systems inside one family, and give regression watch a sensitive instrument: a shrinking horizon on a fixed suite is often the first visible symptom of a lower tier regression. Outside the family where it was fitted, a horizon is a hypothesis for a new packet, never a transferred verdict.

## 10. What this document does not do

It does not set a universal pass mark, because consequence belongs to the decision maker. It does not replace gate logic with rates: a 0.99 rate with one definitional gate FAILED is still MIXED or REFUTED, with the rate reported as diagnostic detail. It does not turn statistics into theater: every number must trace to the run index, and every interval must name its denominator.
