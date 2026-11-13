# Release process: a release is shipped when the launcher delivers it

Directive (Rene, 2026-11-13): every release goes through the launcher, and installed
copies pull it automatically. A release that is only published on GitHub is not shipped.
It is shipped when a previous-version install has pulled it through the launcher and
come back reporting the new version. This file is the gate. No step is optional, and
the steps run in order.

## Why this gate exists

In November 2026 the fleet quietly stopped updating. Releases v1.1.0 to v1.1.3 were
published correctly, but the machines Rene actually uses never landed them: the Mac
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
  day, and re-baseline that surface through the launcher (step 4) before the next
  release. A transplanted surface can never be the verification surface.

### 3. Publish as latest, with the updater's artifacts complete

The launcher is Xod's own updater (electron/updater.js), not electron-updater: there
is no latest.yml in this design, and none should be invented. What the updater
actually needs on the release, and what must be verified present before step 4:

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

### 4. Verify the pull on a real previous-version install

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
- Record the verification in the release report: surface, from-version, to-version
  as read back, and when the pull completed.

### 5. Only then is it shipped

- Until step 4 passes, every surface that talks about the release (release notes,
  posts, the work board, Linear) calls it "published, uptake unverified", never
  "shipped".
- If step 4 fails, the release stays unverified, the failure is filed as an issue
  the same day, and the next release does not start until the failure is understood.
  A failed pull is a defect in the release, not an errand for the person whose
  machine did not update.

## Standing notes

- The updater's check is unauthenticated and can be rate-limited by GitHub; a
  single failed check is weather, a day of failed checks is a defect. The Python
  status caches the latest release for 24 hours, so the shell's read-back in
  step 4 is the deciding evidence.
- Version on screen: the app is expected to show its running version where a
  person can see it without digging (tracked with the version-display work), so a
  wrong old copy is obvious at a glance.
