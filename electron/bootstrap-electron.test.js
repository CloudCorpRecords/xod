// The bootstrap's verdict, from INSIDE a packaged Electron main process:
//   XOD_ELECTRON_BIN=/path/to/electron node --test electron/bootstrap-electron.test.js
//
// Why this file exists (v1.1.4 field failure, 2026-11-14): the bootstrap
// judged a healthy install broken on every real launch and its repair
// failed the same way, while every release-gate check passed. The gate ran
// under plain Node, where fs is unpatched. Inside a packaged Electron main
// process, Electron's asar-aware fs layer intercepts any path ending in
// .asar: fs.readFileSync on the archive throws "ENOENT, not found in
// ...app.asar" and fs.statSync().isFile() returns false, so decide() threw
// in its archive read and its catch returned "repair" unconditionally.
// No plain-Node test can see that. This test therefore launches a real
// packaged app (an Electron dist layout: binary + resources/app.asar)
// whose main is a harness that requires the bootstrap under test and runs
// decide() in-process, once on the app's OWN app.asar (the field scenario
// verbatim) and once on a target fixture archive handed in by path.
//
// Red on the v1.1.4 bootstrap: both verdicts come back "repair" with
// "cannot read the package: ENOENT, not found in ...app.asar".
// Green on the fix: a healthy archive is a "launch" in-process, and a
// genuinely broken one (the 1.1.3 shape) is still a "repair" for the real
// reasons, never for the read error.
//
// The Electron binary is resolved from XOD_ELECTRON_BIN, then the repo's
// installed electron package, then the release lane's preserved dist under
// ~/workspace/xod-eval. No binary is a hard failure, not a skip: a release
// machine that cannot run this test cannot prove the fix. The dist copy
// the packaged runs launch from lives under XOD_ELECTRON_TEST_SCRATCH
// (default os.tmpdir()); one copy serves every run in this file and is
// removed when the file finishes.
//
// Platforms (AGE-75): the runner used to be Linux-only. It assumed the
// flat dist layout (<dist>/electron with resources/ beside it) and
// launched through xvfb-run, so on darwin it found no dist at all: a Mac
// dist is an app bundle (Electron.app/Contents/MacOS/Electron, resources
// at Contents/Resources) and macOS has no xvfb. Dist resolution and the
// launch route now come from scripts/bootstrap-electron-layout.js, which
// knows both shapes; bootstrap-electron-layout.test.js proves the darwin
// shape on any host, and CI plus the release workflow run this file
// itself on macos runners, where the packaged launch is real.
const { test, after } = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFile, execFileSync } = require("child_process");
const layoutLib = require("./scripts/bootstrap-electron-layout");

// --- the same minimal asar writer bootstrap.test.js uses ---
function writeAsar(file, files) {
  const offsets = {};
  const chunks = [];
  let pos = 0;
  for (const rel of Object.keys(files).sort()) {
    offsets[rel] = pos;
    chunks.push(files[rel]);
    pos += files[rel].length;
  }
  const tree = { files: {} };
  for (const [rel, content] of Object.entries(files)) {
    let node = tree.files;
    const parts = rel.split("/");
    for (const part of parts.slice(0, -1)) {
      node[part] = node[part] || { files: {} };
      node = node[part].files;
    }
    node[parts[parts.length - 1]] = { size: content.length, offset: String(offsets[rel]) };
  }
  const js = Buffer.from(JSON.stringify(tree), "utf8");
  const pad4 = (b) => Buffer.concat([b, Buffer.alloc((4 - (b.length % 4)) % 4)]);
  const header = Buffer.concat([
    (() => { const b = Buffer.alloc(4); b.writeUInt32LE(4 + pad4(js).length); return b; })(),
    (() => { const b = Buffer.alloc(4); b.writeUInt32LE(js.length); return b; })(),
    pad4(js),
  ]);
  const size = Buffer.alloc(8);
  size.writeUInt32LE(4, 0);
  size.writeUInt32LE(header.length, 4);
  fs.writeFileSync(file, Buffer.concat([size, header, ...chunks]));
}

