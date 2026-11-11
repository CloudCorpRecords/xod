#!/usr/bin/env python3
"""AGE-36 (D19), from the AGE-21 re-grade run 8 T1B failure
(mission-91b6fe5463, tree f09b63a):

The Everest research step had its contract repaired by the AGE-32
mechanism (tools: [] repaired to web.run, repair note present) and it
used the access (web_search and a web_scrape of Britannica appear in
its task record). Its claimed result, however, was headed by a bare
token and consisted of six source links with no temperature value, no
units and no context: a link-only non-answer to an instruction
demanding a sourced fact (the temperature, its units and seasonal
context, and the source name and URL). The step was recorded done /
unverifiable on absence of contradiction. The write step then verified
on file existence only, research.md itself states the Everest value
remains to be verified, and the mission ended done with half its goal
unverified. The Jupiter step in the same mission answered properly
(115 moons, NASA, sourced).

The fix: a no-effect step whose own instruction demands a sourced
fact (the _instruction_needs_research test, judged on the step's own
instruction and title only) is judged on whether its claimed result
carries the answer. Outside its citations and bare URLs (pointers to
an answer, not the answer) the report must contain an answer
statement: a clause of at least four words carrying a digit or a
finite reporting verb, and not itself an admission that the fact is
missing or unverified; and the report must name a source URL, as its
instruction requires. A link list, a bare acknowledgment, or prose
stating the fact remains unverified carries no such statement, so
the step fails (contradicted), the same disposition the incompletion
screen gives a report that admits it did not finish: the step was
not blocked (it had its access and used it), it reported, and what
it reported does not answer its instruction. The mission then fails
on that step and does not complete.

Controls pinned here: the genuine Jupiter answer still records
done; an honest report of non-completion still fails through the
incompletion screen; a missing-tool report still records blocked;
the T1VC no-effect report step still fails on its honest admission;
a pure reasoning step and a plain read step are not judged for a
sourced fact; a producing step with declared file effects keeps its
effect based verdict even when its report is terse.

The graded shapes are verbatim from the run 8 evidence
(grade_t1b_full.log, grade_missions_full.log, t1full-facts.json,
grade_t1vc.log), with only the scratch-root prefix swapped for a
temp root.

Run: .venv/bin/python tests/brain/run_missions_answer_content_test.py
"""
import asyncio, json, os, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_answer_content_"))
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

GOAL = (f"Work in the folder {SCRATCH}. Research two facts and write them into a "
        "file named research.md in that folder: (1) how many moons Jupiter has, and "
        "(2) the temperature at the summit of Mount Everest. For each fact, include "
        "its source (the name of the source and its URL) in research.md next to the claim.")
PLAN = {"subtasks": [
    {"title": "Research Jupiter’s moons",
     "instruction": "Find a reliable source for Jupiter’s moon count and report the count, "
                    "the date or context of the count, and the source name and URL. Do not "
                    "present an unverified count as current.",
     "expected_effects": [], "inputs": [{"source": "goal"}], "tools": []},
    {"title": "Research Everest summit temperature",
     "instruction": "Find a reliable source for a temperature at Mount Everest’s summit and "
                    "report the temperature, its units and relevant date or seasonal context, "
                    "and the source name and URL. Make clear that summit temperature varies.",
     "expected_effects": [], "inputs": [{"source": "goal"}], "tools": []},
    {"title": "Write research file",
     "instruction": "Write research.md in the requested folder with the two sourced findings, "
                    "placing each source name and URL next to its claim and retaining the "
                    "relevant date or temperature context.",
     "expected_effects": [{"kind": "create", "path": str(SCRATCH / "research.md"),
                           "content": None}],
     "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"},
                {"source": "prior_step", "ref": "2"}],
     "tools": ["computer_write_file"]},
]}
JUPITER_RESULT = ("NASA reports that Jupiter has **115 moons officially recognized by the "
                  "International Astronomical Union**. Its [Jupiter Moons page]"
                  "(https://science.nasa.gov/jupiter/jupiter-moons/) does not give a date "
                  "for that count, and the count may change as discoveries are confirmed.\n\n"
                  "**Source:** [NASA Science — Jupiter Moons]"
                  "(https://science.nasa.gov/jupiter/jupiter-moons/)")
