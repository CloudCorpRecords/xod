"""Bounded tool context, shared by the parent and its fleet workers.

This is lossy representation, not semantic memory or execution evidence. The
original result still goes to the UI/effect guards. Never slice serialized JSON.
"""
import json


STATE_KEYS = (
    "success", "error", "outcome", "outcome_evidence", "needs_approval",
    "approval_id", "approval", "job_id", "status", "path", "filepath",
    "file_path", "id", "tool", "verification", "exit_code", "message",
)
MARKER = "\n[Tool output omitted; reread the source for missing details.]\n"


def _excerpt(text, limit):
    if len(text) <= limit:
        return text
    if limit <= len(MARKER):
        return "[omitted]"
    keep = (limit - len(MARKER)) // 2
    return text[:keep] + MARKER + text[-keep:]


def _reduce(value, text_limit, members, depth=0):
    if isinstance(value, str):
        return _excerpt(value, text_limit)
    if isinstance(value, dict):
        if depth >= 8:
            return {"_xod_omitted": "nested object; reread source"}
        keys = [k for k in STATE_KEYS if k in value]
        keys += [k for k in value if k not in STATE_KEYS and k != "_xod_context"]
        selected = keys[:members]
        result = {
            _excerpt(str(k), 160): _reduce(value[k], text_limit, members, depth + 1)
            for k in selected
        }
        if len(keys) > len(selected):
            result["_xod_omitted_fields"] = len(keys) - len(selected)
        return result
    if isinstance(value, (list, tuple)):
        if depth >= 8:
            return [{"_xod_omitted": "nested array; reread source"}]
        if len(value) <= members:
            return [_reduce(v, text_limit, members, depth + 1) for v in value]
        head = (members + 1) // 2
        tail = members // 2
        return (
            [_reduce(v, text_limit, members, depth + 1) for v in value[:head]]
            + [{"_xod_omitted_items": len(value) - members}]
            + ([_reduce(v, text_limit, members, depth + 1) for v in value[-tail:]] if tail else [])
        )
    return value


def tool_content(result, max_chars):
    """Return valid, bounded JSON, or a visibly abbreviated plain-text result.

    State fields are prioritized at every retained object, not only at the root.
    Very large state/collections can themselves be abbreviated: the mandatory
    truncation notice forbids treating an excerpt as a complete source.
    """
    if max_chars < 512:
        raise ValueError("Tool context budget must be at least 512 characters")
    raw = result if isinstance(result, str) else json.dumps(result, default=str)
    if len(raw) <= max_chars:
        return raw
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return _excerpt(raw, max_chars)
    notice = {
        "truncated": True, "original_chars": len(raw),
        "reread_required": True,
        "warning": "Excerpt only; omitted data is not evidence of absence or success.",
    }
    text_limit, members = max(128, max_chars // 4), 32
    while True:
        compact = _reduce(data, text_limit, members)
        if isinstance(compact, dict):
            compact["_xod_context"] = notice
        else:
            compact = {"value": compact, "_xod_context": notice}
        encoded = json.dumps(compact, default=str)
        if len(encoded) <= max_chars:
            return encoded
        if text_limit > 96:
            text_limit = max(96, text_limit // 2)
        elif members > 1:
            members = max(1, members // 2)
        else:
            # Even one retained nested value may exceed a very small budget.
            # Be explicit rather than emitting invalid JSON or invented success.
            limit = max_chars // 5
            fallback = {"_xod_context": notice, "excerpt": _excerpt(raw, limit)}
            while len(json.dumps(fallback)) > max_chars:
                limit //= 2
                if limit <= len(MARKER):
                    fallback.pop("excerpt")
                    break
                fallback["excerpt"] = _excerpt(raw, limit)
            return json.dumps(fallback)
