"""
Warm the chat model while the user types. On a 16 GB Mac a local model's weights get pushed out of
memory when it sits idle, and the first request then waits ~10-20 s before the first word. A tiny
request with the router's prompt (max one token) pages the model back in and leaves that prompt in
the server's cache, so pressing Enter starts work right away.

Only for models on this Mac (LM Studio, Ollama, the built-in engine), at most once a minute, and
never while a chat is already using the model.
"""
import asyncio
import logging
import time
from typing import Any, Dict

logger = logging.getLogger("xod.warmup")

EVERY = 60.0
_last = 0.0
_task: "asyncio.Task | None" = None


async def _warm(spoken: bool = False) -> None:
    from app.agent.researcher import AgenticResearcher
    from app.services.llm import llm_service
    try:
        await llm_service.chat_completion(
            messages=AgenticResearcher.router_messages("hi", [], spoken=spoken), max_tokens=1, temperature=0, think=False)
    except Exception as e:
        logger.debug(f"warm-up skipped: {e}")


def warm(spoken: bool = False) -> Dict[str, Any]:
    """spoken: a conversation is starting, so warm the out-loud version of the router prompt."""
    global _last, _task
    from app.services.llm import llm_service
    info = llm_service.get_endpoint_info()
    if info.get("provider") not in ("lm_studio", "ollama", "xod"):
        return {"warmed": False, "reason": "not a local model"}
    now = time.time()
    if (now - _last < EVERY and not spoken) or (_task and not _task.done()):
        return {"warmed": False, "reason": "recently warmed"}
    _last = now
    _task = asyncio.create_task(_warm(spoken))
    return {"warmed": True}
