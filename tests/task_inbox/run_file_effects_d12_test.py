"""
D12 (AGE-29): a blocked step, once unblocked by an operator answer,
completed on an unrelated locate result and its declared effect was
never produced. Graded live in AGE-21 re-grade run 4, Leg C
(task-17a7c053b2, tree 88530cf): the step blocked truthfully on the
missing seed; the driver supplied the real seed and answered; the
re-run's only tool call was a locate_files whose request was the
answer text itself, its ask output became the step's stored result,
and the step closed completed with copy.txt never created.

Two mechanisms, both pinned here:

1. Composition (orchestrator): every step run is a fresh agent
   stream whose only context is its message. The normal path hands
   it _step_prompt (the task, the step, its prior actions). The
   resume path SUBSTITUTED the bare answer ("<answer>\n(continue)")
   for that prompt, so the resumed worker had no step to carry out:
   handed the answer text as its whole instruction, it located the
   file the answer mentioned and reported the locate's ask. The
   person's turn is now appended to the full step prompt as a
   marked section: the answer is input data for the step, never
   its instruction. One shape keeps the bare token by design: a
   step paused at a staged browser submit (the mission-gate pause
   marks it gate=True, its work already staged on the page and
   its submit restored to the sandbox on resume) is confirmed,
   not re-instructed; that is the flow the token was built for,
   and the run_orchestrator A11 case pins it. Every other resume,
   answers and approvals alike, carries the full step prompt.

2. Verification (task_queue._verify_file_effects): the judgment did
   run on the re-run and found nothing, because the step's declared
   effect is invisible to the claim parser. The step names its
   folder and its input by absolute path and its output by bare
   filename ("Work only inside /abs/jobC. Read /abs/jobC/seed.txt
   and create copy.txt ..."): the folder's sentences carry no
   governing effect verb and the input is read-governed, so the
   claim set is empty, and once the supplied input existed the one
   live check (input existence) passed. Declared creations named
   by bare filename inside the text's one named container are now
   claims of the same force (layer 2b), silent when the container's
   own claim already blocks the step (the D8 pins keep their one
   reason) and unresolved when the text names no single container.

Texts are the graded run's verbatim records (request, plan step
texts, the worker's blocked report, the driver's answer, the locate
ask that became the step's result), harvested from
age21r4-evidence/legC.json with only the scratch-root prefix swapped
for a local temp root. The end-to-end fake mirrors the graded
worker's observable behavior: given a step it does the step (or
reports honestly why not); given no step at all it can only react
to the text it was handed, which is what the graded worker did.

Run: .venv/bin/python tests/task_inbox/run_file_effects_d12_test.py
"""
import asyncio, json, os, re, sys, tempfile, time, uuid
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_d12_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.task_queue as TQ
import app.services.orchestrator as O
import app.agent.researcher as RS
from app.services import activity

TQ.notify = lambda title, msg: None
TQ._phone = lambda task, kind: None

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


# ------------------------------------------------------------ graded texts
ANSWER = ("The file seed-input.txt now exists in the job folder "
          "with the input content. Please continue.")
ASK_REPORT = ("**Two close matches, too close to call:**\n"
              "1. **Content** - folder, modified 2026-07-19\n"
              "   Path: /Users/reneturcios/Downloads/Unpacked-Folders/uploads_files_7306575_Handyman_UE_5-1/Content\n"
              "   Why: name matches 'content'; the name is exactly the thing described; it is a folder\n"
              "2. **Content** - folder, modified 2026-10-18\n"
              "   Path: /Users/reneturcios/StrikeMobile/Binaries/Mac/StrikeMobile-Mac-Shipping.app/Contents/UE/Engine/Content\n"
              "   Why: name matches 'content'; the name is exactly the thing described; it is a folder\n"
              "Question: Two close matches: Content and Content. Which one did you mean?")
BLOCKED_REPORT = ("I couldn't create `copy.txt`: `seed-input.txt` is missing from `jobC`. "
                  "I checked that no background job was running and retried once, but the "
                  "source was still unavailable.")
SEED_CONTENT = b"T6C-SEED-7741"
SQUAT_REASON = "directory is on disk at that path"


def step1_text(jobC):
    return (f"Phase 1: Work only inside {jobC}. Read {jobC}/seed-input.txt "
            f"and create copy.txt with exactly the same contents.")