EVEREST_RESULT = ("t6-load-ok\n\n**Source links**\n"
                  "1. [How Cold Is Mount Everest? Weather & Climate Guide](https://www.nepalintrepidtreks.com)\n"
                  "2. [Average Temperature in July on Mount Everest](https://myeveresttrip.com)\n"
                  "3. [What would Mount Everest summit avg temps be like?](https://cdweather.boards.net/thread/2364/mount-everest-summit-avg-temps)\n"
                  "4. [Everest Base Camp Weather and Temperature by Month ...](https://www.magicalnepal.com)\n"
                  "5. [Weather station data from the Mount Everest region, Nepal](https://essd.copernicus.org)\n"
                  "6. [Weather Observations Reach the Summit of Mount Everest](https://repository.library.noaa.gov/view/noaa/54014/noaa_54014_DS1.pdf)")
WRITE_RESULT = ("t6-load-ok\n\n**Source links**\n"
                "1. [Everest Temperature by Month: Base Camp to Summit](https://gohelinepal.com/blogs/everest-temperature-month-by-month)\n"
                "2. [How Cold Is Mount Everest? Temperatures by Season](https://nepalgatewaytrekking.com/blog/how-cold-is-mount-everest)\n"
                "3. [Mount Everest Base Camp Weather & Climate](https://www.himalayanwonders.com)\n"
                "4. [How Cold Is Mount Everest? Weather & Climate Guide](https://www.nepalintrepidtreks.com)\n"
                "5. [Average Temperature in July on Mount Everest](https://myeveresttrip.com)\n"
                "6. [Everest Base Camp Weather and Temperature by Month ...](https://www.magicalnepal.com)")
RESEARCH_MD = ("# Research findings\n\n## Jupiter's moons\n\nNASA reports that Jupiter has "
               "**115 moons officially recognized by the International Astronomical Union**. "
               "The NASA page does not date the count, and it may change as discoveries are "
               "confirmed. Source: [NASA Science — Jupiter Moons]"
               "(https://science.nasa.gov/jupiter/jupiter-moons/).\n\n"
               "## Mount Everest summit temperature\n\nSummit temperature varies with weather "
               "and season. The supplied research result did not provide a temperature or its "
               "context, so a sourced value remains to be verified.\n")
EVEREST_MD_PROSE = ("Summit temperature varies with weather and season. The supplied research "
                    "result did not provide a temperature or its context, so a sourced value "
                    "remains to be verified.")
T1VC_SUB = {"title": "Report the result",
            "instruction": "Report the actual pytest result honestly. If the tests failed as "
                           "expected, explain that f returns 1 while the tests require 2.",
            "expected_effects": [], "contract": {"inputs": [], "tools": []}}
T1VC_RESULT = ("The pytest run **failed as expected**: 1 test failed because `f()` returns "
               "`1`, while the test requires `2`")


def as_sub(plan_step, tools=None):
    return {"title": plan_step["title"], "instruction": plan_step["instruction"],
            "expected_effects": plan_step["expected_effects"],
            "contract": {"inputs": plan_step["inputs"],
                         "tools": plan_step["tools"] if tools is None else tools}}


def outcome(sub, result, status="done"):
    return M._outcome_from_task({"status": status, "result": result}, sub)


def case_unit():
    print("== D19: the graded research steps through the verdict ==", flush=True)
    if not hasattr(M, "_sourced_answer_problem"):
        for name in ("the Everest link-only non-answer fails, contradicted",
                     "the failure evidence names the missing answer content",
                     "prose stating the fact remains unverified fails",
                     "a held-out link-only non-answer fails",
                     "a fact with no source URL fails",
                     "an empty report for a sourced-fact step fails"):
            check(name, False, "no answer-content check on this tree")
    else:
        out = outcome(as_sub(PLAN["subtasks"][1], tools=["web.run"]), EVEREST_RESULT)
        check("the Everest link-only non-answer fails, contradicted",
              out["status"] == "failed" and out["verified_status"] == "contradicted", str(out))
        check("the failure evidence names the missing answer content",
              any("no answer" in e for e in out["evidence"]), str(out["evidence"]))
        out = outcome(as_sub(PLAN["subtasks"][1], tools=["web.run"]), EVEREST_MD_PROSE)
        check("prose stating the fact remains unverified fails",
              out["status"] == "failed", str(out))
        held = {"title": "Research a town population",
                "instruction": "Search for the current population of the town and report "
                               "the figure, the source name and URL.",
                "expected_effects": [],
                "contract": {"inputs": [{"source": "goal"}], "tools": ["web.run"]}}
        out = outcome(held, "ok\n\nSources\n- [Town population guide](https://example.com/pop)")
        check("a held-out link-only non-answer fails",
              out["status"] == "failed", str(out))
        out = outcome(held, "The Example Gazette reports the population is 42,000 as of 2024.")
        check("a fact with no source URL fails",
              out["status"] == "failed", str(out))
        out = outcome(as_sub(PLAN["subtasks"][1], tools=["web.run"]), "")
        check("an empty report for a sourced-fact step fails",
              out["status"] == "failed", str(out))
        out = outcome(held, "The Example Gazette reports the population is 42,000 as of 2024. "
                            "Source: Example Gazette, https://example.com/pop")
        check("a held-out genuine sourced answer still records done",
              out["status"] == "done", str(out))
    out = outcome(as_sub(PLAN["subtasks"][0], tools=["web.run"]), JUPITER_RESULT)
    check("the Jupiter genuine sourced answer still records done",
          out["status"] == "done", str(out))


