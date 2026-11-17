# Workflow separation and private-history recovery plan

**Not executed.** This is the proposed operation for informed review, not
permission to rewrite `main`, create unregistered work, or push. The product
and CI handoff branches are preserved unchanged. Do not substitute a newly
named branch for a requested main rewrite and report it as done.

## Exact inspected refs

| Ref | Inspected full tip | Meaning |
| --- | --- | --- |
| `origin/main` | `d8b30112cfedfc139b8086d8c0471910253804c6` | Imported upstream base, shallow boundary |
| local `main` | `8e843d80554c3f2a23f6f707ef1f925edf7bea1e` | 13 local commits after base before this plan/hygiene commit |
| `xod-handoff-product` | `48c1bbea037bb7ba7c0e00d2abcf25eb93f50ee6` | One product-only commit over imported base |
| `xod-handoff-ci` | `15978f28319cb7d8bd5802ecc76bd974878cf42d` | Same product commit, then one workflow-only commit |

The CI handoff's parent is the product handoff. Their only inter-tip changed
path is `.github/workflows/ci.yml`. Product handoff changes no workflow path
relative to the base. Neither handoff tip tree includes agents or uploads.
They carry the earlier approval/runtime repair, **not all later model, fleet,
native-readiness and observability work**. They are not a complete current
release merely because their separation is clean.

## Every mixed workflow commit in local main after the base

| Source commit | Workflow path | Other nonprivate paths changed | Required reconstruction |
| --- | --- | ---: | --- |
| `88941899297685a09e806868e982bab6e2b52b81` | `.github/workflows/ci.yml` | 16 | Separate product delta and workflow-only delta; omit private memory |
| `339f0d75aa0cc51373076641347a25d5efbeaf3b` | `.github/workflows/ci.yml` | 24 | Separate product delta and workflow-only delta; omit private memory |
| `c90f418649b1e8e96b15977bc78db9d8d49ef2e4` | `.github/workflows/ci.yml` | 16 | Separate product delta and workflow-only delta; omit private-memory removal history too |
| `464026a4716699b637f47f2defe8b6f1d5aab1ae` | `.github/workflows/fleet.yml` | 12 | Separate product delta and workflow-only new file |
| `5d2375caca5ca11840f795b4609ad101b9e306bf` | `.github/workflows/computer-control.yml` | 16 | Separate product delta and workflow-only new file; omit private metadata |

These five, not only the later three mentioned in the handoff, are reachable
from inspected local main. Workflow changes already in upstream history are
not rewritten by this proposal. The handoff CI commit `15978f2` is already
workflow-only and is not modified.

### Private history is a separate blocker

`4509e738` introduced private memory; `88941899`, `339f0d75` and `c90f4186`
modified/removed it. `8d518a25` introduced agent asset metadata; `3b1b81d6`,
`5d2375ca` and `3897f58b` changed it. These are inspected commit prefixes, not
a claim to have scanned all earlier upstream objects.

The two private-only commits are omitted from reconstructed publishable
history. For mixed commits, private path deltas are excluded. Merely adding
a final deletion or ignore rule does not remove old private objects from an
ancestry chain. Never push raw local main, `--all`, `--mirror`, backup refs,
agents, uploads, private archives, generated evidence or credentials.

### Why isolation alone will not unblock a push

Earlier handoffs report GitHub rejected workflow changes for insufficient
workflow authorization. That denial was not retried as a write in this task.
A push of main still carries reachable workflow commits even after those
commits contain nothing else. Isolation enables separate review/delivery; it
does **not** grant permission to push that history. Workflow authorization,
branch protection and current remote tips must be checked before any later
remote update. No permission bypass is proposed.

## Approval gates

Before reconstruction:

1. Confirm accountable owner, actual tracking issue and one accepted merge
   target; register any candidate branch/worktree before creating it.
2. Freeze the exact post-preparation source SHA, base SHA and both handoff
   tips in a private manifest. Re-review if any tip changes. The two handoff
   tips above must remain byte-for-byte the same throughout.
3. Obtain explicit permission to collect complete-history evidence in a
   separate audit copy. The current shallow checkout cannot certify earlier
   upstream ancestry or a complete privacy scan.
4. Create and restore-test the private backup below. Do not trust the moving
   platform backup ref as the sole recovery method.
