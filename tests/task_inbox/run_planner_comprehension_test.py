"""
Planner comprehension (AGE-19): decomposition preserves the ask's verbs,
and a step's stored record agrees with its verified state.

Two defects from the AGE-7 re-grade (Tier 6 gate 6 and the leg C
observation), reproduced and pinned here:

  Defect 2, unfaithful decomposition. A step whose words are "read the
  file <seed> and create <copy> containing exactly its contents" was
  split into single-artifact substeps, one titled "Create exactly one
  file: <seed>": the read input became a creation target. The claim
  parser judged the seed by the create verb elsewhere in its sentence.
  The parser now reads verb governance per clause (a mention governed
  by a read verb is an input, not an effect), and the split seam
  refuses read-governed claims again at the point substeps are minted.
  A genuine multi-create step still splits, and "create a copy of the
  template at Y" keeps Y as the create's claim.

  Defect 1, self-contradictory step records. A step whose worker report
  claimed completion ("Phase 1 is complete... verified their contents
  exactly match") while verification blocked it was stored with status
  blocked AND the completion-claiming report as its result. A blocked
  step's record now speaks in the verified voice ("Did not check out:
  <problems>") and keeps the worker's report below, attributed as the
  step's own unverified account. A completed step's result is still the
  worker's report, which verification has then sustained.

  Composition with D1: with the seed no longer misclaimed as a write,
  the declared-input layer sees it, so a leg C step whose seed is
  missing blocks on the true premise ("the task reads from
  seed-input.txt, but it is not on disk"), never on the false one
  ("the task asked for seed-input.txt to be created").

Run: .venv/bin/python tests/task_inbox/run_planner_comprehension_test.py
"""
import asyncio, json, os, re, sys, tempfile, time, uuid
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_planner_comprehension_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.task_queue as TQ
import app.services.orchestrator as O
import app.agent.researcher as RS
from app.services import activity

NOTIFIES = []
TQ.notify = lambda title, msg: NOTIFIES.append((title, msg))
TQ._phone = lambda task, kind: None

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


# ---------------------------------------------------------------- seam checks
def seam_checks():
    print("seam: verb governance in claims and at the split point")
    job = TMP / "seam"
    seed, copy = job / "seed-input.txt", job / "copy.txt"
    leg_c = (f"Phase 1: read the file {seed} and create {copy} "
             f"containing exactly its contents.")
    claims = TQ._claimed_file_effects(leg_c)
    check("leg C: the read input is not a write claim",
          [str(c["path"]) for c in claims if c["kind"] == "write"] == [str(copy)],
          str([(c["kind"], str(c["path"])) for c in claims]))
    check("leg C: the read input is a declared input (D1 sees it)",
          [str(p) for p in TQ._claimed_input_effects(leg_c)] == [str(seed)],
          str(TQ._claimed_input_effects(leg_c)))
    check("leg C: the step does not split (one real artifact)",
          O._multi_file_write_claims({"text": leg_c, "status": "pending"}) == [],
          str(O._multi_file_write_claims({"text": leg_c, "status": "pending"})))

    two = f"Create the file {job}/a.txt and create the file {job}/b.txt."
    multi = [str(c["path"]) for c in O._multi_file_write_claims({"text": two, "status": "pending"})]
    check("two creates: the multi-file split still happens",
          multi == [str(job / "a.txt"), str(job / "b.txt")], str(multi))

    copy_at = f"Create a copy of the template at {job}/out.txt."
    claims = TQ._claimed_file_effects(copy_at)
    check("create a copy at Y: Y keeps its write claim",
          [(c["kind"], str(c["path"])) for c in claims] == [("write", str(job / "out.txt"))],
          str([(c["kind"], str(c["path"])) for c in claims]))

    then_read = (f"Create {job}/out.txt from the data, then read {seed} "
                 f"and check it.")
    claims = TQ._claimed_file_effects(then_read)
    check("create, then read: only the create is claimed",
          [str(c["path"]) for c in claims] == [str(job / "out.txt")],
          str([(c["kind"], str(c["path"])) for c in claims]))
    check("create, then read: the read file is a declared input",
          [str(p) for p in TQ._claimed_input_effects(then_read)] == [str(seed)],
          str(TQ._claimed_input_effects(then_read)))

    read_only = f"Read the file {seed} and summarize it."
    check("read only: no file-effect claims at all",
          TQ._claimed_file_effects(read_only) == [],
          str(TQ._claimed_file_effects(read_only)))

    sub = O._artifact_substep_text(two, {"path": job / "a.txt"})
    sub_claims = TQ._claimed_file_effects(sub or "")
    check("a minted substep still claims exactly its one file",
          sub is not None and len(sub_claims) == 1
          and str(sub_claims[0]["path"]) == str(job / "a.txt"),
          str(sub_claims))


