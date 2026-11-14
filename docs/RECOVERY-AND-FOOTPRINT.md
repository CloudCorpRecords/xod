# Recovery bootstrap and footprint doctor: design and integration note

Branch: tree/recovery-footprint-2026-11-14, off main 1f23683. Status: a queued
input to the integration line (see Landing order). Nothing here is merged,
pushed, tagged, or released.

## The problem, in three parts

1. Stranded past users. v1.1.3 as shipped dies at main.js line 8 (liveness.js
   missing from app.asar) before its updater can load, so no in-app update
   can ever reach an existing 1.1.3 install. Those users get one manual
   download (the comms drafts in docs/comms/) and, from the release after
   the fix, the bootstrap below so it cannot happen again.
2. Future broken builds. Owned by tree/release-integrity-fix (the packaging
   pattern plus scripts/check_asar.py in the AGE-49 gate). This branch does
   not duplicate it; the bootstrap reuses the same walk at launch time.
3. Sprawl. No component owned the machine footprint. The doctor below does.

## A. The recovery bootstrap (electron/bootstrap.js)

The packaged entry point becomes bootstrap.js (package.json "main"). It is a
single self-contained file whose only relative import is the literal handover
to main.js, executed after the verdict; a test pins that property, because
the release gate's archive walk follows literal imports and this one import
is what makes the gate prove the whole app graph.

On every packaged launch it:

1. Parses app.asar and walks every relative import reachable from the entry
   point, with Node's resolution order. This is the check_asar.py algorithm
   ported to Node, verified to agree with the Python checker on the same
   fixture and on the real shipped 1.1.3 archive (both name the miss at
   main.js line 8).
2. Compares the installed version against KNOWN_BROKEN_VERSIONS (["1.1.3"]).
3. Healthy: loads main.js at once. The check is local only; there is no
   network on the launch path.
4. Broken: never loads main.js. It repairs through the same channel
   updater.js uses (GitHub latest release for CloudCorpRecords/xod, the
   platform artifact, SHA256SUMS.txt checksum verification), verifies the
   staged replacement with the same walk before anything is replaced, and
   swaps it in only after the staged copy passes. On macOS the swap reuses
   the updater's proven shape: a waiter script moves the new bundle in once
   the bootstrap process has quit, keeping the old bundle until the new one
   verifies in place, and restoring it if the move fails. On Windows the
   bootstrap hands the checked NSIS installer to the OS, which is the
   channel updater.js already uses there; the bootstrap never moves files
   itself on Windows.
5. Any failure before the swap (no network, checksum mismatch, a staged copy
   that fails the walk, a latest release that is itself flagged broken)
   leaves the installed app byte-identical and reports where it stopped and
   what to do instead. A failed repair never bricks anything.

The channel constants (repo, artifact naming, sums file) are duplicated from
updater.js on purpose: the bootstrap runs before any app code may load, so it
cannot import app code. The duplication is three constants, commented as
mirrors.

Platforms: the decision and repair logic is platform-independent and fully
fixture-tested on this Linux VM; the macOS staging and swap are built from
the same primitives updater.js runs in production and are proven the first
time the fixed build is cold-launched on a Mac under the AGE-49 gate. The
Windows adapter is design-level: implemented against the updater's installer
hand-off, exercised in tests only through its staging seam.

Standalone mode (support and rescue): `node bootstrap.js check <Xod.app>`
prints the verdict; `repair` runs the same pipeline against an installed
bundle. This is also how the lane proved the verdicts on the real archives.

## B. The footprint doctor (scripts/xod_doctor.py)

A standalone script, stdlib only, in the mold of check_asar.py. It is not an
app subcommand on purpose: app/cli.py talks to a running server, and the
doctor has to work exactly when the app cannot start. Scripts are also where
a support session or a user building from source can reach it.

It inventories the Xod-named children of the platform roots (applications
folders, home, Documents, Downloads, Desktop, Trash, the data parent, the
launch agents folder), one level into archive containers for bundles, and
classifies every item:

- current: the installed app in the applications folder and its data folder.
  Untouchable.
- stale-safe (the only class cleanup removes), each with positive evidence:
  a duplicate app copy of a version that stays represented by a kept copy;
  a clean git checkout whose every commit is in the canonical repo (with
  the HEAD tree size and commit count recorded as evidence); the app's
  scratch folders; its disposable outputs (logs, test runs); an exact byte
  duplicate of a kept file; anything already in the Trash (except a checkout
  that fails the equivalence checks, which stays a hold even there).
