# Incorporation notes: applying this framework beyond the ladder

Added 2026-11-06 on the owner's direction: after the tiers pass, this repo
goes one step further and covers the things the fleet wants next;
start incorporating that material into what is currently building.
This file records how Xod applies the vendored framework beyond the
tier definitions themselves. It is an Xod-side application note, not
part of the vendored framework; the vendored files stay
byte-identical to CloudCorpRecords/agent-orchestration-tiers at
3cb209c.

## Where the rest landed

- Build requirements for the Xod orchestrator: Addendum D of the
  orchestrator scope document (Tree-side planning doc,
  ORCHESTRATOR-SCOPE-FOR-CLAUDE-2026-11-04.md), folding in CLM-014,
  CLM-015, CLM-017, CLM-019, CLM-021 with DELIB-0004, CLM-024,
  DELIB-0006, the G8 stage-labeling rule, FT-05, FT-07, and Stir
  candidate C-1 as build requirements.
- Full mapping of all 87 extracted items, Tree's instantiated
  scorecard for Tiers 6 to 8 scored from 2026-11-06 evidence, and
  the deferred items with reasons: Tree-side records
  INCORPORATION-2026-11-06.md and TREE-SCORECARD-2026-11-06.md,
  kept beside the tiers repo.

## Application rules Xod adopts from the commons

1. **Fleet verdicts are issued per operation class (CLM-020).**
   Xod's tier claim is not one number for the whole product.
   Read-only coordination and irreversible actuation can carry
   different verdicts, and claims are stated per class: for example,
   task delivery and phone posting are separate classes with
   separate evidence. The 1.0.0 claim (reliable through Tier 6)
   should be read, and future claims written, in that shape.
2. **Claim language is stage-labeled (PROTOCOLS.md G8).** Every
   capability statement in release notes and the changelog names
   its validation stage: shadow, canary, or field. Laboratory
   results (suites, probes) are stated as laboratory results;
   present-tense reliability language requires field-stage
   evidence on real devices.
3. **The fault catalog is untested tail risk until run.** Xod has
   not met FT-01 to FT-07 in protocol. Per G8, its assessment must
   say so in those words: a fleet that has never met FT-01 to
   FT-07 in protocol has untested tail risk. The first scheduled
   run targets the phone claim surface (FT-01 partition, FT-02
   clock skew) once a real lane-claim mechanism exists, because
   that is where two controllers could first act at once.
4. **Claims end on events, not only on time (CLM-017).** Where
   this framework's claim language is applied to Xod's own
   resource claims (devices, sessions, tabs), expiry is
   event-based first (owner completion, material state change,
   human takeover) with lease expiry as the backstop.
