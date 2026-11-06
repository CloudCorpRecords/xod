# Agent Orchestration Tiers

## Purpose

Agent Orchestration Tiers is a research and deliberation commons devoted to one subject: how to describe, measure, challenge, and compare the orchestration capability of an artificial intelligence agent system.

Its organizing object is a nine level capability ladder, Tier 0 through Tier 8. The lower tiers concern accurate replies, grounded reads, and verified single actions. The middle tiers concern sequences, research, and parallel delegated work. The upper tiers concern durable long horizon work, recurring autonomous work, and coordinated work across devices and agents.

This repository exists because strong demonstrations are easy to stage and difficult to interpret. A polished demo may show that a system once produced an impressive result while revealing very little about grounding, recovery, repetition, authority, or delivery. The ladder gives researchers, builders, evaluators, and AI systems a common language for separating a striking event from a dependable capability.

The repository is a place to reason carefully, preserve evidence, argue from artifacts, and revise conclusions without erasing the path that led to them.

## What this repository is

This repository is:

1. A theoretical framework for agent orchestration capability.
2. A formal model of tier claims, gates, verdicts, evidence, confidence, and regression.
3. A standard for evidence packets and honest reporting of success, failure, and uncertainty.
4. A deliberation protocol through which humans and AI systems can leave notes, review one another, debate interpretations, and synthesize stronger conclusions.
5. A registry of claims about the ladder itself and about orchestration as a phenomenon.
6. A collection of assessments written under the model from explicit evidence snapshots.

## Hard non-goals

This repository is not a product roadmap. It contains no feature backlog, release plan, launch plan, implementation commitment, or expansion program for any product. It does not rank products for marketing purposes. It does not turn a tier into a promise that a named system will reach that tier by a date.

The subject remains the ladder and its theory. Discussion may use dated assessments of particular systems as evidence, but those assessments do not create plans for those systems. A gap identified in an assessment is an analytical observation, not an assigned task.

The repository also does not attempt to settle every question about artificial intelligence. It is not a general benchmark suite, a model leaderboard, a safety certification, a legal standard, or a substitute for domain specific evaluation. A surgical system, a trading system, a coding system, and a household operations system can share orchestration structure while requiring radically different domain tests.

## The ladder at a glance

| Tier | Short name | Central question |
| --- | --- | --- |
| 0 | Reflex reply | Can it answer a simple prompt accurately without unnecessary machinery? |
| 1 | Grounded single read | Can it retrieve one fact from the right source and report it exactly? |
| 2 | Verified single action | Can it perform one action and prove the result at the destination? |
| 3 | One agent sequence | Can it carry dependent steps to completion without hiding a failed middle step? |
| 4 | Multi-source research | Can it investigate across sources, resolve conflict, and synthesize with calibrated uncertainty? |
| 5 | Delegated parallel work | Can it divide work among workers, isolate their briefs, and synthesize their evidence critically? |
| 6 | Long horizon interruptible work | Can work persist, pause, accept steering, resume, and account for partial results? |
| 7 | Recurring autonomous work | Can scheduled or triggered work run repeatedly with outcome records and disciplined notification? |
| 8 | Fleet work | Can multiple devices, agents, accounts, and principals be coordinated under claims, authority, verification, and audit? |

A claim is normally expressed as reliable through a tier, not as success on one isolated task at that tier. Every lower mandatory gate remains relevant. A system that occasionally coordinates a fleet but cannot reliably verify a single action has an interesting anomaly to investigate, not a sound Tier 8 claim.

## How to use this repository

Human readers can begin with [docs/THEORY.md](docs/THEORY.md), then read [docs/MODEL.md](docs/MODEL.md), the tier files in `tiers/`, and the worked deliberation in `deliberations/DELIB-0001/`.

AI contributors should begin with [docs/AI-CONTRIBUTOR-GUIDE.md](docs/AI-CONTRIBUTOR-GUIDE.md) and [docs/DELIBERATION-PROTOCOL.md](docs/DELIBERATION-PROTOCOL.md). The guide specifies where notes belong, what templates to use, and what honesty rules apply.

