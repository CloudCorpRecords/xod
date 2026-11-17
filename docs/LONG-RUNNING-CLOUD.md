# Long-running cloud execution

Xod now has an **opt-in, model-scoped operating mode** in the existing worker
loop. It does not add another scheduler or replace Muse/Partner approval policy.
The normal local/cloud worker behavior remains the default.

## Enable it

Open **Agent > Team > Long-running cloud execution**.

1. Select the exact provider and model ID used by your installed cloud model.
2. Enter an operating context ceiling that your provider/model supports.
   Xod does not know an arbitrary BYOK model's maximum window and does not
   automatically assume that Terra, or any other model, supports 200k.
3. Review output/safety reserves and the request, time, input and step limits.
4. Enable the mode and save. Invalid settings are rejected, not silently clamped.

A different provider/model continues with normal limits. During an active
long run, a worker, automatic model selection, specialist or retry that would
dispatch to a different model is refused rather than escaping the configured
profile. Configure Team tiers to use that same cloud model when delegating.
Same-model delegation remains explicitly opt-in.

The controls save through `POST /api/agent/long-execution`. Settings live in
Xod's existing local settings store; no API key is added to these settings.

## Default limits after opting in

| Setting | Default |
| --- | ---: |
| Model context ceiling | UNKNOWN until entered |
| Output reserve / requested output per parent or Team call | 4,096 tokens |
| Additional context safety reserve | 4,096 tokens |
| Parent iterations | 120 |
| Iterations per Team worker | At most 40, also bounded by the parent setting |
| Shared admitted HTTP attempts | 160 |
| Active time | 3,600 seconds |
| Shared estimated input-token allowance | 2,000,000 |

The existing Team concurrency and batch limits still apply. Worker calls,
verification calls and compatibility retries share the parent's request and
input allowance. A resume retains spent iterations, attempts and time; it does
not grant a fresh budget. An explicitly new job starts a new allowance.

These are **not dollar-spending caps**. Unknown prices stay unknown. Admitted
attempts consume allowance even when transport fails before a response.

Time is enforced with an asynchronous deadline, not only between iterations.
Cancellation cannot prove that a remote or native effect did not happen:
in-flight intents remain blocked from automatic replay. After a hard crash
without a clean checkpoint close, unknown elapsed time is charged
conservatively, including some downtime.

## Token counting and compaction

Current admission uses **UTF-8 bytes plus message/protocol overhead as a
conservative token estimate**, including tool schemas, tool arguments/results
and message framing. It is not a model-specific tokenizer or measured provider
input usage. Multimodal requests are refused in this mode because their token
cost cannot be bounded by this estimator.

For a configured context ceiling `C`, output reserve `O` and safety reserve `S`:

- Available input budget is `C - O - S`.
- Compaction is considered above 85% of that estimated input budget.
- The target after compaction is 70%.
- The fully reconciled request is checked again immediately before admission.
- Provider retries cannot grow output beyond the configured reserve.

The cloud mode replaces the old five/six/fourteen-result and 48,000-character
history reductions for its parent loop. It retains the full prior text
conversation, subject to actual request admission. Individual results still
have a size limit based on available context, preventing one read from consuming
the entire window.

Before compaction, Xod archives the current transcript locally. Compaction is
deterministic, not an AI-written success summary:

- User and system instructions remain verbatim.
- Tool calls and all their replies stay paired.
- Unresolved approvals and unknown-effect exchanges are never dropped or
  shortened to make room.
- Older completed tool results become structured excerpts; whole old completed
  exchanges can be omitted.
- Recent results normally remain whole. If those alone prevent fitting, safe
  recent results may also become explicit excerpts.
- A mechanical notice points to the local transcript archive and tells the
  model not to replay an action because its output is absent.
- If protected material still cannot fit, the run pauses. Instructions are not
  silently removed to force a request through.

This is **not semantic memory or guaranteed retention of every fact**. The
model may need to reread artifacts. Archive files preserve the transcript the
loop had before this reduction, not tool data already excluded by an earlier
individual-result cap.

## Checkpoints and recovery

Agent > Team lists recent checkpoint IDs, status, model, admitted attempts,
iterations and compaction count. In the **original conversation**, type:

```text
resume checkpoint <32-character checkpoint ID>
```

The checkpoint ID is validated. Cross-conversation/task resumes and completed
checkpoints are refused. Ordinary **continue** also carries the checkpoint ID
when Xod delivered its checkpoint-bearing response. After a budget pause,
increase the intended limit explicitly before resuming if needed; counters are
retained. Extra continuation instructions become part of the resumed goal.

