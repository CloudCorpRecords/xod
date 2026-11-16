# Signing: one stable identity for Xod (AGE-57)

## What was wrong

Through v1.1.10, every Mac release was ad-hoc signed. The shipped build path
is `electron/scripts/build-mac.sh` (the release workflow runs `npm run dist`,
which is that script), and it forced electron-builder identity `-`, the
ad-hoc signature. An ad-hoc signature has no certificate, so the app's
designated requirement, the identity statement macOS derives from the
signature, collapses to the build's cdhash alone:

    designated => cdhash H"1a45fb2dbfcb1c4294f3affb7a6a92a9c704aff5"   (v1.1.8, from the Mac)

macOS binds privacy grants (Accessibility, Screen Recording, and the rest of
TCC) and keychain "Always Allow" entries to the designated requirement. The
cdhash is a hash of that exact build, so it changes on every rebuild, and a
grant made on one build can never match the next. The visible symptoms, all
seen live on the home Mac:

- System Settings shows Xod switched on under Accessibility and Screen
  Recording, while the running app reads both as denied. The Settings list
  is keyed by bundle id for display; the authorization check is keyed by
  the requirement. On v1.1.10 three fresh process generations read desktop
  status ready false after the entries were fixed and the toggles flipped.
- Every update brings back the keychain prompt ("python3.12 wants to use
  your confidential information stored in 'Xod API keys'"), because the
  bundled Python's Always Allow entry was bound to the previous build.

The seal half of this family (the running app rewriting files inside its
own bundle, AGE-78) is fixed separately. Both halves had to land before any
grant could persist.

## The fix

Sign every build with one certificate that never changes. With a
certificate, the designated requirement becomes the bundle identifier plus
the certificate, for example:

    designated => identifier "dev.xod.desktop" and certificate leaf = H"148a..."

That text is identical for every build signed with the same certificate, so
a grant or an Always Allow made once keeps matching forever. A free
self-signed certificate is enough for this: TCC and the keychain check the
requirement, not whether Apple issued the anchor. Only Gatekeeper and
notarization care about Apple issuance (see the Developer ID route below).

What changed in the pipeline:

- `electron/scripts/build-mac.sh` resolves an identity instead of forcing
  ad-hoc: `XOD_MAC_SIGN_IDENTITY` when set, else the certificate
  electron-builder imports from `CSC_LINK` / `CSC_KEY_PASSWORD`, else
  ad-hoc with a loud warning. After signing, it runs
  `scripts/check_designated_requirement.py` on the built app and on the app
  inside the finished DMG, and fails the build when a certificate was
  expected but the product is ad-hoc or carries a cdhash term.
- `scripts/check_designated_requirement.py` is the gate. It can also be run
  by hand against any installed copy:
  `python3 scripts/check_designated_requirement.py /Applications/Xod.app`
- `.github/workflows/release.yml` passes `CSC_LINK` and `CSC_KEY_PASSWORD`
  from GitHub secrets to the Mac build step. Until the secrets are set,
  they expand empty and the build stays ad-hoc (with the warning), so
  setting the secrets is the whole CI change.
- `partner/install.sh` prefers the same identity for the Xod Partner app,
  with the same ad-hoc fallback, so the Partner's Accessibility grant gets
  the same stability when it is next rebuilt.
- `tests/release/run_signing_requirement_test.py` pins all of the above.

## One-time setup (about ten minutes, on a Mac, free)

1. Create the identity:

       bash electron/scripts/create-signing-identity.sh

   It creates a self-signed code signing certificate named
   "Xod Release Signing", imports it into the login keychain, marks it
   trusted for code signing, and leaves a password-protected export at
   `~/xod-signing-identity.p12`. It refuses to replace an existing
   identity: a new certificate would orphan every grant bound to the old
   one, which is the bug being fixed. Back the .p12 and its password up in
   the Secure Vault. The private key never goes in git
   (`scripts/check_release.py` already refuses .p12 files).
2. Give CI the same identity, so release builds and local builds sign
   identically (the script prints these commands too):

       base64 -i ~/xod-signing-identity.p12 | gh secret set CSC_LINK --repo CloudCorpRecords/xod
       gh secret set CSC_KEY_PASSWORD --repo CloudCorpRecords/xod

3. Cut the next release as usual. Its build log shows signing mode `csc`
   (or `identity` for a local build with `XOD_MAC_SIGN_IDENTITY` set), and
   the designated requirement check passes on the built app.

## The first signed build: one last grant round

The first certificate-signed build has a new designated requirement
(certificate-anchored instead of cdhash), so existing grants do not carry
into it. On that build, one last time:

- flip Accessibility and Screen Recording on for Xod in System Settings,
- click Always Allow once on the keychain prompt,
- confirm `/api/desktop/status` reads ready true.

From the next build on, none of that repeats: install build N+1 over N and
the grants and the keychain trust are still in effect with no prompt. That
two-build sequence is the AGE-57 acceptance test. If a granted build still
loses its grants on the following build, some macOS versions only honor
requirement matching for system-trusted anchors; the setup script prints
the one-time system-domain trust command for that case.

## The Developer ID route (paid, optional)

An Apple Developer Program membership (USD 99 per year) issues a Developer
ID certificate. Signing with it, through exactly the same `CSC_LINK`
wiring, gives a requirement anchored to the Apple team id, and adds the two
things a self-signed certificate cannot:

- notarization (`npm run dist:signed` already carries the flags), so
  Gatekeeper accepts fresh downloads with no warning and no right-click
  dance for people installing Xod on their own Macs;
- no local trust step is ever needed, on any machine.

For the owner's own Mac, the self-signed route above fully solves grant and
keychain persistence. Developer ID is a distribution upgrade, and the
spend decision sits with the owner. Either way the pipeline does not change again: only
the certificate inside the secrets does, and switching certificates later
costs exactly one more grant round on the first build signed with the new
one.

## Custody rules

- One identity, kept forever. Losing the .p12 or its password, or minting
  a replacement certificate, resets every grant and keychain entry bound
  to it, once.
- The .p12 and its password live in the Secure Vault and in the two GitHub
  secrets, nowhere else. Never in git, never in a chat, never in a doc.
- If the identity is ever compromised, replace it deliberately (new
  certificate, new secrets, one grant round) and note the change here.
