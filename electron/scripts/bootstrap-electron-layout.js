"use strict";
// Platform layout of a stock Electron dist, for bootstrap-electron.test.js
// (AGE-75). The runner was Linux-only because it assumed the flat dist
// layout everywhere: the binary at <dist>/electron with resources/ beside
// it, launched through xvfb-run. On darwin the dist is an app bundle:
// <dist>/Electron.app/Contents/MacOS/Electron, with resources at
// Electron.app/Contents/Resources (which is also what process.resourcesPath
// reports inside the packaged process, and where the default app.asar
// lives), and there is no xvfb: the binary launches directly.
//
// A layout is { root, binary, resourcesDir, appAsar }:
//   root         the folder that gets copied to scratch for a packaged run
//   binary       the Electron executable inside that root
//   resourcesDir the folder Electron treats as its resources directory
//   appAsar      resourcesDir/app.asar, the default app the binary loads
//
// These helpers are pure path logic plus an injectable exists check, so
// the darwin shapes are testable on any host (bootstrap-electron-layout
// .test.js). This file lives under scripts/ on purpose: electron-builder
// ships only the top-level patterns in electron/package.json build.files,
// so a test helper here never lands in app.asar.
const fs = require("fs");
const path = require("path");

function isAppBundle(p) {
  return path.basename(p).endsWith(".app");
}

function appBinaryName(appPath) {
  return path.basename(appPath).slice(0, -".app".length);
}

// Layout from a binary path. Handles the darwin bundle shape
// (<root>/<App>.app/Contents/MacOS/<binary>) and the flat shape
// (<root>/electron[.exe]) with the same return contract.
function layoutFromBinary(binary) {
  const parts = binary.split(path.sep);
  const appIdx = parts.findIndex((seg) => seg.endsWith(".app"));
  if (appIdx >= 0 && parts[appIdx + 1] === "Contents" && parts[appIdx + 2] === "MacOS") {
    const appPath = parts.slice(0, appIdx + 1).join(path.sep) || path.sep;
    const root = parts.slice(0, appIdx).join(path.sep) || path.sep;
    const resourcesDir = path.join(appPath, "Contents", "Resources");
    return { root, binary, resourcesDir, appAsar: path.join(resourcesDir, "app.asar") };
  }
  const root = path.dirname(binary);
  const resourcesDir = path.join(root, "resources");
  return { root, binary, resourcesDir, appAsar: path.join(resourcesDir, "app.asar") };
}

// Layout from an .app bundle path (a valid XOD_ELECTRON_BIN on darwin).
function layoutFromApp(appPath) {
  const binary = path.join(appPath, "Contents", "MacOS", appBinaryName(appPath));
  const resourcesDir = path.join(appPath, "Contents", "Resources");
  return {
    root: path.dirname(appPath),
    binary,
    resourcesDir,
    appAsar: path.join(resourcesDir, "app.asar"),
  };
}

// Layout from a dist root folder (node_modules/electron/dist, or the
// release lane's preserved electron-bin folder).
function layoutFromDistRoot(root, platform) {
  if (platform === "darwin") return layoutFromApp(path.join(root, "Electron.app"));
  const binary = path.join(root, platform === "win32" ? "electron.exe" : "electron");
  const resourcesDir = path.join(root, "resources");
  return { root, binary, resourcesDir, appAsar: path.join(resourcesDir, "app.asar") };
}

// A candidate may be a binary path, an .app path, or a dist root; return
// its layout shape without touching the filesystem.
function candidateLayout(candidate, platform) {
  if (!candidate) return null;
  if (isAppBundle(candidate)) return layoutFromApp(candidate);
  const base = path.basename(candidate);
  if (candidate.includes(".app" + path.sep) || base === "electron" ||
      base === "electron.exe" || base === "Electron") {
    return layoutFromBinary(candidate);
  }
  return layoutFromDistRoot(candidate, platform);
}

// First candidate whose binary AND resources directory both exist, or
// null. Null stays a hard failure in the runner: a release machine that
// cannot run this proof cannot prove the bootstrap fix.
function resolveDist(candidates, platform, existsSync) {
  const exists = existsSync || ((p) => fs.existsSync(p));
  for (const candidate of candidates) {
    const layout = candidateLayout(candidate, platform);
    if (layout && exists(layout.binary) && exists(layout.resourcesDir)) return layout;
  }
  return null;
}

// The same layout after its root has been copied somewhere else (the
// packaged run launches from a scratch copy, never the installed dist).
function relocate(layout, newRoot) {
  const binary = path.join(newRoot, path.relative(layout.root, layout.binary));
  const resourcesDir = path.join(newRoot, path.relative(layout.root, layout.resourcesDir));
  return { root: newRoot, binary, resourcesDir, appAsar: path.join(resourcesDir, "app.asar") };
}

// The .app bundle inside a layout, or null on flat platforms.
function appBundlePath(layout) {
  const parts = layout.binary.split(path.sep);
  const appIdx = parts.findIndex((seg) => seg.endsWith(".app"));
  if (appIdx < 0) return null;
  return parts.slice(0, appIdx + 1).join(path.sep) || path.sep;
}

// How a packaged binary is launched per platform. Linux needs a virtual
// display, so it goes through xvfb-run; darwin and Windows launch the
// binary directly (there is no xvfb-run on macOS, which is one of the
// reasons the old runner could never pass there).
function launchSpec(platform, binary) {
  const flags = ["--no-sandbox", "--disable-gpu"];
  if (platform === "linux") return { command: "xvfb-run", args: ["-a", binary, ...flags] };
  return { command: binary, args: flags };
}

module.exports = {
  candidateLayout,
  layoutFromBinary,
  layoutFromApp,
  layoutFromDistRoot,
  resolveDist,
  relocate,
  appBundlePath,
  launchSpec,
};
