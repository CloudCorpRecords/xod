"""
Measuring Xod (services/evals.py, services/scorecard.py) and the four agent tools (agent_job, agent_ask, agent_terminal,
agent_setup), with no model and a throwaway data folder:
  - the routing cases load, and every one names abilities the scorer knows
  - the scorer: the right ability passes, driving an agent through the shell fails, the wrong agent fails, a job
    for each of two agents is checked, and it scores both versions of the tools the same
  - every action of the four tools reaches the narrower tool that does it (nothing those had was lost), and the old
    names still run (approval cards and old plans use them)
  - the chat is offered the four, not the eleven, when a request is about coding agents; the planner is told them
  - a run is saved and compared; the Scorecard reads chat turns, model calls and Crew jobs, each next to the window before
  .venv/bin/python tests/smoke/run_evals_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-evals-"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


async def main():
    from app.services import evals, scorecard
    from app.agent import tools

    print("E1 the cases")
    cases = evals.suite()["cases"]
    known = {"job", "job_status", "job_message", "ask", "terminal", "setup", "login", "list", "answer", "web", "other"}
    every = evals.suite("everyday")
    from app.connectors import mac
    mac_names = {t.name for t in await mac.server.list_tools()}
    check("the everyday cases name This Mac tools that exist", every.get("mac_connected") and all(
          set(c["want"]) - {"web", "answer"} <= mac_names for c in every["cases"]), str([c["id"] for c in every["cases"] if not set(c["want"]) - {"web", "answer"} <= mac_names]))
    check("the routing cases load, with unique ids", len(cases) >= 25 and len({c["id"] for c in cases}) == len(cases))
    check("…each wants abilities the scorer knows", all(set(c["want"]) <= known and set(c.get("never", [])) <= known for c in cases),
          str([c["id"] for c in cases if not set(c["want"]) <= known]))

    print("E2 the scorer")
    S = evals.score
    job = {"q": "have claude code fix the tests", "want": ["job"], "agent": "claude"}
    check("the right ability passes (old tool names)", S(job, {"route": "plan", "calls": [("crew_start", {"task": "x"})]})["pass"])
    check("…and new ones, scored the same", S(job, {"route": "plan", "calls": [("agent_job", {"action": "start", "agent": "claude"})]})["pass"])
    r = S({"q": "have muse fix it", "want": ["job"]}, {"route": "plan", "calls": [("computer_run_command", {"command": "muse exec fix it"})]})
    check("driving an agent through the plain shell fails, saying so", not r["pass"] and "plain shell" in r["why"], r["why"])
    r = S(job, {"route": "plan", "calls": [("agent_job", {"action": "start", "agent": "muse"})]})
    check("the wrong agent fails", not r["pass"] and "not claude" in r["why"], r["why"])
    both = {"q": "claude and muse both", "want": ["job"], "all_agents": ["claude", "muse"]}
    check("two agents: both must get a job", not S(both, {"route": "plan", "calls": [("agent_job", {"action": "start", "agent": "claude"})]})["pass"]
          and S(both, {"route": "plan", "calls": [("agent_job", {"action": "start", "agent": "claude"}), ("agent_job", {"action": "start", "agent": "muse"})]})["pass"])
    check("answering instead of doing fails", not S(job, {"route": "answer", "calls": []})["pass"])
    check("a status check isn't a new job", not S({"q": "how's the job", "want": ["job_status"]}, {"route": "plan", "calls": [("agent_job", {"action": "start"})]})["pass"])
    check("a control (weather) passes with a web search", S({"q": "weather", "want": ["web"], "control": True}, {"route": "web", "calls": []})["pass"])

    print("E3 four agent tools, every ability kept")
    want = {("agent_job", "start"): "crew_start", ("agent_job", "status"): "crew_status", ("agent_job", "message"): "crew_message",
            ("agent_job", "interrupt"): "crew_stop", ("agent_job", "stop"): "crew_stop", ("agent_job", "review"): "crew_review",
            ("agent_job", "merge"): "crew_land", ("agent_job", "pr"): "crew_land", ("agent_job", "discard"): "crew_land",
            ("agent_terminal", "start"): "cli_agent_session", ("agent_terminal", "send"): "cli_agent_session",
            ("agent_setup", "list"): "cli_agents", ("agent_setup", "install"): "cli_agent_install", ("agent_setup", "login"): "cli_agent_login",
            ("agent_setup", "next"): "cli_agent_setup", ("agent_setup", "update"): "cli_agent_setup", ("agent_setup", "doctor"): "cli_agent_setup",
            ("agent_setup", "test"): "cli_agent_setup", ("agent_setup", "status"): "cli_agent_setup"}
    wrong = {k: tools._agent_tool(k[0], {"action": k[1]})[0] for k in want if tools._agent_tool(k[0], {"action": k[1]})[0] != want[k]}
    check("every action reaches the tool that does it", not wrong, str(wrong))
    check("agent_ask: a question goes to the conversation, several at once to parallel runs",
          tools._agent_tool("agent_ask", {"agent": "claude", "message": "hi"})[0] == "cli_agent_chat"
          and tools._agent_tool("agent_ask", {"jobs": [{"agent": "claude", "prompt": "a"}]})[0] == "cli_agent_run")
    check("a half-filled call still lands (no action: a task starts a job, a job + text is a follow-up)",
          tools._agent_tool("agent_job", {"task": "fix"})[0] == "crew_start" and tools._agent_tool("agent_job", {"job": "j", "text": "more"})[0] == "crew_message"
          and tools._agent_tool("agent_job", {})[0] == "crew_status")
    defs = {t["function"]["name"] for t in tools.TOOL_DEFINITIONS}
    check("the chat has the four, and the eleven aren't offered", {"agent_job", "agent_ask", "agent_terminal", "agent_setup"} <= defs
          and not (defs & tools.LEGACY_AGENT_TOOLS))
    r = await tools.execute_tool("cli_agents", {})
    check("…the old names still run (approval cards and old plans use them)", "agents" in r, str(r)[:200])
    names = [t["function"]["name"] for t in tools.select_tools_for_query("have claude code fix the tests in ~/code/app")]
    check("naming a coding agent gives the chat the four", {"agent_job", "agent_ask", "agent_terminal", "agent_setup"} <= set(names), str(names[-8:]))
    check("…\"coding agents\", \"crew\" and a job's name count too", all(tools._wants_cli_agent(q) for q in
          ("which coding agents do I have?", "what are my crew jobs doing", "how is the claude job going", "tell claude-job-2 to stop")))
    check("…not otherwise", not tools._wants_cli_agent("what's the capital of Peru") and not tools._wants_cli_agent("make a file on my desktop"))
    from app.agent.researcher import AgenticResearcher
    a = AgenticResearcher()
    check("the quick router never answers a coding-agent request itself", not a._fast_lane_ok("how is the claude job going?", False, False, None)
          and a._fast_lane_ok("how tall is mount everest?", False, False, None))
    from app.services import sentinel
    check("the safety check looks at what hands an agent work, not at a status check",
          sentinel._kind("agent_job", {"action": "start"}) == "run" and sentinel._kind("agent_job", {"action": "status"}) is None
          and sentinel._kind("agent_ask", {}) == "run" and sentinel._kind("agent_terminal", {"action": "read"}) is None)

    print("E4 runs, and the Scorecard")
    fake = {"suite": "agent_routing", "label": "before", "at": time.time() - 100, "passed": 13, "total": 25, "agent_passed": 9,
            "agent_total": 21, "partial": False, "model": "m", "tools": "11 agent tools", "results": [{"id": "x", "q": "q", "pass": False, "why": "w"}]}
    evals.RESULTS.mkdir(parents=True, exist_ok=True)
    (evals.RESULTS / "agent_routing-1000.json").write_text(json.dumps(fake))
    (evals.RESULTS / "agent_routing-2000.json").write_text(json.dumps({**fake, "label": "some", "partial": True, "passed": 6, "total": 6}))
    check("the latest full run stands for the suite (a run of some cases doesn't)", evals.latest()["label"] == "before")
    check("…and the history lists every run, newest first", [h["label"] for h in evals.history()] == ["some", "before"])
    from app.services import dataset_recorder, turn_quality
    c = turn_quality._conn()
    now = time.time()
    rows = [("t1", now - 3600, "hi", "pass", "", 1), ("t2", now - 7200, "fix it", "fail", "correction", -1),
            ("t3", now - 8 * 86400, "old", "pass", "", 1)]
    for tid, ts, q, chk, sig, lab in rows:
        c.execute("INSERT INTO turns (id, session_id, ts, question, answer, tools, self_check, self_reason, user_signal, label) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (tid, "s", ts, q, "a", json.dumps(["agent_job"]), chk, "it missed the point" if chk == "fail" else "", sig, lab))
    c.commit()
    c.close()
    card = scorecard.card(7)
    t, tb = card["now"]["turns"], card["before"]["turns"]
    check("chat answers this week: counted, with Xod's own check and your corrections", t["turns"] == 2 and t["pass_rate"] == 0.5 and t["corrected"] == 1, str(t)[:300])
    check("…next to the week before", tb["turns"] == 1 and tb["pass_rate"] == 1.0)
    check("…what went wrong is listed, with why", t["went_wrong"] and t["went_wrong"][0]["question"] == "fix it" and "missed" in t["went_wrong"][0]["why"])
    check("…and which tools the answers used", ("agent_job", 2) in [tuple(x) for x in t["top_tools"]], str(t["top_tools"]))
    ar = next(x for x in card["routing"]["suites"] if x["suite"] == "agent_routing")
    check("each routing suite is on it, with what failed", ar["latest"]["passed"] == 13 and ar["latest"]["failed"][0]["id"] == "x"
          and {x["suite"] for x in card["routing"]["suites"]} >= {"agent_routing", "everyday"})
    import httpx
    from app.main import app as xod_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=xod_app), base_url="http://127.0.0.1:8000") as cl:
        r = (await cl.get("/api/scorecard?days=7")).json()
        check("the Scorecard panel's API answers", r["now"]["turns"]["turns"] == 2 and r["days"] == 7)


if __name__ == "__main__":
    asyncio.run(main())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)
