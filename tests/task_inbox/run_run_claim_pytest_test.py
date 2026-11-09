"""
Run-claim fix (2026-11-08): a pytest run step must run on an interpreter
that can actually run pytest.

Live record (Rene's Mac, shipped 1.0.2, missions e0c5872dea and
c9a6185f36): step 3 declared the run "python3 -m pytest
test_wordfreq.py". The gate executed it verbatim; the app process's
PATH resolved the bare python3 to /Applications/Xcode.app's python3
(3.9, no pytest), so the run exited 1 with "No module named pytest"
and the step failed as if its tests had failed. The tests themselves
were never exercised: it was an environment failure wearing a test
failure's clothes.

The run check now resolves a sane interpreter for a bare
"pythonX -m pytest" command: the project's own venv first, then the
interpreter the app itself runs on, then a provisioned task venv
(.xod-test-venv in the run's directory). An interpreter under
Xcode.app is disqualified outright. When no interpreter can run
pytest, the problem text says so as an environment problem, never as
a test failure. An interpreter pinned by absolute path (and not the
Xcode stub) is respected verbatim, and non-pytest runs are untouched.

Covered here, with a pytest-less python3 shimmed onto PATH to stand
in for the Xcode stub (this machine's own python3 has pytest, so the
stub has to be staged):
  - the live shape: bare "python3 -m pytest ..." passes on a good
    test file instead of dying with "No module named pytest".
  - the project's own .venv interpreter is the one that runs.
  - when no interpreter can run pytest, the problem is reported as
    an environment problem, not an exit-code failure.
  - controls: a pinned absolute interpreter runs verbatim; a plain
    non-pytest run is unchanged, including its exit-code failure.

Run: .venv/bin/python tests/task_inbox/run_run_claim_pytest_test.py
"""
import os, subprocess, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod_runclaim_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)

import app.services.task_queue as TQ

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


# A pytest-less python3, standing in for the Xcode stub: a bare venv
# python (it has no pytest and never will), symlinked as "python3"
# into a shim dir the cases put first on PATH.
SHIM = TMP / "shim"
SHIM.mkdir()
subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(TMP / "stubenv")],
               check=True, capture_output=True)
(SHIM / "python3").symlink_to(TMP / "stubenv" / "bin" / "python")

PROJ = TMP / "proj"
PROJ.mkdir()
(PROJ / "wordfreq.py").write_text("def top_word(text):\n    return text.split()[0]\n")
(PROJ / "test_wordfreq.py").write_text(
    "from wordfreq import top_word\n\n\ndef test_top_word():\n    assert top_word('a b a') == 'a'\n")


def claim(command, cwd=PROJ):
    return {"command": command, "cwd": cwd, "expect_exit": 0, "refused": None}


def with_stub_path(fn):
    old = os.environ.get("PATH", "")
    os.environ["PATH"] = str(SHIM) + os.pathsep + old
    try:
        return fn()
    finally:
        os.environ["PATH"] = old


# --------------------------------------------------------------- the live shape
def case_live_shape():
    def run():
        return TQ._check_run_claim(claim("python3 -m pytest test_wordfreq.py"))
    problem = with_stub_path(run)
    check("live shape: bare python3 -m pytest passes on a good test file",
          problem is None, problem or "")


def case_project_venv_wins():
    venv_bin = PROJ / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    used = PROJ / "used.txt"
    wrapper = venv_bin / "python"
    wrapper.write_text("#!/bin/sh\n"
                       f"echo wrapper >> {used}\n"
                       f"exec {sys.executable} \"$@\"\n")
    wrapper.chmod(0o755)

    def run():
        return TQ._check_run_claim(claim("python3 -m pytest test_wordfreq.py"))
    problem = with_stub_path(run)
    check("project venv: the run passes", problem is None, problem or "")
    check("project venv: the project's own interpreter is the one that ran",
          used.exists() and "wrapper" in used.read_text(), f"used.txt exists: {used.exists()}")


def case_environment_problem():
    if not (hasattr(TQ, "_python_can_pytest") and hasattr(TQ, "_provision_pytest_venv")):
        check("environment: the resolver seam exists", False, "no resolver helpers on task_queue")
        return
    orig_probe, orig_prov = TQ._python_can_pytest, TQ._provision_pytest_venv
    TQ._python_can_pytest = lambda py: False
    TQ._provision_pytest_venv = lambda cwd: None
    try:
        def run():
            return TQ._check_run_claim(claim("python3 -m pytest test_wordfreq.py"))
        problem = with_stub_path(run)
    finally:
        TQ._python_can_pytest, TQ._provision_pytest_venv = orig_probe, orig_prov
    check("environment: no usable interpreter is reported as a problem",
          problem is not None, "no problem reported")
    if problem:
        check("environment: the problem names the environment, not a test failure",
              "environment" in problem and "not a test failure" in problem
              and "exited with code" not in problem, problem)


def case_controls():
    problem = TQ._check_run_claim(claim(f"{sys.executable} -m pytest test_wordfreq.py"))
    check("control: a pinned absolute interpreter runs verbatim and passes",
          problem is None, problem or "")
    problem = TQ._check_run_claim(claim("ls"))
    check("control: a plain non-pytest run is untouched", problem is None, problem or "")
    problem = TQ._check_run_claim(claim("false"))
    check("control: a plain failing run still reports its exit code",
          problem is not None and "exited with code 1" in problem, problem or "")
    if hasattr(TQ, "_is_disqualified_python"):
        check("control: the Xcode stub interpreter is disqualified",
              TQ._is_disqualified_python("/Applications/Xcode.app/Contents/Developer/usr/bin/python3")
              and not TQ._is_disqualified_python("/usr/bin/python3")
              and not TQ._is_disqualified_python(sys.executable), "")
    else:
        check("control: the Xcode stub interpreter is disqualified", False, "no _is_disqualified_python")


case_live_shape()
case_project_venv_wins()
case_environment_problem()
case_controls()
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
if failed:
    print("FAILED: " + ", ".join(failed), flush=True)
sys.exit(1 if failed else 0)
