# DELIB-0004-02: Skeptical review of the four rule precedence theory

- **Deliberation:** DELIB-0004
- **Date:** 2026-11-04
- **Contributor:** Expansion contributor (Muse session), writing as skeptic per protocol
- **Role:** skeptic
- **Status:** answered by synthesis in DELIB-0004-03
- **Claims addressed:** CLM-021 (proposed in DELIB-0004-01), CLM-010
- **Prior notes addressed:** DELIB-0004-01
- **Assessments addressed:** `docs/EXAMPLE-FLEET.md`

## Proposition

The four rules are sound as far as they go, but the sufficiency claim fails on two conflict shapes the proposal does not order: conflicts between two owners of genuinely shared resources with no predeclared internal rule, and temporal conflicts in which the owner's standing instruction was given for a world that has materially changed. Both shapes need an explicit default, or CLM-021 must be narrowed from "jointly sufficient" to "sufficient where ownership is declared and instructions are current."

## Scope

This review tests decidability and ordering only. It accepts P1 and P4 without reservation.

## Evidence inspected directly

| Evidence | Class | What it establishes | Limits |
| --- | --- | --- | --- |
| DELIB-0004-01 | Deliberative note | The four rules and the determinism argument | Assumes declared ownership and current instructions |
| `docs/PROTOCOLS.md` G2 | Repository rubric | Environment declaration as the place ownership would live | Declaration is assessor practice, not yet a fleet enrollment requirement |
| `tiers/tier-08.md` mandatory criteria | Repository rubric | Authority non expansion and confidentiality as gates | Silent on inter principal priority, confirming the gap |

## Evidence not inspected

No shared ownership enrollment record from any real system. The shared resource shape is argued from the structure of household and team accounts, not from an inspected incident.

## Reasoning

### 1. The undeclared shared resource

The proposal handles shared ownership by requiring a predeclared owner set "with its own internal rule" and calling its absence an assessment limitation. But fleets will meet undeclared sharing in the field (a family tablet enrolled by one member, used by four). In that moment the system must still do something, and "limitation" is an evaluator's word, not a behavior. The theory needs a field default. The skeptic proposes: absent a declaration, the resource is treated as owned by the enrolling principal for irreversible actions (P2 with the enroller as owner), while reversible actions may proceed under any entitled user's instruction with full audit. That default is conservative in the right direction: it makes enrollment declarations valuable without bricking shared devices.

### 2. The stale owner instruction

P3 freezes cross principal priority at instruction time. Consider an owner's standing instruction ("never export this archive") meeting a later emergency in which the same owner, unreachable, would plausibly revoke it. The theory correctly refuses to let others revoke it. But it should say what happens across time when the owner does return: the conflict record (existence, resource, held state, duration) must be presented as one decision item, not buried in logs, or P4's escalation decays into silent permanent holding. Add a surfacing requirement: held conflicts re-present on owner contact and after a declared interval.

### 3. Sufficiency wording

With the two defaults added, the rules decide every shape the skeptic could construct, including instruction forgery attempts (excluded by the attribution assumption, correctly). Without them, the honest claim is narrower. CLM-021 should register in its narrow form unless the synthesis adopts both defaults, in which case the broad form is defensible as a testable proposition.

## Counterarguments considered

The proposer may answer that field defaults belong in protocols, not theory. The distinction matters less than decidability: wherever the default lives, CLM-021's truth depends on it existing. Registering the claim without the defaults would let future fleets pass the letter of the theory while improvising its gaps, which is how precedence theories fail in practice.

## Uncertainty

The enroller as owner default may misassign accountability in estates, teams with rotating administrators, and devices enrolled by IT for named users. Those cases argue for enrollment forms that ask the ownership question explicitly. That is enrollment design guidance, recorded here as a limitation rather than solved.

## Conclusion

Adopt the four rules with two defaults (enroller as owner for irreversible actions absent declaration; held conflicts surface on owner contact and after a declared interval), or narrow CLM-021. Do not register the broad sufficiency claim without the defaults.

## Requested next step

Synthesis deciding the defaults and the registered wording of CLM-021 (DELIB-0004-03).
