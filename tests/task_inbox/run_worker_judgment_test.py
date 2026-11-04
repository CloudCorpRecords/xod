"""
How a coordinator judges a finished worker's report (services/workers.py judge_report).

Live 2026-11-04 (final count, pass g1, P1): both research workers answered with a linked source ("Confirmed:
Python was first released in 1991, according to [Codecademy](...)") and added "Not finished yet: read a
second source". The note was read as an admission, both workers failed, and the task failed with both answers
in hand.

  J1 confirmed with a linked source, plus a note of more it could check: done, the note kept as a caveat
  J2 "Confirmed: Nothing" with a not-finished note: failed
  J3 a plain admission ("Could not read: ... not found"): failed
  J4 confirmed with a source, nothing else: done, no caveat
  J5 confirmed without any source, plus an admission: failed (a claim with no source is not an answer)

Run: .venv/bin/python tests/task_inbox/run_worker_judgment_test.py
"""
import os, sys, tempfile
from pathlib import Path
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
ROOT = Path(__file__).resolve().parents[2]
os.environ.update({"XOD_DATA_DIR": tempfile.mkdtemp(prefix="xod_judge_"), "XOD_NO_UPDATE_CHECK": "1"})
sys.path.insert(0, str(ROOT))
from app.services.workers import judge_report

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


P1 = ("**Confirmed:** Python was first released in **1991**, according to [Codecademy](https://www.codecademy.com/resources/"
      "blog/history-of-python-coding-language).  **Inferred:** Nothing. **Not found or unreadable:** Nothing relevant.  "
      "**Not finished yet:** Read a source whose scrape output explicitly states the year Python was first released, then "
      "cite it in the answer.\n\n<!-- xod-work:{\"goal\": \"...\"} -->")
a, c = judge_report("Look up the year Python was first released, with a source.", P1)
check("J1 confirmed with a linked source plus a note: done, note kept", a is None and c and "Not finished yet" in c, f"{a} {c}")
a, c = judge_report("Look up X", "**Confirmed:** Nothing. **Not finished yet:** I could not reach any source [x](https://a.b).")
check("J2 confirmed nothing: failed", a and c is None, f"{a} {c}")
a, c = judge_report("Read check5.txt and report its status line", "Could not read: the status line, because the file was not found.")
check("J3 a plain admission: failed", a and c is None, f"{a} {c}")
a, c = judge_report("Look up the tower's height", "**Confirmed:** 330 m including antennas ([Xinhua](https://english.news.cn/x)).")
check("J4 confirmed with a source: done, no caveat", a is None and c is None, f"{a} {c}")
a, c = judge_report("Look up the tower's height", "**Confirmed:** about 330 m.  **Not finished yet:** I could not open a source.")
check("J5 confirmed without a source, plus an admission: failed", a and c is None, f"{a} {c}")
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
