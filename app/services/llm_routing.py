"""
Smart model routing: the local model by default, a cloud model for the hard parts, with your
permission and a budget.

Modes (Settings → AI model → "Stronger model for hard steps"):
  off   everything stays on this Mac (default)
  ask   when a turn struggles, the reply offers "Try with <cloud model>" with an estimated cost;
        you can also press it on any reply
  auto  once a turn has failed twice even after self-repair, the rest of that turn switches to
        the cloud model on its own, as long as this month's spend is under the budget

A turn's routing lives in a context variable, so every llm_service call in that turn (planner,
repairs, the final answer) follows it without the agent code knowing.

Every cloud call's tokens and estimated cost are recorded (llm_usage table). PRICES are rough
list prices in USD per million tokens (input, output); edit them in Settings if yours differ.
"""
import contextvars
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.routing")
DB = DATA_DIR / "xod.db"
LOCAL_PROVIDERS = ("xod", "lm_studio", "ollama", "specialist")
FAILURES_TO_ESCALATE = 2

# Prefix match on the model id. Approximate; users can override in settings ("price_overrides").
PRICES: Dict[str, tuple] = {
    "claude-opus": (15.0, 75.0), "claude-fable": (15.0, 75.0), "claude-sonnet": (3.0, 15.0), "claude-haiku": (1.0, 5.0),
    "gpt-5-mini": (0.25, 2.0), "gpt-5": (1.25, 10.0), "gpt-4o-mini": (0.15, 0.6), "gpt-4o": (2.5, 10.0),
    "o3": (2.0, 8.0), "o4-mini": (1.1, 4.4),
    "gemini-2.5-pro": (1.25, 10.0), "gemini-2.5-flash": (0.3, 2.5),
    "llama-3.3-70b": (0.59, 0.79), "meta-llama/llama-3.3-70b": (0.13, 0.4),
    "accounts/fireworks": (0.9, 0.9),
}


@dataclass
class TurnRoute:
    endpoint: Optional[Dict[str, Any]] = None   # set when this turn runs on the cloud model
    failures: int = 0
    escalated_reason: str = ""
    cloud_calls: int = 0
    cost_usd: float = 0.0
    notes: List[str] = field(default_factory=list)


turn = contextvars.ContextVar("xod_turn_route", default=None)


def start_turn(endpoint: Optional[Dict[str, Any]] = None) -> TurnRoute:
    r = TurnRoute(endpoint=endpoint, escalated_reason="you asked" if endpoint else "")
    turn.set(r)
    return r


def current() -> Optional[TurnRoute]:
    return turn.get()


# ---------------------------------------------------------------- settings

def settings() -> Dict[str, Any]:
    from app.services.storage import get_setting
    try:
        s = json.loads(get_setting("routing", "") or "{}")
    except ValueError:
        s = {}
    return {"mode": s.get("mode", "off"), "provider": s.get("provider", ""), "model": s.get("model", ""),
            "monthly_budget_usd": float(s.get("monthly_budget_usd", 5.0)), "price_overrides": s.get("price_overrides", {})}


def save_settings(**changes: Any) -> Dict[str, Any]:
    from app.services.storage import set_setting
    s = settings()
    for k in ("mode", "provider", "model", "monthly_budget_usd", "price_overrides"):
        if changes.get(k) is not None:
            s[k] = changes[k]
    if s["mode"] not in ("off", "ask", "auto"):
        s["mode"] = "off"
    set_setting("routing", json.dumps(s))
    return s


def cloud_endpoint() -> Optional[Dict[str, Any]]:
    """The cloud model to escalate to: the one picked in settings, else the first with a key."""
    from app.services.llm import llm_service
    s = settings()
    tiers = llm_service.get_available_tiers()
    if s["provider"]:
        ep = llm_service._build_endpoint(s["provider"], s["model"] or None)
        if ep["api_key"] != "missing":
            return {**ep, "label": f"{s['provider']}: {ep['model']}"}
    if tiers["cloud_options"]:
        o = tiers["cloud_options"][0]
        return {**llm_service._build_endpoint(o["provider"], o["model"]), "label": o["label"]}
    return None


