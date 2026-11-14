"""
Model switching + Terra default (AGE-60, rides AGE-42).

Field defects (2026-11-14): with OpenAI active on gpt-4o, the quick
picker's OpenAI row was inert: setup_wizard.quick() emitted the CURRENT
model for the current provider and marked every cloud row for that
provider "current" (add(): model == cur_m or provider in CLOUD_DEFAULTS),
so model_switcher.js useModelChoice() early-returned and gpt-6.1-terra
was never offered. model_hub.all_models() had the same one-row
construction, llm._build_endpoint()/get_available_tiers() hard-coded
gpt-4o, and CLOUD_DEFAULTS["openai"] was gpt-4o itself.

This suite proves:
  (a) quick() offers an openai row with model gpt-6.1-terra that is NOT
      marked current when the current model is gpt-4o, and that row is
      clickable through choose();
  (b) after choose("openai", "gpt-6.1-terra", how="you"),
      get_endpoint_info() and the picker row report terra as current;
  (c) _build_endpoint for openai with no explicit model does not
      return gpt-4o;
  (d) CLOUD_DEFAULTS["openai"] == "gpt-6.1-terra".

Run: .venv/bin/python tests/smoke/run_model_switching_test.py
"""
import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-model-switching-"))


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
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


async def main() -> None:
    from app.config import settings
    from app.services import secret_store, setup_wizard
    from app.services.llm import llm_service
    from app.services.model_hub import CLOUD_DEFAULTS

    print("D the OpenAI default itself")
    check("CLOUD_DEFAULTS['openai'] is gpt-6.1-terra", CLOUD_DEFAULTS.get("openai") == "gpt-6.1-terra",
          str(CLOUD_DEFAULTS.get("openai")))
    check("key_choice('openai') offers terra", setup_wizard.key_choice("openai")["model"] == "gpt-6.1-terra",
          str(setup_wizard.key_choice("openai")))

    print("Setup: an OpenAI key saved, OpenAI active on the old gpt-4o")
    secret_store.save("openai_api_key", "sk-test-openai")
    settings.openai_api_key = "sk-test-openai"
    pick = setup_wizard.choose("openai", "gpt-4o", how="you")
    check("fixture: openai/gpt-4o is the saved choice", pick.get("success") and settings.llm_provider == "openai"
          and settings.llm_model == "gpt-4o", str(pick))

    print("A quick() must offer terra as a clickable (not current) row")
    q = await setup_wizard.quick()
    openai_rows = [c for c in q["choices"] if c["provider"] == "openai"]
    terra_rows = [c for c in openai_rows if c["model"] == "gpt-6.1-terra"]
    check("quick() offers an openai row with model gpt-6.1-terra", len(terra_rows) == 1, str(openai_rows))
    if terra_rows:
        check("the terra row is NOT marked current while gpt-4o is current",
              terra_rows[0].get("current") is False, str(terra_rows[0]))
        check("the terra row is not marked missing", not terra_rows[0].get("missing"), str(terra_rows[0]))
    # The click path: model_switcher.js refuses only current/missing rows, then
    # POSTs {provider, model} to /api/setup/choose, which is choose() itself.
    if terra_rows and terra_rows[0].get("current") is False and not terra_rows[0].get("missing"):
        res = setup_wizard.choose(terra_rows[0]["provider"], terra_rows[0]["model"], how="you")
        check("choose() on the offered terra row succeeds (the click path)",
              res.get("success") is True and settings.llm_model == "gpt-6.1-terra", str(res))
    else:
        check("choose() on the offered terra row succeeds (the click path)", False,
              f"row not clickable: {terra_rows}")

    print("B after choosing terra, terra is reported current everywhere")
    info = llm_service.get_endpoint_info()
    check("get_endpoint_info() reports openai/gpt-6.1-terra",
          info.get("provider") == "openai" and info.get("model") == "gpt-6.1-terra", str(info))
    q2 = await setup_wizard.quick()
    terra_now = [c for c in q2["choices"] if c["provider"] == "openai" and c["model"] == "gpt-6.1-terra"]
    check("quick() now marks the terra row current", len(terra_now) == 1 and terra_now[0].get("current") is True,
          str(terra_now))

    print("C endpoint fallbacks never resurrect gpt-4o")
    ep = llm_service._build_endpoint("openai")
    check("_build_endpoint('openai') with no explicit model is not gpt-4o",
          ep.get("model") != "gpt-4o", str(ep))
    check("_build_endpoint('openai') with no explicit model is gpt-6.1-terra",
          ep.get("model") == "gpt-6.1-terra", str(ep))
    tiers = llm_service.get_available_tiers()
    oa = [o for o in tiers.get("cloud_options", []) if o["provider"] == "openai"]
    check("get_available_tiers() openai option is gpt-6.1-terra",
          len(oa) == 1 and oa[0]["model"] == "gpt-6.1-terra", str(oa))

    print("all_models(): the AI models panel surfaces terra the same way")
    from app.services import model_hub
    try:
        allm = await model_hub.all_models()
        oa_rows = [m for m in allm["models"] if m["provider"] == "openai"]
        check("all_models() offers the openai terra row",
              any(m["model"] == "gpt-6.1-terra" for m in oa_rows), str(oa_rows))
        check("all_models() terra row is in use for chat after the switch",
              any(m["model"] == "gpt-6.1-terra" and "chat" in (m.get("in_use") or []) for m in oa_rows),
              str(oa_rows))
    except Exception as e:  # discovery on this machine may be unavailable; the row logic is what matters
        check("all_models() offers the openai terra row", False, f"{type(e).__name__}: {e}")

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
