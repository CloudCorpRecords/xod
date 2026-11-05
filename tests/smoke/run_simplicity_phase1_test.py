"""
Phase 1 of the simplicity redesign (one front door):
  - the merged composer routing (quick task vs mission, plan shown
    only for big or sensitive jobs) via the real work_router.js in node
  - the status line wording and the Work-feed bucket mapping
  - missions.preview_plan / decompose with a stub planner (no server,
    no model, no keychain)
  - the surface itself: one composer in index.html, no Hand off /
    Plan & run choice, model picker + Local chip one layer down in
    the status detail, keep-going state visible in a mission's Details
  python tests/smoke/run_simplicity_phase1_test.py
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="xod-phase1-"))
os.environ["XOD_DATA_DIR"] = str(TMP)
os.environ.setdefault("XOD_SPEECH", "off")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


NODE_HARNESS = r"""
const R = require(process.argv[2]);
const out = { cases: [], status: {}, buckets: {}, outcomes: {} };
const texts = [
  "Check the price of the Sony headphones",
  "Sort the Downloads folder into named subfolders",
  "Apply to the Junior Python role on indeed.com",
  "Send this email to Mariane",
  "Sort the Downloads folder, then send me the list, then delete the duplicates",
  "Delete the old backups",
  "Pay the invoice for the venue",
  "Clean up the photo library, rename the folders by date, and then check every folder is named right",
  "Build the word frequency tool",
];
for (const t of texts) out.cases.push(R.classifyWorkRequest(t));
out.status = {
  idle: R.statusLineText({}), one: R.statusLineText({ working: 1 }),
  two: R.statusLineText({ working: 2 }), needs: R.statusLineText({ needsYou: 1, working: 3 }),
};
out.buckets = {
  waiting: R.taskBucket("waiting"), queued: R.taskBucket("queued"), done: R.taskBucket("done"),
  paused: R.missionBucket("paused"), running: R.missionBucket("running"), failed: R.missionBucket("failed"),
};
out.outcomes = {
  sorted: R.plainOutcome({ title: "Sort x", result: "The folder is sorted. 214 files moved." }),
  fallback: R.plainOutcome({ title: "Build the tool", result: "" }),
};
console.log(JSON.stringify(out));
"""


def main():
    print("W1 merged-composer routing (the real work_router.js, in node)")
    node = shutil.which("node")
    check("node is available for the JS harness", bool(node))
    data = None
    if node:
        harness = TMP / "harness.js"
        harness.write_text(NODE_HARNESS, encoding="utf-8")
        proc = subprocess.run([node, str(harness), str(ROOT / "app" / "static" / "js" / "work_router.js")],
                              capture_output=True, text=True, timeout=60)
        check("router harness runs", proc.returncode == 0, proc.stderr[:200])
        if proc.returncode == 0:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
    if data:
        want = [
            ("task", False), ("task", False), ("task", True), ("task", True),
            ("mission", True), ("task", True), ("task", True), ("mission", True), ("task", False),
        ]
        got = [(c["kind"], c["needsConfirm"]) for c in data["cases"]]
        check("routing matches: quick jobs run, big/sensitive confirm", got == want, str(got))
        sens = data["cases"][4]["sensitive"]
        check("sending + deleting are both caught", "sending" in sens and "deleting" in sens, str(sens))
        check("money is caught", data["cases"][6]["sensitive"] == ["money"], str(data["cases"][6]))
        st = data["status"]
        check("status line: idle", st["idle"] == "All good.", st["idle"])
        check("status line: one job", st["one"] == "Working on 1 thing.", st["one"])
        check("status line: two jobs", st["two"] == "Working on 2 things.", st["two"])
        check("status line: needs-you wins", st["needs"] == "Needs you: 1", st["needs"])
        b = data["buckets"]
        check("feed buckets: waiting/paused need you, running runs, done finishes",
              (b["waiting"], b["queued"], b["done"], b["paused"], b["running"], b["failed"])
              == ("needs", "running", "finished", "needs", "running", "finished"), str(b))
        check("outcome card: plain first sentence",
              data["outcomes"]["sorted"] == "Done. The folder is sorted.", data["outcomes"]["sorted"])
        check("outcome card: title fallback",
              data["outcomes"]["fallback"] == "Done: Build the tool", data["outcomes"]["fallback"])

    print("W2 plan preview reuses the real planner (stub ask, nothing runs)")
    from app.services import missions

    plan_json = json.dumps({"subtasks": [
        {"title": "Sort the files", "instruction": "Sort the Downloads folder.",
         "expected_effects": [], "inputs": [{"source": "goal"}], "tools": []},
        {"title": "Send the list", "instruction": "Send the list to the user.",
         "expected_effects": [], "inputs": [{"source": "goal"}], "tools": []},
    ]})

    async def stub_ask(prompt):
        return plan_json

    async def garbage_ask(prompt):
        return "no json here"

    preview = asyncio.run(missions.decompose("Sort Downloads, then send the list", stub_ask))
    check("decompose returns the validated plan", [s["title"] for s in preview["subtasks"]] == ["Sort the files", "Send the list"])
    missions._default_ask = stub_ask  # preview_plan plans through the same default ask the runner uses
    got = asyncio.run(missions.preview_plan("Sort Downloads, then send the list"))
    check("preview_plan returns plain step titles, creates nothing",
          got["steps"][0]["title"] == "Sort the files" and len(got["steps"]) == 2
          and missions.list_missions() == [], str(got)[:200])
    try:
        asyncio.run(missions.preview_plan("   "))
        check("preview_plan rejects an empty goal", False)
    except ValueError:
        check("preview_plan rejects an empty goal", True)
    missions._default_ask = garbage_ask
    try:
        asyncio.run(missions.preview_plan("anything"))
        check("an unplannable goal is rejected, never invented", False)
    except missions.PlanRejected:
        check("an unplannable goal is rejected, never invented", True)

    print("W3 the surface: one composer, status line, controls one layer down")
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    tasks_js = (ROOT / "app" / "static" / "js" / "tasks.js").read_text(encoding="utf-8")
    missions_js = (ROOT / "app" / "static" / "js" / "missions.js").read_text(encoding="utf-8")
    work_js = (ROOT / "app" / "static" / "js" / "work.js").read_text(encoding="utf-8")
    main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    check("exactly one work composer input", html.count('id="workInput"') == 1)
    check("the two old composer inputs are gone from the surface",
          'id="newTaskInput"' not in html and 'id="newMissionInput"' not in html)
    check("no Hand off / Plan & run choice on the surface",
          ">Hand off</button>" not in html and "Plan &amp; run" not in html)
    form = html[html.index('id="workInput"'):html.index("</form>", html.index('id="workInput"'))]
    check("keep-going lives in the composer's Details, not on the surface",
          "missionContinueChk" in form and "<summary" in form and "Details" in form)
    check("status line is on the surface with its human default",
          'id="xodStatusText">All good.<' in html)
    panel_at = html.index('id="xodStatusPanel"')
    check("model picker is inside the status detail (one layer down)",
          panel_at < html.index('id="modelSwitcherBtn"') < html.index('id="modelSwitcherPanel"'))
    check("Local chip is inside the status detail (one layer down)",
          panel_at < html.index('id="localServersBtn"') < html.index('id="localServersText"'))
    check("no model picker on the top-bar surface before the status detail",
          'id="modelSwitcherBtn"' not in html[:panel_at] and 'id="localServersBtn"' not in html[:panel_at])
    check("plan preview endpoint exists", '"/api/missions/plan"' in main_py)
    check("work.js asks for the plan before big/sensitive missions run",
          "/api/missions/plan" in work_js and "submitWorkText" in work_js)
    check("both old entry points route through the one composer",
          "submitWorkText" in tasks_js and "createMissionFromPanel" in missions_js)
    groups_at = tasks_js.index('["Needs you"')
    check("Work feed order: Needs you, then Working, then Finished",
          groups_at < tasks_js.index('["Working"') < tasks_js.index('["Finished"'))
    check("missions publish into the same feed buckets",
          "__xodMissionBuckets" in missions_js and "__xodMissionBuckets" in tasks_js)
    check("done jobs render as outcome cards", "outcomeCard" in tasks_js and "plainOutcome" in tasks_js)
    check("a mission's Details shows its keep-going state", "If a step fails:" in missions_js)

    failed = [n for n, ok in results if not ok]
    print(f"SUMMARY {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)} checks passed"
          + (f" — failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
