"""
Reference prices for cloud models, from the one dated table the Settings UI also reads:
app/static/model_pricing.json (prices verified by hand against the provider's official
pricing pages; the table says when, in its as_of field).

Lookup mirrors the UI's rule (app/static/js/app.js, lookupModelPrice): the exact model id
first, then the undated alias, so a dated snapshot like gpt-4o-2024-08-06 shares its base
model's price. A model with no row has NO price (None), never a guess: costs computed from
this table are estimates for budgeting, and an invented price would be worse than none.
"""
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from app.paths import APP_ROOT

logger = logging.getLogger("xod.pricing")
TABLE_PATH = APP_ROOT / "app" / "static" / "model_pricing.json"
_ALIAS = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_cache: Dict[str, Any] = {"mtime": None, "table": None}


def table() -> Optional[Dict[str, Any]]:
    try:
        mtime = Path(TABLE_PATH).stat().st_mtime
    except OSError:
        return None
    if _cache["mtime"] != mtime:
        try:
            _cache["table"] = json.loads(Path(TABLE_PATH).read_text(encoding="utf-8"))
            _cache["mtime"] = mtime
        except Exception as e:
            logger.warning(f"Could not read the model pricing table: {e}")
            return None
    return _cache["table"]


def as_of() -> Optional[str]:
    t = table()
    return t.get("as_of") if t else None


def price_for(provider: str, model: str) -> Optional[Dict[str, float]]:
    """{input, output} in USD per 1M tokens for this model, or None when the table has no row."""
    t = table()
    if not t or not model:
        return None
    rows = (t.get("providers") or {}).get(provider)
    if not rows:
        return None
    mid = str(model).strip()
    row = rows.get(mid) or rows.get(_ALIAS.sub("", mid))
    if not row:
        return None
    return {"input": float(row["input"]), "output": float(row["output"])}


def cost_usd(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    """Estimated cost of one call from the reference table, or None when the model is unpriced."""
    row = price_for(provider, model)
    if row is None:
        return None
    return round(prompt_tokens / 1e6 * row["input"] + completion_tokens / 1e6 * row["output"], 6)
