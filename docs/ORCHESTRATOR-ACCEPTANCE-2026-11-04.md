# Orchestrator build: acceptance report (2026-11-04)

Build brief: ORCHESTRATOR-SCOPE-FOR-CLAUDE-2026-11-04 (by Tree), sections 1–12 plus Addendum A (the difficulty
ladder). Scope grew during the session: from Phase 1, to Phases 1–4, to every acceptance test in the spec passing
live, plus headless/lightweight mode. Everything was built and proven on isolated dev instances (each with its own
`XOD_DATA_DIR`; ports 8211, 8212, 8213) against the real model (OpenAI gpt-6.1-terra, the account's configured
model). The installed 0.9.25 app and its data folder were never touched. Nothing is merged to main, nothing is
pushed, no release was cut, and `tree/lightweight-modes` and `tree/family-link-f0` were not modified (the headless
work cherry-picks the former's one commit onto a branch of its own).

## Verdict

**Reliable through Tier 6 on repeated live runs**, with these limits:

- **Tier 5** (delegated parallel work): its live tests pass, but they are the spec's own small cases (2 research
  jobs; 6 checks). That is not wide parallel load.
- **Tier 7** (recurring autonomous work): it passes its live test, a watch job across restarts. That proves the
  mechanics, but the run is compressed: 6 runs in minutes, not a week of real schedule.
- **Tier 8** (fleet work): not claimed. Resource claims for phones are proven live, but there is no real
  multi-device run with real phones.

See the table for the run counts behind each tier.

## Branches

| Branch | Adds |
|---|---|
| `claude/orchestrator-phase1` | ack-first background tasks; durable versioned plan per task; steer inbox; honest step completion; waits on a step's own background jobs; agent shell commands through the executor; the chat can hand work to the orchestrator; named files are read, not hunted |
| `claude/orchestrator-phase2` | workers (briefs, isolated contexts, structured results, honest states); coordinator fan-out; read-only permission envelope; CONFIRMED / FAILED / UNKNOWN / UNVERIFIED outcomes with destination checks; a job's identity is its command |
| `claude/orchestrator-phase3` | resource registry (claims, heartbeats, expiry, FIFO with visible reasons); worker steering; worker stall watchdog; fan-out resume after restart |
| `claude/orchestrator-phase4` | the full stack: routine run records, notification policy, exactly-once delivery; memory in layers (§8); provenance (§10); tasks side by side under the resource registry; UNKNOWN settled at step end; test hooks; live suites. **All live runs below were made on this branch.** |
| `claude/orchestrator-phase5` | the working branch phase4 is fast-forwarded from (identical) |
| `claude/headless-mode` | phase4 + headless and lightweight modes, one switch in the desktop tray, Settings and the terminal |

Each branch merges cleanly into the next. Fixes were made on the lowest branch that had the code and merged up.

## How the counting was done

- **Unit suites**: every orchestrator suite run `--repeat 3` on the exact commit under live test (d22086c),
  plus Xod's existing suites once on that commit.
- **Live**: three full live passes (e1, e2, e3) on the phase4 dev instance, consecutive, all on commit d22086c,
  with no code change in between. Each pass ran all 69 live checks: L1–L4 28, P1 7, S5/S7–S12 34.
  **All three passes: 69/69.** (The run before them, d1 on 80ea46f, was 67/69. Its two misses are the ones
  d22086c fixed.)
- Earlier runs that failed are not hidden: every failure found live, and what fixed it, is listed below with its tier.

## Results by tier (ascending)

