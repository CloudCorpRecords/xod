"""Lightweight zero-dependency pytest shim for Xod workers and verification gates.

When a mission step or verification gate invokes `pytest` or `python3 -m pytest`
on a machine where `pytest` is not installed in the active Python environment,
this module provides:
1. Commonly used pytest API helpers (`raises`, `approx`, `fixture`, `mark`,
   `skip`, `fail`, `warns`, ` MonkeyPatch`) so `import pytest` succeeds inside
   generated or repository test files.
2. A CLI test runner (`python3 -m app.services.pytest_shim [paths...] [-k expr] [-q]`)
   that discovers and executes `test_*.py` / `*_test.py` files and `test_*`
   functions/methods, injecting standard fixtures (`tmp_path`, `tmp_path_factory`,
   `monkeypatch`, `capsys`, `capfd`) and user `@pytest.fixture` functions.
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import importlib.util
import inspect
import io
import math
import os
import pathlib
import sys
import tempfile
import traceback
from typing import Any, Callable, Iterable, List, Optional, Tuple, Type


class Skipped(BaseException):
    """Raised when a test calls pytest.skip()."""


class Failed(AssertionError):
    """Raised when a test calls pytest.fail()."""


class ExceptionInfo:
    def __init__(self, exc_type: Type[BaseException], value: BaseException, tb: Any) -> None:
        self.type = exc_type
        self.value = value
        self.tb = tb

    def match(self, pattern: str) -> bool:
        import re
        text = str(self.value)
        if not re.search(pattern, text):
            raise AssertionError(f"Pattern {pattern!r} does not match {text!r}")
        return True


class _RaisesContext:
    def __init__(
        self,
        expected_exception: Type[BaseException] | Tuple[Type[BaseException], ...],
        match: Optional[str] = None,
    ) -> None:
        self.expected = expected_exception
        self.match_expr = match
        self.excinfo: Optional[ExceptionInfo] = None

    def __enter__(self) -> ExceptionInfo:
        self.excinfo = ExceptionInfo(BaseException, BaseException(), None)
        return self.excinfo

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if exc_type is None:
            raise AssertionError(f"DID NOT RAISE {self.expected}")
        if not issubclass(exc_type, self.expected):
            return False
        assert self.excinfo is not None
        self.excinfo.type = exc_type
        self.excinfo.value = exc_val
        self.excinfo.tb = exc_tb
        if self.match_expr is not None:
            self.excinfo.match(self.match_expr)
        return True


def raises(
    expected_exception: Type[BaseException] | Tuple[Type[BaseException], ...],
    *args: Any,
    match: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    if args:
        func = args[0]
        f_args = args[1:]
        with _RaisesContext(expected_exception, match=match) as excinfo:
            func(*f_args, **kwargs)
        return excinfo
    return _RaisesContext(expected_exception, match=match)


class _Approx:
    def __init__(self, expected: Any, rel: Optional[float] = None, abs_tol: Optional[float] = None) -> None:
        self.expected = expected
        self.rel = 1e-6 if rel is None else rel
        self.abs = 1e-12 if abs_tol is None else abs_tol

    def __eq__(self, actual: Any) -> bool:
        if isinstance(self.expected, (int, float)) and isinstance(actual, (int, float)):
            return math.isclose(float(actual), float(self.expected), rel_tol=self.rel, abs_tol=self.abs)
        if isinstance(self.expected, (list, tuple)) and isinstance(actual, (list, tuple)):
            if len(self.expected) != len(actual):
                return False
            return all(_Approx(e, self.rel, self.abs) == a for e, a in zip(self.expected, actual))
        if isinstance(self.expected, dict) and isinstance(actual, dict):
            if set(self.expected.keys()) != set(actual.keys()):
                return False
            return all(_Approx(self.expected[k], self.rel, self.abs) == actual[k] for k in self.expected)
        return actual == self.expected

    def __repr__(self) -> str:
        return f"approx({self.expected!r})"


def approx(expected: Any, rel: Optional[float] = None, abs: Optional[float] = None) -> _Approx:
    return _Approx(expected, rel=rel, abs_tol=abs)


def skip(reason: str = "") -> None:
    raise Skipped(reason)


def fail(reason: str = "") -> None:
    raise Failed(reason)


@contextlib.contextmanager
def warns(expected_warning: Any = Warning, match: Optional[str] = None):
    import warnings
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        yield record
    if expected_warning is not None:
        matched = [w for w in record if issubclass(w.category, expected_warning)]
        if not matched:
            raise AssertionError(f"DID NOT WARN {expected_warning}")
        if match is not None:
            import re
            if not any(re.search(match, str(w.message)) for w in matched):
                raise AssertionError(f"Warning message did not match {match!r}")


def fixture(func: Optional[Callable] = None, *, scope: str = "function", autouse: bool = False, **kwargs: Any) -> Any:
    def _decorate(fn: Callable) -> Callable:
        setattr(fn, "_pytest_fixture", True)
        setattr(fn, "_pytest_fixture_scope", scope)
        setattr(fn, "_pytest_fixture_autouse", autouse)
        return fn

    if func is not None and callable(func):
        return _decorate(func)
    return _decorate


class _MarkDecorator:
    def __init__(self, name: str, args: tuple = (), kwargs: Optional[dict] = None) -> None:
        self.name = name
        self.args = args
        self.kwargs = kwargs or {}

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if len(args) == 1 and callable(args[0]) and not kwargs and not self.args:
            fn = args[0]
            marks = list(getattr(fn, "_pytest_marks", []))
            marks.append(self)
            setattr(fn, "_pytest_marks", marks)
            return fn
        if self.name == "parametrize" and not self.args:
            return _MarkDecorator(self.name, args=args, kwargs=kwargs)
        if len(args) == 1 and callable(args[0]) and not kwargs and self.args:
            fn = args[0]
            if self.name == "parametrize":
                param_specs = list(getattr(fn, "_pytest_parametrize", []))
                param_specs.append((self.args[0], self.args[1]))
                setattr(fn, "_pytest_parametrize", param_specs)
            elif self.name == "skip":
                setattr(fn, "_pytest_skip", self.args[0] if self.args else "skipped")
            elif self.name == "skipif":
                if self.args and bool(self.args[0]):
                    setattr(fn, "_pytest_skip", self. kwargs.get("reason", "skipif condition met"))
            marks = list(getattr(fn, "_pytest_marks", []))
            marks.append(self)
            setattr(fn, "_pytest_marks", marks)
            return fn
        return _MarkDecorator(self.name, args=args, kwargs=kwargs)


class _MarkGenerator:
    def __getattr__(self, name: str) -> _MarkDecorator:
        return _MarkDecorator(name)


mark = _MarkGenerator()


class MonkeyPatch:
    def __init__(self) -> None:
        self._undo: List[Callable[[], None]] = []

    def setattr(self, target: Any, name: Any, value: Any = ..., raising: bool = True) -> None:
        if value is ... and isinstance(target, str):
            mod_path, _, attr_name = target.rpartition(".")
            import importlib
            obj = importlib.import_module(mod_path)
            value = name
            name = attr_name
            target = obj
        existed = hasattr(target, name)
        if not existed and raising:
            raise AttributeError(f"{target!r} has no attribute {name!r}")
        old_val = getattr(target, name, None)
        setattr(target, name, value)

        def _restore() -> None:
            if existed:
                setattr(target, name, old_val)
            else:
                try:
                    delattr(target, name)
                except AttributeError:
                    pass

        self._undo.append(_restore)

    def delattr(self, target: Any, name: str, raising: bool = True) -> None:
        existed = hasattr(target, name)
        if not existed and raising:
            raise AttributeError(f"{target!r} has no attribute {name!r}")
        if existed:
            old_val = getattr(target, name)
            delattr(target, name)
            self._undo.append(lambda: setattr(target, name, old_val))

    def setitem(self, dic: Any, name: Any, value: Any) -> None:
        existed = name in dic
        old_val = dic.get(name)
        dic[name] = value

        def _restore() -> None:
            if existed:
                dic[name] = old_val
            else:
                dic.pop(name, None)

        self._undo.append(_restore)

    def delitem(self, dic: Any, name: Any, raising: bool = True) -> None:
        existed = name in dic
        if not existed and raising:
            raise KeyError(name)
        if existed:
            old_val = dic.pop(name)
            self._undo.append(lambda: dic.__setitem__(name, old_val))

    def setenv(self, name: str, value: str, prepend: Optional[str] = None) -> None:
        if prepend and name in os.environ:
            value = f"{value}{prepend}{os.environ[name]}"
        self.setitem(os.environ, name, str(value))

    def delenv(self, name: str, raising: bool = True) -> None:
        self.delitem(os.environ, name, raising=raising)

    def syspath_prepend(self, path: Any) -> None:
        p = str(path)
        sys.path.insert(0, p)
        self._undo.append(lambda: sys.path.remove(p) if p in sys.path else None)

    def chdir(self, path: Any) -> None:
        old_cwd = os.getcwd()
        os.chdir(str(path))
        self._undo.append(lambda: os.chdir(old_cwd))

    def undo(self) -> None:
        while self._undo:
            fn = self._undo.pop()
            try:
                fn()
            except Exception:
                pass


class _CaptureResult:
    def __init__(self, out: str, err: str) -> None:
        self.out = out
        self.err = err

    def __iter__(self):
        yield self.out
        yield self.err


class _CapSys:
    def __init__(self) -> None:
        self._out = io.StringIO()
        self._err = io.StringIO()
        self._orig_out = sys.stdout
        self._orig_err = sys.stderr

    def start(self) -> None:
        self._orig_out = sys.stdout
        self._orig_err = sys.stderr
        sys.stdout = self._out
        sys.stderr = self._err

    def stop(self) -> None:
        sys.stdout = self._orig_out
        sys.stderr = self._orig_err

    def readouterr(self) -> _CaptureResult:
        out = self._out.getvalue()
        err = self._err.getvalue()
        self._out.seek(0)
        self._out.truncate(0)
        self._err.seek(0)
        self._err.truncate(0)
        return _CaptureResult(out, err)


class _TmpPathFactory:
    def __init__(self, base: pathlib.Path) -> None:
        self._base = base

    def mktemp(self, basename: str, numbered: bool = True) -> pathlib.Path:
        safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in basename) or "tmp"
        p = pathlib.Path(tempfile.mkdtemp(prefix=f"{safe}_", dir=str(self._base)))
        return p


def _install_self_as_pytest() -> None:
    """Register this module as `pytest` in `sys.modules` if real pytest is absent."""
    if "pytest" not in sys.modules:
        sys.modules["pytest"] = sys.modules[__name__]


def _discover_files(targets: List[str]) -> List[pathlib.Path]:
    found: List[pathlib.Path] = []
    if not targets:
        targets = ["."]
    for raw in targets:
        base_part = raw.split("::", 1)[0]
        p = pathlib.Path(base_part).resolve()
        if p.is_file():
            found.append(p)
        elif p.is_dir():
            for root, dirs, files in os.walk(p):
                dirs[:] = [
                    d for d in sorted(dirs)
                    if not d.startswith(".") and d not in ("__pycache__", "node_modules", "venv", ".venv", "dist", "build")
                ]
                for f in sorted(files):
                    if (f.startswith("test_") and f.endswith(".py")) or f.endswith("_test.py"):
                        found.append(pathlib.Path(root) / f)
    return found


def _load_module(path: pathlib.Path, index: int) -> Any:
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    mod_name = f"_pytest_shim_mod_{index}_{path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _collect_fixtures(mod: Any) -> dict[str, Callable]:
    fixtures: dict[str, Callable] = {}
    conftest_path = pathlib.Path(getattr(mod, "__file__", "")).parent / "conftest.py"
    if conftest_path.is_file():
        try:
            conf_mod = _load_module(conftest_path, 99999)
            for name, obj in inspect.getmembers(conf_mod):
                if callable(obj) and getattr(obj, "_pytest_fixture", False):
                    fixtures[name] = obj
        except Exception:
            pass
    for name, obj in inspect.getmembers(mod):
        if callable(obj) and getattr(obj, "_pytest_fixture", False):
            fixtures[name] = obj
    return fixtures


def _expand_parametrize(fn: Callable) -> List[Tuple[str, dict[str, Any]]]:
    specs = list(getattr(fn, "_pytest_parametrize", []))
    if not specs:
        return [("", {})]
    cases: List[Tuple[str, dict[str, Any]]] = [("", {})]
    for argnames_raw, argvalues in reversed(specs):
        if isinstance(argnames_raw, str):
            argnames = [a.strip() for a in argnames_raw.split(",") if a.strip()]
        else:
            argnames = list(argnames_raw)
        next_cases: List[Tuple[str, dict[str, Any]]] = []
        for suffix, base_kwargs in cases:
            for idx, val in enumerate(argvalues):
                merged = dict(base_kwargs)
                if len(argnames) == 1:
                    merged[argnames[0]] = val
                    label = repr(val)
                else:
                    vals = list(val)
                    for k, v in zip(argnames, vals):
                        merged[k] = v
                    label = "-".join(repr(v) for v in vals)
                new_suffix = f"[{label}]{suffix}" if suffix else f"[{label}]"
                next_cases.append((new_suffix, merged))
        cases = next_cases
    return cases


def _run_single_test(
    fn: Callable,
    extra_kwargs: dict[str, Any],
    user_fixtures: dict[str, Callable],
) -> None:
    if hasattr(fn, "_pytest_skip"):
        raise Skipped(str(getattr(fn, "_pytest_skip")))

    temp_root = pathlib.Path(tempfile.mkdtemp(prefix="xod_pytest_"))
    mp = MonkeyPatch()
    capsys = _CapSys()
    tmp_factory = _TmpPathFactory(temp_root)
    teardowns: List[Any] = []
    resolved: dict[str, Any] = {
        "tmp_path": temp_root,
        "tmpdir": temp_root,
        "tmp_path_factory": tmp_factory,
        "monkeypatch": mp,
    }

    def _resolve_fixture(name: str) -> Any:
        if name in resolved:
            return resolved[name]
        if name in ("capsys", "capfd"):
            capsys.start()
            teardowns.append(capsys.stop)
            resolved[name] = capsys
            return capsys
        if name in user_fixtures:
            fix_fn = user_fixtures[name]
            sig = inspect.signature(fix_fn)
            fix_kwargs = {}
            for p_name, p in sig.parameters.items():
                if p_name == "request":
                    continue
                fix_kwargs[p_name] = _resolve_fixture(p_name)
            res = fix_fn(**fix_kwargs)
            if inspect.isgenerator(res):
                val = next(res)

                def _finish_gen(g=res) -> None:
                    try:
                        next(g)
                    except StopIteration:
                        pass

                teardowns.append(_finish_gen)
                resolved[name] = val
                return val
            resolved[name] = res
            return res
        raise KeyError(name)

    try:
        for fix_name, fix_fn in user_fixtures.items():
            if getattr(fix_fn, "_pytest_fixture_autouse", False):
                _resolve_fixture(fix_name)

        sig = inspect.signature(fn)
        call_kwargs = dict(extra_kwargs)
        for p_name, p in sig.parameters.items():
            if p_name in call_kwargs or p_name == "self":
                continue
            if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            try:
                call_kwargs[p_name] = _resolve_fixture(p_name)
            except KeyError:
                if p.default is inspect.Parameter.empty:
                    raise
        res = fn(**call_kwargs)
        if inspect.iscoroutine(res):
            import asyncio
            asyncio.run(res)
    finally:
        for td in reversed(teardowns):
            try:
                td()
            except Exception:
                pass
        mp.undo()


def main(argv: Optional[List[str]] = None) -> int:
    _install_self_as_pytest()
    parser = argparse.ArgumentParser(prog="pytest", add_help=False)
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-x", "--exitfirst", action="store_true")
    parser.add_argument("-s", action="store_true")
    parser.add_argument("-k", dest="keyword", default=None)
    parser.add_argument("-m", dest="markexpr", default=None)
    parser.add_argument("--tb", dest="tb", default="short")
    parser.add_argument("--maxfail", dest="maxfail", type=int, default=0)
    parser.add_argument("--collect-only", "--co", dest="collect_only", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    args, remaining = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    if args.version:
        print("pytest 8.0.0 (xod-pytest-shim)")
        return 0
    if args.help:
        parser.print_help()
        return 0

    targets = [r for r in remaining if not r.startswith("-")]
    node_filters: dict[pathlib.Path, List[str]] = {}
    for raw in targets:
        if "::" in raw:
            file_part, node_part = raw.split("::", 1)
            p = pathlib.Path(file_part).resolve()
            node_filters.setdefault(p, []).append(node_part)

    files = _discover_files(targets)
    if not files:
        if not args.quiet:
            print("no tests ran in 0.00s")
        return 5

    passed = 0
    failed = 0
    skipped = 0
    failures: List[Tuple[str, str]] = []
    collected: List[Tuple[str, Callable, dict[str, Any], dict[str, Callable]]] = []

    for idx, fpath in enumerate(files):
        try:
            mod = _load_module(fpath, idx)
        except Exception:
            failed += 1
            failures.append((str(fpath.name), traceback.format_exc()))
            continue

        fixtures = _collect_fixtures(mod)
        wanted_nodes = node_filters.get(fpath, [])

        for name, obj in inspect.getmembers(mod):
            if inspect.isfunction(obj) and (name.startswith("test_") or name.endswith("_test")):
                if getattr(obj, "_pytest_fixture", False):
                    continue
                for suffix, params in _expand_parametrize(obj):
                    node_id = f"{fpath.name}::{name}{suffix}"
                    if wanted_nodes and not any(w in node_id for w in wanted_nodes):
                        continue
                    if args.keyword and args.keyword.lower() not in node_id.lower():
                        continue
                    collected.append((node_id, obj, params, fixtures))
            elif inspect.isclass(obj) and (name.startswith("Test") or name.endswith("Tests")):
                for m_name, method in inspect.getmembers(obj):
                    if (m_name.startswith("test_") or m_name.endswith("_test")) and callable(method):
                        for suffix, params in _expand_parametrize(method):
                            node_id = f"{fpath.name}::{name}::{m_name}{suffix}"
                            if wanted_nodes and not any(w in node_id for w in wanted_nodes):
                                continue
                            if args.keyword and args.keyword.lower() not in node_id.lower():
                                continue

                            def _bound_runner(cls=obj, m=m_name, p=params, **kw):
                                inst = cls()
                                if hasattr(inst, "setup_method"):
                                    inst.setup_method()
                                try:
                                    return getattr(inst, m)(**kw)
                                finally:
                                    if hasattr(inst, "teardown_method"):
                                        inst.teardown_method()

                            _bound_runner.__signature__ = inspect.signature(method)  # type: ignore[attr-defined]
                            collected.append((node_id, _bound_runner, params, fixtures))

    if args.collect_only:
        for node_id, _, _, _ in collected:
            print(node_id)
        print(f"{len(collected)} tests collected")
        return 0 if not failed else 2

    for node_id, fn, params, fixtures in collected:
        try:
            _run_single_test(fn, params, fixtures)
            passed += 1
            if args.verbose:
                print(f"{node_id} PASSED")
            elif not args.quiet:
                sys.stdout.write(".")
                sys.stdout.flush()
        except Skipped as s:
            skipped += 1
            if args.verbose:
                print(f"{node_id} SKIPPED ({s})")
            elif not args.quiet:
                sys.stdout.write("s")
                sys.stdout.flush()
        except BaseException:
            failed += 1
            failures.append((node_id, traceback.format_exc()))
            if args.verbose:
                print(f"{node_id} FAILED")
            elif not args.quiet:
                sys.stdout.write("F")
                sys.stdout.flush()
            if args.exitfirst or (args.maxfail and failed >= args.maxfail):
                break

    if not args.verbose and not args.quiet and collected:
        print()

    if failures and args.tb != "no":
        for node_id, tb in failures:
            print(f"___ {node_id} ___")
            print(tb.rstrip())

    summary_parts = []
    if failed:
        summary_parts.append(f"{failed} failed")
    if passed:
        summary_parts.append(f"{passed} passed")
    if skipped:
        summary_parts.append(f"{skipped} skipped")
    if not summary_parts:
        summary_parts.append("no tests ran")
    print(", ".join(summary_parts))
    return 1 if failed else 0


_install_self_as_pytest()

if __name__ == "__main__":
    raise SystemExit(main())
