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
