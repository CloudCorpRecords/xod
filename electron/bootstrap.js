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
//      against the release's SHA256SUMS.txt), verifies the staged copy before anything
//      is replaced (an app bundle with the same walk; an installer by its checksum,
//      which is the only verification an installer carries), and swaps it in only once
//      the new copy is proven. If the
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

// The archive this file checks is a real file on disk, and every read of it
// must reach the real filesystem. Inside a packaged Electron main process
// that is NOT what require("fs") does: Electron's asar-aware fs layer
// intercepts any path ending in .asar, treating the archive itself as a
// directory in the archive scheme, so fs.readFileSync(app.asar) throws
// "ENOENT, not found in ...app.asar" and fs.statSync(app.asar).isFile()
// returns false. That is the v1.1.4 field failure (2026-11-14): this
// bootstrap's decide() threw on the app's own archive on EVERY launch, its
// catch returned "repair", and the repair's staged-copy check failed the
// same way, so a healthy, checksum-verified install could never start.
// Every release-gate check ran under plain Node, where fs is unpatched,
// and passed. Electron ships the unpatched module as "original-fs" for
// exactly this case; use it whenever the runtime is Electron, and plain fs
// everywhere else (the standalone CLI, the test suites), where original-fs
// does not exist. Semantics for every non-archive path are identical.
const fs = (() => {
  if (process.versions && process.versions.electron) {
    try { return require("original-fs"); } catch (e) {
      // Never silent: falling back to the asar-patched fs inside Electron is
      // the v1.1.4 failure mode, so say so where support can see it.
      try { console.error("xod bootstrap: original-fs unavailable in Electron, falling back to fs: " + (e && e.message)); } catch (_) { /* keep loading */ }
    }
  }
  return require("fs");
})();
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

// Version strings reach the known-broken gate from two places (package.json
// inside an archive, a release tag) and they do not always arrive clean: a
// leading "v" or build metadata must not let a known broken release slip
// past the comparison, and a version that cannot be read at all returns
// null here so the caller treats the copy as unproven, never as healthy.
const normalizeVersion = (v) => {
  if (v === null || v === undefined) return null;
  const core = String(v).trim().replace(/^v/i, "").split("+")[0].split("-")[0];
  return /^\d+(\.\d+)*$/.test(core) ? core : null;
};
const isKnownBroken = (v) => {
  const n = normalizeVersion(v);
  return n !== null && KNOWN_BROKEN_VERSIONS.some((k) => normalizeVersion(k) === n);
};

// ---------------------------------------------------------------------------
// Guardian coordination (AGE-72). Xod Partner's update guardian runs a
// health battery after an updater swap and can roll the install back while
// the battery runs. A second swap landing in the middle of that verdict
// interleaves with the rollback and can leave a mixed install (the bundle
// at one version, the backend at another), so the guardian publishes
// battery-in-progress.json in its state dir for exactly the span of a
// verdict, and every Electron swap path defers while a fresh marker
// exists. The marker reader is duplicated in updater.js on purpose: this
// file may not import siblings before its own verdict (see the header).
// The freshness ceiling sits well above the battery's worst case (health
// wait plus chat, task and permission probes), so an abandoned marker
// from a crashed guardian can never block swaps forever.
// ---------------------------------------------------------------------------
const GUARDIAN_BATTERY_MAX_AGE_MS = 20 * 60 * 1000;
function guardianStateDir(home = os.homedir()) {
  return path.join(home, "Library", "Application Support", "XodPartner");
}
function guardianBatteryActive(stateDir = guardianStateDir(), now = Date.now()) {
  try {
    const raw = fs.readFileSync(path.join(stateDir, "battery-in-progress.json"), "utf8");
    const at = Date.parse(JSON.parse(raw).at || "");
    return Number.isFinite(at) && now - at >= 0 && now - at < GUARDIAN_BATTERY_MAX_AGE_MS;
  } catch (e) { return false; }
}