5. Obtain explicit approval for **local main history replacement** after
   candidate review. Approval to inspect/build candidates is not approval
   to move main. Approval to move local main is not remote force-push approval.

Creating new commit identities changes descendants, invalidates old commit
references/signatures, can disrupt open reviews and collaborators, and can
make an ordinary remote push non-fast-forward. Existing checkpoints/private
archives must remain recoverable. No claim that a rewrite will speed saving
or shrink this modest repository is made.

## Private backup and tested restoration, before any replacement

Proposed Bash sequence, **not run here**. Run only with a clean tracked/index
state after the plan/hygiene commit, registered work and reviewed source pins.
The backup remains under ignored `generated-artifacts/` and must be copied
to owner-controlled durable private backup storage before relying on it.
It contains private history: never publish, push, attach to a PR or serve it.

```sh
set -euo pipefail
test -z "$(git status --porcelain --untracked-files=no)"
BASE=d8b30112cfedfc139b8086d8c0471910253804c6
SOURCE="$(git rev-parse refs/heads/main)"
# Stop here and obtain recorded approval of this exact SOURCE, not an old HEAD.
test "$(git rev-parse xod-handoff-product)" = 48c1bbea037bb7ba7c0e00d2abcf25eb93f50ee6
test "$(git rev-parse xod-handoff-ci)" = 15978f28319cb7d8bd5802ecc76bd974878cf42d
BACKUP="$PWD/generated-artifacts/repository-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -m 700 "$BACKUP"
git show-ref >"$BACKUP/refs.txt"
printf '%s\n' "$BASE" "$SOURCE" >"$BACKUP/source-pins.txt"
COMMON="$(git rev-parse --git-common-dir)"
parts=()
for p in HEAD objects refs packed-refs shallow; do
  test ! -e "$COMMON/$p" || parts+=("$p")
done
tar -czf "$BACKUP/git-history.tar.gz" -C "$COMMON" "${parts[@]}"
mkdir -m 700 "$BACKUP/restore.git"
tar -xzf "$BACKUP/git-history.tar.gz" -C "$BACKUP/restore.git"
git --git-dir="$BACKUP/restore.git" fsck --connectivity-only --no-reflogs --no-dangling
test "$(git --git-dir="$BACKUP/restore.git" rev-parse refs/heads/main)" = "$SOURCE"
test "$(git --git-dir="$BACKUP/restore.git" rev-parse xod-handoff-product)" = \
  48c1bbea037bb7ba7c0e00d2abcf25eb93f50ee6
test "$(git --git-dir="$BACKUP/restore.git" rev-parse xod-handoff-ci)" = \
  15978f28319cb7d8bd5802ecc76bd974878cf42d
```

This snapshots objects/refs plus the shallow boundary, not Git credential
configuration, hooks, a keyring or untracked user data. A shallow bundle is
not assumed independently restorable. All local user files remain untouched;
this backup is not permission to delete them.

## Exact candidate reconstruction algorithm

This is a review recipe, **not an executed shell script**. `BASE` and `SOURCE`
refer to the approved frozen manifest, not moving branch names.

1. In a registered, isolated candidate worktree at BASE, enumerate source
   commits with `git rev-list --reverse BASE..SOURCE`. Require every commit
   in this range to have exactly one parent. Stop for any merge, unknown
   object, changed source tip or unreviewed path.
2. For each old commit, in order, build its binary product patch:

   ```sh
   git diff --binary "$old^" "$old" -- . \
     ':(exclude).github/workflows/**' \
     ':(exclude).agents/**' ':(exclude)attached_assets/**'
   ```

   Review an allowlist of every remaining changed path. Stop on any other
   private, generated or secret-bearing content. Apply the reviewed patch
   to the candidate with `git apply --index`, then commit only if the staged
   delta is nonempty. Record old-to-new mapping in the private manifest.
3. For that same old commit, generate each workflow path delta separately:

   ```sh
   git diff --binary "$old^" "$old" -- "$workflow_path"
   ```

   Apply and commit it separately, after the product delta. Assert the commit
   contains **only that exact `.github/workflows/` path**, no source, test,
   docs, metadata or private files. Preserve the order of all five workflow
   deltas, including the three successive `ci.yml` edits. Skip empty patches;
   never manufacture a private-only commit.
