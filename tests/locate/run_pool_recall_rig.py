"""
Acceptance rig for Fix Round 5 Lane 1 (candidate generation recall).

Re-run 4 graded the 0.9.18 build: the correct target appeared in the
delivered candidates on only 2 of 13 hunts, while the production tried
lists proved the routes themselves returned volume (filename route: 257
hits on the lease hunt, 176 on the budget hunt, 63 on the reunion hunt).
The targets were lost between the routes and the scored pool: the
filename route returned the first 2000 hits in walk order, the content
route scanned in walk order until its file cap or deadline cut, and the
Spotlight route truncated in index order, so on common-term hunts the
caps filled on home-wide noise before the walk ever reached the target.

This rig reproduces that pressure deterministically:

  - the 25-file planted forest, built exactly per the re-run 3 driver
    recipe (same names, contents, mtimes), in the SECOND root, so every
    route walks the decoy volume first;
  - a decoy corpus of thousands of generated files in the FIRST root:
    6100 small text files under a nest of folders whose names carry the
    common hunt terms (lease, apartment, spring, japan, trip, travel,
    invoice, bill, payment, tax, return, statement, budget, work,
    expenses, family, reunion, gathering, contract, agreement, signed,
    xod, source, project). Because the filename route matches on the
    full path, folder names alone make every decoy a filename hit on
    every common-term hunt, and because the decoys are text files with
    common-term bodies, they also spend the content route's whole file
    budget. 700 more decoys collide on single hunt terms in their own
    names (Lease Record N.pdf, Invoice Record N.pdf, and friends);
  - an R1/R2 tree in the second root: a folder named xod holding
    docs/CHANGELOG.md, decoy xod folders (xod-corrupt-0914-hold style
    and xod-eval-sandbox style), stray tools.py files in backup folders
    whose contents mention changelog, and library CHANGELOG.md copies.

Runs the real locate() for all 13 hunt request texts verbatim and
asserts every real target reaches the delivered candidate set. On
pristine a4bd54e the volume hunts lose their targets (the filename cap
and the content file cap both cut inside the decoy root, before the
forest root is walked); on the fixed branch all targets are delivered
and N1 (the nonexistent zebrafish deck) still returns no target.

No model, no Keychain, no network:
  PYTHON_KEYRING_BACKEND=tests.support.filekeyring.FileKeyring \
    ~/Documents/xod/.venv/bin/python tests/locate/run_pool_recall_rig.py
Set XOD_RIG_ROOT to run the same rig against another checkout (used to
record the pristine a4bd54e baseline without copying the file).
"""
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("XOD_RIG_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-pool-rig-data-")

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

# Decoy bodies rotate through these common-term word sets. Deliberately
# absent: sourdough (L1 stays a rare-term hunt, as in production), the
# L3 content words, zebrafish and the other N1 terms (N1 must stay an
# honest negative), and any numeral or year a hunt asks about.
DECOY_BODY_SETS = [
    "lease apartment rent spring",
    "japan trip travel booking",
    "invoice bill payment receipt",
    "tax return statement wage",
    "budget work expenses finance",
    "family reunion gathering photo",
    "contract agreement signed consulting",
    "spring statement lease tax",
]

# Single-term name collisions: (name stem prefix, extension, count).
NAME_DECOYS = [
    ("Lease Record", ".pdf", 70),
    ("Invoice Record", ".pdf", 70),
    ("Budget Record", ".xlsx", 70),
    ("Japan Record", ".pdf", 70),
    ("Family Record", ".png", 70),
    ("Reunion Record", ".png", 70),
    ("Contract Record", ".pdf", 70),
    ("Tax Record", ".pdf", 70),
    ("Statement Record", ".pdf", 70),
    ("Work Record", ".xlsx", 70),
]

