"""
Self-healing: break things on purpose and check Xod recovers by itself.

  A  result checks catch fake successes (blank image, missing file, failed command, empty search)
  B  error classification
  C  wrong file name -> repair finds/reads the right file (real model decides the fix)
  D  Xod's browser killed -> doctor restarts it and the navigation succeeds
  E  side-effect tool fails -> NOT re-run, reported instead
  F  hung task in the inbox -> doctor stops it and marks it failed
  G  chat model unloaded -> doctor notices and reloads it
  H  repeat failure uses the lesson learned from the first fix

Test data stays out of the real training set and lessons file.
"""
import asyncio, json, os, subprocess, sys, tempfile, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Its own data folder: its own browser to kill and its own task list, never your real Xod's.
os.environ["XOD_DATA_DIR"] = os.environ.get("XOD_TEST_DATA_DIR") or tempfile.mkdtemp(prefix="xod-heal-")
import sys as _sys; _sys.path.insert(0, str(ROOT)); from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
RUN = _DATA / "test_runs" / "self_heal"
RUN.mkdir(parents=True, exist_ok=True)
os.environ["XOD_DATASET_DB"] = str(RUN / "dataset.db")
sys.path.insert(0, str(ROOT))

from app.agent import healing  # noqa: E402
from app.services import doctor as doctor_mod  # noqa: E402
doctor_mod.LESSONS_PATH = RUN / "lessons.json"
if doctor_mod.LESSONS_PATH.exists():
    doctor_mod.LESSONS_PATH.unlink()
from app.services.doctor import doctor  # noqa: E402
from app.agent.researcher import AgenticResearcher  # noqa: E402

results = []


