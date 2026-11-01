"""
Clue hygiene for locate.parse_clues (app/services/locate.py).

Regression suite for Fix Round 4, Lane 1: the instruction frame of a find
request ("Reply with its full path.", "somewhere on this computer",
"in its name") must never become clues. On pristine main those frame words
landed in name_tokens, so files were credited for containing the requester's
own phrasing (full, path, reply, its, name, somewhere, computer) instead of
the target's identity.

Built from the actual 13 re-run 3 request texts (L1-L10, R1-R2, N1). For
each request: the target description tokens must survive parsing (in
name_tokens, content_words, or purpose_terms), and none of the frame tokens
{reply, full, path, its, somewhere, name, computer} may appear in
name_tokens or content_words. Kind, year, excluded-year, season, and folder
signals must keep working exactly as before. One related parse check: a
negated word ("not the draft") names what to avoid, so it must not survive
as a positive name clue either; on pristine main it did, and once the frame
tokens are stripped it is the only name clue left and wrongly collects the
all-name-clues scoring bonus, outranking the signed target.

No model, no network, no real Keychain:
  PYTHON_KEYRING_BACKEND=tests.support.filekeyring.FileKeyring .venv/bin/python tests/smoke/run_locate_clue_hygiene_test.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-clue-hygiene-data-")

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


FRAME_TOKENS = {"reply", "full", "path", "its", "somewhere", "name", "computer"}

CASES = [
    ("L1", "Find the file with Sourdough in its name. Reply with its full path.",
     {"sourdough"}),
    ("L2", "Find the PDF from last spring about my apartment lease. Reply with its full path.",
     {"apartment", "lease"}),
    ("L3", "Find the file that mentions chicken broth and carrots simmering. Reply with its full path.",
     {"chicken", "broth", "carrots", "simmering"}),
    ("L4", "Find the file for the tax thing, the wage statement from my employer, somewhere on this computer. Reply with its full path.",
     {"wage", "statement", "employer", "tax"}),
    ("L5", "Find the folder that has my Japan trip stuff. Reply with its full path.",
     {"japan", "trip"}),
    ("L6", "Find my signed consulting contract, not the draft. Reply with its full path.",
     {"signed", "consulting", "contract"}),
    ("L7", "Find invoice 1043. Reply with its full path.",
     {"invoice", "1043"}),
    ("L8", "Find the family reunion photo from 2024. Reply with its full path.",
     {"family", "reunion"}),
    ("L9", "Find the tax return summary for 2025, not 2024. Reply with its full path.",
     {"tax", "return", "summary"}),
    ("L10", "Find the budget spreadsheet for work. Reply with its full path.",
     {"budget", "work"}),
    ("R1", "Find the folder where the Xod source code lives. Reply with its full path.",
     {"xod", "source", "code"}),
    ("R2", "Find the changelog file for the Xod project. Reply with its full path.",
     {"changelog", "xod", "project"}),
    ("N1", "Find the quarterly board deck about zebrafish. Reply with its full path.",
     {"quarterly", "board", "deck", "zebrafish"}),
]


def main():
    from app.services.locate import parse_clues

    by_id = {}
    for case_id, request, targets in CASES:
        clues = parse_clues(request)
        by_id[case_id] = clues
        all_tokens = set(clues.name_tokens) | set(clues.content_words) | set(clues.purpose_terms)
        missing = targets - all_tokens
        check(f"{case_id}: target tokens survive parsing",
              not missing,
              f"missing={sorted(missing)} name={clues.name_tokens} purpose={clues.purpose_terms}")
        leaked_name = FRAME_TOKENS & set(clues.name_tokens)
        check(f"{case_id}: no frame tokens in name_tokens",
              not leaked_name,
              f"leaked={sorted(leaked_name)} name={clues.name_tokens}")
        leaked_content = FRAME_TOKENS & set(clues.content_words)
        check(f"{case_id}: no frame tokens in content_words",
              not leaked_content,
              f"leaked={sorted(leaked_content)} content={clues.content_words}")

    c = by_id["L2"]
    check("L2 preserved: kind is pdf and a season window is set",
          c.kind == "pdf" and c.date_window is not None,
          f"kind={c.kind} window={c.date_window}")
    c = by_id["L5"]
    check("L5 preserved: folder request still wants a folder",
          c.want_folder and c.kind == "folder",
          f"want_folder={c.want_folder} kind={c.kind}")
    c = by_id["L7"]
    check("L7 preserved: invoice number stays a name token, not a year",
          "1043" in c.name_tokens and c.year is None,
          f"name={c.name_tokens} year={c.year}")
    c = by_id["L8"]
    check("L8 preserved: photo kind and year 2024",
          c.kind == "image" and c.year == 2024,
          f"kind={c.kind} year={c.year}")
    c = by_id["L9"]
    check("L9 preserved: year 2025 with 2024 excluded",
          c.year == 2025 and c.excluded_years == [2024],
          f"year={c.year} excluded={c.excluded_years}")
    c = by_id["L6"]
    check("L6 preserved: the negated decoy word is not a positive name clue",
          "draft" not in c.name_tokens,
          f"name={c.name_tokens}")
    c = by_id["L10"]
    check("L10 preserved: spreadsheet kind",
          c.kind == "spreadsheet",
          f"kind={c.kind}")
    c = by_id["R1"]
    check("R1 preserved: folder request still wants a folder",
          c.want_folder and c.kind == "folder",
          f"want_folder={c.want_folder} kind={c.kind}")

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)} passed; failed={failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
