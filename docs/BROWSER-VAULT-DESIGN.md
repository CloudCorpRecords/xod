# Browser strength + shared password vault: audit and design

Owner directive (Rene, 2026-11-22 18:54 PT): Xod's browser capabilities must
be extremely strong, able to do anything, including heavy JavaScript web
applications like Handshake AI / SuperAnnotate; and Xod must share the same
password system Tree has, so when Xod needs a credential Tree already holds,
it just pulls it and proceeds. Xod is permissionless by design: grant once
and it acts, including passwords.

Lane: browser-vault-lane-2026-11-22. Branch tree/browser-vault-2026-11-22,
base 55bf086 (v1.1.18). Linear: AGE-97. Merge target: main. This document is
the audit and the design; the build that rides with it is the credential
broker (app/services/muse_vault.py) wired into credential_vault.get_login.
Everything bigger is listed as a follow-up at the end, deliberately not built
here.

## 1. Browser capability inventory (what exists at 55bf086)

### The mission browser: app/services/browser_sandbox.py (~5,000 lines)

A raw-CDP Chrome driver (websockets, no Playwright/Selenium dependency):

- Launches Chrome with `--remote-debugging-port` (9228 for the normal data
  folder, `XOD_BROWSER_PORT` override, a hashed port for any other data
  folder so two Xods can never drive each other's browser) and
  `--user-data-dir=<XOD_DATA_DIR>/xod_ai_browser_sandbox`: **Xod's own
  profile**, persistent across reboots, deliberately separate from the
  user's Chrome profile.
- Mission engine with indexed page elements: click, type, select, choose,
  check/uncheck, hover, scroll, press_key, read_more; multi-tab work
  (OPEN TABS listing, `switch_tab`); file upload via
  `DOM.setFileInputFiles` (`upload_file`, `upload_resume`); downloads via
  `Page.setDownloadBehavior` into the active workspace; JS evaluation;
  per-site learned tips; named-surface guard (AGE-71) so a mission that
  names a surface must actually land on it.
- Sign-in steps: `log_in` types a saved login straight from the Keychain
  (the LLM never sees the values), `create_password` generates a strong
  password on a sign-up form and saves it to the Keychain first.
- Headless or headful; careful liveness probing before it ever kills a
  Chrome (a busy heavy page gets a long final chance, because both Xod
  instances share the profile and port).

### Take Over: app/services/browser_remote.py

The phone shows the sandbox browser's screen (JPEG over Agent Mail, E2E
encrypted) and the person can tap/type/scroll. `note_signin()` detects a
sign-in wall Xod has no saved login for and routes the person to sign in
once from their phone; the session then persists in Xod's profile.

### The approver's browser surfaces

- app/services/muse_chrome.py: Muse Chrome extension approval cards read
  through the Windows UIA tree. **Dry run is absolute in this build**: it
  reads, decides, logs, escalates what it would press (via muse_escalate),
  and never presses. The Mac route is unbuilt (AGE-57), and the card
  patterns are still placeholders awaiting one real signed-in card capture
  (AGE-91).
- app/services/muse_app.py + muse_review.py: the Muse app watcher and the
  rubber-stamp approver with its own audit (DATA_DIR/muse_approver).
- The Partner (partner/): screen control on the user's real desktop,
  the route that presses approval cards in the user's actual apps.
- app/services/browser_ops.py: desktop/browser mission addressing,
  including the named-surface detection the AGE-71 guard uses.

### Credentials today: app/services/credential_vault.py + onepassword.py

- Xod's own vault: passwords in the OS keystore via `keyring` /
  keychain.set_item, service name "Xod: <site>"; a metadata-only index at
  DATA_DIR/vault_index.json (site, username, saved_at, source — never a
  password). `get_login(url)` returns (username, password) to browser code
  only; `describe_login` is the metadata view models may see.
- Fallback: the user's own 1Password through its `op` CLI, when installed
  and enabled in Settings (onepassword.py). Values in memory only.
