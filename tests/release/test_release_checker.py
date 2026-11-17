"""Narrow public-scope exception; all other release protections remain active."""
import ast
import contextlib
import importlib.util
import io
from pathlib import Path
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_checker", ROOT / "scripts/check_release.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
POLICY = "app/services/fleet_policy.py"
scope = next(n.value for n in ast.walk(ast.parse((ROOT / POLICY).read_text()))
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and n.value.startswith("@") and "/" not in n.value)


class ReleaseChecker(unittest.TestCase):
    def check_text(self, text, path=POLICY, size=None):
        out = io.StringIO()
        real_open = open
        def fixture_open(name, *args, **kwargs):
            return io.StringIO(text) if str(name) == path else real_open(ROOT / name, *args, **kwargs)
        with patch.object(checker, "files", return_value=[path]), \
                patch.object(checker.getpass, "getuser", return_value=scope[1:]), \
                patch.object(checker.os.path, "expanduser", return_value="/fixture/build-home"), \
                patch.object(checker.os.path, "isfile", return_value=True), \
                patch.object(checker.os.path, "getsize", return_value=len(text) if size is None else size), \
                patch.object(checker.subprocess, "run", return_value=types.SimpleNamespace(stdout="")), \
                patch("builtins.open", side_effect=fixture_open), \
                patch.object(checker.sys, "argv", ["check_release.py"]), \
                contextlib.redirect_stdout(out):
            result = checker.main()
        return result, out.getvalue()

    def test_exact_public_scope_in_policy_is_allowed(self):
        self.assertEqual(self.check_text(f'PACKAGE_ROOT = "{scope}"')[0], 0)

    def test_same_literal_in_another_file_is_not_exempt(self):
        self.assertEqual(self.check_text(f'PACKAGE_ROOT = "{scope}"', "app/services/other.py")[0], 1)

    def test_unrelated_username_in_policy_is_not_exempt(self):
        self.assertEqual(self.check_text(f'OWNER = "{scope[1:]}"')[0], 1)

    def test_credential_in_policy_is_not_exempt(self):
        code, output = self.check_text('API_KEY = "' + "sk-" + "AbcDef0123456789" * 3 + '"')
        self.assertEqual(code, 1)
        self.assertIn("OpenAI-style key", output)

    def test_forbidden_paths_are_not_exempt(self):
        self.assertEqual(self.check_text('x = 1', "data/fixture.py")[0], 1)

    def test_file_size_limit_is_not_exempt(self):
        self.assertEqual(self.check_text('x = 1', size=checker.MAX_BYTES + 1)[0], 1)


if __name__ == "__main__":
    unittest.main()
