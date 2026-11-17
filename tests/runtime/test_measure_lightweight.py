"""Cheap deterministic checks for the read-only measurement collector."""
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

path = Path(__file__).with_name("measure_lightweight.py")
spec = importlib.util.spec_from_file_location("footprint", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MeasurementTests(unittest.TestCase):
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
