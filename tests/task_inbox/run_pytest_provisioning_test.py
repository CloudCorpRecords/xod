"""
AGE-76: pytest venv provisioning must not happen inside a timed window.

Record (v1.1.6 gauntlet, adjudicated in V116-SHIP-2026-11-14.md):
run_missions_two_instance cases t1d and t1e failed identically on base
and head on a contended machine, because the suite's verify gate had to
provision a pytest venv inside the suite's 180 second window and lost
that race under load. Provisioning is two subprocess steps (venv
creation, then a pip install of pytest from the network), each allowed
300 seconds in app/services/task_queue.py: on a quiet machine the pip
step alone measured 15 to 56 seconds, and under load it can exceed the
whole window. The missions that failed (t1d, t1e) declare no pytest
run at all: they were collateral, stuck behind the one gate that was
busy provisioning for t1a's test step.

The fix has three parts, all covered here:
  1. Provisioning is single-flight per directory and cached: two
     processes (or threads) resolving a pytest interpreter for the
     same directory must not both provision the same task venv, and
     a venv that already runs pytest is reused, never re-provisioned.
  2. Interpreter probes are memoized per interpreter in-process: a
     contended machine pays one subprocess probe per interpreter, not
     one per candidate per run claim.
  3. The two-instance suite provisions its scratch task venv BEFORE
     its timed window (before the creator spawn, the GO signal, and
     the 180 second deadline), through a helper that is importable
     without running the suite, and calling that helper twice
     provisions at most once.

The provisioning steps are stubbed with a slow fake (a pip step that
sleeps) so the race is deterministic: no network, no wall-clock luck.

Run: .venv/bin/python tests/task_inbox/run_pytest_provisioning_test.py
"""
import os, subprocess, sys, tempfile, threading, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod_pytest_provisioning_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True, exist_ok=True)

import app.services.task_queue as TQ

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


# ---------------------------------------------------------- fake provisioning
class FakeRun:
    """A subprocess.run stand-in simulating venv creation and a slow
    pip install. Counts each step; a probe (-c import pytest) succeeds
    against the fake venv python it creates (a shell script)."""

    def __init__(self):
        self.venv_calls = 0
        self.pip_calls = 0
        self.lock = threading.Lock()
        self.real_run = subprocess.run

    def __call__(self, args, **kwargs):
        args = [str(a) for a in args]
        if "-c" in args and any("import pytest" in a for a in args):
            return subprocess.CompletedProcess(args, 0)
        if "-m" in args and "venv" in args:
            with self.lock:
                self.venv_calls += 1
            venv_dir = Path(args[-1])
            bindir = venv_dir / "bin"
            bindir.mkdir(parents=True, exist_ok=True)
            py = bindir / "python"
            py.write_text("#!/bin/sh\nexit 0\n")
            py.chmod(0o755)
            time.sleep(0.1)
            return subprocess.CompletedProcess(args, 0)
        if "-m" in args and "pip" in args:
            with self.lock:
                self.pip_calls += 1
            time.sleep(0.6)
            return subprocess.CompletedProcess(args, 0)
        return subprocess.CompletedProcess(args, 0)


def case_single_flight_and_cache():
    cwd = TMP / "proj-single"
    cwd.mkdir()
    fake = FakeRun()
    cache = getattr(TQ, "_PYTEST_PROBE_CACHE", None)
    if cache is not None:
        cache.clear()
    subprocess.run = fake
    try:
        barrier = threading.Barrier(2)
        outs = {}

        def worker(slot):
            barrier.wait()
            outs[slot] = TQ._provision_pytest_venv(cwd)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        got = [outs.get(0), outs.get(1)]
        check("single-flight: both concurrent provisioners return the venv python",
              got[0] is not None and got[0] == got[1], f"got={got}")
        check("single-flight: the venv was created exactly once for two provisioners",
              fake.venv_calls == 1, f"venv_calls={fake.venv_calls}")
        check("single-flight: pytest was installed exactly once for two provisioners",
              fake.pip_calls == 1, f"pip_calls={fake.pip_calls}")
        again = TQ._provision_pytest_venv(cwd)
        check("cache: an existing pytest-ready task venv is reused, not re-provisioned",
              again == got[0] and fake.venv_calls == 1 and fake.pip_calls == 1,
              f"again={again} venv_calls={fake.venv_calls} pip_calls={fake.pip_calls}")
    finally:
        subprocess.run = fake.real_run