# --------------------------------------------------------------------- fakes
STEP_RE = re.compile(r"YOUR STEP NOW \(step \d+ of \d+\):\n(.+?)\n\nDo only this step", re.S)
READ_CREATE_RE = re.compile(r"read the file (\S+) and create (\S+)")
ONE_FILE_RE = re.compile(r"Create exactly one file: (\S+)")
WRITE_RE = re.compile(r"Create the file (\S+) containing exactly this: (\S+)")
JOB = {}


def _record_write(path, content: bytes) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    c = activity._conn()
    c.execute("INSERT INTO activity (id, ts, origin, tool, title, detail, ok, undo, undoable) VALUES (?,?,?,?,?,?,?,?,?)",
              (uuid.uuid4().hex, time.time(), activity.origin.get(), "computer_write_file", "Wrote a file",
               str(path), 1, json.dumps([{"kind": "write_file", "path": str(path)}]), 1))
    c.commit()
    c.close()


class FakeResearcher:
    mode = "honest"   # "honest" or "lie" (claims completion, writes wrong bytes)

    def __init__(self, *a, **k):
        pass

    async def run_research_stream(self, messages, **k):
        msg = messages[-1]["content"]
        await asyncio.sleep(0.05)
        if "(continue)" in msg:
            yield {"type": "content", "content": "Continued."}
            return
        m = STEP_RE.search(msg)
        step = m.group(1).strip() if m else msg
        if type(self).mode == "lie":
            w = WRITE_RE.search(step)
            if w:
                _record_write(w.group(1), b"WRONG-CONTENTS")
                yield {"type": "content", "content":
                       "Phase 1 is complete. I created the file and verified "
                       "its contents exactly match what was asked."}
                return
        rc = READ_CREATE_RE.search(step)
        if rc:
            seed, copy = rc.group(1), rc.group(2)
            if Path(seed).exists():
                _record_write(copy, Path(seed).read_bytes())
                yield {"type": "content", "content":
                       f"Read {seed} and created {copy} containing exactly its contents."}
            else:
                yield {"type": "content", "content":
                       f"I couldn't read the file {seed} because it does not exist, "
                       "so there is nothing to copy."}
            return
        one = ONE_FILE_RE.search(step)
        if one:
            target = Path(one.group(1))
            seed = JOB.get("seed")
            if seed and target != Path(seed) and Path(seed).exists():
                _record_write(target, Path(seed).read_bytes())
            elif target.exists():
                _record_write(target, target.read_bytes())
            else:
                _record_write(target, b"")
            yield {"type": "content", "content": f"Created {target}."}
            return
        w = WRITE_RE.search(step)
        if w:
            _record_write(w.group(1), w.group(2).encode())
            yield {"type": "content", "content": f"Wrote {w.group(1)}."}
            return
        yield {"type": "content", "content": f"Did: {step[:80]}"}


RS.AgenticResearcher = FakeResearcher


async def fake_planner(request):
    return [l.strip("- ").strip() for l in request.split("\n") if l.strip().startswith("- ")]


O.planner = fake_planner


def request_for(*steps):
    return "Do these steps:\n" + "\n".join(f"- {s}" for s in steps)


