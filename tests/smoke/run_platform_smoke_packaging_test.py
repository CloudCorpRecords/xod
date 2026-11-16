"""
The platform smoke's packaging check (AGE-73): electron/package.json's build.files is a
list of glob patterns (with ! negations), and the check must expand them the way
electron-builder does before reading any file. The pre-fix check treated the patterns
as literal filenames and crashed on the first one (electron/*.js, then
electron/!*.test.js), failing the CI Python checks step and the Windows from-scratch
step on every run since v1.1.4 while the app itself was fine.

Builds throwaway electron folders and proves: the real repo folder passes; a glob list
passes when every require is packed; a require the patterns leave out is named; a file
removed by a ! negation counts as not packed; a literal list (the pre-glob shape) still
works; a directory pattern packs what is under it.

  python tests/smoke/run_platform_smoke_packaging_test.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_platform_smoke", ROOT / "tests" / "smoke" / "run_platform_smoke.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


def fake_electron(files, contents):
    """A throwaway electron folder: package.json with the given build.files list, plus
    the given relative path -> text files."""
    edir = Path(tempfile.mkdtemp(prefix="xod-packaging-")) / "electron"
    edir.mkdir()
    (edir / "package.json").write_text(json.dumps({"build": {"files": files}}))
    for rel, text in contents.items():
        p = edir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return edir


def main():
    real = ROOT / "electron"
    check("the real electron folder: every require is packed", smoke.missing_requires(real) == [],
          str(smoke.missing_requires(real)))
    packed = smoke.packed_files(real)
    check("…the glob list expands to the shipped files, test files excluded",
          "main.js" in packed and "liveness.js" in packed and "package.json" in packed
          and not any(f.endswith(".test.js") for f in packed), str(sorted(packed)))

    edir = fake_electron(["*.js", "!*.test.js", "package.json"],
                         {"main.js": 'require("./helper");\nrequire("./gone");\n',
                          "helper.js": "", "gone.test.js": ""})
    check("a require the patterns leave out is named, not crashed on",
          smoke.missing_requires(edir) == ["gone.js"], str(smoke.missing_requires(edir)))

    edir = fake_electron(["*.js", "!*.test.js", "package.json"],
                         {"main.js": 'require("./helper");\n', "helper.js": ""})
    check("a glob list passes when every require is packed", smoke.missing_requires(edir) == [],
          str(smoke.missing_requires(edir)))

    edir = fake_electron(["*.js", "!*.test.js", "package.json"],
                         {"main.js": 'require("./skip.test");\n', "skip.test.js": ""})
    check("a file removed by a ! negation counts as not packed",
          smoke.missing_requires(edir) == ["skip.test.js"], str(smoke.missing_requires(edir)))

    edir = fake_electron(["main.js", "helper.js", "package.json"],
                         {"main.js": 'require("./helper");\n', "helper.js": ""})
    check("a literal list (the pre-glob shape) still works", smoke.missing_requires(edir) == [],
          str(smoke.missing_requires(edir)))

    edir = fake_electron(["*.js", "build", "package.json"],
                         {"main.js": 'require("./build/inner");\n', "build/inner.js": ""})
    check("a directory pattern packs what is under it", smoke.missing_requires(edir) == [],
          str(smoke.missing_requires(edir)))

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
