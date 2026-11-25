"""
context_hygiene: keep agent tool-call context crisp and focused across long
browser and desktop runs.

  - prune_tool_history(): strips stale base64 screenshot_uri payloads and trims
    oversized historical DOM/AX dumps from older turns while preserving recent
    screenshots, compact deltas, validation_errors, and mission checklists.
  - dedupe_tool_surface(): trims overlapping browser/desktop/phone tools per
    active mode so the model chooses cleanly with fewer wasted turns.
"""
import copy
import json
from typing import Any, Dict, List, Optional


_BROWSER_PREFIXES = ("browser_",)
_DESKTOP_PREFIXES = ("desktop_", "terminal_")


def prune_tool_history(
    messages: List[Dict[str, Any]],
    *,
    keep_recent_screenshots: int = 2,
    max_old_tool_chars: int = 1800,
) -> List[Dict[str, Any]]:
    """Return a compacted copy of chat/tool messages without mutating the input."""
    out = copy.deepcopy(messages or [])
    tool_indices = [i for i, m in enumerate(out) if m.get("role") == "tool"]
    keep_shot_set = set(tool_indices[-max(0, keep_recent_screenshots):])

    for idx in tool_indices:
        msg = out[idx]
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        is_recent = idx in keep_shot_set
        try:
            data = json.loads(content)
        except Exception:
            if not is_recent and len(content) > max_old_tool_chars:
                msg["content"] = content[:max_old_tool_chars] + "\n...[pruned older tool output]"
            continue
        if not isinstance(data, dict):
            continue
        changed = False
        if not is_recent and "screenshot_uri" in data and data["screenshot_uri"]:
            data["screenshot_uri"] = "[pruned stale screenshot]"
            changed = True
        if not is_recent:
            for big_key in ("elements", "markdown", "content", "html", "controls"):
                val = data.get(big_key)
                if isinstance(val, str) and len(val) > 600:
                    data[big_key] = val[:600] + "...[pruned]"
                    changed = True
                elif isinstance(val, list) and len(val) > 12:
                    data[big_key] = val[:12]
                    data[f"{big_key}_pruned"] = True
                    changed = True
        if changed:
            msg["content"] = json.dumps(data, ensure_ascii=False)
    return out


def dedupe_tool_surface(
    tools: List[Dict[str, Any]],
    *,
    mode: str = "general",
    allow_phone: bool = False,
) -> List[Dict[str, Any]]:
    """Filter overlapping tool definitions for the active execution mode.
    mode: 'browser' | 'desktop' | 'general'."""
    out: List[Dict[str, Any]] = []
    for t in tools or []:
        fn = (t.get("function") or {}) if isinstance(t, dict) else {}
        name = str(fn.get("name") or "")
        if not name:
            continue
        if mode == "browser":
            if name.startswith(_DESKTOP_PREFIXES):
                continue
            if name == "phone" and not allow_phone:
                continue
        elif mode == "desktop":
            if name.startswith(_BROWSER_PREFIXES):
                continue
            if name == "phone" and not allow_phone:
                continue
        out.append(t)
    return out
