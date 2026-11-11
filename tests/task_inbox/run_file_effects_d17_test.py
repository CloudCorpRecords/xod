"""
Task inbox regression test: a file claim's kind is judged by the
wording that governs that claim's own path (D17, found by the AGE-21
Tier 6 re-grade run 7 on main bc3c689, 2026-11-11).

Run 7's Leg B Phase 1 step text (verbatim in both plan v1 and v2):
"Work only inside <scratch>/jobB throughout; create the folder if
needed, then create <scratch>/jobB/part-1.txt containing exactly
"T6B-PART1"." The step's own tool calls wrote part-1.txt and verified
its exact bytes twice, about 20 seconds before the step was judged
blocked with "the task asked for part-1.txt to be created, but it is
not on disk", while the file sat exact on disk.

Mechanism: _claimed_file_effects assigned a claim's target kind from
any directory or folder word anywhere in a sentence naming the
claim's path. The word "folder" in this sentence governs the
container ("create the folder if needed"), not the file, yet it made
the part-1.txt claim a directory claim. The verify layer's
directory-target branch then demanded is_dir() of the file and
reported it absent; the file-target branches below it, the ones that
consult the disk, the container framing, and the step's recorded
effects, never ran for that claim. (The live step wrote through
shell commands, which record no write_file ops, and the
recorded-effects branch additionally requires the folder to be the
text's only mentioned path, which a two-path step never satisfies:
neither D8 branch could have sustained this step as the claim stood.)

The fix is the mission layer's per-effect kind rule (D9,
missions._effect_is_directory) carried into the stepwise claim
parser: a target whose basename carries a file suffix is a file
whatever the surrounding prose says, and an extensionless target is
a directory only when the directory wording governs that path
itself ("directory at <path>", "<path> folder"). The container path
in the graded text then drops out as a container-only prefix of the
file claim (the H6 rule), and the file claim is judged by the
file-target layers against the disk.

Controls pin the other directions: the same text with part-1.txt
genuinely absent still blocks naming part-1.txt; a genuine directory
creation still parses and verifies as a directory; a directory
squatting on a file's path still blocks with the squatting reason;
and the Leg C shape (a declared input genuinely absent) still
blocks truthfully.

Every graded text below is verbatim from age21r7-evidence
(legB-plan-final.json); only the scratch-root prefix is swapped.

Run: .venv/bin/python tests/task_inbox/run_file_effects_d17_test.py
"""
import asyncio, json, os, re, sys, tempfile, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_d17_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from app.paths import DATA_DIR as _DATA  # noqa: E402
os.environ["XOD_DATASET_DB"] = str(_DATA / "test_runs" / "flywheel_test.db")
import app.services.task_queue as TQ
import app.services.orchestrator as O
import app.agent.researcher as RS

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
    tid = task_id or f"task-d17-{_n[0]}"
    return q._verify_file_effects({"id": tid, "request": step_text}, time.time())


S = TMP / "scratch"
S.mkdir(parents=True)

LEGB_P1 = (f"Phase 1: Work only inside {S}/jobB throughout; create the folder if needed, "
           f"then create {S}/jobB/part-1.txt containing exactly \"T6B-PART1\".")
ABSENT_REASON = "the task asked for part-1.txt to be created, but it is not on disk"
SQUAT_REASON = "directory is on disk at that path"


