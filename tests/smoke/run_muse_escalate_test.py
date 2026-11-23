"""
Escalate-to-Tree fallback (services/muse_escalate.py): when Xod cannot resolve
an approval card or a Muse chat option widget itself, it sends one escalation
per showing to Tree via the tree-inbox transport (kind "task"), deduped by an
escalation id hashed from (kind, target_id), with expiry re-arming the id when
the showing ends.

Covered here:
  E1  payload shape and transport call for each of the four KINDS
  E2  dedupe per showing across same/different kind and target_id
  E3  transport failure is not recorded and retries succeed once fixed
  E4  queued transport records as queued, counts in queued stat, and dedupes
  E5  expiry returns count, clears live(), increments expired, audits, re-arms id
  E6  unknown kind returns None and never calls the transport
  E7  status() shape: live count, stats dict, and escalations entry fields
  E8  card_press_unverified only writes phase escalation/escalation_expired to muse_review.audit
  E9  capture seam (_shot_fn), extra payload fields, and non-raising audit failures

  .venv/bin/python tests/smoke/run_muse_escalate_test.py
"""
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-museescalate-"))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def main():
    from app.services import agent_log, muse_escalate, muse_review

    def reset_state():
        muse_escalate._state["live"].clear()
        muse_escalate._state["stats"].clear()
        muse_escalate._state["stats"].update({"sent": 0, "queued": 0, "failed": 0, "expired": 0})
        muse_escalate._send_fn = None
        muse_escalate._shot_fn = None

    def install_transport(state="inbox", msg_id="msg", error=None):
        sent = []

        def fake_send(kind, body, payload):
            sent.append((kind, body, payload))
            if error is not None:
                raise error
            return {"state": state, "id": f"{msg_id}-{len(sent)}"}

        muse_escalate._send_fn = fake_send
        return sent

    print("E1 payload shape and transport delivery for all four kinds")
    reset_state()
    sent1 = install_transport(state="inbox", msg_id="e1")
    check("KINDS defines the four documented escalation kinds",
          set(muse_escalate.KINDS) == {
              "option_widget_held",
              "option_widget_unpressable",
              "card_press_unverified",
              "chrome_card_unpressable",
          } and len(muse_escalate.KINDS) == 4, str(muse_escalate.KINDS))

    cases = [
        {
            "kind": "option_widget_held",
            "surface": "muse_app_options",
            "target_id": "widget-held-1",
            "title": "Which account should I use?",
            "text": "Which account should I use to send the update?",
            "options": ["Work account", "Personal account"],
            "button": None,
            "tried": ["AI review by fake-model", "heuristic ranking topped: Work account"],
            "reason": "no clear pick: accounts are equivalent",
        },
        {
            "kind": "option_widget_unpressable",
            "surface": "muse_app_options",
            "target_id": "widget-unpress-1",
            "title": "Send the 7 queued DMs?",
            "text": "Want me to send those 7 queued DMs?",
            "options": ["Send the 7 queued DMs", "Wait for now"],
            "button": "Send the 7 queued DMs",
            "tried": ["AI review picked: Send the 7 queued DMs", "AXPress returned -25204"],
            "reason": "the press failed: AXPress returned -25204",
        },
        {
            "kind": "card_press_unverified",
            "surface": "muse_app",
            "target_id": "card-unverified-1",
            "title": "Approval request for Gmail",
            "text": "Allow Tree to send this reply?",
            "options": [],
            "button": "Reply",
            "tried": ["rubber-stamp approve", "pressed Reply", "panel re-read"],
            "reason": "card is still showing on the re-read",
        },
        {
            "kind": "chrome_card_unpressable",
            "surface": "chrome_extension",
            "target_id": "chrome-card-1",
            "title": "Approval request for Browser",
            "text": "Allow Tree to upload report.pdf?",
            "options": ["Allow", "Deny"],
            "button": "Allow",
            "tried": ["read Chrome UIA card", "policy decision: would press Allow"],
            "reason": "Chrome extension watcher is dry-run in this build",
        },
    ]

    for idx, c in enumerate(cases, 1):
        out = muse_escalate.escalate(
            c["kind"], c["target_id"], surface=c["surface"],
            title=c["title"], text=c["text"], options=c["options"],
            button=c["button"], tried=c["tried"], reason=c["reason"],
        )
        ok_payload = (
            isinstance(out, dict)
            and out.get("version") == 1
            and isinstance(out.get("escalation_id"), str)
            and out["escalation_id"].startswith("museesc-")
            and out.get("kind") == c["kind"]
            and out.get("surface") == c["surface"]
            and out.get("target_id") == c["target_id"]
            and out.get("title") == c["title"]
            and out.get("text") == c["text"]
            and isinstance(out.get("options"), list) and out["options"] == c["options"]
            and out.get("button") == c["button"]
            and isinstance(out.get("tried"), list) and out["tried"] == c["tried"]
            and out.get("reason") == c["reason"]
            and "device" in out and isinstance(out.get("device"), str)
            and isinstance(out.get("ts"), (int, float)) and out["ts"] > 0
        )
        check(f"{c['kind']}: escalate() returns complete payload shape", ok_payload, str(out))

        ok_transport = False
        if len(sent1) == idx:
            t_kind, t_body, t_payload = sent1[-1]
            ok_transport = (
                t_kind == "task"
                and f"XOD ESCALATION [{c['kind']}]" in t_body
                and out is not None and out["escalation_id"] in t_body
                and c["text"] in t_body
                and c["reason"] in t_body
                and t_payload is out
            )
        check(f"{c['kind']}: fake transport received task, body header/id, and same payload",
              ok_transport, str(sent1[-1]) if sent1 else "no transport call")

    print("E2 dedupe per showing: same (kind, target_id) dedupes; different kind or target sends")
    reset_state()
    sent2 = install_transport(state="inbox", msg_id="e2")
    p2_first = muse_escalate.escalate(
        "option_widget_held", "target-dedupe-1", surface="muse_app_options",
        title="Q1", text="Pick one", options=["A", "B"], reason="no clear pick",
    )
    p2_dup = muse_escalate.escalate(
        "option_widget_held", "target-dedupe-1", surface="muse_app_options",
        title="Q1", text="Pick one", options=["A", "B"], reason="no clear pick",
    )
    check("second escalate() with same kind and target_id returns None without resending",
          p2_first is not None and p2_dup is None and len(sent2) == 1,
          f"first={p2_first} dup={p2_dup} calls={len(sent2)}")

    p2_diff_kind = muse_escalate.escalate(
        "option_widget_unpressable", "target-dedupe-1", surface="muse_app_options",
        title="Q1", text="Pick one", options=["A", "B"], button="A", reason="AXPress failed",
    )
    check("same target_id with a different kind sends a distinct escalation",
          p2_diff_kind is not None
          and p2_diff_kind["escalation_id"] != p2_first["escalation_id"]
          and len(sent2) == 2,
          f"diff_kind={p2_diff_kind} calls={len(sent2)}")

    p2_diff_target = muse_escalate.escalate(
        "option_widget_held", "target-dedupe-2", surface="muse_app_options",
        title="Q2", text="Pick another", options=["C", "D"], reason="no clear pick",
    )
    check("same kind with a different target_id sends a distinct escalation",
          p2_diff_target is not None
          and p2_diff_target["escalation_id"] != p2_first["escalation_id"]
          and len(sent2) == 3 and len(muse_escalate.live()) == 3,
          f"diff_target={p2_diff_target} calls={len(sent2)}")

    print("E3 transport failure is not recorded and retries succeed once transport recovers")
    reset_state()
    sent3_fail = install_transport(error=RuntimeError("tree-inbox down"))
    out3_fail = muse_escalate.escalate(
        "option_widget_held", "target-retry-1", surface="muse_app_options",
        title="Q?", text="Need help", options=["Yes", "No"], reason="review unavailable",
    )
    st3_fail = muse_escalate.status()
    check("raising transport makes escalate() return None, leaves live() empty, increments failed",
          out3_fail is None
          and len(sent3_fail) == 1
          and muse_escalate.live() == []
          and st3_fail["stats"]["failed"] == 1
          and st3_fail["stats"]["sent"] == 0,
          f"out={out3_fail} status={st3_fail}")

    sent3_ok = install_transport(state="inbox", msg_id="e3-ok")
    out3_ok = muse_escalate.escalate(
        "option_widget_held", "target-retry-1", surface="muse_app_options",
        title="Q?", text="Need help", options=["Yes", "No"], reason="review unavailable",
    )
    st3_ok = muse_escalate.status()
    check("fixing transport allows retry of same kind and target_id to succeed and record",
          out3_ok is not None
          and len(sent3_ok) == 1
          and len(muse_escalate.live()) == 1
          and muse_escalate.live()[0]["escalation_id"] == out3_ok["escalation_id"]
          and st3_ok["stats"]["sent"] == 1
          and st3_ok["stats"]["failed"] == 1,
          f"out={out3_ok} status={st3_ok}")

    print("E4 queued transport records as queued, increments queued stat, and dedupes")
    reset_state()
    sent4 = install_transport(state="queued", msg_id="e4-q")
    out4 = muse_escalate.escalate(
        "chrome_card_unpressable", "chrome-queued-1", surface="chrome_extension",
        title="Approval request for Browser", text="Allow Tree to submit form?",
        options=["Allow", "Deny"], button="Allow", reason="dry-run watcher",
    )
    st4 = muse_escalate.status()
    check("queued transport records live entry with transport='queued' and increments queued stat only",
          out4 is not None
          and out4["transport"] == "queued"
          and out4["message_id"] == "e4-q-1"
          and len(muse_escalate.live()) == 1
          and muse_escalate.live()[0]["transport"] == "queued"
          and st4["stats"]["queued"] == 1
          and st4["stats"]["sent"] == 0,
          f"out={out4} status={st4}")

    out4_dup = muse_escalate.escalate(
        "chrome_card_unpressable", "chrome-queued-1", surface="chrome_extension",
        title="Approval request for Browser", text="Allow Tree to submit form?",
        options=["Allow", "Deny"], button="Allow", reason="dry-run watcher",
    )
    check("dedupe applies to queued escalations",
          out4_dup is None and len(sent4) == 1
          and muse_escalate.status()["stats"]["queued"] == 1
          and muse_escalate.status()["stats"]["sent"] == 0,
          f"dup={out4_dup} calls={len(sent4)} status={muse_escalate.status()}")

    print("E5 expiry returns count, empties live(), increments expired, audits open/decided, re-arms id")
    reset_state()
    sent5 = install_transport(state="inbox", msg_id="e5")
    orig_opened = agent_log.approval_opened
    orig_decided = agent_log.approval_decided
    opened_calls = []
    decided_calls = []
    agent_log.approval_opened = lambda pid, kind, app, title, detail="": opened_calls.append(
        {"pid": pid, "kind": kind, "app": app, "title": title, "detail": detail}
    )
    agent_log.approval_decided = lambda pid, decision: decided_calls.append(
        {"pid": pid, "decision": decision}
    )
    try:
        out5 = muse_escalate.escalate(
            "option_widget_held", "target-expire-1", surface="muse_app_options",
            title="Deploy now?", text="Should I deploy to staging?",
            options=["Deploy now", "Hold"], reason="review unavailable",
        )
        check("audit-open captures call with kind 'muse_escalation' and matching escalation id",
              out5 is not None
              and len(opened_calls) == 1
              and opened_calls[0]["pid"] == out5["escalation_id"]
              and opened_calls[0]["kind"] == "muse_escalation"
              and opened_calls[0]["app"] == "Muse app",
              str(opened_calls))

        expired_n = muse_escalate.expire("target-expire-1")
        st5 = muse_escalate.status()
        check("expire(target_id) returns 1, empties live(), and increments expired stat",
              expired_n == 1
              and muse_escalate.live() == []
              and st5["stats"]["expired"] == 1,
              f"expired={expired_n} live={muse_escalate.live()} status={st5}")
        check("audit-decided captures expiry decision for the escalation id",
              len(decided_calls) == 1
              and decided_calls[0]["pid"] == out5["escalation_id"]
              and "expired by Xod" in decided_calls[0]["decision"],
              str(decided_calls))

        out5_rearm = muse_escalate.escalate(
            "option_widget_held", "target-expire-1", surface="muse_app_options",
            title="Deploy now?", text="Should I deploy to staging?",
            options=["Deploy now", "Hold"], reason="review unavailable",
        )
        check("after expiry, same kind and target_id re-arms and escalates again",
              out5_rearm is not None
              and out5_rearm["escalation_id"] == out5["escalation_id"]
              and len(sent5) == 2
              and len(muse_escalate.live()) == 1
              and len(opened_calls) == 2,
              f"rearm={out5_rearm} calls={len(sent5)}")

        muse_escalate.escalate(
            "option_widget_unpressable", "target-expire-1", surface="muse_app_options",
            title="Deploy now?", text="Should I deploy to staging?",
            options=["Deploy now", "Hold"], button="Deploy now", reason="AXPress failed",
        )
        other5 = muse_escalate.escalate(
            "option_widget_held", "target-keep-1", surface="muse_app_options",
            title="Other?", text="Keep me live", options=["A", "B"], reason="no clear pick",
        )
        none_expired = muse_escalate.expire("no-such-target")
        multi_expired = muse_escalate.expire("target-expire-1")
        check("expire() closes all live kinds for target_id without touching other targets",
              none_expired == 0
              and multi_expired == 2
              and len(muse_escalate.live()) == 1
              and muse_escalate.live()[0]["escalation_id"] == other5["escalation_id"]
              and muse_escalate.status()["stats"]["expired"] == 3
              and len(decided_calls) == 3,
              f"none={none_expired} multi={multi_expired} live={muse_escalate.live()}")
    finally:
        agent_log.approval_opened = orig_opened
        agent_log.approval_decided = orig_decided

    print("E6 unknown kind returns None and never calls the transport")
    reset_state()
    sent6 = install_transport(state="inbox", msg_id="e6")
    out6a = muse_escalate.escalate(
        "not_a_real_kind", "target-unknown-1", surface="muse_app",
        title="Unknown", text="Should not send", reason="bogus",
    )
    out6b = muse_escalate.escalate(
        "", "target-unknown-2", surface="muse_app",
        title="Empty", text="Should not send", reason="bogus",
    )
    check("unknown kind returns None, never calls transport, leaves live and stats clean",
          out6a is None and out6b is None
          and sent6 == []
          and muse_escalate.live() == []
          and muse_escalate.status()["stats"] == {"sent": 0, "queued": 0, "failed": 0, "expired": 0},
          f"out6a={out6a} out6b={out6b} sent={sent6} status={muse_escalate.status()}")

    print("E7 status() shape: live count, stats dict, and escalations list entry fields")
    reset_state()
    install_transport(error=RuntimeError("transient error"))
    muse_escalate.escalate(
        "option_widget_held", "st-failed", surface="muse_app_options",
        title="F", text="Fail once", options=["A", "B"], reason="err",
    )
    install_transport(state="inbox", msg_id="e7-inbox")
    p7_expired = muse_escalate.escalate(
        "option_widget_held", "st-exp", surface="muse_app_options",
        title="E", text="Will expire", options=["A", "B"], reason="err",
    )
    muse_escalate.expire("st-exp")
    p7_a = muse_escalate.escalate(
        "card_press_unverified", "st-live-1", surface="muse_app",
        title="Card 1", text="Approve Gmail", button="Reply", reason="still showing",
    )
    install_transport(state="queued", msg_id="e7-queued")
    p7_b = muse_escalate.escalate(
        "chrome_card_unpressable", "st-live-2", surface="chrome_extension",
        title="Card 2", text="Approve Upload", options=["Allow", "Deny"], button="Allow", reason="dry-run",
    )

    st7 = muse_escalate.status()
    live7 = muse_escalate.live()
    expected_entry_keys = {"escalation_id", "kind", "surface", "target_id", "transport"}
    entries_ok = (
        isinstance(st7.get("escalations"), list)
        and len(st7["escalations"]) == 2
        and all(expected_entry_keys.issubset(e.keys()) for e in st7["escalations"])
        and {(e["escalation_id"], e["kind"], e["surface"], e["target_id"], e["transport"])
             for e in st7["escalations"]} == {
            (p7_a["escalation_id"], "card_press_unverified", "muse_app", "st-live-1", "inbox"),
            (p7_b["escalation_id"], "chrome_card_unpressable", "chrome_extension", "st-live-2", "queued"),
        }
    )
    check("status() live count matches live() and stats dict carries exact counts",
          st7.get("live") == len(live7) == 2
          and isinstance(st7.get("stats"), dict)
          and st7["stats"] == {"sent": 2, "queued": 1, "failed": 1, "expired": 1}
          and p7_expired is not None,
          str(st7))
    check("status()['escalations'] entries carry escalation_id, kind, surface, target_id, and transport",
          entries_ok, str(st7.get("escalations")))

    st7["stats"]["sent"] = 999
    live7[0]["kind"] = "mutated"
    st7_after = muse_escalate.status()
    check("status()['stats'] and live() return copies without mutating internal _state",
          st7_after["stats"]["sent"] == 2
          and muse_escalate.live()[0]["kind"] in ("card_press_unverified", "chrome_card_unpressable"),
          str(st7_after))

    print("E8 only card_press_unverified writes phase escalation/escalation_expired to muse_review.audit")
    reset_state()
    install_transport(state="inbox", msg_id="e8")
    orig_audit = muse_review.audit
    audit_calls = []
    muse_review.audit = lambda record: audit_calls.append(record)
    try:
        for k, surf, tid in (
            ("option_widget_held", "muse_app_options", "e8-held"),
            ("option_widget_unpressable", "muse_app_options", "e8-unpress"),
            ("chrome_card_unpressable", "chrome_extension", "e8-chrome"),
        ):
            muse_escalate.escalate(
                k, tid, surface=surf, title="T", text="X",
                options=["Allow", "Deny"], button="Allow", reason="stuck",
            )
            muse_escalate.expire(tid)
        check("other three kinds do NOT write to muse_review.audit on escalate or expire",
              audit_calls == [], str(audit_calls))

        p8 = muse_escalate.escalate(
            "card_press_unverified", "e8-card-1", surface="muse_app",
            title="Approval request for Gmail", text="Allow Tree to reply?",
            button="Reply", tried=["pressed Reply"], reason="still showing",
        )
        check("card_press_unverified writes phase 'escalation' row on escalate",
              len(audit_calls) == 1
              and audit_calls[0].get("phase") == "escalation"
              and audit_calls[0].get("card_id") == "e8-card-1"
              and p8 is not None and audit_calls[0].get("escalation_id") == p8["escalation_id"]
              and audit_calls[0].get("kind") == "card_press_unverified"
              and audit_calls[0].get("transport") == "inbox"
              and audit_calls[0].get("message_id") == p8["message_id"],
              str(audit_calls))

        muse_escalate.expire("e8-card-1")
        check("card_press_unverified writes phase 'escalation_expired' row on expire",
              len(audit_calls) == 2
              and audit_calls[1].get("phase") == "escalation_expired"
              and audit_calls[1].get("card_id") == "e8-card-1"
              and audit_calls[1].get("escalation_id") == p8["escalation_id"]
              and audit_calls[1].get("kind") == "card_press_unverified",
              str(audit_calls))
    finally:
        muse_review.audit = orig_audit

    print("E9 capture seam (_shot_fn), extra fields, and non-raising audit failures")
    reset_state()
    sent9 = install_transport(state="inbox", msg_id="e9")
    shots_seen = []

    def fake_shot(tid):
        shots_seen.append(tid)
        if tid == "shot-err":
            return {"path": None, "error": "screen locked"}
        return {"path": f"/tmp/shots/{tid}.png", "error": None}

    muse_escalate._shot_fn = fake_shot
    p9_ok = muse_escalate.escalate(
        "option_widget_held", "shot-ok", surface="muse_app_options",
        title="Q", text="Choose", options=["A", "B"], reason="no clear pick", capture=True,
    )
    p9_err = muse_escalate.escalate(
        "option_widget_held", "shot-err", surface="muse_app_options",
        title="Q", text="Choose", options=["A", "B"], reason="no clear pick", capture=True,
    )
    p9_extra = muse_escalate.escalate(
        "chrome_card_unpressable", "extra-1", surface="chrome_extension",
        title="Chrome", text="Allow?", options=["Allow", "Deny"], button="Allow",
        reason="dry-run", screenshot="/tmp/existing.png", capture=True,
        extra={"origin": "https://example.com", "permission": "upload", "kind": "should_not_override"},
    )
    check("capture=True calls _shot_fn and records path or error on payload and body",
          shots_seen == ["shot-ok", "shot-err"]
          and p9_ok is not None and p9_ok["screenshot"] == "/tmp/shots/shot-ok.png"
          and "Screenshot: /tmp/shots/shot-ok.png" in sent9[0][1]
          and p9_err is not None and p9_err["screenshot"] is None and p9_err["screenshot_error"] == "screen locked"
          and "Screenshot: unavailable (screen locked)" in sent9[1][1],
          f"shots={shots_seen} p9_ok={p9_ok} p9_err={p9_err}")
    check("explicit screenshot skips _shot_fn and extra adds fields without overriding core keys",
          p9_extra is not None
          and p9_extra["screenshot"] == "/tmp/existing.png"
          and p9_extra["kind"] == "chrome_card_unpressable"
          and p9_extra.get("origin") == "https://example.com"
          and "Origin: https://example.com" in sent9[2][1]
          and "Permission: upload" in sent9[2][1],
          f"p9_extra={p9_extra}")

    reset_state()
    install_transport(state="inbox", msg_id="e9-res")
    orig_opened = agent_log.approval_opened
    orig_decided = agent_log.approval_decided
    orig_audit = muse_review.audit
    agent_log.approval_opened = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db locked"))
    agent_log.approval_decided = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db locked"))
    muse_review.audit = lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full"))
    try:
        p9_resilient = muse_escalate.escalate(
            "card_press_unverified", "resilient-1", surface="muse_app",
            title="Card", text="Allow?", button="Allow", reason="still showing",
        )
        n9_resilient = muse_escalate.expire("resilient-1")
        check("broken agent_log/muse_review never raise out of escalate() or expire()",
              p9_resilient is not None and n9_resilient == 1 and muse_escalate.live() == [],
              f"p={p9_resilient} n={n9_resilient}")
    finally:
        agent_log.approval_opened = orig_opened
        agent_log.approval_decided = orig_decided
        muse_review.audit = orig_audit

    reset_state()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
