# Repository registration and safe cleanup plan

Prepared November 16, 2026 (Pacific time). **Plan only: no branch, worktree or
product file was deleted, moved, renamed, merged or repurposed.** The only
tracking removal is the explicitly requested agent metadata file; its local
copy remains. No history rewrite or push has been executed. The Round 4
execution request and outstanding gates are recorded in
`WORKFLOW-HISTORY-PLAN.md`; this cleanup document itself authorizes no file
deletion, move or rename.

## Evidence inspected and what it does not prove

| Evidence | Result | Limit |
| --- | --- | --- |
| Supplied status, pieces list, repair handoff and Round 3 handoff | Report a 122-finding audit, including 39 unregistered worktrees and 19 unregistered branches | Narrative summaries, not the original finding records |
| `docs/orchestration-tiers/deliberations/DELIB-0002/01-registration-proposal.md` | Proposes registering six orchestration claims, CLM-015 through CLM-020 | A claim-registry deliberation, not a branch/worktree audit or an ownership assignment |
| `.local/reports/xod-registration-plan.md` | Existing read-only registration proposal and three conditional fixture branch candidates | Not a completed wider-project audit |
| `.local/reports/xod-registration-inventory.json` | 56 planning records, captured 2026-11-16T23:16:37Z | Not authoritative owners/issue registration; several local tips have advanced |
| `.local/reports/xod-registration-ledger.csv` and `xod-registration-plan.html` | CSV/HTML views of that same inventory | Derivatives, not independent audit sources |
| Local inventory/render scripts | Explain Git-derived evidence and report generation | Script existence does not establish ownership or original audit closure |
| Fresh read-only `git ls-remote --heads origin` | 40 live GitHub branch heads; all exactly match the earlier inventory | Does not establish PR state, branch protection, owner or other-machine dirty state |
| Current local refs and registered worktrees | Four local product branches and one product checkout | Not a search of remote machines or every filesystem |
| Three named Crew fixture repositories | Still present; equal main/elsewhere tips; each has untracked `here.txt` | Branch redundancy does not authorize deleting files/directories |
| Git history and handoff trees | Five mixed workflow commits; two handoff branches remain unchanged | Shallow boundary prevents complete-history ancestry/privacy claims |

The current source history inspected ends at
`8e843d80554c3f2a23f6f707ef1f925edf7bea1e`. This document's preparation commit
will follow that snapshot; a later operation must freeze and re-review its
actual source SHA. No new remote commit was written or pushed here.

### Missing original sources

The coordinating host's `github-builds/XOD-WORKTREE-OWNERS.md`,
`control-plane/` audit exports, `xod-eval/` gate records and
`goals/xod-launch/files/XOD-REPLIT-BRIEF-2026-11-16.md` are absent from this
checkout. No export of the original 122 finding IDs, authoritative owner
assignments, AGE-61 through AGE-70 issue records, current integration line,
original checkout paths, PR state or branch protection is available.

Request those exports, including repository identity, finding ID/category,
device/path or ref, audited tip, timestamp, owner, real issue link, accepted
merge target and closure evidence. Also request read-only current inventories
and dirty state from the source machines. Missing here does not mean missing
on the original host.

**Do not infer the other audit categories by subtraction, equate these 40
branches to the reported 19, invent 122 rows, or close findings from counts.**

## Registration ledger for all supported entries

The 56 stable REG IDs below are carried over from the existing local ledger,
not invented Linear issues or original audit finding IDs.

- **Owner UNASSIGNED** means no accountable owner was verified. A commit
  author, branch prefix or generated fixture name is not an assignment.
- **LOCAL_ONLY** means a local planning row exists. Authoritative registration
  and external issue linkage are UNKNOWN, not proven absent.
- Actions are proposed `REGISTER`, `MERGE`, `PARK` or `DELETE-CANDIDATE`.
  None authorizes execution. `REGISTER` also holds implementation until owner,
  actual issue and accepted target exist.
- Target **M** is this product repository's `refs/heads/main`, subject to
  confirmation of exactly one integration line. Delivery stages are not
  competing integration lines. Target **F1/F2/F3** is the retained main in
  that same fixture repository.
- Evidence **L**: current local Git ref; **W**: current worktree list;
  **R**: fresh live GitHub head read-back; **F**: current fixture ref/status
  read-back. Short tips below identify full commits in the local inventory
  or repository; ancestry counts beyond the shallow boundary are UNKNOWN.

