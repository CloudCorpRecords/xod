"""
Stable task identity for a later Xod-Tree task sync (orchestrator spec, update note 2026-11-04, hook 3):
a task can carry another agent's id for the same work (external_ref), and be found by it.

  I1 an orchestrated task keeps the external_ref it was submitted with; the API takes it too
  I2 by_external_ref finds that task; an unknown ref finds nothing
  I3 the task.created event carries it, so a replay can match without scraping
  I4 a database from before the column gets it on upgrade, its old tasks intact

Run: .venv/bin/python tests/task_inbox/run_task_identity_test.py
"""
import json, os, sqlite3, sys, tempfile
from pathlib import Path
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_identity_"))
os.environ.update({"XOD_DATA_DIR": str(TMP), "XOD_NO_UPDATE_CHECK": "1"})
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


import app.services.task_queue as TQ
from app.services import orchestrator as O
TQ.notify = lambda *a, **k: None
q = TQ.task_queue
print("I4 upgrade")
oldid = q.create("an older task")["id"]
db = TMP / "xod.db"
c = sqlite3.connect(db)
c.execute("DROP INDEX IF EXISTS tasks_external_ref")
c.execute("ALTER TABLE tasks DROP COLUMN external_ref")     # the database as it was before this change
c.commit()
c.close()
import subprocess   # the upgrade runs when Xod starts: a fresh process, as after an update
out = subprocess.run([sys.executable, "-c", "import json,sys; sys.path.insert(0, %r); import app.services.task_queue as TQ; "
                      "t = TQ.task_queue.get(%r); print(json.dumps({k: t.get(k) for k in ('request', 'external_ref')} | "
                      "{'has': 'external_ref' in t}))" % (str(ROOT), oldid)], capture_output=True, text=True, env=dict(os.environ))
old = json.loads(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() else {}
check("a database from before gets the column on start, its tasks intact", old.get("request") == "an older task" and old.get("has")
      and old.get("external_ref") is None, out.stdout[-200:] + out.stderr[-300:])

print("I1-I3 external_ref")
ack = O.submit("Summarize the weekly notes", external_ref="tree:task-8812")
t = q.get(ack["task_id"])
check("the task keeps the external_ref it was submitted with", t["external_ref"] == "tree:task-8812")
check("by_external_ref finds it", (q.by_external_ref("tree:task-8812") or {}).get("id") == t["id"])
check("an unknown ref finds nothing", q.by_external_ref("tree:nope") is None)
plain = q.get(O.submit("Another task")["task_id"])
check("a task without one has none", plain["external_ref"] is None)
ev = [json.loads(l) for l in (TMP / "event_log.jsonl").read_text().splitlines() if '"task.created"' in l]
check("task.created carries it", any(e["ids"].get("external_ref") == "tree:task-8812" and e["ids"]["task_id"] == t["id"] for e in ev), str(ev[-2:])[:300])
from fastapi.testclient import TestClient
from app.main import app
r = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)).post("/api/orchestrator/tasks", json={"request": "Check the build", "external_ref": "tree:task-9001"}).json()
check("the API takes it", q.get(r["ack"]["task_id"])["external_ref"] == "tree:task-9001", str(r)[:200])
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
