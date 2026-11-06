"""
Repo Worker policy: the rules every Xod copy runs when it works other people's
repositories. This file is user-facing on purpose. It is the one place the
policy lives, it is plain data with comments, and it is diffable, so a person
can read exactly what their copy will and will not do before it does anything.

The gates come from the campaign's maintainer profile (Stages 0 to 6 and the
gate-pass definition). The philosophy, in Rene's words: software is built,
then fixed, then fixed again. These gates exist for safety, for respecting a
repo's own rules, and for honesty of evidence. They are not a perfection bar.
Anything a gate cannot read is UNKNOWN, and an unknown is never a pass.
"""

POLICY = {
    # ------------------------------------------------------------------
    # Identity. Repo Worker acts only as the signed-in GitHub account on
    # this Xod. It never creates or uses a second identity, and it never
    # routes around an account-level restriction (for example a comment
    # block) through another account or channel.
    "identity": {
        "act_as": "the GitHub account signed in on this Xod",
        "second_identity": False,
        "route_around_restrictions": False,
        # Every commit Repo Worker makes carries this sign-off trailer.
        # The name and the noreply address are filled from the signed-in
        # account at run time; the shape is fixed here.
        "sign_off_template": "Signed-off-by: {name} <{user_id}+{login}@users.noreply.github.com>",
    },

    # ------------------------------------------------------------------
    # Gate pass (maintainer profile, "What gate pass means"). A candidate
    # earns a pass only when EVERY gate below reads green with evidence.
    # Anything missing is a kill, a defer, or a report, named as exactly
    # that. There is no partial pass and no benefit of the doubt.
    "gates": {
        # Gate 1: Greenlight's verdict on the named issue.
        #   GO passes. TAKEN kills: an open PR already does the work, and
        #   that is a fact, not a judgment a clearing can move (Gate 4
        #   kills on the open PR too).
        #   RISKY, STALE, and an on-paper GATED are judgment calls, and
        #   they clear the way the hand-run waves clear them: with a
        #   recorded firsthand-evidence clearing passed into the gate (a
        #   maintainer invitation thread behind a STALE, a full firsthand
        #   read of the repo's AI policy behind a GATED, a file-level
        #   inspection of flagged PRs behind a RISKY), naming the
        #   evidence, its source, who recorded it, and when. Without that
        #   record, RISKY defers and STALE and GATED kill. Empty or
        #   placeholder evidence is not a record and clears nothing.
        "greenlight_verdicts_pass": ["GO"],
        "greenlight_verdicts_kill": ["TAKEN", "GATED", "STALE"],
        "greenlight_verdicts_clearable": ["RISKY", "STALE", "GATED"],
        # Gate 2: a fresh racecheck, run immediately before any build.
        #   CLEAR passes. RACE kills, and never clears: a clearing record
        #   cannot move it. SUSPECT kills unless every matched PR is
        #   inspected and proven unrelated, with the proof logged as a
        #   clearing record the same way as Gate 1.
        "racecheck_pass": ["CLEAR"],
        "racecheck_clearable": ["SUSPECT"],
        # Gate 3: the repo's own AI and contributing policy, read directly
        # (never just the index class). A label-scoped AI restriction or
        # an AI-prose ban kills the candidates it scopes. Disclosure does
        # not cure a scope ban. A rule that PR text be human-written does
        # not kill the candidate, but it blocks an automated open until a
        # person has written the prose.
        "policy_scope_ban_kills": True,
        "disclosure_cures_scope_ban": False,
        # Gate 4: never-build, one open PR per repo, issue still open and
        # unassigned (or assigned to the signed-in account itself).
        "one_open_pr_per_repo": True,
        "issue_must_be_open": True,
        "issue_must_be_unassigned": True,
        # Gate 5: budget, checked before any push phase. The GitHub core
        # rate-limit remainder must be at or above this floor, or the work
        # defers instead of starting what it cannot finish.
        "min_core_remaining_before_push": 800,
        # The rule the whole file hangs on: a gate that errored, timed
        # out, or could not be read is unknown, and an unknown gate means
        # the candidate does not proceed. It defers, named as unknown.
        "unknown_gate_means_no_open": True,
    },

    # ------------------------------------------------------------------
    # Never build. Two layers, both binding:
    #   1. Greenlight's own gates, first: a repo the index or a live check
    #      calls gated, agents-banned, or behaviorally gated is out,
    #      whatever this list says.
    #   2. This standing list: repos where building is known to be wrong
    #      (agent bans, maintainers who reject this kind of contribution,
    #      or a dedicated parallel effort that owns the repo). Kept from
    #      the campaign's wave rules; a person can edit their copy.
    "never_build": [
        "actualbudget/actual",
        "modelcontextprotocol/typescript-sdk",
        "modelcontextprotocol/python-sdk",
        "dubinc/dub",
        "astral-sh/uv",
        "astral-sh/ruff",
        "triggerdotdev/trigger.dev",
        "twentyhq/twenty",
        "activepieces/activepieces",
        "nuxt/nuxt",
        "EbookFoundation/free-programming-books",
        "huggingface/transformers",
        "spotDL/spotify-downloader",       # behaviorally gated
        "qutebrowser/qutebrowser",         # maintainer stance against LLM PRs, 2026-11-05
        "redwoodjs/graphql",               # collaborators-only PR creation
        "RaspberryPiFoundation/blockly",   # scoped below, not a full ban
    ],
    # Label-scoped exceptions to a full ban: the repo is buildable except
    # on the named labels. blockly bans AI work on good-first-issue items
    # (its PR #10629 closure, 2026-10-26); other work there additionally
    # needs prose a person wrote, which the policy scan also enforces.
    "never_build_labels": {
        "RaspberryPiFoundation/blockly": ["good-first-issue", "good first issue"],
    },
    # Repos a parallel dedicated effort owns: report-only, never built
    # or auto-fixed by a general Repo Worker run.
    "report_only": [
        "usebruno/bruno",
        "reticlehq/reticle",
    ],

    # ------------------------------------------------------------------
    # Prose. These are the prosecheck limits (the pr_prose_gate rules).
    # A FAIL blocks the open or the reply; the fix is to rewrite shorter
    # and re-check, never to waive.
    "prose": {
        "pr_body_max_words": 120,
        "pr_body_max_headings": 3,
        "pr_body_max_bullets": 7,
        "commit_subject_max_chars": 72,
        "commit_body_max_words": 60,
        "reply_max_words": 80,
        "test_report_dumps_allowed": False,
    },

    # ------------------------------------------------------------------
    # The open. Opening a PR in someone else's repo is an outward act, so
    # it sits behind the family approval model: the plan is prepared, the
    # gates are re-run fresh, and a person's approval (or a standing lane
    # the person configured) is what turns a plan into an open.
    # Phase 1 ships with real opens OFF: repo_open_plan prepares and
    # verifies everything and then stops, in dry-run, every time.
    "open": {
        "allow_real_open": False,
        "requires_approval": True,
        "verify_by_readback": True,   # never trust a creation response alone
        "force_push": False,          # never, on any branch, ever
    },

    # ------------------------------------------------------------------
    # CLAs. When a repo gates on a CLA, Repo Worker routes to the signing
    # flow under the user's standing authorization where they have given
    # one. Contracts and NDAs are never in scope.
    "cla": {
        "route_to_signing_when_authorized": True,
        "contracts_and_ndas": False,
    },

    # ------------------------------------------------------------------
    # The fleet CLIs the wrappers run (phase 1: wrap, do not rewrite).
    # Versions are floors, resolved through npx at run time. prosecheck
    # is the one gap: built, in daily use, but not yet published to npm,
    # so its command is a setting a person can point at a local build
    # (node <path>/dist/cli.js). Until it resolves, the prose gate reads
    # unknown, and unknown means no open. That is the rule working.
    "cli": {
        "greenlight": "@reneturcios/greenlight@0.26.0",
        "reprocheck": "@reneturcios/reprocheck@0.2.0",
        "commitcheck": "@reneturcios/commitcheck@0.1.0",
        "cidiff": "@reneturcios/cidiff@0.1.2",
        "shipcheck": "@reneturcios/shipcheck@0.2.0",
        "prwatch": "@reneturcios/prwatch@0.2.0",
        "prosecheck_package": "@reneturcios/prosecheck",
        "prosecheck_command": None,   # e.g. ["node", "C:/tools/prosecheck/dist/cli.js"]
        "cli_timeout_sec": 300,
    },
}
