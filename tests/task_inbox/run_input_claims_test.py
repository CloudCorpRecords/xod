"""
Task inbox regression test: a step that declares an input file cannot
complete when that input was never on disk (Tier 6 defect D1, 2026-11-07).

The Tier 6 protocol run caught the fabrication end to end: a step was
asked to read seed-input.txt and create copy.txt from it; seed-input.txt
did not exist; the step nonetheless completed and copy.txt held the
invented string "content from step 1". Two judgment gaps made it
possible: the file-effect verifier modeled claimed outputs only, never
declared inputs, and the self-report check was bypassed entirely for any
step whose text carried an output claim, so the step's own admission
that the input was missing was never read.

This suite pins both fixes: TaskQueue._claimed_input_effects extracts
declared inputs conservatively, _verify_file_effects fails a task whose
declared input is missing even when its output sits on disk looking
plausible, and orchestrator._self_report_problem stops bypassing the
report when a declared input is missing (and keeps bypassing it when
the input is there, so honest claim-carrying steps are still judged by
the disk).

Ported onto main by AGE-15 (2026-11-10) from tree/tests-push-d1
(63cff5c), with added cases where the declared-input layer meets the D5
file-effect shapes: an aggregate line-list output whose input is
declared verifies clean when both are right, and blocks with only the
input problem when the input is missing (the output check must not
double-block or contradict it), and a present input with wrong output
content blocks on the content, not on the input. The D1 layer checks
that a declared input exists; it does not check the input's content
(the original fix did not either), so no content-of-input case is
pinned here.

Run: .venv/bin/python tests/task_inbox/run_input_claims_test.py
"""
import asyncio, os, sys, tempfile, time as _time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
TMP = Path(tempfile.mkdtemp(prefix="xod_input_claims_"))
os.environ["XOD_DATASET_DB"] = str(_DATA / "test_runs" / "flywheel_test.db")
import app.services.task_queue as TQ
import app.services.activity as ACT
import app.services.orchestrator as ORCH

TQ.DB_PATH, TQ.SHOTS_DIR = TMP / "tasks.db", TMP / "shots"
TQ.notify = lambda title, msg: print(f"   [notification] {title}: {msg}", flush=True)
if hasattr(TQ, "_phone"):
    TQ._phone = lambda task, kind: None

JOB = TMP / "jobC"
JOB.mkdir()
SEED = JOB / "seed-input.txt"
COPY = JOB / "copy.txt"
# Two sentences, the shape a planner writes: the input named in a read
# sentence of its own, the output claimed in the next.
LEG_C_STEP = (f"Read the file {SEED}. Then create {COPY} containing exactly its contents.")


def check(cond, name, detail=""):
    if cond:
        print(f"PASS {name}", flush=True)
    else:
        print(f"FAIL {name} :: {detail}", flush=True)
    return bool(cond)


