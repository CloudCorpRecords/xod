// Updates itself: when a newer release is out on GitHub, Xod downloads its installer in the
// background, checks it (SHA-256 from the release's SHA256SUMS.txt, the app's version, bundle id and
// signature), then asks "Restart now / Later". Later installs it when you quit. Your data is outside
// the app (~/Library/Application Support/Xod), so an update never touches it.
//
// Electron's built-in updater needs an Apple Developer ID signature; Xod's .dmg is ad-hoc signed,
// so this swaps the app bundle itself: a small script waits for Xod to quit, moves the new Xod.app
// into place (keeping the old one until the new one is in), and opens it again.
//
// Off when "Tell me about new versions" is off in Settings, with XOD_NO_UPDATE_CHECK=1, or from source.
const { app, dialog, shell } = require("electron");
const { spawn, spawnSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { Readable } = require("stream");
const { pipeline } = require("stream/promises");

const REPO = "CloudCorpRecords/xod";
const BUNDLE_ID = "dev.xod.desktop";
const FIRST_CHECK_MS = 45 * 1000;
const CHECK_EVERY_MS = 6 * 3600 * 1000;
const WORK = path.join(os.homedir(), "Library", "Caches", "Xod", "updates");

let ready = null;        // {version, appPath} once a checked update is staged
let busy = false;
let asked = new Set();   // versions we already asked about this run
let installing = false;

const ver = (v) => (String(v || "").match(/\d+/g) || [0]).slice(0, 3).map(Number);
const newer = (a, b) => { const x = ver(a), y = ver(b); for (let i = 0; i < 3; i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0); } return false; };
const log = (...a) => console.log("[updater]", ...a);

function currentBundle() {
  // .../Xod.app/Contents/MacOS/Xod → .../Xod.app
  return path.resolve(path.dirname(app.getPath("exe")), "..", "..");
}

function canReplace(bundle) {
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
  if (!r.ok) throw new Error(`GitHub said ${r.status}`);
  const d = await r.json();
  const version = String(d.tag_name || "").replace(/^v/, "");
  const arch = process.arch === "arm64" ? "arm64" : "x64";
  const dmg = (d.assets || []).find((a) => a.name.endsWith(`-${arch}.dmg`));
  const sums = (d.assets || []).find((a) => a.name === "SHA256SUMS.txt");
  return { version, name: d.name, notes: d.body || "", page: d.html_url, dmg, sums };
}

async function download(url, dest, tries = 3) {
  for (let i = 1; ; i++) {
    try {
      const r = await fetch(url, { headers: { "User-Agent": "Xod-updater" }, redirect: "follow", signal: AbortSignal.timeout(30 * 60 * 1000) });
      if (!r.ok || !r.body) throw new Error(`download failed (${r.status})`);
      await pipeline(Readable.fromWeb(r.body), fs.createWriteStream(dest));
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
async function stage(rel) {
  fs.rmSync(WORK, { recursive: true, force: true });
  fs.mkdirSync(WORK, { recursive: true });
  const dmgPath = path.join(WORK, rel.dmg.name);
  log("downloading", rel.dmg.name);
  await download(rel.dmg.browser_download_url, dmgPath);
  if (rel.sums) {
    const sumsPath = path.join(WORK, "SHA256SUMS.txt");
    await download(rel.sums.browser_download_url, sumsPath);
    const line = fs.readFileSync(sumsPath, "utf8").split("\n").find((l) => l.trim().endsWith(rel.dmg.name));
    const want = line && line.trim().split(/\s+/)[0].toLowerCase();
    const got = await sha256(dmgPath);
    if (!want || want !== got) throw new Error("the download's checksum doesn't match the release");
  } else {
    throw new Error("the release has no SHA256SUMS.txt to check the download against");
  }
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
  if (v !== rel.version) throw new Error(`the downloaded app is version ${v}, not ${rel.version}`);
  run("/usr/bin/codesign", ["--verify", "--deep", "--strict", staged]);
  spawnSync("/usr/bin/xattr", ["-dr", "com.apple.quarantine", staged]);
  return staged;
}

// A detached script swaps the bundles after this process exits, then (optionally) opens Xod again.
function swap(relaunch) {
  if (!ready || installing) return;
  installing = true;
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
  const child = spawn("/bin/bash", [script, String(process.pid), ready.appPath, target, relaunch ? "1" : "0"], {
    detached: true, stdio: "ignore",
  });
  child.unref();
  log("installing", ready.version, "into", target, relaunch ? "(then reopening)" : "(on quit)");
}

async function ask(win) {
  if (!ready || asked.has(ready.version)) return;
  asked.add(ready.version);
  const opts = {
    type: "info", buttons: ["Restart now", "Later"], defaultId: 0, cancelId: 1,
    message: `Xod ${ready.version} is ready to install`,
    detail: `You have ${app.getVersion()}. Restart to update now, or it installs when you quit Xod. Your chats, projects and settings stay as they are.`,
  };
  const { response } = win && !win.isDestroyed() ? await dialog.showMessageBox(win, opts) : await dialog.showMessageBox(opts);
  if (response === 0) {
    swap(true);
    app.quit();
  }
}

async function check(getWin, appUrl, { manual = false } = {}) {
  if (busy || installing) return;
  busy = true;
  try {
    if (ready) return await ask(getWin());
    if (!manual && !(await enabled(appUrl))) return;
    const rel = await latest();
    if (!newer(rel.version, app.getVersion())) {
      if (manual) await dialog.showMessageBox({ type: "info", message: "Xod is up to date", detail: `You have the newest version, ${app.getVersion()}.` });
      return;
    }
    const bundle = currentBundle();
    if (!rel.dmg || !canReplace(bundle)) {
      // Can't swap it in place (e.g. not in a folder Xod can write to): open the download page instead.
      if (manual || !asked.has(rel.version)) {
        asked.add(rel.version);
        const { response } = await dialog.showMessageBox({ type: "info", buttons: ["Download", "Later"], defaultId: 0, cancelId: 1,
          message: `Xod ${rel.version} is out`, detail: "Xod can't update itself where it's installed. Download the new version and drag it to Applications." });
        if (response === 0) shell.openExternal(rel.page);
      }
      return;
    }
    const appPath = await stage(rel);
    ready = { version: rel.version, appPath };
    await ask(getWin());
  } catch (e) {
    log("update check failed:", e.message || e);
    if (manual) dialog.showMessageBox({ type: "warning", message: "Couldn't update Xod", detail: String(e.message || e) });
  } finally {
    busy = false;
  }
}

function start(getWin, getAppUrl) {
  if (!app.isPackaged) return { check: () => {} };
  setTimeout(() => check(getWin, getAppUrl()), FIRST_CHECK_MS);
  setInterval(() => check(getWin, getAppUrl()), CHECK_EVERY_MS).unref();
  app.on("before-quit", () => { if (ready && !installing) swap(false); });   // "Later": install on the way out
  return { check: (manual = true) => check(getWin, getAppUrl(), { manual }) };
}

module.exports = { start, _test: { newer, canReplace, stage, swap, setReady: (r) => { ready = r; } } };
