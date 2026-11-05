# Handoff before 1.0.0 (2026-11-04)

This is the one note to read before continuing toward Xod 1.0.0. It covers what was built, what was changed, what
passed, what failed, what was stopped halfway, and what is left. It replaces `BEFORE-1.0-STILL-TO-DO.md`. The
orchestrator's own evidence (live runs, tier by tier) is in `ORCHESTRATOR-ACCEPTANCE-2026-11-04.md`.

**Status: not released.** The version is still 0.9.25, and nothing has been merged to `main`.

---

## 1. Where everything is

Only two branches hold this work:

| Repo | Branch | What it holds |
|---|---|---|
| `CloudCorpRecords/xod` | **`claude/release-next`** | Everything for the desktop: the orchestrator (phases 1–5), headless and lightweight modes, browser-ops, family and phone features, GitHub sign-in, the test fixes, and this note. 100+ commits ahead of `main`. |
| `CloudCorpRecords/xod-android` | **`claude/scan-on-open`** | The phone app: scan-to-pair, several computers per phone, "add this phone to another computer", and GitHub sign-in. 7 commits ahead of `main`. |

The desktop work was built on stacked local branches. The orchestrator went phase1 → phase2 → phase3 → phase4 →
phase5, and phase5 was then merged into `headless-mode` and `browser-ops-module`, which both feed `release-next`.
Those intermediate branches were **not** pushed, because `release-next` contains every one of their commits.

**Off-limits:** `tree/lightweight-modes` and `tree/family-link-f0` belong to Tree. Read them, don't change them.
The headless work uses a cherry-pick of the first one's commit and leaves the original untouched.

---

## 2. What was built

### 2.1 Orchestrator (long work in the background, honestly reported)

This was built to Tree's brief, ORCHESTRATOR-SCOPE-FOR-CLAUDE-2026-11-04, sections 1–12 plus Addendum A.

- **Phase 1: tasks that answer at once and then work**
  - A task is acknowledged in milliseconds and gets a durable, versioned plan.
  - A steer inbox handles pause, resume, redirect and added instructions.
  - A step completes only on an established outcome. "Unclear" counts as unverified, and an admission like "could
    not read" is never treated as success.
  - A step waits for its own background jobs.
  - Agent shell commands go through the executor, so nothing runs unlogged.
  - The chat can hand long work to the orchestrator: the planner knows `xod_task`.
  - A file named by its full path is read, not hunted for.
  - The Tasks panel shows the plan, the ack, steers and the controls.
- **Phase 2: workers**
  - Workers get briefs and run in isolated contexts, returning structured results with honest states.
  - A coordinator fans work out to them, under a read-only permission envelope.
  - Every side effect gets an outcome: CONFIRMED, FAILED, UNKNOWN or UNVERIFIED, checked at the destination.
  - A background job is identified by its command and folder, not its label, so an identical job is not rerun.
- **Phase 3: shared resources**
  - A resource registry handles claims, heartbeats, expiry and a FIFO queue with visible reasons.
  - Workers can be steered, and a stall watchdog watches them.
  - Fan-out resumes after a restart.
- **Phase 4: the full stack**
  - Routine run records, a notification policy and exactly-once delivery.
  - Memory in layers (§8) and provenance (§10).
  - Tasks run side by side under the resource registry.
  - UNKNOWN is settled at the end of the step.
  - Test hooks, and live acceptance suites (L1–L4, P1, S5–S12).
- **Phase 5: fixes from live runs**
  - Results travel between steps in full.
  - A command run twice in one step is reported as a duplicated side effect.
  - A chat turn waits for its own background jobs.
  - Agent Mail and phone sends are read back at their destination.
  - xod.db runs in WAL mode, with no event-loop stalls under load.
  - Asking Chrome's debug port never freezes the server.
  - A paused job keeps its best draft.
  - Tasks can carry another agent's id (`external_ref`), one of the five task-sync hooks for Tree. The other four
    hooks are assessed in the acceptance report.

**Verdict, from the acceptance report:** reliable through Tier 6 on repeated live runs, on the real model (OpenAI
gpt-6.1-terra). Tiers 0–8 all passed 3/3 consecutive live passes of all 69 checks, with these limits:
- Tier 5 was tested only at the spec's own small scale.
- Tier 7 was tested only compressed into minutes, not over a real week.
- Tier 8 (fleet) proves the mechanics only, with no real multi-device run.

### 2.2 Headless and lightweight modes

- **One switch** on the desktop (tray checkboxes, plus Settings → "How Xod runs") and in the terminal.
  - Terminal: `xod mode headless|light on|off`, `xod start --light`.
  - `xod task`, `xod plan` and `xod steer pause|resume|cancel|redirect|steer` drive the orchestrator.
- The setting is saved in `desktop.json`, and `XOD_HEADLESS` / `XOD_LIGHTWEIGHT` override it for one launch.
- **Lightweight** skips nine optional loops (they are named in `/api/mode`) and closes the idle sandbox browser after
  3 minutes. Measured idle cost: full mode 137 MB and 2.9% CPU, lightweight 54 MB and 0.2% CPU.
