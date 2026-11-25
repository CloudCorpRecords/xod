"""
desktop_run: deterministic, verified multi-step runner for native computer apps.

Mirrors browser_run.py for macOS/Windows desktop workflows: chains native app
actions (open_app, look, click, type, key, scroll, wait, assert, zoom_crop,
terminal_send, terminal_read) in one call, verifying each step's landing state
against the live desktop (front app, window title, visible AX text/controls)
before advancing, with bounded retry on miss.

A run is a list of step dicts:
  {"action": "open_app", "app": "Notes", "expect": {"app_is": "Notes"}}
  {"action": "look", "expect": {"text_contains": "All iCloud"}}
  {"action": "click", "target": "New Note", "prefer_ax": true}   # element number OR label substring
  {"action": "click", "x": 420, "y": 180}
  {"action": "type", "target": "Search", "text": "hello", "enter": true}
  {"action": "key", "keys": "cmd+n"}
  {"action": "scroll", "direction": "down", "amount": 5}
  {"action": "wait", "seconds": 1.0}
  {"action": "assert", "expect": {"app_is": "Notes", "text_contains": "hello"}}
  {"action": "zoom_crop", "x": 420, "y": 180, "radius": 140}
  {"action": "terminal_send", "command": "ls -la"}
  {"action": "terminal_read"}

expect keys:
  app_is            front app name matches (case-insensitive substring)
  window_contains   any open window title contains this string
  text_contains     visible AX screen text contains this string
  element_contains  any numbered AX element label contains this string
  element_count_min at least N interactive AX elements visible
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("xod.desktop_run")

MAX_STEPS = 40
MAX_RETRIES = 2


def _match_element_by_label(elements_text: str, target: str) -> Optional[int]:
    """Resolve a human label substring (e.g. 'New Note') to its 1-based element index
    from desktop.look()'s formatted element lines: '[3] Button \"New Note\" at (100,50)'."""
    q = str(target or "").strip().lower()
    if not q:
        return None
    exact_hit = None
    partial_hit = None
    for line in (elements_text or "").splitlines():
        line_s = line.strip()
        if not line_s.startswith("[") or "]" not in line_s:
            continue
        idx_str = line_s[1:line_s.index("]")]
        if not idx_str.isdigit():
            continue
        idx = int(idx_str)
        low = line_s.lower()
        if f'"{q}"' in low:
            exact_hit = idx
            break
        if q in low and partial_hit is None:
            partial_hit = idx
    return exact_hit if exact_hit is not None else partial_hit


def _check_expect(expect: Dict[str, Any], state: Dict[str, Any]) -> Tuple[bool, str]:
    """Evaluate expect assertions against a desktop.look() snapshot."""
    if not expect:
        return True, "ok"
    app_name = str(state.get("app") or "")
    text = str(state.get("text") or "")
    els = str(state.get("elements") or "")
    count = int(state.get("count") or 0)
    wins = state.get("windows") or []

    if expect.get("app_is"):
        want = str(expect["app_is"]).strip().lower()
        if want not in app_name.lower():
            return False, f"expected front app '{expect['app_is']}', got '{app_name}'"
    if expect.get("window_contains"):
        want = str(expect["window_contains"]).strip().lower()
        titles = [str(w.get("title") or "") for w in wins if isinstance(w, dict)]
        if not any(want in t.lower() for t in titles):
            return False, f"no window title matched '{expect['window_contains']}'"
    if expect.get("text_contains"):
        want = str(expect["text_contains"]).strip().lower()
        if want not in text.lower() and want not in els.lower():
            return False, f"screen text did not contain '{expect['text_contains']}'"
    if expect.get("element_contains"):
        want = str(expect["element_contains"]).strip().lower()
        if want not in els.lower():
            return False, f"no visible control matched '{expect['element_contains']}'"
    if expect.get("element_count_min") is not None:
        need = int(expect["element_count_min"])
        if count < need:
            return False, f"expected at least {need} controls, saw {count}"
    return True, "ok"