### Product checkout, local refs and fixture entries

| ID | Ref or location | Owner | Registry | Action / target | Evidence |
| --- | --- | --- | --- | --- | --- |
| REG-001 | local `main` | UNASSIGNED | LOCAL_ONLY | PARK / M; do not push private checkpoint history | L `8e843d80554c` |
| REG-002 | local `replit-agent` | UNASSIGNED | LOCAL_ONLY | PARK / M; platform-managed, no repurposing | L `6591ba3826ab` |
| REG-003 | local `xod-handoff-ci` | UNASSIGNED | LOCAL_ONLY | MERGE / M only after product and workflow authorization | L `15978f28319c` |
| REG-004 | local `xod-handoff-product` | UNASSIGNED | LOCAL_ONLY | MERGE / M after review; preserve existing ref | L `48c1bbea037b` |
| REG-005 | current product checkout, branch main | UNASSIGNED | LOCAL_ONLY | PARK / M; retain all local files | W `8e843d80554c`; only intended plan/hygiene edits |
| REG-006 | F1 `elsewhere` | UNASSIGNED | LOCAL_ONLY | DELETE-CANDIDATE / F1, branch only, not approved | F `9a34cfb8a88b`; identical main; zero unique commits |
| REG-007 | F1 `main` | UNASSIGNED | LOCAL_ONLY | PARK / F1 | F same tip; untracked file retained |
| REG-008 | F1 registered checkout | UNASSIGNED | LOCAL_ONLY | PARK / F1; no directory cleanup | F main checked out, `here.txt` untracked |
| REG-009 | F2 `elsewhere` | UNASSIGNED | LOCAL_ONLY | DELETE-CANDIDATE / F2, branch only, not approved | F `b8302149996a`; identical main; zero unique commits |
| REG-010 | F2 `main` | UNASSIGNED | LOCAL_ONLY | PARK / F2 | F same tip; untracked file retained |
| REG-011 | F2 registered checkout | UNASSIGNED | LOCAL_ONLY | PARK / F2; no directory cleanup | F main checked out, `here.txt` untracked |
| REG-012 | F3 `elsewhere` | UNASSIGNED | LOCAL_ONLY | DELETE-CANDIDATE / F3, branch only, not approved | F `642814d89eec`; identical main; zero unique commits |
| REG-013 | F3 `main` | UNASSIGNED | LOCAL_ONLY | PARK / F3 | F same tip; untracked file retained |
| REG-014 | F3 registered checkout | UNASSIGNED | LOCAL_ONLY | PARK / F3; no directory cleanup | F main checked out, `here.txt` untracked |
| REG-055 | `gitsafe-backup/main` tracking ref | UNASSIGNED | LOCAL_ONLY | PARK / M; preserve platform metadata | L `8e843d80554c`; not a frozen manual backup |
| REG-056 | `origin/HEAD` symbolic alias | UNASSIGNED | LOCAL_ONLY | PARK / M; not a work branch | L points to `origin/main`, `d8b30112cfed` |

F1 is `/tmp/xod-crew-0gergmq_/repo`; F2 is `/tmp/xod-crew-4jnp3h4m/repo`;
F3 is `/tmp/xod-crew-fe2mxi_w/repo`. These are disposable test locations,
not product paths. Each was re-read with `rev-parse main elsewhere`,
`rev-list --count main..elsewhere`, `worktree list --porcelain`, and
`status --porcelain=v1 --untracked-files=all`. Unique count was 0 in each.
No fixture file contents were read. No product deletion candidate is proven.

### All 40 live remote heads

Refs are `refs/heads/<name>` on origin; every proposed target is M.
All non-main entries require owner, issue, PR/protection and complete ancestry
review before merge or any deletion proposal.

