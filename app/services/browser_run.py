"""
browser_run: deterministic long-form browser runs for Xod (AGE-99).

The mission engine (browser_sandbox.run_browser_mission) is an LLM loop capped at
MISSION_MAX_STEPS and aimed at one goal on one surface. This module is the other
shape Rene asked for: a scripted run that chains many browser tasks in one go,
across several tabs, where every step is verified against the live page before
the run moves on, and a missed step is recovered by re-reading state and changing
strategy, never by blindly repeating the call that just failed.

A run is a list of step dicts:

  {"action": "navigate", "url": "https://example.org", "expect": {"url_contains": "example"}}
  {"action": "open_tab", "url": "..."}            open a tab and move the claim to it
  {"action": "switch_tab", "tab": {"index": 1}}   or {"url_contains": ...} / {"title_contains": ...} / {"id": ...}
  {"action": "close_tab", "tab": {"index": 2}}
  {"action": "click", "target": "Sign in", "expect": {"text_contains": "Welcome"}}
  {"action": "type", "selector": "#q", "text": "hello", "press_enter": true}
  {"action": "fill", "fields": [{"selector": "#name", "value": "Ada"}]}
  {"action": "scroll", "direction": "down", "amount": 600}   or {"to_selector": "#footer"}
  {"action": "press_key", "key": "Enter"}
  {"action": "wait", "target": "#results"}        or {"seconds": 2}
  {"action": "extract", "name": "headline"}       page text stored under this name in the run record
  {"action": "assert", "expect": {...}}          verification only, no action

expect keys: url_contains, title_contains, text_contains, selector (must exist),
value_equals {"selector": ..., "value": ...}. A step without an explicit expect
gets the default landing check for its action (a typed field is read back, a tab
switch must show the resolved tab active, a navigation must land on its URL).

Recovery ladder per step (each rung re-reads live state first, and the run record
keeps every attempt):
  1. settle: wait briefly, re-verify. Pages often land late.
  2. re-pin: if the active tab drifted from the claimed tab, switch back, retry once.
  3. alternate strategy: a click that missed is retried through the indexed
     element map (observe + interact_by_index); a type that did not stick is
     retried through interact_by_index; a navigation is re-issued after the
     state read. The identical failed call is never repeated back to back.

The run holds the sandbox tab claim for its whole length and serializes with
missions through the same lock, so a run and a mission never drive the browser
at the same time. The claim is released when the run ends, pass or fail.
"""
import asyncio
import time
from typing import Any, Dict, List, Optional

ACTIONS = ("navigate", "open_tab", "switch_tab", "close_tab", "click", "type",
           "fill", "scroll", "press_key", "wait", "extract", "assert")


class StepFailed(Exception):
    pass


def _ok(res: Any) -> bool:
    return isinstance(res, dict) and bool(res.get("success", True)) and "error" not in res


async def page_state(sb) -> Dict[str, Any]:
    """Live state, read fresh: tab list, which tab is active, where it sits."""
    tabs_res = await sb.manage_tabs("list")
    tabs = tabs_res.get("tabs", []) if isinstance(tabs_res, dict) else []
    active_id = tabs_res.get("active_tab_id") if isinstance(tabs_res, dict) else None
    active = next((t for t in tabs if t.get("id") == active_id), None)
    return {
        "url": (active or {}).get("url", sb.current_url),
        "title": (active or {}).get("title", sb.current_title),
        "active_tab_id": active_id,
        "claimed_tab_id": getattr(sb, "claimed_tab_id", None),
        "tab_count": len([t for t in tabs if t.get("type") == "page"]),
        "tabs": tabs,
    }


async def _field_value(sb, selector: str) -> Optional[str]:
    res = await sb.evaluate_js(
        f"(() => {{ const el = document.querySelector({selector!r}); "
        f"return el ? (el.value !== undefined ? el.value : el.textContent) : null; }})()"
    )
    if isinstance(res, dict) and res.get("success"):
        return res.get("result")
    return None


