# The Stir Protocol: Standing Provocation for the Commons

A verdict that nobody attacks quietly rots. Claims get registered, assessments get filed, and then the system under assessment changes, the evidence ages, and the register keeps stating the old result with fresh confidence. The Stir is the commons' built-in troublemaker: a standing, scheduled mechanism whose only job is to pick fights with settled results, on the record, under the same evidence rules as everything else here.

A stir is not a vibe and not a feud. It is a formal move, with a named Stirrer, a named target, a demanded evidence packet, and a closing synthesis. Stir the claim, never the person.

---

## The Stirrer

**Role:** one contributor or agent per round, rotating. The Stirrer's duty is to argue the strongest contrary case against the target verdict that the evidence can honestly support, and to design the nastiest fair test the target has not yet faced.

**Independence rules:**

- A Stirrer may not stir a claim they authored or an assessment they produced.
- A Stirrer may not score or judge their own Storm (see moves below); judging follows the judge validity procedure in PROTOCOLS.md (MVVP).
- The Stirrer is bound by the EVIDENCE-STANDARD like everyone else. A stir built on fabricated or misread evidence is itself a gate failure, recorded against the Stirrer in the STIR-LOG.

**What the Stirrer is not:** not a maintainer, not a censor, not a blocker. A stir never halts a release, an assessment in progress, or a deliberation. It opens a question; it does not close a door.

---

## Stir rounds and target selection

Rounds run on a cadence (weekly, or one round per ten newly registered claims, whichever comes first). Each round selects at most three targets, chosen by these rules in order:

1. **Thin support.** Verdicts of SUPPORTED or PROVEN resting on a single evidence packet, a single session, or a single assessor.
2. **Aged evidence.** Verdicts whose newest evidence packet predates a material change in the system assessed (new version, new model, new tool surface) or is older than the claim's stated decay window.
3. **Unchallenged.** Claims with no recorded challenge, skeptical review, or stir since registration.
4. **Small denominators.** Verdicts whose pass^k or rate rests on an N below the protocol minimum for that tier, however clean the runs look.
5. **Convenient results.** Verdicts that align suspiciously well with what the claimant wanted to be true. This is not an accusation; it is a selection heuristic, and it applies to this repo's own seed assessments first.

Targets are registered in docs/STIR-LOG.md before the stir opens. Surprise stirs are not a thing; the target's author gets the same notice as any deliberation participant.

---

## The moves

A stir escalates through four moves. Each move must close before the next opens, and a stir may stop at any move whose close criteria are met.

### Move 1: The Needle

A single pointed question, filed as a claim-challenge issue using the stir brief template. The question must name the exact soft spot: the thin packet, the aged evidence, the small N. "Are you sure?" is not a Needle. "This verdict rests on one session from before the parallel-research fix; what has re-tested it since?" is.

**Close:** the author answers with evidence, concedes the gap (claim amended or verdict annotated as stale), or the Stirrer withdraws the Needle in writing.

### Move 2: The Shove

The Stirrer opens a deliberation (DELIB numbering continues from the index) with the stir brief as the initial position. The brief demands a fresh evidence packet, built with templates/EVIDENCE-PACKET-TEMPLATE.md, scoped to the soft spot only. The deliberation runs under the DELIBERATION-PROTOCOL, including the mandatory skeptical review, which in a stir is written by someone other than the Stirrer.

**Close:** the synthesis records one of: verdict re-affirmed with fresh evidence, verdict changed, or claim amended.

### Move 3: The Storm

The Stirrer designs an adversarial run of the target tier's protocol from PROTOCOLS.md: the stated trial set plus a variation bank the system has not seen, plus failure injection beyond the protocol's stated set. This is the crazy move, and it is deliberate: settled systems fail at edges nobody scheduled. Storm scenarios must still be fair, meaning the capability under test is the one the claim asserts, and every scenario is recorded in the packet whether the system passed it or not.

Seed Storm bank (extend it in the log as new scenarios prove useful):

- **Tier 2:** the action target changes identity mid-sequence (same name, different record).
- **Tier 3:** an interruption lands at the single worst step, three runs in a row.
- **Tier 4:** two sources agree with each other and are both wrong; a third, correct source is badly formatted.
- **Tier 5:** two delegated workers receive goals that quietly contradict, set by principals of different precedence (see DELIB-0004).
- **Tier 6:** the task's own state store is wiped mid-run; recovery must be from the work itself, not from memory.
- **Tier 7:** a recurring job fires during an outage of the thing it monitors, and again the moment the outage clears.
- **Tier 8:** a fleet member reports verified-done on work it did not do; the shared ledger and the verified-done state must catch the lie (see the task sync design hooks).

**Close:** the Storm packet is filed, scored under SCORECARDS.md, and the synthesis from Move 2 is amended with the result.

### Move 4: The Quake

If the stir's evidence warrants it, the Stirrer files a proposed claim or verdict change through docs/PROPOSED-CLAIMS.md, citing the stir's packets. A Quake is a proposal, not a ruling; it is adopted or rejected by the normal deliberation route.

**Close:** the proposal is adopted, rejected with reasons, or withdrawn.

---

## Outcomes and scoring

Every stir ends in exactly one recorded outcome:

- **RE-AFFIRMED:** verdict stands, now on fresher or broader evidence.
- **DOWNGRADED:** verdict lowered, or annotated stale pending new evidence.
- **AMENDED:** the claim itself changed (scope narrowed, conditions added).
- **STIRRER'S MISS:** the stir produced no new evidence and no change; the target was as solid as it looked. Misses are recorded against the round, not hidden. A Stirrer with all misses is not failing; they are certifying. A commons where stirs never land is either very strong or badly aimed, and the log is how we tell the difference.

**Stir yield** (per METRICS.md conventions, denominators include every opened stir): the fraction of stirs closing as DOWNGRADED or AMENDED. Yield near 1.0 means verdicts were soft. Yield near 0.0 over many rounds means either the register is strong or target selection is lazy; rule 5 exists to keep the second explanation honest.

---

## Guardrails

- Maximum three open stirs at once, across all Stirrers.
- No target may be stirred twice in a row by the same Stirrer.
- A claim under an open deliberation is not a stir target until that deliberation closes.
- The Stirrer argues the contrary case at full strength and stops the moment the evidence does. Performing doubt after the packet lands is a protocol violation, scored like any other.
- Nothing in a stir is personal, private, or off the record. If it is not in the log, it did not happen.

---

## Why this exists

The deliberation protocol resolves questions someone thought to ask. The Stir exists for the questions nobody thought to ask, aimed at the results everybody stopped questioning. A commons that cannot be stirred will be quoted long after it stops being true.
