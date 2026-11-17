"""Opt-in cloud run budgets, checkpoint lifecycle and request admission.

One context variable is inherited by fleet workers: retries and done-checks
consume the SAME request/input allowance. Provider credentials are not copied
from endpoint configuration. Private task content may itself be sensitive.
"""
import asyncio
import contextvars
from contextlib import aclosing
import functools
import json
import time
import uuid
from urllib.parse import urlparse
from app.agent.long_context import ContextFull, compact, estimate
from app.services import run_checkpoint as store, run_observability as obs

active = contextvars.ContextVar("xod_long_execution", default=None)
DEFAULTS = {
    "enabled": False, "provider": "", "model": "", "context_tokens": 0,
    "output_tokens": 4096, "safety_tokens": 4096, "max_steps": 120,
    "max_requests": 160, "max_seconds": 3600, "max_input_tokens": 2_000_000,
}
RANGES = {"context_tokens": (8192, 2_000_000), "output_tokens": (512, 16384),
          "safety_tokens": (1024, 32768), "max_steps": (1, 500),
          "max_requests": (1, 1000), "max_seconds": (10, 14400),
          "max_input_tokens": (8192, 50_000_000)}
REMOTE = ("openai", "anthropic", "gemini", "groq", "fireworks", "openrouter", "custom")


class Pause(Exception):
    pass


def load():
    from app.services.storage import get_setting
    try:
        value = json.loads(get_setting("long_execution", "") or "{}")
        return {**DEFAULTS, **{k: v for k, v in value.items() if k in DEFAULTS}}
    except Exception:
        return dict(DEFAULTS)


def save(changes):
    from app.services.storage import set_setting
    if not isinstance(changes, dict) or any(k not in DEFAULTS for k in changes):
        raise ValueError("Unknown long-execution setting")
    cfg = {**load(), **changes}
    if type(cfg["enabled"]) is not bool:
        raise ValueError("enabled must be a boolean")
    for key, (low, high) in RANGES.items():
        value = cfg[key]
        if key == "context_tokens" and value == 0 and not cfg["enabled"]:
            continue
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key} must be an integer from {low} to {high}")
    if not isinstance(cfg["model"], str) or len(cfg["model"]) > 200:
        raise ValueError("Invalid model identifier")
    if cfg["provider"] not in ("", *REMOTE):
        raise ValueError("Long execution supports cloud providers only")
    if cfg["enabled"] and (not cfg["model"].strip() or not cfg["provider"]):
        raise ValueError("Select the exact cloud provider/model and supply its context limit")
    if cfg["context_tokens"] and cfg["context_tokens"] <= cfg["output_tokens"] + cfg["safety_tokens"] + 2048:
        raise ValueError("Context must leave room for input, output and the safety reserve")
    set_setting("long_execution", json.dumps(cfg))
    return cfg


def endpoint():
    from app.services import llm_routing
    from app.services.llm import llm_service
    route = llm_routing.current()
    return (route.endpoint if route and route.endpoint else llm_service.get_endpoint_info())


def matches(cfg, info):
    host = urlparse(info.get("base_url") or "").hostname or ""
    return (info.get("provider") in REMOTE and
            host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0") and
            (cfg["provider"], cfg["model"]) == (info.get("provider"), info.get("model")))


