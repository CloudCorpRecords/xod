"""
The locate service (app/services/locate.py) on a planted forest: about 25
files in a temp tree with decoys (same name different years, right name
wrong folder, content-heavy near misses) and backdated mtimes.

Per clue type: name fragment, kind+season, content words, purpose (W2),
folder-by-contents, photo+year, name+year. Named regressions from the
baseline hunts: "tax return summary for 2025, not 2024" must put the 2025
file on top, and a source folder must be findable by name. Close decoys
must produce ASK, a nonexistent target an honest not-found with a tried
list, and every returned path must exist and be complete.

No model, no Keychain, no network:
  PYTHON_KEYRING_BACKEND=tests.support.filekeyring.FileKeyring .venv/bin/python tests/locate/run_locate_test.py
"""
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-locate-data-")

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


def plant(base, rel, body="x", when=None):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, bytes):
        p.write_bytes(body)
    else:
        p.write_text(body)
    if when:
        ts = datetime(*when).timestamp()
        os.utime(p, (ts, ts))
    return p


def sane(res):
    """Every candidate in any result: absolute, exists, never truncated."""
    for c in res.get("candidates", []):
        if not os.path.isabs(c["path"]) or not os.path.exists(c["path"]):
            return False
        if c["is_folder"]:
            if not os.path.isdir(c["path"]):
                return False
        elif os.path.splitext(c["path"])[1] != os.path.splitext(c["name"])[1]:
            return False
    return True


