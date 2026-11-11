"""
Task inbox regression test: the verify layer must judge the planner's
own live phrasings, not inferred ones (D6 and D7, found by the AGE-21
Tier 6 re-grade on main 3bd6a99, 2026-11-10).

Both predecessor fixes were verified against inferred or direct-shape
repro texts and the live planner routed around both:

- D6 (container grounding): _path_framed_as_container recognized a
  sentence saying inside/within, or a second full path inside the
  folder. The planner's live Phase 1 texts carry the containment in a
  locative anaphor instead: "Create <folder> if needed. Create
  marker-1.txt, marker-2.txt, and marker-3.txt there with exactly ..."
  (AGE-21 Leg A), "Create <folder> if needed, then create a.txt there
  containing exactly LOAD<n>-A." (AGE-21 load jobs R1/R2/R3), "Create
  <folder> if needed. In it, create marker-1.txt ..." (AGE-7 Leg A).
  The folder claim stayed a file claim and blocked on "the task asked
  for <folder> to be created as a file, but a directory is on disk at
  that path" with the folder and its byte-exact files on disk.

- D7 (declared inputs vs decomposition): the original Leg C request
  declares seed-input.txt as the file to READ, but the executed plan's
  first substep claims seed-input.txt as its own output ("Create
  exactly one file: <...>/seed-input.txt. ..."), so the declared-input
  layer, which never checks a claimed output as an input, never fired:
  the worker invented the seed's contents and the task completed.
  The supplied-input gate judges the conflict before the step runs,
  from the ORIGINAL request's own framing: a path a clause attaches a
  read verb to, and no clause attaches a creation verb to, is a
  supplied input the plan may not create.

Every step text below is verbatim from the AGE-21 / AGE-7 plan records
(~/workspace/xod-eval/age21-evidence, age7-evidence); only the scratch
root prefix is swapped for a local temp root. The honest shapes stay
honest: the D5 squatting shape still blocks with its true reason, a
never-created anaphoric container still blocks, a supplied input that
is present is not gated, and an intermediate the original request
itself creates is never gated.

Run: .venv/bin/python tests/task_inbox/run_file_effects_d6d7_test.py
"""
import asyncio, json, os, re, sys, tempfile, time, uuid
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_d6d7_"))
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


def verify(step_text):
    _n[0] += 1
    return q._verify_file_effects({"id": f"task-d6d7-{_n[0]}", "request": step_text}, time.time())


S = TMP / "scratch"
S.mkdir(parents=True)
_SS = str(S)

# ---- verbatim live texts (root prefix swapped only) ----------------------
A21_P1 = (f"Phase 1: Create {S}/jobA2 if needed. Create marker-1.txt, marker-2.txt, and "
          f"marker-3.txt there with exactly \"T6A-ONE\", \"T6A-TWO\", and \"T6A-THREE\", "
          f"respectively.")
A7_P1 = (f"Phase 1: Create {S}/jobA2 if needed. In it, create marker-1.txt containing exactly "
         f"\"T6A-ONE\", marker-2.txt containing exactly \"T6A-TWO\", and marker-3.txt containing "
         f"exactly \"T6A-THREE\".")
RJ_P1 = {n: f"Phase 1: Create {S}/jobR{n} if needed, then create a.txt there containing "
            f"exactly LOAD{n}-A." for n in (1, 2, 3)}
A_ORIG = (f"Do this file job in exactly these phases, one phase at a time. Work only inside "
          f"the folder {S}/jobA2 (create it if needed). Phase 1: create three files: "
          f"marker-1.txt containing exactly T6A-ONE, marker-2.txt containing exactly T6A-TWO, "
          f"marker-3.txt containing exactly T6A-THREE. Phase 2: create tally.txt containing "
          f"the three lines: marker-1.txt, marker-2.txt, marker-3.txt. Phase 3: read "
          f"marker-2.txt and create echo.txt containing exactly what marker-2.txt contains. "
          f"Phase 4: create done.txt containing exactly T6A-DONE.")
C_ORIG = (f"Do this file job in exactly these phases, one phase at a time. Work only inside "
          f"the folder {S}/jobC. Phase 1: read the file {S}/jobC/seed-input.txt and create "
          f"copy.txt containing exactly its contents. Phase 2: create after.txt containing "
          f"exactly T6C-AFTER.")
