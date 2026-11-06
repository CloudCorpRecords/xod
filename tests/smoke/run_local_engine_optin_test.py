"""
Local engines are opt-in: Xod must not force one on a person who never picked it
(a new user's report, 2026-11-06).

What he hit on a fresh Windows install (16 GB RAM): Xod's autoconnect pass
picked the best local model it could find all by itself, started Ollama when it
wasn't running, and fetched extra models (a small embedding model, then a
vision model) — each load spawns Ollama runner processes (llama-server.exe),
and on a loaded 16 GB machine the cycle of starts, loads and crashes made his
terminal keep opening and closing while Defender ate the CPU. He never asked
for a local model.

The invariant this suite pins, at autoconnect.check() (the boot/30 s pass):
  - a fresh install (nothing saved): no local pick is persisted, no local
    server is started, no model is fetched. The pass may SAY a local model is
    available, and how to pick it.
  - a saved local choice keeps today's behaviour exactly, whoever made it:
    the person's own pick (model_chosen_by "you", the pre-existing-settings
    default) gets its stopped server started (N4/N5), and a pick Xod made
    itself under the old behaviour (model_chosen_by "auto") is not un-chosen —
    its server is started when stopped (N3) and it is revised when its model
    goes away (N6). What already chose local is not the new-install problem.

Check N1 is the failing-first core: on the pristine tree the fresh pass
persists an auto pick of the Ollama model. N3–N6 are preservation checks and
pass on the pristine tree too.
Run: .venv/bin/python tests/smoke/run_local_engine_optin_test.py
"""
import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-local-optin-"))


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


OLLAMA_SERVER = {"provider": "ollama", "name": "Ollama", "version": "0.40",
                 "base_url": "http://localhost:11434/v1",
                 "models": [{"id": "qwen3.5:4b", "tools": True, "size_gb": 3.1}]}
PROFILE = {"ai_memory_gb": 16.0, "runs_on": "gpu", "gpus": [], "gpu": None,
           "power": {"battery": False, "plugged_in": True}}

calls = {"start_ollama": 0, "start_pull": []}


def _run_pass(discoveries):
    """autoconnect.check() with the outside world faked at its edges, like the
    boot-guard suite: discovery returns the given server lists in turn, no apps
    to start except where a scenario says so, spies on server starts/pulls."""
    from app.services import autoconnect, embeddings, machine, model_sources, ollama_hub, vision

    seq = list(discoveries)

    async def fake_discover():
        return seq.pop(0) if seq else []

    async def fake_added():
        return []

    async def fake_start_ollama():
        calls["start_ollama"] += 1
        return {"success": True}

    async def fake_start_pull(name):
        calls["start_pull"].append(name)
        return {"success": True}

    async def fake_installed():
        return [{"name": embeddings.OLLAMA_MODEL}]

    async def fake_needed():
        return False

    orig = (autoconnect.discover, autoconnect.installed_apps, autoconnect.free_ollama_orphans,
            model_sources.discover, machine.profile, ollama_hub.start_ollama,
            ollama_hub.start_pull, ollama_hub.installed, vision.needed)
    autoconnect.discover = fake_discover
    autoconnect.installed_apps = lambda: {"ollama": "/fake/ollama.exe", "lm_studio": None, "jan": None}
    autoconnect.free_ollama_orphans = lambda: None
    model_sources.discover = fake_added
    machine.profile = lambda *a, **k: dict(PROFILE)
    ollama_hub.start_ollama = fake_start_ollama
    ollama_hub.start_pull = fake_start_pull
    ollama_hub.installed = fake_installed
    vision.needed = fake_needed
    autoconnect._last_try.clear()
    try:
        return asyncio.run(autoconnect.check())
    finally:
        (autoconnect.discover, autoconnect.installed_apps, autoconnect.free_ollama_orphans,
         model_sources.discover, machine.profile, ollama_hub.start_ollama,
         ollama_hub.start_pull, ollama_hub.installed, vision.needed) = orig


def _fresh(settings, delete_setting):
    for k in ("llm_provider", "llm_model", "llm_base_url", "model_chosen_by"):
        delete_setting(k)
    settings.llm_provider = "lm_studio"          # the class defaults a fresh install boots with
    settings.llm_model = "google/gemma-4-12b-qat"
    settings.llm_base_url = "http://localhost:1234/v1"
    calls["start_ollama"] = 0
    calls["start_pull"] = []