async def run_desktop_steps(steps: List[Dict[str, Any]], desktop_mod=None) -> Dict[str, Any]:
    """Execute a verified sequence of desktop actions. Stops on the first failed step
    (after bounded retries) and returns a structured per-step transcript."""
    if desktop_mod is None:
        from app.services import desktop as desktop_mod
    if not isinstance(steps, list) or not steps:
        return {"success": False, "error": "steps must be a non-empty list", "steps": []}
    if len(steps) > MAX_STEPS:
        return {"success": False, "error": f"too many steps ({len(steps)} > {MAX_STEPS})", "steps": []}

    started = time.time()
    records: List[Dict[str, Any]] = []
    last_look: Optional[Dict[str, Any]] = None

    for i, raw in enumerate(steps):
        if not isinstance(raw, dict):
            return {"success": False, "error": f"step {i + 1} is not a dict", "steps": records}
        action = str(raw.get("action") or "").strip().lower()
        expect = raw.get("expect") or {}
        retries = max(0, min(int(raw.get("retries", 1)), MAX_RETRIES))
        step_rec: Dict[str, Any] = {"step": i + 1, "action": action, "attempts": 0, "ok": False}

        for attempt in range(retries + 1):
            step_rec["attempts"] = attempt + 1
            try:
                if action == "open_app":
                    app_name = str(raw.get("app") or raw.get("name") or "").strip()
                    res = await desktop_mod.open_app(app_name)
                    if not res.get("success"):
                        raise RuntimeError(res.get("error") or f"open_app({app_name}) failed")
                    if not expect and app_name:
                        expect = {"app_is": app_name}
                elif action == "look":
                    last_look = await desktop_mod.look(marks=bool(raw.get("marks", True)))
                    if not last_look.get("success"):
                        raise RuntimeError(last_look.get("error") or "look failed")
                    res = {"app": last_look.get("app"), "count": last_look.get("count")}
                elif action == "click":
                    el_id = raw.get("element")
                    if el_id is None and raw.get("target"):
                        if last_look is None:
                            last_look = await desktop_mod.look(marks=False)
                        el_id = _match_element_by_label(last_look.get("elements") or "", str(raw["target"]))
                        if el_id is None:
                            # Refresh look once in case UI just updated
                            last_look = await desktop_mod.look(marks=False)
                            el_id = _match_element_by_label(last_look.get("elements") or "", str(raw["target"]))
                        if el_id is None:
                            raise RuntimeError(f"could not find control matching '{raw['target']}'")
                    res = await desktop_mod.click(
                        element=el_id, x=raw.get("x"), y=raw.get("y"),
                        button=str(raw.get("button") or "left"),
                        clicks=int(raw.get("clicks") or 1),
                        prefer_ax=bool(raw.get("prefer_ax", True)),
                    )
                    if not res.get("success"):
                        raise RuntimeError(res.get("error") or "click failed")
                elif action == "type":
                    el_id = raw.get("element")
                    if el_id is None and raw.get("target"):
                        if last_look is None:
                            last_look = await desktop_mod.look(marks=False)
                        el_id = _match_element_by_label(last_look.get("elements") or "", str(raw["target"]))
                        if el_id is None:
                            last_look = await desktop_mod.look(marks=False)
                            el_id = _match_element_by_label(last_look.get("elements") or "", str(raw["target"]))
                        if el_id is None:
                            raise RuntimeError(f"could not find input matching '{raw['target']}'")
                    res = await desktop_mod.type_text(
                        text=str(raw.get("text") or ""),
                        element=el_id,
                        enter=bool(raw.get("enter", False)),
                    )
                    if not res.get("success"):
                        raise RuntimeError(res.get("error") or "type failed")
                elif action == "key":
                    res = await desktop_mod.key(str(raw.get("keys") or raw.get("key") or ""))
                    if not res.get("success"):
                        raise RuntimeError(res.get("error") or "key failed")
                elif action == "scroll":
                    res = await desktop_mod.scroll(
                        direction=str(raw.get("direction") or "down"),
                        amount=int(raw.get("amount") or 5),
                        element=raw.get("element"), x=raw.get("x"), y=raw.get("y"),
                    )
                    if not res.get("success"):
                        raise RuntimeError(res.get("error") or "scroll failed")
                elif action == "wait":
                    await asyncio.sleep(max(0.05, min(float(raw.get("seconds") or 1.0), 15.0)))
                    res = {"waited": True}
                elif action == "zoom_crop":
                    res = await desktop_mod.zoom_crop(
                        float(raw.get("x") or 0), float(raw.get("y") or 0),
                        radius=int(raw.get("radius") or 160),
                    )
                    if not res.get("success"):
                        raise RuntimeError(res.get("error") or "zoom_crop failed")
                elif action == "assert":
                    last_look = await desktop_mod.look(marks=False)
                    res = {"app": last_look.get("app"), "count": last_look.get("count")}
                elif action == "terminal_send":
                    res = await desktop_mod.terminal_send(str(raw.get("command") or ""))
                    if not res.get("success"):
                        raise RuntimeError(res.get("error") or "terminal_send failed")
                elif action == "terminal_read":
                    res = await desktop_mod.terminal_read()
                    if not res.get("success"):
                        raise RuntimeError(res.get("error") or "terminal_read failed")
                else:
                    raise RuntimeError(f"unknown desktop_run action '{action}'")

                if expect:
                    if action not in ("look", "assert"):
                        await asyncio.sleep(0.25)
                        last_look = await desktop_mod.look(marks=False)
                    ok, why = _check_expect(expect, last_look or {})
                    if not ok:
                        raise RuntimeError(f"landing check failed: {why}")

                step_rec["ok"] = True
                step_rec["result"] = res
                break
            except Exception as e:
                step_rec["error"] = str(e)
                if attempt < retries:
                    await asyncio.sleep(0.35)
                    last_look = None  # force fresh look before retry
        records.append(step_rec)
        if not step_rec["ok"]:
            return {
                "success": False,
                "failed_step": i + 1,
                "error": step_rec.get("error") or "step failed",
                "elapsed_s": round(time.time() - started, 2),
                "steps": records,
            }

    return {
        "success": True,
        "completed": len(records),
        "elapsed_s": round(time.time() - started, 2),
        "steps": records,
    }
