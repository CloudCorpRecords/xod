"""Token-budgeted, deterministic compaction; never an LLM claim of completion."""
import json
from app.agent.context import tool_content


class ContextFull(Exception):
    pass


def estimate(messages, tools=None):
    """Conservative byte-based token estimate, including schemas and framing.

    No exact tokenizer is available for arbitrary BYOK models. UTF-8 bytes plus
    framing deliberately overestimates ordinary text; it is NOT provider usage.
    Images/audio cannot be bounded this way and are refused in long-run mode.
    """
    for message in messages:
        if not isinstance(message.get("content", ""), (str, type(None))):
            raise ContextFull("Multimodal context is not supported by this token estimator.")
    body = json.dumps({"messages": messages, "tools": tools or []}, ensure_ascii=False, default=str)
    return len(body.encode("utf-8")) + 64 * len(messages) + 512


def _protected(message):
    """Never discard an unresolved approval or unknown effect to make room."""
    if message.get("role") != "tool":
        return False
    try:
        value = json.loads(message.get("content") or "{}")
    except (ValueError, TypeError):
        return False
    def visit(obj):
        if isinstance(obj, dict):
            if obj.get("needs_approval") or str(obj.get("outcome", "")).lower() in ("unknown", "uncertain"):
                return True
            return any(visit(v) for v in obj.values())
        if isinstance(obj, list):
            return any(visit(v) for v in obj)
        return False
    return visit(value)


def groups(messages):
    """Assistant tool calls and ALL their replies form one indivisible group."""
    result = []
    for message in messages:
        if message.get("role") == "tool" and result and result[-1][0].get("tool_calls"):
            result[-1].append(message)
        else:
            result.append([message])
    for group in result:
        first = group[0]
        if first.get("tool_calls"):
            ids = [c.get("id") for c in first["tool_calls"]]
            replies = [m.get("tool_call_id") for m in group[1:]]
            if not all(ids) or len(ids) != len(set(ids)) or sorted(ids) != sorted(replies):
                raise ContextFull("Incomplete tool exchange; refusing compaction or dispatch.")
        elif first.get("role") == "tool":
            raise ContextFull("Orphan tool result; refusing compaction or dispatch.")
    return result


def compact(messages, tools, target, archive):
    """Keep every instruction and recent exchanges; archive older tool groups.

    archive receives the original transcript before anything is removed.
    Archive references are data, not instructions or evidence of success.
    """
    before = estimate(messages, tools)
    exchanges = groups([m for m in messages if m.get("name") != "xod_context_checkpoint"])
    if before <= target:
        return list(messages), None
    path = archive(messages)
    changed = []
    for index, group in enumerate(exchanges):
        if index < len(exchanges) - 3 and not any(_protected(m) for m in group):
            group = [dict(m) for m in group]
            for message in group:
                if message.get("role") == "tool":
                    message["content"] = tool_content(message.get("content") or "", 1800)
        changed.append(group)
    notice = {"role": "system", "name": "xod_context_checkpoint", "content": (
        "Xod context checkpoint: older completed tool exchanges may be omitted. "
        "This is a mechanical excerpt, NOT a success verdict. Do not replay actions "
        "because their output is absent. Reread affected artifacts or the local transcript "
        f"archive when needed. Archive: {path}"
    )}
    def flattened():
        return [notice] + [m for group in changed for m in group]
    # Remove whole OLD completed exchanges, never user/system instructions or
    # approvals/unknown outcomes. Recent exchanges are preserved unchanged.
    for index, group in enumerate(changed):
        if estimate(flattened(), tools) <= target:
            break
        if index < len(changed) - 3 and group[0].get("tool_calls") and not any(_protected(m) for m in group):
            changed[index] = []
    result = flattened()
    after = estimate(result, tools)
    if after > target:
        # A single large recent read must not permanently block a run merely
        # because it is recent. Its full text is already archived; keep paired,
        # structured excerpts, but never shorten unresolved-effect exchanges.
        for group in changed:
            if any(_protected(m) for m in group):
                continue
            for index, message in enumerate(group):
                if message.get("role") == "tool":
                    group[index] = {**message, "content": tool_content(message.get("content") or "", 1800)}
        result = flattened()
        after = estimate(result, tools)
    if after > target:
        raise ContextFull("Pinned instructions, recent exchanges or unresolved effects exceed the context budget.")
    return result, {"before_estimated_tokens": before, "after_estimated_tokens": after,
                    "estimator": "utf8_bytes_plus_framing_upper_estimate", "archive": path}
