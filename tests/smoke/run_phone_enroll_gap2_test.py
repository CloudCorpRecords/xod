"""
Pairing Gap 2 (2026-11-09): the phone's enrollment ask over HTTP to the
computer's own post office (services/post_office.py /v1/enroll/advertise,
wired by services/home_post.py's enroll_intake).

The gap this closes: the companion sent its gated family-enrollment ask
only over Agent Mail (family.advertise). Mail carries no local presence,
so the owner's same-Wi-Fi auto-approve (2026-11-05 rule) could never fire
for a real phone — every ask pended for a desktop tap. The desktop's main
backend cannot be the HTTP channel: it deliberately answers only on
localhost (its local_only_guard). The surface a phone can actually reach
is the computer's own post office (:8790, every interface), so the ask
arrives there instead and goes through the SAME intake as mail — one
gate (services/phone_enroll.py), two transports:

  - gate-pass ask from a same-Wi-Fi source -> auto-enrolled, audit entry
    written with the presence evidence and surfaced in state()
  - gate-pass ask from a loopback source -> auto-enrolled (loopback kind)
  - gate-pass ask from a public or tailnet source -> NO presence, pends
    exactly like the mail channel
  - non-member ask (a different family's advertisement) -> refused
  - member ask with no sign-in proof (or a wrong-account one) -> refused,
    presence or not, nothing filed
  - a post office built without an intake (a shared relay) has no such
    route at all (404), and says so in its about

Presence is classified from the request's OBSERVED source address
(Starlette TestClient's per-client address here stands in for the wire),
never from anything the advertisement claims. The classifier's network
list is pinned for determinism; classifier behaviour itself is covered
by run_phone_enroll_autoapprove_test.py.

Run: .venv/bin/python tests/smoke/run_phone_enroll_gap2_test.py
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
TMP = Path(tempfile.mkdtemp(prefix="xod-phone-gap2-"))
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

# The founder Mac's fixed test identity is shared with the enroll suites:
# extract its secret literals from the auto-approve suite's source (that
# module runs its own suite at import, so it cannot simply be imported).
import re  # noqa: E402
_src = (ROOT / "tests" / "smoke" / "run_phone_enroll_autoapprove_test.py").read_text(encoding="utf-8")
_m = re.search(r'MAC_SIGN_SECRET, MAC_BOX_SECRET = "([^"]+)", "([^"]+)"', _src)
MAC_SIGN_SECRET, MAC_BOX_SECRET = _m.group(1), _m.group(2)

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


def phone_records(ident, did, name, family=FAMILY, account=ACCOUNT):
    base = {"id": did, "name": name, "platform": "Android", "endpoint": None,
            "capabilities": ["approvals", "phone control"], "account": account,
            "last_seen": "2026-11-02T20:00:00-07:00"}
    return (fr.sign_record({**base, "status": "joining"}, ident, family, account),
            fr.sign_record({**base, "status": "active"}, ident, family, account))


def advertisement(ident, did, name, sso="valid", sso_account=ACCOUNT, family=FAMILY, account=ACCOUNT):
    joining, active = phone_records(ident, did, name, family=family, account=account)
    issued = int(time.time())
    adv = {"v": 1, "kind": "xod-phone-advertise", "account": account, "provider": "github",
           "family": family, "record": joining, "record_active": active,
           "mail_address": f"{did}@test.local", "issued_at": issued}
    if sso == "valid":
        adv["sso"] = sso_assertion(ident, did, issued, account=sso_account)
    return adv


def fresh_identity(a, b):
    return mc.Identity.from_secrets(mc.b64e(bytes(range(a, a + 32))), mc.b64e(bytes(range(b, b + 32))))


def main():
    from fastapi.testclient import TestClient
    from app.services import home_post, local_presence as lp, phone_enroll as pe
    from app.services.post_office import make_app

    # Deterministic presence: this host's networks are pinned to one LAN.
    lp.local_networks = lambda: [ipaddress.ip_network("192.168.1.0/24")]

    seed_founder_registry()
    check("rule: auto-approve defaults ON", pe.auto_approve_enabled() is True)

    po = make_app(str(TMP / "po.db"), lambda: "vector.local:8790",
                  enroll_intake=home_post.enroll_intake)
    lan = TestClient(po, client=("192.168.1.50", 50000))
    loop = TestClient(po, client=("127.0.0.1", 50000))
    remote = TestClient(po, client=("203.0.113.9", 50000))
    tail = TestClient(po, client=("100.64.0.5", 50000))

    about = lan.get("/.well-known/xod-relay").json()
    check("surface: a computer's own post office offers the enroll route",
          about.get("enroll_advertise") is True, json.dumps(about))

    # ---------------------------------------------------------- LAN auto-approves
    wifi_phone = fresh_identity(0, 32)
    r = lan.post("/v1/enroll/advertise",
                 json={"advertisement": advertisement(wifi_phone, "wifi-phone", "Wi-Fi Phone")})
    body = r.json()
    check("lan: gate-pass ask from the same Wi-Fi enrolls without a tap",
          r.status_code == 200 and body.get("ok") and body.get("state") == "enrolled"
          and body.get("auto_approved") is True, r.text[:300])
    check("lan: the presence evidence rode along (same_wifi, observed source)",
          (body.get("presence") or {}).get("kind") == "same_wifi"
          and (body.get("presence") or {}).get("source") == "192.168.1.50", json.dumps(body)[:300])
    view = fr.load_registry()
    check("lan: registry read-back trusts the device",
          fr.trusted_member(view, "wifi-phone") is not None)
    check("lan: nothing is left pending", pe.pending() == [])
    audit = pe.state().get("auto_approvals") or []
    check("lan: the audit entry is written and surfaced, evidence attached",
          len(audit) == 1 and audit[0]["device_id"] == "wifi-phone"
          and audit[0]["evidence"] == "same_wifi" and audit[0]["source"] == "192.168.1.50",
          json.dumps(audit)[:200])

    # ---------------------------------------------------------- loopback auto-approves
    loop_phone = fresh_identity(32, 64)
    r = loop.post("/v1/enroll/advertise",
                  json={"advertisement": advertisement(loop_phone, "loop-phone", "Loop Phone")})
    body = r.json()
    check("loopback: gate-pass ask from this same computer enrolls (its own kind)",
          r.status_code == 200 and body.get("state") == "enrolled"
          and (body.get("presence") or {}).get("kind") == "loopback", r.text[:300])

    # ---------------------------------------------------------- remote pends like mail
    far_phone = fresh_identity(64, 96)
    r = remote.post("/v1/enroll/advertise",
                    json={"advertisement": advertisement(far_phone, "far-phone", "Far Phone")})
    body = r.json()
    check("remote: a public-source ask does NOT auto-approve — it pends",
          r.status_code == 200 and body.get("ok") and body.get("state") == "pending"
          and not body.get("auto_approved"), r.text[:300])
    check("remote: the ask is pending with no presence attached",
          any(p["record"]["id"] == "far-phone" and not p.get("presence") for p in pe.pending()))
    check("remote: the device is not trusted",
          fr.trusted_member(fr.load_registry(), "far-phone") is None)

    tail_phone = fresh_identity(96, 128)
    r = tail.post("/v1/enroll/advertise",
                  json={"advertisement": advertisement(tail_phone, "tail-phone", "Tail Phone")})
    body = r.json()
    check("tailnet: a tailnet-source ask pends too (reachable is not local)",
          r.status_code == 200 and body.get("state") == "pending", r.text[:300])

    # ---------------------------------------------------------- the gate still refuses
    stranger = fresh_identity(128, 160)
    r = lan.post("/v1/enroll/advertise",
                 json={"advertisement": advertisement(stranger, "stranger-phone", "Stranger Phone",
                                                      family="someone-elses-family")})
    body = r.json()
    check("gate: a non-member family's ask is refused, presence or not",
          r.status_code == 200 and not body.get("ok") and body.get("state") == "refused",
          r.text[:300])

    no_sso = fresh_identity(160, 192)
    r = lan.post("/v1/enroll/advertise",
                 json={"advertisement": advertisement(no_sso, "nosso-phone", "No SSO Phone", sso=None)})
    body = r.json()
    check("gate: a member ask with no sign-in proof is refused over HTTP too",
          not body.get("ok") and body.get("state") == "refused"
          and any("sign-in" in p for p in body.get("problems") or []), r.text[:300])

    wrong_acct = fresh_identity(192, 224)
    r = lan.post("/v1/enroll/advertise",
                 json={"advertisement": advertisement(wrong_acct, "wrongacct-phone", "Wrong Account",
                                                      sso_account="SomeoneElse")})
    body = r.json()
    check("gate: a wrong-account sign-in is refused over HTTP too",
          not body.get("ok") and body.get("state") == "refused", r.text[:300])
    view = fr.load_registry()
    check("gate: the refusals filed nothing and trusted no one",
          all(fr.trusted_member(view, d) is None
              for d in ("stranger-phone", "nosso-phone", "wrongacct-phone"))
          and not any(p["record"]["id"] in ("stranger-phone", "nosso-phone", "wrongacct-phone")
                      for p in pe.pending()))

    # ---------------------------------------------------------- a relay has no such route
    relay = TestClient(make_app(str(TMP / "relay.db"), lambda: "relay.example:8790"),
                       client=("192.168.1.50", 50000))
    check("relay: a post office with no intake says so in its about",
          relay.get("/.well-known/xod-relay").json().get("enroll_advertise") is False)
    r = relay.post("/v1/enroll/advertise",
                   json={"advertisement": advertisement(wifi_phone, "wifi-phone", "Wi-Fi Phone")})
    check("relay: the enroll route does not exist there (404)",
          r.status_code == 404, r.text[:200])
    return finish()


def finish():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
