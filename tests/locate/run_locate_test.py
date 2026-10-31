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

    # ---- Lane 3 (Fix Round 2): determinism, diagnostics, hint precedence ----
    base2 = Path(tempfile.mkdtemp(prefix="xod-locate-var-"))
    rep_a = plant(base2, "alpha/Meeting Notes.pdf", b"%PDF fake", (2025, 5, 1))
    rep_b = plant(base2, "beta/Meeting Notes.pdf", b"%PDF fake", (2025, 5, 1))
    roots2 = [str(base2)]

    class FwdIndex:
        def search(self, clues, roots_):
            return [str(rep_a), str(rep_b)]

    class RevIndex:
        def search(self, clues, roots_):
            return [str(rep_b), str(rep_a)]

    tie_req = "find the Meeting Notes pdf"
    r_empty = loc.locate(tie_req, roots=roots2, index_backend=EmptyIndex())
    r_fwd = loc.locate(tie_req, roots=roots2, index_backend=FwdIndex())
    r_rev = loc.locate(tie_req, roots=roots2, index_backend=RevIndex())
    orders = [[c["path"] for c in r["candidates"]] for r in (r_empty, r_fwd, r_rev)]
    check("tie-order determinism: same-name equal-score candidates keep one order regardless of route production order",
          orders[0] == orders[1] == orders[2] == [str(rep_a), str(rep_b)], str(orders))

    score_maps = [{c["path"]: c["score"] for c in r["candidates"]} for r in (r_empty, r_fwd, r_rev)]
    check("index-blind equivalence: a candidate scores the same whether the index returns it, misses it, or returns it first",
          score_maps[0] == score_maps[1] == score_maps[2], str(score_maps))

    diag_fwd = r_fwd.get("diagnostics") or {}
    diag_by_path = {d["path"]: d for d in diag_fwd.get("candidates", [])}
    da = diag_by_path.get(str(rep_a), {})
    check("diagnostics: per-candidate routes and in_index are reported",
          da.get("in_index") is True and "spotlight" in da.get("routes", []) and "filename" in da.get("routes", []),
          str(diag_fwd)[:300])
    diag_empty = r_empty.get("diagnostics") or {}
    de = {d["path"]: d for d in diag_empty.get("candidates", [])}
    check("diagnostics: index-missed candidates are counted with examples",
          de.get(str(rep_a), {}).get("in_index") is False
          and diag_empty.get("index_missed_count", 0) >= 2
          and str(rep_a) in diag_empty.get("index_missed_examples", []),
          str(diag_empty)[:300])
    check("diagnostics: a fully indexed pool reports zero index-missed",
          diag_fwd.get("index_missed_count") == 0, str(diag_fwd)[:300])

    res = loc.locate("find the tax return summary for 2025, not 2024", extra={"year": 2024},
                     roots=roots, index_backend=EmptyIndex())
    check("hint precedence: a contradicting year hint loses to the text",
          res["clues"]["year"] == 2025 and res["candidates"] and res["candidates"][0]["path"] == str(tax25),
          str(res["clues"]) + " " + str(res["candidates"][:1]))
    res = loc.locate("find the Family Reunion photo from 2024", extra={"kind": "spreadsheet"},
                     roots=roots, index_backend=EmptyIndex())
    check("hint precedence: a contradicting kind hint loses to the text",
          res["clues"]["kind"] == "image" and res["candidates"] and res["candidates"][0]["path"] == str(reunion24),
          str(res["clues"]) + " " + str(res["candidates"][:1]))
    res = loc.locate("find the Family Reunion photo", extra={"year": 2024},
                     roots=roots, index_backend=EmptyIndex())
    check("hint precedence: a hint for a field the text lacks is used",
          res["clues"]["year"] == 2024 and res["candidates"] and res["candidates"][0]["path"] == str(reunion24),
          str(res["clues"]) + " " + str(res["candidates"][:1]))
    res = loc.locate("find the tax return summary for 2025, not 2024", extra={"year": 2025},
                     roots=roots, index_backend=EmptyIndex())
    check("hint precedence: a confirming hint changes nothing",
          res["clues"]["year"] == 2025 and res["candidates"] and res["candidates"][0]["path"] == str(tax25),
          str(res["clues"]) + " " + str(res["candidates"][:1]))
    out = asyncio.run(execute_tool("locate_files", {"request": "find the tax return summary for 2025, not 2024",
                                                    "year": 2024, "root_dir": str(base)}))
    check("hint precedence at the tool dispatch: a contradicting year argument loses to the request text",
          out.get("clues", {}).get("year") == 2025 and out.get("candidates")
          and out["candidates"][0]["path"] == str(tax25), str(out)[:300])

    # every path ever returned exists and is complete
    all_ok = True
    for req in ["find the consulting contract", "find my W2", "find the Smith lease",
                "find the folder that has my Japan trip stuff", "find the xod folder"]:
        if not sane(hunt(req)):
            all_ok = False
    check("every returned path exists and is complete", all_ok)

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


class EmptyIndex:
    def search(self, clues, roots):
        return []


if __name__ == "__main__":
    main()