async def wait_status(q, tid, statuses, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = q.get(tid)
        if t["status"] in statuses:
            return t
        await asyncio.sleep(0.05)
    raise TimeoutError(f"{tid} stuck at {q.get(tid)['status']}")


SEED_CONTENT = b"T6C-SEED-9182"


async def case_leg_c_faithful(q):
    print("e2e: leg C phase runs as one faithful step")
    d = TMP / "work" / f"jobc-{uuid.uuid4().hex[:4]}"
    d.mkdir(parents=True)
    seed, copy = d / "seed-input.txt", d / "copy.txt"
    seed.write_bytes(SEED_CONTENT)
    JOB["seed"] = seed
    mtime0 = seed.stat().st_mtime_ns
    FakeResearcher.mode = "honest"
    text = f"Phase 1: read the file {seed} and create {copy} containing exactly its contents."
    ack = O.submit(request_for(text))
    tid = ack["task_id"]
    t = await wait_status(q, tid, ("done", "failed", "waiting"))
    items, _, _ = O.load_plan(tid)
    check("leg C e2e: the task completes", t["status"] == "done", f"status={t['status']}")
    check("leg C e2e: the plan keeps the one original step (no split)",
          len(items) == 1 and items[0]["text"] == text,
          json.dumps([i["text"][:80] for i in items]))
    check("leg C e2e: no create substep was minted for the read input",
          not any(i["text"].startswith("Create exactly one file:") for i in items),
          json.dumps([i["text"][:80] for i in items]))
    check("leg C e2e: copy.txt is byte-exact against the seed",
          copy.exists() and copy.read_bytes() == SEED_CONTENT)
    check("leg C e2e: the seed was never rewritten",
          seed.read_bytes() == SEED_CONTENT and seed.stat().st_mtime_ns == mtime0)


async def case_leg_c_missing_seed(q):
    print("e2e: leg C with the seed missing blocks on the true premise")
    d = TMP / "work" / f"jobc-{uuid.uuid4().hex[:4]}"
    d.mkdir(parents=True)
    seed, copy = d / "seed-input.txt", d / "copy.txt"
    FakeResearcher.mode = "honest"
    text = f"Phase 1: read the file {seed} and create {copy} containing exactly its contents."
    ack = O.submit(request_for(text))
    tid = ack["task_id"]
    t = await wait_status(q, tid, ("waiting", "failed", "done"))
    items, _, _ = O.load_plan(tid)
    s1 = items[0]
    blocker = s1.get("blocker") or ""
    check("missing seed: the step blocks and the task waits",
          t["status"] == "waiting" and s1["status"] == "blocked",
          f"task={t['status']} step={s1['status']}")
    check("missing seed: the blocker is the D1 input premise",
          "the task reads from" in blocker and "seed-input" in blocker, blocker[:200])
    check("missing seed: the blocker never claims the seed was to be created",
          "asked for seed-input" not in blocker, blocker[:200])
    check("missing seed: the step record speaks in the verified voice",
          (s1.get("result") or "").startswith("Did not check out:"),
          (s1.get("result") or "")[:160])


async def case_blocked_record(q):
    print("e2e: a blocked step's record cannot claim completion")
    d = TMP / "work" / f"joba-{uuid.uuid4().hex[:4]}"
    d.mkdir(parents=True)
    target = d / "phase1.txt"
    FakeResearcher.mode = "lie"
    ack = O.submit(request_for(
        f"Create the file {target} containing exactly this: EXPECTED-TOKEN"))
    tid = ack["task_id"]
    t = await wait_status(q, tid, ("waiting", "failed", "done"))
    items, _, _ = O.load_plan(tid)
    s1 = items[0]
    result = s1.get("result") or ""
    check("blocked record: the step is blocked with its reason",
          s1["status"] == "blocked" and "does not contain exactly" in (s1.get("blocker") or ""),
          json.dumps(s1)[:300])
    check("blocked record: the result opens with the verified verdict",
          result.startswith("Did not check out: "), result[:160])
    check("blocked record: the verdict names the same problem as the blocker",
          "does not contain exactly" in result.split("\n\n")[0], result[:200])
    head, _, tail = result.partition("The step's own report (not sustained by verification):")
    check("blocked record: the worker's claim survives only as its attributed report",
          bool(tail) and "Phase 1 is complete" in tail and "Phase 1 is complete" not in head,
          result[:300])


async def case_completed_record(q):
    print("e2e: a completed step keeps its report as its result")
    d = TMP / "work" / f"jobd-{uuid.uuid4().hex[:4]}"
    d.mkdir(parents=True)
    target = d / "made.txt"
    FakeResearcher.mode = "honest"
    ack = O.submit(request_for(
        f"Create the file {target} containing exactly this: HELLO-42"))
    tid = ack["task_id"]
    t = await wait_status(q, tid, ("done", "failed", "waiting"))
    items, _, _ = O.load_plan(tid)
    s1 = items[0]
    check("completed record: the step completed and the task is done",
          t["status"] == "done" and s1["status"] == "completed",
          f"task={t['status']} step={s1['status']}")
    check("completed record: the result is the worker's sustained report",
          (s1.get("result") or "") == f"Wrote {target}.",
          (s1.get("result") or "")[:160])


async def main():
    seam_checks()
    q = TQ.task_queue = TQ.TaskQueue()
    q.start()
    try:
        await case_leg_c_faithful(q)
        await case_leg_c_missing_seed(q)
        await case_blocked_record(q)
        await case_completed_record(q)
    finally:
        q.stop()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    else:
        print("ALL PLANNER COMPREHENSION CHECKS PASSED", flush=True)
    sys.exit(1 if failed else 0)


asyncio.run(main())
