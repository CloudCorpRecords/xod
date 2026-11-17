"""
Computer control (AGE-85): the adapter layer behind the harness computer-control tools.

The vendored browser-ops CLI implements explicit-target browser inspection,
interaction and screenshots over CDP. Protocol acknowledgements and synthetic
fixtures are not real-device acceptance; see docs/AGE85-REAL-DEVICE-ACCEPTANCE.md.

This module is the clean seam between the harness and any such driver:

  ComputerControlAdapter   the interface: look, screenshot, click, type_text, press
  FakeAdapter              a deterministic in-memory page; proves the wiring end
                           to end with no device, and says it is fake in every result
  BopsAdapter              shells to a configured bops CLI with the exact verb
                           shapes bops documents (look, click, type, press,
                           shot-tab, each with --target); honest errors when no
                           bops is configured or the call fails

The adapter is chosen by environment: XOD_COMPUTER_CONTROL_ADAPTER=fake forces
the fake; =bops and unset use bops (XOD_COMPUTER_CONTROL_BOPS or PATH).
There is no automatic fake fallback: missing configuration is a failure.
Production registration (computer_bridge)
passes the adapter choice through the MCP server's env, never silently: the
server reports which adapter is live in every result.

Nothing here approves anything. Approval lives in the MCP hub: the bridge
registers at ask. Explicit read_only trust permits only targeted look through
the verified launcher. Actions and screenshots still need approval.
"""
import json
import os
import shutil
import subprocess
import functools
import inspect
from typing import Any, Dict, List, Optional
from app.vendor.browser_ops.src.control_inputs import validate


def validated(method):
    """Apply identical validation to fake and real drivers before any action."""
    signature = inspect.signature(method)

    @functools.wraps(method)
    def checked(self, *args, **kwargs):
        try:
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
        except TypeError:
            return self._error("Invalid arguments for " + method.__name__)
        error = validate(method.__name__, bound.arguments)
        result = self._error(error) if error else method(self, *args, **kwargs)
        if self.name == "fake":
            result.update(verification="TEST_ONLY", native_verified=False)
        return result
    return checked


class ComputerControlAdapter:
    """Interface every computer-control driver implements. Methods return dicts."""

    name = "base"

    def _error(self, message):
        return {"success": False, "adapter": self.name, "fake": self.name == "fake",
                "verification": "TEST_ONLY" if self.name == "fake" else "UNKNOWN",
                "native_verified": False, "error": message}

    def look(self, target: str = "") -> Dict[str, Any]:
        raise NotImplementedError

    def screenshot(self, target: str = "") -> Dict[str, Any]:
        raise NotImplementedError

    def click(self, target: str = "", selector: str = "", text: str = "",
              index: Optional[int] = None) -> Dict[str, Any]:
        raise NotImplementedError

    def type_text(self, target: str = "", selector: str = "", text: str = "",
                  value: str = "", clear: bool = False) -> Dict[str, Any]:
        raise NotImplementedError

    def press(self, target: str = "", key: str = "", modifiers: str = "") -> Dict[str, Any]:
        raise NotImplementedError

    def scroll(self, target: str = "", delta_x: int = 0, delta_y: int = 0) -> Dict[str, Any]:
        raise NotImplementedError

    def hover(self, target: str = "", selector: str = "", text: str = "",
              index: Optional[int] = None) -> Dict[str, Any]:
        raise NotImplementedError

    def drag(self, target: str = "", start_x: int = 0, start_y: int = 0,
             end_x: int = 0, end_y: int = 0, steps: int = 10) -> Dict[str, Any]:
        raise NotImplementedError

    def open_app(self, application: str = "") -> Dict[str, Any]:
        raise NotImplementedError


