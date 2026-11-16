"""
The settings snapshot must heal itself after a key store outage at startup
(app/config.py maybe_heal_secrets, hooked into setup_wizard.saved_keys and
the GET /api/config and GET /api/secrets handlers).

The field failure (2026-11-14, diagnosis in
~/workspace/xod-eval/XOD-PROVIDER-PICKER-DIAGNOSIS-2026-11-14.md): Xod
started while the macOS keychain prompt for the new install sat unanswered.
The vault read hit keychain.py's designed 5s timeout, so config.py's
import-time settings snapshot read "no keys" — and nothing ever refreshed
it. The vault held OpenAI, Fireworks and Tavily keys the whole time and
/api/secrets/health read green, yet the BRAIN picker offered no Online
group and the Settings key cards showed every key disconnected, until a
manual POST /api/secrets/retry (or a restart) re-read the vault.

Here a fake keyring parks its reads past the timeout while "blocked" (the
unanswered prompt) and answers once "access is granted", in ONE process:
the snapshot starts stale, and the read paths must then gain the Online
group and flip the /api/config flags on their own — nobody calls the retry
endpoint and nothing restarts. The startup timeout behaviour itself is
untouched: the blocked startup still returns in bounded time.

A throwaway file keyring underneath, never the real Keychain:
  .venv/bin/python tests/smoke/run_secrets_snapshot_heal_test.py
"""
import asyncio
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
TMP = Path(tempfile.mkdtemp(prefix="xod-secrets-heal-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["XOD_TEST_KEYRING_FILE"] = str(TMP / "keys.json")
os.environ["XOD_KEYRING_TIMEOUT"] = "1"  # the test's bound; production default is 5
(TMP / "data").mkdir()

SERVICE = "Xod test keys"
SEEDED = {
    "openai_api_key": "sk-test-openai-1234567890",
    "fireworks_api_key": "fw_test-fireworks-123456",
    "tavily_api_key": "tvly-test-tavily-123456789",
}

# The vault as an earlier, healthy run left it: keys saved, in the one vault item.
vault = {"secrets": {f"{SERVICE}\n{f}": v for f, v in SEEDED.items()}, "checked": []}
(TMP / "keys.json").write_text(json.dumps({f"{SERVICE}\x00_vault": json.dumps(vault)}))

import keyring  # noqa: E402
from tests.support.filekeyring import FileKeyring  # noqa: E402


class PromptKeyring(FileKeyring):
    """Reads park on an unlock prompt while blocked (past the keyring timeout);
    once the person answers the prompt, the same store answers at once."""

    blocked = True

    def get_password(self, service, username):
        if PromptKeyring.blocked:
            time.sleep(30)
        return super().get_password(service, username)


keyring.set_keyring(PromptKeyring())
from app.services import keychain  # noqa: E402

keychain.KEYRING_COOLDOWN = 2.0        # the test's cooldowns; production is 60s
keychain.UNREADABLE_COOLDOWN_SEC = 2.0

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


def main():
    # ---- startup while the prompt is unanswered: the snapshot goes stale,
    # and getting there must stay bounded (the 5s design, 1s here).
    start = time.monotonic()
    from app import config
    took = time.monotonic() - start
    check("startup with a parked key store still returns in bounded time", took < 20, f"took {took:.1f}s")
    check("snapshot built during the outage reads no keys (the field state)",
          config.settings.openai_api_key == "" and config.settings.tavily_api_key == "")
    check("the snapshot knows it is stale", config._secrets_stale is True)
    check("the vault layer reports the problem", keychain.problem() is not None)

    from app.services import setup_wizard
    from app import main as app_main

    # ---- while the store is still blocked, the read paths serve the stale
    # snapshot and the heal must not fire early (or stall).
    start = time.monotonic()
    keys = setup_wizard.saved_keys()
    took = time.monotonic() - start
    check("saved_keys() is empty while blocked, without stalling", keys == [] and took < 10,
          f"keys={keys}, took {took:.1f}s")
    quick = asyncio.run(setup_wizard.quick())
    check("the picker has no Online group while blocked",
          not any(c["group"] == "Online" for c in quick["choices"]), str(quick["choices"]))
    cfg = asyncio.run(app_main.get_config())
    check("/api/config flags are false while blocked",
          cfg["has_openai_key"] is False and cfg["tavily_configured"] is False
          and cfg["has_fireworks_key"] is False, str({k: cfg[k] for k in ("has_openai_key", "tavily_configured", "has_fireworks_key")}))

    # ---- the person answers the prompt (Always Allow): the store answers now.
    PromptKeyring.blocked = False
    time.sleep(2.6)  # past the (test) cooldowns, so the next read really retries

    # saved_keys() is the first read path touched: it must heal the snapshot
    # by itself — no retry endpoint, no restart.
    keys = setup_wizard.saved_keys()
    check("saved_keys() heals once the store answers", keys == ["openai", "fireworks"], str(keys))
    check("the snapshot itself now holds the keys",
          config.settings.openai_api_key == SEEDED["openai_api_key"]
          and config.settings.tavily_api_key == SEEDED["tavily_api_key"])
    check("the stale flag cleared", config._secrets_stale is False)

    quick = asyncio.run(setup_wizard.quick())
    online = {(c["provider"]) for c in quick["choices"] if c["group"] == "Online"}
    check("/api/models/quick gains the Online group (openai + fireworks)",
          online == {"openai", "fireworks"}, str(quick["choices"]))
    cfg = asyncio.run(app_main.get_config())
    check("/api/config flags flip to the vault",
          cfg["has_openai_key"] is True and cfg["has_fireworks_key"] is True
          and cfg["tavily_configured"] is True and cfg["has_tavily_key"] is True,
          str({k: cfg[k] for k in ("has_openai_key", "has_fireworks_key", "tavily_configured", "has_tavily_key")}))

    # ---- phase B: poison the snapshot again (a reload while blocked, the
    # retry endpoint pressed too early) and let GET /api/config be the FIRST
    # read path after access returns: it must heal on its own too.
    # AGE-74 update: a failed reload no longer clears the snapshot (that
    # clearing was the defect); it leaves the last good keys in place and
    # marks the snapshot stale, so the setup state below is "stale with
    # keys" where this suite originally asserted "stale and empty".
    PromptKeyring.blocked = True
    health = config.reload_secrets()
    check("a reload during an outage leaves the snapshot stale, not healed-wrong",
          health["readable"] is False
          and config.settings.openai_api_key == SEEDED["openai_api_key"]
          and config._secrets_stale is True, str(health))
    PromptKeyring.blocked = False
    time.sleep(2.6)
    cfg = asyncio.run(app_main.get_config())
    check("GET /api/config heals the snapshot on its own after an outage",
          cfg["has_openai_key"] is True and cfg["tavily_configured"] is True
          and config.settings.openai_api_key == SEEDED["openai_api_key"],
          str({k: cfg[k] for k in ("has_openai_key", "tavily_configured")}))

    # ---- phase C: poison once more and let GET /api/secrets be the FIRST
    # read path after access returns: it must heal on its own too (this
    # handler was named in the design but never exercised until now).
    PromptKeyring.blocked = True
    config.reload_secrets()
    check("phase C setup: snapshot stale again", config._secrets_stale is True
          and config.settings.openai_api_key == SEEDED["openai_api_key"])
    PromptKeyring.blocked = False
    time.sleep(2.6)
    res = asyncio.run(app_main.secrets_list())
    check("GET /api/secrets heals the snapshot on its own after an outage",
          config.settings.openai_api_key == SEEDED["openai_api_key"]
          and config._secrets_stale is False and res.get("readable") is True,
          str({k: res.get(k) for k in ("readable",)}))

    # ---- phase D: concurrent heal callers must not race. The read paths
    # serve from worker threads (and saved_keys from more than one place);
    # with a stale snapshot and the store answering, eight simultaneous
    # maybe_heal_secrets() calls must produce exactly ONE reload, not a
    # pile-up of overlapping reloads resetting the vault cache under each
    # other (Hydrogen Round 9). The counting wrapper sleeps so the race
    # window is wide open on an unsynchronized implementation.
    PromptKeyring.blocked = True
    config.reload_secrets()
    PromptKeyring.blocked = False
    time.sleep(2.6)
    real_reload = config.reload_secrets
    reload_calls = []

    def counting_reload():
        reload_calls.append(1)
        time.sleep(0.3)
        return real_reload()

    config.reload_secrets = counting_reload
    try:
        barrier = threading.Barrier(8)
        errors = []

        def caller():
            try:
                barrier.wait(timeout=10)
                config.maybe_heal_secrets()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=caller) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    finally:
        config.reload_secrets = real_reload
    check("concurrent heal callers run exactly one reload",
          len(reload_calls) == 1 and not errors,
          f"reloads={len(reload_calls)}, errors={errors}")
    check("the snapshot is healed and consistent after the race",
          config._secrets_stale is False
          and config.settings.openai_api_key == SEEDED["openai_api_key"]
          and config.settings.tavily_api_key == SEEDED["tavily_api_key"])

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
