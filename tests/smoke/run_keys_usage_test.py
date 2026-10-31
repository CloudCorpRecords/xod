"""
Key profiles, the usage ledger, pricing, and pre-flight estimates
(services/key_profiles.py, services/usage_ledger.py, services/pricing.py).

Everything runs against a throwaway data folder and a throwaway Keychain vault
(the file keyring), so no real key is ever read: the "keys" here are made-up
strings, and the checks prove they are stored per profile, resolved at call
time, masked in every listing, and never what the ledger records (it records
the profile NAME).

  .venv/bin/python tests/smoke/run_keys_usage_test.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-keys-usage-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir()

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


LEGACY_KEY = "sk-legacy-test-0001"
WORK_KEY = "sk-work-test-0002"


def main():
    from app.services import key_profiles, pricing, secret_store, usage_ledger

    # ---- migration: the single saved key becomes the Default profile, untouched
    secret_store.save("openai_api_key", LEGACY_KEY)
    listing = key_profiles.list_profiles("openai")
    default = listing["profiles"][0]
    check("default profile exists and is active", default["id"] == "default" and listing["active"] == "default")
    check("default profile holds the existing key", default["has_key"] is True)
    check("listing masks the key", LEGACY_KEY not in str(listing) and default["masked"].endswith(LEGACY_KEY[-4:]))
    value, name = key_profiles.active_key("openai")
    check("active key resolves to the existing key", value == LEGACY_KEY and name == "Default")

    # ---- CRUD
    key_profiles.create_profile("openai", "Work", WORK_KEY)
    listing = key_profiles.list_profiles("openai")
    work = next(p for p in listing["profiles"] if p["name"] == "Work")
    check("created profile is listed, key masked", work["has_key"] and WORK_KEY not in str(listing))
    try:
        key_profiles.create_profile("openai", "work")
        check("duplicate profile name refused", False)
    except ValueError:
        check("duplicate profile name refused", True)
    key_profiles.create_profile("openai", "Empty")
    key_profiles.set_active("openai", work["id"])
    value, name = key_profiles.active_key("openai")
    check("activating a profile switches the resolved key", value == WORK_KEY and name == "Work")
    key_profiles.rename_profile("openai", work["id"], "Workbench")
    names = [p["name"] for p in key_profiles.list_profiles("openai")["profiles"]]
    check("rename sticks", "Workbench" in names and "Work" not in names)
    key_profiles.set_models("openai", work["id"], ["gpt-4o", "gpt-4o-mini"])
    check("detected models stored on the profile", key_profiles.models_for("openai", work["id"]) == ["gpt-4o", "gpt-4o-mini"])
    empty = next(p for p in key_profiles.list_profiles("openai")["profiles"] if p["name"] == "Empty")
    key_profiles.set_profile_key("openai", empty["id"], "sk-empty-test-0003")
    check("a key can be saved to an existing profile", key_profiles.key_for_profile("openai", empty["id"]) == "sk-empty-test-0003")
    key_profiles.delete_profile("openai", empty["id"])
    check("deleted profile's key is gone from the vault", key_profiles.key_for_profile("openai", empty["id"]) == "")
    try:
        key_profiles.delete_profile("openai", "default")
        check("the Default profile cannot be deleted", False)
    except ValueError:
        check("the Default profile cannot be deleted", True)
    key_profiles.delete_profile("openai", work["id"])
    check("deleting the active profile falls back to Default", key_profiles.active_profile("openai")["id"] == "default")

    # ---- the LLM layer resolves the active profile's key at call time
    from app.services.llm import llm_service
    key_profiles.create_profile("openai", "Work", WORK_KEY)
    wid = next(p["id"] for p in key_profiles.list_profiles("openai")["profiles"] if p["name"] == "Work")
    key_profiles.set_active("openai", wid)
    ep = llm_service._build_endpoint("openai")
    check("endpoint uses the active profile's key", ep["api_key"] == WORK_KEY and ep.get("key_profile") == "Work")
    ep2 = llm_service._build_endpoint("openai", key_profile_id="default")
    check("endpoint can be built for a named profile (Detect)", ep2["api_key"] == LEGACY_KEY and ep2.get("key_profile") == "Default")
    key_profiles.set_active("openai", "default")

    # ---- pricing: exact id, undated alias, unknown is None (never a guess)
    row = pricing.price_for("openai", "gpt-4o")
    check("pricing: exact id", row == {"input": 2.5, "output": 10.0}, str(row))
    check("pricing: dated snapshot uses the alias price", pricing.price_for("openai", "gpt-4o-2024-08-06") == row)
    check("pricing: unknown model has no price", pricing.price_for("openai", "mystery-1") is None)
    check("pricing: unknown provider has no price", pricing.price_for("groq", "gpt-4o") is None)
    check("pricing: cost math", pricing.cost_usd("openai", "gpt-4o", 1_000_000, 1_000_000) == 12.5)
    check("pricing: table is dated", bool(pricing.as_of()))

    # ---- the ledger: api counts vs estimates stay distinct, cost from the table
    usage_ledger.append_record("openai", "gpt-4o", key_profile="Work",
                               usage={"prompt_tokens": 100, "completion_tokens": 50}, context="chat:abc")
    usage_ledger.append_record("openai", "gpt-4o", key_profile="Work",
                               usage=None, prompt_chars=400, completion_chars=200, context="chat:abc")
    usage_ledger.append_record("openai", "mystery-1", key_profile="Default",
                               usage={"prompt_tokens": 10, "completion_tokens": 5}, context="task:xyz")
    s = usage_ledger.summary("all")
    t = s["totals"]
    check("ledger totals count every call", t["calls"] == 3, str(t))
    check("ledger separates api from estimated counts", t["records_api"] == 2 and t["records_estimated"] == 1, str(t))
    check("ledger token totals (api + estimated)", t["total_tokens"] == 150 + 150 + 15, str(t))
    expect_cost = round(2 * (100 / 1e6 * 2.5 + 50 / 1e6 * 10.0), 6)
    check("ledger cost comes from the pricing table", abs(t["cost_usd"] - expect_cost) < 1e-9, f"{t['cost_usd']} vs {expect_cost}")
    check("unpriced model counts as unpriced, not zero-priced", t["unpriced_calls"] == 1, str(t))
    by_key = {k["key_profile"]: k for k in s["by_key_profile"]}
    check("ledger breaks down by key profile", by_key["Work"]["calls"] == 2 and by_key["Default"]["calls"] == 1, str(by_key))
    by_model = {m["model"]: m for m in s["by_model"]}
    check("ledger breaks down by model", by_model["gpt-4o"]["total_tokens"] == 300, str(by_model))
    check("ledger breaks down by day", len(s["by_day"]) == 1 and s["by_day"][0]["calls"] == 3, str(s["by_day"]))
    check("ledger last call is the newest record", s["last_call"]["model"] == "mystery-1" and s["last_call"]["key_profile"] == "Default")
    check("ledger names the pricing date", s["pricing_as_of"] == pricing.as_of())
    raw = (TMP / "data" / "usage_ledger.jsonl").read_text()
    check("ledger never contains a key, only profile names", LEGACY_KEY not in raw and WORK_KEY not in raw)
    check("summary of an unknown range falls back to today", usage_ledger.summary("bogus")["range"] == "today")

    # ---- pre-flight estimate: labeled, heuristic in, budget out, table cost
    est = usage_ledger.estimate("openai", "gpt-4o", prompt_chars=4000, max_tokens=500)
    check("estimate shape", est["estimate"] is True and est["tokens_source"] == "estimated", str(est))
    check("estimate token math (chars/4 in, budget out)", est["estimated_prompt_tokens"] == 1000 and est["estimated_completion_tokens"] == 500)
    check("estimate cost from the table", abs(est["estimated_cost_usd"] - (1000 / 1e6 * 2.5 + 500 / 1e6 * 10.0)) < 1e-9, str(est))
    est2 = usage_ledger.estimate("openai", "mystery-1", prompt_chars=400)
    check("estimate for an unpriced model has no cost", est2["estimated_cost_usd"] is None)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