- Quitting the desktop shell waits for its server to stop.

### 2.3 browser-ops (sharing your real browser with the agent)

- browser-ops 1.0.0 is vendored from `CloudCorpRecords/browser-ops` PR #1, commit 6d6cadf.
- Xod's agent gets tools for it, with an adapter, a window guard and owners bound to their lane.
- A request about your own browser is routed to these tools instead of a sandbox mission.
- The answer-writer sees every tab, described in words.
- Screenshots can be named by URL or title, and the vision model describes what they show.
- A sweeper runs every 15 minutes where it applies.

### 2.4 Family and phone

- **One phone, several computers.**
  - Pairing with another computer never unpairs the first.
  - Each computer has its own mailbox on the phone, and replies go back to the computer that asked.
  - Mail signed by the wrong computer is held, not trusted.
  - Android tests: `MultiComputerTest` M1–M7.
- **"Add my phone" from Your Xods.**
  - A computer already paired with the phone sends it a join offer for another family computer, so no second QR
    scan is needed.
  - The phone checks family membership through its EnrollmentGate, which needs the computer in the signed family
    registry and a matching GitHub sign-in. If the check fails, the phone undoes the pairing.
  - Desktop a77b359, Android a91ab30.
- **Scan on open (Android).** An unpaired phone opens straight into the camera. A scanned pairing QR pairs at once,
  and dense QRs are readable.
- **Sign in with GitHub, using the device flow, on both sides.**
  - GitHub itself reports the login and the verified emails (scope `read:user user:email`). Typed-in accounts no
    longer count.
  - The family account is an email (`registry.json`, `cloudtheboy@gmail.com`), while GitHub signs in by login. Both
    sides therefore match on the login **or** any verified email.
  - The desktop's Family panel has a field for the GitHub app's Client ID (`POST /api/family/control/github-client-id`).
  - The computer hands the Client ID to paired phones through `phone_enroll.state()["github_client_id"]`. The phone
    can also have it built in, through `xod.githubClientId` in `local.properties` or `-PxodGithubClientId`.
  - Desktop 8cd5e4d, Android 1d89dbd.

### 2.5 Tests fixed in this pass (stale, not weakened)

- `tests/smoke/run_companion_test.py`: since v0.9.2 the companion waits on the approval **card**, not the button.
  It still has to reach the lassoed card and return there after signing (fe990ae).
- `tests/features/run_connectors_test.py`: the Mac connector has had 28 tools since 956e9fc, and sending a message
  or unlocking always asks. The check names the exact list (1a52cc8).
- `tests/smoke/run_cli_test.py`: since fee43d4 (headless), `xod task` goes through `/api/orchestrator/tasks` and
  prints "Acknowledged …". The test's stand-in server still served the old `/api/tasks` route, so CI failed on
  PR #43. The stand-in now serves the orchestrator route, and the approval and result checks are unchanged.

---

## 3. Test status

### 3.1 Full count on phase5 (2026-11-04)

Every `tests/*/run_*.py` file was run, 140 in all, each with its own throwaway `XOD_DATA_DIR`.

- **114 pass.**
- **5 fail, none known to come from this work:**
  - `brain/run_missions_two_instance_test.py` needs an old-code checkout at `../xod-fix5-pre`
    (`XOD_FIX5_OLD_ROOT`), which this Mac doesn't have.
  - `features/run_apply_test.py` and `features/run_operator_test.py` ran on the local Gemma while memory was tight,
    and failed on the model's behaviour. Not yet compared against `main`.
  - `media/run_media_test.py` and `media/run_swap_test.py` failed because LM Studio returned "Compute error" under
    memory pressure.
- **21 not run:**
  - 16 local-model suites, skipped to spare the laptop's memory:
    - In `smoke`: `inner_life`, `life`, `local_servers`, `local_models`, `memory`, `platform_smoke`,
      `provider_boot_guard`, `recall`, `smoke`, `remote_models`, `provider_promotion`, `tools_reasoning_effort`,
      `windows_tools`.
    - Also `browser_missions/run_suite.py`, `self_heal/run_heal_test.py` and `self_edit/run_self_edit_test.py`.
  - 5 measurement rigs and benchmarks in `locate` and `perf`, which have no pass or fail.

**Caution for the next run:** in a fresh data folder Xod's default model is the **local** one (LM Studio, Gemma 4
12B), not Terra. Some model-driven suites therefore use Gemma even though they never mention it.
`task_inbox/run_task_test.py`, for example, passed on Gemma. To run them on Terra, put the API key in the test's
data folder. On a 16 GB laptop, Gemma (7 GB) plus the suites is enough to run out of memory, so unload it
afterwards with `lms unload --all`.

### 3.2 On `release-next` after the final merge

