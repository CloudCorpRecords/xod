"""
Task inbox regression test: the file-effect check must not fabricate blockers
(D5, found by the AGE-7 Tier 6 re-grade on shipped v1.1.1, 2026-11-10).

In 5 of 6 stepwise file jobs a step blocked on a premise the disk
contradicted, in two shapes:

- Container shape: a step that named its working folder by absolute path
  and the files inside it by bare names ("Create /abs/jobA2 if needed, then
  create three files inside it: marker-1.txt ...") produced a lone claim
  for the folder, classified as a file claim because no sentence with the
  full path called it a directory or folder. The check then demanded a
  file at that path and reported the folder "not on disk" while it sat
  there with byte-exact phase files inside.
- Content shape: an aggregate ask ("containing exactly five lines, in
  order: "part-1.txt", "part-2.txt", ...") had its expected content read
  as the raw prose enumeration, quotes, commas, "and" and all. The file,
  holding exactly the five names one per line, could never equal that
  string, with or without a trailing newline.

The honest shapes must keep blocking: a genuinely missing declared file
(the Leg C shape), a folder that was never created, and aggregate content
that is actually wrong. This suite pins both directions at the parser, at
the check itself, and end to end through the queue.

Run: .venv/bin/python tests/task_inbox/run_file_effects_d5_test.py
"""
import asyncio, os, sys, tempfile, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
TMP = Path(tempfile.mkdtemp(prefix="xod_file_effects_d5_"))
os.environ["XOD_DATASET_DB"] = str(_DATA / "test_runs" / "flywheel_test.db")
import app.services.task_queue as TQ
import app.services.browser_sandbox as BS
import app.services.activity as ACT
import app.agent.researcher as RS

TQ.DB_PATH, TQ.SHOTS_DIR = TMP / "tasks.db", TMP / "shots"
TQ.notify = lambda title, msg: print(f"   [notification] {title}: {msg}", flush=True)
if hasattr(TQ, "_phone"):
    TQ._phone = lambda task, kind: None


async def _boom():
    raise RuntimeError("Background Chrome failed to start or open remote debugging port.")


BS.browser_sandbox_service.ensure_browser_running = _boom

# An empty activity log: the live workers wrote through shell commands,
# which record no write_file ops, so the recorded-ops layer saw exactly
# this and every problem below comes from the request-claims layer.
import sqlite3
ACT.DB = TMP / "activity.db"
_conn = sqlite3.connect(str(ACT.DB))
_conn.execute("CREATE TABLE IF NOT EXISTS activity (id TEXT PRIMARY KEY, ts REAL, origin TEXT, "
              "tool TEXT, title TEXT, detail TEXT, ok INTEGER, undo TEXT, undoable INTEGER, "
              "undone_at REAL, note TEXT)")
_conn.commit()
_conn.close()

q = TQ.TaskQueue()
_n = [0]


def verify(step_text):
    _n[0] += 1
    return q._verify_file_effects({"id": f"task-d5-{_n[0]}", "request": step_text}, time.time())


S = TMP / "scratch"
S.mkdir(parents=True)


def test_parser_enumeration():
    jobB = S / "jobB"
    text = (f"Phase 6: Create {jobB}/summary-v2.txt containing exactly five lines, in order: "
            f"\"part-1.txt\", \"part-2.txt\", \"part-3.txt\", \"part-4.txt\", and \"part-5.txt\".")
    claims = TQ._claimed_file_effects(text)
    assert len(claims) == 1, claims
    c = claims[0]
    assert c["content_lines"] == ["part-1.txt", "part-2.txt", "part-3.txt",
                                  "part-4.txt", "part-5.txt"], c
    assert c["content"] is None, c
    # an unquoted enumeration reads the same way
    claims = TQ._claimed_file_effects(
        f"Phase 4: Create {jobB}/index.txt containing exactly the three lines: a.txt, b.txt, c.txt.")
    assert len(claims) == 1 and claims[0]["content_lines"] == ["a.txt", "b.txt", "c.txt"], claims
    # a single dictated token is not an enumeration and keeps its literal reading
    claims = TQ._claimed_file_effects(
        f"Create a plain text file at {jobB}/made.txt whose entire contents are exactly this: HELLO 42")
    assert len(claims) == 1 and claims[0]["content"] == "HELLO 42", claims
    assert claims[0]["content_lines"] is None, claims
    # an enumeration of prose lines (items with spaces) is not guessed at:
    # it keeps the literal reading rather than a reconstruction
    claims = TQ._claimed_file_effects(
        f"Create {jobB}/notes.txt containing exactly two lines, in order: \"first do X\", and \"then do Y\".")
    assert len(claims) == 1 and claims[0]["content_lines"] is None, claims
    assert claims[0]["content"] is not None, claims
    print("PASS: line enumerations parse to their lines; literal content is untouched", flush=True)