async def page_text(sb) -> str:
    """The page's readable text. extract_content spreads its JS result, so the text
    lives under text_preview (older callers used content); check every known key."""
    content = await sb.extract_content()
    if not isinstance(content, dict):
        return ""
    for key in ("text_preview", "content", "text", "markdown"):
        if content.get(key):
            return content[key]
    return ""


async def check_expect(sb, expect: Dict[str, Any], state: Dict[str, Any]) -> (bool, str):
    """Verify an expect dict against live state. Returns (landed, detail)."""
    if not expect:
        return True, "no expectation"
    if "url_contains" in expect and expect["url_contains"] not in (state.get("url") or ""):
        return False, f"url {state.get('url')!r} does not contain {expect['url_contains']!r}"
    if "title_contains" in expect and expect["title_contains"] not in (state.get("title") or ""):
        return False, f"title {state.get('title')!r} does not contain {expect['title_contains']!r}"
    if "text_contains" in expect:
        text = await page_text(sb)
        if expect["text_contains"] not in text:
            return False, f"page text does not contain {expect['text_contains']!r}"
    if "selector" in expect:
        val = await _field_value(sb, expect["selector"])
        # _field_value returns None only when the element is absent for value-less elements;
        # use a presence probe so an element without a value still counts as present.
        present = await sb.evaluate_js(
            f"(() => !!document.querySelector({expect['selector']!r}))()")
        if not (isinstance(present, dict) and present.get("result") is True):
            return False, f"selector {expect['selector']!r} not present"
    if "value_equals" in expect:
        ve = expect["value_equals"] or {}
        val = await _field_value(sb, ve.get("selector", ""))
        if val != ve.get("value"):
            return False, f"field {ve.get('selector')!r} holds {val!r}, want {ve.get('value')!r}"
    return True, "expectation met"


async def _default_check(sb, step: Dict[str, Any], before: Dict[str, Any],
                         state: Dict[str, Any], result: Dict[str, Any]) -> (bool, str):
    """The landing check a step gets when it carries no explicit expect."""
    action = step["action"]
    if action == "navigate":
        want = step.get("url", "")
        got = state.get("url") or ""
        if want and want.rstrip("/") not in got and got.rstrip("/") not in want:
            return False, f"navigated to {got!r}, want {want!r}"
        return True, "landed on the target URL"
    if action == "open_tab":
        if state["tab_count"] <= before["tab_count"]:
            return False, "tab count did not grow"
        return True, "new tab open and active"
    if action == "switch_tab":
        want = result.get("resolved_tab_id")
        if want and state.get("active_tab_id") != want:
            return False, f"active tab is {state.get('active_tab_id')}, want {want}"
        return True, "target tab active"
    if action == "close_tab":
        if state["tab_count"] >= before["tab_count"]:
            return False, "tab count did not shrink"
        return True, "tab closed"
    if action == "type":
        val = await _field_value(sb, step.get("selector", ""))
        if val != step.get("text"):
            return False, f"field holds {val!r}, typed {step.get('text')!r}"
        return True, "field value read back"
    if action == "fill":
        for f in step.get("fields", []):
            if f.get("selector") and "value" in f:
                val = await _field_value(sb, f["selector"])
                if val != f["value"]:
                    return False, f"field {f['selector']!r} holds {val!r}, want {f['value']!r}"
        return True, "all field values read back"
    return True, "action reported success and state was re-read"


async def _resolve_tab(sb, spec: Any, state: Dict[str, Any]) -> Optional[str]:
    tabs = [t for t in state.get("tabs", []) if t.get("type") == "page"]
    if isinstance(spec, dict):
        if spec.get("id"):
            return spec["id"]
        if spec.get("index") is not None:
            i = int(spec["index"])
            return tabs[i].get("id") if 0 <= i < len(tabs) else None
        if spec.get("url_contains"):
            hit = next((t for t in tabs if spec["url_contains"] in (t.get("url") or "")), None)
            return hit.get("id") if hit else None
        if spec.get("title_contains"):
            hit = next((t for t in tabs if spec["title_contains"] in (t.get("title") or "")), None)
            return hit.get("id") if hit else None
    if isinstance(spec, int):
        return tabs[spec].get("id") if 0 <= spec < len(tabs) else None
    return None


