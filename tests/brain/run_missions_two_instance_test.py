"""
Missions fix round 5 (Lane 3): two instances, one database, reproduction first.

Regression suite for the 2026-10-31 Tier 1 re-run 4 record
(ESCALATION-T1-RERUN4-2026-10-31.md, wobble 4): fix round 4's mission
fixes passed their suite (27/27) and then failed in production, because
production is TWO backend processes sharing ONE task database: the
installed app on port 8000 and a LaunchAgent dev instance on port 8001
(com.xod.testserver, RunAtLoad from the source checkout). Round 4 was
transplanted into the installed app and relaunched it; the dev instance
was never restarted, so it kept running the previous release's code
(15158a1) in memory and went on planning doubled plans, manufacturing
backtick phantom file claims with the old path regex, and dispatching
write steps with the old tool selection, all through the shared store.

This suite reproduces that topology honestly: TWO subprocesses, each
running one code root's real MissionRunner scan loop (missions.start())
with its real TaskQueue file-effect gate and its real tool selector,
against ONE throwaway database in a temp XOD_DATA_DIR, on no ports at
all (the runners are backend processes; nothing here serves HTTP, so
production ports 8000/8001 and the production database are never
touched). The model layer is scripted the way
tests/brain/run_missions_fix4_test.py scripts it: a planner that
returns the T1A and T1D plan shapes from t1-raw4.json (each seat with
its own paraphrase, as two real planner calls produced), and a worker
that creates files through the real execute_tool tools when, and only
when, the executing process's own tool selector offers it a write
tool, and otherwise reports the production no-tools shape. Step
reports quote paths in backticks exactly like production reports do.
The offered tool list for every created task is logged per process.

Configurations:
  (i)  both instances on this branch's code (the control: round 4's
       fixes should make every defect impossible here);
  (ii) MIXED: one instance on pre-round-4 code (15158a1, the
       xod-fix5-pre worktree, override with XOD_FIX5_OLD_ROOT) and one
       on this branch's code. Before the round 5 fix this is the
       implicated configuration and the suite FAILS on it; after the
       fix it must pass, because the fix lives in the shared store and
       in verdict composition, where a stale peer cannot bypass it.

Run: .venv/bin/python tests/brain/run_missions_two_instance_test.py
"""
import json, os, sqlite3, subprocess, sys, tempfile, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD_ROOT = Path(os.environ.get("XOD_FIX5_OLD_ROOT",
                               str(ROOT.parent / "xod-fix5-pre")))
TMP = Path(tempfile.mkdtemp(prefix="xod_missions_two_instance_"))
REAL_HOME = os.environ.get("HOME", "")

