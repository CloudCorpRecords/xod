"""
One Keychain item for all of Xod's secrets (app/services/keychain.py): keys saved the old way (one
item each) move in the first time they're asked for, a fresh start reads the Keychain once no matter
how many keys there are (macOS asks for each item it reads), and saving or removing a key clears the
old item too.

A throwaway file keyring, never the real Keychain, no model needed:
  .venv/bin/python tests/smoke/run_keychain_test.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-keychain-"))
os.environ.pop("XOD_SECRETS_SERVICE", None)  # the real vault's name, so the old-item migration runs (on the file keyring)
os.environ["XOD_TEST_KEYRING_FILE"] = str(TMP / "keys.json")
os.environ["XOD_DATA_DIR"] = str(TMP / "data")

import keyring  # noqa: E402
from tests.support.filekeyring import FileKeyring  # noqa: E402

reads = []


class CountingKeyring(FileKeyring):
    def get_password(self, service, username):
        reads.append((service, username))
        return super().get_password(service, username)


keyring.set_keyring(CountingKeyring())
from app.services import keychain, secret_store  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


def vault_json():
    """The vault as stored: one "_vault" item, or on Windows (2,560 bytes an item) "_vault" plus its pieces."""
    raw = keyring.get_password("Xod API keys", "_vault")
    head = json.loads(raw)
    if "pieces" in head:
        raw = "".join(keyring.get_password("Xod API keys", f"_vault.{i}") for i in range(1, head["pieces"] + 1))
    return json.loads(raw)


def one_vault_read(r):
    """A launch's reads are just the vault: "_vault", and on Windows its pieces."""
    return r and r[0] == ("Xod API keys", "_vault") and all(s == "Xod API keys" and a.startswith("_vault.") for s, a in r[1:])


def main():
    # Saved the old way: one item per key.
    keyring.set_password("Xod API keys", "tavily_api_key", "tvly-old-123456789")
    keyring.set_password("Xod MCP", "github:bearer_token", "ghp-old-123456789")
    keyring.set_password("Xod identity", "sign", "sign-secret")
    check("an old key is still found", secret_store.get("tavily_api_key") == "tvly-old-123456789")
    check("…and so are connector tokens and identity keys",
          keychain.get_password("Xod MCP", "github:bearer_token") == "ghp-old-123456789" and keychain.get_password("Xod identity", "sign") == "sign-secret")
    vault = vault_json()
    check("they're now in the one vault item", len(vault["secrets"]) == 3, json.dumps(sorted(vault["secrets"])))

    # Everything a launch reads. The first launch after the update also looks once for each old item
    # (a lookup for an item that doesn't exist never prompts); every launch after that reads one item.
    def launch():
        keychain.reset_cache()
        reads.clear()
        secret_store.migrate()
        for f in secret_store.SECRET_FIELDS:
            secret_store.get(f)
        secret_store.status()
        keychain.get_password("Xod MCP", "github:bearer_token")
        keychain.get_password("Xod identity", "sign")
        keychain.get_password("Xod identity", "box")
        return list(reads)
    first = launch()
    check("the first launch after the update never re-reads an old item it already moved",
          not any(r in first for r in [("Xod API keys", "tavily_api_key"), ("Xod MCP", "github:bearer_token"), ("Xod identity", "sign")]), str(first))
    check("every launch after that reads the Keychain once", one_vault_read(launch()), str(reads))

    secret_store.save("tavily_api_key", "tvly-new-987654321")
    check("saving a key updates the vault", secret_store.get("tavily_api_key") == "tvly-new-987654321")
    check("…and removes the old item", FileKeyring().get_password("Xod API keys", "tavily_api_key") is None)
    secret_store.save("tavily_api_key", "")
    keychain.reset_cache()
    check("removing a key forgets it", secret_store.get("tavily_api_key") == "")
    keychain.delete_password("Xod MCP", "github:bearer_token")
    keychain.reset_cache()
    check("removing a connector token forgets it everywhere",
          keychain.get_password("Xod MCP", "github:bearer_token") is None and FileKeyring().get_password("Xod MCP", "github:bearer_token") is None)
    try:
        keychain.delete_password("Xod MCP", "never-saved")
        check("removing something that isn't there says so", False)
    except keyring.errors.PasswordDeleteError:
        check("removing something that isn't there says so", True)

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
