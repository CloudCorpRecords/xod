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

## Live Terra result: UNKNOWN, blocked by missing access

Requested model ID: `gpt-6.1-terra`.

The November 16, 2026 checks were:

1. OpenAI integration discovery returned an empty integration list for this
   workspace.
2. Approved existence-only secret checks reported `OPENAI_API_KEY`,
   `AI_INTEGRATIONS_OPENAI_API_KEY`, and `FIREWORKS_API_KEY` absent. No secret
   value was retrieved or printed.
3. Official Replit documentation confirms managed AI access is a platform
   capability, but does not establish that an authorized route or Terra model
   access is available to this workspace.

No authorized managed OpenAI route was available. **Zero live model requests
were sent.** There is no provider request ID, live response, token count,
latency, or proof of Terra availability to report. No different model was
substituted, and no synthetic result is labeled a live pass.

When authorized access is supplied through the approved integration/Secrets
flow, configure that actual route, explicitly save the requested model, and
run one saved-model check. Record timestamp, route hostname, requested and
returned model IDs, response/request ID if available, and the redacted result.
A missing model, rejected credential, or unsupported route stays a failure
or UNKNOWN. Never copy test credentials into product files or require them
for OSS users.
