// Updates itself. Xod asks GitHub for the latest release when it starts, every hour, and when you come
// back to the window; the page shows where things stand (the version pill at the bottom of the sidebar)
// with an Update button. A newer version downloads in the background and is checked: SHA-256 from the
// release's SHA256SUMS.txt, the app's version, bundle id and signature. "Update" then restarts into it;
// if you don't press it, it installs when you quit. Your data is outside the app
// (~/Library/Application Support/Xod), so an update never touches it.
//
// Electron's built-in updater needs an Apple Developer ID signature; Xod's .dmg is ad-hoc signed, so this
// swaps the app bundle itself: a small script waits for Xod to quit, moves the new Xod.app into place
// (keeping the old one until the new one is in), and opens it again.
//
// On Windows the release has an installer (Xod-<version>-x64-setup.exe, a per-user NSIS installer) instead:
// it's downloaded and checked against SHA256SUMS.txt the same way, then run silently when Xod quits, and it
// replaces the installed Xod in place (data in %APPDATA%\Xod is untouched) and opens it again.
//
// Automatic checks are off when "Tell me about new versions" is off, with XOD_NO_UPDATE_CHECK=1, or from
// source; pressing "Check now" always checks.
const { app, dialog, shell } = require("electron");
const { spawn, spawnSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { Readable, Transform } = require("stream");
const { pipeline } = require("stream/promises");

const REPO = "CloudCorpRecords/xod";
const BUNDLE_ID = "dev.xod.desktop";
const FIRST_CHECK_MS = 8 * 1000;
const CHECK_EVERY_MS = 3600 * 1000;
const ON_FOCUS_AFTER_MS = 15 * 60 * 1000;
const WIN = process.platform === "win32";
const WORK = WIN ? path.join(os.tmpdir(), "Xod", "updates") : path.join(os.homedir(), "Library", "Caches", "Xod", "updates");

// What the page sees. state: idle | checking | uptodate | available | downloading | ready | installing | manual | error
let S = { state: "idle", current: app.getVersion(), latest: null, name: null, page: null, progress: 0, error: null, checkedAt: 0, packaged: app.isPackaged };
let ready = null;          // {version, appPath} once a checked update is staged
let rel = null;            // the latest release, when it's newer
let staging = null;        // the download in flight (a promise)
let installAfterStage = false;
let installing = false;
const listeners = [];

const ver = (v) => (String(v || "").match(/\d+/g) || [0]).slice(0, 3).map(Number);
const newer = (a, b) => { const x = ver(a), y = ver(b); for (let i = 0; i < 3; i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0); } return false; };
const log = (...a) => console.log("[updater]", ...a);

function set(patch) {
  S = { ...S, ...patch };
  for (const fn of listeners) { try { fn(S); } catch (e) {} }
}

function currentBundle() {
  // .../Xod.app/Contents/MacOS/Xod → .../Xod.app
  return path.resolve(path.dirname(app.getPath("exe")), "..", "..");
}

function canReplace(bundle) {
  if (WIN) return true;                 // the installer replaces it, wherever it is
  if (!bundle.endsWith(".app") || bundle.includes("/AppTranslocation/") || bundle.startsWith("/Volumes/")) return false;
  try { fs.accessSync(path.dirname(bundle), fs.constants.W_OK); fs.accessSync(bundle, fs.constants.W_OK); return true; } catch (e) { return false; }
}

async function enabled(appUrl) {
  if (process.env.XOD_NO_UPDATE_CHECK) return false;
  try {
    const r = await fetch(`${appUrl}/api/update`, { signal: AbortSignal.timeout(5000) });
    const j = await r.json();
    return j.enabled !== false;
  } catch (e) {
    return true;
  }
}

async function latest() {
  const r = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
    headers: { Accept: "application/vnd.github+json", "User-Agent": "Xod-updater" }, signal: AbortSignal.timeout(15000),
  });
  if (!r.ok) throw new Error(r.status === 403 ? "GitHub is limiting requests right now; try again in a bit" : `GitHub said ${r.status}`);
  const d = await r.json();
  const version = String(d.tag_name || "").replace(/^v/, "");
  const arch = process.arch === "arm64" ? "arm64" : "x64";
  // "dmg" is the download for this computer: the .dmg on a Mac, the setup .exe on Windows.
  const dmg = (d.assets || []).find((a) => (WIN ? a.name.endsWith(`-${arch}-setup.exe`) : a.name.endsWith(`-${arch}.dmg`)));
  const sums = (d.assets || []).find((a) => a.name === "SHA256SUMS.txt");
  return { version, name: d.name, page: d.html_url, dmg, sums };
}