C_SUB1 = (f"Create exactly one file: {S}/jobC/seed-input.txt. This is one file of several "
          f"named by a single larger step; the other files are separate substeps and are NOT "
          f"part of this substep. Create only this file; do not create or modify any other "
          f"file.")
C_SUB2 = (f"Create exactly one file: {S}/jobC/copy.txt. This is one file of several named "
          f"by a single larger step; the other files are separate substeps and are NOT part "
          f"of this substep. Create only this file; do not create or modify any other file.")
INTER_ORIG = (f"Phase 1: create {S}/jobI/raw.txt containing exactly RAW-1. Phase 2: read "
              f"{S}/jobI/raw.txt and create {S}/jobI/final.txt containing exactly its contents.")
INTER_STEP1 = f"Phase 1: create {S}/jobI/raw.txt containing exactly RAW-1."
INTER_STEP2 = (f"Phase 2: read {S}/jobI/raw.txt and create {S}/jobI/final.txt containing "
               f"exactly its contents.")


def unit_cases():
    print("D6: the anaphoric container shapes, at the parser and the check")
    jobA = S / "jobA2"
    check("parser: AGE-21 'there' framing reads as a container",
          TQ._path_framed_as_container(A21_P1, jobA))
    check("parser: AGE-7 'In it' framing reads as a container",
          TQ._path_framed_as_container(A7_P1, jobA))
    for n in (1, 2, 3):
        check(f"parser: load job R{n} 'then create a.txt there' reads as a container",
              TQ._path_framed_as_container(RJ_P1[n], S / f"jobR{n}"))
    squat = S / "squat.txt"
    check("parser: a plain file ask is not container framing",
          not TQ._path_framed_as_container(
              f"Create the file at {squat} containing exactly \"TOKEN\".", squat))

    jobA.mkdir(parents=True, exist_ok=True)
    for nm, tok in (("marker-1.txt", b"T6A-ONE"), ("marker-2.txt", b"T6A-TWO"),
                    ("marker-3.txt", b"T6A-THREE")):
        (jobA / nm).write_bytes(tok)
    probs = verify(A21_P1)
    check("check: AGE-21 Leg A Phase 1 verifies clean with its markers on disk",
          probs == [], f"problems={probs}")
    probs = verify(A7_P1)
    check("check: AGE-7 Leg A Phase 1 verifies clean with its markers on disk",
          probs == [], f"problems={probs}")
    for n in (1, 2, 3):
        jobR = S / f"jobR{n}"
        jobR.mkdir(parents=True, exist_ok=True)
        (jobR / "a.txt").write_bytes(f"LOAD{n}-A".encode())
        probs = verify(RJ_P1[n])
        check(f"check: load job R{n} Phase 1 verifies clean with a.txt on disk",
              probs == [], f"problems={probs}")

    squat.mkdir(exist_ok=True)
    probs = verify(f"Create the file at {squat} containing exactly \"TOKEN\".")
    check("check: a directory squatting on a file's path still blocks with the true reason",
          len(probs) == 1 and "directory is on disk" in probs[0] and "not on disk" not in probs[0],
          f"problems={probs}")
    gone = S / "jobGone"
    probs = verify(f"Phase 1: Create {gone} if needed. Create a.txt there containing "
                   f"exactly \"GONE-A\".")
    check("check: an anaphoric container that was never created still blocks truthfully",
          probs == ["the task asked for jobGone to be created, but it is not on disk"],
          f"problems={probs}")

    print("D7: supplied inputs are read from the original request's own framing")
    jobC = S / "jobC"
    jobC.mkdir(parents=True, exist_ok=True)
    seed = jobC / "seed-input.txt"
    if seed.exists():
        seed.unlink()
    check("parser: the Leg C original declares seed-input.txt a supplied input",
          [str(p) for p in TQ._declared_supplied_inputs(C_ORIG)] == [str(seed)],
          f"inputs={TQ._declared_supplied_inputs(C_ORIG)}")
    check("parser: the Leg A original declares no supplied input (its files are plan-made)",
          TQ._declared_supplied_inputs(A_ORIG) == [],
          f"inputs={TQ._declared_supplied_inputs(A_ORIG)}")
    check("parser: an intermediate the original creates is not a supplied input",
          TQ._declared_supplied_inputs(INTER_ORIG) == [],
          f"inputs={TQ._declared_supplied_inputs(INTER_ORIG)}")
    rw = S / "rewrite.txt"
    check("parser: a same-file rewrite is claimed as an output first, not an input",
          TQ._declared_supplied_inputs(
              f"Create {rw} containing exactly hello, after reading {rw}.") == [])
    cfg, out = S / "config.json", S / "out.txt"
    check("parser: optional read phrasing declares no supplied input",
          TQ._declared_supplied_inputs(
              f"Read {cfg} if it exists, then create {out} with defaults.") == [])
    src, dst = S / "source.txt", S / "dest.txt"
    check("parser: a copy-from shape declares its source a supplied input",
          [str(p) for p in TQ._declared_supplied_inputs(
              f"Copy the file {src} to {dst} with exactly the same contents")] == [str(src)],
          f"inputs={TQ._declared_supplied_inputs(f'Copy the file {src} to {dst} with exactly the same contents')}")

    gate = TQ._supplied_input_problems
    check("gate: the creating substep is named before it can run",
          gate(C_ORIG, C_SUB1) == ["the task reads from seed-input.txt, but it is not on disk"],
          f"gate={gate(C_ORIG, C_SUB1)}")
    check("gate: the copy substep, claiming no supplied input, is not gated",
          gate(C_ORIG, C_SUB2) == [], f"gate={gate(C_ORIG, C_SUB2)}")
    check("gate: a step creating a copy's destination is not gated",
          gate(f"Copy the file {src} to {dst} with exactly the same contents",
               f"Create exactly one file: {dst}.") == [])
    check("gate: a step claiming the copy's source as its output is gated while it is absent",
          gate(f"Copy the file {src} to {dst} with exactly the same contents",
               f"Create exactly one file: {src}.") ==
          ["the task reads from source.txt, but it is not on disk"],
          f"gate={gate(f'Copy the file {src} to {dst} with exactly the same contents', f'Create exactly one file: {src}.')}")
    seed.write_bytes(b"T6C-SEED-9182")
    check("gate: silent once the declared input is supplied",
          gate(C_ORIG, C_SUB1) == [], f"gate={gate(C_ORIG, C_SUB1)}")
    seed.unlink()
    check("gate: fires again when the supplied input is absent again",
          gate(C_ORIG, C_SUB1) == ["the task reads from seed-input.txt, but it is not on disk"],
          f"gate={gate(C_ORIG, C_SUB1)}")
    check("gate: the step creating an intermediate is not gated",
          gate(INTER_ORIG, INTER_STEP1) == [], f"gate={gate(INTER_ORIG, INTER_STEP1)}")
    check("gate: the step consuming an intermediate is not gated",
          gate(INTER_ORIG, INTER_STEP2) == [], f"gate={gate(INTER_ORIG, INTER_STEP2)}")


