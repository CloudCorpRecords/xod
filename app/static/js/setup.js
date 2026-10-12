// First-run setup wizard (backend: services/setup_wizard.py, services/local_engine.py).
// Steps: check the Mac → pick an AI model (LM Studio / Ollama / built-in / cloud) → download if
// needed → say hi to it → starter tasks. Opens by itself for new users; Tools → "Setup & AI model" reopens it.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let state = null;
  let choice = null;      // {provider, model, label, needsDownload}
  let pollTimer = null;

  const STARTERS = [
    { icon: "globe", text: "What's new on Hacker News today? Give me the top 5 with one line each." },
    { icon: "folder-search", text: "Look through my Downloads folder and tell me what's taking up the most space." },
    { icon: "image", text: "Make an image of a cozy cabin under the northern lights." },
    { icon: "file-text", text: "Research the best note-taking apps for Mac and save a short comparison to a project." },
  ];

  function shell(inner) {
    let root = $("setupWizard");
    if (!root) {
      root = document.createElement("div");
      root.id = "setupWizard";
      root.className = "fixed inset-0 z-[90] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm";
      document.body.appendChild(root);
    }
    root.innerHTML = `
      <div class="w-full max-w-2xl max-h-[92vh] overflow-y-auto rounded-3xl bg-surface-850 border border-white/[0.08] shadow-2xl">
        <div class="px-7 pt-6 pb-2 flex items-center justify-between">
          <div class="flex items-center gap-3">
            <div class="xod-mascot xod-mascot--small" data-mood="happy"></div>
            <span class="font-display text-xl text-white">Set up Xod</span>
          </div>
          <button data-setup="close" class="p-1.5 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
        </div>
        <div class="px-7 pb-7">${inner}</div>
      </div>`;
    if (window.lucide) lucide.createIcons();
  }

  function chip(ok, text) {
    return `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[12px] ${ok ? "bg-emerald-500/10 text-emerald-300" : "bg-amber-500/10 text-amber-300"}">
      <i data-lucide="${ok ? "check" : "alert-triangle"}" class="size-3.5"></i>${esc(text)}</span>`;
  }

  function option(o) {
    return `<button data-setup="pick" data-provider="${esc(o.provider)}" data-model="${esc(o.model)}" data-label="${esc(o.label)}" data-download="${o.needsDownload ? 1 : 0}"
      class="w-full text-left p-4 rounded-2xl border transition-all ${o.recommended ? "border-brand-500/60 bg-brand-500/[0.07]" : "border-white/[0.08] bg-surface-900/60"} hover:border-brand-400 ${o.disabled ? "opacity-40 pointer-events-none" : ""}">
      <div class="flex items-start gap-3">
        <div class="size-9 rounded-xl flex items-center justify-center shrink-0 ${o.recommended ? "bg-brand-500/20 text-brand-300" : "bg-surface-700 text-zinc-300"}"><i data-lucide="${o.icon}" class="size-4"></i></div>
        <div class="min-w-0 flex-1">
          <p class="text-white text-[14px] font-medium flex items-center gap-2 flex-wrap">${esc(o.title)}${o.recommended ? `<span class="text-[10px] px-2 py-0.5 rounded-full bg-brand-500 text-surface-900 font-semibold">Recommended</span>` : ""}</p>
          <p class="text-[12px] text-zinc-400 mt-0.5">${esc(o.sub)}</p>
        </div>
      </div>
    </button>`;
  }

  function stepChoose() {
    const s = state;
    const opts = [];
    const lm = s.lm_studio, ol = s.ollama;
    if (lm.online && (lm.loaded || lm.models.length)) {
      const m = lm.loaded || lm.models[0];
      // A model over ~6 GB on a 16 GB Mac pushes it into swap, and every answer waits on the disk.
      const gb = (lm.sizes || {})[m];
      const heavy = gb && s.mac.ram_gb && gb > s.mac.ram_gb * 0.38;
      opts.push({ provider: "lm_studio", model: m, label: m, icon: "server", title: `LM Studio: ${m}`,
                  sub: (lm.loaded ? "Already running. Nothing to download." : "Installed in LM Studio. Xod loads it when needed.")
                       + (heavy ? ` At ${gb.toFixed(1)} GB it's large for a ${s.mac.ram_gb} GB Mac, so answers will be slow; the built-in ${(s.builtin.find((x) => x.recommended) || {}).label || "model"} is about three times faster.` : ""),
                  recommended: !!lm.loaded && !heavy });
    }
    if (ol.online && ol.models.length) {
      opts.push({ provider: "ollama", model: ol.models[0], label: ol.models[0], icon: "server", title: `Ollama: ${ol.models[0]}`,
                  sub: "Already installed. Nothing to download." });
    }
    const rec = s.builtin.find((m) => m.recommended);
    const noLocal = !opts.some((o) => o.recommended);
    for (const m of s.builtin) {
      if (!m.recommended && !m.downloaded) continue;   // others are under "More sizes"
      opts.push({ provider: "xod", model: m.repo, label: m.label, icon: "sparkles",
                  title: `Built-in: ${m.label}`, needsDownload: !m.downloaded,
                  sub: `${m.blurb} ${m.downloaded ? "Downloaded." : `One ${m.download_gb} GB download.`}`,
                  recommended: m.recommended && noLocal, disabled: !s.mac.apple_silicon });
    }
    const more = s.builtin.filter((m) => !m.recommended && !m.downloaded);
    shell(`
      <h2 class="text-white text-lg font-semibold mt-2">Your Mac</h2>
      <div class="flex flex-wrap gap-2 mt-2">
        ${chip(s.mac.apple_silicon, s.mac.apple_silicon ? "Apple silicon" : "Not Apple silicon")}
        ${chip(s.mac.ram_gb >= 8, `${s.mac.ram_gb} GB memory`)}
        ${chip(s.mac.disk_free_gb >= 10, `${Math.round(s.mac.disk_free_gb)} GB free`)}
        ${chip(true, `macOS ${s.mac.macos}`)}
      </div>
      ${s.mac.issues.map((i) => `<p class="text-[12px] text-amber-300 mt-2">${esc(i)}</p>`).join("")}

      <h2 class="text-white text-lg font-semibold mt-6">Pick Xod's brain</h2>
      <p class="text-[13px] text-zinc-400 mt-1">Everything below runs on your Mac and stays private. You can change it any time in Settings.</p>
      <div class="space-y-2.5 mt-3">${opts.map(option).join("")}</div>
      ${more.length ? `
        <details class="mt-3">
          <summary class="text-[13px] text-zinc-400 cursor-pointer hover:text-white">More built-in sizes</summary>
          <div class="space-y-2.5 mt-2.5">${more.map((m) => option({ provider: "xod", model: m.repo, label: m.label, icon: "sparkles",
            title: `Built-in: ${m.label}`, needsDownload: true, disabled: !m.fits || !s.mac.apple_silicon,
            sub: `${m.blurb} ${m.download_gb} GB download.${m.fits ? "" : ` Needs ${m.min_ram_gb} GB+ of memory.`}` })).join("")}</div>
        </details>` : ""}
      <div class="mt-5 p-4 rounded-2xl border border-white/[0.06] bg-surface-900/40 flex items-center gap-3">
        <i data-lucide="cloud" class="size-4 text-zinc-400 shrink-0"></i>
        <p class="text-[12px] text-zinc-400 flex-1">Prefer a cloud model (Claude, GPT, Gemini)? Add its API key in Settings. ${s.cloud_keys.length ? `You already have: ${esc(s.cloud_keys.join(", "))}.` : ""}</p>
        <button data-setup="settings" class="px-3 py-1.5 rounded-lg text-[12px] text-zinc-200 bg-surface-700 hover:bg-surface-600 shrink-0">Open Settings</button>
      </div>`);
  }

  function stepDownload() {
    shell(`
      <h2 class="text-white text-lg font-semibold mt-2">Downloading ${esc(choice.label)}</h2>
      <p class="text-[13px] text-zinc-400 mt-1">One time only. You can keep using your Mac; Xod saves it in the Hugging Face cache.</p>
      <div class="mt-5 h-3 rounded-full bg-surface-700 overflow-hidden"><div id="setupBar" class="h-full bg-brand-500 transition-all" style="width:0%"></div></div>
      <p id="setupBarText" class="text-[12px] text-zinc-400 mt-2">Starting…</p>
      <div class="mt-6 flex justify-between">
        <button data-setup="back" class="px-3 py-2 rounded-lg text-[13px] text-zinc-400 hover:text-white">Back</button>
      </div>`);
    const tick = async () => {
      const d = await (await fetch(`/api/setup/download?repo=${encodeURIComponent(choice.model)}`)).json();
      const bar = $("setupBar"), txt = $("setupBarText");
      if (!bar) return clearInterval(pollTimer);
      bar.style.width = `${d.percent}%`;
      if (d.status === "error") { clearInterval(pollTimer); txt.innerHTML = `<span class="text-red-300">Download failed: ${esc(d.error)}</span>`; return; }
      txt.textContent = `${d.downloaded_gb} of ${d.total_gb} GB (${d.percent}%)`;
      if (d.status === "done") { clearInterval(pollTimer); await useChoice(); }
    };
    clearInterval(pollTimer);
    pollTimer = setInterval(tick, 1500);
    tick();
  }

  async function useChoice() {
    const r = await (await fetch("/api/setup/choose", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: choice.provider, model: choice.model }) })).json();
    if (!r.success) { showToast(r.error || "Couldn't use that model"); return stepChoose(); }
    stepTest();
  }

  async function stepTest() {
    shell(`
      <h2 class="text-white text-lg font-semibold mt-2">Saying hi to ${esc(choice.label)}</h2>
      <p class="text-[13px] text-zinc-400 mt-1">${choice.provider === "xod" ? "Starting the built-in model (the first start takes up to a minute)…" : "Checking it answers…"}</p>
      <div id="setupTest" class="mt-5 p-4 rounded-2xl bg-surface-900/60 border border-white/[0.06] text-[13px] text-zinc-300 flex items-center gap-2">
        <i data-lucide="loader-2" class="size-4 animate-spin text-brand-400"></i> Waiting for an answer…
      </div>`);
    const r = await (await fetch("/api/setup/test", { method: "POST" })).json();
    const box = $("setupTest");
    if (!box) return;
    if (!r.success) {
      box.innerHTML = `<span class="text-red-300">${esc(r.error)}</span>`;
      box.insertAdjacentHTML("afterend", `<div class="mt-4 flex gap-2"><button data-setup="back" class="px-3 py-2 rounded-lg text-[13px] bg-surface-700 text-white">Pick another</button><button data-setup="retest" class="px-3 py-2 rounded-lg text-[13px] text-zinc-300 hover:text-white">Try again</button></div>`);
      return;
    }
    await fetch("/api/setup/complete", { method: "POST" });
    stepDone(r);
  }

  function stepDone(r) {
    shell(`
      <h2 class="text-white text-lg font-semibold mt-2">You're all set</h2>
      <div class="mt-3 p-4 rounded-2xl bg-emerald-500/[0.07] border border-emerald-500/20 text-[13px] text-zinc-200">
        <span class="text-emerald-300 font-medium">${esc(choice.label)}:</span> “${esc(r.reply)}” <span class="text-zinc-500">(${r.seconds}s)</span>
      </div>
      <h3 class="text-white text-[15px] font-semibold mt-6">Try one of these</h3>
      <div class="grid sm:grid-cols-2 gap-2.5 mt-3">
        ${STARTERS.map((t, i) => `<button data-setup="starter" data-i="${i}" class="text-left p-3.5 rounded-2xl border border-white/[0.08] bg-surface-900/60 hover:border-brand-400 flex gap-3">
          <i data-lucide="${t.icon}" class="size-4 text-brand-300 shrink-0 mt-0.5"></i><span class="text-[13px] text-zinc-200">${esc(t.text)}</span></button>`).join("")}
      </div>
      <p class="text-[12px] text-zinc-500 mt-5">Tip: hand Xod a longer job in <b class="text-zinc-300">Tasks</b>, and it asks before anything is sent, paid or deleted.</p>
      <div class="mt-5 flex justify-end"><button data-setup="close" class="px-4 py-2 rounded-xl bg-brand-500 text-surface-900 text-[13px] font-semibold hover:bg-brand-400">Start using Xod</button></div>`);
  }

  async function open() {
    shell(`<div class="py-16 text-center text-zinc-400 text-[13px]"><i data-lucide="loader-2" class="size-5 animate-spin mx-auto mb-2 text-brand-400"></i>Checking your Mac…</div>`);
    state = await (await fetch("/api/setup")).json();
    stepChoose();
  }

  function close() {
    clearInterval(pollTimer);
    const root = $("setupWizard");
    if (root) root.remove();
    if (typeof loadConfig === "function") loadConfig();
  }

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-setup]");
    if (!b || !b.closest("#setupWizard")) return;
    const act = b.dataset.setup;
    if (act === "close") return close();
    if (act === "settings") { close(); return openSettingsModal(); }
    if (act === "back") { clearInterval(pollTimer); state = await (await fetch("/api/setup")).json(); return stepChoose(); }
    if (act === "retest") return stepTest();
    if (act === "starter") {
      const t = STARTERS[+b.dataset.i];
      close();
      const input = $("chatInput") || document.querySelector("#chatForm textarea");
      if (input) { input.value = t.text; input.focus(); input.dispatchEvent(new Event("input")); }
      return;
    }
    if (act === "pick") {
      choice = { provider: b.dataset.provider, model: b.dataset.model, label: b.dataset.label, needsDownload: b.dataset.download === "1" };
      if (choice.needsDownload) {
        const r = await (await fetch("/api/setup/download", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repo: choice.model }) })).json();
        if (!r.success) return showToast(r.error || "Couldn't start the download");
        return stepDownload();
      }
      return useChoice();
    }
  });

  window.openSetupWizard = open;
  document.addEventListener("DOMContentLoaded", async () => {
    try {
      const s = await (await fetch("/api/setup")).json();
      if (s.show_on_start) { state = s; stepChoose(); }
    } catch (e) {}
  });
})();