def test_container_shape():
    jobA = S / "jobA2"
    jobA.mkdir(parents=True)
    (jobA / "marker-1.txt").write_bytes(b"T6A-ONE")
    (jobA / "marker-2.txt").write_bytes(b"T6A-TWO")
    (jobA / "marker-3.txt").write_bytes(b"T6A-THREE")
    text = (f"Phase 1: Create {jobA} if needed, then create three files inside it: "
            f"marker-1.txt containing exactly \"T6A-ONE\", marker-2.txt containing exactly "
            f"\"T6A-TWO\", and marker-3.txt containing exactly \"T6A-THREE\".")
    problems = verify(text)
    assert problems == [], problems
    jobL = S / "jobL1"
    jobL.mkdir()
    (jobL / "a.txt").write_bytes(b"T6L1-A")
    text = (f"Phase 1: Create {jobL} if needed, then create a.txt inside it "
            f"containing exactly \"T6L1-A\".")
    problems = verify(text)
    assert problems == [], problems
    print("PASS: a folder named as the step's container, with its files inside, does not block", flush=True)


def test_container_absent_still_blocks():
    gone = S / "jobGone"
    text = (f"Phase 1: Create {gone} if needed, then create three files inside it: "
            f"marker-1.txt containing exactly \"T6A-ONE\".")
    problems = verify(text)
    assert problems == [f"the task asked for jobGone to be created, but it is not on disk"], problems
    print("PASS: a container that was never created still blocks, truthfully", flush=True)


def test_directory_where_a_file_was_asked_blocks_truthfully():
    squat = S / "squat.txt"
    squat.mkdir()
    problems = verify(f"Create the file at {squat} containing exactly \"TOKEN\".")
    assert len(problems) == 1 and "directory is on disk" in problems[0], problems
    assert "not on disk" not in problems[0], problems
    print("PASS: a directory squatting on a file's path blocks with the true reason", flush=True)


def test_content_shape():
    jobB = S / "jobB"
    jobB.mkdir(exist_ok=True)
    for i in range(1, 6):
        (jobB / f"part-{i}.txt").write_bytes(f"T6B-PART{i}".encode())
    text = (f"Phase 6: Create {jobB}/summary-v2.txt containing exactly five lines, in order: "
            f"\"part-1.txt\", \"part-2.txt\", \"part-3.txt\", \"part-4.txt\", and \"part-5.txt\".")
    # the report's exact bytes: the five names, no trailing newline
    (jobB / "summary-v2.txt").write_bytes(b"part-1.txt\npart-2.txt\npart-3.txt\npart-4.txt\npart-5.txt")
    assert verify(text) == []
    # and with a trailing newline: the other natural shape of the same ask
    (jobB / "summary-v2.txt").write_bytes(b"part-1.txt\npart-2.txt\npart-3.txt\npart-4.txt\npart-5.txt\n")
    assert verify(text) == []
    jobL3 = S / "jobL3"
    jobL3.mkdir()
    for nm, tok in (("a.txt", "T6L3-A"), ("b.txt", "T6L3-B"), ("c.txt", "T6L3-C")):
        (jobL3 / nm).write_bytes(tok.encode())
    text = (f"Phase 4: Create {jobL3}/index.txt containing exactly three lines, in order: "
            f"\"a.txt\", \"b.txt\", and \"c.txt\".")
    # the report's exact bytes: the three names, with trailing newline
    (jobL3 / "index.txt").write_bytes(b"a.txt\nb.txt\nc.txt\n")
    assert verify(text) == []
    (jobL3 / "index.txt").write_bytes(b"a.txt\nb.txt\nc.txt")
    assert verify(text) == []
    print("PASS: aggregate line content verifies in both trailing-newline shapes", flush=True)


def test_content_wrong_still_blocks():
    jobB = S / "jobB"
    text = (f"Phase 6: Create {jobB}/summary-v2.txt containing exactly five lines, in order: "
            f"\"part-1.txt\", \"part-2.txt\", \"part-3.txt\", \"part-4.txt\", and \"part-5.txt\".")
    (jobB / "summary-v2.txt").write_bytes(b"part-1.txt\npart-2.txt\nWRONG\npart-4.txt\npart-5.txt")
    problems = verify(text)
    assert problems == ["summary-v2.txt does not contain exactly what the task asked for"], problems
    # a missing line is wrong content too
    (jobB / "summary-v2.txt").write_bytes(b"part-1.txt\npart-2.txt\npart-3.txt\npart-4.txt")
    problems = verify(text)
    assert problems == ["summary-v2.txt does not contain exactly what the task asked for"], problems
    print("PASS: aggregate content that is actually wrong still blocks", flush=True)