DRIVER = r'''
import asyncio, json, os, sqlite3, sys, threading, time, uuid
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

SEAT = Path(os.environ["XOD_SEAT_ROOT"])
sys.path.insert(0, str(SEAT))
TAG = os.environ["XOD_TAG"]
VARIANT = os.environ["XOD_VARIANT"]
SCRATCH = os.environ["XOD_SCRATCH"]
LOGDIR = Path(os.environ["XOD_LOG_DIR"])
PLAN_DELAY = float(os.environ.get("XOD_PLAN_DELAY", "0.25"))
GO = Path(os.environ["XOD_GO_FILE"])

import app.services.missions as M
import app.services.task_queue as TQ
from app.agent.tools import execute_tool as _execute_tool

WRITE_TOOLS = ("computer_write_file", "fs_write_workspace_file")
DIR_TOOLS = ("computer_manage_file", "fs_create_folder") + WRITE_TOOLS
NO_TOOLS_REPORT = ("I can't create or verify files on this computer with the "
                   "tools available here, so I can't confirm that the file exists.")
SAMPLE_TEXT = ("the quick brown fox and the lazy dog\n"
               "the dog and the fox looked on\n"
               "a bright day and a quiet path\n")
WORDFREQ_PY = ("import re\nimport sys\nfrom collections import Counter\n\n\n"
               "def main():\n"
               "    text = open(sys.argv[1], encoding='utf-8').read()\n"
               "    counts = Counter(w.lower() for w in re.findall(r'[A-Za-z]+', text))\n"
               "    for word, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]:\n"
               "        print(f'{word}: {n}')\n\n\n"
               "if __name__ == '__main__':\n    main()\n")
TEST_PY = ("import subprocess\nimport sys\n\n\n"
           "def run_tool(tmp_path, text):\n"
           "    src = tmp_path / 'input.txt'\n"
           "    src.write_text(text, encoding='utf-8')\n"
           "    out = subprocess.run([sys.executable, 'wordfreq.py', str(src)],\n"
           "                         capture_output=True, text=True)\n"
           "    assert out.returncode == 0\n"
           "    return out.stdout\n\n\n"
           "def test_counts(tmp_path):\n"
           "    assert run_tool(tmp_path, 'the cat and the dog\n') == 'the: 2\nand: 1\ncat: 1\ndog: 1\n'\n\n\n"
           "def test_case(tmp_path):\n"
           "    assert run_tool(tmp_path, 'Dog DOG dog\n') == 'dog: 3\n'\n")


def run_tool(name, args):
    out = {}
    def _go():
        try:
            out["r"] = asyncio.run(_execute_tool(name, args))
        except Exception as e:
            out["e"] = e
    t = threading.Thread(target=_go)
    t.start(); t.join()
    if "e" in out:
        raise out["e"]
    return out["r"]


def offered_names(request):
    if hasattr(M, "_available_tool_names"):
        return list(M._available_tool_names(request))
    from app.agent.tools import select_tools_for_query
    return [t["function"]["name"]
            for t in select_tools_for_query(query=request or "", max_tools=16)]


def log_line(**kw):
    LOGDIR.mkdir(parents=True, exist_ok=True)
    with open(LOGDIR / f"{TAG}.jsonl", "a") as f:
        f.write(json.dumps(kw) + "\n")


def plan_for(goal):
    s = SCRATCH
    if "word-frequency" in goal:
        if VARIANT == "beta":
            titles = ["Make the sample file", "Build the counter tool", "Test the tool"]
            i1 = f"Make sample.txt in {s} with a few paragraphs of text containing repeated words."
            i2 = ("Create wordfreq.py to read the file named by its command-line argument and "
                  "print up to 10 most frequent words as 'word: count', one per line. Count words "
                  "case-insensitively, break frequency ties alphabetically, and run it on sample.txt.")
        else:
            titles = ["Create sample text", "Implement word-frequency tool", "Test the tool"]
            i1 = f"Create sample.txt in {s} with a few paragraphs of text containing repeated words."
            i2 = ("Create wordfreq.py to read the file named by its command-line argument and "
                  "print up to 10 most frequent words as 'word: count', one per line. Count words "
                  "case-insensitively, break frequency ties alphabetically, and run it on sample.txt "
                  "to check the output.")
        subs = [
            {"title": titles[0], "instruction": i1,
             "expected_effects": [{"kind": "create", "path": f"{s}/sample.txt", "content": None}]},
            {"title": titles[1], "instruction": i2,
             "expected_effects": [{"kind": "create", "path": f"{s}/wordfreq.py", "content": None},
                                  {"kind": "run", "command": "python3 wordfreq.py sample.txt",
                                   "cwd": s, "expect_exit": 0}]},
            {"title": titles[2],
             "instruction": ("Create runnable pytest tests in test_wordfreq.py covering counts, case "
                             "handling, output format, ordering, and the 10-word limit. Run the "
                             "tests and verify they pass."),
             "expected_effects": [{"kind": "create", "path": f"{s}/test_wordfreq.py", "content": None},
                                  {"kind": "run", "command": "python3 -m pytest test_wordfreq.py",
                                   "cwd": s, "expect_exit": 0}]},
        ]
        return {"subtasks": subs}
    if "proofdir" in goal:
        pd, note = f"{s}/proofdir", f"{s}/proofdir/note.txt"
        if VARIANT == "beta":
            subs = [
                {"title": "Create proof folder", "instruction": f"Create the folder {pd}.",
                 "expected_effects": [{"kind": "create", "path": pd, "content": None}]},
                {"title": "Write proof note",
                 "instruction": "Create note.txt in proofdir containing exactly baseline proof 7731, with no trailing newline or other characters.",
                 "expected_effects": [{"kind": "create", "path": note, "content": "baseline proof 7731"}]},
                {"title": "Read and report", "instruction": f"Read {note} and report its contents.",
                 "expected_effects": []},
            ]
        else:
            subs = [
                {"title": "Create proof directory", "instruction": f"Create the directory {pd}.",
                 "expected_effects": [{"kind": "create", "path": pd, "content": None}]},
                {"title": "Write proof note",
                 "instruction": "Create note.txt in proofdir containing exactly baseline proof 7731, with no newline or other extra characters.",
                 "expected_effects": [{"kind": "create", "path": note, "content": "baseline proof 7731"}]},
                {"title": "Read and report note", "instruction": f"Read {note} and report its contents.",
                 "expected_effects": []},
            ]
        return {"subtasks": subs}
    if "result file" in goal:
        subs = [
            {"title": "Decide the wording",
             "instruction": "Decide the exact wording of the result summary for the review pack.",
             "expected_effects": []},
            {"title": "Write the result",
             "instruction": f"Put the result in {s}/result.json",
             "expected_effects": [{"kind": "create", "path": f"{s}/result.json", "content": None}]},
        ]
        return {"subtasks": subs}
    return {"subtasks": [{"title": "Do it", "instruction": goal, "expected_effects": []}]}


class ScriptedAsk:
    async def __call__(self, prompt):
        await asyncio.sleep(PLAN_DELAY)
        return json.dumps(plan_for(prompt))


def do_worker(request, offered):
    s = SCRATCH
    write_ok = any(n in offered for n in WRITE_TOOLS)
    dir_ok = any(n in offered for n in DIR_TOOLS)
    note = f"{s}/proofdir/note.txt"
    pd = f"{s}/proofdir"
    if "test_wordfreq" in request:
        p = f"{s}/test_wordfreq.py"
        if not write_ok:
            return NO_TOOLS_REPORT
        run_tool("computer_write_file", {"filepath": p, "content": TEST_PY})
        return (f"I wrote `{p}`, but I can't confirm the tests pass. `sample.txt` is "
                "present in that directory.\n\nThe requested `python3 -m pytest "
                "test_wordfreq.py` command exited with code **2** due to a collection "
                "error. I rewrote the test file afterward but did not rerun the command, "
                "so the revised tests remain unverified.")
    if "note.txt" in request and "Create the file at" in request:
        if not write_ok:
            return NO_TOOLS_REPORT
        run_tool("computer_write_file", {"filepath": note, "content": "baseline proof 7731"})
        return f"Created `{note}`."
    if "wordfreq.py" in request and "Create the file at" in request:
        p = f"{s}/wordfreq.py"
        if not write_ok:
            return NO_TOOLS_REPORT
        run_tool("computer_write_file", {"filepath": p, "content": WORDFREQ_PY})
        return (f"Created `{p}` and ran it against `sample.txt` from that directory. "
                "The command `python3 wordfreq.py sample.txt` exited with code **0**.")
    if "sample.txt" in request and "Create the file at" in request:
        p = f"{s}/sample.txt"
        if not write_ok:
            return NO_TOOLS_REPORT
        run_tool("computer_write_file", {"filepath": p, "content": SAMPLE_TEXT})
        return f"Created `{p}` (5 lines, 381 bytes)."
    if "result.json" in request:
        p = f"{s}/result.json"
        if not write_ok:
            return NO_TOOLS_REPORT
        run_tool("computer_write_file", {"filepath": p, "content": '{"summary": "result ready"}'})
        return f"Created `{p}`."
    if pd in request and "note.txt" not in request:
        if not dir_ok:
            return NO_TOOLS_REPORT
        run_tool("computer_manage_file", {"action": "mkdir", "src": pd})
        if VARIANT == "beta":
            return f"The directory `{pd}` is ready."
        return f"Created the directory `{pd}`."
    if "Read" in request and "note.txt" in request:
        try:
            return f"Read it back: {Path(note).read_text()}"
        except OSError:
            return "note.txt is not there."
    if "Decide the exact wording" in request:
        return "The wording is decided: result ready."
    return "Done."


class SharedGateQueue:
    """Tasks live in the shared database, as production tasks do: either
    instance's runner can read the outcome the executing instance wrote.
    Execution and the file-effect gate run in the creating process, with
    that process's real TaskQueue._verify_file_effects."""

    def __init__(self):
        self._gate = TQ.TaskQueue.__new__(TQ.TaskQueue)
        self._db = Path(M.DB)
        c = self._conn()
        c.execute("CREATE TABLE IF NOT EXISTS harness_tasks "
                  "(id TEXT PRIMARY KEY, request TEXT, status TEXT, result TEXT, created_by TEXT)")
        c.commit(); c.close()

    def _conn(self):
        return sqlite3.connect(str(self._db), timeout=15)

    def create(self, request, title=None):
        tid = "task-hx" + uuid.uuid4().hex[:8]
        offered = offered_names(request)
        write_offered = any(n in offered for n in WRITE_TOOLS)
        log_line(kind="create", tag=TAG, seat=str(SEAT), task=tid,
                 request_first=(request or "").splitlines()[0][:90] if request else "",
                 offered=offered, write_offered=write_offered)
        report = do_worker(request, offered)
        problems = self._gate._verify_file_effects({"id": tid, "request": request}, time.time())
        if problems:
            status = "failed"
            result = f"{report}\n\nNot finished: {'; '.join(problems)}"
        else:
            status, result = "done", report
        c = self._conn()
        c.execute("INSERT INTO harness_tasks VALUES (?,?,?,?,?)",
                  (tid, request, status, result, TAG))
        c.commit(); c.close()
        return {"id": tid, "status": "queued", "title": title}

    def get(self, tid):
        c = self._conn()
        r = c.execute("SELECT id, request, status, result FROM harness_tasks WHERE id = ?",
                      (tid,)).fetchone()
        c.close()
        if not r:
            return None
        return {"id": r[0], "request": r[1], "status": r[2], "result": r[3], "title": ""}


def create_missions():
    M._spawn = lambda mid: None
    goals = {
        "t1a": (f"Work in the folder {SCRATCH}. Build a small word-frequency command line "
                "tool there: create wordfreq.py that reads a text file given as a command "
                "line argument and prints the top 10 most frequent words, one per line, in "
                "the format \"word: count\". Create sample.txt with a few paragraphs of "
                "sample text to run it on. Create runnable pytest tests in "
                "test_wordfreq.py that test wordfreq.py. Verify the tool runs and the "
                "tests pass before finishing."),
        "t1d": (f"Create a folder named proofdir inside {SCRATCH}. Inside it, create a "
                "file named note.txt containing exactly the text between these quotes "
                "and nothing else: \"baseline proof 7731\" Then read note.txt back and "
                "report what it says."),
        "t1e": (f"Prepare a result file for the review pack in {SCRATCH}: decide the "
                "wording, then put the result file in place."),
    }
    for key, goal in goals.items():
        m = M.create_mission(goal)
        print(f"MID {key} {m['id']}", flush=True)


async def serve():
    deadline = time.time() + 120
    while not GO.exists():
        if time.time() > deadline:
            return
        await asyncio.sleep(0.05)
    queue = SharedGateQueue()
    M.runner = M.MissionRunner(queue=queue, ask=ScriptedAsk(), poll_sec=0.02)
    M.start()
    log_line(kind="ready", tag=TAG, seat=str(SEAT))
    while True:
        await asyncio.sleep(0.25)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "create":
        create_missions()
    else:
        asyncio.run(serve())
'''

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def seat_env(cfg: Path, seat_root: Path, tag: str, variant: str, delay: str) -> dict:
    env = dict(os.environ)
    for cand in sorted((Path(REAL_HOME) / "Library" / "Python").glob("*")):
        if (cand / "lib" / "python" / "site-packages" / "pytest").is_dir():
            env["PYTHONUSERBASE"] = str(cand)
            break
    env.update({
        "HOME": str(cfg / "home"),
        "XOD_DATA_DIR": str(cfg / "data"),
        "XOD_SCRATCH": str(cfg / "scratch"),
        "XOD_LOG_DIR": str(cfg / "logs"),
        "XOD_GO_FILE": str(cfg / "GO"),
        "XOD_SEAT_ROOT": str(seat_root),
        "XOD_TAG": tag,
        "XOD_VARIANT": variant,
        "XOD_PLAN_DELAY": delay,
        "XOD_NO_UPDATE_CHECK": "1",
        "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
        "PATH": "/usr/bin:" + env.get("PATH", ""),
    })
    return env


