// Copies a standalone Python 3.12 (python-build-standalone, as installed by `uv python install 3.12`)
// into electron/python-runtime, which electron-builder ships inside Xod.app. Then people who
// download Xod don't need Python installed. Runs before `npm run dist`.
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
