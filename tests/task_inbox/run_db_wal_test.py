"""
xod.db in write-ahead-log mode (services/task_queue.py _init), so a read never waits for a write.

Live 2026-11-04 (load run, asyncio debug): with tasks and workers writing side by side, event-loop reads of xod.db
(settings, Crew jobs) waited on the lock and the server stalled up to 1.3 s.

  D1 xod.db is in WAL mode after Xod starts (and stays so: it is a property of the file)
  D2 a read is answered at once while another connection holds a write transaction open
  D3 an existing rollback-journal database is switched on start, its data intact

Run: .venv/bin/python tests/task_inbox/run_db_wal_test.py
"""
import os, sqlite3, subprocess, sys, tempfile, time, threading
from pathlib import Path
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_wal_"))
os.environ.update({"XOD_DATA_DIR": str(TMP), "XOD_NO_UPDATE_CHECK": "1"})
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


# D3 first: an older database, rollback journal, with a row in it
db = TMP / "xod.db"
c = sqlite3.connect(db)
c.execute("PRAGMA journal_mode=DELETE")
c.execute("CREATE TABLE IF NOT EXISTS settings_probe (k TEXT PRIMARY KEY, v TEXT)")
c.execute("INSERT INTO settings_probe VALUES ('kept', 'yes')")
c.commit()
c.close()

import app.services.task_queue as TQ
TQ.task_queue.list() if hasattr(TQ.task_queue, "list") else TQ._conn()
mode = sqlite3.connect(db).execute("PRAGMA journal_mode").fetchone()[0]
print("D1/D3")
check("xod.db is in WAL mode after start", mode == "wal", mode)
check("its existing data is intact", sqlite3.connect(db).execute("SELECT v FROM settings_probe WHERE k='kept'").fetchone() == ("yes",))

print("D2 a read during an open write")
w = sqlite3.connect(db, timeout=10, isolation_level=None)
w.execute("BEGIN EXCLUSIVE")      # the commit-time lock that blocks readers in rollback-journal mode
w.execute("INSERT INTO settings_probe VALUES ('writing', 'now')")
t0 = time.time()
try:
    r = sqlite3.connect(db, timeout=1).execute("SELECT v FROM settings_probe WHERE k='kept'").fetchone()
except sqlite3.OperationalError as e:
    r = str(e)
took = time.time() - t0
check("answered at once (not after the writer)", r == ("yes",) and took < 0.2, f"{took:.3f}s")
w.execute("COMMIT")
w.close()
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
