// Copies a standalone Python 3.12 (python-build-standalone, as installed by `uv python install 3.12`)
// into electron/python-runtime and installs all of Xod's packages into it, so Xod.app ships
// ready to run: people who download Xod need no Python, no Terminal and no first-run install.
// Runs before `npm run dist`.
//
// Override the source with XOD_BUNDLE_PYTHON=/path/to/python-root (the folder that contains bin/).
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const OUT = path.join(__dirname, "..", "python-runtime");
const SKIP = new Set(["test", "tests", "idlelib", "tkinter", "turtledemo", "__pycache__", "ensurepip/_bundled-unused"]);

function findSource() {
  if (process.env.XOD_BUNDLE_PYTHON) return process.env.XOD_BUNDLE_PYTHON;
  for (const uv of ["uv", path.join(process.env.HOME || "", ".local", "bin", "uv")]) {
    try {
      const exe = execFileSync(uv, ["python", "find", "3.12"], { encoding: "utf8" }).trim();
      return path.resolve(fs.realpathSync(exe), "..", "..");
    } catch (e) { /* try the next */ }
  }
  const managed = path.join(process.env.HOME || "", ".local", "share", "uv", "python");
  if (fs.existsSync(managed)) {
    const hit = fs.readdirSync(managed).filter((d) => /^cpython-3\.12\.\d+-macos-aarch64/.test(d)).sort().pop();
    if (hit) return path.join(managed, hit);
  }
  throw new Error("No standalone Python 3.12 found. Install one with: uv python install 3.12 (or set XOD_BUNDLE_PYTHON).");
}

function copy(src, dst) {
  const st = fs.lstatSync(src);
  if (st.isSymbolicLink()) { fs.symlinkSync(fs.readlinkSync(src), dst); return; }
  if (st.isDirectory()) {
    fs.mkdirSync(dst, { recursive: true });
    for (const name of fs.readdirSync(src)) {
      if (SKIP.has(name) || name.endsWith(".pyc")) continue;
      copy(path.join(src, name), path.join(dst, name));
    }
    return;
  }
  fs.copyFileSync(src, dst);
  fs.chmodSync(dst, st.mode);
}

const src = findSource();
const exe = path.join(src, "bin", "python3.12");
const version = execFileSync(exe, ["-c", "import sys; print(sys.version.split()[0])"], { encoding: "utf8" }).trim();
fs.rmSync(OUT, { recursive: true, force: true });
copy(src, OUT);
fs.writeFileSync(path.join(OUT, "XOD_PYTHON_VERSION"), version + "\n");
console.log(`Bundled Python ${version} from ${src} → ${OUT}`);

// Xod's packages, installed into the bundled Python itself (it's relocatable, so no venv needed).
const py = path.join(OUT, "bin", "python3.12");
// uv marks its Pythons "externally managed"; this copy is Xod's own, so drop the marker.
fs.rmSync(path.join(OUT, "lib", "python3.12", "EXTERNALLY-MANAGED"), { force: true });
const reqs = path.join(__dirname, "..", "..", "requirements.txt");
const sh = (args) => execFileSync(py, args, { stdio: "inherit", env: { ...process.env, PIP_DISABLE_PIP_VERSION_CHECK: "1" } });
sh(["-m", "pip", "install", "--no-compile", "--only-binary=:all:", "-r", reqs]);
// Media Studio's libraries are a one-time download on first use instead (app/services/media_packages.py),
// which keeps Xod's download about a third smaller. pip stays, to fetch them.
// imageio-ffmpeg is left out too: its ffmpeg is a GPL build, so it's fetched from PyPI on first use rather
// than redistributed inside the app.
sh(["-m", "pip", "uninstall", "-y", "torch", "diffusers", "accelerate", "peft", "sympy", "networkx", "mpmath", "imageio-ffmpeg"]);
// Drop the packages' own test suites (never imported by the app).
const sp = path.join(OUT, "lib", "python3.12", "site-packages");
for (const name of fs.readdirSync(sp)) {
  const d = path.join(sp, name, "tests");
  if (fs.existsSync(d) && fs.lstatSync(d).isDirectory()) fs.rmSync(d, { recursive: true, force: true });
}
// Precompiled bytecode (hash-based, so it stays valid inside a read-only, signed app): faster launch.
sh(["-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "unchecked-hash", path.join(OUT, "lib", "python3.12")]);
fs.copyFileSync(reqs, path.join(OUT, "XOD_REQUIREMENTS"));
// Notices for everything that ships inside the app (fails the build on a GPL-family license).
execFileSync("python3", [path.join(__dirname, "..", "..", "scripts", "third_party_licenses.py"), sp,
  path.join(OUT, "THIRD_PARTY_LICENSES.txt")], { stdio: "inherit" });
console.log("Installed Xod's packages into the bundled Python.");
