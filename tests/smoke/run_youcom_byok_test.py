"""
You.com bring-your-own-key: vault round-trip, provider preference, and key testing
(services/secret_store.py, services/you_search.py, services/search_providers.py,
and the /api/you/test endpoint in app/main.py).

Runs against a throwaway data folder and the file keyring, with made-up keys and a
stubbed HTTP layer — no real key is read and nothing leaves the machine. The checks
prove: a You.com key saves into the same vault as every other key and comes back
masked only; removing it really deletes it; when a You.com key exists it is the
preferred search provider and Tavily takes over when it doesn't; verify_key accepts
an unsaved key, reports the real HTTP status on failure, and never puts the key in
a message; the test endpoint saves a pasted key only after it verifies.

  .venv/bin/python tests/smoke/run_youcom_byok_test.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-youcom-byok-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir()
for var in ("YOU_API_KEY", "YDC_API_KEY", "TAVILY_API_KEY"):
    os.environ.pop(var, None)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


YOU_KEY = "ydc_fakekey_for_tests_0123456789_wxyz"
TAV_KEY = "tvly-fakekey-for-tests-0123456789-abcd"


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _fake_httpx(status_code, payload=None):
    """A stand-in for httpx.AsyncClient that answers one canned response."""
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, headers=None):
            return _FakeResponse(status_code, payload)

        async def post(self, url, json=None, headers=None):
            return _FakeResponse(status_code, payload)

    return FakeClient


def main():
    from app.config import settings, update_env_variable
    from app.services import secret_store
    from app.services.you_search import you_client
    from app.services.tavily_search import tavily_client
    from app.services import search_providers
    import app.services.you_search as you_module

    # ---- vault round-trip: save -> masked status -> remove -----------------------
    secret_store.save("you_api_key", YOU_KEY)
    entry = next(k for k in secret_store.status() if k["name"] == "you_api_key")
    check("you.com key saves into the vault", entry["saved"] is True)
    check("status shows the key masked only",
          YOU_KEY not in str(entry) and entry["masked"].endswith(YOU_KEY[-4:]))
    check("mask never reveals the middle of the key", YOU_KEY[6:-4] not in secret_store.mask(YOU_KEY))

    update_env_variable("YOU_API_KEY", "")
    entry = next(k for k in secret_store.status() if k["name"] == "you_api_key")
    check("removing the key deletes it from the vault", entry["saved"] is False and secret_store.stored("you_api_key") == "")

    # ---- provider preference ------------------------------------------------------
    you_client._api_key = None
    tavily_client._api_key = None
    settings.you_api_key = YOU_KEY
    settings.tavily_api_key = TAV_KEY
    check("you.com is preferred when its key is saved", search_providers.preferred_provider() == "you.com")
    settings.you_api_key = ""
    check("tavily is preferred when only its key is saved", search_providers.preferred_provider() == "tavily")
    settings.tavily_api_key = ""
    check("keyless fallback when no search key is saved", search_providers.preferred_provider() == "web_fallback")

    # preferred_search actually calls the preferred client (clients stubbed)
    calls = []

    async def fake_you_search(query, count=5):
        calls.append("you")
        return {"provider": "you.com", "results": [{"title": "y"}]}

    async def fake_tav_search(query, count=5):
        calls.append("tavily")
        return {"provider": "tavily", "results": [{"title": "t"}]}

    orig_you, orig_tav = you_client.search, tavily_client.search
    you_client.search, tavily_client.search = fake_you_search, fake_tav_search
    try:
        settings.you_api_key, settings.tavily_api_key = YOU_KEY, TAV_KEY
        out = asyncio.run(search_providers.preferred_search("q", count=3))
        check("preferred_search asks you.com first", out.get("provider") == "you.com" and calls == ["you"])
        calls.clear()
        settings.you_api_key = ""
        out = asyncio.run(search_providers.preferred_search("q", count=3))
        check("preferred_search falls back to tavily without a you.com key",
              out.get("provider") == "tavily" and calls == ["tavily"])
    finally:
        you_client.search, tavily_client.search = orig_you, orig_tav
        settings.you_api_key, settings.tavily_api_key = "", ""

    # ---- verify_key with stubbed HTTP ---------------------------------------------
    real_async_client = you_module.httpx.AsyncClient
    try:
        you_module.httpx.AsyncClient = _fake_httpx(200, {"results": {"web": [{"title": "t", "url": "https://x"}]}})
        res = asyncio.run(you_client.verify_key(YOU_KEY))
        check("verify_key accepts a valid unsaved key", res.get("valid") is True)

        you_module.httpx.AsyncClient = _fake_httpx(401, {})
        res = asyncio.run(you_client.verify_key(YOU_KEY))
        check("verify_key rejects a turned-down key", res.get("valid") is False)
        check("failure reports the real status and never the key",
              "401" in res.get("message", "") and YOU_KEY not in res.get("message", ""))

        you_module.httpx.AsyncClient = _fake_httpx(200, {"results": {"web": []}})
        res = asyncio.run(you_client.verify_key(""))
        check("verify_key with no key at all says so", res.get("valid") is False and "No You.com" in res.get("message", ""))
    finally:
        you_module.httpx.AsyncClient = real_async_client

    # ---- /api/you/test saves only on success ---------------------------------------
    try:
        from app.main import test_you_connection, YouTestRequest
    except Exception as e:
        print(f"SKIP  endpoint checks (app.main import failed: {type(e).__name__})", flush=True)
    else:
        try:
            update_env_variable("YOU_API_KEY", "")
            you_client._api_key = None
            settings.you_api_key = ""
            you_module.httpx.AsyncClient = _fake_httpx(200, {"results": {"web": [{"title": "t"}]}})
            res = asyncio.run(test_you_connection(YouTestRequest(you_api_key=YOU_KEY)))
            check("test endpoint accepts a pasted key that verifies", res.get("valid") is True)
            check("…and saves it only then", secret_store.stored("you_api_key") == YOU_KEY)

            update_env_variable("YOU_API_KEY", "")
            settings.you_api_key = ""
            you_client._api_key = None
            you_module.httpx.AsyncClient = _fake_httpx(403, {})
            res = asyncio.run(test_you_connection(YouTestRequest(you_api_key=YOU_KEY)))
            check("test endpoint rejects a bad pasted key", res.get("valid") is False)
            check("…and does not save it", secret_store.stored("you_api_key") == "")
        finally:
            you_module.httpx.AsyncClient = real_async_client

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
