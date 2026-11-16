#!/usr/bin/env python3
"""
Refuses to call a Mac build "signed" when macOS cannot recognize the next
build as the same app (AGE-57).

  python3 scripts/check_designated_requirement.py /path/to/Xod.app
  python3 scripts/check_designated_requirement.py --expect-any /path/to/Xod.app

Why this exists: macOS privacy grants (Accessibility, Screen Recording, and
the rest of TCC) and keychain "Always Allow" entries bind to an app's
designated requirement, the identity statement macOS derives from its code
signature. An ad-hoc signature has no certificate, so the designated
requirement collapses to the build's cdhash, a hash of that exact build.
Every rebuild mints a new cdhash, so every grant and every keychain trust
entry silently stops matching: the System Settings row still shows the app
switched on, and the running app still reads denied. Xod shipped ad-hoc
builds through v1.1.10 (electron/scripts/build-mac.sh forced electron-builder
identity "-"), which is why grants never survived an update.

A build passes when its designated requirement is anchored to a certificate
(a self-signed one is enough) and the app's bundle identifier, with no
cdhash term anywhere in it: that requirement is identical for every build
signed with the same certificate, so grants made once keep matching.

--expect-any downgrades a failure to a printed warning (used by the build
script for deliberate ad-hoc local builds, where there is no certificate to
anchor to). Without it, any problem exits 1. Exit 2 means the check itself
could not run (not macOS, or codesign missing): never read that as a pass.

Uses only the standard library and the macOS codesign tool.
"""
import re
import subprocess
import sys
from pathlib import Path

CDHASH_TERM = re.compile(r"cdhash\s+H\"[0-9a-fA-F]+\"")
CERT_ANCHORS = (
    "certificate leaf",
    "certificate 1[",
    "certificate root",
    "anchor apple",
    "anchor = H\"",
    "subject.OU",
    "subject.CN",
)


def parse_requirement(codesign_r_output):
    """The designated requirement text from `codesign -d -r-` output, or None.

    The output is an "Executable=" line followed by "designated => <req>";
    the requirement itself may wrap, so everything after the marker is
    taken and its whitespace collapsed.
    """
    marker = "designated =>"
    idx = codesign_r_output.find(marker)
    if idx < 0:
        return None
    text = " ".join(codesign_r_output[idx + len(marker):].split())
    return text or None


def evaluate(requirement, details_text=""):
    """(ok, problems) for one app's designated requirement and codesign details.

    Pure function over captured codesign output, so the gate suite can feed
    it fixtures on any platform.
    """
    problems = []
    if not requirement:
        problems.append("no designated requirement found in codesign output")
        return False, problems
    if CDHASH_TERM.search(requirement):
        problems.append(
            "designated requirement pins a cdhash term, so it matches this "
            "build only and every grant dies on the next build: " + requirement)
    if not any(anchor in requirement for anchor in CERT_ANCHORS):
        problems.append(
            "designated requirement has no certificate anchor, so it is not "
            "stable across rebuilds: " + requirement)
    if "Signature=adhoc" in details_text:
        problems.append("codesign reports Signature=adhoc (no certificate)")
    return not problems, problems


def _codesign_text(app, args):
    proc = subprocess.run(
        ["codesign"] + args + [str(app)],
        capture_output=True, text=True)
    # codesign writes its reports to stderr.
    return proc.stdout + proc.stderr


def inspect(app):
    """(ok, problems, requirement) for a built .app, using the real codesign."""
    requirement = parse_requirement(_codesign_text(app, ["-d", "-r-", "--verbose=2"]))
    details = _codesign_text(app, ["-dvvv"])
    ok, problems = evaluate(requirement, details)
    return ok, problems, requirement


def main(argv):
    expect_any = "--expect-any" in argv
    apps = [a for a in argv[1:] if not a.startswith("--")]
    if len(apps) != 1:
        print(__doc__)
        return 2
    app = Path(apps[0])
    if sys.platform != "darwin":
        print(f"check_designated_requirement: needs macOS codesign, "
              f"this platform is {sys.platform}; the identity of {app} is UNVERIFIED")
        return 2
    ok, problems, requirement = inspect(app)
    print(f"{app}\n  designated => {requirement}")
    if ok:
        print("  OK: certificate-anchored designated requirement; grants and "
              "keychain trust can survive the next build (AGE-57)")
        return 0
    for problem in problems:
        print(f"  PROBLEM: {problem}")
    if expect_any:
        print("  WARNING ONLY (--expect-any): this build's grants and keychain "
              "trust will not survive an update (AGE-57)")
        return 0
    print("  FAIL: this build would orphan every TCC grant and keychain "
          "Always Allow entry on the next update (AGE-57)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
