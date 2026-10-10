"""
Turns repairs into training data, so mini models learn to avoid the mistake in the first place.

When a tool call fails and a repair fixes it, we know two things about the decision that made the
call (the planner's plan, or a follow-up step's choice):
  - it was wrong            -> label that example -1 ("action failed: ...")
  - what would have worked  -> a "this, not that" pair: the same decision with the working call
                               swapped in (source "repair_fix"), for preference training.

Only decision mistakes count. A crashed browser, an unloaded model, low memory, a rate limit or
a network hiccup aren't the planner's fault, so they're never held against it.

Pairs are only made when the fix is clearly the better answer to the same prompt:
  - same tool, corrected arguments (a typo'd path, a missing parameter), or
  - a different tool when the first could never have worked here (missing API key, site blocks
    automation, nothing found).
A repair that worked around the problem some other way (e.g. listed a folder instead of reading
the file) is labelled but not turned into a pair: it isn't what the planner should have said.
"""
import json
import logging
from typing import Any, Callable, Dict, Optional

from app.services import dataset_recorder

logger = logging.getLogger("xod.repair_learning")

DECISION_KINDS = {"not_found", "bad_args", "empty", "blocked", "missing_key", "bad_output", "unknown"}
SWITCH_OK_KINDS = {"missing_key", "blocked", "empty"}


def _rewrite_planner(response: str, failed_tool: str, failed_args: Dict[str, Any], tool: str,
                     args: Dict[str, Any], canon: Callable[[str], str]) -> Optional[str]:
    from app.services.llm import parse_llm_json
    plan = parse_llm_json(response or "")
    steps = (plan or {}).get("plan_steps") or (plan or {}).get("steps")
    if not isinstance(steps, list):
        return None
    hits = []
    for si, st in enumerate(steps):
        tools = st.get("tools") or []
        if isinstance(tools, str):
            tools = [tools]
        targs = st.get("tool_args") or [{}]
        if isinstance(targs, dict):
            targs = [targs]
        for ti, t in enumerate(tools):
            if canon(str(t)) == failed_tool:
                a = targs[ti] if ti < len(targs) else {}
                hits.append((si, ti, a == failed_args))
    exact = [h for h in hits if h[2]]
    target = exact[0] if exact else (hits[0] if len(hits) == 1 else None)
    if not target:
        return None
    si, ti, _ = target
    st = steps[si]
    tools = st.get("tools") if isinstance(st.get("tools"), list) else [st.get("tools")]
    targs = st.get("tool_args") if isinstance(st.get("tool_args"), list) else [st.get("tool_args") or {}]
    while len(targs) < len(tools):
        targs.append({})
    tools[ti], targs[ti] = tool, args
    st["tools"], st["tool_args"] = tools, targs
    return json.dumps(plan, ensure_ascii=False)


def _rewrite_react(response: str, tool: str, args: Dict[str, Any]) -> Optional[str]:
    from app.services.llm import parse_llm_json
    d = parse_llm_json(response or "")
    if not isinstance(d, dict) or d.get("action") != "tool_call":
        return None
    d.update(tool_name=tool, tool_args=args)
    return json.dumps(d, ensure_ascii=False)


def record_outcome(origin: Optional[Dict[str, Any]], failed_tool: str, failed_args: Dict[str, Any],
                   final_tool: str, final_args: Dict[str, Any], ok: bool, kind: str,
                   canon: Callable[[str], str] = lambda t: t) -> str:
    """Returns what was recorded: "pair", "labelled", or "skipped" (not the decision's fault / no source)."""
    if not origin or not origin.get("id") or kind not in DECISION_KINDS:
        return "skipped"
    reason = f"action failed: {failed_tool} ({kind})"
    dataset_recorder.label(origin["id"], -1, reason)
    if not ok:
        return "labelled"
    if final_tool != failed_tool and kind not in SWITCH_OK_KINDS:
        return "labelled"
    if origin.get("kind") == "planner":
        chosen = _rewrite_planner(origin.get("response", ""), failed_tool, failed_args, final_tool, final_args, canon)
    else:
        chosen = _rewrite_react(origin.get("response", ""), final_tool, final_args)
    if not chosen:
        return "labelled"
    try:
        from app.services import preference_data
        c = dataset_recorder._conn()
        ex = c.execute("SELECT * FROM llm_examples WHERE id = ?", (origin["id"],)).fetchone()
        c.close()
        if not ex:
            return "labelled"
        preference_data._insert(ex["task"], dict(ex), chosen, "repair_fix",
                                f"{reason} -> {final_tool} {json.dumps(final_args, default=str)[:120]}")
        return "pair"
    except Exception as e:
        logger.warning(f"Couldn't save repair pair: {e}")
        return "labelled"
