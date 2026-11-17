"""Deterministic context-pressure regressions. No API keys or desktop effects."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.agent.context import tool_content


class ToolContext(unittest.TestCase):
    def test_small_result_is_unchanged(self):
        result = {"success": False, "error": "unavailable", "exit_code": 7}
        self.assertEqual(json.loads(tool_content(result, 3000)), result)

    def test_state_after_large_output_survives_initial_serialization(self):
        result = {"stdout": "x" * 40000, "stderr": "y" * 40000,
                  "success": False, "needs_approval": True, "approval_id": "hold-17",
                  "outcome": "UNKNOWN", "path": "/scratch/report.json", "exit_code": 9}
        content = tool_content(result, 3000)
        data = json.loads(content)
        for key in ("success", "needs_approval", "approval_id", "outcome", "path", "exit_code"):
            self.assertEqual(data[key], result[key])
        self.assertTrue(data["_xod_context"]["reread_required"])
        self.assertLessEqual(len(content), 3000)

    def test_nested_worker_state_keeps_its_structure(self):
        result = {"stdout": "x" * 30000, "workers": [
            {"output": "z" * 20000, "approval": {"approval_id": "hold-2"},
             "outcome": "UNKNOWN", "success": False}
        ]}
        data = json.loads(tool_content(result, 4000))
        self.assertIsInstance(data["workers"], list)
        self.assertEqual(data["workers"][0]["approval"]["approval_id"], "hold-2")
        self.assertEqual(data["workers"][0]["outcome"], "UNKNOWN")

    def test_json_string_uses_same_policy(self):
        result = {"stdout": "x" * 30000, "error": "do not retry uncertain send"}
        self.assertEqual(json.loads(tool_content(json.dumps(result), 3000))["error"], result["error"])

    def test_plain_text_keeps_both_ends_and_an_omission_notice(self):
        content = tool_content("BEGIN" + "x" * 30000 + "END", 1000)
        self.assertTrue(content.startswith("BEGIN"))
        self.assertTrue(content.endswith("END"))
        self.assertIn("reread", content)
        self.assertLessEqual(len(content), 1000)

    def test_wide_deep_and_unicode_results_are_bounded_valid_json(self):
        for result in (
            {str(i): "é\\\"" * 3000 for i in range(100)},
            [{"path": f"/{i}", "output": "x" * 4000} for i in range(100)],
            {"result": {"result": {"result": {"result": "x" * 40000}}}},
        ):
            for budget in (512, 3000, 10000):
                with self.subTest(budget=budget, kind=type(result).__name__):
                    text = tool_content(result, budget)
                    self.assertLessEqual(len(text), budget)
                    self.assertTrue(json.loads(text)["_xod_context"]["truncated"])

    def test_repeated_compaction_is_stable(self):
        content = tool_content({"stdout": "x" * 30000, "success": False}, 3000)
        for _ in range(100):
            self.assertEqual(tool_content(content, 3000), content)

    def test_invalid_tiny_budget_is_explicit(self):
        with self.assertRaises(ValueError):
            tool_content({}, 80)


if __name__ == "__main__":
    unittest.main()