4. The inspected 13-commit source range would produce 16 nonempty split
   commits by this algorithm. The preparation commit and any later approved
   work extend that count; recompute it from the frozen source, do not assume
   16 remains correct. Retain a mapping for each dropped/private-only source.
5. Compare the candidate product tree to SOURCE excluding the three forbidden
   prefixes above; it must be identical. Compare final workflow bytes to
   SOURCE; they must also be identical. Candidate tip must contain no agents
   or uploads, and the three desktop build assets must match SOURCE.
6. Inspect **every newly reachable commit**, not just HEAD: each changed path
   set must be entirely product or a single workflow file; no agents/uploads
   may ever be introduced. Review the complete publication graph for other
   private data and run the existing release check plus a redacted full-history
   secret scanner. A clean final tree is insufficient. The shallow evidence
   limitation must be resolved before claiming a publishable graph.
7. Run the relevant reliability, approval, native-limit, fleet and runtime
   regressions on the reconstructed candidate. Their earlier passes apply to
   the earlier source tree, not automatically to a reconstruction.
8. Compare all protected refs to the saved manifest. Stop on any drift.
   Keep all candidates for review; do not move main yet.

### Clean delivery route, only if separately chosen

To avoid pushing any workflow delta with restricted authorization, a separately
approved, registered **product-only candidate** can instead apply the reviewed
`BASE..SOURCE` product snapshot (same exclusions) over BASE, then commit that
product delta alone. A separately registered CI stage starts from that product
candidate and applies the five workflow deltas as five workflow-only commits.
The final CI-stage tree must match the sanitized SOURCE.

That option is **not** a main rewrite and is not executed as a silent substitute.
The existing `xod-handoff-product` and `xod-handoff-ci` names/tips remain
unchanged. Never present their old product tree as carrying later work.
Actual issue/owner/merge-target registration is required for new candidates.

## Local replacement and recovery, only after specific approval

After successful candidate review, verified backup, unchanged source and
protected tips, and explicit approval to replace **local** main:

- Require the post-hygiene SOURCE tip tree and verified candidate tip tree to
  be exactly identical. If not, stop; no reset, broad checkout, file deletion
  or automatic conflict resolution.
- The exact conditional pointer replacement would be
  `git update-ref refs/heads/main "$VERIFIED_NEW_MAIN" "$APPROVED_OLD_MAIN"`.
  The old-value check must match the frozen SOURCE. It prevents replacing
  intervening work. No `reset --hard`, pruning or branch deletion is involved.
- Read back main, tree identity, clean tracked state and both unchanged
  handoff tips. Preserve old objects and the private backup.
- Recovery, only with a clean unchanged worktree and recorded matching new
  tip, is the inverse conditional pointer replacement:
  `git update-ref refs/heads/main "$APPROVED_OLD_MAIN" "$VERIFIED_NEW_MAIN"`.
  Because full trees were required equal, no user-file overwrite is needed.
  If state changed since replacement, stop and preserve it before recovery.
- If objects are unavailable, use the privately restored repository and
  verified pins; do not overwrite the live checkout or rely on expiring reflogs.

**No remote update is authorized by this plan.** A future push needs a new
read-back of exact destination/reviewed tips, privacy checks over every object
to be published, appropriate workflow permission, protection checks, explicit
user approval and destination read-back. Replacing remote history would need
its own explicit approval and exact expected-old-tip condition. No force-push
command, credential request or push attempt is executed here.

## Current result

**Owner decision: keep main unchanged.** The owner explicitly declined the
proposed local history rewrite after reviewing its consequences. Reconstruction,
candidate creation and remote delivery are not authorized by that decision.
Ordinary additive commits recording this plan and tracking hygiene do not
rewrite existing history.

History analysis and the recovery/reconstruction plan are DONE. Main rewrite
is **NOT AUTHORIZED** and will not proceed. Any future reconsideration requires
a new explicit request plus owner/issue/target registration, complete
history/privacy evidence, a restored backup and candidate verification.
Remote delivery is additionally blocked on confirmed authorization/protection.
Both original handoff branches remain untouched. See
`REPOSITORY-CLEANUP-PLAN.md` for the full bounded ledger and seven-item status.
