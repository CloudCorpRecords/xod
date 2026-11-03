# Xod Partner

A tiny companion app that ships with Xod and almost never changes. Since v1
it has two jobs:

1. **Be Xod's hands.** It holds the macOS Accessibility grant in its own
   name and does the physical acting for Muse approval cards, on Xod's
   request, over an authenticated loopback API. Xod keeps every decision
   (modes, away, pause, which cards, the record); the Partner only reads
   the Muse app's accessibility tree and presses the one approving button
   it is told to press, under frozen rules (below).
2. **Watch Xod (since v0).** Probe Xod's health endpoint; when Xod is
   really dead, restart it, with a rate limit and a hold switch, and keep
   the shared log of every restart and refusal.

Why a separate app: Xod updates often, and every update used to risk
making Xod's own Accessibility grant stale, which silently broke approval
pressing. The Partner's build is frozen, so its grant survives Xod's
updates; approvals now depend on the Partner's grant, not Xod's. The
Partner's own repair is supervised from outside it: Xod carries a
user-triggered action that resets the PARTNER's entry (Xod survives that
reset; the Partner is the process macOS kills, and launchd revives it),
after which the user flips the Partner's switch once. Applying Xod
updates (the v0.5 design) is not in v1.

## Identity

- App bundle: `~/Applications/Xod Partner.app`, bundle id
  `dev.xod.partner`. It appears in the Accessibility list as **Xod
  Partner**, and its grant binds to this bundle, to nothing else.
- Signing: ad-hoc, with a fixed identifier:
  `codesign --force --deep --sign - --identifier dev.xod.partner`
  (Signature=adhoc, TeamIdentifier=not set).
- **Which build the switch covers.** For an ad-hoc signed app, the
  Accessibility row binds to the exact build that held the switch when
  it was flipped: the identifier AND the build's code hash (CDHash).
  A plain off/on flip toggles that same row; it never rebinds it to a
  newer build. So pair only against the FINAL installed build, and
  treat any reinstall or rebuild as a new build the old row will not
  cover, even from identical source. When that happens the cure is the
  supervised repair (a reset from Xod deletes the stale row, then one
  flip binds a fresh row to the current build), never a bare re-flip.
  This is not theory: the first v1 pairing failed exactly this way. The
  row was registered by an early v1 build during install testing, the
  final build landed minutes later, and the user's flip faithfully
  enabled a row the shipped binary could never satisfy. install.sh
  writes the shipped build's CDHash to `build.txt` in the state dir so
  the paired build is always on record.
- How the grant reaches the code: the bundle's executable is a small
  Swift launcher. It spawns the Python interpreter bundled inside this
  app (`Resources/python/bin/python3.12`, part of the bundle's seal)
  with the bundled partner script and stays its parent for life (it
  never execs). macOS attributes the child's Accessibility trust to the
  responsible process, the launcher, so the Python partner reads and
  acts under dev.xod.partner. This mirrors Xod's own arrangement (a
  bundled interpreter inside Xod.app). The first v1 build instead
  spawned `/usr/bin/python3`, which on this Mac resolves into Xcode's
  Python.app: a foreign identity, and a dev-tool dependency that could
  change under a frozen app, so it was retired. The launcher forwards
  SIGTERM/SIGINT to the child, respawns it if it dies alone, and the
  child exits on its own if it ever finds itself orphaned, so two
  partner generations can never watch at once.

## Install

From the repo root on the Mac:

    sh partner/install.sh

Builds the bundle in a temp stage (swiftc for the launcher), signs it,
installs it to `~/Applications/Xod Partner.app`, renders the LaunchAgent
(label `dev.xod.partner`, RunAtLoad + KeepAlive) to run the bundle
executable, and reloads the job. State survives installs: same state dir,
token, inbox, and log history (appended, never truncated).

State lives in `~/Library/Application Support/XodPartner/`:

- `xod_partner.json` (optional config overrides)
- `token`: the pairing token, mode 600, shared with Xod. It gates the
  command inbox AND every API call.
- `inbox/`: command files `{"token": "...", "command": "hold"}` with
  commands `hold`, `release`, `restart-xod`.