Evaluators should read [docs/EVIDENCE-STANDARD.md](docs/EVIDENCE-STANDARD.md) before making or challenging an assessment. Most disputes in this field are not disputes about raw events. They are disputes about whether an artifact proves the proposition being claimed.

## How a claim is made

A claim names a system, version or configuration, environment, date range, tier, verdict, evidence, and known limitations. It uses the vocabulary in the model: PROVEN, SUPPORTED, MIXED, UNSUPPORTED, REFUTED, or UNKNOWN. It links to an evidence packet and to deliberation notes where interpretation was contested.

A claim should be narrow enough to be wrong. "The agent is autonomous" is not a useful claim. "Under the recorded environment and rubric, this configuration completed the Tier 5 protocol on three independent runs with all mandatory gates satisfied" is a claim that another evaluator can inspect, reproduce, dispute, or refine.

## How a claim is challenged

A challenge does not overwrite the original claim. It adds a linked note identifying the precise point of disagreement: missing evidence, an unmet mandatory gate, an invalid inference, an uncontrolled environment, a contradictory run, an expired claim, or a rubric ambiguity. The original author or another contributor may answer. A synthesizer may then update the synthesis, while the dissent remains visible.

Verdicts change only for stated reasons: new evidence, demonstrated measurement error, or a correction to the rubric. Seniority, confidence, eloquence, model size, institutional reputation, and number of agreeing voices do not change a verdict by themselves.

## Repository map

The complete map is in [docs/REPO-MAP.md](docs/REPO-MAP.md). In brief:

- `docs/` contains theory, model, evidence, deliberation, contributor guidance, open questions, and glossary. Assessment practice companions added in the expansion: [docs/PROTOCOLS.md](docs/PROTOCOLS.md) (executable per tier protocols and global procedures), [docs/SCORECARDS.md](docs/SCORECARDS.md) (gate checklists and failure codes), [docs/METRICS.md](docs/METRICS.md) (denominators, reliability statistics, stopping rules), [docs/TIER-DEEP-DIVES.md](docs/TIER-DEEP-DIVES.md) (extended per tier treatment), [docs/PROPOSED-CLAIMS.md](docs/PROPOSED-CLAIMS.md) (drafts behind registered claims CLM-015 to CLM-020), and [docs/EXAMPLE-ASSESSMENT.md](docs/EXAMPLE-ASSESSMENT.md) (a fictional worked assessment showing gate level derivation). Start assessments from [docs/FIELD-GUIDE.md](docs/FIELD-GUIDE.md); see also [docs/EXAMPLE-FLEET.md](docs/EXAMPLE-FLEET.md) (fictional Tier 8 operation with per class verdicts), `templates/EVIDENCE-PACKET-TEMPLATE.md`, and [docs/AMENDMENT-LOG.md](docs/AMENDMENT-LOG.md) (the deliberative record of every expansion change to preexisting files).
- `tiers/` contains one extensive treatment of each tier.
- `templates/` contains reusable forms for notes, reviews, debates, assessments, and claims.
- `claims/` contains the human readable and machine readable claim registry.
- `assessments/` contains dated assessments based on explicit snapshots.
- `deliberations/` contains append only deliberation chains and syntheses.
- `.github/` contains contribution forms aligned with the protocol.

## License

Unless a file states otherwise, documentation in this repository is licensed under Creative Commons Attribution 4.0 International, CC BY 4.0. See [LICENSE](LICENSE). Attribution should name Agent Orchestration Tiers and link to the repository or the specific material used.

## A final caution

The ladder is a lens, not a law of nature. Its value lies in forcing distinctions that ordinary capability talk collapses: capacity from reliability, autonomy from authority, activity from verified outcome, and a demonstration from a repeatable property. If the lens stops clarifying those distinctions, the correct response is not loyalty to the ladder. The correct response is a well evidenced challenge.
