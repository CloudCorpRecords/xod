"""
Config drift regression (app/config.py load_synced_settings + storage.has_setting):
a SAVED settings value wins verbatim over the .env fallback, even when the
saved value is empty. An empty llm_base_url means "the provider's own
endpoint"; treating it as unset resurrected the LM Studio default URL
(http://localhost:1234/v1) next to provider openai at every launch
(2026-11-01 keys/config drift). Also checks the fallback still works when
nothing was ever saved, and that a saved non-empty value beats the env.

Each scenario runs in a fresh interpreter with an isolated XOD_DATA_DIR and
the file keyring, so no real data and no real Keychain is ever touched.

  python tests/smoke/run_config_drift_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers

CHILD = r"""
import json, os, sys
sys.path.insert(0, os.environ["XOD_REPO_ROOT"])
from app.services.storage import init_db, set_setting, has_setting
init_db()
saved = json.loads(os.environ["XOD_TEST_SAVED"])
for k, v in saved.items():
    set_setting(k, v)
print("has_base_url_row:", has_setting("llm_base_url"))
from app.config import load_synced_settings
s = load_synced_settings()
print(json.dumps({"llm_provider": s.llm_provider, "llm_base_url": s.llm_base_url, "llm_model": s.llm_model}))
"""

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


def scenario(saved, env_extra):
    tmp = tempfile.mkdtemp(prefix="xod-config-drift-")
    env = dict(os.environ)
    env.update({
        "XOD_DATA_DIR": tmp,
        "XOD_SPEECH": "off",
        "XOD_REPO_ROOT": str(ROOT),
        "XOD_TEST_SAVED": json.dumps(saved),
        "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
    })
    env.pop("LLM_PROVIDER", None)
    env.pop("LLM_BASE_URL", None)
    env.pop("LLM_MODEL", None)
    env.update(env_extra)
    r = subprocess.run([sys.executable, "-c", CHILD], env=env,
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr, file=sys.stderr)
        raise SystemExit(f"child failed rc={r.returncode}")
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    has_row = lines[0].endswith("True")
    return has_row, json.loads(lines[-1])


STALE = {"LLM_BASE_URL": "http://localhost:1234/v1"}

# 1. The drift itself: saved empty base_url, stale LM Studio URL in the env.
has_row, s = scenario(
    {"llm_provider": "openai", "llm_base_url": "", "llm_model": "gpt-6.1-terra"}, STALE)
check("saved row exists for llm_base_url", has_row)
check("saved empty base_url wins over stale env", s["llm_base_url"] == "")
check("provider from DB", s["llm_provider"] == "openai")
check("model from DB", s["llm_model"] == "gpt-6.1-terra")

# 2. Nothing saved: the env fallback still applies (fresh-install path).
has_row, s = scenario({}, STALE)
check("no saved row reported", not has_row)
check("env fallback applies when unsaved", s["llm_base_url"] == "http://localhost:1234/v1")

# 3. A saved non-empty value beats a different env value.
has_row, s = scenario(
    {"llm_provider": "openai", "llm_base_url": "https://example.invalid/v1", "llm_model": "gpt-6.1-terra"}, STALE)
check("saved non-empty base_url wins over env", s["llm_base_url"] == "https://example.invalid/v1")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