const HARNESS = [
  '"use strict";',
  'const fs = require("fs");',
  'const path = require("path");',
  'const { app } = require("electron");',
  'const B = require("./bootstrap.js");',
  'const ownAsar = path.join(process.resourcesPath, "app.asar");',
  "const out = { electron: process.versions.electron, ownAsar, probe: {}, own: null, target: null };",
  'try { out.probe.readLength = fs.readFileSync(ownAsar).length; } catch (e) { out.probe.readError = String((e && e.message) || e); }',
  'try { out.probe.statIsFile = fs.statSync(ownAsar).isFile(); } catch (e) { out.probe.statError = String((e && e.message) || e); }',
  'try { out.own = B.decide(ownAsar); } catch (e) { out.ownError = String((e && e.stack) || e); }',
  'if (process.env.XOD_ELECTRON_TEST_TARGET) {',
  '  try { out.target = B.decide(process.env.XOD_ELECTRON_TEST_TARGET); } catch (e) { out.targetError = String((e && e.stack) || e); }',
  "}",
  'fs.writeFileSync(process.env.XOD_ELECTRON_TEST_OUT, JSON.stringify(out, null, 2));',
  "app.exit(0);",
  "",
].join("\n");

const MAIN_113 = [
  "// fixture entry, shaped like the shipped v1.1.3 main.js",
  'const { app } = require("electron");',
  'const fs = require("fs");',
  'const path = require("path");',
  'const own = require("./own_server");',
  'const mode = require("./mode");',
  'const upd = require("./updater");',
  'const { LivenessReview } = require("./liveness");',
  "",
].join("\n");

function fixtureBroken113() {
  return {
    "package.json": Buffer.from(JSON.stringify({ name: "xod-desktop", version: "1.1.3", main: "main.js" })),
    "main.js": Buffer.from(MAIN_113),
    "mode.js": Buffer.from("module.exports = {};\n"),
    "own_server.js": Buffer.from("module.exports = {};\n"),
    "updater.js": Buffer.from('const crypto = require("crypto");\nmodule.exports = {};\n'),
    "preload.js": Buffer.from("// preload\n"),
    "splash.html": Buffer.from("<html></html>\n"),
    "build/icon.png": Buffer.from([0x89, 0x50, 0x4e, 0x47]),
  };
}

function electronDist() {
  const candidates = [];
  if (process.env.XOD_ELECTRON_BIN) candidates.push(process.env.XOD_ELECTRON_BIN);
  try {
    const p = require.resolve("electron", { paths: [__dirname] });
    candidates.push(typeof p === "string" ? p : "");
  } catch (e) { /* not installed here */ }
  const preserved = path.join(os.homedir(), "workspace", "xod-eval", "v114-ship", "artifact", "electron-bin");
  candidates.push(path.join(preserved, "electron"));
  candidates.push(preserved);
  return layoutLib.resolveDist(candidates, process.platform);
}

let sharedLayout = null;
let sharedRoot = null;
after(() => {
  if (sharedRoot) { try { fs.rmSync(sharedRoot, { recursive: true, force: true }); } catch (e) {} }
});
function packagedDist(layout) {
  if (sharedLayout) return sharedLayout;
  const scratchRoot = process.env.XOD_ELECTRON_TEST_SCRATCH || os.tmpdir();
  sharedRoot = fs.mkdtempSync(path.join(scratchRoot, "xod-bootstrap-electron-"));
  const dist = path.join(sharedRoot, "dist");
  fs.cpSync(layout.root, dist, { recursive: true });
  sharedLayout = layoutLib.relocate(layout, dist);
  // Rewriting app.asar inside a copied .app invalidates its signature;
  // re-sign the copy ad hoc so macOS will launch it. Best effort only:
  // a direct exec of the binary usually runs either way, and a codesign
  // failure must not mask the verdict this test exists to produce.
  const app = layoutLib.appBundlePath(sharedLayout);
  if (app) {
    try { execFileSync("codesign", ["--force", "--deep", "--sign", "-", app], { stdio: "ignore" }); } catch (e) {}
  }
  return sharedLayout;
}

