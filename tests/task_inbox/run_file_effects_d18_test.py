"""
Task inbox regression test: a step that works IN a folder must not
claim the folder as its file output (D18, found by the AGE-21 Tier 1
re-grade run 7 on main bc3c689, 2026-11-11).

Run 7's mini-battery J1 step text (verbatim): "In
<scratch>/t1mini/org, create subfolders and move PDF files to pdfs,
PNG and JPG files to images, TXT and MD files to text, and CSV files
to data. Ask where to put any other file types, then verify no loose
files remain." The folder is framed locatively, as the container the
step works inside; the create verb governs the subfolders. The
organize work landed exact on disk (the driver's own disk check read
True), yet the step blocked with "the task asked for org to be
created as a file, but a directory is on disk at that path" and the
task never completed.

Mechanism: the stepwise claim path differs from the mission and
container paths the earlier fixes covered. _claimed_file_effects
mints a write claim for any path whose sentence carries an effect
verb, with no governing test for a locatively fronted path, so org
became a file claim. Neither container branch in _verify_file_effects
could sustain it: the disk-evidence branch of
_path_framed_as_container keys on bare creation FILENAMES (the
step's creations are subfolders and moves, named without
extensions, so it had nothing to find inside the folder), and the
recorded-effects branch keys on write_file activity ops (the step
worked through shell moves, which record none).

The fix adds the missing evidence branch to
_path_framed_as_container, on the D8 principle that a phrase alone
proves nothing: when a sentence opens by placing the step in the
path ("In <path>, ...", a prepositional phrase before any verb), the
folder is framed as the container only when the destination
subfolders that same sentence names ("to pdfs", "to images") are on
disk as directories directly inside it, which is the organize work
the step exists to do.

Controls pin the other directions: the same text against an empty
folder, or a folder holding only a loose unrelated file, still
blocks; a step that truly declares creating a file at a directory's
path still blocks with the squatting reason; and a held-out locative
wording no graded run produced verifies on the same evidence and
blocks without it.

Every graded text below is verbatim from age21r7-evidence
(j1-plan.json); only the scratch-root prefix is swapped.

Run: .venv/bin/python tests/task_inbox/run_file_effects_d18_test.py
"""
import asyncio, json, os, re, shutil, sys, tempfile, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_d18_"))
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
    tid = task_id or f"task-d18-{_n[0]}"
    return q._verify_file_effects({"id": tid, "request": step_text}, time.time())


S = TMP / "scratch"
S.mkdir(parents=True)

J1 = (f"In {S}/org, create subfolders and move PDF files to pdfs, PNG and JPG files to "
      f"images, TXT and MD files to text, and CSV files to data. Ask where to put any "
      f"other file types, then verify no loose files remain.")
HELDOUT = (f"In {S}/org2, create subfolders and move PDF files to pdfs and image files "
           f"to images, then verify no loose files remain.")
SQUAT_REASON = "directory is on disk at that path"
ORGANIZED = (("pdfs", ("invoice.pdf", "letter.pdf")),
             ("images", ("photo1.png", "image2.jpg")),
             ("text", ("memo.txt", "notes.txt", "readme.md")),
             ("data", ("budget.csv", "data.csv")))


def lay_organized(folder):
    for sub, files in ORGANIZED:
        d = folder / sub
        d.mkdir(parents=True, exist_ok=True)
        for f in files:
            (d / f).write_bytes(b"x")


