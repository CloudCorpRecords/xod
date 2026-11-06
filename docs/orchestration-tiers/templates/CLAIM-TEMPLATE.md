# Claim Template

Use this template for a claim about the ladder, the model, or a bounded system capability. Register the same identifier and status in `claims/CLAIMS.md` and `claims/claims.yaml`.

```markdown
## CLM-<NNN>: <Short claim title>

- **Statement:** <One bounded proposition. For a system claim, name the system, configuration, environment, interval, tier, and expected behavior.>
- **Type:** <theoretical, methodological, empirical, definitional, or system specific>
- **Status:** <proposed, supported, mixed, unsupported, refuted, unknown, superseded, or under deliberation>
- **Confidence:** <LOW, MODERATE, or HIGH, or NOT ASSESSED>
- **Introduced:** <YYYY-MM-DD>
- **Introduced by:** <contributor>
- **Evidence:** <links to packets, runs, assessments, or repository sections>
- **Counterevidence:** <links or NONE RECORDED>
- **Related claims:** <CLM identifiers>
- **Deliberations:** <DELIB identifiers or NONE>
- **Expiry or review condition:** <date, configuration change, rubric change, or new evidence class>

### Rationale

<Explain why the claim matters and how the evidence supports it. Separate observation from theory.>

### What would change this status

<Name evidence or a rubric correction that would strengthen, narrow, refute, or retire the claim.>

### History

- <YYYY-MM-DD>: <created, status changed, narrowed, challenged, or superseded, with link and reason>
```

## Claim rules

1. Make the claim narrow enough to be wrong.
2. Do not use a timeless present tense for a changing system.
3. Do not register a product intention or roadmap item as a claim.
4. Preserve the original statement when narrowing a claim; create a history entry and, where necessary, a new claim identifier.
5. A challenge links evidence or a rubric defect. It does not silently rewrite the registry.
6. Keep the Markdown and YAML registries consistent.
