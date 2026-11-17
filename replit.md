# Xod development on Replit

Preserve the existing Python/FastAPI and Electron desktop structure. This is
a local-first, open-source assistant. Muse auto-approval is a core feature,
not an optional integration to remove during cleanup. Users bring their own
provider keys or use local models.

The owner explicitly wants recipient/site "Always allow" within the enabled
auto-approve policy, including email sends. Preserve the press-intent journal,
uncertain-outcome no-repeat protection, absence-scan rule, and versioned
Partner policy. Unscoped permanent grants and negative buttons stay excluded.
Do not commit `attached_assets/` or `.agents/memory/` to product delivery.

## Preview

Run the configured **Xod web preview** workflow. It uses:

```
XOD_LIGHTWEIGHT=1 XOD_MUSE_APP_DRY_RUN=1 XOD_NO_UPDATE_CHECK=1 python -m uvicorn app.main:app --host 0.0.0.0 --port 5000
```

The preview is a development interface, not a production hosting configuration.
It cannot control the user's local Mac/Windows desktop. Lightweight mode skips
optional background services; dry run disables desktop approval clicks.
Dependencies are in requirements.txt. Do not use run.sh here: it creates a venv
and binds to loopback for desktop development.

## Approval checks

```
python tests/smoke/run_muse_app_test.py
python tests/partner/run_approval_reliability_test.py
python tests/partner/run_partner_delegation_test.py
python tests/partner/run_partner_ax_test.py
```

Tests use isolated temporary state and fake desktop trees. The real
CoreFoundation permission-prompt check is explicitly skipped off macOS.

## Model testing

The requested live test model is gpt-6.1-terra. Initial November 16 discovery
found no managed OpenAI connection. After the user supplied OPENAI_API_KEY
through Secrets and explicitly approved its use, actual Xod dispatch to
api.openai.com returned HTTP 200, model gpt-6.1-terra, and "Xod live check OK".
Existing compatibility retries handled rejected max_tokens and temperature.
Managed-key provenance was not established; native MLX inference remains
UNKNOWN. Do not invent credentials, silently substitute models, or count
synthetic HTTP as live proof. OSS users retain BYOK and local operation.

Settings > Models, setup, model panels and paired-device picks use the same
validated install choice. Provider/model/endpoint and ownership are committed
together; keys still use the existing OS secret store and profile mechanism.
See `docs/MODEL-CONFIGURATION.md` for the contract and verification limits.

```sh
python tests/smoke/run_install_model_config_test.py
node tests/runtime/test_model_settings.js
python tests/smoke/run_model_switching_test.py
python tests/smoke/run_provider_boot_guard_test.py
python tests/smoke/run_provider_promotion_test.py
python tests/smoke/run_models_test.py
```

## Runtime checks

Opt-in cloud execution controls, conservative token estimates, checkpoint
privacy/recovery boundaries and actual live compaction evidence are in
`docs/LONG-RUNNING-CLOUD.md`. Do not assume a provider's maximum window from a
user-configured operating ceiling. A checkpoint with pending/uncertain effects
or an approval hold must not be automatically replayed.

```sh
python tests/runtime/test_long_execution.py
node tests/runtime/test_long_execution_ui.js
```

Live context/fleet evaluation evidence, safe opt-in commands and operational
diagnostics are in `docs/LIVE-AGENT-EVALUATION.md`. Large-input recall, sustained
execution, live compaction and native effects require separate evidence.
Do not use a fixture pass or model liveness reply to claim those behaviors.
Same-model workers are explicitly opt-in in Agent > Team; do not silently
enable them in users' settings or weaken approval policy.

```sh
python tests/runtime/test_run_observability.py
node tests/runtime/test_agent_diagnostics.js
```

Computer control's nine operations, explicit look-only trust policy, device
configuration and native acceptance checklist are in `docs/COMPUTER-CONTROL.md`.
Fake mode must be explicitly selected. A connected MCP server does not prove a
real device is configured. Computer actions remain approval-gated; this does
not change Muse auto-approval or Partner policies.

```sh
python tests/computer_control/test_control_contract.py
python tests/computer_control/run_computer_control_test.py
```

Fleet discovery, the pinned optional npm fallback and per-operation trust rules
are documented in `docs/FLEET-BRIDGE.md`. Do not broaden read-only trust from
server names or annotations. The separate fleet workflow runs deterministic
policy tests plus real published MCP round trips with missing tools treated
as failures. Preserve existing same-name user connections at automatic startup.

For the proposed 8 GB always-on budget and repeatable idle CPU/RSS collection,
see `docs/LIGHTWEIGHT-FOOTPRINT.md`. Linux dry runs do not validate the native
Partner/watchdog, desktop Accessibility watcher, Electron shell or loaded model.

```sh
python tests/runtime/test_runtime.py
python tests/runtime/test_task_stop.py
python tests/runtime/benchmark_team.py --check
node tests/runtime/test_refresh_gate.js
python tests/smoke/run_crew_test.py
```

See `docs/RUNTIME-RELIABILITY.md` for measurements, tradeoffs and unverified
native/live-model behavior. Lightweight mode retains Muse watching and Partner
supervision; dry-run and platform limits still apply. Keep the localhost-only
guard: remote browser testing cannot access this local assistant by default.
