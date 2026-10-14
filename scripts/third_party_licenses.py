#!/usr/bin/env python3
"""
Writes THIRD_PARTY_LICENSES.txt: every package shipped inside Xod.app, its license, and the
license text it carries. Fails if anything is under a license we can't ship in an MIT app
(GPL/AGPL family) so it's caught before a release.

  python3 scripts/third_party_licenses.py electron/python-runtime/lib/python3.12/site-packages OUT.txt
"""
import re
import sys
from email.parser import Parser
from pathlib import Path

COPYLEFT = re.compile(r"\b(A?GPL|GNU (Affero )?General Public)\b", re.I)
WEAK_OK = re.compile(r"\b(LGPL|MPL|Mozilla)\b", re.I)   # fine to ship unmodified, with notice

# Code that isn't a Python package but ships with Xod (UI libraries, fonts, the desktop shell).
NON_PYTHON = [
    ("Python (python-build-standalone)", "PSF-2.0", "https://docs.python.org/3/license.html"),
    ("Electron", "MIT", "https://github.com/electron/electron/blob/main/LICENSE"),
    ("Chromium (in Electron)", "BSD-3-Clause and others", "https://chromium.googlesource.com/chromium/src/+/main/LICENSE"),
    ("Tailwind CSS (play CDN build)", "MIT", "https://github.com/tailwindlabs/tailwindcss/blob/main/LICENSE"),
    ("Lucide icons", "ISC", "https://github.com/lucide-icons/lucide/blob/main/LICENSE"),
    ("marked", "MIT", "https://github.com/markedjs/marked/blob/master/LICENSE.md"),
    ("DOMPurify", "Apache-2.0 OR MPL-2.0", "https://github.com/cure53/DOMPurify/blob/main/LICENSE"),
    ("three.js", "MIT", "https://github.com/mrdoob/three.js/blob/dev/LICENSE"),
    ("Fraunces font", "SIL Open Font License 1.1", "https://github.com/undercasetype/Fraunces/blob/master/OFL.txt"),
    ("Figtree font", "SIL Open Font License 1.1", "https://github.com/erikdkennedy/figtree/blob/master/OFL.txt"),
    ("mflux (vendored, patched; app/vendor/mflux)", "MIT", "https://github.com/filipstrand/mflux/blob/main/LICENSE"),
    ("FFmpeg (via imageio-ffmpeg)", "LGPL-2.1+ build", "https://ffmpeg.org/legal.html"),
]


def license_of(meta) -> str:
    expr = meta.get("License-Expression")
    if expr:
        return expr.strip()
    lic = (meta.get("License") or "").strip()
    if lic and len(lic) < 80 and "\n" not in lic:
        return lic
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return "; ".join(classifiers)
    return (lic.splitlines()[0][:80] if lic else "see license text")


def main(site: Path, out: Path) -> int:
    rows, texts, bad = [], [], []
    for dist in sorted(site.glob("*.dist-info"), key=lambda p: p.name.lower()):
        meta = Parser().parsestr((dist / "METADATA").read_text(encoding="utf-8", errors="replace"))
        name, version = meta["Name"], meta["Version"]
        lic = license_of(meta)
        files = [p for p in dist.rglob("*") if p.is_file() and re.match(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", p.name, re.I)]
        rows.append((name, version, lic))
        if COPYLEFT.search(lic) and not WEAK_OK.search(lic):
            bad.append(f"{name} {version}: {lic}")
        for f in files:
            texts.append(f"\n{'=' * 78}\n{name} {version} — {f.relative_to(dist)}\n{'=' * 78}\n{f.read_text(encoding='utf-8', errors='replace').strip()}\n")

    w = max(len(r[0]) for r in rows) + 2
    lines = ["Xod ships with the following third-party software. Thank you to everyone who made it.", "",
             "Other components", "-" * 16]
    lines += [f"{n:<46} {l}\n{'':<46} {u}" for n, l, u in NON_PYTHON]
    lines += ["", f"Python packages ({len(rows)})", "-" * 24]
    lines += [f"{n:<{w}} {v:<14} {l}" for n, v, l in rows]
    lines += ["", "", "License texts", "=" * 13]
    out.write_text("\n".join(lines) + "".join(texts), encoding="utf-8")
    print(f"Wrote {out} ({len(rows)} packages)")
    if bad:
        print("Copyleft licenses that can't ship in Xod.app:\n  " + "\n  ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
