"""
A key store that never answers must not hang Xod's start (app/services/keychain.py).

The 2026-11-08 Steam Deck hang: Xod restarted while the KDE Secret Service collection
was locked, secret_store.migrate() blocked forever inside the keyring's unlock(), and
Xod never bound its port — systemd said "active" the whole time. This test swaps in a
keyring whose reads sleep for two minutes (a locked collection waiting on a prompt
nobody will answer) and checks that migrate() and the startup reads come back within
the keyring timeout instead, that the vault reports itself locked, and that a store
which answers behaves exactly as before.

A throwaway file keyring underneath, never the real Keychain, no model needed:
  .venv/bin/python tests/smoke/run_keychain_timeout_test.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-keyring-timeout-"))
os.environ.pop("XOD_SECRETS_SERVICE", None)  # the real vault's name, as in run_keychain_test.py
os.environ["XOD_TEST_KEYRING_FILE"] = str(TMP / "keys.json")
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_KEYRING_TIMEOUT"] = "2"  # the test's bound; production default is 5

import keyring  # noqa: E402
from tests.support.filekeyring import FileKeyring  # noqa: E402


class HangingKeyring(FileKeyring):
    """Reads park on an unlock prompt forever, like a locked Secret Service collection."""

    def get_password(self, service, username):
        time.sleep(120)
        return super().get_password(service, username)


keyring.set_keyring(HangingKeyring())
from app.services import keychain, secret_store  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


def main():
    check("the test's timeout is the short one", keychain.KEYRING_TIMEOUT == 2.0, str(keychain.KEYRING_TIMEOUT))

    start = time.monotonic()
    report = secret_store.migrate()
    took = time.monotonic() - start
    check("migrate() returns instead of hanging on the locked keyring", isinstance(report, dict) and took < 20,
          f"took {took:.1f}s")

    start = time.monotonic()
    values = [secret_store.get(f) for f in secret_store.SECRET_FIELDS]
    took = time.monotonic() - start
    check("startup reads come back empty, fast (cooldown, not one stall per key)",
          all(v == "" for v in values) and took < 10, f"took {took:.1f}s, values={values!r}")

    start = time.monotonic()
    problem = keychain.problem()
    took = time.monotonic() - start
    check("problem() says the key store looks locked, without stalling",
          problem is not None and "locked" in problem and took < 10, f"took {took:.1f}s, problem={problem!r}")
    check("health() agrees the keys are not readable", secret_store.health()["readable"] is False)

    try:
        secret_store.save("tavily_api_key", "tvly-never-stored")
        check("saving against the locked keyring fails as Unreadable, not a hang", False)
    except keychain.Unreadable:
        check("saving against the locked keyring fails as Unreadable, not a hang", True)
    except Exception as e:
        check("saving against the locked keyring fails as Unreadable, not a hang", False, type(e).__name__)

    # A store that answers: nothing about the timeout machinery may change behaviour.
    keyring.set_keyring(FileKeyring())
    keychain.reset_cache()
    secret_store.save("tavily_api_key", "tvly-fast-123456789")
    check("once the key store answers, save + read work as before",
          secret_store.get("tavily_api_key") == "tvly-fast-123456789")
    check("…and health() is readable again", secret_store.health()["readable"] is True)
    start = time.monotonic()
    secret_store.migrate()
    check("migrate() against the answering store is fast", time.monotonic() - start < 10)

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
