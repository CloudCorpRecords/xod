# Deliberation Protocol

## 1. Purpose

This repository is designed as a chain of reasoning, not a pile of disconnected opinions. The deliberation protocol lets humans and AI systems leave notes, inspect one another's work, dispute interpretations, and arrive at stronger conclusions while preserving the intellectual history of those conclusions.

The protocol values traceability over unanimity. A conclusion with visible dissent and a clear evidence path is more useful than a smooth consensus whose objections were deleted.

## 2. Append only rule

Notes are append only. An author does not edit a note after it enters a deliberation chain, except for a clearly marked administrative correction that does not change substance, such as a broken link repaired to the same target. Substantive corrections are made in a new note that links to the original.

No participant edits another participant's note. A reviewer may quote, summarize, challenge, or supersede an interpretation, but the original text remains attributable to its author and moment.

This rule protects against several failures:

- silent movement of a position after criticism;
- accidental erasure of minority evidence;
- false impressions that an early evaluator saw later artifacts;
- convergence manufactured by editing history.

## 3. Identifiers

Every deliberation has an identifier of the form `DELIB-NNNN`. Every note within it has an identifier of the form `DELIB-NNNN-NN`, assigned in chain order. Claims use `CLM-NNN`. Assessments use a system and date based filename. Reviews should cite the note, claim, run, or artifact they address.

Identifiers are stable. If a note is withdrawn by its author, the withdrawal is a new note. The original identifier is not reused.

## 4. Links and chains

Every note must link to the material it responds to. At minimum, a note names:

- the deliberation or claim it belongs to;
- prior notes it supports, challenges, extends, or corrects;
- evidence artifacts it relies on;
- tier files or model sections that supply the rubric;
- any assessment affected by its conclusion.

Links should be as specific as possible. "The earlier discussion" is weak. "DELIB-0001-02, objection 2, concerning the missing recovery run" is strong.

A deliberation chain is therefore a directed graph. Later readers can follow the path from a proposition through objections, additional evidence, rubric clarifications, and synthesis.

## 5. Roles

Roles describe functions in a round, not permanent identities. The same person or AI may occupy different roles in different deliberations. A participant should declare the role used for a note when the role affects interpretation.

### 5.1 Proposer

The proposer states a proposition narrowly, supplies its initial evidence, names its scope, and identifies what observation would weaken it. A good proposer argues for a claim and makes it easy to test.

### 5.2 Reviewer

The reviewer checks fidelity to evidence and rubric. Review may concern completeness, source quality, classification, arithmetic, internal consistency, missing runs, or untested gates. A review can be favorable. Its duty is accuracy, not opposition.

### 5.3 Skeptic

The skeptic attempts to falsify the proposition or find a cheaper explanation. The skeptic looks for selection bias, hidden human work, duplicate effects, stale state, false independence, environmental peculiarities, and artifacts that do not prove the asserted process. Skepticism should be specific. A general statement that AI systems are unreliable is not a useful skeptical note.

### 5.4 Synthesizer

The synthesizer reads the whole chain, distinguishes settled points from unresolved points, maps disagreements to evidence or rubric causes, and writes the current best conclusion. The synthesizer does not win the debate. The synthesizer accounts for it.

### 5.5 Evidence steward

An optional role responsible for packet integrity: stable links, run indexes, timestamps, hashes where available, and clear labeling of missing material. The steward does not decide the verdict merely by controlling the archive.

## 6. Note structure

A deliberation note contains:

1. identifier and title;
2. author or agent identity as declared by the contributor;
3. date;
4. role;
5. proposition or question;
6. links to prior notes and claims;
7. evidence relied on, with classes;
8. reasoning;
9. uncertainties and alternative explanations;
10. requested action, verdict, or next evidentiary step.

The canonical format is in `templates/NOTE-TEMPLATE.md`.

## 7. Debate rounds

A debate proceeds in rounds. A round is complete when the active proposition has received at least one substantive review and any material objections have either been answered, converted into evidence requests, or recorded as unresolved.

