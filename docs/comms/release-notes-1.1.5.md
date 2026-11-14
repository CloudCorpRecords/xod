# Release notes: v1.1.5

This text is the GitHub release body for v1.1.5.

---

Xod 1.1.4 checks itself before it opens. On real installs that check misfired: from inside the packaged app it could not read the app's own package, so it judged healthy installs broken and tried to repair them, and the repair's check of the fresh copy failed the same way. The installs were never corrupt. The check was reading through the wrong file layer.

Fixed in this release.

1. Download this version and install it over the old copy.
2. Open it. Done.

Your data is not inside the app. It stays where it is, and this install does not touch it.

After this install, updates are automatic again, and the self-check now passes on healthy copies and still catches genuinely broken ones: the release gate now proves the check and the repair from inside the packaged app, not just from the command line.
