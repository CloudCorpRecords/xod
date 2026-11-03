"""
Hunt routing: find/locate requests must dispatch locate_files deterministically.

End-to-end through the task layer (TaskQueue -> AgenticResearcher -> real
execute_tool -> real locate service) against a planted forest in a temp dir,
using the L-set hunt texts and expected paths from the re-run 2 record
(ESCALATION-T1-RERUN2-2026-10-31.md).

The model is stubbed the way the real one behaved in production across three
measurement runs: given a hunt it plans computer_find_files (whose delivered
answer drops the full path), and for the tax-summary hunt it plans a web
search and answers with web source links without ever searching locally.
The contract under test is routing, not prompt wording:

  * a find intent dispatches locate_files (dispatch is asserted from the
    tool calls that actually ran, not from the answer text);
  * the web research path is never used for a local hunt;
  * the delivered answer carries the decisive-evidence structure (ranked
    candidates with full paths and evidence fields, the ask on close
    scores, the tried list on not-found);
  * a literal placeholder string passed as a tool argument (the L7 shape)
    is rejected at the dispatch layer with a named error and never
    executed as a real path;
  * non-locate file work (read a known file, tidy a folder) is NOT routed
    to locate_files: computer tools stay available for it.

Named cases: L5 (Japan trip folder by contents), L9 (tax summary 2025 by
name plus year: local search, no web sources), L7 shape (placeholder).

No real model, no Keychain, no network:
  PYTHON_KEYRING_BACKEND=tests.support.filekeyring.FileKeyring .venv/bin/python tests/task_inbox/run_hunt_routing_test.py
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
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-routing-data-")
os.environ["XOD_FAST_LANE"] = "0"  # find requests belong to the planner; the fast lane is for lookups
os.environ["XOD_DATASET_DB"] = str(Path(os.environ["XOD_DATA_DIR"]) / "flywheel_test.db")

# ---------------------------------------------------------------- the forest
# Planted BEFORE the app imports, and HOME is pointed at it, so the routing
# layer's home-rooted locate dispatch searches exactly this tree (Spotlight
# does not index temp dirs; the filename and content routes carry the hunts).
FOREST = Path(tempfile.mkdtemp(prefix="xod-routing-forest-"))


def plant(rel, body=b"%PDF fake", when=None):
    p = FOREST / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body if isinstance(body, bytes) else body.encode())
    if when:
        ts = datetime(*when).timestamp()
        os.utime(p, (ts, ts))
    return p


SOURDOUGH = plant("personal/recipes/Sourdough Starter Notes.md",
                  "sourdough starter feeding notes, flour and water")
SOUP = plant("personal/recipes/Grandma Soup Recipe.txt",
             "grandma soup: simmer chicken broth and carrots simmering gently, add noodles")
LEASE25 = plant("house/lease/Apartment Lease Agreement 2025.pdf", when=(2025, 4, 15))
LEASE24 = plant("house/lease/Apartment Lease Agreement 2024.pdf", when=(2024, 4, 10))
W2 = plant("taxes/2025/W2 Wage Statement.pdf")
TRAVEL = FOREST / "personal" / "travel"
plant("personal/travel/itinerary.txt", "Japan trip: Tokyo, Kyoto, bullet train, ramen in Osaka")
plant("personal/travel/hotel booking japan.pdf")
plant("personal/travel/photos.txt", "japan trip photos to sort when home")
plant("Japan packing list.txt", "japan packing: socks, charger, passport")
SIGNED = plant("work/contracts/Consulting Contract Signed.pdf")
DRAFT = plant("work/contracts/Consulting Contract Draft.docx", b"fake docx")
INVOICE = plant("work/invoices/Invoice 1043.pdf")
plant("work/invoices/Invoice 1042.pdf")
REUNION24 = plant("photos-archive/Family Reunion 2024.png", b"\x89PNG", (2024, 7, 4))
plant("photos-archive/Family Reunion 2023.png", b"\x89PNG", (2023, 7, 4))
TAX25 = plant("taxes/2025/Tax Return Summary 2025.pdf", when=(2025, 3, 10))
TAX24 = plant("taxes/2024/Tax Return Summary 2024.pdf", when=(2024, 3, 10))
BUDGET = plant("work/Project Budget.xlsx", b"fake sheet")
NOTES = plant("notes/meeting.txt", "standup notes, roadmap chat")

os.environ["HOME"] = str(FOREST)

import app.services.task_queue as TQ  # noqa: E402
import app.services.browser_sandbox as BS  # noqa: E402
import app.agent.researcher as RS  # noqa: E402
from app.services import llm as llm_mod  # noqa: E402
from app.agent.tools import execute_tool as real_execute_tool  # noqa: E402

# The routing seam, when the routing layer exists: point its locate root at
# the forest. On unmodified main there is no such module and nothing to
# point, which is exactly the failure this suite measures.
try:
    from app.services import hunt_routing as HR
    HR.locate_root = lambda: str(FOREST)
except Exception:
    HR = None

TMP = Path(tempfile.mkdtemp(prefix="xod-routing-tasks-"))
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


# ------------------------------------------------------- tool dispatch spy
# Every tool execution in every lane funnels through tools.execute_tool;
# researcher and task_queue hold their own references to it, so patch all.
import app.agent.tools as TOOLS_MOD  # noqa: E402

CALLS = []          # (name, args) for every dispatched tool call
CURRENT = {"plan_tools": [], "plan_args": [], "prose": ""}

_orig_execute_tool = TOOLS_MOD.execute_tool


async def spy_execute_tool(name, arguments, active_workspace_path=None, reviewed=False):
    CALLS.append((name, dict(arguments or {})))
    return await _orig_execute_tool(name, arguments,
                                    active_workspace_path=active_workspace_path,
                                    reviewed=reviewed)


TOOLS_MOD.execute_tool = spy_execute_tool
RS.execute_tool = spy_execute_tool


def tool_names():
    return [n for n, _ in CALLS]


# ---------------------------------------------------------------- stub model
# The planner is scripted the way production behaved: hunts are planned as
# computer_find_files steps (or, for the tax hunt, a web search), and the
# synthesis prose drops the full path / appends web source links.
async def _stub_plan(self, query=None, conversation_history=None, **kw):
    steps = []
    for i, (tool, args) in enumerate(zip(CURRENT["plan_tools"], CURRENT["plan_args"]), 1):
        steps.append({"id": i, "title": f"Step {i}", "tools": [tool],
                      "tool_args": [args], "status": "pending"})
    yield {
        "type": "deliberation_complete",
        "deliberation": {
            "thought": "scripted production-shaped plan",
            "decision": "execute_plan",
            "direct_response": "",
            "plan_title": "Do it",
            "plan_steps": steps,
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

DROP_PROSE = "Found it: the file you asked for. Its full path was not provided by the search."
WEB_PROSE = ("I could not find a full path for your 2025 tax return summary in the provided "
             "information.\n\n**Source links**\n"
             "1. [IRS refunds](https://www.irs.gov/refunds)\n"
             "2. [TurboTax](https://turbotax.intuit.com/)")


async def run_task(task_text, plan_tools, plan_args, prose):
    CALLS.clear()
    CURRENT["plan_tools"] = plan_tools
    CURRENT["plan_args"] = plan_args
    CURRENT["prose"] = prose
    q = TQ.TaskQueue()
    tid = q.create(task_text)["id"]
    await q._run(tid)
    return q.get(tid), list(CALLS)


async def main():
    # ---- L9 (named): tax summary by name plus year. Production answered
    # from the web path with source links and never ran a local search.
    t, calls = await run_task(
        "Find the tax return summary for 2025, not 2024. Reply with its full path.",
        ["web_search"], [{"query": "tax return summary 2025"}], WEB_PROSE)
    r = t.get("result") or ""
    names = [n for n, _ in calls]
    check("L9 dispatch: locate_files was called", "locate_files" in names, str(names))
    check("L9 dispatch: web path not used for a local hunt",
          not any(n in ("web_search", "tavily_search", "you_search") for n in names), str(names))
    check("L9 dispatch: computer_find_files not used", "computer_find_files" not in names, str(names))
    check("L9 delivered: task done", t["status"] == "done", t["status"])
    check("L9 delivered: full path of the 2025 file", str(TAX25) in r, r)
    check("L9 delivered: the 2024 decoy path is not delivered", str(TAX24) not in r, r)
    check("L9 delivered: evidence fields present", "2025-03-10" in r and "year 2025" in r, r)
    check("L9 delivered: no web sources appended", "irs.gov" not in r and "Source links" not in r, r)
    ev = t.get("evidence_json")
    payloads = json.loads(ev) if isinstance(ev, str) else (ev or [])
    check("L9 delivered: decisive payload stored on the task",
          bool(payloads) and payloads[-1]["payload"]["candidates"][0]["path"] == str(TAX25), str(ev)[:200])

    # ---- L5 (named): Japan trip folder, found by its contents. Production
    # missed this folder three runs straight.
    t, calls = await run_task(
        "Find the folder that has my Japan trip stuff. Reply with its full path.",
        ["computer_find_files"], [{"query": "Japan"}], DROP_PROSE)
    r = t.get("result") or ""
    names = [n for n, _ in calls]
    check("L5 dispatch: locate_files was called", "locate_files" in names, str(names))
    check("L5 dispatch: computer_find_files not used", "computer_find_files" not in names, str(names))
    check("L5 delivered: the travel folder path", str(TRAVEL) in r, r)
    check("L5 delivered: folder evidence present", "holds" in r or "folder" in r.lower(), r)

    # ---- L1: filename fragment
    t, calls = await run_task(
        "Find the file with Sourdough in its name. Reply with its full path.",
        ["computer_find_files"], [{"query": "Sourdough"}], DROP_PROSE)
    r = t.get("result") or ""
    check("L1 dispatch: locate_files was called", "locate_files" in tool_names_from(calls), str(calls))
    check("L1 delivered: full path", str(SOURDOUGH) in r, r)

    # ---- L3: content words
    t, calls = await run_task(
        "Find the file that mentions chicken broth and carrots simmering. Reply with its full path.",
        ["computer_find_files"], [{"query": "chicken broth"}], DROP_PROSE)
    r = t.get("result") or ""
    check("L3 dispatch: locate_files was called", "locate_files" in tool_names_from(calls), str(calls))
    check("L3 delivered: full path of the soup recipe", str(SOUP) in r, r)

    # ---- L4: purpose (W2 wage statement)
    t, calls = await run_task(
        "Find the file for the tax thing, the wage statement from my employer, somewhere on this computer. Reply with its full path.",
        ["computer_find_files"], [{"query": "W2"}], DROP_PROSE)
    r = t.get("result") or ""
    check("L4 dispatch: locate_files was called", "locate_files" in tool_names_from(calls), str(calls))
    check("L4 delivered: full path of the W2", str(W2) in r, r)

    # ---- L6: signed contract, not the draft
    t, calls = await run_task(
        "Find my signed consulting contract, not the draft. Reply with its full path.",
        ["computer_find_files"], [{"query": "Consulting Contract"}], DROP_PROSE)
    r = t.get("result") or ""
    check("L6 dispatch: locate_files was called", "locate_files" in tool_names_from(calls), str(calls))
    # Delivered structure per the round 2 contract: either the signed file
    # ranked first (found), or the ask on close scores carrying both full
    # paths and the question (the service scores this pair within its ask
    # margin: "draft" is a name clue, not an exclusion; only years are).
    check("L6 delivered: signed contract path delivered", str(SIGNED) in r, r)
    check("L6 delivered: found-with-signed-first or ask-with-both",
          (t["status"] == "done" and str(SIGNED) in r
           and (str(DRAFT) not in r or r.index(str(SIGNED)) < r.index(str(DRAFT))))
          or (t["status"] == "waiting" and str(SIGNED) in r and str(DRAFT) in r
              and "Which one did you mean?" in r),
          f"{t['status']} :: {r}")

    # ---- L7: invoice number
    t, calls = await run_task(
        "Find invoice 1043. Reply with its full path.",
        ["computer_find_files"], [{"query": "Invoice 1043"}], DROP_PROSE)
    r = t.get("result") or ""
    check("L7 dispatch: locate_files was called", "locate_files" in tool_names_from(calls), str(calls))
    check("L7 delivered: full path of invoice 1043", str(INVOICE) in r, r)

    # ---- L8: photo plus year, ranking preserved
    t, calls = await run_task(
        "Find the family reunion photo from 2024. Reply with its full path.",
        ["computer_find_files"], [{"query": "Family Reunion"}], DROP_PROSE)
    r = t.get("result") or ""
    check("L8 dispatch: locate_files was called", "locate_files" in tool_names_from(calls), str(calls))
    check("L8 delivered: 2024 photo path present", str(REUNION24) in r, r)

    # ---- L2: kind plus rough date (both lease years planted; the expected
    # path must be among the delivered candidates, found or ask)
    t, calls = await run_task(
        "Find the PDF from last spring about my apartment lease. Reply with its full path.",
        ["computer_find_files"], [{"query": "lease"}], DROP_PROSE)
    r = t.get("result") or ""
    check("L2 dispatch: locate_files was called", "locate_files" in tool_names_from(calls), str(calls))
    check("L2 delivered: the 2025 lease path is delivered", str(LEASE25) in r, r)

    # ---- L10: kind plus purpose (work budget spreadsheet)
    t, calls = await run_task(
        "Find the budget spreadsheet for work. Reply with its full path.",
        ["computer_find_files"], [{"query": "budget"}], DROP_PROSE)
    r = t.get("result") or ""
    check("L10 dispatch: locate_files was called", "locate_files" in tool_names_from(calls), str(calls))
    check("L10 delivered: full path of the budget sheet", str(BUDGET) in r, r)

    # ---- N1: not found, tried list delivered, nothing invented
    t, calls = await run_task(
        "Find the quarterly board deck about zebrafish. Reply with its full path.",
        ["computer_find_files"], [{"query": "zebrafish"}], "I could not find that deck anywhere.")
    r = t.get("result") or ""
    check("N1 dispatch: locate_files was called", "locate_files" in tool_names_from(calls), str(calls))
    check("N1 delivered: task done", t["status"] == "done", t["status"])
    check("N1 delivered: said plainly not found", "Not found" in r, r)
    check("N1 delivered: tried routes survive", "filename" in r and "content" in r, r)
    check("N1 delivered: tried terms survive", "zebrafish" in r, r)

    # ---- L7 shape (named): a literal placeholder string as a tool argument
    # is a hard failure at the dispatch layer, never executed as a real path.
    res = await real_execute_tool("computer_read_file",
                                  {"filepath": "<matching file path from search results>"})
    check("placeholder: rejected with the named error",
          isinstance(res, dict) and res.get("success") is False
          and "PlaceholderArgumentError" in str(res.get("error") or ""), str(res)[:240])
    check("placeholder: error code is placeholder_argument",
          isinstance(res, dict) and res.get("error_code") == "placeholder_argument", str(res)[:240])
    res = await real_execute_tool("computer_read_file", {"filepath": str(NOTES)})
    check("placeholder: a real path still reads fine",
          isinstance(res, dict) and res.get("success") is True, str(res)[:240])

    # ---- Non-locate file work is NOT routed to locate_files: reading a
    # file the person named outright stays a plain tool task.
    t, calls = await run_task(
        f"Read the file {NOTES} and tell me what it says.",
        ["computer_read_file"], [{"filepath": str(NOTES)}],
        "It says: standup notes, roadmap chat.")
    names = tool_names_from(calls)
    check("non-locate: locate_files not dispatched for a named-file read",
          "locate_files" not in names, str(names))
    check("non-locate: the read tool still ran", "computer_read_file" in names, str(names))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILURES:", "; ".join(failed), flush=True)
        sys.exit(1)


def tool_names_from(calls):
    return [n for n, _ in calls]


if __name__ == "__main__":
    asyncio.run(main())
