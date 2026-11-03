"""
Boot-path guard: an automatic pick must never persist over a saved
provider (fix lane 0.9.25, the 0.9.24 ship's failed Fix-1 acceptance).

What 0.9.24 proved live: PR #34 taught the boot pass's prefer_key()
call to respect a saved provider, and the Razer's first boot on
0.9.24 read lm_studio / prism-ml/bonsai-27b exactly as saved. A full
stop + relaunch then rewrote the SAVED settings to fireworks /
accounts/fireworks/models/glm-5p3-flash (settings updated_at
2026-11-02T09:57:32, model_chosen_by "auto") — an auto pick persisted
over the member's saved provider, through a boot-path caller the
PR #34 guard did not cover. The same store read back differently
across that morning (saved lm_studio in one read, rewritten
fireworks in the next), so a guard that lives only in each caller's
read of the saved state is not enough.

The invariant this suite pins, at the one write choke point every
boot path funnels through (setup_wizard.choose): with how="auto",
a pick may fill a choice nobody has saved, and may revise Xod's
OWN pick (model_chosen_by "auto", the designed self-healing), but
must NEVER persist over a provider the person saved
(model_chosen_by "you") — whatever the caller believed.

Checks G2/G3 are the failing-first core: they call the boot path's
final writer directly in the Razer shape and fail on the pristine
tree, where choose(how="auto") happily overwrites the person's
pick. G6/G7 run the real autoconnect.check() boot pass with the
local probe absent and slow: pins that the whole pass leaves the
saved state untouched.

Run: .venv/bin/python tests/smoke/run_provider_boot_guard_test.py
"""
import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-provider-boot-guard-"))


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1",
                   "XOD_AUTOCONNECT": "off",
                   "OLLAMA_HOST": f"127.0.0.1:{_closed_port()}"})
for k in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
          "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "FIREWORKS_API_KEY"):
    os.environ.pop(k, None)
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def saved_state(settings, get_setting):
    return (settings.llm_provider, settings.llm_model, settings.llm_base_url,
            get_setting("llm_provider", ""), get_setting("llm_model", ""),
            get_setting("llm_base_url", ""))


RAZER = ("lm_studio", "prism-ml/bonsai-27b", "http://localhost:1234/v1",
         "lm_studio", "prism-ml/bonsai-27b", "http://localhost:1234/v1")


