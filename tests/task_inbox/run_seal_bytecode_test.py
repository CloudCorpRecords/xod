"""
AGE-78 regression test: no bundled-python child may write bytecode into the app bundle.

Root cause (proven live on the v1.1.8 Mac install, record
~/workspace/xod-eval/V118-GATE-2026-11-14.md): the packaged bundle ships
prebuilt .pyc files whose timestamps the install rewrites, so every shipped
.pyc is stale on first launch. The backend itself is safe: electron's
serverEnv() hands it PYTHONDONTWRITEBYTECODE=1. But the MCP stdio layer
(app/services/mcp_client.py) builds each server's environment from scratch
(a small allowlist), dropping the flag, so Xod's own python children (the
built-in Mac connector app.connectors.mac, app.mcp_server, app.phone_mcp)
imported the sealed tree and rewrote 140 .pyc files inside the bundle
within the first minute. A bundle with an invalid seal is not the identity
TCC granted, so Accessibility and Screen Recording grants could never take
effect, which blocks the LF-NAMED-SURFACE family (AGE-71).

Scope note: this suite is about grants sticking WITHIN one installed build.
The separate AGE-57 problem (ad-hoc cdhash-only signing identity, so grants
stale across builds) is out of scope here and is not touched.

What is proven, without any live install:
  - behavioral: the environment mcp_client builds for Xod's own python
    servers carries PYTHONDONTWRITEBYTECODE=1, and a child spawned with
    exactly that env imports a stale-.pyc package from a bundle-like tree
    and writes nothing into it (with a control run proving the same child
    without the flag does rewrite the .pyc, so the probe can see writes);
  - static: every subprocess spawn site in app/ whose child is (or may be)
    the app's own python carries the flag in its env (or -B in argv),
    including the MCP config snippets Xod hands to other apps, the
    terminal `xod` shim, and electron's python spawn helpers. Foreign
    children (osascript, git, ffmpeg, other apps' binaries) are listed
    and passed as foreign; app/vendor is excluded because that module is
    provisioned onto other devices and runs under the device's python.

Run: .venv/bin/python tests/task_inbox/run_seal_bytecode_test.py
"""
import ast
import hashlib
import importlib.util
import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_seal_"))
os.environ.pop("XOD_PACKAGED", None)
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"),
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"),
                   "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


# ---------------------------------------------------------------- behavioral

def _snapshot(tree: Path):
    snap = {}
    for p in sorted(tree.rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(tree))] = (p.stat().st_size, p.stat().st_mtime_ns,
                                              hashlib.sha256(p.read_bytes()).hexdigest())
    return snap


