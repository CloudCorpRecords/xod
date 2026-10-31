"""
Answer-delivery fidelity: decisive tool evidence must reach the delivered answer.

End-to-end through the task layer (TaskQueue -> AgenticResearcher -> real
execute_tool -> real locate service) against a planted forest in a temp dir.
The model is stubbed the way the real one behaved in the baseline hunts: it
calls locate_files, gets the full structured result, then writes a
paraphrasing final answer that drops the paths and the evidence ("its full
path was not provided by the search"). The contract under test is not prompt
wording: the delivered answer (the task's stored result) must carry the
decisive structured payload deterministically.

Cases (hunt shapes from the L-set regression corpus):
  A  decoy years (L9): tax return summary 2025, not 2024
  B  ranking order (L8): family reunion photo 2024 ahead of 2023
  C  ask (close decoys): two Smith leases; both candidates and the question
     survive, and the task waits for an answer instead of picking one
  D  not_found (N1 shape): a zebrafish board deck that does not exist; the
     tried list (routes and terms) survives
  E  output discipline: the user dictated an exact reply; no block is stapled
  W  the worker lane: same contract on worker.stream

No real model, no Keychain, no network:
  PYTHON_KEYRING_BACKEND=tests.support.filekeyring.FileKeyring .venv/bin/python tests/task_inbox/run_delivery_evidence_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-delivery-data-")
os.environ["XOD_FAST_LANE"] = "0"  # find requests belong to the planner; the fast lane is for lookups
os.environ["XOD_DATASET_DB"] = str(Path(os.environ["XOD_DATA_DIR"]) / "flywheel_test.db")

import app.services.task_queue as TQ  # noqa: E402
import app.services.browser_sandbox as BS  # noqa: E402
import app.agent.researcher as RS  # noqa: E402
import app.agent.worker as WK  # noqa: E402
from app.services import llm as llm_mod  # noqa: E402
from app.agent.tools import execute_tool  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="xod_delivery_"))
TQ.DB_PATH, TQ.SHOTS_DIR = TMP / "tasks.db", TMP / "shots"
TQ.notify = lambda title, msg: None
if hasattr(TQ, "_phone"):
    TQ._phone = lambda task, kind: None


async def _no_browser_needed(*a, **k):
    return None


BS.browser_sandbox_service.ensure_browser_running = _no_browser_needed

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {str(detail)[:260]}" if detail and not ok else ""), flush=True)


# ---------------------------------------------------------------- the forest
FOREST = Path(tempfile.mkdtemp(prefix="xod-delivery-forest-"))


def plant(rel, body=b"%PDF fake", when=None):
    p = FOREST / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body if isinstance(body, bytes) else body.encode())
    if when:
        ts = datetime(*when).timestamp()
        os.utime(p, (ts, ts))
    return p


TAX25 = plant("taxes/2025/Tax Return Summary 2025.pdf", when=(2025, 6, 15))
TAX24 = plant("taxes/2024/Tax Return Summary 2024.pdf", when=(2024, 6, 15))
REUNION24 = plant("Pictures/Family Reunion 2024.png", b"\x89PNG", (2024, 7, 4))
REUNION23 = plant("Pictures/Family Reunion 2023.png", b"\x89PNG", (2023, 7, 4))
LEASE_A = plant("Legal/Smith Lease.pdf", when=(2025, 3, 1))
LEASE_B = plant("Archive/Smith Lease.pdf", when=(2025, 3, 1))
plant("Trips/Japan Trip/itinerary.txt", "Japan trip: Tokyo, Kyoto, bullet train, ramen")
plant("notes/meeting.txt", "standup notes, roadmap chat")

# ---------------------------------------------------------------- stub model
# The planner is scripted: one locate_files step against the forest. The
# synthesis model is scripted to behave like the real one did: paraphrase
# and drop the paths and evidence.
CURRENT = {"locate_request": "", "prose": ""}


async def _stub_plan(self, query=None, conversation_history=None, **kw):
    yield {
        "type": "deliberation_complete",
        "deliberation": {
            "thought": "a find request: call locate_files",
            "decision": "execute_plan",
            "direct_response": "",
            "plan_title": "Find it",
            "plan_steps": [{
                "id": 1,
                "title": "Locate the file",
                "tools": ["locate_files"],
                "tool_args": [{"request": CURRENT["locate_request"], "root_dir": str(FOREST)}],
                "status": "pending",
            }],
            "task_focus": "computer",
            "is_swarm": False,
        },
    }


RS.AgenticResearcher.deliberate_and_plan = _stub_plan


async def _stub_synthesis(messages=None, **kw):
    yield {"type": "content", "content": CURRENT["prose"]}


async def _stub_chat(messages=None, **kw):
    return {"content": json.dumps({"action": "conclude", "reasoning": "stub"})}


llm_mod.llm_service.stream_chat_completion = _stub_synthesis
llm_mod.llm_service.chat_completion = _stub_chat

# Worker lane stubs: scripted deliberation (locate call, then a paraphrase),
# a ready computer, and a done-check that agrees.
WK_STATE = {"calls": 0}


async def _stub_deliberate(messages=None, tools=None, **kw):
    WK_STATE["calls"] += 1
    if WK_STATE["calls"] == 1:
        yield {"type": "deliberation_complete",
               "tool_calls": [{"id": "w1", "type": "function",
                               "function": {"name": "locate_files",
                                            "arguments": json.dumps({"request": CURRENT["locate_request"],
                                                                     "root_dir": str(FOREST)})}}],
               "content": ""}
    else:
        yield {"type": "deliberation_complete", "tool_calls": [], "content": CURRENT["prose"]}


llm_mod.llm_service.deliberate_stream = _stub_deliberate


async def _stub_readiness():
    return {"os": "macOS", "shell": "zsh", "home": str(FOREST), "package managers": [],
            "languages": [], "online": True, "free_gb": 99, "admin": False,
            "blockers": [], "notes": [], "model": {"name": "stub", "tools": True, "problem": None}}


async def _stub_done_check(goal, log, content, kind="computer"):
    return {"done": True, "proof": "the stub check passed"}


WK.readiness = _stub_readiness
WK.done_check = _stub_done_check

DROP_PROSE = "Found it: the file you asked for. Its full path was not provided by the search, so I can only give you its name."


async def run_task(task_text, locate_request, prose):
    CURRENT["locate_request"] = locate_request
    CURRENT["prose"] = prose
    q = TQ.TaskQueue()
    tid = q.create(task_text)["id"]
    await q._run(tid)
    return q.get(tid)


async def main():
    # A: decoy years, found
    t = await run_task("Find the tax return summary for 2025, not 2024. Reply with its full path.",
                       "find the tax return summary for 2025, not 2024", DROP_PROSE)
    r = t.get("result") or ""
    check("A tax: task done", t["status"] == "done", t["status"])
    check("A tax: full path of the 2025 file is in the delivered answer", str(TAX25) in r, r)
    check("A tax: the 2024 decoy path is not delivered as a candidate", str(TAX24) not in r, r)
    check("A tax: modified date evidence is in the delivered answer", "2025-06-15" in r, r)
    check("A tax: why evidence is in the delivered answer", "year 2025" in r, r)
    ev = t.get("evidence_json")
    payloads = json.loads(ev) if isinstance(ev, str) else (ev or [])
    check("A tax: complete payload stored on the task, untruncated",
          bool(payloads) and payloads[-1]["payload"]["candidates"][0]["path"] == str(TAX25)
          and bool(payloads[-1]["payload"]["candidates"][0]["why"]), str(ev)[:200])

    # B: ranking order preserved
    t = await run_task("Find the family reunion photo from 2024. Reply with its full path.",
                       "find the Family Reunion photo from 2024", DROP_PROSE)
    r = t.get("result") or ""
    check("B reunion: both candidate paths delivered", str(REUNION24) in r and str(REUNION23) in r, r)
    check("B reunion: ranking order preserved (2024 before 2023)",
          str(REUNION24) in r and str(REUNION23) in r and r.index(str(REUNION24)) < r.index(str(REUNION23)), r)

    # C: ask, both candidates and the question; the task waits
    t = await run_task("Find the Smith lease. Reply with its full path.",
                       "find the Smith lease", "I found a Smith lease for you.")
    r = t.get("result") or ""
    check("C ask: task waits for an answer instead of picking", t["status"] == "waiting" and t.get("waiting_kind") == "question",
          f"{t['status']}/{t.get('waiting_kind')}")
    check("C ask: both candidate paths delivered", str(LEASE_A) in r and str(LEASE_B) in r, r)
    check("C ask: the question survives", "Which one did you mean?" in r, r)

    # D: not found, tried list survives
    t = await run_task("Find the quarterly board deck about zebrafish. Reply with its full path.",
                       "find the quarterly board deck about zebrafish", "I could not find that deck anywhere.")
    r = t.get("result") or ""
    check("D not_found: task done", t["status"] == "done", t["status"])
    check("D not_found: said plainly", "Not found" in r, r)
    check("D not_found: tried routes survive", "filename" in r and "content" in r, r)
    check("D not_found: tried terms survive", "zebrafish" in r, r)

    # E: exact format dictated, no block stapled on
    t = await run_task("Find the tax return summary for 2025, not 2024. Reply with exactly this text and nothing else: FOUND-IT",
                       "find the tax return summary for 2025, not 2024", "FOUND-IT")
    r = (t.get("result") or "").strip()
    check("E exact: delivered answer is exactly the dictated text", r == "FOUND-IT", r)

    # W: the worker lane carries the same contract
    CURRENT["locate_request"] = "find the tax return summary for 2025, not 2024"
    CURRENT["prose"] = DROP_PROSE
    WK_STATE["calls"] = 0
    final = ""
    async for ev in WK.stream("Find the tax return summary for 2025, not 2024 and tell me where it is",
                              [{"role": "user", "content": "find it"}],
                              {"goal": "Find the tax return summary for 2025, not 2024 and tell me where it is",
                               "kind": "computer"}, execute_tool):
        if ev.get("type") == "content":
            final = ev.get("content") or final
    check("W worker: full path in the worker's final answer", str(TAX25) in final, final)
    check("W worker: why evidence in the worker's final answer", "year 2025" in final, final)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILURES:", "; ".join(failed), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
