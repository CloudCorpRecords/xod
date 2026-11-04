"""
Orchestrator Phase 4: scheduled runs with run records, notification policy, delivery accounting.

The routine scheduler, the task queue's finish path, the run records, the outbox and the chat
store are real; only the agent's work is skipped (each run's task is finished with a scripted
report through the queue's own _finish).

  D1 a watch that finds nothing stays silent for a week of hourly runs (168 runs, 0 messages,
     0 pings), and every run has a finished record
  D2 the one run that finds something delivers exactly once; later runs that find the same thing
     are deduped; a different finding is delivered
  D3 exactly once across restarts: (a) restart between detection and delivery, (b) crash after
     the message landed but before the bookkeeping, (c) crash before the message landed
  D4 failures reach the person; three in a row turn the job off and say which result stops arriving
  D5 a scheduled run's task does not ping on its own; an ordinary task still does
  D6 a run waiting on the person: one decision delivery per wait, however many ticks pass
  D7 a watch that cannot read its source is a failure, never "nothing new"

Run: .venv/bin/python tests/task_inbox/run_delivery_test.py [--repeat N]
"""
import json, os, sys, tempfile, time, uuid
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_delivery_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import app.services.task_queue as TQ
import app.services.routines as RT
import app.services.delivery as DL
from app.services import storage

PINGS = []
TQ.notify = lambda title, msg: PINGS.append(("task", title, msg))
TQ._phone = lambda task, kind: None
DL._ping = lambda title, text: PINGS.append(("delivery", title, text))
storage.init_db() if hasattr(storage, "init_db") else None
q = TQ.task_queue = TQ.TaskQueue()
results = []
TAG = {"t": ""}


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def background_messages():
    sid = DL._session_id()
    return [m for m in storage.get_session_messages(sid) if m["role"] == "assistant"]


def one_run(rid, report, status="done"):
    """A scheduled run: the scheduler starts it, its task ends with this report, the next tick settles it."""
    out = RT.run_now(rid, reason="schedule")
    tid = out["task"]["id"]
    if status == "waiting":
        q._update(tid, status="waiting", waiting_kind="approval", waiting_detail={"label": "Send", "page": "a form"})
    else:
        q._finish(tid, status, report, note="Finished")
    RT._follow_up()
    DL.deliver_pending()
    return tid


def d1_silent_week():
    print("D1 a week of hourly no-change runs stays silent")
    r = RT.create(f"Price watch{TAG['t']}", "Check the price of the Trail Runner 2 and tell me if it drops below 80.",
                  schedule={"kind": "every", "hours": 1}, kind="watch")
    n_msgs, n_pings = len(background_messages()), len(PINGS)
    for _ in range(168):
        one_run(r["id"], "Checked the shop: still 89.99.\nWATCH: nothing new")
    runs = DL.runs(r["id"], 500)
    check("168 runs recorded, all finished", len(runs) == 168 and all(x["finished_at"] for x in runs))
    check("every run's outcome is no_change and its delivery suppressed",
          all(x["outcome"] == "no_change" and x["delivery_state"] == "suppressed" for x in runs))
    check("no message reached the chat", len(background_messages()) == n_msgs)
    check("no notification fired", len(PINGS) == n_pings, str(PINGS[n_pings:][:3]))
    check("each run's request carried the watch protocol", "WATCH: nothing new" in q.get(runs[0]["task_id"])["request"])
    return r


def d2_found_once(r):
    print("D2 the run that finds something delivers exactly once")
    n = len(background_messages())
    one_run(r["id"], "The price dropped.\nWATCH: found\nTrail Runner 2 is 74.99 at Shop A")
    msgs = background_messages()
    check("delivered once to the chat", len(msgs) == n + 1 and "74.99" in msgs[-1]["content"])
    check("one ping", sum(1 for p in PINGS if p[0] == "delivery" and p[1] == f"Price watch{TAG['t']}") == 1)
    for _ in range(3):
        one_run(r["id"], "Still low.\nWATCH: found\nTrail Runner 2 is 74.99 at Shop A")
    runs = DL.runs(r["id"], 5)
    check("the same finding again is deduped, not re-delivered",
          len(background_messages()) == n + 1 and [x["delivery_state"] for x in runs[:3]] == ["deduped"] * 3)
    one_run(r["id"], "Even lower.\nWATCH: found\nTrail Runner 2 is 69.99 at Shop B")
    check("a different finding is delivered", len(background_messages()) == n + 2 and "69.99" in background_messages()[-1]["content"])


