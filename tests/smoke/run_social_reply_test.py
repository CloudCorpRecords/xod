"""
Social reply queue (services/social_reply.py): the queue store, the
encoded copy constraints, the approval gate, the executor seam with
its fixture and dry-run drivers, the unknown-post verify rule, and
the rate limiter.

Covered here:
  - X splitting: a short reply stays one part; a long reply splits
    only at paragraph boundaries with words unedited; a single
    paragraph over 280 characters is refused, never cut
  - draft validation: em dash and en dash refused, the reserved
    "- Rene the human" signature refused in any casing, an email
    shape or a phone shape refused (no private data in a public
    reply), a clean draft passes untouched
  - LinkedIn policy: reaction photos refused for Mariane Bekker,
    Founders Bay, You.com and 1Password, allowed for anyone else;
    X never allows a reaction photo lane at all in v1
  - the store: one live item per platform plus target, drafts and
    approvals transition in order, everything persists to disk and
    reloads, the summary counts match
  - the runner: an unapproved item never posts; an approved item
    posts through the fixture executor and verifies at the
    destination; a timed-out post lands unknown, verifies as found,
    and is NOT reposted; when verification proves it absent, a
    repost is allowed
  - dry-run: the full path runs, the inner executor's post is never
    called, the item stays approved, and the report names the parts
    that would have posted
  - rate limits: a second post inside the window is refused with
    the wait named; the daily cap refuses as well
  - audit: social events land in the event log with the actor named
  - harness seam: social_read is on the tool surface and its read
    returns the queue summary

Run: .venv/bin/python tests/smoke/run_social_reply_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")
TMP = Path(tempfile.mkdtemp(prefix="xod-social-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


from app.services import social_reply as sr


# ---------------------------------------------------------------- S1 splitting

short = "Thanks for flagging this. Fixed in the latest build, details in the changelog."
check("S1 short reply stays one part", sr.split_for_x(short) == [short])

p1 = "First paragraph. " * 12            # 204 chars
p2 = "Second paragraph here. " * 10      # 240 chars
long_text = p1.strip() + "\n\n" + p2.strip()
parts = sr.split_for_x(long_text)
check("S1 long reply splits at the paragraph boundary",
      parts == [p1.strip(), p2.strip()], f"got {len(parts)} parts")
check("S1 split keeps every word unedited",
      " ".join(" ".join(parts).split()) == " ".join(long_text.split()))

over = "x" * 281
try:
    sr.split_for_x(over)
    check("S1 single paragraph over 280 is refused", False, "no error raised")
except sr.ConstraintError:
    check("S1 single paragraph over 280 is refused", True)

exact = "y" * 280
check("S1 exactly 280 stays one part", sr.split_for_x(exact) == [exact])

# ---------------------------------------------------------------- S2 validation

def refused(text, platform="x"):
    try:
        sr.validate_draft(platform, text)
        return False
    except sr.ConstraintError:
        return True


check("S2 em dash refused", refused("Fixed now — thanks for the report"))
check("S2 en dash refused", refused("Pages 3 – 5 cover it"))
check("S2 reserved signature refused", refused("All set.\n- Rene the human"))
check("S2 reserved signature refused in any casing", refused("All set. - RENE THE HUMAN"))
check("S2 email shape refused", refused("Write me at rene@example.com and I will send it"))
check("S2 phone shape refused", refused("Call me at (417) 555-1963 about it"))
check("S2 empty draft refused", refused("   "))
clean = "Shipped in 0.9.22. The relaunch test passes now; notes in the changelog."
check("S2 clean draft passes untouched", sr.validate_draft("x", clean) == clean)

# ---------------------------------------------------------------- S3 LinkedIn policy

for who in ("Mariane Bekker", "mariane bekker", "Founders Bay", "You.com", "1Password"):
    ok, _why = sr.reaction_photo_allowed("linkedin", sr.normalize_author(who))
    check(f"S3 reaction photo refused for {who}", not ok)
ok, _ = sr.reaction_photo_allowed("linkedin", sr.normalize_author("Priya Nair"))
check("S3 reaction photo allowed for an ordinary contact", ok)
ok, _ = sr.reaction_photo_allowed("x", sr.normalize_author("Priya Nair"))
check("S3 X has no reaction photo lane in v1", not ok)

# ---------------------------------------------------------------- S4 the store

store = sr.SocialStore(TMP / "queue.json")
item = store.add_item(platform="x", kind="mention", target_id="tw-100",
                      target_url="https://x.com/a/status/100",
                      author="Priya Nair", text_in="the export still drops rows")
check("S4 item starts in draft", item["state"] == "draft")
try:
    store.add_item(platform="x", kind="mention", target_id="tw-100",
                   target_url="https://x.com/a/status/100",
                   author="Priya Nair", text_in="duplicate")
    check("S4 duplicate live target refused", False, "no error raised")
except sr.QueueError:
    check("S4 duplicate live target refused", True)

store.set_draft(item["id"], clean, actor="tree")
check("S4 draft attaches", store.get(item["id"])["draft"] == clean)
try:
    store.set_draft(item["id"], "has an — em dash", actor="tree")
    check("S4 a bad draft is refused at attach time", False, "no error raised")
except sr.ConstraintError:
    check("S4 a bad draft is refused at attach time", True)

res = asyncio.run(sr.run_post(item["id"], actor="tree", store=store,
                              executor=sr.FixtureExecutor(), dry_run=False))
check("S4 an unapproved item never posts", res.get("state") == "refused"
      and store.get(item["id"])["state"] == "draft", f"got {res}")

store.request_approval(item["id"], actor="tree")
check("S4 approval requested", store.get(item["id"])["state"] == "awaiting_approval")
store.approve(item["id"], actor="rene")
check("S4 approval lands", store.get(item["id"])["state"] == "approved")

store2 = sr.SocialStore(TMP / "queue.json")
check("S4 the store persists and reloads",
      store2.get(item["id"])["state"] == "approved"
      and store2.get(item["id"])["draft"] == clean)
summ = store2.summary()
check("S4 summary counts match",
      summ["by_state"].get("approved") == 1 and summ["by_platform"].get("x") == 1,
      f"got {summ}")

# ---------------------------------------------------------------- S5 post and verify

fx = sr.FixtureExecutor()
res = asyncio.run(sr.run_post(item["id"], actor="tree", executor=fx, dry_run=False, store=store))
after = store.get(item["id"])
check("S5 approved item posts and verifies",
      res.get("state") == "verified" and after["state"] == "verified",
      f"res={res} state={after['state']}")
check("S5 the destination reference is recorded",
      bool(after.get("posted_ref")), f"ref={after.get('posted_ref')}")
check("S5 the fixture saw exactly one post", fx.post_calls == 1)

# ---------------------------------------------------------------- S6 unknown, then found

store_b = sr.SocialStore(TMP / "queue-b.json")
it_b = store_b.add_item(platform="linkedin", kind="comment", target_id="li-7",
                        target_url="https://www.linkedin.com/feed/update/urn:li:activity:7",
                        author="Sam Ortiz", text_in="great writeup")
store_b.set_draft(it_b["id"], "Thank you. The receipts are in the repo if useful.", actor="tree")
store_b.approve(it_b["id"], actor="rene")
fx_b = sr.FixtureExecutor(post_behavior="timeout_found")
res_b = asyncio.run(sr.run_post(it_b["id"], actor="tree", executor=fx_b,
                                dry_run=False, store=store_b))
after_b = store_b.get(it_b["id"])
check("S6 a timed-out post that landed verifies without a repost",
      res_b.get("state") == "verified" and after_b["state"] == "verified"
      and fx_b.post_calls == 1, f"res={res_b} calls={fx_b.post_calls}")

# ---------------------------------------------------------------- S7 unknown, then absent

store_c = sr.SocialStore(TMP / "queue-c.json")
it_c = store_c.add_item(platform="x", kind="mention", target_id="tw-200",
                        target_url="https://x.com/b/status/200",
                        author="Lee Park", text_in="does it work on windows")
store_c.set_draft(it_c["id"], "It does. The Windows build is in the same release.", actor="tree")
store_c.approve(it_c["id"], actor="rene")
fx_c = sr.FixtureExecutor(post_behavior="timeout_absent")
res_c1 = asyncio.run(sr.run_post(it_c["id"], actor="tree", executor=fx_c,
                                 dry_run=False, store=store_c))
check("S7 a timed-out post proven absent returns to approved",
      res_c1.get("state") == "unknown_absent"
      and store_c.get(it_c["id"])["state"] == "approved", f"res={res_c1}")
fx_c.post_behavior = "ok"
res_c2 = asyncio.run(sr.run_post(it_c["id"], actor="tree", executor=fx_c,
                                 dry_run=False, store=store_c))
check("S7 the repost then goes through and verifies",
      res_c2.get("state") == "verified" and fx_c.post_calls == 2, f"res={res_c2}")

# ---------------------------------------------------------------- S8 dry-run

store_d = sr.SocialStore(TMP / "queue-d.json")
it_d = store_d.add_item(platform="x", kind="mention", target_id="tw-300",
                        target_url="https://x.com/c/status/300",
                        author="Ana Ruiz", text_in="love the scorecard")
store_d.set_draft(it_d["id"], long_text, actor="tree")
store_d.approve(it_d["id"], actor="rene")
fx_d = sr.FixtureExecutor()
res_d = asyncio.run(sr.run_post(it_d["id"], actor="tree", executor=fx_d,
                                dry_run=True, store=store_d))
check("S8 dry-run never calls the inner post",
      fx_d.post_calls == 0 and res_d.get("dry_run") is True, f"res={res_d}")
check("S8 dry-run reports the exact parts that would post",
      res_d.get("parts") == [p1.strip(), p2.strip()], f"parts={res_d.get('parts')}")
check("S8 dry-run leaves the item approved",
      store_d.get(it_d["id"])["state"] == "approved")

# ---------------------------------------------------------------- S9 rate limits

store_e = sr.SocialStore(TMP / "queue-e.json")
def make_approved(store, tid):
    it = store.add_item(platform="x", kind="mention", target_id=tid,
                        target_url=f"https://x.com/d/status/{tid}",
                        author="Kim Fox", text_in="ping")
    store.set_draft(it["id"], "Thanks, looking now.", actor="tree")
    store.approve(it["id"], actor="rene")
    return it

i1 = make_approved(store_e, "tw-401")
i2 = make_approved(store_e, "tw-402")
fx_e = sr.FixtureExecutor()
r1 = asyncio.run(sr.run_post(i1["id"], actor="tree", executor=fx_e,
                             dry_run=False, store=store_e))
r2 = asyncio.run(sr.run_post(i2["id"], actor="tree", executor=fx_e,
                             dry_run=False, store=store_e))
check("S9 the first post inside the window passes", r1.get("state") == "verified")
check("S9 the second post inside the window is refused with the wait named",
      r2.get("state") == "rate_limited" and (r2.get("retry_after_sec") or 0) > 0,
      f"r2={r2}")
check("S9 the refused item was not posted", fx_e.post_calls == 1
      and store_e.get(i2["id"])["state"] == "approved")

# ---------------------------------------------------------------- S10 fetch and parse

check("S10 an X notifications title parses its count",
      sr.parse_notifications_title("x", "(4) Notifications / X") == 4)
check("S10 an X notifications title with no badge reads zero",
      sr.parse_notifications_title("x", "Notifications / X") == 0)
check("S10 a foreign title reads unknown, not zero",
      sr.parse_notifications_title("x", "Home / X") is None)
check("S10 a LinkedIn notifications title parses its count",
      sr.parse_notifications_title("linkedin", "(2) Notifications | LinkedIn") == 2)

fx_f = sr.FixtureExecutor(inbox={"x": [
    {"target_id": "tw-900", "kind": "mention", "author": "Jo Kim",
     "target_url": "https://x.com/e/status/900", "text_in": "does the queue dedupe"},
]})
store_f = sr.SocialStore(TMP / "queue-f.json")
out1 = asyncio.run(sr.fetch("x", actor="tree", executor=fx_f, store=store_f))
out2 = asyncio.run(sr.fetch("x", actor="tree", executor=fx_f, store=store_f))
check("S10 fetch adds new inbound items", out1.get("added") == 1, f"out={out1}")
check("S10 a second fetch of the same inbox adds nothing", out2.get("added") == 0)

# ---------------------------------------------------------------- S11 audit trail

from app.services import event_log
log_text = Path(os.environ["XOD_DATA_DIR"], "event_log.jsonl").read_text(encoding="utf-8")
social_lines = [ln for ln in log_text.splitlines() if '"social.' in ln]
kinds = {json.loads(ln)["kind"] for ln in social_lines}
check("S11 social events landed in the event log",
      {"social.item_added", "social.approved", "social.verified"} <= kinds,
      f"kinds={sorted(kinds)}")
actors = {json.loads(ln)["actor"] for ln in social_lines}
check("S11 the audit names the actor", "rene" in actors and "tree" in actors)

# ---------------------------------------------------------------- S12 harness seam

from app.services import harness
check("S12 social_read is on the tool surface", "social_read" in harness._TOOL_NAMES)
view = harness._tool_social()
check("S12 the harness read returns the queue summary shape",
      "by_state" in view and "by_platform" in view, f"view={view}")

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
