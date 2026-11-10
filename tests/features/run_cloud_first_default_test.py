"""
R11: the cloud-first default (the 2026-11-09 directive).

Xod was built local-first; for the larger audience that flips. A fresh install
defaults to a cloud posture — an honest "needs a cloud key" setup state — and
one explicit setting, allow_local_inference (default OFF on fresh installs),
gates ALL local / routed / borrowed inference, including the autoconnect
loop's 30-second hunt (the 2026-11-06 fresh-install finding: the loop picked
a local model by itself when the user had never chosen one).

  A  fresh install: the policy decides OFF (source fresh_default), the setup
     state says needs_cloud_key, and over two autoconnect ticks with a
     perfectly good local server answering NOTHING is picked, started, or
     pulled (the Brad case, i + v)
  B  setting OFF + a task run: the chat funnel refuses local with the honest
     setup reason and ZERO local load calls behind it (spies on the FIX C
     budget gate, the built-in engine, and the memory broker); with a cloud
     key configured the same chat reroutes to cloud instead (ii)
  C  setting ON + an over-budget local load: the FIX C refusal behavior is
     unchanged — reroute to cloud when one is configured, the honest budget
     refusal with none (iii)
  D  migration: a cloud-configured install stays OFF (migration_existing_cloud),
     a local-configured install migrates ON (migration_existing_local), and
     the decision is persisted and logged (iv)
  E  the write path: an automatic pick of a local provider is refused while
     OFF; a person's own local pick succeeds and IS the opt-in (flag flips ON)

Failing-first: on pristine 58a897f there is no inference_policy module, so the
suite fails at import — the behavior it proves does not exist there.

Run: .venv/bin/python tests/features/run_cloud_first_default_test.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ["XOD_DATA_DIR"] = os.environ.get("XOD_TEST_DATA_DIR") or tempfile.mkdtemp(prefix="xod-cloudfirst-")
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
sys.path.insert(0, str(ROOT))

results = []


def report(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def reset_install(saved_provider=None):
    """Back to a bare install, optionally with a saved provider choice."""
    from app.services import inference_policy
    from app.services.storage import delete_setting, set_setting
    from app.config import settings
    for key in ("llm_provider", "llm_base_url", "llm_model",
                inference_policy.SETTING_KEY, inference_policy.SOURCE_KEY,
                inference_policy.DECISION_KEY, "model_chosen_by"):
        delete_setting(key)
    inference_policy._decided = False
    settings.llm_provider = saved_provider or "lm_studio"     # the class default a fresh install boots with
    settings.llm_base_url = "http://localhost:1234/v1"
    settings.llm_model = "google/gemma-4-12b-qat"
    settings.openai_api_key = ""
    if saved_provider:
        set_setting("llm_provider", saved_provider)


async def main():
    from app.config import settings
    from app.services import inference_policy
    from app.services.storage import get_setting

    # ------------------------------------------------- A: the Brad case (fresh, OFF)
    reset_install()
    report("A fresh install: the policy decides OFF, source fresh_default",
           inference_policy.allowed() is False
           and get_setting(inference_policy.SOURCE_KEY) == "fresh_default",
           f"source={get_setting(inference_policy.SOURCE_KEY)}")
    report("A fresh install: the setup state is the honest needs_cloud_key",
           inference_policy.setup_state() == "needs_cloud_key",
           f"state={inference_policy.setup_state()}")
    rs = inference_policy.route_state()
    report("A fresh install: the config surface shows route none + setting off",
           rs["active_route"] == "none" and rs["allow_local_inference"] is False,
           f"route={rs['active_route']}")

    from app.services import autoconnect, machine, model_sources, ollama_hub, setup_wizard
    pulls, starts = [], []

    async def fake_discover():
        return [{"provider": "ollama", "name": "Ollama", "version": "0.9",
                 "base_url": "http://127.0.0.1:11434/v1",
                 "models": [{"id": "qwen3:8b", "tools": True, "size_gb": 5.0}]}]

    async def fake_start_pull(model):
        pulls.append(model)
        return {"success": True}

    async def fake_start_ollama():
        starts.append("ollama")
        return {"success": True}

    saved_ac = (autoconnect.discover, autoconnect.installed_apps, autoconnect.free_ollama_orphans,
                machine.profile, model_sources.discover, ollama_hub.start_pull, ollama_hub.start_ollama)
    autoconnect.discover = fake_discover
    autoconnect.installed_apps = lambda: {"ollama": "/usr/local/bin/ollama", "lm_studio": None}
    autoconnect.free_ollama_orphans = lambda: 0
    machine.profile = lambda: {"ai_memory_gb": 16.0, "runs_on": "cpu", "power": {}}
    model_sources.discover = lambda: asyncio.sleep(0, result=[])
    ollama_hub.start_pull = fake_start_pull
    ollama_hub.start_ollama = fake_start_ollama
    try:
        await autoconnect.check()
        await autoconnect.check()      # two 30 s-loop ticks, the Brad window
    finally:
        (autoconnect.discover, autoconnect.installed_apps, autoconnect.free_ollama_orphans,
         machine.profile, model_sources.discover, ollama_hub.start_pull,
         ollama_hub.start_ollama) = saved_ac
    report("A two loop ticks pick NOTHING: no provider was saved by the loop",
           get_setting("llm_provider", "") == "", f"saved={get_setting('llm_provider', '')!r}")
    report("A two loop ticks load NOTHING: no pull, no server start",
           pulls == [] and starts == [], f"pulls={pulls} starts={starts}")
    st = autoconnect.state()
    report("A the loop's own state carries the policy + the honest note",
           st["inference"]["allow_local_inference"] is False
           and any("Local inference is off" in n for n in st["notes"]),
           f"notes={st['notes'][:1]}")

    # ------------------------------------------------- B: OFF + a task run
    from app.services import llm as llm_mod, local_engine, memory_broker, model_budget
    svc = llm_mod.llm_service
    load_calls = []

    async def spy_gate(*a, **k):
        load_calls.append("budget_gate")
        return {"allowed": True}

    async def spy_engine(*a, **k):
        load_calls.append("engine_start")
        return None

    def spy_teacher(*a, **k):
        load_calls.append("teacher_reload")

    saved_llm = (model_budget.model_budget.check_load, local_engine.local_engine.ensure_running,
                 memory_broker.memory_broker.ensure_teacher)
    model_budget.model_budget.check_load = spy_gate
    local_engine.local_engine.ensure_running = spy_engine
    memory_broker.memory_broker.ensure_teacher = spy_teacher
    try:
        raised = None
        try:
            await svc._ensure_local_or_route(
                {"provider": "lm_studio", "model": "m", "base_url": "http://localhost:1234/v1", "api_key": ""})
        except inference_policy.LocalInferenceOff as e:
            raised = e
        report("B OFF + local chat: refused with the honest setup reason",
               raised is not None and "cloud API key" in str(raised), f"{raised}")
        raised = None
        try:
            await svc._ensure_local_or_route(
                {"provider": "xod", "model": "m", "base_url": "http://127.0.0.1", "api_key": "x"})
        except inference_policy.LocalInferenceOff as e:
            raised = e
        report("B OFF + built-in chat: refused the same way", raised is not None)
        report("B OFF: ZERO local load calls behind the refusals (gate/engine/teacher untouched)",
               load_calls == [], f"calls={load_calls}")

        settings.openai_api_key = "sk-test-key"
        info, _ = await svc._ensure_local_or_route(
            {"provider": "lm_studio", "model": "m", "base_url": "http://localhost:1234/v1", "api_key": ""})
        report("B OFF + a cloud key configured: the chat reroutes to cloud, still zero local loads",
               info["provider"] == "openai" and load_calls == [], f"provider={info['provider']}")
        settings.openai_api_key = ""

        r = await svc.load_lmstudio_model("some-model")
        report("B OFF: an explicit LM Studio load request is refused by the policy",
               r.get("success") is False and r.get("local_inference_off") is True, f"{r.get('message', '')[:80]}")
    finally:
        (model_budget.model_budget.check_load, local_engine.local_engine.ensure_running,
         memory_broker.memory_broker.ensure_teacher) = saved_llm

    # ------------------------------------------------- C: ON + over-budget (FIX C unchanged)
    inference_policy.set_allowed(True, source="user_setting")
    decision = {"allowed": False, "engine": "builtin", "model": "m", "est_gb": 7.0,
                "route": "queue", "queued": True,
                "reasons": ["local models would hold 13.0 GB, over the 6.4 GB budget (40% of 16 GB RAM)"]}

    async def refuse(repo):
        raise model_budget.ModelBudgetRefused(decision)

    saved_eng = local_engine.local_engine.ensure_running
    local_engine.local_engine.ensure_running = refuse
    try:
        raised = False
        try:
            await svc._ensure_local_or_route(
                {"provider": "xod", "model": "m", "base_url": "http://127.0.0.1", "api_key": "x"})
        except model_budget.ModelBudgetRefused:
            raised = True
        report("C ON + over-budget, no cloud: the FIX C refusal propagates unchanged", raised)
        settings.openai_api_key = "sk-test-key"
        info, _ = await svc._ensure_local_or_route(
            {"provider": "xod", "model": "m", "base_url": "http://127.0.0.1", "api_key": "x"})
        report("C ON + over-budget, cloud configured: FIX C reroutes to cloud as before",
               info["provider"] == "openai", f"provider={info['provider']}")
        settings.openai_api_key = ""
    finally:
        local_engine.local_engine.ensure_running = saved_eng

    # ------------------------------------------------- D: migration
    reset_install(saved_provider="openai")
    dec = inference_policy.ensure_migrated()
    report("D a cloud-configured install migrates OFF and stays cloud",
           inference_policy.allowed() is False and dec["source"] == "migration_existing_cloud",
           f"decision={dec}")
    reset_install(saved_provider="ollama")
    dec = inference_policy.ensure_migrated()
    report("D a local-configured install migrates ON (it already chose local)",
           inference_policy.allowed() is True and dec["source"] == "migration_existing_local",
           f"decision={dec}")
    report("D the migration decision is persisted in settings",
           get_setting(inference_policy.SOURCE_KEY) == "migration_existing_local"
           and "migration_existing_local" in get_setting(inference_policy.DECISION_KEY),
           f"stored={get_setting(inference_policy.DECISION_KEY)[:90]}")

    # ------------------------------------------------- E: the write path
    reset_install()
    res = setup_wizard.choose("ollama", "qwen3:8b", "http://127.0.0.1:11434/v1", how="auto")
    report("E OFF: an AUTOMATIC pick of a local provider is refused",
           res.get("success") is False and get_setting("llm_provider", "") == "",
           f"{res.get('error', '')[:80]}")
    res = setup_wizard.choose("ollama", "qwen3:8b", "http://127.0.0.1:11434/v1", how="you")
    report("E a person's own local pick succeeds and IS the opt-in",
           res.get("success") is True and inference_policy.allowed() is True
           and get_setting(inference_policy.SOURCE_KEY) == "user_model_choice",
           f"source={get_setting(inference_policy.SOURCE_KEY)}")

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed"
          + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