def _stub_mcp_sdk():
    """stdio_env imports get_default_environment from the MCP SDK at call
    time. The gate venv does not carry the SDK; stub just that one function
    (a plain dict of safe default vars, like the real one) so the env
    construction itself is what is under test."""
    try:
        import mcp.client.stdio  # noqa: F401
        return
    except ImportError:
        pass
    import types
    mcp = types.ModuleType("mcp")
    client = types.ModuleType("mcp.client")
    stdio = types.ModuleType("mcp.client.stdio")

    def get_default_environment():
        return {"PATH": os.environ.get("PATH", ""), "HOME": os.path.expanduser("~"),
                "USER": os.environ.get("USER", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}

    stdio.get_default_environment = get_default_environment
    client.stdio = stdio
    mcp.client = client
    sys.modules.update({"mcp": mcp, "mcp.client": client, "mcp.client.stdio": stdio})


def test_stdio_env_and_launch():
    _stub_mcp_sdk()
    from app.services import mcp_client
    from app.services.connectors import CATALOG
    from app.paths import APP_ROOT

    if not hasattr(mcp_client, "stdio_env"):
        for name in ("stdio_env resolves {xod_python} to this interpreter",
                     "stdio_env runs Xod's own connectors from the app root",
                     "stdio_env gives Xod's own python PYTHONDONTWRITEBYTECODE=1",
                     "stdio_env flags a sys.executable MCP server (app.mcp_server) too",
                     "stdio_env does not force the flag on a foreign server outside a packaged run",
                     "probe child runs under the connectors env",
                     "child spawned with the connectors env writes nothing into the bundle tree",
                     "control: the same child without the flag does rewrite the stale .pyc"):
            check(name, False, "mcp_client.stdio_env does not exist; the stdio env is built inline and drops the flag")
        return

    mac = next(c for c in CATALOG if c["id"] == "mac")
    env, command, cwd = mcp_client.stdio_env("mac", {"command": mac["command"], "args": mac["args"]})
    check("stdio_env resolves {xod_python} to this interpreter", command == sys.executable, command)
    check("stdio_env runs Xod's own connectors from the app root", cwd == str(APP_ROOT), str(cwd))
    check("stdio_env gives Xod's own python PYTHONDONTWRITEBYTECODE=1",
          env.get("PYTHONDONTWRITEBYTECODE") == "1", repr(env.get("PYTHONDONTWRITEBYTECODE")))

    env2, command2, _ = mcp_client.stdio_env("xod", {"command": sys.executable, "args": ["-m", "app.mcp_server"]})
    check("stdio_env flags a sys.executable MCP server (app.mcp_server) too",
          command2 == sys.executable and env2.get("PYTHONDONTWRITEBYTECODE") == "1",
          repr(env2.get("PYTHONDONTWRITEBYTECODE")))

    env3, _, _ = mcp_client.stdio_env("docs", {"command": "npx", "args": ["-y", "some-server"]})
    check("stdio_env does not force the flag on a foreign server outside a packaged run",
          env3.get("PYTHONDONTWRITEBYTECODE") is None, repr(env3.get("PYTHONDONTWRITEBYTECODE")))

    # A bundle-like tree: one package whose shipped .pyc is stale, exactly
    # what a ditto/DMG install leaves behind (source mtime moved after the
    # .pyc was built).
    bundle = TMP / "bundle"
    pkg = bundle / "sealprobe"
    pkg.mkdir(parents=True)
    src = pkg / "__init__.py"
    src.write_text("VALUE = 1\n")
    pyc = Path(importlib.util.cache_from_source(str(src)))
    pyc.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(str(src), cfile=str(pyc), doraise=True)
    st = src.stat()
    os.utime(src, (st.st_atime, st.st_mtime + 10))     # now the shipped .pyc is stale

    child_env = {**env, "PYTHONPATH": str(bundle)}
    before = _snapshot(bundle)
    r = subprocess.run([sys.executable, "-c", "import sealprobe; print(sealprobe.VALUE)"],
                       env=child_env, capture_output=True, text=True, timeout=60)
    check("probe child runs under the connectors env", r.returncode == 0 and r.stdout.strip() == "1",
          (r.stderr or "")[-200:])
    check("child spawned with the connectors env writes nothing into the bundle tree",
          _snapshot(bundle) == before)

    bare_env = {k: v for k, v in child_env.items() if k != "PYTHONDONTWRITEBYTECODE"}
    r2 = subprocess.run([sys.executable, "-c", "import sealprobe; print(sealprobe.VALUE)"],
                        env=bare_env, capture_output=True, text=True, timeout=60)
    check("control: the same child without the flag does rewrite the stale .pyc",
          r2.returncode == 0 and _snapshot(bundle) != before)


# ---------------------------------------------------------------- static audit

SPAWN_ATTRS = {"Popen", "run", "call", "check_call", "check_output",
               "create_subprocess_exec", "create_subprocess_shell"}
FLAG = "PYTHONDONTWRITEBYTECODE"
OWN_MARKERS = ("sys.executable", "VENV_PYTHON", "_venv_python", "{xod_python}")
PARAM_INTERPRETERS = {"py", "base", "own", "active_py", "venv_py", "python", "interpreter"}


def _seg(text, node):
    try:
        return ast.get_source_segment(text, node) or ""
    except Exception:
        return ""


def _func_of(parents, node):
    for p in reversed(parents.get(node, [])):
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return p
    return None


def _ancestor_map(tree):
    parents = {}

    def rec(node, stack):
        parents[node] = stack
        for child in ast.iter_child_nodes(node):
            rec(child, stack + [node])

    rec(tree, [])
    return parents


def _assignments(func):
    out = {}
    if func is None:
        return out
    for n in ast.walk(func):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            out.setdefault(n.targets[0].id, []).append(n.value)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
            out.setdefault(n.target.id, []).append(n.value)
    return out


def _builder_has_flag(root_name, attr, cache):
    """Does the env-builder function <root_name>.<attr> (or bare <attr>) put the flag in its env?"""
    key = (root_name, attr)
    if key in cache:
        return cache[key]
    found = False
    candidates = []
    if root_name:
        candidates.append(ROOT / "app" / "services" / f"{root_name}.py")
        candidates.append(ROOT / "app" / f"{root_name}.py")
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text(errors="replace")
        tree = ast.parse(text)
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == attr:
                found = FLAG in _seg(text, n)
    cache[key] = found
    return found


def _env_has_flag(node, func, text, cache, depth=0):
    """True when the env expression guarantees PYTHONDONTWRITEBYTECODE=1."""
    if node is None or depth > 4:
        return False
    if isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value == FLAG:
                return True
            if k is None and _env_has_flag(v, func, text, cache, depth + 1):
                return True
        return False
    if isinstance(node, ast.Call):
        fname = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if fname == "dict":
            if any(kw.arg == FLAG for kw in node.keywords):
                return True
            return any(_env_has_flag(a, func, text, cache, depth + 1) for a in node.args)
        root = node.func.value.id if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) else None
        attr = node.func.attr if isinstance(node.func, ast.Attribute) else fname
        if attr and _builder_has_flag(root, attr, cache):
            return True
        return False
    if isinstance(node, ast.Name):
        for value in _assignments(func).get(node.id, []):
            if _env_has_flag(value, func, text, cache, depth + 1):
                return True
        if func is not None:   # env["PYTHONDONTWRITEBYTECODE"] = "1" after construction
            for n in ast.walk(func):
                if isinstance(n, ast.Assign):
                    for t in n.targets:
                        if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                                and t.value.id == node.id and isinstance(t.slice, ast.Constant)
                                and t.slice.value == FLAG):
                            return True
        return False
    if isinstance(node, ast.BinOp):     # env | {...} style merges
        return _env_has_flag(node.left, func, text, cache, depth + 1) or _env_has_flag(node.right, func, text, cache, depth + 1)
    return False


