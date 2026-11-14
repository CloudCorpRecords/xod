# Release process: a release is shipped when the launcher delivers it

Directive (2026-11-13): every release goes through the launcher, and installed
copies pull it automatically. A release that is only published on GitHub is not shipped.
It is shipped when a previous-version install has pulled it through the launcher and
come back reporting the new version. This file is the gate. No step is optional, and
the steps run in order.

## Why this gate exists

In November 2026 the fleet quietly stopped updating. Releases v1.1.0 to v1.1.3 were
published correctly, but the machines in daily use never landed them: the Mac
stayed on a 1.0.2 bundle whose updater staged newer apps in its cache that were never
installed, and the Razer's backend was hand-carried onto a 1.0.0 shell, so the shell
(the part that checks for updates) and the backend (the part that reports the version)
disagreed about what was installed. Nobody had verified uptake on a real install, so
every release was announced as shipped while the fleet ran old code. Full audit:
xod-eval, LAUNCHER-AUTOPULL-AUDIT-2026-11-13.md.

## The gate

### 1. Bump the version everywhere, as its own commit

- Write the changelog section first: `## X.Y.Z` at the top of docs/CHANGELOG.md.
- Bump with `python3 scripts/release.py X.Y.Z` (it refuses without the changelog
  section, a clean tree, or main in sync with GitHub). It writes the version into
  every source: app/version.py, app/mcp_server.py, app/connectors/mac.py,
  electron/package.json, npm/package.json, and electron/package-lock.json (both
  entries). Never bump by editing one file by hand.
- Run `python3 scripts/check_versions.py X.Y.Z`. It must pass. This check covers
  every version source; scripts/check_release.py alone compares only two of them.
- The bump lands as its own commit ("Release X.Y.Z") with the tag vX.Y.Z on it.
  No code changes ride along in the bump commit.

### 2. Build from the tag, nowhere else

- Pushing the tag starts .github/workflows/release.yml, which builds
  Xod-X.Y.Z-arm64.dmg and Xod-X.Y.Z-x64-setup.exe from the tagged tree and runs
  scripts/check_release.py before building.
- Hand-built installers and hand-carried file transplants are not a release channel.
  If an emergency transplant is ever used, record it in the release report the same
  day, and re-baseline that surface through the launcher (step 5) before the next
  release. A transplanted surface can never be the verification surface.

### 3. Verify the built artifact before anything is published

A release is not verified until the built artifact itself passes both checks below.
A checksum match, a CI pass, or a source-tree test run is not verification: v1.1.3
matched its published hash exactly and was still dead on arrival, because its
app.asar was missing liveness.js (the packaging allowlist in electron/package.json
omitted it while main.js requires it at line 8), so the app died at startup on every
platform before its updater could ever run. Nobody had opened the built artifact or
cold-launched it. These two checks are that opening and that launch.

- Package contents: run `python3 scripts/check_asar.py <built app.asar>` for each
  platform build (a .app bundle path works too; the script finds
  Contents/Resources/app.asar). It must exit 0: every relative require reachable
  from the packaged entry point resolves to a file inside the archive. Since
  v1.1.4 the packaged entry point is bootstrap.js (electron/package.json names
  it as "main"), so the walk starts there and, through the bootstrap's one
  literal import, proves the whole app graph behind it. Run the check on
  the artifact that will be published, not on a source tree or an earlier build.
- Cold launch: where a machine of the target platform is at hand, put the built
  artifact in a scratch location (never over a working install), launch it, and
  watch it reach its normal running state: main window open, backend answering,
  no main-process exception dialog. Record the launch in the release report
  (platform, artifact, what was observed). Where no launch is feasible, the asar
  check still runs and the report says the cold launch is outstanding; the release
  may be published, but it is not called verified until a launch has been seen.
- If either check fails, the release stops here. Publish nothing, fix the
  packaging or the code, and rebuild from the tag.

### 4. Publish as latest, with the updater's artifacts complete

The launcher is Xod's own updater (electron/updater.js), not electron-updater: there
is no latest.yml in this design, and none should be invented. What the updater
actually needs on the release, and what must be verified present before step 5:

- Xod-X.Y.Z-arm64.dmg (the Mac download it looks for) and Xod-X.Y.Z-x64-setup.exe
  (the Windows one), plus the stable-name copies Xod-Mac.dmg and
  Xod-Windows-Setup.exe.
- SHA256SUMS.txt, with one line per asset whose line ends in the exact asset name.
  The updater finds the checksum by that name and refuses a download whose hash
  does not match, and on Mac it also refuses a staged app whose bundle version is
  not the release version. If either check can fail, the pull fails silently for
  every install.