def unit_cases():
    print("D18 parser: the locative framing reads disk evidence, not the phrase")
    org = S / "org"
    check("parser: the graded text does not frame while the folder is absent",
          not TQ._path_framed_as_container(J1, org))
    lay_organized(org)
    check("parser: the graded text frames with its destinations inside",
          TQ._path_framed_as_container(J1, org))
    empty = S / "orgEmpty"
    empty.mkdir(parents=True)
    check("parser: the graded phrase against an EMPTY folder does not frame",
          not TQ._path_framed_as_container(J1.replace(str(org), str(empty)), empty))
    unrel = S / "orgUnrelated"
    unrel.mkdir(parents=True)
    (unrel / "unrelated.txt").write_bytes(b"loose")
    check("parser: the graded phrase against loose files only does not frame",
          not TQ._path_framed_as_container(J1.replace(str(org), str(unrel)), unrel))

    print("D18 check: the graded shape against the disk")
    probs = verify(J1)
    check("check: J1 verifies clean with the organize work exact on disk",
          probs == [], f"problems={probs}")
    org2 = S / "org2"
    (org2 / "pdfs").mkdir(parents=True)
    (org2 / "images").mkdir(parents=True)
    (org2 / "pdfs" / "a.pdf").write_bytes(b"x")
    (org2 / "images" / "b.png").write_bytes(b"x")
    probs = verify(HELDOUT)
    check("check: held-out locative wording verifies on the same evidence",
          probs == [], f"problems={probs}")

    print("D18 check: the negatives keep their teeth")
    probs = verify(J1.replace(str(org), str(empty)))
    check("check: J1 text against an empty folder still blocks with the squatting reason",
          len(probs) == 1 and SQUAT_REASON in probs[0], f"problems={probs}")
    probs = verify(J1.replace(str(org), str(unrel)))
    check("check: J1 text, folder holding only a loose unrelated file, still blocks",
          len(probs) == 1 and SQUAT_REASON in probs[0], f"problems={probs}")
    partial = S / "orgPartial"
    (partial / "pdfs").mkdir(parents=True)
    (partial / "pdfs" / "a.pdf").write_bytes(b"x")
    probs = verify(J1.replace(str(org), str(partial)))
    check("check: J1 text, only one of four named destinations inside, still blocks",
          len(probs) == 1 and SQUAT_REASON in probs[0], f"problems={probs}")
    probs = verify(f"Create the file at {org} containing exactly \"TOKEN\".")
    check("check: a true file creation declared at a directory's path still blocks (squat)",
          len(probs) == 1 and SQUAT_REASON in probs[0], f"problems={probs}")
    gone = S / "orgGone"
    probs = verify(J1.replace(str(org), str(gone)))
    check("check: J1 text with the folder never created still blocks truthfully",
          len(probs) >= 1 and any("not on disk" in p for p in probs), f"problems={probs}")


# --------------------------------------------------------------------- fakes
STEP_RE = re.compile(r"YOUR STEP NOW \(step \d+ of \d+\):\n(.+?)\n\nDo only this step", re.S)
DEST = {"pdf": "pdfs", "png": "images", "jpg": "images",
        "txt": "text", "md": "text", "csv": "data"}


class FakeResearcher:
    """Does the organize work the live step did: subfolders by type,
    every loose file moved, through direct filesystem moves (the shell
    equivalent; no write_file activity ops are recorded)."""

    def __init__(self, *a, **k):
        pass

    async def run_research_stream(self, messages, **k):
        msg = messages[-1]["content"]
        await asyncio.sleep(0.05)
        m = STEP_RE.search(msg)
        step = m.group(1).strip() if m else msg
        root = None
        mm = re.search(r"In (/\S+?), create subfolders", step)
        if mm:
            root = Path(mm.group(1))
        if root is not None and root.is_dir():
            for sub in set(DEST.values()):
                (root / sub).mkdir(exist_ok=True)
            for f in list(root.iterdir()):
                if f.is_file() and f.suffix.lstrip(".").lower() in DEST:
                    shutil.move(str(f), str(root / DEST[f.suffix.lstrip(".").lower()] / f.name))
            yield {"type": "content",
                   "content": "Organized the folder: all files are in the requested "
                              "subfolders. No loose files remain."}
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


A_ORIG = (f"Organize the files in the folder {S}/org into subfolders by type: pdfs for "
          f"pdf files, images for png and jpg files, text for txt and md files, data "
          f"for csv files. Move every file into its subfolder and leave no loose files "
          f"in the folder.")


async def case_d18_stepwise(qq):
    print("D18 end to end: the verbatim run 7 J1 step completes stepwise")
    org = S / "org"
    org.mkdir(parents=True, exist_ok=True)
    for sub, files in ORGANIZED:
        for f in files:
            (org / f).write_bytes(b"x")
    PLANS[A_ORIG] = [J1]
    ack = O.submit(A_ORIG)
    tid = ack["task_id"]
    t = await wait_status(qq, tid, ("done", "failed", "waiting"), timeout=90)
    check("e2e D18: the task finishes done", t["status"] == "done", f"status={t['status']}")
    items, _, _ = O.load_plan(tid)
    check("e2e D18: the organize step completed, not blocked on its folder",
          items[0]["status"] == "completed" and not items[0].get("blocker"),
          json.dumps(items[0])[:300])
    got = sorted(str(p.relative_to(org)) for p in org.rglob("*") if p.is_file())
    want = sorted(f"{sub}/{f}" for sub, files in ORGANIZED for f in files)
    check("e2e D18: the organized listing is exactly the expected 9 paths",
          got == want, f"got={got}")


async def amain():
    unit_cases()
    qq = TQ.task_queue = TQ.TaskQueue()
    globals()["q"] = qq
    qq.start()
    try:
        await case_d18_stepwise(qq)
    finally:
        qq.stop()


def main():
    asyncio.run(amain())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
        sys.exit(1)
    print("ALL D18 CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