# --------------------------------------------------------------------- fakes
STEP_RE = re.compile(r"YOUR STEP NOW \(step \d+ of \d+\):\n(.+?)\n\nDo only this step", re.S)
EXECUTED = []


def _record_write(path, content: bytes) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    c = activity._conn()
    c.execute("INSERT INTO activity (id, ts, origin, tool, title, detail, ok, undo, undoable) "
              "VALUES (?,?,?,?,?,?,?,?,?)",
              (uuid.uuid4().hex, time.time(), activity.origin.get(), "computer_write_file",
               "Wrote a file", str(path), 1,
               json.dumps([{"kind": "write_file", "path": str(path)}]), 1))
    c.commit()
    c.close()


class FakeResearcher:
    """Does exactly what each live step asks, including inventing the
    seed when a step tells it to create the seed: the gate, not the
    worker's restraint, is what must stop the invention."""

    def __init__(self, *a, **k):
        pass

    async def run_research_stream(self, messages, **k):
        msg = messages[-1]["content"]
        await asyncio.sleep(0.05)
        if "(continue)" in msg or "now exists" in msg:
            yield {"type": "content", "content":
                   "The input file seed-input.txt is present now; I left it exactly as supplied."}
            return
        m = STEP_RE.search(msg)
        step = m.group(1).strip() if m else msg
        jobC = S / "jobC"
        if "Create exactly one file" in step and "seed-input.txt" in step:
            EXECUTED.append("sub1")
            _record_write(jobC / "seed-input.txt", b"T6C-SEED")
            yield {"type": "content", "content": "Created seed-input.txt."}
            return
        if "Create exactly one file" in step and "copy.txt" in step:
            EXECUTED.append("sub2")
            _record_write(jobC / "copy.txt", (jobC / "seed-input.txt").read_bytes())
            yield {"type": "content", "content": "Created copy.txt from seed-input.txt."}
            return
        if "jobA2" in step and "marker-1.txt" in step:
            EXECUTED.append("legA")
            jobA = S / "jobA2"
            for nm, tok in (("marker-1.txt", b"T6A-ONE"), ("marker-2.txt", b"T6A-TWO"),
                            ("marker-3.txt", b"T6A-THREE")):
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


