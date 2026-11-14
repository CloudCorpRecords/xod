// Xod bootstrap: the first code that runs, before the app itself.
//
// Why this exists: v1.1.3 (2026-11-13) shipped with an app.asar that was missing a module its
// own main.js needs at startup, so the app died before any window, backend, or updater code
// could run. No code inside a package broken that way can ever load to fix itself, so the
// check and the repair live here, in a file that loads before the app proper and depends on
// nothing but Node itself. The packaged entry point (package.json "main") is this file.
//
// What it does, on every launch of the packaged app:
//   1. Opens app.asar and checks that every relative import reachable from the app entry
//      point resolves inside the archive (the same walk scripts/check_asar.py runs at
//      release time, ported here so the check also runs on the user's machine).
//   2. Checks the installed version against a list of known broken releases (1.1.3).
//   3. Healthy: hands over to the real main.js at once. No network, no delay beyond the
//      local archive walk.
//   4. Broken: does NOT load main.js (it would crash). Instead it repairs the install from
//      the same channel the updater uses (the latest GitHub release, the download checked
//      against the release's SHA256SUMS.txt), verifies the staged copy the same way before
//      anything is replaced, and swaps it in only once the new copy is proven. If the
//      download or the staged copy fails any check, the existing install is left exactly
//      as it was and the failure is reported plainly. A failed repair never bricks an app
//      that was only mostly broken, and never pretends to have worked.
//
// From source (not packaged) this file is a pass-through: it loads main.js directly.
//
// It also runs standalone, for support and for the stranded 1.1.3 rescue:
//   node bootstrap.js check  <Xod.app | app.asar>   print the verdict, exit 0/1/2
//   node bootstrap.js repair <Xod.app>              repair that install, exit 0/1
//
// This file intentionally uses no relative imports at load time. The single relative
// import further down loads the app entry point, and it only runs after the integrity
// verdict says the whole import graph resolves. Keep it that way.
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");
const { execFile } = require("child_process");

// ---------------------------------------------------------------------------
// Constants. The release channel mirrors electron/updater.js: same repo, same
// GitHub latest-release lookup, same artifact naming, same SHA256SUMS.txt check.
// ---------------------------------------------------------------------------

const REPO = "CloudCorpRecords/xod";
const RELEASES_PAGE = `https://github.com/${REPO}/releases/latest`;
const KNOWN_BROKEN_VERSIONS = ["1.1.3"];
const MAIN_ENTRY = "main.js"; // the real app entry this file hands over to

const ver = (v) => (String(v || "").match(/\d+/g) || [0]).slice(0, 3).map(Number);
const newer = (a, b) => {
  const x = ver(a), y = ver(b);
  for (let i = 0; i < 3; i++) {
    if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0);
  }
  return false;
};

// ---------------------------------------------------------------------------
// The asar walk. Same format parsing and the same resolution order Node uses,
// matching scripts/check_asar.py so the release gate and the launch check can
// never disagree about whether a package can run its own main process.
// ---------------------------------------------------------------------------

const REQUIRE_RE = /require\(\s*['"]([^'"]+)['"]\s*\)/g;
const RESOLVE_SUFFIXES = ["", ".js", ".json", ".node", "/index.js"];

function readAsarHeader(buf) {
  if (buf.length < 16) throw new Error("file is too small to be an asar archive");
  const headerPayloadSize = buf.readUInt32LE(4);
  const jsonSize = buf.readUInt32LE(12);
  const jsonBytes = buf.subarray(16, 16 + jsonSize);
  if (jsonBytes.length !== jsonSize) throw new Error("asar header is truncated");
  const tree = JSON.parse(jsonBytes.toString("utf8"));
  const dataStart = 8 + headerPayloadSize;
  if (dataStart > buf.length) throw new Error("asar header size runs past the end of the file");
  return { tree, dataStart };
}

function flattenAsar(tree, prefix = "", out = {}) {
  for (const [name, entry] of Object.entries(tree.files || {})) {
    const p = prefix + name;
    if (entry.files) flattenAsar(entry, p + "/", out);
    else out[p] = entry;
  }
  return out;
}

class AsarArchive {
  constructor(asarPath) {
    this.path = asarPath;
    this.data = fs.readFileSync(asarPath);
    const { tree, dataStart } = readAsarHeader(this.data);
    this.dataStart = dataStart;
    this.files = flattenAsar(tree);
  }