def unit_cases():
    print("D17 parser: kind is judged per path, by its own governing wording")
    jobB = S / "jobB"
    jobB.mkdir(parents=True)
    (jobB / "part-1.txt").write_bytes(b"T6B-PART1")
    claims = TQ._claimed_file_effects(LEGB_P1)
    p1 = [c for c in claims if c["path"] == jobB / "part-1.txt"]
    check("parser: part-1.txt is a file claim, not a directory claim",
          bool(p1) and p1[0].get("target") == "file", f"claims={claims}")
    check("parser: the container path makes no claim of its own (H6 prefix rule)",
          all(c["path"] != jobB for c in claims), f"claims={claims}")
    proj = S / "projdir"
    claims = TQ._claimed_file_effects(f"Create the directory at {proj}.")
    check("parser: a genuine directory creation still parses as a directory claim",
          len(claims) == 1 and claims[0].get("target") == "directory", f"claims={claims}")
    claims = TQ._claimed_file_effects(
        f"Create a new directory at {proj} for the project.\nCreate the file at {proj}.")
    check("parser: the hardening-round directory shape still parses as one directory claim",
          len(claims) == 1 and claims[0].get("target") == "directory", f"claims={claims}")
    both = S / "bothdir"
    claims = TQ._claimed_file_effects(
        f"Create the directory at {both}.\nCreate the file at {both}/inner.txt.")
    by_target = {c["target"]: c for c in claims}
    check("parser: a step creating a directory and its file keeps both claims",
          len(claims) == 2 and by_target["directory"]["path"] == both
          and by_target["file"]["path"] == both / "inner.txt", f"claims={claims}")
    md = S / "research.md"
    claims = TQ._claimed_file_effects(
        f"In the requested folder, create {md} with the sourced findings.")
    md_claims = [c for c in claims if c["path"] == md]
    check("parser: a suffixed file named beside folder wording stays a file claim",
          bool(md_claims) and md_claims[0].get("target") == "file", f"claims={claims}")

    print("D17 check: the graded shape against the disk")
    probs = verify(LEGB_P1)
    check("check: Leg B Phase 1 verifies clean with part-1.txt exact on disk",
          probs == [], f"problems={probs}")

    print("D17 check: the negatives keep their teeth")
    emptyB = S / "jobBempty"
    emptyB.mkdir(parents=True)
    probs = verify(LEGB_P1.replace(str(jobB), str(emptyB)))
    check("check: same text, part-1.txt genuinely absent, blocks naming part-1.txt",
          ABSENT_REASON.replace("part-1.txt", "part-1.txt") in probs or
          any("part-1.txt" in p and "not on disk" in p for p in probs),
          f"problems={probs}")
    goneB = S / "jobBgone"
    probs = verify(LEGB_P1.replace(str(jobB), str(goneB)))
    check("check: same text, nothing on disk, still blocks",
          len(probs) >= 1, f"problems={probs}")
    squat = S / "squat.txt"
    squat.mkdir(parents=True, exist_ok=True)
    probs = verify(f"Create the file at {squat} containing exactly \"TOKEN\".")
    check("check: a directory squatting on a file's path still blocks with its true reason",
          len(probs) == 1 and SQUAT_REASON in probs[0] and "not on disk" not in probs[0],
          f"problems={probs}")
    legC = S / "jobC"
    legC.mkdir(parents=True, exist_ok=True)
    probs = verify(f"Phase 1: Work only inside {legC} throughout. Read {legC}/seed-input.txt "
                   f"and create copy.txt with exactly the same contents as seed-input.txt.")
    check("check: the Leg C shape, declared input genuinely absent, still blocks truthfully",
          any("seed-input.txt" in p or "copy.txt" in p for p in probs), f"problems={probs}")
    proj.mkdir(parents=True, exist_ok=True)
    probs = verify(f"Create the directory at {proj}.")
    check("check: a created directory verifies as a directory",
          probs == [], f"problems={probs}")


# --------------------------------------------------------------------- fakes
STEP_RE = re.compile(r"YOUR STEP NOW \(step \d+ of \d+\):\n(.+?)\n\nDo only this step", re.S)


class FakeResearcher:
    """Does exactly what the live step did: writes part-1.txt through
    the shell equivalent (a direct write, no write_file activity op,
    which is why the recorded-effects layer saw nothing live)."""

    def __init__(self, *a, **k):
        pass

    async def run_research_stream(self, messages, **k):
        msg = messages[-1]["content"]
        await asyncio.sleep(0.05)
        m = STEP_RE.search(msg)
        step = m.group(1).strip() if m else msg
        if "part-1.txt" in step:
            jobB = S / "jobB"
            jobB.mkdir(parents=True, exist_ok=True)
            (jobB / "part-1.txt").write_bytes(b"T6B-PART1")
            yield {"type": "content",
                   "content": "Phase 1 is complete. Verified that jobB/part-1.txt "
                              "contains exactly T6B-PART1."}
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


A_ORIG = (f"Do this file job in exactly these phases, one phase at a time. Work only "
          f"inside the folder {S}/jobB (create it if needed). Phase 1: create "
          f"part-1.txt containing exactly T6B-PART1.")


async def case_d17_stepwise(qq):
    print("D17 end to end: the verbatim run 7 Leg B Phase 1 completes stepwise")
    PLANS[A_ORIG] = [LEGB_P1]
    ack = O.submit(A_ORIG)
    tid = ack["task_id"]
    t = await wait_status(qq, tid, ("done", "failed", "waiting"), timeout=90)
    check("e2e D17: the task finishes done", t["status"] == "done", f"status={t['status']}")
    items, _, _ = O.load_plan(tid)
    check("e2e D17: Phase 1 completed, not blocked on the false absence",
          items[0]["status"] == "completed" and not items[0].get("blocker"),
          json.dumps(items[0])[:300])
    check("e2e D17: part-1.txt is byte-exact",
          (S / "jobB" / "part-1.txt").read_bytes() == b"T6B-PART1")


async def amain():
    unit_cases()
    qq = TQ.task_queue = TQ.TaskQueue()
    globals()["q"] = qq
    qq.start()
    try:
        await case_d17_stepwise(qq)
    finally:
        qq.stop()


def main():
    asyncio.run(amain())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
        sys.exit(1)
    print("ALL D17 CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
