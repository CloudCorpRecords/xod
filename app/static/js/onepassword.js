// 1Password in Settings (services/onepassword.py): connect it, use a 1Password field for any of Xod's API keys (Xod
// keeps the op:// reference, not the key), and let website logins come from 1Password.
const OP_KEY_NAMES = {
  fireworks_api_key: "Fireworks", anthropic_api_key: "Anthropic (Claude)", openai_api_key: "OpenAI", gemini_api_key: "Google Gemini",
  groq_api_key: "Groq", openrouter_api_key: "OpenRouter", tavily_api_key: "Tavily", you_api_key: "You.com",
  youtube_api_key: "YouTube", trustmrr_api_key: "TrustMRR", huggingface_token: "Hugging Face",
};

async function loadOnePassword() {
  const body = document.getElementById("onePasswordBody");
  if (!body) return;
  let s;
  try { s = await (await fetch("/api/1password")).json(); } catch (e) { body.textContent = "Couldn't reach Xod."; return; }
  if (!s.installed) {
    body.innerHTML = `Keep your API keys and website passwords in 1Password and Xod uses them from there, nothing copied.
      Install 1Password's command line tool (<code class="text-zinc-300">brew install 1password-cli</code>), then in the 1Password app turn on
      Settings → Developer → <b>Integrate with 1Password CLI</b>.
      <a href="${s.install_url}" target="_blank" class="text-brand-300 hover:underline">How to install</a>
      <button onclick="loadOnePassword()" class="ml-2 text-zinc-300 hover:text-white">Check again</button>`;
    return;
  }
  if (!s.connected) {
    const who = s.accounts.length ? ` (${escapeHtml(s.accounts.map((a) => a.email).join(", "))})` : "";
    body.innerHTML = `<div class="flex items-center justify-between gap-3">
      <span>1Password is installed${who}. Connect it to use your keys and logins from there; 1Password asks you to approve.</span>
      <button onclick="connectOnePassword(this)" class="px-3 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-500 text-white font-medium shrink-0">Connect</button></div>
      <p id="onePasswordMsg" class="mt-2 hidden"></p>`;
    return;
  }
  const keyOpts = Object.entries(OP_KEY_NAMES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  body.innerHTML = `
    <div class="flex items-center justify-between gap-3">
      <span class="text-emerald-400">Connected</span>
      <button onclick="disconnectOnePassword()" class="text-zinc-500 hover:text-rose-400">Disconnect</button>
    </div>
    <label class="flex items-center gap-2 text-zinc-300">
      <input type="checkbox" ${s.logins ? "checked" : ""} onchange="setOnePasswordLogins(this.checked)">
      Use my 1Password logins on websites (when Xod has none saved for the site)
    </label>
    <div id="onePasswordKeys" class="text-zinc-500"></div>
    <div class="space-y-2 pt-2 border-t border-white/[0.05]">
      <div class="text-zinc-300">Use a key from 1Password</div>
      <div class="grid grid-cols-1 sm:grid-cols-[10rem_1fr] gap-2">
        <select id="opKeyName" class="bg-surface-900 border border-surface-700 rounded-lg px-2 py-2 text-zinc-100">${keyOpts}</select>
        <input id="opSearch" oninput="searchOnePassword(this.value)" placeholder="Search 1Password (e.g. Fireworks)" autocomplete="off"
          class="bg-surface-900 border border-surface-700 rounded-lg px-3 py-2 text-white placeholder:text-zinc-500 focus:outline-none focus:border-brand-500">
      </div>
      <ul id="opResults" class="space-y-1"></ul>
      <p id="onePasswordMsg" class="hidden"></p>
    </div>`;
  loadOnePasswordKeys();
}

async function loadOnePasswordKeys() {
  const el = document.getElementById("onePasswordKeys");
  try {
    const r = await (await fetch("/api/secrets")).json();
    const from = r.keys.filter((k) => k.from_1password);
    el.textContent = from.length ? `From 1Password: ${from.map((k) => OP_KEY_NAMES[k.name] || k.name).join(", ")}` : "";
  } catch (e) {}
}

function onePasswordMsg(text, ok) {
  const m = document.getElementById("onePasswordMsg");
  if (!m) return;
  m.textContent = text;
  m.className = ok ? "text-emerald-400" : "text-rose-400";
}

async function connectOnePassword(btn) {
  btn.disabled = true;
  btn.textContent = "Waiting for 1Password…";
  const r = await (await fetch("/api/1password/connect", { method: "POST" })).json();
  if (!r.success) { btn.disabled = false; btn.textContent = "Connect"; onePasswordMsg(r.error, false); return; }
  showToast(`1Password connected${r.email ? ` (${r.email})` : ""}`);
  loadOnePassword();
}

async function disconnectOnePassword() {
  if (!confirm("Disconnect 1Password? Keys that come from 1Password stop working until you connect it again or paste them in.")) return;
  await fetch("/api/1password/disconnect", { method: "POST" });
  loadOnePassword();
}

async function setOnePasswordLogins(on) {
  await fetch("/api/1password/logins", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on }) });
  showToast(on ? "Websites can use your 1Password logins" : "1Password logins off");
}

let opSearchTimer = null;
function searchOnePassword(q) {
  clearTimeout(opSearchTimer);
  opSearchTimer = setTimeout(async () => {
    const ul = document.getElementById("opResults");
    if (!q.trim()) { ul.innerHTML = ""; return; }
    const r = await (await fetch(`/api/1password/items?q=${encodeURIComponent(q)}`)).json();
    if (!r.success) { onePasswordMsg(r.error, false); return; }
    ul.innerHTML = r.items.length ? r.items.map((i) => `
      <li><button data-id="${escapeHtml(i.id)}" onclick="pickOnePasswordItem(this.dataset.id, this)" class="w-full text-left px-3 py-1.5 rounded-lg bg-surface-900/70 hover:bg-surface-700 text-zinc-200">
        ${escapeHtml(i.title)} <span class="text-zinc-500">· ${escapeHtml(i.vault)}</span></button></li>`).join("")
      : `<li class="text-zinc-500">Nothing in 1Password matches.</li>`;
  }, 300);
}

async function pickOnePasswordItem(id, btn) {
  const r = await (await fetch(`/api/1password/items/${encodeURIComponent(id)}/fields`)).json();
  if (!r.success) { onePasswordMsg(r.error, false); return; }
  const li = btn.closest("li");
  li.innerHTML = btn.outerHTML + `<div class="flex flex-wrap gap-1.5 mt-1 ml-3">${r.fields.map((f) =>
    `<button data-ref="${escapeHtml(f.reference)}" onclick="linkOnePasswordField(this.dataset.ref)" class="px-2 py-1 rounded-md bg-brand-500/15 hover:bg-brand-500/25 text-brand-200">${escapeHtml(f.label)}</button>`).join("")
    || `<span class="text-zinc-500">No fields with a value.</span>`}</div>`;
}

async function linkOnePasswordField(reference) {
  const name = document.getElementById("opKeyName").value;
  const r = await (await fetch("/api/1password/link", { method: "POST", headers: { "Content-Type": "application/json" },
                                                       body: JSON.stringify({ name, reference }) })).json();
  if (!r.success) { onePasswordMsg(r.error || r.detail, false); return; }
  onePasswordMsg(`${OP_KEY_NAMES[name]} now comes from 1Password.`, true);
  document.getElementById("opResults").innerHTML = "";
  document.getElementById("opSearch").value = "";
  loadOnePasswordKeys();
  if (typeof loadConfig === "function") loadConfig();
}