class FakeAdapter(ComputerControlAdapter):
    """A deterministic fake page, for tests and wiring proofs. Not a device."""

    name = "fake"

    def __init__(self) -> None:
        self.elements: List[Dict[str, Any]] = [
            {"index": 1, "kind": "button", "text": "Save", "selector": "#save"},
            {"index": 2, "kind": "input", "text": "", "selector": "#q", "label": "Search"},
            {"index": 3, "kind": "link", "text": "Home", "selector": "a.home"},
        ]
        self.actions: List[Dict[str, Any]] = []
        self.values: Dict[str, str] = {"#q": ""}

    def _page(self) -> Dict[str, Any]:
        return {"adapter": self.name, "fake": True, "title": "Fake Page",
                "url": "fake://page", "target": "fake-target",
                "elements": list(self.elements),
                "text": ["Fake page ready", "Save", "Home"],
                "values": dict(self.values)}

    @validated
    def look(self, target: str = "") -> Dict[str, Any]:
        return {"success": True, **self._page()}

    @validated
    def screenshot(self, target: str = "") -> Dict[str, Any]:
        return {"success": True, "adapter": self.name, "fake": True,
                "format": "none", "bytes": 0,
                "note": "Fake adapter: no real image was captured."}

    def _resolve(self, selector: str = "", text: str = "",
                 index: Optional[int] = None) -> Optional[Dict[str, Any]]:
        for el in self.elements:
            if index is not None and el["index"] == index:
                return el
            if selector and el.get("selector") == selector:
                return el
            if text and el.get("text") == text:
                return el
        return None

    @validated
    def click(self, target: str = "", selector: str = "", text: str = "",
              index: Optional[int] = None) -> Dict[str, Any]:
        el = self._resolve(selector, text, index)
        if el is None:
            return {"success": False, "adapter": self.name, "fake": True,
                    "error": "No fake element matches that selector, text, or index. Nothing was clicked."}
        self.actions.append({"action": "click", "element": el["index"]})
        return {"success": True, "adapter": self.name, "fake": True,
                "clicked": el, "actions": list(self.actions)}

    @validated
    def type_text(self, target: str = "", selector: str = "", text: str = "",
                  value: str = "", clear: bool = False) -> Dict[str, Any]:
        el = self._resolve(selector, text, None)
        if el is None or el.get("kind") != "input":
            return self._error("No fake input matches. Nothing was typed.")
        key = el.get("selector") or "#q"
        self.values[key] = value if clear else (self.values.get(key, "") + value)
        self.actions.append({"action": "type", "element": el["index"], "value": value})
        return {"success": True, "adapter": self.name, "fake": True,
                "typed_into": el, "value_now": self.values[key],
                "read_back_verified": self.values[key].endswith(value) if value else True,
                "actions": list(self.actions)}

    @validated
    def press(self, target: str = "", key: str = "", modifiers: str = "") -> Dict[str, Any]:
        if not key:
            return {"success": False, "adapter": self.name, "fake": True,
                    "error": "press needs a key. Nothing was pressed."}
        self.actions.append({"action": "press", "key": key, "modifiers": modifiers})
        return {"success": True, "adapter": self.name, "fake": True,
                "pressed": key, "modifiers": modifiers, "actions": list(self.actions)}

    @validated
    def scroll(self, target="", delta_x=0, delta_y=0):
        self.actions.append({"action": "scroll", "delta_x": delta_x, "delta_y": delta_y})
        return {"success": True, "adapter": self.name, "fake": True, "actions": list(self.actions)}

    @validated
    def hover(self, target="", selector="", text="", index=None):
        el = self._resolve(selector, text, index)
        if not el:
            return self._error("No fake element matches. Nothing was hovered.")
        self.actions.append({"action": "hover", "element": el["index"]})
        return {"success": True, "adapter": self.name, "fake": True, "hovered": el}

    @validated
    def drag(self, target="", start_x=0, start_y=0, end_x=0, end_y=0, steps=10):
        self.actions.append({"action": "drag", "start": [start_x, start_y], "end": [end_x, end_y]})
        return {"success": True, "adapter": self.name, "fake": True, "actions": list(self.actions)}

    @validated
    def open_app(self, application=""):
        self.actions.append({"action": "open_app", "application": application})
        return {"success": True, "adapter": self.name, "fake": True,
                "application": application, "note": "Fake adapter: no application was opened."}


