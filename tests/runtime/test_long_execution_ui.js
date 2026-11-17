/* Real panel rendering/save handlers, no browser, API key or native device. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const calls = [], notices = [];
let reply = {}, valid = true;
const values = {
  context_tokens: 200000, output_tokens: 4096, safety_tokens: 4096,
  max_steps: 120, max_requests: 160, max_seconds: 3600, max_input_tokens: 2000000
};
const form = {
  reportValidity: () => valid,
  querySelector: selector => ({
    "[data-long-enabled]": { checked: true },
    "[data-long-provider]": { value: "openai" },
    "[data-long-model]": { value: " fixture-cloud " }
  }[selector]),
  querySelectorAll: () => Object.entries(values).map(([key, value]) =>
    ({ dataset: { long: key }, value: String(value) }))
};
const context = vm.createContext({
  window: {}, console, setTimeout() {}, clearTimeout() {},
  document: { querySelector: () => form, getElementById: () => null },
  fetch: async (url, options) => { calls.push({url, options}); return { json: async () => reply }; },
  showToast: message => notices.push(message)
});
const source = fs.readFileSync("app/static/js/agent.js", "utf8")
  .replace(/\}\)\(\);\s*$/, "window.__tests = { longExecution, onClick }; })();");
vm.runInContext(source, context);
(async () => {
  const html = context.window.__tests.longExecution({
    long_execution: { enabled: true, model: "<script>model</script>" },
    checkpoints: [{ id: "a".repeat(32), status: "paused", reason: "<script>reason</script>" }]
  });
  assert.match(html, /conservative UTF-8 byte estimate/);
  assert.match(html, /not provider-verified/);
  assert.match(html, /No dollar-cost cap/);
  assert.match(html, /data-long-enabled checked/);
  assert.match(html, /resume checkpoint a{32}/);
  assert.match(html, /&lt;script&gt;reason/);
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /value="200000"/); // Unknown capacity is not auto-filled.
  const event = { target: { closest: selector => selector === "[data-long-save]" } };
  reply = { detail: "Context limit required" };
  await context.window.__tests.onClick(event);
  assert.equal(notices.at(-1), "Context limit required");
  assert.equal(calls[0].url, "/api/agent/long-execution");
  const saved = JSON.parse(calls[0].options.body);
  assert.equal(saved.model, "fixture-cloud");
  assert.equal(saved.context_tokens, 200000);
  assert.equal(saved.enabled, true);
  reply = saved;
  await context.window.__tests.onClick(event);
  assert.equal(notices.at(-1), "Long-running limits saved");
  valid = false;
  const before = calls.length;
  await context.window.__tests.onClick(event);
  assert.equal(calls.length, before);
  console.log("15 long-execution UI assertions passed");
})().catch(e => { console.error(e); process.exitCode = 1; });
