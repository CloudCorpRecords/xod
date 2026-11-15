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
            argv = node.args[0] if node.args else None
            argv_seg = _seg(text, argv) if argv is not None else ""
            own = _argv_own_python(argv, func, text)
            if own == "own" and ('"-I"' in argv_seg or '"-E"' in argv_seg):
                check(f"spawn {rel} isolated/ignore-env argv carries -B", '"-B"' in argv_seg,
                      "isolated (-I/-E) python child ignores PYTHONDONTWRITEBYTECODE without -B")
                continue
            if _env_has_flag(env_node, func, text, cache):
                check(f"spawn {rel} env carries {FLAG}", True)
                continue
            if argv is not None and '"-B"' in argv_seg:
                check(f"spawn {rel} argv carries -B", True)
                continue
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


def test_bundle_python_build_seal():
    import struct
    bp = (ROOT / "electron" / "scripts" / "bundle-python.js").read_text(errors="replace")
    check("bundle-python.js sh() helper sets PYTHONDONTWRITEBYTECODE=1 and passes -B",
          'execFileSync(py, ["-B", ...args]' in bp and f'{FLAG}: "1"' in bp)
    check("bundle-python.js version and license python calls set PYTHONDONTWRITEBYTECODE=1",
          bp.count(f'{FLAG}: "1"') >= 3)
    check("bundle-python.js writes sitecustomize.py enforcing sys.dont_write_bytecode = True",
          "sitecustomize.py" in bp and "sys.dont_write_bytecode = True" in bp)
    check("bundle-python.js compileall passes -f (--force) with --invalidation-mode unchecked-hash",
          '"compileall", "-f"' in bp and '"--invalidation-mode", "unchecked-hash"' in bp)

    # Behavioral proof of the v1.1.8 (140 files) / v1.1.9 (64 files) build-time root cause:
    # pip writes timestamp-based .pyc files (flags=0). Without -f, compileall --invalidation-mode
    # unchecked-hash skips them, leaving flags=0 so an unflagged import after install rewrites them.
    # With -f, compileall converts them to unchecked-hash (flags=3), which Python never rewrites.
    cdir = TMP / "compileall_probe"
    cdir.mkdir(parents=True, exist_ok=True)
    mod = cdir / "pip_touched.py"
    mod.write_text("X = 42\n")
    pyc = Path(importlib.util.cache_from_source(str(mod)))
    pyc.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(str(mod), cfile=str(pyc), doraise=True)  # timestamp-based (flags=0), as pip writes
    flags_initial = struct.unpack("<4sL", pyc.read_bytes()[:8])[1]
    subprocess.run([sys.executable, "-B", "-m", "compileall", "-q", "--invalidation-mode", "unchecked-hash", str(cdir)],
                   check=True, capture_output=True)
    flags_no_force = struct.unpack("<4sL", pyc.read_bytes()[:8])[1]
    check("control: compileall without -f skips pre-existing timestamp .pyc from pip (flags stays 0)",
          flags_initial == 0 and flags_no_force == 0, f"initial={flags_initial} no_force={flags_no_force}")

    subprocess.run([sys.executable, "-B", "-m", "compileall", "-f", "-q", "--invalidation-mode", "unchecked-hash", str(cdir)],
                   check=True, capture_output=True)
    flags_forced = struct.unpack("<4sL", pyc.read_bytes()[:8])[1]
    check("compileall with -f converts pre-existing timestamp .pyc to unchecked-hash (flags == 1)",
          flags_forced == 1, f"flags_forced={flags_forced}")

    st = mod.stat()
    os.utime(mod, (st.st_atime, st.st_mtime + 20))  # simulate electron-builder / ditto copy advancing .py mtime
    before_Forced = _snapshot(cdir)
    bare_env = {k: v for k, v in os.environ.items() if k != FLAG}
    r = subprocess.run([sys.executable, "-c", "import pip_touched; print(pip_touched.X)"],
                       cwd=str(cdir), env=bare_env, capture_output=True, text=True, timeout=30)
    check("unchecked-hash .pyc is never rewritten at runtime even by an unflagged child after mtime shift",
          r.returncode == 0 and r.stdout.strip() == "42" and _snapshot(cdir) == before_Forced)


