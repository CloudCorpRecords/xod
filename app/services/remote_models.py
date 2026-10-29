"""
Switching this computer's model from somewhere else: your paired phone (phone_api models.*) or another of your Xods
(xod_network's beacon, /v1/models, and the xod_machines tool's models / set_model).

What can be picked is exactly what the model switcher at the top of the page offers here (setup_wizard.quick): online
models with a saved key, Ollama and LM Studio models while they run, built-in models that are downloaded. A remote
switch names one of those by its provider and model; it can't add a key, a server address or anything new. Every
switch from elsewhere sends a notification here saying who changed it.
"""
import logging
from typing import Any, Dict

logger = logging.getLogger("xod.remote_models")


async def choices() -> Dict[str, Any]:
    """{current: {provider, model, label}, choices: [{provider, model, label, group, current}]}"""
    from app.services import setup_wizard
    q = await setup_wizard.quick()
    keep = ("group", "provider", "model", "label", "current")
    out = [{k: c.get(k) for k in keep} for c in q.get("choices") or [] if not c.get("missing")]
    cur = next((c for c in q.get("choices") or [] if c.get("current")), None)
    return {"current": {k: cur.get(k) for k in ("provider", "model", "label")} if cur else None, "choices": out,
            "lm_studio_online": q.get("lm_studio_online"), "ollama_online": q.get("ollama_online")}


async def use(provider: str, model: str, by: str) -> Dict[str, Any]:
    """Switches to one of choices(), and says so. by: who asked ("your phone", "your Xod on laptop")."""
    from app.services import setup_wizard
    c = await choices()
    hit = next((x for x in c["choices"] if x["provider"] == provider and x["model"] == model), None)
    if not hit:
        return {"success": False, "error": "That model isn't one this computer offers right now (its key, server or download is missing)."}
    if hit["current"]:
        return {"success": True, "label": hit["label"], "message": f"Already using {hit['label']}."}
    r = setup_wizard.choose(provider, model, how="you")
    if not r.get("success"):
        return r
    logger.info(f"model switched to {hit['label']} by {by}")
    try:
        from app.services.task_queue import notify
        notify("Model switched", f"Chats now use {hit['label']} (changed from {by}).")
    except Exception:
        pass
    return {"success": True, "label": hit["label"], "message": f"Chats now use {hit['label']}."}
