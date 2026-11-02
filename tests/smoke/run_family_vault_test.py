"""
Family Link V0-V3: signed registry, family vault, revocation/rotation,
inference borrowing (services/family_registry.py,
services/family_vault.py, services/family_inference.py).

Covered here:
  - a signed, approval-covered registry verifies: trusted members are
    exactly the signed active devices, interim_unsigned clears
  - a tampered record (capability edited after signing) is refused
    outright and reported
  - a forged device (self-signed, never approved) earns no trust: the
    approval digest no longer matches, so nobody is trusted
  - a signed revocation refuses the revoked device even though its
    own record still says active
  - the legacy F0 seed still loads as display-only (no trust)
  - vault: a keyless member unwraps the bundle into its own keystore
    (presence + value read-back through secret_store), a non-member
    cannot, a pinned field is never overwritten, local-only is an
    explicit no-op
  - rotation: after revocation + republish, the removed device is not
    a recipient and cannot apply the new bundle
  - inference: a signed borrow request from a trusted member is
    served and lands in the SERVING device's ledger with the
    requester named; unsigned/forged/revoked callers are refused;
    an unreachable server and a local-only pin both raise the
    explicit BorrowBlocked state

All keys in this suite are test values ("sk-test-not-real"), never
real credentials. Run: .venv/bin/python tests/smoke/run_family_vault_test.py
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
TMP = Path(tempfile.mkdtemp(prefix="xod-family-vault-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir()

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


FAMILY = "turcios-family"
ACCOUNT = "cloudtheboy@gmail.com"
REG = TMP / "registry.json"


def device_record(ident, did, name, platform, endpoint, caps):
    rec = {"id": did, "name": name, "platform": platform, "endpoint": endpoint,
           "capabilities": caps, "status": "active",
           "last_seen": "2026-11-01T20:00:00-07:00", "account": ACCOUNT}
    from app.services import family_registry
    return family_registry.sign_record(rec, ident, FAMILY, ACCOUNT)


def write_registry(data):
    REG.write_text(json.dumps(data))
    os.environ["XOD_FAMILY_REGISTRY_PATH"] = str(REG)
    os.environ.pop("XOD_FAMILY_VAULT_PATH", None)  # bundle sits beside the registry
    return REG


def main():
    from app.services import family_registry as fr
    from app.services import family_vault as fv
    from app.services import family_inference as fi
    from app.services import mail_crypto as mc
    from app.services import secret_store

    mac = mc.Identity.new()
    razer = mc.Identity.new()
    stranger = mc.Identity.new()

    base = {"family": FAMILY, "account": ACCOUNT, "version": 2,
            "updated": "2026-11-01T20:00:00-07:00",
            "devices": [
                device_record(mac, "macbook-pro", "MacBook Pro", "macOS", "100.119.42.98",
                              ["cloud model: openai gpt-6.1-terra"]),
                device_record(razer, "razer", "razer-box", "Windows 11", "100.80.212.74",
                              ["local model: lm_studio prism-ml/bonsai-27b"]),
            ]}
    approved = fr.sign_approval(json.loads(json.dumps(base)), mac, "macbook-pro")
    write_registry(approved)

    # ---- V0: signed + approved registry verifies
    view = fr.load_registry()
    check("signed registry loads ok", view["state"] == "ok", str(view)[:200])
    check("approval verifies", view["approval_verified"] is True, str(view["problems"]))
    check("trusted members are exactly the signed active devices",
          [d["id"] for d in view["trusted_members"]] == ["macbook-pro", "razer"],
          str(view["trusted_members"]))
    check("interim_unsigned clears when every member verifies",
          view["interim_unsigned"] is False)
    check("records carry real keys, not the placeholder",
          all(not d["public_key"].startswith("PLACEHOLDER") for d in view["members"]))

    # ---- V0: tampered record is refused
    tampered = json.loads(json.dumps(approved))
    tampered["devices"][1]["capabilities"] = ["cloud model: free everything"]
    write_registry(tampered)
    view = fr.load_registry()
    check("tampered record is refused, not rendered",
          all(d["id"] != "razer" for d in view["devices"]), str(view["devices"])[:200])
    check("tamper is reported as a signature failure",
          any("signature does not verify" in p for p in view["problems"]), str(view["problems"]))
    check("tampered device gains no trust (the approver's own verified record is unaffected)",
          fr.trusted_member(view, "razer") is None
          and [d["id"] for d in view["trusted_members"]] == ["macbook-pro"],
          str(view["trusted_members"]))

    # ---- V0: forged (self-signed, unapproved) device earns no trust
    forged = json.loads(json.dumps(approved))
    forged["devices"].append(device_record(stranger, "evil-box", "Evil", "Linux",
                                           "100.99.99.99", ["vault please"]))
    write_registry(forged)
    view = fr.load_registry()
    check("forged device is not trusted", fr.trusted_member(view, "evil-box") is None)
    check("an unapproved set means no trusted members at all",
          view["trusted_members"] == [] and view["interim_unsigned"] is True,
          str(view["problems"]))

    # ---- V0: signed revocation refuses the device
    revoked = json.loads(json.dumps(approved))
    revoked["revocations"] = [fr.sign_revocation(revoked, mac, "macbook-pro", "razer")]
    write_registry(revoked)
    view = fr.load_registry()
    check("revoked device is not a member",
          all(d["id"] != "razer" for d in view["members"]), str(view["members"]))
    check("revoked device is not trusted", fr.trusted_member(view, "razer") is None)
    check("revocation is visible in the view",
          view["revoked_by_registry"] == ["razer"], str(view["revoked_by_registry"]))

    # restore the clean approved registry for the vault phase
    write_registry(approved)
    view = fr.load_registry()
    check("clean registry restores trust", len(view["trusted_members"]) == 2)

    # ---- V1: vault publish + apply (keyless member gains the keys)
    secret_store.save("openai_api_key", "")  # the receiving side starts keyless
    out = fv.publish({"openai_api_key": "sk-test-not-real",
                      "tavily_api_key": "tvly-test-not-real"},
                     "macbook-pro", view=view, identity=mac)
    check("publish writes a bundle for both trusted members",
          out["recipients"] == ["macbook-pro", "razer"] and out["version"] == 1, str(out))
    bundle = fv.read_bundle()
    raw = (TMP / "vault.bundle.json").read_text()
    check("bundle on disk is ciphertext only (no key values, no bundle key)",
          "sk-test-not-real" not in raw and "tvly-test-not-real" not in raw, raw[:200])
    got = fv.apply("razer", view=view, identity=razer)
    check("keyless member applies the bundle", got["state"] == "applied", str(got))
    check("unwrapped key is in the member's own keystore afterwards",
          secret_store.get("openai_api_key") == "sk-test-not-real")
    check("second provider key arrived too",
          secret_store.get("tavily_api_key") == "tvly-test-not-real")
    again = fv.apply("razer", view=view, identity=razer)
    check("re-apply of the same version is an explicit current state",
          again["state"] == "current", str(again))

    # a non-member holding the same synced folder gets nothing
    got = fv.apply("evil-box", view=view, identity=stranger)
    check("non-member apply is refused by state", got["state"] == "not_trusted", str(got))
    try:
        key = fv._open_bundle_key(bundle["wrapped"]["razer"], stranger, "evil-box")
        opened_anything = True
    except Exception:
        opened_anything = False
    check("non-member cannot unwrap another member's wrapped key", not opened_anything)

    # ---- V1: per-device pins survive a sync
    from app.services.storage import set_setting
    set_setting(fv.PINNED_SETTING, json.dumps(["tavily_api_key"]))
    secret_store.save("tavily_api_key", "local-pin-value")
    out = fv.publish({"openai_api_key": "sk-test-not-real-v2",
                      "tavily_api_key": "tvly-test-not-real-v2"},
                     "macbook-pro", view=view, identity=mac)
    got = fv.apply("razer", view=view, identity=razer)
    check("pinned field is skipped on apply",
          got["pinned_skipped"] == ["tavily_api_key"]
          and secret_store.get("tavily_api_key") == "local-pin-value", str(got))
    check("unpinned field still updates",
          secret_store.get("openai_api_key") == "sk-test-not-real-v2")
    set_setting(fv.PINNED_SETTING, "[]")

    # ---- V1: local-only pin is an explicit no-op
    set_setting(fv.LOCAL_ONLY_SETTING, "on")
    fv.publish({"openai_api_key": "sk-test-not-real-v3"},
               "macbook-pro", view=view, identity=mac)
    got = fv.apply("razer", view=view, identity=razer)
    check("local-only apply is an explicit no-op",
          got["state"] == "local_only"
          and secret_store.get("openai_api_key") == "sk-test-not-real-v2", str(got))
    set_setting(fv.LOCAL_ONLY_SETTING, "off")

    # ---- V2: revocation rotates the bundle away from the removed device
    revoked = json.loads(json.dumps(approved))
    revoked["revocations"] = [fr.sign_revocation(revoked, mac, "macbook-pro", "razer")]
    write_registry(revoked)
    view2 = fr.load_registry()
    out = fv.publish({"openai_api_key": "sk-test-not-real-v4"},
                     "macbook-pro", view=view2, identity=mac)
    check("after revocation the bundle has one recipient",
          out["recipients"] == ["macbook-pro"], str(out))
    bundle = fv.read_bundle()
    check("removed device has no wrapped key in the rotated bundle",
          "razer" not in (bundle.get("wrapped") or {}))
    got = fv.apply("razer", view=view2, identity=razer)
    check("removed device cannot apply the rotated bundle",
          got["state"] == "not_trusted", str(got))
    write_registry(approved)
    view = fr.load_registry()

    # ---- V3: inference borrowing, served + ledgered on the server
    def stub_serve(provider, model, messages):
        return {"text": "borrowed-answer", "usage": {"prompt_tokens": 11,
                "completion_tokens": 7, "total_tokens": 18},
                "provider": provider, "model": model}

    body = fi.signed_request(razer, "razer", "macbook-pro", "openai", "gpt-6.1-terra",
                             [{"role": "user", "content": "hello"}], task="proof-task")
    out = fi.handle_request(body, view=view, serve_fn=stub_serve, my_id="macbook-pro")
    check("signed borrow from a trusted member is served",
          out["text"] == "borrowed-answer" and out["borrowed"] is True, str(out))
    ledger = (TMP / "data" / "usage_ledger.jsonl").read_text()
    rows = [json.loads(l) for l in ledger.splitlines() if "family-borrow" in l]
    check("the call lands in the SERVING device's ledger naming the requester",
          len(rows) == 1 and rows[0]["context"] == "family-borrow:razer:proof-task"
          and rows[0]["provider"] == "openai" and rows[0]["total_tokens"] == 18,
          ledger[-300:])

    unsigned = {"from": "razer", "to": "macbook-pro", "provider": "openai",
                "model": "gpt-6.1-terra", "messages": [{"role": "user", "content": "hi"}],
                "task": "", "ts": int(time.time()), "nonce": "x"}
    try:
        fi.handle_request(unsigned, view=view, serve_fn=stub_serve, my_id="macbook-pro")
        refused = False
    except fi.BorrowRefused:
        refused = True
    check("unsigned borrow request is refused", refused)

    forged_body = fi.signed_request(stranger, "razer", "macbook-pro", "openai",
                                    "gpt-6.1-terra", [{"role": "user", "content": "hi"}])
    try:
        fi.handle_request(forged_body, view=view, serve_fn=stub_serve, my_id="macbook-pro")
        refused = False
    except fi.BorrowRefused:
        refused = True
    check("borrow request signed by a stranger's key is refused", refused)

    write_registry(revoked)
    view3 = fr.load_registry()
    body = fi.signed_request(razer, "razer", "macbook-pro", "openai", "gpt-6.1-terra",
                             [{"role": "user", "content": "hello"}])
    try:
        fi.handle_request(body, view=view3, serve_fn=stub_serve, my_id="macbook-pro")
        refused = False
    except fi.BorrowRefused:
        refused = True
    check("revoked member's borrow request is refused", refused)
    write_registry(approved)

    # explicit blocked states on the borrowing side
    try:
        fi.borrow("127.0.0.1", "macbook-pro", "openai", "gpt-6.1-terra",
                  [{"role": "user", "content": "hi"}], identity=razer,
                  my_id="razer", port=1, timeout=2)
        blocked = False
    except fi.BorrowBlocked:
        blocked = True
    check("unreachable serving device is an explicit BorrowBlocked", blocked)

    set_setting(fi.LOCAL_ONLY_SETTING, "on")
    try:
        fi.borrow("100.119.42.98", "macbook-pro", "openai", "gpt-6.1-terra",
                  [{"role": "user", "content": "hi"}], identity=razer, my_id="razer")
        blocked = False
    except fi.BorrowBlocked:
        blocked = True
    check("local-only pin refuses to borrow, explicitly", blocked)
    set_setting(fi.LOCAL_ONLY_SETTING, "off")

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
