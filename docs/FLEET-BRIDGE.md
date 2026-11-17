# Fleet bridge: published fallback and trust

## Installation and discovery

Xod supports only these five fleet gate packages:

| CLI | Exact published version | npm registry metadata |
| --- | --- | --- |
| cidiff | 0.1.2 | https://registry.npmjs.org/@reneturcios/cidiff/0.1.2 |
| reprocheck | 0.2.0 | https://registry.npmjs.org/@reneturcios/reprocheck/0.2.0 |
| testdelta | 0.1.0 | https://registry.npmjs.org/@reneturcios/testdelta/0.1.0 |
| skillcheck | 0.1.0 | https://registry.npmjs.org/@reneturcios/skillcheck/0.1.0 |
| contextbudget | 0.1.0 | https://registry.npmjs.org/@reneturcios/contextbudget/0.1.0 |

Verified against the published npm package metadata and distributed JavaScript
on November 16, 2026. Each publishes a same-name CLI at `dist/cli.js` with an
`mcp` subcommand. Runtime dependencies are the MCP JavaScript SDK and zod;
Node.js 20 or newer is required.

Discovery retains the existing precedence:

1. `$XOD_FLEET_TOOLS_DIR`, if set; otherwise `~/workspace/fleet-tools`.
   The chosen folder can contain `node_modules/.bin/<tool>` or a bare shim.
2. A same-name executable on PATH.
3. The reviewed published package under this Xod checkout's
   `node_modules/@reneturcios/<tool>`, launched with Node directly.

The third option is the version-controlled fallback. Root `package.json` pins
the five versions exactly, and `package-lock.json` locks their transitive
dependencies and integrity values. Registry-specific resolved URLs are
omitted from the lockfile so it remains usable through the installer's
configured registry rather than depending on a private Replit hostname.

Optional one-time installation, in the Xod checkout (not `electron/`):

```sh
npm ci --ignore-scripts
# Restart Xod after installation.
```

This is a deliberate installation command, not something the model or startup
runs automatically. Xod never launches `npx`, resolves `latest`, downloads a
package at startup, executes package lifecycle scripts, or adds the wider fleet
catalog. Without network access, use an already complete installed dependency
tree, an appropriate package-manager cache during explicit installation, or
the existing local CLI discovery paths. Missing Node, missing packages,
unreviewed versions, changed code and failed MCP handshakes remain errors.
Discovery reports actionable reasons per missing package in its return value
and startup logs; connection errors also identify the installation remedy.

All automatically added connections retain trust **ask**. Registration is
additive, not an update mechanism: a same-name connection is preserved
verbatim, including disabled state, custom URL/command, credentials and trust.
Repeated startup does not rewrite it. Install changes do not silently replace
an existing local launcher. To switch launchers, review/edit that connection
explicitly in Tools > Connections or remove it and restart for rediscovery.
Normal explicit user MCP imports still support replacement.

These optional servers are real child processes once connected. The earlier
lightweight measurements in `LIGHTWEIGHT-FOOTPRINT.md` predate their installation
and do not include five additional fleet Node processes.

## Narrow read-only policy

The following operation names were checked against the published handlers and
their implementation imports, not inferred from descriptions or annotations:

| Server | Verified operation | Behavior examined |
| --- | --- | --- |
| testdelta | `diff_test_results` | Reads base/head result JSON; compares in memory |
| contextbudget | `budget_path` | Inspects paths and reads text for token estimates |
| skillcheck | `check_skill` | Reads a skill and referenced paths; does not execute skill content |
| reprocheck | `detect_test_runners` | Inspects filenames/config/test text; does not run tests |

**Not on the list:** `reprocheck.run_repro_check`, either cidiff operation,
unknown/new operation names, local shims, custom launch environments and
lookalike servers. cidiff's network-backed operations remain conservative
approval-required calls in this pass, even though they principally fetch CI
data. No whole server or package is granted read-only trust.