  readText(rel) {
    const entry = this.files[rel];
    if (!entry) return null;
    if (entry.unpacked) {
      const sibling = path.join(path.dirname(this.path), path.basename(this.path) + ".unpacked", rel);
      try { return fs.readFileSync(sibling, "utf8"); } catch (e) { return null; }
    }
    const offset = this.dataStart + Number(entry.offset);
    const size = Number(entry.size);
    return this.data.subarray(offset, offset + size).toString("utf8");
  }
}

function normalizeSpec(baseDir, spec) {
  const parts = baseDir.split("/").filter(Boolean);
  for (const seg of spec.split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg === "..") parts.pop();
    else parts.push(seg);
  }
  return parts.join("/");
}

function resolveInArchive(archive, baseDir, spec) {
  const target = normalizeSpec(baseDir, spec);
  for (const suffix of RESOLVE_SUFFIXES) {
    if (archive.files[target + suffix]) return target + suffix;
  }
  return null;
}

// Walk the import graph from the packaged entry point. Returns
// { problems: [...], visited: [...], version } where version comes from the
// package.json inside the archive.
function checkAsar(asarPath) {
  const archive = new AsarArchive(asarPath);
  const problems = [];
  let version = null;
  let entryRel = MAIN_ENTRY;
  const pkgText = archive.readText("package.json");
  if (pkgText) {
    try {
      const pkg = JSON.parse(pkgText);
      version = pkg.version || null;
      if (pkg.main) entryRel = String(pkg.main);
    } catch (e) {
      problems.push("package.json inside the archive is not valid JSON");
    }
  }
  entryRel = entryRel.replace(/^\.\//, "");
  if (!archive.files[entryRel]) {
    problems.push(`entry point ${entryRel} is not inside the archive`);
    return { problems, visited: [], version, fileCount: Object.keys(archive.files).length };
  }
  const visited = new Set();
  const stack = [entryRel];
  while (stack.length) {
    const rel = stack.pop();
    if (visited.has(rel) || !rel.endsWith(".js")) continue;
    visited.add(rel);
    const text = archive.readText(rel);
    if (text === null) {
      problems.push(`${rel}: listed in the archive but its bytes are unreadable`);
      continue;
    }
    const baseDir = rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : "";
    for (const m of text.matchAll(REQUIRE_RE)) {
      const spec = m[1];
      if (!spec.startsWith("./") && !spec.startsWith("../")) continue;
      const line = text.slice(0, m.index).split("\n").length;
      const found = resolveInArchive(archive, baseDir, spec);
      if (!found) problems.push(`${rel}:${line}: require "${spec}" does not resolve inside the archive`);
      else stack.push(found);
    }
  }
  return { problems, visited: [...visited], version, fileCount: Object.keys(archive.files).length };
}

function findAsar(target) {
  const st = fs.existsSync(target) ? fs.statSync(target) : null;
  if (st && st.isFile()) return target;
  for (const cand of [
    path.join(target, "Contents", "Resources", "app.asar"),
    path.join(target, "resources", "app.asar"),
    path.join(target, "app.asar"),
  ]) {
    if (fs.existsSync(cand)) return cand;
  }
  throw new Error(`no app.asar found at or under ${target}`);
}

// The launch verdict. Pure and local: no network, no side effects.
//   { action: "launch" | "repair", version, problems, reasons }
function decide(asarPath) {
  let result;
  try {
    result = checkAsar(asarPath);
  } catch (e) {
    return {
      action: "repair",
      version: null,
      problems: [`cannot read the package: ${e.message}`],
      reasons: ["the installed package cannot be read as an archive"],
    };
  }
  const reasons = [];
  if (result.problems.length) {
    reasons.push(`the package is incomplete: ${result.problems[0]}`);
  }
  if (result.version && KNOWN_BROKEN_VERSIONS.includes(result.version)) {
    reasons.push(`version ${result.version} is a known broken release`);
  }
  return {
    action: reasons.length ? "repair" : "launch",
    version: result.version,
    problems: result.problems,
    reasons,
  };
}

// ---------------------------------------------------------------------------
// Repair. Every side effect goes through an io object so the whole pipeline
// is testable offline. The contract the pipeline keeps:
//   - nothing outside the work dir is written until a staged replacement has
//     passed the checksum check AND the same integrity walk as the launch gate;
//   - any failure before the swap leaves the installed app byte-identical;
//   - the result is honest: { ok, stage, error } names where it stopped.
// ---------------------------------------------------------------------------

async function sha256File(file) {
  const h = crypto.createHash("sha256");
  await new Promise((resolve, reject) => {
    const s = fs.createReadStream(file);
    s.on("data", (d) => h.update(d));
    s.on("end", resolve);
    s.on("error", reject);
  });
  return h.digest("hex");
}

async function repair(appPath, io) {
  const fail = (stage, error) => ({ ok: false, stage, error: String(error && error.message || error) });
  let latest;
  try {
    latest = await io.fetchLatest();
  } catch (e) {
    return fail("fetch", e);
  }
  if (!latest || !latest.version) return fail("fetch", "the release channel returned no version");
  if (KNOWN_BROKEN_VERSIONS.includes(latest.version)) {
    return fail("fetch", `the latest release is ${latest.version}, which is itself a known broken release; there is nothing safe to repair to yet`);
  }
  let workDir;
  try {
    workDir = fs.mkdtempSync(path.join(io.workRoot(), "xod-bootstrap-"));
    io.report(`repairing Xod: downloading ${latest.artifactName} (${latest.version})`);
    const artifactPath = path.join(workDir, latest.artifactName);
    await io.download(latest.artifactUrl, artifactPath);
    const sumsText = await io.fetchText(latest.sumsUrl);
    const line = sumsText.split("\n").find((l) => l.trim().endsWith(latest.artifactName));
    const want = line ? line.trim().split(/\s+/)[0].toLowerCase() : null;
    const got = await sha256File(artifactPath);
    if (!want || want !== got) throw new Error("the download's checksum does not match the release");
    const stagedApp = await io.stageApp(artifactPath, workDir);
    const verdict = decide(findAsar(stagedApp));
    if (verdict.action !== "launch") {
      throw new Error(`the downloaded copy does not pass the integrity check: ${verdict.problems[0] || verdict.reasons[0]}`);
    }
    if (verdict.version !== latest.version) {
      throw new Error(`the downloaded copy reports version ${verdict.version}, not ${latest.version}`);
    }
    io.report(`repairing Xod: the new copy checks out, swapping it in`);
    await io.swapAndRelaunch({ stagedApp, targetApp: appPath, workDir });
    return { ok: true, stage: "done", version: latest.version };
  } catch (e) {
    return fail("repair", e);
  } finally {
    // The staged download is scratch either way. The installed app is only
    // ever changed by swapAndRelaunch, after every check above has passed.
    if (workDir && !(io.keepWork && io.keepWork())) {
      try { fs.rmSync(workDir, { recursive: true, force: true }); } catch (e) {}
    }
  }
}

// ---------------------------------------------------------------------------
// Platform io. macOS is the proven path (built and fixture-tested with this
// lane; the swap mirrors what updater.js already does in production: a waiter
// script moves the new bundle in once this process has quit, keeping the old
// bundle until the new one is in place). Windows design: the NSIS installer
// replaces a per-user install in place and reopens the app, exactly the
// channel updater.js uses there, so the bootstrap downloads the checked
// installer and hands over to it instead of moving files itself.
// ---------------------------------------------------------------------------

function workRootFor(platform) {
  if (platform === "darwin") return path.join(os.homedir(), "Library", "Caches", "Xod");
  return path.join(os.tmpdir(), "Xod");
}

function makeGitHubIo(platform, arch) {
  const https = require("https");
  const fetchJson = (url) => new Promise((resolve, reject) => {
    https.get(url, { headers: { Accept: "application/vnd.github+json", "User-Agent": "Xod-bootstrap" } }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return resolve(fetchJson(res.headers.location));
      }
      let body = "";
      res.on("data", (d) => { body += d; });
      res.on("end", () => {
        if (res.statusCode !== 200) return reject(new Error(`GitHub answered ${res.statusCode}`));
        try { resolve(JSON.parse(body)); } catch (e) { reject(e); }
      });
    }).on("error", reject);
  });
  const io = {
    workRoot: () => {
      const root = workRootFor(platform);
      fs.mkdirSync(root, { recursive: true });
      return root;
    },
    report: (msg) => console.log("[bootstrap]", msg),
    fetchLatest: async () => {
      const d = await fetchJson(`https://api.github.com/repos/${REPO}/releases/latest`);
      const version = String(d.tag_name || "").replace(/^v/, "");
      const isWin = platform === "win32";
      const artifact = (d.assets || []).find((a) => (isWin
        ? a.name.endsWith(`-${arch}-setup.exe`)
        : a.name.endsWith(`-${arch}.dmg`)));
      const sums = (d.assets || []).find((a) => a.name === "SHA256SUMS.txt");
      if (!artifact || !sums) throw new Error("the latest release does not carry the artifact for this computer");
      return {
        version,
        artifactName: artifact.name,
        artifactUrl: artifact.browser_download_url,
        sumsUrl: sums.browser_download_url,
      };
    },
    fetchText: (url) => new Promise((resolve, reject) => {
      https.get(url, { headers: { "User-Agent": "Xod-bootstrap" } }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          return resolve(io.fetchText(res.headers.location));
        }
        let body = "";
        res.on("data", (d) => { body += d; });
        res.on("end", () => (res.statusCode === 200 ? resolve(body) : reject(new Error(`download answered ${res.statusCode}`))));
      }).on("error", reject);
    }),
    download: (url, dest) => new Promise((resolve, reject) => {
      https.get(url, { headers: { "User-Agent": "Xod-bootstrap" } }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          return resolve(io.download(res.headers.location, dest));
        }
        if (res.statusCode !== 200) return reject(new Error(`download answered ${res.statusCode}`));
        const out = fs.createWriteStream(dest);
        res.pipe(out);
        out.on("finish", () => out.close(resolve));
        out.on("error", reject);
      }).on("error", reject);
    }),
  };
  return io;
}

