"""
Production-path pool-recall rig (Fix Round 6, Lane C; BOARD-001).

The old rig (run_pool_recall_rig.py) grades locate() directly: two
small roots, an empty index, and success = the target appears in
result["candidates"]. Production hunts are not that. The researcher
dispatches the locate_files tool exactly once, with ONE root (the
home folder, hunt_routing.locate_root), the real Spotlight index,
and the delivered answer is the decisive-evidence block rendered
from the result (hunt_routing.answer_for). Re-run 5 measured the
gap: the rig scored 12/12 while delivered candidates held the
targets in 2 of 13 hunts.

This rig rebuilds the measurement on the production path:
- ONE root, shaped like a real home: a large generic noise tree and
  vocabulary decoys are created FIRST, the graded forest (the exact
  25 files of the re-run series) LAST, plus the structures the
  production failures involved (xod backup-bundle folders holding
  tools.py files, a real project folder at Documents/xod).
- The index is an emulated Spotlight: same seam
  (search(clues, roots)), mdfind semantics (every query term must
  appear in the name or the text content), the route's own priority
  sort and cap.
- Each hunt runs locate() once with that root and backend, and the
  delivered answer is hunt_routing.answer_for's block. Grading
  reports ROUTE recall (the target was found by some route),
  POOLED recall (it entered the candidate pool), and DELIVERED
  recall (its path is in the delivered block) separately, and names
  the stage where each undelivered target exited, from
  diagnostics["trace"].

The tools-handler wrapper this bypasses is argument plumbing only
(app/agent/tools.py: root_dir -> roots, then locate in a thread);
every function that decides the answer runs here exactly as
production runs it.

Run: .venv/bin/python tests/locate/run_production_recall_rig.py
Exit code 0 when delivered recall equals route recall for the
target hunts (the delivery seam is closed); 1 while a gap remains,
with the table showing where it lives.
"""
import os, sys, tempfile, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import app.services.locate as loc
from app.services import hunt_routing

# The tree is cached across runs (keyed by a marker file): building
# ~36k files is minutes of pure filesystem time on some machines,
# and the fix/re-grade loop needs to be fast. Delete the cache dir
# to rebuild from scratch.
CACHE = Path(tempfile.gettempdir()) / "xod_prod_rig_cache"
HOME = CACHE / "home"
HOME.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- forest
# The exact 25 graded files of the re-run series (rerun3_driver.py
# FOREST_FILES), at <home>/xod-locate-forest as in production.
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

FOREST = HOME / "xod-locate-forest"

HUNTS = [
    ("L1", "Find the file with Sourdough in its name. Reply with its full path.",
     FOREST / "personal/recipes/Sourdough Starter Notes.md"),
    ("L2", "Find the PDF from last spring about my apartment lease. Reply with its full path.",
     FOREST / "house/lease/Apartment Lease Agreement 2025.pdf"),
    ("L3", "Find the file that mentions chicken broth and carrots simmering. Reply with its full path.",
     FOREST / "personal/recipes/Grandma Soup Recipe.txt"),
    ("L4", "Find the file for the tax thing, the wage statement from my employer, somewhere on this computer. Reply with its full path.",
     FOREST / "taxes/2025/W2 Wage Statement.pdf"),
    ("L5", "Find the folder that has my Japan trip stuff. Reply with its full path.",
     FOREST / "personal/travel"),
    ("L6", "Find my signed consulting contract, not the draft. Reply with its full path.",
     FOREST / "work/contracts/Consulting Contract Signed.pdf"),
    ("L7", "Find invoice 1043. Reply with its full path.",
     FOREST / "work/invoices/Invoice 1043.pdf"),
    ("L8", "Find the family reunion photo from 2024. Reply with its full path.",
     FOREST / "photos-archive/Family Reunion 2024.png"),
    ("L9", "Find the tax return summary for 2025, not 2024. Reply with its full path.",
     FOREST / "taxes/2025/Tax Return Summary 2025.pdf"),
    ("L10", "Find the budget spreadsheet for work. Reply with its full path.",
     FOREST / "work/Project Budget.xlsx"),
    ("R1", "Find the folder where the Xod source code lives. Reply with its full path.",
     HOME / "Documents/xod"),
    ("R2", "Find the changelog file for the Xod project. Reply with its full path.",
     HOME / "Documents/xod/CHANGELOG.md"),
    ("N1", "Find the quarterly board deck about zebrafish. Reply with its full path.", None),
]


def write(rel_base: Path, rel: str, content: str, mtime: str = None):
    p = rel_base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    if mtime:
        ts = datetime.strptime(mtime, "%Y%m%d%H%M").timestamp()
        os.utime(p, (ts, ts))
    return p