def step2_text(jobC):
    return f"Phase 2: Work only inside {jobC}. Create after.txt containing exactly T6C-AFTER."


def request_text(jobC):
    return (f"Do this file job in exactly these phases, one phase at a time. "
            f"Work only inside the folder {jobC}. Phase 1: read the file "
            f"{jobC}/seed-input.txt and create copy.txt containing exactly its "
            f"contents. Phase 2: create after.txt containing exactly T6C-AFTER.")


# ------------------------------------------------------------ unit: the verifier sees the effect
def unit_cases():
    print("D12 unit: the bare-filename layer of _verify_file_effects")
    q = TQ.TaskQueue.__new__(TQ.TaskQueue)

    def verify(text, tid="task-d12-unit"):
        return TQ.TaskQueue._verify_file_effects(q, {"id": tid, "request": text}, time.time())

    jobC = TMP / "unit" / "jobC"
    jobC.mkdir(parents=True)
    seed, copy = jobC / "seed-input.txt", jobC / "copy.txt"
    step = step1_text(jobC)
    check("unit: the claim parser alone still sees no claim for the graded step",
          TQ._claimed_file_effects(step) == [])
    claims = TQ._bare_inside_claims(step)
    check("unit: the bare layer resolves copy.txt against the named folder",
          [str(c["path"]) for c in claims] == [str(copy)], f"claims={claims}")
    probs = verify(step)
    check("unit: seed absent, both truthful problems stand (the block)",
          "the task reads from seed-input.txt, but it is not on disk" in probs
          and "the task asked for copy.txt to be created, but it is not on disk" in probs,
          f"problems={probs}")
    seed.write_bytes(SEED_CONTENT)
    probs = verify(step)
    check("unit: seed present and copy absent, the D12 blind spot is closed",
          probs == ["the task asked for copy.txt to be created, but it is not on disk"],
          f"problems={probs}")
    copy.write_bytes(SEED_CONTENT)
    check("unit: the real work verifies clean", verify(step) == [])

    print("D12 unit: the conservative readings stay silent")
    plain = TMP / "unit" / "plain"
    plain.mkdir(parents=True, exist_ok=True)
    src = plain / "in.txt"
    src.write_bytes(b"data")
    check("unit: a bare creation with no named container is not resolved",
          TQ._bare_inside_claims(f"Read {src} and create summary.txt") == [])
    check("unit: that step verifies as before (nothing claimed, nothing objected)",
          verify(f"Read {src} and create summary.txt") == [])
    fa, fb = TMP / "unit" / "fa", TMP / "unit" / "fb"
    fa.mkdir(parents=True, exist_ok=True)
    fb.mkdir(parents=True, exist_ok=True)
    (fb / "in.txt").write_bytes(b"data")
    check("unit: an outside input does not compete with the one working folder",
          [str(c["path"]) for c in TQ._bare_inside_claims(
              f"Work only inside {fa}. Read {fb}/in.txt and create out.txt.")]
          == [str(fa / "out.txt")])
    amb = f"Work only inside {fa}. Work only inside {fb}. Create out.txt."
    check("unit: two candidate containers resolve nothing",
          TQ._bare_inside_claims(amb) == [])
    squat = TMP / "unit" / "squat.txt"
    check("unit: a plain file ask yields no bare claims",
          TQ._bare_inside_claims(f"Create the file at {squat} containing exactly \"TOKEN\".") == [])

    print("D12 unit: the D8 composition (the container's own claim speaks first)")
    jobA = TMP / "unit" / "jobA2"
    jobA.mkdir(parents=True, exist_ok=True)
    a_text = (f"Phase 1: Create {jobA} if needed. Create marker-1.txt, marker-2.txt, "
              f"and marker-3.txt in that folder.")
    probs = verify(a_text)
    check("unit: empty folder, the D8 squat reason remains the single problem",
          len(probs) == 1 and SQUAT_REASON in probs[0], f"problems={probs}")
    for nm, tok in (("marker-1.txt", b"T6A-ONE"), ("marker-2.txt", b"T6A-TWO"),
                    ("marker-3.txt", b"T6A-THREE")):
        (jobA / nm).write_bytes(tok)
    check("unit: the D8 shape with its markers on disk verifies clean",
          verify(a_text) == [])


