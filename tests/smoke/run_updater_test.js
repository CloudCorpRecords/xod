// Xod updating itself (electron/updater.js), against a pretend release on this Mac: a tiny ad-hoc signed
// "Xod.app" in a .dmg, served with its SHA256SUMS.txt from a local server. Checks the download is verified
// (checksum, version, bundle id, signature), a bad one is refused, and the swap puts the new app in place
// of the old one after Xod quits. Nothing touches /Applications, GitHub or your real Xod.
//   node tests/smoke/run_updater_test.js
const { spawn, spawnSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const Module = require("module");

const TMP = fs.mkdtempSync("/private/tmp/xod-updater-");
const results = [];
const check = (name, ok, detail = "") => { results.push([name, ok]); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? "  — " + detail : ""}`); };

// A stand-in for the electron module.
let exePath = "";
const fakeElectron = {
  app: { isPackaged: true, getVersion: () => "0.2.0", getPath: () => exePath, on: () => {} },
  dialog: { showMessageBox: async () => ({ response: 1 }) }, shell: { openExternal: () => {} },
};
const load = Module._load;
Module._load = function (req, ...rest) { return req === "electron" ? fakeElectron : load.call(this, req, ...rest); };
process.env.HOME = TMP;  // the updater keeps its work in ~/Library/Caches/Xod/updates
const updater = require(path.resolve(__dirname, "../../electron/updater.js"));

function fakeApp(dir, version, { id = "dev.xod.desktop", sign = true } = {}) {
  const app = path.join(dir, "Xod.app");
  fs.mkdirSync(path.join(app, "Contents", "MacOS"), { recursive: true });
  fs.writeFileSync(path.join(app, "Contents", "Info.plist"), `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>CFBundleIdentifier</key><string>${id}</string><key>CFBundleShortVersionString</key><string>${version}</string>
<key>CFBundleExecutable</key><string>Xod</string><key>CFBundlePackageType</key><string>APPL</string></dict></plist>`);
  fs.writeFileSync(path.join(app, "Contents", "MacOS", "Xod"), `#!/bin/sh\necho ${version}\n`, { mode: 0o755 });
  if (sign) spawnSync("codesign", ["--force", "--deep", "-s", "-", app]);
  return app;
}

function makeDmg(name, version, opts) {
  const src = fs.mkdtempSync(path.join(TMP, "src-"));
  fakeApp(src, version, opts);
  const dmg = path.join(TMP, "site", name);
  fs.mkdirSync(path.dirname(dmg), { recursive: true });
  fs.rmSync(dmg, { force: true });
  const r = spawnSync("hdiutil", ["create", "-quiet", "-fs", "HFS+", "-srcfolder", src, "-volname", "Install Xod", "-format", "UDZO", dmg]);
  if (r.status !== 0) throw new Error("hdiutil create failed: " + r.stderr);
  return dmg;
}

const sha = (f) => crypto.createHash("sha256").update(fs.readFileSync(f)).digest("hex");

