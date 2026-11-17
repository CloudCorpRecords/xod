# Live context and fleet evaluation

November 16, 2026 America/Los_Angeles (November 17 UTC). These are real
OpenAI requests made by Xod, not fixture replies. Native device acceptance,
Electron behavior and hours-long reliability are not claimed.

## Results at a glance

| Capability | Result | Actual evidence |
| --- | --- | --- |
| Large-input retrieval and amendment handling | PASS | 55,886 provider input tokens; 8/8 answer fields correct |
| Real worker delegation | PASS after repairs | Three initial local Xod workers, each calling the real cloud model and real scratch file tools |
| Bounded concurrency | PASS | Peak two executing workers; third waited 5,507 ms |
| Parent synthesis | PASS | Read-back summary total 142 before correction |
| Follow-up retention and correction | PASS | A fourth worker updated gamma; final read-back total 154, other region contents retained |
| Provider operational metadata | PASS | Each successful worker model call had actual response model, provider request ID and usage |
| Live context compaction | NOT EXERCISED | No compaction events in the short fleet run; deterministic tests exercise compaction |
| Long-duration or restart resilience | UNKNOWN | The final conversation took 52.072 seconds, not hours |
| Browser/Electron/native interaction | UNKNOWN | Tested the real worker execution core directly, not the chat UI or native shell |

## Large-input request

The repeatable input is a synthetic operational mission with 900 varied
inventory records, a contract near the beginning, a midpoint reference,
and a final amendment. The 178,028-character input is not a repeated
padding string. It asks for a structured answer, not a long narrative.

| Field | Observed value |
| --- | --- |
| First HTTP attempt UTC | `2026-11-17T03:31:50.437443+00:00` |
| Route | `https://api.openai.com/v1/chat/completions` |
| Requested, dispatched and returned model | `gpt-6.1-terra` |
| Successful provider request ID | `req_44a13c4f0c0c456b9fa92871593834f7` |
| Input / output / total tokens | 55,886 / 88 / 55,974 |
| Reasoning tokens included in output | 23 |
| Wall time including compatibility retries | 5.191 seconds |
| HTTP attempts | Two 400 compatibility refusals, then one 200 |

Eight field checks passed: project Cedar, beginning CEDAR-731, middle
HERON-248, ending QUARTZ-916, amended spending ceiling 42, reference
record 449, computed reference product 952, and prohibition on external
changes. Two additional checks required at least 30,000 provider input
tokens and the exact returned model. Score: 10/10 assertions, including
8/8 answer-content checks.

This proves retrieval and simple calculation across this input, including
the end amendment. It does not prove general comprehension of every
record, book-length generation, or a long-running task.

## Sustained conversation and real worker effects

The harness calls the same `worker.stream` execution core used by Xod,
which calls `team.delegate_many` and `team.run_subagent`, performs streaming
model calls, runs real file tools, and invokes the existing done-check.
It does not replace model responses or hard-code tool results. Discovery,
model dispatch, approval/sentinel checks and worker limits remain active.

All work occurs in a disposable directory. A test-only executor refuses
shell commands, external tools and any path outside that directory. The
agent is told that boundary. This is a deliberately restricted scratch
task, not evidence of unrestricted desktop abilities.

First turn: delegate one CSV to each of three workers. Each reads its own
source, computes a total and writes a JSON report. The parent reads those
reports, writes a summary and reads it back:

```json
{"project":"Cedar","alpha":51,"beta":46,"gamma":45,"grand_total":142}
```

Second turn: change gamma row g2 price from 7 to 11, while keeping the
original project and constraints. Xod chose to delegate the update to
one additional worker. Source and JSON read-backs, checked independently
by the harness, were:

```text
gamma.csv: g2,3,11
gamma.json: {"project":"Cedar","total":57}
summary.json: {"project":"Cedar","alpha":51,"beta":46,"gamma":57,"grand_total":154}
```

