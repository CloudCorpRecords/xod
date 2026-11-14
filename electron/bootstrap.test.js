// The bootstrap's decisions, with no Electron, no network, and no real app:
//   node --test electron/bootstrap.test.js
//
// The fixtures replicate the failure that stranded every v1.1.3 user: an
// app.asar carrying exactly the 8 files the shipped archive carried, with the
// entry point requiring a module at line 8 that the archive does not hold.
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");
const B = require("./bootstrap");

// --- a minimal asar writer, the same framing scripts/check_asar.py parses ---
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

function fixture113() {
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

function fixtureHealthy(version) {
  const files = fixture113();
  files["package.json"] = Buffer.from(JSON.stringify({ name: "xod-desktop", version, main: "bootstrap.js" }));
  files["bootstrap.js"] = Buffer.from('const m = require("./main.js");\nmodule.exports = m;\n');
  files["liveness.js"] = Buffer.from("class LivenessReview {}\nmodule.exports = { LivenessReview };\n");
  return files;
}

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "xod-bootstrap-test-"));
}

function makeApp(root, asarFiles) {
  const app = path.join(root, "Xod.app");
  const res = path.join(app, "Contents", "Resources");
  fs.mkdirSync(res, { recursive: true });
  writeAsar(path.join(res, "app.asar"), asarFiles);
  return app;
}

function treeManifest(root) {
  const out = [];
  const walk = (dir) => {
    for (const name of fs.readdirSync(dir).sort()) {
      const p = path.join(dir, name);
      if (fs.statSync(p).isDirectory()) walk(p);
      else out.push(`${path.relative(root, p)}:${crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex")}`);
    }
  };
  walk(root);
  return out.join("\n");
}

test("the exact 1.1.3 defect is detected and routed to repair", () => {
  const root = tmpdir();
  const asar = path.join(root, "app.asar");
  writeAsar(asar, fixture113());
  const check = B.checkAsar(asar);
  assert.equal(check.version, "1.1.3");
  assert.ok(check.problems.some((p) => p.startsWith("main.js:8") && p.includes('"./liveness"')),
    `expected the line 8 liveness miss, got: ${check.problems.join(" | ")}`);
  const verdict = B.decide(asar);
  assert.equal(verdict.action, "repair");
  assert.ok(verdict.reasons.some((r) => r.includes("incomplete")), "integrity reason present");
  assert.ok(verdict.reasons.some((r) => r.includes("known broken release")), "version reason present");
});

test("a healthy package is a launch, and the verdict never needs the network", () => {
  const root = tmpdir();
  const asar = path.join(root, "app.asar");
  writeAsar(asar, fixtureHealthy("1.1.4"));
  const check = B.checkAsar(asar);
  assert.deepEqual(check.problems, []);
  assert.ok(check.visited.includes("bootstrap.js") && check.visited.includes("main.js") && check.visited.includes("liveness.js"),
    "the walk from the bootstrap entry reaches the whole graph");
  const verdict = B.decide(asar);
  assert.equal(verdict.action, "launch");
  assert.equal(verdict.version, "1.1.4");
});

test("a known broken version routes to repair even when its graph is complete", () => {
  const root = tmpdir();
  const asar = path.join(root, "app.asar");
  writeAsar(asar, fixtureHealthy("1.1.3"));
  const verdict = B.decide(asar);
  assert.equal(verdict.action, "repair");
  assert.deepEqual(verdict.problems, []);
  assert.ok(verdict.reasons.some((r) => r.includes("1.1.3")));
});

test("an unreadable package routes to repair instead of crashing", () => {
  const root = tmpdir();
  const asar = path.join(root, "app.asar");
  fs.writeFileSync(asar, "this is not an archive");
  const verdict = B.decide(asar);
  assert.equal(verdict.action, "repair");
  assert.ok(verdict.problems[0].includes("cannot read the package"));
});

function repairIo(overrides = {}) {
  const calls = [];
  const sumsFor = (bytes) => `${crypto.createHash("sha256").update(bytes).digest("hex")}  Xod-1.1.4-arm64.dmg\n`;
  const io = {
    workRoot: () => tmpdir(),
    report: (m) => calls.push(`report:${m}`),
    fetchLatest: async () => {
      calls.push("fetchLatest");
      return {
        version: "1.1.4",
        artifactName: "Xod-1.1.4-arm64.dmg",
        artifactUrl: "https://example.invalid/Xod-1.1.4-arm64.dmg",
        sumsUrl: "https://example.invalid/SHA256SUMS.txt",
      };
    },
    download: async (url, dest) => { calls.push("download"); fs.writeFileSync(dest, Buffer.from("DMGBYTES")); },
    fetchText: async () => sumsFor(Buffer.from("DMGBYTES")),
    stageApp: async () => { throw new Error("stageApp must be overridden in this test"); },
    swapAndRelaunch: async () => { calls.push("swap"); },
    ...overrides,
  };
  return { io, calls };
}