| Tier | What was tested live (spec section) | Consecutive live passes | Unit (3 repeats) |
|---|---|---|---|
| 0 Reflex reply | 3 chat questions answered while a 10+ minute task runs, every run (§1) | 3/3 (answers in 1.1–2.0 s) | n/a |
| 1 Single read | S10 (read a document and summarize it); the S5 files that do exist | 3/3 | memory/provenance 63/63 |
| 2 Single action, verified | L1 (ack in 36–156 ms, then one write verified on disk); S9 (a test send dies mid-call: UNKNOWN, checked at the destination, landed → confirmed and not resent; not landed → retried exactly once) | 3/3 | workers 122/122 |
| 3 Multi-step, one agent | L2, L3, L4: 3–8 step plans, each step completed only on an established outcome; L3 kills the backend mid-step, and the plan shows the in-flight step and resumes it with no repeated effect | 3/3 | orchestrator 234/234 |
| 4 Research across sources | P1 (two sourced answers); S5 (6 checks, the 2 unreadable ones named as unread, with the error) | 3/3 | workers 122/122 |
| 5 Delegated parallel work | P1 (two concurrent workers, no context leak, one parent answer); S5 (one parent result with failures itemized); S11 (a frozen worker reported stalled with its true last-activity time) | 3/3 | phase3 75/75, concurrency 33/33 |
| 6 Long-horizon, interruptible | L2 (redirect halfway: completed steps kept, no restart, no duplicate); L3 (restart mid-task); L4 (an 11.5–11.9 minute task, started from the chat, which replied with the task id in 7.6–8.5 s; every effect exactly once) | 3/3 | orchestrator 234/234 |
| 7 Recurring autonomous work | S7 (4 silent no-change runs; the finding delivered exactly once across two backend kills; the same finding in other words not re-delivered) | 3/3 | delivery 78/78 (incl. 168 silent hourly runs) |
| 8 Fleet across devices | S12 (two workers contend for one phone: one granted, one queued with its reason; the grant passes on finish and on holder death) | 3/3, mechanics only | phase3 75/75 |

Also live, not on the ladder:

- **S8 memory** passes 3/3: a preference told once in chat survives two restarts, is applied unprompted by a fresh
  task, and Xod says where it learned it ("You told me in a chat on November 4, 2026").
- **Headless / lightweight**: a lightweight server runs the orchestrator live (L1–L3, 20/20), and the terminal
  drives it.

## Failures found live, by tier, and what fixed them

Every one has a regression test built from the model's real words where there were any.

| Tier | Failure (observed) | Fix |
|---|---|---|
| 2 | A step's file write was in no log: the agent's shell commands bypassed `execute_tool` entirely, so activity log, Sentinel and every gate never saw them | shell commands now go through the executor, still streamed live |
| 2 | The send test action was never offered: an address in the request routes the agent to its browser lane; with no address, the model sent with `curl` via a shell command instead | the S9 step action is scripted behind a test hook (the engine is what §9 tests); `curl` sends are a standing finding, below |
| 3 | A retry after an unconfirmed job said "do it now for real": the job ran twice | a retry after any action is verify-first (outcome UNKNOWN, not failed) |
| 3 | "still running… I can't confirm" marked a step completed; the task's end then reaped the job | broader unverified-report check; the orchestrator waits for a step's own running job and judges on its observed exit |
| 3 | "I couldn't send … the tool isn't available" marked completed | not doing the step's action is not doing the step |
| 3 | A verifying retry's "I did not run it again" was read as failure | neutralized |
| 3 | The same append ran twice under two job names | a job's identity is its command and folder; a command run twice in a step is reported as a duplicated side effect |
| 3 | Restart: the job guard refused to restart a job that died with the server, so the task could never finish | the guard decides on the job's observed fate (running / finished / failed / lost) |
| 3 | "Summarize the file X" was split into read + summarize, and the summary step saw 300 characters of the read | results travel between steps in full; reads stay with the step that uses them |
| 3 | A file named by full path was hunted ("two close matches") | a named existing file is read, not hunted |
| 4 | Missing-file workers passed as done; an empty "Could not read: Nothing" section failed a good worker | a source that never answered fails its worker (search providers count as one); empty sections ignored |
| 4 | A sourced but unconfirmed answer was dropped and the parent said "unknown" | it reaches the synthesis labeled unverified, with why |
| 6 | The chat could not start a background task: the planner only sees tools named in its own prompt | the planner now offers `xod_task` outside tasks |
| 7 | The same restock was delivered twice: once with the finding above the verdict line (empty message), once worded differently or wrapped in a file path | finding parsed on either side of the verdict; same-finding comparison ignores wording and paths, but not numbers |
| 8-adjacent (memory) | Asked where it learned a preference, Xod said it "can't tell"; the live check had wrongly accepted that | facts carry their source and date into the chat; the check now requires a named source |

