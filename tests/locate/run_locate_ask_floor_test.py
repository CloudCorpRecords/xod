"""
The ask floor and the kind-mismatch done block (Fix Round 4, Lane 3).

Re-run 3 shapes, planted in temp trees with an empty index:

  N1  a hunt for a target that does not exist (a quarterly board deck
      about zebrafish) over only irrelevant files must return not_found
      with a tried list, and the rendered delivery must carry the tried
      list and no ask. On the unfixed code the two irrelevant files each
      score one bare name-token hit (30), finish tied, and the ask gate
      fires between them.
  Weak close pair: two candidates tied at one bare name-token hit each
      must be not_found, never an ask (the shape that failed all through
      re-run 3: 12 asks, every one between unrelated files).
  L2  a PDF hunt whose only surviving candidate scores above the floor
      but whose own evidence line says "but it is a document, not a pdf"
      must not close found/done: locate returns not_found with the
      candidate named as checked-and-rejected, and the decisive-evidence
      layer refuses to render a found payload carrying that candidate
      (the block holds at the delivery layer, not only inside locate).

Positive controls: one strong candidate is found; two strong close
candidates ask; exactly one candidate at or above the floor (plus one
below it) is found with the strong candidate alone.

No model, no Keychain, no network:
  PYTHON_KEYRING_BACKEND=tests.support.filekeyring.FileKeyring .venv/bin/python tests/locate/run_locate_ask_floor_test.py
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-askfloor-data-")

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


class EmptyIndex:
    def search(self, clues, roots):
        return []


def main():
    from app.services import locate as loc
    from app.services import decisive_evidence as de
    from app.services import hunt_routing

    floor = getattr(loc, "ASK_FLOOR", None)
    check("floor: ASK_FLOOR exists and sits above FOUND_THRESHOLD",
          floor is not None and loc.FOUND_THRESHOLD < floor <= 55.0,
          f"ASK_FLOOR={floor} FOUND_THRESHOLD={loc.FOUND_THRESHOLD}")

    # ---- N1: nonexistent target, only irrelevant files ----------------
    base = Path(tempfile.mkdtemp(prefix="xod-askfloor-n1-"))
    plant(base, "board.dat", b"\x00\x01\x02")
    plant(base, "deck.dat", b"\x03\x04\x05")
    plant(base, "grocery list.txt", "milk, eggs, bread")
    req_n1 = "find the quarterly board deck about zebrafish"
    res = loc.locate(req_n1, roots=[str(base)], index_backend=EmptyIndex())
    check("N1: status is not_found, not ask", res["status"] == "not_found", str(res)[:300])
    check("N1: no candidates are named", not res["candidates"], str(res["candidates"]))
    routes = {t["route"] for t in res["tried"]}
    check("N1: tried list is carried and covers all routes",
          routes == {"spotlight", "filename", "content"}, str(res["tried"]))
    block = de.block_for(de.collect([("locate_files", res)]), req_n1)
    check("N1 delivery: rendered block says Not found and carries the tried list",
          "Not found" in block and "Tried:" in block and "zebrafish" in block, block[:300])
    check("N1 delivery: rendered block contains no ask",
          "Two close matches" not in block and "Which one did you mean" not in block, block[:300])

    # ---- weak close pair: tied at one bare name-token hit each --------
    base = Path(tempfile.mkdtemp(prefix="xod-askfloor-weak-"))
    plant(base, "harbor.dat", b"\x00\x01")
    plant(base, "anchor.dat", b"\x02\x03")
    res = loc.locate("find the harbor anchor log", roots=[str(base)], index_backend=EmptyIndex())
    check("weak pair: tied weak candidates are not_found, never ask",
          res["status"] == "not_found" and not res["candidates"], str(res)[:300])

    # ---- L2: kind mismatch stated in the candidate's own evidence -----
    # The candidate scores 50 (two name tokens, 60, plus the all-token
    # bonus, 20, minus the kind mismatch, 30): above the ask floor, so
    # only the kind-mismatch block can stop it closing as found.
    base = Path(tempfile.mkdtemp(prefix="xod-askfloor-l2-"))
    lease_txt = plant(base, "Harbor Report.txt", "x")
    req_l2 = "find the harbor report pdf"
    res = loc.locate(req_l2, roots=[str(base)], index_backend=EmptyIndex())
    check("L2: a kind-refuted single candidate is not found/done",
          res["status"] == "not_found" and not res["candidates"], str(res)[:300])
    rejected = res.get("rejected") or []
    check("L2: the refuted candidate is named as checked-and-rejected with its evidence",
          any(c["path"] == str(lease_txt) and any("not a pdf" in w for w in c["why"]) for c in rejected),
          str(rejected))
    block = de.block_for(de.collect([("locate_files", res)]), req_l2)
    check("L2 delivery: block is the not-found rendering with the tried list and the rejection",
          "Not found" in block and "Tried:" in block and "Checked and rejected" in block
          and str(lease_txt) in block and "What the search found" not in block, block[:400])

    # L2 at the delivery layer alone: a found payload carrying the
    # refuted candidate (the shape the unfixed locate produced, and the
    # shape any other producer could hand the task layer) must be
    # downgraded by decisive evidence capture, so no done close can
    # present it.
    stale_payload = {
        "request": req_l2,
        "status": "found",
        "clues": {"name_tokens": ["harbor", "report"], "kind": "pdf", "year": None,
                  "excluded_years": [], "content_words": [], "purpose_terms": [],
                  "want_folder": False, "folder_by_contents": False, "date_label": ""},
        "tried": [{"route": "spotlight", "terms": ["harbor", "report"], "hits": 0},
                  {"route": "filename", "terms": ["harbor", "report"], "hits": 1},
                  {"route": "content", "terms": ["harbor", "report"], "hits": 0}],
        "candidates": [{
            "path": str(lease_txt), "name": "Harbor Report.txt", "kind": "document",
            "is_folder": False, "modified": "2025-04-01", "modified_ts": 1743465600.0,
            "size": 1, "score": 50.0,
            "why": ["name matches 'harbor'", "name matches 'report'",
                    "every name clue is in the name",
                    "but it is a document, not a pdf"],
        }],
    }
    entries = de.collect([("locate_files", stale_payload)])
    check("L2 delivery layer: capture downgrades a refuted found payload to not_found",
          bool(entries) and entries[0]["payload"].get("status") == "not_found"
          and not entries[0]["payload"].get("candidates"),
          str(entries)[:300])
    answer = hunt_routing.answer_for(req_l2, stale_payload)
    check("L2 delivery layer: the delivered answer is not found, never done-with-candidate",
          "Not found" in answer and "Tried:" in answer and "What the search found" not in answer,
          answer[:400])

    # ---- positive control: one strong candidate is found --------------
    base = Path(tempfile.mkdtemp(prefix="xod-askfloor-strong-"))
    tax = plant(base, "taxes/Tax Return Summary 2025.pdf", b"%PDF fake", (2025, 6, 15))
    plant(base, "notes.txt", "unrelated notes")
    res = loc.locate("find the tax return summary for 2025", roots=[str(base)], index_backend=EmptyIndex())
    check("strong single: found with the strong candidate on top",
          res["status"] == "found" and res["candidates"] and res["candidates"][0]["path"] == str(tax),
          str(res)[:300])

    # ---- positive control: two strong close candidates ask ------------
    base = Path(tempfile.mkdtemp(prefix="xod-askfloor-ask-"))
    lease_a = plant(base, "Legal/Smith Lease.pdf", b"%PDF fake", (2025, 3, 1))
    lease_b = plant(base, "Archive/Smith Lease.pdf", b"%PDF fake", (2025, 3, 1))
    res = loc.locate("find the Smith lease", roots=[str(base)], index_backend=EmptyIndex())
    check("strong pair: two strong close candidates still ask, both named",
          res["status"] == "ask"
          and {c["path"] for c in res["candidates"][:2]} == {str(lease_a), str(lease_b)}
          and bool(res.get("question")), str(res)[:300])

    # ---- positive control: one above the floor, one below it ----------
    base = Path(tempfile.mkdtemp(prefix="xod-askfloor-one-"))
    strong = plant(base, "Harbor Report.txt", "the harbor report body")
    plant(base, "harbor.dat", b"\x00\x01")
    res = loc.locate("find the harbor report", roots=[str(base)], index_backend=EmptyIndex())
    check("one above floor: found with the strong candidate alone",
          res["status"] == "found" and [c["path"] for c in res["candidates"]] == [str(strong)],
          str(res)[:300])

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
