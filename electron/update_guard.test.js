// AGE-72: the guardian battery marker that the Electron swap paths defer
// to, and the updater swap's deferral itself. The Partner guardian
// publishes battery-in-progress.json in its state dir for exactly the span
// of a post-swap health verdict; while a fresh marker exists, neither the
// bootstrap repair swap nor the updater swap may land, because two swaps
// interleaving is how an install ends up mixed (bundle at one version,
// backend at another).
//   node --test electron/update_guard.test.js
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const Module = require("module");

const B = require("./bootstrap");

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "xod-update-guard-test-"));
}

function writeMarker(dir, atIso) {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "battery-in-progress.json"),
    JSON.stringify({ for_version: "1.1.5", at: atIso, pid: 4242 }));
}

test("guardianBatteryActive: no marker, no deferral", () => {
  const dir = tmpdir();
  assert.equal(B.guardianBatteryActive(dir), false);
});

test("guardianBatteryActive: a fresh marker defers, a stale or broken one does not", () => {
  const dir = tmpdir();
  const now = Date.now();
  writeMarker(dir, new Date(now - 60 * 1000).toISOString());
  assert.equal(B.guardianBatteryActive(dir, now), true, "a one-minute-old verdict is in progress");
  writeMarker(dir, new Date(now - 25 * 60 * 1000).toISOString());
  assert.equal(B.guardianBatteryActive(dir, now), false, "a marker older than the battery ceiling is abandoned");
  fs.writeFileSync(path.join(dir, "battery-in-progress.json"), "not json");
  assert.equal(B.guardianBatteryActive(dir, now), false, "an unreadable marker never blocks a swap forever");
});

test("guardianStateDir names the Partner state dir under the home dir", () => {
  const dir = B.guardianStateDir(path.join(path.sep, "home", "someone"));
  assert.ok(dir.endsWith(path.join("Library", "Application Support", "XodPartner")), dir);
});

// The updater half, loaded behind an electron stand-in, with HOME pointed
// at a scratch dir so its work dir and the guardian state dir both land in
// the scratch tree.
function loadUpdater(home) {
  process.env.HOME = home;
  const fakeElectron = {
    app: {
      isPackaged: true,
      getVersion: () => "1.1.4",
      getPath: (name) => (name === "exe"
        ? path.join(home, "installed", "Xod.app", "Contents", "MacOS", "Xod")
        : home),
      on: () => {},
    },
    dialog: { showMessageBox: async () => ({ response: 1 }) },
    shell: { openExternal: () => {} },
  };
  const load = Module._load;
  Module._load = function (req, ...rest) {
    return req === "electron" ? fakeElectron : load.call(this, req, ...rest);
  };
  try {
    delete require.cache[require.resolve("./updater")];
    return require("./updater");
  } finally {
    Module._load = load;
  }
}

test("the updater reads the same guardian marker", () => {
  const home = tmpdir();
  const updater = loadUpdater(home);
  const dir = path.join(home, "Library", "Application Support", "XodPartner");
  assert.equal(updater._test.guardianBatteryActive(dir), false);
  writeMarker(dir, new Date().toISOString());
  assert.equal(updater._test.guardianBatteryActive(dir), true);
});

test("the updater swap defers while the guardian battery is mid-verdict", () => {
  const home = tmpdir();
  const updater = loadUpdater(home);
  const stateDir = path.join(home, "Library", "Application Support", "XodPartner");
  const staged = path.join(home, "staged", "Xod.app");
  fs.mkdirSync(staged, { recursive: true });
  // stage() creates the work dir before a payload can be ready; the test
  // stands in for that step.
  fs.mkdirSync(path.join(home, "Library", "Caches", "Xod", "updates"), { recursive: true });
  updater._test.setReady({ version: "1.1.5", appPath: staged });
  writeMarker(stateDir, new Date().toISOString());
  assert.equal(updater._test.swap(false), false, "the swap must not start during a verdict");
  const work = path.join(home, "Library", "Caches", "Xod", "updates");
  assert.ok(!fs.existsSync(path.join(work, "install.sh")), "no swap script was written during the verdict");
  fs.rmSync(path.join(stateDir, "battery-in-progress.json"));
  assert.equal(updater._test.swap(false), true, "once the verdict lands, the same swap proceeds");
  assert.ok(fs.existsSync(path.join(work, "install.sh")), "the swap script is written after the verdict");
});
