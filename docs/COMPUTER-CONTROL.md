# Computer control

## Status and boundaries

Xod exposes nine tools through `app/computer_control_mcp.py`, the registered
`computer-control` MCP connection and `app/services/computer_control.py`.
The vendored device CLI now implements the interaction verbs, rather than
merely accepting adapter methods whose commands do not exist.

**Native device acceptance is UNKNOWN.** The current Linux environment has
no `bops` on PATH, no explicit bops executable configured and no browser-ops
device configuration. No personal device was contacted. Mock CDP responses,
fake adapters and a real MCP connection to a fake adapter are fixture evidence,
not proof of browser effects, app visibility, Windows SSH behavior or Muse
desktop parity.

This change does not modify Muse auto-approval, Partner policies, recipient
rules, approval journals or absence-scan protections.

## Adapter configuration

- Default and `XOD_COMPUTER_CONTROL_ADAPTER=bops`: use the executable in
  `XOD_COMPUTER_CONTROL_BOPS`, otherwise `bops` on PATH.
- `XOD_COMPUTER_CONTROL_ADAPTER=fake`: explicitly select the synthetic device.
  Fake successes say `fake: true`; no native action is performed.
- There is **no automatic fallback to fake** when a real driver is absent.
- The executable setting is one program path, not a shell command plus flags.
  Use the deployed host wrapper or an appropriate local executable launcher.
  The host wrapper uses the browser-ops remote configuration format.
- Remote devices must receive `browser_ops.py`, `control.py` and
  `control_inputs.py` together. The host wrapper alone is not a device upgrade.
  Local browser-ops provisioning copies all three.
- CDP control needs `websocket-client` or `websockets` on the device; the existing
  tab screenshot implementation specifically needs `websocket-client`.
- Connect CDP only to a trusted local browser/debugging endpoint. Do not expose
  its port publicly. Existing signed-in pages can contain private information.

Registration is additive and preserves an existing same-name connection,
including its trust and explicit driver environment. An inherited test-mode
environment cannot override an explicit connection driver selection.

## Permission policy

| Connection setting | Targeted look | Other eight operations |
| --- | --- | --- |
| Default `ask` | Approval required | Approval required |
| Explicit `read_only` | No per-call approval for verified built-in launcher | Approval required |
| `always` | No per-call approval for verified built-in launcher | Still approval required |
| Custom launcher with same reserved name | Approval required | Approval required |

Eligibility is operation-scoped to `computer_look`, with the exact built-in
module launcher, workspace directory and recognized environment keys. A name
or `readOnlyHint` alone cannot grant it. The central policy also feeds sentinel
and read-only worker checks. Hover, wheel events and drag can trigger page
behavior, so they are not read-only. Screenshots may capture unrelated private
desktop content and create files; they are not part of the look exemption.

Select read-only trust explicitly in Tools > Connections only if you intend
to permit observations on the configured driver. Every look requires a tab ID,
but **there is no saved per-origin or per-tab allowlist**. This setting covers
any explicit page target on that configured driver. Keep `ask` if that scope
is too broad. The local driver itself is part of the person's trust decision.

## Actual device CLI contract

Use `tabs_list` through the existing browser-ops tools to obtain a tab ID.
All interaction commands below require that explicit page target; the driver
will not pick an arbitrary active tab.

```sh
bops look --target TAB
bops click --target TAB --selector '#save'
bops type --target TAB --selector '#q' --value='hello' --clear
bops press --target TAB --key Enter --modifiers ctrl,shift
bops scroll --target TAB --delta-x 0 --delta-y 300
bops hover --target TAB --text 'Save'
bops drag --target TAB --start-x 20 --start-y 30 --end-x 120 --end-y 130 --steps 10
bops open-app --app editor
bops shot-tab --target TAB
bops shot-desktop
```

Look returns a bounded visible interactive element map, visible text, title,
URL and viewport, without writing the DOM or creating a screenshot file.
Click and hover take exactly one CSS selector, exact visible label or fresh
look index. Ambiguous, hidden and missing elements fail. Indices describe the
current document, not stable identities; prefer a unique selector and re-look
after a page changes. No automatic scrolling into view is performed.

Typing requires one selector or label and a supported non-password input or
textarea. It never falls back to another field. It verifies the resulting
value against the expected selection replacement. Unsupported selection
read-back or a mismatch is an unverified outcome, not success. Failed typing
MCP errors do not echo typed values or field read-back into SDK error logs.

Scroll deltas are integers within -4000 to 4000, with at least one nonzero
axis. The wheel event goes to the viewport center. Drag uses distinct
in-viewport CSS coordinates and 2 to 60 steps. These are CDP dispatch
acknowledgements, **not proof** that the intended container moved, a hover
effect appeared or a drop handler accepted a drag. Drag attempts a mouse-up
cleanup on failure; a lost connection may still require manual recovery.

