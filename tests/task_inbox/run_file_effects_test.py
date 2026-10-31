"""
Task inbox regression test: a task is only done when its file effects are on disk.

The agent sometimes reports a file task finished without calling a single file
tool (proving loop, 2026-10-30: a create that never created, a delete that never
deleted, both marked done). TaskQueue._run now verifies the effects on disk
before finishing: recorded file ops from the activity log must match the disk,
and file effects the request itself spells out must hold. A mismatch gets one
honest retry with the problem named, then the task fails with the reason.

The fake researchers below never touch tools: one just claims success, the
other performs the effect only when the verification retry asks again.

Run: .venv/bin/python tests/task_inbox/run_file_effects_test.py
"""
import asyncio, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
TMP = Path(tempfile.mkdtemp(prefix="xod_file_effects_"))
os.environ["XOD_DATASET_DB"] = str(_DATA / "test_runs" / "flywheel_test.db")
import app.services.task_queue as TQ
import app.services.browser_sandbox as BS
import app.agent.researcher as RS

TQ.DB_PATH, TQ.SHOTS_DIR = TMP / "tasks.db", TMP / "shots"
TQ.notify = lambda title, msg: print(f"   [notification] {title}: {msg}", flush=True)
if hasattr(TQ, "_phone"):
    TQ._phone = lambda task, kind: None


async def _boom():
    raise RuntimeError("Background Chrome failed to start or open remote debugging port.")


BS.browser_sandbox_service.ensure_browser_running = _boom


class _LyingResearcher:
    """Claims the work is done; never does any of it."""
    calls = 0

    async def run_research_stream(self, messages):
        type(self).calls += 1
        yield {"type": "content", "content": "All done."}


class _RetryHeroResearcher:
    """Lies on the first attempt, actually does the effect on the retry."""
    calls = 0
    effect = None   # callable, set per case

    async def run_research_stream(self, messages):
        type(self).calls += 1
        if type(self).calls >= 2 and type(self).effect is not None:
            type(self).effect()
        yield {"type": "content", "content": "All done."}


async def _run_one(q, request):
    tid = q.create(request)["id"]
    await q._run(tid)
    return q.get(tid)