function run(cmd, args) {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, (err, stdout, stderr) => (err ? reject(new Error(`${cmd} failed: ${stderr || err.message}`)) : resolve(stdout)));
  });
}

// macOS staging: mount the dmg, copy the .app out, unmount. Runs the same
// hdiutil route updater.js uses.
async function stageAppMac(artifactPath, workDir) {
  const mnt = path.join(workDir, "mnt");
  fs.mkdirSync(mnt, { recursive: true });
  await run("/usr/bin/hdiutil", ["attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", mnt, artifactPath]);
  try {
    const appName = fs.readdirSync(mnt).find((n) => n.endsWith(".app"));
    if (!appName) throw new Error("the downloaded disk image holds no .app");
    const staged = path.join(workDir, appName);
    await run("/usr/bin/ditto", [path.join(mnt, appName), staged]);
    return staged;
  } finally {
    try { await run("/usr/bin/hdiutil", ["detach", mnt, "-quiet"]); } catch (e) {}
  }
}

// macOS swap: a small script waits for this process to exit, moves the staged
// bundle into place (the old bundle is kept as a backup until the new one is
// verified in place, and restored if the move fails), then opens the app.
// Returns immediately; the caller quits so the script can proceed.
async function swapAndRelaunchMac({ stagedApp, targetApp, workDir }, pid) {
  const script = path.join(workDir, "swap.sh");
  const q = (s) => `'${String(s).replace(/'/g, "'\\''")}'`;
  const body = [
    "#!/bin/sh",
    `LOG=${q(path.join(workDir, "swap.log"))}`,
    `while kill -0 ${pid} 2>/dev/null; do sleep 0.5; done`,
    `BACKUP=${q(targetApp + ".bootstrap-backup")}`,
    `rm -rf "$BACKUP"`,
    `if mv ${q(targetApp)} "$BACKUP" && mv ${q(stagedApp)} ${q(targetApp)}; then`,
    `  if [ -f ${q(path.join(targetApp, "Contents", "Resources", "app.asar"))} ]; then`,
    `    rm -rf "$BACKUP"`,
    `    /usr/bin/open ${q(targetApp)} >> "$LOG" 2>&1`,
    "    exit 0",
    "  fi",
    "fi",
    `rm -rf ${q(targetApp)}`,
    `mv "$BACKUP" ${q(targetApp)}`,
    `echo "swap failed, previous install restored" >> "$LOG"`,
    "exit 1",
    "",
  ].join("\n");
  fs.writeFileSync(script, body, { mode: 0o755 });
  const { spawn } = require("child_process");
  spawn("/bin/sh", [script], { detached: true, stdio: "ignore" }).unref();
}

