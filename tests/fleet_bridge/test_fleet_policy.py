"""Deterministic discovery, offline, registration and approval regressions.

No network, actual command execution, provider keys or desktop actions.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
TMP = tempfile.TemporaryDirectory(prefix="xod-fleet-policy-")
BASE = Path(TMP.name)
os.environ.update({
    "HOME": str(BASE / "home"), "XDG_CONFIG_HOME": str(BASE / "config"),
    "XDG_DATA_HOME": str(BASE / "share"), "XOD_DATA_DIR": str(BASE / "data"),
    "XOD_NO_UPDATE_CHECK": "1", "XOD_AUTOCONNECT": "0", "XOD_SPEECH": "off",
    "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
})
for key in ("OPENAI_API_KEY", "AI_INTEGRATIONS_OPENAI_API_KEY", "FIREWORKS_API_KEY",
            "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
    os.environ.pop(key, None)
sys.path.insert(0, str(ROOT))
from app import paths
paths.DEV_ENV_PATH = BASE / "absent.env"
from app.services import fleet_bridge as bridge, fleet_policy as policy, mcp_client as mcp


class DiscoveryTests(unittest.TestCase):
    def test_explicit_folder_precedes_path_and_fallback(self):
        folder = BASE / "shims"
        folder.mkdir(exist_ok=True)
        shim = folder / "testdelta"
        shim.touch()
        shim.chmod(0o700)
        with patch.dict(os.environ, {bridge.ENV_DIR: str(folder)}), \
             patch.object(bridge.shutil, "which", return_value="/foreign/tool"), \
             patch.object(policy, "published_spec", return_value=(None, "missing")):
            found = bridge.find_fleet_tools()
        self.assertEqual(found["testdelta"]["command"], str(shim))
        self.assertEqual(found["testdelta"]["source"], "env")

    def test_default_folder_used_without_override(self):
        with patch.dict(os.environ), patch.object(bridge, "DEFAULT_DIR", BASE / "default"), \
             patch.object(bridge, "_shim_in", return_value=Path("/default/shim")):
            os.environ.pop(bridge.ENV_DIR, None)
            self.assertEqual(bridge.find_fleet_tools()["cidiff"]["source"], "fleet-tools")

    def test_path_precedes_published(self):
        with patch.object(bridge, "_shim_in", return_value=None), \
             patch.object(bridge.shutil, "which", return_value="/foreign/tool"):
            self.assertEqual(bridge.find_fleet_tools()["cidiff"]["source"], "path")

    def test_fallback_is_fixed_and_has_no_package_execution_request(self):
        spec = {"command": "/node", "args": ["/pinned/cli.js", "mcp"], "source": "published"}
        with patch.object(bridge, "_shim_in", return_value=None), \
             patch.object(bridge.shutil, "which", return_value=None), \
             patch.object(policy, "published_spec", return_value=(spec, None)):
            self.assertEqual(bridge.find_fleet_tools()["testdelta"], spec)
        self.assertNotIn("npx", json.dumps(spec))

    def test_missing_offline_package_error_is_actionable(self):
        with patch.object(policy, "PACKAGE_ROOT", BASE / "missing"), \
             patch.object(policy.shutil, "which", return_value="/node"):
            spec, error = policy.published_spec("testdelta")
        self.assertIsNone(spec)
        self.assertIn("npm ci --ignore-scripts", error)
        self.assertIn("Offline", error)

    def test_node_missing_is_explicit(self):
        with patch.object(policy.shutil, "which", return_value=None):
            self.assertIn("Node.js 20+", policy.published_spec("testdelta")[1])

    def test_missing_registration_reports_each_tool_without_writing(self):
        with patch.object(bridge, "find_fleet_tools", return_value=dict.fromkeys(bridge.FLEET_TOOLS)), \
             patch.object(policy, "published_spec", return_value=(None, "offline missing")), \
             patch.object(mcp, "add_servers") as add:
            result = bridge.register_fleet_tools()
        self.assertFalse(result["success"])
        self.assertEqual(set(result["errors"]), set(bridge.FLEET_TOOLS))
        add.assert_not_called()

    def test_registration_preserves_existing_same_name_and_disabled_connection(self):
        spec = {"command": "/node", "args": ["/pinned.js", "mcp"], "source": "published"}
        original = {"command": "/custom", "args": ["private"], "enabled": False,
                    "trust": "always", "secret_env": ["TOKEN"], "auth": "bearer"}
        with patch.object(mcp, "CONFIG", BASE / "registration.json"):
            mcp._save_config({"testdelta": original, "personal": {"url": "https://example.invalid"}})
            with patch.object(bridge, "find_fleet_tools", return_value={"testdelta": spec}):
                result = bridge.register_fleet_tools()
                again = bridge.register_fleet_tools()
            self.assertEqual(mcp.load_config()["testdelta"], original)
            self.assertIn("personal", mcp.load_config())
            self.assertEqual(result["preserved"], ["testdelta"])
            self.assertTrue(again["success"])

    def test_user_import_can_still_explicitly_replace(self):
        with patch.object(mcp, "CONFIG", BASE / "replace.json"):
            mcp.add_servers({"a": {"command": "/old"}})
            mcp.add_servers({"a": {"command": "/new"}})
            self.assertEqual(mcp.load_config()["a"]["command"], "/new")

    def test_fake_package_version_is_rejected(self):
        folder = BASE / "bad" / "testdelta"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "package.json").write_text(json.dumps({"name": "@reneturcios/testdelta", "version": "99.0.0"}))
        with patch.object(policy, "PACKAGE_ROOT", folder.parent), \
             patch.object(policy.shutil, "which", return_value="/node"):
            self.assertIn("Unreviewed", policy.published_spec("testdelta")[1])

    def test_expected_version_with_modified_code_is_rejected(self):
        folder = BASE / "modified" / "testdelta"
        (folder / "dist").mkdir(parents=True, exist_ok=True)
        metadata = {"name": "@reneturcios/testdelta", "version": "0.1.0",
                    "type": "module", "bin": {"testdelta": "dist/cli.js"}}
        (folder / "package.json").write_text(json.dumps(metadata))
        (folder / "dist" / "cli.js").write_text("modified code")
        manifest = {"version": "0.1.0", "files": {
            "dist/cli.js": hashlib.sha256(b"reviewed code").hexdigest()}}
        with patch.object(policy, "PACKAGE_ROOT", folder.parent), \
             patch.dict(policy.PACKAGES, {"testdelta": manifest}), \
             patch.object(policy.shutil, "which", return_value="/node"):
            self.assertIn("contents", policy.published_spec("testdelta")[1])

    def test_generated_config_uses_default_ask(self):
        spec = {"command": "/node", "args": ["/pinned.js", "mcp"], "source": "published"}
        with patch.object(bridge, "find_fleet_tools", return_value={"testdelta": spec}), \
             patch.object(mcp, "CONFIG", BASE / "new-registration.json"):
            self.assertEqual(bridge.register_fleet_tools()["registered"], ["testdelta"])
            self.assertEqual(mcp.load_config()["testdelta"]["trust"], "ask")

    def test_no_unreviewed_tools_or_custom_environments_in_readonly_list(self):
        spec = {"command": "/node", "args": ["/pinned.js", "mcp"]}
        with patch.object(policy, "published_spec", return_value=(spec, None)):
            self.assertTrue(policy.verified_read_only("testdelta", spec, "diff_test_results"))
            for changed in ({**spec, "env": {"NODE_OPTIONS": "--import=evil"}},
                            {**spec, "url": "https://example.invalid"}, {**spec, "command": "/evil"}):
                self.assertFalse(policy.verified_read_only("testdelta", changed, "diff_test_results"))
            self.assertFalse(policy.verified_read_only("reprocheck", spec, "run_repro_check"))
            self.assertFalse(policy.verified_read_only("cidiff", spec, "diff_pr_ci"))
            self.assertFalse(policy.verified_read_only("testdelta", spec, "unknown"))


class ApprovalTests(unittest.IsolatedAsyncioTestCase):
    def connection(self, server, name):
        client = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(content=[], isError=False)))
        tool = SimpleNamespace(name=name, description="fixture", inputSchema={},
                               annotations=SimpleNamespace(readOnlyHint=True))
        hub = mcp.MCPHub()
        hub.conns[server] = SimpleNamespace(name=server, client=client, tools=[tool], spec={},
                                            error=None, needs_sign_in=False)
        return hub, client

    async def test_fake_readonly_annotation_cannot_bypass_fleet_approval(self):
        hub, client = self.connection("reprocheck", "run_repro_check")
        with patch.object(mcp, "load_config", return_value={"reprocheck": {"trust": "always"}}):
            result = await hub.call("mcp__reprocheck__run_repro_check", {"command": "echo not-run"})
        self.assertTrue(result["needs_approval"])
        client.call_tool.assert_not_awaited()
        self.assertFalse(hub.read_only("mcp__reprocheck__run_repro_check"))

    async def test_unknown_fleet_tool_is_held_even_at_always(self):
        hub, client = self.connection("testdelta", "new_mutator")
        with patch.object(mcp, "load_config", return_value={"testdelta": {"trust": "always"}}):
            self.assertTrue((await hub.call("mcp__testdelta__new_mutator", {}))["needs_approval"])
        client.call_tool.assert_not_awaited()

    async def test_verified_readonly_respects_ask_and_readonly_levels(self):
        hub, client = self.connection("testdelta", "diff_test_results")
        with patch.object(policy, "verified_read_only", return_value=True):
            with patch.object(mcp, "load_config", return_value={"testdelta": {"trust": "ask"}}):
                self.assertTrue((await hub.call("mcp__testdelta__diff_test_results", {}))["needs_approval"])
            with patch.object(mcp, "load_config", return_value={"testdelta": {"trust": "read_only"}}):
                self.assertTrue((await hub.call("mcp__testdelta__diff_test_results", {}))["success"])
        client.call_tool.assert_awaited_once()

    async def test_explicit_approval_runs_previously_held_operation(self):
        hub, client = self.connection("reprocheck", "run_repro_check")
        with patch.object(mcp, "load_config", return_value={"reprocheck": {"command": "/fixture"}}):
            held = await hub.call("mcp__reprocheck__run_repro_check", {})
            done = await hub.approve(held["approval_id"])
        self.assertTrue(done["success"])
        client.call_tool.assert_awaited_once()

    async def test_other_mcp_connections_keep_existing_behavior(self):
        hub, client = self.connection("personal", "read_items")
        with patch.object(mcp, "load_config", return_value={"personal": {"trust": "read_only"}}):
            self.assertTrue((await hub.call("mcp__personal__read_items", {}))["success"])
        client.call_tool.assert_awaited_once()

    async def test_worker_envelope_uses_effective_policy(self):
        from app.services import workers
        hub, _ = self.connection("reprocheck", "run_repro_check")
        token = workers.envelope.set("read_only")
        try:
            self.assertIsNotNone(workers.allowed("mcp__reprocheck__run_repro_check",
                                                hub.read_only("mcp__reprocheck__run_repro_check")))
        finally:
            workers.envelope.reset(token)


if __name__ == "__main__":
    unittest.main()
