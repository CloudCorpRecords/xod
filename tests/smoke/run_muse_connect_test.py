"""
One-Click Muse Connect (services/muse_connect.py), with a stand-in
device identity and a temp tree-inbox, no keystore and no network:
  - an offer builds, signs, and verifies; its link and text block
    round trip back to the same verifiable offer
  - the confirm code matches what both screens would compute
  - a tampered payload fails verification, an expired offer is
    refused, and the failure words are the plain ones
  - create_offer stores state and drops the offer into the inbox
  - a Muse hello in the inbox advances the connection to live
    and a receipt lands back in the inbox
  - a revocation note from the Muse side closes the connection,
    and revoke from this side does the same plus writes the note
  - when family-rules.json sits in the shared space, the offer
    names it by hash
  .venv/bin/python tests/smoke/run_muse_connect_test.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-muse-connect-"))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


from app.services import mail_crypto as mc
from app.services import muse_connect as mx

IDENT = mc.Identity.new()
INBOX = TMP / "inbox"
INBOX.mkdir()
mx._identity = lambda: IDENT
mx._inbox_dir = lambda: INBOX

# --- the offer itself -------------------------------------------------
offer = mx.build_offer(identity=IDENT)
ok, problems = mx.verify_offer(offer)
check("offer verifies", ok, "; ".join(problems))
check("offer names the manifest", bool(offer["manifest"]["hash"])
      and bool(offer["manifest"]["summary"]))
check("code is 8 chars and matches the core",
      len(offer["code"]) == 8
      and offer["code"] == mx.confirm_code(
          {k: v for k, v in offer.items()
           if k not in ("signature", "code")}))

link = mx.offer_link(offer)
check("link carries the payload", link.startswith("xod://connect?offer="))
back = json.loads(mc.b64d(link.split("offer=", 1)[1]))
ok, problems = mx.verify_offer(back)
check("link round trips to a verifiable offer", ok, "; ".join(problems))

block = mx.offer_text_block(offer)
lines = block.splitlines()
check("text block has the header, code and payload lines",
      lines[0] == "XOD CONNECT OFFER" and lines[1].startswith("Code: ")
      and len(lines) == 4)
from_block = json.loads(mc.b64d(lines[2]))
check("text block round trips", mx.verify_offer(from_block)[0])

tampered = dict(offer)
tampered["manifest"] = dict(offer["manifest"], summary="edited by hand")
ok, problems = mx.verify_offer(tampered)
check("tampered payload is refused", not ok and any("signature" in p for p in problems),
      "; ".join(problems))

stale = mx.build_offer(identity=IDENT, ttl=-60)
ok, problems = mx.verify_offer(stale)
check("expired offer is refused as expired",
      not ok and any("expired" in p for p in problems), "; ".join(problems))

# --- the flow: create, hello, live, revoke -----------------------------
made = mx.create_offer()
oid = made["offer"]["offer_id"]
check("create_offer stores an offered offer", made["state"] == "offered")
check("offer dropped into the inbox for a family-local Muse",
      made["inbox_dropped"] and (INBOX / f"muse-connect-offer-{oid}.json").exists())

st = mx.status()
mine = [o for o in st["offers"] if o["offer_id"] == oid]
check("status shows the offer as offered",
      len(mine) == 1 and mine[0]["state"] == "offered")

hello = {"type": mx.HELLO_TYPE, "offer_id": oid,
         "muse": {"name": "Tree"}, "accepted_at": "2026-11-06T18:00:00Z"}
(INBOX / f"muse-connect-hello-{oid}.json").write_text(json.dumps(hello), encoding="utf-8")
st = mx.status()
conn = [c for c in st["connections"] if c["offer_id"] == oid]
check("hello advances the connection to live",
      len(conn) == 1 and conn[0]["state"] == "live", json.dumps(conn))
check("receipt landed back in the inbox",
      (INBOX / f"muse-connect-receipt-{oid}.json").exists())
mine = [o for o in st["offers"] if o["offer_id"] == oid]
check("offer state reads live once connected", mine[0]["state"] == "live")

note = {"type": mx.REVOKE_TYPE, "offer_id": oid,
        "revoked_at": "2026-11-06T18:05:00Z", "by": "muse"}
(INBOX / f"muse-connect-revoke-{oid}.json").write_text(json.dumps(note), encoding="utf-8")
st = mx.status()
conn = [c for c in st["connections"] if c["offer_id"] == oid]
check("a Muse revocation note closes the connection",
      len(conn) == 1 and conn[0]["state"] == "revoked", json.dumps(conn))

made2 = mx.create_offer()
oid2 = made2["offer"]["offer_id"]
hello2 = dict(hello, offer_id=oid2)
(INBOX / f"muse-connect-hello-{oid2}.json").write_text(json.dumps(hello2), encoding="utf-8")
mx.status()
out = mx.revoke(oid2)
st = mx.status()
conn2 = [c for c in st["connections"] if c["offer_id"] == oid2]
check("revoke from this side flips state and writes the note",
      out["success"] and conn2 and conn2[0]["state"] == "revoked"
      and (INBOX / f"muse-connect-revoke-{oid2}.json").exists())

out = mx.revoke("no-such-offer")
check("revoking an unknown id is an honest failure", not out["success"])

# --- an expired offer stays terminal even if a hello lands -------------
stale2 = mx.build_offer(identity=IDENT, ttl=-60)
store = mx._load_store()
store["offers"][stale2["offer_id"]] = {
    "offer": stale2, "state": "offered", "inbox_dropped": True}
mx._save_store(store)
late_hello = {"type": mx.HELLO_TYPE, "offer_id": stale2["offer_id"],
              "muse": {"name": "Late"}, "accepted_at": "2026-11-06T18:00:00Z"}
(INBOX / f"muse-connect-hello-{stale2['offer_id']}.json").write_text(
    json.dumps(late_hello), encoding="utf-8")
st = mx.status()
late_conn = [c for c in st["connections"]
             if c["offer_id"] == stale2["offer_id"]]
late_offer = [o for o in st["offers"]
              if o["offer_id"] == stale2["offer_id"]]
check("a hello after expiry creates no connection",
      len(late_conn) == 0 and len(late_offer) == 1
      and late_offer[0]["state"] == "expired", json.dumps(late_conn))

# --- rules pickup -------------------------------------------------------
(TMP / "family-rules.json").write_text('{"version": 1, "rules": []}', encoding="utf-8")
offer2 = mx.build_offer(identity=IDENT)
check("offer names the shared rules file by hash",
      bool((offer2.get("rules") or {}).get("sha256"))
      and offer2["rules"]["file"] == "family-rules.json")
check("offer with rules still verifies", mx.verify_offer(offer2)[0])

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:", ", ".join(failed))
    sys.exit(1)
