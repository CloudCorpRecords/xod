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

The owner's chosen policy recognizes exactly "Always allow for this recipient"
and "Always allow for this site" (case-insensitive, surrounding whitespace
ignored). Global or ambiguous variants, including "for all sites" and "for
every recipient", never qualify as scoped approval. It prefers these exact buttons
for cards covered by Xod's auto-approve switch. Next come group, one-time, and
task-scoped approvals. Unscoped "Allow always", "Always allow everything",
negative buttons, expired cards, and cards encountered while off/paused or
while the away policy is inactive are not auto-pressed. The existing switch
covers all eligible Muse cards, including email sends; it is not a separate
per-recipient allowlist.

Current Xod and Partner advertise `always-allow-v1`. Xod validates the chosen
button against all scanned labels and sends both the policy and expected
button to `/ax/press-policy`. Partner re-walks and refuses a changed choice or
policy before acting. A one-time-only Partner is not silently reinterpreted.
The legacy `/ax/press-once` route still enforces `one-time-v1`, even if a
recipient/site Always allow button appears after a scan. Installing a changed
Partner may require macOS permission re-pairing and is not done automatically.

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
   card progresses, scoped recipient/site Always allow is preferred, and
   negative/unscoped permanent buttons are not chosen.
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

## Handoff repair verification

The Linux regression suite includes fresh interpreter processes simulating an
email press whose response is lost. Its durable intent prevents a second press
after restart, until a complete absence scan. It also checks off, expired, and
unscoped cards, policy mismatches, and changed live button choices. These are
fake AX trees and real local storage/HTTP tests, not native desktop acceptance.

The delegation isolation assertion resolves both paths, accommodating macOS
`/var` and `/private/var` aliases. The nested delegation check in the approval
suite exercises the same corrected assertion. Actual macOS execution here
remains UNKNOWN.
