"""
Mission-layer fixes from the AGE-21 re-grade run 3 (2026-11-11):

  - AGE-26 (D9): a create step whose instruction mentions the folder it
    works inside ("Create research.md in the requested folder") was
    classified as a directory step at every layer at once, so the file
    the task had verifiably written was reported not on disk by the
    product's own checker. Directory classification is per effect and
    must be governed by the effect's own path, never by a container
    mention; a file-suffixed target is a file.
  - AGE-27 (D10): a pinned pytest run ("python3 -B -m pytest ...") was
    not recognized as a pytest invocation (the -B flag), so no
    interpreter resolution engaged and the run executed under the app
    bundle's pytest-less interpreter. The detector accepts interpreter
    flags, and resolution falls back to the first pytest-capable
    python3 the search path offers before provisioning a venv.
  - AGE-28 (D11): a mission subtask with a computer-tool capability
    contract was routed to the browser mission surface, whose
    needs-input plea became the step's recorded result. Routing honors
    the declared contract, and a browser plea is never recorded as an
    effects step's claim.

The graded shapes (T1A step 3, T1B step 3, T1D steps 1 and 2) are used
verbatim from the run 3 evidence, with only the scratch-root prefix
swapped for a temp root.

Run: .venv/bin/python tests/brain/run_missions_mission_fix_test.py
"""
import asyncio, json, os, subprocess, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_mission_fix_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import app.services.missions as M
import app.services.task_queue as TQ
import app.services.browser_sandbox as BS
import app.agent.researcher as RS

DBF = TMP / "xod.db"
M.DB = DBF
TQ.DB_PATH, TQ.SHOTS_DIR = DBF, TMP / "shots"
TQ.notify = lambda title, msg: None
TQ._phone = lambda task, kind: None
M._spawn = lambda mid: None

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


class FakeAsk:
    def __init__(self, replies):
        self.replies = list(replies)

    async def __call__(self, prompt):
        return self.replies.pop(0) if self.replies else "{}"


class FakeQueue:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.created = []
        self.tasks = {}

    def create(self, request, title=None, origin=None):
        tid = f"task-fake{len(self.created) + 1}"
        self.created.append({"request": request, "title": title})
        self.tasks[tid] = {"behavior": self.behaviors[len(self.created) - 1]}
        return {"id": tid, "status": "queued", "title": title}

    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None:
            return None
        b = rec["behavior"]
        if callable(b):
            return b(rec)
        return {"id": tid, "title": "", "request": "", **b}


DONE = {"status": "done", "result": "All done."}
SCRATCH = TMP / "scratch"
SCRATCH.mkdir(parents=True, exist_ok=True)

# The graded subtasks, verbatim from the run 3 evidence (titles,
# instructions, effects), with the scratch root swapped to SCRATCH.
T1B3 = {"title": "Write research file",
        "instruction": "Create research.md in the requested folder with both findings, "
                       "including each claim's context and its source name and URL next to it.",
        "expected_effects": [{"kind": "create", "path": str(SCRATCH / "research.md"),
                              "content": None}],
        "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"},
                   {"source": "prior_step", "ref": "2"}],
        "tools": ["computer_write_file"]}
T1D1 = {"title": "Create proof directory",
        "instruction": f"Create the directory {SCRATCH}/proofdir.",
        "expected_effects": [{"kind": "create", "path": str(SCRATCH / "proofdir"),
                              "content": None}],
        "inputs": [{"source": "goal"}], "tools": ["computer_create_directory"]}
T1D2 = {"title": "Write proof note",
        "instruction": "Create note.txt in the proof directory containing exactly "
                       "baseline proof 7731, with no newline or other characters.",
        "expected_effects": [{"kind": "create", "path": str(SCRATCH / "proofdir" / "note.txt"),
                              "content": "baseline proof 7731"}],
        "inputs": [{"source": "goal"}], "tools": ["computer_write_file"]}
PYTEST_CMD = "python3 -B -m pytest -p no:cacheprovider test_wordfreq.py"
T1A3 = {"title": "Test the tool",
        "instruction": "Create runnable pytest tests in test_wordfreq.py that exercise "
                       "the command-line tool, checking counts, formatting, case "
                       "handling, and the 10-word limit. Run the tests and fix any "
                       "failures before finishing.",
        "expected_effects": [{"kind": "create", "path": str(SCRATCH / "test_wordfreq.py"),
                              "content": None},
                             {"kind": "run", "command": PYTEST_CMD, "cwd": str(SCRATCH),
                              "expect_exit": 0}],
        "inputs": [{"source": "goal"},
                   {"source": "file", "ref": str(SCRATCH / "wordfreq.py")}],
        "tools": ["computer_read_file", "computer_write_file"]}
