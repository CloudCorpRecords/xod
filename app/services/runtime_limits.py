"""Small process-local admission gates; not a second task scheduler.

The desktop backend has one event loop. Gates belong to that loop so test runs
and server restarts never inherit a semaphore bound to a dead loop.
"""
import asyncio
import contextvars
from contextlib import asynccontextmanager

resource_holder = contextvars.ContextVar("xod_resource_holder", default=None)
claimed_holders = set()


def worker_limit():
    from app.services.team import load
    return max(1, min(6, load().get("max_parallel", 3)))


def _gate(name):
    loop = asyncio.get_running_loop()
    if not hasattr(loop, "_xod_admission"):
        loop._xod_admission = {}
    if name not in loop._xod_admission:
        loop._xod_admission[name] = {"condition": asyncio.Condition(), "active": 0}
    return loop._xod_admission[name]


@asynccontextmanager
async def slot(name, limit):
    gate = _gate(name)
    condition = gate["condition"]
    async with condition:
        await condition.wait_for(lambda: gate["active"] < limit)
        gate["active"] += 1
    try:
        yield
    finally:
        async with condition:
            gate["active"] -= 1
            condition.notify_all()


@asynccontextmanager
async def inference(info):
    # Shared across chat, planning, read-only workers and delegated workers.
    # Remote model servers retain parallelism; local engines share one machine.
    from urllib.parse import urlparse
    host = urlparse(info.get("base_url", "")).hostname
    local = host in ("localhost", "127.0.0.1", "::1") or (
        not host and info.get("provider") in ("xod", "ollama", "lm_studio", "local", "specialist"))
    async with slot("local-inference" if local else "remote-inference", 1 if local else worker_limit()):
        yield