def report(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


async def run_healing(tool, args, query):
    """Same path chat and tasks use: researcher._run_tool_healing."""
    r = AgenticResearcher()
    events, final = [], None
    async for ev in r._run_tool_healing(tool, args, f"t_{uuid.uuid4().hex[:4]}", query, None):
        if ev["type"] == "_final":
            final = ev
        else:
            events.append(ev)
            if ev["type"] in ("status", "tool_call"):
                print("     ·", ev.get("content") or f"call {ev['name']} {json.dumps(ev.get('args'))[:120]}", flush=True)
    return final, events


def test_checks():
    from PIL import Image
    blank = RUN / "blank.png"
    Image.new("RGB", (512, 512), (0, 0, 0)).save(blank)
    cases = [
        ("blank image", healing.check_result("media_generate_image", {}, {"success": True, "output_path": str(blank)}), "bad_output"),
        ("missing file", healing.check_result("media_generate_video", {}, {"success": True, "output_path": "/nope/x.mp4"}), "bad_output"),
        ("command exit 1", healing.check_result("computer_run_command", {}, {"exit_code": 1, "stderr": "boom"}), "error"),
        ("empty search", healing.check_result("web_search", {}, {"results": []}), "empty"),
        ("error dict", healing.check_result("tavily_search", {}, {"error": "Tool execution failed: x"}), "error"),
        ("real success", healing.check_result("web_search", {}, {"results": [{"title": "a"}]}), None),
    ]
    for name, chk, want in cases:
        report(f"A check: {name}", chk["problem"] == want, f"→ {chk['problem']} {chk['detail'][:60]}")


def test_classify():
    cases = [("Tavily API key is not configured", "missing_key"), ("HTTP 429 Too Many Requests", "rate_limit"),
             ("MPS backend out of memory", "memory"), ("No models loaded in LM Studio", "model_unavailable"),
             ("websocket connection to devtools closed", "browser_down"), ("[Errno 2] No such file or directory: 'x'", "not_found"),
             ("Just a moment... Cloudflare captcha", "blocked"), ("missing required argument 'url'", "bad_args")]
    for text, want in cases:
        got = healing.classify(text)["kind"]
        report(f"B classify: {want}", got == want, f"→ {got}")


async def test_wrong_filename():
    d = RUN / "notes_dir"
    d.mkdir(exist_ok=True)
    (d / "meeting_notes.txt").write_text("The launch moved to Thursday. Budget approved: $12,000.\n" * 3)
    final, ev = await run_healing("computer_read_file", {"path": str(d / "meeting_note.txt")},
                                  f"What does my file {d}/meeting_notes.txt say?")
    res = final["result"]
    text = json.dumps(res)
    ok = "Thursday" in text or "meeting_notes.txt" in text
    report("C wrong file name repaired", ok, f"final tool {final['tool']} {json.dumps(final['args'])[:100]}")


async def test_browser_killed():
    from app.services.browser_sandbox import DEBUG_PORT, browser_sandbox_service
    import psutil
    await browser_sandbox_service.navigate("about:blank")           # make sure it's running, then kill it (any system)
    for p in psutil.process_iter(["cmdline"]):
        try:
            if f"--remote-debugging-port={DEBUG_PORT}" in (p.info["cmdline"] or []):
                p.kill()
        except psutil.Error:
            pass
    await asyncio.sleep(2)
    final, ev = await run_healing("browser_navigate", {"url": "https://example.com"}, "Open example.com")
    res = final["result"]
    ok = healing.check_result(final["tool"], final["args"], res)["ok"] and await doctor._browser_reachable()
    report("D browser killed -> recovered", ok, f"{str(res)[:120]}")


async def test_side_effect_not_rerun():
    calls = []

    async def run(name, a):
        calls.append(name)
        return {"success": True}
    rep = healing.Repairer(run, "write a file", [{"function": {"name": "computer_write_file", "parameters": {}}}], [])
    out = None
    async for e in rep.repair("computer_write_file", {"path": "/x"}, {"success": False, "error": "Permission denied"}, "c1"):
        if e["type"] == "_repair_done":
            out = e
    report("E side-effect tool not re-run", not calls and out and not out["ok"], f"calls={calls}")


async def test_hung_task():
    from app.services import task_queue as tq
    tid = f"task_test_{uuid.uuid4().hex[:6]}"
    c = tq._conn()
    old = time.time() - 4000
    c.execute("INSERT INTO tasks (id, created_at, updated_at, title, request, status) VALUES (?,?,?,?,?, 'working')",
              (tid, old, old, "TEST hung task", "test"))
    c.commit(); c.close()
    rep = await doctor.check(apply_safe_fixes=True)
    t = tq.task_queue.get(tid)
    report("F hung task stopped", t["status"] == "failed", next(c["detail"] for c in rep["checks"] if c["name"] == "tasks")[:120])
    tq.task_queue.delete(tid)


async def test_model_unloaded():
    lms = str(Path.home() / ".lmstudio" / "bin" / ("lms.exe" if os.name == "nt" else "lms"))
    if not os.path.exists(lms):
        print("SKIP  G chat model unloaded -> reloaded  (needs LM Studio; this computer doesn't have it)", flush=True)
        return
    subprocess.run([lms, "unload", "--all"], capture_output=True)
    await asyncio.sleep(2)
    chk = await doctor._check_chat_model()
    noticed = chk["level"] == "fail" and chk["fix"] == "load_chat_model"
    fixed = await doctor.auto_fix("model_unavailable")
    chk2 = await doctor._check_chat_model()
    report("G chat model unloaded -> reloaded", noticed and chk2["ok"], f"noticed={noticed} fix={fixed!r} now={chk2['detail']}")


async def test_lesson_used():
    lessons = doctor.lessons_for("computer_read_file", "not_found")
    report("H lesson recorded from C", bool(lessons), json.dumps(lessons)[:160])


async def main():
    test_checks()
    test_classify()
    await test_side_effect_not_rerun()
    await test_wrong_filename()
    await test_lesson_used()
    await test_browser_killed()
    await test_hung_task()
    await test_model_unloaded()
    print("SUMMARY", "PASS" if all(ok for _, ok in results) else "FAIL", [n for n, ok in results if not ok], flush=True)

asyncio.run(main())
