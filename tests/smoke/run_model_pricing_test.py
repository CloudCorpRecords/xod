"""
Model pricing reference table + Settings model picker wiring:
  - the table at app/static/model_pricing.json loads and is dated
  - lookup rule (mirrors lookupModelPrice in app/static/js/app.js): exact id,
    then the undated alias for dated snapshots, else no row
  - known OpenAI models return their verified rows; unknown ones return none
  - the Settings page carries the detected-models dropdown and price line,
    and app.js fetches the table and populates the dropdown on Detect Models
Nothing is served, signed in to, or called over the network.
  .venv/bin/python tests/smoke/run_model_pricing_test.py
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TABLE_PATH = ROOT / "app" / "static" / "model_pricing.json"
table = json.loads(TABLE_PATH.read_text(encoding="utf-8"))

# The table is dated and says what its numbers mean.
assert table.get("currency") == "USD", table.get("currency")
assert table.get("unit") == "per 1M tokens", table.get("unit")
as_of = date.fromisoformat(table["as_of"])
assert as_of.year >= 2026, as_of
assert "developers.openai.com" in table.get("source", ""), table.get("source")
print(f"table loads: as_of {table['as_of']}, source recorded")


def lookup(provider, model_id):
    """Same rule as lookupModelPrice in app.js: exact, then undated alias."""
    rows = (table.get("providers") or {}).get(provider)
    if not rows or not model_id:
        return None
    mid = str(model_id).strip()
    if mid in rows:
        return rows[mid]
    alias = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", mid)
    return rows.get(alias)


# Verified rows (OpenAI official model pages, checked 2026-10-30).
assert lookup("openai", "gpt-4o") == {"input": 2.5, "output": 10.0}
assert lookup("openai", "gpt-4o-mini") == {"input": 0.15, "output": 0.6}
assert lookup("openai", "gpt-4.1") == {"input": 2.0, "output": 8.0}
assert lookup("openai", "gpt-5-nano") == {"input": 0.05, "output": 0.4}
print("known models return their verified rows")

# Dated snapshots resolve to the alias row.
assert lookup("openai", "gpt-4o-2024-08-06") == {"input": 2.5, "output": 10.0}
assert lookup("openai", "gpt-5-2025-08-07") == {"input": 1.25, "output": 10.0}
print("dated snapshots resolve to the alias row")

# Anything unverified returns no row, so the UI shows no reference price.
assert lookup("openai", "gpt-99-ultra") is None
assert lookup("openai", "") is None
assert lookup("anthropic", "gpt-4o") is None
assert lookup("lm_studio", "gpt-4o") is None
print("unknown models and unpriced providers return no row")

# Every row is a sane positive price pair.
for provider, rows in table["providers"].items():
    for model_id, row in rows.items():
        assert isinstance(row["input"], (int, float)) and row["input"] > 0, (provider, model_id)
        assert isinstance(row["output"], (int, float)) and row["output"] > 0, (provider, model_id)
print("every row is a positive input/output pair")

# Settings wiring: the dropdown and price line exist, and Detect Models fills them.
html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
assert 'id="selectLlmModelDetected"' in html
assert 'id="detectedModelsContainer"' in html
assert 'id="llmModelPriceLine"' in html
js = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
assert "/static/model_pricing.json" in js
assert "populateDetectedModelSelect(models," in js
assert "updateLlmModelPriceLine()" in js
assert "lookupModelPrice" in js
print("settings page carries the dropdown, the price line, and the wiring")

print("ok: model pricing table and picker wiring all check out")
sys.exit(0)