def test_isolated_pytest_probe_and_run_claims():
    tq_text = (ROOT / "app" / "services" / "task_queue.py").read_text(errors="replace")
    check("task_queue._python_can_pytest passes -B alongside -I (since -I ignores PYTHONDONTWRITEBYTECODE)",
          '[py, "-I", "-B", "-c", "import pytest"]' in tq_text)
    check("task_queue._provision_pytest_venv passes -B to venv and pip --no-compile",
          '[base, "-B", "-m", "venv"' in tq_text and '[py, "-B", "-m", "pip", "install", "--no-compile"' in tq_text)
    check("task_queue._check_run_claim passes -B when rewriting python/pytest invocations",
          '[py, "-B"] + argv[1:]' in tq_text and '[active_py, "-B", *argv[1:]]' in tq_text
          and '[py or active_py, "-B", "-m", "pytest", *argv[1:]]' in tq_text)

    # Behavioral proof that CPython's -I (isolated mode) ignores PYTHONDONTWRITEBYTECODE=1 in env
    # unless -B is also passed in argv.
    ibundle = TMP / "isolated_probe"
    ibundle.mkdir(parents=True, exist_ok=True)
    # Put a module inside a site-packages dir or cwd and test -E / -I behavior on sys.dont_write_bytecode:
    env_flagged = {**os.environ, FLAG: "1"}
    r_no_b = subprocess.run([sys.executable, "-I", "-c", "import sys; print(sys.dont_write_bytecode)"],
                            env=env_flagged, capture_output=True, text=True, timeout=30)
    r_with_b = subprocess.run([sys.executable, "-I", "-B", "-c", "import sys; print(sys.dont_write_bytecode)"],
                              env=env_flagged, capture_output=True, text=True, timeout=30)
    check("control: python -I ignores PYTHONDONTWRITEBYTECODE=1 in env (sys.dont_write_bytecode is False)",
          r_no_b.returncode == 0 and r_no_b.stdout.strip() == "False", r_no_b.stdout.strip())
    check("python -I -B enforces sys.dont_write_bytecode == True",
          r_with_b.returncode == 0 and r_with_b.stdout.strip() == "True", r_with_b.stdout.strip())