async def _do_action(sb, step: Dict[str, Any], state: Dict[str, Any],
                     extracts: Dict[str, Any], strategy: str = "primary") -> Dict[str, Any]:
    """Perform one step's action once. Returns the primitive's result dict."""
    action = step["action"]
    if action == "navigate":
        return await sb.navigate(step["url"])
    if action == "open_tab":
        return await sb.manage_tabs("new", url=step.get("url", "about:blank"))
    if action == "switch_tab":
        tab_id = await _resolve_tab(sb, step.get("tab"), state)
        if not tab_id:
            raise StepFailed(f"no tab matches {step.get('tab')!r}")
        res = await sb.manage_tabs("switch", target_id=tab_id)
        res["resolved_tab_id"] = tab_id
        return res
    if action == "close_tab":
        tab_id = await _resolve_tab(sb, step.get("tab"), state)
        if not tab_id:
            raise StepFailed(f"no tab matches {step.get('tab')!r}")
        return await sb.manage_tabs("close", target_id=tab_id)
    if action == "click":
        if strategy == "alternate":
            target = (step.get("target") or "").strip()
            if target.startswith(("#", ".", "[")):
                # Same element, different dispatch: the indexed path grounds by selector and
                # fires the element's own click, which reaches buttons a coordinate click
                # cannot (covered by an overlay, off-viewport, hit-test blocked).
                return await sb.interact_by_index(selector=target, action="click")
            observed = await sb.observe()
            elements = []
            if isinstance(observed, dict):
                elements = observed.get("elements") or observed.get("indexed_elements") or []
            low = target.lower()
            hit = next((e for e in elements
                        if low and low in str(e.get("text") or e.get("label") or "").lower()), None)
            if hit is None:
                raise StepFailed(f"indexed map has no element matching {step.get('target')!r}")
            return await sb.interact_by_index(index=hit.get("index"), action="click")
        return await sb.click(step["target"])
    if action == "type":
        if strategy == "alternate":
            return await sb.interact_by_index(selector=step["selector"], action="type",
                                              text=step["text"],
                                              press_enter=bool(step.get("press_enter")),
                                              clear_first=True)
        return await sb.type_text(step["selector"], step["text"],
                                  press_enter=bool(step.get("press_enter")), clear_first=True)
    if action == "fill":
        return await sb.fill_form(step.get("fields", []), submit_index=step.get("submit_index"))
    if action == "scroll":
        return await sb.scroll(direction=step.get("direction", "down"),
                               amount=int(step.get("amount", 500)),
                               to_selector=step.get("to_selector"))
    if action == "press_key":
        return await sb.press_key(step["key"])
    if action == "wait":
        if step.get("target"):
            return await sb.wait_for_selector(step["target"],
                                              timeout_sec=float(step.get("timeout_sec", 8.0)))
        await asyncio.sleep(float(step.get("seconds", 1.0)))
        return {"success": True, "waited": step.get("seconds", 1.0)}
    if action == "extract":
        text = await page_text(sb)
        extracts[step.get("name", "extract")] = text
        return {"success": True, "chars": len(text)}
    if action == "assert":
        return {"success": True}
    raise StepFailed(f"unknown action {action!r}")


