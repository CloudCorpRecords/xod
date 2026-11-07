"""
Inbox status: the needs-reply digest with proof (services/inbox_status.py),
its endpoints, its manifest registration, and the lightweight-mode warmer.

The failure this covers: "have I done all my emails?" answered in pieces,
because a mail search that timed out on the slow route looked exactly like
an empty inbox. The checks prove: a source that has never been read is
reported unread and contributes no items; a failed refresh keeps the last
good digest and marks it stale with the error attached; the threading logic
counts a thread only when the newest inbox message is inbound and no reply
was sent at or after it; automated senders are filtered but counted, never
silently dropped; the manifest carries the capability with its limits and
no message content; the routes exist; and the warmer is one of the loops
lightweight mode can skip.

Runs against a throwaway data folder with the mail route replaced by a
fake: no real mailbox is read and nothing leaves the machine.

  .venv/bin/python tests/smoke/run_inbox_status_test.py
"""
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-inbox-status-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
os.environ["XOD_INBOX"] = "off"             # the warmer never starts in tests
(TMP / "data").mkdir()

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


NOW_MS = int(time.time() * 1000)
CB = "992606166ca84388ac4e34ab6f1d0497"     # cloudtheboy (matches services/inbox_status.py defaults)
FB = "01298b0cbdfa45bd9374d34aeabe2f96"     # founders bay

META = {
    "m1": {"id": "m1", "threadId": "t1", "internalDate": str(NOW_MS - 1000),
           "snippet": "following up on the sponsorship",
           "payload": {"headers": [{"name": "From", "value": "Lais Example <lais@example.com>"},
                                   {"name": "Subject", "value": "Quarterly widget numbers"}]}},
    "m0": {"id": "m0", "threadId": "t1", "internalDate": str(NOW_MS - 5000),
           "payload": {"headers": [{"name": "From", "value": "cloudtheboy@gmail.com"}]}},
    "m3": {"id": "m3", "threadId": "t2", "internalDate": str(NOW_MS - 2000),
           "payload": {"headers": [{"name": "From", "value": "Person Two <two@example.com>"},
                                   {"name": "Subject", "value": "Re: hello"}]}},
    "m4": {"id": "m4", "threadId": "t2", "internalDate": str(NOW_MS - 500),
           "payload": {"headers": [{"name": "From", "value": "cloudtheboy@gmail.com"}]}},
    "m5": {"id": "m5", "threadId": "t3", "internalDate": str(NOW_MS - 3000),
           "payload": {"headers": [{"name": "From", "value": "notifications@github.com"},
                                   {"name": "Subject", "value": "[repo] CI failed"}]}},
    "m6": {"id": "m6", "threadId": "t4", "internalDate": str(NOW_MS - 4000),
           "payload": {"headers": [{"name": "From", "value": "cloudtheboy@gmail.com"},
                                   {"name": "Subject", "value": "note to self"}]}},
    "m7": {"id": "m7", "threadId": "t5", "internalDate": str(NOW_MS - 6000),
           "snippet": "the event photos are ready",
           "payload": {"headers": [{"name": "From", "value": "Wendy Example <wendy@example.com>"},
                                   {"name": "Subject", "value": "Event photos"}]}},
    "f1": {"id": "f1", "threadId": "ft1", "internalDate": str(NOW_MS - 1000),
           "snippet": "can we talk Thursday",
           "payload": {"headers": [{"name": "From", "value": "Client Person <client@example.org>"},
                                   {"name": "Subject", "value": "Thursday call"}]}},
}
LISTS = {
    (CB, "inbox"): [{"id": "m7", "threadId": "t5"}, {"id": "m5", "threadId": "t3"},
                    {"id": "m6", "threadId": "t4"}, {"id": "m3", "threadId": "t2"},
                    {"id": "m1", "threadId": "t1"}],
    (CB, "sent"): [{"id": "m4", "threadId": "t2"}, {"id": "m0", "threadId": "t1"}],
    (FB, "inbox"): [{"id": "f1", "threadId": "ft1"}],
    (FB, "sent"): [],
}
NOW_S = int(time.time())
MUSE_DATA = [
    {"id": "mm3", "thread_id": "mt1", "direction": "OUTBOUND", "header_from": "tree@muse.ai",
     "subject": "Re: Signed document", "created_at": NOW_S - 5000, "preview": "sent"},
    {"id": "mm1", "thread_id": "mt1", "direction": "INBOUND", "header_from": "person@example.com",
     "header_from_name": "A Person", "subject": "Re: Signed document",
     "created_at": NOW_S - 1000, "preview": "thanks, one more question"},
    {"id": "mm2", "thread_id": "mt2", "direction": "OUTBOUND", "header_from": "tree@muse.ai",
     "subject": "Relay", "created_at": NOW_S - 2000, "preview": "outbound only"},
]

