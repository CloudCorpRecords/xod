"""
Repairs -> training data, and watching background jobs.

  I  a chatty job (lots of stderr) no longer deadlocks its log reader
  J  start a server job -> wait for its "ready" line, its port, its URL
  K  waiting on a job that fails ends early with its output (no waiting out the clock)
  L  check_job returns only output the agent hasn't seen yet
  M  long waits stream progress lines (chat + task card)
  N  a repaired planner call becomes a "this, not that" pair; machinery failures don't count
  O  environment lessons reach the planner prompt
Uses its own dataset DB and lessons file.
"""
import asyncio, json, os, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ["XOD_DATA_DIR"] = os.environ.get("XOD_TEST_DATA_DIR") or tempfile.mkdtemp(prefix="xod-jobs-")   # its own, not yours
PY = "python" if os.name == "nt" else "python3"     # commands run in PowerShell on Windows, zsh/bash elsewhere
import sys as _sys; _sys.path.insert(0, str(ROOT)); from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
RUN = _DATA / "test_runs" / "learning_jobs"
RUN.mkdir(parents=True, exist_ok=True)
os.environ["XOD_DATASET_DB"] = str(RUN / "dataset.db")
if (RUN / "dataset.db").exists():
    (RUN / "dataset.db").unlink()
sys.path.insert(0, str(ROOT))

from app.services import doctor as doctor_mod  # noqa: E402
doctor_mod.LESSONS_PATH = RUN / "lessons.json"
if doctor_mod.LESSONS_PATH.exists():
    doctor_mod.LESSONS_PATH.unlink()
from app.services.doctor import doctor  # noqa: E402
from app.services import dataset_recorder, preference_data  # noqa: E402
from app.services.system_control import system_control_service as sc  # noqa: E402
from app.services.job_watch import wait_for_stream  # noqa: E402
from app.agent.tools import execute_tool  # noqa: E402

results = []


