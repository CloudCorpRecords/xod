"""Synthetic driver/transport checks, NOT native desktop acceptance."""
import argparse
import asyncio
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = tempfile.TemporaryDirectory(prefix="xod_control_contract_")
os.environ.update({"XOD_DATA_DIR": TMP.name, "HOME": TMP.name,
                   "XDG_CONFIG_HOME": TMP.name, "XOD_NO_UPDATE_CHECK": "1"})
from app import paths
paths.DEV_ENV_PATH = Path(TMP.name) / "absent.env"
from app.services.computer_control import BopsAdapter, FakeAdapter, get_adapter
from app.services import browser_ops, computer_bridge, mcp_client
from app.vendor.browser_ops.src import control

LOADER = importlib.machinery.SourceFileLoader("fixture_bops_wrapper", str(browser_ops.MODULE_DIR / "bin/bops"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
WRAPPER = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WRAPPER)


class AdapterContract(unittest.TestCase):
    def test_mcp_device_gestures_do_not_interleave(self):
        from app.computer_control_mcp import _call
        active, highest = 0, 0

        def fixture_action():
            nonlocal active, highest
            active += 1
            highest = max(highest, active)
            time.sleep(0.02)
            active -= 1
            return {"success": True, "fake": True}

        async def run():
            return await asyncio.gather(_call(fixture_action), _call(fixture_action))

        self.assertTrue(all(x["success"] for x in asyncio.run(run())))
        self.assertEqual(highest, 1)

    def test_all_nine_command_shapes_use_argv_without_shell(self):
        adapter = BopsAdapter("/fixture/bops")
        operations = [
            (lambda: adapter.look("tab1"), ["look", "--target", "tab1"]),
            (lambda: adapter.screenshot("tab1"), ["shot-tab", "--target", "tab1"]),
            (lambda: adapter.click("tab1", index=1), ["click", "--target", "tab1", "--index", "1"]),
            (lambda: adapter.type_text("tab1", selector="#q", value="--help", clear=True),
             ["type", "--target", "tab1", "--value=--help", "--selector=#q", "--clear"]),
            (lambda: adapter.press("tab1", key="Enter", modifiers="ctrl,shift"),
             ["press", "--target", "tab1", "--key", "Enter", "--modifiers", "ctrl,shift"]),
            (lambda: adapter.scroll("tab1", delta_y=-200),
             ["scroll", "--target", "tab1", "--delta-x", "0", "--delta-y", "-200"]),
            (lambda: adapter.hover("tab1", text='Save "$()"; snow 雪'),
             ["hover", "--target", "tab1", '--text=Save "$()"; snow 雪']),
            (lambda: adapter.drag("tab1", 1, 2, 30, 40, 8),
             ["drag", "--target", "tab1", "--start-x", "1", "--start-y", "2",
              "--end-x", "30", "--end-y", "40", "--steps", "8"]),
            (lambda: adapter.open_app("editor"), ["open-app", "--app", "editor"]),
        ]
        for operation, argv in operations:
            with self.subTest(argv=argv), patch("app.services.computer_control.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, '{"ok":true}', "")
                self.assertTrue(operation()["success"])
                self.assertEqual(run.call_args.args[0], ["/fixture/bops"] + argv)
                self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_invalid_arguments_never_dispatch_fake_or_real_actions(self):
        cases = [
            ("look", {}), ("look", {"target": "--help"}), ("look", {"target": "bad\nid"}),
            ("hover", {"selector": "#q", "text": "Search"}),
            ("hover", {"index": True}), ("click", {"index": 0}), ("click", {}),
            ("type_text", {"selector": "#q", "clear": "yes"}),
            ("type_text", {"selector": "#q", "value": "x"*10001}),
            ("type_text", {"selector": "#q", "value": "\0"}),
            ("press", {"key": "Enter;run"}), ("press", {"key": "a", "modifiers": "root"}),
            ("scroll", {}), ("scroll", {"delta_y": True}), ("scroll", {"delta_y": 1.5}),
            ("scroll", {"delta_y": 4001}), ("drag", {"end_x": 5, "steps": 100}),
            ("drag", {"end_x": 5, "start_y": -1}), ("drag", {}),
            ("open_app", {"application": "python -c evil"}),
        ]
        for cls in (BopsAdapter, FakeAdapter):
            for action, args in cases:
                kwargs = {"target": "tab1", **args} if action != "open_app" else args
                if action == "look" and not args:
                    kwargs = {}
                adapter = cls()
                with self.subTest(adapter=cls.__name__, action=action, args=args), \
                        patch.object(BopsAdapter, "_run") as dispatch:
                    self.assertFalse(getattr(adapter, action)(**kwargs)["success"])
                    dispatch.assert_not_called()
                    if isinstance(adapter, FakeAdapter):
                        self.assertEqual(adapter.actions, [])

    def test_empty_malformed_and_unconfirmed_output_cannot_succeed(self):
        for stdout in ("", "not json", "null", "[]", '"ok"', "{}", '{"ok":"true"}',
                       '{"ok":true,"success":false}', '{"ok":false,"error":123}'):
            with self.subTest(stdout=stdout), patch("app.services.computer_control.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, stdout, "")
                self.assertFalse(BopsAdapter("/fixture").look("tab1")["success"])

    def test_process_failures_remain_failures_and_timeout_is_uncertain(self):
        with patch("app.services.computer_control.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, '{"ok":true}', "failed")
            self.assertFalse(BopsAdapter("/fixture").look("tab1")["success"])
            run.side_effect = subprocess.TimeoutExpired(["DO_NOT_ECHO_TYPED_CONTENT"], 60)
            result = BopsAdapter("/fixture").scroll("tab1", delta_y=1)
            self.assertFalse(result["success"])
            self.assertTrue(result["outcome_unknown"])
            self.assertNotIn("DO_NOT_ECHO", result["error"])

    def test_fake_mode_is_explicit_and_missing_field_is_not_redirected(self):
        with patch.dict(os.environ, {"XOD_COMPUTER_CONTROL_ADAPTER": "", "XOD_COMPUTER_CONTROL_BOPS": ""}), \
                patch("app.services.computer_control.shutil.which", return_value=None):
            self.assertIsInstance(get_adapter(), BopsAdapter)
            self.assertFalse(get_adapter().look("tab1")["success"])
        with patch.dict(os.environ, {"XOD_COMPUTER_CONTROL_ADAPTER": "unknown"}):
            with self.assertRaises(ValueError):
                get_adapter()
        fake = FakeAdapter()
        self.assertFalse(fake.type_text("tab1", selector="#missing", value="test")["success"])
        self.assertEqual(fake.actions, [])

    def test_read_only_eligibility_requires_own_launcher_and_only_look(self):
        with patch.dict(os.environ, {"XOD_COMPUTER_CONTROL_ADAPTER": "bops"}):
            spec = computer_bridge.server_spec("read_only")
        self.assertTrue(computer_bridge.verified_look(spec, "computer_look"))
        for name in ("computer_hover", "computer_drag", "computer_scroll", "computer_open_app",
                     "computer_click", "computer_type", "computer_press", "computer_screenshot"):
            self.assertFalse(computer_bridge.verified_look(spec, name))
        for change in ({"command": "python"}, {"args": ["-c", "evil"]}, {"cwd": "/tmp"},
                       {"url": "https://example.invalid"}, {"secret_env": {"TOKEN": "fixture"}},
                       {"env": {"PYTHONPATH": "/tmp"}}, {"env": {computer_bridge.ENV_ADAPTER: "unknown"}}):
            self.assertFalse(computer_bridge.verified_look({**spec, **change}, "computer_look"))

    def test_explicit_driver_choice_is_not_overwritten_by_inherited_test_mode(self):
        with patch.dict(os.environ, {"XOD_COMPUTER_CONTROL_ADAPTER": "fake"}):
            spec = computer_bridge.server_spec()
            spec["env"] = {computer_bridge.ENV_ADAPTER: "bops"}
            env, _, _ = mcp_client.stdio_env("computer-control", spec)
            self.assertEqual(env[computer_bridge.ENV_ADAPTER], "bops")


class DeviceContract(unittest.TestCase):
    def test_actual_cli_entrypoint_loads_siblings_and_rejects_invalid_motion(self):
        script = browser_ops.MODULE_DIR / "src/browser_ops.py"
        env = {k: v for k, v in os.environ.items() if not k.endswith(("_KEY", "_TOKEN", "_SECRET"))}
        env["BOPS_CONFIG"] = str(Path(TMP.name) / "absent-device-config.json")
        for verb in ("look", "scroll", "hover", "drag", "open-app"):
            run = subprocess.run([sys.executable, str(script), verb, "--help"],
                                 capture_output=True, text=True, timeout=10, env=env)
            self.assertEqual(run.returncode, 0, run.stderr)
        run = subprocess.run([sys.executable, str(script), "scroll", "--target", "tab1",
                              "--delta-y", "4001"], capture_output=True, text=True, timeout=10, env=env)
        self.assertEqual(run.returncode, 1)
        self.assertFalse(json.loads(run.stdout)["ok"])

    def test_session_correlates_responses_and_ignores_interleaved_events(self):
        ws = Mock()
        ws.recv.side_effect = [
            '{"method":"Target.attachedToTarget","params":{"sessionId":"fixture-session"}}',
            '{"id":1,"result":{"sessionId":"fixture-session"}}',
            '{"method":"Page.frameResized","params":{}}',
            '{"id":2,"result":{}}', '{"id":3,"result":{}}',
        ]
        module = types.SimpleNamespace(create_connection=Mock(return_value=ws))
        with patch.dict(sys.modules, {"websocket": module}):
            with control.Session("tab1", lambda _: {"webSocketDebuggerUrl": "ws://fixture.invalid"},
                                 lambda: [{"id": "tab1", "type": "page"}]) as session:
                session.mouse("mouseWheel", 10, 20, deltaX=0, deltaY=100)
        sent = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        self.assertEqual([x["method"] for x in sent],
                         ["Target.attachToTarget", "Input.dispatchMouseEvent", "Target.detachFromTarget"])
        self.assertEqual(sent[1]["sessionId"], "fixture-session")
        self.assertNotIn("sessionId", sent[2])
        ws.close.assert_called_once()

    def test_session_cdp_error_fails_and_closes_connection(self):
        ws = Mock()
        ws.recv.return_value = '{"id":1,"error":{"message":"synthetic rejected attach"}}'
        module = types.SimpleNamespace(create_connection=Mock(return_value=ws))
        with patch.dict(sys.modules, {"websocket": module}):
            with self.assertRaises(RuntimeError):
                with control.Session("tab1", lambda _: {"webSocketDebuggerUrl": "ws://fixture.invalid"},
                                     lambda: [{"id": "tab1", "type": "page"}]):
                    self.fail("rejected attach must not proceed")
        ws.close.assert_called_once()

    def test_missing_device_websocket_dependencies_fail_honestly(self):
        with patch.dict(sys.modules, {"websocket": None, "websockets.sync.client": None}):
            with self.assertRaisesRegex(RuntimeError, "Install websocket-client or websockets"):
                with control.Session("tab1", lambda _: {"webSocketDebuggerUrl": "ws://fixture.invalid"},
                                     lambda: [{"id": "tab1", "type": "page"}]):
                    self.fail("missing dependencies must not produce a device session")

    def test_device_cli_registers_every_verb_and_validates_before_connecting(self):
        parser = argparse.ArgumentParser()
        control.add_commands(parser.add_subparsers(dest="command", required=True), {}, Mock(), Mock())
        for verb in ("look", "click", "type", "press", "scroll", "hover", "drag", "open-app"):
            self.assertIn(verb, parser.format_help())
        args = parser.parse_args(["scroll", "--target", "tab1", "--delta-y", "9000"])
        with patch.object(control, "Session") as session:
            self.assertFalse(args.fn(args)["ok"])
            session.assert_not_called()
        args = parser.parse_args(["open-app", "--app", "unconfigured"])
        self.assertFalse(args.fn(args)["ok"])

    def test_look_has_no_input_events_and_scroll_uses_actual_cdp_method(self):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        with patch.object(control, "Session", return_value=session):
            session.evaluate.return_value = {"elements": [], "text": "fixture"}
            out = control.run_action("look", {"target": "tab1"}, {}, Mock(), Mock())
            self.assertTrue(out["ok"])
            session.mouse.assert_not_called()
            session.call.assert_not_called()
            session.evaluate.return_value = {"width": 400, "height": 300}
            out = control.run_action("scroll", {"target": "tab1", "delta_x": 0, "delta_y": 30}, {}, Mock(), Mock())
            self.assertTrue(out["ok"])
            session.mouse.assert_called_once_with("mouseWheel", 200, 150, deltaX=0, deltaY=30)

    def test_drag_bounds_and_release_after_intermediate_failure(self):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.evaluate.return_value = {"width": 100, "height": 100}
        args = {"target": "tab1", "start_x": 1, "start_y": 2, "end_x": 100, "end_y": 20, "steps": 2}
        with patch.object(control, "Session", return_value=session):
            self.assertFalse(control.run_action("drag", args, {}, Mock(), Mock())["ok"])
            session.mouse.assert_not_called()
            args["end_x"] = 30
            session.mouse.side_effect = [None, None, RuntimeError("synthetic interrupted drag"), None]
            out = control.run_action("drag", args, {}, Mock(), Mock())
            self.assertFalse(out["ok"])
            self.assertTrue(out["outcome_unknown"])
            self.assertEqual(session.mouse.call_args.args[0], "mouseReleased")

    def test_type_readback_rejects_unchanged_preexisting_text(self):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        element = {"x": 10, "y": 20, "editable": True, "value": "hello",
                   "selection_start": 5, "selection_end": 5}
        with patch.object(control, "Session", return_value=session), patch.object(control, "locate", return_value=element):
            out = control.run_action("type_text", {"target": "tab1", "selector": "#q",
                                     "value": "hello", "clear": False}, {}, Mock(), Mock())
            self.assertFalse(out["ok"])
            self.assertFalse(out["read_back_verified"])

    def test_app_launch_is_allowlisted_argv_only_and_not_visibility_proof(self):
        with patch.object(control.sys, "platform", "darwin"), patch.object(control.subprocess, "run") as run:
            self.assertFalse(control.open_app("unknown", {})["ok"])
            run.assert_not_called()
            run.return_value.returncode = 0
            out = control.open_app("editor", {"allowed_apps": {"editor": {"bundle_id": "com.apple.TextEdit"}}})
            self.assertTrue(out["ok"])
            self.assertFalse(out["verified_visible"])
            self.assertEqual(run.call_args.args[0], ["/usr/bin/open", "-b", "com.apple.TextEdit"])
        with patch.object(control.sys, "platform", "linux"):
            self.assertFalse(control.open_app("editor", {"allowed_apps": {"editor": {}}})["ok"])

    def test_windows_alias_uses_one_configured_executable_without_shell_arguments(self):
        config = {"allowed_apps": {"editor": {"executable": r"C:\Fixture App\editor.exe"}}}
        with patch.object(control.sys, "platform", "win32"), \
                patch.object(control.os.path, "isabs", return_value=True), \
                patch.object(control.os.path, "isfile", return_value=True), \
                patch.object(control.subprocess, "Popen") as launch:
            out = control.open_app("editor", config)
            self.assertTrue(out["ok"])
            self.assertFalse(out["verified_visible"])
            self.assertEqual(launch.call_args.args[0], [r"C:\Fixture App\editor.exe"])
            self.assertFalse(launch.call_args.kwargs.get("shell", False))
            launch.reset_mock()
            self.assertFalse(control.open_app("command", {"allowed_apps": {
                "command": {"executable": r"C:\Windows\system32\cmd.exe /c echo unsafe"}}})["ok"])
            launch.assert_not_called()

    def test_catalog_does_not_bypass_legacy_browser_dispatcher(self):
        catalog = json.loads((browser_ops.MODULE_DIR / "tools.json").read_text())
        controls = {x["name"] for x in catalog["tools"] if x["name"].startswith("computer_")}
        self.assertEqual(len(controls), 9)
        offered = {x["function"]["name"] for x in browser_ops.tool_definitions(["local"])}
        self.assertFalse(controls & offered)
        self.assertEqual(offered, set(browser_ops.TOOL_NAMES))

    def test_remote_envelope_preserves_hostile_text_as_data_on_posix(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "browser_ops.py"
            script.write_text("import json,sys; print(json.dumps(sys.argv[1:]))", encoding="utf-8")
            values = ["type", "--target", "tab1", '--value=quotes "\' ; $(exit 77) `exit 78` & | 雪\nline']
            cmd = WRAPPER.remote_command({"python": sys.executable, "device_dir": folder}, values)
            self.assertEqual(len(shlex.split(cmd)), 3)
            env = {k: v for k, v in os.environ.items() if not k.endswith(("_KEY", "_TOKEN", "_SECRET"))}
            run = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=10, env=env)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout), values)

    def test_remote_transport_cannot_mask_failed_exit_or_malformed_responses(self):
        remote = {"python": "python", "device_dir": "/fixture", "ssh_user_host": "fixture.invalid"}
        for code, stdout in ((1, '{"ok":true}'), (0, "[]"), (0, "not json"), (0, "")):
            with self.subTest(code=code, stdout=stdout), patch.object(WRAPPER.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], code, stdout, "")
                result, status = WRAPPER.run_remote(remote, ["scroll", "--target", "tab1", "--delta-y", "1"])
                self.assertFalse(result["ok"])
                self.assertNotEqual(status, 0)
                self.assertTrue(result["outcome_unknown"])


if __name__ == "__main__":
    unittest.main()
