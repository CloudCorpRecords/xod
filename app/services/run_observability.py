"""Small, payload-free operational spans on Xod's existing bounded event log.

No prompts, tool arguments, response text, endpoints, or credentials are stored.
These spans complement the existing activity/usage stores; they are not verdicts.
"""
import contextvars
from contextlib import contextmanager
import functools
import inspect
import logging
import re
import time
import uuid

_ids = contextvars.ContextVar("xod_run_ids", default=None)
_model = contextvars.ContextVar("xod_model_span", default=None)
ID_KEYS = ("run_id", "batch_id", "worker_id", "model_call_id", "tool_call_id")


def ids():
    return dict(_ids.get() or {})


@contextmanager
def scope(**changes):
    token = _ids.set({**ids(), **{k: v for k, v in changes.items() if k in ID_KEYS and v}})
    try:
        yield
    finally:
        _ids.reset(token)


def emit(kind, **payload):
    try:
        from app.services import event_log, activity, agent_log
        event_log.emit(kind, actor=agent_log.agent.get(),
                       ids=event_log.ids_from_origin(activity.origin.get()),
                       payload={"origin": activity.origin.get(), **payload})
    except Exception:
        logging.getLogger("xod.run_observability").debug("Operational event could not be written")


def _label(value):
    # Provider metadata is untrusted; do not accept arbitrary text as an ID.
    text = str(value or "")
    return text if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", text) else None


def _counts(usage):
    return {k: v for k, v in (usage or {}).items()
            if k in ("prompt_tokens", "completion_tokens", "total_tokens",
                     "input_tokens", "output_tokens") and type(v) is int and v >= 0}


def request_started(info, payload, queue_ms):
    from app.services.long_execution import active
    if active.get():
        active.get().admit(info, payload)
    state = _model.get()
    if state is None:
        return
    state["attempts"] += 1
    state.update(provider=_label(info.get("provider")), requested_model=_label(payload.get("model")))
    emit("model.attempt", attempt=state["attempts"], provider=state["provider"],
         requested_model=state["requested_model"], queue_wait_ms=round(queue_ms, 2))


def response_received(response):
    state = _model.get()
    if state is None:
        return
    state.update(http_status=response.status_code, request_id=_label(response.headers.get("x-request-id")))
    emit("model.response", attempt=state["attempts"], http_status=state["http_status"],
         request_id=state["request_id"])


def output_received(data):
    state = _model.get()
    if state is None:
        return
    if data.get("model"):
        state["response_model"] = _label(data["model"])
    if data.get("usage"):
        state["usage"] = _counts(data["usage"])
    for choice in data.get("choices") or []:
        if choice.get("finish_reason"):
            state["finish_reason"] = _label(choice["finish_reason"])


def metadata():
    state = _model.get() or {}
    return {k: state.get(k) for k in ("request_id", "response_model", "usage", "finish_reason")}


def observed_model(kind):
    def decorate(fn):
        @contextmanager
        def observation():
            state = {"attempts": 0, "kind": kind, "usage": None, "request_id": None,
                     "response_model": None, "finish_reason": None}
            started = time.monotonic()
            token = _model.set(state)
            with scope(model_call_id=uuid.uuid4().hex):
                emit("model.started", call_kind=kind)
                try:
                    yield state
                except BaseException as exc:
                    state["outcome"] = "cancelled" if type(exc).__name__ in ("CancelledError", "GeneratorExit") else "error"
                    state["error_type"] = type(exc).__name__
                    raise
                finally:
                    # A returned error event is not a successful model completion.
                    outcome = state.pop("outcome", "returned" if state.get("http_status") == 200 else "unconfirmed")
                    state["call_kind"] = state.pop("kind")
                    emit("model.finished", **state, outcome=outcome,
                         duration_ms=round((time.monotonic() - started) * 1000))
                    _model.reset(token)

        if inspect.isasyncgenfunction(fn):
            @functools.wraps(fn)
            async def stream(*args, **kwargs):
                from contextlib import aclosing
                with observation() as state:
                    async with aclosing(fn(*args, **kwargs)) as events:
                        async for event in events:
                            if event.get("type") == "error":
                                state["outcome"] = "error"
                            yield event
            return stream

        @functools.wraps(fn)
        async def call(*args, **kwargs):
            with observation():
                return await fn(*args, **kwargs)
        return call
    return decorate


def observed_run(fn):
    @functools.wraps(fn)
    async def stream(*args, **kwargs):
        from contextlib import aclosing
        with scope(run_id=ids().get("run_id") or uuid.uuid4().hex):
            started = time.monotonic()
            outcome = "stream_ended"
            emit("run.started")
            try:
                async with aclosing(fn(*args, **kwargs)) as events:
                    async for event in events:
                        if event.get("type") == "approval_hold":
                            outcome = "waiting_for_approval"
                        elif event.get("type") == "error":
                            outcome = "error"
                        yield event
            except BaseException as exc:
                outcome = "cancelled" if type(exc).__name__ in ("CancelledError", "GeneratorExit") else "error"
                raise
            finally:
                emit("run.finished", outcome=outcome, duration_ms=round((time.monotonic() - started) * 1000))
    return stream


async def execute_tool(name, params, call_id, execute):
    """Correlate existing tool/activity/approval events; log no params or result."""
    with scope(tool_call_id=call_id):
        from app.services.long_execution import active
        run = active.get()
        intent = run.tool_started(name, params, call_id) if run else None
        started = time.monotonic()
        emit("tool.started", tool=_label(name))
        outcome = "unknown"
        try:
            result = await execute(name, params)
            if run:
                run.tool_returned(intent, result)
            if isinstance(result, dict):
                outcome = ("waiting_for_approval" if result.get("needs_approval") else
                           "error" if result.get("error") or result.get("success") is False else "returned")
            else:
                outcome = "returned"
            return result
        except BaseException as exc:
            outcome = "cancelled_unknown_effect" if type(exc).__name__ == "CancelledError" else "error_unknown_effect"
            raise
        finally:
            emit("tool.finished", tool=_label(name), outcome=outcome,
                 duration_ms=round((time.monotonic() - started) * 1000))