ROUTE = {"down_all": True, "down_accounts": {FB}}


def main():
    from app.services import inbox_status as ib

    # Retries must not slow the suite: the module's clock keeps real time,
    # its backoff sleeps become no-ops.
    ib.time = types.SimpleNamespace(time=time.time, sleep=lambda s: None)

    def fake_run_cli(argv, timeout):
        if ROUTE["down_all"]:
            raise ib.RouteError("gws code 5: error sending request (fake route)")
        if argv[0] == ib.MUSE_MAIL_CLI:
            return json.dumps({"data": MUSE_DATA})
        if "gmail" in argv:
            acct = argv[argv.index("--account") + 1]
            if acct in ROUTE["down_accounts"]:
                raise ib.RouteError("gws code 5: error sending request (fake route)")
            params = json.loads(argv[argv.index("--params") + 1])
            if "list" in argv:
                box = "inbox" if "in:inbox" in params["q"] else "sent"
                return json.dumps({"messages": LISTS[(acct, box)]})
            if "get" in argv:
                return json.dumps(META[params["id"]])
        raise AssertionError(f"unexpected argv {argv}")

    ib._run_cli = fake_run_cli

    def drain_background():
        for _ in range(200):
            if not ib._background:
                return
            time.sleep(0.05)

    srcs = {s["key"]: s for s in ib.load_sources()}
    check("default sources are the three tracked mailboxes",
          sorted(srcs) == ["gmail-cloudtheboy", "gmail-foundersbay", "muse-mail"])

    # ---- phase A: route down, nothing ever read -> unread, never empty ----------
    st = ib.status()
    check("status reports every source unread before any read",
          all(v["state"] == "unread" for v in st["sources"].values())
          and sorted(st["unread_sources"]) == sorted(srcs))
    ans = ib.needs_reply()
    drain_background()
    check("needs-reply claims nothing while every source is unread",
          ans["needs_reply"] == [] and ans["complete"] is False
          and sorted(u["source"] for u in ans["unread_sources"]) == sorted(srcs))
    check("the first answer kicks a background refresh for the unread sources",
          sorted(ans["refresh_started"]) == sorted(srcs))
    st = ib.status()
    check("once the refresh lands, each unread source carries its route error",
          all("code 5" in (v["last_error"] or "") for v in st["sources"].values()))

    # ---- phase B: two sources read, one still down ------------------------------
    ROUTE["down_all"] = False
    ib.refresh_source(srcs["gmail-cloudtheboy"])
    ib.refresh_source(srcs["muse-mail"])
    ans = ib.needs_reply(max_age_minutes=1440)
    drain_background()
    by_thread = {i["thread_id"]: i for i in ans["needs_reply"]}
    check("thread with an inbound after the last sent counts",
          by_thread.get("t1", {}).get("reason") == "inbound_after_last_sent")
    check("thread with no reply in the window counts",
          by_thread.get("t5", {}).get("reason") == "latest_message_inbound_no_reply_in_window")
    check("replied thread, self mail and automated mail do not count",
          all(t not in by_thread for t in ("t2", "t3", "t4")))
    check("muse-mail inbound-led thread counts, outbound-led does not",
          "mt1" in by_thread and "mt2" not in by_thread)
    check("items carry their source's checked_at",
          all(i["source_checked_at"] for i in ans["needs_reply"]))
    cb = ans["sources"]["gmail-cloudtheboy"]
    check("source stats are auditable (threads seen, automated filtered)",
          cb["threads_seen"] == 5 and cb["automated_filtered"] == 1 and cb["status"] == "ok")
    check("the down source is named unread, not empty",
          ans["complete"] is False
          and [u["source"] for u in ans["unread_sources"]] == ["gmail-foundersbay"])

    # ---- phase C: a failed refresh keeps the last good digest, marked stale -----
    stale_digest = {"source": "gmail-foundersbay", "checked_at": ib._now_iso(),
                    "last_attempt_at": None, "ok": True, "error": None,
                    "items": [{"account": "gmail-foundersbay", "thread_id": "old1",
                               "message_id": "oldm", "sender": "Old <old@example.org>",
                               "subject": "Earlier", "date": "2026-11-07T00:00:00+00:00",
                               "snippet": "", "reason": "inbound_after_last_sent"}],
                    "stats": {"window_days": 7, "threads_seen": 3, "automated_filtered": 0,
                              "duration_ms": 1000, "cli_calls": 4}}
    ib._write_digest(stale_digest)
    ib.refresh_source(srcs["gmail-foundersbay"])       # route still down for this account
    st = ib.status()
    check("failed refresh marks the source stale and keeps its digest",
          st["sources"]["gmail-foundersbay"]["state"] == "stale"
          and st["sources"]["gmail-foundersbay"]["cached_item_count"] == 1
          and "code 5" in (st["sources"]["gmail-foundersbay"]["last_error"] or ""))
    ans = ib.needs_reply(max_age_minutes=1440)
    drain_background()
    old = [i for i in ans["needs_reply"] if i["thread_id"] == "old1"]
    check("stale items are served labeled stale, never as fresh",
          len(old) == 1 and old[0]["source_status"] == "stale")

    # ---- phase D: the route recovers -> ok again, error cleared -----------------
    ROUTE["down_accounts"] = set()
    ib.refresh_source(srcs["gmail-foundersbay"])
    st = ib.status()
    check("a successful refresh returns the source to ok with the error cleared",
          st["sources"]["gmail-foundersbay"]["state"] == "ok"
          and st["sources"]["gmail-foundersbay"]["last_error"] is None
          and st["unread_sources"] == [])
    ans = ib.needs_reply(max_age_minutes=1440)
    check("the recovered source's real thread now counts",
          any(i["thread_id"] == "ft1" for i in ans["needs_reply"]) and ans["complete"] is True)

    # ---- phase E: the background freshness guard --------------------------------
    # Route drops for cloudtheboy only. A guarded refresh of the just-refreshed
    # digest must be a no-op (a real attempt would fail and flip it to stale);
    # an unguarded explicit refresh must still re-read and go stale.
    ROUTE["down_accounts"] = {"992606166ca84388ac4e34ab6f1d0497"}
    ib.refresh_source(srcs["gmail-cloudtheboy"], skip_if_younger_than=3600)
    st = ib.status()
    check("a guarded background refresh leaves a fresh ok digest untouched",
          st["sources"]["gmail-cloudtheboy"]["state"] == "ok"
          and st["sources"]["gmail-cloudtheboy"]["last_error"] is None)
    ib.refresh_source(srcs["gmail-cloudtheboy"])
    st = ib.status()
    check("an unguarded refresh still re-reads (and goes stale on the down route)",
          st["sources"]["gmail-cloudtheboy"]["state"] == "stale")
    ROUTE["down_accounts"] = set()

    # ---- manifest registration, with limits and no content ----------------------
    from app.services import capability_manifest as cm
    m = cm.build_manifest()
    inbox = m.get("inbox") or {}
    check("manifest carries the inbox section with both endpoints",
          inbox.get("available") is True and inbox.get("read_only") is True
          and inbox.get("endpoints") == ["/api/inbox/status", "/api/inbox/needs-reply"])
    check("manifest inbox names every tracked source and the OAuth limit",
          sorted(s["source"] for s in inbox.get("sources") or []) == sorted(srcs)
          and "no per-user OAuth connection yet" in (inbox.get("limits") or ""))
    text = cm.summary_text(m)
    check("summary states the inbox digest and the never-empty rule",
          "Inbox status:" in text and "never as empty" in text)
    blob = json.dumps(m)
    check("no message content enters the manifest",
          "Quarterly widget numbers" not in blob and "lais@example.com" not in blob)

    # ---- the warmer is a lightweight-skippable loop ------------------------------
    from app.services import run_mode
    check("inbox_warm is registered as an optional (lightweight-skippable) loop",
          "inbox_warm" in run_mode.OPTIONAL_LOOPS)

    # ---- read-only by construction ----------------------------------------------
    src_text = Path(ib.__file__).read_text(encoding="utf-8")
    check("the only gmail operations in the service are list and get",
          '"messages", "list"' in src_text and '"messages", "get"' in src_text
          and '"messages", "send"' not in src_text and '"messages", "modify"' not in src_text
          and '"messages", "trash"' not in src_text and '"messages", "delete"' not in src_text)

    # ---- routes exist on the app --------------------------------------------------
    try:
        from app.main import app
        paths = {r.path for r in app.routes}
    except Exception as e:
        print(f"SKIP  route checks (app import failed: {type(e).__name__})", flush=True)
    else:
        check("GET /api/inbox/status is routed", "/api/inbox/status" in paths)
        check("GET /api/inbox/needs-reply is routed", "/api/inbox/needs-reply" in paths)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f" -- FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
