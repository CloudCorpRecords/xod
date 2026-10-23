// The model switcher at the top of the page: the model answering chats, and one click to use another one you have
// (online with a saved key, Ollama, LM Studio, built-in). Also says when macOS won't let Xod read its saved keys.
let modelSwitcher = null;

function toggleModelSwitcher() {
  const p = document.getElementById("modelSwitcherPanel");
  p.classList.toggle("hidden");
  if (!p.classList.contains("hidden")) loadModelSwitcher();
}
document.addEventListener("click", (e) => {
  const p = document.getElementById("modelSwitcherPanel");
  if (p && !p.classList.contains("hidden") && !e.target.closest("#modelSwitcherPanel, #modelSwitcherBtn")) p.classList.add("hidden");
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

function renderModelSwitcher() {
  const p = document.getElementById("modelSwitcherPanel");
  const r = modelSwitcher;
  const groups = {};
  r.choices.forEach((c, i) => (groups[c.group] = groups[c.group] || []).push([c, i]));
  const row = ([c, i]) => `
    <button onclick="useModelChoice(${i})" class="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left ${c.current ? "bg-brand-500/15 text-white" : "text-zinc-300 hover:bg-surface-700"}">
      <span class="size-1.5 rounded-full shrink-0 ${c.current ? (c.missing ? "bg-amber-400" : "bg-emerald-400") : "bg-transparent"}"></span>
      <span class="flex-1 min-w-0 truncate" title="${escapeHtml(c.model)}">${escapeHtml(c.label)}</span>
      ${c.loaded ? `<span class="text-[10px] text-zinc-500">loaded</span>` : ""}
      ${c.missing ? `<span class="text-[10px] text-amber-300">not available</span>` : c.current ? `<span class="text-[10px] text-brand-300">in use</span>` : ""}
    </button>`;
  const keys = r.keys && !r.keys.readable ? `
    <div class="m-1 mb-2 p-2 rounded-lg bg-amber-500/10 text-amber-200 space-y-1.5">
      <div>Your saved API keys are safe, but macOS isn't letting Xod read them right now.</div>
      <button onclick="retryKeychain()" class="px-2 py-1 rounded-md bg-amber-500/20 hover:bg-amber-500/30 text-amber-100">Try again</button>
      <span class="text-amber-300/80">then choose <b>Always Allow</b></span>
    </div>` : "";
  const order = ["In use", "Online", "Ollama", "LM Studio", "Built-in"];
  p.innerHTML = keys + order.filter((g) => groups[g]).map((g) => `
    <div class="px-2 pt-2 pb-1 text-[10px] uppercase tracking-wider text-zinc-500">${g === "Online" ? "Online (your API keys)" : g}</div>
    ${groups[g].map(row).join("")}`).join("") + `
    <div class="mt-2 pt-2 border-t border-white/[0.05] flex flex-wrap gap-x-3 gap-y-1 px-2 pb-1 text-zinc-400">
      <button onclick="document.getElementById('modelSwitcherPanel').classList.add('hidden'); openSettingsModal()" class="hover:text-white">Add a key or model…</button>
      ${r.lm_studio_online ? `<button onclick="document.getElementById('modelSwitcherPanel').classList.add('hidden'); openLMStudioModal()" class="hover:text-white">LM Studio details</button>` : ""}
    </div>`;
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