// Windows: run the checked installer detached and let it replace the install,
// the same hand-off updater.js performs on quit. There is no in-place file
// swap from inside a running Windows app; the installer owns replacement.
async function stageAppWin(artifactPath) {
  return artifactPath; // the installer is the staged artifact
}

async function swapAndRelaunchWin({ stagedApp }) {
  const { spawn } = require("child_process");
  spawn(stagedApp, ["/S"], { detached: true, stdio: "ignore" }).unref();
}

function makePlatformIo(platform = process.platform, arch = process.arch) {
  const io = makeGitHubIo(platform, arch);
  if (platform === "darwin") {
    io.stageApp = stageAppMac;
    io.swapAndRelaunch = (args) => swapAndRelaunchMac(args, process.pid);
    io.keepWork = () => true; // the swap script and its log live in the work dir
  } else if (platform === "win32") {
    io.stageApp = stageAppWin;
    io.swapAndRelaunch = swapAndRelaunchWin;
    // On Windows the staged app IS the artifact inside the work dir and the
    // installer runs from it after we exit, so the dir must survive repair().
    io.keepWork = () => true;
  }
  return io;
}

// ---------------------------------------------------------------------------
// Entry points.
// ---------------------------------------------------------------------------

async function cli(argv) {
  const [cmd, target] = argv;
  if ((cmd !== "check" && cmd !== "repair") || !target) {
    console.error("usage: node bootstrap.js check|repair <Xod.app | app.asar>");
    return 2;
  }
  let asarPath;
  try {
    asarPath = findAsar(target);
  } catch (e) {
    console.error(`✋ ${e.message}`);
    return 2;
  }
  const verdict = decide(asarPath);
  console.log(`  archive: ${asarPath}`);
  console.log(`  version: ${verdict.version || "unknown"}`);
  if (verdict.action === "launch") {
    console.log("✓ The package is complete: every relative require resolves inside the archive.");
    return 0;
  }
  console.log("✋ This install cannot start:");
  for (const p of verdict.problems) console.log(`  ${p}`);
  for (const r of verdict.reasons) console.log(`  ${r}`);
  if (cmd === "check") return 1;
  const appPath = fs.statSync(target).isDirectory() ? target : path.resolve(target, "..", "..", "..");
  const result = await repair(appPath, makePlatformIo());
  if (result.ok) {
    console.log(`✓ Repaired: Xod ${result.version} is staged to replace the broken install when this process exits.`);
    return 0;
  }
  console.log(`✋ Repair stopped at ${result.stage}: ${result.error}`);
  console.log(`  Nothing was replaced. Download the current version once from ${RELEASES_PAGE} and the app updates itself from there.`);
  return 1;
}

