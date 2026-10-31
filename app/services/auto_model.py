"""Auto model selection: pick the model for a provider from benchmark evidence and cost.

The evidence is a bundled, dated snapshot of ViBench 2.0 results
(app/static/vibench_snapshot.json, source https://vibench.ai/), the same pattern
as the reference pricing table (app/static/model_pricing.json): there is no
public benchmark API, so the numbers ship with the app, carry their date and
source wherever they are shown, and are never presented as live data.

A benchmark row only speaks for an API model when the snapshot maps its display
name to that exact model id (see the snapshot's mapping_rule). Unmapped models
stay selectable by hand and rank last in Auto, with the reason stated, never
silently dropped.
"""
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xod.auto_model")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SNAPSHOT_PATH = STATIC_DIR / "vibench_snapshot.json"
PRICING_PATH = STATIC_DIR / "model_pricing.json"

SETTING_KEY = "llm_auto_model"

FORMULA = ("Auto ranks models by ViBench plan pass rate, breaks ties by lower cost "
           "(ViBench cost per build, else list price per 1M tokens), and ranks models "
           "without benchmark data last.")

_PICK_CACHE_TTL = 60.0
_pick_cache: Dict[str, Any] = {}


def load_snapshot(path: Optional[Path] = None) -> Dict[str, Any]:
    try:
        return json.loads((path or SNAPSHOT_PATH).read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Could not load ViBench snapshot: {e}")
        return {}


def load_pricing(path: Optional[Path] = None) -> Dict[str, Any]:
    try:
        return json.loads((path or PRICING_PATH).read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Could not load model pricing table: {e}")
        return {}


def lookup_price(pricing: Dict[str, Any], provider: str, model_id: str) -> Optional[Dict[str, float]]:
    """Same rule as lookupModelPrice in app/static/js/app.js: the exact id first,
    then the undated alias (dated snapshots share the alias price), else no row."""
    rows = (pricing.get("providers") or {}).get(provider)
    if not rows or not model_id:
        return None
    mid = str(model_id).strip()
    if mid in rows:
        return rows[mid]
    alias = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", mid)
    return rows.get(alias)


def _price_cost(row: Optional[Dict[str, float]]) -> Optional[float]:
    """One comparable per-1M figure for a pricing row: input + output."""
    if not row:
        return None
    try:
        return float(row.get("input") or 0) + float(row.get("output") or 0)
    except (TypeError, ValueError):
        return None


def _money(n: float) -> str:
    return f"${n:,.2f}"


def rank_models(provider: str, model_ids: List[str], snapshot: Optional[Dict[str, Any]] = None,
                pricing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Rank a provider's allowed model ids for Auto mode.

    Primary key: the mapped ViBench row's plan pass rate, highest first.
    Tiebreak: lower cost, ViBench cost per build when the row has one, else the
    pricing table's per-1M input + output. Models with no mapped benchmark row
    rank after all mapped ones (cheapest first among themselves), each with its
    reason stated. Nothing is dropped: every allowed id appears in the result.
    """
    snapshot = load_snapshot() if snapshot is None else snapshot
    pricing = load_pricing() if pricing is None else pricing

    bench_by_id: Dict[str, Dict[str, Any]] = {}
    for row in snapshot.get("rows") or []:
        mid = row.get("model_id")
        if mid:
            bench_by_id[mid] = row

    seen = set()
    entries: List[Dict[str, Any]] = []
    for idx, mid in enumerate(model_ids or []):
        if not mid or mid in seen:
            continue
        seen.add(mid)
        bench = bench_by_id.get(mid)
        price = lookup_price(pricing, provider, mid)
        price_cost = _price_cost(price)
        if bench is not None:
            cost = bench.get("cost_per_build_usd")
            cost_basis = "vibench"
            if cost is None:
                cost, cost_basis = price_cost, "list_price"
            reason = (f"ViBench: plan pass {bench.get('plan_pass_rate')}%, "
                      f"pass@1 {bench.get('pass_at_1')}%, {_money(bench['cost_per_build_usd'])} per build"
                      if bench.get("cost_per_build_usd") is not None else
                      f"ViBench: plan pass {bench.get('plan_pass_rate')}%, pass@1 {bench.get('pass_at_1')}%")
            entries.append({"id": mid, "mapped": True, "benchmark_name": bench.get("display_name"),
                            "plan_pass_rate": bench.get("plan_pass_rate"), "pass_at_1": bench.get("pass_at_1"),
                            "cost_per_build_usd": bench.get("cost_per_build_usd"),
                            "price_per_1m": price, "cost_basis": cost_basis,
                            "_sort": (0, -(bench.get("plan_pass_rate") or 0),
                                      cost if cost is not None else float("inf"), idx),
                            "reason": reason})
        else:
            if price is not None:
                reason = (f"no benchmark data; list price {_money(price['input'])} in / "
                          f"{_money(price['output'])} out per 1M tokens")
            else:
                reason = "no benchmark data, no reference price"
            entries.append({"id": mid, "mapped": False, "benchmark_name": None,
                            "plan_pass_rate": None, "pass_at_1": None, "cost_per_build_usd": None,
                            "price_per_1m": price, "cost_basis": "list_price" if price else None,
                            "_sort": (1, 0, price_cost if price_cost is not None else float("inf"), idx),
                            "reason": reason})

    entries.sort(key=lambda e: e["_sort"])
    ranked = []
    for i, e in enumerate(entries):
        e = {k: v for k, v in e.items() if k != "_sort"}
        e["rank"] = i + 1
        ranked.append(e)

    return {
        "provider": provider,
        "pick": ranked[0]["id"] if ranked else None,
        "pick_reason": ranked[0]["reason"] if ranked else "no models available from this provider",
        "ranked": ranked,
        "formula": FORMULA,
        "snapshot": {"benchmark": snapshot.get("benchmark"), "source": snapshot.get("source"),
                     "snapshot_date": snapshot.get("snapshot_date")},
    }


def auto_enabled() -> bool:
    try:
        from app.services.storage import get_setting
        return get_setting(SETTING_KEY, "0") == "1"
    except Exception:
        return False


def set_auto_enabled(on: bool) -> None:
    from app.services.storage import set_setting
    set_setting(SETTING_KEY, "1" if on else "0")
    _pick_cache.clear()


async def pick_for(llm_service: Any, provider: str) -> Dict[str, Any]:
    """The send-time pick for a provider: rank its current models and take the top.

    The ranking is cached per provider for a minute so a chatty send path does
    not re-ask the provider for its model list on every message; the pick itself
    is still resolved when the caller asks, from that fresh-enough list. The
    list is fetched with the active key profile's key (llm_service resolves it
    at call time), so the cache is keyed by the active profile as well:
    switching keys must not inherit the previous key's ranking.
    """
    now = time.monotonic()
    cache_key = provider
    try:
        from app.services import key_profiles
        if key_profiles.is_key_provider(provider):
            cache_key = f"{provider}|{key_profiles.active_profile(provider)['id']}"
    except Exception:
        pass
    hit = _pick_cache.get(cache_key)
    if hit and now - hit[0] < _PICK_CACHE_TTL:
        return hit[1]
    models = await llm_service.list_models(provider_override=provider)
    ids = [m.get("id") for m in models if m.get("id")]
    result = rank_models(provider, ids)
    _pick_cache[cache_key] = (now, result)
    return result
