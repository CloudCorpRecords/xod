"""
Acceptance rig for Fix Round 4 Lane 2 (locate scoring relevance).

Rebuilds the planted 25-file forest exactly as specified in the re-run 3
driver (same names, contents, mtimes), plants a noise tree beside it that
mimics the whole-home noise observed in re-run 3 (a cel-go style testdata
file that won on generic word overlap, a zxcvbn style English.json, two
llms-full.txt copies, two library CHANGELOG.md files inside a fake Go
module cache, path-named folders, an Android build intermediate folder,
and image decoys), plus a fake project dir holding docs/CHANGELOG.md.

Runs the real locate() over roots covering forest plus noise for all 13
re-run 3 hunt requests and asserts:
  - every hunt true target appears in the delivered candidates, and
  - the field_paths.prompt.txt noise file is NOT in the top two for any
    hunt (it is unrelated to all 13).

No model, no Keychain, no network:
  PYTHON_KEYRING_BACKEND=tests.support.filekeyring.FileKeyring \
    ~/Documents/xod/.venv/bin/python tests/locate/run_scoring_rig.py
Set XOD_RIG_ROOT to run the same rig against another checkout (used to
record the pristine-main baseline without copying the file).
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("XOD_RIG_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-scoring-rig-data-")

# Exact copy of FOREST_FILES from the re-run 3 driver: relpath, content,
# mtime in touch -t format (YYYYMMDDHHMM) or None.
FOREST_FILES = [
    ("house/lease/Apartment Lease Agreement 2025.pdf",
     "Apartment Lease Agreement. Lease agreement between the tenant and Bayview Properties "
     "for the apartment, spring term.", "202504150900"),
    ("house/lease/Apartment Lease Agreement 2024.pdf",
     "Apartment Lease Agreement, prior year term.", "202404100900"),
    ("house/lease/Lease Renewal Addendum.pdf", "Lease Renewal Addendum.", None),
    ("house/repairs/Plumbing Invoice March.pdf", "Plumbing Invoice, March.", None),
    ("house/repairs/HVAC Quote.pdf", "HVAC Quote.", None),
    ("house/photos/Kitchen Before Renovation.png", "PNG placeholder bytes: kitchen before.", None),
    ("taxes/2025/Tax Return Summary 2025.pdf", "Tax Return Summary 2025.", "202503100900"),
    ("taxes/2024/Tax Return Summary 2024.pdf", "Tax Return Summary 2024.", None),
    ("taxes/2025/W2 Wage Statement.pdf", "W2 Wage Statement from employer.", None),
    ("taxes/2024/Charitable Donations.csv", "charity,amount\nShelter,100\n", None),
    ("work/contracts/Consulting Contract Signed.pdf", "Consulting Contract, signed by both parties.", None),
    ("work/contracts/Consulting Contract Draft.docx", "Consulting Contract, draft.", None),
    ("work/invoices/Invoice 1042.pdf", "Invoice 1042.", None),
    ("work/invoices/Invoice 1043.pdf", "Invoice 1043.", None),
    ("work/Project Budget.xlsx", "Project Budget spreadsheet placeholder.", None),
    ("personal/recipes/Grandma Soup Recipe.txt",
     "Grandma Soup Recipe. Add the chicken broth and carrots, simmering gently for an hour.", None),
    ("personal/recipes/Sourdough Starter Notes.md",
     "Sourdough Starter Notes. Feeding schedule: flour and water, twice a day.", None),
    ("personal/travel/Japan Trip Itinerary.pdf", "Japan Trip Itinerary.", None),
    ("personal/travel/Japan Trip Budget.csv", "item,cost\nTrain,200\n", None),
    ("personal/Passwords Backup.txt", "Placeholder only, not real secrets.", None),
    ("archive/2023/Old Lease Agreement 2023.pdf", "Old Lease Agreement 2023.", "202303200900"),
    ("archive/2024/Meeting Notes October.md", "Meeting Notes October.", None),
    ("archive/2024/Meeting Notes November.md", "Meeting Notes November.", None),
    ("photos-archive/Family Reunion 2024.png", "PNG placeholder bytes: family reunion 2024.", None),
    ("photos-archive/Family Reunion 2023.png", "PNG placeholder bytes: family reunion 2023.", None),
]

HUNTS = [
    ("L1", "Find the file with Sourdough in its name. Reply with its full path.",
     "forest/personal/recipes/Sourdough Starter Notes.md"),
    ("L2", "Find the PDF from last spring about my apartment lease. Reply with its full path.",
     "forest/house/lease/Apartment Lease Agreement 2025.pdf"),
    ("L3", "Find the file that mentions chicken broth and carrots simmering. Reply with its full path.",
     "forest/personal/recipes/Grandma Soup Recipe.txt"),
    ("L4", "Find the file for the tax thing, the wage statement from my employer, somewhere on this computer. Reply with its full path.",
     "forest/taxes/2025/W2 Wage Statement.pdf"),
    ("L5", "Find the folder that has my Japan trip stuff. Reply with its full path.",
     "forest/personal/travel"),
    ("L6", "Find my signed consulting contract, not the draft. Reply with its full path.",
     "forest/work/contracts/Consulting Contract Signed.pdf"),
    ("L7", "Find invoice 1043. Reply with its full path.",
     "forest/work/invoices/Invoice 1043.pdf"),
    ("L8", "Find the family reunion photo from 2024. Reply with its full path.",
     "forest/photos-archive/Family Reunion 2024.png"),
    ("L9", "Find the tax return summary for 2025, not 2024. Reply with its full path.",
     "forest/taxes/2025/Tax Return Summary 2025.pdf"),
    ("L10", "Find the budget spreadsheet for work. Reply with its full path.",
     "forest/work/Project Budget.xlsx"),
    ("R1", "Find the folder where the Xod source code lives. Reply with its full path.",
     "project/xod"),
    ("R2", "Find the changelog file for the Xod project. Reply with its full path.",
     "project/xod/docs/CHANGELOG.md"),
    ("N1", "Find the quarterly board deck about zebrafish. Reply with its full path.", None),
]

GENERIC_WORDS = "full path reply statement employer somewhere computer name its"


def plant(base, rel, body, mtime=None):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, bytes):
        p.write_bytes(body)
    else:
        p.write_text(body)
    if mtime:
        ts = datetime.strptime(mtime, "%Y%m%d%H%M").timestamp()
        os.utime(p, (ts, ts))
    return p


def build(tmp):
    forest = tmp / "forest"
    for rel, content, mtime in FOREST_FILES:
        plant(forest, rel, content, mtime)
    noise = tmp / "noise"
    field_paths = plant(noise, "go/pkg/mod/cel-go/testdata/field_paths.prompt.txt",
                        "testdata prompt: give the " + GENERIC_WORDS + " field paths")
    plant(noise, "go/pkg/mod/zxcvbn/English.json",
          '{"words": "' + GENERIC_WORDS + ' common english words"}')
    plant(noise, "go/pkg/mod/badger/CHANGELOG.md", "badger library changelog release notes")
    plant(noise, "go/pkg/mod/fsnotify/CHANGELOG.md", "fsnotify library changelog release notes")
    plant(noise, "webdocs-a/llms-full.txt", "llms full text documentation: " + GENERIC_WORDS)
    plant(noise, "webdocs-b/llms-full.txt", "llms full text documentation: " + GENERIC_WORDS)
    (noise / "folders/abspath").mkdir(parents=True, exist_ok=True)
    (noise / "folders/classpath-snapshot").mkdir(parents=True, exist_ok=True)
    (noise / "android/app/build/intermediates/source_path_outputs").mkdir(parents=True, exist_ok=True)
    plant(noise, "media/rewrite-demo.png", b"\x89PNG demo rewrite")
    plant(noise, "media/bw-packbits.tiff", b"II*\x00 packbits tiff")
    project = tmp / "project" / "xod"
    plant(project, "README.md", "xod: the local agent, source code")
    plant(project, "docs/CHANGELOG.md", "Xod project changelog: locate scoring and fixes")
    return field_paths


class EmptyIndex:
    def search(self, clues, roots):
        return []


def main():
    from app.services import locate as loc

    tmp = Path(tempfile.mkdtemp(prefix="xod-scoring-rig-"))
    field_paths = build(tmp)
    roots = [str(tmp)]
    failures = []
    present = 0
    for label, request, target_rel in HUNTS:
        res = loc.locate(request, roots=roots, index_backend=EmptyIndex())
        cands = res.get("candidates", [])
        paths = [c["path"] for c in cands]
        top2 = paths[:2]
        line = f"{label}: status={res.get('status')} top2={[os.path.basename(p) for p in top2]}"
        if target_rel is not None:
            target = str(tmp / target_rel)
            rank = paths.index(target) if target in paths else None
            if rank is None:
                failures.append(f"{label}: target missing from candidates")
                line += " target=MISSING"
            else:
                present += 1
                line += f" target_rank={rank} target_score={cands[rank]['score']}"
        if str(field_paths) in top2:
            failures.append(f"{label}: field_paths.prompt.txt noise in top two")
            line += " NOISE-IN-TOP2"
        print(line, flush=True)
    total_targets = sum(1 for _, _, t in HUNTS if t is not None)
    print(f"\nTARGETS PRESENT {present}/{total_targets}")
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  " + f)
        print("SUMMARY FAIL")
        return 1
    print("SUMMARY PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