function electronEntry() {
  const { app, dialog } = require("electron");
  // Keep this a literal: the release gate's archive walk (check_asar.py) and
  // the walk above only follow literal relative imports, and this one is what
  // makes the gate prove the whole app graph, not just this file. It runs
  // only after the verdict says every import in that graph resolves.
  const loadMain = () => require("./main.js");
  if (!app.isPackaged || process.env.XOD_BOOTSTRAP_OFF) {
    loadMain();
    return;
  }
  const asarPath = path.join(process.resourcesPath, "app.asar");
  const verdict = decide(asarPath);
  if (verdict.action === "launch") {
    loadMain();
    return;
  }
  // Broken package: main.js would die at its first missing module, and the
  // raw Electron error box is what a stranded user sees today. Show the
  // repair instead, in plain words, and never load the broken entry.
  app.whenReady().then(async () => {
    const bundle = path.resolve(path.dirname(app.getPath("exe")), "..", "..");
    dialog.showMessageBoxSync({
      type: "warning",
      message: "Xod needs to repair itself",
      detail: `This copy of Xod is incomplete and cannot start (${verdict.reasons[0] || "package check failed"}). ` +
        "Xod will download a fresh copy and replace only the app. Your data is kept where it is and is not touched.",
      buttons: ["Repair Xod"],
    });
    const result = await repair(bundle, makePlatformIo());
    if (result.ok) {
      dialog.showMessageBoxSync({
        type: "info",
        message: "Xod is repaired",
        detail: `Xod ${result.version} takes over when this window closes.`,
        buttons: ["Restart Xod"],
      });
      app.quit();
      return;
    }
    dialog.showMessageBoxSync({
      type: "error",
      message: "Xod could not repair itself",
      detail: `The repair stopped (${result.stage}: ${result.error}). Nothing was replaced and your data is untouched. ` +
        `Download the current version once from ${RELEASES_PAGE}; after that, updates are automatic again.`,
      buttons: ["Quit"],
    });
    app.quit();
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    KNOWN_BROKEN_VERSIONS,
    AsarArchive,
    checkAsar,
    decide,
    findAsar,
    repair,
    sha256File,
    makePlatformIo,
    newer,
  };
}

const underElectron = Boolean(process.versions && process.versions.electron);
if (require.main === module) {
  if (underElectron) {
    // The packaged (or source) entry point: Electron loads this file as the
    // main module, so this branch is the launch path.
    electronEntry();
  } else if (process.argv.length > 2) {
    cli(process.argv.slice(2)).then((code) => process.exit(code));
  } else {
    console.error("usage: node bootstrap.js check|repair <Xod.app | app.asar>");
  }
}
