"""Own-Python MCP children retain the host dependency search path."""
import os
import sys
import tempfile
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
with tempfile.TemporaryDirectory(prefix="xod-child-import-") as tmp:
    os.environ.update(XOD_DATA_DIR=tmp, XOD_NO_UPDATE_CHECK="1",
                      PYTHON_KEYRING_BACKEND="tests.support.filekeyring.FileKeyring")
    from app.services.mcp_client import stdio_env
    env, command, cwd = stdio_env("computer-control", {"command": "{xod_python}"})
    result = subprocess.run([command, "-c", "import mcp; import app.computer_control_mcp"],
                            env=env, cwd=cwd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    print("PASS: own Python child imports installed MCP dependencies without changing the bundle")