async def main():
    passed = failed = 0
    results = []

    # --- the parser: a Leg C shaped step names its input, not its output
    inputs = TQ._claimed_input_effects(LEG_C_STEP)
    results.append(check([str(p) for p in inputs] == [str(SEED)],
                         "parser names the declared input only", f"inputs={inputs}"))

    # --- the parser stays conservative: optional and output-only texts claim no input
    results.append(check(TQ._claimed_input_effects(
        f"Read {JOB / 'config.json'} if it exists, then create {JOB / 'out.txt'} with defaults.") == [],
        "optional input phrasing claims no input"))
    results.append(check(TQ._claimed_input_effects(
        f"Create {JOB / 'fresh.txt'} containing exactly hello") == [],
        "a plain create claims no input"))
    results.append(check(TQ._claimed_input_effects("Reply with exactly: ok") == [],
                         "a reply-only request claims no input"))

    # --- the verifier: fabricated output over a missing input is a problem
    q = TQ.TaskQueue()
    real_act_db = ACT.DB
    ACT.DB = TMP / "activity.db"
    try:
        if COPY.exists():
            COPY.unlink()
        COPY.write_text("content from step 1")   # the fabricated artifact, on disk
        if SEED.exists():
            SEED.unlink()
        fake_task = {"id": "task-d1-missing", "request": LEG_C_STEP}
        problems = q._verify_file_effects(fake_task, _time.time() - 60)
        print(f"   missing-input problems: {problems}", flush=True)
        results.append(check(any("reads from seed-input.txt" in p for p in problems),
                             "missing declared input fails verification despite plausible output",
                             f"problems={problems}"))

        # --- and with the input really there, the same shape verifies clean
        SEED.write_text("T6C-SEED-9182")
        COPY.write_text("T6C-SEED-9182")
        fake_task = {"id": "task-d1-present", "request": LEG_C_STEP}
        problems = q._verify_file_effects(fake_task, _time.time() - 60)
        print(f"   present-input problems: {problems}", flush=True)
        results.append(check(not any("reads from" in p for p in problems),
                             "present declared input raises no input problem",
                             f"problems={problems}"))

        # --- a path the request itself claims as an output is never its own input
        results.append(check(TQ._claimed_input_effects(
            f"Create {JOB / 'rewrite.txt'} containing exactly hello, after reading {JOB / 'rewrite.txt'}.") == [],
            "a claimed output claims no input, even named in a read clause"))

        # --- present input, wrong output content: the content layer blocks, the input layer does not
        OUT = JOB / "out.txt"
        step_wrong = f"Read the file {SEED}. Then create {OUT} whose entire contents are exactly this: EXPECTED-1"
        OUT.write_text("WRONG-9")
        problems = q._verify_file_effects({"id": "task-d1-wrong-content", "request": step_wrong},
                                          _time.time() - 60)
        print(f"   wrong-content problems: {problems}", flush=True)
        results.append(check(problems == ["out.txt does not contain exactly what the task asked for"],
                             "present input with wrong output content blocks on content only",
                             f"problems={problems}"))
        OUT.write_text("EXPECTED-1")
        problems = q._verify_file_effects({"id": "task-d1-right-content", "request": step_wrong},
                                          _time.time() - 60)
        results.append(check(problems == [],
                             "present input with exact output content verifies clean",
                             f"problems={problems}"))

        # --- D5 interaction: an aggregate line-list step whose input is declared
        agg_dir = TMP / "jobAgg"
        agg_dir.mkdir()
        seed2 = agg_dir / "seed-list.txt"
        index = agg_dir / "index.txt"
        agg_step = (f"Read the file {seed2}. Then create {index} containing exactly "
                    f"three lines, in order: \"a.txt\", \"b.txt\", and \"c.txt\".")
        seed2.write_text("a.txt\nb.txt\nc.txt")
        index.write_bytes(b"a.txt\nb.txt\nc.txt\n")
        problems = q._verify_file_effects({"id": "task-d1-agg-ok", "request": agg_step},
                                          _time.time() - 60)
        print(f"   aggregate-with-input problems: {problems}", flush=True)
        results.append(check(problems == [],
                             "aggregate output with its declared input present verifies clean",
                             f"problems={problems}"))
        seed2.unlink()
        problems = q._verify_file_effects({"id": "task-d1-agg-missing", "request": agg_step},
                                          _time.time() - 60)
        print(f"   aggregate-missing-input problems: {problems}", flush=True)
        results.append(check(problems == ["the task reads from seed-list.txt, but it is not on disk"],
                             "aggregate output correct but input missing blocks on the input only",
                             f"problems={problems}"))
    finally:
        ACT.DB = real_act_db

    # --- the self-report bypass: missing input reopens the report, present input keeps it shut
    if SEED.exists():
        SEED.unlink()
    admission = f"I could not read {SEED.name} because the file is missing, so I wrote a summary instead."
    problem = ORCH._self_report_problem(LEG_C_STEP, admission)
    results.append(check(problem is not None and "report says" in problem,
                         "an admission is heard when the declared input is missing",
                         f"problem={problem}"))
    SEED.write_text("T6C-SEED-9182")
    problem = ORCH._self_report_problem(LEG_C_STEP, admission)
    results.append(check(problem is None,
                         "the disk still judges when the declared input exists",
                         f"problem={problem}"))

    passed = sum(1 for r in results if r)
    failed = len(results) - passed
    print(f"\n{passed} passed, {failed} failed", flush=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
