"""
Task inbox regression test: the container judgment must rest on
evidence and shape, not on an enumerated list of locative phrases
(D8, found by the AGE-21 Tier 6 re-grade run 2 on main 70d362b,
2026-11-10).

The D6 fix taught _path_framed_as_container three prose branches: a
sentence saying inside/within, a second full path inside the folder,
and the anaphors "there" / "in it". Run 2's planner produced a fourth
framing for the identical job shape, "in that folder", which matched
none of them, and Leg A blocked twice on "the task asked for jobA2
to be created as a file, but a directory is on disk at that path"
with the folder and its byte-exact markers on disk. Three graded
runs, four locatives, one shape: the verifier enumerates prose while
the planner generates it, and enumeration loses one graded failure
at a time.

The D8 grounding decides from evidence instead:

- Disk evidence (_path_framed_as_container): the folder is the
  step text's only mentioned path, and every bare filename the
  step's creation sentences name is on disk as a file directly
  inside it. No locative is read at all.
- Recorded evidence (_verify_file_effects): the step's own activity
  rows show a file effect landing inside the folder during the
  step, and the folder is the text's only mentioned path.

The phrase alone proves nothing, and the controls pin that: the
run 2 text against an empty folder, a folder holding only an
unrelated file, or a folder missing one of its named files still
blocks with the squatting reason, and the D5 plain-file ask still
blocks with its true reason. A held-out locative no graded run has
produced ("in this directory") verifies clean with its files on
disk and blocks without them: the shape is recognized, not the
phrase.

Every graded text below is verbatim from the three runs' evidence
(AGE-7: age7-evidence/legLedger.json; run 1: age21-evidence and
this folder's D6D7 sibling suite; run 2: age21r2-evidence/legA-
prekill-manual.json and AGE21-REGRADE2-2026-11-10.md); only the
scratch-root prefix is swapped for a local temp root.

Run: .venv/bin/python tests/task_inbox/run_file_effects_d8_test.py
"""
import asyncio, json, os, re, sys, tempfile, time, uuid
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_d8_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from app.paths import DATA_DIR as _DATA  # noqa: E402
os.environ["XOD_DATASET_DB"] = str(_DATA / "test_runs" / "flywheel_test.db")
import app.services.task_queue as TQ
import app.services.orchestrator as O
import app.agent.researcher as RS
from app.services import activity

TQ.notify = lambda title, msg: None
TQ._phone = lambda task, kind: None

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""),
          flush=True)


q = TQ.TaskQueue()
_n = [0]


def verify(step_text, task_id=None):
    _n[0] += 1
    tid = task_id or f"task-d8-{_n[0]}"
    return q._verify_file_effects({"id": tid, "request": step_text}, time.time())


S = TMP / "scratch"
S.mkdir(parents=True)

# ---- verbatim live texts (root prefix swapped only) ----------------------
A7_P1 = (f"Phase 1: Create {S}/jobA2 if needed. In it, create marker-1.txt containing exactly "
         f"\"T6A-ONE\", marker-2.txt containing exactly \"T6A-TWO\", and marker-3.txt containing "
         f"exactly \"T6A-THREE\".")
A21_P1 = (f"Phase 1: Create {S}/jobA2 if needed. Create marker-1.txt, marker-2.txt, and "
          f"marker-3.txt there with exactly \"T6A-ONE\", \"T6A-TWO\", and \"T6A-THREE\", "
          f"respectively.")
A21R2_P1 = (f"Phase 1: Create {S}/jobA2 if needed. Create marker-1.txt containing exactly "
            f"\"T6A-ONE\", marker-2.txt containing exactly \"T6A-TWO\", and marker-3.txt "
            f"containing exactly \"T6A-THREE\" in that folder.")
HELD_OUT_P1 = (f"Phase 1: Create {S}/jobA2 if needed. Create marker-1.txt containing exactly "
               f"\"T6A-ONE\", marker-2.txt containing exactly \"T6A-TWO\", and marker-3.txt "
               f"containing exactly \"T6A-THREE\" in this directory.")
A_ORIG = (f"Do this file job in exactly these phases, one phase at a time. Work only inside "
          f"the folder {S}/jobA2 (create it if needed). Phase 1: create three files: "
          f"marker-1.txt containing exactly T6A-ONE, marker-2.txt containing exactly T6A-TWO, "
          f"marker-3.txt containing exactly T6A-THREE. Phase 2: create tally.txt containing "
          f"the three lines: marker-1.txt, marker-2.txt, marker-3.txt. Phase 3: read "
          f"marker-2.txt and create echo.txt containing exactly what marker-2.txt contains. "
          f"Phase 4: create done.txt containing exactly T6A-DONE.")
MARKERS = (("marker-1.txt", b"T6A-ONE"), ("marker-2.txt", b"T6A-TWO"),
           ("marker-3.txt", b"T6A-THREE"))
SQUAT_REASON = "directory is on disk at that path"


