# Connector repairs and verification

## What was fixed

- Repeated Connect clicks share one attempt. Disconnect cancels pending sign-in,
  closes the session, removes its stale approvals and stops the connector before
  attempting credential removal. A locked key store is an error, not a successful
  credential-removal message.
- Reconnecting, disabling or deleting one server no longer restarts unrelated
  servers. Disabled connections cannot dispatch tools.
- A connection is usable only after tool discovery succeeds and returns tools.
  Disconnected sessions do not keep stale tool lists.
- Google connections additionally require a successful authenticated read before
  being shown as Connected or exposing tools to the agent: Gmail `list_labels`,
  Calendar `list_calendars` with one result, and Drive `list_recent_files` with
  one result and content snippets excluded. These minimal checks happen during
  Connect; their contents are discarded. Anonymous tool discovery alone is
  never enough. Interactive OAuth remains enabled until this account check ends.
- HTTP clients are closed with their transports. Configuration is replaced
  atomically with private permissions; malformed existing configuration is not
  silently overwritten.
- Stored OAuth credentials retain absolute token expiry and discovered
  authorization-server metadata, allowing refresh after a process restart.
  Replaced client credentials invalidate old tokens. Refresh responses that omit
  a replacement refresh token retain the previous token, through the MCP SDK.
- Callback states are one-use, expire, and are cancelled on disconnect. The
  callback page says **Finishing sign-in**, not Connected, until token exchange
  and tool discovery finish. SDK state, PKCE and issuer validation remain intact.
- A later failed refresh requests sign-in instead of opening a surprise browser
  from an agent tool call. No failed tool action is automatically replayed.
- The UI shows actionable errors, supports a manual browser sign-in link and
  recovers its controls after a network error.

This does not replace local credentials with a hosted account or Replit
integration. End users still bring their own accounts, OAuth clients and keys.
Trust defaults, destructive-action approvals and the localhost-only guard remain.
Public connectors do not require a key store to connect or disconnect. The
Linux Replit preview currently has no OS keyring backend; authenticated connectors
therefore report that limitation rather than falling back to plaintext secrets.
Secure credential persistence still needs verification on a supported local OS.

## Google setup: Gmail, Calendar and Drive

Authoritative instructions:
<https://developers.google.com/workspace/guides/configure-mcp-servers>.
Google controls these requirements; check the current guide before setup.

1. Obtain the required Google Workspace Developer Preview access.
2. In your own Cloud project, enable **both** services for the connector:

   | Connector | Product API | MCP API | Requested scope |
   | --- | --- | --- | --- |
   | Gmail | `gmail.googleapis.com` | `gmailmcp.googleapis.com` | `https://www.googleapis.com/auth/gmail.readonly` |
   | Calendar | `calendar-json.googleapis.com` | `calendarmcp.googleapis.com` | `https://www.googleapis.com/auth/calendar.events.readonly https://www.googleapis.com/auth/calendar.calendarlist.readonly` |
   | Drive | `drive.googleapis.com` | `drivemcp.googleapis.com` | `https://www.googleapis.com/auth/drive.readonly` |

3. Configure consent and any required test users.
4. Create a **Web application** OAuth client, mark **Use this client for an
   AI-powered agent**, and register the exact loopback callback shown in Xod's
   Connections panel. Do not register this development preview's public address
   as a substitute for the installed app's localhost callback.
5. Enter the client ID and secret in Xod. The secret goes to the OS key store,
   not `mcp_servers.json`.
6. Complete consent. Xod requests `access_type=offline` and `prompt=consent`
   while keeping its configured read-only scopes.

Existing tokens saved before expiry/issuer persistence may require one explicit
sign-in to obtain a fresh, renewable grant. A changed callback must be registered
with Google and the client details re-entered.

**Gmail is read-only in this catalog configuration.** Listing a `create_draft` or
other write tool does not grant its scope or bypass Xod's approval policy. No
test sent email or changed a real mailbox.

## Evidence, not a blanket “all connected” claim

Verification on 2026-11-17 covered **all 32 catalog entries**:
one local connector, 22 automatic OAuth connectors, one token connector, five
public connectors and three own-client Google connectors.

The endpoint sweep used no credentials, no OAuth registration or consent,
at most six concurrent probes, and a 22-second deadline per endpoint. All 31
remote endpoints answered initialization/auth-discovery requests. An initialize
response is only counted if it is a real JSON-RPC result, not an error or HTML.