async def main():
    q = TQ.TaskQueue()

    # --- the claim parser, on the proving suite's own phrasings
    create_req = ("In my home folder there is a folder named xod-eval-sandbox (create it if it does not exist). "
                  "Inside it, create a plain text file named proof.txt whose entire contents are exactly this, "
                  "with no extra words and no extra lines: XOD PROOF TESTNONCE 42")
    claims = TQ._claimed_file_effects(create_req)
    assert len(claims) == 1 and claims[0]["kind"] == "write", claims
    assert claims[0]["path"] == Path.home() / "xod-eval-sandbox" / "proof.txt", claims
    assert claims[0]["content"] == "XOD PROOF TESTNONCE 42", claims

    delete_req = ("Delete the file named proof.txt inside the xod-eval-sandbox folder in my home folder. "
                  "Do not delete the folder itself or anything else. Reply when it is gone.")
    claims = TQ._claimed_file_effects(delete_req)
    assert len(claims) == 1 and claims[0]["kind"] == "delete", claims
    assert claims[0]["path"] == Path.home() / "xod-eval-sandbox" / "proof.txt", claims

    assert TQ._claimed_file_effects(
        "Automated eval task. Do not use any tools, do not browse, and do not create or change any files. "
        "Reply with exactly this text and nothing else: XOD-EVAL-TASK-TEST") == []
    assert TQ._claimed_file_effects(
        "Automated eval task. Do not use any tools. Compute 355 - 128 + 51 and reply with just the number, "
        "nothing else.") == []
    assert TQ._claimed_file_effects(f"Do not delete the file at {TMP}/keep.txt; just read it.") == []
    print("PASS: claim parser reads the suite phrasings and respects negations", flush=True)

    # --- a delete the agent only claims: retried once, then failed honestly
    victim = TMP / "victim.txt"
    victim.write_text("still here")
    _LyingResearcher.calls = 0
    RS.AgenticResearcher = _LyingResearcher
    t = await _run_one(q, f"Delete the file at {victim}. Reply when it is gone.")
    print(f"   delete-lie: status={t['status']} calls={_LyingResearcher.calls} result={t.get('result')!r}", flush=True)
    assert _LyingResearcher.calls == 2, "expected exactly one verification retry"
    assert t["status"] == "failed", f"expected failed, got {t['status']}"
    assert "Not finished" in (t.get("result") or ""), t.get("result")
    assert victim.exists(), "the lying run must not have removed the file"
    print("PASS: a claimed delete with no effect fails instead of finishing", flush=True)

    # --- the retry actually rescues a delete the agent then performs
    _RetryHeroResearcher.calls = 0
    _RetryHeroResearcher.effect = lambda: victim.unlink(missing_ok=True)
    RS.AgenticResearcher = _RetryHeroResearcher
    t = await _run_one(q, f"Delete the file at {victim}. Reply when it is gone.")
    print(f"   delete-hero: status={t['status']} calls={_RetryHeroResearcher.calls}", flush=True)
    assert t["status"] == "done", f"expected done, got {t['status']}: {t.get('result')}"
    assert not victim.exists()
    print("PASS: the verification retry turns a claimed delete into a real one", flush=True)

    # --- a create with dictated contents the agent only claims: failed honestly
    target = TMP / "made.txt"
    _LyingResearcher.calls = 0
    RS.AgenticResearcher = _LyingResearcher
    t = await _run_one(q, f"Create a plain text file at {target} whose entire contents are exactly this: HELLO 42")
    print(f"   create-lie: status={t['status']} result={t.get('result')!r}", flush=True)
    assert t["status"] == "failed" and "Not finished" in (t.get("result") or ""), t
    assert not target.exists()
    print("PASS: a claimed create with no file fails instead of finishing", flush=True)

    # --- the retry rescues the create, byte for byte
    _RetryHeroResearcher.calls = 0
    _RetryHeroResearcher.effect = lambda: target.write_bytes(b"HELLO 42")
    RS.AgenticResearcher = _RetryHeroResearcher
    t = await _run_one(q, f"Create a plain text file at {target} whose entire contents are exactly this: HELLO 42")
    print(f"   create-hero: status={t['status']} calls={_RetryHeroResearcher.calls}", flush=True)
    assert t["status"] == "done", f"expected done, got {t['status']}: {t.get('result')}"
    assert target.read_bytes() == b"HELLO 42"
    print("PASS: the verification retry turns a claimed create into a real one", flush=True)

    # --- wrong contents on disk are caught even when a file exists
    target.write_bytes(b"SOMETHING ELSE")
    _LyingResearcher.calls = 0
    RS.AgenticResearcher = _LyingResearcher
    t = await _run_one(q, f"Create a plain text file at {target} whose entire contents are exactly this: HELLO 42")
    print(f"   create-wrong-content: status={t['status']} result={t.get('result')!r}", flush=True)
    assert t["status"] == "failed" and "Not finished" in (t.get("result") or ""), t
    print("PASS: dictated contents are compared, not just existence", flush=True)

    # --- a directory create is a directory claim (hardening round, 2026-10-30:
    # a mission step that created its project directory exactly as asked was
    # contradicted because the check required is_file() on the directory path)
    proj = TMP / "projdir"
    old_style = f"Create a new directory at {proj} for the project.\nCreate the file at {proj}."
    claims = TQ._claimed_file_effects(old_style)
    assert len(claims) == 1 and claims[0]["kind"] == "write" and claims[0]["target"] == "directory", claims
    new_style = f"Create the directory at {proj}."
    claims = TQ._claimed_file_effects(new_style)
    assert len(claims) == 1 and claims[0]["target"] == "directory", claims
    file_claims = TQ._claimed_file_effects(f"Create a plain text file at {TMP}/plain.txt")
    assert len(file_claims) == 1 and file_claims[0]["target"] == "file", file_claims
    print("PASS: directory claims parse as directories, file claims stay files", flush=True)

    # --- a path named only as the container of another claimed path is not
    # itself a claim (H6, mission-1938ea6dd9: step 2 "Create a file named
    # proof.txt inside /abs/dir ..." manufactured a file claim for the dir,
    # which is_file() could never satisfy, and the step was contradicted
    # while proof.txt sat on disk exactly as specified)
    cont = TMP / "xod-hardening-proof"
    inside_req = (f"Create a file named proof.txt inside {cont} containing the line: hardening proof.\n"
                  f"Create the file at {cont}/proof.txt with exact text: hardening proof")
    claims = TQ._claimed_file_effects(inside_req)
    assert len(claims) == 1, claims
    assert claims[0]["kind"] == "write" and claims[0]["target"] == "file", claims
    assert claims[0]["path"] == cont / "proof.txt", claims
    assert claims[0]["content"] == "hardening proof", claims
    print("PASS: a container-only path makes no claim of its own", flush=True)

    # --- one step that genuinely creates BOTH a directory and a file inside
    # it keeps both claims, each checked as what it is
    both = TMP / "bothdir"
    claims = TQ._claimed_file_effects(
        f"Create the directory at {both}.\nCreate the file at {both}/inner.txt.")
    assert len(claims) == 2, claims
    by_target = {c["target"]: c for c in claims}
    assert by_target["directory"]["path"] == both, claims
    assert by_target["file"]["path"] == both / "inner.txt", claims
    print("PASS: a step creating a directory and its file keeps both claims", flush=True)

    class _DirResearcher:
        """Does exactly what the task asked: makes the directory."""
        target = None

        async def run_research_stream(self, messages):
            Path(type(self).target).mkdir(parents=True, exist_ok=True)
            yield {"type": "content", "content": "Directory created."}

    _DirResearcher.target = proj
    RS.AgenticResearcher = _DirResearcher
    t = await _run_one(q, old_style)
    print(f"   directory-honest: status={t['status']}", flush=True)
    assert t["status"] == "done", f"expected done, got {t['status']}: {t.get('result')}"
    assert proj.is_dir()
    print("PASS: a created directory verifies as done, not contradicted", flush=True)

    proj2 = TMP / "neverdir"
    _LyingResearcher.calls = 0
    RS.AgenticResearcher = _LyingResearcher
    t = await _run_one(q, f"Create a new directory at {proj2} for the project.")
    print(f"   directory-lie: status={t['status']} result={t.get('result')!r}", flush=True)
    assert t["status"] == "failed" and "Not finished" in (t.get("result") or ""), t
    assert not proj2.exists()
    print("PASS: a claimed directory that does not exist still fails honestly", flush=True)

    # --- the H6 shape end to end through the queue: the file inside its
    # named container verifies; a claimed one that was never made still fails
    class _ContainerResearcher:
        """Makes the container directory and the file inside it, as asked."""
        base = None

        async def run_research_stream(self, messages):
            d = Path(type(self).base)
            d.mkdir(parents=True, exist_ok=True)
            (d / "proof.txt").write_text("hardening proof")
            yield {"type": "content", "content": "Created proof.txt inside the folder."}

    cont2 = TMP / "container-case"
    _ContainerResearcher.base = cont2
    RS.AgenticResearcher = _ContainerResearcher
    t = await _run_one(q, f"Create a file named proof.txt inside {cont2} containing the line: hardening proof.\n"
                          f"Create the file at {cont2}/proof.txt with exact text: hardening proof")
    print(f"   container-honest: status={t['status']}", flush=True)
    assert t["status"] == "done", f"expected done, got {t['status']}: {t.get('result')}"
    assert (cont2 / "proof.txt").read_text() == "hardening proof"
    print("PASS: a file created inside its named container verifies as done", flush=True)

    cont3 = TMP / "container-lie"
    _LyingResearcher.calls = 0
    RS.AgenticResearcher = _LyingResearcher
    t = await _run_one(q, f"Create a file named proof.txt inside {cont3} containing the line: hardening proof.\n"
                          f"Create the file at {cont3}/proof.txt with exact text: hardening proof")
    print(f"   container-lie: status={t['status']} result={t.get('result')!r}", flush=True)
    assert t["status"] == "failed" and "Not finished" in (t.get("result") or ""), t
    assert not (cont3 / "proof.txt").exists()
    print("PASS: a claimed file inside a container still fails when nothing was made", flush=True)

    # --- the mirror image (proof-2 re-run, mission-0d528a2c13): a sentence
    # that mentions the file by name while calling its CONTAINER a directory
    # must not classify the file's own claim as a directory claim
    cont4 = TMP / "xod-hardening-proof"
    mirror_req = (f"Create a file proof.txt inside the new directory containing the line hardening proof.\n"
                  f"Create the file at {cont4}/proof.txt with exact text: hardening proof")
    claims = TQ._claimed_file_effects(mirror_req)
    assert len(claims) == 1, claims
    assert claims[0]["path"] == cont4 / "proof.txt" and claims[0]["target"] == "file", claims
    print("PASS: a container called a directory does not make its file a directory claim", flush=True)

    # --- the renderer's fall-through shape (content that rides on the
    # instruction) yields an existence-only claim, never an exact-text one
    ride_req = (f"Create the file at {cont4}/proof.txt. Its contents should be:\n"
                f"hardening proof.\n")
    claims = TQ._claimed_file_effects(ride_req)
    assert len(claims) == 1 and claims[0]["path"] == cont4 / "proof.txt", claims
    assert claims[0]["content"] is None, claims
    print("PASS: content riding on the instruction is checked for existence only", flush=True)

    _ContainerResearcher.base = cont4
    RS.AgenticResearcher = _ContainerResearcher
    t = await _run_one(q, mirror_req)
    print(f"   container-mirror: status={t['status']}", flush=True)
    assert t["status"] == "done", f"expected done, got {t['status']}: {t.get('result')}"
    print("PASS: a file inside a directory the words call new verifies as done", flush=True)

    # --- run effects: the gate executes a declared command and its exit
    # code becomes part of the verdict (H4 remainder: existence is not proof
    # that produced code works)
    class _EffectResearcher:
        """Performs its effect on every attempt, then claims success."""
        calls = 0
        effect = None

        async def run_research_stream(self, messages):
            type(self).calls += 1
            if type(self).effect is not None:
                type(self).effect()
            yield {"type": "content", "content": "All done."}

    _TEST_HARNESS = '''

import sys as _sys, traceback as _tb


def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS " + name)
        except Exception:
            failed += 1
            print("FAIL " + name)
            _tb.print_exc()
    print("%d passed, %d failed" % (len(tests) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    _sys.exit(_main())
'''
    _FAILING_TESTS = "\n".join(
        f"def test_case_{i}():\n    assert undefined_helper_{i}() == {i}\n" for i in range(1, 6)
    ) + _TEST_HARNESS
    _PASSING_TESTS = ("def add(a, b):\n    return a + b\n\n\n"
                      + "\n".join(f"def test_case_{i}():\n    assert add({i}, 1) == {i + 1}\n"
                                  for i in range(1, 6))
                      + _TEST_HARNESS)
    py = sys.executable

    # parser level: the canonical phrasing parses, anchored by the file claim
    probe_dir = TMP / "probe-proj"
    probe_req = (f"Create the file at {probe_dir}/test_probe.py.\n"
                 f"Run exactly this command from the directory {probe_dir} "
                 f"and require exit code 0: {py} test_probe.py")
    file_claims = TQ._claimed_file_effects(probe_req)
    assert len(file_claims) == 1 and file_claims[0]["path"] == probe_dir / "test_probe.py", file_claims
    run_claims = TQ._claimed_run_effects(probe_req, [c["path"] for c in file_claims])
    assert len(run_claims) == 1 and run_claims[0]["refused"] is None, run_claims
    assert run_claims[0]["cwd"] == probe_dir and run_claims[0]["expect_exit"] == 0, run_claims
    print("PASS: a run claim parses from the canonical phrasing and anchors to its file claim", flush=True)

    # (a) THE HARDENING PROBE CASE: a pytest file whose 5 tests all fail
    # with NameError, plus a run claim executing it, must be contradicted,
    # with the nonzero exit and the failure output in the evidence
    def _write_failing():
        probe_dir.mkdir(parents=True, exist_ok=True)
        (probe_dir / "test_probe.py").write_text(_FAILING_TESTS)

    _EffectResearcher.calls = 0
    _EffectResearcher.effect = _write_failing
    RS.AgenticResearcher = _EffectResearcher
    t = await _run_one(q, probe_req)
    print(f"   run-probe-failing: status={t['status']} result={t.get('result')!r}", flush=True)
    assert t["status"] == "failed" and "Not finished" in (t.get("result") or ""), t
    assert "exited with code 1" in (t.get("result") or ""), t.get("result")
    assert "NameError" in (t.get("result") or ""), t.get("result")
    print("PASS: a test file whose tests all fail is contradicted by its run claim", flush=True)

    # (b) the same shape with passing tests verifies
    pass_dir = TMP / "pass-proj"

    def _write_passing():
        pass_dir.mkdir(parents=True, exist_ok=True)
        (pass_dir / "test_ok.py").write_text(_PASSING_TESTS)

    _EffectResearcher.calls = 0
    _EffectResearcher.effect = _write_passing
    RS.AgenticResearcher = _EffectResearcher
    t = await _run_one(q, f"Create the file at {pass_dir}/test_ok.py.\n"
                          f"Run exactly this command from the directory {pass_dir} "
                          f"and require exit code 0: {py} test_ok.py")
    print(f"   run-passing: status={t['status']} calls={_EffectResearcher.calls}", flush=True)
    assert t["status"] == "done", f"expected done, got {t['status']}: {t.get('result')}"
    print("PASS: a test file whose tests pass verifies by its run claim", flush=True)

    # (c) a run claim whose cwd is outside every declared path is refused
    # and never executed (the command would create a marker if it ran)
    anchor_dir = TMP / "anchor-proj"
    outside_dir = TMP / "outside-dir"
    outside_dir.mkdir(parents=True, exist_ok=True)
    marker = outside_dir / "marker.txt"
    _EffectResearcher.effect = lambda: (anchor_dir.mkdir(parents=True, exist_ok=True),
                                        (anchor_dir / "anchor.txt").write_text("here"))
    RS.AgenticResearcher = _EffectResearcher
    t = await _run_one(q, f"Create the file at {anchor_dir}/anchor.txt.\n"
                          f"Run exactly this command from the directory {outside_dir} "
                          f"and require exit code 0: {py} -c \"open('marker.txt', 'w').write('x')\"")
    print(f"   run-unanchored: status={t['status']} result={t.get('result')!r}", flush=True)
    assert t["status"] == "failed" and "refused" in (t.get("result") or ""), t
    assert not marker.exists(), "a refused run claim must never execute"
    print("PASS: an unanchored run claim is refused and not executed", flush=True)

    # (d) a command with shell metacharacters is refused and never executed
    chain_dir = TMP / "chain-proj"
    chained = chain_dir / "chained.txt"

    def _write_chain_anchor():
        chain_dir.mkdir(parents=True, exist_ok=True)
        (chain_dir / "test_ok.py").write_text(_PASSING_TESTS)

    _EffectResearcher.effect = _write_chain_anchor
    RS.AgenticResearcher = _EffectResearcher
    t = await _run_one(q, f"Create the file at {chain_dir}/test_ok.py.\n"
                          f"Run exactly this command from the directory {chain_dir} "
                          f"and require exit code 0: {py} test_ok.py ; {py} -c "
                          f"\"open('chained.txt', 'w').write('x')\"")
    print(f"   run-metachar: status={t['status']} result={t.get('result')!r}", flush=True)
    assert t["status"] == "failed" and "refused" in (t.get("result") or ""), t
    assert "metacharacter" in (t.get("result") or ""), t.get("result")
    assert not chained.exists(), "a refused run claim must never execute"
    print("PASS: a chained command is refused as a shell metacharacter and not executed", flush=True)

    # (e) a run that outlives the timeout is contradicted, with the timeout
    # stated (the cap is injectable so the test does not wait 120 seconds)
    slow_dir = TMP / "slow-proj"
    _EffectResearcher.effect = lambda: (slow_dir.mkdir(parents=True, exist_ok=True),
                                        (slow_dir / "anchor.txt").write_text("here"))
    RS.AgenticResearcher = _EffectResearcher
    old_timeout = TQ.RUN_TIMEOUT_SEC
    TQ.RUN_TIMEOUT_SEC = 1
    try:
        t = await _run_one(q, f"Create the file at {slow_dir}/anchor.txt.\n"
                              f"Run exactly this command from the directory {slow_dir} "
                              f"and require exit code 0: {py} -c \"import time; time.sleep(30)\"")
    finally:
        TQ.RUN_TIMEOUT_SEC = old_timeout
    print(f"   run-timeout: status={t['status']} result={t.get('result')!r}", flush=True)
    assert t["status"] == "failed" and "timed out after 1 seconds" in (t.get("result") or ""), t
    print("PASS: a run that outlives its timeout is contradicted with the timeout stated", flush=True)

    # --- the recorded-ops layer: what the activity log says must match the disk
    import sqlite3, time as _time
    import app.services.activity as ACT
    real_act_db = ACT.DB
    ACT.DB = TMP / "activity.db"
    try:
        ghost = TMP / "ghost.txt"          # written per the log, missing on disk
        back = TMP / "back.txt"            # deleted per the log, still on disk
        back.write_text("i live")
        moved_to = TMP / "moved-here.txt"  # move destination per the log, missing
        conn = sqlite3.connect(str(ACT.DB))
        conn.execute("CREATE TABLE IF NOT EXISTS activity (id TEXT PRIMARY KEY, ts REAL, origin TEXT, "
                     "tool TEXT, title TEXT, detail TEXT, ok INTEGER, undo TEXT, undoable INTEGER, "
                     "undone_at REAL, note TEXT)")
        rows = [
            ("act-t1", '[{"kind": "remove_created", "path": "%s", "after_sha": "x"}]' % ghost),
            ("act-t2", '[{"kind": "untrash", "path": "%s", "copy": "/tmp/snap"}]' % back),
            ("act-t3", '[{"kind": "move_back", "from": "%s", "now_at": "%s"}]' % (TMP / "orig.txt", moved_to)),
        ]
        for rid, undo in rows:
            conn.execute("INSERT INTO activity VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (rid, _time.time(), "task:task-verifyops", "computer_write_file", "t", "", 1, undo, 1, None, ""))
        conn.commit()
        conn.close()
        fake_task = {"id": "task-verifyops", "request": "Reply with exactly: ok"}
        problems = q._verify_file_effects(fake_task, _time.time() - 60)
        print(f"   recorded-ops problems: {problems}", flush=True)
        assert len(problems) == 3, problems
        ghost.write_text("boo")
        back.unlink()
        moved_to.write_text("here")
        assert q._verify_file_effects(fake_task, _time.time() - 60) == []
        print("PASS: recorded file ops are checked against the disk, last state wins", flush=True)
    finally:
        ACT.DB = real_act_db

    # --- a reply-only task is not gated at all
    _LyingResearcher.calls = 0
    RS.AgenticResearcher = _LyingResearcher
    t = await _run_one(q, "Reply with exactly: ok-no-files")
    print(f"   reply-only: status={t['status']} calls={_LyingResearcher.calls}", flush=True)
    assert t["status"] == "done" and _LyingResearcher.calls == 1, t
    print("PASS: reply-only tasks finish untouched", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