- The release is published (not draft, not prerelease) and marked latest, so
  https://api.github.com/repos/CloudCorpRecords/xod/releases/latest returns it.
  Both updaters (electron/updater.js and app/services/updates.py) read only that
  endpoint.

### 5. Verify the pull on a real previous-version install

- Pick a test surface running the previous release, installed the normal way
  (never a transplant, never a dev checkout). The Mac and the Razer each count;
  one surface per platform for a platform release.
- Let the launcher do the work: it checks on launch, hourly, and on focus. Do not
  reinstall by hand, do not copy files, do not sideload the DMG or the setup exe.
  If the surface's Xod is long-running, quit it once so the install-on-quit step
  can fire, then relaunch it.
- Confirm the landing by backend read-back, not by file stamps: the running
  backend's hello/version answer and /api/update status must report X.Y.Z, and
  the update status must show no newer version available. File stamps (the exe's
  FileVersion, a bundle's plist) are not evidence; they are exactly what misled
  the fleet in November 2026.
- Since v1.1.4 the pulled install launches through the recovery bootstrap
  (electron/bootstrap.js), which verifies the package before handing off to
  main.js. A release is not complete until a machine that was running the
  previous release has been launched once through that bootstrap and reached
  its normal running state on the new version.
- Record the verification in the release report: surface, from-version, to-version
  as read back, and when the pull completed.

### 6. Only then is it shipped

- Until step 5 passes, every surface that talks about the release (release notes,
  posts, the work board, Linear) calls it "published, uptake unverified", never
  "shipped".
- If step 5 fails, the release stays unverified, the failure is filed as an issue
  the same day, and the next release does not start until the failure is understood.
  A failed pull is a defect in the release, not an errand for the person whose
  machine did not update.

## The durable doctrine (standing, set 2026-11-14)

The v1.1.3 failure was not one mistake but a shape of work: fixes, gates, and
repairs drifting into separate releases and separate trees until the shipped
artifact was something nobody had ever launched. These rules keep that shape
from coming back. They bind every release after v1.1.4, including this one.

- One integration line. Exactly one branch is the integration line at a time;
  it is named in the newest convergence record in xod-eval and in its Linear
  issue. Every other branch is an input to that line or waits behind it. A new
  branch or worktree is created only after all three registrations exist: an
  entry in the worktree owners registry naming the owner lane and the base, a
  Linear issue naming the branch, and a stated merge target (the integration
  line). A lane that finds an unregistered branch flags it and does not build
  on it.
- A release carries its fixes, its integrity gate, and its self-repair
  together. Never split them across releases in a way that strands users on a
  build that cannot update itself, and never let a deferred piece quietly
  become sprawl on user machines. v1.1.4 is the pattern: the brain fixes, the
  packaging fix and asar gate, the recovery bootstrap, and the footprint
  doctor ship as one release.
- The ship gates, all of them, every time: the version bumped in every source
  with scripts/check_versions.py passing; the built app.asar passing
  scripts/check_asar.py with the packaged entry point (bootstrap.js since
  v1.1.4) as the walk root; a cold launch of the built artifact watched to its
  normal running state; a previous-version install verified to auto-pull the
  release through the launcher (step 5); and, since the bootstrap exists, its
  repair path exercised once against the last broken release's preserved
  fixture before the release is called verified.
- Field verification is part of verified. A release is verified only when the
  built artifact has been checked at the user's actual surface: one correct
  app where the user launches from (for example, exactly one Xod in the Mac's
  search results), a clean launch on a real user machine, and the running
  version read back from the app itself. A checksum match, a CI pass, or a
  source-tree test run is not verification, and when a field report contradicts
  a recorded state, the field report wins and the state is re-derived from the
  artifact or the code.
- Cleanup removes only on positive evidence. The footprint doctor
  (scripts/xod_doctor.py) is the standing cleanup tool: an item is removable
  only when the doctor proves it redundant (a duplicate whose version, build,
  and contents match a kept copy and which no launch agent references; a clean
  checkout whose every commit, reflog-only ones included, is in the canonical
  repo, with no gitignored files present). Every doubt is a hold with the
  reason printed: unique commits, uncommitted work, unreadable identity,
  a symlink, a live dependency. Cleanup is a dry run by default and never
  follows or removes a symlink.

## Standing notes

- The updater's check is unauthenticated and can be rate-limited by GitHub; a
  single failed check is weather, a day of failed checks is a defect. The Python
  status caches the latest release for 24 hours, so the shell's read-back in
  step 5 is the deciding evidence.
- Version on screen: the app is expected to show its running version where a
  person can see it without digging (tracked with the version-display work), so a
  wrong old copy is obvious at a glance.