def case_probe_memoized():
    counter = TMP / "probe-count.txt"
    counter.write_text("")
    fake_py = TMP / "fakebin" / "python"
    fake_py.parent.mkdir(parents=True, exist_ok=True)
    fake_py.write_text(f"#!/bin/sh\necho x >> {counter}\nexit 0\n")
    fake_py.chmod(0o755)
    cache = getattr(TQ, "_PYTEST_PROBE_CACHE", None)
    if cache is not None:
        cache.clear()
    answers = [TQ._python_can_pytest(str(fake_py)) for _ in range(3)]
    invocations = len(counter.read_text().splitlines())
    check("probe: the fake interpreter answers pytest-capable", all(answers), f"{answers}")
    check("probe: repeated probes of one interpreter run it once (memoized)",
          invocations == 1, f"invocations={invocations}")


def case_two_instance_preprovisions():
    suite_path = ROOT / "tests" / "brain" / "run_missions_two_instance_test.py"
    text = suite_path.read_text(errors="replace")
    check("two-instance: the suite exposes a preprovision helper",
          "def preprovision_pytest_venv" in text)
    go_at = text.find('(cfg / "GO").write_text')
    call_at = text.find("preprovision_pytest_venv(cfg")
    deadline_at = text.find("deadline = time.time() + 180")
    check("two-instance: run_config preprovisions before the GO signal and the 180s deadline",
          call_at != -1 and go_at != -1 and deadline_at != -1 and call_at < go_at and call_at < deadline_at,
          f"call={call_at} go={go_at} deadline={deadline_at}")
    check("two-instance: the suite only runs main as a script, so the helper is importable",
          'if __name__ == "__main__":\n    main()' in text)
    if "def preprovision_pytest_venv" not in text:
        check("two-instance: calling the helper twice provisions at most once", False,
              "helper missing")
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("two_instance_suite", suite_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    scratch = TMP / "two-instance-scratch"
    scratch.mkdir()
    provisioned = []
    orig_prov = TQ._provision_pytest_venv

    def counting_provision(cwd):
        provisioned.append(str(cwd))
        venv_dir = Path(cwd) / ".xod-test-venv"
        bindir = venv_dir / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        py = bindir / "python"
        py.write_text("#!/bin/sh\nexit 0\n")
        py.chmod(0o755)
        return str(py)

    TQ._provision_pytest_venv = counting_provision
    try:
        first = mod.preprovision_pytest_venv(scratch)
        second = mod.preprovision_pytest_venv(scratch)
    finally:
        TQ._provision_pytest_venv = orig_prov
    check("two-instance: the helper returns the scratch task venv interpreter",
          first is not None and "xod-test-venv" in str(first), f"first={first}")
    check("two-instance: calling the helper twice provisions at most once",
          len(provisioned) <= 1 and second == first,
          f"provisioned={provisioned} first={first} second={second}")


def case_controls():
    # A project venv that can run pytest is resolved without provisioning.
    proj = TMP / "proj-control"
    (proj / ".venv" / "bin").mkdir(parents=True)
    py = proj / ".venv" / "bin" / "python"
    py.write_text("#!/bin/sh\nexit 0\n")
    py.chmod(0o755)
    cache = getattr(TQ, "_PYTEST_PROBE_CACHE", None)
    if cache is not None:
        cache.clear()
    orig_prov = TQ._provision_pytest_venv
    TQ._provision_pytest_venv = lambda cwd: None
    try:
        got, why = TQ._resolve_pytest_python(proj)
    finally:
        TQ._provision_pytest_venv = orig_prov
    check("control: a project venv interpreter resolves without provisioning",
          got == str(py), f"got={got} why={why}")
    # No usable interpreter anywhere is an environment problem, never a
    # test failure (the run_run_claim_pytest_test contract, kept here).
    orig_probe, orig_prov2 = TQ._python_can_pytest, TQ._provision_pytest_venv
    TQ._python_can_pytest = lambda p: False
    TQ._provision_pytest_venv = lambda cwd: None
    try:
        problem = TQ._check_run_claim({"command": "python3 -m pytest test_wordfreq.py",
                                       "cwd": proj, "expect_exit": 0, "refused": None})
    finally:
        TQ._python_can_pytest, TQ._provision_pytest_venv = orig_probe, orig_prov2
    check("control: no usable interpreter is reported as an environment problem",
          problem is not None and "environment" in problem and "exited with code" not in problem,
          problem or "no problem reported")
    check("control: pytest invocation detection still sees flag forms",
          TQ._is_pytest_invocation(["python3", "-B", "-m", "pytest", "x.py"])
          and not TQ._is_pytest_invocation(["python3", "wordfreq.py"]))


case_single_flight_and_cache()
case_probe_memoized()
case_two_instance_preprovisions()
case_controls()
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
if failed:
    print("FAILED: " + ", ".join(failed), flush=True)
sys.exit(1 if failed else 0)