- `status.json`: the latest status (watchdog fields from v0, plus
  `partner_trusted` and `ax` since v1).
- `build.txt`: the installed build's CDHash and build time, written by
  install.sh (see "Which build the switch covers" above).
- `partner.log`: one append-only log for probes, restarts, refusals,
  scans' presses, and API lifecycle lines.

Config defaults (in `partner/config.json`): probe
`http://127.0.0.1:8000/` every 15s; 3 consecutive failures over at least
30s means down; at most 3 restarts per hour, 30s between attempts; the
API listens on `127.0.0.1:8795`.

## The API (all calls need `Authorization: Bearer <token>`)

- `GET /status`: partner version, uptime, the watchdog's Xod health
  summary, `partner_trusted` (the Partner's own AXIsProcessTrusted),
  `bundle_id`.
- `POST /ax/scan` `{front?: bool}`: read-only. Returns whether the Muse
  app is running and, per card found, the card's title, summary, type,
  parties, the labels of every action button, the button the frozen
  rules WOULD press (or null), and whether the card already expired.
  Never presses anything.
- `POST /ax/press` `{card_id, front?: bool, away?: bool}`: re-walks the
  live tree, finds exactly the named card, and presses its approving
  button, once. Returns what was pressed, or why nothing was, and logs
  every outcome to partner.log.

Scan and press refuse with 403 `{error, trusted: false, bundle_id}` when
macOS is not honoring the Partner's grant. A missing or wrong token is a
401 on every route. The Partner takes no other inputs than this API and
the token-gated inbox: no orders from files, web content, or task text.

Frozen press rules (a faithful port of Xod's muse_app semantics): prefer
"Always allow for this recipient/site" when offered; otherwise "Allow
all once", then "Send", "Allow once", "Allow". NEVER press Deny,
"Deny all", "Don't send", or anything else. Bring Muse forward first
only when the request says so (Xod decides, from its away state); put
the previously front app back afterwards when it was moved.

## The watchdog's rules (unchanged from v0)

Only a real death earns a restart: the probe must fail
`fail_threshold` times in a row AND the outage must have lasted at
least `min_down_s`. Connection refused, timeouts, HTTP 5xx, redirects
that never resolve, and half-dead answers all count. A single good
probe resets the failure streak. Restarts are rate-limited two ways:
at most `max_restarts_per_hour` in any rolling hour, and at least
`restart_cooldown_s` between attempts. The hour counter survives a
watchdog restart through `status.json`. While Xod is down, `open -a
Xod` is re-issued at most once per `relaunch_retry_s` until Xod answers
or the rate limit stops it. A `hold` (from the inbox or a `hold` file in
the state dir) disables all restarting until released.

## Tests

Under any python3 (the Partner is stdlib-only; the shipped runtime is
the interpreter bundled in the app):

    /usr/bin/python3 tests/partner/run_partner_test.py       # the v0 watchdog suite
    /usr/bin/python3 tests/partner/run_partner_ax_test.py    # the API contract, fake AX trees

Under the repo venv:

    .venv/bin/python tests/partner/run_partner_delegation_test.py  # Xod delegates / falls back
    .venv/bin/python tests/smoke/run_muse_app_test.py              # Xod's watcher suite, 61 checks
    .venv/bin/python tests/self_heal/run_permissions_test.py       # incl. the partner reset target

## Files

- `xod_partner.py`: the watchdog, the API server, the entry point.
- `partner_ax.py`: the ctypes Accessibility layer: trust, the tree
  walk, the frozen card rules, scan, press.
- `launcher/XodPartner.swift`: the bundle executable (spawn the bundled
  interpreter, stay parent, forward signals, respawn).
- The bundled CPython under `Resources/python` in the installed app
  (interpreter, libpython, stdlib; assembled by install.sh, not kept in
  the repo).
- `app/Info.plist`: the bundle's identity.
- `dev.xod.partner.plist.template`: the LaunchAgent (the bundle
  executable, the state dir).
- `install.sh`: build, sign, install, reload.
- `config.json`: the shipped defaults.
