// The liveness review's decisions (electron/liveness.js), with no
// Electron and no real backend running:
//   node --test electron/liveness.test.js
const { test } = require("node:test");
const assert = require("node:assert");
const { LivenessReview } = require("./liveness");

function harness(overrides = {}) {
  const h = {
    now: 10_000,
    ledger: null,
    alive: true,
    hello: true,
    expectedPid: 4242,
    calls: [],          // ordered record of side effects: "evidence", "restart", "hello"
    restarts: 0,
    state: {},
    logs: [],
    incidents: [],
    restartOk: true,
    evidenceOk: true,
    ...overrides,
  };
  const io = {
    now: () => h.now,
    readLedger: () => h.ledger,
    expectedPid: () => h.expectedPid,
    processAlive: () => h.alive,
    helloAnswers: async () => { h.calls.push("hello"); return h.hello; },
    captureEvidence: async () => {
      h.calls.push("evidence");
      if (!h.evidenceOk) throw new Error("sample failed");
      return "/incidents/one";
    },
    recordIncident: (dir, rec) => { h.incidents.push({ dir, ...rec }); },
    restart: async () => { h.calls.push("restart"); h.restarts += 1; return h.restartOk; },
    log: (m) => h.logs.push(m),
    loadState: () => h.state,
    saveState: (s) => { h.state = JSON.parse(JSON.stringify(s)); },
  };
  return { h, io };
}

function wedgedLedger(h, agoSec = 60) {
  return {
    pid: 4242, boot_at: h.now - 1000,
    last_activity_at: h.now - agoSec, last_activity_kind: "work_progress",
    in_flight: { "task-1": { kind: "task", since: h.now - 300, last_progress_at: h.now - agoSec } },
  };
}

test("fresh activity with work in flight is watching, never a review", async () => {
  const { h, io } = harness();
  h.ledger = wedgedLedger(h, 5);
  const r = new LivenessReview(io);
  assert.equal(await r.tick(), "watching");
  assert.deepEqual(h.calls, []);
});

test("activity resetting the timer keeps a long job safe", async () => {
  const { h, io } = harness();
  const r = new LivenessReview(io);
  for (let i = 0; i < 30; i++) {
    h.ledger = wedgedLedger(h, 10);        // progress keeps landing every tick
    assert.equal(await r.tick(), "watching");
    h.now += 5;
  }
  assert.equal(h.restarts, 0);
});

test("expiry triggers a review; a review that finds hello answering stands down", async () => {
  const { h, io } = harness();
  h.ledger = wedgedLedger(h, 60);          // quiet step, but the server is fine
  h.hello = true;
  const r = new LivenessReview(io);
  assert.equal(await r.tick(), "review_healthy");
  assert.equal(h.restarts, 0);
  assert.equal(await r.tick(), "standing_down");   // same episode, inside the grace window
  assert.equal(h.calls.filter((c) => c === "hello").length, 1);
});

test("a wedged backend (alive, hello silent) is restarted, evidence first", async () => {
  const { h, io } = harness();
  h.ledger = wedgedLedger(h, 60);
  h.hello = false;
  const r = new LivenessReview(io);
  assert.equal(await r.tick(), "restarted");
  assert.deepEqual(h.calls, ["hello", "evidence", "restart"]);
  h.hello = true;                          // the restarted backend answers
  h.now += 20;
  assert.equal(await r.tick(), "recovered");
  assert.equal(r.state.lastRecoverySec, 20);
});

test("a dead process is restarted without a hello check", async () => {
  const { h, io } = harness();
  h.ledger = wedgedLedger(h, 60);
  h.alive = false; h.hello = false;
  const r = new LivenessReview(io);
  assert.equal(await r.tick(), "restarted");
  assert.deepEqual(h.calls, ["evidence", "restart"]);
});

test("evidence capture failing never blocks the restart", async () => {
  const { h, io } = harness();
  h.ledger = wedgedLedger(h, 60);
  h.hello = false; h.evidenceOk = false;
  const r = new LivenessReview(io);
  assert.equal(await r.tick(), "restarted");
  assert.equal(h.restarts, 1);
});

