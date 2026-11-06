"""
Pairing auto-approve for local presence (the owner's rule, 2026-11-05;
services/phone_enroll.py + services/local_presence.py, HTTP endpoint
/api/phone/enroll/advertise).

The rule: "if device is on Bluetooth or same wifi and it sees it on
the list in xod it can just approve it." An enrollment advertisement
that has passed the FULL pairing gate (signed record, family/account
binding, the signed GitHub sign-in assertion) is approved without
the owner's tap when the transport evidenced local presence —
observed by the receiving Xod from the request's source address,
never claimed by the advertisement. No presence (the Agent Mail
channel, tailnet, off-LAN) pends exactly as before, and a gate
failure refuses exactly as before, presence or not.

Covered here:
  - classifier: loopback and same-subnet RFC1918 are evidence;
    tailnet CGNAT, link-local, public, off-subnet and garbage are not
  - gate-pass + same-Wi-Fi presence -> auto-enrolled, registry
    read-back trusts the device, the pending list is empty, and an
    audit entry (evidence + registry state before/after) is written
    and surfaced in state()
  - gate-pass + NO presence -> still pends for the tap
  - gate-fail (no sign-in proof, wrong-account session) + presence
    -> refused, not approved, nothing filed
  - the owner's toggle off -> presence pends; back on -> a fresh ask
    auto-approves
  - an owner's denial is never overridden by presence
  - static surface: the HTTP endpoint classifies the request source,
    the toggle endpoint exists, the panel renders the audit trail

Run: .venv/bin/python tests/smoke/run_phone_enroll_autoapprove_test.py
"""
import ipaddress
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-phone-autoapprove-"))
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
WIFI = {"kind": "same_wifi", "source": "192.168.1.50",
        "detail": "the request arrived from 192.168.1.50, on this computer's local network 192.168.1.0/24"}

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


def advertisement(ident, did, name, sso="valid", sso_account=ACCOUNT):
    joining, active = phone_records(ident, did, name)
    issued = int(time.time())
    adv = {"v": 1, "kind": "xod-phone-advertise", "account": ACCOUNT, "provider": "github",
           "family": FAMILY, "record": joining, "record_active": active,
           "mail_address": f"{did}@test.local", "issued_at": issued}
    if sso == "valid":
        adv["sso"] = sso_assertion(ident, did, issued, account=sso_account)
    return adv


def fresh_identity(a, b):
    return mc.Identity.from_secrets(mc.b64e(bytes(range(a, a + 32))), mc.b64e(bytes(range(b, b + 32))))


