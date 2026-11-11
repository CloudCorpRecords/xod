#!/usr/bin/env python3
"""AGE-32 (D15) and AGE-33 (D16), from the AGE-21 re-grade run 6 T1B
failure (mission-2c30586f86, tree 23ea447):

  - D15: the plan declared tools: [] for both research subtasks. The
    file-effect gates judge declared effects, and a research step
    declares none, so the empty contract passed every gate; the steps
    ran under a contract naming no research capability, honestly
    reported having no research access, and were recorded done on
    non-answers. The fix: at dispatch, a declared contract that
    cannot serve the capability the step itself declares (sourced
    research with attribution, or a declared run effect) is repaired
    by adding the capability's canonical tool, and the repair is
    recorded in the saved contract and in the assembled request.
    Contracts that already serve the step, steps declaring no
    contract, and steps whose instruction declares no capability
    need (a pure reasoning step; a write step placing the sources it
    was handed) are untouched.
  - D16: the write step's worker stream died after its final tool
    call inside a healthy server; the stall was declared at 10
    minutes and the queue force-stopped the task as failed, with no
    recovery and no fresh check of its declared effects, failing the
    mission with research.md already on disk. The fix: the task
    watchdog re-drives a stalled task (cancel the dead run, queue it
    again) under a bounded budget (2 attempts inside an hour, the
    AGE-18 discipline at task scale) before settling it as failed,
    and the mission verdict gives a stall-stopped step the fresh
    declared-effect check: effects true on disk verify the step,
    effects still false keep the honest failure.

The graded shapes are verbatim from the run 6 evidence (the T1B plan
JSON and the queue's stop result), with only the scratch-root prefix
swapped for a temp root.

Run: .venv/bin/python tests/brain/run_missions_t1b_fix_test.py
"""
import asyncio, json, os, sys, tempfile, time
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_t1b_fix_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import app.services.missions as M
import app.services.task_queue as TQ

DBF = TMP / "xod.db"
M.DB = DBF
TQ.DB_PATH, TQ.SHOTS_DIR = DBF, TMP / "shots"
TQ.notify = lambda title, msg: None
if hasattr(TQ, "_phone"):
    TQ._phone = lambda task, kind: None
M._spawn = lambda mid: None

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}"
          + (f"  {detail}" if detail and not ok else ""), flush=True)


SCRATCH = TMP / "xod-t1-scratch"
SCRATCH.mkdir(parents=True, exist_ok=True)
STOP_TEXT = ("This task stopped making progress, so Xod stopped it. "
             "Retry it from the inbox.")

# The run 6 plan, verbatim (titles, instructions, tools), scratch swapped.
GOAL = (f"Work in the folder {SCRATCH}. Research two facts and write them "
        f"into a file named research.md in that folder: (1) how many moons "
        f"Jupiter has, and (2) the temperature at the summit of Mount "
        f"Everest. For each fact, include its source (the name of the "
        f"source and its URL) in research.md next to the claim.")
PLAN = {"subtasks": [
    {"title": "Research Jupiter’s moons",
     "instruction": "Find a reliable sourced count of Jupiter’s known "
                    "moons, including the source name, URL, and date of "
                    "the count if available. Report these details for "
                    "the writing step.",
     "expected_effects": [], "inputs": [{"source": "goal"}], "tools": []},
    {"title": "Research Everest’s summit temperature",
     "instruction": "Find a reliable sourced temperature for Mount "
                    "Everest’s summit, specifying the time or conditions "
                    "it describes because the temperature varies. Report "
                    "the temperature, source name, and URL for the "
                    "writing step.",
     "expected_effects": [], "inputs": [{"source": "goal"}], "tools": []},
    {"title": "Write research file",
     "instruction": f"Write both findings to {SCRATCH}/research.md, "
                    f"placing each source name and URL next to its claim. "
                    f"Include the count’s date if available and the "
                    f"temperature’s time or conditions.",
     "expected_effects": [{"kind": "create",
                           "path": str(SCRATCH / "research.md"),
                           "content": None}],
     "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"},
                {"source": "prior_step", "ref": "2"}],
     "tools": ["computer_write_file"]},
]}


def as_subtask(plan_step):
    return {"title": plan_step["title"], "instruction": plan_step["instruction"],
            "expected_effects": plan_step["expected_effects"],
            "contract": {"inputs": plan_step["inputs"], "tools": plan_step["tools"]}}


