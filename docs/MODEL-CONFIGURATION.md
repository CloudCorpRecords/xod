# Install-level model configuration

## User flow

Open Settings > Models, select a provider, enter the model name, and save.
Built-in MLX, local servers, OpenAI, Fireworks and the other existing providers
use the same install-level selection. Quick picks, the AI models panel and
paired-device picks are shortcuts to that same writer, not independent model
preferences. Existing seeing, memory-search, explicit tier routing, specialists,
and opt-in Auto model choices keep their separate documented purposes.

For MLX, enter the repository name of an already downloaded built-in model.
Download/manage built-in models in Setup. MLX requires Apple silicon; the
option explains its hardware restriction and a backend check refuses an
unsupported selection before changing anything.

For cloud models, users bring their own provider key or select an existing
key profile. Named cloud providers use their own endpoints. Fireworks supports
its dedicated HTTPS endpoints as well. Add other gateways under AI models >
Model servers with that gateway's own key, then select the added server.
No Replit integration or Replit-specific package is required by an OSS user.

After saving, "Test saved model" sends a small, possibly billed request through
the actual LLM service. It deliberately bypasses Auto model selection and
specialists. It reports the configured provider, model and route hostname,
plus the model actually dispatched and any model ID returned by the provider.
A different dispatched/returned model, fallback, or empty answer is not counted
as successful verification. Dated snapshots of the same base model are accepted.
If a provider omits its response model ID, that field remains null; a connection
test cannot independently certify the provider's internal model implementation.

## Shared contract and compatibility

- `setup_wizard.validate_choice()` performs side-effect-free normalization
  and validation. `setup_wizard.choose()` is the shared install-level writer.
- `/api/config` preflights model fields before saving any keys or unrelated
  settings, then uses that writer rather than directly writing three fields.
- `config.save_model_choice()` commits provider, model, endpoint and
  `model_chosen_by` in one SQLite transaction, then updates the runtime
  snapshot together. The legacy non-secret env mirror remains best effort.
  SQLite continues to win at startup, including explicitly empty saved values.
- Selection validation, the auto-pick ownership guard and the write share a
  reentrant lock. Main runtime endpoint reads share that lock, so they cannot
  read a partially changed selection. The user's saved pick still cannot be
  overwritten by an automatic boot choice.
- Existing `llm_provider`, `llm_model`, and `llm_base_url` records are read
  unchanged. There is no destructive settings-format migration or credential
  migration. The next explicit save normalizes stale cloud endpoint metadata
  without changing an explicitly named model. Existing custom local endpoints
  survive opening Settings and restarting.
- Provider defaults and named endpoints come from the backend. Changing a
  provider in Settings changes both defaults instead of keeping an unrelated
  provider's model or URL. An unsaved key is cleared on provider change.
- Settings refreshes the current install choice when opened, including changes
  made through another picker or a paired device.
- Key profiles remain supported and profile-only keys count as available in
  both Settings and the model picker. Credentials are not in public model
  metadata, ordinary settings or the env mirror.
- Local-server validation parses the hostname; localhost-like public names
  and credentials embedded in endpoint URLs are refused.
- Invalid provider/model/address and unsupported hardware errors are shown
  without silently switching. Key/credit errors are actionable, and echoed
  credentials in model errors are redacted.

## Verification evidence

The following checks passed on Linux in this Replit on November 16, 2026:

| Command | Passed checks | Evidence scope |
| --- | ---: | --- |
| `python tests/smoke/run_install_model_config_test.py` | 25 | Isolated keyring, real ASGI config paths, transactional rollback, actual LLM dispatch with synthetic provider HTTP, provider switching, streaming, profile keys, subprocess restart, validation and hardware refusal |
| `node tests/runtime/test_model_settings.js` | 8 | UI DOM contracts for preserving saved local values, server defaults, key clearing, MLX status, added servers, test results/errors and settings refresh |
| `python tests/smoke/run_model_switching_test.py` | 18 | Terra picker/current/default consistency, synthetic credentials only |
| `python tests/smoke/run_provider_boot_guard_test.py` | 14 | User choices remain protected from automatic boot picks |
| `python tests/smoke/run_provider_promotion_test.py` | 10 | Boot/promotion rules with arrived cloud keys and saved providers |
| `python tests/smoke/run_models_test.py` | 31 | Existing model catalog, tool/vision/scoring behavior, added servers and connectors with a stand-in Ollama |

The new install test uses temporary HOME, XDG config/data and Xod data paths,
an explicitly synthetic file keyring, and no developer env file. It never
uses an actual provider credential or starts an MLX engine. Its provider
responses, including requests named gpt-6.1-terra, are fixtures, NOT live
Terra evidence. The positive MLX selection case fakes hardware/download
availability and verifies wiring only. Native MLX generation is UNKNOWN.

