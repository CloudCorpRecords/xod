// The lightweight modes' decisions (electron/mode.js), with no Electron running:
//   node --test electron/mode.test.js
const { test } = require("node:test");
const assert = require("node:assert");
const { resolveMode, shouldShedOnClose } = require("./mode");

test("default launch is the full app", () => {
  assert.equal(resolveMode([], {}).headless, false);
  assert.equal(resolveMode(["/path/to/Xod"], {}).headless, false);
  assert.equal(resolveMode(null, null).headless, false);
});

test("--headless flag launches headless", () => {
  assert.equal(resolveMode(["Xod", "--headless"], {}).headless, true);
});

test("XOD_HEADLESS env launches headless, spelled the usual ways", () => {
  for (const v of ["1", "true", "TRUE", " yes ", "on"]) {
    assert.equal(resolveMode([], { XOD_HEADLESS: v }).headless, true, `XOD_HEADLESS=${v}`);
  }
  for (const v of ["", "0", "false", "no", "off", "banana"]) {
    assert.equal(resolveMode([], { XOD_HEADLESS: v }).headless, false, `XOD_HEADLESS=${v}`);
  }
});

test("the window sheds only when background running AND the lightweight choice are both on", () => {
  assert.equal(shouldShedOnClose({ background: true, lightBackground: true }), true);
  assert.equal(shouldShedOnClose({ background: true, lightBackground: false }), false);
  assert.equal(shouldShedOnClose({ background: false, lightBackground: true }), false);
  assert.equal(shouldShedOnClose({ background: true }), false);          // older desktop.json: no key yet
  assert.equal(shouldShedOnClose({}), false);
  assert.equal(shouldShedOnClose(null), false);
  assert.equal(shouldShedOnClose(undefined), false);
});

test("the saved switch decides, and a launch flag or env var overrides it for one launch", () => {
  const { resolveMode } = require("./mode");
  assert.equal(resolveMode([], {}, { headless: true }).headless, true);
  assert.equal(resolveMode([], {}, { headless: false }).headless, false);
  assert.equal(resolveMode(["--window"], {}, { headless: true }).headless, false);
  assert.equal(resolveMode([], { XOD_HEADLESS: "0" }, { headless: true }).headless, false);
  assert.equal(resolveMode(["--headless"], {}, { headless: false }).headless, true);
  assert.equal(resolveMode([], {}, undefined).headless, false);
});