function runPackaged(layout, targetAsar, label) {
  return new Promise((resolve, reject) => {
    const dist = packagedDist(layout);
    const root = sharedRoot;
    const ownAsar = dist.appAsar;
    writeAsar(ownAsar, {
      "package.json": Buffer.from(JSON.stringify({
        name: "xod-bootstrap-electron-fixture", version: "9.9.9", main: "harness.js",
      })),
      "harness.js": Buffer.from(HARNESS),
      "bootstrap.js": fs.readFileSync(path.join(__dirname, "bootstrap.js")),
      "main.js": Buffer.from("module.exports = {};\n"),
    });
    const outFile = path.join(root, `verdict-${label}.json`);
    try { fs.rmSync(outFile, { force: true }); } catch (e) {}
    const env = {
      ...process.env,
      XOD_ELECTRON_TEST_OUT: outFile,
      XOD_ELECTRON_TEST_TARGET: targetAsar || "",
    };
    const spec = layoutLib.launchSpec(process.platform, dist.binary);
    execFile(spec.command, spec.args,
      { env, timeout: 120000, maxBuffer: 16 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (!fs.existsSync(outFile)) {
          return reject(new Error(
            `the packaged harness produced no verdict (${label}): ${err ? err.message : "no error"}\n` +
            `stdout: ${String(stdout).slice(-2000)}\nstderr: ${String(stderr).slice(-2000)}`));
        }
        resolve(JSON.parse(fs.readFileSync(outFile, "utf8")));
      });
  });
}

test("inside a packaged Electron process, a healthy package is a launch, not a repair", async () => {
  const dist = electronDist();
  assert.ok(dist, "no Electron dist found: set XOD_ELECTRON_BIN to an electron binary " +
    "in a dist layout (flat, or a .app bundle on darwin), or npm ci the electron package first");
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "xod-bootstrap-electron-targets-"));
  const healthy = path.join(scratch, "healthy-app.asar");
  writeAsar(healthy, {
    "package.json": Buffer.from(JSON.stringify({ name: "xod-desktop", version: "9.9.9", main: "bootstrap.js" })),
    "bootstrap.js": Buffer.from('const m = require("./main.js");\nmodule.exports = m;\n'),
    "main.js": Buffer.from('const l = require("./liveness");\nmodule.exports = {};\n'),
    "liveness.js": Buffer.from("class LivenessReview {}\nmodule.exports = { LivenessReview };\n"),
  });
  const out = await runPackaged(dist, healthy, "healthy");
  assert.ok(out.electron, "the harness ran inside Electron");
  assert.equal(out.ownError, undefined);
  assert.equal(out.own.action, "launch",
    `own archive verdict must be launch in-process; got ${JSON.stringify(out.own)} ` +
    `(fs probe: ${JSON.stringify(out.probe)})`);
  assert.equal(out.own.version, "9.9.9");
  assert.deepEqual(out.own.problems, []);
  assert.equal(out.targetError, undefined);
  assert.equal(out.target.action, "launch",
    `healthy target verdict must be launch in-process; got ${JSON.stringify(out.target)}`);
  assert.deepEqual(out.target.problems, []);
});

test("inside a packaged Electron process, the 1.1.3 defect is still caught for its real reasons", async () => {
  const dist = electronDist();
  assert.ok(dist, "no Electron dist found: set XOD_ELECTRON_BIN first");
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "xod-bootstrap-electron-targets-"));
  const broken = path.join(scratch, "broken-113-app.asar");
  writeAsar(broken, fixtureBroken113());
  const out = await runPackaged(dist, broken, "broken");
  assert.equal(out.own.action, "launch", "the harness app itself stays healthy");
  assert.equal(out.targetError, undefined);
  assert.equal(out.target.action, "repair");
  assert.ok(!out.target.problems.some((p) => p.includes("cannot read the package")),
    `the in-process read must not fail anymore; got: ${out.target.problems.join(" | ")}`);
  assert.ok(out.target.problems.some((p) => p.startsWith("main.js:8") && p.includes('"./liveness"')),
    `the real defect must be named; got: ${out.target.problems.join(" | ")}`);
  assert.ok(out.target.reasons.some((r) => r.includes("known broken release")),
    "the known-broken version gate still fires in-process");
});
