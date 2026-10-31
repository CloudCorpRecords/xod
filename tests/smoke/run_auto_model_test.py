"""
Auto model mode (services/auto_model.py): the ViBench snapshot, the ranking, and
the /api/llm/auto-pick endpoint with a stubbed model list.

  - the shipped snapshot loads, is dated and sourced, carries the official
    ViBench 2.0 rows exactly, and maps only the two justified Anthropic ids
  - ranking fixtures: a clear quality winner, a cost tiebreak at equal plan
    pass rate, list price stepping in when a row has no build cost, unmapped
    models ranked last with their reasons stated and never dropped, and the
    empty list
  - the endpoint answers in its documented shape from a stubbed Detect list
    (the keychain is a throwaway file backend: no real key is read)

  .venv/bin/python tests/smoke/run_auto_model_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-automodel-"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"),
                   "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


from app.services import auto_model

# ---- The shipped snapshot -------------------------------------------------
snap = auto_model.load_snapshot()
check("snapshot loads with source, benchmark and date",
      snap.get("source") == "https://vibench.ai/" and snap.get("benchmark") == "ViBench 2.0"
      and snap.get("snapshot_date") == "2026-10-30")
rows = snap.get("rows") or []
check("snapshot carries the 9 official rows", len(rows) == 9, f"got {len(rows)}")
by_name = {r["display_name"]: r for r in rows}
sol = by_name.get("GPT-6.1 Sol") or {}
check("GPT-6.1 Sol row matches the published table",
      (sol.get("pass_at_1"), sol.get("plan_pass_rate"), sol.get("cost_per_build_usd"), sol.get("build_time"))
      == (64.7, 96.9, 12.34, "3.0h"), str(sol))
opus = by_name.get("Claude Opus 5.5") or {}
check("Claude Opus 5.5 row matches the published table",
      (opus.get("pass_at_1"), opus.get("plan_pass_rate"), opus.get("cost_per_build_usd")) == (48.5, 93.7, 23.05),
      str(opus))
check("every row carries the full schema",
      all(set(r) >= {"display_name", "pass_at_1", "plan_pass_rate", "cost_per_build_usd", "build_time", "model_id"}
          for r in rows))
check("model_id_map holds only the two justified Anthropic ids",
      snap.get("model_id_map") == {"Claude Opus 5.5": "claude-opus-5-5", "Claude Sonnet 5.5": "claude-sonnet-5-5"},
      str(snap.get("model_id_map")))
check("row model_id fields agree with the map",
      all(r["model_id"] == snap["model_id_map"].get(r["display_name"]) for r in rows))
secondary = (snap.get("secondary_tables") or [{}])[0]
check("the GPT generations table rides along as a separate, unmapped run",
      secondary.get("name") == "Scores across GPT generations" and len(secondary.get("rows") or []) == 9
      and all(r["model_id"] is None for r in secondary["rows"]))

# ---- Ranking against the shipped snapshot ---------------------------------
res = auto_model.rank_models("anthropic", ["claude-sonnet-5-5", "claude-opus-5-5"])
check("clear quality winner: opus beats sonnet on plan pass rate",
      res["pick"] == "claude-opus-5-5" and res["ranked"][0]["rank"] == 1
      and "93.7" in res["ranked"][0]["reason"], str(res["ranked"]))
res = auto_model.rank_models("openai", ["gpt-4o", "gpt-5"])
check("unmapped models rank by list price among themselves, none dropped",
      res["pick"] == "gpt-5" and len(res["ranked"]) == 2
      and all(e["mapped"] is False and "no benchmark data" in e["reason"] for e in res["ranked"]),
      str(res["ranked"]))

# ---- Ranking fixtures ------------------------------------------------------
FIX_SNAP = {"benchmark": "ViBench 2.0", "source": "https://vibench.ai/", "snapshot_date": "2026-10-30",
            "rows": [
                {"display_name": "Model A", "pass_at_1": 50.0, "plan_pass_rate": 90.0,
                 "cost_per_build_usd": 30.0, "build_time": "1h", "model_id": "model-a"},
                {"display_name": "Model B", "pass_at_1": 40.0, "plan_pass_rate": 90.0,
                 "cost_per_build_usd": 10.0, "build_time": "1h", "model_id": "model-b"},
                {"display_name": "Model C", "pass_at_1": 10.0, "plan_pass_rate": 95.0,
                 "cost_per_build_usd": None, "build_time": "1h", "model_id": "model-c"},
            ]}
FIX_PRICING = {"providers": {"testprov": {"model-c": {"input": 1.0, "output": 3.0},
                                          "plain-x": {"input": 0.5, "output": 0.5}}}}

res = auto_model.rank_models("testprov", ["model-a", "model-b"], FIX_SNAP, FIX_PRICING)
check("cost tiebreak: equal plan pass rate, the cheaper build wins",
      res["pick"] == "model-b" and res["ranked"][0]["cost_basis"] == "vibench", str(res["ranked"]))
res = auto_model.rank_models("testprov", ["model-a", "model-c"], FIX_SNAP, FIX_PRICING)
check("a row without build cost still wins on quality, priced from the table",
      res["pick"] == "model-c" and res["ranked"][0]["cost_basis"] == "list_price", str(res["ranked"]))
res = auto_model.rank_models("testprov", ["model-b", "plain-x", "mystery"], FIX_SNAP, FIX_PRICING)
check("unmapped models rank last, priced first, reasons stated, nothing dropped",
      [e["id"] for e in res["ranked"]] == ["model-b", "plain-x", "mystery"]
      and res["ranked"][1]["reason"].startswith("no benchmark data; list price")
      and res["ranked"][2]["reason"] == "no benchmark data, no reference price", str(res["ranked"]))
res = auto_model.rank_models("testprov", [], FIX_SNAP, FIX_PRICING)
check("empty allowed list: no pick, no ranks, the reason says so",
      res["pick"] is None and res["ranked"] == [] and "no models" in res["pick_reason"], str(res))
res = auto_model.rank_models("testprov", ["model-a", "model-a", "model-b"], FIX_SNAP, FIX_PRICING)
check("duplicate ids collapse to one entry", len(res["ranked"]) == 2, str(res["ranked"]))

# ---- The endpoint, with a stubbed Detect list ------------------------------
import app.main as main


async def fake_list_models(provider_override=None):
    return [{"id": "claude-sonnet-5-5", "name": "claude-sonnet-5-5", "is_loaded": True},
            {"id": "claude-opus-5-5", "name": "claude-opus-5-5", "is_loaded": True}]


main.llm_service.list_models = fake_list_models
res = asyncio.run(main.get_llm_auto_pick("anthropic"))
check("endpoint returns pick, ranked reasons, formula and snapshot meta",
      res["pick"] == "claude-opus-5-5" and len(res["ranked"]) == 2
      and res["snapshot"]["snapshot_date"] == "2026-10-30" and "plan pass rate" in res["formula"]
      and res["enabled"] is False and res["provider"] == "anthropic", str(res)[:300])
res = asyncio.run(main.get_llm_vibench())
check("vibench endpoint serves the snapshot itself",
      res["benchmark"] == "ViBench 2.0" and len(res["rows"]) == 9)

# ---- Settings wiring --------------------------------------------------------
html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
js = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
check("Settings page carries the Auto toggle, pick line and snapshot view",
      all(x in html for x in ("checkboxLlmAutoModel", "autoModelPickLine", "vibenchDetails", "Auto (benchmark + cost)")))
check("app.js fetches the pick, renders the table and saves the toggle",
      all(x in js for x in ("/api/llm/auto-pick", "renderVibenchTable", "llm_auto_model", "vibench_snapshot.json")))

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
