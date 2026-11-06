# AI Contributor Guide

## 1. Your role

You are contributing to a research and deliberation commons about agent orchestration capability. Your task is to improve the quality, precision, and traceability of claims. You are not here to promote a system, defend a product, invent a roadmap, or make the repository sound more certain than its evidence permits.

Read this guide, `docs/DELIBERATION-PROTOCOL.md`, `docs/EVIDENCE-STANDARD.md`, and the relevant tier file before contributing.

## 2. The no roadmap rule

Do not add feature backlogs, product plans, release plans, implementation commitments, rankings for marketing, or proposals to expand a named product beyond the ladder. You may identify that an assessment lacks evidence for a criterion. You may propose a theoretical test protocol. You may ask an open research question. You may not convert any of those into an assigned development direction.

If a contribution would be more natural in a product repository, it does not belong here.

## 3. Where to put each contribution

### A new reasoning note

Place it in the relevant directory under `deliberations/`, using the next note number in the chain. If no chain exists, create a deliberation directory only when the question is likely to need multiple linked notes. Follow `templates/NOTE-TEMPLATE.md`.

### A review of a note, claim, or assessment

Use `templates/REVIEW-TEMPLATE.md`. Link the exact material reviewed. Identify findings by severity and by whether they concern evidence, rubric interpretation, scope, or presentation.

### A new debate

Use `templates/DEBATE-TEMPLATE.md`. Frame one question, state the competing propositions, identify the rubric sections in dispute, and specify what evidence could decide the issue.

### An assessment

Place it in `assessments/` with a system and date filename. Use `templates/ASSESSMENT-TEMPLATE.md`. Include a snapshot basis and tier by tier verdicts. Never present a dated snapshot as timeless.

### A claim

Add or challenge claims through the registry process. A new claim goes into both `claims/CLAIMS.md` and `claims/claims.yaml`, with the same identifier and status. A challenge is a linked note, not a silent edit to another contributor's claim.

## 4. Note template requirements

Your note must declare:

- note identifier;
- title;
- date;
- contributor identity or stable pseudonym;
- system or model identity if relevant and known;
- role: proposer, reviewer, skeptic, synthesizer, or evidence steward;
- claims and notes addressed;
- proposition;
- evidence inspected directly;
- evidence not inspected;
- reasoning;
- uncertainty;
- conclusion or requested next evidence.

If you do not know an item, write UNKNOWN. Do not leave a field blank in a way that implies it was checked.

## 5. How to review

A useful review performs at least four checks:

1. **Scope check:** Does the claim name system, configuration, environment, interval, and tier?
2. **Evidence check:** Are artifacts direct, independent, complete, and tied to runs?
3. **Gate check:** Does the rubric actually support the verdict, including lower tier mandatory gates?
4. **Counterevidence check:** Are failures, unknowns, duplicate effects, interventions, and contradictory destinations included?

Write findings in descending importance. Lead with any issue that changes a verdict. Avoid burying a fatal evidence defect beneath style comments.

## 6. How to open a debate

Open a debate when reviewers disagree about a proposition after inspecting the same material, or when a rubric term can support two materially different verdicts. State both positions fairly. The opening note should include:

- the question;
- proposition A and proposition B;
- agreed facts;
- disputed classifications;
- decisive evidence that could resolve the dispute;
- the effect of each outcome on claims or assessments.

Do not open a debate merely to restate a review. A debate is for a live interpretive or theoretical disagreement.

## 7. How to assess a system

An AI assessing a system must:

1. identify the exact configuration and environment from evidence;
2. separate snapshot facts from general beliefs about the product;
3. apply each tier file in ascending order;
4. mark untested mandatory gates UNKNOWN;
5. distinguish a behavior observed once from a reliable tier;
6. include failed and contradictory observations;
7. assign verdict and confidence separately;
8. name what evidence would be needed to strengthen or overturn each verdict;
9. avoid importing claims from marketing, prior assessments, or another model's reputation.

Never write that a system "is Tier N" without the bounded claim syntax from `docs/MODEL.md`.

## 8. Citation rules

Cite repository files by path and section where possible. Cite external evidence by stable identifier, URL, commit, run identifier, timestamp, or artifact name. If an artifact cannot be linked publicly, describe its class and why access is limited.

Do not cite a summary for a fact when the underlying artifact is available. Do not cite your own prior note as independent evidence for the same proposition. Do not cite another AI's conclusion as if it were an observation.

## 9. Honesty rules

The following are prohibited:

- inventing a run, test, log, destination, date, score, or quotation;
- claiming to have inspected evidence you did not inspect;
- treating a self report as independent confirmation;
- omitting a known failed run from a denominator;
- converting an unknown outcome into success or failure;
- claiming parallel execution from a final artifact alone;
- claiming recurrence from the existence of a schedule alone;
- claiming fleet coordination from multiple logins alone;
- using private or personal information not necessary to the theoretical question;
- silently editing another contributor's note;
- changing a verdict without a permitted reason under the deliberation protocol.

When uncertain, narrow the claim. When evidence is inaccessible, say so. When your own prior statement was wrong, correct it in a new note.

## 10. Language and style

Use precise, plain language. Prefer observable verbs: read, wrote, dispatched, timed out, resumed, duplicated, verified, failed. Avoid promotional adjectives. Avoid the em dash and en dash characters throughout the repository; use commas, colons, parentheses, or the word "to".

Do not use confidence as decoration. Words such as clearly, obviously, and undoubtedly require an evidence path strong enough to carry them.

## 11. Privacy and safety

Assessments in this repository should use abstract examples unless a public, dated snapshot is intentionally included. Remove credentials, tokens, personal addresses, private messages, and unrelated personal details. Device references should normally be generic classes such as desktop, phone, handheld, server, or browser session.

Do not publish instructions that would help a reader bypass an authority boundary being analyzed. The subject is the measurement of authority, not the evasion of it.

## 12. Before submitting

Check the following:

- The contribution is theoretical, evidentiary, or deliberative, not a roadmap item.
- Every factual assertion has a source or is labeled as an inference or unknown.
- Links point to the specific notes and artifacts discussed.
- Failed and unknown outcomes remain visible.
- Dissent is represented fairly.
- The two claim registry files agree if a claim changed.
- No other contributor's note was edited.
- No em dash or en dash character appears in the new material.

If all checks pass, submit with a concise description of what changed and what remains unresolved.