Checkpoints record conversation, execution log, limits and pending tool intents.
They are written before tool dispatch, using an atomic replacement. Failure to
write stops further effects. A cross-process OS lease prevents two owners from
resuming one checkpoint simultaneously.

**No automatic replay** is permitted if the checkpoint has an unfinished,
uncertain or returned-but-not-yet-committed tool intent. An approval hold is also
not resumed automatically, even if someone later clicked its card. Review the
original approval/effect through the normal workflow and start a clearly scoped
new request after resolving it. There is intentionally no “clear uncertainty”
button that could silently authorize repeating a send or click.

Provider failure can leave an interrupted or unverified checkpoint. A terminal
stream event alone is never a completed-work verdict.

### Private files

Files are under `<Xod data directory>/run_checkpoints/<ID>/`:

- `checkpoint.json`: current state;
- `context-*.json`: pre-compaction transcripts;
- `lease.lock`: process ownership.

On the tested Linux filesystem, directories are private (0700) and state files
are 0600. Windows uses its own OS byte-range lock, but its native locking and
permissions behavior has **not** been accepted on a real Windows device.
Checkpoints may contain private prompts, tool arguments and excerpts, including
sensitive material present in the task itself. Provider connection credentials
are not copied from endpoint configuration. Do not publish checkpoint folders.

Each file is capped at 12 MiB and each run folder at 64 MiB. Quota exhaustion
pauses the run. There is no automatic deletion of old runs, since deleting
unfinished recovery evidence would be destructive. Only remove a run folder
after stopping it and deciding its recovery data is no longer needed.

## Verification in this checkout

```sh
python scripts/verify_agent.py --profile all --output generated-artifacts/token-aware-verification.json
node tests/runtime/test_agent_diagnostics.js
```

`tests/runtime/test_long_execution.py` covers near-200k **estimated** context,
instruction retention, transcript pairing, pinned effects, quotas, private
atomic writes, process leases, crash/reload, model mismatch, shared attempts,
input/output reserves, hard deadlines, continuation beyond 30 iterations,
resume without repeating recorded effects, and approval/uncertainty boundaries.
`tests/runtime/test_long_execution_ui.js` checks the actual rendering and save
handlers, including escaped content and rejected settings.

### Real-provider results

Both evaluations used the authorized `gpt-6.1-terra` route, disposable files and
no browser/Electron/native desktop.

| Case | Result |
| --- | --- |
| Two-turn fleet, long mode, 36-request external test ceiling | **FAILED overall.** All nine artifact/concurrency/metadata checks passed, but the external test allowance expired before final verification. First checkpoint completed; second interrupted. 36 HTTP attempts, 33 successful responses, 120,721 provider tokens, 58.232 seconds. |
| Ten-source context pressure, long mode, 20-request ceiling | **PASSED 5/5 checks.** All ten marker pairs correct, real compaction observed, final verification passed, checkpoint completed, request counter bounded. 18 attempts, 15 successful responses, 122,622 provider tokens, 51.348 seconds. |

The passing pressure run used a **65,536-token configured operating ceiling**,
not a claim about the model's maximum window. It performed **five live
compactions**. Estimated pre/post sizes were 68,328/27,596; 75,780/34,562;
58,714/38,104; 49,568/38,747; and 50,270/39,762. These are the conservative
estimator's values, not five measurements of actual provider input tokens.

Failed evidence is retained in `generated-artifacts/token-aware-fleet-live.json`;
passing evidence is `generated-artifacts/token-aware-compaction-live.json`.
Combined provider usage was 243,343 tokens. Dollar cost is UNKNOWN, not zero.

Repeat the billed pressure case only with an authorized key:

```sh
python tests/runtime/live_agent_eval.py --live --case context --long-execution \
  --context-tokens 65536 --max-requests 20 --timeout 240 \
  --output generated-artifacts/token-aware-compaction-live.json
```

The fleet harness now checks terminal verification separately from file
correctness. Correct files alone are not enough to pass it.

## Not yet proved

- 200k **actual provider tokens** through this mode, or exact model-specific
  tokenization/context metadata.
- Multi-hour autonomous work or reliable success rates across repeated tasks.
- Full native app recovery after real Mac/Windows process crashes.
- Dollar-denominated per-job spending enforcement.
- Seamless continuation through unresolved effects or approvals: these
  deliberately require review rather than automatic replay.
