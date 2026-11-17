"""No credentials or provider calls: check the expanded live evaluation input."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests/runtime/live_agent_eval.py"
spec = importlib.util.spec_from_file_location("live_eval_inputs", HARNESS)
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class LiveEvalInputs(unittest.TestCase):
    def test_default_preserves_original_900_record_case(self):
        text, expected = live.long_case()
        records = [json.loads(s) for s in text.splitlines() if s.startswith("{")]
        self.assertEqual(len(records), 900)
        self.assertEqual(expected["reference_record"], 449)
        self.assertEqual(expected["reference_value"], 952)

    def test_larger_case_has_real_records_and_positioned_contracts(self):
        text, expected = live.long_case(1800)
        lines = text.splitlines()
        records = [json.loads(s) for s in lines if s.startswith("{")]
        self.assertEqual([r["record"] for r in records], list(range(1800)))
        self.assertTrue(lines[0].startswith("MISSION CONTRACT:"))
        self.assertIn("MIDPOINT CONTRACT:", lines[901])
        self.assertTrue(lines[-1].startswith("FINAL AMENDMENT:"))
        self.assertEqual(expected["reference_record"], 899)
        self.assertEqual(expected["reference_value"], records[899]["units"] * records[899]["unit_cost"])
        self.assertEqual(expected["spending_ceiling"], 42)
        self.assertGreater(len(text), 350000)  # Characters are not asserted to be tokens.

    def test_invalid_limits_and_wrong_case_refuse_before_credentials(self):
        for flags in (["--long-records", "2501"], ["--min-input-tokens", "29999"],
                      ["--max-input-characters", "2000001"], ["--case", "fleet", "--long-records", "1800"]):
            with self.subTest(flags=flags):
                proc = subprocess.run(
                    [sys.executable, str(HARNESS), "--live", "--case", "long",
                     "--output", "unused.json", *flags],
                    cwd=ROOT, env={}, capture_output=True, text=True, timeout=15)
                self.assertEqual(proc.returncode, 2)
                self.assertNotIn("OPENAI_API_KEY is unavailable", proc.stderr)

    def test_live_consent_is_still_required(self):
        proc = subprocess.run([sys.executable, str(HARNESS), "--case", "long",
                               "--output", "unused.json"], cwd=ROOT, env={},
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--live is required", proc.stderr)


if __name__ == "__main__":
    unittest.main()