| Metric | Final live run |
| --- | --- |
| First HTTP attempt UTC | `2026-11-17T03:36:40.749493+00:00` |
| Conversation ID | `live-eval-c705b36f072045caabdc9a68ba4ab378` |
| First run ID | `2c7e789e50d246be916d0929f9bdbfea` |
| Correction run ID | `de22eaa1289548ef9afb0bd0caa7bab1` |
| Actual model | `gpt-6.1-terra` throughout |
| Wall time / turn times | 52.072 s / 28.981 s, 22.858 s |
| Requests | 33 HTTP attempts: 30 successful, 3 compatibility refusals |
| Provider input / output / total tokens | 95,349 / 1,954 / 97,303 |
| Estimated token counts | None |
| Initial workers' execution times | 5,806 ms, 5,507 ms, 7,246 ms |
| Third worker queue time | 5,507 ms |
| Correction worker execution time | 10,650 ms |
| Last provider request ID (completion check) | `req_0609a874b4064bd8bfc747145f11d520` |
| Last provider call usage | 965 input, 112 output |
| Final assertions | 9/9 passed |

Assertions cover three completed real workers, initial summary, alpha,
beta, corrected gamma, corrected summary, saved source correction,
bounded concurrency with no active worker left, and provider metadata.
Worker status remains a **worker report**; output assertions use actual
files, not the model's claim. Parallel model time can exceed wall time.

## Failures found and repaired

1. **One-model installations could not delegate.** Team hid the tool when
   all tiers resolved to one model. Added an explicit, default-off
   "Allow workers using the same model" setting in Agent > Team. Existing
   concurrency and approval limits remain in force. Multiple contexts
   can help with one model but consume extra requests; this is opt-in.
2. **Correct delegated work was repeatedly rejected.** The parent saw a
   worker's summary but dropped its locally recorded tool evidence before
   completeness checking. Preserve bounded actual tool evidence, paths
   and worker attribution in the existing checker. Outside-agent prose
   does not become execution evidence, and claims are not independent
   verification.
3. **Cloud retention silently used local limits.** The code consulted a
   nonexistent private locality method, then defaulted to local. The
   fallback now consults the configured provider; explicit worker-tier
   locality still takes precedence.
4. **Follow-ups silently clipped earlier instructions.** Replaced the
   five-message, 1,500-character-prefix behavior with bounded first-
   instruction and recent-message selection. Cloud/local character
   budgets are 48,000/12,000; retained messages preserve both ends when
   clipped and clearly mark omissions. This is not token-aware semantic
   compaction and does not promise full retention of arbitrarily large
   conversations.
5. **Operational correlation was insufficient.** Added payload-free
   lifecycle metadata using the existing event log rather than a second
   telemetry database or scheduler.

## Operational observability

Open Agent > Activity > a job > **Run diagnostics**. The existing detail
view exposes up to 100 recent operational events for that origin. The
existing operator `timeline` tool can filter by chat, run, batch, worker,
model-call and tool-call IDs, as well as actor and existing task/mission
IDs. Scope worker IDs with a run or batch since labels may repeat.

Events include:

- Run start and end, with stream-ended, approval-wait, error or cancellation
  outcomes. A stream ending is not a verified-success verdict.
- Worker queue, admission and terminal status; queue and execution timing.
- Model-call ID, each dispatch attempt, inference admission wait, HTTP
  status, provider request ID, requested and actual model, provider usage,
  finish reason, total duration, and failure/cancellation type.
- Tool-call IDs, start/end, duration and approval/error/uncertain-effect
  states; cancellation does not assert that an external effect did not occur.
- Tool-context reductions and history omissions with character counts.

The **new operational events** do not store prompts, response text,
tool arguments/results, credentials, or endpoint URLs. They use allowlisted
provider metadata. Existing activity, training and audit stores retain
their existing behavior; this is not a project-wide privacy audit.
Missing usage remains null rather than a fabricated zero. Existing
pricing currently produces an unmapped zero for Terra in the usage view:
**the test was not proven free, and no dollar cost is claimed.**
These are bounded local diagnostics, not durable distributed tracing or
cross-process fleet accounting.

