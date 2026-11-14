"""AGE-39 worker side (T2C, three Tier 2 grades running): the step 1
worker ("Find the matching file") called computer_read_file on the
inbox directory, then computer_list_directory, and then stopped,
reporting verbatim that it could not identify the matching file
because "the available listing does not include the files' first
lines". The AGE-39 verdict fix (5700609) makes that admission fail
honestly, but the worker's underlying problem stands: the listing
carried names and sizes only, so the one fact a discovery step turns
on, each candidate file's first line, was never in front of it, and
nothing in the tool surface pushed it to read the candidates.

The fix: computer_list_directory's entries for text files now carry
first_line, the file's actual first line (bounded read, bounded
length; binary and empty files get no preview), and the tool
descriptions say so: a file can be identified by its opening content
from the listing itself, and a report of non-identification is not
acceptable while unread candidates remain.

Pinned here: the graded T2C inbox shape yields the discovery from
the listing alone (directive.txt's first_line is exactly
"CODE: ZULU-9", the decoys carry their own first lines, exactly one
entry matches the 'CODE:' criterion); held-out shapes (a different
prefix in a different folder, a .md and an extensionless file) behave
the same; binary content, an empty file, a file whose first line is
very long, folders, hidden files, and the read_file-on-a-directory
error all behave as before.

Run: .venv/bin/python tests/brain/run_discovery_listing_test.py
"""
import os, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_discovery_listing_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

from app.services.system_control import system_control_service as sc  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def by_name(listing):
    return {f["name"]: f for f in listing["files"]}


# ---- the graded T2C inbox shape -------------------------------------------
S = TMP / "xod-t2-scratch"
(S / "inbox").mkdir(parents=True, exist_ok=True)
(S / "inbox" / "directive.txt").write_text("CODE: ZULU-9\nDirective body.\n")
(S / "inbox" / "note-morning.txt").write_text("Morning note.\n")
(S / "inbox" / "memo-supplies.txt").write_text("Supplies memo.\nMore lines.\n")

print("== the graded T2C inbox: the listing itself makes the discovery ==")
out = sc.list_directory(str(S / "inbox"))
check("listing succeeds", out.get("success") is True, str(out)[:200])
files = by_name(out)
check("all three candidates listed", set(files) == {"directive.txt", "note-morning.txt", "memo-supplies.txt"},
      str(sorted(files)))
check("directive.txt first_line is the planted first line",
      files["directive.txt"].get("first_line") == "CODE: ZULU-9",
      repr(files["directive.txt"].get("first_line")))
check("decoy first lines carried verbatim",
      files["note-morning.txt"].get("first_line") == "Morning note."
      and files["memo-supplies.txt"].get("first_line") == "Supplies memo.")
matches = [n for n, f in files.items() if (f.get("first_line") or "").startswith("CODE:")]
check("exactly one entry matches the 'CODE:' criterion, and it is directive.txt",
      matches == ["directive.txt"], str(matches))
code = files["directive.txt"]["first_line"].split(":", 1)[1].strip()
check("the code value is recoverable from the listing alone", code == "ZULU-9", code)
check("existing telemetry intact (size, extension, modified)",
      files["directive.txt"]["size_bytes"] == len("CODE: ZULU-9\nDirective body.\n")
      and files["directive.txt"]["extension"] == ".txt" and "modified" in files["directive.txt"])

# ---- held-out shapes -------------------------------------------------------
print("== held out: another prefix, other file kinds ==")
H = TMP / "refs"
H.mkdir()
(H / "alpha.md").write_text("REF: KILO-4\nbody\n")
(H / "plain").write_text("just some words\nsecond line\n")
(H / "beta.txt").write_text("nothing here\n")
out = sc.list_directory(str(H))
files = by_name(out)
check("markdown first line carried", files["alpha.md"].get("first_line") == "REF: KILO-4",
      repr(files["alpha.md"].get("first_line")))
check("extensionless text file first line carried", files["plain"].get("first_line") == "just some words",
      repr(files["plain"].get("first_line")))

# ---- controls whose behavior must not move ---------------------------------
print("== controls ==")
B = TMP / "mixed"
B.mkdir()
(B / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)))
(B / "empty.txt").write_bytes(b"")
(B / "long.txt").write_text("L" * 5000 + "\nrest\n")
(B / ".hidden.txt").write_text("CODE: HIDDEN-1\n")
(B / "sub").mkdir()
out = sc.list_directory(str(B))
files = by_name(out)
check("binary file gets no first_line", "first_line" not in files["img.png"], repr(files["img.png"]))
check("empty file gets no first_line and does not break the listing",
      "first_line" not in files["empty.txt"])
check("a very long first line is truncated to the preview bound",
      files["long.txt"].get("first_line") == "L" * 200, str(len(files["long.txt"].get("first_line") or "")))
check("hidden files stay excluded by default", ".hidden.txt" not in files)
check("folders carry no first_line", all("first_line" not in f for f in out["folders"]))
out_h = sc.list_directory(str(B), show_hidden=True)
check("hidden file appears with show_hidden, with its first line",
      by_name(out_h)[".hidden.txt"].get("first_line") == "CODE: HIDDEN-1")
r = sc.read_file(str(S / "inbox"))
check("read_file on a directory still errors, naming the reason",
      r.get("success") is False and "not a regular file" in (r.get("error") or ""), str(r)[:160])
missing = sc.list_directory(str(TMP / "no-such-folder"))
check("a missing directory still errors", missing.get("success") is False)

print(f"\n{len(results) - sum(1 for _, ok in results if not ok)}/{len(results)} checks passed", flush=True)
failed = [n for n, ok in results if not ok]
if failed:
    print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1)
print("ALL DISCOVERY LISTING CHECKS PASSED", flush=True)