# ------------------------------------------------------------ unit: the resume composition
def composition_cases():
    print("D12 unit: _resume_section carries the answer as data")
    sec = O._resume_section(ANSWER + "\n(continue)")
    check("composition: the answer is quoted between markers",
          "--- the person's answer ---" in sec and ANSWER in sec
          and "--- end of answer ---" in sec, sec[:200])
    check("composition: the answer is framed as data, not an instruction",
          "not a new instruction" in sec and "does not replace the step" in sec, sec[:200])
    check("composition: no bare continue token is left as the instruction",
          "(continue)" not in sec, sec[-120:])
    ok = O._resume_section("go ahead")
    check("composition: an approval is a go-ahead note on the step, not a bare token",
          "go ahead" in ok and "Carry out the step above" in ok, ok[:200])


# ------------------------------------------------------------ end to end
STEP_RE = re.compile(r"YOUR STEP NOW \(step \d+ of \d+\):\n(.+?)\n\nDo only this step", re.S)
SEED_RE = re.compile(r"[Rr]ead (\S+?seed-input\.txt) and create copy\.txt")
SEEN_MESSAGES = []


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
    """The graded worker's observable behavior: with a step in front
    of it, it does the step or reports honestly why not; handed a
    bare message with no step in it, the only move the text supports
    is the one the graded worker made (the locate ask, no file work)."""

    def __init__(self, *a, **k):
        pass

    async def run_research_stream(self, messages, **k):
        msg = messages[-1]["content"]
        SEEN_MESSAGES.append(msg)
        await asyncio.sleep(0.02)
        m = STEP_RE.search(msg)
        if not m:
            yield {"type": "content", "content": ASK_REPORT}
            return
        step = m.group(1).strip()
        c = SEED_RE.search(step)
        if c:
            seed = Path(c.group(1))
            copy = seed.parent / "copy.txt"
            if seed.exists():
                _record_write(copy, seed.read_bytes())
                yield {"type": "content", "content":
                       "Read the seed and created copy.txt with exactly the same contents."}
            else:
                yield {"type": "content", "content": BLOCKED_REPORT}
            return
        if "after.txt" in step and "T6C-AFTER" in step:
            base = Path(re.search(r"Work only inside (\S+?)\.", step).group(1))
            _record_write(base / "after.txt", b"T6C-AFTER")
            yield {"type": "content", "content": "Created after.txt containing exactly T6C-AFTER."}
            return
        yield {"type": "content", "content": f"Did: {step[:80]}"}


class DeafResearcher:
    """Never does the step's work: honest about the missing seed,
    then returns the locate ask whatever it is handed. Pins the
    judgment layer independent of the worker."""

    def __init__(self, *a, **k):
        pass

    async def run_research_stream(self, messages, **k):
        msg = messages[-1]["content"]
        await asyncio.sleep(0.02)
        m = re.search(r"(\S+?seed-input\.txt)", msg)
        seed = Path(m.group(1)) if m else None
        if seed is not None and not seed.exists():
            yield {"type": "content", "content": BLOCKED_REPORT}
        else:
            yield {"type": "content", "content": ASK_REPORT}


RS.AgenticResearcher = FakeResearcher
PLAN = []


async def fake_planner(request):
    return list(PLAN)


O.planner = fake_planner


