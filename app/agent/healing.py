"""
Self-healing for agent tool calls: check every result for real, and repair failures in a bounded way.

1. check_result(): a tool saying "success" isn't enough. Files must exist and not be empty, images
   must have content, commands must exit 0, searches must actually return something.
2. classify(): turns an error into a kind of problem (browser down, model unloaded, out of memory,
   missing API key, bad arguments, not found, blocked, ...) with hints for fixing it.
3. Repairer: for a failed call, first lets the doctor fix the machinery (restart the browser,
   reload the chat model, free memory, wait out a rate limit), then retries or asks the model for
   one corrected call. Hard limits: at most 2 repairs per call, never the same call twice, and
   tools with side effects (clicks, typing, writing files, running commands, sending) are never
   re-run or swapped in automatically; for those Xod fixes what it can and reports instead.

Gemma 12B ignores prompt-only rules, so every limit here is enforced in code.
"""
import asyncio
import json
import logging
import os
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("xod.healing")

MAX_REPAIRS_PER_CALL = 2

# Tools that change something outside Xod (or act on a live page). Never retried or chosen by a
# repair: re-running a half-done write, click, or command could do it twice or do the wrong thing.
SIDE_EFFECT_TOOLS = {
    "computer_run_command", "computer_kill_process", "computer_manage_file", "computer_write_file",
    "computer_edit_file", "computer_batch_organize", "computer_run_applescript", "computer_start_job",
    "computer_stop_job", "computer_clipboard", "computer_notify_user", "computer_open_item",
    "fs_write_workspace_file", "fs_move_file", "fs_organize_files", "fs_create_folder",
    "browser_click", "browser_type", "browser_fill_form", "browser_interact", "browser_upload_file",
    "browser_press_key", "browser_evaluate_script", "browser_manage_cookies", "browser_run_mission",
    "browser_handle_captcha", "browser_hover", "browser_restart",
    "memory_store", "update_knowledge_graph_entity", "save_project_dossier", "create_project_folder",
    "xod_self_edit",
}

# Retrying these unchanged just repeats a long wait; a repair should look at why instead.
NO_BLIND_RETRY = {"computer_wait_for", "browser_wait_for"}

SEARCH_TOOLS = {"web_search", "tavily_search", "you_search", "you_news_search", "omni_deep_search",
                "memory_search", "fs_search_workspace", "computer_find_files", "search_all_projects"}
READ_TOOLS = {"web_scrape", "browser_extract_content", "browser_extract_markdown", "browser_extract_data",
              "fs_read_workspace_file", "computer_read_file"}
FILE_READ_TOOLS = {"fs_read_workspace_file", "computer_read_file"}
FILE_MAKING_TOOLS = {"media_generate_image", "media_generate_video", "media_image_create", "media_image_edit",
                     "media_video_splice", "media_video_extract_frame", "media_video_from_frame",
                     "media_video_trim", "media_video_gif", "media_video_overlay", "media_download_youtube",
                     "browser_export_pdf"}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")