| Catalog ID | Configured authentication | Live evidence | Remaining gate |
| --- | --- | --- | --- |
| `mac` | Local stdio, macOS | Linux platform refusal verified; shared stdio compatibility tests passed | Real Mac permissions and apps |
| `notion` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `linear` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `atlassian` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `todoist` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `clickup` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `monday` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `airtable` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `zapier` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `dropbox` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `canva` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `webflow` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `wix` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `stripe` | Automatic OAuth | Authorization metadata discovered | Test account; read-only verification, no payments |
| `paypal` | Automatic OAuth | Authorization metadata discovered | Test account; read-only verification, no payments |
| `intercom` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `sentry` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `vercel` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `netlify` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `supabase` | Automatic OAuth | Authorization metadata discovered | Account consent and non-mutating read |
| `neon` | Automatic OAuth | Authorization metadata discovered | Account consent and non-mutating read |
| `prisma` | Automatic OAuth | Authorization metadata discovered | Account consent and non-mutating read |
| `cloudflare` | Automatic OAuth | Authorization metadata discovered | Account consent, read, refresh and reconnect |
| `github` | Token / optional existing `gh` sign-in | Protected endpoint/auth metadata reached | User token or explicitly authorized CLI account |
| `huggingface` | Public | Actual MCP tools listed | Account-specific tools not tested |
| `mslearn` | Public | Actual tools listed and public documentation search returned content | Initial tools/list transient failure retained in evidence |
| `context7` | Public | Actual MCP tools listed | Individual tool calls not tested |
| `deepwiki` | Public | Actual MCP tools listed | Individual tool calls not tested |
| `cloudflare-docs` | Public | Actual MCP tools listed | Individual tool calls not tested |
| `google-calendar` | Own Google OAuth client | Actual public tool discovery succeeded | Authorized account read and provider eligibility |
| `gmail` | Own Google OAuth client | Actual public tool discovery; simulated full OAuth/read/refresh flow passed | Authorized mailbox read and provider eligibility |
| `google-drive` | Own Google OAuth client | Actual public tool discovery succeeded | Authorized account read and provider eligibility |

Google currently permits unauthenticated initialization and tool listing.
An actual unauthenticated Gmail `list_labels` request returned HTTP 401 with an
OAuth challenge. The simulated lifecycle reproduces this distinction: discovery
succeeds first, the account check opens consent, and no connected session or
agent-visible tools are published until authenticated readiness passes.
**That is not proof of access to a user's mailbox, calendar or files.** No Google
account/client was available for that proof, and no other authenticated provider
account was substituted. The simulated Gmail test uses the actual MCP client and
OAuth implementation against an in-process HTTP fixture, with synthetic tokens
and mailbox data.

## Repeatable checks

Run from the repository root in the installed Python environment:

```sh
python tests/features/test_connector_lifecycle.py
node tests/runtime/test_connector_ui.js
python scripts/verify_agent.py --output generated-artifacts/agent-verification.json
python tests/smoke/run_mcp_import_test.py
python tests/partner/run_mcp_child_import_test.py
python tests/computer_control/run_computer_control_test.py
python tests/features/run_computer_health_test.py
python scripts/check_release.py
```

The main verification command includes the new lifecycle and UI checks: **17
groups passed**, including all three context profiles, worker recovery, Muse
approval policy, approval reliability and fleet scheduling. The four extra
compatibility suites above also passed. The new lifecycle suite passed **34 tests**,
including automatic OAuth registration, public disconnect without a key store,
stopping the old account before saving replacement credentials and rejecting
anonymous Google discovery as account readiness. The final main verification
run includes all 34. The catalog contract includes a subtest for every entry.

Notable test names:

- `test_all_catalog_kinds_save_only_nonsecret_configuration`
- `test_repeated_connect_is_single_flight`
- `test_disconnect_cancels_connect_and_removes_live_session`
- `test_gmail_protocol_read_and_restart_refresh`
- `test_gmail_revoked_refresh_requires_signin_without_browser`
- `test_google_readiness_probes_are_minimal_readonly_and_discarded`
- `test_google_failed_account_read_is_not_accepted`
- `test_google_probe_success_without_oauth_tokens_is_not_accepted`
- `test_google_imported_as_public_cannot_report_connected`
- `test_new_google_client_invalidates_old_tokens`
- `test_google_authorization_requests_offline_readonly_access`
- `test_stale_approvals_removed_on_reconnect_and_disconnect`
- `test_failed_secret_deletion_does_not_claim_success`
- `test_callback_does_not_claim_connected_before_token_exchange`
- `test_corrupt_configuration_is_never_overwritten`
- `test_http_client_is_closed_after_transport_exit`

`tests/features/run_connectors_test.py` is an older opt-in internet sign-in
exercise. It performs real dynamic registration and intentionally submits an
invalid code; it was **not** run as a substitute for authenticated acceptance.
The local-only guard was tested through ASGI requests, not disabled to let a
remote browser testing service reach account sign-in.

Machine-readable local evidence is in the ignored `generated-artifacts/` folder:
`connector-agent-verification.json`, `connector-compatibility.json`,
`connector-discovery.json`, `connector-public-protocol.json` and
`connector-public-read.json`, `connector-google-readiness-challenge.json` and
`connector-verification-summary.json`. Baseline failure logs were retained separately.