Syntax checks and `git diff --check` also passed. No full browser test was
needed: changed persistence and runtime paths were exercised through ASGI and
isolated process/HTTP fixtures; UI controls have deterministic DOM checks.
The configured preview was restarted once and its first-run page rendered.
The changed scripts and `/api/config` loaded successfully, with no JavaScript
exception. The favicon returned 404; that is not a model connection result.
This Linux sandbox reports no native keyring backend, no local model server,
and missing macOS voice/sysctl utilities. Real OS-key-store saves and native
generation are therefore not claimed. The localhost-only access guard remains
enabled; external preview-proxy requests still receive 403 by design.

## Live Terra result: PASS through the configured OpenAI route

Requested model ID: `gpt-6.1-terra`.

Initial November 16, 2026 access checks were:

1. OpenAI integration discovery returned an empty integration list for this
   workspace.
2. Approved existence-only secret checks reported `OPENAI_API_KEY`,
   `AI_INTEGRATIONS_OPENAI_API_KEY`, and `FIREWORKS_API_KEY` absent. No secret
   value was retrieved or printed.
3. Official Replit documentation confirms managed AI access is a platform
   capability, but does not establish that an authorized route or Terra model
   access is available to this workspace.

The user subsequently supplied `OPENAI_API_KEY` through Replit Secrets and
explicitly authorized testing with that key. Its Replit-managed provenance
was not established; this verifies the authorized OpenAI route, not managed
integration provisioning.

Live evidence, November 16 in America/Los_Angeles:

| Field | Observed value |
| --- | --- |
| Successful run start (UTC) | `2026-11-17T01:39:43.872413+00:00` |
| Configured provider and route | `openai`, `https://api.openai.com/v1` |
| Saved, dispatched, and returned model | `gpt-6.1-terra` in all three |
| Final HTTP status | `200` |
| Successful request ID | `req_3c5f7c9b984944c1a22387af9732e7be` |
| Reply | `Xod live check OK` |
| Usage | 15 prompt tokens, 19 completion tokens, 34 total; 5 reasoning tokens included in completion |
| Run duration | 4.285 seconds including compatibility retries |

The check used the real shared selection writer, persisted the selection in
temporary isolated state, read the actual runtime endpoint, and called
`llm_service.chat_completion()` with that explicit endpoint. Auto and
specialist substitution were bypassed. Actual HTTP responses were observed,
not replaced with fixtures. The key was consumed only in process memory from
the approved Secret, never saved to a fixture keyring, env file, or database.
The normal installation's model selection was not changed.

Four live HTTP requests were sent across two invocations. The first
invocation was capped at one attempt and received HTTP 400. The second
allowed three attempts: OpenAI rejected `max_tokens`, then `temperature`;
Xod's existing reactive compatibility handling changed the token-budget
parameter to `max_completion_tokens` and omitted temperature, then received
the successful response. No product inference logic was modified for this
test, and no other model was substituted. These compatibility choices are
cached for the life of the process, not across restarts.

This is a small non-streaming text inference check, not evidence of live
tool execution, streaming, native MLX generation, or managed integration
provisioning. Missing access and failed requests must still be reported
honestly. Never copy test credentials into product files or require them
for OSS users.

## Live long-context and agent evaluation

The follow-up evaluation on November 16, 2026 sent a genuine 55,886-input-token
mission through `llm_service.chat_completion()` to the configured
`gpt-6.1-terra` OpenAI route. Request
`req_44a13c4f0c0c456b9fa92871593834f7` returned the same model, 88 output tokens
(55,974 total), and correct values for all eight beginning/middle/end and
amendment checks. Duration including compatibility retries: 5.191 seconds.
The reply stayed consistent through the final amendment in this structured
retrieval task; this is not a claim of general comprehension or a long soak.

A separate real two-turn worker conversation delegated three initial file
jobs with peak concurrency two, synthesized a read-back total of 142,
then applied a source correction via a fourth worker and verified 154.
It passed nine assertions in 52.072 seconds, using 95,349 input and 1,954
output tokens across 30 successful calls (33 HTTP attempts). The final
completion-check request was `req_0609a874b4064bd8bfc747145f11d520`.
Initial failures exposed and led to repairs in same-model delegation and
parent checking of worker evidence.

See `docs/LIVE-AGENT-EVALUATION.md` for reproduction, all failed-run accounting,
operational metadata, exact limitations and the distinction between large-input
recall, multi-turn work, synthetic compaction checks and untested long-duration
behavior. No native, Electron, managed-key-provenance or dollar-cost claim is
added by these results.

## Round 4: larger real request and exact reproduction

On November 16, 2026 (Pacific), the existing live harness sent one larger
long-context input through the same shared LLM service and authorized OpenAI
route. The test uses 1,800 varied operational records, not repeated filler.
Provider-reported input usage, not characters or estimates, determines whether
the 100,000-input-token requirement passes.

