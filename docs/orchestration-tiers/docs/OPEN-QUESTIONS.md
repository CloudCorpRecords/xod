# Open Questions

These questions are theoretical and methodological. They are not tasks, commitments, feature requests, or a roadmap. Each question identifies why the current ladder may be incomplete and what kind of evidence or argument would advance understanding.

## Measurement

### OQ-01. What is the right denominator for reliability?

Should reliability be counted by task, run, action, day, destination, or some combination? A system can complete most tasks while failing a small number of critical actions inside otherwise successful tasks. The unit chosen changes the verdict.

### OQ-02. How many repetitions are enough?

Replication requirements vary by tier, but no universal number follows from the ladder itself. The right number may depend on consequence, variance, environmental diversity, and whether failures are independent. A principled stopping rule remains open.

### OQ-03. Can controlled tests predict field reliability?

Laboratory protocols can induce interruption, conflict, and contention, but field work adds stale context, ambiguous human intent, account state, depleted resources, and long delays. The mapping from controlled pass rates to field trust is not yet well characterized.

### OQ-04. How should near misses be scored?

A system may detect its own error before harm occurs, recover after visible confusion, or produce the right answer for the wrong reason. The ladder needs a richer account of which near misses strengthen confidence and which reveal fragility.

### OQ-05. When does an old claim stop describing a system?

Models, tools, prompts, policies, and environments change at different rates. A claim expiration rule based only on dates is crude. A rule based on material change requires a theory of which changes are material for which gates.

## Determinism and comparison

### OQ-06. Can verdict determinism survive evaluator judgment?

The model seeks the same verdict from the same packet and rubric, but classification of evidence independence, environmental similarity, and materiality requires judgment. How much judgment can be formalized before the rubric becomes too rigid to describe real systems?

### OQ-07. Are cross product comparisons meaningful?

Two systems may be tested in different accounts, devices, network conditions, and authority envelopes. At what point do environmental differences dominate the orchestration property being compared? A common environment may be fairer but less ecologically valid.

### OQ-08. Is the ladder linear?

The model treats tiers as cumulative. Yet a narrow recurring automation might be reliable before its operator builds general research capability, and a specialized fleet controller might not perform open ended synthesis. Should the model branch by work type while retaining common lower gates?

### OQ-09. Can one score represent a profile?

A system may have a jagged capability profile: strong coding, weak delivery accounting, strong retrieval, weak authority discipline. A highest reliable tier compresses that profile. What companion representation preserves the useful detail without overwhelming the reader?

## Autonomy and authority

### OQ-10. How should autonomy be granted by scope?

Autonomy can vary by action type, destination, reversibility, amount, principal, time, and confidence. A single autonomy level may be too coarse. The open problem is how to express a multidimensional envelope without making it unusable.

### OQ-11. When is escalation a success?

A system that asks for help too often is burdensome. A system that never asks may be reckless. The quality of escalation depends on timing, information supplied, and whether the decision truly belongs to the human. Current rubrics identify escalation events more easily than escalation quality.

### OQ-12. Can authority survive delegation without distortion?

An orchestrator may summarize an instruction for a worker in a way that narrows or expands its scope. Repeated summarization can drift. What evidence would show that authority remained semantically intact through multiple handoffs?

### OQ-13. How should conflicting principals be represented?

Fleet work may involve several people with overlapping resources and incompatible preferences. The ladder assumes permission envelopes, but a theory of precedence, consent, confidentiality, and appeal among principals remains underdeveloped.

## Multi-agent systems and truth

### OQ-14. What counts as independent agreement?

Agents can share models, tools, sources, prompts, and hidden context. Independence may be a matter of degree across several dimensions. A formal account should distinguish evidential independence from organizational separation and stylistic diversity.

### OQ-15. Can deliberation make a group less accurate?

Discussion can correct errors, but it can also anchor participants on an early frame, reward fluency, or convert correlated reports into apparent consensus. Under what protocol conditions does deliberation improve calibration rather than merely increase confidence?

### OQ-16. Who owns a synthesized conclusion?

When several humans and AI systems contribute evidence, objections, and wording, attribution becomes complex. A synthesis needs provenance at the level of propositions, not only a list of participants.

## Memory and continuity

### OQ-17. What should an orchestrator remember?

Operational memory can include facts, commitments, procedures, preferences, failures, and authority. Too little memory causes repetition and amnesia. Too much causes staleness, leakage, and false confidence. The optimal memory is likely selective, but the selection rule is unresolved.

### OQ-18. How can memory prove its provenance?

A recalled instruction may have come from a user, another agent, a tool result, or a model's inference. If provenance is lost, an inference can return later as an apparent fact. What representations preserve provenance without making every operation unusably verbose?

### OQ-19. Is continuity a property of state or behavior?

Two systems can retain the same files while using them differently after restart. Conversely, a system might reconstruct equivalent behavior without preserving the original internal state. Tier 6 needs a behavioral account of continuity that does not fetishize one storage design.

## Long horizon and fleet behavior

### OQ-20. How should partial credit work over long horizons?

A long task may complete valuable phases and fail near the end. The value and risk of partial results depend on side effects, dependencies, and whether the remainder can resume. A single pass or fail label can hide the most important facts.

### OQ-21. What is the right model of a resource claim?

Claims can be exclusive, shared, prioritized, time bounded, preemptible, or delegated. Real devices also have human users who may intervene outside the agent system. A claim model that ignores physical possession and human priority will fail in the field.

### OQ-22. Can audit trails be complete without becoming surveillance?