def report(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


async def wait(args):
    return await execute_tool("computer_wait_for", args)


async def test_chatty_job():
    cmd = f"{PY} -c \"import sys; [sys.stderr.write('x'*200+'\\n') for _ in range(2000)]; print('all done')\""
    j = await sc.start_background_job("chatty", cmd)
    r = await wait({"condition": "job_done", "job_id": j["job_id"], "timeout_sec": 20})
    report("I chatty job finishes (no pipe deadlock)", r.get("met") and r["waited_sec"] < 15, json.dumps(r)[:120])


async def test_server_ready():
    port = 8765
    serve = (f"python -m http.server {port} --bind 127.0.0.1" if sys.platform == "win32" else    # no exec, no python3 there
             f"exec python3 -m http.server {port} --bind 127.0.0.1")
    j = await sc.start_background_job("test server", f"sleep 2; echo 'server ready on {port}'; {serve}", cwd=str(RUN))
    r1 = await wait({"condition": "job_output", "job_id": j["job_id"], "text": "ready on \\d+", "timeout_sec": 30})
    r2 = await wait({"condition": "port_open", "port": port, "timeout_sec": 30})
    r3 = await wait({"condition": "url_ok", "url": f"http://127.0.0.1:{port}/", "timeout_sec": 30})
    report("J wait for job output / port / URL", all(r.get("met") for r in (r1, r2, r3)),
           f"{r1.get('detail')} | {r2.get('detail')} | {r3.get('detail')}")
    await sc.stop_job(j["job_id"])


async def test_failing_job():
    j = await sc.start_background_job("doomed", "sleep 1; echo 'Error: config missing'; exit 3")
    t = time.time()
    r = await wait({"condition": "job_output", "job_id": j["job_id"], "text": "ready", "timeout_sec": 120})
    report("K failed job ends the wait early", not r.get("met") and time.time() - t < 10 and "config missing" in r.get("error", ""),
           f"{time.time() - t:.1f}s: {r.get('error', '')[:120]}")


async def test_check_job_incremental():
    # One line that means the same in PowerShell and bash: tick 1..6, half a second apart.
    j = await sc.start_background_job("ticker", f"{PY} -u -c \"import time; [(print('tick', i, flush=True), time.sleep(0.5)) for i in range(1, 7)]\"")
    await asyncio.sleep(1.2)
    a = await execute_tool("computer_check_job", {"job_id": j["job_id"]})
    await asyncio.sleep(2.5)
    b = await execute_tool("computer_check_job", {"job_id": "ticker"})   # by name works too
    first, second = a["new_output"], b["new_output"]
    report("L check_job shows only new output", first and second and not set(first) & set(second) and b["status"] == "completed",
           f"first={first} second={second} status={b['status']}")


async def test_progress_stream():
    import app.services.job_watch as jw
    jw.PROGRESS_EVERY_SEC = 2
    target = RUN / "later.txt"
    target.unlink(missing_ok=True)

    async def make_later():
        await asyncio.sleep(5)
        target.write_text("hello")
    asyncio.create_task(make_later())
    from app.agent.researcher import AgenticResearcher
    statuses, result = [], None
    async for ev in AgenticResearcher()._exec_tool_stream("computer_wait_for", {"condition": "file_exists", "path": str(target),
                                                                               "timeout_sec": 30}, "w1", None):
        if ev["type"] == "status":
            statuses.append(ev["content"])
        elif ev["type"] == "_result":
            result = ev["result"]
    report("M waits stream progress", result and result.get("met") and len(statuses) >= 1, f"{len(statuses)} updates, e.g. {statuses[:1]}")


def test_repair_pairs():
    from app.services.repair_learning import record_outcome
    plan = {"thought": "read it", "decision": "execute_plan", "plan_steps": [
        {"id": 1, "title": "Read notes", "tools": ["computer_read_file"], "tool_args": [{"filepath": "/x/meeting_note.txt"}]}]}
    msgs = [{"role": "system", "content": "planner"}, {"role": "user", "content": "read /x/meeting_notes.txt"}]
    ex = dataset_recorder.capture(task="planner", messages=msgs, response=json.dumps(plan), model="t", provider="t")
    out = record_outcome({"kind": "planner", "id": ex, "response": json.dumps(plan)}, "computer_read_file",
                         {"filepath": "/x/meeting_note.txt"}, "computer_read_file", {"filepath": "/x/meeting_notes.txt"}, True, "not_found")
    c = dataset_recorder._conn()
    pair = c.execute("SELECT * FROM preference_pairs WHERE example_id = ?", (ex,)).fetchone()
    lab = c.execute("SELECT label, label_reason FROM llm_examples WHERE id = ?", (ex,)).fetchone()
    c.close()
    chosen = json.loads(pair["chosen"]) if pair else {}
    ok = out == "pair" and chosen["plan_steps"][0]["tool_args"][0]["filepath"] == "/x/meeting_notes.txt" and lab["label"] == -1
    report("N1 corrected call -> preference pair", ok, f"{out}, label={lab['label']} {lab['label_reason']}")

    ex2 = dataset_recorder.capture(task="planner", messages=msgs, response=json.dumps(plan), model="t", provider="t")
    out2 = record_outcome({"kind": "planner", "id": ex2, "response": json.dumps(plan)}, "browser_navigate", {}, "browser_navigate", {}, True, "browser_down")
    c = dataset_recorder._conn()
    lab2 = c.execute("SELECT label FROM llm_examples WHERE id = ?", (ex2,)).fetchone()["label"]
    c.close()
    report("N2 machinery failure isn't the planner's fault", out2 == "skipped" and lab2 == 0, f"{out2}, label={lab2}")

    ex3 = dataset_recorder.capture(task="planner", messages=msgs, response=json.dumps(plan), model="t", provider="t")
    out3 = record_outcome({"kind": "planner", "id": ex3, "response": json.dumps(plan)}, "computer_read_file",
                          {"filepath": "/x/meeting_note.txt"}, "computer_list_directory", {"path": "/x"}, True, "not_found")
    report("N3 workaround is labelled, not paired", out3 == "labelled", out3)
    report("N4 pairs counted for Learn from mistakes", preference_data.pair_stats("planner")["by_source"].get("repair_fix") == 1,
           json.dumps(preference_data.pair_stats("planner")["by_source"]))


def test_planner_notes():
    doctor.record_fix("tavily_search", "missing_key", "Tavily API key is not configured", "web_search", {"query": "x"})
    doctor.record_fix("computer_read_file", "not_found", "File not found", "computer_read_file", {"filepath": "/a"})
    notes = doctor.planner_notes()
    report("O environment lessons reach the planner", any("tavily_search" in n and "web_search" in n for n in notes)
           and not any("computer_read_file" in n for n in notes), json.dumps(notes))


def test_plan_fixes():
    from app.agent.researcher import AgenticResearcher as A
    from app.services.job_watch import infer_condition
    n = A._normalize_tool_args([{"name": "name", "val": "build"}, {"name": "command", "val": "sleep 1"}], 1)
    report("P1 name/val args merged", n == [{"name": "build", "command": "sleep 1"}], json.dumps(n))
    r = A()
    r._remember_values({"success": True, "job_id": "job-123"})
    f1 = r._fill_placeholders("computer_check_job", {"job_id": "REPLACE_WITH_JOB_ID_FROM_STEP_1"})
    f2 = r._fill_placeholders("computer_wait_for", {"condition": "job_done"})
    f3 = r._fill_placeholders("computer_wait_for", {"condition": "port_open", "port": 3000})
    report("P2 placeholders filled from earlier steps", f1["job_id"] == "job-123" and f2["job_id"] == "job-123" and "job_id" not in f3,
           f"{f1} {f2} {f3}")
    conds = [infer_condition({"job_id": "j"}), infer_condition({"job_id": "j", "text": "ready"}), infer_condition({"port": 1}),
             infer_condition({"path": "/a"}), infer_condition({"url": "http://x"})]
    report("P3 missing wait condition inferred", conds == ["job_done", "job_output", "port_open", "file_exists", "url_ok"], str(conds))


async def main():
    test_plan_fixes()
    await test_chatty_job()
    await test_server_ready()
    await test_failing_job()
    await test_check_job_incremental()
    await test_progress_stream()
    test_repair_pairs()
    test_planner_notes()
    print("SUMMARY", "PASS" if all(ok for _, ok in results) else "FAIL", [n for n, ok in results if not ok], flush=True)

asyncio.run(main())
