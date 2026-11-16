# Approval reliability: contract and validation

## Boundaries

- Native Xod tools retain their own approval handling.
- Crew's Muse/Claude protocol requests use the user's auto-approve switch but
  still hold requests flagged by the existing safety check or protected writes.
- Muse desktop cards use Xod's mode/pause/presence decision and an OS-specific
  adapter, optionally the authenticated Partner adapter on macOS.
- Partner's optional brain supervises recovery, not approval decisions. It
  must not become an alternative source of permission to click.

This repair keeps these distinct responsibilities rather than replacing them
with another agent or deleting Partner. No model call is needed to choose a
known approval button.

Lightweight mode retains the Muse watcher, Partner supervision, and Computer
Health. It skips unrelated optional services, not the approval relay. Linux
still cannot watch native desktop cards; dry-run preview never presses buttons.

## Button and retry contract

Choose a one-time or task-scoped button. Never silently turn a one-time
approval into permanent recipient/site permission. Users may configure
permanent permissions directly in Muse. Older Partners selecting a permanent
button are refused with a notice; installing a changed Partner may require
macOS permission re-pairing and is not done automatically.

Record intent before issuing a click. Successful and uncertain click attempts
are not repeated while the same card remains visible, including after an Xod
restart. A complete subsequent scan showing absence ends that showing; an
identical later card is eligible again. A missing scan response is not evidence
of absence. Explicit authentication refusals can be retried after access is
restored, because no click was executed.

Tradeoff: a desktop API can acknowledge a press without proving Muse completed
the requested action. Those cards may need manual inspection rather than an
unsafe repeated send. Two genuinely distinct cards with identical content
without an observable absence cannot yet be distinguished reliably.

## Replit evidence and limitations

The web preview loads, and isolated tests exercise fake Mac/Windows trees,
button selection, absence/reappearance, mode and pause behavior, simultaneous
scan requests, helper delegation, expiry, unknown responses, and persisted
intent. No real Muse cards, messages, files, payments, or desktop permissions
are changed by these tests.

Live Terra testing remains blocked by unavailable managed OpenAI integration
discovery. No alternative model has been substituted. Passing fixtures do not
prove real desktop operation, macOS grant continuity, or release readiness.

## Required real-Mac check

1. Use disposable test content and a sandbox recipient/site. Record the Xod and
   Partner build identities and grant status; do not reset permissions silently.
2. Verify off, always, away, pause, and resume on real Muse cards. Confirm a new
   card progresses and negative/permanent buttons are not chosen.
3. Revoke a grant and disconnect Partner. Confirm the UI explains the block;
   restore access and verify an unattempted pending card proceeds.
4. Interrupt a press response and restart Xod. Confirm no duplicate send;
   dismiss the old card and verify an identical new request can proceed.
5. Verify a buttonless card can be read after fronting Muse and that the prior
   application is restored. Check locked-screen behavior separately.
6. With the owner's approved stable signing setup, grant once on build N,
   install N+1 and N+2, and prove actual desktop actions still work without
   repeating permission grants. Record failures, not merely visible toggles.
7. Keep release promotion separate from these checks.
