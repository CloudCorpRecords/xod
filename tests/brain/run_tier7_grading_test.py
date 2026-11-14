"""AGE-46 Tier 7 grading rig: Recurring autonomous work (TIER-DEEP-DIVES.md, tier-07.md).

Encodes Tier 7's deep-dive mission shape at the tool-contract level using
planted watch cycles and scripted task outcomes through the real routines
scheduler (app.services.routines), task_queue, and delivery outbox
(app.services.delivery) (no live LLM).

Contract points verified locally:
  - T7-A (load-bearing gate): Every cycle is recorded including quiet ones
    (5 no_change cycles -> 5 finished routine_runs rows, delivery_state='suppressed',
    0 chat messages, 0 pings).
  - T7-B (load-bearing gate): Material change delivers exactly 1 notification;
    identical finding on next cycle is deduped; a new finding delivers again.
  - T7-C: Unread source ('WATCH: could not check') and missing WATCH verdict
    classify as 'failed', never 'no_change'.
  - T7-D: Restart recovery (delivery.recover) settles a mid-send row at the
    destination without duplicating the chat message.
  - T7-E: Repeated failures (MAX_CONSECUTIVE_FAILURES) disable the routine
    while preserving its id and run history, and notify which result stops.

Live-only assertions (marked SKIPPED with reason; executed in the future
pinned-Mac + family-key ceremony run):
  - Multi-day / multi-week live wall-clock observation across Mac sleep/wake.
  - Live LLM watch execution against live changing sources & phone push.

Run: python tests/brain/run_tier7_grading_test.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_t7_grade_"))
os.environ.update({
    "HOME": str(TMP),
    "XOD_DATA_DIR": str(TMP / "data"),
    "XOD_NO_UPDATE_CHECK": "1",
})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import app.services.task_queue as TQ  # noqa: E402
import app.services.routines as RT  # noqa: E402
import app.services.delivery as DL  # noqa: E402
from app.services import storage  # noqa: E402

PINGS = []
TQ.notify = lambda title, msg: PINGS.append(("task", title, msg))
TQ._phone = lambda task, kind: None
DL._ping = lambda title, text: PINGS.append(("delivery", title, text))
if hasattr(storage, "init_db"):
    storage.init_db()
q = TQ.task_queue = TQ.TaskQueue()

results = []
skipped = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def skip_live(name, reason):
    skipped.append((name, reason))
    print(f"  SKIP (live-only) {name}  [{reason}]", flush=True)


def background_messages():
    sid = DL._session_id()
    return [m for m in storage.get_session_messages(sid) if m["role"] == "assistant"]


def one_cycle(rid, report, status="done"):
    out = RT.run_now(rid, reason="schedule")
    tid = out["task"]["id"]
    q._finish(tid, status, report, note="Finished")
    RT._follow_up()
    DL.deliver_pending()
    return tid


def case_t7a_quiet_cycles_recorded():
    print("== T7-A: quiet cycles recorded in ledger, zero notifications ==", flush=True)
    r = RT.create(
        "Price Watch A",
        "Check price of Widget X and alert if below 80.",
        schedule={"kind": "every", "hours": 1},
        kind="watch",
    )
    n_msgs, n_pings = len(background_messages()), len(PINGS)
    for _ in range(5):
        one_cycle(r["id"], "Checked Widget X: $89.\nWATCH: nothing new")

    runs = DL.runs(r["id"], 50)
    check("T7-A: all 5 quiet cycles recorded with finished_at",
          len(runs) == 5 and all(x["finished_at"] for x in runs), str(len(runs)))
    check("T7-A: every quiet cycle has outcome='no_change' and delivery_state='suppressed'",
          all(x["outcome"] == "no_change" and x["delivery_state"] == "suppressed" for x in runs))
    check("T7-A: zero chat messages and zero pings emitted for quiet cycles",
          len(background_messages()) == n_msgs and len(PINGS) == n_pings)
    return r["id"]


def case_t7b_material_and_deduped_notifications(rid):
    print("== T7-B: material change delivers once; duplicate finding deduped ==", flush=True)
    n_msgs_0 = len(background_messages())
    one_cycle(rid, "Widget X dropped to $79.\nWATCH: found")
    n_msgs_1 = len(background_messages())
    check("T7-B: material change delivered exactly 1 chat message", n_msgs_1 == n_msgs_0 + 1)

    # Same finding again -> deduped
    one_cycle(rid, "Widget X dropped to $79.\nWATCH: found")
    runs = DL.runs(rid, 50)
    n_msgs_2 = len(background_messages())
    check("T7-B: identical finding on next cycle is deduped (0 new messages)",
          n_msgs_2 == n_msgs_1 and runs[0]["delivery_state"] == "deduped",
          f"msgs={n_msgs_2} state={runs[0]['delivery_state']}")

    # Different finding -> delivered
    one_cycle(rid, "Widget X dropped to $69.\nWATCH: found")
    n_msgs_3 = len(background_messages())
    check("T7-B: new material finding delivers 1 new chat message", n_msgs_3 == n_msgs_2 + 1)


def case_t7c_unread_source_is_failure():
    print("== T7-C: unread source / missing verdict is failure, never no_change ==", flush=True)
    c1 = DL.classify("watch", {"status": "done", "result": "API timed out.\nWATCH: could not check source-api"})
    c2 = DL.classify("watch", {"status": "done", "result": "I checked the page and it looked fine."})
    check("T7-C: 'WATCH: could not check' classifies as 'failed' (never no_change)",
          c1["outcome"] == "failed", str(c1))
    check("T7-C: watch omitting WATCH line classifies as 'failed'",
          c2["outcome"] == "failed", str(c2))


def case_t7d_restart_recovery_no_duplicate():
    print("== T7-D: delivery.recover settles mid-send without duplicate ==", flush=True)
    key = "rtn-recover:changed:abc123"
    text = f"Widget Y restocked.\n\n{DL._marker(key)}"
    storage.add_message(DL._session_id(), "assistant", text)
    with DL._conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO deliveries (key, run_id, routine_id, state, text, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (key, "run-rec-1", "rtn-recover", "sending", text, time.time()),
        )
    before = len(background_messages())
    DL.recover()
    after = len(background_messages())
    with DL._conn() as c:
        row = c.execute("SELECT state FROM deliveries WHERE key = ?", (key,)).fetchone()
    check("T7-D: recover() marks already-landed 'sending' row as 'sent' with 0 duplicates",
          row is not None and row["state"] == "sent" and after == before,
          f"state={row['state'] if row else None} before={before} after={after}")


def case_t7e_honest_disablement_preserves_history():
    print("== T7-E: repeated failures disable routine while preserving identity & history ==", flush=True)
    r = RT.create(
        "Inventory Watch E",
        "Check inventory feed.",
        schedule={"kind": "every", "hours": 1},
        kind="watch",
    )
    n_msgs_before = len(background_messages())
    for i in range(DL.MAX_CONSECUTIVE_FAILURES):
        one_cycle(r["id"], f"Credential error on cycle {i + 1}.\nWATCH: could not check inventory-feed")

    after_r = RT.get(r["id"])
    hist = DL.runs(r["id"], 20)
    msgs = background_messages()[n_msgs_before:]
    check("T7-E: routine is disabled after MAX_CONSECUTIVE_FAILURES",
          after_r is not None and after_r["enabled"] is False, str(after_r and after_r["enabled"]))
    check("T7-E: routine id and run history preserved (not recreated)",
          after_r["id"] == r["id"] and after_r["runs"] == DL.MAX_CONSECUTIVE_FAILURES
          and len(hist) >= DL.MAX_CONSECUTIVE_FAILURES,
          f"runs={after_r['runs']} hist={len(hist)}")
    check("T7-E: disablement notice delivered naming the stopped routine",
          any("Inventory Watch E" in m["content"] and "turned it off" in m["content"] for m in msgs),
          str([m["content"][:120] for m in msgs]))


def main():
    rid = case_t7a_quiet_cycles_recorded()
    case_t7b_material_and_deduped_notifications(rid)
    case_t7c_unread_source_is_failure()
    case_t7d_restart_recovery_no_duplicate()
    case_t7e_honest_disablement_preserves_history()
    skip_live("T7-LIVE-1: multi-day / multi-week observation across Mac sleep/wake cycles",
              "requires pinned Mac scratch instance + wall-clock recurrence")
    skip_live("T7-LIVE-2: live LLM watch against live changing source & phone push delivery",
              "requires live LLM + family key ceremony + pinned Mac scratch instance")

    failed = [n for n, ok in results if not ok]
    print(f"\n========== {len(results) - len(failed)}/{len(results)} checks passed "
          f"({len(skipped)} live-only skipped) ==========", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        return 1
    print("ALL TIER 7 GRADING RIG CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
