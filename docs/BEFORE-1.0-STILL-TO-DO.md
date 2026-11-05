# Before 1.0.0: what is finished, what was stopped, what still needs work (2026-11-04)

This note is for whoever picks the work up next, human or AI. Read it before merging toward a release.
The orchestrator itself is covered in `ORCHESTRATOR-ACCEPTANCE-2026-11-04.md`. This note covers the family and
phone work started after that, plus the release steps that are still open.

## Where the code is

| Repo | Branch | State |
|---|---|---|
| xod (desktop) | `claude/orchestrator-phase5` | The working branch. GitHub sign-in (8cd5e4d) and "Add my phone" (a77b359) are here. |
| xod (desktop) | `claude/headless-mode` | phase5's parent work, plus headless and lightweight modes. phase5 is not merged into it yet. |
| xod-android | `claude/scan-on-open` | Multi-computer pairing, "add this phone to another computer", sign-in. **Not pushed; no PR yet.** |

Do not modify `tree/lightweight-modes` or `tree/family-link-f0`. Reading them is fine.

## Finished and tested (not yet proven live end to end)

- **One phone, several computers.** Pairing with another computer never unpairs one. Each computer has its own
  mailbox on the phone, replies go back to the computer that asked, and mail signed by the wrong computer is
  held instead of trusted. (Android `MultiComputerTest` M1–M7.)
- **"Add my phone" from Your Xods.** A computer the phone is already paired with sends it a join offer for
  another family computer, and no second QR scan is needed. If the family check refuses, the phone undoes the
  pairing. (Desktop a77b359, Android a91ab30.)
- **Sign in with GitHub (device flow).** This is on both the phone (Android 1d89dbd) and the desktop (8cd5e4d).
  GitHub reports the login and verified emails, and nothing typed in is trusted. The family account is an email
  (`registry.json`) while GitHub signs in by login, so both sides match on the login **or** any verified email.
  The desktop's Family panel has a field to save the GitHub app's Client ID, and the computer passes it to
  paired phones.
- **Tests:** Android `./gradlew test` passes 152 tests with 0 failures, including `GitHubDeviceTest` G1–G4
  against a local stand-in for GitHub. Desktop `run_sso_control_test.py` passes 43/43, `phone_enroll` 51/51,
  `family_registry` 24/24 and `xod_network` 32/32.

## Stopped, and why

1. **No GitHub OAuth app exists yet, so GitHub sign-in cannot run for real.** Until it has a Client ID, the phone
   says "GitHub sign-in isn't set up yet". To fix:
   - Go to github.com/settings/applications/new. Any name and URL will do.
   - Tick **Enable Device Flow** and click Register.
   - Copy the Client ID (it starts with `Ov23`). It is public, and no client secret is used.
   - Save it in the Family panel ("GitHub sign-in"), or call `POST /api/family/control/github-client-id
     {"client_id": "..."}` on each Xod. For the phone build, it can also go in `local.properties` as
     `xod.githubClientId` or be passed with `-PxodGithubClientId`.
2. **The new phone app (1d89dbd) is built but not installed on the S25.** The phone dropped off USB.
   - Wireless debugging needs the phone to be on Wi-Fi itself, which doesn't work while it is running a hotspot.
   - Downloading the APK over Tailscale also didn't work out.
   - The installed build is bc79cfd. It still has the old typed-in sign-in field, which the new build no longer
     counts.
3. **The live family test was never run.** The plan:
   1. Sign in with GitHub on the phone.
   2. Pair the S25 with the Mac test Xod by QR.
   3. Press "Add my phone" for the Razer test Xod.
   4. Confirm the phone shows both computers and both can message it.

   Test Xods (separate data; the installed app's port 8000 and its data are never touched):
   - Mac: `~/Documents/xod-family-run/start-mac.sh`, port 8216.
   - Razer: `C:\Users\cloud\xod-family-run`, started in the logged-in session through a scheduled task. Starting it
     over plain ssh fails on Credential Manager with error 1312.
   - The QR page is made by `~/Documents/xod-family-run/make_qr.sh`.
4. **The S25's old pairing is stale.** It points at the fleet test Xod (`xod-fleet-test@…:8797`), which ignores it
   ("not a paired phone"), so the phone says the computer is "busy or down". Re-pairing by QR fixes it.

## Still needs work (known, not started)

- **Raw JSON on the phone's Tasks tab.** `phone_action` messages show up as JSON text instead of a readable card.
- **Tier 8 (fleet work) is not claimed.** Resource claims for phones are proven, but no real multi-device run with
  real phones has been done. When it is, any action sent must be a message to the owner only.
- **Tier 7 has been run only in compressed form.** Check the week-long run around 2026-11-11.
- **Family linking by the phone, with Tree.** The vision is that one phone paired with two computers vouches them
  into one family. Coordinate with Tree's `tree/family-link-f0` before building more of it.

## Full test count on phase5 (2026-11-04, about commit 1a52cc8)

Every `tests/*/run_*.py` file (140) was run, each with its own throwaway `XOD_DATA_DIR`. No live acceptance suites
were part of this count.

- **113 pass.** Two stale tests were updated along the way, and both now pass:
  - The companion has waited on the approval card, not the button, since v0.9.2 (fe990ae).
  - The Mac connector has 28 tools, and sending or unlocking always asks (1a52cc8).
- **5 fail, none known to come from this branch:**
  - `brain/run_missions_two_instance_test.py` needs an old-code checkout at `../xod-fix5-pre`
    (`XOD_FIX5_OLD_ROOT`), which isn't on this Mac.
  - `features/run_apply_test.py` and `features/run_operator_test.py` ran on the local Gemma and failed on model
    behaviour while memory was tight. Not yet compared against `main`.
  - `media/run_media_test.py` and `media/run_swap_test.py` failed because LM Studio returned "Compute error"
    under memory pressure.
- **22 were not run:**
  - 13 local-model suites in `smoke`, skipped on purpose to spare the laptop's memory:
    - `inner_life`, `life`, `local_servers`, `local_models`, `memory`, `platform_smoke`, `provider_boot_guard`
    - `recall`, `smoke`, `remote_models`, `provider_promotion`, `tools_reasoning_effort`, `windows_tools`
  - `browser_missions/run_suite.py`, `self_heal/run_heal_test.py` and `self_edit/run_self_edit_test.py`, which
    also use the local model.
  - Five measurement rigs or benchmarks in `locate` and `perf`, which have no pass or fail.

**Caution:** in a fresh data folder Xod's default model is the local one, so some model-driven suites used
Gemma even though they never mention it. `task_inbox/run_task_test.py`, for example, passed on Gemma. To run
these on Terra, put the API key in the test's data folder.

## Release steps still open

1. Rerun the 5 failures and the 21 skipped suites on a machine with memory to spare, or on Terra.
2. Merge phase5 into `claude/headless-mode`, then into the release branch.
3. Write the CHANGELOG entry, then run `scripts/check_release.py` and `scripts/release.py`.
4. Push the Android branch and open its PR.

Releasing without the live family test means the family and phone features ship tested by suites only. Say so in
the release notes, or hold those features back.