# --------------------------------------------------------------------- D15
def case_d15_repair_unit():
    print("== D15: contract repair, unit level ==", flush=True)
    if not hasattr(M, "_capability_contract_repair"):
        # On the base tree there is no repair at all: every unit
        # assertion below is a defect failure, recorded as such so
        # the rest of the suite (dispatch, queue, verdict) still runs.
        for name in ("research step with an empty contract is repaired with web.run",
                     "the repair note names the declared contract and the addition",
                     "the second research step is repaired the same way",
                     "the write step placing handed sources is not repaired",
                     "a contract already declaring web.run is untouched",
                     "a contract declaring a real web tool is untouched",
                     "a pure reasoning step is not force-fed tools",
                     "a step declaring no contract is untouched",
                     "a run-effect step with an empty contract gains the command tool"):
            check(name, False, "no capability contract repair on this tree")
        return
    r = M._capability_contract_repair(as_subtask(PLAN["subtasks"][0]))
    check("research step with an empty contract is repaired with web.run",
          r is not None and r[0] == ["web.run"], str(r))
    check("the repair note names the declared contract and the addition",
          r is not None and "tools: none" in r[1] and "web.run" in r[1],
          str(r and r[1]))
    r = M._capability_contract_repair(as_subtask(PLAN["subtasks"][1]))
    check("the second research step is repaired the same way",
          r is not None and r[0] == ["web.run"], str(r))
    check("the write step placing handed sources is not repaired",
          M._capability_contract_repair(as_subtask(PLAN["subtasks"][2])) is None)

    proper = as_subtask(PLAN["subtasks"][0])
    proper["contract"] = {"inputs": [], "tools": ["web.run"]}
    check("a contract already declaring web.run is untouched",
          M._capability_contract_repair(proper) is None)
    proper2 = as_subtask(PLAN["subtasks"][0])
    proper2["contract"] = {"inputs": [], "tools": ["web_search"]}
    check("a contract declaring a real web tool is untouched",
          M._capability_contract_repair(proper2) is None)

    reasoning = {"title": "Weigh the findings",
                 "instruction": "Decide which of the two delivered findings "
                                "is better supported and explain the choice "
                                "in one paragraph, using only the delivered "
                                "inputs.",
                 "expected_effects": [],
                 "contract": {"inputs": [{"source": "goal"}], "tools": []}}
    check("a pure reasoning step is not force-fed tools",
          M._capability_contract_repair(reasoning) is None)

    nocontract = as_subtask(PLAN["subtasks"][0])
    nocontract["contract"] = {"inputs": [{"source": "goal"}], "tools": None}
    check("a step declaring no contract is untouched",
          M._capability_contract_repair(nocontract) is None)

    run_step = {"title": "Run the checks",
                "instruction": "Run the project's check script and report "
                               "its exit code.",
                "expected_effects": [{"kind": "run", "command": "make check",
                                      "cwd": str(SCRATCH), "expect_exit": 0}],
                "contract": {"inputs": [], "tools": []}}
    r = M._capability_contract_repair(run_step)
    check("a run-effect step with an empty contract gains the command tool",
          r is not None and r[0] == ["computer_run_command"], str(r))


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
# The two research steps report genuine sourced answers, not the bare
# DONE acknowledgment: since AGE-36 (D19) a no-effect step whose
# instruction demands a sourced fact is judged on whether its report
# carries the answer, and a bare acknowledgment is a non-answer that
# fails the step and stops the mission before the write step. These
# fakes state each fact with its source name and URL, as the steps'
# instructions require.
RESEARCH1 = {"status": "done",
             "result": "NASA Science reports that Jupiter has 295 moons in the "
                       "count it lists, and the count may change as discoveries "
                       "are confirmed. Source: NASA Science, "
                       "https://science.nasa.gov/jupiter/jupiter-moons/"}
RESEARCH2 = {"status": "done",
             "result": "Encyclopaedia Britannica reports that the summit "
                       "temperature averages about -36 degrees Celsius in the "
                       "warmest month and varies with weather. Source: "
                       "Encyclopaedia Britannica, "
                       "https://www.britannica.com/place/Mount-Everest"}


async def case_d15_dispatch():
    print("== D15: the run 6 plan through the runner ==", flush=True)
    q = FakeQueue([RESEARCH1, RESEARCH2, DONE])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(PLAN)]),
                             poll_sec=0.01)
    m = M.create_mission(GOAL)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    subs = m["subtasks"]
    check("both research steps' saved contracts carry the repair",
          (subs[0]["contract"] or {}).get("tools") == ["web.run"]
          and (subs[1]["contract"] or {}).get("tools") == ["web.run"],
          str([s["contract"] for s in subs]))
    check("the research requests declare the repaired contract",
          all("capability contract): web.run" in q.created[i]["request"]
              for i in (0, 1)),
          str([c["request"][:200] for c in q.created]))
    check("the repair is recorded in the research requests",
          all("contract was repaired" in q.created[i]["request"] for i in (0, 1)))
    check("the write step's contract and request are untouched",
          (subs[2]["contract"] or {}).get("tools") == ["computer_write_file"]
          and "contract was repaired" not in q.created[2]["request"],
          str(subs[2]["contract"]))


