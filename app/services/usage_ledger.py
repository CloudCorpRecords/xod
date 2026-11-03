"""
The per-call usage ledger: one JSON line per model call, in the app's data folder
(DATA_DIR/usage_ledger.jsonl), kept next to xod.db where the older usage log lives.

Why a second record beside services/usage.py (the Usage tab's SQLite log): this one names
the API key profile that paid for the call (never the key) and prices every call from the
dated reference table (services/pricing.py), so Settings can answer "which key, which
model, how many tokens, about what cost" per key. The SQLite log is untouched.

Token counts are the provider's own when its response carried a usage object
(tokens_source "api"); otherwise they are estimated at about 4 characters per token, the
heuristic services/usage.py already documents, and the record says so (tokens_source
"estimated") so estimates are never silently mixed into actuals. cost_usd is null when the
pricing table has no row for the model: an unpriced call is reported as unpriced, never
priced by guessing.

Writing never blocks or fails a model call: any error here is logged and swallowed.
"""
import datetime as dt
import json
import logging
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR
from app.services import pricing

logger = logging.getLogger("xod.usage_ledger")
LEDGER = DATA_DIR / "usage_ledger.jsonl"
CHARS_PER_TOKEN = 4          # the documented estimate when a provider reports no usage
_lock = threading.Lock()
RANGES = ("today", "7d", "all")


