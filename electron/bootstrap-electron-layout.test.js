// AGE-75: the bootstrap-electron runner must understand the darwin dist
// layout, not only the Linux one. These checks build fake dist trees in
// both shapes on disk and run the runner's own resolution logic against
// them, so they pass or fail identically on any host: the darwin branch
// of the runner is proven even where no Mac exists, and the real packaged
// launch on macOS is wired into CI and the release workflow (macos
// runners execute bootstrap-electron.test.js itself).
//
// Red on the base tree: scripts/bootstrap-electron-layout.js does not
// exist there, and the old resolver in bootstrap-electron.test.js rejects
// a darwin-shaped binary outright (it demands resources/ beside the
// binary; on darwin resources live at Electron.app/Contents/Resources).
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const L = require("./scripts/bootstrap-electron-layout");

function scratch() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "xod-bootstrap-layout-"));
}

function touch(file) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, "");
}

function makeDarwinDist(root) {
  const app = path.join(root, "Electron.app");
  touch(path.join(app, "Contents", "MacOS", "Electron"));
  fs.mkdirSync(path.join(app, "Contents", "Resources"), { recursive: true });
  return app;
}

function makeFlatDist(root) {
  touch(path.join(root, "electron"));
  fs.mkdirSync(path.join(root, "resources"), { recursive: true });
}

test("darwin: a dist root resolves to the binary and resources inside the .app", () => {
  const root = scratch();
  const app = makeDarwinDist(root);
  const layout = L.resolveDist([root], "darwin");
  assert.ok(layout, "the darwin dist root must resolve");
  assert.equal(layout.binary, path.join(app, "Contents", "MacOS", "Electron"));
  assert.equal(layout.resourcesDir, path.join(app, "Contents", "Resources"));
  assert.equal(layout.appAsar, path.join(app, "Contents", "Resources", "app.asar"));
});

test("darwin: the binary path require('electron') returns resolves", () => {
  const root = scratch();
  const app = makeDarwinDist(root);
  const bin = path.join(app, "Contents", "MacOS", "Electron");
  const layout = L.resolveDist([bin], "darwin");
  assert.ok(layout, "a binary inside a .app must resolve (the base runner returned null here)");
  assert.equal(layout.resourcesDir, path.join(app, "Contents", "Resources"),
    "resources are at Contents/Resources, not beside the binary");
  assert.equal(layout.root, root);
});

test("darwin: an .app path itself is a valid candidate", () => {
  const root = scratch();
  const app = makeDarwinDist(root);
  const layout = L.resolveDist([app], "darwin");
  assert.ok(layout);
  assert.equal(layout.binary, path.join(app, "Contents", "MacOS", "Electron"));
});

test("darwin: the launch spec runs the binary directly, never xvfb", () => {
  const spec = L.launchSpec("darwin", "/dist/Electron.app/Contents/MacOS/Electron");
  assert.equal(spec.command, "/dist/Electron.app/Contents/MacOS/Electron");
  assert.ok(!spec.command.includes("xvfb") && !spec.args.some((a) => String(a).includes("xvfb")),
    "macOS has no xvfb-run; the old runner hardcoded it");
});

test("linux: the flat dist still resolves and still launches through xvfb", () => {
  const root = scratch();
  makeFlatDist(root);
  const layout = L.resolveDist([root], "linux");
  assert.ok(layout, "the linux shape must keep working");
  assert.equal(layout.binary, path.join(root, "electron"));
  assert.equal(layout.appAsar, path.join(root, "resources", "app.asar"));
  const spec = L.launchSpec("linux", layout.binary);
  assert.equal(spec.command, "xvfb-run");
  assert.ok(spec.args.includes(layout.binary));
});

test("darwin: relocating a layout keeps the app.asar inside the copied bundle", () => {
  const root = scratch();
  const app = makeDarwinDist(root);
  const layout = L.resolveDist([root], "darwin");
  const copyRoot = path.join(scratch(), "dist");
  const moved = L.relocate(layout, copyRoot);
  assert.equal(moved.binary, path.join(copyRoot, "Electron.app", "Contents", "MacOS", "Electron"));
  assert.equal(moved.appAsar, path.join(copyRoot, "Electron.app", "Contents", "Resources", "app.asar"));
  assert.equal(L.appBundlePath(moved), path.join(copyRoot, "Electron.app"));
});

test("no dist anywhere still resolves to null, never a guessed path", () => {
  const empty = scratch();
  assert.equal(L.resolveDist([empty], "darwin"), null);
  assert.equal(L.resolveDist([empty], "linux"), null);
  assert.equal(L.resolveDist([], "darwin"), null);
});