## Where the spec and the codebase disagree (flagged, not improvised)

1. **Concurrency vs global browser state.** Xod's browser mission and pending-approval state are process-global. I
   resolved this by letting orchestrated tasks run side by side (a pool of 3) while the global state is only touched
   by the task holding the `browser` claim. Classic (non-orchestrated) tasks still run one at a time. True per-task
   browser state would need a deeper change in `browser_sandbox.py`.
2. **Chat and tasks share one process and event loop.** Responsiveness held in every counted run: chat answered in 1.1–2.0 s.
   The spec's "split the chat agent from the execution engine" is met as separate asyncio lanes, not separate
   processes, so a blocking call in a tool would freeze both.
3. **Two multi-step systems coexist.** These are missions ("Plan & run") and orchestrated tasks. The spec asked to
   extend tasks/missions/mission_subtasks. I extended tasks (`plan_json` and friends) and left missions as they were.
4. **The model does not reliably pick an unfamiliar tool.** It reads Xod's own source or uses `curl` instead.
   Sends done through shell commands get only UNVERIFIED outcomes (no destination read-back). Real send tools
   (mail, phone) need verifiers registered to get §9's guarantee.
5. **Xod's agent sometimes ends a turn before a background job finishes.** It checks a 30–75 s job after about
   2 seconds. The orchestrator now compensates by waiting on the job itself, but the agent loop does not.
6. **Tier 7 is compressed.** The week of silent runs is proven in a unit test (168 hourly runs) and live with 4–6
   runs, not over a real week.
7. **Test hooks.** `XOD_TEST_HOOKS=1` adds `test_send`, a scripted-step task, and worker freeze/kill/spawn
   endpoints. They are off unless that variable is set.

## Headless and lightweight (`claude/headless-mode`)

- **One switch.** `headless` and `lightweight` are saved in `desktop.json`. The desktop tray, Settings ("How Xod
  runs") and `xod mode headless|light on|off` all flip the same switch. `XOD_HEADLESS` / `XOD_LIGHTWEIGHT` and
  `--headless` / `--window` override it for one launch.
- **The terminal is the headless way in**, like Claude Code:
  - `xod task` is acknowledged at once, through the orchestrator.
  - `xod plan <task>` shows the live plan.
  - `xod steer <task> pause|resume|cancel|redirect "…"|steer "…"` steers it.
  - `xod start --light` starts a lightweight server.
- **Lightweight server.** It skips 9 optional background loops (each named in `/api/mode`) and closes the idle
  browser after 3 minutes. Measured idle cost on a fresh data folder: **137 MB / 2.9% CPU full vs 54 MB / 0.2%
  lightweight**.
- **Tests:** headless suite 14/14 ×3, browser idle 13/13, Electron mode tests 5/5, live L1–L3 on a lightweight
  server 20/20.

## Reproduce

```
.venv/bin/python tests/task_inbox/run_orchestrator_test.py --repeat 3        # and run_workers_test, run_phase3_test,
                                                                              # run_delivery_test, run_memory_provenance_test,
                                                                              # run_concurrency_test
XOD_TEST_HOOKS=1 XOD_DATA_DIR=<scratch> .venv/bin/python -m uvicorn app.main:app --port 8212
.venv/bin/python tests/task_inbox/live_orchestrator_acceptance.py --base http://127.0.0.1:8212 --data <scratch> \
    --work <dir> --pidfile <pid> --restart-cmd <start.sh> --step-sleep 75
.venv/bin/python tests/task_inbox/live_workers_acceptance.py --base http://127.0.0.1:8212
.venv/bin/python tests/task_inbox/live_spec_acceptance.py  --base http://127.0.0.1:8212 --data <scratch> \
    --work <dir> --pidfile <pid> --restart-cmd <start.sh>
```

Existing suites that are timing- or model-dependent: `tests/smoke/run_conversation_test.py` (a browser voice UI
test) and `tests/smoke/run_memory_test.py` (uses the local LM Studio model). Each failed once under machine load
during this session, and both pass on repeat on this branch and on untouched origin/main.
