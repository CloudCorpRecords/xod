"""
Found-threshold kind discipline (Fix Round 5, Lane 2).

Re-run 4 shapes: two different hunts (L4, a W2 wage statement; L9, the
tax return summary for 2025, not 2024) both closed done on the same
code file, taxonomy.py, credited because its name "fits the purpose
('tax')" and its contents mention 'return' and 'tax'. Trace findings
this suite is built on (verified against the code):

  * parse_clues leaves clues.kind EMPTY for both prompts: neither
    names a kind word. So score_candidate emits no kind line at all,
    the -60 mismatch deduction never applies, and the round 4 guard
    (which keys on the scorer's "but it is a X, not a Y" prose in the
    why lines) has nothing to match.
  * The purpose-in-name check is a substring test on the stem, so the
    purpose term 'tax' earns its full 60 points inside the longer
    token 'taxonomy'. 60 alone is double FOUND_THRESHOLD and above
    ASK_FLOOR; two weak content hits ('return', 'tax') stack on top.
    Pristine scores: L4 68, L9 58, zero name identity either time.

Fixtures are synthetic: a code file whose name carries a purpose term
as a substring of a longer token, with contents mentioning the hunt
terms, planted alone (Tree A, the production shape, where the real
target never reached the scored pool) and alongside the real PDF
targets (Tree B). On pristine code both Tree A hunts close found on
the code file; on the fixed code they must not: Tree A falls to
not_found and Tree B finds the real target with the code file absent.

Controls: the round 4 L2 self-refutation shape (a candidate whose own
why lines state the kind mismatch) must keep being blocked and listed
as checked-and-rejected; a delivery-layer payload whose why lines
carry NO mismatch prose but whose structured kind field contradicts
the requested kind, with no name identity, must be downgraded by
decisive_evidence; the same payload WITH name identity is left alone
by the structured check; genuine kind-matching and whole-token
purpose targets still find cleanly.

No model, no Keychain, no network:
  PYTHON_KEYRING_BACKEND=tests.support.filekeyring.FileKeyring .venv/bin/python tests/locate/run_locate_kind_discipline_test.py
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-kind-data-")

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


L4 = ("Find the file for the tax thing, the wage statement from my employer, "
      "somewhere on this computer. Reply with its full path.")
L9 = "Find the tax return summary for 2025, not 2024. Reply with its full path."

CODE_BODY = ("# module for ledger categories\n"
             "def classify(entry):\n"
             "    # each tax category gets a return code\n"
             "    return entry.tax_code\n")


def plant_code_file(base):
    return plant(base, "code/taxonomy.py", CODE_BODY, (2026, 10, 8))


def main():
    from app.services import locate as loc
    from app.services import decisive_evidence as de

    # ---- trace anchors: the parse facts the discipline is built on --
    clues4 = loc.parse_clues(L4)
    clues9 = loc.parse_clues(L9)
    check("trace: L4 parse leaves kind empty (no kind word in the prompt)",
          clues4.kind == "", repr(clues4.kind))
    check("trace: L9 parse leaves kind empty, year 2025, 2024 excluded",
          clues9.kind == "" and clues9.year == 2025 and clues9.excluded_years == [2024],
          f"kind={clues9.kind!r} year={clues9.year} excluded={clues9.excluded_years}")
    check("trace: both parses carry 'tax' as a purpose term",
          "tax" in clues4.purpose_terms and "tax" in clues9.purpose_terms,
          f"L4={clues4.purpose_terms} L9={clues9.purpose_terms}")

    # ---- scoring: purpose-in-name must be a whole-token match -------
    base = Path(tempfile.mkdtemp(prefix="xod-kind-score-"))
    code_file = plant_code_file(base)
    whole = plant(base, "Tax Notes.txt", "plain notes")
    for label, clues in (("L4", clues4), ("L9", clues9)):
        _, why_code = loc.score_candidate(str(code_file), clues, {"tax", "return"})
        check(f"{label} scoring: no purpose credit for 'tax' inside 'taxonomy'",
              not any("fits the purpose ('tax')" in w for w in why_code), str(why_code))
        score_code, _ = loc.score_candidate(str(code_file), clues, {"tax", "return"})
        check(f"{label} scoring: the code file stays below FOUND_THRESHOLD on weak content alone",
              score_code < loc.FOUND_THRESHOLD, f"score={score_code}")
    _, why_whole = loc.score_candidate(str(whole), clues4, set())
    check("scoring control: a whole-token 'tax' in a name still earns purpose credit",
          any("fits the purpose ('tax')" in w for w in why_whole), str(why_whole))

    # ---- Tree A: code file alone (the production shape) -------------
    for label, req in (("L4", L4), ("L9", L9)):
        base = Path(tempfile.mkdtemp(prefix=f"xod-kind-a-{label.lower()}-"))
        code_file = plant_code_file(base)
        plant(base, "notes.txt", "unrelated notes about lunch")
        plant(base, "grocery list.txt", "milk, eggs, bread")
        res = loc.locate(req, roots=[str(base)], index_backend=EmptyIndex())
        check(f"Tree A {label}: the hunt does not close found on the code file",
              res["status"] != "found"
              and all(c["path"] != str(code_file) for c in res["candidates"]),
              str(res)[:300])
        check(f"Tree A {label}: the honest outcome is not_found with a tried list",
              res["status"] == "not_found" and bool(res["tried"]), str(res)[:300])
        block = de.block_for(de.collect([("locate_files", res)]), req)
        check(f"Tree A {label} delivery: rendered block is the not-found form, no candidate named",
              "Not found" in block and "taxonomy" not in block, block[:300])

    # ---- Tree B: code file alongside the real PDF targets -----------
    base = Path(tempfile.mkdtemp(prefix="xod-kind-b-"))
    code_file = plant_code_file(base)
    w2 = plant(base, "taxes/2025/W2 Wage Statement.pdf", b"%PDF fake", (2025, 6, 15))
    summ25 = plant(base, "taxes/2025/Tax Return Summary 2025.pdf", b"%PDF fake", (2025, 6, 15))
    plant(base, "taxes/2024/Tax Return Summary 2024.pdf", b"%PDF fake", (2024, 6, 15))
    res = loc.locate(L4, roots=[str(base)], index_backend=EmptyIndex())
    check("Tree B L4: found, with the W2 wage statement on top",
          res["status"] == "found" and res["candidates"]
          and res["candidates"][0]["path"] == str(w2), str(res)[:300])
    check("Tree B L4: the code file is not among the delivered candidates",
          all(c["path"] != str(code_file) for c in res["candidates"]), str(res["candidates"]))
    res = loc.locate(L9, roots=[str(base)], index_backend=EmptyIndex())
    check("Tree B L9: found, with the 2025 tax return summary on top",
          res["status"] == "found" and res["candidates"]
          and res["candidates"][0]["path"] == str(summ25), str(res)[:300])
    check("Tree B L9: the code file is not among the delivered candidates",
          all(c["path"] != str(code_file) for c in res["candidates"]), str(res["candidates"]))

    # ---- L2 control: the round 4 self-refutation shape stays blocked
    base = Path(tempfile.mkdtemp(prefix="xod-kind-l2-"))
    lease_txt = plant(base, "Harbor Report.txt", "x")
    req_l2 = "find the harbor report pdf"
    res = loc.locate(req_l2, roots=[str(base)], index_backend=EmptyIndex())
    rejected = res.get("rejected") or []
    check("L2 control: a kind-refuted single candidate is still not found/done",
          res["status"] == "not_found" and not res["candidates"], str(res)[:300])
    check("L2 control: the refuted candidate is still listed as checked-and-rejected",
          any(c["path"] == str(lease_txt) and any("not a pdf" in w for w in c["why"])
              for c in rejected), str(rejected))

    # ---- structured kind discipline at the delivery layer -----------
    # A found payload whose why lines carry NO mismatch prose (the L4
    # and L9 production shape) but whose candidate kind field ("code")
    # contradicts the hunt's requested kind ("pdf"), with no name
    # identity behind it: the structured check must downgrade it.
    base = Path(tempfile.mkdtemp(prefix="xod-kind-struct-"))
    code_file = plant_code_file(base)
    tried = [{"route": "spotlight", "terms": ["tax"], "hits": 0},
             {"route": "filename", "terms": ["tax"], "hits": 1},
             {"route": "content", "terms": ["tax"], "hits": 1}]
    clues_block = {"name_tokens": ["statement", "employer"], "kind": "pdf", "year": None,
                   "excluded_years": [], "content_words": [],
                   "purpose_terms": ["w2", "wage", "tax", "return"],
                   "want_folder": False, "folder_by_contents": False, "date_label": ""}
    payload = {
        "request": L4, "status": "found", "clues": clues_block, "tried": tried,
        "candidates": [{
            "path": str(code_file), "name": "taxonomy.py", "kind": "code",
            "is_folder": False, "modified": "2026-10-08", "modified_ts": 1791000000.0,
            "size": 120, "score": 68.0,
            "why": ["name fits the purpose ('tax')", "contents mention 'return'",
                    "contents mention 'tax'"],
        }],
    }
    entries = de.collect([("locate_files", payload)])
    check("structured sanitize: a kind-contradicting found payload with no name identity is downgraded",
          bool(entries) and entries[0]["payload"].get("status") == "not_found"
          and not entries[0]["payload"].get("candidates"), str(entries)[:300])
    check("structured sanitize: the downgraded candidate is moved to checked-and-rejected",
          bool(entries) and any(c["path"] == str(code_file)
                                 for c in entries[0]["payload"].get("rejected") or []),
          str(entries)[:300])
    block = de.block_for(entries, L4)
    check("structured sanitize delivery: the rendered block is not-found with the rejection listed",
          "Not found" in block and "Checked and rejected" in block and "taxonomy" in block,
          block[:300])

    # Same shape WITH name identity: the structured check alone must
    # not refute it (the prose guard remains the stricter L2 layer).
    payload2 = {
        "request": L4, "status": "found", "clues": clues_block, "tried": tried,
        "candidates": [{
            "path": str(code_file), "name": "Statement Helper.py", "kind": "code",
            "is_folder": False, "modified": "2026-10-08", "modified_ts": 1791000000.0,
            "size": 120, "score": 68.0,
            "why": ["name matches 'statement'", "name fits the purpose ('tax')"],
        }],
    }
    entries2 = de.collect([("locate_files", payload2)])
    check("structured sanitize: name identity rescues a kind-contradicting candidate from the structured check",
          bool(entries2) and entries2[0]["payload"].get("status") == "found"
          and bool(entries2[0]["payload"].get("candidates")), str(entries2)[:300])

    # ---- helper-level checks for the structured predicates ----------
    kc = getattr(loc, "kind_contradicts", None)
    check("helpers: locate.kind_contradicts reads the kind field, not prose",
          kc is not None
          and kc({"kind": "code", "is_folder": False}, "pdf") is True
          and kc({"kind": "pdf", "is_folder": False}, "pdf") is False
          and kc({"kind": "folder", "is_folder": True}, "pdf") is False
          and kc({"kind": "code", "is_folder": False}, "") is False,
          f"kind_contradicts={kc}")
    hi = getattr(loc, "has_name_identity", None)
    check("helpers: locate.has_name_identity needs an exact token or an exact-name shape",
          hi is not None
          and hi({"name": "taxonomy.py"}, clues4) is False
          and hi({"name": "W2 Wage Statement.pdf"}, clues4) is True,
          f"has_name_identity={hi}")

    # ---- positive control: genuine kind-matching target -------------
    base = Path(tempfile.mkdtemp(prefix="xod-kind-pos-"))
    report = plant(base, "Harbor Report.pdf", b"%PDF fake", (2025, 4, 1))
    res = loc.locate(req_l2, roots=[str(base)], index_backend=EmptyIndex())
    check("positive control: a genuine kind-matching target with purpose terms finds cleanly",
          res["status"] == "found" and res["candidates"]
          and res["candidates"][0]["path"] == str(report), str(res)[:300])

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)} passed; failed={failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
