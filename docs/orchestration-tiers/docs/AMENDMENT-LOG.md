# Amendment Log

MODEL section 12 requires amendments to be deliberative acts with stated effects on prior claims, not silent edits. This log is the registry of those acts for the expansion material. Companion documents (PROTOCOLS, SCORECARDS, METRICS, TIER-DEEP-DIVES, PROPOSED-CLAIMS, EXAMPLE-ASSESSMENT, EXAMPLE-FLEET, FIELD-GUIDE, and the evidence packet template) are additions, not amendments: they do not change any tier definition, gate, verdict, or existing claim. Entries below record every change the expansion made to preexisting files, with its authority and its effect. Anything not listed here was not changed.

## 2026-11-04: Expansion additions (no rubric change)

- **Added:** `docs/PROTOCOLS.md`, `docs/SCORECARDS.md`, `docs/METRICS.md`, `docs/TIER-DEEP-DIVES.md`, `docs/PROPOSED-CLAIMS.md`, `docs/EXAMPLE-ASSESSMENT.md`, `docs/EXAMPLE-FLEET.md`, `docs/FIELD-GUIDE.md`, `templates/EVIDENCE-PACKET-TEMPLATE.md`.
- **Authority:** contributor addition under the AI Contributor Guide (theoretical and evidentiary material, no roadmap items).
- **Effect on prior claims:** none. Tier files, MODEL, THEORY, EVIDENCE-STANDARD, and all verdicts in existing assessments are untouched. Where a companion interprets a gate, the tier file governs on conflict.

## 2026-11-04: Claim registrations CLM-015 to CLM-020

- **Changed:** `claims/CLAIMS.md`, `claims/claims.yaml` (six entries added, status proposed, identifiers and statuses verified identical across both files).
- **Authority:** DELIB-0002 (proposal, skeptical review, synthesis), which is the deliberation the Claim Template and registry practice require for contested wording.
- **Qualifications carried:** CLM-016 tests require the sealed canary procedure (PROTOCOLS G4); CLM-018 requires the operationalization in DELIB-0003 before any status above proposed.
- **Effect on prior claims:** none. CLM-001 to CLM-014 wording, status, and confidence are unchanged.

## 2026-11-04: DELIB-0003 operationalization linked to CLM-018

- **Changed:** CLM-018 deliberations field in both registry files (added DELIB-0003). Recorded in DELIB-0003-03 as an administrative link correction; the claim statement, status, and confidence are unchanged.
- **Authority:** DELIB-0002 imposed the narrowing requirement; DELIB-0003 discharges it.
- **Effect:** CLM-018's review condition is now executable (five level retention ladder, scored exposure axis, corpus precommitment, knee and non monotonicity rules). The claim remains proposed at LOW confidence.

## 2026-11-04: Wiring edits (navigation only)

- **Changed:** `README.md` (companion list), `docs/REPO-MAP.md` (companion section), `docs/GLOSSARY.md` (seven terms added: canary statement, clean rate, confirmation rate, escalation quality, failure code, near miss, operation class), `docs/OPEN-QUESTIONS.md` (advancement pointer table appended; no question text altered), `deliberations/INDEX.md` (DELIB-0002 and DELIB-0003 chain entries).
- **Effect:** navigation and vocabulary only. No proposition, gate, or verdict changed.

## 2026-11-04: DELIB-0004 and DELIB-0005 registrations (CLM-021 to CLM-024)

- **Changed:** `claims/CLAIMS.md`, `claims/claims.yaml` (CLM-021 added under DELIB-0004; CLM-022 to CLM-024 under DELIB-0005; all status proposed, registries verified identical). `deliberations/INDEX.md` (DELIB-0004, DELIB-0005 entries).
- **Authority:** the two deliberation chains, each proposal, skeptical review, synthesis, per the repository protocol. DELIB-0004 adopts precedence rules P1 to P4 with the enroller default and resurfacing requirement; DELIB-0005 registers the deliberation conditions, the attribution rule, and origin classed memory with default expiry.
- **Effect on prior claims:** none. CLM-010 (authority non transfer) constrains P1 and is extended, not revised. CLM-006 constrains CLM-022 and is cited, not revised.

## 2026-11-04: Field validity, fault catalog, uncertainty scoring (G8, METRICS section 8)

- **Changed:** `docs/PROTOCOLS.md` (G8 field validation stages and injected fault catalog FT-01 to FT-07; fault codes use the FT prefix to avoid collision with SCORECARDS failure codes), `docs/METRICS.md` (section 8 uncertainty behavior scoring; former closing section renumbered to 9), `docs/EXAMPLE-FLEET.md`, `docs/FIELD-GUIDE.md`, `docs/AMENDMENT-LOG.md` (this file), README and REPO-MAP links, `docs/OPEN-QUESTIONS.md` pointer table updated to the final instrument set.
- **Authority:** contributor addition; instruments for OQ-03, OQ-07, OQ-23, OQ-26. No tier gate, verdict, or claim text changed. G8 fault FT-07 (principal spoof) depends on DELIB-0004's attribution assumption and is scored accordingly.
- **Effect on prior claims:** none. Assessments written before G8 remain valid; their Tier 8 packets carry untested tail risk in the G8 sense and should say so on renewal.

## 2026-11-04: DELIB-0006 measurement instruments (duration, pass^k, judge validity)

- **Changed:** `docs/METRICS.md` (pass^k added to section 2 with independence screen and predeclared k defaults; new section 9, duration scaling and the 50 percent horizon with suite naming, duration source classes, and the not estimable rule; closing section renumbered to 10), `docs/PROTOCOLS.md` (Appendix J, judge validity protocol MVVP with the exclusion rule), `deliberations/INDEX.md` (DELIB-0006 entry). One administrative correction in DELIB-0006-03 aligns its section cross reference (10 corrected to 9) with the adopted numbering; no substance changed.
- **Authority:** DELIB-0006 (proposal, skeptical review, synthesis). The skeptic's guards are adopted in full: horizons are suite named and may be not estimable; pass^k groups require a carryover screen; judges below gold threshold are excluded from gate evidence.
- **Effect on prior claims:** none. No tier, gate, verdict rule, or claim changed. Existing assessments predate the instruments and remain valid; the instruments are optional, and their absence from a packet is a noted limitation, not a defect. External benchmarks are task sources only, never verdict authorities.

## How to add an entry

Any future change to a preexisting file that alters meaning (a gate, a definition, a claim's status, a verdict, a threshold) is appended here in the same change, naming: the files changed, the deliberation or review that authorizes it, the effect on prior claims, and whether prior assessments need reinterpretation. Pure additions of new files that interpret nothing preexisting need an entry only when they are first linked from a preexisting file.