def lay_markers(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for nm, tok in MARKERS:
        (folder / nm).write_bytes(tok)


def unit_cases():
    print("D8 parser: the evidence branch reads the disk, not the locative")
    jobA = S / "jobA2"
    check("parser: run 2 'in that folder' with no folder on disk does not frame",
          not TQ._path_framed_as_container(A21R2_P1, jobA))
    lay_markers(jobA)
    check("parser: run 2 'in that folder' frames on the markers inside it",
          TQ._path_framed_as_container(A21R2_P1, jobA))
    check("parser: held-out 'in this directory' frames on the same evidence",
          TQ._path_framed_as_container(HELD_OUT_P1, jobA))
    empty = S / "jobEmpty"
    empty.mkdir(parents=True, exist_ok=True)
    empty_text = A21R2_P1.replace(str(jobA), str(empty))
    check("parser: the run 2 phrase against an EMPTY folder does not frame",
          not TQ._path_framed_as_container(empty_text, empty))
    unrel = S / "jobUnrelated"
    unrel.mkdir(parents=True, exist_ok=True)
    (unrel / "unrelated.txt").write_bytes(b"not a marker")
    unrel_text = A21R2_P1.replace(str(jobA), str(unrel))
    check("parser: the run 2 phrase against unrelated contents does not frame",
          not TQ._path_framed_as_container(unrel_text, unrel))
    squat = S / "squat.txt"
    check("parser: a plain file ask is not container framing",
          not TQ._path_framed_as_container(
              f"Create the file at {squat} containing exactly \"TOKEN\".", squat))

    print("D8 check: the harvested corpus, verbatim, against byte-exact disks")
    for name, text in (("AGE-7 'In it'", A7_P1), ("run 1 'there'", A21_P1),
                       ("run 2 'in that folder'", A21R2_P1),
                       ("held-out 'in this directory'", HELD_OUT_P1)):
        probs = verify(text)
        check(f"check: Leg A {name} verifies clean with its markers on disk",
              probs == [], f"problems={probs}")
    for n in (1, 2, 3):
        jobR = S / f"jobR{n}"
        jobR.mkdir(parents=True, exist_ok=True)
        (jobR / "a.txt").write_bytes(f"LOAD{n}-A".encode())
        probs = verify(f"Phase 1: Create {jobR} if needed, then create a.txt there "
                       f"containing exactly LOAD{n}-A.")
        check(f"check: run 1 load R{n} verifies clean", probs == [], f"problems={probs}")
    jobR1 = S / "jobR1"
    probs = verify(f"Phase 1: Create {jobR1} if needed, and create a.txt inside it "
                   f"containing exactly \"LOAD1-A\".")
    check("check: run 2 load R1 ('inside it') verifies clean", probs == [],
          f"problems={probs}")
    jobR3 = S / "jobR3"
    probs = verify(f"Phase 1: Create {jobR3} if needed, then create a.txt in it "
                   f"containing exactly \"LOAD3-A\".")
    check("check: run 2 load R3 ('in it') verifies clean", probs == [], f"problems={probs}")
    jobR2 = S / "jobR2"
    probs = verify(f"Phase 1: Create {jobR2} if needed, then create {jobR2}/a.txt "
                   f"containing exactly \"LOAD2-A\".")
    check("check: run 2 load R2 (inner full path) verifies clean", probs == [],
          f"problems={probs}")
    jobB = S / "jobB"
    jobB.mkdir(parents=True, exist_ok=True)
    (jobB / "part-1.txt").write_bytes(b"T6B-PART1")
    probs = verify(f"Phase 1: Create {jobB} if needed, then create {jobB}/part-1.txt "
                   f"containing exactly 'T6B-PART1'.")
    check("check: run 2 Leg B Phase 1 (full paths) verifies clean", probs == [],
          f"problems={probs}")

    print("D8 check: the phrase alone proves nothing (negative controls)")
    probs = verify(empty_text)
    check("check: run 2 text, folder EMPTY, still blocks with the squatting reason",
          len(probs) == 1 and SQUAT_REASON in probs[0], f"problems={probs}")
    probs = verify(unrel_text)
    check("check: run 2 text, folder holds only an unrelated file, still blocks",
          len(probs) == 1 and SQUAT_REASON in probs[0], f"problems={probs}")
    partial = S / "jobPartial"
    partial.mkdir(parents=True, exist_ok=True)
    (partial / "marker-1.txt").write_bytes(b"T6A-ONE")
    (partial / "marker-2.txt").write_bytes(b"T6A-TWO")
    probs = verify(A21R2_P1.replace(str(jobA), str(partial)))
    check("check: run 2 text, only 2 of 3 named markers inside, still blocks",
          len(probs) == 1 and SQUAT_REASON in probs[0], f"problems={probs}")
    squat.mkdir(parents=True, exist_ok=True)
    probs = verify(f"Create the file at {squat} containing exactly \"TOKEN\".")
    check("check: the D5 squatting shape still blocks with its true reason",
          len(probs) == 1 and SQUAT_REASON in probs[0] and "not on disk" not in probs[0],
          f"problems={probs}")
    gone = S / "jobGone"
    probs = verify(f"Phase 1: Create {gone} if needed. Create a.txt there containing "
                   f"exactly \"GONE-A\".")
    check("check: a never-created container still blocks truthfully",
          probs == ["the task asked for jobGone to be created, but it is not on disk"],
          f"problems={probs}")

    print("D8 check: recorded effects are evidence too")
    jobW = S / "jobW"
    jobW.mkdir(parents=True, exist_ok=True)
    (jobW / "README").write_bytes(b"project notes")
    w_text = (f"Phase 1: Create {jobW} if needed. Create README in that folder "
              f"containing the project notes.")
    probs = verify(w_text)
    check("check: extensionless bare name with no recorded op still blocks",
          len(probs) == 1 and SQUAT_REASON in probs[0], f"problems={probs}")
    tid = "task-d8-recorded"
    c = activity._conn()
    c.execute("INSERT INTO activity (id, ts, origin, tool, title, detail, ok, undo, "
              "undoable) VALUES (?,?,?,?,?,?,?,?,?)",
              (uuid.uuid4().hex, time.time(), f"task:{tid}", "computer_write_file",
               "Wrote a file", str(jobW / "README"), 1,
               json.dumps([{"kind": "write_file", "path": str(jobW / "README")}]), 1))
    c.commit()
    c.close()
    probs = verify(w_text, task_id=tid)
    check("check: a recorded write inside the folder satisfies its claim",
          probs == [], f"problems={probs}")
    jobX = S / "jobX"
    jobX.mkdir(parents=True, exist_ok=True)
    probs = verify(f"Phase 1: Create {jobX} if needed. Create README in that folder "
                   f"containing the project notes.", task_id=tid)
    check("check: a recorded write inside a DIFFERENT folder does not satisfy",
          len(probs) == 1 and SQUAT_REASON in probs[0], f"problems={probs}")


# --------------------------------------------------------------------- fakes
STEP_RE = re.compile(r"YOUR STEP NOW \(step \d+ of \d+\):\n(.+?)\n\nDo only this step", re.S)


def _record_write(path, content: bytes) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    c = activity._conn()
    c.execute("INSERT INTO activity (id, ts, origin, tool, title, detail, ok, undo, "
              "undoable) VALUES (?,?,?,?,?,?,?,?,?)",
              (uuid.uuid4().hex, time.time(), activity.origin.get(), "computer_write_file",
               "Wrote a file", str(path), 1,
               json.dumps([{"kind": "write_file", "path": str(path)}]), 1))
    c.commit()
    c.close()


class FakeResearcher:
    """Does exactly what the live step asks: makes the folder and its
    three markers, byte-exact."""

    def __init__(self, *a, **k):
        pass

    async def run_research_stream(self, messages, **k):
        msg = messages[-1]["content"]
        await asyncio.sleep(0.05)
        m = STEP_RE.search(msg)
        step = m.group(1).strip() if m else msg
        if "jobA2" in step and "marker-1.txt" in step:
            jobA = S / "jobA2"
            for nm, tok in MARKERS:
                _record_write(jobA / nm, tok)
            yield {"type": "content", "content": "Created the folder and its three markers."}
            return
        yield {"type": "content", "content": f"Did: {step[:80]}"}


RS.AgenticResearcher = FakeResearcher
PLANS = {}


async def fake_planner(request):
    return PLANS[request]


O.planner = fake_planner


async def wait_status(qq, tid, statuses, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = qq.get(tid)
        if t["status"] in statuses:
            return t
        await asyncio.sleep(0.05)
    raise TimeoutError(f"{tid} stuck at {qq.get(tid)['status']}")


async def case_d8_stepwise(qq):
    print("D8 end to end: the verbatim run 2 Leg A Phase 1 completes stepwise")
    jobA = S / "jobA2"
    PLANS[A_ORIG] = [A21R2_P1]
    ack = O.submit(A_ORIG)
    tid = ack["task_id"]
    t = await wait_status(qq, tid, ("done", "failed", "waiting"), timeout=90)
    check("e2e D8: the task finishes done", t["status"] == "done", f"status={t['status']}")
    items, _, _ = O.load_plan(tid)
    check("e2e D8: Phase 1 completed, not blocked on the folder",
          items[0]["status"] == "completed" and not items[0].get("blocker"),
          json.dumps(items[0])[:300])
    check("e2e D8: the markers are byte-exact",
          all((jobA / nm).read_bytes() == tok for nm, tok in MARKERS))


async def amain():
    unit_cases()
    qq = TQ.task_queue = TQ.TaskQueue()
    globals()["q"] = qq
    qq.start()
    try:
        await case_d8_stepwise(qq)
    finally:
        qq.stop()


def main():
    asyncio.run(amain())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
        sys.exit(1)
    print("ALL D8 CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