def main() -> None:
    from app.config import settings
    from app.services import llm_routing, secret_store, setup_wizard
    from app.services.storage import delete_setting, get_setting

    llm_routing.save_settings(mode="off")

    print("G1 the Razer shape: the person's saved local pick, keys arrived by vault")
    pick = setup_wizard.choose("lm_studio", "prism-ml/bonsai-27b", "http://localhost:1234/v1", how="you")
    check("the person's local pick is saved", pick.get("success") and saved_state(settings, get_setting) == RAZER,
          str(pick))
    secret_store.save("openai_api_key", "<redacted>")
    secret_store.save("fireworks_api_key", "<redacted>")
    settings.openai_api_key = "<redacted>"     # what the next boot's settings load sees
    settings.fireworks_api_key = "<redacted>"
    check("both cloud keys are present", setup_wizard.saved_keys() == ["openai", "fireworks"],
          str(setup_wizard.saved_keys()))

    print("G2 an auto pick of a cloud default must not persist over the person's pick")
    res = setup_wizard.choose("fireworks", "accounts/fireworks/models/glm-5p3-flash", how="auto")
    check("auto choose to fireworks is refused", res.get("success") is False, str(res))
    check("saved + runtime state exactly as the person left it",
          saved_state(settings, get_setting) == RAZER and setup_wizard.chosen_by() == "you",
          str(saved_state(settings, get_setting)))

    print("G3 an auto pick of a local server must not persist over the person's pick either")
    res = setup_wizard.choose("ollama", "llama3.1", how="auto")
    check("auto choose to ollama is refused", res.get("success") is False, str(res))
    check("state still exactly as saved", saved_state(settings, get_setting) == RAZER,
          str(saved_state(settings, get_setting)))

    print("G4 the boot pass itself (prefer_key, respect_saved) still refuses")
    res = setup_wizard.prefer_key(respect_saved=True)
    check("boot prefer_key: no switch", res.get("switched") is False, str(res))
    check("state still exactly as saved", saved_state(settings, get_setting) == RAZER,
          str(saved_state(settings, get_setting)))

    print("G5 Xod's own pick may still be revised by an auto pick (designed self-healing)")
    delete_setting("llm_provider")               # back to a device whose pick is Xod's to make
    settings.llm_provider = "lm_studio"          # the class default a fresh install boots with
    first = setup_wizard.choose("lm_studio", "prism-ml/bonsai-27b", "http://localhost:1234/v1", how="auto")
    check("an auto pick on a never-saved device lands and marks itself Xod's own",
          first.get("success") is True and setup_wizard.chosen_by() == "auto", str(first))
    res = setup_wizard.choose("ollama", "llama3.1", how="auto")
    check("auto choose over Xod's own pick succeeds",
          res.get("success") is True and settings.llm_provider == "ollama", str(res))
    res = setup_wizard.prefer_key(respect_saved=True)
    check("but the boot prefer_key still never fires over any saved provider",
          res.get("switched") is False and settings.llm_provider == "ollama", str(res))

    print("G6 the full boot pass with the local probe ABSENT leaves the saved state untouched")
    setup_wizard.choose("lm_studio", "prism-ml/bonsai-27b", "http://localhost:1234/v1", how="you")
    _run_boot_pass(probe="absent")
    check("after autoconnect.check(): saved + runtime still the person's pick",
          saved_state(settings, get_setting) == RAZER and setup_wizard.chosen_by() == "you",
          str(saved_state(settings, get_setting)))

    print("G7 the full boot pass with the local probe SLOW leaves the saved state untouched")
    _run_boot_pass(probe="slow")
    check("after autoconnect.check(): saved + runtime still the person's pick",
          saved_state(settings, get_setting) == RAZER and setup_wizard.chosen_by() == "you",
          str(saved_state(settings, get_setting)))

    print("G8 a device with nothing saved may still be defaulted by the boot pass")
    delete_setting("llm_provider")
    settings.llm_provider = "lm_studio"          # the class default a fresh install boots with
    settings.llm_model = "google/gemma-4-12b-qat"
    res = setup_wizard.prefer_key(respect_saved=True)
    check("boot: with nothing saved, the saved key is the sensible default",
          res.get("switched") is True and settings.llm_provider == "openai", str(res))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


def _run_boot_pass(probe: str) -> None:
    """autoconnect.check() with the outside world faked at its edges:
    discovery (the local probe) absent or slow, no installed apps to
    start, a minimal machine profile. The pass's own logic — the
    prefer_key boot call and the ranked auto-pick — runs for real."""
    from app.services import autoconnect, model_sources

    async def fake_discover_absent():
        return []

    async def fake_discover_slow():
        await asyncio.sleep(3)
        return []

    async def fake_added():
        return []

    orig = (autoconnect.discover, autoconnect.installed_apps,
            autoconnect.free_ollama_orphans, model_sources.discover)
    autoconnect.discover = fake_discover_slow if probe == "slow" else fake_discover_absent
    autoconnect.installed_apps = lambda: {}
    autoconnect.free_ollama_orphans = lambda: None
    model_sources.discover = fake_added
    from app.services import machine
    orig_profile = machine.profile
    machine.profile = lambda *a, **k: {"ai_memory_gb": 16.0, "runs_on": "gpu", "gpus": [],
                                       "gpu": None, "power": {"battery": False, "plugged_in": True}}
    try:
        asyncio.run(autoconnect.check())
    finally:
        (autoconnect.discover, autoconnect.installed_apps,
         autoconnect.free_ollama_orphans, model_sources.discover) = orig
        machine.profile = orig_profile


if __name__ == "__main__":
    main()
