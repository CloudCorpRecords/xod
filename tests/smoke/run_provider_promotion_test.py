"""
A member's saved provider always wins at boot (fix lane 0.9.24, defect 1).

The 0.9.23 ship proved this defect live: the Razer's saved provider was
lm_studio / prism-ml/bonsai-27b (saved by autoconnect, so marked Xod's
own pick), and once the family vault delivered cloud keys, the boot-time
autoconnect pass called setup_wizard.prefer_key(), which promoted the
runtime to the cloud default openai / gpt-4o — leaving the LM Studio
base_url in place, an incoherent pair — reproducibly at every boot.

The boot path must never re-default a SAVED provider, whatever marked
the choice. A device with NO saved provider may still default to a
saved key. The Settings path keeps its documented behaviour (a key the
person saves on this device wins while the choice is still Xod's own
pick; a model the person picked is never changed) — that behaviour is
pinned by run_local_servers_test.py and re-checked here.

Key arrival is simulated the way the family vault does it:
secret_store.save() into the keystore, then the value present in
settings (what a fresh boot's load_synced_settings() produces).

Run: .venv/bin/python tests/smoke/run_provider_promotion_test.py
"""
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-provider-promotion-"))


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1",
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


def main() -> None:
    from app.config import settings
    from app.services import llm_routing, secret_store, setup_wizard
    from app.services.storage import get_setting, set_setting

    llm_routing.save_settings(mode="off")

    # R11 fixture note: this suite models an install that RUNS local as Xod's
    # own pick (the 0.9.23 Razer shape). Under the cloud-first policy that
    # state exists only on an install that opted into local inference, so the
    # fixture opts in explicitly; the promotion rules under test are unchanged.
    from app.services import inference_policy
    inference_policy.set_allowed(True, source="test_fixture")

    def boot_prefer():
        """The boot/autoconnect invocation of prefer_key, exactly as
        app/services/autoconnect.py makes it. Returns (result, error)."""
        try:
            return setup_wizard.prefer_key(respect_saved=True), None
        except TypeError as e:
            return None, e

    print("P1 the Razer shape: a saved local provider, chosen by autoconnect")
    pick = setup_wizard.choose("lm_studio", "prism-ml/bonsai-27b", "http://localhost:1234/v1", how="auto")
    check("the local pick is saved", pick.get("success") and settings.llm_provider == "lm_studio"
          and get_setting("llm_provider", "") == "lm_studio", str(pick))
    check("…and marked Xod's own pick (the promotion precondition)",
          setup_wizard.chosen_by() == "auto")

    print("P2 a cloud key arrives through the family vault (keystore write only)")
    secret_store.save("openai_api_key", "<redacted>")
    settings.openai_api_key = "<redacted>"  # what the next boot's settings load sees
    check("the key is present", setup_wizard.saved_keys()[:1] == ["openai"], str(setup_wizard.saved_keys()))

    print("P3 boot: the saved provider must win over the arrived key")
    res, err = boot_prefer()
    if err is not None:
        check("boot: prefer_key offers a saved-wins mode for the boot path", False, f"TypeError: {err}")
        flipped = setup_wizard.prefer_key()
        check("boot (0.9.23 behaviour): the boot pass flips the saved local pick to the cloud default",
              flipped.get("switched") is True and settings.llm_provider == "openai", str(flipped))
        setup_wizard.choose("lm_studio", "prism-ml/bonsai-27b", "http://localhost:1234/v1", how="auto")
    else:
        check("boot: no switch over a saved provider", res.get("switched") is False, str(res))
        check("boot: provider/model/base_url exactly as saved",
              settings.llm_provider == "lm_studio" and settings.llm_model == "prism-ml/bonsai-27b"
              and settings.llm_base_url == "http://localhost:1234/v1",
              f"{settings.llm_provider}/{settings.llm_model}@{settings.llm_base_url}")
        check("boot: the stored provider row is untouched",
              get_setting("llm_provider", "") == "lm_studio", get_setting("llm_provider", ""))

    print("P4 the Mac shape: a saved cloud provider keeps its saved model")
    setup_wizard.choose("openai", "gpt-6.1-terra", "", how="you")
    res, err = boot_prefer()
    check("boot: saved terra stays terra", err is None and res.get("switched") is False
          and settings.llm_provider == "openai" and settings.llm_model == "gpt-6.1-terra",
          str(err or res))

    print("P5 no saved provider: the boot may still default to the saved key")
    set_setting("llm_provider", "")  # nothing saved: Xod has not picked/persisted a provider
    settings.llm_provider = "lm_studio"  # the class default a fresh install boots with
    settings.llm_model = "google/gemma-4-12b-qat"
    res, err = boot_prefer()
    check("boot: with nothing saved, the saved key is the sensible default",
          err is None and res.get("switched") is True and settings.llm_provider == "openai",
          str(err or res))

    print("P6 the Settings path keeps its documented behaviour")
    setup_wizard.choose("ollama", "llama3.1", how="auto")
    res = setup_wizard.prefer_key()
    check("a key saved by the person still wins over Xod's own local pick",
          res.get("switched") is True and settings.llm_provider == "openai", str(res))
    setup_wizard.choose("ollama", "llama3.1", how="you")
    res = setup_wizard.prefer_key()
    check("a model the person picked is never changed",
          res.get("switched") is False and settings.llm_provider == "ollama", str(res))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