async function main() {
  check("version compare", updater._test.newer("0.3.0", "0.2.0") && !updater._test.newer("0.2.0", "0.2.0") && updater._test.newer("0.10.0", "0.9.9"));

  const site = path.join(TMP, "site");
  fs.mkdirSync(site, { recursive: true });
  const server = http.createServer((req, res) => {
    const f = path.join(site, decodeURIComponent(req.url.slice(1)));
    if (!fs.existsSync(f)) { res.writeHead(404); return res.end(); }
    res.writeHead(200, { Connection: "close" }); fs.createReadStream(f).pipe(res);
  }).listen(0, "127.0.0.1");
  await new Promise((r) => server.once("listening", r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const rel = (name, sums = "SHA256SUMS.txt") => ({ version: "0.3.0", dmg: { name, browser_download_url: `${base}/${name}` },
    sums: sums && { name: "SHA256SUMS.txt", browser_download_url: `${base}/${sums}` } });

  // A good release.
  const good = makeDmg("Xod-0.3.0-arm64.dmg", "0.3.0");
  fs.writeFileSync(path.join(site, "SHA256SUMS.txt"), `${sha(good)}  Xod-0.3.0-arm64.dmg\n`);
  let staged = null;
  try { staged = await updater._test.stage(rel("Xod-0.3.0-arm64.dmg")); } catch (e) { check("a good release is downloaded and checked", false, e.message); }
  if (staged) check("a good release is downloaded and checked", fs.existsSync(path.join(staged, "Contents", "Info.plist")));

  // A tampered download: the checksum doesn't match.
  fs.writeFileSync(path.join(site, "BADSUMS.txt"), `${"0".repeat(64)}  Xod-0.3.0-arm64.dmg\n`);
  await updater._test.stage(rel("Xod-0.3.0-arm64.dmg", "BADSUMS.txt")).then(() => check("a download that doesn't match its checksum is refused", false),
    (e) => check("a download that doesn't match its checksum is refused", /checksum/.test(e.message), e.message));
  await updater._test.stage(rel("Xod-0.3.0-arm64.dmg", null)).then(() => check("…and so is one with no checksum", false),
    (e) => check("…and so is one with no checksum", /SHA256SUMS/.test(e.message), e.message));

  // The wrong version inside, an app that isn't Xod, and an unsigned one.
  for (const [label, opts, want, version] of [["the wrong version inside", {}, /version/, "0.2.9"], ["an app that isn't Xod", { id: "com.example.other" }, /isn't Xod/, "0.3.0"],
    ["an unsigned app", { sign: false }, /codesign/, "0.3.0"]]) {
    const name = `Xod-0.3.0-arm64.dmg`;
    const dmg = makeDmg(name, version, opts);
    fs.writeFileSync(path.join(site, "SHA256SUMS.txt"), `${sha(dmg)}  ${name}\n`);
    await updater._test.stage(rel(name)).then(() => check(`${label} is refused`, false), (e) => check(`${label} is refused`, want.test(e.message), e.message));
  }

  // The swap: the "installed" Xod is replaced after this pretend Xod process exits, the old one is gone.
  const good2 = makeDmg("Xod-0.3.0-arm64.dmg", "0.3.0");
  fs.writeFileSync(path.join(site, "SHA256SUMS.txt"), `${sha(good2)}  Xod-0.3.0-arm64.dmg\n`);
  staged = await updater._test.stage(rel("Xod-0.3.0-arm64.dmg"));
  const apps = path.join(TMP, "Applications");
  fs.mkdirSync(apps);
  const installed = fakeApp(apps, "0.2.0");
  exePath = path.join(installed, "Contents", "MacOS", "Xod");
  check("it can update an app in a folder it can write to", updater._test.canReplace(installed));
  check("…but not one running from the disk image", !updater._test.canReplace("/Volumes/Install Xod/Xod.app"));

  // swap() waits for Xod (process.pid) to exit: stand in a short-lived process for Xod.
  const u2path = path.resolve(__dirname, "../../electron/updater.js");
  delete require.cache[u2path];
  const u2 = require(u2path);
  const holder = spawn("sleep", ["2"]);
  const realPid = process.pid;
  Object.defineProperty(process, "pid", { value: holder.pid, configurable: true });
  u2._test.setReady({ version: "0.3.0", appPath: staged });
  u2._test.swap(false);
  Object.defineProperty(process, "pid", { value: realPid, configurable: true });
  const before = spawnSync(path.join(installed, "Contents", "MacOS", "Xod")).stdout.toString().trim();
  check("it waits for Xod to quit before touching the app", before === "0.2.0", before);
  let after = "";
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, 250));
    try { after = spawnSync(path.join(installed, "Contents", "MacOS", "Xod")).stdout.toString().trim(); } catch (e) {}
    if (after === "0.3.0") break;
  }
  check("then the new version is in place", after === "0.3.0", after);
  check("…and the old copy is cleaned up", !fs.existsSync(installed + ".previous") && !fs.existsSync(staged));

  server.close();
  const failed = results.filter(([, ok]) => !ok).map(([n]) => n);
  console.log(`\nSUMMARY ${failed.length ? "FAIL" : "PASS"} ${JSON.stringify(failed)}`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
