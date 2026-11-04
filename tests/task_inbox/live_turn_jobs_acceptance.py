"""
Live: a chat turn answers from its background job's observed end, not from an early check
(agent/turn_jobs.py), against a RUNNING dev instance and the real model.

  C1 asked to run a ~40 s command in the background and report what it prints, the chat answers with the
     printed text, which exists only once the job has finished
  C2 asked to start a local web server in the background, the chat does not wait for it to finish (it never
     will) and says it is running

Run: .venv/bin/python tests/task_inbox/live_turn_jobs_acceptance.py --base http://127.0.0.1:8212
"""
import argparse, json, re, socket, sys, time, urllib.request, uuid

ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True)
args = ap.parse_args()
BASE = args.base.rstrip("/")
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def http(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method="POST" if data else "GET", headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def chat(question):
    body = json.dumps({"messages": [{"role": "user", "content": question}]}).encode()
    req = urllib.request.Request(BASE + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    t0, text, statuses = time.time(), [], []
    with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:])
            except ValueError:
                continue
            if ev.get("type") == "content" and ev.get("content"):
                text.append(str(ev["content"]))
            elif ev.get("type") == "status":
                statuses.append(str(ev.get("content")))
    return time.time() - t0, "".join(text), statuses


def jobs():
    r = http("/api/computer/jobs")
    return r.get("jobs", r) if isinstance(r, dict) else r


print("C1 a background job's printed result")
tag = uuid.uuid4().hex[:6]
before = {j["id"] for j in jobs()}
secs, text, st = chat(f"Start this as a background job (computer_start_job), then tell me exactly what it printed once it "
                      f"finished: sleep 40; echo finished-{tag}")
new = [j for j in jobs() if j["id"] not in before]
print(f"     {secs:.0f}s; jobs {[(j['name'], j['status']) for j in new]}; answer: {text[:300]!r}")
check("it ran as a background job", any(f"finished-{tag}" in (j.get("command") or "") for j in new), str(new)[:300])
check("the answer has what it printed at the end", f"finished-{tag}" in text, text[:300])
check("it said it was waiting for the job", any("Waiting for" in s or "Still waiting" in s for s in st), str(st[-6:]))
check("it does not say the job is still running", not re.search(r"still running|hasn.t finished|not finished yet", text, re.I), text[:300])

print("C2 a service is not waited for")
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
before = {j["id"] for j in jobs()}
secs, text, st = chat(f"Start a local web server in the background with computer_start_job: python3 -m http.server {port} "
                      f"--bind 127.0.0.1 . Tell me when it is up.")
new = [j for j in jobs() if j["id"] not in before]
print(f"     {secs:.0f}s; jobs {[(j['name'], j['status']) for j in new]}; answer: {text[:300]!r}")
check("the server is running", any(j["status"] == "running" and str(port) in (j.get("command") or "") for j in new), str(new)[:300])
check("the answer came without waiting out the bound (< 100 s)", secs < 100, f"{secs:.0f}s")
check("the answer says it is up or running", re.search(r"\b(up|running|serving|listening|started)\b", text, re.I), text[:300])
for j in new:
    try:
        urllib.request.urlopen(urllib.request.Request(BASE + f"/api/computer/jobs/{j['id']}/stop", method="POST", data=b"{}",
                                                      headers={"Content-Type": "application/json"}), timeout=10)
    except Exception:
        pass
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} live checks passed")
sys.exit(1 if failed else 0)
