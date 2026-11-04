"""
Live acceptance for orchestrator Phase 2, against a RUNNING dev instance and the real model.

  P1 two unrelated research jobs at once: the planner fans them out, two workers run
     concurrently in their own contexts, each returns a structured result, and the task's one
     answer draws on both (spec section 4)

Run: .venv/bin/python tests/task_inbox/live_workers_acceptance.py --base http://127.0.0.1:8212
"""
import argparse, json, sys, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True)
args = ap.parse_args()
BASE = args.base.rstrip("/")
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def http(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


print("P1 two unrelated research jobs at once")
req = ("Two separate questions, look both up: (1) What is the height in metres of the Eiffel Tower including antennas? "
       "(2) In what year was the Python programming language first released? Give me both answers with sources.")
tid = http("/api/orchestrator/tasks", {"request": req})["ack"]["task_id"]
t0 = time.time()
last = None
while time.time() - t0 < 900:
    p = http(f"/api/tasks/{tid}/plan")
    sig = (p["status"], tuple(i["status"] for i in p["items"]), tuple(w["shown_state"] for w in p.get("workers", [])))
    if sig != last:
        print(f"     {time.time() - t0:6.1f}s {sig}", flush=True)
        last = sig
    if p["status"] in ("done", "failed", "cancelled"):
        break
    time.sleep(1.5)
items = p["items"]
fan = [i for i in items if i.get("parallel")]
print("     plan:", json.dumps([{"text": i["text"][:70], "parallel": i.get("parallel")} for i in items])[:600])
check("the planner fanned the two jobs out in one parallel step", len(fan) == 1 and len(fan[0]["parallel"]) == 2)
ws = [w for w in http("/api/workers")["workers"] if fan and w.get("parent_id") == (fan[0].get("coordinator") or "")]
check("two workers ran under one coordinator", len(ws) == 2, str([w["id"] for w in ws]))
if len(ws) == 2:
    a, b = ws
    overlap = a["created_at"] < (b["finished_at"] or 1e18) and b["created_at"] < (a["finished_at"] or 1e18)
    check("they ran concurrently", overlap, f"a {a['created_at']:.0f}-{a['finished_at']:.0f} b {b['created_at']:.0f}-{b['finished_at']:.0f}")
    check("each returned a structured result", all((w.get("result") or {}).get("outcome") == "done" and (w.get("result") or {}).get("report")
                                                    for w in ws), str([(w.get("result") or {}).get("outcome") for w in ws]))
    eiffel = [w for w in ws if "Eiffel" in w["brief"]]
    python = [w for w in ws if "Python" in w["brief"]]
    check("no context leaked: each brief holds only its own job",
          len(eiffel) == 1 and len(python) == 1 and "Python" not in eiffel[0]["brief"] and "Eiffel" not in python[0]["brief"])
res = (p.get("result") or "")
print("     answer:", res[:400].replace("\n", " "))
check("the task finished", p["status"] == "done", p["status"])
check("the one answer draws on both", ("330" in res or "324" in res) and "1991" in res)
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} live checks passed")
sys.exit(1 if failed else 0)
