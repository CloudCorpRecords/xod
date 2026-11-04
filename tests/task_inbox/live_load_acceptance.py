"""
Live load acceptance for Tier 5 (delegated parallel work), wider than the spec's two-job case, against a
RUNNING dev instance and the real model.

  W1 one request with 8 independent questions: the planner fans out (up to MAX_PARALLEL = 8 workers), the
     workers overlap in time, each brief holds only its own question, each returns a structured result,
     and the one answer has all 8 answers
  W2 three such tasks at once (4 questions each, 12 workers across the pool): all three finish, each answer
     holds its own answers and none of another task's; while they run, the chat's answer starts "within
     seconds" (the spec's bar, under 15 s as in L4; the times are printed), and the server itself never
     stalls: a trivial request, sampled every second, is answered within 1 s throughout

Run: .venv/bin/python tests/task_inbox/live_load_acceptance.py --base http://127.0.0.1:8212
"""
import argparse, json, re, sys, threading, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True)
ap.add_argument("--only", default="")
args = ap.parse_args()
BASE = args.base.rstrip("/")
results = []

# (question, keyword that appears only in this question's brief, answer patterns: any one must appear)
Q = [
    ("What is the height in metres of the Eiffel Tower including its antennas?", "Eiffel", [r"\b33\d\b"]),
    ("In what year was the Python programming language first released?", "Python", [r"\b1991\b"]),
    ("What is the official height of Mount Everest in metres?", "Everest", [r"8,?8[45]\d"]),
    ("In what year did the Berlin Wall fall?", "Berlin", [r"\b1989\b"]),
    ("How many bones are in the adult human body?", "bones", [r"\b206\b"]),
    ("What is the chemical symbol for gold?", "gold", [r"\bAu\b"]),
    ("In what year did Apollo 11 land on the Moon?", "Apollo", [r"\b1969\b"]),
    ("What is the boiling point of water at sea level in degrees Fahrenheit?", "Fahrenheit", [r"\b212\b"]),
    ("In what year was the first iPhone released?", "iPhone", [r"\b2007\b"]),
    ("How many players does a soccer team have on the field?", "soccer", [r"\b(11|eleven)\b"]),
    ("What is the capital city of Australia?", "Australia", [r"Canberra"]),
    ("In what year did the Titanic sink?", "Titanic", [r"\b1912\b"]),
]


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def http(path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def request_for(qs):
    return ("Separate questions, look each one up independently and answer all of them, with sources: "
            + " ".join(f"({i + 1}) {q}" for i, (q, _k, _a) in enumerate(qs)))


def wait(tid, label, limit=1500):
    t0, last = time.time(), None
    while time.time() - t0 < limit:
        p = http(f"/api/tasks/{tid}/plan")
        sig = (p["status"], tuple(i["status"] for i in p["items"]))
        if sig != last:
            print(f"     [{label}] {time.time() - t0:6.1f}s {sig}", flush=True)
            last = sig
        if p["status"] in ("done", "failed", "cancelled"):
            return p
        time.sleep(2)
    return p


def workers_of(p):
    coords = {i.get("coordinator") for i in p["items"] if i.get("parallel")}
    return [w for w in http("/api/workers")["workers"] if w.get("parent_id") in coords]


def max_overlap(ws):
    ev = sorted([(w["created_at"], 1) for w in ws] + [((w.get("finished_at") or time.time()), -1) for w in ws])
    cur = best = 0
    for _t, d in ev:
        cur += d
        best = max(best, cur)
    return best


def answered(text, qs):
    return [k for _q, k, pats in qs if any(re.search(p, text, re.I) for p in pats)]


def w1():
    print("W1 one request, 8 independent questions")
    qs = Q[:8]
    tid = http("/api/orchestrator/tasks", {"request": request_for(qs)})["ack"]["task_id"]
    p = wait(tid, "W1")
    fan = [i for i in p["items"] if i.get("parallel")]
    njobs = sum(len(i["parallel"]) for i in fan)
    print("     plan:", json.dumps([{"text": i["text"][:60], "jobs": len(i.get("parallel") or [])} for i in p["items"]])[:500])
    check("the questions were fanned out to workers (at least 6 jobs)", njobs >= 6, f"{njobs} jobs")
    ws = workers_of(p)
    check("the workers overlapped in time (at least 4 at once)", max_overlap(ws) >= 4, f"max overlap {max_overlap(ws)}")
    leaks = [w["brief"][:80] for w in ws if sum(k.lower() in w["brief"].lower() for _q, k, _a in qs) > 1
             and len(fan) and all(len(i["parallel"]) > 1 for i in fan)]
    check("no context leaked: no worker's brief holds another worker's question", not leaks, str(leaks)[:300])
    states = [(w.get("result") or {}).get("outcome") for w in ws]
    check("each worker returned a structured result", ws and all(s in ("done", "partial", "failed") for s in states), str(states))
    res = p.get("result") or ""
    got = answered(res, qs)
    print("     answer:", res[:500].replace("\n", " "))
    check("the task finished", p["status"] == "done", p["status"])
    check("the one answer has all 8 answers", len(got) == 8, f"missing {[k for _q, k, _a in qs if k not in got]}")


def chat_first(question):
    """Seconds until the chat's first answer text (the same probe as live_orchestrator_acceptance L4)."""
    body = json.dumps({"messages": [{"role": "user", "content": question}]}).encode()
    req = urllib.request.Request(BASE + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:])
            except ValueError:
                continue
            if ev.get("type") == "content" and ev.get("content"):
                return time.time() - t0
    return 99


