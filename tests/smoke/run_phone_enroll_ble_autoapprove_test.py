"""
Pairing auto-approve on Bluetooth presence (the owner's rule,
2026-11-05; services/ble_presence.py + services/phone_enroll.py,
phone side dev.xod.mail BlePresence served by dev.xod.android
BlePairing, read here by services/phone_ble.py).

The rule: "if device is on Bluetooth or same wifi and it sees it on
the list in xod it can just approve it." A bare BLE sighting is not
evidence (anonymous UUID, rotating addresses). What counts is the
signed presence token this computer read over BLE from the phone:
bound to the phone's live pairing ask (its issued_at) and signed by
the phone's family key, re-verified against the ask's own verified
record before it may replace the owner's tap, the full pairing gate
still applying. No valid token, or a token for a different ask or
key, pends or refuses exactly as before.

Covered here:
  - evidence: a valid bound token verifies; a token signed by a
    different key, a token bound to a different ask, a stale token
    and a stale sighting are not evidence; garbage is not observed
  - sighting first, ask second: gate-pass + bound BLE sighting
    enrolls without a tap, audit entry carries the bluetooth kind
  - ask first, sighting second: a pending ask auto-approves when
    note_ble_sighting files the bound token
  - the owner's toggle off -> the sighting pends, labelled bluetooth
  - an owner's denial is never overridden by a sighting
  - gate failure (no sign-in proof) + sighting -> refused
  - static surface: phone_ble reads TOKEN_UUID into note_ble_sighting

Run: .venv/bin/python tests/smoke/run_phone_enroll_ble_autoapprove_test.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-phone-ble-autoapprove-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
os.environ["XOD_TEST_KEYRING_FILE"] = str(TMP / "keys.json")
os.environ["XOD_FAMILY_REGISTRY_PATH"] = str(TMP / "registry.json")
os.environ["XOD_FAMILY_DEVICE_ID"] = "vector-mac"
(TMP / "data").mkdir()

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


FAMILY, ACCOUNT = "vector-family", "VectorAccount"
MAC_SIGN_SECRET, MAC_BOX_SECRET = "QEFCQ0RFRkdISUpLTE1OT1BRUlNUVVZXWFlaW1xdXl8", "YGFiY2RlZmdoaWprbG1ub3BxcnN0dXZ3eHl6e3x9fn8"
MAC_SIGNED = {
    "id": "vector-mac", "name": "Vector Mac", "platform": "macOS", "endpoint": "100.64.0.1",
    "capabilities": ["macOS hands"], "account": ACCOUNT, "status": "active",
    "last_seen": "2026-11-02T20:00:00-07:00",
    "public_key": "JUO5L_EJVRFHatyDadtt3JM2ZaEZeN2hQE7hBmypVZ0", "box_key": "Z13VdO13iTELPS52gfN5C0ZsdzsVIf7PNld5WDcepS8",
    "signature": "7zaNyOsb84oWOwHxUiwoVXRI17GIVx-zDMNNyCLkyxyE3cgng7oM-XigEB2QvivDaMr5JdiKE1-6SjXNguO5BA",
}
REG_PATH = Path(os.environ["XOD_FAMILY_REGISTRY_PATH"])

from app.services import family_registry as fr          # noqa: E402
from app.services import keychain, mail_crypto as mc    # noqa: E402

keychain.set_password(fr.FAMILY_SERVICE, "device_sign_key", MAC_SIGN_SECRET)
keychain.set_password(fr.FAMILY_SERVICE, "device_box_key", MAC_BOX_SECRET)
mac_ident = mc.Identity.from_secrets(MAC_SIGN_SECRET, MAC_BOX_SECRET)


def write_registry(data):
    REG_PATH.write_text(json.dumps(data), encoding="utf-8")


def seed_founder_registry():
    data = {"family": FAMILY, "account": ACCOUNT, "version": 1,
            "updated": "2026-11-02T20:00:00-07:00", "devices": [dict(MAC_SIGNED)]}
    write_registry(fr.sign_approval(data, mac_ident, "vector-mac"))


def sso_assertion(ident, device_id, issued_at, account=ACCOUNT, provider="github"):
    payload = {"account": account, "device_id": device_id, "issued_at": int(issued_at), "provider": provider}
    return {**payload, "signature": ident.sign(mc.canonical(payload))}


def phone_records(ident, did, name):
    base = {"id": did, "name": name, "platform": "Android", "endpoint": None,
            "capabilities": ["approvals", "phone control"], "account": ACCOUNT,
            "last_seen": "2026-11-02T20:00:00-07:00"}
    return (fr.sign_record({**base, "status": "joining"}, ident, FAMILY, ACCOUNT),
            fr.sign_record({**base, "status": "active"}, ident, FAMILY, ACCOUNT))


def advertisement(ident, did, name, sso="valid"):
    joining, active = phone_records(ident, did, name)
    issued = int(time.time())
    adv = {"v": 1, "kind": "xod-phone-advertise", "account": ACCOUNT, "provider": "github",
           "family": FAMILY, "record": joining, "record_active": active,
           "mail_address": f"{did}@test.local", "issued_at": issued}
    if sso == "valid":
        adv["sso"] = sso_assertion(ident, did, issued)
    return adv


def ble_token(ident, did, adv_issued_at, issued_at=None, nonce="test-nonce"):
    """Exactly what dev.xod.mail BlePresence mints on the phone."""
    payload = {"kind": "xod-ble-presence", "device_id": did,
               "adv_issued_at": int(adv_issued_at),
               "issued_at": int(time.time() if issued_at is None else issued_at),
               "nonce": nonce}
    return {**payload, "signature": ident.sign(mc.canonical(payload))}


def fresh_identity(a, b):
    return mc.Identity.from_secrets(mc.b64e(bytes(range(a, a + 32))), mc.b64e(bytes(range(b, b + 32))))


def main():
    from app.services import ble_presence as bp
    from app.services import phone_enroll as pe

    now = time.time()
    unit = fresh_identity(0, 32)
    pub = unit.sign_pub
    tok = ble_token(unit, "unit-phone", 1111, issued_at=now)
    check("observe: a well-formed token is recorded", bp.observe(tok, ble_address="AA:BB", now=now) is True)
    ev = bp.evidence_for("unit-phone", pub, 1111, now=now)
    check("evidence: a bound token signed by the ask's key verifies",
          ev is not None and ev.get("kind") == "bluetooth" and ev.get("source") == "AA:BB", json.dumps(ev or {}))
    other = fresh_identity(32, 64)
    check("evidence: the same sighting under a different key is not evidence",
          bp.evidence_for("unit-phone", other.sign_pub, 1111, now=now) is None)
    check("evidence: a token bound to a different ask is not evidence",
          bp.evidence_for("unit-phone", pub, 2222, now=now) is None)
    old = ble_token(unit, "unit-phone", 1111, issued_at=now - 3600)
    bp.observe(old, now=now)
    check("evidence: a stale token is not evidence",
          bp.evidence_for("unit-phone", pub, 1111, now=now) is None)
    fresh_tok = ble_token(unit, "unit-phone", 1111, issued_at=now)
    bp.observe(fresh_tok, now=now - 3600)
    check("evidence: a stale sighting is not evidence",
          bp.evidence_for("unit-phone", pub, 1111, now=now) is None)
    check("observe: garbage is refused",
          bp.observe({"kind": "xod-ble-presence", "device_id": "x"}) is False
          and bp.observe({"kind": "other"}) is False and bp.observe("nope") is False)
    bp.clear()

    # ------------------------------------------------- sighting first, ask second
    seed_founder_registry()
    auto_phone = fresh_identity(64, 96)
    adv = advertisement(auto_phone, "ble-phone", "BLE Phone")
    check("observe: the bound sighting is filed before the ask arrives",
          bp.observe(ble_token(auto_phone, "ble-phone", adv["issued_at"]), ble_address="CC:DD") is True)
    r = pe.intake(adv)
    check("auto: gate-pass + bound BLE sighting enrolls without a tap",
          r.get("ok") and r.get("state") == "enrolled" and r.get("auto_approved") is True,
          json.dumps(r))
    check("auto: registry read-back trusts the device",
          fr.trusted_member(fr.load_registry(), "ble-phone") is not None)
    check("auto: nothing is left pending", pe.pending() == [])
    audit = pe.state().get("auto_approvals") or []
    check("audit: the auto-approval is recorded with the bluetooth kind",
          len(audit) == 1 and audit[0]["device_id"] == "ble-phone"
          and audit[0]["evidence"] == "bluetooth", json.dumps(audit)[:200])

    # ------------------------------------------------- ask first, sighting second
    late_phone = fresh_identity(96, 128)
    adv2 = advertisement(late_phone, "late-phone", "Late Phone")
    r = pe.intake(adv2)
    check("pend: gate-pass with no sighting yet still pends",
          r.get("ok") and r.get("state") == "pending", json.dumps(r))
    r = pe.note_ble_sighting(ble_token(late_phone, "late-phone", adv2["issued_at"]), ble_address="EE:FF")
    check("auto: the sighting landing second takes the tap",
          r.get("ok") and r.get("state") == "enrolled" and r.get("auto_approved") is True,
          json.dumps(r))
    check("auto: the second phone is trusted on read-back and nothing pends",
          fr.trusted_member(fr.load_registry(), "late-phone") is not None and pe.pending() == [])

    # ------------------------------------------------- an unbound sighting does not approve
    stray = fresh_identity(128, 160)
    adv3 = advertisement(stray, "stray-phone", "Stray Phone")
    r = pe.intake(adv3)
    check("pend: the third ask pends", r.get("ok") and r.get("state") == "pending", json.dumps(r))
    r = pe.note_ble_sighting(ble_token(stray, "stray-phone", adv3["issued_at"] + 999))
    check("binding: a token minted for a different ask does not approve this one",
          r.get("ok") and r.get("state") == "pending"
          and fr.trusted_member(fr.load_registry(), "stray-phone") is None, json.dumps(r))
    r = pe.deny("stray-phone")
    check("deny: the owner can deny the pending ask", r.get("ok"), json.dumps(r))
    r = pe.note_ble_sighting(ble_token(stray, "stray-phone", adv3["issued_at"]))
    check("deny: a later bound sighting does not override the denial",
          fr.trusted_member(fr.load_registry(), "stray-phone") is None
          and not any(p["record"]["id"] == "stray-phone" for p in pe.pending()), json.dumps(r))

    # ------------------------------------------------- the owner's toggle
    check("toggle: the owner can switch the rule off", pe.set_auto_approve_enabled(False) is False)
    held = fresh_identity(160, 192)
    adv4 = advertisement(held, "held-phone", "Held Phone")
    bp.observe(ble_token(held, "held-phone", adv4["issued_at"]))
    r = pe.intake(adv4)
    check("toggle off: gate-pass + BLE sighting pends, labelled with its presence",
          r.get("ok") and r.get("state") == "pending"
          and any(p["record"]["id"] == "held-phone" and (p.get("presence") or {}).get("kind") == "bluetooth"
                  for p in pe.pending()), json.dumps(r))
    check("toggle: the owner can switch it back on", pe.set_auto_approve_enabled(True) is True)

    # ------------------------------------------------- gate failures refuse
    nosso = fresh_identity(192, 224)
    adv5 = advertisement(nosso, "nosso-phone", "No SSO Phone", sso=None)
    bp.observe(ble_token(nosso, "nosso-phone", adv5["issued_at"]))
    r = pe.intake(adv5)
    check("gate: a BLE sighting never rescues a missing sign-in proof",
          not r.get("ok") and r.get("state") == "refused", json.dumps(r))
    check("gate: the refusal filed nothing and trusted no one",
          fr.trusted_member(fr.load_registry(), "nosso-phone") is None
          and not any(p["record"]["id"] == "nosso-phone" for p in pe.pending()))

    # ------------------------------------------------- static surface
    ble_src = (ROOT / "app" / "services" / "phone_ble.py").read_text(encoding="utf-8")
    check("ble: phone_ble reads the token characteristic into enroll",
          "7c0d0003-7a1d-4f3e-9b5a-58f1d0c0de01" in ble_src and "note_ble_sighting" in ble_src)
    return finish()


def finish():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