async def wait_status(q, tid, statuses, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = q.get(tid)
        if t["status"] in statuses:
            return t
        await asyncio.sleep(0.05)
    raise TimeoutError(f"{tid} stuck at {q.get(tid)['status']}")


def make_case(tag):
    jobC = TMP / "e2e" / tag / "jobC"
    jobC.mkdir(parents=True)
    PLAN[:] = [step1_text(jobC), step2_text(jobC)]
    return jobC


async def case_graded_sequence(q):
    print("D12 e2e: the graded sequence (block, seed supplied, answer)")
    jobC = make_case("case1")
    seed, copy, after = jobC / "seed-input.txt", jobC / "copy.txt", jobC / "after.txt"
    tid = O.submit(request_text(jobC))["task_id"]
    t = await wait_status(q, tid, ("waiting", "failed", "done"))
    check("e2e block: the task waits on the person", t["status"] == "waiting", t["status"])
    items, _, _ = O.load_plan(tid)
    s1 = items[0]
    check("e2e block: step 1 blocked on the missing input, verified voice",
          s1["status"] == "blocked" and "seed-input.txt" in (s1.get("blocker") or "")
          and (s1.get("result") or "").startswith("Did not check out:"),
          json.dumps(s1)[:260])
    check("e2e block: nothing was produced", not seed.exists() and not copy.exists())
    n_msgs = len(SEEN_MESSAGES)
    seed.write_bytes(SEED_CONTENT)
    res = q.answer(tid, ANSWER)
    check("e2e: the answer was accepted", bool(res.get("success")), str(res))
    t = await wait_status(q, tid, ("done", "failed", "waiting"), timeout=60)
    check("e2e recovery: the task finishes done", t["status"] == "done", t["status"])
    check("e2e recovery: copy.txt is byte-exact against the supplied seed",
          copy.exists() and copy.read_bytes() == SEED_CONTENT)
    check("e2e recovery: phase 2 landed byte-exact",
          after.exists() and after.read_bytes() == b"T6C-AFTER")
    items, _, _ = O.load_plan(tid)
    s1 = items[0]
    check("e2e recovery: step 1's stored result is its own work, not the locate ask",
          "Two close matches" not in (s1.get("result") or ""), (s1.get("result") or "")[:120])
    resumed = [m for m in SEEN_MESSAGES[n_msgs:] if "Phase 1" in m]
    check("e2e composition: the resumed run received the step itself",
          any("YOUR STEP NOW" in m and "create copy.txt" in m for m in resumed),
          f"resumed messages={len(resumed)}")
    check("e2e composition: and the answer as a marked data section",
          any("--- the person's answer ---" in m and ANSWER in m for m in resumed))


async def case_answer_without_seed(q):
    print("D12 e2e: negative control (answer supplied, seed never created)")
    jobC = make_case("case2")
    seed, copy = jobC / "seed-input.txt", jobC / "copy.txt"
    tid = O.submit(request_text(jobC))["task_id"]
    t = await wait_status(q, tid, ("waiting", "failed", "done"))
    check("control block: the task waits on the person", t["status"] == "waiting", t["status"])
    res = q.answer(tid, ANSWER)
    check("control: the answer was accepted", bool(res.get("success")), str(res))
    t = await wait_status(q, tid, ("waiting", "done", "failed"), timeout=60)
    items, _, _ = O.load_plan(tid)
    s1 = items[0]
    check("control: the task does not finish done on an unproduced effect",
          t["status"] == "waiting" and s1["status"] == "blocked",
          f"task={t['status']} step={s1['status']}")
    check("control: the renewed block names the still-missing input",
          "seed-input.txt" in (s1.get("blocker") or ""), s1.get("blocker") or "")
    check("control: copy.txt was never invented", not copy.exists())
    await q.cancel(tid)


async def case_deaf_worker(q):
    print("D12 e2e: the judgment layer alone (the resumed worker does no work)")
    jobC = make_case("case3")
    seed, copy = jobC / "seed-input.txt", jobC / "copy.txt"
    RS.AgenticResearcher = DeafResearcher
    try:
        tid = O.submit(request_text(jobC))["task_id"]
        t = await wait_status(q, tid, ("waiting", "failed", "done"))
        check("deaf block: the task waits on the person", t["status"] == "waiting", t["status"])
        seed.write_bytes(SEED_CONTENT)
        res = q.answer(tid, ANSWER)
        check("deaf: the answer was accepted", bool(res.get("success")), str(res))
        t = await wait_status(q, tid, ("waiting", "done", "failed"), timeout=60)
        items, _, _ = O.load_plan(tid)
        s1 = items[0]
        check("deaf: the task does not finish done", t["status"] == "waiting", t["status"])
        check("deaf: step 1 is blocked again, on the unproduced effect",
              s1["status"] == "blocked" and "copy.txt" in (s1.get("blocker") or ""),
              json.dumps(s1)[:260])
        check("deaf: copy.txt was never created", not copy.exists())
        await q.cancel(tid)
    finally:
        RS.AgenticResearcher = FakeResearcher


async def main():
    unit_cases()
    composition_cases()
    q = TQ.task_queue = TQ.TaskQueue()
    q.start()
    try:
        await case_graded_sequence(q)
        await case_answer_without_seed(q)
        await case_deaf_worker(q)
    finally:
        q.stop()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1 if failed else 0)


asyncio.run(main())
