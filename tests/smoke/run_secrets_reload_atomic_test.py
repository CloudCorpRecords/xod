"""
reload_secrets() must be atomic from the reader's perspective (AGE-74).

The defect (Hydrogen Round 9, accepted as a v1.1.6 follow-up and filed as
AGE-74): app/config.py reload_secrets() first reset the vault cache, then
overwrote the live settings snapshot field by field with whatever
secret_store.get() returned at that moment. If the key store turned
unreadable between the caller's problem() check and the fresh load (the
prompt was dismissed, the store relocked), every get() came back "" and
the reload wiped all in-memory keys, including keys saved in-process via
update_env_variable, until the next successful reload. Even on the happy
path the swap was not a swap: a reader polling the snapshot during a
reload could observe a mix of the old and new snapshots, and two
concurrent reloads could reset the vault cache under each other.

The fix: reload_secrets() reads the new snapshot fully into a local dict
and swaps the live settings over in a single update only after that read
has fully succeeded; a reload whose vault read fails leaves the previous
snapshot untouched and marks it stale. Reloads are serialized.

Covered here, all in ONE process:
  - phase A: a reload while the vault has turned unreadable preserves
    the last good snapshot exactly (including a key saved in-process),
    reports readable False and stays stale, while a reader thread reads
    the snapshot and secrets health continuously across the reload.
  - phase B: a successful reload swaps v1 to v2 in one step: a reader
    reading health and the snapshot continuously across a deliberately
    slowed reload only ever observes complete snapshots (v1 or v2),
    never a mix and never a cleared field, and health reads readable
    throughout.
  - phase C: concurrent reloads serialize and land the same complete
    snapshot; no reader observation is mixed or cleared.
  - phase D (control): a plain reload against an unchanged readable
    vault returns readable and leaves the snapshot as it was.

A throwaway file keyring underneath, never the real Keychain:
  .venv/bin/python tests/smoke/run_secrets_reload_atomic_test.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-secrets-reload-atomic-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["XOD_TEST_KEYRING_FILE"] = str(TMP / "keys.json")
(TMP / "data").mkdir()

SERVICE = "Xod test keys"
V1 = {
    "openai_api_key": "sk-v1-openai-1111111111",
    "fireworks_api_key": "fw_v1-fireworks-111111",
    "tavily_api_key": "tvly-v1-tavily-111111111",
}
GROQ_V1 = "gsk-v1-groq-1111111111"  # saved in-process via update_env_variable in phase A
V2 = {
    "openai_api_key": "sk-v2-openai-2222222222",
    "fireworks_api_key": "fw_v2-fireworks-222222",
    "tavily_api_key": "tvly-v2-tavily-222222222",
}
OPENAI_V3 = "sk-v3-openai-3333333333"

# The vault as an earlier, healthy run left it: keys saved, in the one vault item.
vault = {"secrets": {f"{SERVICE}\n{f}": v for f, v in V1.items()}, "checked": []}
(TMP / "keys.json").write_text(json.dumps({f"{SERVICE}\x00_vault": json.dumps(vault)}))

import keyring  # noqa: E402
from tests.support.filekeyring import FileKeyring  # noqa: E402


class FlakyKeyring(FileKeyring):
    """Answers normally while mode is "ok"; while mode is "fail" every read
    raises at once, the shape of a vault that turned unreadable between the
    caller's problem() check and reload_secrets()' fresh load."""

    mode = "ok"

    def get_password(self, service, username):
        if FlakyKeyring.mode == "fail":
            raise RuntimeError("the key store turned unreadable")
        return super().get_password(service, username)


keyring.set_keyring(FlakyKeyring())

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


def main():
    from app import config
    from app.services import keychain, secret_store

    fields = list(secret_store.SECRET_FIELDS)

    def snap():
        return tuple(getattr(config.settings, f) for f in fields)

    def expected(**overrides):
        base = {f: "" for f in fields}
        base.update(overrides)
        return tuple(base[f] for f in fields)

    S1 = expected(**V1, groq_api_key=GROQ_V1)
    S2 = expected(**V2)  # groq deleted from the vault in phase B
    S3 = expected(**{**V2, "openai_api_key": OPENAI_V3})

    # ---- startup on a healthy vault: the snapshot holds the seeded keys.
    check("startup snapshot holds the seeded keys", snap() == expected(**V1), str(snap()))
    check("startup snapshot is not stale", config._secrets_stale is False)

    # A key saved in-process (the Hydrogen case): update_env_variable puts
    # it in the vault and in the live snapshot in the same call.
    config.update_env_variable("GROQ_API_KEY", GROQ_V1)
    check("in-process saved key is in the snapshot", snap() == S1, str(snap()))

    # ---- continuous reader: records only STABLE snapshots (two identical
    # back-to-back reads), so a sample straddling the one-instant swap on
    # the fixed code cannot be mistaken for a mixed state, while a mixed
    # state that persists (the old field-by-field overwrite, or a cleared
    # snapshot) is recorded many times over.
    class Reader:
        def __init__(self):
            self.stop = threading.Event()
            self.stable = []
            self.health_flags = []
            self.thread = threading.Thread(target=self._run, daemon=True)

        def _run(self):
            while not self.stop.is_set():
                a, b = snap(), snap()
                if a == b:
                    self.stable.append(a)
                try:
                    self.health_flags.append(bool(secret_store.health()["readable"]))
                except Exception:
                    self.health_flags.append(None)
                time.sleep(0.001)

        def __enter__(self):
            self.thread.start()
            return self

        def __exit__(self, *exc):
            self.stop.set()
            self.thread.join(timeout=10)
            return False

    # ---- phase A: the vault turns unreadable; the reload must fail
    # without touching the snapshot.
    FlakyKeyring.mode = "fail"
    with Reader() as reader:
        health = config.reload_secrets()
        time.sleep(0.05)  # let the reader keep polling the failed state
    FlakyKeyring.mode = "ok"
    check("a reload against an unreadable vault reports readable False",
          health["readable"] is False, str(health))
    check("the failed reload leaves the snapshot stale", config._secrets_stale is True)
    check("the failed reload preserves the last good snapshot exactly",
          snap() == S1, str(snap()))
    check("the reader polled continuously during the failed reload",
          len(reader.stable) >= 20, f"polls={len(reader.stable)}")
    check("the reader never saw a cleared or mixed snapshot during the failed reload",
          all(s == S1 for s in reader.stable),
          f"distinct={ {s for s in reader.stable if s != S1} }")

    # ---- phase B: a successful reload swaps v1 to v2 in one step, even
    # when every field read is slow (the reader's window is wide open).
    keychain.reset_cache()  # the "Try again" action: clears the outage cooldown before the vault-side writes
    for f, v in V2.items():
        secret_store.save(f, v)
    secret_store.save("groq_api_key", "")
    check("vault-side changes do not leak into the snapshot before a reload",
          snap() == S1, str(snap()))

    real_get = secret_store.get

    def slow_get(name):
        time.sleep(0.08)
        return real_get(name)

    secret_store.get = slow_get
    try:
        with Reader() as reader:
            health = config.reload_secrets()
    finally:
        secret_store.get = real_get
    check("the slowed reload reports readable True", health["readable"] is True, str(health))
    check("the slowed reload clears the stale flag", config._secrets_stale is False)
    check("the snapshot is exactly v2 after the reload", snap() == S2, str(snap()))
    check("the pydantic view agrees with the swapped snapshot",
          config.settings.model_dump()["openai_api_key"] == V2["openai_api_key"])
    check("the reader polled continuously during the slowed reload",
          len(reader.stable) >= 20, f"polls={len(reader.stable)}")
    check("the reader only ever saw complete snapshots (v1 or v2) during the reload",
          all(s in (S1, S2) for s in reader.stable),
          f"mixed={ [s for s in reader.stable if s not in (S1, S2)][:3] }")
    check("health read readable continuously during the successful reload",
          len(reader.health_flags) >= 20 and all(f is True for f in reader.health_flags),
          f"flags={reader.health_flags[:20]}")

    # ---- phase C: concurrent reloads serialize and stay consistent.
    secret_store.save("openai_api_key", OPENAI_V3)
    secret_store.get = slow_get
    errors = []
    try:
        with Reader() as reader:
            def call_reload():
                try:
                    config.reload_secrets()
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            threads = [threading.Thread(target=call_reload) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
    finally:
        secret_store.get = real_get
    check("concurrent reloads all complete without errors", not errors, str(errors))
    check("the snapshot after concurrent reloads is exactly v3", snap() == S3, str(snap()))
    check("the reader only ever saw complete snapshots during concurrent reloads",
          all(s in (S2, S3) for s in reader.stable),
          f"mixed={ [s for s in reader.stable if s not in (S2, S3)][:3] }")

    # ---- phase D (control): a plain reload on an unchanged readable
    # vault is a no-op for the snapshot.
    health = config.reload_secrets()
    check("control reload returns readable and leaves the snapshot as it was",
          health["readable"] is True and snap() == S3 and config._secrets_stale is False,
          str(health))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
