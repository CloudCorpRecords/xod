"""Cheap deterministic checks for the read-only measurement collector."""
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
import os
from unittest.mock import Mock, patch

path = Path(__file__).with_name("measure_lightweight.py")
spec = importlib.util.spec_from_file_location("footprint", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MeasurementTests(unittest.TestCase):
    def fixture_process(self, pid=123, created=10):
        proc = Mock()
        proc.pid = pid
        proc.create_time.return_value = created
        proc.memory_info.return_value = SimpleNamespace(rss=10 * module.MIB)
        proc.cpu_times.return_value = SimpleNamespace(user=.1, system=0)
        proc.status.return_value = "running"
        proc.ppid.return_value = 1
        proc.name.return_value = "synthetic"
        return proc

    def collect_fixture(self, trees, **changes):
        args = SimpleNamespace(
            pid=123, partner_pid=None, model_pid=None, shell_pid=None,
            port=1, settle=0, duration=3, interval=1, startup_timeout=10,
            label="synthetic-unit-test")
        vars(args).update(changes)
        with patch.object(module, "descendants", side_effect=trees), \
             patch.object(module, "api", return_value={}), \
             patch.object(module, "host_metadata", return_value={}), \
             patch.object(module.time, "monotonic", side_effect=[1, 1, 2, 2, 3, 4]), \
             patch.object(module.time, "sleep"):
            return module.collect(args, args.pid, 0)

    def test_same_attach_schema_on_mac_windows_and_linux(self):
        for platform_name in ("darwin", "win32", "linux"):
            with self.subTest(platform=platform_name), patch.object(module.sys, "platform", platform_name):
                proc = self.fixture_process()
                result = self.collect_fixture(lambda *_: [proc])
                self.assertEqual(result["schema"], 1)
                self.assertEqual(result["configuration"]["mode"], "attach")
                self.assertEqual(result["summary"]["backend"]["rss_mib"]["mean"], 10)
                self.assertEqual(result["summary"]["whole_application"]["verification"], "UNKNOWN")
                self.assertIsNone(result["summary"]["whole_application"]["rss_mib"])

    def test_collector_itself_is_excluded_from_parent_tree(self):
        parent, collector = self.fixture_process(), self.fixture_process(os.getpid())
        result = self.collect_fixture(lambda *_: [parent, collector])
        self.assertEqual(result["summary"]["observed_total"]["rss_mib"]["mean"], 10)
        self.assertNotIn(os.getpid(), [p["pid"] for p in result["process_inventory"]])

    def test_access_denied_is_unavailable_not_zero(self):
        proc = self.fixture_process()
        proc.memory_info.side_effect = module.psutil.AccessDenied(123)
        result = self.collect_fixture(lambda *_: [proc])
        self.assertIsNone(result["summary"]["backend"]["rss_mib"])
        self.assertIsNone(result["summary"]["observed_total"]["rss_mib"])
        self.assertEqual(result["summary"]["backend"]["unavailable_samples"], 3)
        self.assertIn("AccessDenied", result["samples"][0]["groups"]["backend"]["unavailable_reasons"])

    def test_missing_optional_root_invalidates_totals(self):
        proc = self.fixture_process()
        result = self.collect_fixture(lambda pid, *_: [proc] if pid == 123 else [], shell_pid=456)
        self.assertIsNone(result["summary"]["shell"]["rss_mib"])
        self.assertIsNone(result["summary"]["observed_total"]["rss_mib"])
        self.assertIsNotNone(result["summary"]["backend"]["rss_mib"])

    def test_overlapping_roots_count_each_process_once(self):
        backend, shell = self.fixture_process(), self.fixture_process(456)
        result = self.collect_fixture(
            lambda pid, *_: [shell] if pid == 456 else [backend, shell], shell_pid=456)
        self.assertEqual(result["summary"]["observed_total"]["rss_mib"]["mean"], 20)
        self.assertEqual(len(result["process_inventory"]), 2)
        self.assertEqual(next(p["group"] for p in result["process_inventory"] if p["pid"] == 456), "shell")

    def test_reused_root_pid_is_never_followed(self):
        roots = iter([[self.fixture_process(created=10)], [self.fixture_process(created=20)],
                      [self.fixture_process(created=20)]])
        result = self.collect_fixture(lambda *_: next(roots))
        self.assertEqual(result["summary"]["backend"]["unavailable_samples"], 2)
        self.assertIn("RootPIDReused", result["samples"][-1]["groups"]["backend"]["unavailable_reasons"])

    def test_electron_parent_does_not_absorb_explicit_backend(self):
        backend, shell = self.fixture_process(), self.fixture_process(456)
        result = self.collect_fixture(
            lambda pid, *_: [backend] if pid == 123 else [shell, backend], shell_pid=456)
        self.assertEqual(result["summary"]["observed_total"]["rss_mib"]["mean"], 20)
        self.assertEqual(result["summary"]["backend"]["rss_mib"]["mean"], 10)
        self.assertEqual(next(p["group"] for p in result["process_inventory"] if p["pid"] == 123), "backend")

    def test_first_cpu_sample_does_not_become_zero_in_total(self):
        proc = self.fixture_process()
        result = self.collect_fixture(lambda *_: [proc])
        self.assertEqual(result["summary"]["observed_total"]["cpu_valid_samples"], 2)
        self.assertIsNone(result["samples"][0]["groups"]["backend"]["cpu_percent_one_core"])

    def test_tree_access_denied_records_reason(self):
        reasons = []
        with patch.object(module.psutil, "Process", side_effect=module.psutil.AccessDenied(123)):
            self.assertEqual(module.descendants(123, reasons), [])
        self.assertEqual(reasons, ["AccessDenied"])

    def test_attach_does_not_launch_or_signal_user_process(self):
        import sys
        with module.tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            argv = ["collector", "--pid", "123", "--duration", "1", "--output", str(output)]
            with patch.object(sys, "argv", argv), patch.object(module, "collect", return_value={
                "label": "fixture", "ready_s": None, "host": {}, "summary": {}, "approval_state": {}
            }), patch.object(module.subprocess, "Popen") as launch, \
                    patch.object(module.os, "killpg", create=True) as kill:
                module.main()
            self.assertTrue(output.exists())
            launch.assert_not_called()
            kill.assert_not_called()

    def test_empty_series_is_unknown(self):
        self.assertIsNone(module.describe([]))

    def test_nearest_rank_p95_preserves_spikes(self):
        self.assertEqual(module.percentile(list(range(1, 101)), .95), 95)

    def test_statistics_include_variance(self):
        result = module.describe([1, 2, 3])
        self.assertEqual(result["mean"], 2)
        self.assertEqual(result["median"], 2)
        self.assertEqual(result["max"], 3)
        self.assertGreater(result["stdev"], 0)

    def test_disappeared_process_is_not_an_error(self):
        with patch.object(module.psutil, "Process", side_effect=module.psutil.NoSuchProcess(999)):
            self.assertEqual(module.descendants(999), [])

    def test_process_tree_includes_root(self):
        with patch.object(module.psutil, "Process") as factory:
            root = factory.return_value
            root.children.return_value = ["child"]
            self.assertEqual(module.descendants(12), [root, "child"])
            root.children.assert_called_once_with(recursive=True)

    def test_isolated_child_does_not_use_file_keyring(self):
        source = path.read_text()
        self.assertIn("keyring.backends.null.Keyring", source)
        self.assertNotIn("filekeyring", source)
        self.assertNotIn("os.environ.copy()", source)

    def test_no_inference_in_launcher(self):
        self.assertNotIn("chat_completion", module.CHILD)
        self.assertIn('set_setting("muse_auto_approve", "on")', module.CHILD)

    def test_cpu_uses_one_core_and_ignores_first_sample(self):
        proc = Mock()
        proc.pid = 123
        proc.create_time.return_value = 10
        proc.memory_info.return_value = SimpleNamespace(rss=10 * module.MIB)
        proc.cpu_times.side_effect = [
            SimpleNamespace(user=value, system=0) for value in (.1, .2, .4)]
        proc.ppid.return_value = 1
        proc.name.return_value = "fixture"
        args = SimpleNamespace(
            pid=123, partner_pid=None, model_pid=None, shell_pid=None,
            port=1, settle=0, duration=3, interval=1,
            startup_timeout=10, label="synthetic-metric-unit-test")
        with patch.object(module, "descendants", return_value=[proc]), \
             patch.object(module.psutil, "pid_exists", return_value=True), \
             patch.object(module.psutil, "Process"), \
             patch.object(module, "api", return_value={}), \
             patch.object(module, "host_metadata", return_value={}), \
             patch.object(module.time, "monotonic", side_effect=[1, 1, 2, 2, 3, 4]), \
             patch.object(module.time, "sleep"):
            result = module.collect(args, 123, 0)
        cpu = result["summary"]["backend"]["cpu_percent_one_core"]
        self.assertAlmostEqual(cpu["mean"], 15)
        self.assertAlmostEqual(cpu["max"], 20)
        self.assertIsNone(result["samples"][0]["groups"]["backend"]["cpu_percent_one_core"])
        self.assertEqual(result["summary"]["backend"]["rss_mib"]["mean"], 10)
        self.assertIsNone(result["ready_s"])  # Attachment is not startup timing.
        self.assertEqual(result["configuration"]["missing_components"], ["partner", "model", "shell"])


if __name__ == "__main__":
    unittest.main()