PLEA = ("### 💬 I need something from you\n\n"
        "Could you provide access to a terminal and local file-writing tools? I can’t "
        "create the test file or run the required command through the current browser "
        "interface. The earlier run also reported that pytest is not installed.")


def _eff_is_dir(sub, eff):
    """The per-effect directory test where it exists; on a tree without
    it, the step-level test is the behavior under judgment."""
    fn = getattr(M, "_effect_is_directory", None)
    return fn(sub, eff) if fn is not None else M._declares_directory(sub)


# --------------------------------------------------------------------- D9
def case_d9_classification():
    check("D9: a file step that names its folder is not a directory step",
          M._declares_directory(T1B3) is False
          and _eff_is_dir(T1B3, T1B3["expected_effects"][0]) is False)
    req = M.render_request(T1B3)
    check("D9: the rendered task asks for the file, not a directory at its path",
          f"Create the file at {SCRATCH}/research.md." in req
          and "Create the directory at" not in req, req)
    tools, what = M._effect_tools(T1B3, T1B3["expected_effects"][0])
    check("D9: the step is equipped with file tools, not directory tools",
          what == "write the file" and "computer_write_file" in tools, f"{what} {tools}")
    claims = TQ._claimed_file_effects(req)
    md = [c for c in claims if c["path"].name == "research.md"]
    check("D9: the task-layer claim for research.md is a file claim",
          bool(md) and md[0].get("target") != "directory", str(claims))

    check("D9: the genuine directory step (T1D step 1) still classifies as one",
          _eff_is_dir(T1D1, T1D1["expected_effects"][0]) is True
          and f"Create the directory at {SCRATCH}/proofdir." in M.render_request(T1D1))
    check("D9: a file created inside a named directory (T1D step 2) is a file step",
          _eff_is_dir(T1D2, T1D2["expected_effects"][0]) is False
          and M._effect_tools(T1D2, T1D2["expected_effects"][0])[1] == "write the file")


def case_d9_fresh_check():
    research = SCRATCH / "research.md"
    research.write_text("# Research\n\nJupiter has 115 confirmed moons (NASA).\n")
    check("D9: the fresh check verifies the written file instead of denying it",
          M._fresh_effect_problems(T1B3) == [], str(M._fresh_effect_problems(T1B3)))
    (SCRATCH / "proofdir").mkdir(exist_ok=True)
    check("D9: the genuine directory verifies as a directory",
          M._fresh_effect_problems(T1D1) == [])
    (SCRATCH / "proofdir" / "note.txt").write_text("baseline proof 7731")
    check("D9: the file inside the named directory verifies",
          M._fresh_effect_problems(T1D2) == [])
    research.unlink()
    check("D9: a genuinely missing required file still fails the fresh check",
          any("research.md to be created, but it is not on disk" in p
              for p in M._fresh_effect_problems(T1B3)))
    research.write_text("# Research\n\nJupiter has 115 confirmed moons (NASA).\n")
    # The graded composition: the task finished with a stale recorded
    # problem; the verdict is composed fresh, so the step verifies.
    task = {"status": "failed",
            "result": "Wrote `research.md` (755 bytes). The write did not confirm its "
                      "full path or contents.\n\nNot finished: the task asked for "
                      "research.md to be created, but it is not on disk"}
    out = M._outcome_from_task(task, dict(T1B3, contract={"inputs": T1B3["inputs"],
                                                          "tools": T1B3["tools"]}))
    check("D9: the graded 'Not finished' composition now verifies the step",
          out["status"] == "done" and out["verified_status"] == "verified", str(out))


