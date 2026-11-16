"""Install-level selection: real ASGI/config/runtime code, synthetic keyring and HTTP.

No real provider, credential, download, speaker or desktop control is used.
Run: python tests/smoke/run_install_model_config_test.py
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-install-model-config-"))
os.environ.update(
    HOME=str(TMP / "home"), XDG_CONFIG_HOME=str(TMP / "config"),
    XDG_DATA_HOME=str(TMP / "share"), XOD_DATA_DIR=str(TMP / "data"),
    XOD_LIGHTWEIGHT="1", XOD_MUSE_APP_DRY_RUN="1", XOD_AUTOCONNECT="0",
    XOD_NO_UPDATE_CHECK="1", XOD_SPEECH="off",
    PYTHON_KEYRING_BACKEND="tests.support.filekeyring.FileKeyring",
    XOD_TEST_KEYRING_FILE=str(TMP / "test-keyring.json"),
    XOD_SECRETS_SERVICE="Xod synthetic fixture",
)
for key in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "OPENAI_API_KEY",
            "FIREWORKS_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
            "GROQ_API_KEY", "OPENROUTER_API_KEY"):
    os.environ.pop(key, None)
sys.path.insert(0, str(ROOT))
from app import paths
paths.DEV_ENV_PATH = TMP / "absent-developer.env"
from app.config import load_synced_settings, settings, update_env_variable
from app.services import key_profiles, llm_routing, local_engine, setup_wizard, storage
from app.services.llm import llm_service, plain_error
from app.main import app
import httpx

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'} {name}", flush=True)


def snapshot():
    saved = storage.get_all_settings()
    return ([getattr(settings, f"llm_{f}") for f in ("provider", "model", "base_url")],
            {k: saved.get(k) for k in ("llm_provider", "llm_model", "llm_base_url", "model_chosen_by")})


async def main():
    # These are deliberately unusable fixture strings, stored only in the
    # isolated test keyring. Never run with the developer's real keychain.
    openai_fixture, fireworks_fixture = "<synthetic-openai-fixture>", "<synthetic-fireworks-fixture>"
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")
    async with client:
        original = snapshot()
        invalid = await client.post("/api/config", json={
            "llm_provider": "not-a-provider", "openai_api_key": openai_fixture})
        check("Settings rejects invalid provider before saving a key or changing the choice",
              invalid.status_code == 400 and snapshot() == original and not settings.openai_api_key)
        r = await client.post("/api/config", json={
            "llm_provider": "openai", "llm_model": "gpt-6.1-terra",
            "llm_base_url": "http://localhost:1234/v1", "openai_api_key": openai_fixture})
        check("Settings saves OpenAI BYOK and clears a stale local endpoint",
              r.status_code == 200 and settings.llm_provider == "openai"
              and settings.llm_base_url == "https://api.openai.com/v1"
              and setup_wizard.chosen_by() == "you")
        cfg = (await client.get("/api/config")).json()
        check("Public configuration agrees with runtime and does not expose credentials",
              cfg["model_configuration"]["current"]["model"] == "gpt-6.1-terra"
              and cfg["has_openai_key"] and openai_fixture not in json.dumps(cfg))
        check("Keys are absent from the ordinary settings database and env mirror",
              openai_fixture not in json.dumps(storage.get_all_settings())
              and openai_fixture not in paths.ENV_PATH.read_text())
        # A fresh interpreter must see the same selection without inheriting
        # the process's in-memory settings object or a developer env file.
        child = subprocess.run([sys.executable, "-c",
            "from pathlib import Path; from app import paths; "
            f"paths.DEV_ENV_PATH=Path({str(TMP / 'absent-developer.env')!r}); "
            "from app.config import settings; import json; "
            "print(json.dumps([settings.llm_provider,settings.llm_model,settings.llm_base_url]))"],
            cwd=ROOT, text=True, capture_output=True, timeout=30, check=True)
        check("A real process restart reads the same persisted provider/model/endpoint",
              json.loads(child.stdout.strip().splitlines()[-1]) == snapshot()[0])
        # A deliberate failure midway through SQL must roll back every field.
        before = snapshot()
        conn = storage.get_db_connection()
        conn.execute("""CREATE TRIGGER fixture_reject_model BEFORE INSERT ON settings
                        WHEN NEW.key='llm_model' BEGIN SELECT RAISE(ABORT, 'fixture failure'); END""")
        conn.commit()
        try:
            setup_wizard.choose("fireworks", "accounts/fireworks/models/fixture")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("Fixture database failure was not reached")
        check("A failed model transaction changes neither saved nor runtime fields", snapshot() == before)
        conn.execute("DROP TRIGGER fixture_reject_model")
        conn.commit()
        conn.close()
        r = await client.post("/api/config", json={
            "llm_provider": "fireworks", "fireworks_api_key": fireworks_fixture})
        check("Provider-only Settings switch uses that provider's model and endpoint",
              r.status_code == 200 and settings.llm_model == "accounts/fireworks/models/glm-5p3-flash"
              and settings.llm_base_url == "https://api.fireworks.ai/inference/v1")
        check("Automatic picks still cannot overwrite a user choice",
              not setup_wizard.choose("openai", "gpt-6.1-terra", how="auto")["success"]
              and settings.llm_provider == "fireworks")

        requests = []
        response_status = [200]
        response_model = [None]
        def handle(request):
            payload = json.loads(request.content)
            requests.append((request.url.host, payload["model"], request.headers.get("authorization")))
            if response_status[0] != 200:
                return httpx.Response(response_status[0], json={"error": {"message": "model unavailable"}})
            if payload.get("stream"):
                event = {"choices": [{"delta": {"content": "Fixture stream"}, "finish_reason": None}]}
                return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                      content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")
            return httpx.Response(200, json={"choices": [
                {"message": {"role": "assistant", "content": "Fixture answer"}, "finish_reason": "stop"}],
                "model": response_model[0]})
        real_client = httpx.AsyncClient
        def provider_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handle)
            return real_client(*args, **kwargs)
        llm_routing.save_settings(mode="off")
        with patch("app.services.llm.httpx.AsyncClient", side_effect=provider_client):
            message = await llm_service.chat_completion([{"role": "user", "content": "Fixture"}], max_tokens=16)
            check("Actual agent inference dispatch uses saved Fireworks selection and BYOK",
                  requests[-1] == ("api.fireworks.ai", settings.llm_model, f"Bearer {fireworks_fixture}")
                  and message["_provider"] == "fireworks" and message["_model"] == settings.llm_model)
            streamed = [event async for event in llm_service.stream_chat_completion(
                [{"role": "user", "content": "Fixture stream"}], max_tokens=16)]
            check("Streaming inference uses the same saved Fireworks provider/model/key",
                  streamed and requests[-1] == ("api.fireworks.ai", settings.llm_model, f"Bearer {fireworks_fixture}"))
            await client.post("/api/setup/choose", json={"provider": "openai", "model": "gpt-6.1-terra"})
            test = (await client.post("/api/setup/test")).json()
            check("Saved-model test reaches the real dispatch with the selected model, not a substitute",
                  test["success"] and test["matched_selection"] and test["actual"]["model"] == "gpt-6.1-terra"
                  and requests[-1] == ("api.openai.com", "gpt-6.1-terra", f"Bearer {openai_fixture}"))
            response_model[0] = "a-different-model"
            test = (await client.post("/api/setup/test")).json()
            check("A provider-reported substitute model is not counted as verification",
                  not test["success"] and not test["matched_selection"]
                  and test["actual"]["response_model"] == "a-different-model")
            response_model[0] = None
            response_status[0] = 404
            test = (await client.post("/api/setup/test")).json()
            check("An unavailable model is a failed connection test, never a fixture fallback pass",
                  not test["success"] and test["model"] == "gpt-6.1-terra" and "404" in test["error"])
            response_status[0] = 200

        # Verify profile-only keys stay visible and really reach dispatch.
        profiles = key_profiles.create_profile("openai", "Fixture profile", "<synthetic-profile-fixture>")
        pid = next(p["id"] for p in profiles["profiles"] if p["name"] == "Fixture profile")
        key_profiles.set_active("openai", pid)
        settings.openai_api_key = ""
        check("Profile-only BYOK remains selectable", "openai" in setup_wizard.saved_keys())
        with patch("app.services.llm.httpx.AsyncClient", side_effect=provider_client):
            test = (await client.post("/api/setup/test")).json()
            check("Saved-model execution uses the active key profile",
                  test["success"] and requests[-1][2] == "Bearer <synthetic-profile-fixture>")
        check("401 and 429 errors give actionable key/credit guidance",
              "Settings" in plain_error({"provider": "openai"}, 401)
              and "credit" in plain_error({"provider": "fireworks"}, 429))
        check("Provider errors redact echoed credentials before returning them",
              openai_fixture not in plain_error(
                  {"provider": "openai", "api_key": openai_fixture}, 404,
                  json.dumps({"error": {"message": f"Bad request {openai_fixture}"}})))

        before = snapshot()
        for endpoint in ("http://localhost.evil.invalid:8080/v1", "http://127.0.0.1.evil.invalid/v1",
                         "http://name:password@localhost:8080/v1"):
            result = setup_wizard.choose("local", "fixture", endpoint)
            check(f"Local endpoint validation refuses {endpoint.split('@')[-1]} without a partial switch",
                  not result["success"] and snapshot() == before)
        r = setup_wizard.choose("lm_studio", "fixture-local", "http://localhost:1235/v1")
        restored = load_synced_settings()
        check("Existing local-server behavior and non-default endpoint survive persistence",
              r["success"] and restored.llm_model == "fixture-local"
              and restored.llm_base_url == "http://localhost:1235/v1")
        before = snapshot()
        with patch.object(local_engine, "is_apple_silicon", return_value=False):
            r = await client.post("/api/config", json={"llm_provider": "xod", "llm_model": "mlx-community/fixture"})
            check("Unsupported MLX hardware is clearly refused before changing settings",
                  r.status_code == 400 and "Apple silicon" in r.json()["detail"] and snapshot() == before)
        with patch.object(local_engine, "is_apple_silicon", return_value=True), \
             patch.object(local_engine, "catalog_entry", return_value={"repo": "mlx-community/fixture"}), \
             patch.object(local_engine, "is_downloaded", return_value=True):
            r = setup_wizard.choose("xod", "mlx-community/fixture")
            info = llm_service.get_endpoint_info()
            check("MLX selection/dispatch contract remains wired (hardware/download fixtures only)",
                  r["success"] and info["provider"] == "xod" and info["model"] == "mlx-community/fixture")
        from app.services.model_hub import cloud_base_url
        check("Fireworks credentials cannot be routed to a hostname substring spoof",
              cloud_base_url("fireworks", "https://fireworks.attacker.invalid/v1")
              == "https://api.fireworks.ai/inference/v1")
        check("Valid Fireworks dedicated endpoints are retained",
              cloud_base_url("fireworks", "https://dedicated.fireworks.ai/inference/v1")
              == "https://dedicated.fireworks.ai/inference/v1")

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results)-len(failed)}/{len(results)} passed. All provider HTTP responses were synthetic.")
    print("Live Terra: NOT TESTED here. Native MLX: NOT TESTED here.")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
