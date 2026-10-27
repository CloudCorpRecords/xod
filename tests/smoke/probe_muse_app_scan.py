"""
Manual probe (not a CI suite): time one READ-ONLY scan of the real Muse app with the in-process
AX walk and confirm it finishes fast — the osascript scan took ~25s and was killed at 15s.
Never presses anything (_scan(False)).
  .venv/bin/python tests/smoke/probe_muse_app_scan.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_DATA_DIR", str(Path(tempfile.mkdtemp(prefix="xod-scanprobe-")) / "data"))
sys.path.insert(0, str(ROOT))

from app.services import muse_app  # noqa: E402

t = time.time()
res = muse_app._scan(False)
elapsed = time.time() - t
print(f"read-only scan: {elapsed:.2f}s -> {res}")
assert elapsed < 5, f"scan too slow: {elapsed:.2f}s"
print("ok: scan finished well under the old 15s timeout")