TEXT_DECOY_COUNT = 6100  # past the content route file cap of 6000


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
    # Second root: the forest, exactly per the recipe, plus the project.
    main_root = tmp / "main"
    forest = main_root / "forest"
    for rel, content, mtime in FOREST_FILES:
        plant(forest, rel, content, mtime)
    project = main_root / "project" / "xod"
    plant(project, "README.md", "xod: the local agent, source code")
    plant(project, "docs/CHANGELOG.md",
          "Xod project changelog: locate fixes, pool recall, release notes")
    # R1/R2 decoys: wrong xod folders, stray tools.py files in backup
    # folders whose contents mention the changelog, library changelogs.
    (main_root / "xod-corrupt-0914-hold").mkdir(parents=True, exist_ok=True)
    plant(main_root, "xod-corrupt-0914-hold/hold-note.txt", "held copy, do not use")
    (main_root / "xod-eval-sandbox").mkdir(parents=True, exist_ok=True)
    plant(main_root, "xod-eval-sandbox/sandbox-note.txt", "sandbox copy, experiments only")
    plant(main_root, "backups/promotion-a/tools.py",
          "# tools: mentions changelog, xod, project in passing\nVALUE = 1\n")
    plant(main_root, "backups/promotion-b/app__agent__tools.py",
          "# tools: mentions changelog, xod, project in passing\nVALUE = 2\n")
    plant(main_root, "library/badger/CHANGELOG.md", "badger library changelog release notes")
    plant(main_root, "go/pkg/mod/fsnotify/CHANGELOG.md",
          "fsnotify library changelog release notes")

    # First root: the decoy volume every route walks before the forest.
    volume = tmp / "volume"
    nest = volume / "records"
    for seg in ("lease-apartment-spring", "japan-trip-travel",
                "invoice-bill-payment", "tax-return-statement",
                "budget-work-expenses", "family-reunion-gathering",
                "contract-agreement-signed", "xod-source-project"):
        nest = nest / seg
    nest.mkdir(parents=True, exist_ok=True)
    for i in range(TEXT_DECOY_COUNT):
        body = DECOY_BODY_SETS[i % len(DECOY_BODY_SETS)]
        (nest / f"record-{i:05d}.txt").write_text(f"decoy record {i}: {body}")
    by_name = volume / "by-name"
    by_name.mkdir(parents=True, exist_ok=True)
    for stem, ext, count in NAME_DECOYS:
        for i in range(count):
            body = b"placeholder bytes" if ext in (".png",) else f"decoy {stem} {i}"
            plant(by_name, f"{stem} {i:03d}{ext}", body)
    return volume, main_root


class EmptyIndex:
    def search(self, clues, roots):
        return []


def main():
    from app.services import locate as loc

    tmp = Path(tempfile.mkdtemp(prefix="xod-pool-rig-"))
    t0 = time.time()
    volume, main_root = build(tmp)
    print(f"built decoy volume plus forest in {time.time() - t0:.1f}s under {tmp}",
          flush=True)
    roots = [str(volume), str(main_root)]
    failures = []
    present = 0
    for label, request, target_rel in HUNTS:
        t_hunt = time.time()
        res = loc.locate(request, roots=roots, index_backend=EmptyIndex())
        cands = res.get("candidates", [])
        paths = [c["path"] for c in cands]
        tried = {t["route"]: t["hits"] for t in res.get("tried", [])}
        line = (f"{label}: status={res.get('status')} tried={tried} "
                f"elapsed={time.time() - t_hunt:.1f}s")
        if target_rel is not None:
            target = str(main_root / target_rel)
            rank = paths.index(target) if target in paths else None
            if rank is None:
                failures.append(f"{label}: target missing from candidates: {target_rel}")
                line += " target=MISSING"
            else:
                present += 1
                line += f" target_rank={rank} target_score={cands[rank]['score']}"
        else:
            if paths:
                failures.append(f"{label}: expected no candidates, got {paths}")
                line += " UNEXPECTED-CANDIDATES"
            else:
                line += " no-target-ok"
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
