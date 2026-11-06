"""
Phase 0 of the simplicity redesign (presentation rules only):
  - project scratch classification (origin marker / tags / known legacy debris names)
  - agent mail heartbeat/test-ping filtering in threads() and status() counts
Pure storage-level checks against a throwaway XOD_DATA_DIR; no server, no keychain.
  python tests/smoke/run_simplicity_phase0_test.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="xod-phase0-"))
os.environ["XOD_DATA_DIR"] = str(TMP)
os.environ.setdefault("XOD_SPEECH", "off")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def main():
    from app.services.project_storage import is_scratch_project
    from app.services import agent_mail

    print("P1 project classification: real work stays real")
    for name, tags, origin in [("Daily Three", [], "user"), ("bullshit", [], "user"),
                               ("Getting started", ["welcome"], "user"),
                               ("Test Kitchen recipes", [], "user"),
                               ("Quarterly taxes", ["finance"], "user")]:
        check(f"real: {name!r}", not is_scratch_project(name, tags, origin))

    print("P2 project classification: test debris goes to Playground")
    for name in ["File creation unavailable", "Proof file", "Proof File", "Proof File Saver",
                 "Proof File Writer", "Update proof", "Word Frequency Tests", "Word Frequency Test"]:
        check(f"scratch by name: {name!r}", is_scratch_project(name, [], "user"))
    check("scratch by origin marker", is_scratch_project("Anything at all", [], "proving"))
    check("scratch by origin marker (test)", is_scratch_project("Anything at all", [], "test"))
    check("scratch by tag", is_scratch_project("Anything at all", ["scratch"], "user"))

    print("P3 ping/test detection")
    check("hello subject is a ping", agent_mail.is_ping_or_test("phone@home.example", "hello", "{}"))
    check("bare hello text is a ping", agent_mail.is_ping_or_test("iphone@home.example", "", "hello"))
    check("takeover-check sender is a test agent",
          agent_mail.is_ping_or_test("takeover-check-0940@renes-macbook-pro.tail804523.ts.net:8790", "connect?", "{}"))
    check("a real message is not a ping", not agent_mail.is_ping_or_test("dana@post.example.com", "Lunch?", "hello there, long time"))
    check("a real hello-line inside a message is not a ping",
          not agent_mail.is_ping_or_test("dana@post.example.com", "Intro", "hello"))

    print("P4 threads()/status() hide pings but keep real mail (nothing deleted)")
    now = time.time()
    rows = [
        # id, thread, peer, subject, text, minutes ago
        ("m1", "t-hello", "phone@renes-iphone.tail.example", "hello", json.dumps({"battery": 90}), 30),
        ("m2", "t-hello", "phone@renes-iphone.tail.example", "hello", json.dumps({"battery": 88}), 20),
        ("m3", "t-takeover", "takeover-check-0940@renes-macbook-pro.tail.example:8790", "connect?", "{}", 25),
        ("m4", "t-real", "dana@post.example.com", "Site notes", "the draft is ready", 10),
        ("m5", "t-mixed", "dana@post.example.com", "hello", "{}", 15),
        ("m6", "t-mixed", "dana@post.example.com", "Real follow-up", "sending the file now", 5),
    ]
    with agent_mail._db() as c:
        for mid, thread, peer, subject, text, mins in rows:
            c.execute("""INSERT INTO mail_messages (id, msg_id, thread, direction, peer, recipients, kind,
                         subject, text, status, verified, problem, in_reply_to, origin, created_at, read)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                      (mid, f"msg-{mid}", thread, "in", peer, "[]", "message", subject, text,
                       "new", 1, None, None, "mail", now - mins * 60))
    req = agent_mail.threads("requests")
    req_threads = {t["thread"] for t in req}
    check("hello-only thread is not listed", "t-hello" not in req_threads, str(req_threads))
    check("takeover-check thread is not listed", "t-takeover" not in req_threads, str(req_threads))
    check("real thread is listed", "t-real" in req_threads, str(req_threads))
    mixed = next((t for t in req if t["thread"] == "t-mixed"), None)
    check("mixed thread is listed by its real message",
          bool(mixed) and mixed["last"]["subject"] == "Real follow-up" and mixed["unread"] == 1,
          str(mixed and mixed.get("last")))
    st = agent_mail.status()
    check("status requests count ignores pings/test agents", st["requests"] == 1, str(st.get("requests")))
    with agent_mail._db() as c:
        total = c.execute("SELECT COUNT(*) FROM mail_messages").fetchone()[0]
    check("no message was deleted", total == len(rows), str(total))
    full = agent_mail.thread("t-hello", mark_read=False)
    check("ping thread is still stored and readable by id", len(full) == 2, str(len(full)))

    failed = [n for n, ok in results if not ok]
    print(f"SUMMARY {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)} checks passed"
          + (f" — failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