class Run:
    def __init__(self, cfg, owner, decision, saved=None):
        self.cfg = cfg
        self.start = time.monotonic()
        self.elapsed = saved.get("elapsed_seconds", 0) if saved else 0
        if saved and saved.get("status") == "running":
            # A hard crash did not close its last active interval. Charge the
            # unknown gap conservatively; a restart must not reset time budgets.
            self.elapsed += min(cfg["max_seconds"], max(0, time.time() - saved["updated_at"]))
        self.halt_reason = None
        self.state = saved or {
            "version": 1, "id": uuid.uuid4().hex, "owner": owner,
            "goal": decision["goal"], "kind": decision.get("kind", "computer"),
            "provider": cfg["provider"], "model": cfg["model"],
            "steps": 0, "requests": 0, "estimated_input_tokens": 0,
            "compactions": 0, "pending": {}, "status": "running",
        }
        self.state["status"] = "running"
        self.resumed = bool(saved)
        self.state["limits"] = dict(cfg)
        if saved and decision.get("extra"):
            self.state["goal"] += "\n\nLatest user continuation instructions: " + decision["extra"]

    def persist(self):
        self.state.update(updated_at=time.time(),
                          elapsed_seconds=self.elapsed + time.monotonic() - self.start)
        try:
            store.write(self.state["id"], "checkpoint.json", self.state)
        except OSError:
            self.halt_reason = "Checkpoint storage unavailable; further actions are refused"
            raise

    def check(self):
        if self.state["status"] in ("completed", "paused", "interrupted", "ended_unverified"):
            raise Pause("This run is no longer active; late child dispatch is refused")
        if self.halt_reason:
            raise Pause(self.halt_reason)
        if self.state["status"] == "waiting_for_approval":
            raise Pause("An existing approval must be resolved; further dispatch is paused")
        if any(p["state"] == "uncertain" for p in self.state["pending"].values()):
            raise Pause("An action has an uncertain outcome; automatic continuation is refused")
        if self.elapsed + time.monotonic() - self.start >= self.cfg["max_seconds"]:
            raise Pause("Run time allowance exhausted")
        if self.state["requests"] >= self.cfg["max_requests"]:
            raise Pause("Shared HTTP request allowance exhausted")

    def archive(self, messages):
        number = self.state["compactions"] + 1
        path = store.write(self.state["id"], f"context-{number}-{uuid.uuid4().hex[:8]}.json", messages)
        self.state["compactions"] = number
        return path

    def fit(self, messages, tools=None, output=None):
        output = output or self.cfg["output_tokens"]
        ceiling = self.cfg["context_tokens"] - output - self.cfg["safety_tokens"]
        current = estimate(messages, tools)
        if current <= int(ceiling * .85):
            return messages
        # Compact BEFORE the hard ceiling, leaving room for another result.
        result, event = compact(messages, tools, int(ceiling * .70), self.archive)
        if event:
            obs.emit("context.token_compacted",
                     **{k: v for k, v in event.items() if k != "archive"},
                     context_tokens=self.cfg["context_tokens"],
                     limit_source="user_configured", checkpoint_id=self.state["id"])
        return result

    def sync(self, conversation, log):
        # A completed parent transcript commits returned local/child effects.
        # Started/uncertain intents are never cleared merely by resuming.
        from app.agent.long_context import groups
        groups(conversation)
        self.state["pending"] = {k: v for k, v in self.state["pending"].items()
                                 if v["state"] != "returned"}
        self.state.update(conversation=conversation, log=log)
        self.persist()

    def tool_started(self, name, args, cid):
        self.check()
        key = f"{obs.ids().get('worker_id', 'main')}:{cid}"
        self.state["pending"][key] = {"state": "started", "tool": name, "id": cid, "args": args}
        self.persist()  # If this fails, the effect MUST NOT execute.
        return key

    def tool_returned(self, key, result):
        uncertain = isinstance(result, dict) and str(result.get("outcome", "")).lower() in ("unknown", "uncertain")
        self.state["pending"][key]["state"] = "uncertain" if uncertain else "returned"
        if isinstance(result, dict) and result.get("needs_approval"):
            self.state["status"] = "waiting_for_approval"
        try:
            self.persist()
        except OSError:
            self.state["pending"][key]["state"] = "uncertain"
            raise

    def admit(self, info, payload):
        self.check()
        from app.agent.long_context import groups
        groups(payload["messages"])
        if not matches(self.cfg, info):
            raise Pause("The dispatched model differs from this run's configured cloud profile")
        output = max(payload.get("max_tokens", 0), payload.get("max_completion_tokens", 0))
        if output > self.cfg["output_tokens"]:
            raise Pause("Provider retry requested more output than the configured reserve")
        payload["messages"] = self.fit(payload["messages"], payload.get("tools"), output)
        count = estimate(payload["messages"], payload.get("tools"))
        if count + output + self.cfg["safety_tokens"] > self.cfg["context_tokens"]:
            raise Pause("Full request exceeds the configured context limit")
        if self.state["estimated_input_tokens"] + count > self.cfg["max_input_tokens"]:
            raise Pause("Shared estimated input-token allowance exhausted")
        self.state["requests"] += 1  # Atomic on the event loop, before HTTP.
        self.state["estimated_input_tokens"] += count
        self.persist()
        obs.emit("run.budget", checkpoint_id=self.state["id"], requests=self.state["requests"],
                 estimated_input_tokens=self.state["estimated_input_tokens"],
                 context_tokens=self.cfg["context_tokens"], limit_source="user_configured",
                 estimator="utf8_bytes_plus_framing_upper_estimate")


