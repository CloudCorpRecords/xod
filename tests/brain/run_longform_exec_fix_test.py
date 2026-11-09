"""
Long-form execution fix suite (2026-11-09, branch tree/longform-exec-fix-2026-11-09):

Run 4 of the long-form bench (BENCH-LONGFORM-BUILD-INSTALL-2026-11-08.md) failed in step
execution, not planning: one step naming three files ran as a single model stream (585 s,
wrote only Info.plist), its one retry re-ran the whole step, and the retry hit the 300 s
no-output stall — a raw httpx read timeout, because llm._lines' quiet watchdog covered
only the built-in engine, not LM Studio / Ollama.

This suite covers the fix:
  1. Multi-file step splitting (unit): claims, per-file substep texts, spec fragments,
     non-splittable steps left alone.
  2. Retry-split (unit): a failed multi-file step split on its retry keeps the verified
     file as a completed substep and never re-runs it.
  3. Local-provider stall watchdog (unit): llm._lines raises StreamStalled at the local
     quiet limits instead of waiting out 300 s; non-local providers are untouched.
  4. End-to-end: a 3-file step executes as three single-file streams, each checkpointed
     in the plan's artifact ledger, and the task finishes done.
  5. End-to-end: a substep whose first stream errors is retried alone; the substep that
     already landed is never re-run.

Run:
  .venv/bin/python tests/brain/run_longform_exec_fix_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.setdefault("XOD_NO_UPDATE_CHECK", "1")

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_longform_exec_"))
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

from app.agent import researcher as RS  # noqa: E402
from app.agent import worker as W  # noqa: E402
from app.services import activity  # noqa: E402
from app.services import llm as LLM  # noqa: E402
from app.services import orchestrator as O  # noqa: E402
from app.services import task_queue as TQ  # noqa: E402

TQ.notify = lambda title, msg: None
TQ._phone = lambda task, kind: None


async def _no_tab(self, task, resuming):
    return True


TQ.TaskQueue._ensure_tab = _no_tab

passed = 0
failures = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed
    if ok:
        passed += 1
        print(f"  PASS {name}", flush=True)
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"  [{detail}]" if detail else ""), flush=True)


# ---------------------------------------------------------------------------
# 1. Multi-file step splitting (unit)
# ---------------------------------------------------------------------------
print("1. Multi-file step splitting (unit)")
RUN4_REQUEST = (
    "Build and install a macOS right-click Quick Action service in ~/Library/Services "
    "that sends selected text to a local AI model and displays the response, then register "
    "it with pbs and test it."
)
shaped = O._sanitize_and_shape_steps(RUN4_REQUEST, ["anything"])
step1 = {"text": shaped[0], "status": "pending", "parallel": None}
claims = O._multi_file_write_claims(step1)
check("1a. Shaped Run 4 step 1 yields 3 file-write claims",
      [c["path"].name for c in claims] == ["Info.plist", "document.wflow", "ask_ai.py"],
      str(claims))

sub_texts = [O._artifact_substep_text(shaped[0], c) for c in claims]
check("1b. Every claim produces a substep text", all(sub_texts), str(sub_texts))
for c, t in zip(claims, sub_texts):
    got = TQ._claimed_file_effects(t or "")
    check(f"1c. Substep for {c['path'].name} claims exactly its own file",
          len(got) == 1 and got[0]["path"] == c["path"], f"{t!r} -> {got}")
check("1d. Info.plist substep carries its spec fragment (NSServices)",
      "NSServices" in (sub_texts[0] or ""), sub_texts[0] or "")
check("1e. ask_ai.py substep carries its spec fragment (local endpoint, no external key)",
      "127.0.0.1:1234" in (sub_texts[2] or ""), sub_texts[2] or "")
check("1f. Substep texts forbid touching the sibling files",
      all("NOT part of this substep" in (t or "") for t in sub_texts), str(sub_texts))

check("1g. Single-file step is not splittable",
      O._multi_file_write_claims({"text": f"Create the file {TMP}/one.py with A = 1",
                                  "status": "pending", "parallel": None}) == [])
check("1h. Delete step is not splittable",
      O._multi_file_write_claims({"text": f"Delete the files {TMP}/a.py and {TMP}/b.py",
                                  "status": "pending", "parallel": None}) == [])
check("1i. Parallel fan-out step is not splittable",
      O._multi_file_write_claims({"text": shaped[0], "status": "pending",
                                  "parallel": ["x", "y"]}) == [])
check("1j. A substep is never re-split",
      O._multi_file_write_claims({"text": shaped[0], "status": "pending", "parallel": None,
                                  "substep_of": "s-abc"}) == [])
check("1k. Register/test steps of the shaped plan are not splittable",
      O._multi_file_write_claims({"text": shaped[1], "status": "pending", "parallel": None}) == []
      and O._multi_file_write_claims({"text": shaped[2], "status": "pending", "parallel": None}) == [])


# ---------------------------------------------------------------------------
# 2. Retry-split (unit): verified file kept, missing file retried alone
# ---------------------------------------------------------------------------
print("2. Retry-split of a failed multi-file step (unit)")


class StubQ:
    """Just the queue surface _maybe_split_step touches: file-effect verification by disk
    truth (here: scripted) and progress lines."""
    def __init__(self, verified_names):
        self.verified_names = verified_names
        self.progress = []

    def _verify_file_effects(self, task, started):
        name = Path(task["request"].split("Create the file ", 1)[1]).name
        return [] if name in self.verified_names else [f"claimed file {name} is missing"]

    def _progress(self, task_id, line):
        self.progress.append(line)


q2 = TQ.TaskQueue()
tid2 = q2.create("Build the two-file thing", mode="stepwise")["id"]
proj2 = TMP / "retry_proj"
proj2.mkdir(parents=True, exist_ok=True)
f1, f2 = proj2 / "first.py", proj2 / "second.py"
big_step = O._item(f"Create the files {f1} (A = 1), and {f2} (B = 2).")
big_step["status"] = "in_progress"
big_step["attempts"] = 1
big_step["started_at"] = time.time() - 600
big_step["retry_pending"] = True
big_step["note"] = "first attempt did not check out: claimed file second.py is missing"
items2 = [big_step]
O.save_plan(tid2, items2, [], bump=True, why="test seed")
stub = StubQ(verified_names={"first.py"})
changed = O._maybe_split_step(stub, tid2, items2, [], big_step)
check("2a. Retry-split fires on a failed multi-file step", changed is True)
check("2b. The step is replaced by two substeps",
      len(items2) == 2 and all(i.get("substep_of") == big_step["id"] for i in items2),
      json.dumps(items2))
check("2c. The verified file becomes a completed substep with its artifact recorded",
      items2[0]["status"] == "completed" and items2[0].get("artifacts") == [str(f1)],
      json.dumps(items2[0]))
check("2d. The missing file stays pending, carrying the failed attempt's note and start time",
      items2[1]["status"] == "pending"
      and str(items2[1].get("note") or "").startswith("first attempt did not check out")
      and items2[1].get("prior_started_at") == big_step["started_at"],
      json.dumps(items2[1]))
saved2, _v2, _i2 = O.load_plan(tid2)
check("2e. The split plan persists (save/load roundtrip)",
      [i["status"] for i in saved2] == ["completed", "pending"], json.dumps(saved2))

fresh_step = O._item(f"Create the files {proj2}/x.py (X = 1), and {proj2}/y.py (Y = 2).")
items2b = [fresh_step]
changed_b = O._maybe_split_step(StubQ(verified_names=set()), tid2, items2b, [], fresh_step)
check("2f. A fresh pending multi-file step splits with nothing pre-completed",
      changed_b is True and all(i["status"] == "pending" for i in items2b),
      json.dumps(items2b))

resumed_step = O._item(f"Create the files {proj2}/x.py (X = 1), and {proj2}/y.py (Y = 2).")
resumed_step["status"] = "in_progress"
resumed_step["started_at"] = time.time() - 60
items2c = [resumed_step]
changed_c = O._maybe_split_step(StubQ(verified_names=set()), tid2, items2c, [], resumed_step)
check("2g. An in-flight (resumed, not retrying) multi-file step is NOT split",
      changed_c is False and len(items2c) == 1, json.dumps(items2c))


# ---------------------------------------------------------------------------
# 3. Local-provider stall watchdog (unit)
# ---------------------------------------------------------------------------
print("3. Local-provider stall watchdog (unit)")


class FakeResponse:
    def __init__(self, script):
        self.script = script   # list of (delay, line-or-None-to-end)

    def aiter_lines(self):
        script = self.script

        async def gen():
            for delay, line in script:
                if delay:
                    await asyncio.sleep(delay)
                if line is None:
                    return
                yield line
        return gen()


async def _drain(resp, info):
    return [line async for line in LLM._lines(resp, info)]


async def stall_tests():
    os.environ["XOD_LOCAL_STALL_FIRST_SEC"] = "0.4"
    os.environ["XOD_LOCAL_STALL_SEC"] = "0.4"
    try:
        t0 = time.time()
        try:
            await _drain(FakeResponse([(0, "data: one"), (5, "data: two")]),
                         {"provider": "lm_studio"})
            stalled, quiet = False, None
        except LLM.StreamStalled as e:
            stalled, quiet = True, e.quiet
        dt = time.time() - t0
        check("3a. Mid-stream quiet past the limit raises StreamStalled fast (not 300 s)",
              stalled and quiet == 0.4 and dt < 3, f"stalled={stalled} quiet={quiet} dt={dt:.2f}")
        t0 = time.time()
        try:
            await _drain(FakeResponse([(5, "data: late")]), {"provider": "ollama"})
            stalled2 = False
        except LLM.StreamStalled:
            stalled2 = True
        check("3b. No first output past the first-output limit raises StreamStalled",
              stalled2 and time.time() - t0 < 3, f"dt={time.time() - t0:.2f}")
        lines = await _drain(FakeResponse([(0, "data: one"), (0, "data: two"), (0, None)]),
                             {"provider": "lm_studio"})
        check("3c. A healthy local stream passes all lines through",
              lines == ["data: one", "data: two"], str(lines))
        lines = await _drain(FakeResponse([(0.6, "data: slow-but-cloud"), (0, None)]),
                             {"provider": "fireworks"})
        check("3d. Cloud providers are not subject to the local stall watchdog",
              lines == ["data: slow-but-cloud"], str(lines))
    finally:
        del os.environ["XOD_LOCAL_STALL_FIRST_SEC"]
        del os.environ["XOD_LOCAL_STALL_SEC"]


asyncio.run(stall_tests())


# ---------------------------------------------------------------------------
# 4/5. End-to-end through run_stepwise with a scripted researcher
# ---------------------------------------------------------------------------
print("4/5. End-to-end split execution and substep-level retry")


def record_write(path: Path) -> None:
    c = activity._conn()
    c.execute(
        "INSERT INTO activity (id, ts, origin, tool, title, detail, ok, undo, undoable) VALUES (?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, time.time(), activity.origin.get(), "computer_write_file", "Wrote a file",
         str(path), 1, json.dumps([{"kind": "write_file", "path": str(path)}]), 1),
    )
    c.commit()
    c.close()


CONTENTS = {"a.py": "A = 1\n", "b.py": "B = 2\n", "c.txt": "hello\n",
            "x.py": "X = 1\n", "y.py": "Y = 2\n", "summary.txt": "done\n"}


def make_researcher(calls, fail_once=()):
    attempts = {}

    class ScriptedResearcher:
        def __init__(self, *a, **k):
            pass

        async def run_research_stream(self, messages, **k):
            goal = W._step_goal(messages[-1]["content"])
            targets = [name for name in CONTENTS if f"/{name}" in goal]
            calls.append(goal)
            yield {"type": "status", "content": "• running"}
            for name in targets:
                attempts[name] = attempts.get(name, 0) + 1
                if name in fail_once and attempts[name] == 1:
                    yield {"type": "error",
                           "content": "lm_studio stopped responding (no output for 90s)."}
                    return
                # the goal names the file's directory; find the full path in the goal text
                import re as _re
                m = _re.search(r"([^\s\"'<>|`]+/" + _re.escape(name) + r")", goal)
                p = Path(os.path.expanduser(m.group(1).rstrip(".,;:)`")))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(CONTENTS[name], encoding="utf-8")
                record_write(p)
                yield {"type": "content", "content": f"Created {p}"}
    return ScriptedResearcher


async def run_task(planner_steps, researcher_cls, timeout=30):
    orig_rs, orig_pl = RS.AgenticResearcher, O.planner
    RS.AgenticResearcher = researcher_cls

    async def fake_planner(_req):
        return planner_steps
    O.planner = fake_planner
    q = TQ.task_queue = TQ.TaskQueue()
    q.start()
    try:
        tid = O.submit("scripted long-form task")["task_id"]
        t0 = time.time()
        while time.time() - t0 < timeout:
            if q.get(tid)["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.05)
        return q.get(tid), O.load_plan(tid)[0]
    finally:
        q.stop()
        RS.AgenticResearcher, O.planner = orig_rs, orig_pl


async def e2e_split():
    proj = TMP / "e2e_proj"
    proj.mkdir(parents=True, exist_ok=True)
    steps = [
        f"Create the files {proj}/a.py (A = 1), {proj}/b.py (B = 2), and {proj}/c.txt (hello).",
        f"Create the file {proj}/summary.txt with the word done.",
    ]
    calls = []
    task, items = await run_task(steps, make_researcher(calls))
    subs = [i for i in items if i.get("substep_of")]
    check("4a. Task with a 3-file step finishes done", task["status"] == "done",
          f"{task['status']}: {task.get('result')}")
    check("4b. The 3-file step became 3 substeps, all completed",
          len(subs) == 3 and all(i["status"] == "completed" for i in subs), json.dumps(items))
    check("4c. Each substep's artifact ledger entry is exactly its one file",
          all(len(i.get("artifacts") or []) == 1 and
              (i["artifacts"][0].endswith("/a.py") or i["artifacts"][0].endswith("/b.py")
               or i["artifacts"][0].endswith("/c.txt")) for i in subs),
          json.dumps([i.get("artifacts") for i in subs]))
    one_file_goals = [g for g in calls
                      if sum(1 for n in ("a.py", "b.py", "c.txt") if f"/{n}" in g) == 1]
    check("4d. Each of the 3 files got its own model stream (3 single-file step goals)",
          len(one_file_goals) == 3, f"{len(calls)} streams: {calls}")
    check("4e. All three files exist on disk with their contents",
          all((proj / n).read_text(encoding="utf-8") == CONTENTS[n]
              for n in ("a.py", "b.py", "c.txt")))


async def e2e_retry():
    proj = TMP / "e2e_retry"
    proj.mkdir(parents=True, exist_ok=True)
    steps = [f"Create the files {proj}/x.py (X = 1), and {proj}/y.py (Y = 2)."]
    calls = []
    task, items = await run_task(steps, make_researcher(calls, fail_once={"y.py"}))
    subs = [i for i in items if i.get("substep_of")]
    x_streams = [g for g in calls if "/x.py" in g]
    y_streams = [g for g in calls if "/y.py" in g]
    check("5a. Task finishes done after the y.py substep's first stream errors",
          task["status"] == "done", f"{task['status']}: {task.get('result')}")
    check("5b. Both substeps completed; y.py took 2 attempts, x.py was never re-run",
          len(subs) == 2 and all(i["status"] == "completed" for i in subs)
          and len(x_streams) == 1 and len(y_streams) == 2,
          f"x={len(x_streams)} y={len(y_streams)} items={json.dumps(items)}")
    check("5c. Both files exist on disk",
          (proj / "x.py").exists() and (proj / "y.py").exists())


asyncio.run(e2e_split())
asyncio.run(e2e_retry())

print(f"\n{passed}/{passed + len(failures)} checks passed")
if failures:
    print("FAILED:", *failures, sep="\n  ")
    sys.exit(1)

