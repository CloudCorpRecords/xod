"""
Worker routing fix (2026-11-08): a dictated file write is a computer job.

Live record (the owner's Mac, shipped 1.0.2, missions b4cb8ea060 and
a09cc20b6e): step 2, "Write proof note", carried the assembled request

    Create note.txt in proofdir containing exactly baseline proof 7731
    Create the file at /Users/owner/xod-t1-scratch/proofdir/note.txt
    with exact text: baseline proof 7731

and finished with ZERO tool calls: the worker router did not claim the
request (its plain-words job pattern names scripts, csv and json files
but not a plain dictated file), so the request fell through to the
planner, whose deliberation answered direct with a prose refusal ("I
can't create the file because I don't have access to file-writing
tools in this session"). The disk verifier failed the step honestly,
but no file tool was ever offered the job. The write path itself was
never the problem: both write services create parents and only report
success after a stat, and the outcomes layer read-back-verifies a
write that actually happens.

Covered here:
  - the live request shape routes to the worker as a computer job.
  - the plain-words kin route too: a named file with dictated contents
    ("containing exactly", "whose contents are exactly this").
  - questions and non-file writing stay out of the worker.
  - end to end: the routed job, driven by a scripted model through
    the REAL tool executor, lands the file on disk with the exact
    dictated text.

  python tests/smoke/run_worker_file_job_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-worker-filejob-"))
os.environ["XOD_DATA_DIR"] = str(TMP / "data")

from app.agent import worker  # noqa: E402
from app.agent import tools as agent_tools  # noqa: E402
from app.services import capabilities  # noqa: E402

failures, passed = [], 0


def check(name, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  PASS {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}  {detail}")


# ------------------------------------------------------------------ routing
print("routing: a dictated file write is a computer job")
PROOFDIR = TMP / "xod-t1-scratch" / "proofdir"
NOTE = PROOFDIR / "note.txt"
LIVE_REQUEST = ("Create note.txt in proofdir containing exactly baseline proof 7731.\n"
                f"Create the file at {NOTE} with exact text: baseline proof 7731")
r = worker.route(LIVE_REQUEST, [{"role": "user", "content": LIVE_REQUEST}])
check("the live mission step-2 request routes to the worker",
      bool(r) and r.get("kind") == "computer", str(r))

for q in ("Create a plain text file at " + str(TMP / "hello.txt") + " whose contents are exactly this: HELLO 42",
          "make a file called shopping.txt containing exactly: milk, eggs, rice",
          "Write the file at " + str(TMP / "w.txt") + " with exact text: pinned 1234"):
    r = worker.route(q, [{"role": "user", "content": q}])
    check(f"worker: {q[:60]}...", bool(r) and r.get("kind") == "computer", str(r))

for q in ("What's the last line of big.log?",
          "Write me a poem about autumn leaves",
          "How do I create a file in Python?",
          "Tell me about the file system on this computer"):
    r = worker.route(q, [{"role": "user", "content": q}])
    check(f"not the worker: {q}", r is None, str(r))

r = worker.route("Write a script that saves a csv",
                 [{"role": "user", "content": "Write a script that saves a csv"}])
check("the pre-existing script/csv routing still claims its jobs",
      bool(r) and r.get("kind") == "computer", str(r))


# ------------------------------------------------------- end to end, real tools
class Script:
    replies, verdicts, seen = [], [], []


def call(name, args):
    return [{"id": f"c{len(Script.seen)}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}]


async def fake_deliberate(messages, tools=None, **kw):
    Script.seen.append([dict(m) for m in messages])
    r = Script.replies.pop(0) if Script.replies else ("say", "All done.")
    if r[0] == "tool":
        yield {"type": "deliberation_complete", "tool_calls": call(r[1], r[2]), "content": ""}
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


async def main():
    print("end to end: the routed job lands the dictated file")
    PROOFDIR.mkdir(parents=True)
    decision = worker.route(LIVE_REQUEST, [{"role": "user", "content": LIVE_REQUEST}])
    if not decision:
        check("end to end: the job was routed", False, "route returned None")
        return
    Script.replies = [("tool", "computer_write_file",
                       {"filepath": str(NOTE), "content": "baseline proof 7731"}),
                      ("say", "Created note.txt with the exact text.")]
    Script.verdicts = [{"done": True, "proof": "note.txt is on disk with the dictated text"}]
    evs = []
    async for ev in worker.stream(LIVE_REQUEST, [{"role": "user", "content": LIVE_REQUEST}],
                                  decision, agent_tools.execute_tool):
        evs.append(ev)
    check("end to end: note.txt exists on disk afterwards", NOTE.is_file(),
          f"dir listing: {list(PROOFDIR.iterdir())}")
    if NOTE.is_file():
        check("end to end: its contents are the dictated text, byte for byte",
              NOTE.read_text(encoding="utf-8") == "baseline proof 7731",
              repr(NOTE.read_text(encoding="utf-8")))


asyncio.run(main())
print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print("FAILED: " + ", ".join(failures))
sys.exit(1 if failures else 0)