async function download(url, dest, onBytes, tries = 3) {
  for (let i = 1; ; i++) {
    try {
      const r = await fetch(url, { headers: { "User-Agent": "Xod-updater" }, redirect: "follow", signal: AbortSignal.timeout(30 * 60 * 1000) });
      if (!r.ok || !r.body) throw new Error(`download failed (${r.status})`);
      const total = Number(r.headers.get("content-length")) || 0;
      let got = 0;
      const count = new Transform({ transform(chunk, _e, cb) { got += chunk.length; if (onBytes) onBytes(got, total); cb(null, chunk); } });
      await pipeline(Readable.fromWeb(r.body), count, fs.createWriteStream(dest));
      return;
    } catch (e) {
      if (i >= tries) throw e;
      await new Promise((r) => setTimeout(r, 1000 * i));   // a dropped connection: try again
    }
  }
}

function sha256(file) {
  return new Promise((resolve, reject) => {
    const h = crypto.createHash("sha256");
    fs.createReadStream(file).on("data", (c) => h.update(c)).on("end", () => resolve(h.digest("hex"))).on("error", reject);
  });
}

function run(cmd, args) {
  const r = spawnSync(cmd, args, { encoding: "utf8" });
  if (r.status !== 0) throw new Error(`${path.basename(cmd)} failed: ${(r.stderr || r.stdout || "").trim().slice(0, 300)}`);
  return (r.stdout || "").trim();
}

function plist(appPath, key) {
  return run("/usr/bin/plutil", ["-extract", key, "raw", "-o", "-", path.join(appPath, "Contents", "Info.plist")]);
}

// Download, verify and unpack; returns the staged Xod.app.
async function stage(release, onProgress) {
  fs.rmSync(WORK, { recursive: true, force: true });
  fs.mkdirSync(WORK, { recursive: true });
  if (!release.sums) throw new Error("the release has no SHA256SUMS.txt to check the download against");
  const dmgPath = path.join(WORK, release.dmg.name);
  log("downloading", release.dmg.name);
  await download(release.dmg.browser_download_url, dmgPath, (got, total) => onProgress && total && onProgress(got / total));
  const sumsPath = path.join(WORK, "SHA256SUMS.txt");
  await download(release.sums.browser_download_url, sumsPath);
  const line = fs.readFileSync(sumsPath, "utf8").split("\n").find((l) => l.trim().endsWith(release.dmg.name));
  const want = line && line.trim().split(/\s+/)[0].toLowerCase();
  const got = await sha256(dmgPath);
  if (!want || want !== got) throw new Error("the download's checksum doesn't match the release");
  if (WIN) return dmgPath;              // the checked installer; it runs when Xod quits (swap)
  const mnt = fs.mkdtempSync(path.join(os.tmpdir(), "xod-update-"));
  const staged = path.join(WORK, "Xod.app");
  run("/usr/bin/hdiutil", ["attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", mnt, dmgPath]);
  try {
    run("/usr/bin/ditto", [path.join(mnt, "Xod.app"), staged]);
  } finally {
    spawnSync("/usr/bin/hdiutil", ["detach", mnt, "-quiet"]);
  }
  fs.rmSync(dmgPath, { force: true });
  if (plist(staged, "CFBundleIdentifier") !== BUNDLE_ID) throw new Error("the downloaded app isn't Xod");
  const v = plist(staged, "CFBundleShortVersionString");
  if (v !== release.version) throw new Error(`the downloaded app is version ${v}, not ${release.version}`);
  run("/usr/bin/codesign", ["--verify", "--deep", "--strict", staged]);
  spawnSync("/usr/bin/xattr", ["-dr", "com.apple.quarantine", staged]);
  return staged;
}

// A detached script swaps the bundles after this process exits, then (optionally) opens Xod again.
function swap(relaunch) {
  if (!ready || installing) return false;
  installing = true;
  if (WIN) {
    // Silent per-user install over the current one; --force-run opens Xod again afterwards.
    const child = spawn(ready.appPath, relaunch ? ["/S", "--force-run"] : ["/S"], { detached: true, stdio: "ignore" });
    child.unref();
    log("installing", ready.version, relaunch ? "(then reopening)" : "(on quit)");
    return true;
  }
  const target = currentBundle();
  const script = path.join(WORK, "install.sh");
  fs.writeFileSync(script, `#!/bin/bash
# Written by Xod's updater (electron/updater.js).
PID="$1"; NEW="$2"; OLD="$3"; RELAUNCH="$4"
for i in $(seq 1 120); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
BACKUP="$OLD.previous"
rm -rf "$BACKUP"
if mv "$OLD" "$BACKUP" && mv "$NEW" "$OLD"; then
  rm -rf "$BACKUP"
else
  [ -d "$OLD" ] || mv "$BACKUP" "$OLD"
fi
[ "$RELAUNCH" = "1" ] && open "$OLD"
`, { mode: 0o700 });
  const child = spawn("/bin/bash", [script, String(process.pid), ready.appPath, target, relaunch ? "1" : "0"], { detached: true, stdio: "ignore" });
  child.unref();
  log("installing", ready.version, "into", target, relaunch ? "(then reopening)" : "(on quit)");
  return true;
}

