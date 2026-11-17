# Agent verification without desktop dependencies

## What this build changes

This is a reliability pass on the existing parent and Team worker loops, not a
new orchestrator. It preserves BYOK/local models, Muse auto-approval policies,
and the separate approval gates on computer actions.

- Tool context is no longer cut through serialized JSON. Parent and fleet
  workers use the same structured representation and provider-specific limits.
- Oversized results prioritize errors, approval state, paths, exit codes and
  outcome fields, including in retained nested objects. Collections, depth and
  strings remain bounded. Truncation is explicit and requires rereading source
  material; it is not a promise to remember everything.
- Initial reductions emit `context.tool_bounded` with character counts only.
  Existing `context.compacted` events cover later reductions. Neither event
  stores the tool payload.
- A fleet worker cannot mark itself successful if every tool action failed.
  Recovery with a later successful action remains possible. Approval holds
  stay waiting, not ordinary failures.
- Continuation guidance distinguishes persistent constraints from completed
  one-time procedures. Targeted corrections should inspect and reuse existing
  work, not replay the original fleet. This is model guidance, not an exactly-once
  execution guarantee.

The complete, unabridged tool result still reaches the existing effect and
approval logic. Context excerpts do not grant permission, retry uncertain
actions or certify a worker's output. Worker verification remains
`worker_report`, not independent proof of correctness.

## First validate logic; then apply constraints

Run from a checkout with the project's normal Python dependencies installed:

```sh
python scripts/verify_agent.py --profile all --output generated-artifacts/agent-verification.json
```

This command runs deterministic tests, not live provider calls or native
desktop operations. It does not require Electron, a running Xod server,
desktop permissions, or API credentials.

The same long-sequence scenarios run under three envelopes:

| Profile | Tool context envelope | Purpose |
| --- | --- | --- |
| `correctness` | Test-only: up to 1,000,000 characters per result, 1,000 recent results | Isolate execution logic from ordinary context pressure |
| `cloud` | Current production cloud result/history limits | Check the same behavior with cloud-style result retention |
| `local` | Current production local result/history limits | Reapply the tight local result-retention constraints |

Profiles change only this test process. They do not modify user settings,
production defaults, model selection, trust rules, or auto-approval policies.
They use character budgets, **not a 200,000-token context-window claim**.
The parent stress test raises its step ceiling to 50 to exercise 45 actions;
the normal worker remains limited to 30 model steps and 25 minutes per run.
This is not a redesign of the durable task queue.

For separate reports:

```sh
python scripts/verify_agent.py --profile correctness --output generated-artifacts/correctness.json
python scripts/verify_agent.py --profile cloud --output generated-artifacts/cloud.json
python scripts/verify_agent.py --profile local --output generated-artifacts/local.json
```

To check repeatability with fresh processes, use `--repeat 3`. Repetition is
not an hours-long soak. Each child has a 180-second timeout by default,
adjustable with `--timeout` up to 600 seconds. A failed, timed-out or unavailable
check makes the final command fail. Negative-control tests verify that behavior.
The JSON report retains each result, duration, exit code and bounded output.
Reports can contain local paths in tracebacks; inspect before sharing.

## What is actually tested

`tests/runtime/test_long_running.py` runs real `worker.stream` and
`team.run_subagent` control flow with **scripted model replies**:

- 45 parent actions, large tool outputs, real disposable JSON-file reads,
  a mid-run revision from total 142 to 154, and independent final-file checks.
- 24 fleet-worker actions retaining structured failure and nested outcome
  information through repeated context reductions.
- Parent approval after 12 earlier actions, with the next requested tool
  demonstrably not executed.
- Failed approval results retaining their waiting state and original card ID.
- A worker that claims completion after all tools fail being marked partial;
  the corresponding recovery case is allowed.
- Cancellation during inference propagating and closing the model generator.
- Tool call/result pairing checked at each scripted model turn.

`tests/runtime/test_tool_context.py` covers valid JSON, state after large
output, nested structure, both-end text excerpts, bounded wide/deep/Unicode
objects, stable repeated reduction and explicit rejection of tiny budgets.
`tests/runtime/test_verification_runner.py` supplies negative controls for the
runner itself.

The command also runs existing lifecycle, task-stop, scheduling, observability,
Muse policy, approval reliability and Partner delegation checks. Their precise
results are in the exported report; a suite's zero exit is not a live-model score.

## Give this to an independent AI reviewer

Use the whole updated source checkout, not only this document or the JSON
report. Supply this prompt:

> Review this Xod checkout. Start with docs/AGENT-VERIFICATION.md, then inspect
> app/agent/context.py, app/agent/worker.py and app/services/team.py. Run
> python scripts/verify_agent.py --profile all and report failures without
> weakening the tests. Distinguish scripted model behavior from actual model
> decisions. Add adversarial cases for discarded middle content, nested
> approvals, wrong final answers, orphan tool results and cancellation.
> Do not bypass approval gates, repeat uncertain external effects, rewrite
> Git history, use personal credentials, or perform external actions.
> Report separately: execution-logic correctness, constrained-context results,
> live-model quality, long-duration behavior, and native desktop behavior.
> Passing one category is not evidence for another.

## Next layer: real models, still no native desktop

The existing opt-in commands in `docs/LIVE-AGENT-EVALUATION.md` call the actual
model through the worker core and use disposable files. They are billed,
request-bounded evaluations. They are not launched by the deterministic runner.
Use an explicitly authorized provider key; never copy it into a report.
Current live commands request the exact `gpt-6.1-terra` model and do not silently
substitute another model.

This pass ran the bounded two-turn fleet case twice with the authorized
`gpt-6.1-terra` key. Both runs used the same 36-request ceiling and no native
desktop. The failed run is retained, not overwritten:

| Observation | Before continuation guidance | After guidance |
| --- | --- | --- |
| Evidence file under `generated-artifacts/` | `fleet-harness-validation.json` | `fleet-harness-continuation.json` |
| Initial workers completed | 3 | 3 |
| Correction workers completed | 3, including unrelated regions | 1, affected region only |
| HTTP attempts / successful responses | 36 / 33 | 32 / 29 |
| Input / output tokens | 93,560 / 2,057 | 94,127 / 2,025 |
| Final summary total | 142, stale: FAIL | 154, correct: PASS |
| End-to-end assertions | 8/9 | 9/9 |
| Wall time | 54.502 seconds | 56.062 seconds |

The first run exhausted its HTTP request allowance. It updated gamma to 57
but did not update the summary before termination. The later run preserved
alpha/beta and correctly read back the changed summary. It used fewer requests,
but was **not faster** and did **not use fewer tokens**. Across both runs, provider
usage was 191,769 tokens. Dollar cost is UNKNOWN, not zero; the current pricing
table does not establish a Terra rate.

Neither live run emitted a context-compaction event. These are short real-model
execution/correction results, not live long-context pressure, repeated-run
reliability, an hours-long soak or native-device acceptance. The new stress
tests cover context pressure with scripted replies. The earlier large-input
evaluation remains historical evidence for its earlier code version.

Before native constraints, independently test real-model multi-turn correction,
delegation decisions, premature completion, and context pressure. Then test
restart recovery, real local inference and disposable native-device actions
separately. Unknown provider token limits and whole-app memory stay UNKNOWN.
