"""
D13 (AGE-30): the bare-claims layer mined the composed request's
delivered-inputs reference sections and failed a completed read step
on a false absence. Graded live in AGE-21 re-grade run 5, T1D
(mission-fc9c15c25b, tree b45ec29): step 3 was a pure read ("Read
/abs/proofdir/note.txt and report its contents", no declared
effects); the worker read the file and reported its exact contents;
the file sat on disk exact. The task still closed "Not finished: the
task asked for note.txt to be created, but it is not on disk".

Mechanism: a mission step's composed request appends the delivered
inputs (the mission goal, earlier steps' results, delivered file
contents) inside a fence the product itself labels "reference only;
not this step's instructions". Every claim layer strips that fence
before mining, and layer 2b (the D12 bare-filename resolver) did
not: it mined the goal's creation language ("create a file named
note.txt" inside the folder the goal names first, the scratch root),
resolved the bare name against that container instead of the file's
real folder, and produced a creation claim against a step that
declared no creation at all. The identical battery item passed in
runs 3 and 4, before layer 2b existed.

The fix scopes claim mining to the step's own instruction text:
_bare_inside_claims strips the reference fence like every other
claim layer, and _verify_file_effects consults the container-framing
judgments (_path_framed_as_container, _recorded_write_inside) on the
stripped text as well, so reference prose cannot contribute bare
filenames, anaphors, or path sets to those judgments either.

Pinned here: the graded T1D composition (goal, step texts, and
reports verbatim from age21r5-evidence, scratch prefix swapped)
produces no claim and no gate problem for the read step; a genuinely
absent read target still fails as a missing input; and the D12
behavior is untouched (a bare creation claim in a step's OWN text
still resolves against its named container and still blocks when
the file is absent, fence or no fence).

Run: .venv/bin/python tests/task_inbox/run_file_effects_d13_test.py
"""
import os, sys, tempfile, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_d13_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.task_queue as TQ
from app.services.missions import INPUT_FENCE_START, INPUT_FENCE_END

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


GATE = TQ.TaskQueue.__new__(TQ.TaskQueue)


def gate_problems(request, tid="t"):
    return GATE._verify_file_effects({"id": tid, "request": request}, time.time())


# ------------------------------------------------------------ graded texts
SCRATCH = TMP / "xod-t1-scratch"
PROOFDIR = SCRATCH / "proofdir"
NOTE = PROOFDIR / "note.txt"
GOAL = (f"Create a folder named proofdir inside {SCRATCH}. Inside it, create "
        f"a file named note.txt containing exactly the text between these "
        f"quotes and nothing else: \"baseline proof 7731\" Then read note.txt "
        f"back and report what it says.")
STEP2_RESULT = (f"Created `{NOTE}` (19 bytes). I haven’t read it back to "
                f"verify its contents.")
READ_STEP = f"Read {NOTE} and report its contents."


def composed_read_request():
    """The T1D step 3 task text as assemble_step composes it: the
    step's own rendered request, its contract line, then the fenced
    delivered inputs (goal, step 2's result, the file's contents)."""
    parts = [READ_STEP, "",
             "Declared tools for this step (its capability contract): "
             "computer_read_file", "", INPUT_FENCE_START, "",
             "Mission goal (declared input):", GOAL, "",
             "Result of step 2 (Write proof note) (declared input):",
             STEP2_RESULT, "",
             f"Contents of {NOTE} (declared input):", "baseline proof 7731",
             "", INPUT_FENCE_END]
    return "\n".join(parts)


def main():
    PROOFDIR.mkdir(parents=True)
    NOTE.write_bytes(b"baseline proof 7731")
    req = composed_read_request()

    bare = TQ._bare_inside_claims(req)
    check("bare claims: nothing is mined from the composed request's "
          "reference sections", bare == [], str(bare))

    claims = TQ._claimed_file_effects(req)
    check("path claims: the read step declares no write claim",
          all(c["kind"] != "write" for c in claims), str(claims))

    problems = gate_problems(req, "d13-read")
    check("gate: the completed read step verifies clean",
          problems == [], str(problems))

    # The same step with the note genuinely absent: the honest input
    # problem fires, and no creation claim is invented for it.
    NOTE.unlink()
    problems = gate_problems(req, "d13-read-absent")
    check("gate: an absent read target fails as a missing input",
          any("the task reads from note.txt, but it is not on disk" in p
              for p in problems), str(problems))
    check("gate: no creation claim is invented for the absent read target",
          not any("asked for note.txt to be created" in p for p in problems),
          str(problems))
    NOTE.write_bytes(b"baseline proof 7731")

    # D12 control: a bare creation in the step's OWN text still
    # resolves against its one named container and blocks when absent.
    job = TMP / "jobC"
    job.mkdir()
    (job / "seed-input.txt").write_text("T6C-SEED-5319")
    leg_c = (f"Phase 1: Work only inside {job}. Read {job}/seed-input.txt "
             f"and create copy.txt with exactly the same contents.")
    problems = gate_problems(leg_c, "d13-legc")
    check("control: the D12 bare claim in a step's own text still blocks",
          any("the task asked for copy.txt to be created, but it is not on disk"
              in p for p in problems), str(problems))
    (job / "copy.txt").write_text("T6C-SEED-5319")
    problems = gate_problems(leg_c, "d13-legc-done")
    check("control: the D12 shape verifies when the work is on disk",
          problems == [], str(problems))

    # A bare creation in the step's own text still blocks with a fence
    # attached whose reference text creates other files by bare name.
    job2 = TMP / "jobD"
    job2.mkdir()
    own = (f"Create the folder {job2} if needed. Create summary.txt there "
           f"containing exactly DONE.\n\n{INPUT_FENCE_START}\n\nMission goal "
           f"(declared input):\nCreate a folder named other. Inside it, "
           f"create a file named other.txt containing exactly the text "
           f"\"x\"\n\n{INPUT_FENCE_END}")
    problems = gate_problems(own, "d13-fenced-own")
    check("control: an own-text bare claim blocks with a fence attached",
          any("the task asked for summary.txt to be created, but it is not "
              "on disk" in p for p in problems), str(problems))
    mined = {str(c["path"]) for c in TQ._bare_inside_claims(own)}
    check("control: the fence's bare names are never resolved as claims",
          mined == {str(job2 / "summary.txt")}, str(mined))

    bad = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} D13 checks pass")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
