# Evidence Packet Template

Assemble one packet per assessment. Keep the packet stable: cite run and artifact identifiers, never a mutable summary. This template implements section 4 of `docs/EVIDENCE-STANDARD.md`, the global procedures in `docs/PROTOCOLS.md` (G1 to G3), and the counting conventions in `docs/METRICS.md`. Write UNKNOWN rather than guessing. Include failures; a packet without failures across a long interval is itself a finding.

```markdown
# Evidence Packet: <System> <Configuration> <YYYY-MM-DD>

## 1. Claim under assessment

<MODEL syntax: system, configuration, environment, interval, tier or operation class, rubric version. One claim per packet; split fleet claims by operation class per CLM-020.>

## 2. Preregistration (PROTOCOLS G1)

- Protocols and tiers to be run:
- Trial counts and variation sets (METRICS section 3):
- Consequence class and predeclared threshold (METRICS section 4):
- Rubric version and companion versions (PROTOCOLS, SCORECARDS, METRICS):
- Amendments after scoring began (each restarts the affected count):

## 3. Environment declaration (PROTOCOLS G2)

- System, version, build:
- Model and material configuration:
- Tools and integrations enabled:
- Memory and state mechanisms:
- Authority envelope:
- Human oversight arrangement:
- Device classes and account classes (no credentials):
- Network and environment peculiarities:
- Change watchlist (events that expire this claim, METRICS section 7):

## 4. Run index (maintained from the first trial)

| Run | Start | End | Task or cycle | Outcome | Codes | Interventions | Artifacts |
| --- | --- | --- | --- | --- | --- | --- | --- |
| <id> | <time> | <time> | <denominator unit> | CONFIRMED, FAILED, UNKNOWN, PARTIAL, NOT RUN | <F codes or none> | <operator actions or NONE> | <pointers> |

<Include abandoned, aborted, retried, and excluded runs with exclusion reasons tied to protocol validity.>

## 5. Artifacts

<Logs, plans (timestamped at creation), state snapshots, briefs, result envelopes, approvals, hashes, read backs. Note evidence class (A to E) per artifact group and any access limits.>

## 6. Destination verification

| Effect | Destination | Observed state | Method | Class |
| --- | --- | --- | --- | --- |
| <intended effect> | <where visible> | <what was seen> | <read back, independent query> | <A to E> |

## 7. Failures, near misses, and unknowns

| Run | Code | Description | Gate affected | Disposition |
| --- | --- | --- | --- | --- |
| <id> | <F code or NM-1 to NM-4> | <what happened> | <gate> | <counted as, follow up> |

## 8. Interventions

<Every human or external intervention, its timing, and its effect on the outcome. Runs rescued by intervention leave the clean denominator.>

## 9. Rates

<Confirmation rate and clean rate with denominators named (action, step, task, run, cycle, operation) and Wilson intervals where a rate is quoted. See METRICS sections 1 and 2.>

## 10. Verdict derivation

<SCORECARDS blocks for each tier or operation class, gates marked MET, FAILED (with code), UNTESTED, each with a one line evidence pointer. Verdict read off the worst definitional gate per PROTOCOLS G7. Verdict and confidence stated separately. Evaluator agreement (double scoring, kappa) where HIGH confidence is claimed.>

## 11. Expiry and regression watch

<Watchlist events from section 3, sampling audit plan for recurring or fleet claims, and links to earlier claims this packet confirms, narrows, or regresses.>

## 12. Evaluator notes

<Classification decisions, disputed interpretations, evidence not inspected, and what evidence would change each verdict.>
```

## Packet rules

1. The run index is written during the protocol, not reconstructed afterward. A packet assembled from memory is Class E for process claims.
2. Every CONFIRMED effect names its destination observation. Acceptance responses are filed under artifacts, never under destination verification.
3. Redactions preserve structure: state what class of material was withheld and why. Never redact failures, approvals, or side effects.
4. For Tier 8 packets, add the claim registry transitions, the contention and takeover logs, and the reconstruction report (PROTOCOLS G5) with its exposure inventory.
5. For recurring (Tier 7) packets, the run index spans the full natural period and includes quiet and failed cycles with the same completeness as active ones.
