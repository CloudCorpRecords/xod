"""
Vault apply refreshes the running process's settings snapshot
(services/family_vault.py apply + app/config.py reload_secrets).

The defect (Run 6 attempt 3, 2026-11-09): a family vault apply lands
delivered keys in the device's keystore, and readers of the keystore
see them at once — but step execution (services/harness.py
_tool_config) and /api/config read provider keys from the config
settings snapshot taken at process start, which nothing refreshed.
A device that received its OpenAI key through the family channel
reported keys_present openai=true on the sync read-back while its
first bench step died with "there's no OpenAI key saved", and only a
restart closed the gap.

Covered here, all in ONE process (the process IS the long-lived
device process; no restart, no manual reload after the apply):
  - before the delivery, the step-executor key view is keyless
    (negative control: this is the state attempt 3's step 1 saw)
  - a vault apply lands the key in the keystore AND in the settings
    snapshot the step executor and /api/config read
  - the applied result reports settings_refreshed
  - a second delivery (rotated value) refreshes the snapshot again

All keys in this suite are test values, never real credentials.
Run: .venv/bin/python tests/smoke/run_vault_apply_refresh_test.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-vault-apply-refresh-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir()

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


FAMILY = "test-family"
ACCOUNT = "cloudtheboy@gmail.com"
REG = TMP / "registry.json"


def device_record(ident, did, name, platform, endpoint, caps):
    rec = {"id": did, "name": name, "platform": platform, "endpoint": endpoint,
           "capabilities": caps, "status": "active",
           "last_seen": "2026-11-01T20:00:00-07:00", "account": ACCOUNT}
    from app.services import family_registry
    return family_registry.sign_record(rec, ident, FAMILY, ACCOUNT)


def main():
    from app.services import family_registry as fr
    from app.services import family_vault as fv
    from app.services import mail_crypto as mc
    from app.services import secret_store
    from app import config
    from app.services import harness

    mac = mc.Identity.new()
    razer = mc.Identity.new()

    base = {"family": FAMILY, "account": ACCOUNT, "version": 2,
            "updated": "2026-11-01T20:00:00-07:00",
            "devices": [
                device_record(mac, "macbook-pro", "MacBook Pro", "macOS", "100.119.42.98",
                              ["cloud model: openai gpt-6.1-terra"]),
                device_record(razer, "razer", "razer-box", "Windows 11", "100.80.212.74",
                              ["local model: lm_studio prism-ml/bonsai-27b"]),
            ]}
    approved = fr.sign_approval(json.loads(json.dumps(base)), mac, "macbook-pro")
    REG.write_text(json.dumps(approved))
    os.environ["XOD_FAMILY_REGISTRY_PATH"] = str(REG)
    os.environ.pop("XOD_FAMILY_VAULT_PATH", None)  # bundle sits beside the registry
    view = fr.load_registry()
    check("signed registry loads with both members trusted",
          [d["id"] for d in view["trusted_members"]] == ["macbook-pro", "razer"],
          str(view["trusted_members"]))

    # ---- process start, keyless: pin the settings snapshot to the
    # keyless keystore exactly as a process that booted before the
    # delivery would hold it.
    secret_store.save("openai_api_key", "")
    config.reload_secrets()
    check("negative control: snapshot is keyless before the delivery",
          config.settings.openai_api_key == "")
    check("negative control: the step-executor key view is keyless",
          harness._tool_config()["keys_present"]["openai"] is False)

    # ---- the family delivery lands in this same process
    out = fv.publish({"openai_api_key": "sk-test-not-real"},
                     "macbook-pro", view=view, identity=mac)
    check("publish writes a bundle for both trusted members",
          out["recipients"] == ["macbook-pro", "razer"] and out["version"] == 1, str(out))
    got = fv.apply("razer", view=view, identity=razer)
    check("member applies the bundle", got["state"] == "applied", str(got))
    check("the applied result reports the settings refresh",
          got.get("settings_refreshed") is True, str(got))
    check("the key is in the member's keystore",
          secret_store.get("openai_api_key") == "sk-test-not-real")

    # ---- THE regression: the snapshot step execution and /api/config
    # read now holds the delivered key, same process, no restart.
    check("settings snapshot holds the delivered key in the same process",
          config.settings.openai_api_key == "sk-test-not-real",
          f"settings.openai_api_key={config.settings.openai_api_key!r}")
    check("the step-executor key view (harness._tool_config) sees the key",
          harness._tool_config()["keys_present"]["openai"] is True)
    check("/api/config's has_openai_key source reads true",
          bool(config.settings.openai_api_key) is True)

    # ---- a second delivery refreshes again (rotation is not one-shot)
    fv.publish({"openai_api_key": "sk-test-not-real-v2"},
               "macbook-pro", view=view, identity=mac)
    got = fv.apply("razer", view=view, identity=razer)
    check("second delivery applies and refreshes",
          got["state"] == "applied" and got.get("settings_refreshed") is True, str(got))
    check("snapshot holds the rotated key",
          config.settings.openai_api_key == "sk-test-not-real-v2"
          and harness._tool_config()["keys_present"]["openai"] is True)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