def managed(fn):
    @functools.wraps(fn)
    async def stream(query, messages, decision, execute_tool, spoken=False):
        cfg = load()
        if active.get() is not None or (not cfg["enabled"] and not decision.get("checkpoint_id")):
            async with aclosing(fn(query, messages, decision, execute_tool, spoken)) as events:
                async for event in events:
                    yield event
            return
        if not decision.get("checkpoint_id") and not matches(cfg, endpoint()):
            yield {"type": "status", "content": "Long execution is not configured for this model; using normal limits."}
            async with aclosing(fn(query, messages, decision, execute_tool, spoken)) as events:
                async for event in events:
                    yield event
            return
        from app.services import activity
        owner = activity.origin.get()
        run = None
        try:
            if not cfg["enabled"] or not matches(cfg, endpoint()):
                raise Pause("Configure and enable long execution for this exact cloud model in Agent > Team")
            saved = None
            if decision.get("checkpoint_id"):
                saved = store.load(decision["checkpoint_id"])
                if saved["owner"] != owner:
                    raise Pause("Checkpoint belongs to a different conversation or task")
                if (saved["provider"], saved["model"]) != (cfg["provider"], cfg["model"]):
                    raise Pause("Checkpoint model does not match the configured profile")
                if saved.get("pending") or saved.get("status") == "waiting_for_approval":
                    raise Pause("Checkpoint contains uncommitted/uncertain effects or an approval hold. Review the original actions; automatic replay is refused.")
                if saved.get("status") == "completed":
                    raise Pause("This checkpoint already completed; start a new request instead")
            run = Run(cfg, owner, decision, saved)
            with store.lease(run.state["id"]):
                token = active.set(run)
                try:
                    run.persist()
                    remaining = cfg["max_seconds"] - run.elapsed
                    async with asyncio.timeout(max(.001, remaining)):
                        async with aclosing(fn(query, messages, decision, execute_tool, spoken)) as events:
                            async for event in events:
                                yield event
                    if run.state["status"] == "running":
                        run.state["status"] = "ended_unverified"
                except (asyncio.CancelledError, GeneratorExit):
                    run.state["status"] = "interrupted"
                    raise
                except Exception:
                    run.state["status"] = "interrupted"
                    raise
                finally:
                    try:
                        run.persist()
                    finally:
                        active.reset(token)
        except (Pause, ContextFull, TimeoutError, OSError, ValueError) as exc:
            reason = str(exc) if not isinstance(exc, (OSError, TimeoutError)) else (
                "Run time allowance exhausted" if isinstance(exc, TimeoutError) else "Checkpoint storage unavailable; no further actions dispatched")
            if run:
                if run.state["status"] != "waiting_for_approval":
                    run.state["status"] = "paused"
                run.state["reason"] = reason
                try:
                    run.persist()
                except OSError:
                    pass
                obs.emit("run.paused", checkpoint_id=run.state["id"], reason=type(exc).__name__)
            marker = {"goal": run.state["goal"] if run else decision["goal"],
                      "kind": decision.get("kind", "computer"), "left": reason}
            if run:
                marker["checkpoint_id"] = run.state["id"]
            yield {"type": "content", "content": f"**Paused, not verified complete:** {reason}. "
                   "Review the checkpoint in Agent > Team. Continue only after resolving the limit or hold."
                   "\n\n<!-- xod-work:" + json.dumps(marker) + " -->"}
            yield {"type": "done"}
    return stream
