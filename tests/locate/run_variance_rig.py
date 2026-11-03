"""
Variance rig for the locate service (Lane 3, Fix Round 2).

Plants the 25-file baseline forest recipe from the 2026-10-31 escalation
re-run (plus a two-file same-name tie probe) into a temp dir, then runs the
SERVICE-LEVEL locate() for the hunt set under three index configurations:

  lag            the index sees nothing (the window right after planting,
                 while Spotlight is still catching up)
  settled_fwd    the index sees every planted file, in walk order
  settled_rev    the index sees every planted file, in reverse order

It also runs the real Spotlight backend once immediately after planting and
once after mdimport on the temp dir, and records what the real index did.

Decomposition printed as JSON:
  lag_vs_settled   target presence / rank / score changes and full-order
                   changes between the lag run and the settled run
  fwd_vs_rev       full-order and rank changes between the two settled runs
                   (pure route-production-order instability)
  route_set_scores candidate score changes between lag and settled for the
                   same candidate (scoring sensitivity to the route set)
  real_index       mdfind hit counts before / after mdimport, and whether
                   any hunt result changed between those two real runs

Usage:
  PYTHON_KEYRING_BACKEND=tests.support.filekeyring.FileKeyring \
    .venv/bin/python tests/locate/run_variance_rig.py [out.json]
No model, no Keychain, no network. mdimport touches only the temp fixture.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-rig-data-")

# (relative path, body, mtime or None) -- the 25-file ground truth recipe
FOREST = [
    ("house/lease/Apartment Lease Agreement 2025.pdf", "lease agreement with Bayview Properties", (2025, 4, 15)),
    ("house/lease/Apartment Lease Agreement 2024.pdf", "lease agreement with Bayview Properties", (2024, 4, 10)),
    ("house/lease/Lease Renewal Addendum.pdf", "lease renewal addendum", None),
    ("house/repairs/Plumbing Invoice March.pdf", "plumbing invoice", None),
    ("house/repairs/HVAC Quote.pdf", "hvac quote", None),
    ("house/photos/Kitchen Before Renovation.png", "PNG", None),
    ("taxes/2025/Tax Return Summary 2025.pdf", "tax return summary", (2025, 3, 10)),
    ("taxes/2024/Tax Return Summary 2024.pdf", "tax return summary", None),
    ("taxes/2025/W2 Wage Statement.pdf", "w2 wage statement", None),
    ("taxes/2024/Charitable Donations.csv", "charity,amount", None),
    ("work/contracts/Consulting Contract Signed.pdf", "consulting contract signed", None),
    ("work/contracts/Consulting Contract Draft.docx", "consulting contract draft", None),
    ("work/invoices/Invoice 1042.pdf", "invoice 1042", None),
    ("work/invoices/Invoice 1043.pdf", "invoice 1043", None),
    ("work/Project Budget.xlsx", "project budget sheet", None),
    ("personal/recipes/Grandma Soup Recipe.txt", "grandma soup: chicken broth carrots simmer", None),
    ("personal/recipes/Sourdough Starter Notes.md", "sourdough starter: feeding schedule flour water", None),
    ("personal/travel/Japan Trip Itinerary.pdf", "japan trip itinerary", None),
    ("personal/travel/Japan Trip Budget.csv", "japan trip budget", None),
    ("personal/Passwords Backup.txt", "placeholder, no real secrets", None),
    ("archive/2023/Old Lease Agreement 2023.pdf", "old lease agreement", (2023, 3, 20)),
    ("archive/2024/Meeting Notes October.md", "meeting notes october", None),
    ("archive/2024/Meeting Notes November.md", "meeting notes november", None),
    ("photos-archive/Family Reunion 2024.png", "PNG", None),
    ("photos-archive/Family Reunion 2023.png", "PNG", None),
    # tie probe: same name, same bytes, same mtime, different folders
    ("probe-a/Status Report.pdf", "status report", (2025, 5, 1)),
    ("probe-b/Status Report.pdf", "status report", (2025, 5, 1)),
]

HUNTS = [
    ("L1", "find the Sourdough Starter Notes", "personal/recipes/Sourdough Starter Notes.md"),
    ("L2", "find the lease agreement PDF from last spring", "house/lease/Apartment Lease Agreement 2025.pdf"),
    ("L3", "find the recipe with chicken broth and carrots inside", "personal/recipes/Grandma Soup Recipe.txt"),
    ("L4", "find my W2 wage statement", "taxes/2025/W2 Wage Statement.pdf"),
    ("L7", "find invoice 1043", "work/invoices/Invoice 1043.pdf"),
    ("L8", "find the Family Reunion photo from 2024", "photos-archive/Family Reunion 2024.png"),
    ("L9", "find the tax return summary for 2025, not 2024", "taxes/2025/Tax Return Summary 2025.pdf"),
    ("L10", "find the work budget spreadsheet", "work/Project Budget.xlsx"),
    ("TIE", "find the Status Report pdf", "probe-a/Status Report.pdf"),
]


class EmptyIndex:
    def search(self, clues, roots):
        return []


class AllIndex:
    def __init__(self, paths, reverse=False):
        self.paths = list(reversed(paths)) if reverse else list(paths)

    def search(self, clues, roots):
        return list(self.paths)


def plant(base):
    paths = []
    for rel, body, when in FOREST:
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        if when:
            ts = datetime(*when).timestamp()
            os.utime(p, (ts, ts))
        paths.append(str(p))
    return paths


def run_set(loc, base, backend):
    out = {}
    for hid, req, target_rel in HUNTS:
        target = str(base / target_rel)
        res = loc.locate(req, roots=[str(base)], index_backend=backend)
        cands = [{"path": c["path"], "score": c["score"]} for c in res.get("candidates", [])]
        rank = next((i for i, c in enumerate(cands) if c["path"] == target), None)
        out[hid] = {
            "request": req, "target": target, "status": res.get("status"),
            "target_present": rank is not None,
            "target_rank": rank,
            "target_score": cands[rank]["score"] if rank is not None else None,
            "order": [c["path"] for c in cands],
            "scores": {c["path"]: c["score"] for c in cands},
            "spotlight_hits": next((t["hits"] for t in res.get("tried", []) if t["route"] == "spotlight"), None),
        }
    return out


def compare(a, b):
    """Diff two run sets: target movement plus full-order movement."""
    target_diffs, order_diffs, score_diffs = [], [], 0
    for hid in a:
        x, y = a[hid], b[hid]
        if (x["target_present"], x["target_rank"], x["target_score"]) != (y["target_present"], y["target_rank"], y["target_score"]):
            target_diffs.append(hid)
        if x["order"] != y["order"]:
            order_diffs.append(hid)
        for p in set(x["scores"]) | set(y["scores"]):
            if x["scores"].get(p) != y["scores"].get(p):
                score_diffs += 1
    return {"target_diffs": target_diffs, "order_diffs": order_diffs, "candidate_score_diffs": score_diffs}


def main():
    from app.services import locate as loc

    base = Path(tempfile.mkdtemp(prefix="xod-rig-forest-"))
    paths = plant(base)

    lag = run_set(loc, base, EmptyIndex())
    fwd = run_set(loc, base, AllIndex(paths))
    rev = run_set(loc, base, AllIndex(paths, reverse=True))

    # real Spotlight: immediately after planting, then after mdimport
    real = {}
    for label in ("immediate", "after_mdimport"):
        if label == "after_mdimport":
            try:
                subprocess.run(["/usr/bin/mdimport", str(base)], capture_output=True, timeout=60)
                time.sleep(3)
            except Exception as e:
                real["mdimport_error"] = str(e)[:120]
        out = {}
        for hid, req, target_rel in HUNTS:
            target = str(base / target_rel)
            res = loc.locate(req, roots=[str(base)])  # default SpotlightBackend
            cands = res.get("candidates", [])
            rank = next((i for i, c in enumerate(cands) if c["path"] == target), None)
            out[hid] = {
                "target_present": rank is not None, "target_rank": rank,
                "spotlight_hits": next((t["hits"] for t in res.get("tried", []) if t["route"] == "spotlight"), None),
                "order": [c["path"] for c in cands],
            }
        real[label] = out

    report = {
        "forest_root": str(base),
        "hunts": {hid: {"lag": lag[hid], "settled_fwd": fwd[hid], "settled_rev": rev[hid]} for hid, _, _ in HUNTS},
        "decomposition": {
            "lag_vs_settled": compare(lag, fwd),
            "fwd_vs_rev_route_order": compare(fwd, rev),
            "lag_vs_rev": compare(lag, rev),
        },
        "real_index": {
            "spotlight_hits_immediate": {h: real["immediate"][h]["spotlight_hits"] for h, _, _ in HUNTS},
            "spotlight_hits_after_mdimport": {h: real["after_mdimport"][h]["spotlight_hits"] for h, _, _ in HUNTS},
            "target_diffs_immediate_vs_mdimport": [
                h for h, _, _ in HUNTS
                if (real["immediate"][h]["target_present"], real["immediate"][h]["target_rank"])
                != (real["after_mdimport"][h]["target_present"], real["after_mdimport"][h]["target_rank"])],
            "order_diffs_immediate_vs_mdimport": [
                h for h, _, _ in HUNTS if real["immediate"][h]["order"] != real["after_mdimport"][h]["order"]],
        },
    }
    text = json.dumps(report, indent=1)
    print(text)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text)
    d = report["decomposition"]
    print("\nDECOMPOSITION lag_vs_settled:", d["lag_vs_settled"], file=sys.stderr)
    print("DECOMPOSITION fwd_vs_rev:", d["fwd_vs_rev_route_order"], file=sys.stderr)
    print("REAL INDEX:", report["real_index"], file=sys.stderr)


if __name__ == "__main__":
    main()