| Field | Observed value |
| --- | --- |
| Run start (UTC) | `2026-11-17T05:50:33.664004+00:00` |
| Run start (America/Los_Angeles) | `2026-11-16T21:50:33.664004-08:00` |
| Provider and route | `openai`, `https://api.openai.com/v1` |
| Saved, dispatched and returned model | `gpt-6.1-terra` in all three |
| Successful request ID | `req_8817f05184c14545bcaa7c15c20f7ae0` |
| Final HTTP status | `200` |
| Provider-reported input tokens | **112,486** |
| Completion / total tokens | 59 / 112,545 |
| Reported cached input / reasoning tokens | 0 / 0 |
| Duration including compatibility retries | 5.553 seconds |
| Synthetic input characters | 357,936, not a token count |
| Input SHA-256 | `988abeabb125d6e42ee5c92bcc38ccda1e50968be126f0d187cde4c08d724180` |
| Outcome | **PASS, 10/10 checks** |

Coherence checks, independently compared to the input generator:

| Check | Expected and returned |
| --- | --- |
| Beginning | Project `Cedar`, marker `CEDAR-731` |
| Middle | Marker `HERON-248`; reference record `899` |
| Cross-record calculation | Record 899 units × unit cost = `110`, not their sum |
| End | Marker `QUARTZ-916` |
| Final amendment overrides beginning | Spending ceiling `42`, not baseline `90` |
| Persistent prohibition | `external changes` |
| Actual usage and model | At least 100,000 provider input tokens and exact returned model; both passed |

Three HTTP attempts were made against the same route and model. Existing
compatibility handling received HTTP 400 for the original token parameter
(`req_3af4d6fc4f0946499216cb8b1f12dca7`), then HTTP 400 before omitting the
unsupported temperature setting (`req_71458bcae59a4bd9907d15d60807f0c1`),
then the successful request above. The two rejected responses reported no
token usage; no dollar-cost or free-request claim is made. The cumulative
message-character allowance counts all three attempts.

The machine-readable evidence is
`generated-artifacts/round4-long-context-live.json`, deliberately ignored by
Git and delivered separately. The normal installation's saved model and
credentials were not changed; real credentials remained in process memory.

### Re-run from the repository root

Prerequisites: the project's Python dependencies are installed, network access
to the configured OpenAI route works, and an authorized **`OPENAI_API_KEY`**
is already supplied to the process through the OS/workspace secret mechanism.
That is the only required credential variable. Do not put a key in a command,
document, test fixture, issue, Git config or output report. The model ID is
pinned by the harness; the key must actually have access to it. No different
model is silently substituted.

The larger, potentially billed reproduction command is:

```sh
python tests/runtime/live_agent_eval.py --live --case long \
  --long-records 1800 --min-input-tokens 100000 \
  --max-input-characters 1800000 --max-requests 3 --timeout 180 \
  --output generated-artifacts/round4-long-context-live.json
```

`--live` is mandatory consent to provider calls. `--long-records` controls the
number of distinct records; it does not guarantee a token count.
`--min-input-tokens` is checked against actual response usage. The other limits
cap cumulative message characters (including retries), HTTP attempts and total
wall seconds; per-request generation remains capped at 4,096 tokens.
The command exits zero only if all coherence, usage and returned-model checks
pass. A timeout, missing key, model denial or exhausted budget is not a pass.

The original accepted 900-record case remains reproducible without changing
its default size or 30,000-token minimum:

```sh
python tests/runtime/live_agent_eval.py --live --case long \
  --max-requests 3 --timeout 180 \
  --output generated-artifacts/long-context-live.json
```

The existing fleet evaluation is a separate, possibly billed test:

```sh
python tests/runtime/live_agent_eval.py --live --case fleet --same-model \
  --max-requests 36 --timeout 300 \
  --output generated-artifacts/fleet-context-reproduction.json
```

These commands do not enable long-running mode in the real installation.
For the distinct context-compaction test and its operating-ceiling flags, see
`LONG-RUNNING-CLOUD.md`. Large-input recall is not proof of fleet success,
200,000-token operating capacity, native desktop behavior or an hours-long soak.

The harness itself sets temporary `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`
and `XOD_DATA_DIR`, uses the null keyring backend, and sets
`PYTHON_KEYRING_BACKEND`, `XOD_SPEECH`, `XOD_NO_UPDATE_CHECK`,
`XOD_MUSE_APP_DRY_RUN` and `XOD_LIGHTWEIGHT`. Users do not need to set those
variables or provide a Replit integration. Other inherited credential-variable
names ending in `_API_KEY`, `_TOKEN` or `_SECRET` are removed from the test
environment after the authorized OpenAI key is consumed.

Without sending provider requests, verify the input generator, larger-case
positioning, CLI bounds and mandatory live consent with:

```sh
python tests/runtime/test_live_eval_inputs.py
```