def _argv_own_python(node, func, text, depth=0):
    """'own' when the spawned argv is (or resolves to) the app's own python, else 'foreign'."""
    if node is None or depth > 4:
        return "foreign"
    if isinstance(node, ast.Starred):
        return _argv_own_python(node.value, func, text, depth + 1)
    seg = _seg(text, node)
    if any(m in seg for m in OWN_MARKERS) and not isinstance(node, (ast.Name,)):
        return "own"
    if isinstance(node, (ast.List, ast.Tuple)):
        if node.elts and _argv_own_python(node.elts[0], func, text, depth + 1) == "own":
            return "own"
        return "foreign"
    if isinstance(node, ast.Name):
        if node.id in PARAM_INTERPRETERS:
            return "own"      # an interpreter parameter in this codebase resolves to sys.executable candidates
        for value in _assignments(func).get(node.id, []):
            if _argv_own_python(value, func, text, depth + 1) == "own":
                return "own"
        return "foreign"
    if isinstance(node, ast.Call):
        callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if callee == "_venv_python":
            return "own"      # a venv made from the app's interpreter still runs the bundled interpreter
        if func is not None:
            for n in ast.walk(ast.parse(text)):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == callee:
                    if any(m in _seg(text, n) for m in OWN_MARKERS):
                        return "own"
        return "foreign"
    if isinstance(node, ast.JoinedStr):
        return "own" if any(m in seg for m in OWN_MARKERS) else "foreign"
    return "foreign"