test("a restart that never answers is recorded as a failed recovery", async () => {
  const { h, io } = harness();
  h.ledger = wedgedLedger(h, 60);
  h.hello = false;
  const r = new LivenessReview(io, { recoveryTimeoutSec: 100 });
  assert.equal(await r.tick(), "restarted");
  h.now += 50;
  assert.equal(await r.tick(), "recovering");
  h.now += 60;
  assert.equal(await r.tick(), "recovery_failed");
});

test("backoff spaces restarts out inside the window", async () => {
  const { h, io } = harness();
  h.hello = false;
  const r = new LivenessReview(io, { recoveryTimeoutSec: 10 });
  h.ledger = wedgedLedger(h, 60);
  assert.equal(await r.tick(), "restarted");       // restart 1 at t
  h.now += 20;                                     // recovery never comes
  assert.equal(await r.tick(), "recovery_failed");
  h.ledger = wedgedLedger(h, 60);
  assert.equal(await r.tick(), "backoff");         // 20s < 60s gap before restart 2
  assert.equal(h.restarts, 1);
  h.now += 50;                                     // 70s since restart 1
  h.ledger = wedgedLedger(h, 60);
  assert.equal(await r.tick(), "restarted");
  assert.equal(h.restarts, 2);
});

test("the budget holds: after 3 restarts in the window there are no more", async () => {
  const { h, io } = harness();
  h.hello = false;
  const r = new LivenessReview(io, { recoveryTimeoutSec: 10, backoffSec: [0, 0, 0] });
  for (let i = 0; i < 3; i++) {
    h.ledger = wedgedLedger(h, 60);
    assert.equal(await r.tick(), "restarted", `restart ${i + 1}`);
    h.now += 20;
    assert.equal(await r.tick(), "recovery_failed");
  }
  h.ledger = wedgedLedger(h, 60);
  assert.equal(await r.tick(), "held");
  assert.equal(h.restarts, 3);
  h.now += 30;
  h.ledger = wedgedLedger(h, 60);
  assert.equal(await r.tick(), "held");            // still holding, no respin
  assert.equal(h.restarts, 3);
  h.hello = true;                                  // it came back on its own
  assert.equal(await r.tick(), "recovered");
  assert.equal(r.state.held, false);
});

test("an idle machine stays quiet: no review, no hello checks, no restarts", async () => {
  const { h, io } = harness();
  h.ledger = { pid: 4242, boot_at: h.now - 5000, last_activity_at: h.now - 4000,
               last_activity_kind: "request", in_flight: {} };
  h.hello = false;                                 // even a dead hello is not our business while idle
  const r = new LivenessReview(io);
  for (let i = 0; i < 5; i++) {
    assert.equal(await r.tick(), "quiet");
    h.now += 60;
  }
  assert.deepEqual(h.calls, []);
  assert.equal(h.restarts, 0);
});

test("recent activity with nothing in flight still earns a review", async () => {
  const { h, io } = harness();
  h.ledger = { pid: 4242, boot_at: h.now - 1000, last_activity_at: h.now - 60,
               last_activity_kind: "request", in_flight: {} };
  h.hello = false;
  const r = new LivenessReview(io);
  assert.equal(await r.tick(), "restarted");       // a chat turn wedged mid-request
});

test("a ledger from another backend instance is not ours to restart", async () => {
  const { h, io } = harness();
  h.ledger = wedgedLedger(h, 60);
  h.ledger.pid = 9999;                             // some other instance's timer
  h.hello = false;
  const r = new LivenessReview(io);
  assert.equal(await r.tick(), "quiet");
  assert.equal(h.restarts, 0);
});

test("no ledger at all is quiet (boot, or a backend older than the timer)", async () => {
  const { h, io } = harness();
  const r = new LivenessReview(io);
  assert.equal(await r.tick(), "quiet");
});
