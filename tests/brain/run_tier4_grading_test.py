"""AGE-46 Tier 4 grading rig: Research across sources (TIER-DEEP-DIVES.md, tier-04.md).

Encodes Tier 4's deep-dive mission shape at the tool-contract level using
planted conflicting/syndicated sources and scripted tool/worker outcomes
through the real MissionRunner, _sourced_answer_problem, _capability_contract_repair,
and workers.coordinate (no live LLM).

Contract points verified locally:
  - T4-A: Research contract repair & _sourced_answer_problem gate (rejects
    unsourced or URL-only claims; accepts sourced fact with URL + statement).
  - T4-B: Conflicting primary/secondary/syndicated sources reconciled by scope
    (incorporation vs public launch), preserving Confirmed / Inferred / Unknown
    separation in synthesis.md without averaging dates.
  - T4-C (load-bearing gate): A failed/unread source in workers.coordinate is
    never silently treated as an empty result; coordinator marks the worker
    failed and returns outcome='partial' with the unread source named.

Live-only assertions (marked SKIPPED with reason; executed in the future
pinned-Mac + family-key ceremony run):
  - Live LLM adjudicating conflicting live web sources without averaging or
    counting syndicated copies as independent confirmations.
  - Live LLM ranking primary records over secondary summaries on live URLs.

Run: python tests/brain/run_tier4_grading_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_t4_grade_"))
os.environ.update({
    "HOME": str(TMP),
    "XOD_DATA_DIR": str(TMP / "data"),
    "XOD_NO_UPDATE_CHECK": "1",
})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import app.services.missions as M  # noqa: E402
import app.services.task_queue as TQ  # noqa: E402
import app.services.workers as W  # noqa: E402

DBF = TMP / "xod.db"
M.DB = DBF
TQ.DB_PATH, TQ.SHOTS_DIR = DBF, TMP / "shots"
TQ.notify = lambda title, msg: None
if hasattr(TQ, "_phone"):
    TQ._phone = lambda task, kind: None
M._spawn = lambda mid: None

results = []
skipped = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def skip_live(name, reason):
    skipped.append((name, reason))
    print(f"  SKIP (live-only) {name}  [{reason}]", flush=True)


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
        tid = f"task-t4-{len(self.created) + 1}"
        self.created.append({"id": tid, "request": request, "title": title})
        idx = len(self.created) - 1
        beh = self.behaviors[idx] if idx < len(self.behaviors) else {"status": "failed", "result": "unexpected task"}
        self.tasks[tid] = {"id": tid, "request": request, "title": title or "", "behavior": beh}
        return {"id": tid, "status": "queued", "title": title}

    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None:
            return None
        b = rec["behavior"]
        if callable(b):
            return b(rec)
        return {"id": tid, "title": rec["title"], "request": rec["request"], **b}


def case_t4a_sourced_answer_unit():
    print("== T4-A: research contract repair & sourced-answer gate ==", flush=True)
    step = {
        "title": "Research founding date",
        "instruction": "Find a reliable source for Acme Labs' founding date and report it with the source name and URL.",
        "expected_effects": [],
        "contract": {"inputs": [], "tools": []},
    }
    repair = M._capability_contract_repair(step)
    check("T4-A: research step with empty tools is repaired to web.run",
          repair is not None and repair[0] == ["web.run"], str(repair))

    prob_no_url = M._sourced_answer_problem(step, "Acme Labs was founded in 2018.")
    check("T4-A: unsourced claim without URL is rejected by _sourced_answer_problem",
          prob_no_url is not None and "URL" in prob_no_url, str(prob_no_url))

    prob_url_only = M._sourced_answer_problem(step, "See https://registry.example/7741")
    check("T4-A: URL-only report with no answer statement is rejected",
          prob_url_only is not None and "no answer statement" in prob_url_only, str(prob_url_only))

    prob_ok = M._sourced_answer_problem(
        step,
        "Confirmed: Acme Labs was incorporated on 2018-03-14 per Delaware Registry (https://registry.example/7741).",
    )
    check("T4-A: sourced claim with source name, URL, and fact passes gate",
          prob_ok is None, str(prob_ok))


async def case_t4b_contradiction_and_synthesis():
    print("== T4-B: conflicting & syndicated sources reconciled with Confirmed/Inferred/Unknown ==", flush=True)
    ws = TMP / "t4b"
    ws.mkdir(parents=True, exist_ok=True)
    p_reg = ws / "registry_primary.txt"
    p_blog = ws / "launch_blog.txt"
    p_wire = ws / "syndicated_wire.txt"
    out = ws / "synthesis.md"

    p_reg.write_text("PRIMARY RECORD: Acme Labs incorporated 2018-03-14 (Delaware filing #7741).\n", encoding="utf-8")
    p_blog.write_text("COMPANY BLOG: Acme Labs publicly launched on 2019-06-01.\n", encoding="utf-8")
    p_wire.write_text("WIRE SYNDICATION (reprint of launch_blog): Acme Labs launched on 2019-06-01.\n", encoding="utf-8")

    plan = {
        "subtasks": [
            {
                "title": "Inspect primary and secondary sources",
                "instruction": (
                    f"Read {p_reg}, {p_blog}, and {p_wire}. Identify any date conflict, "
                    "check whether wire is independent of blog, and report scoped facts."
                ),
                "expected_effects": [],
                "inputs": [
                    {"source": "goal"},
                    {"source": "file", "ref": str(p_reg)},
                    {"source": "file", "ref": str(p_blog)},
                    {"source": "file", "ref": str(p_wire)},
                ],
                "tools": ["computer_read_file"],
            },
            {
                "title": "Write scoped synthesis",
                "instruction": (
                    f"Write {out} separating Confirmed, Inferred, and Unknown sections, "
                    "preserving the incorporation vs public-launch scope."
                ),
                "expected_effects": [{"kind": "create", "path": str(out), "content": None}],
                "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
                "tools": ["computer_write_file"],
            },
        ]
    }
    goal = (
        f"In {ws}, investigate when Acme Labs was founded across registry_primary.txt, "
        f"launch_blog.txt, and syndicated_wire.txt, and write a synthesis to {out}."
    )

    def s1(rec):
        return {
            "id": rec["id"], "title": rec["title"], "request": rec["request"],
            "status": "done",
            "result": (
                "Confirmed: Incorporated 2018-03-14 per registry_primary.txt (#7741); "
                "publicly launched 2019-06-01 per launch_blog.txt. "
                "syndicated_wire.txt is a reprint of launch_blog.txt (not independent). "
                "Unknown: exact first-hire date."
            ),
            "evidence": [{"tool": "computer_read_file", "path": str(p_reg)},
                         {"tool": "computer_read_file", "path": str(p_blog)},
                         {"tool": "computer_read_file", "path": str(p_wire)}],
        }

    def s2(rec):
        body = (
            "# Acme Labs Founding Synthesis\n\n"
            "## Confirmed\n"
            "- Incorporated: 2018-03-14 (primary: registry_primary.txt, filing #7741).\n"
            "- Public launch: 2019-06-01 (launch_blog.txt; syndicated_wire.txt is a non-independent reprint).\n\n"
            "## Inferred\n"
            "- The 2018 vs 2019 difference reflects incorporation preceding public product launch.\n\n"
            "## Unknown\n"
            "- Exact date operations began between 2018-03-14 and 2019-06-01.\n"
        )
        out.write_text(body, encoding="utf-8")
        return {
            "id": rec["id"], "title": rec["title"], "request": rec["request"],
            "status": "done", "result": f"Wrote synthesis to {out}.",
        }

    q = FakeQueue([s1, s2])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(plan)]), poll_sec=0.01)
    m = M.create_mission(goal)
    await runner.drive(m["id"])

    m = M.get_mission(m["id"])
    text = out.read_text(encoding="utf-8") if out.exists() else ""
    check("T4-B: mission completes done", m["state"] == "done", f"state={m['state']} reason={m.get('failure_reason')}")
    check("T4-B: synthesis.md preserves both scoped dates without false compromise",
          "2018-03-14" in text and "2019-06-01" in text and "syndicated" in text.lower())
    check("T4-B: synthesis.md separates Confirmed, Inferred, and Unknown",
          "## Confirmed" in text and "## Inferred" in text and "## Unknown" in text)


async def case_t4c_unread_source_narrowing():
    print("== T4-C: unread source in workers.coordinate is failed, never empty ==", flush=True)
    orig_agent = W.agent
    orig_synth = W.synthesize

    async def scripted_agent(brief, wid):
        job = brief.split("JOB:\n", 1)[1].split("\n", 1)[0].strip()
        if "archive" in job.lower():
            yield {"type": "tool_result", "name": "computer_read_file",
                   "data": {"success": False, "error": "ENOENT: archive_1999.txt not found"}}
            yield {"type": "content", "content": "Archive check complete; nothing found."}
        else:
            yield {"type": "tool_result", "name": "web_scrape",
                   "data": {"success": True, "url": "https://registry.example/7741", "content": "2018-03-14"}}
            yield {"type": "content", "content": "Confirmed: Incorporated 2018-03-14 (https://registry.example/7741)."}

    async def scripted_synth(job, ok_results):
        return "Confirmed from registry: 2018-03-14."

    W.agent = scripted_agent
    W.synthesize = scripted_synth
    try:
        res = await W.coordinate(
            "t4-parent-1",
            "Verify Acme Labs dates across registry and archive",
            [
                {"task": "Check registry primary record", "outcome": "Sourced incorporation date"},
                {"task": "Check archive_1999.txt for early history", "outcome": "Archive date or unread notice"},
            ],
            timeout=10.0,
        )
        rendered = W.render_result(res)
        check("T4-C: coordinator outcome is 'partial' when one source is unread",
              res["outcome"] == "partial", str(res))
        check("T4-C: unread worker is recorded in failures with tool error",
              len(res["failures"]) == 1 and "ENOENT" in res["failures"][0]["why"],
              str(res["failures"]))
        check("T4-C: render_result names the unread source under 'Workers that did not deliver'",
              "Workers that did not deliver:" in rendered and "archive_1999.txt" in rendered, rendered)
    finally:
        W.agent = orig_agent
        W.synthesize = orig_synth


async def main():
    case_t4a_sourced_answer_unit()
    await case_t4b_contradiction_and_synthesis()
    await case_t4c_unread_source_narrowing()
    skip_live("T4-LIVE-1: LLM adjudicates live conflicting web sources without averaging dates",
              "requires live LLM + pinned Mac scratch instance + family key ceremony")
    skip_live("T4-LIVE-2: LLM rejects syndicated copies as independent confirmations on live URLs",
              "requires live LLM + live web search + pinned Mac scratch instance")

    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed "
          f"({len(skipped)} live-only skipped) ==========", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        return 1
    print("ALL TIER 4 GRADING RIG CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
