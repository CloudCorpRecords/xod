// The model switcher at the top of the page: searchable across all cloud + local models
// (online with a saved key, Ollama, LM Studio, built-in), filtering out embedding-only models.
let modelSwitcher = null;
let modelSwitcherQuery = "";

const _EMBED_MODEL_RE = /(embed|nomic-embed|bge-|mxbai-embed|text-embedding)/i;

function toggleModelSwitcher() {
  const p = document.getElementById("modelSwitcherPanel");
  p.classList.toggle("hidden");
  if (!p.classList.contains("hidden")) {
    modelSwitcherQuery = "";
    loadModelSwitcher();
  }
}
document.addEventListener("click", (e) => {
  const p = document.getElementById("modelSwitcherPanel");
  if (p && !p.classList.contains("hidden") && !e.target.closest("#modelSwitcherPanel, #modelSwitcherBtn, #statusBrainSwitchBtn")) p.classList.add("hidden");
});

async function loadModelSwitcher() {
  const p = document.getElementById("modelSwitcherPanel");
  if (!modelSwitcher) p.innerHTML = `<div class="p-3 text-zinc-500">Looking for your models…</div>`;
  try {
    modelSwitcher = await (await fetch("/api/models/quick")).json();
  } catch (e) {
    p.innerHTML = `<div class="p-3 text-red-300">Couldn't reach Xod.</div>`;
    return;
  }
  renderModelSwitcher();
}

function filterModelSwitcher(val) {
  modelSwitcherQuery = String(val || "");
  renderModelSwitcher(true);
}

function renderModelSwitcher(keepFocus = false) {
  const p = document.getElementById("modelSwitcherPanel");
  const r = modelSwitcher;
  if (!p || !r) return;
  const q = modelSwitcherQuery.trim().toLowerCase();
  const groups = {};
  (r.choices || []).forEach((c, i) => {
    if (!c.current && _EMBED_MODEL_RE.test(String(c.model || c.label || ""))) return;
    if (q) {
      const hay = `${c.label || ""} ${c.model || ""} ${c.provider || ""} ${c.group || ""} ${c.group === "Online" ? "cloud" : "local"}`.toLowerCase();
      if (!hay.includes(q)) return;
    }
    (groups[c.group] = groups[c.group] || []).push([c, i]);
  });
  const isLocal = (g) => g === "Ollama" || g === "LM Studio" || g === "Built-in";
  const row = ([c, i]) => `
    <button onclick="useModelChoice(${i})" class="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left ${c.current ? "bg-brand-500/15 text-white" : "text-zinc-300 hover:bg-surface-700"}">
      <span class="size-1.5 rounded-full shrink-0 ${c.current ? (c.missing ? "bg-amber-400" : "bg-emerald-400") : "bg-transparent"}"></span>
      <span class="flex-1 min-w-0 truncate" title="${escapeHtml(c.model)}">${escapeHtml(c.label)}</span>
      ${isLocal(c.group) ? `<span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-300 shrink-0">Local</span>` : `<span class="text-[10px] px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-300 shrink-0">Cloud</span>`}
      ${c.loaded ? `<span class="text-[10px] text-zinc-500">loaded</span>` : ""}
      ${c.missing ? `<span class="text-[10px] text-amber-300">offline</span>` : c.current ? `<span class="text-[10px] text-brand-300">in use</span>` : ""}
    </button>`;
  const keys = r.keys && !r.keys.readable ? `
    <div class="m-1 mb-2 p-2 rounded-lg bg-amber-500/10 text-amber-200 space-y-1.5">
      <div>Your saved API keys are safe, but macOS isn't letting Xod read them right now.</div>
      <button onclick="retryKeychain()" class="px-2 py-1 rounded-md bg-amber-500/20 hover:bg-amber-500/30 text-amber-100">Try again</button>
      <span class="text-amber-300/80">then choose <b>Always Allow</b></span>
    </div>` : "";
  const searchBox = `
    <div class="p-1.5 pb-1 sticky top-0 bg-surface-800/95">
      <input id="modelSwitcherSearch" type="text" value="${escapeHtml(modelSwitcherQuery)}"
             oninput="filterModelSwitcher(this.value)"
             placeholder="Search models (e.g. glm, gpt, qwen, local)…"
             class="w-full bg-surface-900 border border-white/[0.08] rounded-lg px-2.5 py-1.5 text-[12px] text-white placeholder-zinc-500 focus:outline-none focus:border-brand-500" />
    </div>`;
  const order = ["In use", "Online", "Ollama", "LM Studio", "Built-in"];
  const body = order.filter((g) => groups[g]).map((g) => `
    <div class="px-2 pt-2 pb-1 text-[10px] uppercase tracking-wider text-zinc-500">${g === "Online" ? "Cloud (your API keys)" : g + " (local)"}</div>
    ${groups[g].map(row).join("")}`).join("") || `<div class="p-3 text-[12px] text-zinc-500">No models match "${escapeHtml(modelSwitcherQuery)}".</div>`;
  p.innerHTML = searchBox + keys + `<div class="max-h-72 overflow-y-auto">${body}</div>` + `
    <div class="mt-2 pt-2 border-t border-white/[0.05] flex flex-wrap items-center justify-between gap-2 px-2 pb-1 text-[11.5px] text-zinc-400">
      <button onclick="document.getElementById('modelSwitcherPanel').classList.add('hidden'); if(window.openModelsPanel) openModelsPanel();" class="text-brand-300 hover:text-brand-200 font-medium">All models &amp; Local AI →</button>
      <button onclick="document.getElementById('modelSwitcherPanel').classList.add('hidden'); openSettingsModal()" class="hover:text-white">Keys &amp; providers</button>
    </div>`;
  if (keepFocus) {
    const inp = document.getElementById("modelSwitcherSearch");
    if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
  }
}

async function useModelChoice(i) {
  const c = modelSwitcher.choices[i];
  if (c.current || c.missing) return;
  const res = await (await fetch("/api/setup/choose", { method: "POST", headers: { "Content-Type": "application/json" },
                                                        body: JSON.stringify({ provider: c.provider, model: c.model }) })).json();
  if (!res.success) { showToast(res.error || "Couldn't switch."); return; }
  showToast(`Chats now use ${c.label}`);
  document.getElementById("modelSwitcherPanel").classList.add("hidden");
  modelSwitcher = null;
  if (typeof loadConfig === "function") await loadConfig(); else await updateLMStudioUI();
}

async function retryKeychain() {
  const res = await (await fetch("/api/secrets/retry", { method: "POST" })).json();
  showToast(res.readable ? "Your saved keys are back." : "macOS still says no. Open Keychain Access if no prompt appeared.");
  if (res.readable && typeof loadConfig === "function") await loadConfig();
  await loadModelSwitcher();
}