`shot-desktop` retains the existing Windows interactive scheduled-task
implementation. A Mac desktop screenshot is not implemented by that command.

### App aliases

App launch is allowlisted in device `config.json`, or in `device_runtime`
for Xod-managed local provisioning. No app is allowed by default.

macOS example:

```json
{"allowed_apps":{"editor":{"bundle_id":"com.apple.TextEdit"}}}
```

Windows example (replace with a real existing path on the device):

```json
{"allowed_apps":{"editor":{"executable":"C:\\Windows\\System32\\notepad.exe"}}}
```

The API accepts the alias `editor`, not an arbitrary executable, URL, shell
command or arguments. Linux app launching fails explicitly. An accepted
macOS/Windows launch returns `launch_requested: true`,
`verified_visible: false`; native visibility is not inferred from an exit code.

### Failures and transport

The adapter requires a JSON object with boolean `ok: true` and a zero exit
code. Empty stdout, arbitrary text, arrays, missing confirmation, CLI errors,
missing dependencies and process failures cannot become successes. Adapter
failure becomes an MCP error rather than a successful outer tool envelope.

Timeouts and uncertain action outcomes must be inspected on the device before
retrying. A timeout is not a cancellation guarantee. Neither adapter nor driver
automatically retries an action. User arguments travel as JSON/base64 data in
the SSH bootstrap, not interpolated shell syntax. That encoding is not secret
storage or encryption. Native Windows OpenSSH shell compatibility is unverified.

## Regression evidence

```sh
python tests/computer_control/test_control_contract.py -v
python tests/computer_control/run_computer_control_test.py
```

The first checks all nine argv shapes, shared validation, missing drivers,
malformed output, timeouts, driver selection, CLI registration, read-only
eligibility, CDP response correlation, wheel/drag dispatch, app allowlisting,
typing read-back and a hostile-text POSIX transport round trip.
The second uses real stdio MCP and the chat's `execute_tool` with an explicitly
fake device, checking approval, trust preservation, all tools, strict integer
inputs, failure propagation and spoofed launcher rejection.

`.github/workflows/computer-control.yml` runs these deterministic suites.
Adding that file does not mean hosted CI has run. The legacy Mac Chrome suite
`tests/smoke/run_browser_ops_test.py` is not native acceptance for the new
controls and was not run on this Linux host.

Local verification on November 16, 2026 (America/Los_Angeles):

- `tests/computer_control/test_control_contract.py`: **21 passed**.
- `tests/computer_control/run_computer_control_test.py`: **45 passed**.
- `tests/fleet_bridge/test_fleet_policy.py`: **19 passed**.
- `tests/fleet_bridge/run_fleet_bridge_test.py --require-tools`: **17 passed**.
- `tests/smoke/run_mcp_import_test.py`: **41 passed**.
- `tests/smoke/run_sentinel_test.py`: **36 passed**.
- `tests/smoke/run_team_test.py`: **37 passed**.

Total: **216 passed**. Python compilation, catalog JSON, workflow YAML and
whitespace checks passed. The restarted app connected all nine computer
tools and its landing page rendered. Existing Linux keyring availability and
browser Tailwind/form/favicon warnings remain; this is not an installed
native app or a production acceptance run.

## Native acceptance checklist

Every row below is currently **UNKNOWN**. Run only on an explicitly authorized
test device with disposable local HTML, a fresh profile and a scratch app.
Record OS, Chrome, Python, driver revision and dependency versions, actual
commands, observed effects and failures without recording private content.

| Check | Required evidence |
| --- | --- |
| Native look and screenshot | Correct authorized target map and actual tab PNG; unrelated tabs unchanged |
| Click, type and press | Scratch button changes once, exact field read-back, expected Enter handler once |
| Vertical and horizontal scroll | Measured intended container movement in both axes; no accidental target change |
| Hover | Visible scratch hover effect on intended element, including ambiguous/missing locator failures |
| Drag | Correct scratch drop and mouse-up; out-of-bounds refusal and interrupted-drag recovery |
| macOS app launch | Allowed scratch app appears; unlisted aliases and arbitrary commands fail |
| Windows app launch | Allowed scratch app appears; invalid or missing .exe and unlisted aliases fail |
| Windows remote transport | Quotes, Unicode, newlines, dollar signs and backticks arrive unchanged without shell execution |
| Native trust behavior | Default observation holds; explicit read-only look passes; each action still holds |
| Disconnection and timeout | No automatic repeated action; uncertain state reported and inspected before retry |
| Existing native tab safety | Legacy ownership, last-tab and per-window guards still pass on the updated deployment |

Do not mark the native release acceptance complete from fixture results.
