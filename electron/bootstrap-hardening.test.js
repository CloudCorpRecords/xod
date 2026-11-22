// The bootstrap's loop hardening (2026-11-22), with no Electron and no
// network:  node --test electron/bootstrap-hardening.test.js
//
// The incident these pin: a healthy install was rolled back to a build
// whose bootstrap cannot read its own archive, so every launch offered
// a repair that could never succeed — the same dialog forever, with no
// memory of having tried, and a repair swap that deleted the previous
// bundle the moment any app.asar existed, leaving nothing to fall back
// to when the new copy could not start. Against the pre-hardening
// bootstrap these tests fail: the counter, the health marker and the
// verified swap script do not exist there.
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const B = require("./bootstrap");

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "xod-bootstrap-hardening-"));
}

test("repair attempts are counted per version and stop at the cap", () => {
  const dir = tmpDir();
  assert.equal(B.repairAttempts(dir, "1.1.4"), 0);
  assert.equal(B.recordRepairAttempt(dir, "1.1.4"), 1);
  assert.equal(B.recordRepairAttempt(dir, "1.1.4"), 2);
  assert.equal(B.recordRepairAttempt(dir, "1.1.4"), 3);
  assert.equal(B.repairAttempts(dir, "1.1.4"), B.MAX_REPAIR_ATTEMPTS);
  // a different broken version starts from zero
  assert.equal(B.repairAttempts(dir, "1.1.5"), 0);
});

test("a healthy launch writes the marker and forgives the repair count", () => {
  const dir = tmpDir();
  B.recordRepairAttempt(dir, "1.1.14");
  B.recordRepairAttempt(dir, "1.1.14");
  B.noteHealthyLaunch(dir, "1.1.14");
  const marker = JSON.parse(fs.readFileSync(B.healthMarkerPath(dir), "utf8"));
  assert.equal(marker.version, "1.1.14");
  assert.ok(marker.at, "marker carries a timestamp");
  assert.equal(B.repairAttempts(dir, "1.1.14"), 0);
});

test("the paused-repair report lands in the data dir and says what happened", () => {
  const dir = tmpDir();
  const text = B.writeRepairReport(dir,
    { problems: ["cannot read the package: asar header is truncated"],
      reasons: ["the installed package cannot be read as an archive"] }, 3);
  const onDisk = fs.readFileSync(path.join(dir, "bootstrap-repair-report.txt"), "utf8");
  assert.equal(onDisk, text);
  assert.match(onDisk, /stopped trying on its own/);
  assert.match(onDisk, /cannot be read as an archive/);
});

test("the mac swap script verifies a healthy launch before deleting the backup", () => {
  const script = B.buildSwapScriptMac({
    stagedApp: "/tmp/stage/Xod.app",
    targetApp: "/Applications/Xod.app",
    workDir: "/tmp/work",
    pid: 4242,
    version: "1.1.14",
    dataDir: "/Users/test/Library/Application Support/Xod",
  });
  // it waits for the process, swaps, opens the new copy
  assert.match(script, /while kill -0 4242/);
  assert.match(script, /bootstrap-health\.json/);
  // the marker wait comes after the open, and the backup made by THIS
  // swap is never deleted between the relaunch and the verification
  // verdict (an earlier `rm -rf "$BACKUP"` clears a stale backup from a
  // previous run, before this swap makes its own)
  const openAt = script.indexOf("/usr/bin/open");
  const waitAt = script.indexOf("did not prove a healthy launch");
  const verdictAt = script.indexOf("if [ $ok = 1 ]");
  assert.ok(openAt > 0 && waitAt > openAt, "verification wait follows the relaunch");
  assert.ok(verdictAt > openAt, "a verification verdict gates what happens to the backup");
  const between = script.slice(openAt, verdictAt);
  assert.ok(!between.includes('rm -rf "$BACKUP"'),
    "the backup survives until the new copy proves a healthy launch");
  assert.ok(script.slice(verdictAt).includes('rm -rf "$BACKUP"'),
    "the backup is deleted once verification passes");
  // and the failure path restores and relaunches the previous install
  assert.match(script, /restoring the previous install/);
  assert.match(script, /previous install restored and relaunched/);
});

test("a truncated archive is the repair case, in the dialog's own words", () => {
  // Build the smallest healthy asar the writer in bootstrap.test.js
  // would produce, then truncate it the way a partial copy leaves it.
  const dir = tmpDir();
  const good = path.join(dir, "app.asar");
  const files = {
    "package.json": Buffer.from(JSON.stringify({ name: "xod-desktop", version: "1.1.14", main: "main.js" })),
    "main.js": Buffer.from("module.exports = {};\n"),
  };
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
    tree.files[rel] = { size: content.length, offset: String(offsets[rel]) };
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
  fs.writeFileSync(good, Buffer.concat([size, header, ...chunks]));
  assert.equal(B.decide(good).action, "launch");
  const bytes = fs.readFileSync(good);
  fs.writeFileSync(good, bytes.subarray(0, 10)); // a copy that died mid-write
  const verdict = B.decide(good);
  assert.equal(verdict.action, "repair");
  assert.match(verdict.reasons[0], /cannot be read as an archive/);
});