# ---------------------------------------------------------------------- 1. checking results
def _error_text(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    for k in ("error", "message", "stderr", "detail"):
        v = result.get(k)
        if v and isinstance(v, str) and (k != "message" or result.get("success") is False):
            return v
    return ""


def _output_paths(result: Dict[str, Any]) -> List[str]:
    paths = []
    for k in ("output_path", "file_path", "saved_path", "pdf_path"):
        v = result.get(k)
        if isinstance(v, str) and (v.startswith("/") or os.path.isabs(v)):     # C:\... on Windows too
            paths.append(v)
    return paths


def check_result(tool: str, args: Dict[str, Any], result: Any) -> Dict[str, Any]:
    """{"ok": bool, "problem": None | "error" | "empty" | "bad_output", "detail": str}"""
    if not isinstance(result, dict):
        return {"ok": False, "problem": "error", "detail": "the tool returned nothing usable"}

    # Commands: the exit code is the truth.
    if tool == "computer_run_command":
        if result.get("status") == "timeout":
            return {"ok": False, "problem": "error", "detail": f"command timed out: {result.get('stderr', '')[-300:]}"}
        if result.get("exit_code") not in (0, None):
            return {"ok": False, "problem": "error",
                    "detail": f"exit code {result.get('exit_code')}: {(result.get('stderr') or result.get('stdout') or '')[-400:]}"}
        return {"ok": True, "problem": None, "detail": ""}

    # Held for your OK is not a failure: "fixing" it would mean finding another way to do what you haven't approved.
    if result.get("needs_approval"):
        return {"ok": True, "problem": None, "detail": ""}

    err = _error_text(result)
    if result.get("success") is False or (result.get("error") and not result.get("success")):
        return {"ok": False, "problem": "error", "detail": err or "the tool reported a failure"}

    # Made a file? It has to exist, be non-empty, and (for images) not be blank.
    if tool in FILE_MAKING_TOOLS:
        for p in _output_paths(result):
            if not os.path.exists(p):
                return {"ok": False, "problem": "bad_output", "detail": f"said it saved {p}, but the file isn't there"}
            if os.path.getsize(p) < 200:
                return {"ok": False, "problem": "bad_output", "detail": f"{os.path.basename(p)} is empty"}
            if p.lower().endswith(IMAGE_EXT):
                try:
                    import numpy as np
                    from PIL import Image
                    with Image.open(p) as im:
                        arr = np.asarray(im.convert("L").resize((128, 128)), dtype=float)
                    if arr.std() < 2.0:
                        return {"ok": False, "problem": "bad_output", "detail": f"{os.path.basename(p)} is a blank image"}
                except Exception as e:
                    return {"ok": False, "problem": "bad_output", "detail": f"{os.path.basename(p)} can't be opened: {e}"}

    # Searches and reads: an empty answer is a soft failure worth one more try.
    if tool in SEARCH_TOOLS:
        lists = [v for k, v in result.items() if isinstance(v, list) and k in ("results", "items", "organic", "data", "matches", "files", "memories", "news")]
        if lists and not any(lists) and not result.get("answer"):
            return {"ok": False, "problem": "empty", "detail": "the search returned no results"}
    if tool in READ_TOOLS and tool not in FILE_READ_TOOLS:
        # A web page with almost no text usually didn't load. A short file is just a short file ("buy milk"), and an
        # empty one is an answer too: treating those as failures sent the repair after a file that was read fine.
        text = next((result.get(k) for k in ("content", "markdown", "text", "data") if isinstance(result.get(k), str)), None)
        if text is not None and len(text.strip()) < 40:
            return {"ok": False, "problem": "empty", "detail": "the page had almost no readable content"}
    return {"ok": True, "problem": None, "detail": ""}


# ---------------------------------------------------------------------- 2. classifying errors
# (kind, pattern, hint). First match wins, so specific kinds come before generic ones.
_KINDS: List[Tuple[str, str, str]] = [
    ("blocked", r"captcha|bot check|are you a robot|access denied|forbidden|\b403\b|cloudflare",
     "The site is blocking automated access. Don't try to get around it: use a different source, or ask the user to take over."),
    ("missing_key", r"api[ _-]?key|requires_api_key|unauthori[sz]ed|\b401\b|not configured|no token",
     "This service needs an API key that isn't set. Use a tool that doesn't need it (e.g. web_search), or tell the user which key to add in Settings."),
    ("rate_limit", r"\b429\b|rate.?limit|too many requests|quota",
     "The service is rate-limiting. Wait and retry once, or use another service."),
    ("memory", r"out of memory|not enough (free )?memory|memory (ran|is) (out|low)|\boom\b|mps backend",
     "This Mac is short on memory. Free memory first, or use a smaller model/size."),
    ("model_unavailable", r"no models? (is )?loaded|model.*not loaded|lm studio|localhost:1234|failed to load model",
     "The chat model isn't loaded. Reload it and retry."),
    ("browser_down", r"devtools|\bcdp\b|websocket|target closed|browser (is )?(not running|closed|crashed)|9228|session closed|no page",
     "The browser stopped responding. Restart it and retry."),
    ("timeout", r"timed? ?out|timeout|deadline exceeded",
     "It took too long. Retry once, maybe with a smaller request."),
    ("network", r"connection (refused|reset|error)|connecterror|getaddrinfo|name or service not known|unreachable|ssl|network",
     "Network problem. Retry once after a moment; check the address."),
    ("not_found", r"\b404\b|not found|no such file|does not exist|enoent|isn't installed|unknown (model|tool)",
     "Something referenced doesn't exist. Correct the path/URL/name (check spelling), or search for the right one."),
    ("bad_args", r"missing|required|invalid|keyerror|typeerror|valueerror|expected|must be|unexpected keyword",
     "The tool was called with wrong or missing arguments. Call it again with corrected arguments matching its schema."),
]
TRANSIENT = {"browser_down", "model_unavailable", "memory", "rate_limit", "timeout", "network"}


def classify(detail: str, problem: Optional[str] = None) -> Dict[str, str]:
    if problem == "empty":
        return {"kind": "empty", "hint": "Nothing came back. Try a broader or differently-worded query, or another source."}
    if problem == "bad_output":
        return {"kind": "bad_output", "hint": "The tool said it worked but its output is broken. Try again with different settings (seed, size, model)."}
    low = (detail or "").lower()
    for kind, pattern, hint in _KINDS:
        if re.search(pattern, low):
            return {"kind": kind, "hint": hint}
    return {"kind": "unknown", "hint": "Read the error, then either correct the call or try a different tool."}


def fingerprint(tool: str, args: Dict[str, Any]) -> str:
    try:
        return tool + ":" + json.dumps(args or {}, sort_keys=True, default=str)
    except Exception:
        return tool + ":" + str(args)


# ---------------------------------------------------------------------- 3. repairing
REPAIR_PROMPT = """You are Xod's repair step. A tool call failed. Decide ONE next move.

Return JSON only:
{"action": "retry", "tool_name": "<same tool>", "tool_args": {...corrected args...}, "reason": "<one sentence>"}
{"action": "switch", "tool_name": "<another tool from the list>", "tool_args": {...}, "reason": "<one sentence>"}
{"action": "give_up", "reason": "<one sentence the user will read: what failed and what they can do>"}

Rules:
- Change something. Never repeat the exact same call.
- Only use tools from AVAILABLE TOOLS, with arguments that match their parameters.
- Never try to get around a CAPTCHA, login wall, or block; give_up or use another source.
- If the problem needs the user (a missing API key, a login, a file only they have), give_up and say exactly what they need to do."""


class Repairer:
    """
    Runs one failed call through check -> doctor fix -> retry/repair, up to MAX_REPAIRS_PER_CALL.
    `run(tool, args)` executes a tool and returns its result dict (the caller's executor).
    Yields UI events; the final event is {"type": "_repair_done", ...}.
    """

    def __init__(self, run: Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]],
                 user_query: str, tool_defs: List[Dict[str, Any]], candidate_tools: List[Dict[str, Any]]):
        self.run = run
        self.user_query = user_query
        self.defs = {t["function"]["name"]: t["function"] for t in tool_defs}
        self.candidates = [t["function"]["name"] for t in candidate_tools]

    def _tools_for_prompt(self, tool: str) -> List[Dict[str, Any]]:
        names = [tool] + [n for n in self.candidates if n != tool and n not in SIDE_EFFECT_TOOLS][:8]
        if tool in SEARCH_TOOLS or tool in READ_TOOLS:
            names += [n for n in ("web_search", "tavily_search", "web_scrape") if n not in names and n in self.defs]
        out = []
        for n in names:
            d = self.defs.get(n)
            if d:
                out.append({"name": n, "description": (d.get("description") or "")[:220],
                            "parameters": list((d.get("parameters") or {}).get("properties", {}).keys()),
                            "required": (d.get("parameters") or {}).get("required", [])})
        return out

    async def _ask_model(self, tool: str, args: Dict[str, Any], detail: str, kind: Dict[str, str],
                         history: List[Dict[str, Any]], think: bool = False) -> Dict[str, Any]:
        from app.services.llm import JSON_MIN_TOKENS, llm_service, parse_llm_json
        lessons = []
        try:
            from app.services.doctor import doctor
            lessons = doctor.lessons_for(tool, kind["kind"])
        except Exception:
            pass
        user = (f"USER REQUEST: {self.user_query[:600]}\n\n"
                f"FAILED CALL: {tool} {json.dumps(args, default=str)[:800]}\n"
                f"WHAT WENT WRONG ({kind['kind']}): {detail[:700]}\n"
                f"HINT: {kind['hint']}\n"
                + (f"EARLIER ATTEMPTS: {json.dumps(history, default=str)[:900]}\n" if history else "")
                + (f"WHAT FIXED THIS BEFORE: {json.dumps(lessons)[:600]}\n" if lessons else "")
                + f"\nAVAILABLE TOOLS:\n{json.dumps(self._tools_for_prompt(tool), indent=1)[:3500]}")
        try:
            reply = await llm_service.chat_completion(
                messages=[{"role": "system", "content": REPAIR_PROMPT}, {"role": "user", "content": user}],
                temperature=0.1, max_tokens=JSON_MIN_TOKENS, think=think, task="repair")
            decision = parse_llm_json(reply.get("content", "")) or {"action": "give_up", "reason": "I couldn't work out a fix."}
            decision["_example_id"] = reply.get("_example_id")
            return decision
        except Exception as e:
            return {"action": "give_up", "reason": f"I couldn't work out a fix ({e})."}

    async def repair(self, tool: str, args: Dict[str, Any], result: Dict[str, Any], call_id: str):
        from app.services.doctor import doctor
        seen = {fingerprint(tool, args)}
        history: List[Dict[str, Any]] = []
        cur_tool, cur_args, cur_result = tool, args, result
        check = check_result(tool, args, result)
        same_retry_used = False
        attempt = 0
        first_kind = None
        while not check["ok"] and attempt < MAX_REPAIRS_PER_CALL:
            attempt += 1
            kind = classify(check["detail"], check["problem"])
            first_kind = first_kind or kind["kind"]
            doctor.record_failure(cur_tool, kind["kind"], check["detail"])
            yield {"type": "status", "content": f"🩺 {cur_tool} didn't work ({kind['kind'].replace('_', ' ')}): {check['detail'][:140]}"}

            # 1. Fix the machinery when that's the problem.
            fixed = await doctor.auto_fix(kind["kind"])
            if fixed:
                yield {"type": "status", "content": f"🩺 {fixed}"}

            # 2. Decide the next call.
            if cur_tool in SIDE_EFFECT_TOOLS:
                # Never re-run something that may have half-happened; report instead.
                break
            if kind["kind"] in TRANSIENT and not same_retry_used and cur_tool not in NO_BLIND_RETRY and \
                    (fixed or kind["kind"] in ("timeout", "network", "rate_limit")):
                same_retry_used = True
                nxt = {"action": "retry", "tool_name": cur_tool, "tool_args": cur_args, "reason": "retrying after the fix", "same": True}
            elif kind["kind"] in ("blocked", "missing_key") and not self._tools_for_prompt(cur_tool)[1:]:
                break
            else:
                # Fast first (most fixes are obvious: a typo, a missing argument); think if that didn't work.
                nxt = await self._ask_model(cur_tool, cur_args, check["detail"], kind, history,
                                            think=bool(history) or kind["kind"] == "unknown")
            if nxt.get("action") not in ("retry", "switch") or not nxt.get("tool_name"):
                history.append({"gave_up": nxt.get("reason")})
                break
            new_tool, new_args = nxt["tool_name"], nxt.get("tool_args") or {}
            fp = fingerprint(new_tool, new_args)
            if new_tool not in self.defs or (new_tool in SIDE_EFFECT_TOOLS) or (fp in seen and not nxt.get("same")):
                history.append({"rejected": new_tool, "why": "unknown/side-effect tool or a repeat"})
                break
            seen.add(fp)
            rid = f"{call_id}_fix{attempt}"
            yield {"type": "status", "content": f"🔧 {nxt.get('reason', 'Trying a fix')[:160]}"}
            yield {"type": "tool_call", "name": new_tool, "args": new_args, "id": rid}
            try:
                new_result = await self.run(new_tool, new_args)
            except Exception as e:
                new_result = {"success": False, "error": str(e)}
            new_check = check_result(new_tool, new_args, new_result)
            yield {"type": "tool_result", "name": new_tool, "id": rid, "data": new_result,
                   "summary": ("✓ fixed" if new_check["ok"] else f"still failing: {new_check['detail'][:120]}")}
            history.append({"tool": new_tool, "args": new_args, "ok": new_check["ok"], "detail": new_check["detail"][:200]})
            if nxt.get("_example_id"):  # the repair decision itself becomes training data
                from app.services import dataset_recorder
                dataset_recorder.label(nxt["_example_id"], 1 if new_check["ok"] else -1,
                                       "fixed it" if new_check["ok"] else f"action failed: {new_check['detail'][:120]}")
            if new_check["ok"]:
                doctor.record_fix(tool, kind["kind"], check["detail"], new_tool, new_args, auto_fix=fixed)
            cur_tool, cur_args, cur_result, check = new_tool, new_args, new_result, new_check

        if check["ok"] and attempt:
            if cur_tool == tool:
                yield {"type": "status", "content": f"✓ Recovered: {cur_tool} worked after {attempt} fix{'es' if attempt > 1 else ''}"}
            else:  # a different tool ran fine; that isn't necessarily what this step was for
                yield {"type": "status", "content": f"↪ Worked around it with {cur_tool} instead of {tool}"}
        elif not check["ok"]:
            reason = next((h["gave_up"] for h in reversed(history) if h.get("gave_up")), None)
            from app.services import llm_routing
            switched = llm_routing.note_failure(f"{cur_tool}: {check['detail']}"[:200])
            if switched:   # auto mode: the rest of this turn uses the stronger model
                yield {"type": "status", "content": switched}
            if isinstance(cur_result, dict):
                cur_result = {**cur_result, "verified": False, "problem": check["detail"],
                              **({"what_to_do": reason} if reason else {})}
        yield {"type": "_repair_done", "tool": cur_tool, "args": cur_args, "result": cur_result,
               "ok": check["ok"], "repairs": history, "kind": first_kind}
