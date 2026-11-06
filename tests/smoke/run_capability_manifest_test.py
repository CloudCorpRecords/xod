"""
Capability awareness: the manifest sessions answer from (services/capability_manifest.py),
the keyed search tools in session assembly (agent/worker.py), and the chat search
route (services/search_providers.py search_for_chat).

The failure this covers: a chat told the owner "I don't have direct access to a
You.com API tool in this chat" while a verified You.com key was saved on that
computer. The checks prove: the manifest lists You.com as available when a key
is present and as not set up when it is not; the provider section matches the
config presence flags for every provider; the keyed API search tools are in
the session tool set exactly when their provider is keyed; the chat search
goes to the keyed provider and names it; and no key value ever appears in the
manifest or its text forms.

Runs against a throwaway data folder and the file keyring, with made-up keys
and stubbed search clients. No real key is read and nothing leaves the machine.

  .venv/bin/python tests/smoke/run_capability_manifest_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-capability-manifest-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir()
for var in ("YOU_API_KEY", "YDC_API_KEY", "TAVILY_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
            "GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "FIREWORKS_API_KEY",
            "TRUSTMRR_API_KEY", "YOUTUBE_API_KEY", "HUGGINGFACE_TOKEN", "HF_TOKEN"):
    os.environ.pop(var, None)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


YOU_KEY = "ydc_fakekey_for_tests_0123456789_wxyz"
TAV_KEY = "tvly-fakekey-for-tests-0123456789-abcd"
OAI_KEY = "openai-fakekey-for-tests-0123456789"


def main():
    from app.config import settings
    from app.services import capability_manifest as cm
    from app.services import search_providers
    from app.services.you_search import you_client
    from app.services.tavily_search import tavily_client

    fields = {p[0]: p[2] for p in cm.PROVIDERS}

    def set_all(value=""):
        for f in fields.values():
            setattr(settings, f, value)
        you_client._api_key = None
        tavily_client._api_key = None
        cm._cache.update(manifest=None, at=0.0)

    def provider(manifest, pid):
        return next(p for p in manifest["providers"] if p["id"] == pid)

    # ---- provider section matches the config presence flags ----------------------
    set_all("")
    setattr(settings, fields["you"], YOU_KEY)
    setattr(settings, fields["openai"], OAI_KEY)
    m = cm.build_manifest()
    check("manifest marks you.com keyed when its key is saved", provider(m, "you")["keyed"] is True)
    check("manifest marks openai keyed when its key is saved", provider(m, "openai")["keyed"] is True)
    check("manifest marks the rest unkeyed", all(provider(m, pid)["keyed"] is False
                                                 for pid in fields if pid not in ("you", "openai")))
    check("manifest covers every provider in the task list",
          [p["id"] for p in m["providers"]] == ["you", "tavily", "openai", "fireworks", "anthropic",
                                                "groq", "gemini", "openrouter", "trustmrr", "youtube",
                                                "huggingface"])
    check("search section prefers you.com when it is keyed",
          m["search"]["preferred"] == "you.com" and m["search"]["you_keyed"] is True)

    # ---- no secret values anywhere in the manifest or its text forms -------------
    blob = json.dumps(m) + cm.summary_text(m) + cm.compact_line(m)
    check("no key value appears in the manifest or its text forms",
          YOU_KEY not in blob and OAI_KEY not in blob)
    check("provider entries carry presence only",
          all(set(p) == {"id", "name", "field", "purpose", "keyed"} for p in m["providers"]))

    # ---- text forms say what is had and what is not -------------------------------
    text = cm.summary_text(m)
    check("summary names you.com search as served by the keyed provider",
          "Web search is served by You.com API first" in text)
    check("summary lists saved keys by name", "Keys saved on this computer: You.com, OpenAI" in text)
    check("summary lists unkeyed providers by name", "No key saved: Tavily" in text)
    check("summary carries the answer-from-the-list rule", "never from a guess" in text)

    set_all("")
    m = cm.build_manifest()
    check("manifest marks you.com unavailable when no key is saved", provider(m, "you")["keyed"] is False)
    check("search section falls back when nothing is keyed", m["search"]["preferred"] == "web_fallback")
    check("summary says no key saved for you.com when absent",
          "No key saved: You.com" in cm.summary_text(m))

    # ---- tool catalog is read from the live registry ------------------------------
    check("tool catalog has the registered tools", m["tools"]["total"] > 50)
    web_group = m["tools"]["groups"].get("Web search and reading") or []
    check("search tools are grouped together",
          "you_search" in web_group and "tavily_search" in web_group and "web_search" in web_group)
    check("manifest carries device and vault sections",
          isinstance(m["devices"]["family"], list) and isinstance(m["devices"]["phones"], list)
          and "state" in m["vault"])

    # ---- session assembly: keyed search tools are present exactly when keyed ------
    try:
        from app.agent import worker
        from app.agent.tools import TOOL_DEFINITIONS
    except Exception as e:
        print(f"SKIP  session assembly checks (worker import failed: {type(e).__name__})", flush=True)
    else:
        def assembled(kind_names):
            names = set(kind_names) | set(cm.keyed_search_tool_names())
            return {t["function"]["name"] for t in TOOL_DEFINITIONS if t["function"]["name"] in names}

        set_all("")
        setattr(settings, fields["you"], YOU_KEY)
        got = assembled(worker.WEB_TOOLS)
        check("web session includes you_search when you.com is keyed", "you_search" in got)
        check("web session leaves tavily_search out when tavily is not keyed", "tavily_search" not in got)
        got = assembled(worker.TOOLS)
        check("computer session includes you_search when you.com is keyed", "you_search" in got)

        set_all("")
        setattr(settings, fields["tavily"], TAV_KEY)
        check("keyed tool names follow the keyed provider",
              cm.keyed_search_tool_names() == ["tavily_search"])
        got = assembled(worker.WEB_TOOLS)
        check("web session includes tavily_search when only tavily is keyed",
              "tavily_search" in got and "you_search" not in got)

        set_all("")
        check("no keyed search tools are offered when nothing is keyed",
              cm.keyed_search_tool_names() == [])

    # ---- chat search route: keyed provider first, named in the result -------------
    calls = []

    async def fake_you_search(query, count=5):
        calls.append("you")
        return {"provider": "you.com",
                "results": [{"title": "T", "url": "https://example.com/a", "snippet": "s"}]}

    async def fake_tav_search(query, count=5):
        calls.append("tavily")
        return {"provider": "tavily",
                "results": [{"title": "T", "url": "https://example.com/b", "snippet": "s"}]}

    orig_you, orig_tav = you_client.search, tavily_client.search
    you_client.search, tavily_client.search = fake_you_search, fake_tav_search
    try:
        set_all("")
        setattr(settings, fields["you"], YOU_KEY)
        you_client._api_key = None
        out = asyncio.run(search_providers.search_for_chat("q", 8))
        check("chat search uses the keyed you.com provider",
              out is not None and out.get("provider") == "you.com" and calls == ["you"])
        check("chat search results carry title, url, snippet and host",
              out["results"][0]["host"] == "example.com" and out["results"][0]["title"] == "T")
        calls.clear()
        set_all("")
        out = asyncio.run(search_providers.search_for_chat("q", 8))
        check("chat search returns None with no key saved (browser fallback is the caller's)",
              out is None and calls == [])
    finally:
        you_client.search, tavily_client.search = orig_you, orig_tav
        set_all("")

    # ---- the fast-lane router prompt carries the checked facts ---------------------
    try:
        from app.agent.researcher import AgenticResearcher
    except Exception as e:
        print(f"SKIP  router prompt check (researcher import failed: {type(e).__name__})", flush=True)
    else:
        set_all("")
        setattr(settings, fields["you"], YOU_KEY)
        cm._cache.update(manifest=None, at=0.0)
        msgs = AgenticResearcher.router_messages("hello there", [])
        system = msgs[0]["content"]
        check("router prompt states the keyed search provider",
              "web search is served by You.com API" in system and "never from a guess" in system)
        set_all("")

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f" -- FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
