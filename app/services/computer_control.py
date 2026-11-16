"""
Computer control (AGE-85): the adapter layer behind the harness computer-control tools.

Tree steers a real signed-in browser through bops (the browser-ops host wrapper):
look (screenshot plus an interactive element map and visible text), click by
selector or text or index, type with read-back, press for keys, and tab and
desktop screenshots, all over CDP on a device browser the person is already
signed into. Xod's vendored browser-ops copy predates that surface: it can
register and sweep tabs and take screenshots, but it cannot look at a page's
elements or act inside the page at all. Xod's desktop tools (services/desktop.py)
do look, click, type and key on the LOCAL machine only, and only when the
screen add-on is ready, which AGE-57 signing currently blocks on the Mac.

This module is the clean seam between the harness and any such driver:

  ComputerControlAdapter   the interface: look, screenshot, click, type_text, press
  FakeAdapter              a deterministic in-memory page; proves the wiring end
                           to end with no device, and says it is fake in every result
  BopsAdapter              shells to a configured bops CLI with the exact verb
                           shapes bops documents (look, click, type, press,
                           shot-tab, each with --target); honest errors when no
                           bops is configured or the call fails

The adapter is chosen by environment: XOD_COMPUTER_CONTROL_ADAPTER=fake forces
the fake; =bops forces bops; unset picks bops when a bops CLI is configured
(XOD_COMPUTER_CONTROL_BOPS or a bops on PATH) and the fake otherwise, so a
machine with no device route still registers the tools and answers honestly
through the fake only in tests. Production registration (computer_bridge)
passes the adapter choice through the MCP server's env, never silently: the
server reports which adapter is live in every result.

Nothing here approves anything. Approval lives in the MCP hub: the bridge
registers the server at trust "ask", so every call, reads included in v1,
waits for the person's OK on the approval card before the adapter runs.
"""
import json
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional


class ComputerControlAdapter:
    """Interface every computer-control driver implements. Methods return dicts."""

    name = "base"

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

    def look(self, target: str = "") -> Dict[str, Any]:
        return {"success": True, **self._page()}

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

    def click(self, target: str = "", selector: str = "", text: str = "",
              index: Optional[int] = None) -> Dict[str, Any]:
        el = self._resolve(selector, text, index)
        if el is None:
            return {"success": False, "adapter": self.name, "fake": True,
                    "error": "No fake element matches that selector, text, or index. Nothing was clicked."}
        self.actions.append({"action": "click", "element": el["index"]})
        return {"success": True, "adapter": self.name, "fake": True,
                "clicked": el, "actions": list(self.actions)}

    def type_text(self, target: str = "", selector: str = "", text: str = "",
                  value: str = "", clear: bool = False) -> Dict[str, Any]:
        el = self._resolve(selector, text, None) or self.elements[1]
        key = el.get("selector") or "#q"
        self.values[key] = value if clear else (self.values.get(key, "") + value)
        self.actions.append({"action": "type", "element": el["index"], "value": value})
        return {"success": True, "adapter": self.name, "fake": True,
                "typed_into": el, "value_now": self.values[key],
                "read_back_verified": self.values[key].endswith(value) if value else True,
                "actions": list(self.actions)}

    def press(self, target: str = "", key: str = "", modifiers: str = "") -> Dict[str, Any]:
        if not key:
            return {"success": False, "adapter": self.name, "fake": True,
                    "error": "press needs a key. Nothing was pressed."}
        self.actions.append({"action": "press", "key": key, "modifiers": modifiers})
        return {"success": True, "adapter": self.name, "fake": True,
                "pressed": key, "modifiers": modifiers, "actions": list(self.actions)}


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
            return {"success": False, "adapter": self.name,
                    "error": "No bops CLI is configured on this machine "
                             "(set XOD_COMPUTER_CONTROL_BOPS). Nothing was done."}
        try:
            proc = subprocess.run([self.command] + argv, capture_output=True,
                                  text=True, timeout=60)
        except Exception as e:
            return {"success": False, "adapter": self.name,
                    "error": f"bops call failed: {type(e).__name__}: {e}"}
        out = (proc.stdout or "").strip()
        try:
            data = json.loads(out) if out else {}
        except ValueError:
            data = {"raw": out[:2000]}
        if proc.returncode != 0:
            return {"success": False, "adapter": self.name,
                    "error": (data.get("error") or proc.stderr or "bops error")[:500],
                    "data": data}
        return {"success": bool(data.get("ok", True)), "adapter": self.name, "data": data}

    def look(self, target: str = "") -> Dict[str, Any]:
        return self._run(["look", "--target", target] if target else ["look"])

    def screenshot(self, target: str = "") -> Dict[str, Any]:
        return self._run(["shot-tab", "--target", target] if target else ["shot-desktop"])

    def click(self, target: str = "", selector: str = "", text: str = "",
              index: Optional[int] = None) -> Dict[str, Any]:
        argv = ["click", "--target", target]
        if index is not None:
            argv += ["--index", str(index)]
        elif selector:
            argv += ["--selector", selector]
        elif text:
            argv += ["--text", text]
        else:
            return {"success": False, "adapter": self.name,
                    "error": "click needs a selector, text, or index. Nothing was clicked."}
        return self._run(argv)

    def type_text(self, target: str = "", selector: str = "", text: str = "",
                  value: str = "", clear: bool = False) -> Dict[str, Any]:
        argv = ["type", "--target", target, "--value", value]
        if selector:
            argv += ["--selector", selector]
        elif text:
            argv += ["--text", text]
        if clear:
            argv += ["--clear"]
        return self._run(argv)

    def press(self, target: str = "", key: str = "", modifiers: str = "") -> Dict[str, Any]:
        if not key:
            return {"success": False, "adapter": self.name,
                    "error": "press needs a key. Nothing was pressed."}
        argv = ["press", "--target", target, "--key", key]
        if modifiers:
            argv += ["--modifiers", modifiers]
        return self._run(argv)


def get_adapter() -> ComputerControlAdapter:
    """Adapter selection: explicit env wins; else bops if configured; else fake."""
    choice = (os.environ.get("XOD_COMPUTER_CONTROL_ADAPTER") or "").strip().lower()
    if choice == "fake":
        return FakeAdapter()
    if choice == "bops":
        return BopsAdapter()
    if os.environ.get("XOD_COMPUTER_CONTROL_BOPS") or shutil.which("bops"):
        return BopsAdapter()
    return FakeAdapter()
