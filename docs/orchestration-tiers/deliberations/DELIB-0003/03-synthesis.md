# DELIB-0003-03: Synthesis, CLM-018 operationalization adopted with exposure scoring

- **Deliberation:** DELIB-0003
- **Date:** 2026-11-04
- **Contributor:** Expansion contributor (Muse session), writing as synthesizer per protocol
- **Role:** synthesizer
- **Status:** current synthesis
- **Claims addressed:** CLM-018 (primary), CLM-009 (related)
- **Prior notes addressed:** DELIB-0003-01, DELIB-0003-02; requirement originating in DELIB-0002-02 and DELIB-0002-03
- **Assessments addressed:** NONE

## Proposition under synthesis

How should the "identifiable knee" in CLM-018 be measured so the claim can gain or lose support from evidence?

## Current conclusion

The operationalization in DELIB-0003-01 is adopted with all three amendments from DELIB-0003-02. The adopted measurement, which now serves as the linked narrowing note DELIB-0002 required for CLM-018, is:

1. **Retention ladder.** Five levels, L0 to L4: identifiers only; identifiers plus hashes; plus structured action records; plus redacted payloads; plus full payloads. Applied to the same operations.
2. **Fidelity axis.** Reconstruction fidelity per PROTOCOLS G5 at each level (proportion of material steps recovered by an independent reviewer from the trail alone).
3. **Exposure axis (scored).** Per operation, counts of retained items in four classes (direct identifiers of principals, content payloads, third party identifiers, precise location or timing traces), combined with predeclared, published severity weights.
4. **Corpus discipline.** Corpus class and operation count named in the study registration before scoring. Results generalize within the named class until replicated outside it.
5. **Knee rule.** The knee is the lowest level after which doubling retained content volume yields under 2 percentage points of additional fidelity, sustained across two successive doublings. The claim predicts fidelity slope near zero beyond the knee while exposure slope remains positive.
6. **Non monotonicity rule.** If fidelity at a higher level falls below a lower level by more than 2 points, the ladder is reported defective for that corpus, the knee is NOT LOCATED, and the study gives CLM-018 no support.

## Evidence considered

| Evidence | What it contributes |
| --- | --- |
| DELIB-0002-02 objection 1, DELIB-0002-03 qualification 1 | The requirement this chain satisfies |
| DELIB-0003-01 | Ladder, fidelity measure, plateau rule |
| DELIB-0003-02 | Scored exposure axis, corpus precommitment, non monotonicity rule |
| `docs/PROTOCOLS.md` G5, `docs/METRICS.md` section 6 | The scoring instruments both axes rely on |

## Decisive arguments

A trade off claim needs two numeric axes or it cannot lose. The original draft measured accountability and merely listed confidentiality; the reviewer's scoring amendment is therefore not an improvement but the condition of falsifiability, and it is adopted in full. Corpus precommitment converts the claim's "varies by domain" phrase from protection against refutation into a replication program. The non monotonicity rule closes the last soft direction, in which a defective ladder could have been reported as a located knee.

## Agreed facts

- No corpus has yet been scored under this measurement. CLM-018 remains proposed at LOW confidence.
- The 2 point threshold, the doubling rule, and the severity weights are conventions, predeclared so they can be disputed and revised by note.
- The registered wording of CLM-018 is unchanged; this synthesis is its narrowing note, not an amendment to its text.

## Dissent preserved

None on the measurement. The proposer's caution that five levels locate a knee only to within one level stands as a recorded precision limit. If a future study needs finer location, it must insert intermediate levels before scoring, not interpolate afterward.

## Current verdicts

- **Operationalization as stated above:** ADOPTED as the linked narrowing note for CLM-018.
- **CLM-018 itself:** remains PROPOSED. Its review condition now reads, in executable form: a study under this measurement, in a named corpus class, reporting both axes.

## Evidence that could change this synthesis

A pilot study showing the four exposure classes cannot be double scored at acceptable agreement (METRICS section 6) would reopen the exposure axis design. A study completing under the measurement moves CLM-018 through its own status rules; it does not reopen this synthesis unless it shows the measurement itself to be inapplicable.

## Verdict change history

- 2026-11-04: Synthesis created from DELIB-0003-01 and DELIB-0003-02. The narrowing requirement imposed on CLM-018 by DELIB-0002-03 is discharged by this note. Registry entry for CLM-018 unchanged (statement, status, confidence); its deliberations field already points to DELIB-0002 and should gain DELIB-0003 by registry amendment in the same change as any future status movement, or sooner by an administrative link correction.
