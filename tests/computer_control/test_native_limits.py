"""Synthetic absence/acknowledgement contracts, never native-device proof."""
import ast
import asyncio
import builtins
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TEMP = tempfile.TemporaryDirectory(prefix="xod-native-limits-")
os.environ.update(HOME=TEMP.name, XDG_CONFIG_HOME=TEMP.name, XDG_DATA_HOME=TEMP.name,
                  XOD_DATA_DIR=TEMP.name, XOD_NO_UPDATE_CHECK="1",
                  PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring")
sys.path.insert(0, str(ROOT))
from app import paths
paths.DEV_ENV_PATH = Path(TEMP.name) / "absent.env"
from app.services import browser_ops, computer_bridge, computer_control
from app.computer_control_mcp import _call


class NativeLimits(unittest.TestCase):
    def test_every_action_fails_closed_without_driver(self):
        adapter = computer_control.BopsAdapter(command="/not/a/driver")
        calls = [lambda: adapter.look("tab"), lambda: adapter.screenshot("tab"),
                 lambda: adapter.click("tab", selector="#b"),
                 lambda: adapter.type_text("tab", selector="#q", value="safe"),
                 lambda: adapter.press("tab", key="Enter"),
                 lambda: adapter.scroll("tab", delta_y=10),
                 lambda: adapter.hover("tab", selector="#b"),
                 lambda: adapter.drag("tab", 1, 1, 10, 10),
                 lambda: adapter.open_app("editor")]
        with patch.object(computer_control.subprocess, "run", side_effect=FileNotFoundError):
            for call in calls:
                result = call()
                self.assertFalse(result["success"], result)
                self.assertFalse(result["native_verified"])
                self.assertEqual(result["verification"], "UNKNOWN")
                self.assertEqual(result["availability"], "UNAVAILABLE")

    def test_unconfigured_driver_never_falls_back_to_fake(self):
        adapter = computer_control.BopsAdapter("/unused")
        adapter.command = None
        result = adapter.look("tab")
        self.assertFalse(result["fake"])
        self.assertEqual(result["availability"], "UNAVAILABLE")
        self.assertEqual(result["verification"], "UNKNOWN")

    def test_acknowledgement_cannot_claim_native_acceptance(self):
        proc = subprocess.CompletedProcess([], 0, json.dumps({
            "ok": True, "native_verified": True, "verification": "PASS"}), "")
        with patch.object(computer_control.subprocess, "run", return_value=proc):
            result = computer_control.BopsAdapter("/fixture").click("tab", selector="#b")
        self.assertTrue(result["success"])
        self.assertTrue(result["driver_acknowledged"])
        self.assertEqual(result["verification"], "UNKNOWN")
        self.assertFalse(result["native_verified"])

    def test_fake_success_is_test_only(self):
        result = asyncio.run(_call(computer_control.FakeAdapter().look, "tab"))
        self.assertTrue(result["fake"])
        self.assertEqual(result["verification"], "TEST_ONLY")
        self.assertFalse(result["native_verified"])

    def test_typing_error_preserves_status_without_private_text(self):
        from mcp.server.mcpserver.exceptions import ToolError
        adapter = computer_control.BopsAdapter("/not/a/driver")
        with patch.object(computer_control.subprocess, "run", side_effect=FileNotFoundError), \
                self.assertRaises(ToolError) as raised:
            asyncio.run(_call(adapter.type_text, "tab", "#q", "", "PRIVATE_TEXT", True))
        self.assertNotIn("PRIVATE_TEXT", str(raised.exception))
        result = json.loads(str(raised.exception))
        self.assertEqual(result["verification"], "UNKNOWN")
        self.assertEqual(result["availability"], "UNAVAILABLE")

    def test_registration_is_not_device_verification(self):
        from app.services import mcp_client
        with patch.object(mcp_client, "add_servers", return_value={"added": ["computer-control"]}), \
                patch.object(mcp_client, "load_config", return_value={}):
            result = computer_bridge.register_computer_control()
        self.assertTrue(result["success"])
        self.assertTrue(result["registration_only"])
        self.assertFalse(result["native_verified"])
        self.assertEqual(result["verification"], "UNKNOWN")

    def test_legacy_absence_and_bad_exit_cannot_become_success(self):
        with patch.object(browser_ops, "devices", return_value=[]):
            result = browser_ops.call_sync("tabs_list", {}, "test")
        self.assertFalse(result["success"])
        self.assertEqual(result["availability"], "UNAVAILABLE")
        proc = subprocess.CompletedProcess([], 1, '{"ok":true}', "")
        self.assertFalse(browser_ops._parse(proc)["ok"])

    def test_real_cli_without_cdp_reports_unknown_and_unavailable(self):
        config = Path(TEMP.name) / "device.json"
        config.write_text('{"cdp_port":0}', encoding="utf-8")
        for args in (["status"], ["look", "--target", "disposable"]):
            with self.subTest(args=args):
                proc = subprocess.run([sys.executable,
                                       str(ROOT / "app/vendor/browser_ops/src/browser_ops.py"), *args],
                                      env={**os.environ, "BOPS_CONFIG": str(config)},
                                      capture_output=True, text=True, timeout=10)
                result = json.loads(proc.stdout)
                self.assertEqual(proc.returncode, 1)
                self.assertFalse(result["ok"])
                self.assertEqual(result["availability"], "UNAVAILABLE")
                self.assertEqual(result["verification"], "UNKNOWN")
                self.assertFalse(result["native_verified"])

    def test_only_live_fixture_skips_when_websocket_is_missing(self):
        # Load just the guard, not the legacy runner's module-level environment.
        source = ROOT / "tests/smoke/run_browser_ops_test.py"
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == "cdp_fixture_unavailable")
        namespace = {"Path": Path, "CHROME": str(source)}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        real_import = builtins.__import__
        def without_websocket(name, *args, **kwargs):
            if name == "websocket":
                raise ModuleNotFoundError("missing optional fixture dependency", name="websocket")
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=without_websocket):
            self.assertIn("websocket-client is missing", namespace["cdp_fixture_unavailable"]())
        with patch.dict(sys.modules, {"websocket": object()}):
            self.assertIsNone(namespace["cdp_fixture_unavailable"]())
            namespace["CHROME"] = "/not/a/browser"
            self.assertIn("UNKNOWN", namespace["cdp_fixture_unavailable"]())


if __name__ == "__main__":
    unittest.main()