def _write_many(jobs):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(24) as ex:
        list(ex.map(lambda j: write(*j), jobs))


def build_noise():
    """Created FIRST, like a real home that existed before the forest:
    a large generic tree, vocabulary decoys, the xod backup bundles
    the production R1/R2 asks were lost between, and the real project
    folder the R hunts target.

    Scale is a production condition, not decoration: the filename and
    content routes share a bounded walk, and a real home holds far more
    entries than any small fixture, so a walk budget spent in whichever
    subtrees come first may never reach a target tree at all. The first
    version of this rig built 6,200 files, the caps never bound, and it
    graded 12/12 — the old rig's blind spot reproduced. The noise trees
    here hold ~36,000 entries and sort before "xod-locate-forest" in
    directory order, the shape a lived-in home presents. (The AGE-25
    walk budgets now sit above this fixture, so the rig grades recall
    under first-subtree order pressure; the true-scale condition, a
    ~560,000-entry home against the walk, is covered by the Mac probe
    in the AGE-25 report. The production Mac's exact directory order
    is not reproduced and does not need to be: what re-run 5 measured
    is the outcome, caps binding before the forest.)"""
    jobs = []
    for tree in ("a-noise", "archive-deep", "Library"):
        for d in range(60):
            for i in range(200):
                jobs.append((HOME, f"{tree}/batch-{d:02d}/document-{d:02d}-{i:03d}.txt",
                             f"Generic document {d}-{i}. Meeting notes, lists and drafts.\n"))
    _write_many(jobs)
    decoys = [
        ("Documents/leases/Lease Agreement 2026.pdf", "Lease Agreement 2026 term."),
        ("Documents/leases/Lease Agreement 2019.pdf", "Lease Agreement 2019 term."),
        ("archive/Lease Agreement 2022.pdf", "Lease Agreement 2022."),
        ("Documents/taxes/Tax Return Summary 2023.pdf", "Tax Return Summary 2023."),
        ("Documents/taxes/Tax Return Summary 2026.pdf", "Tax Return Summary 2026."),
        ("Documents/taxes/W2 Wage Statement 2024.pdf", "W2 Wage Statement 2024."),
        ("Documents/invoices/Invoice 1041.pdf", "Invoice 1041."),
        ("Documents/invoices/Invoice 1044.pdf", "Invoice 1044."),
        ("Documents/invoices/Invoice 1045.pdf", "Invoice 1045."),
        ("Documents/Home Budget.xlsx", "Home Budget spreadsheet placeholder."),
        ("archive/2024/Project Budget 2024.xlsx", "Project Budget 2024 spreadsheet placeholder."),
        ("Documents/contracts/Consulting Contract Unsigned.pdf", "Consulting Contract, unsigned."),
        ("Documents/recipes/Sourdough Bread Recipe.md", "Sourdough Bread Recipe. starter, flour, bake."),
        ("Documents/recipes/Tomato Soup Recipe.txt", "Tomato Soup Recipe. Warm the chicken broth, add tomatoes."),
        ("photos/Family Reunion 2022.png", "PNG placeholder bytes: family reunion 2022."),
        ("photos/Family Reunion 2025.png", "PNG placeholder bytes: family reunion 2025."),
        ("Documents/CHANGELOG.md", "# Changelog\nA different project's changelog.\n"),
    ]
    for rel, content in decoys:
        write(HOME, rel, content)
    for bundle in ("xod-bundle-backup-2026-11-01-sealed0918-carryover",
                   "xod-bundle-backup-2026-10-31-promotion"):
        write(HOME, f"{bundle}/app__agent__tools.py", "# bundled copy\n")
        write(HOME, f"{bundle}/tools.py", "# bundled copy\n")
        write(HOME, f"{bundle}/CHANGELOG.md", "# Xod changelog (backup bundle copy)\n")
    write(HOME, "Documents/xod/CHANGELOG.md", "# Xod Changelog\n" + "Release notes.\n" * 40)
    write(HOME, "Documents/xod/app/agent/tools.py", "# the real tools module\n")
    write(HOME, "Documents/xod/app/services/locate.py", "# the real locate service\n")
    write(HOME, "Documents/xod/README.md", "# Xod\nThe source tree.\n")


def build_forest():
    for rel, content, mtime in FOREST_FILES:
        write(FOREST, rel, content, mtime)


