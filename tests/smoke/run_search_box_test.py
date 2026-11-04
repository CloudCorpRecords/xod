"""
A search box is a search box, whatever it holds (services/browser_sandbox.py _looks_like_search_box).

Live 2026-11-04: Google's search box (a textarea labelled "Search", role combobox) already holding the
query "site:clevelandclinic.org adult human body 206 bones" was taken for a form question, and a research
step stopped to ask the person what to type.

Run: .venv/bin/python tests/smoke/run_search_box_test.py
"""
import os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_DATA_DIR", tempfile.mkdtemp(prefix="xod_searchbox_"))
sys.path.insert(0, str(ROOT))
from app.services.browser_sandbox import _looks_like_search_box as S

cases = [
    ("Google's box holding a query", {"tag": "textarea", "role": "combobox", "type": "textarea", "label": "Search",
                                      "text": "site:clevelandclinic.org adult human body 206 bones"}, True),
    ("an empty search box", {"tag": "input", "type": "text", "label": None, "text": "Search"}, True),
    ("role searchbox", {"tag": "input", "role": "searchbox", "type": "text", "text": "rust async"}, True),
    ("type search", {"tag": "input", "type": "search", "text": "(unlabeled)"}, True),
    ("a phone field", {"tag": "input", "type": "tel", "label": "Phone number", "text": "Phone number"}, False),
    ("a name field holding a value", {"tag": "input", "type": "text", "label": "Full name", "text": "Sam Lee"}, False),
    ("an email field", {"tag": "input", "type": "email", "label": "Email", "text": "Email"}, False),
]
bad = [n for n, el, want in cases if S(el) != want]
for n, el, want in cases:
    print(f"  {'PASS' if S(el) == want else 'FAIL'} {n}: {'search box' if want else 'not a search box'}")
print(f"\n{len(cases) - len(bad)}/{len(cases)} checks passed")
sys.exit(1 if bad else 0)
