# Assessment Template

Use this template for a dated assessment of one system configuration in one environment. Replace every bracketed field. Write UNKNOWN rather than guessing.

```markdown
# Assessment: <System> <Configuration> <YYYY-MM-DD>

## Assessment basis

- **System:** <name>
- **Version or configuration:** <identifier or UNKNOWN>
- **Environment:** <tools, devices, accounts, services, and constraints in generic terms unless a public snapshot requires detail>
- **Interval:** <start> to <end>
- **Assessor:** <name, model, organization, or stable pseudonym>
- **Rubric:** <repository commit or version>
- **Evidence packet:** <link or description>
- **Snapshot source:** <dated facts on which the assessment is based>
- **Material exclusions:** <what was not assessed>

## Executive result

<Reliable through Tier N, if justified, followed by the highest tier verdicts and the main limitations. Do not begin with a higher tier anomaly.>

## Evidence summary

| Source | Class | Coverage | Limits |
| --- | --- | --- | --- |
| <run set, report, log, or snapshot> | <A to E> | <tiers and gates> | <limits> |

## Human oversight and interventions

<Describe approvals, operator help, hidden or visible human work, and escalation behavior. Write UNKNOWN if the record does not establish this.>

## Tier by tier findings

### Tier 0: Reflex reply

- **Verdict:** <PROVEN, SUPPORTED, MIXED, UNSUPPORTED, REFUTED, or UNKNOWN>
- **Confidence:** <LOW, MODERATE, or HIGH>
- **Evidence:** <specific runs or snapshot facts>
- **Mandatory gates met:** <list>
- **Mandatory gates unmet or untested:** <list>
- **Failures:** <list or NONE OBSERVED IN SNAPSHOT>
- **Interpretation:** <bounded conclusion>

<Repeat for Tiers 1 through 8.>

## Highest reliable tier

<State the highest tier supported under the gated model and explain which lower or same tier gate controls the result.>

## Upper tier observations that do not change the reliable tier

<List impressive or partial behaviors above the reliable tier, with their limits. This section prevents an anomaly from being silently converted into a claim.>

## Regressions

<Name any later evidence that weakens a prior claim, or state that no regression evidence is included in the snapshot.>

## Unknowns

<List every material fact not established by the snapshot.>

## Evidence that would change this assessment

<Name the smallest set of runs, artifacts, or read backs that would raise, lower, or sharpen each uncertain verdict. This is an evidentiary statement, not a product plan.>

## Related deliberations

<Link notes and debates that interpret this snapshot.>
```

## Assessment rules

- Assess the configuration and interval, not the brand in perpetuity.
- Use ascending tier order.
- Do not infer an untested lower gate from an upper tier event.
- Keep verdict and confidence separate.
- Include failed, contradictory, and unknown evidence.
- Treat a snapshot as historical evidence after material configuration changes.
