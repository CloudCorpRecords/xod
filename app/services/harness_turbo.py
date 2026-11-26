"""Harness Turbo: prompt cache optimization, rolling tool-output compaction,
and parallel read-only tool batch execution for Xod.

Goals:
1. Maximize KV prompt-cache hit rates across Anthropic, OpenAI, Fireworks,
   Gemini, and local MLX/LM Studio by keeping system prefixes and tool schemas
   byte-identical and deterministically ordered across turns.
2. Cut token bloat on multi-step browser/file/research tasks by compacting
   stale tool results (>3 steps old) while keeping recent tool outputs full.
3. Speed up multi-lookup turns by executing parallel-safe read-only tool calls
   concurrently via asyncio.gather.
"""
import asyncio
import copy
import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

_PARALLEL_SAFE_EXACT = {
    "web_search", "tavily_search", "you_search", "memory_search",
    "desktop_chrome_tabs", "desktop_chrome_read_tab",
    "fleet_chat_list", "fleet_chat_read", "fleet_blackboard_read",
    "mcp_discover_tools",
    "computer_read_file", "computer_list_dir", "computer_find_files",
    "fs_read_workspace_file", "fs_list_workspace",
}

LOCAL_PROVIDERS = {"xod", "lm_studio", "ollama", "local"}
LOCAL_MCP_TOOL_CAP = 6


def is_local_provider(info_or_name: Any) -> bool:
    """True when the active LLM provider is an on-device local runtime (MLX/LM Studio/Ollama)."""
    if isinstance(info_or_name, dict):
        provider = str(info_or_name.get("provider") or "").strip().lower()
        base_url = str(info_or_name.get("base_url") or "").strip().lower()
        if provider in LOCAL_PROVIDERS:
            return True
        if any(h in base_url for h in ("127.0.0.1", "localhost", "0.0.0.0")):
            return True
        return False
    return str(info_or_name or "").strip().lower() in LOCAL_PROVIDERS

_SAFE_READONLY_CMD_RE = re.compile(
    r"^\s*(?:git\s+(?:status|diff|log|branch|show|rev-parse)\b|"
    r"ls\b|pwd\b|whoami\b|uname\b|which\b|wc\b|head\b|tail\b|"
    r"node\s+--check\b|python3?\s+-m\s+(?:pytest|unittest)\b|pytest\b|"
    r"npm\s+test\b|cargo\s+test\b|go\s+test\b)"
    r"(?![^;&|`$]*(?:[;&|`$]|\brm\b|\bsudo\b|\bcurl\b|\bwget\b|>))",
    re.I,
)


def is_safe_readonly_command(cmd: str) -> bool:
    """True for strictly read-only inspection or local test commands with no chaining/redirection."""
    s = str(cmd or "").strip()
    if not s or "\n" in s or len(s) > 240:
        return False
    return bool(_SAFE_READONLY_CMD_RE.match(s))


def is_parallel_safe(tool_name: str) -> bool:
    """True if a tool call is side-effect-free and safe to run concurrently in a batch."""
    n = str(tool_name or "").strip()
    if n in _PARALLEL_SAFE_EXACT:
        return True
    if n.startswith("mcp__"):
        try:
            from app.services.mcp_client import mcp_hub
            return bool(mcp_hub.read_only(n))
        except Exception:
            return False
    return False