def main() -> None:
    from app.config import settings
    from app.services import llm_routing, setup_wizard
    from app.services.storage import delete_setting, get_setting

    llm_routing.save_settings(mode="off")

    print("N1 a fresh install, Ollama already running with a model: no forced local pick")
    _fresh(settings, delete_setting)
    state = _run_pass([[OLLAMA_SERVER]])
    check("nothing was persisted as the provider", get_setting("llm_provider", "") == "",
          f"saved={get_setting('llm_provider', '')!r} runtime={settings.llm_provider!r}")
    check("the runtime provider is still the fresh default", settings.llm_provider == "lm_studio",
          settings.llm_provider)
    check("no local server was started", calls["start_ollama"] == 0, str(calls))
    check("no model was fetched", calls["start_pull"] == [], str(calls))
    check("the pass says a local model is there to pick, in Setup",
          any("Setup" in n for n in state["notes"]), str(state["notes"]))
    # A second pass (the 30 s loop): still hands off.
    state = _run_pass([[OLLAMA_SERVER]])
    check("a second pass still persists nothing and fetches nothing",
          get_setting("llm_provider", "") == "" and calls["start_pull"] == [],
          f"saved={get_setting('llm_provider', '')!r} calls={calls}")

    print("N2 a fresh install with a saved cloud key still defaults to the key (unchanged)")
    from app.services import secret_store
    _fresh(settings, delete_setting)
    secret_store.save("openai_api_key", "sk-test-not-real")
    settings.openai_api_key = "sk-test-not-real"
    state = _run_pass([[OLLAMA_SERVER]])
    check("the key wins over the local model", settings.llm_provider == "openai", settings.llm_provider)
    secret_store.save("openai_api_key", "")
    settings.openai_api_key = ""

    print("N3 Xod's own earlier auto pick (a saved local choice), its server stopped: kept as today")
    _fresh(settings, delete_setting)
    pick = setup_wizard.choose("ollama", "qwen3.5:4b", how="auto")
    check("setup: the auto pick is saved as Xod's own", pick.get("success") and setup_wizard.chosen_by() == "auto",
          str(pick))
    calls["start_ollama"] = 0
    calls["start_pull"] = []
    state = _run_pass([[], [OLLAMA_SERVER]])     # not answering, then answering (if something started it)
    check("Ollama was started for the saved choice, as before", calls["start_ollama"] == 1, str(calls))
    check("no model was fetched", calls["start_pull"] == [], str(calls))
    check("the saved pick itself was not rewritten or un-chosen",
          get_setting("llm_provider", "") == "ollama" and settings.llm_provider == "ollama"
          and settings.llm_model == "qwen3.5:4b",
          f"saved={get_setting('llm_provider', '')!r} runtime={settings.llm_provider!r} {settings.llm_model!r}")

    print("N6 Xod's own pick's model is gone: it revises its own pick, as before")
    _fresh(settings, delete_setting)
    pick = setup_wizard.choose("ollama", "gone:7b", how="auto")
    check("setup: the auto pick is saved", pick.get("success") and setup_wizard.chosen_by() == "auto", str(pick))
    other = {**OLLAMA_SERVER, "models": [{"id": "other:4b", "tools": True, "size_gb": 3.0}]}
    state = _run_pass([[other]])
    check("the pass switched Xod's own pick to the model that is there",
          settings.llm_provider == "ollama" and settings.llm_model == "other:4b",
          f"{settings.llm_provider} {settings.llm_model}")

    print("N4 the person's own local pick, its server stopped: started as today")
    _fresh(settings, delete_setting)
    pick = setup_wizard.choose("ollama", "qwen3.5:4b", how="you")
    check("setup: the person's pick is saved", pick.get("success") and setup_wizard.chosen_by() == "you", str(pick))
    calls["start_ollama"] = 0
    calls["start_pull"] = []
    state = _run_pass([[], [OLLAMA_SERVER]])
    check("Ollama was started for the person's pick", calls["start_ollama"] == 1, str(calls))
    check("the person's state is untouched",
          settings.llm_provider == "ollama" and settings.llm_model == "qwen3.5:4b",
          f"{settings.llm_provider} {settings.llm_model}")

    print("N5 the person's own local pick, server already answering on 11434: nothing to start")
    calls["start_ollama"] = 0
    calls["start_pull"] = []
    state = _run_pass([[OLLAMA_SERVER]])
    check("no start attempted against a live server", calls["start_ollama"] == 0, str(calls))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
