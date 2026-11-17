// Deterministic rendering and disconnect feedback; no browser or account.
const fs = require("fs");
const vm = require("vm");
const assert = require("assert/strict");
const source = fs.readFileSync("app/static/js/mcp.js", "utf8");
const start = source.indexOf("  const STATE = {");
const end = source.indexOf("  // ---------------------------------------------------------------- paste anything", start);
assert.ok(start > 0 && end > start);
let toasts = [], renders = 0;
const context = {
  location: {origin: "http://127.0.0.1:8765"},
  esc: value => String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;"),
  TRUST: {ask: "Ask", read_only: "Read only"},
  confirm: () => true,
  api: async () => ({success: false, error: "Key store is locked"}),
  showToast: message => toasts.push(message),
  render: async () => renders++,
};
vm.createContext(context);
vm.runInContext(source.slice(start, end) + "\nglobalThis.card = connectorCard; globalThis.onConnector = onConnector;", context);
const gmail = {id: "gmail", name: "Gmail (official)", icon: "mail", kind: "own", state: "off",
  setup_url: "https://developers.google.com/workspace/guides/configure-mcp-servers"};
const html = context.card(gmail);
assert.ok(html.includes("Web application"));
assert.ok(!html.includes("Desktop OAuth"));
assert.ok(html.includes("Developer Preview"));
assert.ok(html.includes("MCP API"));
assert.ok(html.includes("AI-powered agent"));
assert.ok(html.includes("http://127.0.0.1:8765/oauth/callback"));
assert.ok(html.includes('aria-label="Gmail (official) OAuth client ID"'));
assert.ok(html.includes('aria-label="Gmail (official) OAuth client secret"'));
assert.ok(html.includes("read-only"));
assert.ok(html.includes("offline refresh"));
const needs = context.card({...gmail, state: "needs_sign_in", error: "Sign in again"});
assert.ok(needs.includes("Sign in again"));
const signing = context.card({...gmail, state: "signing_in", sign_in_url: 'https://provider.test/auth?state="x'});
assert.ok(signing.includes("Open sign-in page"));
assert.ok(signing.includes("&quot;x"));
assert.ok(signing.includes("noopener noreferrer"));
(async () => {
  await context.onConnector({dataset: {cn: "disconnect"}, closest: () => ({dataset: {connector: "gmail"}})}, {type: "click"});
  assert.deepEqual(toasts, ["Key store is locked"]);
  assert.equal(renders, 1);
  context.api = async () => { throw Error("fixture network failure"); };
  const button = {dataset: {cn: "connect"}, closest: () => ({
    dataset: {connector: "gmail"}, querySelector: () => null,
  })};
  await context.onConnector(button, {type: "click"});
  assert.equal(button.disabled, false);
  assert.ok(toasts[1].includes("Couldn't reach Xod"));
  assert.equal(renders, 2);
  console.log("Connector rendering, setup instructions, disconnect errors and network recovery passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