async def run_tool_batch(
    calls: List[Tuple[str, Dict[str, Any]]],
    executor: Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Run a batch of tool calls concurrently when all are read-only, else sequentially."""
    if len(calls) > 1 and all(is_parallel_safe(name) for name, _ in calls):
        return list(await asyncio.gather(*(executor(name, args) for name, args in calls)))
    results = []
    for name, args in calls:
        results.append(await executor(name, args))
    return results


def _compact_tool_content(raw: str, max_chars: int = 480) -> str:
    """Compact a large historical tool result while preserving key status/metadata."""
    s = str(raw or "")
    if len(s) <= max_chars:
        return s
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            kept = {}
            for k in ("success", "ok", "status", "error", "title", "url", "path", "count", "summary", "session_id", "task_id"):
                if k in obj:
                    v = obj[k]
                    kept[k] = v[:180] if isinstance(v, str) else v
            if kept:
                kept["_compacted"] = f"trimmed {len(s)} chars from earlier turn"
                out = json.dumps(kept, ensure_ascii=False)
                if len(out) <= max_chars:
                    return out
    except Exception:
        pass
    head = s[: max_chars - 90].rstrip()
    return f"{head}\n… [compacted {len(s)} chars from earlier turn]"


def compact_stale_tool_messages(
    messages: List[Dict[str, Any]],
    keep_recent_tools: int = 3,
    stale_threshold: int = 900,
    max_stale_chars: int = 480,
) -> Tuple[List[Dict[str, Any]], int]:
    """Compact oversized role=='tool' messages older than the newest `keep_recent_tools`.
    Returns (new_messages, chars_saved). Never mutates caller's list."""
    tool_indices = [i for i, m in enumerate(messages or []) if isinstance(m, dict) and m.get("role") == "tool"]
    if len(tool_indices) <= keep_recent_tools:
        return list(messages or []), 0
    stale_set = set(tool_indices[:-keep_recent_tools])
    out = []
    saved = 0
    for i, m in enumerate(messages):
        if i in stale_set and isinstance(m.get("content"), str) and len(m["content"]) > stale_threshold:
            compacted = _compact_tool_content(m["content"], max_chars=max_stale_chars)
            saved += max(0, len(m["content"]) - len(compacted))
            m2 = dict(m)
            m2["content"] = compacted
            out.append(m2)
        else:
            out.append(m)
    return out, saved


def sort_tools_deterministically(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Sort tool schemas alphabetically by function name so KV prompt prefixes stay stable."""
    if not tools or len(tools) <= 1:
        return tools
    return sorted(tools, key=lambda t: str((t.get("function") or {}).get("name") or ""))


def optimize_payload(payload: Dict[str, Any], info: Dict[str, Any]) -> Dict[str, Any]:
    """Apply prompt-cache and context optimizations in-place on an LLM request payload.

    When running a local model (Apple Silicon MLX, LM Studio, Ollama), automatically
    engages the tighter Local Model Harness Turbo compaction profile so 4B–12B models
    stay inside their fast 4K–8K KV-cache sweet spot.
    """
    if not isinstance(payload, dict):
        return payload

    local_mode = is_local_provider(info)

    # 1. Compact stale historical tool outputs (tighter profile for local models)
    msgs = payload.get("messages")
    if isinstance(msgs, list) and msgs:
        if local_mode:
            compacted, _ = compact_stale_tool_messages(
                msgs,
                keep_recent_tools=2,
                stale_threshold=500,
                max_stale_chars=240,
            )
        else:
            compacted, _ = compact_stale_tool_messages(msgs)
        payload["messages"] = compacted

    # 2. Deterministic tool ordering for stable prefix caching
    if payload.get("tools"):
        payload["tools"] = sort_tools_deterministically(payload["tools"])

    # 3. Anthropic ephemeral cache breakpoints on static system prompt + last tool definition
    provider = str((info or {}).get("provider") or "").lower()
    base_url = str((info or {}).get("base_url") or "").lower()
    if provider == "anthropic" or "anthropic.com" in base_url:
        pmsgs = payload.get("messages") or []
        if pmsgs and isinstance(pmsgs[0], dict) and pmsgs[0].get("role") == "system" and isinstance(pmsgs[0].get("content"), str):
            pmsgs = list(pmsgs)
            pmsgs[0] = {
                "role": "system",
                "content": [{"type": "text", "text": pmsgs[0]["content"], "cache_control": {"type": "ephemeral"}}],
            }
            payload["messages"] = pmsgs
        if payload.get("tools"):
            tools_copy = copy.deepcopy(payload["tools"])
            tools_copy[-1]["cache_control"] = {"type": "ephemeral"}
            payload["tools"] = tools_copy

    return payload


def extract_cached_tokens(usage: Optional[Dict[str, Any]]) -> int:
    """Extract provider-reported cached prompt tokens across OpenAI, Fireworks, Gemini, Anthropic."""
    if not isinstance(usage, dict):
        return 0
    if usage.get("cache_read_input_tokens"):
        return int(usage["cache_read_input_tokens"] or 0)
    if usage.get("cached_tokens"):
        return int(usage["cached_tokens"] or 0)
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
    if isinstance(details, dict) and details.get("cached_tokens"):
        return int(details["cached_tokens"] or 0)
    return 0