# ---------------------------------------------------------------- escalation during a turn

def note_failure(reason: str) -> Optional[str]:
    """Called when a step fails even after self-repair. Returns a status line if this turn now escalates."""
    r = current()
    if not r or r.endpoint:
        return None
    r.failures += 1
    s = settings()
    if s["mode"] != "auto" or r.failures < FAILURES_TO_ESCALATE:
        return None
    if month_spend() >= s["monthly_budget_usd"]:
        r.notes.append("budget reached")
        return None
    ep = cloud_endpoint()
    if not ep:
        return None
    r.endpoint, r.escalated_reason = ep, reason
    return f"☁ Two steps failed on the local model; switching to {ep['label']} for the rest of this answer"


def should_offer() -> bool:
    r = current()
    return bool(r and not r.endpoint and r.failures >= 1 and settings()["mode"] in ("ask", "auto") and cloud_endpoint())


# ---------------------------------------------------------------- usage and cost

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS llm_usage (ts REAL, provider TEXT, model TEXT,
                 prompt_tokens INTEGER, completion_tokens INTEGER, cost_usd REAL, estimated INTEGER)""")
    return c


def price_for(model: str) -> Optional[tuple]:
    over = settings().get("price_overrides") or {}
    for table in (over, PRICES):
        for prefix in sorted(table, key=len, reverse=True):
            if (model or "").startswith(prefix) or prefix in (model or ""):
                v = table[prefix]
                return (float(v[0]), float(v[1]))
    return None


def cost(model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    p = price_for(model)
    return None if p is None else round(prompt_tokens / 1e6 * p[0] + completion_tokens / 1e6 * p[1], 6)


def record(provider: str, model: str, usage: Optional[Dict[str, Any]], prompt_chars: int = 0, completion_chars: int = 0) -> None:
    """Records a cloud call. Uses the provider's token counts, or estimates (~4 characters a token)."""
    if provider in LOCAL_PROVIDERS:
        return
    estimated = not usage
    pt = int((usage or {}).get("prompt_tokens") or (usage or {}).get("input_tokens") or prompt_chars / 4)
    ct = int((usage or {}).get("completion_tokens") or (usage or {}).get("output_tokens") or completion_chars / 4)
    c_usd = cost(model, pt, ct)
    try:
        with _conn() as c:
            c.execute("INSERT INTO llm_usage VALUES (?,?,?,?,?,?,?)", (time.time(), provider, model, pt, ct, c_usd, int(estimated)))
    except Exception as e:
        logger.warning(f"usage not recorded: {e}")
    r = current()
    if r:
        r.cloud_calls += 1
        r.cost_usd += c_usd or 0.0


def month_spend() -> float:
    import datetime as dt
    start = dt.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    with _conn() as c:
        v = c.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE ts >= ?", (start,)).fetchone()[0]
    return round(float(v or 0), 4)


def summary() -> Dict[str, Any]:
    import datetime as dt
    start = dt.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    with _conn() as c:
        rows = c.execute("SELECT provider, model, COUNT(*), SUM(prompt_tokens), SUM(completion_tokens), COALESCE(SUM(cost_usd),0) "
                         "FROM llm_usage WHERE ts >= ? GROUP BY provider, model ORDER BY 6 DESC", (start,)).fetchall()
    s = settings()
    ep = cloud_endpoint()
    return {"settings": s, "month_spend_usd": month_spend(), "cloud_model": ep["label"] if ep else None,
            "by_model": [{"provider": r[0], "model": r[1], "calls": r[2], "prompt_tokens": r[3] or 0,
                          "completion_tokens": r[4] or 0, "cost_usd": round(r[5], 4)} for r in rows]}


def estimate_turn_cost(messages: List[Dict[str, Any]], endpoint: Dict[str, Any]) -> Optional[float]:
    """Rough cost of re-running a turn on the cloud: ~4 model calls over the conversation, ~1.5k tokens out each."""
    chars = sum(len(str(m.get("content") or "")) for m in messages[-12:])
    prompt = int(chars / 4) + 6000   # system prompt and tool definitions
    return cost(endpoint["model"], prompt * 4, 1500 * 4)