- hold, with the reason printed: the only or kept copy of a version; unique
  commits; uncommitted changes; a linked worktree; a folder a launch agent
  runs from (a live dependency); the canonical repo itself; a database
  backup; model weights whose removal forces a re-download; a separate
  product (Xod Partner, by bundle id); a symlink; or no redundancy evidence.

Cleanup is a dry run unless --yes is passed, removes only the stale-safe
class, and reports measured bytes per item and in total.

Platforms: macOS rules are fixture-proven and are the rules the 2026-11-14
census applied by hand. Windows roots follow the updater's facts (per-user
install under %LOCALAPPDATA%\Programs, data in %APPDATA%\Xod) and are
design-level until a Windows machine proves them.

## C. Stranded-user comms

docs/comms/release-notes-fixed-release-DRAFT.md (Block A for the fixed
release, Block B for the later release that ships the bootstrap) and
docs/comms/readme-notice-DRAFT.md. Drafts only; nothing is published.

## Landing order (convergence)

This branch is an input to the single integration line, not a parallel line.

1. First, the integration line (the combine and re-grade tree, which also
   carries tree/release-integrity-fix @ dfe9938) completes its re-grade and
   its AGE-49 ship step. That ship is the fixed release the stranded 1.1.3
   users rescue themselves with, and it does not wait for this branch.
2. After that ship completes, this branch merges into the integration line.
   The bootstrap and the doctor ride the following release.

## Files the integration line also touches (conflict map)

- electron/package.json: BOTH branches edit it. tree/release-integrity-fix
  replaced the build.files allowlist with a pattern ("*.js", "!*.test.js",
  "splash.html", "build/icon.png", "package.json"). This branch changed
  "main" to "bootstrap.js" and added one "bootstrap.js" line to the old
  allowlist so a build from this branch alone is self-consistent. Merge
  resolution: take the release-integrity files pattern wholesale (it already
  covers bootstrap.js via "*.js" and excludes bootstrap.test.js via
  "!*.test.js"), keep this branch's "main" change, drop this branch's
  files-list line. Neither branch touches the version field; the bump stays
  with the ship step.
- docs/RELEASE-PROCESS.md: touched by tree/release-integrity-fix only. This
  branch deliberately does not touch it. After both land, two short lines
  are worth porting into it from this note: the packaged entry point is
  bootstrap.js (so the asar gate walk starts there), and a release is not
  complete until a machine with the previous release has been launched once
  through the bootstrap. The second is a process line, not a code gate.
- electron/main.js, electron/updater.js: neither branch modifies them. The
  bootstrap loads main.js unchanged and mirrors updater.js constants.
- scripts/ and tests/smoke/: disjoint new files (check_asar.py and its test
  there; xod_doctor.py and its test here). electron/bootstrap.js and
  electron/bootstrap.test.js are new. app/ is untouched by both branches.

## Gate evidence produced by this lane

- Bootstrap tests (node --test electron/bootstrap.test.js): 12/12. Includes
  the exact 1.1.3 fixture (the shipped 8-file set, the miss at main.js
  line 8) routed to repair; a healthy package deciding launch; a failed
  download and a checksum mismatch leaving the install byte-identical; a
  staged-but-broken copy never swapped; a full successful repair; the
  latest-release-is-broken stop; the Windows installer seam; and the
  no-relative-import-before-the-verdict pin.
- Existing electron suites unchanged and green: liveness and mode, 18/18.
- Cross-implementation agreement: the release-integrity lane's
  scripts/check_asar.py and this bootstrap's walk both fail the same
  fixture at main.js:8, and the bootstrap's standalone check on the real
  shipped 1.1.3 app.asar (preserved by that lane) reports the same miss and
  version. The fixed build's asar passes the walk with zero problems.
- Doctor tests (tests/smoke/run_xod_doctor_test.py): 27/27, covering one of
  each census shape, the no-canonical-repo hold, and clean dry-run/apply
  semantics (exactly the stale-safe set removed, measured bytes equal).
- Doctor CLI exercised end to end (--json inventory and a dry-run clean) on
  a fixture home.
- Fleet gate note: the treeman and gitcleanplan local builds currently fail
  with MODULE_NOT_FOUND in this VM (their node_modules were purged); per the
  fleet gate rule that is UNKNOWN, not a pass. Worktree facts for the shared
  gitdir were collected by hand (23 trees) and this lane's tree is
  registered in XOD-WORKTREE-OWNERS.md, which is the operative control until
  the builds are restored.