// The installed bundle's own version, read from its app.asar the same way
// the verdict reads it. Null when it cannot be read: an unreadable package
// is the core repair case, and an unreadable version proves nothing about
// which side of the floor the install sits on.
function installedVersionOf(appPath) {
  try { return decide(findAsar(appPath)).version; } catch (e) { return null; }
}

// ---------------------------------------------------------------------------
// Repair loop hardening (2026-11-22). The field failure that day: an update
// guardian rolled a healthy install back to a two-week-old build whose own
// bootstrap cannot read its archive under Electron, so every launch showed
// the repair dialog and every repair failed the same way — an endless loop
// with no memory of having tried. Two mechanisms break that class of loop:
//
//   1. A repair attempt counter, persisted in the app's data dir (which no
//      app swap touches): after MAX_REPAIR_ATTEMPTS failed repairs against
//      the same installed version, the bootstrap stops offering repairs,
//      writes one plain report file, and says so once. A healthy launch
//      clears the counter for that version.
//   2. A healthy-launch marker: every packaged launch whose verdict is
//      "launch" writes bootstrap-health.json (version + time) into the
//      data dir. The macOS repair swap script waits for a FRESH marker
//      naming the new version before it deletes the previous bundle, so a
//      swapped-in copy that cannot actually start rolls itself back
//      instead of stranding the install (see buildSwapScriptMac).
// ---------------------------------------------------------------------------
const MAX_REPAIR_ATTEMPTS = 3;

function xodDataDir(home = os.homedir(), platform = process.platform) {
  if (platform === "darwin") return path.join(home, "Library", "Application Support", "Xod");
  if (platform === "win32") {
    return path.join(process.env.APPDATA || path.join(home, "AppData", "Roaming"), "Xod");
  }
  return path.join(home, ".local", "share", "Xod");
}

function healthMarkerPath(dataDir) { return path.join(dataDir, "bootstrap-health.json"); }
function repairStatePath(dataDir) { return path.join(dataDir, "bootstrap-repair.json"); }

function readRepairState(dataDir) {
  try {
    const data = JSON.parse(fs.readFileSync(repairStatePath(dataDir), "utf8"));
    if (data && typeof data === "object" && data.attempts) return data;
  } catch (e) { /* no state yet is a clean slate */ }
  return { attempts: {} };
}

