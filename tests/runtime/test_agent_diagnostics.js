// DOM-contract checks, not a browser journey or live inference.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const sheet = { innerHTML: "", parentElement: { classList: { add() {} } } };
const body = { innerHTML: "" };
const calls = [];
const brain = { provider: "openai", model: "synthetic", where: "online", auto: true };
let response = {
  settings: { enabled: true, same_model_workers: false },
  tiers: { small: brain, standard: brain, large: brain }, brains: [brain], agents: []
};
const context = vm.createContext({
  window: {}, document: { getElementById: () => sheet }, console,
  fetch: async (url, options) => { calls.push({ url, options }); return { json: async () => response }; },
  showToast() {}, setTimeout() {}, clearTimeout() {}
});
const source = fs.readFileSync("app/static/js/agent.js", "utf8")
  .replace(/\}\)\(\);\s*$/, "window.__tests = { team, openDetail, onChange }; })();");
vm.runInContext(source, context);
(async () => {
  await context.window.__tests.team(body);
  assert.match(body.innerHTML, /data-team-same/);
  assert.match(body.innerHTML, /additional requests/);
  assert.match(body.innerHTML, /Enable same-model workers/);
  response.settings.same_model_workers = true;
  await context.window.__tests.team(body);
  assert.match(body.innerHTML, /data-team-same checked/);
  assert.doesNotMatch(body.innerHTML, /Enable same-model workers above/);
  // This saves the existing API setting, without changing approval policy.
  await context.window.__tests.onChange({
    target: { checked: true, closest: selector => selector === "[data-team-same]" }
  }).catch(e => {
    // render() has no real DOM in this contract test, after the save request.
    if (!(e instanceof TypeError) && e.name !== "TypeError") throw e;
  });
  const saved = calls.find(c => c.options.method === "POST");
  assert.equal(saved.url, "/api/agent/team");
  assert.deepEqual(JSON.parse(saved.options.body), { same_model_workers: true });
  response = { detail: { status: "done", order: [], steps: {}, observability: [{
    kind: "model.finished", ids: { worker_id: "sub1", run_id: "run-fixture" },
    payload: { request_id: "req_fixture", response_model: "<script>unsafe</script>", usage: null }
  }] } };
  await context.window.__tests.openDetail("chat:synthetic");
  assert.match(sheet.innerHTML, /Run diagnostics/);
  assert.match(sheet.innerHTML, /req_fixture/);
  assert.match(sheet.innerHTML, /Missing usage is unknown/);
  assert.match(sheet.innerHTML, /&lt;script&gt;/);
  assert.doesNotMatch(sheet.innerHTML, /<script>unsafe/);
  console.log("12 Agent diagnostics DOM assertions passed");
})().catch(e => { console.error(e); process.exitCode = 1; });