Read-only eligibility requires the precise published launcher and package
version/interface, plus matching SHA-256 digests for its distributed JavaScript
files. Digests live in `app/services/fleet_packages.json`; policy lives in
`app/services/fleet_policy.py`. A changed package fails fallback discovery,
and a changed launcher, custom environment or altered source cannot obtain
verified read-only status merely by using a familiar tool name. Updating a
package requires reviewing its handlers again and updating pins, lockfile,
digests and regression evidence together.

The list defines eligibility, not automatic execution permission. At default
trust **ask**, even a verified read needs approval. A person can select
**read_only** to allow only the verified reads without repeated approval.
Command-running and unreviewed fleet operations still require an approval
card, even if a server advertises readOnlyHint or is set to **always**.
A deliberate approval can run the held operation; delegation cannot widen
a read-only worker's permission envelope.

The MCP hub, displayed tool status, sentinel review and actual worker
execution all use the same effective policy. Other user-defined MCP servers
retain their existing trust/annotation behavior. A custom server using one of
the five reserved fleet names retains its configuration, but its unverified
operations are conservatively approval-gated.

This is not a sandbox, code-signing scheme, or defense against a compromised
local account/runtime. Node and dependencies are installed through the
user's trusted package pipeline. Read-only also does not mean non-sensitive:
these tools can read local files, and outputs remain untrusted content.

## Regression coverage and CI

```sh
python tests/fleet_bridge/test_fleet_policy.py
python tests/fleet_bridge/run_fleet_bridge_test.py --require-tools
npm ci --dry-run --ignore-scripts --offline --no-audit
```

The deterministic suite covers folder/PATH precedence, fixed fallback
selection, missing/offline packages, missing Node, changed versions/source,
idempotency, preserving same-name user connections, default ask, explicit
user replacement, fake read-only annotations, unknown operations, approval,
other MCP connections and worker envelopes.

The real suite starts all five actual published MCP servers and calls them
through Xod's actual tool dispatcher. It checks a planted test regression,
context budgeting, skill inspection, runner detection, read-only worker
execution and refusal to execute repro commands from a read-only worker.
No provider credentials, live GitHub API calls or desktop actions are needed.
`--require-tools` turns missing dependencies or lack of a verified published
read-only round trip into a failure, never an all-skipped CI pass.

`.github/workflows/fleet.yml` is a separate, independently landable workflow.
It installs the lockfile with lifecycle scripts disabled and runs both suites.
The existing `ci.yml` is unchanged, so product files and this workflow can be
landed separately if GitHub workflow authorization is restricted. No workflow
or product branch was pushed from this workspace as part of this task.
Local Linux results do not establish that the hosted macOS job has run.

### Observed verification

Passed in this Linux workspace on November 16, 2026:

| Suite | Result |
| --- | ---: |
| `tests/fleet_bridge/test_fleet_policy.py` | 19 tests passed |
| `tests/fleet_bridge/run_fleet_bridge_test.py --require-tools` | 17 checks passed against real published servers |
| `tests/smoke/run_mcp_import_test.py` | 41 checks passed |
| `tests/smoke/run_sentinel_test.py` | 36 checks passed |
| `tests/smoke/run_team_test.py` | 37 checks passed |

The older MCP-import fixture initially failed because Linux had no native
keyring and its external Python child could not find workspace dependencies
after HOME isolation. The fixture now uses the synthetic test keyring,
isolated HOME/XDG paths, no developer env file, and an explicit PYTHONPATH
for its test-only external server. The application was not changed to save
credentials insecurely or inherit a developer's entire environment.

The offline npm install-plan dry run and Python compilation passed. After
one final backend restart, all five fleet servers connected and advertised
their expected 7 tools in total. The existing computer-control connection also
connected. The first-run page rendered in a local screenshot without a
JavaScript exception. Linux still reports no native keyring; native Partner
watchdog restrictions and the localhost-only preview guard remain intact.
No desktop clicks, model requests, GitHub API calls, pushes or hosted CI
executions were performed for this verification.