async def case_d9_mission_end_to_end():
    """The full T1B shape through the runner: the write step's task
    lands research.md and finishes with the stale recorded problem;
    the mission must complete with the step verified."""
    plan = {"subtasks": [
        {"title": "Research Jupiter’s moons",
         "instruction": "Find a reliable, current source for Jupiter’s confirmed moon count.",
         "expected_effects": [], "inputs": [{"source": "goal"}], "tools": ["web_search"]},
        {"title": "Research Everest summit temperature",
         "instruction": "Find a reliable source for a temperature at Mount Everest’s summit.",
         "expected_effects": [], "inputs": [{"source": "goal"}], "tools": ["web_search"]},
        dict(T1B3),
    ]}

    def write_step(rec):
        (SCRATCH / "research.md").write_text(
            "# Research\n\nJupiter has 115 confirmed moons (NASA Science, "
            "https://science.nasa.gov).\n")
        return {"id": "task-fake3", "title": "", "request": "", "status": "failed",
                "result": "Wrote `research.md` (755 bytes). The write did not confirm its "
                          "full path or contents.\n\nNot finished: the task asked for "
                          "research.md to be created, but it is not on disk"}

    q = FakeQueue([DONE, DONE, write_step])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]), poll_sec=0.01)
    m = M.create_mission("Research two facts and write them into research.md")
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s3 = m["subtasks"][2]
    check("D9: the mission completes with the write step verified",
          m["state"] == "done" and s3["status"] == "done"
          and s3["verified_status"] == "verified",
          f"state={m['state']} sub={s3}")
    check("D9: the step was told to create the file at its path",
          any(f"Create the file at {SCRATCH}/research.md." in c["request"]
              for c in q.created), str([c["request"][:80] for c in q.created]))


# --------------------------------------------------------------------- D10
def _make_shim_venv():
    shim = TMP / "shimvenv"
    if not (shim / "bin" / "python").exists():
        subprocess.run([sys.executable, "-m", "venv", str(shim)],
                       check=True, capture_output=True)
    return shim / "bin" / "python"


def case_d10_detector():
    check("D10: the verbatim -B command is a pytest invocation",
          TQ._is_pytest_invocation(TQ._split_command(PYTEST_CMD)) is True)
    check("D10: flags with values are skipped (-X utf8)",
          TQ._is_pytest_invocation(["python3", "-X", "utf8", "-m", "pytest", "t.py"]) is True)
    check("D10: the run-2 shape still counts",
          TQ._is_pytest_invocation(TQ._split_command("python3 -m pytest -q test_wordfreq.py")) is True)
    check("D10: a non-pytest module run does not count",
          TQ._is_pytest_invocation(TQ._split_command("python3 -B -m pip install x")) is False)
    check("D10: a pinned absolute interpreter still runs verbatim",
          TQ._is_pytest_invocation(["/opt/x/bin/python", "-B", "-m", "pytest", "t.py"]) is False)
    check("D10: the Xcode stub is never respected as pinned",
          TQ._is_pytest_invocation(["/Applications/Xcode.app/Contents/Developer/usr/bin/python3",
                                    "-m", "pytest", "t.py"]) is True)


def case_d10_resolution():
    shim_py = _make_shim_venv()
    proj = TMP / "d10proj"
    proj.mkdir(exist_ok=True)
    (proj / "wordfreq.py").write_text(
        "import sys, collections\n"
        "words = open(sys.argv[1]).read().lower().split()\n"
        "for w, c in collections.Counter(words).most_common(10):\n"
        "    print(f'{w}: {c}')\n")
    (proj / "test_wordfreq.py").write_text(
        "import subprocess, sys\n"
        "def test_counts(tmp_path):\n"
        "    s = tmp_path / 's.txt'; s.write_text('the sun and the sun')\n"
        "    out = subprocess.run([sys.executable, 'wordfreq.py', str(s)],\n"
        "                         capture_output=True, text=True)\n"
        "    assert out.returncode == 0 and 'the: 2' in out.stdout\n"
        "def test_format(tmp_path):\n"
        "    s = tmp_path / 's.txt'; s.write_text('birds birds sun')\n"
        "    out = subprocess.run([sys.executable, 'wordfreq.py', str(s)],\n"
        "                         capture_output=True, text=True)\n"
        "    assert out.stdout.splitlines()[0] == 'birds: 2'\n")
    (proj / "test_broken.py").write_text("def test_nope():\n    assert False\n")

    from app.services import agent_clis
    real_executable, real_path = sys.executable, os.environ.get("PATH", "")
    real_base = TQ.BASE_DIR
    agent_clis._path_cache.update({"at": 0, "path": ""})
    # The graded layout: the app root carries no pytest.py shim, and the
    # app itself runs on a pytest-less interpreter; a pytest-capable
    # python3 exists on the machine's PATH (the system python).
    os.environ["PATH"] = str(Path(real_executable).parent) + os.pathsep + real_path
    sys.executable = str(shim_py)
    TQ.BASE_DIR = TMP
    try:
        py, why = TQ._resolve_pytest_python(proj)
        check("D10: resolution finds a pytest-capable interpreter past the app one",
              py is not None and TQ._python_can_pytest(py), f"py={py} why={why}")
        claim = {"command": PYTEST_CMD, "cwd": proj, "expect_exit": 0,
                 "refused": TQ._run_refusal(PYTEST_CMD)}
        problem = TQ._check_run_claim(claim)
        check("D10: the verbatim command verifies under a pytest-less app interpreter",
              problem is None, str(problem))
        broken = {"command": "python3 -B -m pytest -p no:cacheprovider test_broken.py",
                  "cwd": proj, "expect_exit": 0, "refused": None}
        problem = TQ._check_run_claim(broken)
        check("D10: a failing test still fails, on the run's own output",
              problem is not None and "exited with code 1" in problem
              and "No module named pytest" not in problem, str(problem))
        plain = {"command": "python3 -B wordfreq.py s.txt", "cwd": proj,
                 "expect_exit": 0, "refused": None}
        (proj / "s.txt").write_text("the sun and the sun")
        check("D10: a plain python run effect still runs",
              TQ._check_run_claim(plain) is None)
    finally:
        sys.executable, os.environ["PATH"] = real_executable, real_path
        TQ.BASE_DIR = real_base
        agent_clis._path_cache.update({"at": 0, "path": ""})


