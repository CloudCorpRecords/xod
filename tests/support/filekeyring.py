"""A throwaway keyring for tests: a JSON file named by XOD_TEST_KEYRING_FILE, never the real Keychain.
Use with PYTHON_KEYRING_BACKEND=tests.support.filekeyring.FileKeyring."""
import json
import os

from keyring.backend import KeyringBackend


class FileKeyring(KeyringBackend):
    priority = 1

    def _path(self):
        if not os.environ.get("XOD_TEST_KEYRING_FILE"):      # a test that didn't name one: a throwaway file for this run
            import tempfile
            os.environ["XOD_TEST_KEYRING_FILE"] = os.path.join(tempfile.mkdtemp(prefix="xod-keys-"), "keys.json")
        return os.environ["XOD_TEST_KEYRING_FILE"]

    def _load(self):
        try:
            with open(self._path()) as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def get_password(self, service, username):
        return self._load().get(f"{service}\x00{username}")

    def set_password(self, service, username, password):
        d = self._load()
        d[f"{service}\x00{username}"] = password
        with open(self._path(), "w") as f:
            json.dump(d, f)

    def delete_password(self, service, username):
        d = self._load()
        if d.pop(f"{service}\x00{username}", None) is None:  # like the real Keychain
            from keyring.errors import PasswordDeleteError
            raise PasswordDeleteError(username)
        with open(self._path(), "w") as f:
            json.dump(d, f)
