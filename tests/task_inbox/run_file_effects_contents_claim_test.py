"""
Declared contents are not creation claims (AGE-87): the bare-claims
layer mined a token out of the exact contents a task dictated for a
file and failed a completed task on that token's absence. Graded live
in the 2026-11-17 MacBook dogfood of the installed 1.1.11 build
(task-947d6d2f83, evidence MACBOOK-XOD-TEST-2026-11-17): the request
asked for a folder, a file named dogfood.txt inside it whose entire
contents are one dictated line ("Xod 1.1.11 dogfood test
2026-11-17."), and a read-back. The agent created the file
byte-exact (35 bytes, verified independently over SSH), but the
verifier twice concluded "the task asked for 1.1.11 to be created,
but it is not on disk", forced a retry, and closed the task failed.

Mechanism: the dictated line rides in the same creation sentence as
the file's own name ("create a plain text file named dogfood.txt
whose entire contents are exactly this single line: Xod 1.1.11
..."), and _bare_creation_filenames mined every filename-shaped
token in that sentence, so the version token inside the declared
contents ("1.1.11", a dotted token with a numeric suffix) came out
as a bare creation filename. _bare_inside_claims then resolved it
against the one named container, the folder, and layer 2b of the
gate demanded a file named 1.1.11 inside it. Nothing named 1.1.11
was ever asked for; the string appears only inside the requested
file contents, and the claim parser's own exact-content reading
(_EXACT_CONTENT_RE) already treats everything after the declaration's
colon as content, not instruction.

The fix holds the bare miner to that same reading: within a
creation sentence, the body of an exact-content declaration (the
text after the declaration's colon, the span _EXACT_CONTENT_RE
captures as the content) is not mined for bare filenames. Names the
sentence gives before the declaration, the file's own name above
all, are mined exactly as before.

Pinned here: the dogfood-shaped request (scratch prefix swapped)
mines only dogfood.txt, resolves only that one claim, and verifies
clean with the file on disk byte-exact; a genuinely absent declared
file still blocks with its true reason; and a genuine separate
creation target named in its own sentence still blocks when absent
and verifies when present, so no real check is loosened.

Run: .venv/bin/python tests/task_inbox/run_file_effects_contents_claim_test.py
"""
import os, sys, tempfile, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_contents_claim_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.task_queue as TQ

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


GATE = TQ.TaskQueue.__new__(TQ.TaskQueue)


def gate_problems(request, tid="t"):
    return GATE._verify_file_effects({"id": tid, "request": request}, time.time())


# ------------------------------------------------------------ graded text
SCRATCH = TMP / "xod-dogfood-2026-11-17"
DOGFOOD = SCRATCH / "dogfood.txt"
LINE = b"Xod 1.1.11 dogfood test 2026-11-17."
REQUEST = (f"Create a folder at {SCRATCH} if it does not already exist. "
           f"Inside it, create a plain text file named dogfood.txt whose "
           f"entire contents are exactly this single line: Xod 1.1.11 "
           f"dogfood test 2026-11-17. Then read the file back and confirm "
           f"its contents.")


def main():
    SCRATCH.mkdir(parents=True)
    DOGFOOD.write_bytes(LINE)

    bare = TQ._bare_creation_filenames(REQUEST)
    check("bare filenames: only the file's own name is mined, not the "
          "version token inside its declared contents",
          bare == ["dogfood.txt"], str(bare))

    claims = TQ._bare_inside_claims(REQUEST)
    check("bare claims: the one claim resolves to dogfood.txt in the folder",
          [str(c["path"]) for c in claims] == [str(DOGFOOD)], str(claims))

    problems = gate_problems(REQUEST, "contents-claim-dogfood")
    check("gate: the completed dogfood task verifies clean",
          problems == [], str(problems))

    # Control: the declared file genuinely absent still blocks, with
    # its own true reason and no other.
    DOGFOOD.unlink()
    problems = gate_problems(REQUEST, "contents-claim-absent")
    check("control: a genuinely absent declared file still blocks",
          problems == ["the task asked for dogfood.txt to be created, "
                       "but it is not on disk"], str(problems))
    DOGFOOD.write_bytes(LINE)

    # Control: a genuine separate creation target, named in its own
    # creation sentence after the dogfood sentence, still blocks when
    # absent (and the contents token still does not).
    two = (REQUEST + f" Also create a file named release-notes.txt "
           f"inside {SCRATCH} containing exactly DONE.")
    problems = gate_problems(two, "contents-claim-two-absent")
    check("control: a genuine separate creation target still blocks "
          "when absent",
          problems == ["the task asked for release-notes.txt to be "
                       "created, but it is not on disk"], str(problems))
    (SCRATCH / "release-notes.txt").write_bytes(b"DONE")
    problems = gate_problems(two, "contents-claim-two-done")
    check("control: the two-target shape verifies when both files are "
          "on disk", problems == [], str(problems))

    bad = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} contents-claim checks pass")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
