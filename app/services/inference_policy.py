"""
Cloud-first inference policy (R11, 2026-11-09, Rene's directive).

Xod was built local-first. For the larger audience that flips: a fresh install
defaults to a CLOUD provider posture — the setup state asks the person for
their own cloud API key (BYOK) — and inference that runs on this computer or
is routed to a family device happens only when the person has explicitly
turned it on. One setting gates all of it:

    allow_local_inference   (storage key, "1"/"0")

OFF (the fresh-install default) means, full stop:
  - no code path loads a local model: not the autoconnect loop (which used to
    hunt this computer for a model every 30 s and pick one by itself — the
    2026-11-06 fresh-install finding), not Auto routing, not a chat whose
    saved provider happens to be local, not a family borrow in or out;
  - a chat that would have gone local reroutes to the configured cloud
    provider when one exists, and otherwise fails with the honest setup
    reason (LocalInferenceOff): Xod needs a cloud key, or the person's
    explicit opt-in. It never falls back to hunting local models.

ON is permission to try, not permission to blow the budget: the FIX C memory
budget gate (services/model_budget.py) still decides every load exactly as
before.

Existing installs are never flipped silently. The first time the policy is
consulted on an install that predates it, a migration decision is made once,
persisted, and logged (an event_log "inference.policy_decided" event plus the
decision record kept in settings):

  - no saved llm_provider (a fresh install, or one that never chose):
        OFF, source "fresh_default"
  - saved llm_provider is a local provider (xod / lm_studio / ollama / local):
        ON, source "migration_existing_local" — that install already chose
        local; it counts as having opted in, and its behavior does not change
  - saved llm_provider is a cloud provider:
        OFF, source "migration_existing_cloud" — it stays cloud; nothing
        local starts happening behind its back

A person picking a local model in Setup/Settings is an explicit choice and
turns the setting ON (source "user_model_choice"); flipping the setting
itself records source "user_setting". The active route and the setting's
state are readable on /api/config ("inference" block) and in autoconnect's
state, so provider/endpoint state is explicit and inspectable — the
2026-11-01 drift incident (an OpenAI provider silently pointing at an
LM Studio address) is the shape this exists to prevent.
"""
import json
import logging
import time
from typing import Any, Dict

logger = logging.getLogger("xod.inference_policy")

SETTING_KEY = "allow_local_inference"
SOURCE_KEY = "allow_local_inference_source"
DECISION_KEY = "allow_local_inference_decision"

LOCAL_PROVIDERS = ("xod", "lm_studio", "ollama", "local")
CLOUD_PROVIDERS = ("openai", "gemini", "groq", "openrouter", "fireworks", "anthropic", "custom")

OFF_MESSAGE = ("Local inference is off and no cloud API key is configured. "
               "Add your cloud API key in Settings → AI model, or turn on "
               "\"Allow local inference\" there to use models on this computer.")


class LocalInferenceOff(Exception):
    """A local route/load was refused by the cloud-first policy, stated plainly."""


_decided = False


def _get(name: str, default: str = "") -> str:
    from app.services.storage import get_setting
    return get_setting(name, default) or default


def _saved_provider() -> str:
    return _get("llm_provider", "")


def fresh_install() -> bool:
    """Nobody has ever saved a model choice on this install."""
    return not _saved_provider()


def ensure_migrated() -> Dict[str, Any]:
    """Make (once) and persist the policy decision for this install, and log it.

    Returns the decision record. Safe to call any number of times: after the
    first call the persisted setting IS the decision, and a person changing
    the setting afterwards is never overridden by re-deriving."""
    global _decided
    if _decided and _get(SETTING_KEY) in ("0", "1"):
        return decision()
    existing = _get(SETTING_KEY)
    if existing in ("0", "1"):
        _decided = True
        return decision()
    saved = _saved_provider()
    if not saved:
        allowed, source = False, "fresh_default"
    elif saved in LOCAL_PROVIDERS:
        allowed, source = True, "migration_existing_local"
    else:
        allowed, source = False, "migration_existing_cloud"
    _persist(allowed, source, saved)
    _decided = True
    return decision()


def _persist(allowed: bool, source: str, provider: str = "") -> None:
    from app.services.storage import set_setting
    rec = {"allowed": bool(allowed), "source": source,
           "provider_at_decision": provider or _saved_provider(), "at": time.time()}
    set_setting(SETTING_KEY, "1" if allowed else "0")
    set_setting(SOURCE_KEY, source)
    set_setting(DECISION_KEY, json.dumps(rec))
    logger.info(f"inference policy: allow_local_inference={'on' if allowed else 'off'} "
                f"({source}; provider at decision: {rec['provider_at_decision'] or 'none'})")
    try:
        from app.services import event_log
        event_log.emit("inference.policy_decided", actor="system", payload=rec)
    except Exception:
        pass                     # the log must never break the policy


def decision() -> Dict[str, Any]:
    """The persisted decision record ({} when it was never made)."""
    raw = _get(DECISION_KEY)
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def allowed() -> bool:
    """May Xod run or route inference on this computer / a family device?"""
    ensure_migrated()
    return _get(SETTING_KEY) == "1"


def set_allowed(on: bool, source: str = "user_setting") -> Dict[str, Any]:
    """A person (or their explicit model choice) flips the setting. Logged the
    same way as the migration decision."""
    global _decided
    _persist(bool(on), source)
    _decided = True
    return decision()


def setup_state() -> str:
    """The honest first-run posture, for status surfaces:
    "needs_cloud_key" — nothing can answer yet: no cloud key saved and local
        inference off. This is the fresh-install state, and it says so.
    "cloud" — a cloud key is saved; Xod answers online.
    "local_opt_in" — the person turned local inference on."""
    from app.services import setup_wizard
    if setup_wizard.saved_keys():
        return "cloud"
    if allowed():
        return "local_opt_in"
    return "needs_cloud_key"


def route_state() -> Dict[str, Any]:
    """The active route and the setting, for /api/config and friends.
    active_route: "cloud" (a cloud provider answers), "local" (a provider on
    this computer), "device" (a server on the person's own network / a family
    device answers), or "none" (nothing can answer yet)."""
    from app.config import settings
    ensure_migrated()
    provider = settings.llm_provider or ""
    base = (settings.llm_base_url or "").rstrip("/")
    if provider == "custom":
        route = "device"         # a server the person added (model_sources): their own machine or network
    elif provider in CLOUD_PROVIDERS:
        route = "cloud"
    elif provider in LOCAL_PROVIDERS:
        route = "local" if (not base or "localhost" in base or "127.0.0.1" in base) else "device"
    else:
        route = "none"
    if route in ("local", "device") and not allowed() and setup_state() == "needs_cloud_key":
        route = "none"           # the saved default would go local, but the policy says it may not
    return {"allow_local_inference": _get(SETTING_KEY) == "1",
            "allow_local_inference_source": _get(SOURCE_KEY),
            "active_route": route,
            "provider": provider, "model": settings.llm_model or "",
            "base_url": settings.llm_base_url or "",
            "setup_state": setup_state()}