def main():
    from app.services import local_presence as lp
    from app.services import phone_enroll as pe

    # ------------------------------------------------------------ classifier
    nets = [ipaddress.ip_network("192.168.1.0/24"), ipaddress.ip_network("100.64.0.0/10")]
    check("classifier: loopback is local presence (its own kind)",
          (lp.classify("127.0.0.1", networks=nets) or {}).get("kind") == "loopback")
    check("classifier: a same-subnet Wi-Fi address is evidence",
          (lp.classify("192.168.1.50", networks=nets) or {}).get("kind") == "same_wifi")
    for ip, why in [("100.64.0.5", "tailnet CGNAT is reachable, not local"),
                    ("192.168.2.50", "private but a different subnet"),
                    ("8.8.8.8", "public"), ("169.254.1.1", "link-local"),
                    ("garbage", "unparseable"), (None, "missing")]:
        check(f"classifier: {ip} is not evidence ({why})", lp.classify(ip, networks=nets) is None)

    # ------------------------------------------------------------ the happy path
    seed_founder_registry()
    check("rule: auto-approve defaults ON (the owner's standing order)",
          pe.auto_approve_enabled() is True and pe.state()["auto_approve_local"] is True)

    auto_phone = fresh_identity(0, 32)
    r = pe.intake(advertisement(auto_phone, "auto-phone", "Auto Phone"), presence=dict(WIFI))
    check("auto: gate-pass + same-Wi-Fi presence enrolls without a tap",
          r.get("ok") and r.get("state") == "enrolled" and r.get("auto_approved") is True,
          json.dumps(r))
    view = fr.load_registry()
    check("auto: registry read-back trusts the device",
          fr.trusted_member(view, "auto-phone") is not None)
    check("auto: nothing is left pending", pe.pending() == [])
    st = pe.state()
    audit = st.get("auto_approvals") or []
    check("audit: the auto-approval is in state() for the panel, newest first",
          len(audit) == 1 and audit[0]["device_id"] == "auto-phone"
          and audit[0]["evidence"] == "same_wifi" and audit[0]["source"] == "192.168.1.50",
          json.dumps(audit)[:200])
    check("audit: registry state before/after is recorded",
          audit and audit[0]["before"]["device_status"] is None
          and audit[0]["after"]["device_status"] == "active"
          and audit[0]["after"]["device_trusted"] is True, json.dumps(audit)[:200])

    # ------------------------------------------------------------ no presence pends
    quiet_phone = fresh_identity(64, 96)
    r = pe.intake(advertisement(quiet_phone, "quiet-phone", "Quiet Phone"))
    check("pend: gate-pass with NO presence still pends for the tap",
          r.get("ok") and r.get("state") == "pending", json.dumps(r))
    check("pend: the ask is on the list, with no presence attached",
          any(p["record"]["id"] == "quiet-phone" and not p.get("presence") for p in pe.pending()))
    check("pend: the device is not trusted", fr.trusted_member(fr.load_registry(), "quiet-phone") is None)

    # ------------------------------------------------------------ gate failures refuse
    no_sso = fresh_identity(96, 128)
    r = pe.intake(advertisement(no_sso, "nosso-phone", "No SSO Phone", sso=None), presence=dict(WIFI))
    check("gate: presence never rescues a missing sign-in proof",
          not r.get("ok") and r.get("state") == "refused", json.dumps(r))
    wrong_acct = fresh_identity(128, 160)
    r = pe.intake(advertisement(wrong_acct, "wrongacct-phone", "Wrong Account Phone",
                                sso_account="SomeoneElse"), presence=dict(WIFI))
    check("gate: presence never rescues a session for a different account",
          not r.get("ok"), json.dumps(r))
    view = fr.load_registry()
    check("gate: the refusals filed nothing and trusted no one",
          fr.trusted_member(view, "nosso-phone") is None
          and fr.trusted_member(view, "wrongacct-phone") is None
          and not any(p["record"]["id"] in ("nosso-phone", "wrongacct-phone") for p in pe.pending()))

    # ------------------------------------------------------------ the owner's toggle
    check("toggle: the owner can switch the rule off", pe.set_auto_approve_enabled(False) is False)
    held_phone = fresh_identity(160, 192)
    held_adv = advertisement(held_phone, "held-phone", "Held Phone")
    r = pe.intake(held_adv, presence=dict(WIFI))
    check("toggle off: gate-pass + presence pends, labelled with its presence",
          r.get("ok") and r.get("state") == "pending"
          and any(p["record"]["id"] == "held-phone" and (p.get("presence") or {}).get("kind") == "same_wifi"
                  for p in pe.pending()), json.dumps(r))
    check("toggle: the owner can switch it back on", pe.set_auto_approve_enabled(True) is True)
    r = pe.intake(held_adv, presence=dict(WIFI))
    check("toggle on: the same phone asking again is auto-approved",
          r.get("ok") and r.get("state") == "enrolled" and r.get("auto_approved") is True,
          json.dumps(r))

    # ------------------------------------------------------------ denial is final vs the rule
    r = pe.deny("quiet-phone")
    check("deny: the owner can deny the pending ask", r.get("ok"), json.dumps(r))
    r = pe.intake(advertisement(quiet_phone, "quiet-phone", "Quiet Phone"), presence=dict(WIFI))
    check("deny: presence does not override an owner's denial",
          r.get("state") == "denied"
          and fr.trusted_member(fr.load_registry(), "quiet-phone") is None, json.dumps(r))

    # ------------------------------------------------------------ static surface
    main_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    check("http: the advertise endpoint classifies the request source",
          "local_presence.classify" in main_src and "presence=presence" in main_src)
    check("http: the toggle endpoint exists", "/api/phone/enroll/auto-approve" in main_src)
    panel = (ROOT / "app" / "static" / "js" / "phone.js").read_text(encoding="utf-8")
    check("panel: the family view renders the auto-approval audit trail",
          "auto_approvals" in panel and "Auto-approved" in panel)
    return finish()


def finish():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
