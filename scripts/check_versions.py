#!/usr/bin/env python3
"""
Fails when the version written in one place disagrees with the version written in another.

  python3 scripts/check_versions.py              # every version source must agree, and the
                                                 # changelog must carry a section for it
  python3 scripts/check_versions.py 1.2.3        # every source must also equal 1.2.3

Why this exists: Xod updates itself through the launcher. The desktop shell compares the
version in its own package.json against the newest GitHub release; the backend reports
app/version.py. If the places drift apart, an install can report one version and update
against another, and the fleet ends up running code its shell thinks is something else
(the November 2026 stall: backends hand-carried onto old shells, shells that never
pulled the release that was actually published). scripts/release.py bumps every place
in PLACES; this script is the independent check that the bump landed everywhere.
scripts/check_release.py only compares two of the places; this one compares them all.

Uses only the standard library. Exit 0 when everything agrees, 1 otherwise.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Mirror of PLACES in scripts/release.py: (file, pattern with the version in group 1).
PLACES = [
    ("app/version.py", r'VERSION = "([^"]+)"'),
    ("app/mcp_server.py", r'version="([^"]+)"'),
    ("app/connectors/mac.py", r'version="([^"]+)"'),
    ("electron/package.json", r'^  "version": "([^"]+)"'),
    ("npm/package.json", r'^  "version": "([^"]+)"'),
    ("electron/package-lock.json", r'^  "version": "([^"]+)"'),
    ("electron/package-lock.json", r'^    "": \{\r?\n      "name": "xod-desktop",\r?\n      "version": "([^"]+)"'),
]


def read_version(rel: str, pattern: str):
    path = ROOT / rel
    if not path.is_file():
        return None, "file missing"
    m = re.search(pattern, path.read_text(encoding="utf-8"), re.M)
    if not m:
        return None, "version pattern not found"
    return m.group(1), None


def main() -> int:
    expected = sys.argv[1] if len(sys.argv) > 1 else None
    if expected and not re.fullmatch(r"\d+\.\d+\.\d+", expected):
        print(f"usage: python3 scripts/check_versions.py [X.Y.Z]  (got {expected!r})")
        return 2
    found = []
    problems = []
    for rel, pattern in PLACES:
        version, err = read_version(rel, pattern)
        found.append((rel, version))
        if err:
            problems.append(f"{rel}: {err}")
    versions = {v for _, v in found if v}
    for rel, version in found:
        print(f"  {rel}: {version or '(unreadable)'}")
    if len(versions) > 1:
        detail = ", ".join(f"{rel}={v}" for rel, v in found if v)
        problems.append(f"version sources disagree: {detail}")
    agreed = next(iter(versions)) if len(versions) == 1 else None
    if expected and agreed and agreed != expected:
        problems.append(f"version sources agree on {agreed}, not the expected {expected}")
    if agreed:
        changelog = ROOT / "docs" / "CHANGELOG.md"
        text = changelog.read_text(encoding="utf-8") if changelog.is_file() else ""
        if not re.search(rf"^## {re.escape(agreed)}\s*$", text, re.M):
            problems.append(f'docs/CHANGELOG.md has no "## {agreed}" section')
    if problems:
        print("✋ Version check failed:")
        for p in problems:
            print(f"  {p}")
        print("Fix with scripts/release.py (it bumps every place), never by editing one file.")
        return 1
    print(f"✓ Every version source agrees: {agreed}, and the changelog carries it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
