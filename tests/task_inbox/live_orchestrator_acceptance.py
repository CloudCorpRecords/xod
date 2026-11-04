"""
Live acceptance for orchestrator Phase 1, against a RUNNING dev instance and the real model.

Never point this at the installed app: it creates tasks, writes files under a scratch folder,
and (for the restart test) kills and restarts the server process it is given.

  L1 ack       : submit returns an ack with a task id before any step has run (spec section 2)
  L2 interrupt : change the target halfway; completed steps survive, finish on the new target,
                 no restart, no duplicated side effects (spec section 6, the user's own move)
  L3 restart   : kill -9 the backend mid-task, restart it; the plan shows the in-flight step and
                 what remains, the task resumes, no completed step's effect repeats (section 3)
  L4 responsive: a ~10 minute task runs while three unrelated chat questions are asked; each
                 answer starts within seconds and the task completes normally (section 1)

Run: .venv/bin/python tests/task_inbox/live_orchestrator_acceptance.py --base http://127.0.0.1:8211 \
        --data /path/to/dev/XOD_DATA_DIR --work /scratch/folder [--only L2] [--restart-cmd "..."]
"""
import argparse, json, os, re, signal, sqlite3, subprocess, sys, time, urllib.request
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True)
ap.add_argument("--data", required=True)
ap.add_argument("--work", required=True)
ap.add_argument("--only", default="")
ap.add_argument("--pidfile", default="")
ap.add_argument("--restart-cmd", default="")
ap.add_argument("--long-steps", type=int, default=8)
args = ap.parse_args()
BASE, DB, WORK = args.base.rstrip("/"), Path(args.data) / "xod.db", Path(args.work)
WORK.mkdir(parents=True, exist_ok=True)
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def http(path, body=None, method=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def plan(tid):
    return http(f"/api/tasks/{tid}/plan")


def wait(tid, pred, timeout, every=1.0, label=""):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            p = plan(tid)
        except Exception:
            time.sleep(every)
            continue
        sig = (p["status"], tuple(i["status"] for i in p["items"]))
        if sig != last:
            print(f"     {time.time() - t0:6.1f}s {p['status']:9} " + " ".join(i["status"][:4] for i in p["items"]), flush=True)
            last = sig
        if pred(p):
            return p
        time.sleep(every)
    raise TimeoutError(f"{label or tid}: condition not met in {timeout}s")


def writes_by_path(tid):
    """Every successful write-tool call the task recorded, from the activity log: path -> count.
    (Xod's agent can also write a file without going through a logged tool; the tests below
    therefore also check modification times on disk, which see every writer.)"""
    c = sqlite3.connect(str(DB))
    rows = c.execute("SELECT detail FROM activity WHERE origin = ? AND ok = 1 AND tool IN "
                     "('computer_write_file','fs_write_workspace_file','computer_edit_file')", (f"task:{tid}",)).fetchall()
    c.close()
    out = {}
    for (detail,) in rows:
        out[detail] = out.get(detail, 0) + 1
    return out


def done(p):
    return p["status"] in ("done", "failed", "cancelled")


def three_file_request(d):
    return (f"Create three text files, one at a time, one file per step:\n"
            f"1. Create the file {d}/alpha.txt containing exactly this: ALPHA\n"
            f"2. Create the file {d}/beta.txt containing exactly this: BETA\n"
            f"3. Create the file {d}/gamma.txt containing exactly this: GAMMA\n"
            f"Use your file write tool for each file. Do not create anything else.")


# ------------------------------------------------------------------------- L1
def l1():
    print("L1 ack before work")
    d = WORK / f"l1-{int(time.time())}"
    d.mkdir()
    t0 = time.time()
    r = http("/api/orchestrator/tasks", {"request": f"Create the file {d}/one.txt containing exactly this: ONE"})
    dt = time.time() - t0
    ack = r["ack"]
    p = plan(ack["task_id"])
    check("ack has task id and scope", ack["task_id"].startswith("task-") and "one.txt" in ack["scope"], ack["scope"][:80])
    check("ack within about a second", dt < 1.5, f"{dt * 1000:.0f} ms")
    check("no step had run at ack time", not any(i["status"] in ("in_progress", "completed") for i in p["items"]),
          f"plan items at ack read: {len(p['items'])}")
    p = wait(ack["task_id"], done, 300)
    check("task then finished", p["status"] == "done" and (d / "one.txt").read_text() == "ONE", p["status"])


# ------------------------------------------------------------------------- L2
def l2():
    print("L2 interrupt: change the target halfway")
    x = WORK / f"l2x-{int(time.time())}"
    y = WORK / f"l2y-{int(time.time())}"
    x.mkdir()
    y.mkdir()
    tid = http("/api/orchestrator/tasks", {"request": three_file_request(x)})["ack"]["task_id"]
    p = wait(tid, lambda p: sum(i["status"] == "completed" for i in p["items"]) >= 1 or done(p), 400, 0.5)
    check("plan has 3 steps", len(p["items"]) == 3, str([i["text"][:50] for i in p["items"]]))
    s = http(f"/api/tasks/{tid}/steer", {"kind": "redirect",
                                          "body": f"Change of plan: put the remaining files in {y} instead of {x}. Same names and contents."})
    print(f"     steer -> {s}")
    check("redirect accepted as queued (not claimed applied)", s.get("state") == "queued", s.get("applies", ""))
    p = wait(tid, done, 600, 1.0)
    inbox = p["inbox"]
    w = writes_by_path(tid)
    mt = {f: (x / f).stat().st_mtime_ns for f in ("alpha.txt",) if (x / f).exists()}
    xs, ys = sorted(f.name for f in x.iterdir()), sorted(f.name for f in y.iterdir())
    print(f"     X={xs} Y={ys} writes={ {Path(k).name + ('@X' if str(x) in k else '@Y'): v for k, v in w.items()} }")
    check("task finished", p["status"] == "done", p["status"])
    check("completed step survived (alpha.txt in X, correct content)", (x / "alpha.txt").exists() and (x / "alpha.txt").read_text() == "ALPHA")
    check("finished against the new target (gamma.txt in Y, not in X)",
          (y / "gamma.txt").exists() and not (x / "gamma.txt").exists() and (y / "gamma.txt").read_text() == "GAMMA")
    beta_where = [n for n, dd in (("X", x), ("Y", y)) if (dd / "beta.txt").exists()]
    check("beta.txt exists exactly once (in X if it was in flight, else Y)", len(beta_where) == 1, str(beta_where))
    check("no duplicated side effects (each path written once)", all(v == 1 for v in w.values()), str(w))
    check("redirect applied with what it changed", any(s["kind"] == "redirect" and s["state"] == "applied" for s in inbox),
          next((s["applied_note"] for s in inbox if s["kind"] == "redirect"), ""))
    sts = [i["status"] for i in p["items"]]
    check("plan kept history (completed kept, superseded shown cancelled)", sts[0] == "completed" and "cancelled" in sts, str(sts))
    return tid


# ------------------------------------------------------------------------- L3
def server_pid():
    return int(Path(args.pidfile).read_text().strip())


def l3():
    print("L3 kill the backend mid-task, restart it")
    if not (args.pidfile and args.restart_cmd):
        print("  SKIP (needs --pidfile and --restart-cmd)")
        return
    d = WORK / f"l3-{int(time.time())}"
    d.mkdir()
    req = (f"Do these steps one at a time, one per step:\n"
           f"1. Create the file {d}/first.txt containing exactly this: FIRST\n"
           f"2. Run the shell command: sleep 30 && echo slow > {d}/slow-marker.txt\n"
           f"3. Create the file {d}/third.txt containing exactly this: THIRD\n")
    tid = http("/api/orchestrator/tasks", {"request": req})["ack"]["task_id"]
    wait(tid, lambda p: len(p["items"]) >= 2 and p["items"][1]["status"] == "in_progress", 400, 0.5)
    time.sleep(8)   # well inside step 2's 30 s command
    first_mtime = (d / "first.txt").stat().st_mtime_ns if (d / "first.txt").exists() else None
    os.kill(server_pid(), signal.SIGKILL)
    print("     killed backend pid", server_pid(), flush=True)
    time.sleep(2)
    c = sqlite3.connect(str(DB))
    row = c.execute("SELECT status, plan_json FROM tasks WHERE id = ?", (tid,)).fetchone()
    c.close()
    items = json.loads(row[1])["items"]
    print(f"     on disk while down: status={row[0]} plan={[i['status'] for i in items]}")
    check("while down, the plan shows exactly which step was in flight",
          [i["status"] for i in items][:2] == ["completed", "in_progress"] and all(i["status"] == "pending" for i in items[2:]))
    subprocess.Popen(args.restart_cmd, shell=True)
    for _ in range(90):
        try:
            http("/api/tasks/counts")
            break
        except Exception:
            time.sleep(1)
    p = plan(tid)
    check("after restart the plan still shows the in-flight step and what remains",
          [i["status"] for i in p["items"]][0] == "completed" and p["items"][1]["status"] == "in_progress",
          str([i["status"] for i in p["items"]]))
    p = wait(tid, done, 600, 1.0)
    w = writes_by_path(tid)
    check("task resumed and finished", p["status"] == "done", p["status"])
    hist = [p["items"][1].get("note") or ""] + [e["text"] for e in p["items"][1].get("log") or []]
    check("the resumed step is marked resumed (in its note or its log)", any("resumed" in h for h in hist), str(hist)[:300])
    check("completed step 1 not repeated (first.txt untouched since before the kill)",
          first_mtime is not None and (d / "first.txt").stat().st_mtime_ns == first_mtime and w.get(str(d / "first.txt"), 0) <= 1,
          f"mtime before={first_mtime} after={(d / 'first.txt').stat().st_mtime_ns} logged writes={w}")
    print(f"     step 2 result: {(p['items'][1].get('result') or '')[:200]!r}")
    check("resumed step 2 completed only on a report that confirms it",
          p["items"][1]["status"] == "completed" and not re.search(r"can.t (tell|verify|confirm|check)", (p["items"][1].get("result") or "").lower()))
    check("all effects present", all((d / f).exists() for f in ("first.txt", "slow-marker.txt", "third.txt")))


# ------------------------------------------------------------------------- L4
def chat(question):
    """Asks the chat one question; returns (seconds to first answer text, seconds to end, text)."""
    body = json.dumps({"messages": [{"role": "user", "content": question}]}).encode()
    req = urllib.request.Request(BASE + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    first, text = None, []
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
                first = first or time.time() - t0
                text.append(str(ev["content"]))
    return first, time.time() - t0, "".join(text)


def l4():
    print(f"L4 responsiveness during a long task ({args.long_steps} steps of ~40 s)")
    d = WORK / f"l4-{int(time.time())}"
    d.mkdir()
    steps = "\n".join(f"{n}. Run the shell command: sleep 40 && echo step{n} >> {d}/log.txt"
                      for n in range(1, args.long_steps + 1))
    req = f"Do these steps one at a time, one per step, in order:\n{steps}"
    t_start = time.time()
    tid = http("/api/orchestrator/tasks", {"request": req})["ack"]["task_id"]
    wait(tid, lambda p: any(i["status"] == "in_progress" for i in p["items"]), 300, 0.5)
    qs = ["What is the capital of Portugal? One word.",
          "What is 17 times 23? Just the number.",
          "Give me a synonym for 'quick'. One word."]
    expect = ["lisbon", "391", ""]
    for q, e in zip(qs, expect):
        time.sleep(30)
        first, total, text = chat(q)
        p = plan(tid)
        running = p["status"] == "working"
        print(f"     Q: {q!r} -> first text {first and round(first, 1)} s, done {total:.1f} s, task {p['status']}: {text.strip()[:60]!r}")
        check(f"answered within seconds while the task works: {q[:30]}",
              first is not None and first < 15 and running and (e in text.lower()), f"first={first} total={total:.1f}")
    p = wait(tid, done, 1800, 3.0)
    elapsed = time.time() - t_start
    log = (d / "log.txt").read_text().split() if (d / "log.txt").exists() else []
    check("the long task completed normally", p["status"] == "done", p["status"])
    check("every step's effect happened exactly once, in order", log == [f"step{n}" for n in range(1, args.long_steps + 1)], str(log))
    print(f"     long task took {elapsed / 60:.1f} minutes")


tests = {"L1": l1, "L2": l2, "L3": l3, "L4": l4}
for name, fn in tests.items():
    if args.only and name not in args.only.split(","):
        continue
    try:
        fn()
    except Exception as e:
        check(f"{name} ran to the end", False, repr(e))
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} live checks passed")
sys.exit(1 if failed else 0)