def case_controls():
    print("== D19: dispositions that must not move ==", flush=True)
    out = outcome(T1VC_SUB, T1VC_RESULT)
    check("T1VC: the no-effect report step still fails on its honest admission",
          out["status"] == "failed", str(out))
    out = outcome(as_sub(PLAN["subtasks"][1], tools=["web.run"]),
                  "I was unable to complete the research and could not finish the report.")
    check("an honest report of non-completion still fails, not blocked or done",
          out["status"] == "failed", str(out))
    out = outcome(as_sub(PLAN["subtasks"][1], tools=["web.run"]),
                  "I have no tools to access the web from this step, so I cannot report the fact.")
    check("a missing-tool report still records blocked",
          out["status"] == "blocked", str(out))
    reasoning = {"title": "Weigh the findings",
                 "instruction": "Decide which of the two delivered findings is better "
                                "supported and explain the choice in one paragraph, using "
                                "only the delivered inputs.",
                 "expected_effects": [],
                 "contract": {"inputs": [{"source": "goal"}], "tools": []}}
    out = outcome(reasoning, "All done.")
    check("a pure reasoning step is not judged for a sourced fact",
          out["status"] == "done", str(out))
    read_step = {"title": "Read the note",
                 "instruction": "Read note.txt in the proof directory and report what it "
                                "says for the next step.",
                 "expected_effects": [],
                 "contract": {"inputs": [{"source": "goal"}], "tools": ["computer_read_file"]}}
    out = outcome(read_step, "All done.")
    check("a plain read step is not judged for a sourced fact",
          out["status"] == "done", str(out))
    # A producing step keeps its effect based verdict: the write step's
    # file is on disk, so its terse link-list report is not judged here.
    (SCRATCH / "research.md").write_text(RESEARCH_MD)
    out = outcome(as_sub(PLAN["subtasks"][2]), WRITE_RESULT)
    check("the write step with its file on disk keeps its verified verdict",
          out["status"] == "done" and out["verified_status"] == "verified", str(out))
    (SCRATCH / "research.md").unlink()


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


async def case_mission_end_to_end():
    print("== D19: the graded T1B composition, end to end ==", flush=True)

    def write_step(rec):
        (SCRATCH / "research.md").write_text(RESEARCH_MD)
        return {"id": "task-fake3", "title": "", "request": "", "status": "done",
                "result": WRITE_RESULT}

    q = FakeQueue([{"status": "done", "result": JUPITER_RESULT},
                   {"status": "done", "result": EVEREST_RESULT}, write_step])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(PLAN)]), poll_sec=0.01)
    m = M.create_mission(GOAL)
    await runner.drive(m["id"])
    m = M.get_mission(m["id"])
    subs = m["subtasks"]
    check("the Jupiter step is done in the mission",
          subs[0]["status"] == "done", str(subs[0].get("status")))
    check("the Everest step is failed in the mission, not done",
          subs[1]["status"] == "failed", str(subs[1].get("status")))
    check("the write step does not run on the non-answer",
          subs[2]["status"] == "skipped", str(subs[2].get("status")))
    check("the mission does not complete with half its goal missing",
          m["state"] == "failed", f"state={m['state']}")
    check("research.md is not produced from the non-answer",
          not (SCRATCH / "research.md").exists())


async def main():
    case_unit()
    case_controls()
    await case_mission_end_to_end()
    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed ==========",
          flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        return 1
    print("ALL ANSWER CONTENT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