- **Nothing asks Tree.** If Tree (the family's Muse, who holds the
  family's saved logins through its own Secure Vault / connectors) has a
  login Xod lacks, Xod's only recourse today is the phone Take Over.

## 2. Gaps that stop Xod on a Handshake-class heavy JS app

Ranked by whether they block the Handshake AI / SuperAnnotate shape of
work specifically:

1. **Wrong browser for Handshake.** Handshake work happens in the user's
   main Chrome: the Timer extension lives there, the fellow dashboard
   session is signed in there, and SuperAnnotate opens from there. Xod's
   mission browser is a separate profile with separate cookies; it cannot
   see the Timer extension, the user's Handshake session, or any site the
   user signed into in their real Chrome. Chrome also refuses to share one
   profile between two running instances, and a normally-launched user
   Chrome exposes no debugging port to attach to. Driving the real browser
   therefore means one of: (a) the user runs their main Chrome with a
   remote-debugging port and Xod attaches (same CDP engine, profile is the
   user's own), or (b) Xod drives the real Chrome window through the
   desktop layer, the way the Partner and the muse_app watcher already
   drive real apps. Both are real builds; see Follow-ups. The design rule
   either way: **Handshake-class work drives the user's real browser,
   never a fresh profile.**
2. **No credential sharing with Tree** (fixed by this lane's build, §3).
   A Handshake/SuperAnnotate sign-in Xod has never seen is exactly the
   "Tree already has it" case: today it dead-ends at Take Over.
3. **Extension cards are read-only.** muse_chrome never presses (dry run
   absolute, AGE-91 patterns untuned, Mac route unbuilt under AGE-57).
   Escalation to Tree covers the gap today; pressing is a follow-up gated
   on a real card capture.
4. **No WebAuthn / passkey / SSO-button path.** The mission browser fills
   username+password forms only. "Continue with Google"-class SSO and
   passkeys need either the user's real browser (where the identity
   session already lives — another reason gap 1 is the root one) or a
   dedicated flow. Follow-up, tied to gap 1.
5. **Extensions in Xod's own profile are unmanaged.** Nothing installs,
   pins, or verifies an extension in the sandbox profile, so an
   extension-dependent workflow (a timer, an annotator helper) cannot be
   provisioned by Xod itself. Follow-up; mostly moot for Handshake once
   gap 1 lands, real for other sites.
6. **Popup and auth-window handling is tab-shaped.** OAuth popups and
   `window.open` flows surface as tabs the mission can switch to, which
   covers most cases; a true separate-window popup with its own session
   dance is not separately handled. Follow-up, minor.

What is NOT a gap: raw page mechanics. Uploads, downloads, multi-tab,
arbitrary JS evaluation, long-page interaction, and surviving a busy heavy
page are all already in the sandbox engine; a heavy JS app inside Xod's own
profile is workable today. The blockers are identity-shaped (which browser,
whose session, whose password), not DOM-shaped.

## 3. The credential broker (built in this lane)

### Shape

New module **app/services/muse_vault.py**, modeled directly on
muse_escalate.py, because that module already solved "Xod asks Tree for
help over the family route, with an audit, dedupe, and an injectable
transport seam":

- `get_login(url_or_domain, timeout=DEFAULT_TIMEOUT)` →
  (username, password) or None. The one entry point.
- `set_provider(fn)` — an injectable credential provider,
  `fn(site) -> (username, password) | None`. This is the Xod Connect seam:
  when a live Connect session to Tree exists in-process, it registers a
  provider and answers synchronously from Tree's own vault. Tests inject
  fakes here. `set_provider(None)` restores the default transport.
- Default transport, **Tree over the tree-inbox** (services/tree_inbox.py,
  the same route muse_escalate rides):
  1. Xod writes a credential request: kind "task", text in plain words
     (site, request id, what to do), plus a structured
     `extra={"credential_request": {...}}` mirror, exactly like
     escalations carry their payload.
  2. Tree's side answers by writing one response file per request into
     the **response directory**: a sibling of the tree-inbox folder named
     `tree-vault-responses` (env `XOD_TREE_VAULT_DIR` overrides; if no
     shared inbox is reachable there is no transport and the broker does
     not pretend otherwise). Response JSON:
     `{"request_id", "site", "found": true, "username", "password"}` or
     `{"request_id", "site", "found": false}`.
  3. Xod polls the response directory until the deadline, reads the file,
     **deletes it immediately**, and holds the value in memory only.
- Why not Agent Mail: it is async identity/relay plumbing for
  person-to-device messages; bolting a blocking request/response onto it
  is a bigger build than the inbox route that already carries
  escalations. Why not the family key channel: that channel moves family
  vault bundle fields between enrolled devices' Xod services, not
  website logins from Tree's vault. The tree-inbox route is the existing
  Xod→Tree channel; the response directory is its minimal mirror.

### Rules (the vault contract)

- **Never persisted by Xod.** A brokered password is never written to the
  Keychain vault, the vault index, any file, or any log. It lives in the
  caller's memory for the one sign-in, like a 1Password value does today.
  (Logins Xod itself creates at sign-up still save to its own vault via
  `create_password`; that is Xod's own credential, unchanged.)
- **Never logged.** The audit (DATA_DIR/muse_vault/audit.jsonl, the
  muse_review.audit pattern) records only: timestamp, site, source
  ("provider" / "tree-inbox"), and outcome ("supplied", "not_found",
  "unreachable", "failed", "no_transport"). Never a username, never a
  password, never a fragment of either.
- **Fail closed.** Unknown site, Tree unreachable, response timeout,
  malformed response, provider raising: all return None. The broker never
  guesses, generates, or reuses a password for a different site. A miss
  falls through to the existing paths (1Password, then the Take Over
  sign-in note) exactly as a vault miss does today.
- **Bounded.** One request per (site) in flight is not enforced in v1;
  instead a short negative cache (60 s) stops a mission from paying the
  full timeout repeatedly for a site Tree already said it does not have,
  and the default timeout is short (20 s) because a human-speed Tree
  answer is either quick or not coming this mission. A queued (outbox)
  request is not waited on: no inbox, no wait.
- **Site keys are credential_vault.site_key's**, so "app.handshake.com"
  and "handshake.com" ask for the same login the vault would.

### Wiring

`credential_vault.get_login` gains one fallback step, in this order:
local Keychain vault → **Tree broker (muse_vault)** → 1Password. Local
behavior is byte-identical on a hit; the broker is consulted only on a
local miss, and only when a provider is injected or a shared tree-inbox
is reachable, so a machine with no Tree route behaves exactly as before.
browser_sandbox._mission_log_in and _mission_create_password pick the
broker up for free through get_login; no mission code changes.

### Tree-side consumer (spec, built on Tree's side, not in this repo)

Tree's tree-inbox watcher already delivers kind "task" texts into the
main conversation. On a text carrying a credential request (the
structured `credential_request` field names the request id and site),
Tree looks the site up in its own saved logins (Secure Vault /
1Password connector), and writes the response file described above into
`tree-vault-responses` next to the tree-inbox. If Tree has no login for
the site, it writes `found: false`. Tree never puts a credential in chat
text, an email, or the inbox message itself; the response file is the
only channel, and Xod deletes it on read.

## 4. Follow-ups (deliberately not built here)

1. **Drive the user's real Chrome for Handshake-class work.** Attach
   mode: when the user's main Chrome runs with a remote-debugging port,
   the mission engine points at that port and the user's own profile
   (sessions + Timer extension included) instead of launching Xod's
   profile; plus the desktop-layer route as the no-port fallback. This
   is the root fix for gaps 1, 4, and most of 5. Needs a live Handshake
   session to verify against; do not build it blind.
2. **Seal vault responses.** Encrypt the response file to the requesting
   Xod's family box key (services/mail_crypto.py, the family_key_channel
   pattern), so a credential never sits in plaintext even transiently in
   the synced space. The request id becomes the nonce reference.
3. **muse_chrome pressing.** Blocked on AGE-91 (one real signed-in card
   capture to tune CARD_PATTERNS) and AGE-57 for the Mac route.
   Escalation covers it meanwhile.
4. **SSO / passkey flows** in the mission browser once the real-browser
   route exists (the identity session lives there).
5. **Extension provisioning** for Xod's own profile: install/pin/verify
   an extension from a mission, for extension-dependent sites that are
   not Handshake.
6. **Broker polish:** per-site in-flight request coalescing, and a
   `describe_login` metadata path so note_signin can know Tree has a
   login before a mission starts (a `found`-only probe that never moves
   the password until the sign-in step asks for it).
