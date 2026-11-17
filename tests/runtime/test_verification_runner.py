"""Negative controls for the local verifier: failed tests must fail the report."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("verify_agent", ROOT / "scripts/verify_agent.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class VerificationRunner(unittest.TestCase):
    def test_nonzero_process_cannot_be_reported_as_passed(self):
        failed = subprocess.CompletedProcess([], 1, "", "Ran 5 tests\nFAILED (failures=1)")
        with patch.object(runner.subprocess, "run", return_value=failed):
            result = runner.run_check(("fixture", "fixture.py"), 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["unittest_count"], 5)

    def test_timeout_is_not_success(self):
        with patch.object(runner.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 1)):
            result = runner.run_check(("fixture", "fixture.py"), 1)
        self.assertEqual(result["status"], "timed_out")
        self.assertIsNone(result["exit_code"])

    def test_missing_runtime_is_explicit(self):
        with patch.object(runner.subprocess, "run", side_effect=FileNotFoundError):
            result = runner.run_check(("fixture", "fixture.py"), 1)
        self.assertEqual(result["status"], "unavailable")

    def test_selected_profile_is_passed_only_to_child_environment(self):
        with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            result = runner.run_check(("fixture", "fixture.py"), 1, "correctness")
        self.assertEqual(run.call_args.kwargs["env"]["XOD_TEST_CONTEXT_PROFILE"], "correctness")
        self.assertEqual(result["profile"], "correctness")
        self.assertEqual(result["status"], "passed")


if __name__ == "__main__":
    unittest.main()