# ---------------------------------------------------------------- index
class EmulatedSpotlight(loc.SpotlightBackend):
    """mdfind semantics over the same tree: a path matches when every
    query term appears in its name, a parent folder's name, or its
    text content; results get the route's own priority sort and cap.
    The index (path -> name+body haystack) is built once and shared,
    the way a real index is: mdfind does not rescan the disk per
    query, and neither does the emulation."""

    _index = None

    @classmethod
    def _build_index(cls, roots):
        index = {}
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in loc.NOISE_DIRS]
                for name in filenames:
                    p = os.path.join(dirpath, name)
                    hay = (name + " " + dirpath).lower()
                    body = ""
                    try:
                        if os.path.getsize(p) <= 256 * 1024:
                            body = Path(p).read_text(encoding="utf-8", errors="replace").lower()
                    except OSError:
                        pass
                    index[p] = hay + "\n" + body
        return index

    def search(self, clues, roots):
        terms = clues.name_tokens + clues.purpose_terms + clues.content_words
        if clues.year:
            terms = terms + [str(clues.year)]
        terms = [t.lower() for t in terms[:6] if len(t) > 1]
        if not terms:
            return []
        if EmulatedSpotlight._index is None:
            EmulatedSpotlight._index = self._build_index(roots)
        found = [p for p, hay in EmulatedSpotlight._index.items()
                 if all(t in hay for t in terms)]
        found.sort(key=lambda p: loc._pool_priority(p, clues))
        return found[:loc.SPOTLIGHT_ROUTE_CAP]


# ---------------------------------------------------------------- grading
def stage_of_exit(target: Path, result, route_pool: set) -> str:
    t = str(target)
    trace = (result.get("diagnostics") or {}).get("trace") or {}
    cand_paths = [c["path"] for c in result.get("candidates") or []]
    if t in cand_paths:
        return "delivered"
    if t in (trace.get("rejected") or []):
        return "gate-rejected (in top 8, refused by eligibility gates)"
    if t in (trace.get("top") or []):
        return "in top 8 but not in final candidates"
    below = {b["path"]: b["score"] for b in trace.get("below_cut") or []}
    if t in below:
        return f"below the MAX_CANDIDATES cut (score {below[t]})"
    if t in route_pool:
        return "pooled but below threshold (or beyond trace window)"
    tried = {x["route"]: x for x in result.get("tried") or []}
    fn_hits = (tried.get("filename") or {}).get("hits", "?")
    ct = tried.get("content") or {}
    return (f"never pooled (filename hits {fn_hits}, content hits {ct.get('hits', '?')}, "
            f"content partial: {ct.get('partial', False)} {ct.get('partial_reason', '')})")


def main():
    marker = CACHE / "built.ok"
    if marker.exists():
        print(f"reusing cached tree at {HOME}", flush=True)
    else:
        build_noise()
        build_forest()
        marker.write_text("ok", encoding="utf-8")
    root = str(HOME)
    backend = EmulatedSpotlight()
    rows = []
    route_hits = pooled_hits = delivered_hits = 0
    targets = 0
    for label, request, target in HUNTS:
        clues = loc.parse_clues(request, None)
        spot = set(backend.search(clues, [root]))
        fn = set(loc.filename_search(clues, [root]))
        content = loc.content_search(clues, [root], deadline_s=30)
        route_pool = spot | fn | set(content.keys())
        result = loc.locate(request, roots=[root], index_backend=EmulatedSpotlight())
        block = hunt_routing.answer_for(request, result)
        if target is None:
            ok = result["status"] == "not_found"
            rows.append((label, "honest not_found" if ok else f"BAD: status {result['status']}",
                         "-", "-", "PASS" if ok else "FAIL"))
            continue
        targets += 1
        t = str(target)
        in_route = t in route_pool
        in_pool = in_route  # the pool is exactly the union of route hits
        delivered = t in block
        route_hits += in_route
        pooled_hits += in_pool
        delivered_hits += delivered
        rows.append((label, stage_of_exit(target, result, route_pool),
                     "Y" if in_route else "-", "Y" if delivered else "-",
                     "PASS" if delivered else "FAIL"))
        print(f"{label}: {rows[-1][1]} (route {'Y' if in_route else '-'}, "
              f"delivered {'Y' if delivered else '-'})", flush=True)
    print(f"root: {root}")
    print(f"{'hunt':5} {'stage of exit / status':58} {'route':5} {'deliv':5} grade")
    for label, stage, r, d, g in rows:
        print(f"{label:5} {stage:58} {r:5} {d:5} {g}")
    print(f"\nroute recall {route_hits}/{targets}   pooled recall {pooled_hits}/{targets}   "
          f"delivered recall {delivered_hits}/{targets}   (N1 graded separately above)")
    gap = route_hits - delivered_hits
    print("delivery seam: CLOSED" if gap == 0 else f"delivery seam: OPEN, {gap} target(s) lost after the routes")
    return 0 if gap == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
