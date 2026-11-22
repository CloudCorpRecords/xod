# Xod — approval watcher update + self-repair root cause and hardening
**Date:** 2026-11-22 · **Machine:** Renes-MacBook-Pro-2 · **Branch:** `tree/muse-option-widgets-2026-11-22` (local commits `0de8b874`, `b5ee2ba4`; not pushed — the ship call is Rene's)

This report covers two jobs, in the order Rene re-prioritized them: (1) Xod was down with a looping self-repair dialog — root-caused, repaired, and hardened; (2) the approval cards that piled up in the Muse app — root-caused, with the current state and destination read-backs. The agent-loop approver build (trigger → screenshot → model review → act → verify) resumes on top of this; its status is at the end.

---

## Part 1 — Self-repair: root cause, fix, hardening

### The cause chain (every link from a log line, a state file, or a measured artifact)

1. **11:01 AM PT** — Xod v1.1.14 is published to GitHub (release API timestamps: created 18:50Z, published 19:01Z). The in-app updater downloads it to `~/Library/Caches/Xod/updates`.
2. **11:08:32** — the Xod Partner's update guardian sees the staged 1.1.14 payload, installed is 1.1.13, and snapshots the installed app (partner.log).
3. **11:09:10** — **the snapshot FAILED verification** (partner.log: "snapshot for payload 1.1.14 FAILED verification; the retained snapshot was not touched"). The guardian's snapshot verification compared the copy byte-for-byte against the live tree; the copy raced files the running backend rewrites inside its own bundle (bytecode caches, logs). The retained snapshot therefore stayed at **1.1.4**, from 2026-11-14. The guardian proceeded anyway, with no flag that its rollback target was now stale.
4. **~11:54–11:55** — Xod is quit (by this lane, to restart it with a watcher patch). The updater's `install.sh` runs on quit and swaps 1.1.13 → 1.1.14. The swap landed (the battery below ran against a working 1.1.14 backend). In the same window, macOS posted the notification Rene screenshotted — '"Xod" was prevented from modifying apps on your Mac' (App Management TCC). Contributing friction, not the cause: the swap demonstrably completed.
5. **11:55:27–11:56:59** — guardian health battery on 1.1.14: **health PASS, chat exact reply PASS, permission reads PASS, file round trip FAIL** ("file task ended with status 'failed'" — one agent-task probe; why that single task failed is not recorded beyond its status). The battery rule was `all probes must pass`, so one flaky, model-dependent probe condemned a healthy install.
6. **11:56:59** — the guardian rolls back — to its retained snapshot, **1.1.4**, skipping the actually-replaced 1.1.13 entirely — and relaunches it.
7. The restored 1.1.4 can never start: its bootstrap predates the `original-fs` fix documented in the current `electron/bootstrap.js` header (the "v1.1.4 field failure, 2026-11-14"), so under Electron its integrity check throws on its own archive → verdict "the installed package cannot be read as an archive" → the repair dialog Rene screenshotted — and that build's repair fails its staged-copy check the same way, so **the dialog loops forever**. Proven by the bundle on disk at 12:02 (Info.plist 1.1.4; app.asar 196,226 bytes dated Nov 14 09:33, size-identical to the Nov 14 staged repair copy in `~/Library/Caches/Xod/xod-bootstrap-8kRG5o`) and by the guardian's own post-rollback battery: every probe "Connection refused" — the backend never started.
8. **11:58:59** — the guardian writes `blocked-update-1.1.14.json` (`restored_version: "1.1.4"`, rollbacks: 1) and stops, leaving the dead 1.1.4 installed. Rene finds the looping repair dialog.

Guardian state history shows the same shape repeatedly (blocked 0.9.15 / 0.9.17 ×2 / 0.9.18…, failures dominated by file-round-trip and connection errors) — the design had form.

**Suspected, labeled as such:** the exact file(s) that changed mid-copy in step 3 are not recorded; the volatile-writes mechanism is the only one consistent with a byte-for-byte manifest failing on a live app tree, and the hardening removes the whole class rather than depending on the guess.

### The fix to the install (done, verified)

- Downloaded `Xod-1.1.14-arm64.dmg` from the GitHub release; **SHA-256 matched the release digest exactly** (`af7f2215…101b`, also the value in the updater's cached SHA256SUMS.txt).
- Replaced `/Applications/Xod.app` from the mounted DMG (`ditto`), after killing the wedged processes.
- **Cold launch, watched:** the installed 1.1.14 reached its normal running state — Electron main up, backend (pid 49144) listening on 127.0.0.1:8000, `server.json` refreshed to that pid, `/api/desktop/status` answering 200, live mail/task traffic in `desktop-server.log`. Still healthy at the last check (backend uptime 16+ minutes, HTTP 200).
- One obstacle on the way: a **Nov 14 test fixture** (`~/xod-v115-stage/rig/fixture/Xod.app`, an instrumented 1.1.5-era test double sharing the `dev.xod.desktop` bundle id) got launched by LaunchServices alongside the real app and confused `open -a Xod`. It was a launchd-orphaned leftover, not a live lane's work (nothing in that directory modified since Nov 14); it was killed and the real app launched cleanly by path. The verified-good DMG remains at `~/Library/Caches/Xod/repair-2026-11-22/` on the Mac.

### Hardening (committed `b5ee2ba4`; guardian half also deployed live, see below)

**Update guardian — `partner/partner_updates.py`:**
- **Freshness gate:** a rollback may only restore the snapshot of the install the payload actually replaced. A stale/missing snapshot → the new install is left in place, the payload is blocked, and `guardian-report-<version>.json` is written. (Kills step 6's failure mode categorically.)
- **Confirmation battery:** one failed battery never rolls anything back; the battery re-runs once and only a second consecutive failure acts. (Kills step 5's failure mode.)
- **Put-back:** if a restored build cannot start at all (health fails) while the replaced build had passed health, the replaced build — kept aside during the restore — is put back. A restored build that answers health but fails agent probes stays (it is a working app). (Kills step 7–8's "dead app left installed" outcome.)
- **Volatile-tolerant verification:** snapshot/restore verification ignores `__pycache__`, `*.pyc`, `*.log` — the paths a running app rewrites mid-copy. (Kills step 3's failure mode.)
- **Honesty:** a payload with no verified snapshot of the current install logs `ROLLBACK PROTECTION UNAVAILABLE` up front; `status()` exposes `pending_snapshot_ok` and `last_guardian_report`.

**Bootstrap — `electron/bootstrap.js`:**
- **Verify before swap** already existed (checksum vs SHA256SUMS.txt + the asar require-walk + version match on the staged copy) and is unchanged.
- **Verified swap:** every healthy packaged launch now writes `bootstrap-health.json` (version + time) into the data dir, and the macOS repair swap script waits up to 120 s for a *fresh* marker naming the new version before deleting the previous bundle. No marker → the script restores and relaunches the previous install itself and logs it. Previously the backup was deleted the moment any app.asar existed.
- **Attempt counter:** repairs against the same installed version are counted in the data dir (`bootstrap-repair.json`, which no swap touches). After **3**, the bootstrap stops offering repairs, writes `bootstrap-repair-report.txt`, and shows one "repairs are paused" dialog instead of looping. A healthy launch clears the count.

### Regression gate (repeatable, with the old-vs-new contrast demonstrated)

- `tests/partner/run_partner_updates_hardening_test.py` — three incident-shaped cases: (A) stale snapshot refused, (B) transient battery failure → confirmation, no rollback, (C) restored build dead → put-back. **Against the old guardian it fails at case A with "the guardian restored a stale snapshot over the new install" — the incident itself. Against the new guardian: all pass.** The pre-existing guardian suite (`run_partner_updates_test.py`) passes in full against the hardened code.
- `electron/bootstrap-hardening.test.js` — counter cap, health marker + forgiveness, paused-repair report, verified swap script ordering, truncated-archive verdict. **Against the old bootstrap: 4 of 5 fail** (the APIs don't exist; the truncated-archive verdict predates the change and passes on both). Against the new bootstrap: all pass, and the pre-existing suites stay green (bootstrap 19/19, bootstrap-electron 2/2; combined run 26/26).

### Deployment state

- **Guardian: LIVE on the Mac.** The installed Partner was running a `partner_updates.py` older than the repo HEAD (it lacked the AGE-72 version-agreement code). The hardened file (repo HEAD + this change) was deployed to `~/Applications/Xod Partner.app/Contents/Resources/partner/` after a constructor/status/tick API-compatibility check against the installed `xod_partner.py`, with the previous file backed up at `~/Library/Application Support/XodPartner/partner_updates.py.bak-2026-11-22-pre-hardening`. The Partner was restarted via its existing LaunchAgent (`dev.xod.partner`, no new plist): clean start (pid 55217), AX API listening on 127.0.0.1:8795, probing Xod successfully. Its retained snapshot on disk is still the stale 1.1.4 — now inert: the freshness gate refuses to restore it, and the next staged payload will snapshot the healthy 1.1.14.
- **Bootstrap: committed, ships with the next release.** It lives inside app.asar, so it cannot be file-patched into the installed app honestly; the Mac's installed 1.1.14 still runs the pre-hardening bootstrap until a release carries `b5ee2ba4`. Same for the watcher picker fix (`0de8b874`). **A v1.1.15 release is the durable ship for both** — that call is Rene's.

### TCC audit (evidence-based; the TCC databases are not readable from SSH — no Full Disk Access — so each line rests on observed behavior)

- **Accessibility — Xod Partner (`dev.xod.partner`): GRANTED and effective.** It performed live AX scans and presses all morning; earlier status probes reported trusted with no stale grant.
- **Accessibility / Screen Recording — Xod's own backend identity: NOT granted.** The running backend's `/api/desktop/status` reports `accessibility: false, screen: false` for its computer-control features. By design the Partner holds the AX grant and does the pressing; Xod's own desktop control is the surface that lacks it.
- **App Management: BLOCKED for Xod's own updater process** (macOS notification in Rene's screenshot, in the update window). The Partner's guardian swaps of /Applications succeeded in the same period, so the block is specific to the Xod app's own context. This is the AGE-57 pattern to watch: both Xod.app and Xod Partner.app are ad-hoc signed, so TCC grants bind to the cdhash and can go stale on rebuild.

### What could still regress this

- **The installed bootstrap is pre-hardening until a release ships** (above). If the guardian (now hardened) ever restores a build whose bootstrap is the 1.1.4-era one, that build still can't self-repair — but the guardian can no longer restore a stale snapshot, and its put-back covers a restored build that can't start.
- **The battery still leans on the agent stack** (a chat reply and a model-performed file task). The confirmation battery halves the flake risk; it does not make a genuinely broken model route distinguishable from a broken app. If Xod's configured model loses its key, expect guardian reports, not silent damage — that is the intended failure shape now.
- **Snapshot of a live tree remains racy in principle.** Volatile paths are excluded from verification; a non-volatile file changing mid-copy can still fail a snapshot — but that now degrades to "rollback protection unavailable + report", never to a stale restore.
- **Two Xod.app copies sharing one bundle id** (the Nov 14 fixture) confused LaunchServices once. The fixture is dead; any future test rig should use a distinct bundle id or be removed when its run ends.
- **Ad-hoc signing (AGE-57)** can still stale the Partner's Accessibility grant on a Partner rebuild; the symptom would be approval presses stopping, and the fix is a re-grant by Rene.

---

## Part 2 — The approval cards

### What the watcher was, and why cards piled up

Xod 1.1.14's Muse app watcher (`app/services/muse_app.py`, pressing via macOS Accessibility through the Xod Partner, switch `muse_auto_approve` = on/always, not paused) was already live and pressing Allow-style cards — the devin.ai code-share card was **pressed by Xod at 11:43:10** and the Instagram send card at **11:43:41** (agent_approvals rows). The pile-up had one root cause: the button picker didn't know connector **action cards**, whose primary buttons are verbs like **Reply** (Gmail send) and **Create** (calendar). Those cards were logged "not pressed: no button Xod may press" at 11:35:32 (calendar), 11:43:56 and 11:45:13 (Gmail), and again at 12:02:03 when the Gmail card re-presented. The AGE-91 Chrome-extension watcher is a separate surface: Windows-UIA-only and still `DRY_RUN = True` in the shipped code — no real Chrome card has ever been pressed; it remains a Windows item.

### The fix (built in the earlier phase of this task, committed `0de8b874`)

The picker now filters the card's body-text button, details toggles, and the Don't/Deny twins out of candidacy, then picks the card's primary action verb (Reply, Create, Post, Share, Continue, Approve, Pay, Sign, …) or the single remaining affirmative. Per Rene's 2026-11-22 policy there is **no category never-list**: every approval card on the watched surfaces is Xod's to click; a card is held only when two real actions compete with no preference order, and the hold is logged. Dry-run mapping proven against the real cards before any live use: **Gmail → "Reply", Johnny Oz calendar → "Create", devin.ai → "Allow"**. Suites: muse_app 65/65, muse_chrome 16/16, muse_options 14/14. The patch was live on the Mac briefly on the pre-incident install and verified in situ against the real Gmail card; the self-update and rollback then replaced that bundle, so **the currently installed stock 1.1.14 does not carry it** — it ships with the same next release as the bootstrap hardening.

### Current card state and destination read-backs (verified 12:22–12:23 PT)

Rene's 11:58 screenshot showed the panel as Allowed / "Always allowed": while Xod was down, Rene granted the standing allow on the Mac, and the waiting workers executed. Verified at the destinations:

- **Gmail nudge — SENT.** Sent folder holds the nudge replies from cloudtheboy@gmail.com, including reticle PR #2094 at 11:43:11 and again at 12:02:47, PR #1635 at 11:44:21, PR #1633 at 11:57:21 (the batch Rene authorized with "Send the six nudges now"; the #2093 nudge was separately held by its own lane over a stale draft, per the campaign record).
- **Johnny Oz event — CREATED.** Google Calendar: "Rene x Johnny Oz — AI chat", **Mon Nov 23, 2026, 2:00–2:30 PM PT**, Zoom location, attendee Johnny Oz (johnnyozbooking@gmail.com), created 2026-11-22 11:42:47 PT, event id `blnsojps2mvstvdf4tu84tlf40`.
- **Devin code share — DONE.** Card pressed by Xod at 11:43:10; the free Devin account was created and verified the same day (credits exhausted at signup; no payment entered — recorded in MEMORY.md).
- **Instagram send — pressed by Xod at 11:43:41** ("allowed by Xod"). The send executed under the same grant window; it was not separately re-read at the destination in this pass.

No approval cards are currently pending: the last watcher row is the 12:02:03 Gmail re-presentation (held by the stock picker), and that send has since landed (12:02:47), so nothing is waiting on a tap.

### Approver agent loop — status

Per Rene's architecture direction (trigger → perceive/screenshot → model review with a logged opinion → act → verify; "build it like you"), the loop is **not yet built** — the repair incident preempted it, on Rene's own re-prioritization. Inventory already established for it: Xod's own configured model route is **unproven** (an SSH-side probe hit "No API key configured for OPENAI" but also a Keychain access failure, so the key's absence inside the real backend process is not proven either way — the retest must go through the running backend's own API); the Xod Connect route to the paired Muse is asynchronous (signed offer + tree-inbox + Agent Mail), not a synchronous ask. The picker fix above becomes the loop's ACT-step button locator. Next step when the approver phase resumes: settle the model route with evidence through the live backend, then DECIDE-ONLY over the next real cards before enabling acting.