Tier 8 asks for a human followable record of consequential actions. Complete capture can expose private content and create a new security target. The theoretical problem is to preserve accountability while minimizing retained sensitive detail.

### OQ-23. How do we evaluate rare catastrophic coordination failures?

The most important fleet failures may be infrequent and context dependent: duplicate irreversible actions, authority leakage, or two controllers issuing incompatible commands. Ordinary success rates can look excellent while tail risk remains unacceptable. Evaluation needs a principled treatment of rare events.

## Human factors

### OQ-24. Does visible process detail increase or decrease trust calibration?

Plans, logs, and status reports can help a human supervise. They can also create theater: a convincing appearance of control that exceeds actual state knowledge. The ladder values observed status over theatrical status, but the boundary is empirically difficult.

### OQ-25. What is the cost of interruption?

Tier 6 treats interruptibility as a virtue, but frequent steering can fragment work and introduce contradictions. A complete theory should measure not only whether steering is accepted, but whether the system integrates it coherently and knows when to ask for clarification.

### OQ-26. Can an agent know that it does not know?

Many tier failures involve an agent treating an unread source as empty or an unknown outcome as settled. Better uncertainty behavior may be trainable, procedural, or architectural. The open question is how to distinguish genuine calibrated uncertainty from formulaic hedging.

## Advancement pointers (expansion)

The questions above remain open. The following pointers record where the expansion material (PROTOCOLS, SCORECARDS, METRICS, TIER-DEEP-DIVES, PROPOSED-CLAIMS, EXAMPLE-ASSESSMENT, DELIB-0002) gives each question a working instrument or a registered claim, so future contributors start from the instrument rather than from the question alone. A pointer does not answer its question.

| Question | Instrument or claim that advances it |
| --- | --- |
| OQ-01 (denominator) | `docs/METRICS.md` section 1 (five denominators, dual reporting rule) |
| OQ-02 (repetitions) | `docs/METRICS.md` section 3 (consequence scaled minimums and escalation rule); PROTOCOLS preregistration (G1) |
| OQ-03 (lab to field) | PROTOCOLS G8 field validation stages (shadow, canary, field) with matched denominator gap reporting; supported by environment declaration (G2) and sampling audits (METRICS section 7) |
| OQ-04 (near misses) | `docs/METRICS.md` section 5 (NM-1 to NM-4 taxonomy and scoring rule) |
| OQ-05 (claim expiry) | CLM-017 (event based expiry); `docs/METRICS.md` section 7 watchlists |
| OQ-06 (determinism) | `docs/METRICS.md` section 6 (double scoring, kappa, disagreement ledger) |
| OQ-07 (cross product comparison) | PROTOCOLS G8 matched stage and matched environment rule (cross stage comparison named as a category error). Empirical comparability limits remain open |
| OQ-08 (linearity) | CLM-020 (per operation class verdicts); TIER-DEEP-DIVES Tier 8 partial participation |
| OQ-09 (profile vs score) | SCORECARDS gate blocks plus failure codes as the companion representation |
| OQ-10 (autonomy by scope) | Model claim syntax (scope, duration, reversibility, escalation) applied per action class in PROTOCOLS Tier 8 |
| OQ-11 (escalation quality) | CLM-015 (timing, payload, routing as separately scoreable dimensions) |
| OQ-12 (authority through delegation) | PROTOCOLS Tier 8 authority boundary test; fault FT-05 (authority echo) in the G8 catalog; CLM-010 remains the governing claim |
| OQ-13 (conflicting principals) | CLM-021 and DELIB-0004 (precedence rules P1 to P4 with enroller default and resurfacing). Field test defined; substantively open until a two principal packet exists |
| OQ-14 (independent agreement) | CLM-016 canary procedure (PROTOCOLS G4) as a detection instrument; CLM-006 governs interpretation |
| OQ-15 (deliberation harm) | CLM-022 (three conditions, factorial ablation) and DELIB-0005. Instrumented; no experimental result yet |
| OQ-16 (synthesis ownership) | CLM-023 (deletion test, proposition level attribution). Instrumented; audit of real syntheses outstanding |
| OQ-17 (what to remember) | CLM-024 selection rule (commitments, caused effects, authority, open questions retained; rest expires). Instrumented |
| OQ-18 (memory provenance) | CLM-024 origin classes with default expiry and the promotion ban; canary sealing (PROTOCOLS G4) as the anchoring technique. Instrumented |
| OQ-19 (continuity) | CLM-019 (behavioral continuity); TIER-DEEP-DIVES Tier 6 |
| OQ-20 (partial credit) | CLM-014 plus METRICS dual denominators (task outcome beside action effects) |
| OQ-21 (resource claims) | PROTOCOLS Tier 8 claim fields plus human takeover event (G6); owner class and preemption refined in DELIB-0004 |
| OQ-22 (audit vs surveillance) | CLM-018 as operationalized in DELIB-0003 (scored exposure axis); reconstruction exposure note (G5) |
| OQ-23 (rare catastrophes) | PROTOCOLS G8 injected fault catalog FT-01 to FT-07 with pass conditions; natural frequency treatment remains open by design |
| OQ-24 (process theater) | TIER-DEEP-DIVES Tier 6 (status from artifacts); SCORECARDS status gate requires artifact pointers |
| OQ-25 (cost of interruption) | TIER-DEEP-DIVES Tier 6 steering semantics (received, queued, applied, rejected); cost quantification open |
| OQ-26 (knowing ignorance) | METRICS section 8 (unknown precision, unknown recall, premature closure rate). Instrumented; per tier profiles outstanding |
