"""
What a worker's done-check sees of the pages it read (agent/worker.py done_check, _steps_text).

Live 2026-11-04 (final count, pass h3, W2): four research workers read pages that held their answers (Cleveland
Clinic: "Adults have between 206 and 213 bones"), but the checker was shown each page's last 500 characters, the
footer, and sent every worker back with "the scrape output does not establish it" until the task failed.

  V1 the checker sees the page's opening, where the answer usually is, even on a long page
  V2 it sees a sentence deep in the page that bears on the goal
  V3 a command's output is still shown by its end (where errors and results are)
  V4 done_check's prompt carries the excerpt (through the real function, with the model stubbed)

Run: .venv/bin/python tests/task_inbox/run_done_check_view_test.py
"""
import asyncio, json, os, sys, tempfile
from pathlib import Path
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
ROOT = Path(__file__).resolve().parents[2]
os.environ.update({"XOD_DATA_DIR": tempfile.mkdtemp(prefix="xod_dcview_"), "XOD_NO_UPDATE_CHECK": "1"})
sys.path.insert(0, str(ROOT))
from app.agent import worker as WK

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


FOOTER = "\n".join(f"Advertisement. Cookie settings. Privacy policy link {i}." for i in range(120))
TOP = "## Bones\nAdults have between 206 and 213 bones. You use all of them each day to sit, stand and move.\n"
DEEP = TOP.replace("206", "000") + "Intro text about joints.\n" * 40 + "For adults, there are 206 bones in the skeleton.\n" + FOOTER
GOAL = "Look up how many bones are in the adult human body and find a source for the answer."


def entry(name, res):
    out = WK._out(res)
    page = WK._is_page(res)
    return {"name": name, "args": {"url": res.get("url", "x")}, "fp": "", "failed": WK._failed(res),
            "out": out[:8000] if page else out[-3000:], "page": page, "exit": res.get("exit_code")}


log1 = [entry("web_scrape", {"url": "https://my.clevelandclinic.org/bones", "title": "Bones", "content": TOP + FOOTER})]
txt = WK._steps_text(log1, goal=GOAL)
check("V1 the page's opening is shown on a long page", "between 206 and 213 bones" in txt, txt[:300])
log2 = [entry("web_scrape", {"url": "https://openstax.org/x", "title": "Skeleton", "content": DEEP})]
txt = WK._steps_text(log2, goal=GOAL)
check("V2 a sentence deep in the page about the goal is shown", "there are 206 bones in the skeleton" in txt, txt[:600])
log3 = [entry("computer_run_command", {"stdout": "line\n" * 900 + "Python 3.12.1", "exit_code": 0})]
txt = WK._steps_text(log3, goal="check python works")
check("V3 a command's output is shown by its end", "Python 3.12.1" in txt, txt[-200:])

seen = {}


async def fake_chat(messages=None, **k):
    seen["prompt"] = messages[0]["content"]
    return {"content": json.dumps({"done": True, "proof": "206 bones", "missing": "", "needs_person": False})}


WK.llm_service.chat_completion = fake_chat
v = asyncio.run(WK.done_check(GOAL, log1, "Adults have 206 bones [Cleveland Clinic](https://my.clevelandclinic.org/bones).", "web"))
check("V4 done_check's prompt carries the answer from the page", "between 206 and 213 bones" in seen.get("prompt", "") and v["done"],
      seen.get("prompt", "")[:300])

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:", *failed, sep="\n  ")
    sys.exit(1)
