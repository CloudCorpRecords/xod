"""
Phone routing: a job that belongs on the person's own phone must reach the phone tool.

Two surfaces lost such jobs on 2026-11-02 (see ~/workspace/xod-eval/PHONE-MAC-LANE-2026-11-02.md, Job 2):
- POST /api/tasks: worker.route() only knew kinds "computer" and "web", and worker.stream() offered only the
  computer or web tool sets, neither of which contains the phone tool. A Facebook-by-phone request was
  classified "web" and the executor searched DuckDuckGo for the request text.
- POST /api/chat: the researcher's tool selection only adds the phone tool for a narrow keyword list
  ("my phone", "tiktok", "instagram", ...). "open Facebook on my S25 Ultra" matched none of them, so the
  agent truthfully answered that it had no phone tools. (The 2026-10-30 Calculator success was a chat whose
  words, "open the calculator app on my phone", happened to hit the list.)

This suite pins the routing: phone jobs route to a phone kind, the worker offers the phone tool (and only it)
when a paired phone is online, refuses plainly when none is online, and chat tool selection finds the phone
tool for the wordings people actually use.

  python tests/smoke/run_phone_routing_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="xod-phone-routing-")
os.environ["XOD_DATA_DIR"] = os.path.join(TMP, "data")

from app.agent import worker  # noqa: E402
from app.agent import tools as agent_tools  # noqa: E402
from app.services import capabilities, phone_mcp  # noqa: E402

failures, passed = [], 0


def check(name, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failures.append(name)
        print(f"  ✗ {name}  {detail}")


# ------------------------------------------------------------------ R1 routing: phone jobs get a phone kind
print("R1 worker routing")
PHONE_JOBS = [
    "Use my phone to open Facebook and post a comment on my latest post",
    "open Facebook on my S25 and post a comment on my post",
    "post this photo to Instagram from my phone",
    "open the Calculator app on my phone",
    "text Maria from my phone that I am running late",
]
for q in PHONE_JOBS:
    r = worker.route(q, [{"role": "user", "content": q}])
    check(f"phone kind: {q[:52]}", bool(r) and r.get("kind") == "phone", str(r))

r = worker.route("Install jq", [{"role": "user", "content": "Install jq"}])
check("computer job stays computer", bool(r) and r.get("kind") == "computer", str(r))
r = worker.route("what's the weather tomorrow", [{"role": "user", "content": "what's the weather tomorrow"}])
check("weather is not the worker", r is None, str(r))
r = worker.route("compare phones with the best cameras", [{"role": "user", "content": "compare phones with the best cameras"}])
check("researching phones is not a phone job", not r or r.get("kind") != "phone", str(r))
r = worker.route("research the best Android phone to buy", [{"role": "user", "content": "research the best Android phone to buy"}])
check("buying advice is not a phone job", not r or r.get("kind") != "phone", str(r))

# A named website is a browser job, not the phone app.
r = worker.route("go to facebook.com and post a comment", [{"role": "user", "content": "go to facebook.com and post a comment"}])
check("facebook.com is not a phone job", not r or r.get("kind") != "phone", str(r))

# The researcher's browser mission must not hijack a phone job (it fires before worker routing and
# matches "open"/"post" plus the site name: the exact shape of the 2026-11-02 live failure).
from app.agent.researcher import AgenticResearcher  # noqa: E402
PHONE_Q = "Use my phone to open Facebook and post a comment on my most recent post"
check("browser mission leaves a phone job alone", AgenticResearcher()._mission_request(PHONE_Q, {}) is None,
      str(AgenticResearcher()._mission_request(PHONE_Q, {})))
check("browser mission still takes a website job",
      AgenticResearcher()._mission_request("go to facebook.com and post a comment", {}) is not None)

# A paused phone job picked up with "continue" keeps its kind.
hist = [{"role": "user", "content": "post a comment on Facebook from my phone"},
        {"role": "assistant", "content": 'Paused. <!-- xod-work:{"goal": "post a comment on Facebook from my phone", "kind": "phone", "left": "the comment is typed"} -->'},
        {"role": "user", "content": "continue"}]
r = worker.route("continue", hist)
check("continue keeps the phone kind", bool(r) and r.get("kind") == "phone" and r.get("resume") == "the comment is typed", str(r))


# ------------------------------------------------------------------ R2 chat tool selection finds the phone
print("R2 chat tool selection")
def selected_names(q):
    return [t["function"]["name"] for t in agent_tools.select_tools_for_query(q)]

for q in ("open Facebook on my S25 Ultra and comment on my own post",
          "use my cellphone to open Facebook",
          "post to Facebook for me",
          "open the calculator app on my phone"):
    check(f"chat offers phone: {q[:52]}", "phone" in selected_names(q), str(selected_names(q)))
check("chat does not offer phone for small talk", "phone" not in selected_names("what is the capital of France"),
      str(selected_names("what is the capital of France")))


# ------------------------------------------------------------------ R3 the worker runs a phone job on the phone tool
print("R3 worker phone run")
class Script:
    replies, verdicts, seen_tools = [], [], []


def _call(name, args):
    return [{"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]


async def fake_deliberate(messages, tools=None, **kw):
    Script.seen_tools.append([t["function"]["name"] for t in (tools or [])])
    r = Script.replies.pop(0) if Script.replies else ("say", "All done.")
    if r[0] == "tool":
        yield {"type": "deliberation_complete", "tool_calls": _call(r[1], r[2]), "content": ""}
    else:
        yield {"type": "deliberation_complete", "tool_calls": [], "content": r[1]}


async def fake_chat(messages, **kw):
    v = Script.verdicts.pop(0) if Script.verdicts else {"done": True, "proof": "ok"}
    return {"content": json.dumps(v)}


worker.llm_service.deliberate_stream = fake_deliberate
worker.llm_service.chat_completion = fake_chat


async def fake_model():
    return {"model": "scripted", "server": "test", "tools": True, "problem": None}


capabilities._chat_model = fake_model

PHONE_STATE = {"phones": [{"address": "phone@test:8790", "online": True, "model": "SM-S938U"}]}
phone_mcp.phone_list = lambda: PHONE_STATE

executed = []


async def fake_execute(name, args):
    executed.append((name, args))
    return {"success": True, "summary": "the phone did it and the screen shows it"}


async def run_phone_job():
    q = "Use my phone to open Facebook and post a comment on my latest post"
    decision = worker.route(q, [{"role": "user", "content": q}])
    evs = []
    async for ev in worker.stream(q, [{"role": "user", "content": q}], decision, fake_execute):
        evs.append(ev)
    return evs


Script.replies = [("tool", "phone", {"action": "task", "app": "Facebook", "goal": "post a comment on my latest post"}),
                  ("say", "Done: the comment is on your latest post.")]
Script.verdicts = [{"done": True, "proof": "the phone showed the comment posted"}]
evs = asyncio.run(run_phone_job())
check("the model was offered the phone tool", bool(Script.seen_tools) and "phone" in Script.seen_tools[0],
      str(Script.seen_tools[:1]))
check("no computer or web tools offered alongside the phone tool",
      bool(Script.seen_tools) and all(n in ("phone", "team_delegate") for n in Script.seen_tools[0]),
      str(Script.seen_tools[:1]))
check("the phone tool ran", [n for n, _ in executed] == ["phone"], str(executed))
check("the job finished with an answer", any(e["type"] == "content" and "comment" in e.get("content", "") for e in evs),
      str([e["type"] for e in evs]))

# No phone online: the job stops plainly, the model is never asked to improvise with other tools.
Script.seen_tools, executed = [], []
PHONE_STATE["phones"] = [{"address": "phone@test:8790", "online": False}]
evs = asyncio.run(run_phone_job())
text = " ".join(e.get("content", "") for e in evs if e["type"] == "content")
check("offline phone: plain refusal", "phone" in text.lower() and ("isn't" in text or "not" in text.lower()), text[:200])
check("offline phone: model never called", Script.seen_tools == [], str(Script.seen_tools))
check("offline phone: nothing executed", executed == [], str(executed))

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print("FAILURES:", failures)
    sys.exit(1)