def estimate_tokens(chars: int) -> int:
    return max(0, int(chars) // CHARS_PER_TOKEN)


def _counts(usage: Optional[Dict[str, Any]], prompt_chars: int, completion_chars: int):
    """(prompt_tokens, completion_tokens, tokens_source) from a provider usage object or characters."""
    u = usage or {}
    pt = u.get("prompt_tokens", u.get("input_tokens"))
    ct = u.get("completion_tokens", u.get("output_tokens"))
    if pt is not None and ct is not None:
        return int(pt), int(ct), "api"
    if pt is None:
        pt = max(1, estimate_tokens(prompt_chars)) if prompt_chars else 0
    if ct is None:
        ct = estimate_tokens(completion_chars)
    return int(pt), int(ct), "estimated"


def append_record(provider: str, model: str, key_profile: Optional[str] = None,
                  usage: Optional[Dict[str, Any]] = None, prompt_chars: int = 0,
                  completion_chars: int = 0, context: str = "") -> None:
    """One finished call. Called from llm_routing.record, the choke point every LLM-service
    call passes through. Never raises."""
    try:
        pt, ct, source = _counts(usage, prompt_chars, completion_chars)
        rec = {
            "ts": time.time(),
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
            "provider": provider,
            "model": model,
            "key_profile": key_profile or "",
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
            "tokens_source": source,
            "cost_usd": pricing.cost_usd(provider, model, pt, ct),
            "context": context or "",
        }
        line = json.dumps(rec)
        with _lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(LEDGER, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:
        logger.debug(f"usage ledger skipped: {e}")


def _read_all() -> List[Dict[str, Any]]:
    try:
        lines = LEDGER.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
            if isinstance(rec, dict) and "ts" in rec:
                out.append(rec)
        except ValueError:
            continue
    return out


def _since(rng: str) -> float:
    if rng == "today":
        return dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if rng == "7d":
        return time.time() - 7 * 86400
    return 0.0


def _blank() -> Dict[str, Any]:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "cost_usd": 0.0, "unpriced_calls": 0, "records_api": 0, "records_estimated": 0}


def _add(bucket: Dict[str, Any], rec: Dict[str, Any]) -> None:
    bucket["calls"] += 1
    bucket["prompt_tokens"] += int(rec.get("prompt_tokens") or 0)
    bucket["completion_tokens"] += int(rec.get("completion_tokens") or 0)
    bucket["total_tokens"] += int(rec.get("total_tokens") or 0)
    if rec.get("cost_usd") is None:
        bucket["unpriced_calls"] += 1
    else:
        bucket["cost_usd"] = round(bucket["cost_usd"] + float(rec["cost_usd"]), 6)
    if rec.get("tokens_source") == "api":
        bucket["records_api"] += 1
    else:
        bucket["records_estimated"] += 1


def summary(rng: str = "today") -> Dict[str, Any]:
    """Totals and breakdowns for one range ("today", "7d", "all"), from the JSONL ledger."""
    if rng not in RANGES:
        rng = "today"
    since = _since(rng)
    rows = [r for r in _read_all() if float(r.get("ts") or 0) >= since]
    totals = _blank()
    by_model: Dict[str, Dict[str, Any]] = {}
    by_key: Dict[str, Dict[str, Any]] = {}
    by_day: Dict[str, Dict[str, Any]] = {}
    for rec in rows:
        _add(totals, rec)
        mk = f"{rec.get('provider', '')}\n{rec.get('model', '')}"
        m = by_model.setdefault(mk, {**_blank(), "provider": rec.get("provider", ""), "model": rec.get("model", "")})
        _add(m, rec)
        kk = f"{rec.get('provider', '')}\n{rec.get('key_profile', '')}"
        k = by_key.setdefault(kk, {**_blank(), "provider": rec.get("provider", ""), "key_profile": rec.get("key_profile", "")})
        _add(k, rec)
        day = dt.datetime.fromtimestamp(float(rec.get("ts") or 0)).strftime("%Y-%m-%d")
        d = by_day.setdefault(day, {**_blank(), "day": day})
        _add(d, rec)
    last = None
    all_rows = _read_all()
    if all_rows:
        r = max(all_rows, key=lambda x: float(x.get("ts") or 0))
        last = {k: r.get(k) for k in ("timestamp", "provider", "model", "key_profile", "prompt_tokens",
                                      "completion_tokens", "total_tokens", "tokens_source", "cost_usd", "context")}
    return {
        "range": rng,
        "totals": totals,
        "by_model": sorted(by_model.values(), key=lambda x: -x["total_tokens"]),
        "by_key_profile": sorted(by_key.values(), key=lambda x: -x["total_tokens"]),
        "by_day": [by_day[d] for d in sorted(by_day)],
        "last_call": last,
        "pricing_as_of": pricing.as_of(),
        "costs_are_estimates": True,
    }


def mission_totals(mission_id: str) -> Dict[str, Any]:
    """Token and cost totals for one mission (services/missions.py), summed
    from this ledger. A mission's calls carry two of the ledger's context
    tags: its planning calls run under "mission:<id>" (the mission runner
    stamps the activity origin while it plans), and each subtask's calls run
    under "task:<task id>" (the origin the task queue stamps on every call
    inside a task), resolved to the mission through the mission_subtasks
    rows in xod.db. Returns {} when nothing attributable is recorded, so
    the caller keeps saying usage is not tracked rather than showing
    numbers that were not measured."""
    if not mission_id:
        return {}
    tags = {f"mission:{mission_id}"}
    try:
        db = DATA_DIR / "xod.db"
        if db.exists():
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                rows = conn.execute("SELECT task_id FROM mission_subtasks WHERE mission_id = ?",
                                    (mission_id,)).fetchall()
            finally:
                conn.close()
            tags.update(f"task:{row[0]}" for row in rows if row and row[0])
    except Exception:
        pass
    totals = _blank()
    for rec in _read_all():
        if rec.get("context") in tags:
            _add(totals, rec)
    if not totals["calls"]:
        return {}
    totals["attribution"] = ("planning calls plus subtask execution calls, "
                             "matched by the ledger context tags")
    totals["pricing_as_of"] = pricing.as_of()
    return totals


def _default_output_allowance() -> int:
    try:
        from app.services.storage import get_setting
        return max(0, int(get_setting("usage_output_allowance", "") or 1024))
    except Exception:
        return 1024


# Pre-flight prompt estimates run high on real prompts: provider tokenizers pack
# typical English and code denser than the raw chars-per-token heuristic assumes.
# Measured against the provider counts on three gpt-4o planning prompts in the
# Brain v1 hardening round (2026-10-30): actual/estimated ratios 0.869, 0.917,
# 0.861 (mean 0.88). The pre-flight estimate applies that factor so the number a
# user sees before sending is the expected size, not a consistent overstatement.
# Ledger records are untouched: when a provider count is unavailable they keep
# the raw heuristic and stay labeled estimated.
PROMPT_ESTIMATE_FACTOR = 0.88


def estimate(provider: str, model: str, prompt_chars: int, max_tokens: Optional[int] = None) -> Dict[str, Any]:
    """A pre-flight estimate for one call that has not run yet. Everything here is an
    estimate and says so: input tokens from the prompt's size at ~4 characters a token,
    output tokens the call's own budget (or the usage_output_allowance setting)."""
    pt = max(1, int(estimate_tokens(prompt_chars) * PROMPT_ESTIMATE_FACTOR)) if prompt_chars else 0
    ct = int(max_tokens) if max_tokens else _default_output_allowance()
    return {
        "estimate": True,
        "provider": provider,
        "model": model,
        "estimated_prompt_tokens": pt,
        "estimated_completion_tokens": ct,
        "estimated_total_tokens": pt + ct,
        "estimated_cost_usd": pricing.cost_usd(provider, model, pt, ct),
        "pricing_as_of": pricing.as_of(),
        "tokens_source": "estimated",
    }