# --------------------------------------------------------------------- D16
async def case_d16_queue():
    print("== D16: watchdog re-drive under a bounded budget ==", flush=True)
    q = TQ.TaskQueue()   # never started: pure watchdog bookkeeping
    t = q.create("Write research file (mission step 3)")
    q._update(t["id"], status="working", now="Starting")
    q._progress(t["id"], "✓ wrote research.md")
    q._touch_progress(t["id"], at=time.time() - 5000)
    r = await q.run_watchdog(stall_sec=60, recover_sec=120)
    after = q.get(t["id"])
    check("first recover pass re-drives the wedged task instead of failing it",
          after["status"] == "queued" and r.get("redriven") == [t["id"]],
          f"status={after['status']} r={r}")
    check("the re-drive is recorded in the task's history",
          any("queued this task again" in str(h.get("text", ""))
              for h in after.get("history") or []),
          str([h.get("text") for h in after.get("history") or []]))
    # The re-driven task gets a fresh stall window: an immediate pass
    # at the stall threshold alone must not touch it.
    r = await q.run_watchdog(stall_sec=60, recover_sec=120)
    check("a re-driven task is not re-flagged inside its fresh window",
          q.get(t["id"])["status"] == "queued" and not r.get("redriven"),
          f"r={r}")
    # It wedges again in exactly the same way, twice more: the second
    # re-drive lands, then the budget is spent and the stop is honest.
    for _ in range(2):
        q._update(t["id"], status="working", now="Starting")
        q._touch_progress(t["id"], at=time.time() - 5000)
        await q.run_watchdog(stall_sec=60, recover_sec=120)
    after = q.get(t["id"])
    check("after the budget is spent the task fails with the true reason",
          after["status"] == "failed"
          and "stopped making progress" in (after.get("result") or ""),
          f"status={after['status']} result={after.get('result')}")
    # A task the user is cancelling is never re-driven.
    t2 = q.create("Wedged task with a cancel pending")
    q._update(t2["id"], status="working", now="Starting")
    q._touch_progress(t2["id"], at=time.time() - 5000)
    q._cancel_requested.add(t2["id"])
    r = await q.run_watchdog(stall_sec=60, recover_sec=120)
    check("a cancel-pending wedged task is stopped, not re-driven",
          q.get(t2["id"])["status"] == "failed"
          and t2["id"] not in (r.get("redriven") or []),
          f"status={q.get(t2['id'])['status']} r={r}")


def case_d16_verdict():
    print("== D16: the verdict fresh-checks a stall-stopped step ==", flush=True)
    sub = {"id": "sub-t1b3", "title": "Write research file",
           "instruction": "Write both findings to research.md in the "
                          "requested folder.",
           "expected_effects": [{"kind": "create",
                                 "path": str(SCRATCH / "research.md"),
                                 "content": None}],
           "required_inputs": []}
    task = {"status": "failed", "result": STOP_TEXT, "error": ""}
    target = SCRATCH / "research.md"
    target.write_text("Jupiter has 295 moons (IAU). "
                      "Everest summit temperature varies.\n")
    out = M._outcome_from_task(task, sub)
    check("deliverable on disk: the step verifies on its effects",
          out["status"] == "done" and out["verified_status"] == "verified",
          str(out))
    check("the evidence names the stall and the fresh check",
          any("stopped making progress" in e and "fresh check" in e
              for e in out.get("evidence") or []), str(out.get("evidence")))
    target.unlink()
    out = M._outcome_from_task(task, sub)
    check("deliverable absent: the step still fails with the stop reason",
          out["status"] == "failed" and out["verified_status"] == "failed",
          str(out))
    bare = dict(sub)
    bare["expected_effects"] = []
    out = M._outcome_from_task(task, bare)
    check("a stall-stopped step with no declared effects still fails",
          out["status"] == "failed", str(out))
    other = {"status": "failed", "result": "The worker crashed on a bad "
                                           "tool call.", "error": ""}
    target.write_text("Jupiter has 295 moons (IAU).\n")
    out = M._outcome_from_task(other, sub)
    check("a non-stall failure is not rescued by the fresh check",
          out["status"] == "failed", str(out))
    target.unlink()


async def case_d16_mission_end_to_end():
    print("== D16: the graded composition, end to end ==", flush=True)
    target = SCRATCH / "research.md"
    if target.exists():
        target.unlink()

    def write_step(rec):
        target.write_text("# Research\n\nJupiter has 295 moons (IAU, "
                          "https://www.iau.org).\n")
        return {"id": "task-fake3", "title": "", "request": "",
                "status": "failed", "result": STOP_TEXT}

    plan3 = dict(PLAN["subtasks"][2])
    plan = {"subtasks": [dict(PLAN["subtasks"][0], tools=["web.run"]),
                         dict(PLAN["subtasks"][1], tools=["web.run"]), plan3]}
    q = FakeQueue([RESEARCH1, RESEARCH2, write_step])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]),
                             poll_sec=0.01)
    m = M.create_mission(GOAL)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    s3 = m["subtasks"][2]
    check("the mission completes: its deliverable landed before the stream died",
          m["state"] == "done" and s3["status"] == "done"
          and s3["verified_status"] == "verified",
          f"state={m['state']} sub={s3}")


async def main():
    case_d15_repair_unit()
    await case_d15_dispatch()
    await case_d16_queue()
    case_d16_verdict()
    await case_d16_mission_end_to_end()
    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed ==========",
          flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        return 1
    print("ALL T1B FIX CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