async def _verify(sb, step: Dict[str, Any], before: Dict[str, Any],
                  result: Dict[str, Any]) -> (bool, str, Dict[str, Any]):
    """Verify a step's landing. Exception-safe by contract: a state read or a
    check that times out (a busy page, a socket mid-navigation) is a miss with
    its cause recorded, never an exception that kills the run."""
    try:
        state = await page_state(sb)
    except Exception as e:
        return False, f"state read failed: {type(e).__name__}: {e}", dict(before)
    try:
        if step.get("expect"):
            ok, detail = await check_expect(sb, step["expect"], state)
        else:
            ok, detail = await _default_check(sb, step, before, state, result)
    except Exception as e:
        return False, f"verification error: {type(e).__name__}: {e}", state
    return ok, detail, state


async def _recheck(sb, step: Dict[str, Any], before: Dict[str, Any],
                   state: Dict[str, Any]) -> (bool, str):
    """The settle rung's bare re-check, with the same exception contract as
    _verify: a timed-out read is a miss, not a crash."""
    try:
        if step.get("expect"):
            return await check_expect(sb, step["expect"], state)
        return await _default_check(sb, step, before, state, {})
    except Exception as e:
        return False, f"verification error: {type(e).__name__}: {e}"


async def _run_step(sb, step: Dict[str, Any], extracts: Dict[str, Any],
                    max_recoveries: int) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"action": step.get("action"), "attempts": []}
    try:
        before = await page_state(sb)
    except Exception as e:  # even the opening state read can time out on a busy browser
        rec["attempts"].append({"strategy": "primary",
                                "error": f"state read failed: {type(e).__name__}: {e}"})
        rec["status"] = "failed"
        rec["detail"] = f"state read failed: {type(e).__name__}: {e}"
        rec["url_before"] = rec["url_after"] = None
        rec["title_after"] = None
        rec["active_tab_id"] = None
        return rec
    rec["url_before"] = before.get("url")

    async def attempt(strategy: str) -> (bool, str, Dict[str, Any]):
        try:
            state = await page_state(sb)
        except Exception as e:
            rec["attempts"].append({"strategy": strategy,
                                    "error": f"state read failed: {type(e).__name__}: {e}"})
            return False, f"state read failed: {type(e).__name__}: {e}", before
        try:
            result = await _do_action(sb, step, state, extracts, strategy)
        except StepFailed as e:
            rec["attempts"].append({"strategy": strategy, "error": str(e)})
            return False, str(e), state
        except Exception as e:  # a primitive raising is a miss, not a run crash
            rec["attempts"].append({"strategy": strategy, "error": f"{type(e).__name__}: {e}"})
            return False, str(e), state
        if not _ok(result):
            rec["attempts"].append({"strategy": strategy, "result": str(result)[:300]})
            return False, f"action reported failure: {str(result)[:200]}", state
        ok, detail, after = await _verify(sb, step, before, result)
        rec["attempts"].append({"strategy": strategy, "verified": ok, "detail": detail})
        return ok, detail, after

    ok, detail, after = await attempt("primary")
    rungs = 0
    while not ok and rungs < max_recoveries:
        rungs += 1
        # Rung 1: settle and re-verify only. The action may have landed late.
        await asyncio.sleep(0.8)
        try:
            state_now = await page_state(sb)
        except Exception as e:  # a timed-out read is a miss; fall back to the last known state
            rec["attempts"].append({"strategy": "settle-recheck",
                                    "error": f"state read failed: {type(e).__name__}: {e}"})
            state_now = after
            ok2 = False
        else:
            ok2, detail2 = await _recheck(sb, step, before, state_now)
        if ok2:
            rec["attempts"].append({"strategy": "settle-recheck", "verified": True, "detail": detail2})
            ok, detail, after = True, f"landed late ({detail2})", state_now
            break
        # Rung 2: re-pin the claim if the active tab drifted, then retry once.
        claimed = getattr(sb, "claimed_tab_id", None)
        if claimed and state_now.get("active_tab_id") != claimed:
            try:
                await sb.manage_tabs("switch", target_id=claimed)
            except Exception as e:  # re-pin failed; the alternate rung still gets its turn
                rec["attempts"].append({"strategy": "repin",
                                        "error": f"{type(e).__name__}: {e}"})
                ok, detail, after = await attempt("alternate")
                if ok:
                    break
                continue
            ok, detail, after = await attempt("primary-after-repin")
            if ok:
                break
            continue
        # Rung 3: alternate strategy (indexed click / indexed type / fresh navigate).
        ok, detail, after = await attempt("alternate")
        if ok:
            break

    rec["status"] = "landed" if ok and len(rec["attempts"]) == 1 else ("recovered" if ok else "failed")
    rec["detail"] = detail
    rec["url_after"] = after.get("url")
    rec["title_after"] = after.get("title")
    rec["active_tab_id"] = after.get("active_tab_id")
    return rec


