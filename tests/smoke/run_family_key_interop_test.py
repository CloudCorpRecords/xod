"""
Family key interoperability (R12): the vault bundle's home in the
family synced space, enroll -> keys, the family key channel, and
revocation re-wrap (services/family_vault.py,
services/family_key_channel.py, services/phone_enroll.py hooks,
services/xod_network.py beacon route).

The owner's rules under test (2026-11-01/02 design, restated and
extended 2026-11-09):
  - the encrypted vault bundle lives in the family synced space,
    resolved by the family_rules ladder: the rdrive files root
    first, the registry's own synced space (a Drive folder) as the
    alternate root; only the per-recipient wrapped bundle ever
    lands there — never plaintext keys
  - completing gated enrollment triggers fetch + apply into the
    device's own keystore, ending in a has_key read-back for the
    family default provider — no manual key juggling
  - the session walls are product defects, not the trust boundary
    (macOS Keychain -25308 for SSH/headless processes; Windows
    Credential Manager 1312 for service/SSH sessions): the device's
    own running Xod service performs the keystore read/unwrap/
    delivery for an authenticated family requester — signed
    registry membership + the signed SSO assertion + the bundle
    recipient check — sealed to the requester, audit-logged by
    field name only
  - revocation is re-wrap: the stored bundle is republished without
    the removed device, and status() flags wraps_revoked until a
    trusted device with keys does it

All keys here are test values, never real credentials. One process
plays every device (the family_vault suite's fiction): devices are
separated by their identities and by explicit identity arguments;
the shared keyring file is the OS-keystore stand-in and is the ONE
file excluded from the plaintext scan, exactly because a keystore
is where plaintext is supposed to end up.

Run: .venv/bin/python tests/smoke/run_family_key_interop_test.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-family-key-interop-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
os.environ["XOD_TEST_KEYRING_FILE"] = str(TMP / "keys.json")
os.environ["XOD_FAMILY_DEVICE_ID"] = "macbook-pro"
(TMP / "data").mkdir()

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


FAMILY = "interop-family"
ACCOUNT = "cloudtheboy@gmail.com"
KEYRING_FILE = TMP / "keys.json"
DRIVE_SPACE = TMP / "drive-space"                 # the registry's synced space (Drive stand-in)
REG = DRIVE_SPACE / "family" / "registry.json"
DRIVE_BUNDLE = DRIVE_SPACE / "family" / "vault.bundle.json"
RDRIVE_ACCOUNT = TMP / "Documents" / "rdrive" / "data" / "files" / "cloudtheboy"
RDRIVE_BUNDLE = RDRIVE_ACCOUNT / "family" / "vault.bundle.json"
os.environ["XOD_FAMILY_REGISTRY_PATH"] = str(REG)

KEY1, KEY2, KEY3 = "sk-interop-not-real-1", "tvly-interop-not-real-2", "sk-interop-not-real-3"

from app.services import family_registry as fr          # noqa: E402
from app.services import family_vault as fv             # noqa: E402
from app.services import keychain, mail_crypto as mc    # noqa: E402
from app.services import secret_store                   # noqa: E402
from app.services.storage import set_setting            # noqa: E402

mac = mc.Identity.new()
razer = mc.Identity.new()
deck = mc.Identity.new()
canary = mc.Identity.new()
stranger = mc.Identity.new()

# This process's desktop IS macbook-pro: seed its family identity
# into the test keyring so fr.device_identity() returns it.
_sign, _box = mac.secrets()
keychain.set_password(fr.FAMILY_SERVICE, "device_sign_key", _sign)
keychain.set_password(fr.FAMILY_SERVICE, "device_box_key", _box)


def record(ident, did, name, platform, status="active"):
    rec = {"id": did, "name": name, "platform": platform, "endpoint": None,
           "capabilities": ["family member"], "status": status,
           "last_seen": "2026-11-09T08:00:00-08:00", "account": ACCOUNT}
    return fr.sign_record(rec, ident, FAMILY, ACCOUNT)


def write_registry(devices, revocations=None, approver=mac, approver_id="macbook-pro"):
    data = {"family": FAMILY, "account": ACCOUNT, "version": 2,
            "updated": "2026-11-09T08:00:00-08:00", "devices": devices}
    if revocations:
        data["revocations"] = revocations
    REG.parent.mkdir(parents=True, exist_ok=True)
    REG.write_text(json.dumps(fr.sign_approval(data, approver, approver_id)))
    return fr.load_registry()


def wipe_keys():
    for field in secret_store.SECRET_FIELDS:
        secret_store.save(field, "")


def sso(ident, device_id, account=ACCOUNT, issued_at=None):
    issued = int(issued_at if issued_at is not None else time.time())
    payload = {"account": account, "device_id": device_id,
               "issued_at": issued, "provider": "github"}
    return {**payload, "signature": ident.sign(mc.canonical(payload))}


def advertisement(ident, did, name):
    base = {"id": did, "name": name, "platform": "Linux", "endpoint": None,
            "capabilities": ["family member"], "account": ACCOUNT,
            "last_seen": "2026-11-09T08:00:00-08:00"}
    joining = fr.sign_record({**base, "status": "joining"}, ident, FAMILY, ACCOUNT)
    active = fr.sign_record({**base, "status": "active"}, ident, FAMILY, ACCOUNT)
    issued = int(time.time())
    return {"v": 1, "kind": "xod-phone-advertise", "account": ACCOUNT,
            "provider": "github", "family": FAMILY, "record": joining,
            "record_active": active, "mail_address": "", "issued_at": issued,
            "sso": sso(ident, did, issued_at=issued)}


MAC_REC = record(mac, "macbook-pro", "MacBook Pro", "macOS")
RAZER_REC = record(razer, "razer", "Razer", "Windows 11")
DECK_REC = record(deck, "deck", "Steam Deck", "Linux")
CANARY_REC = record(canary, "family-canary", "Canary", "Linux")


def main():
    from app.services import phone_enroll as pe

    check("seeded desktop identity is the registry mac",
          fr.device_identity().sign_pub == mac.sign_pub)

    # ------------------------------------------------ A: the bundle home ladder
    os.environ.pop("XOD_FAMILY_VAULT_PATH", None)
    check("ladder: with no rdrive space the bundle lives beside the registry",
          fv.bundle_path() == DRIVE_BUNDLE, str(fv.bundle_path()))
    check("ladder: that home reports as the registry space",
          fv.bundle_home()["space"] == "registry-space", str(fv.bundle_home()))
    RDRIVE_ACCOUNT.mkdir(parents=True)
    check("ladder: an rdrive files root outranks the registry space for a fresh bundle",
          fv.bundle_path() == RDRIVE_BUNDLE, str(fv.bundle_path()))
    check("ladder: the rdrive home reports as rdrive",
          fv.bundle_home()["space"] == "rdrive", str(fv.bundle_home()))
    DRIVE_BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    DRIVE_BUNDLE.write_text("{}")
    check("ladder: an existing bundle is never silently migrated to another space",
          fv.bundle_path() == DRIVE_BUNDLE, str(fv.bundle_path()))
    DRIVE_BUNDLE.unlink()
    os.environ["XOD_FAMILY_VAULT_PATH"] = str(TMP / "custom" / "bundle.json")
    check("ladder: the env override wins outright",
          fv.bundle_path() == TMP / "custom" / "bundle.json"
          and fv.bundle_home()["space"] == "env")
    os.environ.pop("XOD_FAMILY_VAULT_PATH")
    set_setting(fv.PATH_SETTING, str(TMP / "custom2" / "bundle.json"))
    check("ladder: the saved setting wins over discovery",
          fv.bundle_path() == TMP / "custom2" / "bundle.json"
          and fv.bundle_home()["space"] == "setting")
    set_setting(fv.PATH_SETTING, "")

    # ------------------------------------------------ B: publish/fetch via the rdrive space
    view = write_registry([MAC_REC, RAZER_REC])
    check("scratch family: mac + razer trusted",
          [d["id"] for d in view["trusted_members"]] == ["macbook-pro", "razer"],
          str(view["problems"]))
    wipe_keys()
    out = fv.publish({"openai_api_key": KEY1, "tavily_api_key": KEY2},
                     "macbook-pro", view=view, identity=mac)
    check("publish lands the bundle in the rdrive synced space",
          RDRIVE_BUNDLE.is_file() and out["version"] == 1, str(out))
    raw = RDRIVE_BUNDLE.read_text()
    check("the synced-space bundle is ciphertext only",
          KEY1 not in raw and KEY2 not in raw)
    got = fv.apply("razer", view=view, identity=razer)
    check("a member fetches + applies from the rdrive space",
          got["state"] == "applied"
          and secret_store.get("openai_api_key") == KEY1
          and secret_store.get("tavily_api_key") == KEY2, str(got))
    st = fv.status(view, "razer")
    check("status names the bundle home (rdrive) and no revoked wraps",
          st["bundle_home"]["space"] == "rdrive" and st["wraps_revoked"] == [],
          str(st["bundle_home"]))

    # the rdrive root goes away (this machine has only the Drive space)
    (TMP / "Documents").rename(TMP / "Documents.bak")
    check("ladder: without rdrive the bundle resolves to the registry space again",
          fv.bundle_path() == DRIVE_BUNDLE, str(fv.bundle_path()))

    # ------------------------------------------------ C: enroll -> keys, end to end
    wipe_keys()
    set_setting(fv.VERSION_SETTING, "0")
    out = fv.sync_keys("deck", identity=deck)
    check("enroll->keys: an unenrolled device syncs nothing",
          out["state"] == "not_trusted", str(out))
    r = pe.intake(advertisement(deck, "deck", "Steam Deck"))
    check("enroll: the fresh device's gated advertisement pends",
          r.get("ok") and r.get("state") == "pending", json.dumps(r))
    r = pe.approve("deck")
    check("enroll: approval enrolls it (read back trusted)",
          r.get("ok") and r.get("state") == "enrolled", json.dumps(r)[:300])
    check("enroll: the completion carries the vault sync follow-through",
          isinstance(r.get("vault_sync"), dict) and "state" in r["vault_sync"],
          json.dumps(r)[:300])
    view = fr.load_registry()
    check("enroll: deck is now a trusted member",
          fr.trusted_member(view, "deck") is not None)
    out = fv.publish({"openai_api_key": KEY3}, "macbook-pro", view=view, identity=mac)
    check("enroll: the republished bundle wraps for the new member",
          "deck" in out["recipients"] and DRIVE_BUNDLE.is_file(), str(out))
    wipe_keys()
    set_setting(fv.VERSION_SETTING, "0")
    out = fv.sync_keys("deck", identity=deck)
    check("enroll->keys: sync applies the bundle into the device's own keystore",
          out["state"] == "applied" and secret_store.get("openai_api_key") == KEY3,
          str(out))
    check("enroll->keys: the read-back proves the family default key is held",
          out.get("has_key") is True and out.get("keys_present") == {"openai_api_key": True},
          str(out))
    out = fv.sync_keys("deck", identity=deck)
    check("enroll->keys: a second sync is an explicit current, still holding the key",
          out["state"] == "current" and out.get("has_key") is True, str(out))

    # ------------------------------------------------ D: the family key channel
    from app.services import family_key_channel as fk
    view = fr.load_registry()
    body = fk.signed_key_request(deck, "deck", "macbook-pro", "deliver",
                                 ["openai_api_key"], ACCOUNT)
    out = fk.handle_key_request(body, view=view, my_id="macbook-pro", identity=mac)
    opened = mc.open_envelope(deck, "xod-family:deck", out["envelope"])
    check("channel: an enrolled recipient is delivered the key, sealed to its own box key",
          opened.get("signature_ok") is True
          and (opened.get("msg") or {}).get("fields", {}).get("openai_api_key") == KEY3,
          str({k: v for k, v in out.items() if k != "envelope"}))
    check("channel: the delivery is signed by the serving device the registry knows",
          (opened.get("keys") or {}).get("sign") == mac.sign_pub)

    # the full client, transport stubbed to the serving handler
    import httpx

    class _Resp:
        def __init__(self, payload, status=200):
            self._p, self.status_code = payload, status

        def json(self):
            return self._p

    class _Client:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            try:
                return _Resp(fk.handle_key_request(json, view=fr.load_registry(),
                                                   my_id="macbook-pro", identity=mac))
            except PermissionError as e:
                return _Resp({"detail": str(e)}, 403)

    real_client = httpx.Client
    httpx.Client = _Client
    try:
        wipe_keys()
        got = fk.fetch_keys_into_keystore("100.64.0.1", "macbook-pro",
                                          ["openai_api_key"], identity=deck, my_id="deck")
        check("channel: a headless requester fetches into its own keystore, presence read back",
              got["state"] == "stored" and got["fields"] == {"openai_api_key": True}
              and got["has_key"] is True and secret_store.get("openai_api_key") == KEY3,
              str(got))
    finally:
        httpx.Client = real_client
    try:
        fk.request_keys("127.0.0.1", "macbook-pro", ["openai_api_key"],
                        identity=deck, my_id="deck", port=1, timeout=2)
        blocked = False
    except fk.KeyBlocked:
        blocked = True
    check("channel: an unreachable server is an explicit KeyBlocked, never a silent wait",
          blocked)

    def refused(body, view=None):
        try:
            fk.handle_key_request(body, view=view or fr.load_registry(),
                                  my_id="macbook-pro", identity=mac)
            return ""
        except fk.KeyRefused as e:
            return str(e)

    body = fk.signed_key_request(stranger, "stranger", "macbook-pro", "deliver",
                                 ["openai_api_key"], ACCOUNT)
    check("channel: a non-member is refused", "not a trusted family member" in refused(body),
          refused(body))
    # canary enrolls AFTER the last publish: trusted, but not a bundle recipient
    view = write_registry([MAC_REC, RAZER_REC, DECK_REC, CANARY_REC])
    body = fk.signed_key_request(canary, "family-canary", "macbook-pro", "deliver",
                                 ["openai_api_key"], ACCOUNT)
    msg = refused(body, view)
    check("channel: a trusted member the bundle does not wrap for is refused",
          "not a recipient" in msg, msg)
    body = fk.signed_key_request(deck, "deck", "macbook-pro", "deliver",
                                 ["openai_api_key"], "SomeoneElse")
    msg = refused(body, view)
    check("channel: a wrong-account SSO assertion is refused",
          "signed-in account does not match" in msg, msg)
    body = fk.signed_key_request(deck, "deck", "macbook-pro", "deliver",
                                 ["openai_api_key"], ACCOUNT)
    del body["sso"]
    body["signature"] = deck.sign(mc.canonical({k: v for k, v in body.items() if k != "signature"}))
    msg = refused(body, view)
    check("channel: no sign-in proof, no keys", "no sign-in proof" in msg, msg)
    body = fk.signed_key_request(deck, "deck", "macbook-pro", "deliver",
                                 ["openai_api_key"], ACCOUNT)
    body["ts"] = int(time.time()) - 3600
    body["signature"] = deck.sign(mc.canonical({k: v for k, v in body.items() if k != "signature"}))
    msg = refused(body, view)
    check("channel: a stale request is refused", "stale" in msg, msg)
    body = fk.signed_key_request(deck, "deck", "macbook-pro", "deliver",
                                 ["openai_api_key"], ACCOUNT)
    body["signature"] = stranger.sign(mc.canonical({k: v for k, v in body.items() if k != "signature"}))
    msg = refused(body, view)
    check("channel: a forged signature is refused", "signature does not verify" in msg, msg)
    body = fk.signed_key_request(deck, "deck", "macbook-pro", "deliver",
                                 ["not_a_family_field"], ACCOUNT)
    msg = refused(body, view)
    check("channel: an unknown field is refused by name", "not family key fields" in msg, msg)
    body = fk.signed_key_request(razer, "razer", "macbook-pro", "apply", [], ACCOUNT)
    out = fk.handle_key_request(body, view=view, my_id="macbook-pro", identity=mac)
    check("channel: the apply op runs the vault apply in the serving process",
          out.get("op") == "apply" and out.get("state") in ("applied", "current"), str(out))
    # delivery does not depend on the serving keystore's API-key vault
    # read when the bundle itself can be unwrapped (the headless point)
    real_get = secret_store.get

    def down_get(name):
        raise RuntimeError("keystore down")

    secret_store.get = down_get
    try:
        body = fk.signed_key_request(deck, "deck", "macbook-pro", "deliver",
                                     ["openai_api_key"], ACCOUNT)
        out = fk.handle_key_request(body, view=fr.load_registry(),
                                    my_id="macbook-pro", identity=mac)
        opened = mc.open_envelope(deck, "xod-family:deck", out["envelope"])
        delivered = (opened.get("msg") or {}).get("fields", {}).get("openai_api_key") == KEY3
    finally:
        secret_store.get = real_get
    check("channel: delivery unwraps the bundle even when the API-key vault read is down",
          delivered)

    # ------------------------------------------------ E: revocation is re-wrap
    raw = json.loads(REG.read_text())
    raw["revocations"] = [fr.sign_revocation(raw, mac, "macbook-pro", "razer")]
    REG.write_text(json.dumps(raw))
    view = fr.load_registry()
    check("revoke: the registry revocation lands (razer untrusted)",
          fr.trusted_member(view, "razer") is None)
    st = fv.status(view, "macbook-pro")
    check("revoke: status honestly flags the bundle still wrapping for the revoked device",
          st["wraps_revoked"] == ["razer"], str(st["wraps_revoked"]))
    out = fv.rewrap_after_revocation("razer", "macbook-pro", view=view)
    check("revoke: rewrap republishes without the removed device",
          out["state"] == "rewrapped" and "razer" not in out["recipients"], str(out))
    bundle = fv.read_bundle()
    check("revoke: the rotated bundle carries no wrapped key for the removed device",
          "razer" not in (bundle.get("wrapped") or {}))
    check("revoke: the flag clears once a trusted device has republished",
          fv.status(fr.load_registry(), "macbook-pro")["wraps_revoked"] == [])
    out = fv.sync_keys("razer", identity=razer)
    check("revoke: the removed device syncs nothing afterwards",
          out["state"] == "not_trusted", str(out))
    r = pe.revoke("deck")
    check("revoke: the enrollment revoke carries its rewrap follow-through",
          r.get("ok") and isinstance(r.get("vault_rewrap"), dict)
          and r["vault_rewrap"].get("state") == "rewrapped", json.dumps(r)[:300])
    check("revoke: after it, the bundle no longer wraps for the revoked device",
          "deck" not in (fv.read_bundle().get("wrapped") or {}))
    # a publisher holding no keys says so, plainly
    raw = json.loads(REG.read_text())
    raw["revocations"] = (raw.get("revocations") or []) + [
        fr.sign_revocation(raw, mac, "macbook-pro", "family-canary")]
    REG.write_text(json.dumps(raw))
    wipe_keys()
    out = fv.rewrap_after_revocation("family-canary", "macbook-pro")
    check("revoke: a keyless publisher reports no_keys instead of pretending",
          out["state"] == "no_keys", str(out))
    secret_store.save("openai_api_key", KEY3)
    out = fv.rewrap_after_revocation("family-canary", "macbook-pro")
    check("revoke: with keys back, the same rewrap completes",
          out["state"] == "rewrapped" and "family-canary" not in out["recipients"], str(out))

    # ------------------------------------------------ G: the session wall is a typed state
    real_identity = fr.device_identity

    def locked_identity():
        raise keychain.Unreadable("test: the OS keystore refused the read")

    fr.device_identity = locked_identity
    try:
        out = fv.sync_keys("macbook-pro")
    finally:
        fr.device_identity = real_identity
    check("wall: identity locked out of the keystore is the typed keystore_unreachable state",
          out["state"] == "keystore_unreachable", str(out))

    # ------------------------------------------------ F: no plaintext outside the keystore
    log_text = (TMP / "data" / "event_log.jsonl").read_text() \
        if (TMP / "data" / "event_log.jsonl").exists() else ""
    check("audit: key deliveries are in the family audit trail by field name",
          "family.key_delivered" in log_text and "openai_api_key" in log_text)
    leaks = []
    for p in TMP.rglob("*"):
        if p.is_file() and p != KEYRING_FILE:
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for k in (KEY1, KEY2, KEY3):
                if k in text:
                    leaks.append(f"{k} in {p.relative_to(TMP)}")
    check("invariant: no plaintext key anywhere outside the keystore stand-in "
          "(synced spaces, data dir, audit log)", not leaks, "; ".join(leaks))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