def run_config(label: str, seat_a: Path, seat_b: Path):
    print(f"\n=== configuration: {label} ===", flush=True)
    cfg = TMP / label
    for d in ("home", "data", "scratch", "logs"):
        (cfg / d).mkdir(parents=True, exist_ok=True)
    driver = cfg / "driver.py"
    driver.write_text(DRIVER)
    py = sys.executable

    def spawn(seat_root, tag, variant, delay, mode=None):
        env = seat_env(cfg, seat_root, tag, variant, delay)
        args = [py, str(driver)] + ([mode] if mode else [])
        return subprocess.Popen(args, cwd=str(seat_root), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)

    created = spawn(seat_a, "creator", "alpha", "0.1", mode="create")
    out, _ = created.communicate(timeout=120)
    mids = {}
    for line in out.splitlines():
        if line.startswith("MID "):
            _, key, mid = line.split()
            mids[key] = mid
    if len(mids) != 3:
        check(f"{label}: missions created", False, out[-500:])
        return
    procs = [spawn(seat_a, "A", "alpha", "0.25"), spawn(seat_b, "B", "beta", "0.55")]
    try:
        (cfg / "GO").write_text("go")
        db = cfg / "data" / "xod.db"
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                rows = c.execute("SELECT state FROM missions").fetchall()
                c.close()
                if len(rows) == 3 and all(r[0] in ("done", "failed") for r in rows):
                    break
            except sqlite3.Error:
                pass
            time.sleep(0.5)
        time.sleep(3)   # settle: a slower driver's outcome save can land after a finish
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()

    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    missions = {r["id"]: dict(r) for r in c.execute("SELECT * FROM missions")}
    subs = {}
    for r in c.execute("SELECT * FROM mission_subtasks ORDER BY mission_id, idx"):
        subs.setdefault(r["mission_id"], []).append(dict(r))
    tasks = [dict(r) for r in c.execute("SELECT * FROM harness_tasks")]
    c.close()
    by_key = {k: missions[v] for k, v in mids.items() if v in missions}

    def has_phantom(mid):
        # A phantom is a VERDICT text (a failure reason or an evidence
        # string, in the mission row, the report, a subtask row, or a
        # task result tail) that names a backticked path as a file to be
        # created. Claimed reports are not scanned: a report quoting a
        # path in backticks is innocent, it is the extraction of that
        # quote into a checked claim that manufactures the phantom.
        texts = []
        m = missions.get(mid) or {}
        texts.append(m.get("failure_reason") or "")
        try:
            rep = json.loads(m.get("report_json") or "{}")
        except ValueError:
            rep = {}
        texts.append(rep.get("failure_reason") or "")
        for s in rep.get("subtasks", []):
            texts += list(s.get("evidence") or [])
        for s in subs.get(mid, []):
            texts.append(s.get("evidence") or "")
        by_id = {t["id"]: t for t in tasks}
        for s in subs.get(mid, []):
            t = by_id.get(s.get("task_id") or "")
            if t:
                texts.append(t.get("result") or "")
        return any("` to be created" in t for t in texts)

    offered = []
    for tag in ("A", "B"):
        lf = cfg / "logs" / f"{tag}.jsonl"
        if lf.is_file():
            for line in lf.read_text().splitlines():
                rec = json.loads(line)
                if rec.get("kind") == "create":
                    offered.append(rec)
    note_offers = [r for r in offered if "note.txt" in r["request_first"] or "note.txt" in r["request_first"]]
    print(f"  matrix {label}: " + "; ".join(
        f"{k}: state={by_key[k]['state']} rows={len(subs.get(v, []))} phantom={has_phantom(v)}"
        for k, v in mids.items()), flush=True)
    for r in offered:
        print(f"  offered[{r['tag']}@{Path(r['seat']).name}] {r['request_first'][:60]!r} "
              f"write_offered={r['write_offered']}", flush=True)

    for key in ("t1a", "t1d"):
        mid = mids[key]
        check(f"{label} {key}: one plan of 3 subtask rows, not a doubled plan",
              len(subs.get(mid, [])) == 3, f"rows={len(subs.get(mid, []))}")
        check(f"{label} {key}: no backtick phantom claim in any verdict text",
              not has_phantom(mid))
    t1d = by_key["t1d"]
    note = cfg / "scratch" / "proofdir" / "note.txt"
    check(f"{label} t1d: mission closes done", t1d["state"] == "done",
          f"state={t1d['state']} reason={t1d.get('failure_reason')}")
    check(f"{label} t1d: note.txt landed with exact bytes",
          note.is_file() and note.read_bytes() == b"baseline proof 7731",
          f"exists={note.is_file()}")
    no_tools_claims = [s for s in subs.get(mids["t1d"], [])
                       if "can't create or verify" in (s.get("claimed") or "")]
    check(f"{label} t1d: no subtask reported having no file tools",
          not no_tools_claims, f"claims={[s['id'] for s in no_tools_claims]}")
    t1a = by_key["t1a"]
    sc = cfg / "scratch"
    check(f"{label} t1a: sample.txt, wordfreq.py and test_wordfreq.py all landed",
          all((sc / f).is_file() for f in ("sample.txt", "wordfreq.py", "test_wordfreq.py")))
    if t1a["state"] == "failed":
        check(f"{label} t1a: the failure reason cites the step's own admission, honestly",
              "did not finish" in (t1a.get("failure_reason") or ""),
              f"reason={t1a.get('failure_reason')}")
    t1e_mid = mids["t1e"]
    result_json = sc / "result.json"
    assembly_named = any("Assembly stopped" in (s.get("evidence") or "")
                         for s in subs.get(t1e_mid, []))
    t1e_no_tools = any("can't create or verify" in (s.get("claimed") or "")
                       for s in subs.get(t1e_mid, []))
    check(f"{label} t1e: the paraphrase write step lands its file or fails loudly at assembly, "
          "never silently inside the subtask",
          (result_json.is_file() or assembly_named) and not (t1e_no_tools and not result_json.is_file()),
          f"result={result_json.is_file()} assembly={assembly_named} no_tools={t1e_no_tools} "
          f"state={by_key['t1e']['state']} reason={by_key['t1e'].get('failure_reason')}")


def main():
    run_config("both-current", ROOT, ROOT)
    if (OLD_ROOT / "app" / "services" / "missions.py").is_file():
        run_config("mixed-stale-peer", ROOT, OLD_ROOT)
    else:
        print(f"\nSKIP mixed configuration: pre-round-4 worktree not found at {OLD_ROOT} "
              "(set XOD_FIX5_OLD_ROOT)", flush=True)
        check("mixed configuration ran", False, f"old root missing: {OLD_ROOT}")
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1 if failed else 0)


main()