def test_spawn_site_audit():
    cache = {}
    files = [p for p in sorted((ROOT / "app").rglob("*.py"))
             if "__pycache__" not in p.parts and "vendor" not in p.parts]
    sites = 0
    for path in files:
        text = path.read_text(errors="replace")
        tree = ast.parse(text)
        parents = _ancestor_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            attr = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if attr not in SPAWN_ATTRS:
                continue
            root = node.func.value.id if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) else ""
            if root not in ("subprocess", "asyncio", ""):
                continue
            sites += 1
            func = _func_of(parents, node)
            rel = f"{path.relative_to(ROOT)}:{node.lineno}"
            env_node = next((kw.value for kw in node.keywords if kw.arg == "env"), None)
            if _env_has_flag(env_node, func, text, cache):
                check(f"spawn {rel} env carries {FLAG}", True)
                continue
            argv = node.args[0] if node.args else None
            if argv is not None and '"-B"' in _seg(text, argv):
                check(f"spawn {rel} argv carries -B", True)
                continue
            own = _argv_own_python(argv, func, text)
            if own != "own" and attr == "create_subprocess_shell" and func is not None \
                    and "sys.executable" in _seg(text, func):
                own = "own"     # a shell whose function rewrites commands onto sys.executable (system_control)
            check(f"spawn {rel} is a foreign child or inherits a flagged env", own != "own",
                  "bundled-python child without PYTHONDONTWRITEBYTECODE")
    check("audit found spawn sites to judge", sites > 50, f"sites={sites}")


def test_mcp_snippets_carry_flag():
    # The configs Xod hands to OTHER MCP apps spawn the bundled python with
    # exactly the env dict written here, so the flag has to be in the dict.
    for relpath, funcname in (("app/main.py", "mcp_self_config_endpoint"),
                              ("app/services/auto_connect.py", "mcp_entry")):
        text = (ROOT / relpath).read_text(errors="replace")
        tree = ast.parse(text)
        entries = []
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == funcname:
                for d in ast.walk(n):
                    if isinstance(d, ast.Dict):
                        keys = [k.value for k in d.keys if isinstance(k, ast.Constant)]
                        if "command" in keys and "executable" in _seg(text, d):
                            env_keys = []
                            for k, v in zip(d.keys, d.values):
                                if isinstance(k, ast.Constant) and k.value == "env" and isinstance(v, ast.Dict):
                                    env_keys = [kk.value for kk in v.keys if isinstance(kk, ast.Constant)]
                            entries.append(env_keys)
        check(f"{relpath} {funcname} hands out {len(entries)} own-python MCP entr(ies)", len(entries) >= 1,
              f"entries={entries}")
        for env_keys in entries:
            check(f"{relpath} {funcname} snippet env carries {FLAG}", FLAG in env_keys, f"env keys={env_keys}")


def test_electron_spawns():
    text = (ROOT / "electron" / "main.js").read_text(errors="replace")

    def window(start_marker, size):
        i = text.find(start_marker)
        return text[i:i + size] if i >= 0 else ""

    check("electron serverEnv already flags the backend spawn (control)",
          FLAG in window("function serverEnv", 400))
    check("electron run() helper flags the python children it launches",
          FLAG in window("function run(cmd, args", 400))
    check("electron findPython probe spawns carry the flag",
          FLAG in window("function findPython", 500))
    i = text.find('spawnSync(PY, ["-c", "import sys"]')
    check("electron ensurePythonEnv probe of the bundled python carries the flag",
          i >= 0 and FLAG in text[i:i + 220], text[i:i + 120] if i >= 0 else "call not found")


def test_terminal_shim():
    text = (ROOT / "app" / "services" / "terminal_command.py").read_text(errors="replace")
    check("terminal `xod` shim exports the flag for the packaged POSIX launcher",
          f"{FLAG}=1" in text and "PYTHONUTF8=1" in text)
    check("terminal `xod` shim sets the flag for the packaged Windows launcher",
          f"set {FLAG}=1" in text)


def main():
    test_stdio_env_and_launch()
    test_spawn_site_audit()
    test_mcp_snippets_carry_flag()
    test_electron_spawns()
    test_terminal_shim()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + "; ".join(failed), flush=True)
        sys.exit(1)
    print("PASS: no bundled-python child can write bytecode into the bundle (AGE-78)", flush=True)


if __name__ == "__main__":
    main()
