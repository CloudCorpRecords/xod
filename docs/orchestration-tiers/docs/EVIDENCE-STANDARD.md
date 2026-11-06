# Evidence Standard

## 1. Principle

A capability claim is only as strong as the path from an observed event to the conclusion drawn from it. This standard defines that path.

Evidence is not a pile of screenshots, a confident transcript, or a statement that a tool returned success. Evidence is a preserved record that allows another evaluator to determine what was requested, what environment applied, what the system did, what happened at each relevant destination, what failed, and what remains unknown.

## 2. What counts as evidence

### 2.1 Observed runs

A run record should include the task, start and end times, system configuration, environment, inputs, protocol, outputs, side effects, failures, interventions, and final status. A run is stronger when the task was specified before execution and the denominator includes abandoned, failed, and retried attempts.

### 2.2 Destination read backs

For an action, the preferred evidence is observation at the destination after the action. Examples include reading the created record from the target system, confirming a message in the recipient visible thread, inspecting the final file at its path, or querying an external service state.

An action API returning success is evidence that a request was accepted or processed by that API. It is not complete evidence that the intended real world state resulted.

### 2.3 Logs and state artifacts

Logs are evidence when they are contemporaneous, timestamped, attributable to a run, and sufficiently complete to reconstruct material transitions. Useful artifacts include plans, task state snapshots, worker briefs, result envelopes, approval records, delivery records, hashes, and error traces.

Logs lose force when they are edited without provenance, generated after the fact from memory, missing failure intervals, or impossible to connect to the claimed output.

### 2.4 Repeated trials

Repetition turns anecdote into a pattern. Trials should vary surface details while preserving the capability being measured. Repetition under identical conditions can detect flakiness. Repetition under relevant variation tests robustness. Both are useful, and an assessment should say which was performed.

### 2.5 Independent checks

An independent check uses a source or method not wholly controlled by the original claim path. Independence is a spectrum. A second agent reading the same self report is weakly independent. A destination query through a separate interface is stronger. A skeptic attempting to find duplication or missing delivery can provide independence even when using some shared infrastructure.

### 2.6 Negative and failed evidence

A failed run is evidence. A timed out source is evidence about coverage, not evidence that the target was empty. A contradictory destination record is strong evidence against a specific success claim. Suppressing failures invalidates the denominator and therefore weakens every positive inference drawn from the set.

## 3. What does not count, by itself

The following do not establish a tier by themselves:

- vendor or developer assertions;
- a edited demonstration;
- a benchmark score from a different construct;
- a transcript with no underlying artifacts;
- an agent's statement that it verified something, without the verification artifact;
- a successful final artifact when the process claim concerns delegation, interruption, recurrence, or coordination;
- screenshots detached from time, task, and destination context;
- testimonials;
- agreement among agents who all read the same unverified report;
- absence of complaints;
- a plan that was written but not executed;
- a job definition that exists but has not been observed running.

Such material may be included in a packet as context. It should be labeled with its evidence class and limits.

## 4. Evidence packet

Every substantial assessment should have an evidence packet with the following sections:

1. **Claim:** the proposition being assessed, in model syntax.
2. **Subject:** system, configuration, environment, and interval.
3. **Protocol:** tasks, rubric version, repetitions, and variations.
4. **Run index:** every run, including failures, retries, aborts, and exclusions with reasons.
5. **Artifacts:** links or identifiers for logs, outputs, read backs, state snapshots, and approvals.
6. **Interventions:** every human or external intervention and its effect.
7. **Destination verification:** where each material effect was observed.
8. **Failures and unknowns:** unresolved contradictions, inaccessible sources, and untested gates.
9. **Evaluator notes:** classification decisions and disputed interpretations.
10. **Verdict derivation:** gate by gate reasoning from evidence to verdict.

A packet should be stable enough that a later reviewer can cite a particular run or artifact without relying on a mutable summary.

## 5. Outcome vocabulary

Use these words precisely:

- **CONFIRMED:** the intended outcome was observed at the relevant destination or through an adequate independent source.
- **FAILED:** the attempt was made and the intended outcome did not occur, or a mandatory condition was violated.
- **UNKNOWN:** the attempt's outcome could not be established from available evidence.
- **PARTIAL:** some specified components were confirmed and others failed or remain unknown. The components must be enumerated.
- **NOT RUN:** the protocol step was not attempted. This is different from failure and different from unknown outcome after an attempt.

Never convert UNKNOWN to FAILED merely because a tool timed out. Never convert UNKNOWN to CONFIRMED because a later retry succeeded. A retry is a new attempt with its own outcome, and the possibility of duplicate effects from the first attempt must be handled explicitly.

## 6. Read back discipline

Before claiming an action complete, ask:

1. What state should exist if the action worked?
2. Where would that state be visible?
3. Did I observe that state, or only a proxy?
4. Could the observed state have been produced by an earlier attempt or another actor?
5. If the first attempt's outcome was unknown, could a second attempt duplicate it?
6. Does the observation cover all destinations or only one?

For Tier 8, read back discipline applies at every endpoint, not merely at the final synthesis point.

## 7. Source conflict

When sources conflict, record both, identify which source is authoritative for the specific fact, and seek an underlying record where feasible. Do not average incompatible values into a false compromise. Do not discard the less convenient source without a reason tied to provenance, freshness, scope, or demonstrated error.

A failed source is UNREAD, not EMPTY. A search result snippet is a lead, not a destination record. A cached value is evidence about the cache at its timestamp before it is evidence about the present world.

## 8. Time and freshness

Every material fact should carry the time at which it was observed and, where relevant, the time to which it refers. Current interface text, prices, schedules, account states, and software versions can change. An old artifact can prove a historical event while proving little about the present.

Assessments should distinguish:

- event time;
- observation time;
- artifact creation time;
- report time;
- claim interval.

Confusing these times is a common source of false confidence.

## 9. Evidence integrity

Where practical, retain hashes, immutable identifiers, signed records, export timestamps, and repository commits. If material is redacted for privacy or security, preserve enough structure to evaluate the claim and state what class of material was withheld.

Redaction should protect secrets and personal information. It should not be used to remove failures, approvals, side effects, or other facts material to the verdict.

## 10. Honest uncertainty

An evaluator may conclude that the evidence does not decide the question. That is a successful act of evaluation, not a missing result. UNKNOWN and UNSUPPORTED verdicts protect the registry from accumulating folklore.

The standard is simple: report the strongest proposition the evidence actually carries, name what would strengthen it, and do not borrow confidence from the importance of the question or the reputation of the system.