### Round 0: Framing

The proposer defines the question, scope, rubric, and evidence initially available. Framing should state what is not being debated. Many arguments persist because participants answer different questions with the same words.

### Round 1: Evidence inspection

Reviewers inspect artifacts and classify them. Challenges should target identifiable defects: an absent denominator, an unverified destination, a nonindependent confirmation, a missing lower gate, or a run whose environment differs materially.

### Round 2: Counterargument and supplementation

The skeptic presents the strongest alternative interpretation. The proposer may add evidence, narrow the claim, concede a defect, or defend the classification with rubric citations. Adding evidence is preferred to repeating rhetoric.

### Round 3: Synthesis

The synthesizer states the best supported conclusion, the verdict if any, points of consensus, points of dissent, and what new evidence would change the result. If consensus is absent, the synthesis says so.

### Later rounds

A closed deliberation can be reopened by new evidence, a demonstrated rubric error, a changed claim, or a relevant regression. It should not be reopened merely because a participant dislikes the outcome.

## 8. Convergence

Convergence means the remaining disagreement has been located and bounded. It does not require identical beliefs.

A deliberation may converge to:

- a shared verdict;
- a narrower claim that all reviewers can evaluate;
- agreement that the evidence is insufficient;
- agreement that the rubric is ambiguous and needs amendment;
- a split verdict with conditions clearly stated;
- a decision to await a specified class of evidence.

A synthesis that reports convergence should link to the notes that produced it. A synthesis that hides a live objection is invalid under this protocol.

## 9. Preserving dissent

Dissent is data. It may reveal a missing control, a domain exception, an ambiguous tier boundary, or a future regression risk. Dissenting notes remain linked from the synthesis with a fair one sentence characterization and a direct link.

A dissent should not be summarized in a way its author would not recognize. Where feasible, quote the decisive sentence or use the dissenter's own proposition line.

Consensus by exhaustion is discouraged. If participants stop replying, the synthesis should say the chain went quiet, not that all objections were resolved.

## 10. Verdict change rules

A verdict may change only when at least one of the following is documented:

1. **New evidence:** a run, artifact, or destination observation not previously in the packet materially affects a gate.
2. **Evidence reclassification:** an artifact is shown to belong to a different evidence class, to be invalid, or to concern a different environment or interval.
3. **Rubric error:** the prior verdict applied the wrong mandatory criterion, threshold, or definition.
4. **Claim narrowing or correction:** the proposition itself is revised, in which case the new verdict applies to the revised claim and the original remains in history.
5. **Regression or expiration:** later comparable evidence or material configuration change alters the current status.

A verdict may not change solely because a more famous system, a larger model, a project maintainer, or a majority of commenters prefers another answer. Votes can reveal where to investigate. They are not evidence of the underlying capability.

## 11. Synthesis files

A synthesis is a living summary with a stable location in the deliberation directory. It must include:

- current proposition;
- current verdict or state of disagreement;
- evidence considered;
- decisive arguments;
- dissent;
- open evidence requests;
- history of verdict changes and reasons;
- links to every note in the chain.

The synthesis is the entry point, not the replacement for the chain. Any strong sentence in a synthesis should be traceable to a note or artifact.

## 12. Conflict and conduct

Disagreement should be directed at propositions, classifications, and artifacts. Do not attribute motives to another contributor when a methodological explanation is available. Do not use private information as debate ammunition. Do not expose credentials, personal data, or restricted operational detail in an evidence packet.

AI contributors should disclose when they are summarizing material they did not directly inspect. Human contributors should disclose material conflicts, such as authorship of the assessed system, when known.

## 13. What makes a deliberation successful

A successful deliberation leaves the repository with at least one of the following:

- a better bounded claim;
- stronger or more honestly classified evidence;
- a rubric ambiguity made explicit;
- a false inference removed;
- a dissent that identifies a future test;
- a synthesis that lets a new reader reconstruct the reasoning quickly.

It need not produce agreement. It must produce clarity.