test("a failed download leaves the install byte-identical and reports honestly", async () => {
  const root = tmpdir();
  const app = makeApp(root, fixture113());
  const before = treeManifest(root);
  const { io } = repairIo({ download: async () => { throw new Error("network is down"); } });
  const result = await B.repair(app, io);
  assert.equal(result.ok, false);
  assert.ok(result.error.includes("network is down"));
  assert.equal(treeManifest(root), before, "the install was not touched");
});

test("a checksum mismatch stops before anything is staged or swapped", async () => {
  const root = tmpdir();
  const app = makeApp(root, fixture113());
  const before = treeManifest(root);
  const { io, calls } = repairIo({ fetchText: async () => "0".repeat(64) + "  Xod-1.1.4-arm64.dmg\n" });
  const result = await B.repair(app, io);
  assert.equal(result.ok, false);
  assert.ok(result.error.includes("checksum"));
  assert.ok(!calls.includes("swap"), "no swap was attempted");
  assert.equal(treeManifest(root), before);
});

test("a staged copy that fails the integrity walk is never swapped in", async () => {
  const root = tmpdir();
  const app = makeApp(root, fixture113());
  const staged = makeApp(path.join(root, "staged-root"), fixture113()); // still broken
  const before = treeManifest(path.join(root, "Xod.app"));
  const { io, calls } = repairIo({ stageApp: async () => staged });
  const result = await B.repair(app, io);
  assert.equal(result.ok, false);
  assert.ok(result.error.includes("integrity check"));
  assert.ok(!calls.includes("swap"));
  assert.equal(treeManifest(app), before);
});

test("a checked, complete replacement is swapped in and relaunched once", async () => {
  const root = tmpdir();
  const app = makeApp(root, fixture113());
  const staged = makeApp(path.join(root, "staged-root"), fixtureHealthy("1.1.4"));
  let swaps = 0;
  const { io } = repairIo({
    stageApp: async () => staged,
    swapAndRelaunch: async ({ stagedApp, targetApp }) => {
      swaps++;
      const backup = targetApp + ".test-backup";
      fs.renameSync(targetApp, backup);
      fs.renameSync(stagedApp, targetApp);
      fs.rmSync(backup, { recursive: true, force: true });
    },
  });
  const result = await B.repair(app, io);
  assert.equal(result.ok, true);
  assert.equal(result.version, "1.1.4");
  assert.equal(swaps, 1);
  const verdict = B.decide(B.findAsar(app));
  assert.equal(verdict.action, "launch");
  assert.equal(verdict.version, "1.1.4");
});

test("the latest release being broken itself stops the repair at the fetch", async () => {
  const root = tmpdir();
  const app = makeApp(root, fixture113());
  const { io } = repairIo({
    fetchLatest: async () => ({ version: "1.1.3", artifactName: "x", artifactUrl: "u", sumsUrl: "s" }),
  });
  const result = await B.repair(app, io);
  assert.equal(result.ok, false);
  assert.equal(result.stage, "fetch");
});

test("windows staging hands the installer the artifact itself, never a bundle swap", async () => {
  const io = B.makePlatformIo("win32", "x64");
  assert.equal(await io.stageApp("C:\\tmp\\Xod-1.1.4-x64-setup.exe", "C:\\tmp\\work"), "C:\\tmp\\Xod-1.1.4-x64-setup.exe");
  assert.equal(io.keepWork(), true, "the installer runs from the work dir after exit, so it is kept");
});

test("the bootstrap source loads nothing relative before the verdict", () => {
  const src = fs.readFileSync(path.join(__dirname, "bootstrap.js"), "utf8");
  const specs = [...src.matchAll(/require\(\s*['"]([^'"]+)['"]\s*\)/g)]
    .map((m) => m[1])
    .filter((s) => s.startsWith("./") || s.startsWith("../"));
  assert.deepEqual(specs, ["./main.js"],
    "the only relative import in the bootstrap is the app entry it hands over to, after the check");
});

test("findAsar resolves an app bundle and version compare behaves", () => {
  const root = tmpdir();
  const app = makeApp(root, fixtureHealthy("1.1.4"));
  assert.equal(B.findAsar(app), path.join(app, "Contents", "Resources", "app.asar"));
  assert.equal(B.newer("1.1.4", "1.1.3"), true);
  assert.equal(B.newer("1.1.3", "1.1.3"), false);
});