function repairAttempts(dataDir, version) {
  const n = Number((readRepairState(dataDir).attempts || {})[String(version || "unknown")]);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

function recordRepairAttempt(dataDir, version) {
  const key = String(version || "unknown");
  const state = readRepairState(dataDir);
  state.attempts[key] = repairAttempts(dataDir, version) + 1;
  state.last_attempt = { version: key, at: new Date().toISOString() };
  try {
    fs.mkdirSync(dataDir, { recursive: true });
    fs.writeFileSync(repairStatePath(dataDir), JSON.stringify(state, null, 1));
  } catch (e) { /* the counter is best-effort; a failed write must not block a repair */ }
  return state.attempts[key];
}

function clearRepairAttempts(dataDir, version) {
  const key = String(version || "unknown");
  const state = readRepairState(dataDir);
  if (!state.attempts[key]) return;
  delete state.attempts[key];
  try {
    fs.mkdirSync(dataDir, { recursive: true });
    fs.writeFileSync(repairStatePath(dataDir), JSON.stringify(state, null, 1));
  } catch (e) { /* best-effort, same as recordRepairAttempt */ }
}

// A packaged launch whose verdict was "launch": prove it in the data dir
// (the repair swap script watches for exactly this) and forgive any repair
// attempts recorded against this version.
function noteHealthyLaunch(dataDir, version) {
  try {
    fs.mkdirSync(dataDir, { recursive: true });
    fs.writeFileSync(healthMarkerPath(dataDir),
      JSON.stringify({ version: String(version || "unknown"), at: new Date().toISOString() }));
  } catch (e) { /* a marker that cannot be written must not stop a healthy launch */ }
  clearRepairAttempts(dataDir, version);
}

function writeRepairReport(dataDir, verdict, attempts) {
  const lines = [
    "Xod self-repair is paused for this copy.",
    "",
    `The installed package failed its integrity check ${attempts} times and repairs`,
    "have not produced a copy that starts, so Xod has stopped trying on its own",
    "instead of showing the repair prompt on every launch.",
    "",
    `Package problems: ${(verdict.problems || []).join("; ") || "none recorded"}`,
    `Reasons: ${(verdict.reasons || []).join("; ") || "none recorded"}`,
    "",
    `Download the current version once from ${RELEASES_PAGE} and reinstall it;`,
    "your data (in this folder) is not touched by a reinstall.",
    "",
  ].join("\n");
  try {
    fs.mkdirSync(dataDir, { recursive: true });
    fs.writeFileSync(path.join(dataDir, "bootstrap-repair-report.txt"), lines);
  } catch (e) { /* the dialog still carries the message */ }
  return lines;
}

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
  } else {
    problems.push("package.json is missing from the archive");
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
      unreadable: true,
      version: null,
      problems: [`cannot read the package: ${e.message}`],
      reasons: ["the installed package cannot be read as an archive"],
    };
  }
  const reasons = [];
  if (result.problems.length) {
    reasons.push(`the package is incomplete: ${result.problems[0]}`);
  }
  const version = normalizeVersion(result.version);
  if (!version) {
    reasons.push("the version inside the package could not be read, so this copy cannot be cleared as healthy");
  } else if (isKnownBroken(version)) {
    reasons.push(`version ${version} is a known broken release`);
  }
  return {
    action: reasons.length ? "repair" : "launch",
    // The archive read and parsed, so whatever the walk found is a property
    // of the copy itself, not of the moment it was read.
    unreadable: false,
    version: result.version,
    problems: result.problems,
    reasons,
  };
}

// ---------------------------------------------------------------------------
// Repair. Every side effect goes through an io object so the whole pipeline
// is testable offline. The contract the pipeline keeps:
//   - nothing outside the work dir is written until a staged replacement has
//     passed the checksum check AND, when the staged artifact is an app
//     bundle, the same integrity walk as the launch gate (a staged installer
//     carries no archive to walk; the checksum is its verification);
//   - the staged bundle's walk waits for the staged copy to settle first
//     (see decideStaged): a read that fails while the copy is still landing
//     is retried, never reported as the copy failing its check;
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

// ---------------------------------------------------------------------------
// Staged-copy settle (2026-11-24). The field failure that day: a repair
// downloaded 1.1.22, the release checksum matched, staging copied the bundle
// out, and the integrity verdict read the staged app.asar before the copy
// had settled — ENOENT, "not found in .../xod-bootstrap-WGSg8M/Xod.app/
// Contents/Resources/app.asar" — so a good, checksum-verified download was
// reported as failing its integrity check and the install was stranded on
// the old version behind a "could not repair itself" dialog. The staged
// copy's bytes are already pinned by the release checksum before staging
// runs, so a read that fails at this point is a property of the moment,
// never of the copy; and a copy that is genuinely incomplete fails the walk
// identically on every read, so waiting and re-reading can never clear a
// real defect. The staged verdict therefore waits until the staged archive
// is present, readable through the same channel the verdict uses, and the
// same size on two consecutive polls, and a verdict that still cannot read
// the package is retaken a bounded number of times before the repair gives
// up. A copy that stays unreadable fails as a READ failure, in those words —
// never as an integrity failure it never got the chance to take.
// ---------------------------------------------------------------------------
const STAGED_SETTLE_ATTEMPTS = 40; // ~20s at the default poll
const STAGED_SETTLE_DELAY_MS = 500;
const STAGED_READ_RETRIES = 3;
const STAGED_READ_RETRY_DELAY_MS = 1000;

const defaultSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForStagedAsar(stagedApp, io = {}) {
  const sleep = io.sleep || defaultSleep;
  const settle = io.stagedSettle || {};
  const attempts = settle.attempts || STAGED_SETTLE_ATTEMPTS;
  const delayMs = settle.delayMs !== undefined ? settle.delayMs : STAGED_SETTLE_DELAY_MS;
  let lastSize = -1;
  for (let i = 0; i < attempts; i++) {
    let asarPath = null;
    try { asarPath = findAsar(stagedApp); } catch (e) { /* not there yet */ }
    if (asarPath) {
      try {
        const st = fs.statSync(asarPath);
        const fd = fs.openSync(asarPath, "r");
        try {
          const probe = Buffer.alloc(16);
          fs.readSync(fd, probe, 0, 16, 0);
        } finally { fs.closeSync(fd); }
        if (st.size > 0 && st.size === lastSize) return asarPath;
        lastSize = st.size;
      } catch (e) { lastSize = -1; }
    }
    if (i < attempts - 1) await sleep(delayMs);
  }
  // Out of patience: hand back whatever resolves now (or let findAsar throw
  // its honest "no app.asar found" error) and let the verdict retries in
  // decideStaged speak for the copy.
  return findAsar(stagedApp);
}

async function decideStaged(stagedApp, io = {}) {
  const sleep = io.sleep || defaultSleep;
  const retries = io.stagedReadRetries !== undefined ? io.stagedReadRetries : STAGED_READ_RETRIES;
  const retryDelayMs = io.stagedReadRetryDelayMs !== undefined
    ? io.stagedReadRetryDelayMs : STAGED_READ_RETRY_DELAY_MS;
  const asarPath = await waitForStagedAsar(stagedApp, io);
  let verdict = decide(asarPath);
  for (let attempt = 1; verdict.unreadable && attempt <= retries; attempt++) {
    if (io.report) io.report(`repairing Xod: the staged copy is not readable yet, checking again (${attempt}/${retries})`);
    await sleep(retryDelayMs);
    verdict = decide(asarPath);
  }
  return verdict;
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
  if (isKnownBroken(latest.version)) {
    return fail("fetch", `the latest release is ${latest.version}, which is itself a known broken release; there is nothing safe to repair to yet`);
  }
  // Version floor (AGE-72): repair targets releases/latest, and latest can
  // be OLDER than the installed bundle whenever a newer build shipped as a
  // prerelease (2026-11-14: a healthy 1.1.6 install repaired itself into
  // 1.1.4, the then-latest, and wedged). Never move an install backwards:
  // refuse here, in the same shape as the known-broken refusal above. The
  // floor binds only when the installed version actually reads.
  const installed = normalizeVersion(io.installedVersion
    ? await io.installedVersion(appPath)
    : installedVersionOf(appPath));
  if (installed && newer(installed, latest.version)) {
    return fail("fetch", `the latest release is ${latest.version}, which is older than the installed ${installed}; repairing would move this install backwards, so nothing was downloaded`);
  }
  let workDir;
  try {
    workDir = fs.mkdtempSync(path.join(io.workRoot(), "xod-bootstrap-"));
    io.report(`repairing Xod: downloading ${latest.artifactName} (${latest.version})`);
    const artifactPath = path.join(workDir, latest.artifactName);
    await io.download(latest.artifactUrl, artifactPath);
    const sumsText = await io.fetchText(latest.sumsUrl);
    const line = sumsText.split("\n").find((l) => {
      const parts = l.trim().split(/\s+/);
      return parts.length >= 2 && parts[parts.length - 1].replace(/^\*/, "") === latest.artifactName;
    });
    const want = line ? line.trim().split(/\s+/)[0].toLowerCase() : null;
    const got = await sha256File(artifactPath);
    if (!want || want !== got) throw new Error("the download's checksum does not match the release");
    const stagedApp = await io.stageApp(artifactPath, workDir);
    if (io.stagedKind === "installer") {
      // The staged artifact is the platform installer itself. There is no
      // app.asar inside it to walk; the checksum that just matched
      // SHA256SUMS.txt is its verification, the same evidence the updater
      // acts on, and it also pins the version: bytes that hash to the
      // release's sum are the release's bytes. The version check below
      // belongs to app bundles, whose package can be read directly.
      io.report(`repairing Xod: the installer matches the release checksum`);
    } else {
      const verdict = await decideStaged(stagedApp, io);
      if (verdict.unreadable) {
        throw new Error(`the downloaded copy could not be read after waiting for it to settle: ${verdict.problems[0] || verdict.reasons[0]}`);
      }
      if (verdict.action !== "launch") {
        throw new Error(`the downloaded copy does not pass the integrity check: ${verdict.problems[0] || verdict.reasons[0]}`);
      }
      if (verdict.version !== latest.version) {
        throw new Error(`the downloaded copy reports version ${verdict.version}, not ${latest.version}`);
      }
    }
    // Non-interleave (AGE-72): the guardian may be mid-verdict on this
    // install right now. Swapping under its battery invites its rollback
    // to restore over this repair, so defer with a clear line instead;
    // the staged download is scratch and the next launch retries.
    const batteryActive = io.guardianBatteryActive
      ? io.guardianBatteryActive()
      : guardianBatteryActive();
    if (batteryActive) {
      return fail("repair", "the update guardian is in the middle of its post-update health check on this install; the repair swap is deferred until that verdict lands, and nothing was replaced");
    }
    io.report(`repairing Xod: the new copy checks out, swapping it in`);
    await io.swapAndRelaunch({ stagedApp, targetApp: appPath, workDir, version: latest.version });
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
        res.on("error", reject);
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
// bundle into place, opens it, and then VERIFIES the swap the only way that
// counts: the new copy must prove a healthy launch by writing a fresh
// bootstrap-health.json marker naming its version (noteHealthyLaunch runs
// on every healthy packaged launch) within the wait window. Only then is
// the previous bundle deleted. If the marker never lands — the new copy
// cannot start, the exact stranding this bootstrap exists to prevent — the
// previous bundle is restored and relaunched, and the log says so. Before
// this verification existed, the script deleted the backup the moment an
// app.asar file existed in place, so a swapped-in copy that could not
// actually start left nothing to roll back to.
// Returns immediately; the caller quits so the script can proceed.
function buildSwapScriptMac({ stagedApp, targetApp, workDir, pid, version, dataDir }) {
  const q = (s) => `'${String(s).replace(/'/g, "'\\''")}'`;
  const marker = healthMarkerPath(dataDir);
  const startedAt = Math.floor(Date.now() / 1000);
  return [
    "#!/bin/sh",
    `LOG=${q(path.join(workDir, "swap.log"))}`,
    `while kill -0 ${pid} 2>/dev/null; do sleep 0.5; done`,
    `BACKUP=${q(targetApp + ".bootstrap-backup")}`,
    `MARKER=${q(marker)}`,
    `WANT=${q(String(version || ""))}`,
    `rm -rf "$BACKUP"`,
    `if mv ${q(targetApp)} "$BACKUP" && mv ${q(stagedApp)} ${q(targetApp)}; then`,
    `  if [ -f ${q(path.join(targetApp, "Contents", "Resources", "app.asar"))} ]; then`,
    `    /usr/bin/open ${q(targetApp)} >> "$LOG" 2>&1`,
    "    ok=0",
    "    for i in $(seq 1 240); do",
    `      if [ -f "$MARKER" ]; then`,
    `        mver=$(sed -n 's/.*"version"[^"]*"\\([^"]*\\)".*/\\1/p' "$MARKER" | head -1)`,
    `        mtime=$(stat -f %m "$MARKER" 2>/dev/null || echo 0)`,
    `        if [ "$mver" = "$WANT" ] && [ "$mtime" -ge ${startedAt - 5} ]; then ok=1; break; fi`,
    "      fi",
    "      sleep 0.5",
    "    done",
    "    if [ $ok = 1 ]; then",
    `      rm -rf "$BACKUP"`,
    `      echo "swap verified: the new copy proved a healthy launch" >> "$LOG"`,
    "      exit 0",
    "    fi",
    `    echo "the new copy did not prove a healthy launch within 120s; restoring the previous install" >> "$LOG"`,
    "  fi",
    "fi",
    `rm -rf ${q(targetApp)}`,
    `mv "$BACKUP" ${q(targetApp)}`,
    `/usr/bin/open ${q(targetApp)} >> "$LOG" 2>&1`,
    `echo "swap failed verification, previous install restored and relaunched" >> "$LOG"`,
    "exit 1",
    "",
  ].join("\n");
}

async function swapAndRelaunchMac({ stagedApp, targetApp, workDir, version }, pid) {
  const script = path.join(workDir, "swap.sh");
  fs.writeFileSync(script, buildSwapScriptMac({
    stagedApp, targetApp, workDir, pid,
    version: version || null,
    dataDir: xodDataDir(),
  }), { mode: 0o755 });
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
    io.stagedKind = "app"; // repair() walks the staged bundle's app.asar
    io.swapAndRelaunch = (args) => swapAndRelaunchMac(args, process.pid);
    io.keepWork = () => true; // the swap script and its log live in the work dir
  } else if (platform === "win32") {
    io.stageApp = stageAppWin;
    io.stagedKind = "installer"; // repair() verifies the staged installer by checksum
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
  const dataDir = xodDataDir();
  if (verdict.action === "launch") {
    // Prove the healthy launch in the data dir: a repair swap script may
    // be waiting for exactly this marker before it deletes the previous
    // bundle, and a healthy launch forgives this version's repair count.
    noteHealthyLaunch(dataDir, verdict.version);
    loadMain();
    return;
  }
  // Broken package: main.js would die at its first missing module, and the
  // raw Electron error box is what a stranded user sees today. Show the
  // repair instead, in plain words, and never load the broken entry.
  app.whenReady().then(async () => {
    const bundle = path.resolve(path.dirname(app.getPath("exe")), "..", "..");
    // Loop guard: repairs against this same broken copy are counted in
    // the data dir, which no swap touches. After MAX_REPAIR_ATTEMPTS the
    // bootstrap stops offering repairs — the 2026-11-22 loop showed this
    // same dialog on every launch with no memory of having tried — and
    // leaves one plain report instead.
    const versionKey = verdict.version || "unknown";
    if (repairAttempts(dataDir, versionKey) >= MAX_REPAIR_ATTEMPTS) {
      const report = writeRepairReport(dataDir, verdict, repairAttempts(dataDir, versionKey));
      dialog.showMessageBoxSync({
        type: "error",
        message: "Xod repairs are paused",
        detail: report,
        buttons: ["Quit"],
      });
      app.quit();
      return;
    }
    recordRepairAttempt(dataDir, versionKey);
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
    decideStaged,
    waitForStagedAsar,
    findAsar,
    repair,
    sha256File,
    makePlatformIo,
    newer,
    normalizeVersion,
    isKnownBroken,
    guardianBatteryActive,
    guardianStateDir,
    installedVersionOf,
    MAX_REPAIR_ATTEMPTS,
    xodDataDir,
    healthMarkerPath,
    repairStatePath,
    repairAttempts,
    recordRepairAttempt,
    clearRepairAttempts,
    noteHealthyLaunch,
    writeRepairReport,
    buildSwapScriptMac,
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
