"""
Phase 1: phone enrollment into the signed family registry
(services/phone_enroll.py, phone_api family.* ops, /api/phone/enroll/*
endpoints, Phones panel SSO-first section).

The phone signs in with the family account (GitHub, via rdrive) and
advertises itself with a self-signed family record; one tap on the
desktop (or a trusted phone's signed approval) enrolls it as a full
member of the signed registry (services/family_registry.py). The QR
pair-offer remains as the labelled offline fallback.

Mechanics, per the shipped V0 format: a record's self-signature
covers its status, and trust comes from the approver's signature over
the member digest, so the advertisement carries two phone-signed
variants of the record — "joining" (the intent the panel displays)
and "active" (filed byte-identical on approval). Revocation is a
signed revocation entry; the record itself is never edited.

Covered here:
  - a valid signed advertisement pends; tampered, wrong-account and
    impersonating advertisements are refused with reasons
  - the safety number matches the pinned cross-language vector
  - desktop approve files the active record and the read-back trusts
    it; approving twice does not duplicate it
  - control gates: a paired phone is allowed, and after a desktop
    revoke the same address is refused (phone_api.allowed + the
    enrollment gate agree)
  - re-enroll after revoke with a rotated key replaces the record and
    clears the revocation, read back trusted under the new key; the
    SAME key after revoke is refused (rotate-first rule)
  - phone-as-approver: a joining desktop is enrolled by the phone's
    signature over the exact candidate member digest; a forged or
    non-member approval is refused and changes nothing
  - the pinned approval-signature vector verifies (the xod-android
    FamilyTest reproduces this signature byte-for-byte in Kotlin)
  - deny suppresses the same advertisement; a newer one re-pends
  - phone_api exposes family.advertise / family.state / family.approve
  - the panel leads with the family section and labels the QR flow
    "Offline fallback" (static checks, the house pattern)

Run: .venv/bin/python tests/smoke/run_phone_enroll_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-phone-enroll-"))
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


# ------------------------------------------------------------ pinned vectors
# Generated once from the Python reference (services/mail_crypto.py +
# services/family_registry.py) with fixed secrets; the Kotlin suite
# (xod-android FamilyTest) pins the same values and must reproduce the
# approval signature byte-for-byte.
FAMILY, ACCOUNT = "vector-family", "VectorAccount"
PHONE_SIGN_SECRET, PHONE_BOX_SECRET = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8", "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8"
MAC_SIGN_SECRET, MAC_BOX_SECRET = "QEFCQ0RFRkdISUpLTE1OT1BRUlNUVVZXWFlaW1xdXl8", "YGFiY2RlZmdoaWprbG1ub3BxcnN0dXZ3eHl6e3x9fn8"
PHONE_PUB, PHONE_BOX = "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg", "NYBy1jZYgNGu6jKa35EhODhR7SGijjt16WXQ0s0WYlQ"
SAFETY = "41726 22047 16319 49803 09537 95061"
_PHONE_BASE = {
    "id": "vector-phone", "name": "Vector Phone", "platform": "Android", "endpoint": None,
    "capabilities": ["approvals", "phone control"], "account": ACCOUNT,
    "last_seen": "2026-11-02T20:00:00-07:00", "public_key": PHONE_PUB, "box_key": PHONE_BOX,
}
PHONE_SIGNED = {**_PHONE_BASE, "status": "joining",
                "signature": "6GPVDuEqMAsb2oUUZfp3Rd3td7ayha0F_A11mofo8cPPY0m4h4T_zb_271GF2XaRpf9VAGg5MjZeXoIQYiUzBg"}
PHONE_ACTIVE = {**_PHONE_BASE, "status": "active",
                "signature": "x17heKGkdcgznqB0KVdpFMZxbtbP7Y3afAXq5I91lGRP2qxVC1bAnQIVa6nLRSR1AWdpPViq8W0SK1oHNgSvCw"}
MAC_SIGNED = {
    "id": "vector-mac", "name": "Vector Mac", "platform": "macOS", "endpoint": "100.64.0.1",
    "capabilities": ["macOS hands"], "account": ACCOUNT, "status": "active",
    "last_seen": "2026-11-02T20:00:00-07:00",
    "public_key": "JUO5L_EJVRFHatyDadtt3JM2ZaEZeN2hQE7hBmypVZ0", "box_key": "Z13VdO13iTELPS52gfN5C0ZsdzsVIf7PNld5WDcepS8",
    "signature": "7zaNyOsb84oWOwHxUiwoVXRI17GIVx-zDMNNyCLkyxyE3cgng7oM-XigEB2QvivDaMr5JdiKE1-6SjXNguO5BA",
}
PINNED_DIGEST = {
    "family": FAMILY, "account": ACCOUNT,
    "devices": [
        {"id": "vector-mac", "public_key": MAC_SIGNED["public_key"], "status": "active"},
        {"id": "vector-phone", "public_key": PHONE_PUB, "status": "active"},
    ],
}
PINNED_APPROVAL_SIG = "nJHmjWkpz9rZ8INiqjBK3IhiZFuVb5n_A125mkQx4vkhFHcTrKQZ0MrWEac0hrztqHwyzb8a0fLkjy6MlegRAw"
MAIL = "vectorphone@test.local"
REG_PATH = Path(os.environ["XOD_FAMILY_REGISTRY_PATH"])

from app.services import family_registry as fr          # noqa: E402
from app.services import keychain, mail_crypto as mc    # noqa: E402

# This desktop IS the vector mac: seed its family identity into the test keyring.
keychain.set_password(fr.FAMILY_SERVICE, "device_sign_key", MAC_SIGN_SECRET)
keychain.set_password(fr.FAMILY_SERVICE, "device_box_key", MAC_BOX_SECRET)
mac_ident = mc.Identity.from_secrets(MAC_SIGN_SECRET, MAC_BOX_SECRET)
phone_ident = mc.Identity.from_secrets(PHONE_SIGN_SECRET, PHONE_BOX_SECRET)


def write_registry(data):
    REG_PATH.write_text(json.dumps(data), encoding="utf-8")


def seed_founder_registry():
    data = {"family": FAMILY, "account": ACCOUNT, "version": 1,
            "updated": "2026-11-02T20:00:00-07:00", "devices": [dict(MAC_SIGNED)]}
    write_registry(fr.sign_approval(data, mac_ident, "vector-mac"))


def sso_assertion(ident, device_id, issued_at, account=ACCOUNT, provider="github"):
    """The phone's signed sign-in proof (standing rule, 2026-11-02):
    the family key attests a live GitHub session for the family account."""
    payload = {"account": account, "device_id": device_id, "issued_at": int(issued_at), "provider": provider}
    return {**payload, "signature": ident.sign(mc.canonical(payload))}


def advertisement(record, record_active, issued_at=None, account=ACCOUNT, family=FAMILY, mail=MAIL,
                  ident=None, sso="valid", sso_provider="github", sso_issued_at=None):
    issued = int(issued_at if issued_at is not None else time.time())
    adv = {"v": 1, "kind": "xod-phone-advertise", "account": account, "provider": "github",
           "family": family, "record": record, "record_active": record_active,
           "mail_address": mail, "issued_at": issued}
    if sso == "valid":
        adv["sso"] = sso_assertion(ident or phone_ident, record["id"],
                                   sso_issued_at if sso_issued_at is not None else issued,
                                   account=account, provider=sso_provider)
    elif sso == "wrongkey":
        adv["sso"] = sso_assertion(mac_ident, record["id"], issued, account=account, provider=sso_provider)
    return adv


def fresh_identity(a, b):
    return mc.Identity.from_secrets(mc.b64e(bytes(range(a, a + 32))), mc.b64e(bytes(range(b, b + 32))))


def phone_records(ident, did="vector-phone", name="Vector Phone", last_seen="2026-11-02T20:00:00-07:00"):
    base = {"id": did, "name": name, "platform": "Android", "endpoint": None,
            "capabilities": ["approvals", "phone control"], "account": ACCOUNT,
            "last_seen": last_seen}
    return (fr.sign_record({**base, "status": "joining"}, ident, FAMILY, ACCOUNT),
            fr.sign_record({**base, "status": "active"}, ident, FAMILY, ACCOUNT))


def main():
    seed_founder_registry()
    view = fr.load_registry()
    check("seed: founder mac is a trusted member",
          fr.trusted_member(view, "vector-mac") is not None and view["approval_verified"])

    try:
        from app.services import phone_enroll as pe
    except Exception as e:  # failing-first: the module does not exist on main
        check("services/phone_enroll.py exists", False, f"{type(e).__name__}: {e}")
        return finish()

    # ------------------------------------------------------------ intake
    st = pe.state()
    check("state: honest empty pending, account from the registry",
          st["account"] == ACCOUNT and st["pending"] == [] and st["my_trusted"] is True)

    r = pe.intake(advertisement(dict(PHONE_SIGNED), dict(PHONE_ACTIVE)))
    check("intake: valid signed advertisement pends", r.get("ok") and r.get("state") == "pending", json.dumps(r))
    pend = pe.pending()
    check("pending: the phone is listed with its safety number",
          len(pend) == 1 and pend[0]["record"]["id"] == "vector-phone" and pend[0]["safety"] == SAFETY,
          json.dumps(pend)[:200])

    r = pe.intake(advertisement(dict(PHONE_SIGNED), dict(PHONE_ACTIVE)))
    check("intake: the same advertisement again refreshes, never duplicates",
          r.get("ok") and len(pe.pending()) == 1)

    tampered = dict(PHONE_SIGNED, capabilities=["approvals", "phone control", "silent key export"])
    r = pe.intake(advertisement(tampered, dict(PHONE_ACTIVE)))
    check("intake: a record edited after signing is refused (signature)",
          not r.get("ok") and any("signature" in p for p in r.get("problems", [])), json.dumps(r))
    tampered_active = dict(PHONE_ACTIVE, endpoint="https://evil.example")
    r = pe.intake(advertisement(dict(PHONE_SIGNED), tampered_active))
    check("intake: an edited active variant is refused (signature)",
          not r.get("ok") and any("signature" in p for p in r.get("problems", [])), json.dumps(r))
    swapped = dict(PHONE_ACTIVE, public_key=MAC_SIGNED["public_key"], box_key=MAC_SIGNED["box_key"])
    r = pe.intake(advertisement(dict(PHONE_SIGNED), swapped))
    check("intake: an active variant that is not the joining record's twin is refused",
          not r.get("ok"), json.dumps(r))
    check("intake: the refusals changed nothing", len(pe.pending()) == 1
          and pe.pending()[0]["record"]["capabilities"] == ["approvals", "phone control"])

    r = pe.intake(advertisement(dict(PHONE_SIGNED), dict(PHONE_ACTIVE), account="SomeoneElse"))
    check("intake: an advertisement bound to another account is refused",
          not r.get("ok") and any("account" in p for p in r.get("problems", [])), json.dumps(r))

    r = pe.intake(advertisement(dict(PHONE_SIGNED, id="vector-mac"), dict(PHONE_ACTIVE, id="vector-mac")))
    check("intake: claiming a member's id under a different key is refused (impersonation)",
          not r.get("ok"), json.dumps(r))

    r = pe.intake(advertisement(dict(PHONE_SIGNED), dict(PHONE_ACTIVE), mail="other@test.local"),
                  mail_sender=MAIL)
    check("intake: over the mail channel the sender must be the advertised address",
          not r.get("ok"), json.dumps(r))

    stale = advertisement(dict(PHONE_SIGNED), dict(PHONE_ACTIVE), issued_at=time.time() - 3 * 3600)
    r = pe.intake(stale)
    check("intake: a stale advertisement is refused (replay window)", not r.get("ok"), json.dumps(r))

    # -------------------------------- standing rule (2026-11-02): pair only to a
    # backend in the signed family network, with a proper GitHub sign-in.
    r = pe.intake(advertisement(dict(PHONE_SIGNED), dict(PHONE_ACTIVE), sso=None))
    check("sso: an advertisement with no sign-in proof is refused",
          not r.get("ok") and any("sign-in" in p for p in r.get("problems", [])), json.dumps(r))
    r = pe.intake(advertisement(dict(PHONE_SIGNED), dict(PHONE_ACTIVE), sso="wrongkey"))
    check("sso: a sign-in proof signed by another key is refused",
          not r.get("ok") and any("sign-in" in p for p in r.get("problems", [])), json.dumps(r))
    r = pe.intake(advertisement(dict(PHONE_SIGNED), dict(PHONE_ACTIVE), sso_provider="google"))
    check("sso: a non-GitHub session is refused (GitHub is the family sign-in)",
          not r.get("ok") and any("GitHub" in p for p in r.get("problems", [])), json.dumps(r))
    bad_acct = advertisement(dict(PHONE_SIGNED), dict(PHONE_ACTIVE))
    bad_acct["sso"] = sso_assertion(phone_ident, "vector-phone", int(time.time()), account="SomeoneElse")
    r = pe.intake(bad_acct)
    check("sso: a session for a different account than the family's is refused",
          not r.get("ok") and any("account" in p for p in r.get("problems", [])), json.dumps(r))
    r = pe.intake(advertisement(dict(PHONE_SIGNED), dict(PHONE_ACTIVE),
                                sso_issued_at=int(time.time()) - 3 * 3600))
    check("sso: a stale sign-in proof is refused (sign in again)",
          not r.get("ok") and any("sign-in" in p for p in r.get("problems", [])), json.dumps(r))
    check("sso: the refusals left the good pending advertisement untouched",
          len(pe.pending()) == 1)

    # ------------------------------------------------------------ desktop approve
    r = pe.approve("vector-phone")
    check("approve: one tap enrolls the phone", r.get("ok"), json.dumps(r))
    view = fr.load_registry()
    member = fr.trusted_member(view, "vector-phone")
    raw = json.loads(REG_PATH.read_text())
    filed = next(d for d in raw["devices"] if d["id"] == "vector-phone")
    check("approve: the filed record is the phone's own active variant, byte-identical",
          filed == PHONE_ACTIVE, json.dumps(filed)[:160])
    check("approve: read-back trusts the phone (signed record + approval covers it)",
          member is not None and member["public_key"] == PHONE_PUB and view["approved_by"] == "vector-mac")
    r = pe.approve("vector-phone")
    check("approve: approving an enrolled phone is a no-op, not a duplicate",
          r.get("ok") and r.get("state") == "already_enrolled"
          and sum(1 for d in json.loads(REG_PATH.read_text())["devices"] if d["id"] == "vector-phone") == 1,
          json.dumps(r))
    check("approve: the pending list is empty afterwards", pe.pending() == [])

    # ------------------------------------------------------------ control gate + revoke
    from app.services import agent_mail, phone_api, phone_mcp
    agent_mail._pin(MAIL, PHONE_PUB, PHONE_BOX, "mine")
    agent_mail.set_contact(MAIL, trust="mine")
    paired = phone_mcp.pair_phone(MAIL, "Vector Phone")
    check("gate: the enrolled phone pairs for control from its pinned contact",
          paired.get("success") is True, json.dumps(paired)[:160])
    check("gate: a paired, enrolled phone is allowed", phone_api.allowed(MAIL) is True)
    check("gate: enrollment reports no refusal before any revoke", pe.control_refused(MAIL) is False)

    r = pe.revoke("vector-phone")
    check("revoke: the desktop revokes the phone", r.get("ok"), json.dumps(r))
    view = fr.load_registry()
    rec = next((d for d in view["devices"] if d["id"] == "vector-phone"), None)
    check("revoke: read-back shows revoked and untrusted",
          rec is not None and rec["status"] == "revoked" and fr.trusted_member(view, "vector-phone") is None)
    check("gate: after revoke the enrollment gate refuses the address", pe.control_refused(MAIL) is True)
    check("gate: after revoke phone_api.allowed refuses the phone", phone_api.allowed(MAIL) is False)

    r = pe.intake(advertisement(dict(PHONE_SIGNED), dict(PHONE_ACTIVE)))
    check("revoke: the SAME key cannot re-enroll after revoke (rotate first)",
          not r.get("ok") and any("rotat" in p for p in r.get("problems", [])), json.dumps(r))

    # ------------------------------------------------------------ re-enroll with a rotated key
    phone2 = fresh_identity(128, 160)
    rec2_joining, rec2_active = phone_records(phone2, last_seen="2026-11-02T21:00:00-07:00")
    r = pe.intake(advertisement(rec2_joining, rec2_active, ident=phone2))
    check("re-enroll: a rotated key may advertise for a revoked id", r.get("ok"), json.dumps(r))
    r = pe.approve("vector-phone")
    view = fr.load_registry()
    member = fr.trusted_member(view, "vector-phone")
    raw = json.loads(REG_PATH.read_text())
    check("re-enroll: trusted again under the NEW key, revocation cleared in the approved write",
          r.get("ok") and member is not None and member["public_key"] == phone2.sign_pub
          and not any(e.get("device_id") == "vector-phone" for e in raw.get("revocations", [])), json.dumps(r))
    check("gate: control is allowed again after re-enroll",
          pe.control_refused(MAIL) is False and phone_api.allowed(MAIL) is True)

    # ------------------------------------------------------------ phone as approver
    razer = fresh_identity(192, 224)
    razer_rec = fr.sign_record({
        "id": "vector-razer", "name": "Vector Razer", "platform": "Windows 11", "endpoint": None,
        "capabilities": ["Windows hands"], "account": ACCOUNT, "status": "active",
        "last_seen": "2026-11-02T21:05:00-07:00"}, razer, FAMILY, ACCOUNT)
    raw = json.loads(REG_PATH.read_text())
    raw["devices"].append(razer_rec)
    write_registry(raw)

    cands = pe.join_candidates()
    check("approver: the joining desktop is offered to the phone with the set digest",
          len(cands) == 1 and cands[0]["record"]["id"] == "vector-razer" and "devices" in cands[0]["digest"],
          json.dumps(cands)[:200])
    sig = phone2.sign(mc.canonical(cands[0]["digest"]))
    r = pe.apply_phone_approval("vector-razer", sig, "vector-phone")
    view = fr.load_registry()
    check("approver: the phone's signed approval enrolls the desktop, read back trusted",
          r.get("ok") and fr.trusted_member(view, "vector-razer") is not None
          and view["approved_by"] == "vector-phone", json.dumps(r))

    other = fresh_identity(32, 64)  # not any member
    other_rec = fr.sign_record({
        "id": "vector-other", "name": "Vector Other", "platform": "Linux", "endpoint": None,
        "capabilities": ["shell"], "account": ACCOUNT, "status": "active",
        "last_seen": "2026-11-02T21:10:00-07:00"}, other, FAMILY, ACCOUNT)
    raw = json.loads(REG_PATH.read_text())
    raw["devices"].append(other_rec)
    write_registry(raw)
    cand = next(c for c in pe.join_candidates() if c["record"]["id"] == "vector-other")
    forged = mac_ident.sign(mc.canonical(cand["digest"]))  # a real signature, the wrong signer
    r = pe.apply_phone_approval("vector-other", forged, "vector-phone")
    view = fr.load_registry()
    check("approver: a signature from anyone but the named approver is refused",
          not r.get("ok") and fr.trusted_member(view, "vector-other") is None, json.dumps(r))
    stranger_sig = other.sign(mc.canonical(cand["digest"]))
    r = pe.apply_phone_approval("vector-other", stranger_sig, "vector-other")
    check("approver: a device cannot approve itself into the family", not r.get("ok"), json.dumps(r))

    # ------------------------------------------------------------ pinned vector (Kotlin mirror)
    cand_digest = fr.member_digest({"family": FAMILY, "account": ACCOUNT,
                                    "devices": [dict(MAC_SIGNED), dict(PHONE_ACTIVE)]})
    check("vector: the candidate digest matches the pinned digest",
          cand_digest == PINNED_DIGEST, json.dumps(cand_digest))
    check("vector: the pinned approval signature verifies against the phone key",
          mc.verify(PHONE_PUB, mc.canonical(PINNED_DIGEST), PINNED_APPROVAL_SIG))

    # ------------------------------------------------------------ deny
    import hashlib
    phone3 = mc.Identity.from_secrets(
        mc.b64e(hashlib.sha256(b"vector-phone3-sign").digest()),
        mc.b64e(hashlib.sha256(b"vector-phone3-box").digest()))
    join3, active3 = phone_records(phone3, did="vector-other-phone", name="Vector Other Phone",
                                   last_seen="2026-11-02T21:15:00-07:00")
    r = pe.intake(advertisement(join3, active3, ident=phone3))
    check("deny: a second phone may pend",
          r.get("ok") and any(p["record"]["id"] == "vector-other-phone" for p in pe.pending()), json.dumps(r))
    r = pe.deny("vector-other-phone")
    check("deny: denying removes the pending advertisement",
          r.get("ok") and not any(p["record"]["id"] == "vector-other-phone" for p in pe.pending()), json.dumps(r))
    r = pe.intake(advertisement(join3, active3, ident=phone3))
    check("deny: the same advertisement does not re-pend after a denial",
          r.get("state") == "denied"
          and not any(p["record"]["id"] == "vector-other-phone" for p in pe.pending()), json.dumps(r))
    time.sleep(1.1)  # a genuinely later ask: its issued_at is a second newer
    r = pe.intake(advertisement(join3, active3, ident=phone3))
    check("deny: a NEWER advertisement from the same phone re-pends (the phone asked again)",
          r.get("ok") and any(p["record"]["id"] == "vector-other-phone" for p in pe.pending()), json.dumps(r))

    # ------------------------------------------------------------ phone_api surface
    check("api: family ops are answered for a pinned contact (pairing is what they establish)",
          phone_api.family_allowed(MAIL) is True
          and phone_api.family_allowed("stranger@test.local") is False)
    check("api: family.* ops are registered",
          all(op in phone_api.OPS for op in ("family.advertise", "family.state", "family.approve")))
    out = asyncio.run(phone_api.run("family.state", {}))
    check("api: family.state answers with the account and the pending phone",
          out.get("ok") and out["data"]["account"] == ACCOUNT
          and any(p["record"]["id"] == "vector-other-phone" for p in out["data"]["pending"]),
          json.dumps(out)[:200])
    reg = (out.get("data") or {}).get("registry") or {}
    check("api: family.state carries the raw registry so the phone verifies membership itself",
          reg.get("approved_by") == "vector-phone" and bool(reg.get("approval_signature"))
          and len(reg.get("devices") or []) >= 3, json.dumps(reg)[:160])

    # ------------------------------------------------------------ static surface checks
    main_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    check("http: the enrollment endpoints exist",
          all(s in main_src for s in ("/api/phone/enroll/state", "/api/phone/enroll/advertise",
                                      "/api/phone/enroll/approve", "/api/phone/enroll/deny",
                                      "/api/phone/enroll/revoke")))
    panel = (ROOT / "app" / "static" / "js" / "phone.js").read_text(encoding="utf-8")
    check("panel: a family section leads and the QR flow is labelled the offline fallback",
          "familyView" in panel and "Offline fallback" in panel)
    return finish()


def finish():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
