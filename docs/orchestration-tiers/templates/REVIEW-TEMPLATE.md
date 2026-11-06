# Review Template

Use this template to review a note, claim, assessment, evidence packet, or proposed synthesis. A review checks fidelity to evidence and rubric. It may support the material under review, oppose it, or find that it cannot yet be judged.

```markdown
# Review: <material title and identifier>

- **Date:** <YYYY-MM-DD>
- **Reviewer:** <name, organization, model, or stable pseudonym>
- **Material reviewed:** <path, note identifier, claim identifier, or assessment>
- **Rubric version:** <repository commit or version>
- **Review outcome:** <supports verdict, supports with narrowing, verdict changing issue found, insufficient evidence, or rubric ambiguity>

## Summary

<Give the overall result in a short paragraph. Lead with any issue that changes a verdict.>

## Scope check

- System and configuration: <adequate or issue>
- Environment: <adequate or issue>
- Interval: <adequate or issue>
- Tier and gates: <adequate or issue>
- Exclusions and limitations: <adequate or issue>

## Evidence check

| Check | Result | Notes |
| --- | --- | --- |
| Directness | <pass, concern, fail, or unknown> | <artifact and reasoning> |
| Independence | <pass, concern, fail, or unknown> | <relationship among sources> |
| Completeness | <pass, concern, fail, or unknown> | <denominator, failures, retries> |
| Freshness | <pass, concern, fail, or unknown> | <event and observation times> |
| Destination verification | <pass, concern, fail, or unknown> | <where effects were observed> |
| Intervention accounting | <pass, concern, fail, or unknown> | <human or external help> |

## Findings

### Finding 1: <title>

- **Severity:** <verdict changing, material, minor, or editorial>
- **Type:** <evidence, rubric, scope, inference, or presentation>
- **Location:** <section, run, claim, or artifact>
- **Problem:** <what is wrong or missing>
- **Effect:** <how the finding changes confidence or verdict>
- **Resolution:** <new evidence, narrowing, correction, or rubric clarification needed>

<Repeat for each finding.>

## Gate by gate check

| Tier | Gate | Evidence | Result |
| --- | --- | --- | --- |
| <tier> | <mandatory criterion> | <artifact or UNKNOWN> | <met, unmet, mixed, or untested> |

## Counterevidence

<List contradictory runs, missing destinations, duplicate effects, correlated confirmations, or plausible alternative explanations. Write NONE FOUND only if a real search was performed and describe its scope.>

## Recommendation

<State whether the claim should stand, narrow, change verdict, remain unknown, or enter deliberation. Give the reason in one or two sentences.>
```

## Review rules

- Review the proposition as written, not a more favorable version invented during review.
- Distinguish absence of evidence from evidence of absence.
- Do not treat agreement among correlated agents as independent confirmation.
- Quote or link the decisive passage or artifact for every verdict changing finding.
- Preserve a record of checks that passed as well as checks that failed.