def d3_restarts():
    print("D3 exactly once across restarts")
    r = RT.create("Restock watch", "Tell me when the blue size 10 is back.", schedule={"kind": "every", "hours": 1}, kind="watch")
    # (a) detection recorded, the process dies before delivering
    out = RT.run_now(r["id"])
    q._finish(out["task"]["id"], "done", f"WATCH: found\nBlue size 10 is back in stock{TAG['t']}", note="Finished")
    RT._follow_up()                                 # detected; delivery pending
    n = len(background_messages())
    DL.recover()                                    # restart
    DL.deliver_pending()
    DL.recover()
    DL.deliver_pending()
    check("(a) restart between detection and delivery: delivered exactly once", len(background_messages()) == n + 1)
    # (b) the message lands, then the process dies before the bookkeeping
    out = RT.run_now(r["id"])
    q._finish(out["task"]["id"], "done", f"WATCH: found\nGreen size 9 is back too{TAG['t']}", note="Finished")
    RT._follow_up()
    real_send = DL._send

    def send_then_die(key, text, title):
        real_send(key, text, title)
        raise SystemExit("power cut")
    DL._send = send_then_die
    try:
        DL.deliver_pending()
    except SystemExit:
        pass
    DL._send = real_send
    n = len(background_messages())
    DL.recover()
    DL.deliver_pending()
    check("(b) crash after the message landed: confirmed at the destination, not re-sent", len(background_messages()) == n,
          f"{n} -> {len(background_messages())}")
    st = [d for d in DL.delivered_messages() if f"Green size 9 is back too{TAG['t']}" in d["text"]]
    check("(b) the outbox row is settled as sent", len(st) == 1 and st[0]["state"] == "sent")
    # (c) the process dies before the message is written
    out = RT.run_now(r["id"])
    q._finish(out["task"]["id"], "done", f"WATCH: found\nRed size 11 restocked{TAG['t']}", note="Finished")
    RT._follow_up()

    def die_first(key, text, title):
        raise SystemExit("power cut")
    DL._send = die_first
    try:
        DL.deliver_pending()
    except SystemExit:
        pass
    DL._send = real_send
    n = len(background_messages())
    DL.recover()
    DL.deliver_pending()
    check("(c) crash before the message landed: sent exactly once on recovery", len(background_messages()) == n + 1
          and sum(1 for m in background_messages() if f"Red size 11 restocked{TAG['t']}" in m["content"]) == 1)


def d4_failures():
    print("D4 failures reach the person; a broken job is turned off with notice")
    r = RT.create("News digest", "Summarise the news.", schedule={"kind": "every", "hours": 6})
    n = len(background_messages())
    one_run(r["id"], "The site timed out.", status="failed")
    check("a failure is delivered", len(background_messages()) == n + 1 and "did not complete" in background_messages()[-1]["content"])
    one_run(r["id"], "Timed out again.", status="failed")
    one_run(r["id"], "Timed out a third time.", status="failed")
    rr = RT.get(r["id"])
    last = background_messages()[-1]["content"]
    check("three failures in a row turn it off", rr["enabled"] is False)
    check("and the person is told which result will stop arriving", "News digest" in last and "turned it off" in last, last[:200])


def d5_task_pings():
    print("D5 scheduled runs ping only through delivery")
    r = RT.create("Quiet check", "Check something.", schedule={"kind": "every", "hours": 1}, kind="watch")
    before = len([p for p in PINGS if p[0] == "task"])
    one_run(r["id"], "WATCH: nothing new")
    check("a scheduled run's task does not ping on its own", len([p for p in PINGS if p[0] == "task"]) == before)
    t = q.create("An ordinary task")
    q._finish(t["id"], "done", "Done.", note="Finished")
    check("an ordinary task still pings", len([p for p in PINGS if p[0] == "task"]) == before + 1)


def d6_decision():
    print("D6 one decision delivery per wait")
    r = RT.create("Form sender", "Fill the form and send it.", schedule={"kind": "every", "hours": 24})
    n = len(background_messages())
    tid = one_run(r["id"], "", status="waiting")
    for _ in range(5):
        RT._follow_up()
        DL.deliver_pending()
    check("the person hears about the decision once", len(background_messages()) == n + 1 and "needs you" in background_messages()[-1]["content"])
    q._finish(tid, "done", "Sent after your OK.", note="Finished")
    RT._follow_up()
    DL.deliver_pending()
    check("the run's final result is still delivered after the decision", len(background_messages()) == n + 2
          and "Sent after your OK" in background_messages()[-1]["content"])


def d7_unreadable():
    print("D7 an unreadable source is not 'nothing new'")
    r = RT.create("Blocked watch", "Watch the page.", schedule={"kind": "every", "hours": 1}, kind="watch")
    n = len(background_messages())
    one_run(r["id"], "The page returned 503.\nWATCH: could not check the shop page (HTTP 503)")
    check("reported as a failure and delivered", DL.runs(r["id"], 1)[0]["outcome"] == "failed" and len(background_messages()) == n + 1)
    one_run(r["id"], "Looked around.")      # no WATCH line at all
    check("a watch with no verdict is a failure, not silence", DL.runs(r["id"], 1)[0]["outcome"] == "failed")


def main(repeat):
    for i in range(repeat):
        print(f"=== run {i + 1}/{repeat}")
        TAG["t"] = f" #{i + 1}"
        r = d1_silent_week()
        d2_found_once(r)
        d3_restarts()
        d4_failures()
        d5_task_pings()
        d6_decision()
        d7_unreadable()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--repeat") + 1]) if "--repeat" in sys.argv else 1
    main(n)
