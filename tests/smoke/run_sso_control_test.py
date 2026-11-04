"""
SSO control: one identity, one enrollment, one gate
(services/github_device.py + services/sso_control.py,
/api/family/control endpoints, Family panel control surface).

The pairing-gate rule (2026-11-02 20:31 PT) for the desktop's own control,
plus the 2026-11-03 direction change (no secret courier):

  - device-flow first: GitHub sign-in needs only a client_id; no
    client_secret is ever sent or stored; the token lands in the
    keychain only and never appears in any returned payload
  - the gate refuses unless BOTH legs hold: this device is a
    trusted member of the signed registry AND a proven GitHub
    session for the same account (registry account or its
    github_login claim). Refusals: signed out, unreachable
    (= unknown, never a pass), password session, unproven
    provider, wrong account, unsigned registry, not a member,
    revoked
  - the one real secret (rdrive's GitHub client secret) rides
    the family vault as a namespaced service-secret field:
    staged via secure entry, published inside the bundle,
    landed by the existing vault apply; presence is reported,
    a value never is
  - control_state carries no secret values; the setup checklist
    is ordered; the recovery block names a wiped machine's
    remedy (apply the vault) instead of an error

Run: .venv/bin/python tests/smoke/run_sso_control_test.py
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
TMP = Path(tempfile.mkdtemp(prefix="xod-sso-control-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
os.environ["XOD_TEST_KEYRING_FILE"] = str(TMP / "keys.json")
os.environ["XOD_FAMILY_REGISTRY_PATH"] = str(TMP / "registry.json")
os.environ["XOD_FAMILY_DEVICE_ID"] = "vector-mac"
os.environ.pop("XOD_RDRIVE_URL", None)
os.environ.pop("XOD_GITHUB_CLIENT_ID", None)
(TMP / "data").mkdir()

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


def finish():
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


# ------------------------------------------------------------ family fixture
# This suite generates its own founder identity and signs its own
# record (the cross-language pinned vectors live in
# run_phone_enroll_test.py; nothing here needs them).
FAMILY, ACCOUNT = "vector-family", "VectorAccount"
REG_PATH = Path(os.environ["XOD_FAMILY_REGISTRY_PATH"])
FAKE_GH_TOKEN = "gho_FAKE_TOKEN_FOR_TESTS"
FAKE_OAI_KEY = "sk-FAKE-OPENAI-FOR-TESTS"
FAKE_SVC_SECRET = "FAKE-RDRIVE-CLIENT-SECRET-FOR-TESTS"

from app.services import family_registry as fr          # noqa: E402
from app.services import keychain, mail_crypto as mc    # noqa: E402

MAC_SIGN_SECRET = mc.b64e(bytes(range(0, 32)))
MAC_BOX_SECRET = mc.b64e(bytes(range(32, 64)))
keychain.set_password(fr.FAMILY_SERVICE, "device_sign_key", MAC_SIGN_SECRET)
keychain.set_password(fr.FAMILY_SERVICE, "device_box_key", MAC_BOX_SECRET)
mac_ident = mc.Identity.from_secrets(MAC_SIGN_SECRET, MAC_BOX_SECRET)
MAC_SIGNED = fr.sign_record({
    "id": "vector-mac", "name": "Vector Mac", "platform": "macOS", "endpoint": "100.64.0.1",
    "capabilities": ["macOS hands"], "account": ACCOUNT, "status": "active",
    "last_seen": "2026-11-02T20:00:00-07:00"}, mac_ident, FAMILY, ACCOUNT)


def write_registry(data):
    REG_PATH.write_text(json.dumps(data), encoding="utf-8")


def seed_founder_registry(extra=None):
    data = {"family": FAMILY, "account": ACCOUNT, "version": 1,
            "updated": "2026-11-02T20:00:00-07:00", "devices": [dict(MAC_SIGNED)]}
    if extra:
        data.update(extra)
    write_registry(fr.sign_approval(data, mac_ident, "vector-mac"))


def gh_claim(account=ACCOUNT, **kw):
    base = {"state": "signed_in", "account": account, "provider": "github",
            "method": "device", "source": "stub", "github_login": None}
    base.update(kw)
    return base


def main():
    seed_founder_registry()

    try:
        from app.services import github_device as gd
        from app.services import sso_control as sc
    except Exception as e:  # failing-first: the modules do not exist on main
        check("services/github_device.py + services/sso_control.py exist", False, f"{type(e).__name__}: {e}")
        return finish()
    check("services/github_device.py + services/sso_control.py exist", True)

    # ------------------------------------------------ device flow (stub transport)
    posts = []

    token_responses = iter([
        {"error": "authorization_pending"},
        {"error": "slow_down", "interval": 9},
        {"access_token": FAKE_GH_TOKEN, "token_type": "bearer", "scope": "read:user"},
    ])

    def stub_post(url, data):
        posts.append((url, dict(data)))
        if url == gd.DEVICE_CODE_URL:
            return {"device_code": "dc-test", "user_code": "WXYZ-1234",
                    "verification_uri": "https://github.com/login/device",
                    "expires_in": 900, "interval": 5}
        if url == gd.ACCESS_TOKEN_URL:
            return next(token_responses)
        return {}

    def stub_user(url, headers):
        assert headers.get("Authorization") == f"Bearer {FAKE_GH_TOKEN}"
        return {"login": "VectorGH", "id": 4242}

    out = gd.begin("")
    check("device: no client id reads unconfigured, nothing posted",
          out["state"] == "unconfigured" and posts == [])
    out = gd.begin("client-id-123", post_form=stub_post)
    check("device: begin returns the code to show, never the device_code",
          out["state"] == "pending" and out["user_code"] == "WXYZ-1234"
          and "device_code" not in json.dumps(out) and "dc-test" not in json.dumps(out))
    r1 = gd.poll(post_form=stub_post, get_json=stub_user)
    r2 = gd.poll(post_form=stub_post, get_json=stub_user)
    check("device: pending then slow_down are honest waiting states",
          r1["state"] == "pending" and r2["state"] == "slow_down" and r2["interval"] == 9)
    r3 = gd.poll(post_form=stub_post, get_json=stub_user)
    check("device: approval completes, identity proven by GitHub",
          r3["state"] == "signed_in" and r3["account"] == "VectorGH" and r3["github_id"] == 4242)
    check("device: the token is in the keychain only, in no payload",
          keychain.get_password(fr.FAMILY_SERVICE, "github_access_token") == FAKE_GH_TOKEN
          and FAKE_GH_TOKEN not in json.dumps([r1, r2, r3, out]))
    check("device: no client_secret was ever sent to GitHub",
          all("secret" not in k for _, d in posts for k in d)
          and all(set(d) <= {"client_id", "scope", "device_code", "grant_type"} for _, d in posts))

    denied = iter([{"error": "access_denied"}])
    gd.begin("client-id-123", post_form=stub_post)
    r = gd.poll(post_form=lambda u, d: next(denied) if u == gd.ACCESS_TOKEN_URL else stub_post(u, d),
                get_json=stub_user)
    check("device: a denied approval is a terminal, honest state", r["state"] == "denied")
    gd.begin("client-id-123", post_form=stub_post)
    gd._pending["expires_at"] = time.time() - 1
    check("device: an abandoned flow expires instead of lingering",
          gd.poll(post_form=stub_post, get_json=stub_user)["state"] == "expired")

    who = gd.verify(get_json=stub_user)
    check("device: verify returns the claim (login + provider), never the token",
          who["state"] == "signed_in" and who["provider"] == "github"
          and who["account"] == "VectorGH" and FAKE_GH_TOKEN not in json.dumps(who))

    # ------------------------------------------------ the gate
    v = sc.gate({"state": "signed_out", "account": None, "provider": None})
    check("gate: signed out refuses", not v["allowed"] and any("not signed in" in r for r in v["reasons"]))
    v = sc.gate({"state": "unknown", "account": None, "provider": None})
    check("gate: an uncheckable sign-in refuses (unknown is never a pass)",
          not v["allowed"] and any("couldn't be checked" in r for r in v["reasons"]))
    v = sc.gate({"state": "signed_in", "account": ACCOUNT, "provider": None, "method": "password"})
    check("gate: a password session refuses, naming the password",
          not v["allowed"] and any("password" in r for r in v["reasons"]))
    v = sc.gate({"state": "signed_in", "account": ACCOUNT, "provider": None, "method": None})
    check("gate: an unproven provider refuses",
          not v["allowed"] and any("isn't proven to be GitHub" in r for r in v["reasons"]))
    v = sc.gate(gh_claim(account="SomebodyElse"))
    check("gate: a GitHub session for a different account refuses",
          not v["allowed"] and any("different account" in r for r in v["reasons"]))
    v = sc.gate(gh_claim())
    check("gate: GitHub for the family account + trusted member allows",
          v["allowed"] and v["reasons"] == [], json.dumps(v))

    # github_login binding: device-flow logins are GitHub logins, the
    # registry account is the rdrive username; the claim binds them.
    seed_founder_registry(extra={"github_login": "VectorGH"})
    v = sc.gate(gh_claim(account="VectorGH"))
    check("gate: a device-flow login matching the registry github_login allows",
          v["allowed"], json.dumps(v))
    seed_founder_registry()

    # Unsigned registry: display-only records grant nothing.
    write_registry({"family": FAMILY, "account": ACCOUNT, "version": 1,
                    "devices": [{k: MAC_SIGNED[k] for k in
                                 ("id", "name", "platform", "endpoint", "capabilities", "account", "status", "last_seen")}]})
    v = sc.gate(gh_claim())
    check("gate: an unsigned registry refuses (not a verified member)",
          not v["allowed"] and any("not a verified family member" in r for r in v["reasons"]))
    seed_founder_registry()

    # Revoked member refuses even with a perfect session.
    data = json.loads(REG_PATH.read_text(encoding="utf-8"))
    data["revocations"] = [fr.sign_revocation(data, mac_ident, "vector-mac", "vector-mac")]
    write_registry(data)
    v = sc.gate(gh_claim())
    check("gate: a revoked device refuses, naming the removal",
          not v["allowed"] and any("removed from the family" in r for r in v["reasons"]))
    seed_founder_registry()

    # No registry at all.
    REG_PATH.unlink()
    v = sc.gate(gh_claim())
    check("gate: no registry refuses", not v["allowed"]
          and any("no readable family registry" in r for r in v["reasons"]))
    seed_founder_registry()

    # ------------------------------------------------ session providers
    def stub_status(url, headers=None):
        return {"user": ACCOUNT, "github": True, "method": "github"}

    claim = sc.session(fetch=stub_status, get_json=stub_user)   # device token still stored: provider A wins
    check("session: the device-flow claim is used when a token is saved",
          claim["source"] == "github-device" and claim["state"] == "signed_in", json.dumps(claim))
    gd.forget_token()
    sc._GITHUB_CACHE.clear()
    os.environ["XOD_RDRIVE_URL"] = "https://rdrive.invalid"
    claim = sc.session(fetch=lambda u, h=None: {"user": ACCOUNT, "github": True})
    check("session: rdrive status without a method stays unproven",
          claim["state"] == "signed_in" and claim["provider"] is None, json.dumps(claim))
    claim = sc.session(fetch=stub_status)
    check("session: rdrive status WITH a GitHub method proves the provider",
          claim["provider"] == "github" and sc.gate(claim)["allowed"], json.dumps(claim))
    claim = sc.session(fetch=lambda u, h=None: {"user": None, "github": False})
    check("session: rdrive with no user is signed out, github_configured reads false",
          claim["state"] == "signed_out" and claim["github_configured"] is False)
    def boom(url, headers=None):
        raise ConnectionError("nope")
    claim = sc.session(fetch=boom)
    check("session: an unreachable rdrive is unknown, never signed out",
          claim["state"] == "unknown", json.dumps(claim))
    os.environ.pop("XOD_RDRIVE_URL", None)

    # ------------------------------------------------ vault service secret
    from app.services import family_vault, secret_store
    r = sc.stage_service_secret("not_a_real_field", "x")
    check("service secret: an unlisted field refuses", not r["success"])
    r = sc.stage_service_secret("rdrive_github_client_secret", FAKE_SVC_SECRET)
    st = sc.service_secret_state()
    check("service secret: staged via secure entry; state is presence-only",
          r["success"] and st["rdrive_github_client_secret"]["present"]
          and FAKE_SVC_SECRET not in json.dumps(st))
    secret_store.save("openai_api_key", FAKE_OAI_KEY)
    out = sc.publish_service_secrets("vector-mac")
    check("service secret: publish carries provider keys + the namespaced field",
          "service:rdrive_github_client_secret" in out["fields"] and "openai_api_key" in out["fields"],
          json.dumps(out))
    # The wiped-machine half: clear this keystore, then the EXISTING
    # vault apply must land both fields back (self-healing leg).
    secret_store.save("openai_api_key", "")
    secret_store.save("service:rdrive_github_client_secret", "")
    applied = family_vault.apply("vector-mac")
    check("service secret: vault apply lands it on a member device (wipe recovery)",
          applied["state"] == "applied"
          and secret_store.stored("service:rdrive_github_client_secret") == FAKE_SVC_SECRET
          and secret_store.stored("openai_api_key") == FAKE_OAI_KEY, json.dumps(applied))

    # ------------------------------------------------ control state aggregate
    keychain.set_password(fr.FAMILY_SERVICE, "github_access_token", FAKE_GH_TOKEN)
    cs = sc.control_state(fetch=boom, get_json=stub_user)
    blob = json.dumps(cs)
    check("control: no token, key, or service-secret value anywhere in the aggregate",
          FAKE_GH_TOKEN not in blob and FAKE_OAI_KEY not in blob and FAKE_SVC_SECRET not in blob)
    check("control: header carries account, family, session + gate verdict",
          cs["account"]["family"] == FAMILY and cs["gate"]["registry"]["my_trusted"]
          and cs["approvals"]["phone_pending"] == 0)
    check("control: setup checklist is the fresh-install order",
          [s["id"] for s in cs["setup"]] == ["rdrive", "signin", "registry", "enrolled", "vault", "working"],
          json.dumps([s["id"] for s in cs["setup"]]))
    check("control: recovery block is explicit (bundle applied here: not needed)",
          cs["recovery"]["needed"] is False and cs["recovery"]["applied_version"] >= 1)

    # ------------------------------------------------ static wiring
    main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    fam_js = (ROOT / "app" / "static" / "js" / "family.js").read_text(encoding="utf-8")
    check("wiring: control + device + service-secret routes exist",
          all(x in main_py for x in ("/api/family/control", "device/begin", "device/poll",
                                     "control/session", "control/service-secret")))
    check("wiring: the Family panel renders the control surface + device sign-in",
          "/api/family/control" in fam_js and "device-begin" in fam_js
          and "Getting to working" in fam_js)

    return finish()


if __name__ == "__main__":
    sys.exit(main())