## Full live-run accounting

Do not hide the failed attempts behind the final passing run:

| Run | HTTP attempts / successes | Provider total tokens | Outcome |
| --- | --- | ---: | --- |
| Large context | 3 / 1 | 55,974 | Passed |
| Baseline fleet | 20 / 17 | 50,025 | Failed: delegation unavailable, request budget exhausted |
| First repaired fleet | 36 / 33 | 98,838 | Failed: correct initial files, checker lost worker evidence; correction not completed |
| Final repaired fleet | 33 / 30 | 97,303 | Passed all nine assertions |
| **All runs** | **92 / 81** | **302,140** | No dollar-cost assertion |

The 11 failed HTTP attempts were compatibility refusals. Existing
compatibility handling learned the token-budget and temperature settings
in process memory; the streaming and non-streaming parameter-learning
paths still incur a separate temperature refusal in a fresh process.
No substitute model was used.

## Reproduction and deterministic checks

These commands require an authorized `OPENAI_API_KEY` supplied through the
environment. They may incur provider charges. Do not paste keys into
commands or files.

```sh
python tests/runtime/live_agent_eval.py --live --case long \
  --max-requests 3 --timeout 180 --output generated-artifacts/long-context-live.json
python tests/runtime/live_agent_eval.py --live --case fleet --same-model \
  --max-requests 36 --timeout 300 --output generated-artifacts/fleet-live-final.json
```

Each invocation removes the credential from its environment before Xod
imports, keeps it only in process memory, and isolates HOME, XDG and Xod
state. It uses the real model-selection writer in disposable storage.
It does not change the normal install or save the key to a file keyring.
Only the exact OpenAI route/model is admitted. Hard ceilings are 36 HTTP
requests, 600 seconds, 4,096 output tokens per dispatch and one million
cumulative input characters, with lower command-specific limits above.
Input character budgets include compatibility attempts and are not
equivalent to token or dollar limits.

Generated JSON evidence and logs are ignored by Git and deployment.
The files include synthetic task data and provider metadata, not secrets.
The workspace evidence files are:

- `generated-artifacts/long-context-live.json`
- `generated-artifacts/fleet-live-baseline.json`
- `generated-artifacts/fleet-live-verified.json`
- `generated-artifacts/fleet-live-final.json`

Deterministic checks passed, using isolated state and no live provider:

| Command | Passed |
| --- | ---: |
| `python tests/runtime/test_run_observability.py` | 17 tests |
| `node tests/runtime/test_agent_diagnostics.js` | 12 DOM assertions |
| `python tests/smoke/run_team_test.py` | 37 checks |
| `python tests/runtime/test_runtime.py` | 13 tests |
| `python tests/brain/run_longform_exec_fix_test.py` | 32 checks |
| `python tests/task_inbox/run_workers_test.py` | 43 checks |
| `python tests/smoke/run_sentinel_test.py` | 36 checks |
| `python tests/smoke/run_install_model_config_test.py` | 25 checks |
| `python tests/runtime/benchmark_team.py --check` | Scheduler invariants |

That is 203 Python tests/checks and 12 DOM assertions, plus scheduler
invariants. Synthetic checks cover clipping, tool compaction, redaction,
retry/usage metadata, cancellation cleanup, approval propagation and
same-model opt-in; they are not counted as live model evidence.

## Remaining proof gaps

- Real long-context behavior through all chat routing and mission-planning
  entrypoints, rather than this explicit LLM route plus actual worker core.
- Live compaction under extended tool output and many corrective turns.
- Multi-hour unattended work, crash/restart/reconnect, remote-effect
  uncertainty, and repeat runs for nondeterministic model reliability.
- Real native devices, Electron, local inference, external coding-agent
  fleets, and production deployment.
- Accurate dollar pricing or enforced monetary caps for this model.
- The diagnostics UI has DOM-contract coverage, not a real browser journey.
  The localhost-only guard remains intact.