SEED_CONTENT = b"T6C-SEED-9182"


async def case_d7_stepwise(qq):
    print("D7 end to end: the gated substep never runs; supply and answer complete the task")
    jobC = S / "jobC"
    seed, copy = jobC / "seed-input.txt", jobC / "copy.txt"
    for p in (seed, copy):
        if p.exists():
            p.unlink()
    PLANS[C_ORIG] = [C_SUB1, C_SUB2]
    before = list(EXECUTED)
    ack = O.submit(C_ORIG)
    tid = ack["task_id"]
    t = await wait_status(qq, tid, ("waiting", "failed", "done"))
    check("e2e D7: the task waits on the person instead of completing on an invented input",
          t["status"] == "waiting", f"status={t['status']}")
    items, _, _ = O.load_plan(tid)
    s1 = items[0]
    check("e2e D7: step 1 is blocked with the declared-input truth",
          s1["status"] == "blocked" and s1.get("waiting") and
          s1.get("blocker") == "the task reads from seed-input.txt, but it is not on disk",
          json.dumps(s1)[:300])
    check("e2e D7: the creating substep never executed and no seed exists",
          EXECUTED == before and not seed.exists(),
          f"executed={EXECUTED} seed_exists={seed.exists()}")
    check("e2e D7: the copy substep never ran either",
          items[1]["status"] == "pending" and not copy.exists())
    wd = t.get("waiting_detail") or {}
    check("e2e D7: the wait's message names the missing input",
          "seed-input.txt" in (wd.get("message") or ""), str(wd)[:300])

    seed.write_bytes(SEED_CONTENT)
    res = qq.answer(tid, "The file seed-input.txt now exists in the job folder "
                         "with the input content. Please continue.")
    check("e2e D7: the answer was accepted", bool(res.get("success")), str(res))
    t = await wait_status(qq, tid, ("done", "failed"), timeout=90)
    check("e2e D7: the task finishes done after the supply", t["status"] == "done",
          f"status={t['status']}")
    check("e2e D7: the supplied seed was never overwritten",
          seed.read_bytes() == SEED_CONTENT, f"seed={seed.read_bytes()}")
    check("e2e D7: copy.txt is byte-exact against the supplied seed",
          copy.exists() and copy.read_bytes() == SEED_CONTENT,
          f"copy={copy.read_bytes() if copy.exists() else None}")
    items, _, _ = O.load_plan(tid)
    check("e2e D7: every step completed", all(i["status"] == "completed" for i in items),
          json.dumps([i["status"] for i in items]))


async def case_d6_stepwise(qq):
    print("D6 end to end: the verbatim Leg A Phase 1 completes stepwise")
    jobA = S / "jobA2"
    PLANS[A_ORIG] = [A21_P1]
    ack = O.submit(A_ORIG)
    tid = ack["task_id"]
    t = await wait_status(qq, tid, ("done", "failed", "waiting"), timeout=90)
    check("e2e D6: the task finishes done", t["status"] == "done", f"status={t['status']}")
    items, _, _ = O.load_plan(tid)
    check("e2e D6: Phase 1 completed, not blocked on the folder",
          items[0]["status"] == "completed" and not items[0].get("blocker"),
          json.dumps(items[0])[:300])
    check("e2e D6: the markers are byte-exact",
          all((jobA / nm).read_bytes() == tok for nm, tok in
              (("marker-1.txt", b"T6A-ONE"), ("marker-2.txt", b"T6A-TWO"),
               ("marker-3.txt", b"T6A-THREE"))))


async def amain():
    unit_cases()
    qq = TQ.task_queue = TQ.TaskQueue()
    globals()["q"] = qq
    qq.start()
    try:
        await case_d7_stepwise(qq)
        await case_d6_stepwise(qq)
    finally:
        qq.stop()


def main():
    asyncio.run(amain())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
        sys.exit(1)
    print("ALL D6 D7 CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