def test_missing_declared_file_still_blocks():
    jobC = S / "jobC"
    jobC.mkdir()
    text = f"Create exactly one file: {jobC}/seed-input.txt."
    problems = verify(text)
    assert problems == ["the task asked for seed-input.txt to be created, but it is not on disk"], problems
    (jobC / "seed-input.txt").write_bytes(b"T6C-SEED-9182")
    assert verify(text) == []
    print("PASS: the Leg C shape still blocks when the file is missing, and passes when present", flush=True)


def test_single_token_control():
    jobB = S / "jobB"
    text = f"Phase 2: Create {jobB}/part-2.txt containing exactly \"T6B-PART2\"."
    assert (jobB / "part-2.txt").read_bytes() == b"T6B-PART2"
    assert verify(text) == []
    print("PASS: the single-token sibling shape that passed live still passes", flush=True)


class _FolderResearcher:
    """Does the container step's work exactly: folder plus byte-exact markers."""
    base = None

    async def run_research_stream(self, messages):
        d = Path(type(self).base)
        d.mkdir(parents=True, exist_ok=True)
        (d / "marker-1.txt").write_bytes(b"T6A-ONE")
        (d / "marker-2.txt").write_bytes(b"T6A-TWO")
        (d / "marker-3.txt").write_bytes(b"T6A-THREE")
        yield {"type": "content", "content": "Created the folder and its three markers."}


class _LyingResearcher:
    calls = 0

    async def run_research_stream(self, messages):
        type(self).calls += 1
        yield {"type": "content", "content": "All done."}


async def _run_one(request):
    tid = q.create(request)["id"]
    await q._run(tid)
    return q.get(tid)


async def test_end_to_end():
    jobA = S / "jobE2E"
    req = (f"Create {jobA} if needed, then create three files inside it: "
           f"marker-1.txt containing exactly \"T6A-ONE\", marker-2.txt containing exactly "
           f"\"T6A-TWO\", and marker-3.txt containing exactly \"T6A-THREE\".")
    _FolderResearcher.base = jobA
    RS.AgenticResearcher = _FolderResearcher
    t = await _run_one(req)
    print(f"   container-e2e: status={t['status']}", flush=True)
    assert t["status"] == "done", f"expected done, got {t['status']}: {t.get('result')}"
    print("PASS: the container step finishes done end to end when the work is on disk", flush=True)

    jobB = S / "jobE2EAgg"
    jobB.mkdir()
    agg_req = (f"Create {jobB}/summary-v2.txt containing exactly five lines, in order: "
               f"\"part-1.txt\", \"part-2.txt\", \"part-3.txt\", \"part-4.txt\", and \"part-5.txt\".")

    class _AggResearcher:
        async def run_research_stream(self, messages):
            (jobB / "summary-v2.txt").write_bytes(
                b"part-1.txt\npart-2.txt\npart-3.txt\npart-4.txt\npart-5.txt")
            yield {"type": "content", "content": "Summary written."}

    RS.AgenticResearcher = _AggResearcher
    t = await _run_one(agg_req)
    print(f"   aggregate-e2e: status={t['status']}", flush=True)
    assert t["status"] == "done", f"expected done, got {t['status']}: {t.get('result')}"
    print("PASS: the aggregate step finishes done end to end when the lines are on disk", flush=True)

    _LyingResearcher.calls = 0
    RS.AgenticResearcher = _LyingResearcher
    t = await _run_one(f"Create exactly one file: {S}/unmade.txt.")
    print(f"   missing-e2e: status={t['status']} calls={_LyingResearcher.calls}", flush=True)
    assert t["status"] == "failed" and "Not finished" in (t.get("result") or ""), t
    print("PASS: a claimed file that was never made still fails end to end", flush=True)


def main():
    test_parser_enumeration()
    test_container_shape()
    test_container_absent_still_blocks()
    test_directory_where_a_file_was_asked_blocks_truthfully()
    test_content_shape()
    test_content_wrong_still_blocks()
    test_missing_declared_file_still_blocks()
    test_single_token_control()
    asyncio.run(test_end_to_end())
    print("ALL D5 FILE-EFFECT CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
