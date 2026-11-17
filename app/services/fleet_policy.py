"""Reviewed published-package identity and a deliberately narrow read-only list.

Hash checks detect unreviewed package changes, not a hostile local OS or runtime.
No package is downloaded or executed here.
"""
import hashlib
import json
from pathlib import Path
import shutil

from app.paths import APP_ROOT

PACKAGES = json.loads(Path(__file__).with_name("fleet_packages.json").read_text())
PACKAGE_ROOT = APP_ROOT / "node_modules" / "@reneturcios"
READ_ONLY = {
    "testdelta": frozenset({"diff_test_results"}),
    "skillcheck": frozenset({"check_skill"}),
    "contextbudget": frozenset({"budget_path"}),
    "reprocheck": frozenset({"detect_test_runners"}),
}


def published_spec(tool):
    """Return a validated, installed, pinned fallback or an actionable error."""
    manifest = PACKAGES.get(tool)
    if not manifest:
        return None, "Not one of Xod's five supported fleet packages."
    node = shutil.which("node")
    if not node:
        return None, "Node.js 20+ is required for the published fleet fallback."
    root = PACKAGE_ROOT / tool
    remedy = "Run npm ci --ignore-scripts in the Xod checkout, then restart Xod."
    try:
        metadata = json.loads((root / "package.json").read_text())
        if metadata.get("name") != f"@reneturcios/{tool}" or metadata.get("version") != manifest["version"]:
            return None, f"Unreviewed {tool} package version. {remedy}"
        if metadata.get("type") != "module" or metadata.get("bin", {}).get(tool) != "dist/cli.js":
            return None, f"Unexpected {tool} package interface. {remedy}"
        for rel, expected in manifest["files"].items():
            if hashlib.sha256((root / rel).read_bytes()).hexdigest() != expected:
                return None, f"Unreviewed {tool} package contents ({rel}). {remedy}"
    except FileNotFoundError:
        return None, f"Published {tool}@{manifest['version']} is not installed locally. {remedy} Offline? Use a complete cached install or provide a local CLI."
    except (OSError, ValueError, TypeError, AttributeError):
        return None, f"Cannot validate the installed {tool} package. {remedy}"
    return {
        "command": node, "args": [str(root / "dist" / "cli.js"), "mcp"],
        "source": f"published:@reneturcios/{tool}@{manifest['version']}",
    }, None


def verified_read_only(server, spec, tool):
    """Only reviewed operations on our exact published launcher qualify.

    A server name, tool annotation or version string alone is not proof.
    Local shims and customized launch environments remain approval-gated.
    """
    if tool not in READ_ONLY.get(server, ()):
        return False
    expected, _ = published_spec(server)
    if not expected or not isinstance(spec, dict):
        return False
    if any(spec.get(k) for k in ("url", "env", "secret_env", "cwd")):
        return False
    return (spec.get("command") == expected["command"]
            and spec.get("args") == expected["args"])