def main():
    from app.services import locate as loc

    base = Path(tempfile.mkdtemp(prefix="xod-locate-"))
    docs = base / "Documents"

    tax25 = plant(docs, "taxes/2025/Tax Return Summary 2025.pdf", b"%PDF fake", (2025, 6, 15))
    tax24 = plant(docs, "taxes/2024/Tax Return Summary 2024.pdf", b"%PDF fake", (2024, 6, 15))
    plant(docs, "taxes/2024/tax questions.txt", "tax return filing questions for the accountant")
    w2 = plant(docs, "work/W2 2024 Wage Statement.pdf", b"%PDF fake")
    plant(docs, "work/payroll record.txt", "wages salary earnings wage statement stub details")
    contract = plant(docs, "work/Consulting Contract Signed.pdf", b"%PDF fake")
    plant(docs, "work/Consulting Contract Draft.txt", "draft consulting contract, unsigned")
    reunion24 = plant(base, "Pictures/Family Reunion 2024.png", b"\x89PNG", (2024, 7, 4))
    plant(base, "Pictures/Family Reunion 2023.png", b"\x89PNG", (2023, 7, 4))
    plant(base, "Pictures/beach sunset 2024.png", b"\x89PNG", (2024, 8, 1))
    plant(docs, "Trips/Japan Trip/itinerary.txt", "Japan trip: Tokyo, Kyoto, bullet train, ramen")
    plant(docs, "Trips/Japan Trip/hotel booking japan.pdf", b"%PDF fake")
    plant(docs, "Trips/Japan Trip/photos.txt", "japan trip photos to sort")
    plant(docs, "Japan packing list.txt", "japan packing: socks, charger, passport")
    plant(docs, "recipes/pasta recipe.txt", "recipe: fresh tomatoes, basil, garlic, boil pasta")
    plant(docs, "finance/Invoice 1042 Acme.pdf", b"%PDF fake")
    plant(docs, "finance/budget 2025.xlsx", b"fake sheet")
    garden = plant(docs, "notes/garden journal.txt", "planted heirloom tomatoes and basil this year")
    plant(docs, "notes/meeting.txt", "standup notes, roadmap chat")
    lease_a = plant(docs, "Legal/Smith Lease.pdf", b"%PDF fake", (2025, 3, 1))
    lease_b = plant(docs, "Archive/Smith Lease.pdf", b"%PDF fake", (2025, 3, 1))
    window = loc.season_window("last spring")
    mid_spring = (window[0] + window[1]) / 2
    spring_sheet = plant(docs, "finance/expenses.xlsx", b"fake sheet")
    os.utime(spring_sheet, (mid_spring, mid_spring))
    winter_sheet = plant(docs, "Archive/expenses.xlsx", b"fake sheet")
    old = window[0] - 45 * 86400
    os.utime(winter_sheet, (old, old))
    xod_dir = docs / "projects" / "xod"
    plant(docs, "projects/xod/README.md", "# xod\nthe local agent")
    plant(docs, "projects/xod/.git/HEAD", "ref: refs/heads/main")

    roots = [str(base)]

    def hunt(req, **kw):
        kw.setdefault("roots", roots)
        kw.setdefault("index_backend", EmptyIndex())
        return loc.locate(req, **kw)

    # name fragment: the baseline partial named the file but gave no full path
    res = hunt("find the consulting contract")
    check("fragment: consulting contract found", res["status"] == "found" and res["candidates"], str(res)[:300])
    check("fragment: top is the signed contract, full path",
          res["candidates"] and res["candidates"][0]["path"] == str(contract), str(res["candidates"][:1]))
    check("fragment: candidates carry evidence", bool(res["candidates"] and res["candidates"][0]["why"]))

    # kind + rough season (ranking signal, not a filter)
    res = hunt("find the spreadsheet from last spring")
    check("kind+season: found", res["status"] == "found", str(res)[:300])
    check("kind+season: the spring-modified sheet wins",
          res["candidates"] and res["candidates"][0]["path"] == str(spring_sheet), str(res["candidates"][:2]))

    # content words
    res = hunt("find the note about heirloom tomatoes")
    check("content words: garden journal found by its contents",
          res["status"] == "found" and res["candidates"] and res["candidates"][0]["path"] == str(garden),
          str(res)[:300])

    # purpose: W2 must not wedge and must beat the content-heavy paystub decoy
    t0 = time.time()
    res = hunt("find my W2")
    check("purpose W2: found fast", res["status"] == "found" and time.time() - t0 < 30, str(res)[:200])
    check("purpose W2: the W2 wage statement is top",
          res["candidates"] and res["candidates"][0]["path"] == str(w2), str(res["candidates"][:2]))

    # folder by contents
    res = hunt("find the folder that has my Japan trip stuff")
    top = res["candidates"][0] if res["candidates"] else {}
    check("folder-by-contents: a folder is returned", res["status"] in ("found", "ask") and top.get("is_folder"), str(res)[:300])
    check("folder-by-contents: it is the Japan Trip folder",
          top.get("path") == str(docs / "Trips" / "Japan Trip"), str(top))

    # photo + year
    res = hunt("find the Family Reunion photo from 2024")
    check("photo+year: the 2024 reunion photo is top",
          res["status"] == "found" and res["candidates"] and res["candidates"][0]["path"] == str(reunion24),
          str(res)[:300])

    # NAMED REGRESSION 1: name+year with an excluded year
    res = hunt("find the tax return summary for 2025, not 2024")
    check("REGRESSION tax 2025 not 2024: found", res["status"] == "found", str(res)[:300])
    check("REGRESSION tax 2025 not 2024: 2025 file top-ranked",
          res["candidates"] and res["candidates"][0]["path"] == str(tax25), str(res["candidates"][:2]))
    check("REGRESSION tax 2025 not 2024: the 2024 file is not a candidate",
          all(c["path"] != str(tax24) for c in res["candidates"]), str(res["candidates"]))

    # NAMED REGRESSION 2: a source folder found by name (repo checkout shape)
    res = hunt("find the xod folder")
    check("REGRESSION source folder by name: xod folder returned",
          res["status"] == "found" and res["candidates"] and res["candidates"][0]["path"] == str(xod_dir)
          and res["candidates"][0]["is_folder"], str(res)[:300])

    # close decoys: ASK, with both named
    res = hunt("find the Smith lease")
    check("close decoys: status is ask", res["status"] == "ask", str(res)[:300])
    check("close decoys: both leases are the top two",
          {c["path"] for c in res["candidates"][:2]} == {str(lease_a), str(lease_b)}, str(res["candidates"][:2]))
    check("close decoys: a question is included", bool(res.get("question")))

    # not found: honest, with what was tried
    res = hunt("find my helicopter registration document")
    check("not found: status", res["status"] == "not_found" and not res["candidates"], str(res)[:300])
    routes = {t["route"] for t in res["tried"]}
    check("not found: tried list covers all routes", routes == {"spotlight", "filename", "content"}, str(res["tried"]))

    # the Spotlight seam: an injected index can be the only route that hits
    class OneIndex:
        def search(self, clues, roots_):
            return [str(tax25)]
    empty = Path(tempfile.mkdtemp(prefix="xod-locate-empty-"))
    res = loc.locate("find the tax return summary for 2025", roots=[str(empty)], index_backend=OneIndex())
    check("index seam: fake index hit is used and verified",
          res["status"] == "found" and res["candidates"] and res["candidates"][0]["path"] == str(tax25)
          and res["tried"][0]["route"] == "spotlight" and res["tried"][0]["hits"] == 1, str(res)[:300])

    # the tool layer speaks the same language
    import asyncio
    from app.agent.tools import execute_tool, TOOL_DEFINITIONS
    check("tool registered", any(t["function"]["name"] == "locate_files" for t in TOOL_DEFINITIONS))
    out = asyncio.run(execute_tool("locate_files", {"request": "find the tax return summary for 2025, not 2024",
                                                     "root_dir": str(base)}))
    check("tool returns the structured result",
          out.get("status") == "found" and out["candidates"] and out["candidates"][0]["path"] == str(tax25),
          str(out)[:300])

    # every path ever returned exists and is complete
    all_ok = True
    for req in ["find the consulting contract", "find my W2", "find the Smith lease",
                "find the folder that has my Japan trip stuff", "find the xod folder"]:
        if not sane(hunt(req)):
            all_ok = False
    check("every returned path exists and is complete", all_ok)

    # ---- Lane 2 (Fix Round 2): bounded content search, pathological tree ----
    # A FIFO with a text extension blocks open() forever on the unbounded
    # code (reproduced on main under a 20s external timeout: no return).
    # The bounded route must skip it without opening it, cap every read,
    # skip and count what it cannot scan, and label any budget trip.
    pbase = Path(tempfile.mkdtemp(prefix="xod-locate-patho-"))
    pdocs = pbase / "Documents"
    pdocs.mkdir(parents=True)
    good = plant(pdocs, "recipes/broth notes.txt",
                 "chicken broth with carrots and celery, simmer gently")
    huge = pdocs / "huge.txt"
    with open(huge, "wb") as f:
        f.seek(8 * 1024 * 1024 - 1)
        f.write(b"x")
    with open(huge, "r+b") as f:
        f.seek(8 * 1024 * 1024 - 64)
        f.write(b"chicken broth carrots")
    plant(pdocs, "binary.txt", bytes(range(256)) * 64)
    unread = plant(pdocs, "unreadable.txt", "chicken broth carrots secret")
    os.chmod(unread, 0o000)
    os.mkfifo(pdocs / "pipe.txt")
    pclues = loc.parse_clues("", {"content_words": ["chicken", "broth", "carrots"]})
    proots = [str(pbase)]
    try:
        stats = {}
        t0 = time.time()
        hits = loc.content_search(pclues, proots, deadline_s=10, stats=stats)
        elapsed = time.time() - t0
        check("patho: content_search completes inside budget over FIFO tree",
              elapsed < 10, f"elapsed={elapsed:.2f} stats={stats}")
        check("patho: genuine hit still found with all its words",
              str(good) in hits and {"chicken", "broth", "carrots"} <= hits[str(good)],
              str(hits))
        check("patho: FIFO counted as nonregular skip, never opened",
              stats.get("skipped_nonregular", 0) >= 1, str(stats))
        check("patho: unreadable file skipped and counted, not a hit",
              stats.get("skipped", 0) >= 2 and str(unread) not in hits, str(stats))
        check("patho: oversized file skipped, words past the cap not matched",
              stats.get("skipped_oversized", 0) >= 1 and str(huge) not in hits, str(stats))
        check("patho: no budget trip on a small tree, not labeled partial",
              stats.get("partial") is False, str(stats))

        stats = {}
        hits = loc.content_search(pclues, proots, deadline_s=0, stats=stats)
        check("patho: zero deadline trips at once, labeled deadline partial",
              stats.get("partial") is True and stats.get("partial_reason") == "deadline",
              str(stats))
        stats = {}
        loc.content_search(pclues, proots, deadline_s=10, max_files=0, stats=stats)
        check("patho: file cap trips, labeled file_cap partial",
              stats.get("partial") is True and stats.get("partial_reason") == "file_cap",
              str(stats))
        stats = {}
        loc.content_search(pclues, proots, deadline_s=10, max_entries=0, stats=stats)
        check("patho: entry cap trips, labeled entry_cap partial",
              stats.get("partial") is True and stats.get("partial_reason") == "entry_cap",
              str(stats))

        t0 = time.time()
        res = loc.locate("find the file with chicken broth carrots", roots=proots,
                         index_backend=EmptyIndex(), content_deadline_s=5, hunt_deadline_s=20)
        elapsed = time.time() - t0
        check("patho: full locate completes inside the hunt budget",
              elapsed < 20, f"elapsed={elapsed:.2f}")
        check("patho: full locate still finds the genuine hit",
              any(c["path"] == str(good) for c in res["candidates"]), str(res)[:300])
        ctried = [t for t in res["tried"] if t["route"] == "content"]
        check("patho: locate content tried entry is not partial on a small tree",
              bool(ctried) and ctried[0].get("partial") is False, str(res["tried"]))

        t0 = time.time()
        res = loc.locate("find the file with chicken broth carrots", roots=proots,
                         index_backend=EmptyIndex(), content_deadline_s=0, hunt_deadline_s=20)
        elapsed = time.time() - t0
        ctried = [t for t in res["tried"] if t["route"] == "content"]
        check("patho: locate labels a tripped content budget in tried",
              elapsed < 20 and bool(ctried) and ctried[0].get("partial") is True
              and ctried[0].get("partial_reason") == "deadline",
              f"elapsed={elapsed:.2f} tried={res['tried']}")
    finally:
        os.chmod(unread, 0o644)

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


class EmptyIndex:
    def search(self, clues, roots):
        return []


if __name__ == "__main__":
    main()
