#!/usr/bin/env python3
"""
Releases Xod: one command, and both the Mac and the Windows app are built, published and offered to everyone.

  python scripts/release.py 0.5.1              # bump the version everywhere, commit, tag v0.5.1, push
  python scripts/release.py 0.5.1 --dry-run    # show what it would change, change nothing
  python scripts/release.py --notes 0.5.1      # print the release notes (the workflow uses this)

Before running it, write what changed under "## 0.5.1" at the top of docs/CHANGELOG.md. It refuses to go on
without that, with uncommitted changes, off main, or when scripts/check_release.py finds something private.

Pushing the tag starts .github/workflows/release.yml: it builds Xod-<version>-arm64.dmg and
Xod-<version>-x64-setup.exe (plus copies named Xod-Mac.dmg and Xod-Windows-Setup.exe, the links that never
change), checks them, and publishes the release. Installed copies of Xod see it within an hour and update.
Uses only the standard library.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "CloudCorpRecords/xod"
# Every place the version is written, as (file, pattern with the version in group 1).
PLACES = [
    ("app/version.py", r'VERSION = "([^"]+)"'),
    ("app/mcp_server.py", r'version="([^"]+)"'),
    ("app/connectors/mac.py", r'version="([^"]+)"'),
    ("electron/package.json", r'^  "version": "([^"]+)"'),
    ("npm/package.json", r'^  "version": "([^"]+)"'),               # the xod-agent npm package (the terminal command)
    ("electron/package-lock.json", r'^  "version": "([^"]+)"'),                                   # the package
    ("electron/package-lock.json", r'^    "": \{\r?\n      "name": "xod-desktop",\r?\n      "version": "([^"]+)"'),  # its root entry
]


def current() -> str:
    return re.search(r'VERSION = "([^"]+)"', (ROOT / "app/version.py").read_text(encoding="utf-8")).group(1)


def changelog_section(version: str) -> str:
    text = (ROOT / "docs/CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(version)}\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def notes(version: str) -> str:
    base = f"https://github.com/{REPO}/releases"
    body = changelog_section(version) or "See the changelog."
    return f"""## Download

| | |
|---|---|
| **Mac** (Apple silicon, macOS 13+) | [Xod-{version}-arm64.dmg]({base}/download/v{version}/Xod-{version}-arm64.dmg) |
| **Windows** (10 or 11, 64-bit) · preview | [Xod-{version}-x64-setup.exe]({base}/download/v{version}/Xod-{version}-x64-setup.exe) |

Links that always give the newest version: [Xod-Mac.dmg]({base}/latest/download/Xod-Mac.dmg) · [Xod-Windows-Setup.exe]({base}/latest/download/Xod-Windows-Setup.exe)

**Already have Xod?** It updates itself: click **Update** at the bottom of the sidebar.

**Mac, new install:** open the .dmg and drag Xod to Applications. The first time, right-click Xod → Open (it isn't notarized by Apple yet).

**Windows, new install:** run the setup .exe. It installs for your user only (no admin). If SmartScreen says it "protected your PC" (the installer isn't code-signed yet), click **More info → Run anyway**.

## What's new

{body}

Files are checked against `SHA256SUMS.txt` before the updater installs them.
"""


def git(*args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=check).stdout.strip()


def fail(msg: str) -> None:
    print(f"✋ {msg}")
    sys.exit(1)


def bump(version: str, dry: bool) -> list:
    changed = []
    for rel, pattern in PLACES:
        path = ROOT / rel
        text = path.read_bytes().decode("utf-8")      # as is: keep the file's own line endings
        m = re.search(pattern, text, re.M)
        if not m:
            fail(f"couldn't find the version in {rel} (pattern {pattern!r}); update PLACES in this script")
        new = text[:m.start(1)] + version + text[m.end(1):]
        if new != text:
            changed.append(f"{rel}: {m.group(1)} → {version}")
            if not dry:
                path.write_bytes(new.encode("utf-8"))
    return changed


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--notes" in sys.argv:
        print(notes(args[0] if args else current()))
        return
    if not args or not re.fullmatch(r"\d+\.\d+\.\d+", args[0]):
        fail("usage: python scripts/release.py 1.2.3 [--dry-run]")
    version, dry = args[0], "--dry-run" in sys.argv
    old = current()
    problems = []
    if tuple(map(int, version.split("."))) <= tuple(map(int, old.split("."))):
        problems.append(f"{version} isn't newer than the current version, {old}")
    if not changelog_section(version):
        problems.append(f'docs/CHANGELOG.md has no "## {version}" section yet: write what changed first')
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        problems.append("releases are made from main")
    if git("status", "--porcelain"):
        problems.append("there are uncommitted changes: commit or stash them first (the changelog too)")
    git("fetch", "-q", "origin")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        problems.append("main isn't the same as GitHub's: pull or push first")
    if git("tag", "-l", f"v{version}"):
        problems.append(f"the tag v{version} already exists")
    if problems and not dry:
        fail("\n  ".join(["Not releasing:"] + problems))

    for line in bump(version, dry):
        print(f"  {line}")
    if dry:
        print("\nDry run: nothing changed." + ("".join(f"\n  ✋ {p}" for p in problems) if problems else
              f" It would commit 'Release {version}', tag v{version} and push both."))
        return
    if subprocess.run([sys.executable, "scripts/check_release.py"], cwd=ROOT).returncode:
        git("checkout", "--", ".")
        fail("scripts/check_release.py found something: the version bump was undone")
    git("add", *sorted({rel for rel, _ in PLACES}))
    git("commit", "-q", "-m", f"Release {version}")
    git("tag", f"v{version}")
    git("push", "-q", "origin", "main")
    git("push", "-q", "origin", f"v{version}")
    print(f"\n✓ Released {version}: GitHub is building the Mac and Windows apps and will publish them "
          f"(about 20–30 minutes).\n  Follow it: https://github.com/{REPO}/actions/workflows/release.yml")


if __name__ == "__main__":
    main()