| ID | Branch | Owner | Registry | Action / target | R: live tip |
| --- | --- | --- | --- | --- | --- |
| REG-015 | `claude/release-next` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `c37a0d00200b` |
| REG-016 | `dependabot/npm_and_yarn/electron/electron-45.3.1` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `66771e7f650d` |
| REG-017 | `dependabot/pip/comtypes-1.4.17` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `1e524a1d519d` |
| REG-018 | `dependabot/pip/mlx-lm-0.32.2` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `64b25d4b3cd1` |
| REG-019 | `dependabot/pip/pyobjc-framework-applicationservices-13.0` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `616e6c58572d` |
| REG-020 | `dependabot/pip/pyobjc-framework-quartz-13.0` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `639a872df592` |
| REG-021 | `dependabot/pip/yt-dlp-2026.10.26` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `6a0a7adf7c30` |
| REG-022 | `feature/agentmail-live` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `9abb590f8358` |
| REG-023 | `main` | UNASSIGNED | LOCAL_ONLY | PARK / M; retained target | `d8b30112cfed` |
| REG-024 | `memory-unified-store` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `7d722c3aa25b` |
| REG-025 | `tree/age57-stable-signing-2026-11-15` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `64a476bfdef2` |
| REG-026 | `tree/age71-named-surface-2026-11-14` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `62d442545de4` |
| REG-027 | `tree/age71-named-surface-detection-2026-11-16` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `c5da73550126` |
| REG-028 | `tree/age72-version-floor-2026-11-16` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `602c5fe8e794` |
| REG-029 | `tree/age73-smoke-glob-fixture-2026-11-15` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `0042f820f826` |
| REG-030 | `tree/age74-reload-secrets-atomic-2026-11-16` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `9c145c8fbe55` |
| REG-031 | `tree/age75-darwin-runner-2026-11-16` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `29fc9983112a` |
| REG-032 | `tree/age76-provisioning-2026-11-16` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `087ed134b0f2` |
| REG-033 | `tree/age77-discovery-contract-2026-11-14` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `bc00448d41ef` |
| REG-034 | `tree/age77-iteration2-2026-11-14` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `3ae8ba5e6a4d` |
| REG-035 | `tree/age77-iteration3-2026-11-15` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `142a622e3f21` |
| REG-036 | `tree/age78-iteration2-2026-11-15` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `30efd5d9beab` |
| REG-037 | `tree/age78-seal-bytecode-2026-11-14` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `7c81406ab05e` |
| REG-038 | `tree/age83-stall-diagnosis-2026-11-16` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `9fe869639a6d` |
| REG-039 | `tree/bench-ble-guidance` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `f9dc2bcaa715` |
| REG-040 | `tree/ble-pairing-evidence` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `93e5d48e3cfb` |
| REG-041 | `tree/family-link-f0` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `cdfd38d8b5e8` |
| REG-042 | `tree/fireworks-catalog` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `745751dee078` |
| REG-043 | `tree/fleet-bridge-2026-11-16` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `26fa9bed0b94` |
| REG-044 | `tree/harness-computer-control-2026-11-16` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `6089c4d60047` |
| REG-045 | `tree/inbox-status` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `6423172770fa` |
| REG-046 | `tree/keychain-ci-2026-11-16` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `40a359a54b9b` |
| REG-047 | `tree/keychain-ci-proof-2026-11-16` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `043b8b55e135` |
| REG-048 | `tree/lightweight-modes` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `1d2905ca88e0` |
| REG-049 | `tree/live-regrade2-2026-11-14` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `b0d25a0eb71b` |
| REG-050 | `tree/local-engine-opt-in` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `ff2b5c64af90` |
| REG-051 | `tree/repo-worker-phase2` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `1551e904e34a` |
| REG-052 | `tree/tests-push-d1` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `63cff5c5b90e` |
| REG-053 | `tree/tier78-probes` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `05cd50353e25` |
| REG-054 | `tree/v115-bootstrap-fix-2026-11-14` | UNASSIGNED | LOCAL_ONLY | REGISTER / M | `184617e85b40` |

### Proposed order, not executed

1. Confirm the integrator, one accepted integration target, accountable owners,
   and actual tracking entries. Do not create competing branches/worktrees
   before registration. These REG rows are the import/reconciliation inputs.
2. Obtain the missing original audit and current source-machine evidence.
   Match records by repository/ref/path/tip/time, not aggregate counts.
3. Review per-owner unique commits, PR state and protections using complete
   history. A negative ancestry result in this shallow checkout proves neither
   divergence nor safe deletion. No product entry currently qualifies.
4. Consider the three branch-only fixture candidates only with their owner's
   explicit approval and unchanged evidence. Directory/untracked-data deletion
   is a separate decision and is not proposed in this round.
5. For workflow separation, follow `WORKFLOW-HISTORY-PLAN.md`. Preserve both
   named handoff refs and never merge/push raw local checkpoint history.

## Tracking hygiene applied, not a history scrub

