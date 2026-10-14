"""
Warm the chat model while the user types. On a 16 GB Mac a local model's weights get pushed out of
memory when it sits idle, and the first request then waits ~10-20 s before the first word. A tiny
request with the router's prompt (max one token) pages the model back in and leaves that prompt in
the server's cache, so pressing Enter starts work right away.

Only for models on this Mac (LM Studio, Ollama, the built-in engine), at most once a minute, and
never while a chat is already using the model. Sooner when a long prompt (an answer written from web pages,
a browser task step) has run since: the server's prompt cache is small (8k tokens on a 16 GB Mac), so those
push the router's prompt out, and re-reading it costs ~4-6 s on the next question. Also starts Xod's browser,
so the first search doesn't wait for it, and runs once more after each answer, while the model is idle.
"""
import asyncio
import logging
import time
from typing import Any, Dict

logger = logging.getLogger("xod.warmup")

EVERY = 60.0
BIG_PROMPT_CHARS = 2500       # a prompt this long can push the router's prompt out of the server's cache
_last = 0.0
_dirty = False
_task: "asyncio.Task | None" = None


def note_prompt(messages: Any, task: "str | None" = None) -> None:
    """Called by llm_service for every request: a long one means the router's prompt may no longer be cached."""
    global _dirty
    if task == "router":
        return
    try:
        size = sum(len(m.get("content") if isinstance(m.get("content"), str) else str(m.get("content"))) for m in messages)
    except Exception:
        return
    if size > BIG_PROMPT_CHARS:
        _dirty = True


async def _warm(spoken: bool = False, delay: float = 0.0) -> None:
    from app.agent.researcher import AgenticResearcher
    from app.services.llm import llm_service
    from app.services import web_reader
    global _dirty
    if delay:
        await asyncio.sleep(delay)
    asyncio.ensure_future(web_reader.warm())
    _dirty = False
    try:
        await llm_service.prefill(AgenticResearcher.router_messages("hi", [], spoken=spoken))
        # The follow-up rewrite's instructions too (services/turn_quality.py): short, so both stay cached.
        from app.services import turn_quality
        await llm_service.prefill([{"role": "system", "content": turn_quality.UNDERSTAND_PROMPT}, {"role": "user", "content": "Conversation:"}])
    except Exception as e:
        logger.debug(f"warm-up skipped: {e}")


def warm(spoken: bool = False, after_answer: bool = False) -> Dict[str, Any]:
    """spoken: a conversation is starting, so warm the out-loud version of the router prompt. after_answer: an
    answer just finished; get ready for the next question while the person reads."""
    global _last, _task
    from app.services.llm import llm_service
    info = llm_service.get_endpoint_info()
    if info.get("provider") not in ("lm_studio", "ollama", "xod", "local"):
        return {"warmed": False, "reason": "not a local model"}
    now = time.time()
    if _task and not _task.done():
        return {"warmed": False, "reason": "warming"}
    if now - _last < EVERY and not spoken and not _dirty:
        return {"warmed": False, "reason": "recently warmed"}
    _last = now
    _task = asyncio.create_task(_warm(spoken, delay=1.5 if after_answer else 0.0))
    return {"warmed": True}