function restartInto() {
  set({ state: "installing", progress: 1 });
  if (swap(true)) setTimeout(() => app.quit(), 300);
}

// Starts (or joins) the background download of the newer release.
function startStaging() {
  if (staging || ready || !rel) return staging;
  set({ state: "downloading", progress: 0, error: null });
  staging = stage(rel, (p) => set({ state: "downloading", progress: p }))
    .then((appPath) => {
      ready = { version: rel.version, appPath };
      set({ state: "ready", progress: 1 });
      if (installAfterStage) restartInto();
    })
    .catch((e) => {
      log("download failed:", e.message || e);
      installAfterStage = false;
      set({ state: "error", error: `Couldn't download ${rel.version}: ${e.message || e}` });
    })
    .finally(() => { staging = null; });
  return staging;
}

let checking = null;
async function check({ manual = false, appUrl = "" } = {}) {
  if (!app.isPackaged) { set({ state: "idle", error: null }); return S; }
  if (installing) return S;
  if (checking) return checking;
  if (ready) { set({ state: "ready" }); return S; }
  if (staging) return S;
  checking = (async () => {
    try {
      if (!manual && !(await enabled(appUrl))) return S;
      set({ state: "checking", error: null });
      const r = await latest();
      const checkedAt = Date.now();
      if (!newer(r.version, app.getVersion())) {
        rel = null;
        set({ state: "uptodate", latest: r.version, name: r.name, page: r.page, checkedAt });
        return S;
      }
      rel = r;
      if (!r.dmg || !canReplace(currentBundle())) {
        set({ state: "manual", latest: r.version, name: r.name, page: r.page, checkedAt,
              error: WIN ? "There's no Windows installer in this release yet. Download it from the release page."
                     : "Xod can't replace itself where it's installed. Download it and drag it to Applications." });
        return S;
      }
      set({ state: "available", latest: r.version, name: r.name, page: r.page, checkedAt });
      startStaging();     // download in the background, so "Update" is one click
      return S;
    } catch (e) {
      log("check failed:", e.message || e);
      set({ state: "error", error: `Couldn't check for updates: ${e.message || e}`, checkedAt: Date.now() });
      return S;
    } finally {
      checking = null;
    }
  })();
  return checking;
}

// "Update": restart into the new version now (downloading first if it isn't ready yet).
async function install() {
  if (installing) return S;
  if (ready) { restartInto(); return S; }
  if (S.state === "manual" && S.page) { shell.openExternal(S.page); return S; }
  if (!rel) await check({ manual: true });
  if (ready) { restartInto(); return S; }
  if (!rel || S.state === "manual") return S;
  installAfterStage = true;
  startStaging();
  return S;
}

function start(getWin, getAppUrl) {
  if (!app.isPackaged) return { status: () => S, check: async () => S, install: async () => S, onState: () => {}, menuCheck: () => {} };
  setTimeout(() => check({ appUrl: getAppUrl() }), FIRST_CHECK_MS);
  setInterval(() => check({ appUrl: getAppUrl() }), CHECK_EVERY_MS).unref();
  app.on("browser-window-focus", () => { if (Date.now() - (S.checkedAt || 0) > ON_FOCUS_AFTER_MS) check({ appUrl: getAppUrl() }); });
  app.on("before-quit", () => { if (ready && !installing) swap(false); });   // not pressed: install on the way out

  // Xod → Check for Updates…: check now and say what happened.
  async function menuCheck() {
    const st = await check({ manual: true, appUrl: getAppUrl() });
    const win = getWin();
    const show = (o) => (win && !win.isDestroyed() ? dialog.showMessageBox(win, o) : dialog.showMessageBox(o));
    if (st.state === "uptodate") return show({ type: "info", message: "Xod is up to date", detail: `You have the newest version, ${S.current}.` });
    if (st.state === "error") return show({ type: "warning", message: "Couldn't check for updates", detail: st.error || "" });
    if (st.state === "manual") {
      const { response } = await show({ type: "info", buttons: ["Download", "Later"], defaultId: 0, cancelId: 1, message: `Xod ${st.latest} is out`, detail: st.error });
      if (response === 0) shell.openExternal(st.page);
      return;
    }
    const { response } = await show({ type: "info", buttons: ["Update", "Later"], defaultId: 0, cancelId: 1, message: `Xod ${st.latest} is out`,
      detail: `You have ${S.current}. Update downloads it, checks it and restarts Xod. Your chats, projects and settings stay as they are.` });
    if (response === 0) install();
  }

  return {
    status: () => S,
    check: () => check({ manual: true, appUrl: getAppUrl() }),
    install,
    onState: (fn) => listeners.push(fn),
    menuCheck,
  };
}

module.exports = { start, _test: { newer, canReplace, stage, swap, check, install, status: () => S, onState: (fn) => listeners.push(fn), setReady: (r) => { ready = r; } } };
