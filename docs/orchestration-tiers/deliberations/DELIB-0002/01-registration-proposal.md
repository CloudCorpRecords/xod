# DELIB-0002-01: Register CLM-015 to CLM-020 as proposed claims

- **Deliberation:** DELIB-0002
- **Date:** 2026-11-04
- **Contributor:** Expansion contributor (Muse session)
- **Role:** proposer
- **Status:** open, answered by DELIB-0002-02, synthesis in DELIB-0002-03
- **Claims addressed:** CLM-015, CLM-016, CLM-017, CLM-018, CLM-019, CLM-020
- **Prior notes addressed:** NONE
- **Assessments addressed:** NONE

## Proposition

The six statements drafted in `docs/PROPOSED-CLAIMS.md` (CLM-015 to CLM-020) should be entered in `claims/CLAIMS.md` and `claims/claims.yaml` with status `proposed`, so that the repository's review machinery (deliberation, review templates, verdict change rules) governs their future rather than a companion document.

## Scope

This note concerns registry practice only. It does not assert that any of the six statements is true, and it proposes no change to any tier file, verdict, or existing claim. Each claim's evidence path, counterevidence (currently NONE RECORDED), and review condition are carried over verbatim from the draft.

## Evidence inspected directly

| Evidence | Class | What it establishes | Limits |
| --- | --- | --- | --- |
| `claims/CLAIMS.md` (CLM-001 to CLM-014) | Repository rubric | Registry format, status vocabulary, review condition practice | Says nothing about the new statements' truth |
| `templates/CLAIM-TEMPLATE.md` | Repository rubric | Filing rules: narrow, dated, reviewable, registry consistency | Procedural only |
| `docs/PROPOSED-CLAIMS.md` | Contributor draft | The six statements with support and refute conditions | Authored by the proposer; not independent evidence |

## Evidence not inspected

No field packet, inter evaluator study, or fleet record supporting any of the six statements was inspected. Their statuses must therefore remain `proposed` regardless of how plausible the reasoning reads.

## Reasoning

The registry distinguishes `proposed` from `supported` precisely so that interesting, falsifiable statements can be tracked before evidence arrives. Leaving the six statements in a companion document would orphan them from the review condition and history machinery. Registration costs nothing epistemically, because `proposed` asserts only that the statement is narrow enough to be wrong and names what would change its status. Each of the six satisfies that bar: each names the observation that would support it and the observation that would refute it.

## Counterarguments considered

The strongest objection is registry dilution: a registry full of proposed statements may be quoted as if proposed meant endorsed. The mitigation is status discipline in citation (a claim quoted without its status is misquoted), which the synthesis preserves as an explicit rule. A second objection, that CLM-019 is definitional and should amend Tier 6 directly, is answered by the deliberation requirement for rubric changes: definitional claims earn their amendment through evidence, not by editing the tier file.

## Uncertainty

It is unknown whether CLM-016 (canary detection) can be operationalized without the canary itself contaminating the systems it tests. That uncertainty is recorded as the claim's review condition rather than resolved here.

## Conclusion

Register all six as `proposed`. No other registry entry changes.

## Requested next step

A skeptical review of the registration, focused on dilution risk and on whether any statement is too broad to be wrong (DELIB-0002-02).
