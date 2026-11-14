"""
The asar require-resolution checker (scripts/check_asar.py) proves both ways.
Release 1.1.3 (2026-11-13) shipped an app.asar whose main.js requires ./liveness at line 8
while the packaging allowlist left liveness.js out of the archive, so the app died at
startup on every platform and the release was still reported verified. The checker is now
a release gate step; this test pins its behavior: a fixture with a missing require fails
and names the module, a complete fixture passes, and a non-archive is a clean error.

Run: python3 tests/smoke/run_check_asar_test.py
"""
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts" / "check_asar.py"


def _pad4(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 4)


def write_asar(path: Path, files: dict):
    """Minimal asar writer for fixtures: header tree plus concatenated file bytes."""
    offsets, data, pos = {}, b"", 0
    for rel in sorted(files):
        offsets[rel] = pos
        data += files[rel]
        pos += len(files[rel])
    tree = {"files": {}}
    for rel, content in files.items():
        node = tree["files"]
        parts = rel.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {"files": {}})["files"]
        node[parts[-1]] = {"size": len(content), "offset": str(offsets[rel])}
    js = json.dumps(tree, separators=(",", ":")).encode()
    header = struct.pack("<I", 4 + len(_pad4(js))) + struct.pack("<I", len(js)) + _pad4(js)
    size = struct.pack("<I", 4) + struct.pack("<I", len(header))
    path.write_bytes(size + header + data)


def run_checker(target: Path):
    out = subprocess.run([sys.executable, str(CHECKER), str(target)],
                         capture_output=True, text=True, timeout=60)
    return out.returncode, out.stdout + out.stderr


PKG = b'{"name":"fixture","main":"main.js"}'
tmp = Path(tempfile.mkdtemp())
cases = []

# 1. The 1.1.3 shape: main.js requires a module the archive does not carry.
broken = tmp / "broken.asar"
write_asar(broken, {
    "package.json": PKG,
    "main.js": b'const x = require("./liveness");\n',
})
code, out = run_checker(broken)
cases.append(("missing require fails the check", code == 1))
cases.append(("missing require is named in the output", "./liveness" in out and "main.js:1" in out))

# 2. A complete fixture passes, including folder (index.js) resolution.
complete = tmp / "complete.asar"
write_asar(complete, {
    "package.json": PKG,
    "main.js": b'const a = require("./liveness");\nconst b = require("./sub");\n',
    "liveness.js": b"module.exports = {};\n",
    "sub/index.js": b'const c = require("../liveness");\nmodule.exports = {};\n',
})
code, out = run_checker(complete)
cases.append(("complete fixture passes", code == 0))

# 3. A transitively missing module is found and blamed on the file that requires it.
transitive = tmp / "transitive.asar"
write_asar(transitive, {
    "package.json": PKG,
    "main.js": b'const a = require("./a");\n',
    "a.js": b'const b = require("./b");\n',
})
code, out = run_checker(transitive)
cases.append(("transitive missing require fails", code == 1 and "a.js:1" in out and "./b" in out))

# 4. A file that is not an archive is a usage error, not a pass and not a crash.
not_asar = tmp / "not-an-archive.bin"
not_asar.write_bytes(b"this is not an asar")
code, out = run_checker(not_asar)
cases.append(("non-archive exits 2 without a traceback", code == 2 and "Traceback" not in out))

ok = 0
for name, passed in cases:
    print(f"  {'PASS' if passed else 'FAIL'} {name}")
    ok += passed
print(f"\n{ok}/{len(cases)} checks passed")
sys.exit(0 if ok == len(cases) else 1)
