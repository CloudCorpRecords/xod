"""
Goals and the morning brief (services/goals.py), with a scripted model: a goal gets a plan and starts a check-in task
at once; a check-in's PROGRESS line moves the plan on (done, blocked with why, the whole goal done); check-ins come
round on their own; a failed check-in is noted; the brief is due once a day after its time and says what's going on;
and the chat sentences that make a goal or switch the brief on. No real model needed.

  python tests/smoke/run_goals_test.py
"""
import asyncio
import datetime as dt
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="xod-goals-")
os.environ["XOD_DATA_DIR"] = os.path.join(TMP, "data")

from app.services import goals  # noqa: E402
from app.services.task_queue import task_queue, _conn as task_db  # noqa: E402
from app.services import llm as llm_mod  # noqa: E402

failures, passed = [], 0


def check(name, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failures.append(name)
        print(f"  ✗ {name}  {str(detail)[:300]}")


PLAN = {"title": "Flat under $2k", "outcome": "A signed lease for a 1-bed under $2,000 a month",
        "deadline": "2026-11-30", "steps": ["List what matters (area, size, move-in date)", "Find listings under $2k",
                                             "Shortlist the best five", "Draft messages to the landlords"], "check_every_hours": 24}


async def fake_chat(messages, **kw):
    if "Spanish" in messages[-1]["content"]:
        return {"content": json.dumps({"title": "Spanish for the trip", "steps": ["Pick a course", "Practise daily"], "check_every_hours": 72})}
    return {"content": json.dumps(PLAN)}
llm_mod.llm_service.chat_completion = fake_chat


def finish(task_id, status, report):
    with task_db() as c:
        c.execute("UPDATE tasks SET status = ?, result = ?, finished_at = ? WHERE id = ?", (status, report, time.time(), task_id))


print("G1 making a goal")
g = asyncio.run(goals.create("find me a flat under $2k by the end of November"))
check("it has the plan the model wrote", g["title"] == "Flat under $2k" and len(g["plan"]) == 4 and g["deadline"] == "2026-11-30", g)
t = task_queue.get(g["last_task_id"])
check("the first check-in started as a task", t and t["title"] == "◎ Flat under $2k" and g["checking"], t)
check("the check-in asks for step 1 and a PROGRESS line, and not to send or buy",
      "Work on step 1" in t["request"] and "PROGRESS: step 1 done" in t["request"] and "don't send, buy" in t["request"], t["request"][:400])
check("the next one is a day later", abs(g["next_check_at"] - (time.time() + 24 * 3600)) < 120, g["next_check_at"])

print("G2 a check-in moves the plan on")
finish(g["last_task_id"], "done", "Area: Mission or Noe; 1 bed; from Nov 1.\nPROGRESS: step 1 done")
goals.tick()
g = goals.get(g["id"])
check("step 1 is done and the next step is 2", g["plan"][0]["status"] == "done" and g["next_step"] == PLAN["steps"][1], g["plan"])
check("what it found is in the log (without the PROGRESS line)", g["log"] and "Mission" in g["log"][-1]["text"] and "PROGRESS" not in g["log"][-1]["text"], g["log"])
check("not checking any more, and no new check-in before it's due", not g["checking"])

print("G3 due again → a new check-in on its own")
goals._save(g["id"], next_check_at=time.time() - 5)
started = goals.tick()
g = goals.get(g["id"])
t = task_queue.get(g["last_task_id"])
check("it checked in by itself", g["id"] in started and g["checking"] and "Work on step 2" in t["request"], started)
check("…and the request carries what earlier check-ins found", "Mission" in t["request"], t["request"][:600])

print("G4 blocked, failed, and done")
finish(g["last_task_id"], "done", "Couldn't search one site without an account.\nPROGRESS: step 2 blocked: Zillow wants a sign-in")
goals.tick()
g = goals.get(g["id"])
check("blocked: marked, with why", g["plan"][1]["status"] == "blocked" and "sign-in" in g["plan"][1].get("note", ""), g["plan"][1])
goals.check_in(g["id"])
g = goals.get(g["id"])
finish(g["last_task_id"], "failed", "")
goals.tick()
g = goals.get(g["id"])
check("a failed check-in is noted and it isn't stuck 'checking'", not g["checking"] and "failed" in g["log"][-1]["text"], g["log"][-1:])
goals.check_in(g["id"])
g = goals.get(g["id"])
finish(g["last_task_id"], "done", "Lease signed for the Noe place.\nPROGRESS: goal done")
goals.tick()
g = goals.get(g["id"])
check("PROGRESS: goal done → the goal is done", g["status"] == "done" and g["progress"]["done"] == 4, g)
check("a done goal doesn't check in again", g["id"] not in (goals._save(g["id"], next_check_at=time.time() - 5) or goals.tick()))

print("G5 the morning brief")
g2 = asyncio.run(goals.create("learn enough Spanish for my trip", start_now=False))
s = goals.set_brief(enabled=True, at="00:00")
check("on, at the chosen time", s["enabled"] and s["time"] == "00:00")
check("due today (after its time, not run yet)", goals._brief_due())
started = goals.tick()
brief = [t for t in task_queue.list() if t["title"] == "☀ Morning brief"]
check("it ran as a task", "brief" in started and brief, started)
check("it covers the active goals and what's next (not the finished one)",
      g2["title"] in brief[0]["request"] and "next:" in brief[0]["request"] and "Flat under $2k" not in brief[0]["request"], brief[0]["request"][:500])
check("…and today's calendar and news", "calendar" in brief[0]["request"] and "news" in brief[0]["request"])
check("once a day: not due again today", not goals._brief_due())
s = goals.set_brief(at="23:59")
check("moved later today: it comes again today", s["last_date"] == "" if dt.datetime.now().strftime("%H:%M") < "23:59" else True, s)
goals.set_brief(enabled=False)
check("off: never due", not goals._brief_due())

print("G6 from the chat")
for text, kind in (("New goal: find a flat under $2k by November", "goal"), ("Set a goal to learn Spanish before my trip", "goal"),
                   ("My goal is to run a 10k in March", "goal"), ("turn on the morning brief at 7:30am", "brief"),
                   ("stop the morning brief", "brief")):
    r = goals.route(text)
    check(f"“{text}” → {kind}", r and r["kind"] == kind, r)
r = goals.route("turn on the morning brief at 7:30am")
check("…at 7:30 am → 07:30", r["at"] == "07:30" and r["on"], r)
check("“stop the morning brief” → off", goals.route("stop the morning brief")["on"] is False)
for text in ("what's my goal for today?", "how do I set goals in life", "what's the weather"):
    check(f"not a goal: “{text}”", goals.route(text) is None, goals.route(text))


async def chat_goal():
    evs = [e async for e in goals.stream_chat({"kind": "goal", "text": "find a flat under $2k by November"})]
    return "".join(e.get("content", "") for e in evs if e["type"] == "content")
answer = asyncio.run(chat_goal())
check("the chat answer shows the plan and says it started", "Flat under $2k" in answer and "1. List what matters" in answer
      and "started on step 1" in answer, answer[:300])

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print("FAILED: " + "; ".join(failures))
    sys.exit(1)
