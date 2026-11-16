// Deterministic DOM-contract checks without a browser, credentials or network.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("app/static/js/app.js", "utf8");
const start = source.indexOf("function handleProviderChange(");
const end = source.indexOf("\nfunction setFireworksModel(", start);
assert(start >= 0 && end > start);
const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    const classes = new Set();
    elements.set(id, { value: "", textContent: "", disabled: false,
      classList: { add: x => classes.add(x), remove: x => classes.delete(x),
        contains: x => classes.has(x) } });
  }
  return elements.get(id);
}
const mlx = {};
const context = vm.createContext({
  window: { xodModelConfig: {
    defaults: {
      openai: { model: "gpt-6.1-terra", base_url: "https://api.openai.com/v1" },
      fireworks: { model: "accounts/fireworks/models/server-choice", base_url: "https://api.fireworks.ai/inference/v1" },
    },
    mlx_supported: false, mlx_unavailable: "The built-in engine needs Apple silicon.",
  } },
  document: { getElementById: element, querySelector: () => mlx,
    querySelectorAll: () => [], readyState: "complete" },
  refreshLlmModels: () => {}, kpOnProviderChange: () => {},
  location: { hash: "" },
});
vm.runInContext(source.slice(start, end), context);
element("selectLlmProvider").value = "lm_studio";
element("inputLlmBaseUrl").value = "http://localhost:1235/v1";
element("inputLlmModel").value = "saved-local-model";
vm.runInContext("handleProviderChange(false)", context);
assert.equal(element("inputLlmBaseUrl").value, "http://localhost:1235/v1");
assert.equal(element("inputLlmModel").value, "saved-local-model");
console.log("PASS opening Settings preserves a saved local endpoint and model");

element("selectLlmProvider").value = "openai";
element("inputCloudKey").value = "<unsaved-other-provider-fixture>";
vm.runInContext("handleProviderChange()", context);
assert.equal(element("inputLlmModel").value, "gpt-6.1-terra");
assert.equal(element("inputLlmBaseUrl").value, "https://api.openai.com/v1");
assert.equal(element("inputCloudKey").value, "");
console.log("PASS switching provider uses server defaults and clears another provider's unsaved key");

element("selectLlmProvider").value = "fireworks";
vm.runInContext("handleProviderChange()", context);
assert.equal(element("inputLlmModel").value, "accounts/fireworks/models/server-choice");
console.log("PASS provider defaults come from the server, not an independent frontend model table");
assert.equal(mlx.disabled, true);
assert(mlx.title.includes("Apple silicon"));
console.log("PASS unsupported built-in MLX selection has a clear unavailable state");

element("selectLlmProvider").value = "custom";
vm.runInContext("handleProviderChange(false)", context);
assert.equal(element("groupBaseUrl").classList.contains("hidden"), false);
assert.equal(element("groupCloudKey").classList.contains("hidden"), true);
assert(element("modelConfigurationHint").textContent.includes("Model servers"));
console.log("PASS added model servers retain their existing registration/key flow");

vm.runInContext(fs.readFileSync("app/static/js/settings.js", "utf8"), context);
(async () => {
  let finish;
  context.fetch = async (url, options) => {
    assert.equal(url, "/api/setup/test");
    assert.equal(options.method, "POST");
    return new Promise(resolve => { finish = resolve; });
  };
  const pending = context.window.testSavedModel();
  assert.equal(element("testSavedModel").disabled, true);
  finish({ ok: true, json: async () => ({
    success: true, provider: "openai", model: "gpt-6.1-terra",
    route: "api.openai.com", reply: "<literal reply, not markup>",
  }) });
  await pending;
  assert(element("savedModelTestResult").textContent.includes("<literal reply, not markup>"));
  assert(element("savedModelTestResult").textContent.includes("gpt-6.1-terra"));
  assert.equal(element("testSavedModel").disabled, false);
  console.log("PASS saved-model test shows the confirmed identity and treats provider text as text");
  context.fetch = async () => ({ ok: true, json: async () => ({
    success: false, error: "OpenAI refused the API key. Paste a new one in Settings.",
  }) });
  await context.window.testSavedModel();
  assert(element("savedModelTestResult").textContent.includes("Paste a new one"));
  assert.equal(element("testSavedModel").disabled, false);
  console.log("PASS actionable connection errors are visible and the test button recovers");
  assert(source.slice(source.indexOf("function openSettingsModal()"),
    source.indexOf("function closeSettingsModal()")).includes("loadConfig()"));
  console.log("PASS Settings reloads the install choice after other pickers or paired devices change it");
  console.log("8/8 checks passed. No browser or live provider was used.");
})().catch(error => { console.error(error); process.exitCode = 1; });