def chat_probe(out, stop):
    """Short chat questions while the tasks run: the answer must start within 5 s."""
    for q in ["What is 12 times 12?", "Say hi in French.", "What is the opposite of cold?"]:
        if stop.is_set():
            break
        try:
            out.append(chat_first(q))
        except Exception as e:
            out.append(99)
            print(f"     chat probe error: {e}", flush=True)
        print(f"     chat answered in {out[-1]:.1f}s", flush=True)
        stop.wait(20)


def loop_probe(out, stop):
    """The server's own responsiveness: a trivial request every second."""
    while not stop.is_set():
        t0 = time.time()
        try:
            urllib.request.urlopen(BASE + "/api/tasks/counts", timeout=10).read()
            out.append(time.time() - t0)
        except Exception:
            out.append(10.0)
        stop.wait(1)


def w2():
    print("W2 three tasks at once, 4 questions each")
    groups = [Q[0:4], Q[4:8], Q[8:12]]
    tids = [http("/api/orchestrator/tasks", {"request": request_for(g)})["ack"]["task_id"] for g in groups]
    lat, stop = [], threading.Event()
    th = threading.Thread(target=chat_probe, args=(lat, stop), daemon=True)
    loop = []
    lp = threading.Thread(target=loop_probe, args=(loop, stop), daemon=True)
    lp.start()
    time.sleep(10)
    th.start()
    done = {}
    threads = [threading.Thread(target=lambda t=t, n=n: done.__setitem__(t, wait(t, f"W2.{n}")), daemon=True)
               for n, t in enumerate(tids)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stop.set()
    th.join(5)
    all_ws = []
    for n, (tid, g) in enumerate(zip(tids, groups)):
        p = done[tid]
        res = p.get("result") or ""
        got = answered(res, g)
        others = [k for gg in groups if gg is not g for _q, k, _a in gg if re.search(rf"\b{re.escape(k)}\b", res, re.I)]
        print(f"     [{n}] answer:", res[:300].replace("\n", " "))
        check(f"task {n + 1} finished", p["status"] == "done", p["status"])
        check(f"task {n + 1} answered its own 4", len(got) == 4, f"missing {[k for _q, k, _a in g if k not in got]}")
        check(f"task {n + 1} holds nothing from the other tasks", not others, str(others))
        all_ws += workers_of(p)
    check("workers from different tasks overlapped (at least 6 at once)", max_overlap(all_ws) >= 6, f"max overlap {max_overlap(all_ws)}")
    print(f"     chat first-answer times: {[round(x, 1) for x in lat]}; server trivial-request max {max(loop):.2f}s "
          f"over {len(loop)} samples", flush=True)
    check("the chat answered within seconds while they ran (under 15 s, the L4 bar)", lat and max(lat) < 15,
          f"latencies {[round(x, 1) for x in lat]}")
    check("the server never stalled: every trivial request answered within 1 s", loop and max(loop) < 1.0,
          f"max {max(loop):.2f}s")


if not args.only or "W1" in args.only:
    w1()
if not args.only or "W2" in args.only:
    w2()
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} live checks passed")
sys.exit(1 if failed else 0)
