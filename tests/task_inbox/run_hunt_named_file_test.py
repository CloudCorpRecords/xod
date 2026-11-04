"""
A file named outright by its full path is read, not hunted (services/hunt_routing.py).
Live orchestrator S10 (2026-11-04): "Summarize the file /abs/report-7569.txt" went to locate_files and
answered "two close matches, too close to call" because similarly named files sat in the same folder.

Run: .venv/bin/python tests/task_inbox/run_hunt_named_file_test.py
"""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("XOD_DATA_DIR", tempfile.mkdtemp())
from app.services.hunt_routing import is_find_intent

d = Path(tempfile.mkdtemp())
for n in ("report-7569.txt", "report-7570.txt", "report-8a1c.txt"):
    (d / n).write_text("x")
cases = [
    (f"Summarize the file {d / 'report-7569.txt'} in two sentences.", False),
    (f"YOUR STEP NOW: Summarize the file {d / 'report-7569.txt'}. Related past work: find my reports", False),
    ("find my tax return summary from 2025", True),
    (f"find the report in {d}", True),            # a folder, not a named file: still a hunt
]
ok = 0
for text, want in cases:
    got = is_find_intent(text)
    print(f"  {'PASS' if got == want else 'FAIL'} {want=} {text[:70]}")
    ok += got == want
print(f"\n{ok}/{len(cases)} checks passed")
sys.exit(0 if ok == len(cases) else 1)