def test_missed_runtime_spawns_and_envs():
    sc_text = (ROOT / "app" / "services" / "system_control.py").read_text(errors="replace")
    check("system_control._spawn_tool_exec forces PYTHONDONTWRITEBYTECODE=1 in child env",
          'kwargs["env"] = {**os.environ, **(kwargs.get("env") or {}), "PYTHONDONTWRITEBYTECODE": "1"}' in sc_text)
    check("system_control._shell rewrites bare pytest with -B",
          "{py_q} -B -m pytest" in sc_text)

    scw_text = (ROOT / "app" / "services" / "system_control_win.py").read_text(errors="replace")
    check("system_control_win.exec_shell and _ps force PYTHONDONTWRITEBYTECODE=1 in child env",
          scw_text.count(f'"{FLAG}": "1"') >= 2)

    from app.services import agent_clis
    ac_env = agent_clis._env()
    check("agent_clis._env() sets PYTHONDONTWRITEBYTECODE=1 (its PATH prepends bundled python bin)",
          ac_env.get(FLAG) == "1", repr(ac_env.get(FLAG)))
    ac_text = (ROOT / "app" / "services" / "agent_clis.py").read_text(errors="replace")
    check("agent_clis.search_path() login-shell probe sets PYTHONDONTWRITEBYTECODE=1",
          'clean_env["PYTHONDONTWRITEBYTECODE"] = "1"' in ac_text)

    op_text = (ROOT / "app" / "services" / "onepassword.py").read_text(errors="replace")
    check("onepassword._run sets PYTHONDONTWRITEBYTECODE=1 alongside search_path()",
          "PYTHONDONTWRITEBYTECODE=\"1\"" in op_text)

    rw_text = (ROOT / "app" / "services" / "repo_worker.py").read_text(errors="replace")
    check("repo_worker._run_cli and _run_commands set PYTHONDONTWRITEBYTECODE=1",
          rw_text.count(f'"{FLAG}": "1"') >= 2)

    tc_text = (ROOT / "app" / "services" / "terminal_command.py").read_text(errors="replace")
    check("terminal_command.shell_path login-shell probe sets PYTHONDONTWRITEBYTECODE=1",
          'env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}' in tc_text)

    from app.services import auto_connect, mcp_client
    aconn_text = (ROOT / "app" / "services" / "auto_connect.py").read_text(errors="replace")
    check("auto_connect._ping forces PYTHONDONTWRITEBYTECODE=1 in StdioServerParameters env",
          'env["PYTHONDONTWRITEBYTECODE"] = "1"' in aconn_text)

    bare_py_name = Path(sys.executable).name
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + old_path
    try:
        env_bare, cmd_bare, _ = mcp_client.stdio_env("custom-py", {"command": bare_py_name, "args": ["-m", "some_mod"]})
    finally:
        os.environ["PATH"] = old_path
    check("mcp_client.stdio_env flags a bare python command resolving into the bundled prefix",
          env_bare.get(FLAG) == "1", repr((cmd_bare, env_bare.get(FLAG))))

    # Behavioral check: a stale pre-v1.1.9 ~/.codex/config.toml [mcp_servers.xod] block without
    # PYTHONDONTWRITEBYTECODE is upgraded in place by merge_toml_config instead of skipped.
    stale_toml = TMP / "stale_codex.toml"
    stale_toml.write_text(
        '[other]\nkey = "val"\n\n[mcp_servers.xod]\ncommand = "/Applications/Xod.app/Contents/Resources/python/bin/python3.12"\n'
        'args = ["-m", "app.mcp_server"]\ncwd = "/Applications/Xod.app/Contents/Resources/backend"\n\n'
        '[mcp_servers.xod.env]\nXOD_URL = "http://127.0.0.1:8000"\nPYTHONPATH = "/Applications/Xod.app/Contents/Resources/backend"\n',
        encoding="utf-8",
    )
    entry = auto_connect.mcp_entry(8000)
    res1 = auto_connect.merge_toml_config(stale_toml, entry)
    updated_toml = stale_toml.read_text(encoding="utf-8")
    res2 = auto_connect.merge_toml_config(stale_toml, entry)
    check("auto_connect.merge_toml_config upgrades a stale pre-v1.1.9 [mcp_servers.xod] block to include PYTHONDONTWRITEBYTECODE",
          res1["ok"] and res1["changed"] and not res1["already"]
          and 'PYTHONDONTWRITEBYTECODE = "1"' in updated_toml
          and updated_toml.count("[mcp_servers.xod]") == 1
          and res2["ok"] and res2["already"] and not res2["changed"],
          repr((res1, res2, updated_toml)))

    dt_text = (ROOT / "app" / "services" / "desktop.py").read_text(errors="replace")
    check("desktop._fresh_check passes -B alongside sys.executable",
          '[sys.executable, "-B", "-c", "import Quartz, ApplicationServices as A;"' in dt_text)

    el_text = (ROOT / "electron" / "main.js").read_text(errors="replace")
    check("electron startServer passes -B when spawning the uvicorn backend",
          'spawn(PY, ["-B", "-m", "uvicorn"' in el_text)

    swift_text = (ROOT / "partner" / "launcher" / "XodPartner.swift").read_text(errors="replace")
    sh_text = (ROOT / "partner" / "install.sh").read_text(errors="replace")
    check("partner launcher XodPartner.swift and partner/install.sh set PYTHONDONTWRITEBYTECODE=1 and -B",
          'env["PYTHONDONTWRITEBYTECODE"] = "1"' in swift_text and '"-B"' in swift_text
          and "PYTHONDONTWRITEBYTECODE=1" in sh_text and " -B -c " in sh_text)


def main():
    test_stdio_env_and_launch()
    test_spawn_site_audit()
    test_mcp_snippets_carry_flag()
    test_electron_spawns()
    test_terminal_shim()
    test_bundle_python_build_seal()
    test_isolated_pytest_probe_and_run_claims()
    test_missed_runtime_spawns_and_envs()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + "; ".join(failed), flush=True)
        sys.exit(1)
    print("PASS: no bundled-python child can write bytecode into the bundle (AGE-78)", flush=True)


if __name__ == "__main__":
    main()