# --------------------------------------------------------------------- D11
def case_d11_routing():
    BS.browser_sandbox_service.pending_submit = None
    BS.browser_sandbox_service.latest_mission = {}
    r = RS.AgenticResearcher()
    deliberation = {"plan_steps": [{
        "id": 1, "title": "Test the tool", "tools": ["browser_run_mission"],
        "tool_args": [{"goal": T1A3["instruction"], "start_url": None}]}]}
    assembled = (M.render_request(T1A3) + "\n\nDeclared tools for this step (its "
                 "capability contract): computer_read_file, computer_write_file")
    check("D11: the assembled request declares its contract",
          "Declared tools for this step (its capability contract): "
          "computer_read_file, computer_write_file" in assembled)
    check("D11: a computer-contract step is not routed to the browser surface",
          r._mission_request(assembled, deliberation) is None)
    browser_q = (M.render_request(T1A3) + "\n\nDeclared tools for this step (its "
                 "capability contract): browser_navigate, browser_click")
    check("D11: a browser-contract step still routes to the browser surface",
          r._mission_request(browser_q, deliberation) is not None)
    check("D11: a request with no declared contract follows the deliberation",
          r._mission_request(T1A3["instruction"], deliberation) is not None)
    parse = getattr(RS, "_declared_contract_tools", None)
    check("D11: contract parsing distinguishes absent, empty and named contracts",
          parse is not None
          and parse("no contract here") is None
          and parse("Declared tools for this step (its capability contract): none") == []
          and parse(assembled) == ["computer_read_file", "computer_write_file"])


def case_d11_outcome():
    (SCRATCH / "test_wordfreq.py").write_text("def test_ok():\n    assert True\n")
    (SCRATCH / "wordfreq.py").write_text("print('word: 1')\n")
    sub = dict(T1A3, contract={"inputs": T1A3["inputs"], "tools": T1A3["tools"]})
    task = {"status": "failed", "result": PLEA + "\n\nNot finished: the run failed"}
    out = M._outcome_from_task(task, sub)
    check("D11: the browser plea is not recorded as the step's claimed result",
          out["claimed"] != PLEA and PLEA not in (out["claimed"] or ""), str(out))
    check("D11: the outcome names the routing failure, not the step's work",
          out["status"] == "failed" and any("browser surface" in e
                                            for e in out["evidence"]), str(out))
    research_sub = {"title": "Research", "instruction": "Look it up",
                    "expected_effects": [], "tools": ["web_search"]}
    out2 = M._outcome_from_task({"status": "failed", "result": PLEA}, research_sub)
    check("D11: a no-effects step's question is not judged a routing failure",
          not any("browser surface" in e for e in out2["evidence"]), str(out2))


async def main():
    case_d9_classification()
    case_d9_fresh_check()
    await case_d9_mission_end_to_end()
    case_d10_detector()
    case_d10_resolution()
    case_d11_routing()
    case_d11_outcome()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1 if failed else 0)


asyncio.run(main())
