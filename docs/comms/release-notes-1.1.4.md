# Release notes: v1.1.4

Merged from docs/comms/release-notes-fixed-release-DRAFT.md Block A and
Block B. Both blocks are true of this single release: v1.1.4 is at once the
fixed release stranded 1.1.3 users rescue themselves with, and the release
that installs the self-repair bootstrap and the footprint doctor. This text
is the GitHub release body for v1.1.4.

---

Xod 1.1.3 does not open. A file the app needs at startup was left out of the
package, so it closes before anything loads, including its own updater.

Fixed in this release.

If you have 1.1.3:

1. Download this version and install it over the old copy.
2. Open it. Done.

Your data is not inside the app. It stays where it is, and this install does
not touch it.

After this one manual install, updates are automatic again.

New in this release: Xod now checks itself before it opens.

If a future build is ever incomplete, Xod downloads a fresh copy and repairs
itself at launch instead of failing to start. If it cannot repair, it says so
and leaves the copy you have in place.

The new doctor (scripts/xod_doctor.py) shows everything Xod has put on your
machine and removes only what it can prove is a duplicate or already kept
somewhere safe.