Only `.agents/agent_assets_metadata.toml` was removed from the Git index using
`git rm --cached`; the file and all four local memory Markdown files remain.
`.gitignore` now excludes `.agents/` entirely. `.replitignore` excludes agents
and uploaded working materials as well as generated evidence. Uploads and
memory were not deleted or moved. Old commits still contain private working
material; ignoring or untracking today does not make that history publishable.

All three assets remain tracked and byte-identical to the inspected HEAD:

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `electron/build/entitlements.mac.plist` | 935 | `acc7c5b3218c3e66b7e761aaa672e50e6a891010acdc95c68e1ea4df10b88903` |
| `electron/build/icon.ico` | 51,934 | `7f54430915c9dc294436b17735a41f3a1984e13652e6cb28c9c89e306209afe3` |
| `electron/build/icon.png` | 100,654 | `4e07225f14d905a791f8c26b8effa008d5e2513f352ac9d87b0879df6c2a39e4` |

Survey of this working checkout: about 837 MB on disk, about 82 MB of saved
Git history, 904 tracked files initially, 5,019 bytes of `replit.md`; no survey
timeout or in-progress Git operation. Dependency installs and intended demo
media explain most disk usage. No saving-speed problem was demonstrated.

| Measure | Before | After this preparation |
| --- | --- | --- |
| Product/fixture checkouts | 1 / 3 | 1 / 3 |
| Live remote branches | 40 | 40 |
| Local product branches | 4 | 4 |
| Tracked working-material files under agents/uploads | 1 | 0 |
| Desktop build assets tracked | 3 | 3, unchanged |
| Total tracked paths | 904 | 905 after two plan documents are added and metadata is untracked |
| Disk / saved history | About 837 MB / 82 MB | Same rounded size; no history pruning or space-saving claim |
| Project instructions | 5,019 bytes | Unchanged |
| Runtime check duration / checkpoint speed | Not measured for this plan | No performance claim |

Validation is limited to ledger completeness, actual Git refs/paths, unchanged
asset bytes, ignore rules, diff hygiene and the existing release checker.
Runtime/browser/native suites are not rerun for documentation/index changes.

## Seven-item Round 3 closeout

Evidence from earlier work is linked, not claimed as executed by this
repository-preparation task.

1. **DONE, bounded plan; reconciliation BLOCKED:** all 56 available records
   listed with owner/state/action/evidence. Original 122 rows, authoritative
   registry, issues and source-machine inventories remain unavailable.
2. **DONE for readiness changes/procedure; native acceptance BLOCKED:**
   `AGE85-REAL-DEVICE-ACCEPTANCE.md` records earlier checks and the exact Mac
   procedure. Actual Mac/Windows device evidence is still unavailable here.
3. **DONE previously:** `requirements.txt` pins `websocket-client==1.9.0`.
   The earlier native-readiness commit records why; not reinstalled here.
4. **BLOCKED, plan DONE:** five mixed workflow commits identified in
   `WORKFLOW-HISTORY-PLAN.md`. The earlier owner-decision assertion has been
   removed. Round 4 requests conditional execution; outstanding registration,
   replacement-review, backup, privacy and authorization gates are documented there.
   No history rewrite or remote push executed; both handoff refs unchanged.
5. **DONE here:** only the requested metadata file untracked, whole agents
   directory ignored, all local memory retained, all three build assets intact.
6. **DONE previously for bounded live context/fleet proof:** see
   `LIVE-AGENT-EVALUATION.md` and `MODEL-CONFIGURATION.md`; recorded Terra input
   was 55,886 tokens, followed by real worker/follow-up evidence. This is not
   hours-long, interruption or live-compaction proof; no model calls run here.
7. **DONE previously for collector portability; whole-app figure BLOCKED:**
   `LIGHTWEIGHT-FOOTPRINT.md` and native-acceptance notes distinguish Linux
   observations/platform fixtures from missing native Partner/Electron data.
   No resource measurement or invented whole-app number added here.

## Reconciliation results — placeholder (November 16, 2026, Pacific)

Awaiting the two exports named in the Round 4 follow-up: the 122 finding
records and the authoritative owners registry. Reconciliation results and
their source provenance will go here once those exports are supplied.
No finding rows, owners, registry matches or decisions have been invented.
The existing bounded ledger is unchanged; this placeholder authorizes no
deletion, move or rename.