class BopsAdapter(ComputerControlAdapter):
    """Drives a real device browser through a configured bops CLI.

    Verb shapes are the ones bops documents in its own usage text:
      bops look  --target <id>
      bops click --target <id> (--index <n> | --selector <css> | --text <text>)
      bops type  --target <id> [--selector <css> | --text <label>] --value <text> [--clear]
      bops press --target <id> --key <key> [--modifiers ctrl,shift]
      bops shot-tab --target <id>
    Unproven on a real device from the Linux build sandbox: no signed-in
    device browser is reachable from here, so this adapter is exercised only
    for its honest failure when bops is missing or errors.
    """

    name = "bops"

    def __init__(self, command: Optional[str] = None) -> None:
        self.command = command or os.environ.get("XOD_COMPUTER_CONTROL_BOPS") or shutil.which("bops")

    def _run(self, argv: List[str]) -> Dict[str, Any]:
        if not self.command:
            return {**self._error("No bops CLI is configured on this machine "
                                 "(set XOD_COMPUTER_CONTROL_BOPS). Nothing was done."),
                    "availability": "UNAVAILABLE", "outcome_unknown": False}
        try:
            proc = subprocess.run([self.command] + argv, capture_output=True,
                                  text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return {**self._error("bops timed out. Outcome is unknown; inspect the device before retrying."),
                    "outcome_unknown": True}
        except OSError as e:
            return {**self._error(f"bops could not start ({type(e).__name__}). Check the configured executable."),
                    "availability": "UNAVAILABLE", "outcome_unknown": False}
        out = (proc.stdout or "").strip()
        try:
            data = json.loads(out)
        except ValueError:
            return {**self._error("bops did not return a JSON object. Check its version and device configuration."),
                    "outcome_unknown": argv[0] not in ("look", "shot-tab", "shot-desktop")}
        if not isinstance(data, dict):
            return {**self._error("bops returned an invalid non-object response."),
                    "outcome_unknown": argv[0] not in ("look", "shot-tab", "shot-desktop")}
        if proc.returncode != 0:
            return {**self._error(str(data.get("error") or proc.stderr or "bops error")[:500]),
                    "availability": data.get("availability", "UNKNOWN"),
                    "data": data, "outcome_unknown": data.get("outcome_unknown", False)}
        success = data.get("ok") is True and data.get("success") is not False
        return {"success": success, "adapter": self.name, "fake": data.get("fake") is True,
                # A CLI acknowledgement, including CDP dispatch, is not native
                # effect acceptance. Even read-back evidence is scoped to that
                # field, not a passed real-device acceptance matrix.
                "verification": "TEST_ONLY" if data.get("fake") is True else "UNKNOWN",
                "native_verified": False, "driver_acknowledged": success,
                "availability": data.get("availability", "UNKNOWN"),
                "outcome_unknown": data.get("outcome_unknown", False), "data": data,
                **({} if success else {"error": str(data.get("error") or "bops did not confirm success.")[:500]})}

    @validated
    def look(self, target: str = "") -> Dict[str, Any]:
        return self._run(["look", "--target", target] if target else ["look"])

    @validated
    def screenshot(self, target: str = "") -> Dict[str, Any]:
        return self._run(["shot-tab", "--target", target] if target else ["shot-desktop"])

    @validated
    def click(self, target: str = "", selector: str = "", text: str = "",
              index: Optional[int] = None) -> Dict[str, Any]:
        argv = ["click", "--target", target]
        if index is not None:
            argv += ["--index", str(index)]
        elif selector:
            argv += ["--selector=" + selector]
        elif text:
            argv += ["--text=" + text]
        else:
            return {"success": False, "adapter": self.name,
                    "error": "click needs a selector, text, or index. Nothing was clicked."}
        return self._run(argv)

    @validated
    def type_text(self, target: str = "", selector: str = "", text: str = "",
                  value: str = "", clear: bool = False) -> Dict[str, Any]:
        argv = ["type", "--target", target, "--value=" + value]
        if selector:
            argv += ["--selector=" + selector]
        elif text:
            argv += ["--text=" + text]
        if clear:
            argv += ["--clear"]
        return self._run(argv)

    @validated
    def press(self, target: str = "", key: str = "", modifiers: str = "") -> Dict[str, Any]:
        if not key:
            return {"success": False, "adapter": self.name,
                    "error": "press needs a key. Nothing was pressed."}
        argv = ["press", "--target", target, "--key", key]
        if modifiers:
            argv += ["--modifiers", modifiers]
        return self._run(argv)

    @validated
    def scroll(self, target="", delta_x=0, delta_y=0):
        return self._run(["scroll", "--target", target, "--delta-x", str(delta_x), "--delta-y", str(delta_y)])

    @validated
    def hover(self, target="", selector="", text="", index=None):
        argv = ["hover", "--target", target]
        argv += (["--index", str(index)] if index is not None else
                 ["--selector=" + selector] if selector else ["--text=" + text])
        return self._run(argv)

    @validated
    def drag(self, target="", start_x=0, start_y=0, end_x=0, end_y=0, steps=10):
        return self._run(["drag", "--target", target, "--start-x", str(start_x),
                          "--start-y", str(start_y), "--end-x", str(end_x),
                          "--end-y", str(end_y), "--steps", str(steps)])

    @validated
    def open_app(self, application=""):
        return self._run(["open-app", "--app", application])


def get_adapter() -> ComputerControlAdapter:
    """Fake is explicit only. Missing real configuration is an honest failure."""
    choice = (os.environ.get("XOD_COMPUTER_CONTROL_ADAPTER") or "").strip().lower()
    if choice not in ("", "fake", "bops"):
        raise ValueError("Unsupported XOD_COMPUTER_CONTROL_ADAPTER; choose bops or explicit test-only fake.")
    if choice == "fake":
        return FakeAdapter()
    return BopsAdapter()
