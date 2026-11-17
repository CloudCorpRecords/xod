"""Shared, dependency-free validation for the host adapter and device CLI."""
import re

TARGET = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
APP = re.compile(r"[A-Za-z0-9_.-]{1,80}\Z")
KEYS = {
    "Enter", "Return", "Escape", "Esc", "Tab", "Backspace", "Delete",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End",
    "PageUp", "PageDown", "Space",
} | {f"F{i}" for i in range(1, 13)}
MODIFIERS = {"ctrl", "control", "shift", "alt", "meta", "cmd", "command"}


def validate(action, values):
    """Return an error, or None. Booleans are not integer coordinates."""
    for name in ("target", "selector", "text", "value", "key", "modifiers", "application"):
        value = values.get(name, "")
        if not isinstance(value, str) or "\0" in value:
            return f"{name} must be text without NUL characters."
        limit = 10000 if name == "value" else 2000
        if len(value) > limit:
            return f"{name} is too long (maximum {limit} characters)."
    target = values.get("target", "")
    if action != "open_app" and not (action == "screenshot" and not target):
        if not TARGET.fullmatch(target) or target.startswith("-"):
            return "A valid explicit browser target is required; look up its tab ID first."
    if action in ("click", "hover", "type_text"):
        selector, text, index = (values.get(k) for k in ("selector", "text", "index"))
        if sum(bool(v) for v in (selector, text)) + (index is not None) != 1:
            return "Use exactly one selector, text label, or index."
        if index is not None and (type(index) is not int or not 1 <= index <= 500):
            return "index must be an integer from 1 to 500."
    if action == "type_text" and type(values.get("clear", False)) is not bool:
        return "clear must be a boolean."
    if action == "press":
        key = values.get("key", "")
        if not (key in KEYS or (len(key) == 1 and key.isprintable())):
            return "Use a supported key name or one printable character."
        mods = values.get("modifiers", "")
        if mods and any(m.strip().lower() not in MODIFIERS for m in mods.split(",")):
            return "modifiers must be comma-separated ctrl, shift, alt or meta."
    if action == "scroll":
        for name in ("delta_x", "delta_y"):
            value = values.get(name, 0)
            if type(value) is not int or not -4000 <= value <= 4000:
                return f"{name} must be an integer from -4000 to 4000."
        if not (values.get("delta_x") or values.get("delta_y")):
            return "scroll needs a nonzero delta_x or delta_y."
    if action == "drag":
        for name in ("start_x", "start_y", "end_x", "end_y"):
            value = values.get(name)
            if type(value) is not int or not 0 <= value <= 16384:
                return f"{name} must be an integer from 0 to 16384."
        steps = values.get("steps", 10)
        if type(steps) is not int or not 2 <= steps <= 60:
            return "steps must be an integer from 2 to 60."
        if (values["start_x"], values["start_y"]) == (values["end_x"], values["end_y"]):
            return "Drag start and end must differ."
    if action == "open_app" and not APP.fullmatch(values.get("application", "")):
        return "application must be a configured app alias (letters, digits, dot, dash or underscore)."
    return None
