const assert = require("node:assert/strict");
const createRefreshGate = require("../../app/static/js/refresh-gate.js");
const tick = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  let active = 0, peak = 0, calls = 0, releases = [], rendered = [];
  const refresh = createRefreshGate(async current => {
    const n = ++calls;
    peak = Math.max(peak, ++active);
    await new Promise(resolve => releases.push(resolve));
    if (current()) rendered.push(n);
    active--;
  });
  const done = refresh();
  refresh(false); refresh(false); refresh(); // action invalidates old response
  releases.shift()(); await tick();
  assert.equal(calls, 2);
  assert.deepEqual(rendered, []);
  refresh(false); // timer must not invalidate a slow but valid reply
  releases.shift()(); await tick();
  assert.deepEqual(rendered, [2]);
  releases.shift()(); await done;
  assert.equal(peak, 1);
  assert.deepEqual(rendered, [2, 3]);
  let errors = 0;
  const failing = createRefreshGate(async () => { throw Error("offline"); }, () => errors++);
  await failing(); await failing();
  assert.equal(errors, 2); // no permanent latch after network failure
  console.log("PASS: single-flight, coalescing, stale-response rejection, slow responses and error recovery");
})().catch(e => { console.error(e); process.exitCode = 1; });