Phase5 merged in with one conflict, in `app/services/browser_sandbox.py`. Both sides were kept: the debug-port
probe runs off the event loop, and `last_used` is stamped. These suites pass on the merged branch:
- `devtools_offloop` 4/4, `browser_ops` 55/55, `headless_mode` 14/14
- `sso_control` 43/43, `phone_enroll` 51/51, `browser_remote` 22/22
- `orchestrator` 79/79, `workers` 43/43

The full 140-file count has **not** been rerun on `release-next` itself.

### 3.3 Android

`./gradlew test` on `claude/scan-on-open`: 152 tests, 0 failures. This includes `GitHubDeviceTest` G1–G4, which runs
the whole device flow against a local stand-in for GitHub.

---

## 4. Stopped halfway, and why

1. **No GitHub OAuth app exists yet, so GitHub sign-in cannot run for real.** Until one exists, the phone says
   "GitHub sign-in isn't set up yet". The owner will do this:
   - Go to github.com/settings/applications/new. Any name and URL will do.
   - Tick **Enable Device Flow** and register.
   - Copy the Client ID (it starts with `Ov23`). It is public, and no client secret is used.
   - Save it in the Family panel, or with `POST /api/family/control/github-client-id {"client_id": "..."}` on each Xod.
2. **The new phone build (1d89dbd) is not installed on the S25.**
   - The phone dropped off USB.
   - Wireless debugging needs the phone itself on Wi-Fi, which isn't possible while it runs a hotspot.
   - Downloading the APK over Tailscale didn't work out either.
   - The S25 still runs bc79cfd, whose typed-in sign-in no longer counts.
   - Build it with `./gradlew assembleFullDebug`; the APK is `app/build/outputs/apk/full/debug/app-full-debug.apk`.
3. **The live family test was never run.** The plan:
   1. Sign in with GitHub on the phone.
   2. Pair the S25 with the Mac test Xod by QR.
   3. Press "Add my phone" for the Razer test Xod.
   4. Confirm that the phone lists both computers and that both can message it.

   The test Xods use separate data. The installed app's port 8000 and its data folder must never be used for tests.
   - **Mac:** `~/Documents/xod-family-run/start-mac.sh`, on port 8216, with Agent Mail on 8798 and the beacon on 8796.
   - **Razer:** `C:\Users\cloud\xod-family-run`. Start it in the logged-in session through a scheduled task, because
     a process started over plain ssh fails in Credential Manager with error 1312.
   - **The QR page:** `~/Documents/xod-family-run/make_qr.sh`, which uses `GET /api/phone/pair-offer`.
4. **The S25's old pairing is stale.** It points at the fleet test Xod (`xod-fleet-test@…:8797`), which ignores it
   ("not a paired phone"), so the phone shows the computer as "busy or down". Pairing again by QR fixes it.

---

## 5. Known problems not yet worked on

- **Raw JSON on the phone's Tasks tab.** `phone_action` messages show up as JSON text instead of a readable card.
- **Tier 8 (fleet).** No real multi-device run with real phones has been done. When it is, any action sent must be a
  message to the owner only.
- **Tier 7 has been run only in compressed form.** Check the week-long run around 2026-11-11.
- **Task-sync hooks for Tree.** These are listed in the acceptance report:
  - device-prefixed ids;
  - task pickup as a claim with a lease;
  - a "verified" task state;
  - named claimed, heartbeat, verified and released events;
  - a per-device event log with no global ordering.
- **Family linking by the phone.** In the vision, one phone paired with two computers vouches them into one family.
  Coordinate with Tree's `tree/family-link-f0` before building more of it.

---

## 6. Steps to 1.0.0

1. Rerun the 5 failures and 21 skipped suites on `release-next`, on a machine with memory to spare or with Terra
   keys in the test data folders. Compare `apply` and `operator` against `main`.
2. Register the GitHub app and run the live family test (section 4).
3. Fix the raw-JSON phone cards.
4. Bump the version from 0.9.25 to 1.0.0 in **both** `app/version.py` and `electron/package.json`
   (`scripts/check_release.py` checks that they match).
5. Write the `CHANGELOG.md` entry, using sections 2 and 4 of this note.
6. Open the PR from `claude/release-next` to `main`, and merge after review.
7. Run `scripts/check_release.py`, then `scripts/release.py`.
8. Merge the Android PR, `claude/scan-on-open` → `main` in `xod-android`.

If 1.0 ships before step 2, say in the release notes that the family and phone features are tested by suites
only, or hold them back.

---

## 7. Rules that held during this work (keep them)

- **Test on dev instances only**, each with its own `XOD_DATA_DIR` and port. Never use the installed app (port 8000)
  or its data.
- **Each bug fix comes with a regression test.** Never weaken a test to make it pass. A stale test may be updated
  only to match intended behaviour, and the commit must say why.
- **Report only what was observed.** A suite that didn't run is "not run", not "passed".
- **The pre-commit hook rejects the owner's first name in files.**
- **Commit trailer:** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
