# xod-partner

The small companion that watches Xod so Xod never has to watch itself.

Xod cannot reliably repair itself: the 2026-10-30 permission incident
proved a process does not survive operating on its own foundations (the
Accessibility reset killed Xod's own server mid-repair). The Partner
stands outside that boundary. It is deliberately tiny and frozen: new
features belong in Xod, never here.

## The charter (five jobs, from the design record)

1. Hold the TCC permissions and do the physical acting on Xod's request.
2. Watch Xod's health and restart Xod when it dies or wedges.
3. Apply Xod updates: stage, swap, restart, verify, roll back on failure.
4. Keep the shared log both apps write to.
5. Supervise permission repairs end to end.

**v0 implements jobs 2 and 4, plus the launchd floor.** Permissions (job
1 and 5) and update-swapping (job 3) arrive in later versions, only once
this skeleton is proven live. v0 watches one target only: the installed
Xod at http://127.0.0.1:8000/.

## How v0 behaves

- Probes Xod every 15 seconds. One failure is noise: a restart needs 3
  consecutive failures spanning at least 30 seconds, so a Xod that is
  itself mid-restart is never double-restarted.
- Restart means: quit the app cleanly (escalating to a kill only if it
  will not exit), reopen it, and poll for health for up to 90 seconds.
  Every step of the episode is logged.
- Anti-loop discipline: backoff between attempts (30s, 120s, 300s) and a
  hard cap of 3 restarts in any rolling hour. Hitting the cap parks the
  Partner in a HOLD: a loud log entry, `hold: "cap"` in the status file,
  and no more restarts until a human intervenes or a `release_hold`
  command arrives.
- HOLD file: while a file named `HOLD` exists in the state dir, the
  Partner probes and reports but never restarts. Delete the file to
  release it. This switch is human-only; no command clears it.

## State dir and files

`~/Library/Application Support/XodPartner/` holds everything:

- `app/` the installed copy of the watchdog (the repo is the source of
  truth; the running Partner never reads the repo, so repo edits do not
  silently change it)
- `partner.log` append-only event log, ISO timestamps, one line per event
- `status.json` rewritten on every state change: partner pid, last probe
  and its result, consecutive failures, restarts this hour, Xod state,
  last action and outcome. This is the file Xod and Tree read.
- `token` the inbox pairing token (chmod 600, generated on first run)
- `inbox/` command drop folder, `rejected/` where bad files land
- `partner.out.log`, `partner.err.log` launchd stdout and stderr

## Command inbox

Drop a JSON file into `inbox/`. Shape:

```json
{"token": "<contents of the token file>", "cmd": "restart_xod"}
```

Commands in v0:

- `restart_xod` run a restart cycle now (still respects the cap, the
  backoff, and the HOLD file)
- `verify` probe now and rewrite the status file
- `release_hold` clear a cap HOLD and reset the restart budget

Files with a wrong token, an unknown command, or malformed JSON are
logged and moved to `rejected/`, never executed. Executed or rejected,
no command file stays in the inbox.

## Config

`config.json` next to the watchdog (and optionally in the state dir,
which wins) overrides the defaults in `xod_partner.py`: probe URL and
interval, the failure gate, backoff schedule, hourly cap, timeouts.

## Install

```sh
STATE="$HOME/Library/Application Support/XodPartner"
mkdir -p "$STATE/app" "$STATE/inbox" "$STATE/rejected"
cp partner/xod_partner.py partner/config.json "$STATE/app/"
sed "s|__STATE_DIR__|$STATE|g" partner/dev.xod.partner.plist.template \
  > "$HOME/Library/LaunchAgents/dev.xod.partner.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/dev.xod.partner.plist"
```

launchd runs the Partner with RunAtLoad and KeepAlive: if the Partner
itself dies, launchd is the dumb floor that brings it back.

## Removal

```sh
launchctl bootout "gui/$(id -u)/dev.xod.partner"
rm "$HOME/Library/LaunchAgents/dev.xod.partner.plist"
rm -rf "$HOME/Library/Application Support/XodPartner"
```

Removing the state dir deletes the log and the token with it; copy
`partner.log` somewhere first if the history matters. Removing the
Partner changes nothing about Xod itself.

## Tests

```sh
python3 tests/partner/run_partner_test.py
```

The suite drives the decision core with a fake probe, a fake restarter,
and a hand-moved clock. It never touches the real Xod.
