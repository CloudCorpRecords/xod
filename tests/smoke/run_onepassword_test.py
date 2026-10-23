"""
1Password (services/onepassword.py) with a stand-in `op` (tests/support/fake_op.py), a throwaway keyring and data
folder, never the real 1Password or Keychain:
  - not installed: Settings says how to get it, and nothing reads from it
  - connect, then search items and pick a field: Settings sees names and op:// references, never values
  - an API key saved as a reference: Xod uses the key, the vault holds only the reference
  - website logins: a site with no login saved in Xod uses its Login item in 1Password, only when that's turned on;
    a login saved in Xod wins
  - signed out: a clear message, no crash
  .venv/bin/python tests/smoke/run_onepassword_test.py
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-1password-"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1",
                   "XOD_SECRETS_SERVICE": "Xod test keys", "XOD_VAULT_PREFIX": "Xod test",
                   "XOD_VAULT_INDEX": str(TMP / "vault_index.json"),
                   "FAKE_OP_DATA": str(TMP / "op.json"), "FAKE_OP_LOG": str(TMP / "op.log")})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []

DATA = {"signed_in": True, "accounts": [{"email": "me@example.com", "url": "my.1password.com"}], "items": [
    {"id": "fw1", "title": "Fireworks", "category": "API_CREDENTIAL", "vault": {"name": "Private"}, "urls": [],
     "fields": [{"label": "credential", "type": "CONCEALED", "value": "fw_from_1password_123456"},
                {"label": "notesPlain", "type": "STRING", "value": ""}]},
    {"id": "in1", "title": "Indeed", "category": "LOGIN", "vault": {"name": "Private"}, "urls": [{"href": "https://secure.indeed.com/login"}],
     "fields": [{"label": "username", "type": "STRING", "value": "me@example.com"},
                {"label": "password", "type": "CONCEALED", "value": "hunter2-from-1p"}]},
]}


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def save_data():
    (TMP / "op.json").write_text(json.dumps(DATA))


async def main():
    from app.services import onepassword, secret_store, credential_vault, agent_clis
    bin_dir = TMP / "bin"
    bin_dir.mkdir()
    agent_clis.search_path = lambda: str(bin_dir) + os.pathsep + "/usr/bin:/bin"    # never the real op
    save_data()

    print("P1 not installed")
    s = onepassword.status()
    check("Settings says it isn't installed, with where to get it", not s["installed"] and s["install_url"].startswith("https://"))
    check("…and nothing is read from it", onepassword.read("op://Private/Fireworks/credential") == "")

    shutil.copy(ROOT / "tests/support/fake_op.py", bin_dir / "op")
    os.chmod(bin_dir / "op", 0o755)
    print("P2 connect, search, pick")
    s = onepassword.status()
    check("installed: shows the 1Password account, not connected yet", s["installed"] and not s["connected"] and s["accounts"][0]["email"] == "me@example.com")
    check("…and asking that never needed an approval (only `op account list`)",
          all(json.loads(l)[:2] == ["account", "list"] for l in (TMP / "op.log").read_text().splitlines()))
    check("connecting asks 1Password once", onepassword.connect()["email"] == "me@example.com" and onepassword.status()["connected"])
    found = onepassword.items("firew")
    check("search finds items by name", [i["title"] for i in found] == ["Fireworks"])
    fields = onepassword.fields("fw1")
    check("an item's fields come as names and op:// references", fields == [{"label": "credential", "type": "CONCEALED",
                                                                             "reference": "op://Private/Fireworks/credential"}], str(fields))
    check("…never values", "fw_from_1password" not in json.dumps(fields) and "fw_from_1password" not in json.dumps(found))

    print("P3 a key from 1Password")
    import httpx
    from app.main import app as xod_app
    from app.config import settings
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=xod_app), base_url="http://127.0.0.1:8000") as cl:
        r = (await cl.post("/api/1password/link", json={"name": "fireworks_api_key", "reference": "op://Private/Fireworks/credential"})).json()
        check("linking a field to the Fireworks key works", r.get("success") and r["masked"].startswith("1Password:"), str(r))
        check("Xod uses the key itself", settings.fireworks_api_key == "fw_from_1password_123456")
        keys = json.loads((TMP / "keys.json").read_text())
        check("…while the vault holds only the reference", "fw_from_1password" not in json.dumps(keys) and "op://Private/Fireworks" in json.dumps(keys))
        st = (await cl.get("/api/secrets")).json()
        fw = next(k for k in st["keys"] if k["name"] == "fireworks_api_key")
        check("Settings shows it comes from 1Password", fw["from_1password"] and "fw_from" not in fw["masked"])
        r = (await cl.post("/api/1password/link", json={"name": "not_a_key", "reference": "op://x/y/z"}))
        check("only Xod's own keys can be linked", r.status_code == 400)
    from app.services import keychain
    keychain.reset_cache()
    onepassword._values.clear()
    check("a fresh start reads it from 1Password again", secret_store.get("fireworks_api_key") == "fw_from_1password_123456")
    from app.config import update_env_variable
    update_env_variable("FIREWORKS_API_KEY", "op://Private/Fireworks/credential")
    check("a reference pasted into a key field works at once", settings.fireworks_api_key == "fw_from_1password_123456")

    print("P4 website logins")
    check("off by default: no 1Password login offered", credential_vault.get_login("https://indeed.com") is None
          and credential_vault.describe_login("indeed.com") is None)
    onepassword.set_logins(True)
    check("on: a site with no login in Xod uses its 1Password Login item", credential_vault.get_login("https://www.indeed.com/jobs") == ("me@example.com", "hunter2-from-1p"))
    d = credential_vault.describe_login("indeed.com")
    check("…and what the model is told has neither username nor password", d and "hunter2" not in json.dumps(d) and "me@example.com" not in json.dumps(d), str(d))
    check("a site with nothing in 1Password still has none", credential_vault.get_login("https://example.org") is None)
    credential_vault.save_login("indeed.com", "saved@xod.test", "saved-in-xod")
    check("a login saved in Xod wins", credential_vault.get_login("indeed.com") == ("saved@xod.test", "saved-in-xod"))
    credential_vault.delete_login("indeed.com")

    print("P5 signed out, disconnected")
    DATA["signed_in"] = False
    save_data()
    onepassword._values.clear()
    onepassword._items.update(at=0.0, items=None)
    try:
        onepassword.connect()
        check("signed out: connecting says what to turn on", False)
    except onepassword.OnePasswordError as e:
        check("signed out: connecting says what to turn on", "Integrate with 1Password CLI" in str(e), str(e))
    check("…a key just reads as missing", secret_store.get("fireworks_api_key") == "")
    check("…and logins fall back to none", credential_vault.get_login("indeed.com") is None)
    DATA["signed_in"] = True
    save_data()
    onepassword.disconnect()
    check("disconnected: nothing is read from 1Password", onepassword.read("op://Private/Fireworks/credential") == ""
          and credential_vault.get_login("indeed.com") is None)
    log = (TMP / "op.log").read_text()
    check("Xod never asked 1Password to sign in, make or change anything", not any(w in log for w in ('"signin"', '"create"', '"edit"', '"delete"')))


if __name__ == "__main__":
    asyncio.run(main())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed", flush=True)
    if failed:
        print("FAILED: " + "; ".join(failed), flush=True)
    os._exit(1 if failed else 0)
