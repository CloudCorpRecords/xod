# DELIB-0004-03: Synthesis, precedence rules adopted with two field defaults

- **Deliberation:** DELIB-0004
- **Date:** 2026-11-04
- **Contributor:** Expansion contributor (Muse session), writing as synthesizer per protocol
- **Role:** synthesizer
- **Status:** current synthesis
- **Claims addressed:** CLM-021 (registered by this synthesis), CLM-010
- **Prior notes addressed:** DELIB-0004-01, DELIB-0004-02
- **Assessments addressed:** `docs/EXAMPLE-FLEET.md`

## Proposition under synthesis

Can fleet instruction conflicts among principals be governed deterministically without authority expansion or confidentiality loss?

## Current conclusion

Yes, as a testable theory. The precedence rules are adopted in this order, with the skeptic's two defaults incorporated:

- **P1. Authority ceiling.** No combination of principals authorizes what none could authorize alone for that resource, destination, and interval.
- **P2. Owner governs.** The resource owner's (accountable principal's) instruction governs the resource. Absent a declared owner set, the enrolling principal is owner for irreversible actions; reversible actions may proceed under any entitled user with full audit. Owner sets and internal rules belong in the environment declaration (PROTOCOLS G2, now also a fleet enrollment requirement for Tier 8 claims).
- **P3. Recency is intra principal only.** Later instructions supersede earlier ones from the same principal, never across principals.
- **P4. Deadlock escalates on existence only, and resurfaces.** Incompatible instructions after P1 to P3: disclose existence, resource, and needed decision to each affected principal; hold the safest state (no irreversible action); re-present the held conflict on owner contact and after a declared interval. Standing safety instructions from the owner execute under P2 without escalation.

CLM-021 is registered as proposed in the broad form, because with the defaults adopted every conflict shape raised in this chain is decided by the rules. Its review condition is the extended authority protocol: two principal instruction conflicts in a fleet packet, scored for correct rule application, zero authority expansion, zero content disclosure.

## Evidence considered

| Evidence | What it contributes |
| --- | --- |
| DELIB-0004-01 | The four rules and the determinism argument |
| DELIB-0004-02 | The undeclared shared resource and stale instruction shapes; the two defaults |
| `docs/EXAMPLE-FLEET.md` Operation 3 | Existence only escalation behaving as P4 predicts |
| CLM-010, THEORY sections 2.3 to 2.4 | The non transfer constraint P1 generalizes between principals |

## Decisive arguments

The skeptic's objection was decidability in the field, not elegance, and it was correct: a precedence theory that answers undeclared cases with an evaluator's limitation has a hole exactly where fleets operate. The enroller default is conservative in the auditable direction and creates the right incentive (declare ownership at enrollment). The resurfacing requirement prevents P4 from decaying into silent permanent holds, which would convert a confidentiality protection into an availability failure. With both, the theory decides; without either, it would merely advise.

## Agreed facts

- Authority non expansion (CLM-010) binds between principals exactly as it binds across delegation.
- Existence only disclosure is sufficient for entitled principals to decide; content disclosure is never required by deadlock.
- No real multi principal record has yet tested the rules. CLM-021 is proposed, confidence MODERATE.

## Dissent preserved

The skeptic's caution on estates, rotating administrators, and IT enrolled devices stands: the enroller default can misassign accountability where enrollment and ownership legitimately differ. Recorded as the claim's known limitation and as enrollment design guidance (ask the ownership question explicitly at enrollment). If field evidence shows systematic misassignment, CLM-021 narrows by claim correction under the verdict change rules, with this dissent as the starting note.

## Current verdicts

- **Precedence theory P1 to P4 with defaults:** ADOPTED as the repository's working theory for OQ-13.
- **CLM-021:** REGISTERED (proposed, MODERATE) in `claims/CLAIMS.md` and `claims/claims.yaml` in the same change as this synthesis.

## Evidence that could change this synthesis

A two principal fleet packet in which a conflict shape defeats the rules (undecidable, misordered against accountability, or resolvable only by content disclosure) narrows or refutes CLM-021 and reopens this chain. Human factors evidence that entitled principals systematically refuse existence only escalation would reopen P4's disclosure design without touching P1 to P3.

## Verdict change history

- 2026-11-04: Synthesis created from DELIB-0004-01 and DELIB-0004-02. CLM-021 registered as proposed. OQ-13 advances from open to instrumented (theory stated, test defined in PROTOCOLS Tier 8 extension recorded in the claim's review condition).
