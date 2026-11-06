"""
Tier 7 process probe: a watch finding survives a hard process kill.

The delivery suite (run_delivery_test.py, case D3) simulates restarts
by calling delivery.recover() inside one process. This probe checks
the same promise across real process boundaries: one process detects
a finding and is hard-killed with no cleanup before delivering, a
second process recovers and delivers it exactly once, and a third
recovery delivers nothing again. Run records, the chat store and the
delivery outbox all live in one shared data dir, as they would for
the real backend. Only the agent's work is scripted, as in D3.

Run: .venv/bin/python tests/task_inbox/run_tier7_process_probe.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FINDING = "Probe Berry is back in stock at 12.50"


def child_env(tmp):
    env = dict(os.environ)
    env.update({
        "XOD_DATA_DIR": str(tmp / "data"),
        "HOME": str(tmp),
        "XOD_NO_UPDATE_CHECK": "1",
        "XOD_SECRETS_SERVICE": "Xod tier7 probe keys",
        "XOD_TEST_KEYRING_FILE": str(tmp / "keys.json"),
        "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
        "PYTHONUTF8": "1",
    })
    return env


def child_setup():
    os.environ.setdefault(
        "PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
    sys.path.insert(0, str(ROOT))
    import app.services.task_queue as TQ
    import app.services.routines as RT
    import app.services.delivery as DL
    from app.services import storage
    pings_path = Path(os.environ["XOD_DATA_DIR"]).parent / "pings.log"

    def note(*_a):
        with pings_path.open("a", encoding="utf-8") as f:
            f.write("ping\n")

    TQ.notify = lambda title, msg: note()
    TQ._phone = lambda task, kind: None
    DL._ping = lambda title, text: note()
    if hasattr(storage, "init_db"):
        storage.init_db()
    TQ.task_queue = TQ.TaskQueue()
    return TQ, RT, DL, storage


def child_detect(tmp):
    TQ, RT, DL, storage = child_setup()
    r = RT.create("Restock probe watch", "Tell me when Probe Berry is back.",
                  schedule={"kind": "every", "hours": 1}, kind="watch")
    out = RT.run_now(r["id"], reason="schedule")
    TQ.task_queue._finish(out["task"]["id"], "done",
                          f"WATCH: found\n{FINDING}", note="Finished")
    RT._follow_up()
    (tmp / "rid.txt").write_text(r["id"], encoding="utf-8")
    print("DETECTED", flush=True)
    time.sleep(120)


def child_recover(tmp):
    TQ, RT, DL, storage = child_setup()
    rid = (tmp / "rid.txt").read_text(encoding="utf-8").strip()
    DL.recover()
    DL.deliver_pending()
    DL.recover()
    DL.deliver_pending()
    sid = DL._session_id()
    msgs = [m for m in storage.get_session_messages(sid)
            if m["role"] == "assistant" and "Probe Berry" in (m["content"] or "")]
    runs = DL.runs(rid, 10)
    pings_path = tmp / "pings.log"
    n_pings = (len(pings_path.read_text(encoding="utf-8").splitlines())
               if pings_path.exists() else 0)
    print("RESULT " + json.dumps({
        "messages": len(msgs),
        "pings": n_pings,
        "runs": [{"outcome": x["outcome"],
                  "delivery_state": x["delivery_state"],
                  "finished": bool(x["finished_at"])} for x in runs],
    }), flush=True)
    os._exit(0)


def main():
    if len(sys.argv) > 3 and sys.argv[1] == "--child":
        tmp = Path(sys.argv[3])
        if sys.argv[2] == "detect":
            child_detect(tmp)
        else:
            child_recover(tmp)
        return
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok)))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}"
              + (f"  [{detail}]" if detail and not ok else ""), flush=True)

    tmp = Path(tempfile.mkdtemp(prefix="xod_tier7_probe_"))
    (tmp / "data").mkdir(parents=True)
    env = child_env(tmp)
    me = str(Path(__file__).resolve())
    det = subprocess.Popen([sys.executable, me, "--child", "detect", str(tmp)],
                           env=env, cwd=str(ROOT), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
    seen = ""
    deadline = time.time() + 90
    while time.time() < deadline and "DETECTED" not in seen:
        line = det.stdout.readline()
        if not line:
            break
        seen += line
    check("detect process recorded the finding", "DETECTED" in seen, seen[-300:])
    det.kill()
    det.wait()
    check("detect process was hard-killed before delivering",
          det.returncode not in (0, None), str(det.returncode))

    def recover():
        out = subprocess.run([sys.executable, me, "--child", "recover", str(tmp)],
                             env=env, cwd=str(ROOT), capture_output=True,
                             text=True, timeout=180)
        for line in out.stdout.splitlines():
            if line.startswith("RESULT "):
                return json.loads(line[7:])
        raise RuntimeError("no RESULT from recover child: "
                           + out.stdout[-300:] + out.stderr[-300:])

    r1 = recover()
    check("the finding was delivered exactly once after the kill",
          r1["messages"] == 1, json.dumps(r1))
    check("exactly one delivery ping across processes",
          r1["pings"] == 1, json.dumps(r1))
    check("one run record, finished, outcome changed and delivered",
          len(r1["runs"]) == 1 and r1["runs"][0]["finished"]
          and r1["runs"][0]["outcome"] == "changed"
          and r1["runs"][0]["delivery_state"] == "delivered",
          json.dumps(r1["runs"]))
    r2 = recover()
    check("a second recovery delivers nothing twice",
          r2["messages"] == 1 and r2["pings"] == 1, json.dumps(r2))
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:")
        for n in failed:
            print(f"  {n}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