async def run_browser_steps(sb, steps: List[Dict[str, Any]], owner: str = "browser-run",
                            stop_on_failure: bool = True, max_recoveries: int = 2,
                            claim: bool = True) -> Dict[str, Any]:
    """Runs a scripted long-form browser run with per-step verification and recovery.
    Serializes with missions through the sandbox mission lock and holds the tab claim
    for the whole run. Returns the full run record."""
    if getattr(sb, "_mission_lock", None) is None:
        sb._mission_lock = asyncio.Lock()
    async with sb._mission_lock:
        return await _run_steps_unlocked(sb, steps, owner, stop_on_failure, max_recoveries, claim)


async def _run_steps_unlocked(sb, steps, owner, stop_on_failure, max_recoveries, claim):
    started = time.time()
    record: Dict[str, Any] = {
        "run_id": f"run_{int(started * 1000)}",
        "owner": owner,
        "status": "running",
        "steps": [],
        "extracts": {},
        "started_at": time.strftime("%H:%M:%S"),
    }
    claim_res = None
    try:
        if claim:
            # Warm the browser first: a cold start can restart it mid-first-navigation,
            # and a claim taken before that restart points at a tab that no longer exists.
            try:
                await sb.ensure_browser_running()
            except Exception:
                pass
            claim_res = await sb.claim_tab(owner=owner)
            record["claim"] = claim_res
            if not _ok(claim_res):
                record["status"] = "failed"
                record["final_answer"] = f"could not claim a tab: {claim_res}"
                return record
        for i, step in enumerate(steps):
            if not isinstance(step, dict) or step.get("action") not in ACTIONS:
                record["steps"].append({"action": (step or {}).get("action"),
                                        "status": "failed", "detail": "unknown action"})
                if stop_on_failure:
                    break
                continue
            reclaimed = False
            if claim and getattr(sb, "claimed_tab_id", None) is None:
                # A browser restart or the close of the claimed tab took the claim;
                # re-pin to the tab the run is actually on and say so in the record.
                try:
                    re = await sb.claim_tab(owner=owner)
                    reclaimed = bool(_ok(re))
                except Exception:
                    reclaimed = False
            rec = await _run_step(sb, step, record["extracts"], max_recoveries)
            rec["step_num"] = i + 1
            if reclaimed:
                rec["reclaimed"] = True
            record["steps"].append(rec)
            if rec["status"] == "failed" and stop_on_failure:
                break
        landed = sum(1 for s in record["steps"] if s["status"] in ("landed", "recovered"))
        record["steps_landed"] = landed
        record["steps_total"] = len(steps)
        record["status"] = "completed" if landed == len(steps) and len(record["steps"]) == len(steps) else "incomplete"
        try:
            final_state = await page_state(sb)
        except Exception:  # the run record stands even if the last state read times out
            final_state = {}
        record["final_url"] = final_state.get("url")
        record["final_title"] = final_state.get("title")
        record["final_tabs"] = [{"id": t.get("id"), "title": t.get("title"), "url": t.get("url")}
                                for t in final_state.get("tabs", []) if t.get("type") == "page"]
        return record
    finally:
        if claim and claim_res is not None:
            try:
                sb.release_tab_claim()
            except Exception:
                pass
        record["secs"] = round(time.time() - started, 1)
